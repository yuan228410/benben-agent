from pathlib import Path
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
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


class ModelHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        ModelHandler.requests.append({
            "method": "GET",
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "x-api-key": self.headers.get("x-api-key"),
            "anthropic-version": self.headers.get("anthropic-version"),
        })
        body = json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "content": [{"type": "text", "text": "ok"}],
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

home = tempfile.mkdtemp(prefix="web-connections-")
config_path = os.path.join(home, "config.json")
log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
env = dict(
    os.environ,
    HOME=home,
    BENBEN_CONFIG=config_path,
    OPENAI_MODEL="env-model",
)
web = subprocess.Popen(
    [
        sys.executable, AGENT, "--web", "--port", "0",
        "--config", config_path,
        "--base-url", model_base,
        "--api-key", "cli-key",
        "-m", "m1",
        "--reasoning", "deepseek:low",
    ],
    stdout=log_handle,
    stderr=log_handle,
    text=True,
    env=env,
)

url = None
for _ in range(100):
    log_handle.flush()
    with open(log_path, encoding="utf-8") as reader:
        text = reader.read()
    match = re.search(r"http://\S+\?token=[\w-]+", text)
    if match:
        url = match.group(0)
        break
    if web.poll() is not None:
        break
    time.sleep(0.1)
check("web started", url is not None, open(log_path, encoding="utf-8").read()[-500:])


def api(path, body=None, expect_error=False):
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
        if expect_error:
            return exc.code, exc.read().decode("utf-8")
        raise
    return 200, text


def api_json(path, body=None, expect_error=False):
    code, text = api(path, body, expect_error)
    if expect_error:
        return code, text
    return json.loads(text)


def get_html():
    token = url.rsplit("token=", 1)[1]
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


