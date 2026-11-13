import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py")
)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


home = tempfile.mkdtemp(prefix="model-switch-")
config_path = os.path.join(home, "config.json")
config = agent.empty_config()
config["connections"]["first"] = {
    "provider": "custom",
    "base_url": "http://127.0.0.1:1/v1",
    "api_format": "openai-chat-completions",
    "model": "model-a",
    "api_key": "key-a",
}
config["connections"]["second"] = {
    "provider": "custom",
    "base_url": "http://127.0.0.1:2/v1",
    "api_format": "openai-chat-completions",
    "model": "model-b",
    "api_key": "key-b",
}
config["active"] = "first"
agent.save_config(config, config_path)

agent_path = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=config_path)
result = subprocess.run(
    [
        sys.executable,
        agent_path,
        "--config",
        config_path,
        "--no-env",
        "--shell",
        "auto",
    ],
    input=(
        "/model list all\n"
        "/model use second model-b\n"
        "/model\n"
        "/model use --save second new-model\n"
        "/exit\n"
    ),
    text=True,
    capture_output=True,
    env=env,
    timeout=40,
)
check("model switch exit", result.returncode == 0, result.stderr[-1000:])
check("list all first", "first" in result.stdout and "model-a" in result.stdout, result.stdout[-2000:])
check("list all second", "second" in result.stdout and "model-b" in result.stdout, result.stdout[-2000:])
check("switch connection", "current connection: second" in result.stdout, result.stdout[-2000:])
check("switch model", "model: model-b" in result.stdout, result.stdout[-2000:])
check("switch save", "current connection: second" in result.stdout, result.stdout[-2000:])
saved = json.load(open(config_path, encoding="utf-8"))
check("save active", saved["active"] == "second", saved)
check("save model", saved["connections"]["second"]["model"] == "new-model", saved)
check("save preserves first", saved["connections"]["first"]["model"] == "model-a", saved)

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
