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
    calls = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw) if raw else {}
        ModelHandler.calls.append(payload)
        messages = payload.get("messages") or []
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            message = {"role": "assistant", "content": "final ok"}
            finish = "stop"
        else:
            text = last.get("content") or ""
            if isinstance(text, list):
                text = " ".join(
                    str(part.get("text") or "")
                    for part in text
                    if isinstance(part, dict)
                )
            if "deny" in text:
                name = "deny.txt"
            elif "timeout" in text:
                name = "timeout.txt"
            elif "cancel" in text:
                name = "cancel.txt"
            elif "plan" in text:
                name = "plan.txt"
            else:
                name = "allow.txt"
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({
                            "path": name,
                            "content": "ok\n",
                        }),
                    },
                }],
            }
            finish = "tool_calls"
        body = json.dumps({
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=model.serve_forever, daemon=True).start()
model_base = f"http://127.0.0.1:{model.server_port}/v1"


def start_web(home, extra_args, extra_env=None):
    workspace = os.path.join(home, "workspace")
    os.makedirs(workspace, exist_ok=True)
    config_path = os.path.join(home, "config.json")
    log_path = os.path.join(home, "web.log")
    log_handle = open(log_path, "w+", encoding="utf-8")
    env = dict(
        os.environ,
        HOME=home,
        BENBEN_CONFIG=config_path,
        OPENAI_MODEL="env-model",
    )
    if extra_env:
        env.update(extra_env)
    process = subprocess.Popen(
        [
            sys.executable, AGENT, "--web", "--port", "0",
            "--config", config_path,
            "--cwd", workspace,
            "--base-url", model_base,
            "--api-key", "test-key",
            "-m", "m1",
        ] + extra_args,
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
    if url is None:
        log_handle.flush()
        text = open(log_path, encoding="utf-8").read()
        process.kill()
        raise RuntimeError("web did not start: " + text[-1000:])
    return process, url, log_handle, workspace


def make_api(url):
    token = url.rsplit("token=", 1)[1]
    base = url.split("?", 1)[0]

    def request(path, body=None, expect_error=False):
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

    def json_api(path, body=None, expect_error=False):
        code, text = request(path, body, expect_error)
        if expect_error:
            return code, text
        return json.loads(text)

    def html():
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8")

    def chat_stream(message):
        req = urllib.request.Request(
            base + "/api/chat/stream",
            data=json.dumps({"message": message, "images": []}).encode(),
            headers={"Content-Type": "application/json", "X-Token": token},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=60)

    return request, json_api, html, chat_stream


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


def read_until(response, wanted, limit=200):
    events = []
    for _ in range(limit):
        event = read_event(response)
        if event is None:
            return None, events
        events.append(event)
        if event.get("type") == wanted:
            return event, events
    return None, events


def tool_result_text(events):
    return "\n".join(
        str(event.get("text") or "")
        for event in events
        if event.get("type") == "tool_result"
    )


print("== web approval with --confirm ==")
home = tempfile.mkdtemp(prefix="web-approval-")
process, url, log_handle, workspace = start_web(home, ["--confirm"])
request, api, get_html, chat_stream = make_api(url)

try:
    html = get_html()
    check("web html approval modal", 'id="approval-modal"' in html)
    check("web html approval detail", 'id="approval-detail"' in html)
    check("web html approval functions", "showApproval" in html and "respondApproval" in html)

    response = chat_stream("write allow")
    try:
        approval_event, events = read_until(response, "approval")
        check("approval event", approval_event is not None, events[-5:])
        approval = (
            json.loads(approval_event["text"])
            if approval_event is not None
            else None
        )
        check(
            "approval action",
            approval is not None and approval.get("action") == "write_file",
            approval,
        )
        check(
            "approval detail",
            approval is not None and "allow.txt" in approval.get("detail", ""),
            approval,
        )
        check(
            "approval timeout field",
            approval is not None and int(approval.get("timeout") or 0) >= 1,
            approval,
        )
        code, _text = request(
            "/api/approvals/respond",
            {"id": approval["id"], "allow": True},
        )
        check("approval allow response", code == 200, code)
        done, rest = read_until(response, "done")
        check("approval allow done", done is not None and done.get("ok") is True, done)
        check(
            "approval allow tool result",
            "wrote" in tool_result_text(events + rest),
            tool_result_text(events + rest),
        )
        check(
            "approval allow file",
            os.path.isfile(os.path.join(workspace, "allow.txt")),
        )
        code, _text = request(
            "/api/approvals/respond",
            {"id": approval["id"], "allow": True},
            expect_error=True,
        )
        check("approval stale id rejected", code == 404, code)
    finally:
        response.close()

    response = chat_stream("write deny")
    try:
        approval_event, events = read_until(response, "approval")
        approval = (
            json.loads(approval_event["text"])
            if approval_event is not None
            else None
        )
        check("deny approval event", approval is not None, events[-5:])
        request(
            "/api/approvals/respond",
            {"id": approval["id"], "allow": False},
        )
        done, rest = read_until(response, "done")
        check("deny done", done is not None, done)
        check(
            "deny tool result",
            "permission denied" in tool_result_text(events + rest),
            tool_result_text(events + rest),
        )
        check(
            "deny file not created",
            not os.path.exists(os.path.join(workspace, "deny.txt")),
        )
    finally:
        response.close()

    response = chat_stream("write cancel")
    try:
        approval_event, events = read_until(response, "approval")
        approval = (
            json.loads(approval_event["text"])
            if approval_event is not None
            else None
        )
        check("cancel approval event", approval is not None, events[-5:])
        request("/api/chat/stop", {})
        done, rest = read_until(response, "done")
        check("cancel done", done is not None, done)
        text = "\n".join(
            str(event.get("text") or "")
            for event in events + rest
            if event.get("type") == "warning"
        )
        check("cancel warning", "审批已取消" in text, text)
        check(
            "cancel file not created",
            not os.path.exists(os.path.join(workspace, "cancel.txt")),
        )
    finally:
        response.close()

    code, _text = request(
        "/api/chat",
        {"message": "hello", "images": []},
        expect_error=True,
    )
    check("non-stream approval rejected", code == 409, code)
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()

print("== web plan guard approval ==")
home_plan = tempfile.mkdtemp(prefix="web-approval-plan-")
plan_process, plan_url, plan_log, plan_workspace = start_web(
    home_plan, ["--plan", "--plan-guard", "approve"]
)
plan_request, _plan_api, _plan_html, plan_chat = make_api(plan_url)
try:
    response = plan_chat("write plan")
    try:
        approval_event, events = read_until(response, "approval")
        approval = (
            json.loads(approval_event["text"])
            if approval_event is not None
            else None
        )
        check("plan approval event", approval is not None, events[-5:])
        check(
            "plan approval action",
            approval is not None
            and approval.get("action", "").startswith("plan:"),
            approval,
        )
        plan_request(
            "/api/approvals/respond",
            {"id": approval["id"], "allow": True},
        )
        done, rest = read_until(response, "done")
        check("plan approval done", done is not None, done)
        check(
            "plan approval file",
            os.path.isfile(os.path.join(plan_workspace, "plan.txt")),
        )
    finally:
        response.close()
finally:
    plan_process.terminate()
    try:
        plan_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        plan_process.kill()
    plan_log.close()

print("== web approval timeout ==")
home_timeout = tempfile.mkdtemp(prefix="web-approval-timeout-")
timeout_process, timeout_url, timeout_log, timeout_workspace = start_web(
    home_timeout,
    ["--confirm"],
    {"BENBEN_WEB_APPROVAL_TIMEOUT": "1"},
)
_timeout_request, _timeout_api, _timeout_html, timeout_chat = make_api(timeout_url)
try:
    response = timeout_chat("write timeout")
    try:
        approval_event, events = read_until(response, "approval")
        approval = (
            json.loads(approval_event["text"])
            if approval_event is not None
            else None
        )
        check("timeout approval event", approval is not None, events[-5:])
        done, rest = read_until(response, "done")
        check("timeout done", done is not None, done)
        warnings = "\n".join(
            str(event.get("text") or "")
            for event in events + rest
            if event.get("type") == "warning"
        )
        check("timeout warning", "审批超时" in warnings, warnings)
        check(
            "timeout tool denied",
            "permission denied" in tool_result_text(events + rest),
            tool_result_text(events + rest),
        )
        check(
            "timeout file not created",
            not os.path.exists(os.path.join(timeout_workspace, "timeout.txt")),
        )
    finally:
        response.close()
finally:
    timeout_process.terminate()
    try:
        timeout_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        timeout_process.kill()
    timeout_log.close()

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("FAILURES: none")
