from pathlib import Path
import importlib.util
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HOME = tempfile.mkdtemp(prefix="compact-home-")
os.environ["HOME"] = HOME

spec = importlib.util.spec_from_file_location("agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


class Handler(BaseHTTPRequestHandler):
    fail = False
    requests = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.requests.append(json.loads(raw))
        if Handler.fail:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "## 目标\n测试摘要\n## 已完成\n无"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = agent.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=5, max_retries=0)

ws = agent.Workspace(tempfile.mkdtemp(prefix="benben-workspace-"))
image_part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
system = {"role": "system", "content": "SYS"}
messages = [
    system,
    {"role": "user", "content": [{"type": "text", "text": "第一轮问题"}, image_part]},
    {"role": "assistant", "content": "第一轮回答"},
    {"role": "user", "content": "第二轮问题"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{\"path\":\".\"}"}}
    ]},
    {"role": "tool", "tool_call_id": "c1", "content": "a.txt\nb.txt"},
    {"role": "assistant", "content": "第二轮回答"},
    {"role": "user", "content": "第三轮问题"},
    {"role": "assistant", "content": "第三轮回答"},
    {"role": "user", "content": "第四轮问题"},
    {"role": "assistant", "content": "第四轮回答"},
]
session = agent.create_session("m", ws.root)
session.log(messages)
path = session.path

# keep last 2 user turns: split before 第三轮问题
stats = agent.compact_history(client, messages, session, 2)
check("stats before", stats["before_messages"] == 6, stats)
check("stats kept", stats["kept_messages"] == 4, stats)
check("stats after", stats["after_messages"] == 5, stats)
check("persisted", stats["persisted"] is True)
check("usage merged", stats["usage"]["total_tokens"] == 10, stats["usage"])
check("uses one chunk", stats["chunks"] == 1, stats)

prompt = Handler.requests[-1]["messages"][0]["content"]
check("image omitted", "base64" not in prompt and "AAAA" not in prompt, prompt[:200])
check("image placeholder", "[图片]" in prompt)
check("tool rendered", "list_dir" in prompt and "a.txt" in prompt)
check("no tools passed", "tools" not in Handler.requests[-1], Handler.requests[-1].keys())

check("memory summary first", messages[1].get("_compact") is True, messages[:2])
check("memory keeps tail", messages[2]["content"] == "第三轮问题", messages[2])
check("memory length", len(messages) == 6, len(messages))

with open(path, encoding="utf-8") as handle:
    lines = [json.loads(line) for line in handle if line.strip()]
markers = [record for record in lines if record.get("_type") == "compact"]
check("marker appended", len(markers) == 1, len(markers))
check("marker summary", "测试摘要" in markers[0]["summary"])
check("old history kept on disk", any(record.get("content") == "第一轮回答" for record in lines))
check("tail duplicated on disk", sum(1 for record in lines if record.get("content") == "第四轮回答") == 2)

loaded = [system]
loaded_session = agent.load_session(session.uuid, loaded)
check("load summary", loaded[1].get("_compact") is True, loaded[:2])
check("load keeps tail", loaded[2]["content"] == "第三轮问题", loaded[2])
check("load drops old", all(message.get("content") != "第一轮回答" for message in loaded), loaded)
check("load length", len(loaded) == 6, len(loaded))
check("load saved", loaded_session.saved == len(loaded))

rows = agent.session_summaries()
row = next(item for item in rows if item["uuid"] == session.uuid)
check("summary session count", row["count"] == 5, row)
check("summary session title", row["title"] == "第一轮问题", row)

view = agent.history_view(loaded)
check("history notice", view[0]["role"] == "notice" and "测试摘要" in view[0]["summary"], view[0])
check("history no old user", all(item.get("text") != "第一轮问题" for item in view), view)

saved = agent.render_history(loaded, "m", ws.root)
check("save renders summary", "## 上下文摘要" in saved and "测试摘要" in saved, saved[:200])

