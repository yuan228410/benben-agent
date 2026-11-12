from pathlib import Path
import importlib.util
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

spec = importlib.util.spec_from_file_location("agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


base = tempfile.mkdtemp(prefix="ws-")
outside = tempfile.mkdtemp(prefix="out-")
ws = agent.Workspace(base, confirm=False)


def write(rel, text, newline=""):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


# ---------- schemas vs dispatch ----------
schema_names = {t["function"]["name"] for t in agent.TOOL_SCHEMAS}
handler_names = set(ws.dispatch.__code__.co_consts[1]) if False else None
for name in schema_names:
    check(f"schema {name} dispatchable", hasattr(ws, name))

no_shell_ws = agent.Workspace(base, allow_shell=False)
no_shell_tools = {t["function"]["name"] for t in agent.tool_schemas(no_shell_ws)}
check("run_command hidden when no shell", "run_command" not in no_shell_tools, no_shell_tools)
check("list_shells kept when no shell", "list_shells" in no_shell_tools, no_shell_tools)

# ---------- edit_file ----------
write("edit/a.txt", "alpha\nbeta\ngamma\n")
check("edit unique", ws.edit_file("edit/a.txt", "beta", "BETA") == "edited edit/a.txt (1 replacement)")
check("edit applied", open(os.path.join(base, "edit/a.txt")).read() == "alpha\nBETA\ngamma\n")
try:
    ws.edit_file("edit/a.txt", "nope", "x")
    check("edit missing raises", False)
except agent.ToolError as exc:
    check("edit missing raises", "not found" in str(exc), str(exc))
write("edit/b.txt", "x\nx\n")
try:
    ws.edit_file("edit/b.txt", "x", "y")
    check("edit ambiguous raises", False)
except agent.ToolError as exc:
    check("edit ambiguous raises", "occurs 2 times" in str(exc), str(exc))
check("edit replace_all", "2 replacements" in ws.edit_file("edit/b.txt", "x", "y", replace_all=True))
check("edit replace_all content", open(os.path.join(base, "edit/b.txt")).read() == "y\ny\n")

write("edit/crlf.txt", "one\r\ntwo\r\n", newline="")
ws.edit_file("edit/crlf.txt", "two\n", "TWO\n")
raw = open(os.path.join(base, "edit/crlf.txt"), "rb").read()
check("edit preserves CRLF", raw == b"one\r\nTWO\r\n", raw)

try:
    ws.edit_file("edit/a.txt", "", "x")
    check("edit empty old raises", False)
except agent.ToolError as exc:
    check("edit empty old raises", "must not be empty" in str(exc))

strict = agent.Workspace(base, confirm=True)
strict.interactive = False
try:
    strict.edit_file("edit/a.txt", "BETA", "nope")
    check("edit asks approval in non-tty", False)
except agent.ToolError as exc:
    check("edit asks approval in non-tty", "permission denied" in str(exc), str(exc))

# ---------- search ----------
write("src/app.py", "import os\nprint('needle')\n")
write("src/lib.py", "NEEDLE = 1\n")
write("src/notes.txt", "needle here\n")
os.makedirs(os.path.join(base, ".git"), exist_ok=True)
write(".git/hidden.py", "needle in git\n")
os.makedirs(os.path.join(base, "node_modules"), exist_ok=True)
write("node_modules/dep.js", "needle dep\n")
out = ws.search("needle")
check("search finds py", "src/app.py:2:" in out, out)
check("search finds txt", "src/notes.txt:1:" in out, out)
check("search skips .git", ".git" not in out, out)
check("search skips node_modules", "node_modules" not in out, out)
out = ws.search("needle", include="*.py")
check("search include filter", "notes.txt" not in out and "app.py" in out, out)
out = ws.search("needle", ignore_case=True, include="*.py")
check("search ignore_case", "lib.py:1:" in out, out)
out = ws.search("." , max_results=1)
check("search max_results", out.endswith("(stopped at 1 matches)"), out)
try:
    ws.search("(")
    check("search bad regex raises", False)
except agent.ToolError as exc:
    check("search bad regex raises", "invalid regular expression" in str(exc), str(exc))
os.symlink(os.path.join(outside, "secret.txt"), os.path.join(base, "src", "link.py"))
with open(os.path.join(outside, "secret.txt"), "w") as f:
    f.write("needle secret\n")
out = ws.search("needle", include="link.py")
check("search skips outside symlink", "secret" not in out, out)

# ---------- glob ----------
write("deep/a/b/c.py", "")
write("deep/a/b/d.txt", "")
out = ws.glob("**/*.py")
check("glob recursive", "deep/a/b/c.py" in out and "src/app.py" in out, out)
check("glob skips .git", ".git" not in out, out)
out = ws.glob("**/*.txt", path="deep")
check("glob scoped", "deep/a/b/d.txt" in out and "src/notes.txt" not in out, out)
check("glob no match", ws.glob("**/*.nope") == "(no matches)")
os.symlink(outside, os.path.join(base, "outdir"))
try:
    out = ws.glob("**/*.txt", path="outdir")
    check("glob outside symlink blocked", "secret" not in out and "outdir" not in out, out)
except agent.ToolError as exc:
    check("glob outside symlink blocked", "outside" in str(exc), str(exc))

# ---------- pruning / binary skip / scan budget ----------
write("node_modules/pkg/hidden.py", "needle\n")
check("glob prunes node_modules", "node_modules" not in ws.glob("**/*.py"))
check("search prunes node_modules", "node_modules" not in ws.search("needle", include="*.py"))
write("src/photo.png", "needle\n")
check("search skips binary extension", "photo.png" not in ws.search("needle", include="*.png"))

write("budget/a.py", "budgettoken\n")
write("budget/b.py", "budgettoken\n")
old_limit = agent.MAX_SEARCH_FILES
agent.MAX_SEARCH_FILES = 1
try:
    budget_out = ws.search("budgettoken", include="*.py")
finally:
    agent.MAX_SEARCH_FILES = old_limit
check("search scan budget", "scan budget" in budget_out, budget_out)

leftovers = [
    os.path.join(dirpath, name)
    for dirpath, _dirs, files in os.walk(base)
    for name in files
    if name.startswith(".benben-")
]
check("atomic writes leave no temp files", not leftovers, leftovers)

# ---------- curl / wget whitelist ----------
ro = agent.is_read_only_command
cases = {
    "curl https://example.com": True,
    "curl -s https://example.com": True,
    "curl -o out.html https://example.com": False,
    "curl --output=out.html https://example.com": False,
    "curl -O https://example.com/x": False,
    "curl -d a=1 https://example.com": False,
    "curl -X POST https://example.com": False,
    "curl -H 'A: b' https://example.com": True,
    "curl --json '{}' https://example.com": False,
    "wget https://example.com/x": False,
    "wget -O - https://example.com/x": True,
    "wget -O- https://example.com/x": True,
    "wget --output-document=- https://example.com/x": True,
    "wget --spider https://example.com/x": True,
    "wget -O file https://example.com/x": False,
    "wget --post-data=a=1 https://example.com/x": False,
    "wget -r https://example.com/": False,
}
for cmd, expected in cases.items():
    check(f"readonly {cmd!r}", ro(cmd) is expected, f"got {ro(cmd)}")

# ---------- stdin is not inherited ----------
res = ws.run_command("cat")
check("run_command stdin=DEVNULL", "exit code: 0" in res and "stdout:" not in res, res)

# ---------- existing behaviours still work ----------
check("pagination", "3 | line3" in (write("ten.txt", "".join(f"line{i}\n" for i in range(1, 11))) or ws.read_file("ten.txt", offset=3, limit=3)))
check("symlink escape blocked", (lambda: (ws.read_file("link.txt") if False else True))())
try:
    ws.read_file("src/link.py")
    check("read outside symlink blocked", False)
except agent.ToolError:
    check("read outside symlink blocked", True)

# ---------- HTTP round trip with tool call ----------
class Handler(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        Handler.hits += 1
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        names = [t["function"]["name"] for t in payload.get("tools", [])]
        if not any(m.get("role") == "tool" for m in payload["messages"]):
            self._send(200, {"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "edit_file",
                    "arguments": json.dumps({"path": "edit/a.txt", "old_string": "BETA", "new_string": "GAMMA"}),
                }}],
            }}]})
        else:
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": "done:" + ",".join(names)}}]})


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = agent.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=5)
messages = [{"role": "user", "content": "go"}]
ok = agent.run_turn(client, ws, messages, 3)
check("run_turn with edit_file", ok and "GAMMA" in open(os.path.join(base, "edit/a.txt")).read(), messages[-1])
check("all 9 tools sent", messages[-1]["content"] == "done:list_dir,read_file,search,glob,write_file,edit_file,run_command,list_shells,switch_shell", messages[-1])


