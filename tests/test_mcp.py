import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
SERVER = str(Path(__file__).resolve().parent / "mcp_stdio_server.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


print("== config ==")
servers = agent._normalize_mcp_servers({
    "fake": {
        "command": sys.executable,
        "args": [SERVER],
        "env": {"MCP_TEST": "1"},
    },
    "remote": {
        "type": "http",
        "url": "http://127.0.0.1:1/mcp",
        "headers": {"Authorization": "Bearer x"},
    },
    "bad name!": {"command": "x"},
    "missing": {"args": []},
})
check("mcp config stdio", servers.get("fake", {}).get("type") == "stdio", servers)
check("mcp config http", servers.get("remote", {}).get("type") == "http", servers)
check("mcp config skips bad name", "bad name!" not in servers, servers)
check("mcp config skips missing", "missing" not in servers, servers)
check("mcp default off", agent.default_settings()["mcp"] is False)
check("mcp default timeout", agent.default_settings()["mcp_timeout"] == 60)

config_path = os.path.join(tempfile.mkdtemp(prefix="mcp-config-"), "config.json")
config = agent.empty_config()
config["mcpServers"] = servers
agent.save_config(config, config_path)
saved = json.load(open(config_path, encoding="utf-8"))
check("mcp saved", "fake" in saved.get("mcpServers", {}), saved.get("mcpServers"))
check("mcp saved http", "remote" in saved.get("mcpServers", {}), saved.get("mcpServers"))

print("== CLI wizard ==")
wizard_manager = agent.McpManager({}, timeout=5, enabled=False)
wizard_workspace = agent.Workspace(tempfile.mkdtemp(prefix="mcp-wizard-"))
wizard_workspace.mcp = wizard_manager
wizard_path = os.path.join(tempfile.mkdtemp(prefix="mcp-wizard-config-"), "config.json")
wizard_config = agent.empty_config()
wizard_config["mcpServers"] = wizard_manager.configs
wizard_args = SimpleNamespace(mcp=False)
with patch(
    "builtins.input",
    side_effect=["", sys.executable, SERVER, "", "", "", "", ""],
):
    agent.run_mcp_command(
        wizard_workspace,
        "add wizard",
        wizard_config,
        wizard_path,
        wizard_args,
    )
check("wizard adds server", "wizard" in wizard_manager.configs, wizard_manager.configs)
check(
    "wizard disabled by default",
    wizard_manager.enabled is False,
    wizard_manager.enabled,
)
saved_wizard = json.load(open(wizard_path, encoding="utf-8"))
check(
    "wizard persisted",
    "wizard" in saved_wizard.get("mcpServers", {}),
    saved_wizard.get("mcpServers"),
)
with patch("builtins.input", side_effect=[""]):
    agent.run_mcp_command(
        wizard_workspace, "on", wizard_config, wizard_path, wizard_args
    )
check("wizard global on", wizard_manager.enabled is True, wizard_manager.enabled)
check(
    "wizard tools exposed",
    any(name.startswith("mcp__wizard__") for name in wizard_manager.tools),
    wizard_manager.tools,
)
with patch("builtins.input", side_effect=["y"]):
    agent.run_mcp_command(
        wizard_workspace, "remove wizard", wizard_config, wizard_path, wizard_args
    )
check("wizard removes server", "wizard" not in wizard_manager.configs)
saved_wizard = json.load(open(wizard_path, encoding="utf-8"))
check(
    "wizard remove persisted",
    "wizard" not in saved_wizard.get("mcpServers", {}),
    saved_wizard.get("mcpServers"),
)
wizard_manager.close()

print("== disabled manager management ==")
disabled_manager = agent.McpManager({}, timeout=5, enabled=False)
check("disabled no tools", agent.McpManager({}, enabled=False).tools == {})
disabled_manager.add_server("fake", servers["fake"])
check(
    "add while disabled",
    disabled_manager.states["fake"]["status"] == "disabled",
    disabled_manager.states,
)
disabled_workspace = agent.Workspace(tempfile.mkdtemp(prefix="mcp-disabled-"))
disabled_workspace.mcp = disabled_manager
check(
    "disabled no schemas",
    not [
        schema for schema in agent.tool_schemas(disabled_workspace)
        if schema["function"]["name"].startswith("mcp__")
        or schema["function"]["name"] in ("mcp_list_resources", "mcp_read_resource")
    ],
    agent.tool_schemas(disabled_workspace),
)
disabled_manager.set_enabled(True)
check("enable manager", disabled_manager.enabled is True, disabled_manager.enabled)
check(
    "enable connects",
    disabled_manager.states["fake"]["status"] == "connected",
    disabled_manager.states,
)
names = [
    schema["function"]["name"]
    for schema in agent.tool_schemas(disabled_workspace)
    if schema["function"]["name"].startswith("mcp__")
]
check("enabled exposes tools", len(names) == 2, names)
probe = disabled_manager.test_server("fake")
check("manager test server", probe.get("ok") is True and len(probe.get("tools") or []) == 2, probe)
disabled_manager.set_server_enabled("fake", False)
check(
    "disable server",
    disabled_manager.states["fake"]["status"] == "disabled",
    disabled_manager.states,
)
check(
    "disable server removes tools",
    not [
        schema for schema in agent.tool_schemas(disabled_workspace)
        if schema["function"]["name"].startswith("mcp__")
    ],
)
disabled_manager.set_server_enabled("fake", True)
disabled_manager.set_enabled(False)
check(
    "disable manager",
    not agent.McpManager({}, enabled=True).tools
    and not [
        schema for schema in agent.tool_schemas(disabled_workspace)
        if schema["function"]["name"].startswith("mcp__")
    ],
)
disabled_manager.set_enabled(True)
disabled_manager.update_server("fake", {**servers["fake"], "timeout": 7})
check("update server", disabled_manager.configs["fake"].get("timeout") == 7, disabled_manager.configs)
check("remove server", disabled_manager.remove_server("fake") is True)
check("remove clears config", "fake" not in disabled_manager.configs, disabled_manager.configs)
check("remove clears tools", not disabled_manager.tools, disabled_manager.tools)
disabled_manager.close()

print("== stdio ==")
manager = agent.McpManager({"fake": servers["fake"]}, timeout=5)
check("stdio connected", manager.states["fake"]["status"] == "connected", manager.states)
check("stdio tools", len(manager.tools) == 2, manager.tools)
echo_name = next(
    name for name, info in manager.tools.items() if info["tool"] == "echo"
)
write_name = next(
    name for name, info in manager.tools.items() if info["tool"] == "write"
)
check("stdio namespaced", echo_name.startswith("mcp__fake__"), echo_name)
check("stdio readonly annotation", manager.tools[echo_name]["read_only"] is True)
check("stdio write annotation", manager.tools[write_name]["read_only"] is False)
workspace = agent.Workspace(tempfile.mkdtemp(prefix="mcp-work-"))
workspace.mcp = manager
schemas = agent.tool_schemas(workspace)
check(
    "stdio schemas injected",
    any(schema["function"]["name"] == echo_name for schema in schemas),
    [schema["function"]["name"] for schema in schemas],
)
readonly_schemas = agent.tool_schemas(workspace)
# The manager itself filters on workspace.read_only.
workspace.read_only = True
readonly_schemas = agent.tool_schemas(workspace)
check(
    "stdio readonly filter",
    any(schema["function"]["name"] == echo_name for schema in readonly_schemas)
    and all(schema["function"]["name"] != write_name for schema in readonly_schemas),
    [schema["function"]["name"] for schema in readonly_schemas],
)
workspace.read_only = False
check(
    "stdio call",
    workspace.dispatch(echo_name, {"text": "hello"}) == "echo: hello",
)
check("stdio resources capability", manager.has_resources() is True, manager.states)
check("stdio prompts capability", manager.has_prompts() is True, manager.states)
resource_text = workspace.dispatch("mcp_list_resources", {})
check("stdio list resources", "file:///docs/readme.txt" in resource_text, resource_text)
read_text = workspace.dispatch(
    "mcp_read_resource",
    {"server": "fake", "uri": "file:///docs/readme.txt"},
)
check("stdio read resource", "resource text for" in read_text, read_text)
image_text = workspace.dispatch(
    "mcp_read_resource",
    {"server": "fake", "uri": "file:///docs/logo.png"},
)
check("stdio binary resource", "binary resource" in image_text, image_text)
prompt_text = manager.prompts_text()
check("stdio list prompts", "code-review" in prompt_text, prompt_text)
rendered_prompt = manager.get_prompt_text(
    "fake", "code-review", {"language": "python", "focus": "security"}
)
check(
    "stdio render prompt",
    "review python" in rendered_prompt and "security" in rendered_prompt,
    rendered_prompt,
)
resource_schema_names = [
    schema["function"]["name"]
    for schema in agent.tool_schemas(workspace)
    if schema["function"]["name"] in ("mcp_list_resources", "mcp_read_resource")
]
check("stdio resource tools", len(resource_schema_names) == 2, resource_schema_names)
try:
    workspace.read_only = True
    workspace.dispatch(write_name, {"text": "nope"})
    check("stdio readonly dispatch", False)
except agent.ToolError:
    check("stdio readonly dispatch", True)
workspace.read_only = False
workspace.plan_mode = True
workspace.plan_guard = "deny"
try:
    workspace.dispatch(echo_name, {"text": "plan"})
    check("stdio plan guard", False)
except agent.ToolError as exc:
    check("stdio plan guard", "plan mode" in str(exc), str(exc))
workspace.plan_mode = False
workspace.plan_guard = "off"
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    agent.run_mcp_command(workspace, "")
check("mcp command status", "fake" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    agent.run_mcp_command(workspace, "tools")
check("mcp command tools", "echo" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    agent.run_mcp_command(workspace, "resources")
check("mcp command resources", "readme.txt" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    agent.run_mcp_command(
        workspace, "resource fake file:///docs/readme.txt"
    )
check("mcp command read resource", "resource text" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    agent.run_mcp_command(workspace, "prompts")
check("mcp command prompts", "code-review" in buffer.getvalue(), buffer.getvalue())
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    agent.run_mcp_command(
        workspace, "prompt fake code-review language=python focus=security"
    )
check("mcp command prompt", "review python" in buffer.getvalue(), buffer.getvalue())
check("mcp restart", "fake" in manager.restart("fake"), manager.states)
scheduler = agent.SchedulerService(
    workspace,
    agent.Connection(
        "http://127.0.0.1:1/v1",
        "m",
        "openai-chat-completions",
        "k",
    ),
    base_dir=tempfile.mkdtemp(prefix="mcp-scheduler-"),
    timeout=1,
)
read_only_task = scheduler._make_workspace({
    "workspace": workspace.root,
    "read_only": True,
})
full_task = scheduler._make_workspace({
    "workspace": workspace.root,
    "read_only": False,
})
check("scheduler mcp copied", read_only_task.mcp is manager, read_only_task.mcp)
read_only_names = [
    item["function"]["name"]
    for item in agent.tool_schemas(read_only_task)
    if item["function"]["name"].startswith("mcp__")
]
full_names = [
    item["function"]["name"]
    for item in agent.tool_schemas(full_task)
    if item["function"]["name"].startswith("mcp__")
]
check(
    "scheduler readonly mcp filter",
    echo_name in read_only_names and write_name not in read_only_names,
    read_only_names,
)
check("scheduler full mcp tools", write_name in full_names, full_names)
manager.close()
workspace.mcp = None


class HttpMcpHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw) if raw else {}
        method = payload.get("method")
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Mcp-Session-Id", "test-session")
            self.end_headers()
            return
        request_id = payload.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-fake", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [{
                    "name": "echo",
                    "description": "Echo over HTTP",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                    "annotations": {"readOnlyHint": True},
                }],
            }
        elif method == "tools/call":
            arguments = (payload.get("params") or {}).get("arguments") or {}
            result = {
                "content": [{"type": "text", "text": "http: " + str(arguments.get("text", ""))}],
            }
        else:
            result = {}
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "test-session")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), HttpMcpHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
http_manager = agent.McpManager({
    "http": {
        "type": "http",
        "url": f"http://127.0.0.1:{server.server_port}/mcp",
    },
}, timeout=5)
check("http connected", http_manager.states["http"]["status"] == "connected", http_manager.states)
http_name = next(iter(http_manager.tools))
check(
    "http call",
    http_manager.call_tool(http_name, {"text": "hello"}) == "http: hello",
)
http_manager.close()
server.shutdown()

