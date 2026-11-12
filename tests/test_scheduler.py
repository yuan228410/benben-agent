import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta
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


home = tempfile.mkdtemp(prefix="schedule-home-")
agent.SESSIONS_DIR = os.path.join(home, "sessions")
base = tempfile.mkdtemp(prefix="schedule-ws-")
schedule_dir = os.path.join(home, "schedules")
service_dir = os.path.join(home, "service-schedules")

# ---------- parsing ----------
now = datetime(2026, 11, 12, 8, 0, 0)
parsed = agent.parse_schedule_when("every 30m", now)
check("parse every", parsed["type"] == "every" and parsed["interval"] == 1800, parsed)
check("parse every next", parsed["next"] == "2026-11-12T08:30:00", parsed)
parsed = agent.parse_schedule_when("daily 09:00", now)
check("parse daily", parsed["type"] == "daily" and parsed["next"] == "2026-11-12T09:00:00", parsed)
parsed = agent.parse_schedule_when("daily 07:30", now)
check("parse daily rolls over", parsed["next"] == "2026-11-13T07:30:00", parsed)
parsed = agent.parse_schedule_when("once 2026-11-12 15:00", now)
check("parse once", parsed["type"] == "once" and parsed["next"] == "2026-11-12T15:00:00", parsed)
for bad in ("every", "every 0s", "daily 25:00", "once 2020-01-01T00:00", "weekly"):
    try:
        agent.parse_schedule_when(bad, now)
        check(f"reject {bad}", False)
    except ValueError:
        check(f"reject {bad}", True)

# ---------- prompt ----------
enabled_prompt = agent.build_system_prompt(
    base, {}, None, "interactive", scheduler_enabled=True
)
check("prompt has schedule tools", "create_schedule" in enabled_prompt and "every 30s" in enabled_prompt)
check(
    "prompt schedule disabled",
    "create_schedule" not in agent.build_system_prompt(base, {}, None, "interactive"),
)
run_prompt = agent.build_system_prompt(
    base, {}, None, "scheduled", scheduler_enabled=False, scheduled_run=True
)
check("scheduled prompt note", "本次运行由定时任务触发" in run_prompt and "create_schedule" not in run_prompt)

# ---------- storage ----------
tasks = [{"id": "abcd1234", "name": "demo", "prompt": "x", "when": "every 1h"}]
path = agent.save_schedule_tasks(tasks, schedule_dir)
check("tasks saved", os.path.exists(path), path)
check("tasks private", oct(os.stat(path).st_mode & 0o777) == "0o600", oct(os.stat(path).st_mode & 0o777))
check("dir private", oct(os.stat(schedule_dir).st_mode & 0o777) == "0o700")
check("tasks loaded", agent.load_schedule_tasks(schedule_dir)[0]["id"] == "abcd1234")
record = {"id": "abcd1234", "status": "ok", "output": "done"}
run_path = agent.append_schedule_run("abcd1234", record, schedule_dir)
check("run log private", oct(os.stat(run_path).st_mode & 0o777) == "0o600")
check("runs loaded", agent.read_schedule_runs("abcd1234", 5, schedule_dir)[0]["output"] == "done")
agent.append_schedule_run("abcd1234", {"id": "abcd1234", "status": "error"}, schedule_dir)
check("runs limited", len(agent.read_schedule_runs("abcd1234", 1, schedule_dir)) == 1)

# ---------- service ----------
workspace = agent.Workspace(base)
connection = agent.Connection(
    "http://127.0.0.1:1/v1",
    "fake-model",
    "openai-chat-completions",
    "sk-secret-value",
    name="demo-connection",
)
service = agent.SchedulerService(
    workspace,
    connection,
    "fake-model",
    base_dir=service_dir,
)
workspace.scheduler = service
created = service.create_task(
    "磁盘检查",
    "检查磁盘使用率",
    "every 30m",
    read_only=True,
)
check("create result id", "定时任务已创建" in created and "只读" in created, created)
task = service.list_tasks()[0]
check("task fields", task["read_only"] is True and task["model"] == "fake-model", task)
check("task connection snapshot", task["connection"]["api_key"] == "sk-secret-value")
check("task next run", bool(task["next_run"]))
public = agent.public_schedule_task(task)
check("public task hides key", "sk-secret-value" not in json.dumps(public, ensure_ascii=False), public)
listed = service.list_text()
check("list text hides key", "sk-secret-value" not in listed and task["id"] in listed, listed)
check("no runs yet", "还没有运行记录" in service.history_text(task["id"]))
check("pause task", "已暂停" in service.set_enabled(task["id"], False))
check("resume task", "已启用" in service.set_enabled(task["id"], True))

