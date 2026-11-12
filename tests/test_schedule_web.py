from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "m1"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "web scheduled ok"},
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


def start_web(home, args):
    config_path = os.path.join(home, "config.json")
    log_path = os.path.join(home, "web.log")
    handle = open(log_path, "w+", encoding="utf-8")
    env = dict(os.environ, HOME=home, BENBEN_CONFIG=config_path)
    process = subprocess.Popen(
        [
            sys.executable, AGENT, "--web", "--port", "0",
            "--config", config_path,
            "--base-url", model_base,
            "--api-key", "test-key",
            "-m", "m1",
            *args,
        ],
        stdout=handle,
        stderr=handle,
        text=True,
        env=env,
    )
    url = None
    for _ in range(100):
        handle.flush()
        with open(log_path, encoding="utf-8") as reader:
            text = reader.read()
        match = re.search(r"http://\S+\?token=[\w-]+", text)
        if match:
            url = match.group(0)
            break
        if process.poll() is not None:
            break
        time.sleep(0.1)
    return process, handle, url, log_path


def make_api(url):
    token = url.rsplit("token=", 1)[1]
    base = url.split("?", 1)[0]

    def api(path, body=None, expect_error=False):
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
            if expect_error:
                return exc.code, exc.read().decode("utf-8")
            raise
        return json.loads(text) if text else {}

    return api, base, token


home = tempfile.mkdtemp(prefix="schedule-web-")
workspace = tempfile.mkdtemp(prefix="schedule-web-ws-")
schedules_dir = os.path.join(home, ".benben-agent", "schedules")
os.makedirs(schedules_dir, exist_ok=True)
tasks = {
    "version": 1,
    "tasks": [{
        "id": "aaaa1111",
        "name": "网页定时任务",
        "prompt": "回复 web scheduled ok",
        "when": "every 1h",
        "type": "every",
        "next_run": "2030-01-01T10:00:00",
        "enabled": True,
        "read_only": True,
        "connection": {
            "name": "fake",
            "base_url": model_base,
            "api_format": "openai-chat-completions",
            "api_key": "test-key",
            "provider": "custom",
            "anthropic_auth": "x-api-key",
            "anthropic_version": "2023-06-01",
        },
        "model": "m1",
        "workspace": workspace,
        "shell": "auto",
        "allow_shell": True,
        "skills": {},
        "allow_skill_scripts": False,
        "max_steps": 5,
        "created": "2026-11-12T10:00:00",
        "last_run": None,
        "last_status": None,
        "run_count": 0,
    }],
}
with open(os.path.join(schedules_dir, "tasks.json"), "w", encoding="utf-8") as handle:
    json.dump(tasks, handle, ensure_ascii=False)

process, log_handle, url, log_path = start_web(home, ["--schedule"])
check("schedule web started", url is not None, open(log_path, encoding="utf-8").read()[-800:])
try:
    api, base, token = make_api(url)
    data = api("/api/schedules")
    check("api enabled", data.get("enabled") is True, data)
    check("api task listed", data["tasks"][0]["id"] == "aaaa1111", data)
    check("api hides key", "test-key" not in json.dumps(data, ensure_ascii=False))

    with urllib.request.urlopen(base + "/?token=" + token, timeout=30) as response:
        html = response.read().decode("utf-8")
    check("html schedule button", 'id="schedules-open"' in html)
    check("html schedule modal", 'id="schedule-modal"' in html and "refreshSchedules" in html)

    disabled = api("/api/schedules/enable", {"id": "aaaa1111", "enabled": False})
    check("api pause", "暂停" in disabled["message"], disabled)
    enabled = api("/api/schedules/enable", {"id": "aaaa1111", "enabled": True})
    check("api enable", "启用" in enabled["message"], enabled)

    history = api("/api/schedules/history", {"id": "aaaa1111", "limit": 5})
    check("api history empty", "还没有运行记录" in history["history"], history)
    code, _ = api("/api/schedules/history", {"id": "nope"}, expect_error=True)
    check("api history unknown", code == 404, code)

    run = api("/api/schedules/run", {"id": "aaaa1111"})
    check("api run queued", "已排队" in run["message"], run)

    output = ""
    notes = []
    for _ in range(100):
        data = api("/api/schedules")
        notes.extend(data.get("notifications") or [])
        task = data["tasks"][0]
        if notes:
            output = notes[0].get("output") or ""
            break
        time.sleep(0.1)
    check("api run completed", task["run_count"] == 1 and task["last_status"] == "ok", task)
    check("api notification", bool(notes) and notes[0]["status"] == "ok", notes)
    check("api notification output", "web scheduled ok" in output, output)

    history = api("/api/schedules/history", {"id": "aaaa1111", "limit": 5})
    check("api history filled", "web scheduled ok" in history["history"], history)

    deleted = api("/api/schedules/delete", {"id": "aaaa1111"})
    check("api delete", "已删除" in deleted["message"], deleted)
    check("api tasks empty", api("/api/schedules")["tasks"] == [])
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()
    model.shutdown()

# ---------- disabled mode ----------
home_disabled = tempfile.mkdtemp(prefix="schedule-web-off-")
process, log_handle, url, log_path = start_web(home_disabled, [])
check("disabled web started", url is not None, open(log_path, encoding="utf-8").read()[-800:])
try:
    api, base, token = make_api(url)
    data = api("/api/schedules")
    check("disabled api", data == {"enabled": False, "tasks": [], "notifications": []}, data)
    code, _ = api("/api/schedules/run", {"id": "x"}, expect_error=True)
    check("disabled run rejected", code == 404, code)
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
