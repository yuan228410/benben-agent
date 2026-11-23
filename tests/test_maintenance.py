import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


base = tempfile.mkdtemp(prefix="maintenance-")
agent.SESSION_DIR = base
agent.SESSIONS_DIR = os.path.join(base, "sessions")
agent.TRASH_DIR = os.path.join(agent.SESSIONS_DIR, "trash")
workspace = tempfile.mkdtemp(prefix="maintenance-workspace-")

print("== trash and restore ==")
created = []
for index in range(3):
    session = agent.create_session("m", workspace)
    created.append(session)
    time.sleep(0.02)

first = created[0]
trashed_path = agent.trash_session(first.uuid)
check("trash moves file", not os.path.exists(first.path), first.path)
check("trash file exists", os.path.exists(trashed_path), trashed_path)
rows = agent.trashed_session_rows()
check("trash list", len(rows) == 1 and rows[0]["uuid"] == first.uuid, rows)
check("trash excluded from sessions", len(agent._session_files()) == 2)
restored_path = agent.restore_session(first.uuid)
check("restore moves file", os.path.exists(restored_path), restored_path)
check("restore clears trash", not agent.trashed_session_rows())
check("restore visible", len(agent._session_files()) == 3)
try:
    agent.restore_session(first.uuid)
    check("restore missing rejected", False)
except agent.SessionError:
    check("restore missing rejected", True)

print("== prune ==")
agent.trash_session(created[0].uuid)
agent.trash_session(created[1].uuid)
check("trash two", len(agent.trashed_session_rows()) == 2)
removed = agent.purge_trash()
check("purge trash", len(removed) == 2 and not agent.trashed_session_rows(), removed)

# Recreate a fresh set with known mtimes.
agent.purge_trash()
for path in agent._session_files():
    try:
        os.remove(path)
    except OSError:
        pass
prune_sessions = []
for index in range(4):
    session = agent.create_session("m", workspace)
    prune_sessions.append(session)
    stamp = time.time() - (10 - index) * 86400
    os.utime(session.path, (stamp, stamp))

dry = agent.prune_sessions(keep_count=2, dry_run=True)
check("prune dry count", len(dry) == 2, dry)
check("prune dry keeps files", len(agent._session_files()) == 4)
executed = agent.prune_sessions(keep_count=2, dry_run=False)
check("prune executed count", len(executed) == 2, executed)
check("prune moves files", len(agent._session_files()) == 2)
check("prune trash count", len(agent.trashed_session_rows()) == 2)
protected = agent.prune_sessions(keep_count=0, protect={prune_sessions[3].uuid}, dry_run=True)
check(
    "prune protects active",
    all(row["uuid"] != prune_sessions[3].uuid for row in protected),
    protected,
)

print("== check command ==")
home = tempfile.mkdtemp(prefix="maintenance-home-")
env = dict(
    os.environ,
    HOME=home,
    BENBEN_CONFIG=os.path.join(home, "config.json"),
)
check_run = subprocess.run(
    [
        sys.executable,
        AGENT,
        "--check",
        "--no-config",
        "--no-env",
        "--base-url",
        "http://127.0.0.1:1/v1",
        "--api-key",
        "k",
        "-m",
        "m",
        "--cwd",
        workspace,
    ],
    text=True,
    capture_output=True,
    env=env,
    timeout=40,
)
check("check exit", check_run.returncode == 0, check_run.stderr[-800:])
check("check output", "self-check" in check_run.stdout and "workspace:" in check_run.stdout, check_run.stdout[-1000:])
check("check skips network", "network probe skipped" in check_run.stdout, check_run.stdout[-1000:])


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "m"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
doctor_run = subprocess.run(
    [
        sys.executable,
        AGENT,
        "--doctor",
        "--no-config",
        "--no-env",
        "--base-url",
        f"http://127.0.0.1:{server.server_port}/v1",
        "--api-key",
        "k",
        "-m",
        "m",
        "--cwd",
        workspace,
    ],
    text=True,
    capture_output=True,
    env=env,
    timeout=40,
)
check("doctor exit", doctor_run.returncode == 0, doctor_run.stderr[-800:])
check("doctor probe", "model API probe: 1 model(s)" in doctor_run.stdout, doctor_run.stdout[-1000:])
server.shutdown()

print("== SIGTERM ==")
process = subprocess.Popen(
    [
        sys.executable,
        AGENT,
        "--no-config",
        "--no-env",
        "--base-url",
        "http://127.0.0.1:1/v1",
        "--api-key",
        "k",
        "-m",
        "m",
        "--cwd",
        workspace,
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env=env,
)
time.sleep(0.8)
process.terminate()
try:
    process.communicate(timeout=10)
except subprocess.TimeoutExpired:
    process.kill()
    process.communicate()
    check("sigterm exits", False, "timeout")
else:
    check("sigterm exits", process.returncode == 128 + signal.SIGTERM, process.returncode)

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