# ---------- tool exposure ----------
normal_names = [item["function"]["name"] for item in agent.tool_schemas(workspace)]
check("schedule tools exposed", "create_schedule" in normal_names and "list_schedules" in normal_names, normal_names[-10:])
scheduled_workspace = service._make_workspace(task)
scheduled_names = [item["function"]["name"] for item in agent.tool_schemas(scheduled_workspace)]
check(
    "read-only scheduled tools",
    scheduled_names == ["list_dir", "read_file", "search", "glob"],
    scheduled_names,
)
check("no schedule tools inside scheduled run", "create_schedule" not in scheduled_names)
try:
    scheduled_workspace.dispatch("write_file", {"path": "x.txt", "content": "x"})
    check("read-only dispatch blocked", False)
except agent.ToolError as exc:
    check("read-only dispatch blocked", "read-only" in str(exc), str(exc))
try:
    scheduled_workspace.dispatch("create_schedule", {"name": "x", "prompt": "y", "when": "every 1h"})
    check("schedule dispatch blocked in run", False)
except agent.ToolError as exc:
    check("schedule dispatch blocked in run", "disabled" in str(exc), str(exc))

full_task = service.list_tasks()[0]
full_task["read_only"] = False
full_workspace = service._make_workspace(full_task)
full_names = [item["function"]["name"] for item in agent.tool_schemas(full_workspace)]
check("full scheduled tools", "write_file" in full_names and "run_command" in full_names, full_names)
check("full scheduled no scheduler", "create_schedule" not in full_names, full_names)


# ---------- execution ----------
class FakeClient:
    model = "fake-model"
    last_reasoning_ignored = False
    last_usage = {}
    last_finish_reason = "stop"
    last_turn_usage = {}
    last_turn_truncated = False

    def chat(self, messages, tools):
        self.last_usage = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
        return {"role": "assistant", "content": "检查完成：磁盘使用率 42%"}


original_build_client = agent.build_client
agent.build_client = lambda *args, **kwargs: FakeClient()
try:
    result = service.execute_task(task["id"], trigger="manual")
finally:
    agent.build_client = original_build_client
check("run ok", result["status"] == "ok", result)
check("run output", "磁盘使用率" in result["output"], result)
check(
    "run session saved",
    bool(result["session"]) and bool(agent._find_session_paths(result["session"])),
    result,
)
history = service.history_text(task["id"])
check("run history", "检查完成" in history and "manual" in history, history)
updated = service.list_tasks()[0]
check("run count", updated["run_count"] == 1 and updated["last_status"] == "ok", updated)
notes = service.drain_notifications()
check("notification queued", len(notes) == 1 and notes[0]["status"] == "ok", notes)
check("notification text", "定时任务" not in agent.format_schedule_notification(notes[0]))
check("notification body", "检查完成" in agent.format_schedule_notification(notes[0]))

# ---------- catch-up ----------
service.create_task("周期任务", "do", "every 1h")
periodic = [item for item in service.list_tasks() if item["name"] == "周期任务"][0]
with service._lock:
    for item in service._tasks:
        if item["id"] == periodic["id"]:
            item["next_run"] = (
                datetime.now() - timedelta(hours=3)
            ).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
    service._save()
