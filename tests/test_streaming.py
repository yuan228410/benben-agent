import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


def start(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


def write_event(handler, event, split=None):
    line = ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
    if split:
        index = line.find(split)
        if index >= 0:
            cut = index + 1
            handler.wfile.write(line[:cut])
            handler.wfile.flush()
            time.sleep(0.02)
            handler.wfile.write(line[cut:])
            handler.wfile.flush()
            return
    handler.wfile.write(line)
    handler.wfile.flush()


def sse_start(handler):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()


def json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class OpenAIHandler(BaseHTTPRequestHandler):
    mode = "basic"
    requests = []
    calls = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        json_response(self, {"data": [{"id": "m"}]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw) if raw else {}
        OpenAIHandler.requests.append(payload)
        OpenAIHandler.calls += 1
        mode = OpenAIHandler.mode

        if mode == "direct":
            json_response(self, {
                "choices": [{
                    "message": {"role": "assistant", "content": "direct"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            })
            return
        if mode == "options" and "stream_options" in payload:
            json_response(self, {"error": "unknown stream_options"}, 400)
            return
        if mode == "unsupported" and payload.get("stream"):
            json_response(self, {"error": "stream is not supported"}, 400)
            return
        if mode == "retry" and OpenAIHandler.calls == 1:
            json_response(self, {"error": "temporary"}, 500)
            return
        if mode == "no_stream" or not payload.get("stream"):
            json_response(self, {
                "choices": [{
                    "message": {"role": "assistant", "content": "**完整**"},
                    "finish_reason": "stop",
                }],
            })
            return

        sse_start(self)
        if mode == "basic":
            self.wfile.write(b": ping\n\n")
            self.wfile.flush()
            write_event(self, {
                "choices": [{"delta": {"reasoning_content": "想"}, "finish_reason": None}]
            })
            write_event(self, {
                "choices": [{"delta": {"content": "你"}, "finish_reason": None}]
            }, split="你".encode())
            write_event(self, {
                "choices": [{"delta": {"content": "好"}, "finish_reason": None}]
            })
            write_event(self, {
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "run_command", "arguments": '{"command"'},
                }]}, "finish_reason": None}]
            })
            write_event(self, {
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "function": {"arguments": ': "echo hi"}'},
                }]}, "finish_reason": "tool_calls"}]
            })
            write_event(self, {
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif mode == "no_done":
            write_event(self, {
                "choices": [{"delta": {"content": "ok"}, "finish_reason": None}]
            })
            write_event(self, {
                "choices": [{"delta": {}, "finish_reason": "stop"}]
            })
        elif mode in ("options", "retry"):
            write_event(self, {
                "choices": [{"delta": {"content": "opt"}, "finish_reason": None}]
            })
            write_event(self, {
                "choices": [{"delta": {}, "finish_reason": "stop"}]
            })
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif mode == "resend":
            write_event(self, {
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "c1",
                    "function": {"name": "run_command", "arguments": '{"command"'},
                }]}, "finish_reason": None}]
            })
            write_event(self, {
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "function": {"arguments": '{"command": "echo hi"}'},
                }]}, "finish_reason": "tool_calls"}]
            })
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif mode == "interrupt":
            write_event(self, {
                "choices": [{"delta": {"content": "abc"}, "finish_reason": None}]
            })
        elif mode == "slow":
            for index in range(40):
                try:
                    write_event(self, {
                        "choices": [{
                            "delta": {"content": str(index)},
                            "finish_reason": None,
                        }]
                    })
                except (BrokenPipeError, ConnectionResetError):
                    break
                time.sleep(0.1)
        elif mode == "cli":
            write_event(self, {
                "choices": [{"delta": {"content": "第一段\n第二行"}, "finish_reason": None}]
            })
            write_event(self, {
                "choices": [{"delta": {}, "finish_reason": "stop"}]
            })
            write_event(self, {
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            })
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()


