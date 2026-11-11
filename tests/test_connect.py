from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest.mock
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


def run_wizard(config, path, inputs, secret="sk-test-abcdef", models=None, provider=None):
    answers = iter(inputs)
    output = []
    connection = agent.run_connect_wizard(
        config,
        path,
        provider=provider,
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: secret,
        output_fn=output.append,
        model_lister=lambda _connection: list(models or []),
    )
    return connection, "\n".join(output)


root = tempfile.mkdtemp(prefix="connect-test-")

# ---------- custom wizard ----------
path = os.path.join(root, "custom.json")
config = agent.empty_config()
connection, output = run_wizard(
    config,
    path,
    ["3", "1", "http://127.0.0.1:9/v1", "local", "2", "y"],
    models=["model-a", "model-b"],
)
check("custom saved", connection is not None)
check("custom base", connection.base_url == "http://127.0.0.1:9/v1")
check("custom model", connection.model == "model-b")
check("custom active", config["active"] == "local")
check("custom file exists", os.path.exists(path))
check("custom summary", "已连接: local" in output, output[-300:])
check("custom key masked", "sk-test-abcdef" not in output)

# ---------- deepseek preset ----------
path = os.path.join(root, "deepseek.json")
config = agent.empty_config()
connection, output = run_wizard(
    config,
    path,
    ["1", "", "", "ds", "", "y"],
    models=["deepseek-chat", "deepseek-reasoner"],
)
check("deepseek base", connection.base_url == "https://api.deepseek.com/v1")
check("deepseek format", connection.api_format == "openai-chat-completions")
check("deepseek model", connection.model == "deepseek-chat")
check("deepseek auth", connection.anthropic_auth == "x-api-key")

# ---------- minimax anthropic preset ----------
path = os.path.join(root, "minimax.json")
config = agent.empty_config()
connection, output = run_wizard(
    config,
    path,
    ["2", "3", "", "mm", "", "1", "y"],
    models=["MiniMax-M3.1-Flash"],
)
check("minimax base", connection.base_url == "https://api.minimaxi.com/anthropic")
check("minimax format", connection.api_format == "anthropic-messages")
check("minimax auth", connection.anthropic_auth == "bearer")
check("minimax domestic", "api.minimax.io" not in json.dumps(config))
check("minimax model", connection.model == "MiniMax-M3.1-Flash")

# ---------- cancel ----------
path = os.path.join(root, "cancel.json")
config = agent.empty_config()
connection, output = run_wizard(config, path, ["q"])
check("cancel returns none", connection is None)
check("cancel does not write", not os.path.exists(path))

# ---------- keep existing key ----------
path = os.path.join(root, "keep.json")
config = agent.empty_config()
config["connections"]["ds"] = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "api_format": "openai-chat-completions",
    "model": "deepseek-chat",
    "api_key": "sk-old-key",
}
config["active"] = "ds"
agent.save_config(config, path)
connection, output = run_wizard(
    config,
    path,
    ["", "", "ds", "1", "y"],
    secret="",
    models=["deepseek-chat", "deepseek-reasoner"],
    provider="deepseek",
)
check("keep key", connection.api_key == "sk-old-key")
check("keep key config", config["connections"]["ds"]["api_key"] == "sk-old-key")
check("keep warning", "已存在" in output)

# ---------- model fetch failure -> manual ----------
path = os.path.join(root, "manual.json")
config = agent.empty_config()


def failing_lister(_connection):
    raise agent.ChatError("HTTP 404 not found")


answers = iter(["1", "http://127.0.0.1:9/v1", "manual", "typed-model", "y"])
output = []
connection = agent.run_connect_wizard(
    config,
    path,
    provider="custom",
    input_fn=lambda _prompt: next(answers),
    secret_fn=lambda _prompt: "sk-manual",
    output_fn=output.append,
    model_lister=failing_lister,
)
check("manual model after failure", connection is not None and connection.model == "typed-model")
check("manual failure message", "获取模型列表失败" in "\n".join(output))

# ---------- use / list / delete ----------
path = os.path.join(root, "commands.json")
config = agent.empty_config()
for name, model in (("one", "one-model"), ("two", "two-model")):
    config["connections"][name] = {
        "provider": "custom",
        "base_url": "https://example.com/v1",
        "api_format": "openai-chat-completions",
        "model": model,
        "api_key": f"sk-{name}",
    }
config["active"] = "one"
agent.save_config(config, path)


class FakeClient:
    def __init__(self, connection):
        self.model = connection.model
        self.reasoning = None


def factory(connection):
    return FakeClient(connection)


defaults = agent.Connection("https://example.com/v1", "x", "openai-chat-completions")
stdout = io.StringIO()
with contextlib.redirect_stdout(stdout):
    client, selected = agent.run_connect_command(
        "list", config, path, defaults, client_factory=factory, reasoning=None
    )