service._tick()
check("catch-up queued once", service._queued == {periodic["id"]}, service._queued)
check("catch-up advanced", service.list_tasks()[1]["next_run"] > datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

service.create_task("一次性任务", "do once", "once 2030-01-01T10:00")
once_task = [item for item in service.list_tasks() if item["name"] == "一次性任务"][0]
with service._lock:
    for item in service._tasks:
        if item["id"] == once_task["id"]:
            item["next_run"] = (
                datetime.now() - timedelta(minutes=5)
            ).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
    service._save()
service._tick()
once_after = [item for item in service.list_tasks() if item["id"] == once_task["id"]][0]
check("once disables after catch-up", once_after["enabled"] is False and once_after["next_run"] is None, once_after)

# ---------- delete ----------
message = service.delete_task(task["id"])
check("delete task", "已删除" in message and all(item["id"] != task["id"] for item in service.list_tasks()))
check("delete run log", not os.path.exists(agent.schedule_run_path(task["id"], service_dir)))

# ---------- CLI helper ----------
buffer = io.StringIO()
with redirect_stdout(buffer):
    agent.run_schedule_command(None, "")
check("disabled schedule message", "--schedule" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with redirect_stdout(buffer):
    agent.run_schedule_command(service, "list")
check("cli schedule list", "周期任务" in buffer.getvalue(), buffer.getvalue())

# ---------- CLI integration ----------
cli_home = tempfile.mkdtemp(prefix="schedule-cli-")
cli_dir = os.path.join(cli_home, ".benben-agent", "schedules")
os.makedirs(cli_dir, exist_ok=True)
with open(os.path.join(cli_dir, "tasks.json"), "w", encoding="utf-8") as handle:
    json.dump({
        "version": 1,
        "tasks": [{
            "id": "bbbb2222",
            "name": "CLI 任务",
            "prompt": "检查",
            "when": "every 2h",
            "type": "every",
            "next_run": "2030-01-01T10:00:00",
            "enabled": True,
            "read_only": False,
            "connection": {
                "name": None,
                "base_url": "http://127.0.0.1:1/v1",
                "api_format": "openai-chat-completions",
                "api_key": "k",
            },
            "model": "m",
            "workspace": cli_home,
            "shell": "auto",
            "allow_shell": True,
            "skills": {},
            "allow_skill_scripts": False,
            "max_steps": 5,
            "created": "2026-11-12T10:00:00",
            "run_count": 0,
        }],
    }, handle, ensure_ascii=False)
env = dict(os.environ, HOME=cli_home, BENBEN_CONFIG=os.path.join(cli_home, "config.json"))
result = subprocess.run(
    [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "benben-agent.py"),
        "--schedule",
        "--no-config",
        "--no-env",
        "--base-url",
        "http://127.0.0.1:1/v1",
        "--api-key",
        "k",
        "-m",
        "m",
        "--shell",
        "auto",
    ],
    input="/schedule\n/schedule off bbbb2222\n/schedule on bbbb2222\n/schedule history bbbb2222\n/schedule delete bbbb2222\n/exit\n",
    text=True,
    capture_output=True,
    env=env,
    timeout=40,
)
check("cli schedule start", result.returncode == 0, result.stderr[-800:])
check("cli schedule enabled line", "[schedule] enabled" in result.stderr, result.stderr[-800:])
check("cli schedule list output", "CLI 任务" in result.stdout and "bbbb2222" in result.stdout, result.stdout[-1200:])
check("cli schedule pause", "已暂停" in result.stdout, result.stdout[-1200:])
check("cli schedule resume", "已启用" in result.stdout, result.stdout[-1200:])
check("cli schedule history", "还没有运行记录" in result.stdout, result.stdout[-1200:])
check("cli schedule delete", "已删除" in result.stdout, result.stdout[-1200:])

# Disabled mode still reports the hint.
result = subprocess.run(
    [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "benben-agent.py"),
        "--no-config",
        "--no-env",
        "--base-url",
        "http://127.0.0.1:1/v1",
        "--api-key",
        "k",
        "-m",
        "m",
        "--shell",
        "auto",
    ],
    input="/schedule\n/exit\n",
    text=True,
    capture_output=True,
    env=dict(os.environ, HOME=tempfile.mkdtemp(prefix="schedule-cli-off-")),
    timeout=40,
)
check("cli schedule disabled", "scheduled tasks are disabled" in result.stdout, result.stdout[-800:])

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