# append a new turn after compaction, then load again
messages.append({"role": "user", "content": "压缩后问题"})
messages.append({"role": "assistant", "content": "压缩后回答"})
session.log(messages)
loaded2 = [system]
agent.load_session(session.uuid, loaded2)
check("post-compact load", loaded2[-2]["content"] == "压缩后问题", loaded2[-2:])
check("post-compact drops old", all(message.get("content") != "第一轮回答" for message in loaded2))

# second compaction: last marker wins
stats2 = agent.compact_history(client, messages, session, 1)
check("second stats", stats2["keep"] == 1 and stats2["kept_messages"] == 2, stats2)
loaded3 = [system]
agent.load_session(session.uuid, loaded3)
check("second marker wins", len(loaded3) == 4, loaded3)
check("second summary only", loaded3[1].get("_compact") is True and loaded3[2]["content"] == "压缩后问题", loaded3)

# resume -> append -> compact again using the resumed session object
resumed = [{"role": "system", "content": "SYS"}]
resumed_session = agent.load_session(session.uuid, resumed)
resumed.append({"role": "user", "content": "恢复后问题"})
resumed.append({"role": "assistant", "content": "恢复后回答"})
resumed_session.log(resumed)
stats_resume = agent.compact_history(client, resumed, resumed_session, 1)
check("resume compact", stats_resume["before_total"] == 5, stats_resume)
again = [{"role": "system", "content": "SYS"}]
agent.load_session(session.uuid, again)
check("resume compact load", len(again) == 4 and again[1].get("_compact"), again)
check("resume compact tail", again[2]["content"] == "恢复后问题", again)
check("resume compact no old", all(m.get("content") != "压缩后问题" for m in again), again)

# keep=0: summary only
session0 = agent.create_session("m", ws.root)
messages0 = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "问题一"},
    {"role": "assistant", "content": "回答一"},
    {"role": "user", "content": "问题二"},
    {"role": "assistant", "content": "回答二"},
]
session0.log(messages0)
stats0 = agent.compact_history(client, messages0, session0, 0)
check("keep0 stats", stats0["after_messages"] == 1 and stats0["kept_messages"] == 0, stats0)
loaded0 = [{"role": "system", "content": "SYS"}]
agent.load_session(session0.uuid, loaded0)
check("keep0 load", len(loaded0) == 2 and loaded0[1].get("_compact"), loaded0)

# nothing to compact
try:
    agent.compact_history(client, [{"role": "system", "content": "SYS"}], None, 2)
    check("empty raises", False)
except agent.CompactError:
    check("empty raises", True)

# failure leaves the file untouched
before = os.path.getsize(path)
Handler.fail = True
try:
    agent.compact_history(client, messages, session, 0)
    check("failure raises", False)
except agent.ChatError:
    check("failure raises", True)
Handler.fail = False
check("failure no write", os.path.getsize(path) == before)

# long history triggers chunking
old_max = agent.COMPACT_MAX_CHARS
agent.COMPACT_MAX_CHARS = 300
Handler.requests.clear()
session2 = agent.create_session("m", ws.root)
messages2 = [{"role": "system", "content": "SYS"}]
for index in range(8):
    messages2.append({"role": "user", "content": ("问题%d " % index) + "很长的内容" * 30})
    messages2.append({"role": "assistant", "content": ("回答%d " % index) + "很长的回答" * 30})
session2.log(messages2)
stats3 = agent.compact_history(client, messages2, session2, 0)
check("chunked", stats3["chunks"] > 1, stats3["chunks"])
check("chunked calls", len(Handler.requests) == stats3["chunks"], len(Handler.requests))
check("rolling prompt", "已有摘要" in Handler.requests[1]["messages"][0]["content"], Handler.requests[1]["messages"][0]["content"][:120])
agent.COMPACT_MAX_CHARS = old_max

# web notice shape
check("notice keep label", "保留最近 2 轮" in view[0]["text"], view[0]["text"])

server.shutdown()
shutil.rmtree(HOME, ignore_errors=True)
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
