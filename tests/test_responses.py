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
    headers = []
    mode = "text"
    calls = 0
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Handler.headers.append({k.lower(): v for k, v in self.headers.items()})
        self._json({"data": [{"id": "resp-10"}, {"id": "resp-3"}]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(raw)
        with Handler.lock:
            Handler.requests.append(payload)
            Handler.headers.append({k.lower(): v for k, v in self.headers.items()})
            Handler.calls += 1
            call = Handler.calls
        if Handler.mode == "tool" or (Handler.mode == "tool_then_text" and call == 1):
            output = [
                {"type": "reasoning", "content": [
                    {"type": "reasoning_text", "text": "need list"}
                ]},
                {"type": "function_call", "id": "fc_1", "call_id": "call-1",
                 "name": "list_dir", "arguments": "{}", "status": "completed"},
            ]
            status = "completed"
            incomplete = None
        elif Handler.mode == "truncate":
            output = []
            status = "incomplete"
            incomplete = {"reason": "max_output_tokens"}
        else:
            output = [
                {"type": "reasoning", "content": [
                    {"type": "reasoning_text", "text": "think"}
                ]},
                {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": "done"}
                ]},
            ]
            status = "completed"
            incomplete = None
        self._json({
            "id": "resp_1",
            "object": "response",
            "status": status,
            "incomplete_details": incomplete,
            "output": output,
            "usage": {
                "input_tokens": 34,
                "output_tokens": 15,
                "total_tokens": 49,
                "input_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        })


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"
ws = agent.Workspace(tempfile.mkdtemp(prefix="benben-workspace-"))


def client():
    return agent.ResponsesClient(BASE, "secret", "resp-test", timeout=5)


print("== request translation ==")
Handler.mode = "text"
Handler.calls = 0
Handler.requests.clear()
Handler.headers.clear()
messages = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": [
        {"type": "text", "text": "看这个图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]},
    {"role": "assistant", "content": "先看目录", "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": "list_dir", "arguments": "{\"path\":\".\"}"}}
    ]},
    {"role": "tool", "tool_call_id": "t1", "content": "a.txt"},
    {"role": "user", "content": "继续"},
]
tools = [{
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "list",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}]
c = client()
result = c.chat(messages, tools)
sent = Handler.requests[-1]
check("model", sent["model"] == "resp-test")
check("max_output_tokens", sent["max_output_tokens"] == 8192, sent)
check("store false", sent["store"] is False, sent)
check("instructions", sent["instructions"] == "SYS", sent)
items = sent["input"]
check("input count", len(items) == 5, items)
user = items[0]
check("user role", user["role"] == "user", user)
check("user image", any(p.get("type") == "input_image" and p.get("image_url") == "data:image/png;base64,AAAA" for p in user["content"]), user)
check("user text", any(p.get("type") == "input_text" and p.get("text") == "看这个图" for p in user["content"]), user)
check("assistant text", items[1] == {"role": "assistant", "content": "先看目录"}, items[1])
check("function call", items[2] == {
    "type": "function_call", "call_id": "t1", "name": "list_dir", "arguments": "{\"path\":\".\"}"
}, items[2])
check("function output", items[3] == {
    "type": "function_call_output", "call_id": "t1", "output": "a.txt"
}, items[3])
check("later user", items[4] == {"role": "user", "content": "继续"}, items[4])
check("tool flat", sent["tools"][0]["type"] == "function" and sent["tools"][0]["name"] == "list_dir", sent.get("tools"))
check("tool choice", sent["tool_choice"] == "auto", sent)
check("bearer", Handler.headers[-1].get("authorization") == "Bearer secret", Handler.headers[-1])
check("response text", result["content"] == "done", result)
check("response reasoning", result.get("reasoning_content") == "think", result)
check("usage", c.last_usage == {
    "prompt_tokens": 34,
    "completion_tokens": 15,
    "total_tokens": 49,
    "prompt_tokens_details": {"cached_tokens": 10},
    "completion_tokens_details": {"reasoning_tokens": 5},
}, client().last_usage)

print("== stop mapping ==")
Handler.mode = "truncate"
Handler.calls = 0
c = client()
c.chat([{"role": "user", "content": "hi"}], [])
check("truncate length", c.last_finish_reason == "length", c.last_finish_reason)
Handler.mode = "tool"
Handler.calls = 0
c = client()
result = c.chat([{"role": "user", "content": "hi"}], tools)
check("tool finish", c.last_finish_reason == "tool_calls", c.last_finish_reason)
check("tool converted", result["tool_calls"][0]["id"] == "call-1" and result["tool_calls"][0]["function"]["name"] == "list_dir", result)

print("== run_turn tool round trip ==")
Handler.mode = "tool_then_text"
Handler.calls = 0
Handler.requests.clear()
c = client()
events = []
turn_messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "看看目录"}]
ok = agent.run_turn(c, ws, turn_messages, 3, emit=lambda kind, text: events.append((kind, text)))
check("turn ok", ok)
check("two calls", len(Handler.requests) == 2, len(Handler.requests))
second = Handler.requests[1]["input"]
check("second function call", any(item.get("type") == "function_call" and item.get("call_id") == "call-1" for item in second), second)
check("second function output", any(item.get("type") == "function_call_output" and item.get("call_id") == "call-1" for item in second), second)
check("no reasoning item", not any(item.get("type") == "reasoning" for item in second), second)

