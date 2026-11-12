from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

spec = importlib.util.spec_from_file_location(
    "agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py")
)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


def sample(name="deepseek", model="deepseek-chat", key="sk-test-123456"):
    return {
        "provider": name,
        "base_url": "https://api.deepseek.com/v1",
        "api_format": "openai-chat-completions",
        "model": model,
        "api_key": key,
    }


root = tempfile.mkdtemp(prefix="config-test-")
config_dir = os.path.join(root, "new", "dir")
config_path = os.path.join(config_dir, "config.json")

# ---------- save / load ----------
config = agent.empty_config()
config["connections"]["deepseek"] = sample()
config["connections"]["mm"] = {
    "provider": "minimax",
    "base_url": "https://api.minimaxi.com/anthropic",
    "api_format": "anthropic-messages",
    "model": "MiniMax-M3.1-Flash",
    "api_key": "sk-mm",
    "anthropic_auth": "bearer",
}
config["active"] = "deepseek"
saved = agent.save_config(config, config_path)
check("save returns path", saved == config_path)
check("config exists", os.path.exists(config_path))
if os.name != "nt":
    check("config mode 0600", (os.stat(config_path).st_mode & 0o777) == 0o600)
    check("config dir mode 0700", (os.stat(config_dir).st_mode & 0o777) == 0o700)
loaded = agent.load_config(config_path)
check("roundtrip active", loaded["active"] == "deepseek")
check("roundtrip key", loaded["connections"]["deepseek"]["api_key"] == "sk-test-123456")
check("roundtrip auth", loaded["connections"]["mm"]["anthropic_auth"] == "bearer")
check("roundtrip version", loaded["connections"]["mm"]["anthropic_version"] == "2023-06-01")
check("openai omits auth", "anthropic_auth" not in loaded["connections"]["deepseek"])
check("openai omits version", "anthropic_version" not in loaded["connections"]["deepseek"])
check(
    "atomic temp cleaned",
    not [name for name in os.listdir(config_dir) if name.startswith(".benben-config-")],
)

# ---------- missing / malformed ----------
missing = os.path.join(root, "missing.json")
check("missing config is empty", agent.load_config(missing) == agent.empty_config())
broken = os.path.join(root, "broken.json")
with open(broken, "w", encoding="utf-8") as handle:
    handle.write("{not json")
try:
    agent.load_config(broken)
    check("broken config raises", False)
except agent.ConfigError:
    check("broken config raises", True)

mixed = os.path.join(root, "mixed.json")
with open(mixed, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "version": 1,
            "active": "gone",
            "connections": {
                "good": sample(),
                "bad url": {
                    "base_url": "ftp://example.com",
                    "api_format": "openai-chat-completions",
                    "model": "x",
                },
                "bad format": {
                    "base_url": "https://example.com/v1",
                    "api_format": "bogus",
                    "model": "x",
                },
            },
        },
        handle,
    )
stderr = io.StringIO()
with contextlib.redirect_stderr(stderr):
    mixed_config = agent.load_config(mixed)
check("malformed profiles skipped", list(mixed_config["connections"]) == ["good"])
check("missing active cleared", mixed_config["active"] is None)
check("load warnings", "active connection" in stderr.getvalue(), stderr.getvalue())

# ---------- validation ----------
for bad in ("with space", "a/b", "", "x" * 40):
    try:
        agent._validate_connection_name(bad)
        check(f"reject name {bad!r}", False)
    except agent.ConfigError:
        check(f"reject name {bad!r}", True)
check("accept chinese name", agent._validate_connection_name("国内-节点1") == "国内-节点1")
for bad in ("", "ftp://x", "https://", "example.com/v1"):
    try:
        agent._validate_base_url(bad)
        check(f"reject url {bad!r}", False)
    except agent.ConfigError:
        check(f"reject url {bad!r}", True)
check(
    "strip base slash",
    agent._validate_base_url("https://api.deepseek.com/v1/")
    == "https://api.deepseek.com/v1",
)
try:
    agent._normalize_profile("x", {"base_url": "https://x/v1", "api_format": "openai-chat-completions"})
    check("model required", False)
except agent.ConfigError:
    check("model required", True)

# ---------- model response parsing ----------
shapes = [
    {"data": [{"id": "b10"}, {"id": "b2"}, "a1"]},
    {"models": [{"name": "b10"}, {"model": "b2"}, "a1"]},
    [{"model_name": "b10"}, "a1", {"id": "b2"}],
]
for index, shape in enumerate(shapes):
    check(f"model shape {index}", agent.extract_model_ids(shape) == ["a1", "b2", "b10"])
check("single model object", agent.extract_model_ids({"id": "solo"}) == ["solo"])
check("empty model response", agent.extract_model_ids({"data": []}) == [])


class ModelsHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"models": ["b10", {"name": "b2"}, "a1"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


models_server = HTTPServer(("127.0.0.1", 0), ModelsHandler)
threading.Thread(target=models_server.serve_forever, daemon=True).start()
fetched = agent.fetch_model_ids(
    agent.Connection(
        f"http://127.0.0.1:{models_server.server_port}/v1",
        "m",
        "openai-chat-completions",
        "k",
    )
)
check("fetch model ids tolerant shape", fetched == ["a1", "b2", "b10"], fetched)
models_server.shutdown()

# ---------- key mask ----------
check("mask long key", agent.mask_api_key("sk-123456789") == "sk-****6789")
check("mask short key", agent.mask_api_key("short") == "****")
check("mask empty key", agent.mask_api_key("") == "(empty)")


class Args:
    profile = None
    base_url = None
    api_key = None
    model = None
    api_format = None
    anthropic_auth = None
    anthropic_version = None


config = agent.empty_config()
config["connections"]["p"] = sample(key="sk-profile")
config["connections"]["q"] = {
    "provider": "minimax",
    "base_url": "https://api.minimaxi.com/v1",
    "api_format": "openai-chat-completions",
    "model": "MiniMax-M2.7",
    "api_key": "sk-q",
}
config["active"] = "p"

old_env = {name: os.environ.get(name) for name in ("OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL")}
try:
    for name in old_env:
        os.environ.pop(name, None)
    resolved = agent.resolve_connection(Args(), config)
    check("config base", resolved.base_url == "https://api.deepseek.com/v1")
    check("config model", resolved.model == "deepseek-chat")
    check("config key", resolved.api_key == "sk-profile")
    check("config source", resolved.sources["model"] == "config")

    os.environ["OPENAI_MODEL"] = "env-model"
    os.environ["OPENAI_API_KEY"] = "env-key"
    resolved = agent.resolve_connection(Args(), config)
    check("env overrides config model", resolved.model == "env-model")
    check("env overrides config key", resolved.api_key == "env-key")
    check("env source", resolved.sources["model"] == "env")

    cli = Args()
    cli.model = "cli-model"
    resolved = agent.resolve_connection(cli, config)
    check("cli overrides env", resolved.model == "cli-model")
    check("cli source", resolved.sources["model"] == "cli")

    resolved = agent.resolve_connection(Args(), config, use_env=False)
    check("no env uses config", resolved.model == "deepseek-chat")

    profile_args = Args()
    profile_args.profile = "q"
    resolved = agent.resolve_connection(profile_args, config)
    check("profile selection", resolved.name == "q")
    check("profile env still wins", resolved.model == "env-model")
    resolved = agent.resolve_connection(profile_args, config, use_env=False)
    check("profile model", resolved.model == "MiniMax-M2.7")

    unknown = Args()
    unknown.profile = "nope"
    try:
        agent.resolve_connection(unknown, config)
        check("unknown profile raises", False)
    except agent.ConfigError:
        check("unknown profile raises", True)

    empty_key = Args()
    empty_key.api_key = ""
    resolved = agent.resolve_connection(empty_key, config)
    check("cli empty key wins", resolved.api_key == "" and resolved.sources["api_key"] == "cli")

    custom_args = Args()
    custom_args.base_url = "https://example.com/v1"
    resolved = agent.resolve_connection(custom_args, agent.empty_config(), use_env=False)
    check("custom base does not get builtin key", resolved.api_key == "")
finally:
    for name, value in old_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

# ---------- blank key env fallback ----------
raw = sample(key="")
raw_default = dict(raw, base_url=agent.DEFAULT_BASE_URL)
raw_custom = dict(raw, base_url="https://example.com/v1")
old_key = os.environ.get("OPENAI_API_KEY")
try:
    os.environ["OPENAI_API_KEY"] = "from-env"
    connection = agent.connection_from_profile("p", raw_custom)
    check("blank profile key uses env", connection.api_key == "from-env")
    check("blank profile key source", connection.sources["api_key"] == "env")
    os.environ.pop("OPENAI_API_KEY", None)
    connection = agent.connection_from_profile("p", raw_default)
    check("blank profile key uses builtin", connection.api_key == agent.DEFAULT_API_KEY)
    connection = agent.connection_from_profile("p", raw_custom)
    check("custom blank key stays empty", connection.api_key == "")
    check("custom blank key source", connection.sources["api_key"] == "empty")
finally:
    if old_key is None:
        os.environ.pop("OPENAI_API_KEY", None)
    else:
        os.environ["OPENAI_API_KEY"] = old_key

# ---------- public view ----------
public = agent.public_connections(config, config_path, "p")
check("public active", public["active"] == "p")
check("public runtime", public["runtime"] == "p")
check("public key hidden", "sk-profile" not in json.dumps(public))
check("public key hint", public["connections"][0]["key_hint"].endswith("file"))
check("public protocols", "anthropic-messages" in public["protocols"])
check(
    "public minimax domestic",
    public["presets"]["minimax"]["base_urls"]["anthropic-messages"]
    == "https://api.minimaxi.com/anthropic",
)
check(
    "public no international minimax",
    "api.minimax.io" not in json.dumps(public),
)

# ---------- client factory ----------
chat = agent.build_client(
    agent.Connection("https://x/v1", "m", "openai-chat-completions", "k"),
    timeout=7,
    max_retries=2,
    temperature=0.2,
    max_tokens=123,
    reasoning=agent._parse_reasoning("deepseek:low"),
)
check("chat client", isinstance(chat, agent.ChatClient))
check("chat timeout", chat.timeout == 7)
check("chat reasoning", chat.reasoning is not None and chat.reasoning.label == "deepseek:low")
check("chat max tokens", chat.max_tokens == 123)

responses = agent.build_client(
    agent.Connection("https://x/v1", "m", "openai-responses", "k"),
    timeout=7,
    max_retries=2,
)
check("responses client", isinstance(responses, agent.ResponsesClient))
anthropic = agent.build_client(
    agent.Connection(
        "https://api.minimaxi.com/anthropic",
        "m",
        "anthropic-messages",
        "k",
        provider="minimax",
        anthropic_auth="bearer",
        anthropic_version="2024-01-01",
    ),
    timeout=7,
    max_retries=2,
)
check("anthropic client", isinstance(anthropic, agent.AnthropicClient))
check("anthropic auth", anthropic.anthropic_auth == "bearer")
check("anthropic version", anthropic.anthropic_version == "2024-01-01")
check("anthropic default max tokens", anthropic.max_tokens == 8192)

shutil.rmtree(root, ignore_errors=True)
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
