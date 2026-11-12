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


# ---------- fallback ----------
fallback = agent._fallback_model_info()
check("fallback context 256k", fallback.context_window == 256000, fallback)
check("fallback max output", fallback.max_output_tokens == 8192, fallback)
check("fallback input text", fallback.input == ("text",), fallback)
check("fallback input unknown", fallback.input_known is False, fallback)
check("fallback source", fallback.source == "fallback", fallback)
check("fallback images unknown", agent.model_supports_images(fallback) is None)
check("default payload", agent.default_models_payload()["fallback"]["context_window"] == 256000)

# ---------- models.json roundtrip ----------
root = tempfile.mkdtemp(prefix="model-info-")
models_path = os.path.join(root, "models.json")
payload = {
    "version": 1,
    "fallback": {
        "context_window": 128000,
        "max_output_tokens": 4096,
        "input": ["text", "image"],
        "supports_tools": True,
        "supports_reasoning": None,
    },
    "models": {
        "deepseek/deepseek-chat": {
            "name": "DeepSeek Chat",
            "context_window": 64000,
            "max_output_tokens": 2048,
            "input": ["text"],
            "output": ["text"],
            "supports_tools": True,
            "supports_parallel_tools": False,
            "supports_reasoning": False,
            "api_formats": ["openai-chat-completions"],
            "notes": "test model",
        },
        "m": {
            "context_window": 1000,
            "input": ["text", "image"],
            "supports_reasoning": True,
            "reasoning_providers": ["deepseek"],
        },
    },
}
agent.save_models_config(payload, models_path)
check("models file private", oct(os.stat(models_path).st_mode & 0o777) == "0o600")
loaded = agent.load_models_config(models_path)
check("loaded fallback", loaded["fallback"].context_window == 128000, loaded["fallback"])
check("loaded model count", len(loaded["models"]) == 2, loaded["models"])
registry = agent.ModelRegistry(models_path)
info = registry.resolve("deepseek", "deepseek-chat", "openai-chat-completions")
check("resolve exact", info.context_window == 64000 and info.name == "DeepSeek Chat", info)
check("resolve reasoning false", info.supports_reasoning is False, info)
check("resolve notes", info.notes == "test model", info)
check("resolve by model", registry.resolve("other", "deepseek-chat").context_window == 64000)
check("resolve short key", registry.resolve("custom", "m").context_window == 1000)
check("resolve fallback", registry.resolve("custom", "unknown").context_window == 128000)
check("resolve fallback source", registry.resolve("custom", "unknown").source == "fallback")
override = registry.resolve("deepseek", "deepseek-chat", override={"context_window": 32000})
check("connection override", override.context_window == 32000, override)
check("override source", override.source == "connection", override)
check("override keeps model", override.max_output_tokens == 2048, override)

# ---------- modalities and context ----------
check("images yes", agent.model_supports_images(registry.resolve("custom", "m")) is True)
check("images no", agent.model_supports_images(info) is False)
messages = [{"role": "system", "content": "x" * 4000}]
usage = agent.context_usage(messages, registry.resolve("custom", "m"))
check("context usage window", usage["window"] == 1000, usage)
check("context usage tokens", usage["tokens"] > 0, usage)
check("context usage percent", 0 <= usage["percent"] <= 100, usage)
check("model summary", "context: 1000" in agent.model_info_summary(
    registry.resolve("custom", "m")
))

# ---------- remote metadata ----------
remote = {
    "data": [
        {
            "id": "remote-model",
            "context_length": 16000,
            "max_output_tokens": 2048,
            "input_modalities": ["text", "image"],
            "supports_tools": True,
            "supports_reasoning": True,
        }
    ]
}
cards = agent.extract_remote_model_info(remote)
check("remote extracted", cards["remote-model"]["context_window"] == 16000, cards)
registry.merge_remote(remote)
check("remote merged", registry.resolve("x", "remote-model").context_window == 16000)
check("remote source", registry.resolve("x", "remote-model").source == "remote")
check("remote images", agent.model_supports_images(registry.resolve("x", "remote-model")) is True)

# ---------- model info upsert and prompts ----------
upserted = agent.upsert_model_info(
    "custom",
    "upsert-model",
    {
        "context_window": 64000,
        "max_output_tokens": 2048,
        "input": ["text", "image"],
        "output": ["text"],
        "supports_tools": True,
        "supports_reasoning": True,
        "reasoning_providers": ["deepseek"],
        "notes": "first",
    },
    models_path,
)
check("upsert source", upserted.source == "models.json", upserted)
check("upsert context", upserted.context_window == 64000, upserted)
reloaded = agent.ModelRegistry(models_path).resolve("custom", "upsert-model")
check("upsert reload", reloaded.notes == "first", reloaded)
updated = agent.upsert_model_info(
    "custom", "upsert-model", {"notes": "second"}, models_path
)
check("upsert preserves", updated.context_window == 64000, updated)
check("upsert updated notes", updated.notes == "second", updated)

