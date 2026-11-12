import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

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


# ---------- presets and config ----------
check("preset count", len(agent.AUTO_COMPACT_PRESETS) == 8, agent.AUTO_COMPACT_PRESETS)
check("preset order", list(agent.AUTO_COMPACT_PRESETS)[0] == "minimal")
check("preset maximum", agent.AUTO_COMPACT_PRESETS["maximum"]["keep"] == 1)
args = SimpleNamespace(
    auto_compact_enabled=True,
    auto_compact_level="aggressive",
    auto_compact_threshold=None,
    auto_compact_target=None,
    auto_compact_keep=None,
    idle_compact_enabled=True,
    idle_compact_minutes=5,
    idle_compact_level=None,
    idle_compact_min_percent=10,
    idle_compact_min_messages=2,
)
auto = agent.auto_compact_config(args)
check("auto config level", auto["level"] == "aggressive", auto)
check("auto config threshold", auto["threshold"] == 70, auto)
check("auto config target", auto["target_ratio"] == 35, auto)
check("auto config keep", auto["keep"] == 3, auto)
idle = agent.idle_compact_config(args)
check("idle inherits level", idle["level"] == "aggressive", idle)
check("idle minutes", idle["minutes"] == 5, idle)
check("idle target", idle["target_ratio"] == 35, idle)
check("idle min context", idle["min_context_percent"] == 10, idle)

override = SimpleNamespace(**vars(args))
override.auto_compact_threshold = 50
override.auto_compact_target = 20
override.auto_compact_keep = 2
override.idle_compact_level = "light"
auto = agent.auto_compact_config(override)
idle = agent.idle_compact_config(override)
check("auto override threshold", auto["threshold"] == 50, auto)
check("auto override target", auto["target_ratio"] == 20, auto)
check("auto override keep", auto["keep"] == 2, auto)
check("idle explicit level", idle["level"] == "light", idle)
check("idle target override", idle["target_ratio"] == 20, idle)
check("idle keep override", idle["keep"] == 2, idle)

# ---------- config normalization ----------
raw = {
    "auto_compact": {
        "enabled": True,
        "level": "maximum",
        "threshold": 45,
        "target_ratio": 8,
        "keep": 2,
    },
    "idle_compact": {
        "enabled": True,
        "minutes": 3,
        "level": "very-light",
        "min_context_percent": 5,
        "min_messages": 2,
    },
}
normalized = agent._normalize_defaults(raw)
check("normalize auto", normalized["auto_compact"]["enabled"] is True, normalized)
check("normalize level", normalized["auto_compact"]["level"] == "maximum")
check("normalize idle", normalized["idle_compact"]["minutes"] == 3, normalized)
check("normalize idle level", normalized["idle_compact"]["level"] == "very-light")


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
    auto_compact_enabled = None
    auto_compact_level = None
    auto_compact_threshold = None
    auto_compact_target = None
    auto_compact_keep = None
    idle_compact_enabled = None
    idle_compact_minutes = None
    idle_compact_level = None
    idle_compact_min_percent = None
    idle_compact_min_messages = None
    host = None
    port = None


cfg = agent.empty_config()
cfg["defaults"]["auto_compact"].update({
    "enabled": True,
    "level": "extreme",
    "threshold": 55,
    "target_ratio": 15,
    "keep": 1,
})
cfg["defaults"]["idle_compact"].update({
    "enabled": True,
    "minutes": 7,
    "level": "very-aggressive",
    "min_context_percent": 12,
    "min_messages": 4,
})
resolved = Args()
agent.resolve_settings(resolved, cfg)
check("resolve auto enabled", resolved.auto_compact_enabled is True)
check("resolve auto level", resolved.auto_compact_level == "extreme")
check("resolve idle minutes", resolved.idle_compact_minutes == 7)
check("resolve idle level", resolved.idle_compact_level == "very-aggressive")

# ---------- keep count and compaction ----------
messages = [{"role": "system", "content": "system"}]
for index in range(6):
    messages.append({"role": "user", "content": (f"user {index} " + "x" * 200)})
    messages.append({"role": "assistant", "content": (f"assistant {index} " + "y" * 200)})
info = agent._fallback_model_info()._replace(context_window=1000)
keep = agent._auto_keep_count(messages, 500, 5)
check("auto keep count", isinstance(keep, int) and 1 <= keep <= 5, keep)
tiny_keep = agent._auto_keep_count(messages, 1, 5)
check("auto keep minimum", tiny_keep == 1, tiny_keep)


