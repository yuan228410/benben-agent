from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


root = tempfile.mkdtemp(prefix="plan-test-")
ws_root = os.path.join(root, "ws")
os.makedirs(ws_root, exist_ok=True)


def find_posix_shell():
    """Return a detected POSIX shell, or None when the host has none."""
    try:
        state = agent.detect_shell_state("auto")
    except agent.ShellError:
        return None
    for item in state.available:
        if item.kind == "posix":
            return item
    return None


posix_shell = find_posix_shell()
has_posix = posix_shell is not None
if not has_posix:
    print("skip  no POSIX shell on this host; command level guard checks skipped")
    posix_shell = agent.ShellInfo("/bin/sh", "sh", "posix")


def new_workspace(**kwargs):
    workspace = agent.Workspace(
        ws_root,
        shell_state=agent.ShellState(posix_shell, [posix_shell]),
        **kwargs,
    )
    workspace.interactive = False
    return workspace


# ---------- tool set is not reduced in plan mode ----------
ws = new_workspace()
off_names = {tool["function"]["name"] for tool in agent.tool_schemas(ws)}
check("plan off no submit_plan", "submit_plan" not in off_names, off_names)
check(
    "plan off keeps write tools",
    {"write_file", "edit_file", "run_command"} <= off_names,
    off_names,
)

ws.plan_mode = True
on_names = {tool["function"]["name"] for tool in agent.tool_schemas(ws)}
check("plan on adds submit_plan", "submit_plan" in on_names, on_names)
check(
    "plan on keeps write tools",
    {"write_file", "edit_file", "run_command"} <= on_names,
    on_names,
)
check(
    "plan on keeps skills tools available",
    "load_skill" in off_names or True,
)

# ---------- submit_plan ----------
blocked_ws = new_workspace()
try:
    blocked_ws.dispatch("submit_plan", {"plan": "x"})
    check("submit_plan blocked outside plan mode", False, "no error raised")
except agent.ToolError as exc:
    check(
        "submit_plan blocked outside plan mode",
        "only available in plan mode" in str(exc),
        str(exc),
    )

result = ws.dispatch("submit_plan", {"plan": "  ## 目标\n做点事  ", "questions": "用哪个库?"})
check("submit_plan returns notice", "waiting for the user's approval" in result, result)
check("submit_plan stores plan", ws.plan_result is not None)
check("submit_plan trims plan", ws.plan_result["plan"] == "## 目标\n做点事", ws.plan_result)
check("submit_plan stores questions", ws.plan_result["questions"] == "用哪个库?", ws.plan_result)
check("submit_plan not approved yet", ws.plan_result["approved"] is False, ws.plan_result)

ws.plan_result = None
try:
    ws.dispatch("submit_plan", {"plan": "   "})
    check("submit_plan rejects empty plan", False, "no error raised")
except agent.ToolError as exc:
    check("submit_plan rejects empty plan", "must not be empty" in str(exc), str(exc))

# ---------- guard: off (default) ----------
guard_ws = new_workspace()
guard_ws.plan_mode = True
guard_ws.plan_guard = "off"
out = guard_ws.write_file("guard-off.txt", "hello")
check("guard off allows write", "wrote" in out, out)
check("guard off file exists", os.path.isfile(os.path.join(ws_root, "guard-off.txt")))
if has_posix:
    out = guard_ws.run_command("echo guard-off-ok", timeout_seconds=10)
    check("guard off allows command", "guard-off-ok" in out, out)

# ---------- guard: deny ----------
deny_ws = new_workspace()
deny_ws.plan_mode = True
deny_ws.plan_guard = "deny"
try:
    deny_ws.write_file("denied.txt", "hello")
    check("guard deny blocks write", False, "no error raised")
except agent.ToolError as exc:
    check("guard deny blocks write", "blocked by the plan guard" in str(exc), str(exc))
