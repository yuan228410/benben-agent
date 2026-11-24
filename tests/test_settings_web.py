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
    delay = 0.0

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _payload = json.loads(raw) if raw else {}
        if ModelHandler.delay:
            time.sleep(ModelHandler.delay)
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
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

home = tempfile.mkdtemp(prefix="settings-web-")
workspace = os.path.join(home, "workspace")
os.makedirs(workspace)
config_path = os.path.join(home, "config.json")
with open(config_path, "w", encoding="utf-8") as handle:
    json.dump({
        "version": 2,
        "active": "test",
        "connections": {
            "test": {
                "provider": "custom",
                "base_url": model_base,
                "api_format": "openai-chat-completions",
                "model": "m1",
                "api_key": "sk-secret-value",
            },
        },
        "mcpServers": {
            "fake": {
                "command": "echo",
                "args": ["x"],
                "env": {"MCP_TOKEN": "mcp-secret-value"},
            },
        },
        "defaults": {
            "max_steps": 20,
            "stream": True,
            "confirm": False,
        },
    }, handle)

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
check("settings web started", url is not None, open(log_path, encoding="utf-8").read()[-800:])


def make_api(url):
    token = url.rsplit("token=", 1)[1]
    base = url.split("?", 1)[0].rstrip("/")

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

    return request, json_api, html


request, api, get_html = make_api(url)

try:
    html = get_html()
    check("settings html modal", 'id="settings-modal"' in html)
    check("settings html open", 'id="settings-open"' in html)
    check("settings html save", "saveSettings" in html and "collectSettings" in html)
    check("settings html reset", "resetSettings" in html)

    data = api("/api/settings")
    check("settings max steps", data["defaults"]["max_steps"] == 20, data)
    check("settings builtin", data["builtin"]["max_steps"] == 100, data["builtin"])
    check("settings live keys", "confirm" in data["live_keys"], data["live_keys"])
    check("settings restart keys", "skills" in data["restart_keys"], data["restart_keys"])
    check("settings runtime confirm", data["runtime"]["confirm"] is False, data["runtime"])
    settings_text = json.dumps(data)
    check("settings hides api key", "sk-secret-value" not in settings_text)
    check("settings hides mcp secret", "mcp-secret-value" not in settings_text)

    saved = api("/api/settings", {
        "defaults": {
            "confirm": True,
            "thinking": True,
            "stream": False,
            "plan_mode": False,
            "plan_guard": "off",
            "job_poll_seconds": 3,
            "skills": True,
            "max_steps": 50,
            "web": {"host": "127.0.0.1", "port": 9999},
        },
    })
    check("settings saved confirm", saved["defaults"]["confirm"] is True, saved)
    check("settings saved steps", saved["defaults"]["max_steps"] == 50, saved)
    check("settings applied confirm", "confirm" in saved["applied"], saved)
    check("settings applied stream", "stream" in saved["applied"], saved)
    check("settings applied poll", "job_poll_seconds" in saved["applied"], saved)
    check("settings restart skills", "skills" in saved["restart_required"], saved)
    check("settings restart steps", "max_steps" in saved["restart_required"], saved)
    check("settings runtime confirm true", saved["runtime"]["confirm"] is True, saved["runtime"])
    check("settings runtime stream false", saved["runtime"]["stream"] is False, saved["runtime"])
    check("settings runtime thinking true", saved["runtime"]["show_thinking"] is True, saved["runtime"])

    runtime = api("/api/runtime")
    check("runtime stream false", runtime["stream"] is False, runtime)
    check("runtime thinking true", runtime["show_thinking"] is True, runtime)
    check("runtime confirm true", runtime["confirm"] is True, runtime)

    with open(config_path, encoding="utf-8") as handle:
        saved_config = json.load(handle)
    defaults = saved_config["defaults"]
    check("config saved confirm", defaults["confirm"] is True, defaults)
    check("config saved skills", defaults["skills"] is True, defaults)
    check("config saved steps", defaults["max_steps"] == 50, defaults)
    check("config saved web port", defaults["web"]["port"] == 9999, defaults)
    check("config keeps connection", "test" in saved_config["connections"], saved_config)
    check(
        "config keeps api key",
        saved_config["connections"]["test"]["api_key"] == "sk-secret-value",
        saved_config["connections"],
    )
    check("config keeps mcp", "fake" in saved_config["mcpServers"], saved_config)

    code, _text = request(
        "/api/settings",
        {"defaults": {"max_steps": 0}},
        expect_error=True,
    )
    check("settings invalid steps", code == 400, code)
    code, _text = request(
        "/api/settings",
        {"defaults": {"cwd": "/definitely/not/a/real/dir"}},
        expect_error=True,
    )
    check("settings invalid cwd", code == 400, code)
    code, _text = request(
        "/api/settings",
        {"defaults": {"shell": "definitely-not-a-shell-xyz"}},
        expect_error=True,
    )
    check("settings invalid shell", code == 400, code)
    code, _text = request(
        "/api/settings",
        {"defaults": {"reasoning": "not-a-reasoning"}},
        expect_error=True,
    )
    check("settings invalid reasoning", code == 400, code)

    with open(config_path, encoding="utf-8") as handle:
        unchanged = json.load(handle)
    check(
        "invalid save leaves config",
        unchanged["defaults"]["max_steps"] == 50,
        unchanged["defaults"],
    )

    ModelHandler.delay = 2.0
    token = url.rsplit("token=", 1)[1]
    base = url.split("?", 1)[0].rstrip("/")
    chat_error = []

    def run_chat():
        req = urllib.request.Request(
            base + "/api/chat/stream",
            data=json.dumps({"message": "hello", "images": []}).encode(),
            headers={"Content-Type": "application/json", "X-Token": token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                response.read()
        except Exception as exc:
            chat_error.append(str(exc))

    thread = threading.Thread(target=run_chat, daemon=True)
    thread.start()
    time.sleep(0.4)
    code, _text = request(
        "/api/settings",
        {"defaults": {"confirm": False}},
        expect_error=True,
    )
    check("settings busy rejected", code == 409, code)
    thread.join(timeout=10)
    ModelHandler.delay = 0.0
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("FAILURES: none")