print("== image/error ==")
try:
    client().chat([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/bmp;base64,AAAA"}}
    ]}], [])
    check("bmp rejected", False)
except agent.ChatError as exc:
    check("bmp rejected", "Responses API does not support image/bmp" in str(exc), str(exc))

print("== list models ==")
models = client().list_models()
check("models natural sort", models == ["resp-3", "resp-10"], models)

print("== CLI ==")
home = tempfile.mkdtemp(prefix="responses-home-")
env = dict(os.environ, HOME=home)
Handler.mode = "text"
Handler.calls = 0
Handler.requests.clear()
Handler.headers.clear()
result = subprocess.run(
    [sys.executable, AGENT, "--api-format", "openai-responses", "--base-url", BASE,
     "--api-key", "t", "-m", "resp-test", "--max-tokens", "123", "hi"],
    capture_output=True, text=True, timeout=60, env=env,
)
check("cli exit", result.returncode == 0, result.stderr[-300:])
check("cli output", "done" in result.stdout, result.stdout[-300:])
sent = Handler.requests[-1]
check("cli max output", sent["max_output_tokens"] == 123, sent.get("max_output_tokens"))
check("cli store", sent["store"] is False, sent)
check("cli instructions", "instructions" in sent, sent.keys())
check("cli auth", Handler.headers[-1].get("authorization") == "Bearer t", Handler.headers[-1])

result = subprocess.run(
    [sys.executable, AGENT, "--api-format", "openai-responses", "--base-url", BASE,
     "--api-key", "t", "-m", "resp-test", "--reasoning", "openai:high", "hi"],
    capture_output=True, text=True, timeout=60, env=env,
)
check("responses reasoning exit", result.returncode == 0, result.stderr[-300:])
sent = Handler.requests[-1]
check("responses reasoning effort", sent.get("reasoning") == {"effort": "high"}, sent.get("reasoning"))

result = subprocess.run(
    [sys.executable, AGENT, "--api-format", "openai-responses", "--reasoning", "deepseek:low", "hi"],
    capture_output=True, text=True, timeout=30, env=env,
)
check("reasoning rejected", result.returncode == 2, result.returncode)
check("reasoning message", "not supported with --api-format openai-responses" in result.stderr, result.stderr[-200:])
shutil.rmtree(home, ignore_errors=True)

print("== web ==")
home = tempfile.mkdtemp(prefix="responses-web-")
env = dict(os.environ, HOME=home)
Handler.mode = "text"
Handler.calls = 0
Handler.requests.clear()
log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
web = subprocess.Popen(
    [sys.executable, AGENT, "--web", "--port", "0", "--api-format", "openai-responses",
     "--base-url", BASE, "--api-key", "t", "-m", "resp-test"],
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
    base = url.split("?", 1)[0]
    token = url.rsplit("token=", 1)[1]
    req = urllib.request.Request(
        base + "/api/chat", data=json.dumps({"message": "hi", "images": []}).encode(),
        headers={"Content-Type": "application/json", "X-Token": token}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read())
    check("web chat ok", data.get("ok") is True, data)
    check("web responses payload", Handler.requests[-1].get("model") == "resp-test")
    with open(log_path, encoding="utf-8") as reader:
        check("web log api", "api: openai-responses" in reader.read())
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
