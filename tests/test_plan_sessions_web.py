from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw) if raw else {}
        messages = payload.get("messages") or []
        last = messages[-1] if messages else {}
        text = last.get("content") or ""
        if isinstance(text, list):
            text = " ".join(
                str(part.get("text") or "")
                for part in text
                if isinstance(part, dict)
            )
        if "make plan" in text:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_plan",
                    "type": "function",
                    "function": {
                        "name": "submit_plan",
                        "arguments": json.dumps({
                            "plan": "Session A plan",
                            "questions": "",
                        }),
                    },
                }],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "ok"}
            finish = "stop"
        body = json.dumps({
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=model.serve_forever, daemon=True).start()
model_base = f"http://127.0.0.1:{model.server_port}/v1"

home = tempfile.mkdtemp(prefix="plan-sessions-web-")
workspace = os.path.join(home, "workspace")
os.makedirs(workspace)
config_path = os.path.join(home, "config.json")
log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
env = dict(
    os.environ,
    HOME=home,
    BENBEN_CONFIG=config_path,
    OPENAI_MODEL="env-model",
)
process = subprocess.Popen(
    [
        sys.executable, AGENT, "--web", "--port", "0",
        "--config", config_path,
        "--cwd", workspace,
        "--base-url", model_base,
        "--api-key", "test-key",
        "-m", "m1",
    ],
    stdout=log_handle,
    stderr=log_handle,
    text=True,
    env=env,
)
url = None
for _ in range(150):
    log_handle.flush()
    with open(log_path, encoding="utf-8") as reader:
        text = reader.read()
    match = re.search(r"http://\S+\?token=[\w-]+", text)
    if match:
        url = match.group(0)
        break
    if process.poll() is not None:
        break
    time.sleep(0.1)
check("plan sessions web started", url is not None, open(log_path, encoding="utf-8").read()[-800:])

token = url.rsplit("token=", 1)[1]
base = url.split("?", 1)[0]


def api(path, body=None, expect_error=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json", "X-Token": token},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if expect_error:
            return exc.code, exc.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8')}") from exc
    return 200, text


def api_json(path, body=None, expect_error=False):
    code, text = api(path, body, expect_error)
    if expect_error:
        return code, text
    return json.loads(text)


def chat_stream(message):
    req = urllib.request.Request(
        base + "/api/chat/stream",
        data=json.dumps({"message": message, "images": []}).encode(),
        headers={"Content-Type": "application/json", "X-Token": token},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=60)


def read_event(response):
    data_lines = []
    while True:
        raw = response.readline()
        if not raw:
            return None
        line = raw.decode("utf-8").rstrip("\n")
        if line == "":
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            data_lines = []
            if payload.startswith("data: "):
                return json.loads(payload[6:])
            continue
        if line.startswith("data: "):
            data_lines.append(line)


def read_until_done(response, limit=200):
    events = []
    for _ in range(limit):
        event = read_event(response)
        if event is None:
            return None, events
        events.append(event)
        if event.get("type") == "done":
            return event, events
    return None, events


try:
    code, text = api("/api/plan", {"enabled": True})
    check("plan mode enabled", code == 200, text)

    response = chat_stream("make plan")
    try:
        done, events = read_until_done(response)
        plans = [event for event in events if event.get("type") == "plan"]
        check("session A plan event", bool(plans), events)
        check(
            "session A plan text",
            "Session A plan" in (plans[0].get("text") if plans else ""),
            plans,
        )
        check("session A done", done is not None, events[-5:])
        session_a = done.get("session") if done else None
        check("session A id", isinstance(session_a, str) and session_a, session_a)
    finally:
        response.close()

    runtime = api_json("/api/runtime")
    check("session A pending", runtime["plan_pending"] is True, runtime)
    check("session A mode", runtime["plan_mode"] is True, runtime)

    code, text = api("/api/new", {})
    check("new session ok", code == 200, text)
    runtime = api_json("/api/runtime")
    check("new session plan mode reset", runtime["plan_mode"] is False, runtime)
    check("new session plan cleared", runtime["plan_pending"] is False, runtime)

    response = chat_stream("hello")
    try:
        done, events = read_until_done(response)
        check("new session done", done is not None, events[-5:])
        check(
            "new session no old plan",
            not any(event.get("type") == "plan" for event in events),
            events,
        )
    finally:
        response.close()

    code, text = api(
        "/api/open",
        {"session": session_a},
    )
    check("open session A ok", code == 200, text)
    runtime = api_json("/api/runtime")
    check("session A mode restored", runtime["plan_mode"] is True, runtime)
    check("session A pending restored", runtime["plan_pending"] is True, runtime)

    code, text = api("/api/delete", {"session": session_a})
    check("delete session A ok", code == 200, text)
    runtime = api_json("/api/runtime")
    check("delete resets plan mode", runtime["plan_mode"] is False, runtime)
    check("delete clears plan", runtime["plan_pending"] is False, runtime)

    code, text = api("/api/session/restore", {"session": session_a})
    check("restore session A ok", code == 200, text)
    code, text = api("/api/open", {"session": session_a})
    check("reopen session A ok", code == 200, text)
    runtime = api_json("/api/runtime")
    check("deleted plan not restored", runtime["plan_pending"] is False, runtime)
    check("deleted plan mode not restored", runtime["plan_mode"] is False, runtime)
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()
    model.shutdown()

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("FAILURES: none")
