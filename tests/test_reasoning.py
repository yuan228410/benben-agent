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


class Handler(BaseHTTPRequestHandler):
    requests = []
    mode = "plain"

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(raw)
        Handler.requests.append(payload)
        if Handler.mode == "reasoning":
            message = {"role": "assistant", "content": "ok", "reasoning_content": "thinking"}
            usage = {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8,
                "completion_tokens_details": {"reasoning_tokens": 2},
            }
        else:
            message = {"role": "assistant", "content": "ok"}
            usage = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
        body = json.dumps({
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": usage,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}/v1"


def parse(value):
    return agent._parse_reasoning(value)


def payload(value):
    return agent.reasoning_payload(parse(value))


print("== mapping ==")
check("deepseek off", payload("deepseek:off") == {"thinking": {"type": "disabled"}}, payload("deepseek:off"))
check("deepseek minimal", payload("deepseek:minimal") == {"reasoning_effort": "low"})
check("deepseek max", payload("deepseek:max") == {"reasoning_effort": "high"})
check("kimi off", payload("kimi:off") == {"thinking": {"type": "disabled"}})
check("kimi low", payload("kimi:low") == {"reasoning_effort": "low"})
check("minimax minimal", payload("minimax:minimal") == {"reasoning_effort": "low"})
check("minimax max", payload("minimax:max") == {"reasoning_effort": "max"})
check("none alias", payload("kimi:none") == {"thinking": {"type": "disabled"}})
for value in ("minimax:off", "minimax:none"):
    try:
        parse(value)
        check(f"reject {value}", False)
    except agent.ReasoningError:
        check(f"reject {value}", True)
for value in ("qwen:low", "glm:off", "claude:low", "deepseek:bogus", "deepseek"):
    try:
        parse(value)
        check(f"reject {value}", False)
    except agent.ReasoningError:
        check(f"reject {value}", True)

print("== request payload ==")
ws = agent.Workspace(tempfile.mkdtemp(prefix="benben-workspace-"))
for value, expected in [
    ("deepseek:off", {"thinking": {"type": "disabled"}}),
    ("deepseek:minimal", {"reasoning_effort": "low"}),
    ("kimi:low", {"reasoning_effort": "low"}),
    ("minimax:max", {"reasoning_effort": "max"}),
]:
    Handler.requests.clear()
    client = agent.ChatClient(BASE, "", "m", timeout=5, reasoning=parse(value))
    client.chat([{"role": "user", "content": "hi"}], [])
    sent = Handler.requests[-1]
    ok = all(sent.get(key) == val for key, val in expected.items())
    check(f"inject {value}", ok, sent)
    check(f"no extra temp {value}", "temperature" not in sent, sent)

Handler.requests.clear()
client = agent.ChatClient(BASE, "", "m", timeout=5)
client.chat([{"role": "user", "content": "hi"}], [])
sent = Handler.requests[-1]
check("no config sends nothing", "reasoning_effort" not in sent and "thinking" not in sent, sent)

print("== ignored off warning ==")
Handler.mode = "reasoning"
client_off = agent.ChatClient(BASE, "", "m", timeout=5, reasoning=parse("deepseek:off"))
messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
events = []
ok = agent.run_turn(client_off, ws, messages, 2, emit=lambda kind, text: events.append((kind, text)))
check("turn ok", ok)
check("ignored flag", client_off.last_reasoning_ignored is True)
warnings = [text for kind, text in events if kind == "warning"]
check("one warning", len(warnings) == 1, warnings)
check("warning text", warnings and "忽略" in warnings[0], warnings)

client_low = agent.ChatClient(BASE, "", "m", timeout=5, reasoning=parse("deepseek:low"))
events2 = []
agent.run_turn(client_low, ws, [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}], 2,
               emit=lambda kind, text: events2.append((kind, text)))
check("no warning for low", not any(kind == "warning" for kind, _ in events2), events2)
Handler.mode = "plain"

print("== CLI validation ==")
home = tempfile.mkdtemp(prefix="reasoning-home-")
env = dict(os.environ, HOME=home)
result = subprocess.run(
    [sys.executable, AGENT, "--reasoning", "minimax:off", "hi"],
    capture_output=True, text=True, timeout=30, env=env,
)
check("minimax off exit", result.returncode == 2, result.returncode)
check("minimax off message", "cannot disable thinking" in result.stderr, result.stderr[-200:])
result = subprocess.run(
    [sys.executable, AGENT, "--reasoning", "qwen:low", "hi"],
    capture_output=True, text=True, timeout=30, env=env,
)
check("qwen exit", result.returncode == 2)
check("qwen message", "unsupported reasoning provider" in result.stderr, result.stderr[-200:])

interactive = subprocess.run(
    [sys.executable, AGENT, "--base-url", BASE, "--api-key", "t", "-m", "Kimi-K3.1-Pro"],
    input="/reasoning\n/reasoning kimi:low\n/reasoning\n/reasoning clear\n/reasoning\n/exit\n",
    capture_output=True, text=True, timeout=30, env=env,
)
out = interactive.stdout
check("interactive default", "reasoning: default" in out, out[-500:])
check("interactive set", "reasoning: kimi:low" in out, out[-500:])
check("interactive clear", "reasoning: cleared" in out, out[-500:])
check("banner reasoning", "reasoning: default" in out, out[:500])

shutil.rmtree(home, ignore_errors=True)

print("== web ==")
home = tempfile.mkdtemp(prefix="reasoning-web-")
env = dict(os.environ, HOME=home)
Handler.requests.clear()
log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
web = subprocess.Popen(
    [sys.executable, AGENT, "--web", "--port", "0", "--base-url", BASE,
     "--api-key", "t", "-m", "Kimi-K3.1-Pro", "--reasoning", "kimi:low"],
    stdout=log_handle, stderr=log_handle, text=True, env=env,
)
url = None
for _ in range(100):
    log_handle.flush()
    with open(log_path, encoding="utf-8") as reader:
        text = reader.read()
    match = re.search(r"web ui: (http://\S+)", text)
    if match:
        url = match.group(1)
        break
    if web.poll() is not None:
        break
    time.sleep(0.1)
check("web started", url is not None, open(log_path, encoding="utf-8").read()[-300:])
if url:
    base = url.split("?", 1)[0].rstrip("/")
    token = url.rsplit("token=", 1)[1]
    req = urllib.request.Request(
        base + "/api/chat", data=json.dumps({"message": "hi", "images": []}).encode(),
        headers={"Content-Type": "application/json", "X-Token": token}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        json.loads(response.read())
    sent = Handler.requests[-1]
    check("web inject", sent.get("reasoning_effort") == "low", sent)
    req = urllib.request.Request(
        base + "/api/model", data=json.dumps({"model": "MiniMax-M3.1-Flash"}).encode(),
        headers={"Content-Type": "application/json", "X-Token": token}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read())
    check("web mismatch warning", bool(data.get("reasoning_warning")), data)
    with open(log_path, encoding="utf-8") as reader:
        check("web reasoning log", "reasoning: kimi:low" in reader.read())
web.terminate()
try:
    web.wait(timeout=5)
except subprocess.TimeoutExpired:
    web.kill()
log_handle.close()
shutil.rmtree(home, ignore_errors=True)
server.shutdown()

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
