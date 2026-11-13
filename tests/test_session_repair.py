from pathlib import Path
import importlib.util
import json
import os
import tempfile

HOME = tempfile.mkdtemp(prefix="session-repair-home-")
os.environ["HOME"] = HOME
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


os.makedirs(agent.SESSIONS_DIR, exist_ok=True)


def write_session(uuid, records, partial=None):
    path = os.path.join(agent.SESSIONS_DIR, f"001-20261113-{uuid}.jsonl")
    with open(path, "wb") as handle:
        for record in records:
            handle.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        if partial:
            handle.write(partial)
    return path


def load(uuid, system=True):
    messages = [{"role": "system", "content": "s"}] if system else []
    session = agent.load_session(uuid, messages)
    return messages, session


# ---------- trailing interrupted tool call ----------
path = write_session("aaaa1111", [
    {"_type": "meta", "version": 1, "uuid": "aaaa1111", "sequence": 1,
     "created": "2026-11-13T10:00:00", "model": "m", "workspace": "/tmp/ws"},
    {"role": "user", "content": "run both"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "c2", "type": "function", "function": {"name": "search", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "c1", "content": "ok"},
])
messages, session = load("aaaa1111")
tool_ids = [
    message.get("tool_call_id")
    for message in messages
    if message.get("role") == "tool"
]
check("trailing repair inserted missing", tool_ids == ["c1", "c2"], messages)
check("trailing repair content", "interrupted" in messages[-1]["content"], messages[-1])
persisted = open(path, encoding="utf-8").read()
check("trailing repair persisted", '"tool_call_id": "c2"' in persisted, persisted)
check("repair message persisted", "interrupted" in persisted, persisted)

messages2, _ = load("aaaa1111")
tool_ids2 = [
    message.get("tool_call_id")
    for message in messages2
    if message.get("role") == "tool"
]
check("trailing repair idempotent", tool_ids2 == ["c1", "c2"], tool_ids2)

# ---------- incomplete last line ----------
partial_path = write_session("bbbb2222", [
    {"_type": "meta", "version": 1, "uuid": "bbbb2222", "sequence": 2,
     "created": "2026-11-13T10:00:00", "model": "m", "workspace": "/tmp/ws"},
    {"role": "user", "content": "hello"},
], partial=b'{"role": "assistant"')
messages3, session3 = load("bbbb2222")
check("partial line skipped", [m.get("role") for m in messages3] == ["system", "user"], messages3)
check("partial line newline fixed", open(partial_path, "rb").read().endswith(b"\n"))
messages3.append({"role": "user", "content": "after repair"})
session3.log(messages3)
lines = open(partial_path, "rb").read().splitlines()
parsed = []
for line in lines:
    try:
        parsed.append(json.loads(line.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
check("new record parseable", any(
    record.get("role") == "user" and record.get("content") == "after repair"
    for record in parsed
), parsed)

# ---------- mid-history missing tool result ----------
mid_path = write_session("cccc3333", [
    {"_type": "meta", "version": 1, "uuid": "cccc3333", "sequence": 3,
     "created": "2026-11-13T10:00:00", "model": "m", "workspace": "/tmp/ws"},
    {"role": "user", "content": "first"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "m1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}},
    ]},
    {"role": "user", "content": "second"},
])
mid_messages, _ = load("cccc3333")
roles = [message.get("role") for message in mid_messages]
check(
    "mid-history repair order",
    roles == ["system", "user", "assistant", "tool", "user"],
    roles,
)
mid_again, _ = load("cccc3333")
check(
    "mid-history repair idempotent",
    [m.get("role") for m in mid_again] == ["system", "user", "assistant", "tool", "user"],
    mid_again,
)
check(
    "mid-history not appended to file",
    '"tool_call_id": "m1"' not in open(mid_path, encoding="utf-8").read(),
)

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