try:
    code, html = 200, get_html()
    check("web html connect button", "连接设置" in html)
    check("web html modal", 'id="connect-modal"' in html)
    check("web html preset select", 'id="conn-provider"' in html)
    check("web html slash connect", "openConnectModal()" in html)
    check("web html slash shell", "handleShellCommand" in html)

    data = api_json("/api/connections")
    check("initial no connections", data["connections"] == [])
    check("initial active", data["active"] is None)
    check("config path exposed", data["config_path"] == config_path)
    check(
        "domestic minimax preset",
        data["presets"]["minimax"]["base_urls"]["anthropic-messages"]
        == "https://api.minimaxi.com/anthropic",
    )
    check("no international minimax", "api.minimax.io" not in json.dumps(data))
    check("formats", "anthropic-messages" in data["protocols"])

    # Fetch models for a new connection.
    code, text = api("/api/connections/models", {
        "provider": "custom",
        "base_url": model_base,
        "api_format": "openai-chat-completions",
        "model": "model-a",
        "api_key": "sk-new",
    })
    fetched = json.loads(text)
    check("fetch models", fetched["models"] == ["model-a", "model-b"])

    # Save and activate.
    code, text = api("/api/connections/save", {
        "name": "work",
        "provider": "custom",
        "base_url": model_base,
        "api_format": "openai-chat-completions",
        "model": "model-a",
        "api_key": "sk-secret-value",
        "make_active": True,
    })
    saved = json.loads(text)
    check("save active", saved["active"] == "work")
    check("save returned model", saved["connection"]["model"] == "model-a")
    check("save response hides key", "sk-secret-value" not in text)

    state = api_json("/api/state")
    check("state connection", state["connection"] == "work")
    check("state model", state["model"] == "model-a")
    check("state base", state["base_url"] == model_base)

    data = api_json("/api/connections")
    check("saved listed", [item["name"] for item in data["connections"]] == ["work"])
    check("saved runtime", data["connections"][0]["runtime"] is True)
    check("saved key hidden", "sk-secret-value" not in json.dumps(data))
    check("saved key hint", data["connections"][0]["key_hint"] == "sk-****alue")
    check("env override listed", data["env_overrides"] == ["OPENAI_MODEL"])

    # Model fetch with a saved name reuses the stored key.
    ModelHandler.requests.clear()
    code, text = api("/api/connections/models", {"name": "work"})
    check("saved model fetch", json.loads(text)["models"] == ["model-a", "model-b"])
    auth = ModelHandler.requests[-1]["authorization"]
    check("saved key reused", auth == "Bearer sk-secret-value", auth)

    # Saving without a key keeps the old key.
    code, text = api("/api/connections/save", {
        "name": "work",
        "provider": "custom",
        "base_url": model_base,
        "api_format": "openai-chat-completions",
        "model": "model-b",
        "make_active": True,
    })
    config = json.load(open(config_path, encoding="utf-8"))
    check("key preserved", config["connections"]["work"]["api_key"] == "sk-secret-value")
    check("model updated", config["connections"]["work"]["model"] == "model-b")
    if os.name != "nt":
        check("config mode", (os.stat(config_path).st_mode & 0o777) == 0o600)

    # Create a second connection without activating it.
    code, text = api("/api/connections/save", {
        "name": "other",
        "provider": "custom",
        "base_url": model_base,
        "api_format": "openai-chat-completions",
        "model": "model-c",
        "api_key": "sk-other",
        "make_active": False,
    })
    check("second saved", json.loads(text)["active"] == "work")
    check("quality: runtime unchanged", api_json("/api/state")["model"] == "model-b")

    used = api_json("/api/connections/use", {"name": "other"})
    check("use other", used["active"] == "other" and used["model"] == "model-c")
    check("use state", api_json("/api/state")["connection"] == "other")

    code, text = api("/api/connections/delete", {"name": "other"}, expect_error=True)
    check("cannot delete active", code == 400, code)
    code, text = api("/api/connections/delete", {"name": "work"})
    check("delete inactive", json.loads(text)["deleted"] == "work")
    check("delete persisted", "work" not in json.load(open(config_path, encoding="utf-8"))["connections"])

    # MiniMax Anthropic preset: domestic URL, bearer auth, reasoning warning.
    code, text = api("/api/connections/save", {
        "name": "mm",
        "provider": "minimax",
        "base_url": "https://api.minimaxi.com/anthropic",
        "api_format": "anthropic-messages",
        "model": "MiniMax-M3.1-Flash",
        "api_key": "sk-mm",
        "make_active": True,
    })
    mm = json.loads(text)
    check("minimax saved", mm["connection"]["name"] == "mm")
    check("minimax active", mm["active"] == "mm")
    check("minimax reasoning warning", bool(mm["reasoning_warning"]))
    config = json.load(open(config_path, encoding="utf-8"))
    check("minimax auth bearer", config["connections"]["mm"]["anthropic_auth"] == "bearer")
    state = api_json("/api/state")
    check("anthropic state", state["api_format"] == "anthropic-messages")

    # Validation errors.
    code, text = api("/api/connections/save", {
        "name": "bad name",
        "base_url": model_base,
        "api_format": "openai-chat-completions",
        "model": "x",
    }, expect_error=True)
    check("bad name rejected", code == 400, code)
    code, text = api("/api/connections/save", {
        "name": "badurl",
        "base_url": "ftp://example.com",
        "api_format": "openai-chat-completions",
        "model": "x",
    }, expect_error=True)
    check("bad url rejected", code == 400, code)
    code, text = api("/api/connections/save", {
        "name": "badformat",
        "base_url": model_base,
        "api_format": "bogus",
        "model": "x",
    }, expect_error=True)
    check("bad format rejected", code == 400, code)

    # Shell listing and switching.
    shells = api_json("/api/shells")
    current_name = shells["current"]["name"]
    check("shells current", bool(current_name), shells)
    check("shells available", bool(shells["available"]), shells)
    state = api_json("/api/state")
    check("state shell", state["shell"]["current"]["name"] == current_name, state.get("shell"))

    switched_name = None
    for item in shells["available"]:
        if item["name"] != current_name:
            switched_name = item["name"]
            break
    if switched_name:
        switched = api_json("/api/shell", {"name": switched_name})
        check("shell switched", switched["current"]["name"] == switched_name, switched)
        check(
            "shell state switched",
            api_json("/api/state")["shell"]["current"]["name"] == switched_name,
        )
        restored = api_json("/api/shell", {"name": current_name})
        check("shell restored", restored["current"]["name"] == current_name, restored)
    code, text = api("/api/shell", {"name": "definitely-not-a-shell"}, expect_error=True)
    check("bad shell rejected", code == 400, code)
finally:
    web.terminate()
    try:
        web.wait(timeout=5)
    except subprocess.TimeoutExpired:
        web.kill()
    log_handle.close()
    model.shutdown()
    shutil.rmtree(home, ignore_errors=True)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