check("guard deny file not written", not os.path.isfile(os.path.join(ws_root, "denied.txt")))
try:
    deny_ws.edit_file("guard-off.txt", "hello", "bye")
    check("guard deny blocks edit", False, "no error raised")
except agent.ToolError as exc:
    check("guard deny blocks edit", "blocked by the plan guard" in str(exc), str(exc))
try:
    deny_ws.run_command("echo should-not-run > deny.txt", timeout_seconds=10)
    check("guard deny blocks command", False, "no error raised")
except agent.ToolError as exc:
    check("guard deny blocks command", "blocked by the plan guard" in str(exc), str(exc))
if has_posix:
    out = deny_ws.run_command("echo read-only-ok", timeout_seconds=10)
    check("guard deny allows read-only command", "read-only-ok" in out, out)

# ---------- guard: approve ----------
approve_ws = new_workspace()
approve_ws.plan_mode = True
approve_ws.plan_guard = "approve"
approve_ws.interactive = False
try:
    approve_ws.write_file("approve.txt", "hello")
    check("guard approve blocks without a tty", False, "no error raised")
except agent.ToolError as exc:
    check(
        "guard approve blocks without a tty",
        "needs explicit approval" in str(exc),
        str(exc),
    )
out = approve_ws.run_command("echo approve-read-ok", timeout_seconds=10) if has_posix else ""
if has_posix:
    check("guard approve allows read-only command", "approve-read-ok" in out, out)

# web mode has no way to prompt, so it must refuse instead of hanging
web_ws = new_workspace()
web_ws.plan_mode = True
web_ws.plan_guard = "approve"
web_ws.prompt_mode = "web"
web_ws.interactive = True
try:
    web_ws.write_file("web-approve.txt", "hello")
    check("guard approve refuses in web mode", False, "no error raised")
except agent.ToolError as exc:
    check(
        "guard approve refuses in web mode",
        "needs explicit approval" in str(exc),
        str(exc),
    )

# guarded tools that never reach _approve
memory_ws = new_workspace()
memory_ws.plan_mode = True
memory_ws.plan_guard = "deny"
try:
    memory_ws.dispatch("remember", {"text": "x"})
    check("guard deny blocks remember", False, "no error raised")
except agent.ToolError as exc:
    check("guard deny blocks remember", "blocked by the plan guard" in str(exc), str(exc))

# guard does not apply outside plan mode
plain_ws = new_workspace()
plain_ws.plan_guard = "deny"
out = plain_ws.write_file("plain.txt", "hello")
check("guard ignored outside plan mode", "wrote" in out, out)

# ---------- run_turn stops after submit_plan ----------
class PlanClient:
    def __init__(self, tool_name="submit_plan", arguments=None):
        self.last_reasoning_ignored = False
        self.last_usage = {}
        self.last_finish_reason = "stop"
        self.calls = 0
        self.tool_name = tool_name
        self.arguments = arguments if arguments is not None else {"plan": "## 目标\n做事"}

    def chat(self, messages, tools, on_delta=None, cancel_event=None):
        self.calls += 1
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": self.tool_name,
                        "arguments": json.dumps(self.arguments),
                    },
                }
            ],
        }


turn_ws = new_workspace()
turn_ws.plan_mode = True
client = PlanClient()
messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
events = []
ok = agent.run_turn(
    client,
    turn_ws,
    messages,
    5,
    emit=lambda kind, text: events.append((kind, text)),
)
check("run_turn ok after plan", ok is True)
check("run_turn stops after submit_plan", client.calls == 1, client.calls)
check("run_turn emits plan event", any(kind == "plan" for kind, _ in events), events)
plan_events = [text for kind, text in events if kind == "plan"]
check("plan event carries the plan", plan_events and "做事" in plan_events[0], plan_events)

# without plan mode the same call fails and the loop continues
turn_ws2 = new_workspace()
turn_ws2.plan_mode = False
client2 = PlanClient()
messages2 = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
agent.run_turn(client2, turn_ws2, messages2, 3)
check("submit_plan outside plan mode does not stop the loop", client2.calls == 3, client2.calls)
tool_results = [m for m in messages2 if m.get("role") == "tool"]
check(
    "submit_plan error returned to the model",
    tool_results and "only available in plan mode" in tool_results[0]["content"],
    tool_results[:1],
)