prompt_info = agent._fallback_model_info()._replace(key="custom/prompt-model")
answers = iter(["", "128000", "4096", "2", "", "n", "y", "deepseek", "prompt note"])
fields = agent.prompt_model_info(
    "custom",
    "prompt-model",
    prompt_info,
    "openai-chat-completions",
    input_fn=lambda _prompt: next(answers),
    output_fn=lambda _text: None,
)
check("prompt context", fields and fields["context_window"] == 128000, fields)
check("prompt max output", fields and fields["max_output_tokens"] == 4096, fields)
check("prompt image", fields and fields["input"] == ["text", "image"], fields)
check("prompt tools false", fields and fields["supports_tools"] is False, fields)
check("prompt reasoning", fields and fields["supports_reasoning"] is True, fields)
check("prompt providers", fields and fields["reasoning_providers"] == ["deepseek"], fields)
check("prompt notes", fields and fields["notes"] == "prompt note", fields)
configured = prompt_info._replace(source="models.json")
check(
    "prompt configured default no",
    agent.prompt_model_info(
        "custom",
        "prompt-model",
        configured,
        "openai-chat-completions",
        input_fn=lambda _prompt: "n",
        output_fn=lambda _text: None,
    )
    is None,
)

registry = agent.ModelRegistry(models_path)
answers = iter(["", "32000", "1024", "1", "", "y", "n", ""])
saved = agent.configure_model_info(
    "custom",
    "configured-model",
    registry,
    "openai-chat-completions",
    input_fn=lambda _prompt: next(answers),
    output_fn=lambda _text: None,
)
check("configure saved", saved is not None and saved.context_window == 32000, saved)
check(
    "configure persisted",
    agent.ModelRegistry(models_path).resolve("custom", "configured-model").max_output_tokens
    == 1024,
)

# ---------- ensure file ----------
new_models = os.path.join(root, "new", "models.json")
check("ensure models creates", agent.ensure_models_file(new_models) is True)
check("ensure models exists", os.path.isfile(new_models))
created = json.load(open(new_models, encoding="utf-8"))
check("ensure fallback 256k", created["fallback"]["context_window"] == 256000, created)
check("ensure models empty", created["models"] == {}, created)
check("ensure again", agent.ensure_models_file(new_models) is False)

# ---------- CLI ----------
agent_path = str(Path(__file__).resolve().parents[1] / "benben-agent.py")


def run_cli(home, workspace, user_input, extra=None):
    env = dict(
        os.environ,
        HOME=home,
        BENBEN_CONFIG=os.path.join(home, ".benben-agent", "config.json"),
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
            *(extra or []),
        ],
        input=user_input,
        text=True,
        capture_output=True,
        env=env,
        cwd=workspace,
        timeout=40,
    )


home = tempfile.mkdtemp(prefix="model-cli-")
workspace = tempfile.mkdtemp(prefix="model-cli-ws-")
os.makedirs(os.path.join(home, ".benben-agent"), exist_ok=True)
with open(os.path.join(home, ".benben-agent", "models.json"), "w", encoding="utf-8") as handle:
    json.dump({
        "version": 1,
        "fallback": {"context_window": 256000},
        "models": {
            "m": {
                "context_window": 1000,
                "max_output_tokens": 100,
                "input": ["text", "image"],
                "output": ["text"],
                "supports_tools": True,
                "supports_reasoning": False,
                "notes": "CLI 测试模型",
            }
        },
    }, handle)
result = run_cli(home, workspace, "/model info\n/context\n/status\n/exit\n")
check("cli model info", "context: 1000" in result.stdout and "CLI 测试模型" in result.stdout, result.stdout[-1500:])
check("cli context command", "context: ~" in result.stdout, result.stdout[-1500:])
check("cli status model info", "model info:" in result.stdout, result.stdout[-1500:])
check("cli startup model info", "model info:" in result.stdout, result.stdout[:1500])

# Text-only image preflight.
with open(os.path.join(home, ".benben-agent", "models.json"), "w", encoding="utf-8") as handle:
    json.dump({
        "version": 1,
        "fallback": {"context_window": 256000},
        "models": {
            "m": {
                "context_window": 1000,
                "input": ["text"],
                "supports_tools": True,
            }
        },
    }, handle)
image_path = os.path.join(workspace, "tiny.png")
with open(image_path, "wb") as handle:
    handle.write(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
result = run_cli(home, workspace, "", extra=["--image", image_path, "test"])
check("cli image blocked", result.returncode == 1, result.stderr[-800:])
check("cli image message", "text-only" in result.stderr, result.stderr[-800:])

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
