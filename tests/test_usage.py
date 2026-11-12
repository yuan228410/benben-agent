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
usage_text = next((t for k, t in events if k == "usage"), "")
check("usage event", bool(usage_text), events)
# 新格式：turn / total 分段，含 hit 命中率、new 新增、calls 调用次数
check("usage turn segment", "turn:" in usage_text, usage_text)
check("usage total segment", "total:" in usage_text, usage_text)
check("usage calls", "calls=1" in usage_text, usage_text)
check("usage hit rate", "hit=20%" in usage_text, usage_text)
check("usage new field", "new=8" in usage_text, usage_text)
check("usage total tokens", "tokens=15" in usage_text, usage_text)
check("usage reasoning", "reasoning=3" in usage_text, usage_text)
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
        k == "usage" and "| context=" in t and "/1000" in t and "%" in t
        for k, t in events3
    ),
    events3,
)

# --- normalize_usage：不同网关的缓存字段都要归一化 ---
n1 = agent.normalize_usage({
    "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
    "prompt_tokens_details": {"cached_tokens": 30},
    "completion_tokens_details": {"reasoning_tokens": 5},
})
check("normalize openai cached", n1.get("cached_tokens") == 30, n1)
check("normalize openai reasoning", n1.get("reasoning_tokens") == 5, n1)
check("normalize openai totals", n1.get("prompt_tokens") == 100 and n1.get("total_tokens") == 110, n1)

n2 = agent.normalize_usage({
    "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
    "prompt_cache_hit_tokens": 40,
})
check("normalize hit-tokens cached", n2.get("cached_tokens") == 40, n2)

check("normalize non-dict", agent.normalize_usage(None) == {}, agent.normalize_usage(None))
check("normalize empty", agent.normalize_usage({}) == {}, agent.normalize_usage({}))

# --- project_context_tokens：用真实值 + 估算增量推算上下文 ---
pc = agent.ChatClient("http://127.0.0.1:1/v1", "", "m", timeout=5)
pc.last_prompt_tokens = None
pc.last_prompt_estimate = None
check("project no baseline", agent.project_context_tokens(pc, [{"role": "user", "content": "x"}]) is None)

base_msgs = [{"role": "user", "content": "x" * 100}]
pc.last_prompt_tokens = 500
pc.last_prompt_estimate = agent.estimate_tokens(base_msgs)
same = agent.project_context_tokens(pc, list(base_msgs))
check("project zero delta", same == 500, same)
more = base_msgs + [{"role": "user", "content": "y" * 100}]
grew = agent.project_context_tokens(pc, more)
check("project positive delta", isinstance(grew, int) and grew > 500, grew)
check("project delta is estimate-based", grew == 500 + (agent.estimate_tokens(more) - pc.last_prompt_estimate), grew)

server.shutdown()

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