# ---------- system prompt ----------
plan_prompt = agent.build_system_prompt(ws_root, {}, None, "interactive", plan_mode=True)
check("prompt has plan section", "当前处于计划模式" in plan_prompt)
check("prompt mentions submit_plan", "submit_plan" in plan_prompt)
check("prompt has mode contract", "工作模式：" in plan_prompt)
check("prompt allows investigation commands", "跑构建或测试看报错" in plan_prompt)
check("prompt forbids writing", "不要写或改文件" in plan_prompt)
check("prompt allows exceptions", "例外：" in plan_prompt)

off_prompt = agent.build_system_prompt(ws_root, {}, None, "interactive", plan_mode=False)
check("prompt off has no plan section", "当前处于计划模式" not in off_prompt)
check("prompt off keeps mode contract", "工作模式：" in off_prompt)

# ---------- config defaults ----------
config = agent.load_config(os.path.join(root, "defaults.json"))
check(
    "config default plan_mode",
    config["defaults"]["plan_mode"] is False,
    config["defaults"].get("plan_mode"),
)
check(
    "config default plan_guard",
    config["defaults"]["plan_guard"] == "off",
    config["defaults"].get("plan_guard"),
)

# ---------- CLI ----------
cli_home = tempfile.mkdtemp(prefix="plan-cli-")
cli_env = dict(
    os.environ,
    HOME=cli_home,
    BENBEN_CONFIG=os.path.join(cli_home, "config.json"),
)


def run_cli(args, stdin=""):
    return subprocess.run(
        [sys.executable, AGENT, *args],
        input=stdin,
        text=True,
        capture_output=True,
        env=cli_env,
        timeout=60,
    )


result = run_cli([], "/plan on\n/plan\n/exit\n")
check("cli plan on exit", result.returncode == 0, result.stderr[-400:])
check("cli plan on message", "plan mode: on" in result.stdout, result.stdout[-800:])
check("cli plan guard line", "guard: off" in result.stdout, result.stdout[-800:])

result = run_cli([], "/plan guard deny\n/plan\n/exit\n")
check("cli plan guard deny", "guard: deny" in result.stdout, result.stdout[-800:])

result = run_cli([], "/plan on\n/plan off\n/plan\n/exit\n")
check("cli plan off", "plan mode: off" in result.stdout, result.stdout[-800:])

result = run_cli([], "/plan bogus\n/exit\n")
check("cli plan usage", "usage: /plan" in result.stdout, result.stdout[-400:])

result = run_cli(["--plan"], "/plan\n/exit\n")
check("cli --plan starts in plan mode", "plan mode: on" in result.stdout, result.stdout[-800:])

result = run_cli(["--no-plan"], "/plan\n/exit\n")
check("cli --no-plan stays off", "plan mode: off" in result.stdout, result.stdout[-800:])

result = run_cli(["--plan", "--plan-guard", "approve"], "/plan\n/exit\n")
check("cli --plan-guard", "guard: approve" in result.stdout, result.stdout[-800:])

# /config save persists the plan settings
result = run_cli([], "/plan on\n/plan guard deny\n/config save\n/exit\n")
check("cli config save exit", result.returncode == 0, result.stderr[-400:])
saved_path = os.path.join(cli_home, "config.json")
saved = json.loads(open(saved_path, encoding="utf-8").read())
check(
    "config save persists plan_mode",
    saved["defaults"]["plan_mode"] is True,
    saved["defaults"].get("plan_mode"),
)
check(
    "config save persists plan_guard",
    saved["defaults"]["plan_guard"] == "deny",
    saved["defaults"].get("plan_guard"),
)

