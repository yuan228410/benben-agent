import importlib.util
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py")
)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


home = tempfile.mkdtemp(prefix="memory-home-")
base = tempfile.mkdtemp(prefix="memory-ws-")
agent.SESSIONS_DIR = os.path.join(home, "sessions")
memory_dir = os.path.join(home, "memory")

connection = agent.Connection(
    "http://127.0.0.1:1/v1",
    "fake-model",
    "openai-chat-completions",
    "sk-test",
    name="demo",
)
memory = agent.MemoryService(
    connection,
    "fake-model",
    base_dir=memory_dir,
    workspace=base,
    daily_time="23:50",
)

# ---------- storage and long-term memory ----------
status = memory.status_text()
check("status dir", memory_dir in status, status)
check("status empty", "long-term: 0 entries" in status, status)
check("remember", "remembered in MEMORY.md" in memory.remember("用户偏好简洁中文回答。", "偏好"))
check("remember dedup", "already remembered" in memory.remember("用户偏好简洁中文回答。", "偏好"))
try:
    memory.remember("API key: sk-abcdefghijklmnop")
    check("remember secret rejected", False)
except agent.ToolError as exc:
    check("remember secret rejected", "secret" in str(exc), str(exc))
check("memory file private", oct(os.stat(memory.memory_path).st_mode & 0o777) == "0o600")
check("memory dir private", oct(os.stat(memory_dir).st_mode & 0o777) == "0o700")
check("remember tags", "[偏好]" in open(memory.memory_path, encoding="utf-8").read())

# ---------- episodic memory ----------
check("save episode", "saved episode 2026-11-12" in memory.save_episode(
    "### 目标\n实现记忆", "2026-11-12", "测试"
))
episode_path = os.path.join(memory.episodes_dir, "2026-11-12.md")
check("episode exists", os.path.isfile(episode_path))
check("episode private", oct(os.stat(episode_path).st_mode & 0o777) == "0o600")
listed = memory.list_episodes()
check("list episode", "2026-11-12" in listed and "1 entries" in listed, listed)
read = memory.read_episode("2026-11-12")
check("read episode", "实现记忆" in read and "## " in read, read)
try:
    memory.read_episode("2020-01-01")
    check("missing episode rejected", False)
except agent.ToolError as exc:
    check("missing episode rejected", "no episode" in str(exc), str(exc))

# ---------- recall ----------
check("recall long-term", "MEMORY.md" in memory.recall("简洁", scope="long_term"))
check("recall episode", "episodes/2026-11-12.md" in memory.recall("实现记忆", scope="episodes"))
check("recall all", "简洁" in memory.recall("简洁"))
check("recall miss", memory.recall("绝对不存在的词") == "no memory matches")
try:
    memory.recall("x", scope="bad")
    check("recall scope rejected", False)
except agent.ToolError as exc:
    check("recall scope rejected", "scope" in str(exc), str(exc))

# ---------- session timestamps ----------
session = agent.create_session("fake-model", base)
session.log([
    {"role": "system", "content": "system"},
    {"role": "user", "content": "今天完成了记忆功能"},
    {"role": "assistant", "content": "好的"},
])
with open(session.path, encoding="utf-8") as handle:
    records = [json.loads(line) for line in handle if line.strip()]
check("session user timestamp", "_ts" in records[1], records)
check("session assistant timestamp", "_ts" in records[2], records)

# A past-dated session for deterministic daily generation.
past_path = os.path.join(agent.SESSIONS_DIR, "010-20261112-aaaa1111.jsonl")
with open(past_path, "w", encoding="utf-8") as handle:
    for record in [
        {"_type": "meta", "created": "2026-11-12T09:00:00", "workspace": base},
        {"role": "user", "content": "今天研究了长期记忆", "_ts": "2026-11-12T10:00:00"},
        {"role": "assistant", "content": "确定用 Markdown 存储", "_ts": "2026-11-12T10:01:00"},
    ]:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

# ---------- automatic daily episode ----------
class FakeClient:
    model = "fake-model"
    last_reasoning_ignored = False
    last_usage = {"total_tokens": 9}
    last_finish_reason = "stop"
    last_turn_usage = {}
    last_turn_truncated = False

    def chat(self, messages, tools):
        blob = json.dumps(messages, ensure_ascii=False)
        if "情景记忆" in blob and "当天对话" in blob:
            return {"role": "assistant", "content": "### 目标\n整理记忆功能"}
        if any(message.get("role") == "tool" for message in messages):
            return {"role": "assistant", "content": "完成"}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "generate_daily_episode",
                    "arguments": '{"date":"auto"}',
                },
            }],
        }


