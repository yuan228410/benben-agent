from pathlib import Path
import tempfile
import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

spec = importlib.util.spec_from_file_location("agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


class Handler(BaseHTTPRequestHandler):
    response_finish = "length"

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "ok", "reasoning_content": "think"},
            "finish_reason": Handler.response_finish,
        }], "usage": {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            "prompt_tokens_details": {"cached_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 3},
        }}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = agent.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=5)
ws = agent.Workspace(tempfile.mkdtemp(prefix="benben-workspace-"))
messages = [{"role": "user", "content": "hi"}]
events = []
ok = agent.run_turn(client, ws, messages, 2, emit=lambda kind, text: events.append((kind, text)))
print("events:", events)
check("turn ok", ok)
check("usage event", any(k == "usage" and "input=10" in t and "output=5" in t and "reasoning=3" in t and "cached=2" in t for k, t in events), events)
check("warning event", any(k == "warning" and "截断" in t for k, t in events), events)
check("usage totals", client.last_turn_usage == {
    "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    "reasoning_tokens": 3, "cached_tokens": 2,
}, client.last_turn_usage)
check("truncated flag", client.last_turn_truncated is True)

Handler.response_finish = "stop"
client2 = agent.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=5)
events2 = []
agent.run_turn(client2, ws, [{"role": "user", "content": "hi"}], 2, emit=lambda kind, text: events2.append((kind, text)))
check("no warning on stop", not any(k == "warning" for k, _ in events2), events2)
check("usage still emitted", any(k == "usage" for k, _ in events2), events2)
events3 = []
agent.run_turn(
    client2,
    ws,
    [{"role": "user", "content": "hi"}],
    2,
    emit=lambda kind, text: events3.append((kind, text)),
    context_window=1000,
)
check(
    "usage context emitted",
    any(
        k == "usage" and "| context=~" in t and "/1000" in t and "%" in t
        for k, t in events3
    ),
    events3,
)
server.shutdown()

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