print("== OpenAI chat completions ==")
server, base = start(OpenAIHandler)
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
deltas = []
reply = client.chat(
    [{"role": "user", "content": "hi"}],
    [],
    on_delta=lambda kind, text: deltas.append((kind, text)),
)
check("openai content", reply["content"] == "你好", reply)
check("openai reasoning", reply.get("reasoning_content") == "想", reply)
check(
    "openai tool call",
    reply.get("tool_calls", [{}])[0].get("function", {}).get("arguments")
    == '{"command": "echo hi"}',
    reply,
)
check(
    "openai deltas",
    deltas == [("thinking", "想"), ("content", "你"), ("content", "好")],
    deltas,
)
check("openai usage", client.last_usage.get("prompt_tokens") == 5, client.last_usage)
check("openai finish", client.last_finish_reason == "tool_calls", client.last_finish_reason)
sent = OpenAIHandler.requests[-1]
check("openai asks stream", sent.get("stream") is True, sent)
check(
    "openai asks usage",
    sent.get("stream_options") == {"include_usage": True},
    sent,
)

print("== no [DONE] ==")
OpenAIHandler.mode = "no_done"
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
reply = client.chat([{"role": "user", "content": "hi"}], [], on_delta=lambda k, t: None)
check("no done still succeeds", reply["content"] == "ok", reply)

print("== tool argument full resend ==")
OpenAIHandler.mode = "resend"
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
reply = client.chat([{"role": "user", "content": "hi"}], [], on_delta=lambda k, t: None)
check(
    "resend merged",
    reply["tool_calls"][0]["function"]["arguments"] == '{"command": "echo hi"}',
    reply,
)

print("== direct JSON fallback ==")
OpenAIHandler.mode = "direct"
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
reply = client.chat([{"role": "user", "content": "hi"}], [], on_delta=lambda k, t: None)
check("direct json content", reply["content"] == "direct", reply)

print("== stream_options fallback ==")
OpenAIHandler.mode = "options"
OpenAIHandler.calls = 0
OpenAIHandler.requests.clear()
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
reply = client.chat([{"role": "user", "content": "hi"}], [], on_delta=lambda k, t: None)
check("options fallback reply", reply["content"] == "opt", reply)
check("options cached", client.supports_stream_options is False)
check("options requests", len(OpenAIHandler.requests) == 2, OpenAIHandler.requests)
check(
    "options removed",
    "stream_options" not in OpenAIHandler.requests[-1],
    OpenAIHandler.requests[-1],
)

print("== unsupported stream fallback ==")
OpenAIHandler.mode = "unsupported"
OpenAIHandler.calls = 0
OpenAIHandler.requests.clear()
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
reply = client.chat([{"role": "user", "content": "hi"}], [], on_delta=lambda k, t: None)
check("unsupported reply", reply["content"] == "**完整**", reply)
check("unsupported cached", client.supports_stream is False)
check("unsupported non-stream", "stream" not in OpenAIHandler.requests[-1], OpenAIHandler.requests[-1])

print("== retry before first byte ==")
OpenAIHandler.mode = "retry"
OpenAIHandler.calls = 0
OpenAIHandler.requests.clear()
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=1)
reply = client.chat([{"role": "user", "content": "hi"}], [], on_delta=lambda k, t: None)
check("retry content", reply["content"] == "opt", reply)
check("retry counted", len(OpenAIHandler.requests) == 2, OpenAIHandler.requests)

print("== interrupted stream ==")
OpenAIHandler.mode = "interrupt"
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
interrupted = []
try:
    client.chat(
        [{"role": "user", "content": "hi"}],
        [],
        on_delta=lambda kind, text: interrupted.append(text),
    )
    check("interrupted raises", False)
except agent.ChatError as exc:
    check("interrupted raises", "finish_reason" in str(exc), str(exc))
check("interrupted kept partial", interrupted == ["abc"], interrupted)

print("== cancellation ==")
OpenAIHandler.mode = "slow"
client = agent.ChatClient(base, "", "m", timeout=5, max_retries=0)
cancel = threading.Event()
threading.Timer(0.3, cancel.set).start()
seen = []
started = time.monotonic()
try:
    client.chat(
        [{"role": "user", "content": "hi"}],
        [],
        on_delta=lambda kind, text: seen.append(text),
        cancel_event=cancel,
    )
    check("cancel raises", False)
