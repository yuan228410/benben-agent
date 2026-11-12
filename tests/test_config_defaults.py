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


root = tempfile.mkdtemp(prefix="config-defaults-")

# ---------- empty config and roundtrip ----------
config = agent.empty_config()
check("version 2", config["version"] == 2, config)
defaults = config["defaults"]
check("defaults cwd", defaults["cwd"] is None, defaults)
check("defaults shell", defaults["shell"] == "auto", defaults)
check("defaults memory", defaults["memory"] is False, defaults)
check("defaults web", defaults["web"]["port"] == agent.WEB_PORT, defaults)

path = os.path.join(root, "roundtrip.json")
agent.save_config(config, path)
loaded = agent.load_config(path)
check("defaults roundtrip", loaded["defaults"]["max_steps"] == 100, loaded["defaults"])
check("defaults web roundtrip", loaded["defaults"]["web"]["host"] == "127.0.0.1")
check("defaults saved", "defaults" in json.load(open(path, encoding="utf-8")))

missing_path = os.path.join(root, "created.json")
check("ensure creates", agent.ensure_config_file(agent.empty_config(), missing_path) is True)
check("ensure file exists", os.path.exists(missing_path))
check("ensure does not rewrite", agent.ensure_config_file(agent.empty_config(), missing_path) is False)

# ---------- invalid defaults fallback ----------
invalid_path = os.path.join(root, "invalid.json")
with open(invalid_path, "w", encoding="utf-8") as handle:
    json.dump({
        "version": 2,
        "connections": {},
        "defaults": {
            "max_steps": 0,
            "timeout": -1,
            "shell": "",
            "reasoning": "not-a-reasoning",
            "memory_daily_time": "25:00",
            "memory": True,
            "web": {"host": "", "port": 99999},
        },
    }, handle)
with open(os.devnull, "w") as devnull:
    import contextlib
    with contextlib.redirect_stderr(devnull):
        invalid = agent.load_config(invalid_path)
check("invalid max_steps fallback", invalid["defaults"]["max_steps"] == 100, invalid["defaults"])
check("invalid timeout fallback", invalid["defaults"]["timeout"] == 120.0, invalid["defaults"])
check("invalid shell fallback", invalid["defaults"]["shell"] == "auto", invalid["defaults"])
check("invalid reasoning fallback", invalid["defaults"]["reasoning"] is None, invalid["defaults"])
check("invalid daily time fallback", invalid["defaults"]["memory_daily_time"] is None, invalid["defaults"])
check("valid memory kept", invalid["defaults"]["memory"] is True, invalid["defaults"])
check("invalid web fallback", invalid["defaults"]["web"]["port"] == agent.WEB_PORT, invalid["defaults"])
check("invalid web host fallback", invalid["defaults"]["web"]["host"] == "127.0.0.1", invalid["defaults"])


# ---------- resolve settings precedence ----------
class Args:
    cwd = None
    shell = None
    shell_enabled = None
    shell_fallback = None
    confirm = None
    thinking = None
    reasoning = None
    max_steps = None
    timeout = None
    temperature = None
    max_tokens = None
    plain = None
    verbose_tools = None
    skills = None
    only_own_skills = None
    skill_scripts = None
    schedule = None
    memory = None
    memory_daily = None
    memory_daily_time = None
    host = None
    port = None


resolved_config = agent.empty_config()
resolved_config["defaults"].update({
    "cwd": "/tmp/config-cwd",
    "shell": "bash",
    "confirm": True,
    "thinking": True,
    "max_steps": 7,
    "timeout": 5.5,
    "skills": True,
    "skill_scripts": False,
    "memory": True,
    "memory_daily": True,
    "memory_daily_time": "23:50",
    "web": {"host": "0.0.0.0", "port": 9999},
})
resolved = Args()
agent.resolve_settings(resolved, resolved_config)
check("resolve cwd", resolved.cwd == "/tmp/config-cwd", resolved.cwd)
check("resolve shell", resolved.shell == "bash", resolved.shell)
check("resolve confirm", resolved.confirm is True)
check("resolve max steps", resolved.max_steps == 7)
check("resolve timeout", resolved.timeout == 5.5)
check("resolve skills", resolved.skills is True)
check("resolve scripts", resolved.skill_scripts is False)
check("resolve memory", resolved.memory is True)
check("resolve daily time", resolved.memory_daily_time == "23:50")
check("resolve web", resolved.host == "0.0.0.0" and resolved.port == 9999)

override = Args()
override.cwd = "/cli-cwd"
override.memory = False
override.max_steps = 3
agent.resolve_settings(override, resolved_config)
check("cli cwd wins", override.cwd == "/cli-cwd")
check("cli memory wins", override.memory is False)
check("cli max steps wins", override.max_steps == 3)

# ---------- CLI creates defaults ----------
agent_path = str(Path(__file__).resolve().parents[1] / "benben-agent.py")