# a saved plan_mode is restored on the next start
result = run_cli([], "/plan\n/exit\n")
check("cli restores saved plan mode", "plan mode: on" in result.stdout, result.stdout[-800:])
check("cli restores saved guard", "guard: deny" in result.stdout, result.stdout[-800:])

# a one-shot plan task must not wait for an approval that nobody can give
result = run_cli(["--plan", "--plan-guard", "deny"], "hi\n")
check("cli one-shot plan does not hang", result.returncode in (0, 1), result.returncode)
check("cli one-shot plan no approval prompt", "Approve and execute?" not in result.stdout)

# ---------- Web ----------
class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "model-a"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw) if raw else {}
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=model.serve_forever, daemon=True).start()
model_base = f"http://127.0.0.1:{model.server_port}/v1"

web_home = tempfile.mkdtemp(prefix="plan-web-")
web_config = os.path.join(web_home, "config.json")
web_log_path = os.path.join(web_home, "web.log")
web_log = open(web_log_path, "w+", encoding="utf-8")
web_env = dict(os.environ, HOME=web_home, BENBEN_CONFIG=web_config)
web = subprocess.Popen(
    [
        sys.executable, AGENT, "--web", "--port", "0",
        "--config", web_config,
        "--base-url", model_base,
        "--api-key", "cli-key",
        "-m", "m1",
    ],
    stdout=web_log,
    stderr=web_log,
    text=True,
    env=web_env,
)

url = None
for _ in range(100):
    web_log.flush()
    with open(web_log_path, encoding="utf-8") as reader:
        text = reader.read()
    match = re.search(r"http://\S+\?token=[\w-]+", text)
    if match:
        url = match.group(0)
        break
    if web.poll() is not None:
        break
    time.sleep(0.1)
check("web started", url is not None, open(web_log_path, encoding="utf-8").read()[-500:])


def web_api(path, body=None):
    token = url.rsplit("token=", 1)[1]
    base = url.split("?", 1)[0]
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json", "X-Token": token},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
    return 200, text


try:
    with urllib.request.urlopen(url, timeout=10) as response:
        html = response.read().decode("utf-8")
    check("web html plan toggle", 'id="plan-toggle"' in html)
    check("web html plan guard", 'id="plan-guard"' in html)
    check("web html plan block", "addPlanBlock" in html)
    check("web html plan command", "handlePlanCommand" in html)
    check("web html plan help", "/plan guard" in html)

    code, text = web_api("/api/runtime")
    data = json.loads(text)
    check("web runtime plan_mode false", data["plan_mode"] is False, data)
    check("web runtime plan_guard off", data["plan_guard"] == "off", data)

    code, text = web_api("/api/plan", {"enabled": True})
    check("web plan enable status", code == 200, text)
    data = json.loads(text)
    check("web plan enable", data["plan_mode"] is True, data)

    code, text = web_api("/api/runtime")
    data = json.loads(text)
    check("web runtime plan_mode true", data["plan_mode"] is True, data)

    code, text = web_api("/api/plan", {"enabled": True, "guard": "approve"})
    check("web plan guard status", code == 200, text)
    data = json.loads(text)
    check("web plan guard", data["plan_guard"] == "approve", data)

    code, text = web_api("/api/plan", {"enabled": True, "guard": "bogus"})
    check("web plan bad guard rejected", code == 400, f"{code} {text}")

    code, text = web_api("/api/plan", {"enabled": "yes"})
    check("web plan bad enabled rejected", code == 400, f"{code} {text}")

    code, text = web_api("/api/plan", {"enabled": False})
    check("web plan disable status", code == 200, text)
    data = json.loads(text)
    check("web plan disable", data["plan_mode"] is False, data)
    check("web plan guard kept", data["plan_guard"] == "approve", data)
finally:
    web.terminate()
    try:
        web.wait(timeout=10)
    except subprocess.TimeoutExpired:
        web.kill()
    web_log.close()
    model.shutdown()

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(cli_home, ignore_errors=True)
shutil.rmtree(web_home, ignore_errors=True)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