original_build_client = agent.build_client
agent.build_client = lambda *args, **kwargs: FakeClient()
try:
    generated = memory.generate_daily_episode("2026-11-12")
    check("generate episode", generated.startswith("generated episode 2026-11-12"), generated)
    check("auto marker", "benben:auto-summary:2026-11-12" in open(episode_path, encoding="utf-8").read())
    check("generate idempotent", memory.generate_daily_episode("2026-11-12").startswith("exists:"))
    check("generate empty", memory.generate_daily_episode("2021-01-01").startswith("empty:"))
    check("has auto summary", memory.has_auto_summary("2026-11-12") is True)

    # A: startup catch-up writes yesterday.
    yesterday = agent._memory_yesterday()
    yesterday_path = os.path.join(agent.SESSIONS_DIR, "011-20261112-bbbb2222.jsonl")
    with open(yesterday_path, "w", encoding="utf-8") as handle:
        for record in [
            {"_type": "meta", "created": yesterday + "T09:00:00", "workspace": base},
            {"role": "user", "content": "昨天在做记忆", "_ts": yesterday + "T10:00:00"},
            {"role": "assistant", "content": "继续", "_ts": yesterday + "T10:01:00"},
        ]:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    memory._catch_up_worker()
    check("catch-up episode", memory.has_auto_summary(yesterday) is True)

    # Built-in scheduled task uses memory-only tools and generates today.
    memory.daily_time = "00:01"
    today_session = agent.create_session("fake-model", base)
    today_session.log([
        {"role": "system", "content": "system"},
        {"role": "user", "content": "今天继续实现记忆"},
        {"role": "assistant", "content": "完成"},
    ])
    workspace = agent.Workspace(base)
    workspace.memory = memory
    scheduler = agent.SchedulerService(
        workspace,
        connection,
        "fake-model",
        base_dir=os.path.join(home, "schedules"),
        memory=memory,
    )
    workspace.scheduler = scheduler
    task = scheduler.ensure_memory_daily_task("00:01")
    check("builtin task", task.get("builtin") == "memory_daily", task)
    check("builtin read-only", task.get("read_only") is True and task.get("allow_shell") is False, task)
    scheduled_workspace = scheduler._make_workspace(task)
    check("builtin memory only", scheduled_workspace.memory_only is True)
    check(
        "builtin tools",
        [item["function"]["name"] for item in agent.tool_schemas(scheduled_workspace)]
        == [item["function"]["name"] for item in agent.MEMORY_SCHEMAS],
        agent.tool_schemas(scheduled_workspace),
    )
    run = scheduler.execute_task(task["id"], trigger="manual")
    check("builtin run", run["status"] == "ok", run)
    today_path = os.path.join(memory.episodes_dir, agent._memory_today() + ".md")
    check("builtin generated today", os.path.isfile(today_path), today_path)
    check("builtin auto marker", "benben:auto-summary" in open(today_path, encoding="utf-8").read())
finally:
    agent.build_client = original_build_client

# ---------- tool exposure ----------
workspace = agent.Workspace(base)
workspace.memory = memory
names = [item["function"]["name"] for item in agent.tool_schemas(workspace)]
for name in ("remember", "recall", "save_episode", "list_episodes", "read_episode", "generate_daily_episode"):
    check(f"tool {name}", name in names, names[-12:])
check("tool dispatch remember", "remembered" in workspace.dispatch(
    "remember", {"text": "通过工具写入的记忆", "tags": "测试"}
))
check("tool dispatch recall", "通过工具写入的记忆" in workspace.dispatch("recall", {"query": "工具写入"}))
try:
    workspace.dispatch("remember", {"text": "password=abcdef"})
    check("tool secret rejected", False)
except agent.ToolError as exc:
    check("tool secret rejected", "secret" in str(exc), str(exc))

read_only = agent.Workspace(base)
read_only.memory = memory
read_only.read_only = True
read_only_names = [item["function"]["name"] for item in agent.tool_schemas(read_only)]
check(
    "read-only memory tools",
    "recall" in read_only_names and "remember" not in read_only_names,
    read_only_names,
)
try:
    read_only.dispatch("save_episode", {"summary": "x"})
    check("read-only write blocked", False)
except agent.ToolError as exc:
    check("read-only write blocked", "read-only" in str(exc), str(exc))

# ---------- prompt ----------
memory_prompt = agent.build_system_prompt(
    base, {}, None, "interactive", memory_enabled=True
)
check("prompt memory tools", "remember" in memory_prompt and "save_episode" in memory_prompt)
check(
    "prompt memory off",
    "长期记忆与情景记忆" not in agent.build_system_prompt(base, {}, None, "interactive"),
)

# ---------- CLI helper ----------
buffer = io.StringIO()
with redirect_stdout(buffer):
    agent.run_memory_command(memory, "status")
check("cli memory status", "long-term:" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with redirect_stdout(buffer):
    agent.run_memory_command(memory, "search 简洁")
check("cli memory search", "MEMORY.md" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with redirect_stdout(buffer):
    agent.run_memory_command(None, "")
check("cli memory disabled", "--memory" in buffer.getvalue(), buffer.getvalue())

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
