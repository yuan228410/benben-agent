"""Changing the stream timeouts in the web settings must apply immediately.

The two values live on the chat client, and switching models replaces that
client, so there are three things to check:
  * saving a new value updates the live client,
  * a model switch keeps the values instead of resetting them to the defaults,
  * the keys are advertised as live so the UI does not tell the user to restart.

The defaults themselves are pinned too: one 300 second value used to cover both
waits, and a connect wait that long made a gateway which never answered look
like a frozen interface.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(ROOT, "benben-agent.py")

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name} {detail}")
        failures.append(name)


# ---------- the two waits are separate values ----------

spec = importlib.util.spec_from_file_location("agent_t", AGENT)
agent = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(agent)

defaults = agent.default_settings()
check(
    "connect timeout is shorter than the idle timeout",
    defaults["stream_connect_timeout"] < defaults["stream_idle_timeout"],
    (defaults["stream_connect_timeout"], defaults["stream_idle_timeout"]),
)
check(
    "defaults are 30/60, not one 300 second value",
    defaults["stream_connect_timeout"] == 30
    and defaults["stream_idle_timeout"] == 60,
    (defaults["stream_connect_timeout"], defaults["stream_idle_timeout"]),
)
fresh = agent.ChatClient("http://127.0.0.1:9/v1", "", "m")
check(
    "client defaults match the settings defaults",
    fresh.stream_connect_timeout == 30.0 and fresh.stream_idle_timeout == 60.0,
    (fresh.stream_connect_timeout, fresh.stream_idle_timeout),
)
check(
    "both keys are live-updatable",
    "stream_connect_timeout" in agent.LIVE_DEFAULT_KEYS
    and "stream_idle_timeout" in agent.LIVE_DEFAULT_KEYS,
    sorted(agent.LIVE_DEFAULT_KEYS),
)

# ---------- the live web settings really apply ----------

home = os.environ["HOME"]
workspace = os.path.join(home, "ws")
os.makedirs(workspace, exist_ok=True)

# Two connections so a model switch can be exercised.
config = {
    "version": 2,
    "active": "alpha",
    "connections": {
        "alpha": {
            "provider": "custom",
            "base_url": "http://127.0.0.1:9/v1",
            "api_format": "openai-chat-completions",
            "model": "m",
            "api_key": "x",
        },
        "beta": {
            "provider": "custom",
            "base_url": "http://127.0.0.1:9/v1",
            "api_format": "openai-chat-completions",
            "model": "m2",
            "api_key": "x",
        },
    },
}
config_path = os.path.join(home, "cfg.json")
with open(config_path, "w", encoding="utf-8") as fh:
    json.dump(config, fh)
os.chmod(config_path, 0o600)

proc = subprocess.Popen(
    [
        sys.executable, AGENT, "--web", "--port", "0", "--cwd", workspace,
        "--config", config_path, "--stream", "--max-steps", "2",
    ],
    cwd=ROOT,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=dict(os.environ, BENBEN_CONFIG=config_path),
)

url_line = ""
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    line = proc.stdout.readline()
    if not line:
        break
    if "web ui:" in line:
        url_line = line.strip()
        break
if not url_line:
    print("FAIL web server did not start")
    proc.kill()
    raise SystemExit(1)

base = re.search(r"(http://[^\s]+)/\?", url_line).group(1)
token = re.search(r"token=([^\s&]+)", url_line).group(1)


def drain():
    for _line in proc.stdout:
        pass


threading.Thread(target=drain, daemon=True).start()


def call(path, payload=None):
    url = f"{base}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("X-Token", token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


status, settings = call("/api/settings")
check("settings readable", status == 200, settings)
live_keys = settings.get("live_keys") or []
check(
    "stream timeouts are advertised as live",
    "stream_idle_timeout" in live_keys and "stream_connect_timeout" in live_keys,
    live_keys,
)
check(
    "saved defaults show the new values",
    settings["defaults"]["stream_idle_timeout"] == 60
    and settings["defaults"]["stream_connect_timeout"] == 30,
    settings["defaults"],
)

status, body = call(
    "/api/settings",
    {"defaults": {"stream_idle_timeout": 90, "stream_connect_timeout": 45}},
)
check("save accepted", status == 200, body)
applied = (body.get("applied") or []) if isinstance(body, dict) else []
check(
    "save reports both keys applied",
    "stream_idle_timeout" in applied and "stream_connect_timeout" in applied,
    applied,
)

status, runtime = call("/api/runtime")
timeouts = runtime.get("stream_timeouts") if isinstance(runtime, dict) else None
check(
    "runtime reflects the new timeouts",
    timeouts == {"idle": 90, "connect": 45},
    timeouts,
)

# Switching connections rebuilds the client; the saved values must survive.
status, body = call("/api/connections/use", {"name": "beta"})
check("connection switch accepted", status == 200, body)
if status == 200:
    status, runtime = call("/api/runtime")
    timeouts = runtime.get("stream_timeouts") if isinstance(runtime, dict) else None
    check(
        "model switch keeps the saved timeouts",
        timeouts == {"idle": 90, "connect": 45},
        timeouts,
    )

proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()

print()
if failures:
    print("FAILURES:", failures)
    raise SystemExit(1)
print("FAILURES: none")