print("== end-to-end CLI ==")


class E2EModelHandler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw)
        E2EModelHandler.calls.append(payload)
        if any(
            message.get("role") == "tool"
            for message in payload.get("messages", [])
        ):
            message = {"role": "assistant", "content": "final ok"}
            finish = "stop"
        else:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "mcp__fake__echo",
                        "arguments": '{"text":"hello"}',
                    },
                }],
            }
            finish = "tool_calls"
        body = json.dumps({
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


e2e_server = ThreadingHTTPServer(("127.0.0.1", 0), E2EModelHandler)
threading.Thread(target=e2e_server.serve_forever, daemon=True).start()
e2e_home = tempfile.mkdtemp(prefix="mcp-e2e-home-")
e2e_config = os.path.join(e2e_home, "config.json")
with open(e2e_config, "w", encoding="utf-8") as handle:
    json.dump({
        "version": 2,
        "active": None,
        "connections": {},
        "mcpServers": {
            "fake": {"command": sys.executable, "args": [SERVER]},
        },
        "defaults": {"mcp": True, "mcp_timeout": 10},
    }, handle)
e2e_env = dict(
    os.environ,
    HOME=e2e_home,
    BENBEN_CONFIG=e2e_config,
)
e2e = subprocess.run(
    [
        sys.executable,
        AGENT,
        "--config",
        e2e_config,
        "--no-env",
        "--base-url",
        f"http://127.0.0.1:{e2e_server.server_port}/v1",
        "--api-key",
        "k",
        "-m",
        "m",
        "--no-stream",
        "--mcp",
        "go",
    ],
    text=True,
    encoding="utf-8",
    errors="replace",
    capture_output=True,
    env=e2e_env,
    timeout=60,
)
check("e2e mcp exit", e2e.returncode == 0, e2e.stderr[-1000:])
check("e2e mcp enabled", "[mcp] enabled" in e2e.stderr, e2e.stderr[-1000:])
check("e2e mcp tool shown", "mcp__fake__echo" in e2e.stdout, e2e.stdout[-1000:])
check("e2e model called twice", len(E2EModelHandler.calls) == 2, len(E2EModelHandler.calls))
if len(E2EModelHandler.calls) >= 2:
    tool_messages = [
        message
        for message in E2EModelHandler.calls[1].get("messages", [])
        if message.get("role") == "tool"
    ]
    check(
        "e2e mcp result passed to model",
        bool(tool_messages) and tool_messages[0].get("content") == "echo: hello",
        tool_messages,
    )
e2e_server.shutdown()

print("== failure isolation ==")
bad_manager = agent.McpManager({
    "bad": {"command": "/definitely/not/a/command", "args": []},
}, timeout=1)
check("bad server failed", bad_manager.states["bad"]["status"] == "failed", bad_manager.states)
check("bad server no tools", not bad_manager.tools, bad_manager.tools)
bad_manager.close()

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
