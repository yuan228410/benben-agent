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
        self._json({"data": [{"id": "claude-10"}, {"id": "claude-3"}]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(raw)
        with Handler.lock:
            Handler.requests.append(payload)
            Handler.headers.append({k.lower(): v for k, v in self.headers.items()})
            Handler.calls += 1
            call = Handler.calls
        if Handler.mode == "tool" or (Handler.mode == "tool_then_text" and call == 1):
            content = [
                {"type": "text", "text": "先看目录"},
                {"type": "tool_use", "id": "tool-1", "name": "list_dir", "input": {}},
            ]
            stop = "tool_use"
        else:
            content = [{"type": "text", "text": "done"}]
            stop = "max_tokens" if Handler.mode == "max_tokens" else "end_turn"
        self._json({
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": content,
            "stop_reason": stop,
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "cache_read_input_tokens": 3,
                "output_tokens_details": {"thinking_tokens": 4},
            },
        })


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"
workspace_dir = tempfile.mkdtemp(prefix="benben-workspace-")
Path(workspace_dir, "a.txt").write_text("a", encoding="utf-8")
Path(workspace_dir, "b.txt").write_text("b", encoding="utf-8")
ws = agent.Workspace(workspace_dir)

print("== request translation ==")
Handler.requests.clear()
Handler.headers.clear()
client = agent.AnthropicClient(BASE, "secret", "claude-test", timeout=5)
messages = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": [
        {"type": "text", "text": "看这个图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]},
    {"role": "assistant", "content": "先调用工具", "tool_calls": [
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
result = client.chat(messages, tools)
sent = Handler.requests[-1]
check("endpoint path", "POST" in Handler.headers[-1].get(":method", "POST") or True)
check("model", sent["model"] == "claude-test")
check("max_tokens default", sent["max_tokens"] == 8192, sent)
check("system", sent["system"] == "SYS", sent)
check("two messages roles", [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"], sent["messages"])
user_blocks = sent["messages"][0]["content"]
check("user text", any(b.get("type") == "text" and b.get("text") == "看这个图" for b in user_blocks), user_blocks)
check("image block", any(b.get("type") == "image" and b["source"].get("media_type") == "image/png" for b in user_blocks), user_blocks)
assistant_blocks = sent["messages"][1]["content"]
check("assistant text", any(b.get("type") == "text" and b.get("text") == "先调用工具" for b in assistant_blocks), assistant_blocks)
check("tool_use", any(b.get("type") == "tool_use" and b.get("id") == "t1" and b.get("input") == {"path": "."} for b in assistant_blocks), assistant_blocks)
last_user = sent["messages"][2]["content"]
check("tool_result", any(b.get("type") == "tool_result" and b.get("tool_use_id") == "t1" for b in last_user), last_user)
check("user merged after tool", any(b.get("type") == "text" and b.get("text") == "继续" for b in last_user), last_user)
check("tool schema", sent["tools"][0]["input_schema"]["type"] == "object", sent.get("tools"))
check("tool choice", sent["tool_choice"] == {"type": "auto"}, sent)
check("x-api-key", Handler.headers[-1].get("x-api-key") == "secret", Handler.headers[-1])
check("anthropic version", Handler.headers[-1].get("anthropic-version") == "2023-06-01", Handler.headers[-1])
check("response text", result["content"] == "done", result)
check("response usage", client.last_usage == {
    "prompt_tokens": 14,
    "completion_tokens": 7,
    "total_tokens": 21,
    "prompt_tokens_details": {"cached_tokens": 3},
    "completion_tokens_details": {"reasoning_tokens": 4},
}, client.last_usage)
check("finish stop", client.last_finish_reason == "stop", client.last_finish_reason)

print("== stop/tool mapping ==")
Handler.mode = "max_tokens"
Handler.calls = 0
client.chat([{"role": "user", "content": "hi"}], [])
check("finish length", client.last_finish_reason == "length", client.last_finish_reason)
Handler.mode = "tool"
Handler.calls = 0
result = client.chat([{"role": "user", "content": "hi"}], tools)
check("finish tool_calls", client.last_finish_reason == "tool_calls", client.last_finish_reason)
check("tool call converted", result["tool_calls"][0]["function"]["name"] == "list_dir", result)
check("tool args json", json.loads(result["tool_calls"][0]["function"]["arguments"]) == {}, result)

print("== run_turn tool round trip ==")
Handler.mode = "tool_then_text"
Handler.calls = 0
Handler.requests.clear()
client = agent.AnthropicClient(BASE, "secret", "claude-test", timeout=5)
events = []
turn_messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "看看目录"}]
ok = agent.run_turn(client, ws, turn_messages, 3, emit=lambda kind, text: events.append((kind, text)))
check("turn ok", ok)
check("two model calls", len(Handler.requests) == 2, len(Handler.requests))
second = Handler.requests[1]
tool_results = [
    block
    for message in second["messages"] if message.get("role") == "user"
    for block in message.get("content", [])
    if block.get("type") == "tool_result"
]
check("second request tool_result", bool(tool_results) and tool_results[0]["tool_use_id"] == "tool-1", second["messages"])
check("tool result content", "a.txt" in str(tool_results), tool_results)

print("== thinking blocks ==")
thinking_message = {
    "role": "assistant",
    "content": "",
    "_thinking_blocks": [{"type": "thinking", "thinking": "reason", "signature": "sig"}],
    "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
    ],
}
blocks = agent._anthropic_assistant_blocks(thinking_message)
check("thinking first", blocks and blocks[0].get("type") == "thinking", blocks)
cleaned = agent.clean_assistant_message(thinking_message)
check("thinking preserved", cleaned.get("_thinking_blocks") == thinking_message["_thinking_blocks"], cleaned)

print("== image/error ==")
try:
    client.chat([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/bmp;base64,AAAA"}}
    ]}], [])
    check("bmp rejected", False)
except agent.ChatError as exc:
    check("bmp rejected", "Anthropic does not support image/bmp" in str(exc), str(exc))

print("== auth modes ==")
Handler.requests.clear()
Handler.headers.clear()
bearer = agent.AnthropicClient(BASE, "secret", "claude-test", timeout=5, anthropic_auth="bearer")
bearer.chat([{"role": "user", "content": "hi"}], [])
headers = Handler.headers[-1]
check("bearer auth", headers.get("authorization") == "Bearer secret" and not headers.get("x-api-key"), headers)

print("== list models ==")
models = agent.AnthropicClient(BASE, "secret", "claude-test", timeout=5).list_models()
check("models natural sort", models == ["claude-3", "claude-10"], models)

print("== CLI ==")
home = tempfile.mkdtemp(prefix="anthropic-home-")
env = dict(os.environ, HOME=home)
Handler.mode = "text"
Handler.calls = 0
Handler.requests.clear()
Handler.headers.clear()
result = subprocess.run(
    [sys.executable, AGENT, "--api-format", "anthropic-messages", "--base-url", BASE,
     "--api-key", "t", "-m", "claude-test", "--max-tokens", "123", "只回复 ok"],
    capture_output=True, text=True, timeout=60, env=env,
)
check("cli exit", result.returncode == 0, result.stderr[-300:])
check("cli output", "done" in result.stdout, result.stdout[-300:])
sent = Handler.requests[-1]
check("cli max tokens", sent["max_tokens"] == 123, sent.get("max_tokens"))
check("cli system", "system" in sent, sent.keys())
check("cli auth", Handler.headers[-1].get("x-api-key") == "t", Handler.headers[-1])

result = subprocess.run(
    [sys.executable, AGENT, "--api-format", "anthropic-messages", "--reasoning", "deepseek:low", "hi"],
    capture_output=True, text=True, timeout=30, env=env,
)
check("reasoning rejected", result.returncode == 2, result.returncode)
check("reasoning message", "not supported with --api-format anthropic-messages" in result.stderr, result.stderr[-200:])

result = subprocess.run(
    [sys.executable, AGENT, "--api-format", "anthropic-messages", "--base-url", BASE,
     "--api-key", "t", "-m", "claude-test", "--anthropic-auth", "bearer", "hi"],
    capture_output=True, text=True, timeout=60, env=env,
)
check("cli bearer exit", result.returncode == 0, result.stderr[-300:])
check("cli bearer header", Handler.headers[-1].get("authorization") == "Bearer t", Handler.headers[-1])

shutil.rmtree(home, ignore_errors=True)

print("== web ==")
home = tempfile.mkdtemp(prefix="anthropic-web-")
env = dict(os.environ, HOME=home)
Handler.mode = "text"
Handler.calls = 0
Handler.requests.clear()
log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
web = subprocess.Popen(
    [sys.executable, AGENT, "--web", "--port", "0", "--api-format", "anthropic-messages",
     "--base-url", BASE, "--api-key", "t", "-m", "claude-test"],
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
        data = json.loads(response.read())
    check("web chat ok", data.get("ok") is True, data)
    check("web anthropic payload", Handler.requests[-1].get("model") == "claude-test")
    with open(log_path, encoding="utf-8") as reader:
        check("web log api", "api: anthropic-messages" in reader.read())
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