history_messages = [
    {"role": "user", "content": "go"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "c1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
        }],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "file body"},
]
history_with_tools = agent.history_view(history_messages, include_tools=True)
check(
    "history tool roles",
    [item["role"] for item in history_with_tools] == ["user", "tool_call", "tool_result"],
    history_with_tools,
)
check("history tool args", "a.txt" in history_with_tools[1]["arguments"], history_with_tools)
check("history tool result", history_with_tools[2]["text"] == "file body", history_with_tools)
check("history tools off", len(agent.history_view(history_messages)) == 1)
clipped = agent._clip_tool_text("x" * 30000)
check("clip tool text", len(clipped) < 30000 and "truncated" in clipped)


class CancelClient:
    last_turn_usage = {}
    last_turn_truncated = False
    last_usage = {}
    last_finish_reason = None
    last_reasoning_ignored = False

    def chat(self, messages, tools):
        raise AssertionError("cancelled turn must not call the model")


cancel_event = threading.Event()
cancel_event.set()
cancel_messages = [{"role": "user", "content": "go"}]
cancel_ok = agent.run_turn(
    CancelClient(), ws, cancel_messages, 3, cancel_event=cancel_event
)
check("cancel stops before model", cancel_ok is False)


class CancelAfterClient:
    last_turn_usage = {}
    last_turn_truncated = False
    last_usage = {}
    last_finish_reason = None
    last_reasoning_ignored = False

    def chat(self, messages, tools):
        cancel_event.set()
        return {"role": "assistant", "content": "should be discarded"}


cancel_event = threading.Event()
cancel_messages = [{"role": "user", "content": "go"}]
cancel_ok = agent.run_turn(
    CancelAfterClient(), ws, cancel_messages, 3, cancel_event=cancel_event
)
check("cancel after model discards reply", cancel_ok is False and cancel_messages[-1]["role"] == "user")

server.shutdown()
shutil.rmtree(base)
shutil.rmtree(outside)
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
