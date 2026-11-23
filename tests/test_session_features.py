import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


base = tempfile.mkdtemp(prefix="session-features-")
agent.SESSION_DIR = base
agent.SESSIONS_DIR = os.path.join(base, "sessions")
agent.TRASH_DIR = os.path.join(agent.SESSIONS_DIR, "trash")
workspace = tempfile.mkdtemp(prefix="session-workspace-")
sessions = [agent.create_session("m", workspace) for _ in range(3)]
for session in sessions:
    session.log([
        {"role": "system", "content": "x"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ])
    time.sleep(0.02)

oldest = sessions[0]
pinned = agent.set_session_flags(oldest.uuid, pinned=True)
check("pin flag", pinned["pinned"] is True and pinned["archived"] is False, pinned)
archived = agent.set_session_flags(sessions[1].uuid, archived=True)
check("archive flag", archived["archived"] is True, archived)

rows = agent.session_summaries()
check("three sessions", len(rows) == 3, rows)
check("pinned first", rows[0]["uuid"] == oldest.uuid, rows)
by_uuid = {row["uuid"]: row for row in rows}
check("pinned row", by_uuid[oldest.uuid]["pinned"] is True, by_uuid[oldest.uuid])
check("archived row", by_uuid[sessions[1].uuid]["archived"] is True, by_uuid[sessions[1].uuid])
check("size present", all(int(row.get("size") or 0) > 0 for row in rows), rows)

lines = agent.format_session_list(rows, oldest.uuid)
check("flags in list", any("P-" in line for line in lines), lines)
check("archive in list", any("-A" in line for line in lines), lines)
check("size in list", any("B " in line for line in lines), lines)
check("current in list", any(line.startswith("*") for line in lines), lines)

unpinned = agent.set_session_flags(oldest.uuid, pinned=False)
check("unpin", unpinned["pinned"] is False, unpinned)
unarchived = agent.set_session_flags(sessions[1].uuid, archived=False)
check("unarchive", unarchived["archived"] is False, unarchived)

try:
    agent.set_session_flags("deadbeef", pinned=True)
    check("unknown flags rejected", False)
except agent.SessionError:
    check("unknown flags rejected", True)

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
