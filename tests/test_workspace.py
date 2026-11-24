import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


print("== normalization ==")
base = tempfile.mkdtemp(prefix="workspace-base-")
nested = os.path.join(base, "project")
os.makedirs(nested)
check("absolute workspace", agent.normalize_workspace(nested) == os.path.realpath(nested))
check(
    "relative workspace",
    agent.normalize_workspace("project", base) == os.path.realpath(nested),
)
missing = os.path.join(base, "missing")
try:
    agent.normalize_workspace(missing)
    check("missing workspace rejected", False)
except agent.WorkspaceError:
    check("missing workspace rejected", True)
file_path = os.path.join(base, "file.txt")
open(file_path, "w").close()
try:
    agent.normalize_workspace(file_path)
    check("file workspace rejected", False)
except agent.WorkspaceError:
    check("file workspace rejected", True)

print("== switch workspace ==")
current = tempfile.mkdtemp(prefix="workspace-current-")
target = tempfile.mkdtemp(prefix="workspace-target-")
workspace = agent.Workspace(current)
workspace.memory = SimpleNamespace(workspace=current)
args = SimpleNamespace(cwd=current, skills=False, only_own_skills=False)
switched = agent.switch_workspace(workspace, target, args=args)
check("switch root", switched == os.path.realpath(target), switched)
check("switch workspace root", workspace.root == os.path.realpath(target), workspace.root)
check("switch args cwd", args.cwd == os.path.realpath(target), args.cwd)
check(
    "switch memory workspace",
    workspace.memory.workspace == os.path.realpath(target),
    workspace.memory.workspace,
)


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{server.server_port}/v1"

print("== CLI session workspace ==")
home = tempfile.mkdtemp(prefix="workspace-cli-")
workspace_a = tempfile.mkdtemp(prefix="workspace-a-")
workspace_b = tempfile.mkdtemp(prefix="workspace-b-")
env = dict(
    os.environ,
    HOME=home,
    BENBEN_CONFIG=os.path.join(home, "config.json"),
)
base_args = [
    sys.executable,
    AGENT,
    "--no-config",
    "--no-env",
    "--base-url",
    base_url,
    "--api-key",
    "k",
    "-m",
    "m",
    "--no-stream",
    "--yes",
    "--cwd",
    home,
]
run = subprocess.run(
    base_args,
    input=(
        f"/session new {workspace_a}\n"
        "first workspace\n"
        f"/session new {workspace_b}\n"
        "second workspace\n"
        "/workspace\n"
        "/exit\n"
    ),
    text=True,
    encoding="utf-8",
    errors="replace",
    capture_output=True,
    env=env,
    timeout=60,
)
check("cli workspace exit", run.returncode == 0, run.stderr[-800:])
check(
    "cli session new workspace",
    f"workspace: {os.path.realpath(workspace_a)}" in run.stdout,
    run.stdout[-1500:],
)
check(
    "cli workspace status",
    f"workspace: {os.path.realpath(workspace_b)}" in run.stdout,
    run.stdout[-1500:],
)

sessions_dir = os.path.join(home, ".benben-agent", "sessions")
session_files = sorted(Path(sessions_dir).glob("*.jsonl"))
metas = []
uuids = {}
for path in session_files:
    with open(path, encoding="utf-8") as handle:
        meta = json.loads(handle.readline())
    metas.append(meta)
    uuids[meta.get("workspace")] = meta.get("uuid")
check("cli two sessions", len(session_files) == 2, [str(p) for p in session_files])
check(
    "cli meta workspace a",
    os.path.realpath(workspace_a) in uuids,
    metas,
)
check(
    "cli meta workspace b",
    os.path.realpath(workspace_b) in uuids,
    metas,
)

print("== CLI resume workspace ==")
uuid_a = uuids[os.path.realpath(workspace_a)]
resume = subprocess.run(
    base_args + ["-c", uuid_a],
    input="/status\n/exit\n",
    text=True,
    encoding="utf-8",
    errors="replace",
    capture_output=True,
    env=env,
    timeout=60,
)
check("cli resume exit", resume.returncode == 0, resume.stderr[-800:])
check(
    "cli resume switches workspace",
    f"workspace: {os.path.realpath(workspace_a)}" in resume.stdout,
    resume.stdout[-1500:],
)

print("== CLI workspace use/list ==")
run = subprocess.run(
    base_args,
    input=(
        "/workspace\n"
        "/workspace use 1\n"
        "hello\n"
        "/exit\n"
    ),
    text=True,
    encoding="utf-8",
    errors="replace",
    capture_output=True,
    env=env,
    timeout=60,
)
check("cli workspace list exit", run.returncode == 0, run.stderr[-800:])
check(
    "cli workspace list output",
    "recent workspaces:" in run.stdout,
    run.stdout[-1500:],
)

server.shutdown()
if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