except agent.ChatCancelled:
    check("cancel raises", True)
elapsed = time.monotonic() - started
check("cancel fast", elapsed < 2.5, elapsed)
check("cancel partial", bool(seen), seen)


class AnthropicHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        sse_start(self)
        events = [
            {"type": "message_start", "message": {"usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": 2,
            }}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "想"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "signature_delta", "signature": "sig"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "text_delta", "text": "你好"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2,
             "content_block": {"type": "tool_use", "id": "tu1",
                               "name": "run_command", "input": {}}},
            {"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta",
                       "partial_json": '{"command": '}},
            {"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta",
                       "partial_json": '"echo hi"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
             "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        ]
        for event in events:
            self.wfile.write(
                ("event: x\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
            )
            self.wfile.flush()


print("== Anthropic ==")
server2, base2 = start(AnthropicHandler)
client = agent.AnthropicClient(base2, "k", "m", timeout=5, max_retries=0)
deltas = []
reply = client.chat(
    [{"role": "user", "content": "hi"}],
    [],
    on_delta=lambda kind, text: deltas.append((kind, text)),
)
check("anthropic content", reply["content"] == "你好", reply)
check("anthropic thinking", reply.get("reasoning_content") == "想", reply)
check(
    "anthropic signature",
    reply.get("_thinking_blocks", [{}])[0].get("signature") == "sig",
    reply,
)
check(
    "anthropic tool",
    reply["tool_calls"][0]["function"]["arguments"] == '{"command": "echo hi"}',
    reply,
)
check("anthropic deltas", deltas == [("thinking", "想"), ("content", "你好")], deltas)
check("anthropic usage", client.last_usage.get("prompt_tokens") == 7, client.last_usage)


class ResponsesHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        sse_start(self)
        events = [
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "message"}},
            {"type": "response.output_text.delta", "output_index": 0,
             "delta": "你好"},
            {"type": "response.output_item.done", "output_index": 0,
             "item": {"type": "message"}},
            {"type": "response.output_item.added", "output_index": 1,
             "item": {"type": "function_call", "call_id": "c1",
                      "name": "run_command", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 1,
             "delta": '{"command"'},
            {"type": "response.function_call_arguments.delta", "output_index": 1,
             "delta": ': "echo hi"}'},
            {"type": "response.output_item.done", "output_index": 1,
             "item": {"type": "function_call", "call_id": "c1",
                      "name": "run_command",
                      "arguments": '{"command": "echo hi"}'}},
            {"type": "response.completed", "response": {
                "output": [
                    {"type": "message", "content": [
                        {"type": "output_text", "text": "你好"},
                    ]},
                    {"type": "function_call", "call_id": "c1",
                     "name": "run_command",
                     "arguments": '{"command": "echo hi"}'},
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            }},
        ]
        for event in events:
            self.wfile.write(
                ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
            )
            self.wfile.flush()


print("== Responses ==")
server3, base3 = start(ResponsesHandler)
client = agent.ResponsesClient(base3, "k", "m", timeout=5, max_retries=0)
deltas = []
reply = client.chat(
    [{"role": "user", "content": "hi"}],
    [],
    on_delta=lambda kind, text: deltas.append((kind, text)),
)
check("responses content", reply["content"] == "你好", reply)
check(
    "responses tool",
    reply["tool_calls"][0]["function"]["arguments"] == '{"command": "echo hi"}',
    reply,
)
check("responses deltas", deltas == [("content", "你好")], deltas)
check("responses usage", client.last_usage.get("prompt_tokens") == 5, client.last_usage)


class StubClient:
    model = "m"
    reasoning = None
    last_usage = {"prompt_tokens": 3, "completion_tokens": 2}
    last_finish_reason = "stop"
    last_reasoning_ignored = False
    last_turn_usage = {}
    last_turn_truncated = False

    def chat(self, messages, tools, on_delta=None, cancel_event=None):
        on_delta("content", "A")
        on_delta("thinking", "T")
        on_delta("content", "B")
        return {
            "role": "assistant",
            "content": "AB",
            "reasoning_content": "T",
        }


print("== run_turn events ==")
ws = agent.Workspace(tempfile.mkdtemp(prefix="stream-events-"))
events = []
ok = agent.run_turn(
    StubClient(),
    ws,
    [{"role": "user", "content": "hi"}],
    1,
    show_thinking=True,
    emit=lambda kind, text: events.append((kind, text)),
    stream=True,
)
check("turn ok", ok)
check(
    "turn events order",
    [event for event in events if event[0] in (
        "content_start", "content_delta", "thinking_delta", "content",
    )] == [
        ("content_start", "1"),
        ("content_delta", "A"),
        ("thinking_delta", "T"),
        ("content_delta", "B"),
        ("content", "AB"),
    ],
    events,
)


print("== CLI ==")
OpenAIHandler.mode = "cli"
OpenAIHandler.requests.clear()
home = tempfile.mkdtemp(prefix="stream-cli-")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=os.path.join(home, "config.json"))
base_args = [
    sys.executable,
    AGENT,
    "--no-config",
    "--no-env",
    "--base-url",
    base,
    "--api-key",
    "k",
    "-m",
    "m",
    "--yes",
]
stream_run = subprocess.run(
    base_args + ["--stream", "hello"],
    text=True,
    capture_output=True,
    env=env,
    timeout=40,
)
check("cli stream exit", stream_run.returncode == 0, stream_run.stderr[-800:])
check(
    "cli stream raw",
    "agent> 第一段\n第二行" in stream_run.stdout,
    stream_run.stdout[-800:],
)
check("cli stream request", OpenAIHandler.requests[-1].get("stream") is True)
OpenAIHandler.mode = "cli"
OpenAIHandler.requests.clear()
plain_run = subprocess.run(
    base_args + ["--no-stream", "hello"],
    text=True,
    capture_output=True,
    env=env,
    timeout=40,
)
check("cli no-stream exit", plain_run.returncode == 0, plain_run.stderr[-800:])
check("cli no-stream text", "**完整**" in plain_run.stdout, plain_run.stdout[-800:])
check("cli no-stream request", "stream" not in OpenAIHandler.requests[-1])

OpenAIHandler.mode = "cli"
OpenAIHandler.requests.clear()
interactive_run = subprocess.run(
    base_args + ["--stream"],
    input=(
        "/stream\n"
        "/stream off\n"
        "first turn\n"
        "/config save\n"
        "/stream on\n"
        "second turn\n"
        "/stream auto\n"
        "/exit\n"
    ),
    text=True,
    capture_output=True,
    env=env,
    timeout=60,
)
check("cli stream switch exit", interactive_run.returncode == 0, interactive_run.stderr[-800:])
check(
    "cli stream status",
    "stream: on (session) | startup default: on" in interactive_run.stdout,
    interactive_run.stdout[-1500:],
)
check(
    "cli stream off message",
    "stream: off; next turns wait for the full response" in interactive_run.stdout,
    interactive_run.stdout[-1500:],
)
turn_requests = OpenAIHandler.requests
stream_flags = [bool(item.get("stream")) for item in turn_requests]
check("cli stream switch requests", stream_flags[-2:] == [False, True], stream_flags)
saved_config = json.load(
    open(os.path.join(home, "config.json"), encoding="utf-8")
)
check(
    "cli config save stream",
    saved_config.get("defaults", {}).get("stream") is False,
    saved_config.get("defaults"),
)

print("== config defaults ==")
defaults = agent.default_settings()
check("default stream", defaults["stream"] is True)
check("default stream idle", defaults["stream_idle_timeout"] == 300)
check("default stream flush", defaults["stream_flush_ms"] == 40)
check("default debug stream", defaults["debug_stream"] is False)

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
