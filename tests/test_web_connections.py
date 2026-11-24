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
from urllib.parse import parse_qs, urlparse

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
MCP_SERVER = str(Path(__file__).resolve().parent / "mcp_stdio_server.py")
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
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw) if raw else {}
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                {"choices": [{"delta": {"content": "流"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "式"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 2}},
            ]
            for event in events:
                self.wfile.write(
                    ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
                )
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
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


class PagedModelHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        PagedModelHandler.requests.append(self.path)
        query = parse_qs(urlparse(self.path).query)
        after = (query.get("after_id") or [""])[0]
        pages = {
            "": (["pm-1", "pm-2"], "pm-2", True),
            "pm-2": (["pm-3", "pm-4"], "pm-4", True),
            "pm-4": (["pm-5", "pm-6"], "", False),
        }
        ids, last_id, has_more = pages.get(after, ([], "", False))
        body = json.dumps({
            "data": [{"id": name} for name in ids],
            "has_more": has_more,
            "last_id": last_id,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=model.serve_forever, daemon=True).start()
model_base = f"http://127.0.0.1:{model.server_port}/v1"
paged_model = ThreadingHTTPServer(("127.0.0.1", 0), PagedModelHandler)
threading.Thread(target=paged_model.serve_forever, daemon=True).start()
paged_base = f"http://127.0.0.1:{paged_model.server_port}/v1"

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
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
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
    check("web html slash status", "handleStatusCommand" in html)
    check("web html slash session", "handleSessionCommand" in html)
    check(
        "web html session menu",
        'id="session-menu"' in html
        and 'id="session-rename"' in html
        and 'id="session-delete"' in html,
    )
    check(
        "web html trash manager",
        'id="trash-modal"' in html
        and 'id="trash-open"' in html
        and "openTrashModal" in html
        and "restoreTrashSession" in html
        and "deleteTrashSession" in html,
    )
    check("web html no side delete", 'class="delete"' not in html)
    check("web html export", 'id="session-export"' in html and 'id="export-modal"' in html)
    check("web html tool details", "addToolDetail" in html and "tool-detail" in html)
    check("web html model info", 'id="model-info"' in html and "renderModelInfo" in html)
    check(
        "web html model dropdown",
        'id="conn-model-picker"' in html
        and 'id="conn-model-select"' in html
        and "renderFetchedModels" in html,
    )
    check("web html no model datalist", 'id="conn-model-options"' not in html)
    check("web html connection select", 'id="connection-select"' in html)
    check(
        "web html help modal",
        'id="help-open"' in html
        and 'id="help-modal"' in html
        and "openHelpModal" in html
        and "/session" in html
        and "/compact" in html
        and "/jobs" in html,
    )
    check(
        "web html model info modal",
        'id="model-info-modal"' in html and 'id="model-info-edit"' in html,
    )
    check("web html thinking toggle", 'id="thinking-toggle"' in html)
    check("web html reasoning select", 'id="reasoning"' in html)
    check("web html stop button", 'id="stop"' in html)

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

    # Paginated /models responses must be followed to completion.
    code, text = api("/api/connections/models", {
        "provider": "custom",
        "base_url": paged_base,
        "api_format": "openai-chat-completions",
        "model": "pm-1",
        "api_key": "sk-paged",
    })
    paged_models = json.loads(text)["models"]
    check(
        "fetch models all pages",
        paged_models == ["pm-1", "pm-2", "pm-3", "pm-4", "pm-5", "pm-6"],
        paged_models,
    )
    check(
        "fetch models page cursor",
        any("after_id=pm-2" in path for path in PagedModelHandler.requests),
        PagedModelHandler.requests,
    )
    check(
        "fetch models next cursor",
        any("after_id=pm-4" in path for path in PagedModelHandler.requests)
        and len(PagedModelHandler.requests) == 3,
        PagedModelHandler.requests,
    )

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
    check("save needs model info", saved["needs_model_info"] is True, saved)

    state = api_json("/api/state")
    check("state connection", state["connection"] == "work")
    check("state model", state["model"] == "model-a")
    check("state base", state["base_url"] == model_base)
    check("state workspace", state["workspace"] == os.path.realpath("."), state.get("workspace"))
    check("state model info", state["model_info"]["context_window"] == 256000, state.get("model_info"))
    check("state context", state["context"]["window"] == 256000, state.get("context"))
    check("state auto compact", state["auto_compact"]["enabled"] is False, state.get("auto_compact"))
    check("state idle compact", state["idle_compact"]["enabled"] is False, state.get("idle_compact"))
    models_payload = api_json("/api/models")
    check("models info", models_payload["info"]["context_window"] == 256000, models_payload.get("info"))

    jobs_payload = api_json("/api/jobs")
    check("jobs list empty", jobs_payload["jobs"] == [], jobs_payload)
    job_config = api_json("/api/jobs/config", {"poll_seconds": 3, "follow_lines": 4})
    check("jobs poll config", job_config["poll_seconds"] == 3, job_config)
    check("jobs lines config", job_config["follow_lines"] == 4, job_config)
    code, _ = api("/api/jobs/status", {"id": "job-nope"}, expect_error=True)
    check("jobs unknown id", code == 404, code)
    check("web html job block", "job-block" in html and "handleJobEvent" in html)
    check(
        "web html stream",
        "beginStreamBubble" in html
        and "appendStreamDelta" in html
        and "content_delta" in html,
    )
    check(
        "web html sidebar groups",
        'id="sessions-group"' in html
        and 'id="settings-group"' in html
        and 'id="settings-summary"' in html
        and 'id="session-search"' in html,
    )
    check(
        "web html session extras",
        'id="session-archived"' in html
        and 'id="session-pin"' in html
        and 'id="session-archive"' in html
        and "checkRuntimeDrift" in html,
    )
    check(
        "web html stream toggle",
        'id="stream-toggle"' in html and "/stream" in html,
    )
    check(
        "web html stream first",
        html.find('id="stream-toggle"') < html.find('id="thinking-toggle"'),
    )
    check(
        "web html stream checked",
        'id="stream-toggle" checked' in html,
    )
    check(
        "web html new workspace",
        'id="new-session-modal"' in html
        and 'id="new-session-workspace"' in html
        and "/workspace" in html,
    )
    check(
        "web html mcp manager",
        'id="mcp-modal"' in html
        and 'id="mcp-open"' in html
        and 'id="mcp-form"' in html
        and 'id="mcp-browser-modal"' in html
        and "saveMCPForm" in html
        and "openMCPResources" in html
        and "openMCPPrompts" in html,
    )
    check(
        "web html groups init order",
        html.find("const settingsGroupEl") < html.find("restoreSideGroup(settingsGroupEl")
        and html.find("const toolsGroupEl") < html.find("restoreSideGroup(toolsGroupEl"),
    )

    model_info_payload = api_json("/api/model/info")
    check("model info needs fill", model_info_payload["needs_model_info"] is True, model_info_payload)
    saved_info = api_json("/api/model/info", {
        "model": "model-a",
        "context_window": 128000,
        "max_output_tokens": 4096,
        "input": ["text", "image"],
        "output": ["text"],
        "supports_tools": True,
        "supports_reasoning": False,
        "reasoning_providers": [],
        "notes": "web test",
    })
    check("model info saved", saved_info["info"]["context_window"] == 128000, saved_info)
    check("model info source", saved_info["info"]["source"] == "models.json", saved_info)
    check("model info no longer needs fill", saved_info["needs_model_info"] is False, saved_info)
    refreshed_info = api_json("/api/model/info")
    check("model info persisted", refreshed_info["info"]["input"] == ["text", "image"], refreshed_info)
    check("model info image support", "image" in refreshed_info["info"]["input"], refreshed_info)

    # Runtime toggles: show thinking and reasoning level.
    runtime = api_json("/api/runtime")
    check("runtime thinking default", runtime["show_thinking"] is False, runtime)
    check("runtime reasoning initial", runtime["reasoning"]["label"] == "deepseek:low", runtime)
    check("runtime reasoning supported", runtime["reasoning"]["supported"] is True, runtime)
    thinking = api_json("/api/thinking", {"show": True})
    check("thinking enabled", thinking["show_thinking"] is True, thinking)
    check("thinking in runtime", api_json("/api/runtime")["show_thinking"] is True)
    reasoning = api_json("/api/reasoning", {"value": "kimi:low"})
    check("reasoning changed", reasoning["reasoning"]["label"] == "kimi:low", reasoning)
    check("reasoning in runtime", api_json("/api/runtime")["reasoning"]["label"] == "kimi:low")
    cleared = api_json("/api/reasoning", {"value": ""})
    check("reasoning cleared", cleared["reasoning"]["label"] == "", cleared)
    code, text = api("/api/reasoning", {"value": "qwen:low"}, expect_error=True)
    check("reasoning invalid rejected", code == 400, code)
    api_json("/api/reasoning", {"value": "deepseek:low"})
    stopped = api_json("/api/chat/stop", {})
    check("stop idle request", stopped["stopping"] is False, stopped)

    # Create a session, then rename it through the title API.
    code, text = api("/api/chat", {"message": "hello", "images": []})
    chat = json.loads(text)
    check("chat created session", bool(chat.get("session")), chat)
    session_uuid = chat["session"]
    title_data = api_json("/api/session/title", {
        "session": session_uuid,
        "title": "自定义标题",
    })
    check("web title updated", title_data["title"] == "自定义标题", title_data)
    sessions = api_json("/api/sessions")["sessions"]
    title_row = next(item for item in sessions if item["uuid"] == session_uuid)
    check("web title in list", title_row["title"] == "自定义标题", title_row)

    code, stream_text = api(
        "/api/chat/stream", {"message": "stream please", "images": []}
    )
    check("web stream status", code == 200, code)
    check("web stream start", "content_start" in stream_text, stream_text[-800:])
    check("web stream delta", "content_delta" in stream_text, stream_text[-800:])
    check("web stream final", '"type": "content"' in stream_text, stream_text[-800:])
    check("web stream done", '"type": "done"' in stream_text, stream_text[-800:])
    start_at = stream_text.find("content_start")
    delta_at = stream_text.find("content_delta")
    done_at = stream_text.find('"type": "done"')
    check(
        "web stream order",
        0 <= start_at < delta_at < done_at,
        (start_at, delta_at, done_at),
    )

    stream_state = api_json("/api/stream")
    check("stream default on", stream_state["stream"] is True, stream_state)
    stream_off = api_json("/api/stream", {"enabled": False})
    check("stream api off", stream_off["stream"] is False, stream_off)
    runtime_state = api_json("/api/runtime")
    check("runtime stream off", runtime_state["stream"] is False, runtime_state)
    code, quiet_text = api(
        "/api/chat/stream", {"message": "no stream please", "images": []}
    )
    check("web no-stream status", code == 200, code)
    check("web no-stream no delta", "content_delta" not in quiet_text, quiet_text[-800:])
    check("web no-stream final", '"type": "content"' in quiet_text, quiet_text[-800:])
    check("web no-stream done", '"type": "done"' in quiet_text, quiet_text[-800:])
    stream_on = api_json("/api/stream", {"enabled": True})
    check("stream api on", stream_on["stream"] is True, stream_on)

    # Export a synthetic session with thinking and tools.
    sessions_dir = os.path.join(home, ".benben-agent", "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    export_path = os.path.join(sessions_dir, "099-20261112-deadbeef.jsonl")
    export_records = [
        {"_type": "meta", "version": 1, "uuid": "deadbeef", "sequence": 99,
         "created": "2026-11-12T10:00:00", "model": "m", "workspace": "/tmp/ws"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "answer", "_thinking": "secret thought",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "tool output"},
    ]
    with open(export_path, "w", encoding="utf-8") as handle:
        for record in export_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    exported = api_json("/api/session/export", {
        "session": "deadbeef",
        "include_thinking": True,
        "include_tools": True,
    })
    check("export filename", exported["filename"].startswith("deadbeef-"), exported)
    check("export thinking", "secret thought" in exported["markdown"], exported["markdown"][:500])
    check("export tools", "tool output" in exported["markdown"] and "read_file" in exported["markdown"])
    minimal = api_json("/api/session/export", {
        "session": "deadbeef",
        "include_thinking": False,
        "include_tools": False,
    })
    check("export minimal", "secret thought" not in minimal["markdown"] and "tool output" not in minimal["markdown"])

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
    runtime = api_json("/api/runtime")
    check("anthropic reasoning supported", runtime["reasoning"]["supported"] is True, runtime)
    check("anthropic runtime format", runtime["api_format"] == "anthropic-messages", runtime)
    code, text = api("/api/reasoning", {"value": "deepseek:low"}, expect_error=True)
    check("anthropic wrong provider rejected", code == 400, code)
    anthropic_reasoning = api_json("/api/reasoning", {"value": "anthropic:low"})
    check(
        "anthropic reasoning set",
        anthropic_reasoning["reasoning"]["label"] == "anthropic:low",
        anthropic_reasoning,
    )

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

    # Workspace selection for new sessions and resume.
    original_state = api_json("/api/state")
    original_workspace = original_state["workspace"]
    original_default = original_state["default_workspace"]
    check(
        "web default workspace",
        original_default == original_workspace,
        original_state,
    )
    workspace_a = os.path.join(home, "workspace-a")
    workspace_b = os.path.join(home, "workspace-b")
    os.makedirs(workspace_a, exist_ok=True)
    os.makedirs(workspace_b, exist_ok=True)
    new_a = api_json("/api/new", {"workspace": workspace_a})
    check(
        "web new workspace",
        new_a["workspace"] == os.path.realpath(workspace_a),
        new_a,
    )
    check("web new workspace changed", new_a["workspace_changed"] is True, new_a)
    check(
        "web state workspace",
        api_json("/api/state")["workspace"] == os.path.realpath(workspace_a),
    )
    check(
        "web default workspace stable",
        api_json("/api/state")["default_workspace"] == os.path.realpath(original_default),
        original_default,
    )
    code, _ = api(
        "/api/new",
        {"workspace": os.path.join(home, "missing-workspace")},
        expect_error=True,
    )
    check("web bad workspace rejected", code == 400, code)
    sessions_dir = os.path.join(home, ".benben-agent", "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    workspace_session = "aa11bb22"
    with open(
        os.path.join(sessions_dir, f"999-20261123-{workspace_session}.jsonl"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write(json.dumps({
            "_type": "meta",
            "version": 1,
            "uuid": workspace_session,
            "sequence": 999,
            "created": "2026-11-23T10:00:00",
            "model": "m",
            "workspace": os.path.realpath(workspace_a),
        }) + "\n")
        handle.write(json.dumps({
            "role": "user",
            "content": "workspace a chat",
        }) + "\n")
    new_b = api_json("/api/new", {"workspace": workspace_b})
    check(
        "web switch workspace b",
        new_b["workspace"] == os.path.realpath(workspace_b),
        new_b,
    )
    opened = api_json("/api/open", {"session": workspace_session})
    check("web open workspace changed", opened["workspace_changed"] is True, opened)
    check(
        "web open workspace path",
        opened["workspace"] == os.path.realpath(workspace_a),
        opened,
    )
    check(
        "web open workspace state",
        api_json("/api/state")["workspace"] == os.path.realpath(workspace_a),
    )
    default_new = api_json("/api/new", {})
    check(
        "web new default workspace",
        default_new["workspace"] == os.path.realpath(workspace_a),
        default_new,
    )
    check("web new default unchanged", default_new["workspace_changed"] is False, default_new)
    restored = api_json("/api/new", {"workspace": original_workspace})
    check(
        "web workspace restored",
        restored["workspace"] == os.path.realpath(original_workspace),
        restored,
    )

    # Session trash, restore and prune API.
    trash_uuid = "bb22cc33"
    with open(
        os.path.join(sessions_dir, f"998-20261123-{trash_uuid}.jsonl"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write(json.dumps({
            "_type": "meta",
            "version": 1,
            "uuid": trash_uuid,
            "sequence": 998,
            "created": "2026-11-23T10:00:00",
            "model": "m",
            "workspace": os.path.realpath(original_workspace),
        }) + "\n")
        handle.write(json.dumps({"role": "user", "content": "trash me"}) + "\n")
    deleted_payload = api_json("/api/delete", {"session": trash_uuid})
    check("web delete trashes", deleted_payload.get("trashed") is True, deleted_payload)
    trash_payload = api_json("/api/trash")
    check(
        "web trash list",
        any(row["uuid"] == trash_uuid for row in trash_payload["sessions"]),
        trash_payload,
    )
    check("web trash size", trash_payload["total_size"] > 0, trash_payload)
    restored_payload = api_json("/api/session/restore", {"session": trash_uuid})
    check("web restore", restored_payload["restored"] == trash_uuid, restored_payload)
    check(
        "web restore list",
        any(row["uuid"] == trash_uuid for row in api_json("/api/sessions")["sessions"]),
    )
    permanent_uuid = "dd44ee55"
    with open(
        os.path.join(sessions_dir, f"996-20261123-{permanent_uuid}.jsonl"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write(json.dumps({
            "_type": "meta",
            "version": 1,
            "uuid": permanent_uuid,
            "sequence": 996,
            "created": "2026-11-23T10:00:00",
            "model": "m",
            "workspace": os.path.realpath(original_workspace),
        }) + "\n")
        handle.write(json.dumps({"role": "user", "content": "delete forever"}) + "\n")
    api_json("/api/delete", {"session": permanent_uuid})
    permanent = api_json(
        "/api/session/trash/delete", {"session": permanent_uuid}
    )
    check(
        "web permanent delete",
        permanent["deleted"] == permanent_uuid,
        permanent,
    )
    trash_after = api_json("/api/trash")
    check(
        "web permanent delete gone",
        not any(
            row["uuid"] == permanent_uuid
            for row in trash_after["sessions"]
        ),
        trash_after,
    )
    api_json("/api/delete", {"session": trash_uuid})
    emptied = api_json("/api/session/trash/empty", {})
    check("web empty trash", emptied["removed"] >= 1, emptied)
    prune_uuid = "cc33dd44"
    with open(
        os.path.join(sessions_dir, f"997-20261123-{prune_uuid}.jsonl"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write(json.dumps({
            "_type": "meta",
            "version": 1,
            "uuid": prune_uuid,
            "sequence": 997,
            "created": "2026-11-23T10:00:00",
            "model": "m",
            "workspace": os.path.realpath(original_workspace),
        }) + "\n")
        handle.write(json.dumps({"role": "user", "content": "prune me"}) + "\n")
    before_prune = len(api_json("/api/sessions")["sessions"])
    dry = api_json("/api/session/prune", {"keep_count": 0, "confirm": False})
    check("web prune dry-run", dry["dry_run"] is True, dry)
    check("web prune candidates", len(dry["sessions"]) > 0, dry)
    check(
        "web prune keeps files",
        len(api_json("/api/sessions")["sessions"]) == before_prune,
    )
    code, _ = api(
        "/api/session/prune",
        {"keep_count": -1},
        expect_error=True,
    )
    check("web prune invalid", code == 400, code)
    paged = api_json("/api/sessions?limit=2&offset=0")
    check("web sessions paged size", len(paged["sessions"]) <= 2, paged)
    check("web sessions paged total", paged["total"] >= len(paged["sessions"]), paged)
    second_page = api_json("/api/sessions?limit=2&offset=2")
    check("web sessions offset", second_page["offset"] == 2, second_page)
    empty_search = api_json("/api/sessions?q=definitely-no-such-session")
    check(
        "web sessions search empty",
        empty_search["total"] == 0 and empty_search["sessions"] == [],
        empty_search,
    )

    flag_uuid = "dd44ee55"
    with open(
        os.path.join(sessions_dir, f"996-20261123-{flag_uuid}.jsonl"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write(json.dumps({
            "_type": "meta",
            "version": 1,
            "uuid": flag_uuid,
            "sequence": 996,
            "created": "2026-11-23T10:00:00",
            "model": "m",
            "workspace": os.path.realpath(original_workspace),
        }) + "\n")
        handle.write(json.dumps({"role": "user", "content": "flag me"}) + "\n")
    pinned = api_json("/api/session/flags", {"session": flag_uuid, "pinned": True})
    check("web pin", pinned["pinned"] is True, pinned)
    listed = api_json("/api/sessions?archived=1")
    pinned_row = next(
        (row for row in listed["sessions"] if row["uuid"] == flag_uuid), None
    )
    check("web pinned row", pinned_row and pinned_row["pinned"] is True, pinned_row)
    check("web pinned first", listed["sessions"][0]["uuid"] == flag_uuid, listed["sessions"][:3])
    archived = api_json("/api/session/flags", {"session": flag_uuid, "archived": True})
    check("web archive", archived["archived"] is True, archived)
    visible = api_json("/api/sessions")
    check(
        "web archived hidden",
        all(row["uuid"] != flag_uuid for row in visible["sessions"]),
        visible,
    )
    with_archived = api_json("/api/sessions?archived=1")
    check(
        "web archived listed",
        any(row["uuid"] == flag_uuid for row in with_archived["sessions"]),
        with_archived,
    )
    api_json("/api/session/flags", {"session": flag_uuid, "archived": False})
    health = api_json("/api/health")
    check(
        "web health",
        health["ok"] is True and bool(health.get("version")),
        health,
    )
    mcp_status = api_json("/api/mcp")
    check("web mcp disabled", mcp_status["enabled"] is False, mcp_status)
    check("web mcp empty configs", mcp_status["configs"] == [], mcp_status)
    added = api_json("/api/mcp/add", {
        "name": "web-fake",
        "config": {
            "type": "stdio",
            "command": sys.executable,
            "args": [MCP_SERVER],
            "timeout": 10,
        },
    })
    check("web mcp add", added.get("ok") is True, added)
    mcp_status = api_json("/api/mcp")
    check(
        "web mcp added disabled",
        mcp_status["enabled"] is False
        and any(item["name"] == "web-fake" for item in mcp_status["configs"]),
        mcp_status,
    )
    check("web mcp add persisted", "web-fake" in json.load(open(config_path, encoding="utf-8"))["mcpServers"])
    enabled_mcp = api_json("/api/mcp/on", {})
    check("web mcp on", enabled_mcp["enabled"] is True, enabled_mcp)
    mcp_status = api_json("/api/mcp")
    check(
        "web mcp connected",
        any(
            item["name"] == "web-fake" and item["status"] == "connected"
            for item in mcp_status["servers"]
        ),
        mcp_status,
    )
    check("web mcp tools", len(mcp_status["tools"]) >= 2, mcp_status)
    resource_data = api_json("/api/mcp/resources?server=web-fake")
    check(
        "web mcp resources",
        any("readme.txt" in (item.get("uri") or "") for item in resource_data["resources"]),
        resource_data,
    )
    read_data = api_json("/api/mcp/resource/read", {
        "server": "web-fake",
        "uri": "file:///docs/readme.txt",
    })
    check("web mcp read resource", "resource text" in read_data["text"], read_data)
    prompt_data = api_json("/api/mcp/prompts?server=web-fake")
    check(
        "web mcp prompts",
        any(item.get("name") == "code-review" for item in prompt_data["prompts"]),
        prompt_data,
    )
    rendered = api_json("/api/mcp/prompt/get", {
        "server": "web-fake",
        "name": "code-review",
        "arguments": {"language": "python", "focus": "security"},
    })
    check(
        "web mcp render prompt",
        "review python" in rendered["text"] and "security" in rendered["text"],
        rendered,
    )
    tested = api_json("/api/mcp/test", {"name": "web-fake"})
    check("web mcp test", tested.get("ok") is True and tested.get("tools"), tested)
    updated = api_json("/api/mcp/update", {
        "name": "web-fake",
        "config": {"timeout": 7},
    })
    check("web mcp update", updated.get("ok") is True, updated)
    disabled_server = api_json("/api/mcp/disable", {"name": "web-fake"})
    check("web mcp disable", disabled_server["server"]["status"] == "disabled", disabled_server)
    mcp_status = api_json("/api/mcp")
    check("web mcp disabled tools", mcp_status["tools"] == [], mcp_status)
    enabled_server = api_json("/api/mcp/enable", {"name": "web-fake"})
    check("web mcp enable", enabled_server["server"]["status"] == "connected", enabled_server)
    secret_add = api_json("/api/mcp/add", {
        "name": "web-secret",
        "config": {
            "type": "http",
            "url": "http://127.0.0.1:1/mcp",
            "headers": {"Authorization": "Bearer very-secret"},
            "enabled": False,
        },
    })
    check("web mcp secret add", secret_add.get("ok") is True, secret_add)
    secret_status = api_json("/api/mcp")
    secret_row = next(
        item for item in secret_status["configs"] if item["name"] == "web-secret"
    )
    check(
        "web mcp secret hidden",
        "Authorization" in secret_row["header_keys"]
        and "headers" not in secret_row
        and "very-secret" not in json.dumps(secret_status),
        secret_row,
    )
    api_json("/api/mcp/remove", {"name": "web-secret"})
    removed = api_json("/api/mcp/remove", {"name": "web-fake"})
    check("web mcp remove", removed.get("ok") is True, removed)
    mcp_status = api_json("/api/mcp")
    check("web mcp removed", mcp_status["configs"] == [], mcp_status)
    api_json("/api/mcp/off", {})
    check("web mcp off", api_json("/api/mcp")["enabled"] is False)

    runtime = api_json("/api/runtime")
    check("web runtime session field", "session" in runtime, runtime)
    check(
        "web runtime model field",
        isinstance(runtime.get("model"), str) and bool(runtime.get("model")),
        runtime,
    )

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
    paged_model.shutdown()
    shutil.rmtree(home, ignore_errors=True)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