check("list returns nothing", client is None and selected is None)
check("list shows names", "one" in stdout.getvalue() and "two" in stdout.getvalue())
check("list marks active", "* one" in stdout.getvalue())

client, selected = agent.run_connect_command(
    "use two", config, path, defaults, client_factory=factory, reasoning=None
)
check("use client", client is not None and client.model == "two-model")
check("use active", config["active"] == "two")
check("use persisted", agent.load_config(path)["active"] == "two")

client, selected = agent.run_connect_command(
    "delete two", config, path, defaults, client_factory=factory, reasoning=None
)
check("delete active rejected", client is None and selected is None and "two" in config["connections"])

with unittest.mock.patch("builtins.input", return_value="y"):
    client, selected = agent.run_connect_command(
        "delete one", config, path, defaults, client_factory=factory, reasoning=None
    )
check("delete non-active", "one" not in config["connections"])
check("delete persisted", "one" not in agent.load_config(path)["connections"])

stdout = io.StringIO()
with contextlib.redirect_stdout(stdout):
    client, selected = agent.run_connect_command(
        "bogus", config, path, defaults, client_factory=factory, reasoning=None
    )
check("bogus action rejected", client is None and "usage:" in stdout.getvalue())

# ---------- CLI: --connect then one-shot ----------
class ServerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "model=" + str(payload.get("model"))},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), ServerHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}/v1"

home = tempfile.mkdtemp(prefix="connect-cli-")
config_path = os.path.join(home, "config.json")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=config_path)
for _name in ("OPENAI_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
    env.pop(_name, None)
script = (
    f"3\n1\n{base}\nlocal\nsk-cli\n1\ny\n"
)
result = subprocess.run(
    [sys.executable, AGENT, "--connect", "hi"],
    input=script,
    text=True,
    capture_output=True,
    env=env,
    timeout=30,
)
check("cli connect exit", result.returncode == 0, result.stderr[-500:])
check("cli connect reply", "model=fake-model" in result.stdout, result.stdout[-500:])
check("cli connect config", os.path.exists(config_path))
saved = json.load(open(config_path, encoding="utf-8"))
check("cli connect active", saved["active"] == "local")
check("cli connect model", saved["connections"]["local"]["model"] == "fake-model")

# --no-config only ignores saved values at startup; management must not drop them.
no_config_path = os.path.join(home, "no-config.json")
json.dump({
    "version": 1,
    "active": "keep",
    "connections": {
        "keep": {
            "provider": "custom",
            "base_url": base,
            "api_format": "openai-chat-completions",
            "model": "keep-model",
            "api_key": "sk-keep",
        },
    },
}, open(no_config_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
script = f"3\n1\n{base}\nnewone\nsk-new\n1\ny\n"
result = subprocess.run(
    [sys.executable, AGENT, "--no-config", "--connect", "--config", no_config_path],
    input=script,
    text=True,
    capture_output=True,
    env=env,
    timeout=30,
)
no_config_saved = json.load(open(no_config_path, encoding="utf-8"))
check("no-config connect exit", result.returncode == 0, result.stderr[-500:])
check("no-config keeps old connection", "keep" in no_config_saved["connections"])
check("no-config adds new connection", "newone" in no_config_saved["connections"])
check("no-config active new", no_config_saved["active"] == "newone")

# ---------- CLI: --profile / env / --no-env ----------
saved["connections"]["second"] = {
    "provider": "custom",
    "base_url": base,
    "api_format": "openai-chat-completions",
    "model": "second-model",
    "api_key": "sk-second",
}
saved["active"] = "local"
json.dump(saved, open(config_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

result = subprocess.run(
    [sys.executable, AGENT, "--profile", "second", "hi"],
    text=True,
    capture_output=True,
    env=env,
    timeout=30,
)
check("profile cli", result.returncode == 0 and "model=second-model" in result.stdout, result.stdout[-300:])

env_model = dict(env, OPENAI_MODEL="env-model")
result = subprocess.run(
    [sys.executable, AGENT, "--profile", "second", "hi"],
    text=True,
    capture_output=True,
    env=env_model,
    timeout=30,
)
check("env beats profile", "model=env-model" in result.stdout, result.stdout[-300:])
check("env warning", "takes precedence" in result.stderr, result.stderr[-500:])

result = subprocess.run(
    [sys.executable, AGENT, "--profile", "second", "--no-env", "hi"],
    text=True,
    capture_output=True,
    env=env_model,
    timeout=30,
)
check("no-env beats env", "model=second-model" in result.stdout, result.stdout[-300:])

server.shutdown()
shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(home, ignore_errors=True)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
