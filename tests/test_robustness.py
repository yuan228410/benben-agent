from pathlib import Path
import importlib.util
import json
import os
import tempfile
import threading
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


class Handler(BaseHTTPRequestHandler):
    mode = "chat_list"
    calls = 0
    requests = []

    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Handler.requests.append(json.loads(raw))
        Handler.calls += 1
        path = self.path
        if Handler.mode == "chat_list":
            self._json({
                "choices": [{
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
            return
        if Handler.mode == "anthropic_string":
            self._json({
                "content": "ok",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
            return
        if Handler.mode == "responses_text":
            self._json({
                "status": "completed",
                "output_text": "ok",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            })
            return
        if Handler.mode == "bad_tool":
            if Handler.calls == 1:
                self._json({
                    "choices": [{
                        "message": {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]},
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })
            else:
                self._json({
                    "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })
            return
        self._json({"error": "unknown mode"})


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"

print("== protocol fallbacks ==")
Handler.mode = "chat_list"
chat = agent.ChatClient(BASE + "/v1", "t", "m", timeout=5)
msg = chat.chat([{"role": "user", "content": "hi"}], [])
check("chat content list", msg["content"] == "ok", msg)

Handler.mode = "anthropic_string"
anthropic = agent.AnthropicClient(BASE, "t", "m", timeout=5)
msg = anthropic.chat([{"role": "user", "content": "hi"}], [])
check("anthropic string content", msg["content"] == "ok", msg)

Handler.mode = "responses_text"
responses = agent.ResponsesClient(BASE, "t", "m", timeout=5)
msg = responses.chat([{"role": "user", "content": "hi"}], [])
check("responses output_text", msg["content"] == "ok", msg)

print("== malformed tool calls ==")
cleaned = agent.clean_assistant_message({
    "role": "assistant",
    "content": "",
    "tool_calls": [{"id": "x"}],
})
check("cleaned call", cleaned.get("tool_calls") == [{
    "id": "x",
    "type": "function",
    "function": {"name": "unknown", "arguments": "{}"},
}], cleaned)

Handler.mode = "bad_tool"
Handler.calls = 0
Handler.requests.clear()
chat = agent.ChatClient(BASE + "/v1", "t", "m", timeout=5)
ws = agent.Workspace(tempfile.mkdtemp(prefix="robust-ws-"))
messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
ok = agent.run_turn(chat, ws, messages, 3)
check("turn survives malformed call", ok, messages)
check("tool result added", any(
    message.get("role") == "tool" and message.get("tool_call_id") == "x"
    for message in messages
), messages)
check("next request has tool", any(
    message.get("role") == "tool" for message in Handler.requests[-1]["messages"]
), Handler.requests[-1]["messages"])

server.shutdown()
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