def run_agent(home, workspace, args, user_input, config_path=None):
    env = dict(
        os.environ,
        HOME=home,
        BENBEN_CONFIG=config_path or os.path.join(home, ".benben-agent", "config.json"),
    )
    return subprocess.run(
        [
            sys.executable,
            agent_path,
            "--no-env",
            "--base-url",
            "http://127.0.0.1:1/v1",
            "--api-key",
            "k",
            "-m",
            "m",
            "--shell",
            "auto",
            *args,
        ],
        input=user_input,
        text=True,
        capture_output=True,
        env=env,
        cwd=workspace,
        timeout=40,
    )


home1 = tempfile.mkdtemp(prefix="config-cli-")
workspace1 = tempfile.mkdtemp(prefix="config-cli-ws-")
result = run_agent(home1, workspace1, [], "/status\n/exit\n")
config_path1 = os.path.join(home1, ".benben-agent", "config.json")
check("cli default config created", os.path.isfile(config_path1), result.stderr[-500:])
written = json.load(open(config_path1, encoding="utf-8"))
check("cli default version", written["version"] == 2, written)
check("cli default max steps", written["defaults"]["max_steps"] == 100, written["defaults"])
check("cli default memory", written["defaults"]["memory"] is False, written["defaults"])
check("cli status memory off", "memory: off" in result.stdout, result.stdout[-800:])

# ---------- config defaults applied, CLI overrides ----------
home2 = tempfile.mkdtemp(prefix="config-apply-")
workspace2 = tempfile.mkdtemp(prefix="config-apply-ws-")
config_path2 = os.path.join(home2, ".benben-agent", "config.json")
os.makedirs(os.path.dirname(config_path2), exist_ok=True)
base = agent.empty_config()
base["defaults"].update({
    "cwd": workspace2,
    "memory": True,
    "thinking": True,
    "max_steps": 7,
})
with open(config_path2, "w", encoding="utf-8") as handle:
    json.dump(base, handle, ensure_ascii=False)
result = run_agent(home2, workspace2, [], "/status\n/exit\n")
check("config memory applied", "memory: on" in result.stdout, result.stdout[-1000:])
check("config thinking applied", "thinking: on" in result.stdout, result.stdout[-1000:])
check("config cwd applied", f"workspace: {workspace2}" in result.stdout, result.stdout[-1000:])

result = run_agent(home2, workspace2, ["--no-memory", "--no-thinking"], "/status\n/exit\n")
check("cli memory override", "memory: off" in result.stdout, result.stdout[-1000:])
check("cli thinking override", "thinking: off" in result.stdout, result.stdout[-1000:])

# ---------- /config save ----------
home3 = tempfile.mkdtemp(prefix="config-save-")
workspace3 = tempfile.mkdtemp(prefix="config-save-ws-")
config_path3 = os.path.join(home3, ".benben-agent", "config.json")
result = run_agent(
    home3,
    workspace3,
    ["--memory", "--thinking", "--max-steps", "7"],
    "/config save\n/exit\n",
)
check("config save exit", result.returncode == 0, result.stderr[-500:])
saved = json.load(open(config_path3, encoding="utf-8"))["defaults"]
check("config save memory", saved["memory"] is True, saved)
check("config save thinking", saved["thinking"] is True, saved)
check("config save max steps", saved["max_steps"] == 7, saved)
check("config save cwd", saved["cwd"] == workspace3, saved)
check("config save skill scripts", saved["skill_scripts"] is True, saved)

# ---------- --no-config does not create ----------
home4 = tempfile.mkdtemp(prefix="config-none-")
workspace4 = tempfile.mkdtemp(prefix="config-none-ws-")
result = run_agent(home4, workspace4, ["--no-config"], "/exit\n")
check("no-config exit", result.returncode == 0, result.stderr[-500:])
check(
    "no-config does not create",
    not os.path.exists(os.path.join(home4, ".benben-agent", "config.json")),
)

# ---------- explicit negative overrides config ----------
home5 = tempfile.mkdtemp(prefix="config-neg-")
workspace5 = tempfile.mkdtemp(prefix="config-neg-ws-")
config_path5 = os.path.join(home5, ".benben-agent", "config.json")
os.makedirs(os.path.dirname(config_path5), exist_ok=True)
neg = agent.empty_config()
neg["defaults"].update({
    "memory": True,
    "memory_daily": True,
    "memory_daily_time": "23:50",
    "thinking": True,
    "plain": True,
})
with open(config_path5, "w", encoding="utf-8") as handle:
    json.dump(neg, handle, ensure_ascii=False)
result = run_agent(
    home5,
    workspace5,
    ["--no-memory", "--no-thinking", "--no-plain"],
    "/status\n/exit\n",
)
check("negative memory override", "memory: off" in result.stdout, result.stdout[-1000:])
check("negative thinking override", "thinking: off" in result.stdout, result.stdout[-1000:])
check("negative memory no enabled line", "[memory] enabled" not in result.stderr, result.stderr[-1000:])

# ---------- /config show ----------
result = run_agent(home1, workspace1, [], "/config\n/exit\n")
check("config show path", config_path1 in result.stdout, result.stdout[-800:])
check("config show defaults", '"max_steps": 100' in result.stdout, result.stdout[-800:])

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