class FakeClient:
    model = "m"
    reasoning = None
    last_reasoning_ignored = False
    last_usage = {"total_tokens": 5}
    last_finish_reason = "stop"
    last_turn_usage = {}
    last_turn_truncated = False

    def chat(self, messages, tools):
        return {"role": "assistant", "content": "## 目标\n自动压缩测试"}


def make_messages():
    rows = [{"role": "system", "content": "system"}]
    for index in range(6):
        rows.append({"role": "user", "content": (f"user {index} " + "x" * 200)})
        rows.append({"role": "assistant", "content": (f"assistant {index} " + "y" * 200)})
    return rows


client = FakeClient()
auto_config = {"enabled": True, "threshold": 50, "target_ratio": 50, "keep": 5}
below = make_messages()[:3]
check("below threshold", agent.maybe_auto_compact(client, below, None, info, auto_config) is None)
working = make_messages()
stats = agent.maybe_auto_compact(client, working, None, info, auto_config)
check("auto compact triggered", stats is not None and not stats.get("error"), stats)
check("auto compact summary", working[1].get("_compact") is True, working[:2])
check("auto compact reduced", len(working) < len(make_messages()), len(working))
check("auto compact tokens", stats["after_tokens"] < stats["before_tokens"], stats)

disabled = dict(auto_config, enabled=False)
check(
    "disabled no compact",
    agent.maybe_auto_compact(client, make_messages(), None, info, disabled) is None,
)

# ---------- idle compaction ----------
idle_config = {
    "enabled": True,
    "minutes": 1,
    "target_ratio": 50,
    "keep": 5,
    "min_context_percent": 0,
    "min_messages": 1,
}
not_idle = make_messages()
check(
    "not idle",
    agent.maybe_idle_compact(
        client, not_idle, None, info, idle_config, time.monotonic()
    ) is None,
)
idle_messages = make_messages()
idle_stats = agent.maybe_idle_compact(
    client,
    idle_messages,
    None,
    info,
    idle_config,
    time.monotonic() - 120,
)
check("idle triggered", idle_stats is not None and not idle_stats.get("error"), idle_stats)
check("idle summary", idle_messages[1].get("_compact") is True, idle_messages[:2])
check(
    "idle min messages",
    agent.maybe_idle_compact(
        client,
        make_messages()[:3],
        None,
        info,
        dict(idle_config, min_messages=100),
        time.monotonic() - 120,
    ) is None,
)
check(
    "idle min context",
    agent.maybe_idle_compact(
        client,
        make_messages(),
        None,
        info,
        dict(idle_config, min_context_percent=101),
        time.monotonic() - 120,
    ) is None,
)

# ---------- CLI integration ----------
agent_path = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
home = tempfile.mkdtemp(prefix="auto-compact-")
workspace = tempfile.mkdtemp(prefix="auto-compact-ws-")
config_path = os.path.join(home, ".benben-agent", "config.json")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=config_path)
result = subprocess.run(
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
        "--auto-compact",
        "--auto-compact-level",
        "aggressive",
        "--idle-compact",
        "--idle-compact-minutes",
        "5",
    ],
    input=(
        "/compact auto\n"
        "/compact auto presets\n"
        "/compact idle\n"
        "/compact auto level maximum\n"
        "/compact idle level light\n"
        "/config save\n"
        "/exit\n"
    ),
    text=True,
    capture_output=True,
    env=env,
    cwd=workspace,
    timeout=40,
)
check("cli compact exit", result.returncode == 0, result.stderr[-800:])
check("cli auto status", "auto compact: on" in result.stdout, result.stdout[-2000:])
check("cli presets", "very-aggressive" in result.stdout and "maximum" in result.stdout)
check("cli idle status", "idle compact: on" in result.stdout, result.stdout[-2000:])
check("cli auto level set", "level: maximum" in result.stdout, result.stdout[-2000:])
check("cli idle level set", "level: light" in result.stdout, result.stdout[-2000:])
saved = json.load(open(config_path, encoding="utf-8"))["defaults"]
check("save auto enabled", saved["auto_compact"]["enabled"] is True, saved)
check("save auto level", saved["auto_compact"]["level"] == "maximum", saved)
check("save idle minutes", saved["idle_compact"]["minutes"] == 5, saved)
check("save idle level", saved["idle_compact"]["level"] == "light", saved)

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
