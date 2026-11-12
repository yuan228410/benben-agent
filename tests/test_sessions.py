from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="session-home-")
os.environ["HOME"] = HOME
AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


os.makedirs(agent.SESSIONS_DIR, exist_ok=True)
path = os.path.join(agent.SESSIONS_DIR, "001-20261111-a1b2c3d4.jsonl")
records = [
    {"_type": "meta", "version": 1, "uuid": "a1b2c3d4", "sequence": 1,
     "created": "2026-11-11T10:00:00", "model": "m", "workspace": "/tmp/ws"},
    {"role": "user", "content": "第一个问题"},
    {"role": "assistant", "content": "回答一"},
    {"role": "user", "content": "第二个问题"},
    {"role": "assistant", "content": "回答二"},
    {"_type": "compact", "version": 1, "keep": 1, "tail_count": 2,
     "summary": "摘要内容"},
    {"role": "user", "content": "保留的问题"},
    {"role": "assistant", "content": "保留的回答"},
]
with open(path, "w", encoding="utf-8") as handle:
    for record in records:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

rows = agent.session_summaries()
check("one session", len(rows) == 1, rows)
row = rows[0]
check("uuid", row["uuid"] == "a1b2c3d4", row)
check("title from first user", row["title"] == "第一个问题", row)
check("count after compact", row["count"] == 3, row)
check("meta model", row["model"] == "m", row)
check("meta workspace", row["workspace"] == "/tmp/ws", row)

original_count = agent._session_count
calls = []


def counting(target):
    calls.append(target)
    return original_count(target)


agent._SESSION_SUMMARY_CACHE.clear()
agent._session_count = counting
agent.session_summaries()
agent.session_summaries()
check("cache avoids recount", len(calls) == 1, calls)

with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"role": "user", "content": "新消息"}, ensure_ascii=False) + "\n")
rows2 = agent.session_summaries()
check("cache invalidated", len(calls) == 2, calls)
check("new count", rows2[0]["count"] == 4, rows2[0])

new_session = agent.create_session("m", "/tmp/ws")
check("new sessions subdir", os.path.dirname(new_session.path) == agent.SESSIONS_DIR, new_session.path)
os.remove(new_session.path)

legacy_name = "002-20261111-b2c3d4e5.jsonl"
legacy_path = os.path.join(agent.SESSION_DIR, legacy_name)
with open(legacy_path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "_type": "meta", "version": 1, "uuid": "b2c3d4e5", "sequence": 2,
        "created": "2026-11-11T10:00:00", "model": "m", "workspace": "/tmp/ws",
    }, ensure_ascii=False) + "\n")
moved = agent.migrate_legacy_sessions()
check("legacy session moved", moved == 1, moved)
check("legacy source removed", not os.path.exists(legacy_path))
check("legacy target exists", os.path.exists(os.path.join(agent.SESSIONS_DIR, legacy_name)))

# ---------- command helpers ----------
rows = agent.session_summaries()
check("two sessions listed", len(rows) == 2, rows)
lines = agent.format_session_list(rows, "a1b2c3d4")
check("list has header", lines[0].startswith("  #"), lines)
check("list marks current", any(line.startswith("*") and "a1b2c3d4" in line for line in lines), lines)
check("resolve by number", agent.resolve_session_ref("1", rows)["uuid"] == rows[0]["uuid"])
check("resolve by uuid", agent.resolve_session_ref("a1b2c3d4", rows)["uuid"] == "a1b2c3d4")
try:
    agent.resolve_session_ref("99", rows)
    check("resolve out of range", False)
except agent.SessionError:
    check("resolve out of range", True)

# ---------- titles ----------
session_obj = agent.Session(path, "a1b2c3d4", 1)
check("set title", session_obj.set_title("自定义标题") == "自定义标题")
check("title marker read", agent._session_title(path) == "自定义标题")
rows = agent.session_summaries()
row = next(item for item in rows if item["uuid"] == "a1b2c3d4")
check("summary custom title", row["title"] == "自定义标题", row)
agent.set_session_title("b2c3d4e5", "另一个标题")
rows = agent.session_summaries()
row = next(item for item in rows if item["uuid"] == "b2c3d4e5")
check("title by uuid", row["title"] == "另一个标题", row)
check("title marker latest wins", session_obj.set_title("第二个标题") == "第二个标题" and agent._session_title(path) == "第二个标题")
try:
    session_obj.set_title("  ")
    check("empty title rejected", False)
except agent.SessionError:
    check("empty title rejected", True)
loaded_title = []
agent.load_session("a1b2c3d4", loaded_title)
check(
    "title marker not a message",
    bool(loaded_title) and all(item.get("role") and not item.get("_type") for item in loaded_title),
    loaded_title,
)

# ---------- CLI /session ----------
env = dict(os.environ, HOME=HOME, BENBEN_CONFIG=os.path.join(HOME, "config.json"))
export_path = os.path.join(HOME, "session-export.md")
result = subprocess.run(
    [sys.executable, AGENT, "--shell", "auto"],
    input=(
        "/session\n"
        "/session use 1\n"
        "/session rename 我的会话\n"
        "/session info\n"
        f"/session export 2 --tool --thinking {export_path}\n"
        "/session delete 2\n"
        "y\n"
        "/session\n"
        "/exit\n"
    ),
    text=True,
    capture_output=True,
    env=env,
    timeout=30,
)
check("cli session exit", result.returncode == 0, result.stderr[-500:])
check("cli session list", "sessions in" in result.stdout, result.stdout[-800:])
check("cli session use", "resumed session" in result.stdout, result.stdout[-800:])
check("cli session rename", "session title updated: 我的会话" in result.stdout, result.stdout[-800:])
check("cli session info", "current session" in result.stdout and "我的会话" in result.stdout, result.stdout[-800:])
check("cli session export", "exported session" in result.stdout and os.path.exists(export_path), result.stdout[-800:])
if os.path.exists(export_path):
    exported_text = open(export_path, encoding="utf-8").read()
    check("cli export markdown", "# 会话记录" in exported_text and "## 用户" in exported_text, exported_text[:300])
check("cli session delete", "deleted session" in result.stdout, result.stdout[-800:])
check("cli delete removed file", not os.path.exists(path))
check("cli kept other file", os.path.exists(os.path.join(agent.SESSIONS_DIR, legacy_name)))

shutil.rmtree(HOME, ignore_errors=True)
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
