from pathlib import Path
import importlib.util
import json
import os
import shutil
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


os.makedirs(agent.SESSION_DIR, exist_ok=True)
path = os.path.join(agent.SESSION_DIR, "001-20261111-a1b2c3d4.jsonl")
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

shutil.rmtree(HOME, ignore_errors=True)
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
