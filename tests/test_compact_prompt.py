"""检查压缩摘要的模板和滚动压缩的合并规则。

以前提示词只列了 7 个小节名，没有说明，也没说滚动压缩时新旧内容冲突该以
谁为准。后果：旧摘要里已完成的事被继续挂在"未完成"，先后矛盾的方案被并列
写进摘要。

现在模板带每节说明、8 个小节，滚动压缩有单独的合并说明。

运行时**不校验**模型的回复格式：模型偶尔回散文时，下一次压缩会把它当作
「已有摘要」重新按模板整理；为此多花一次完整调用（约 13 万 token）不值得。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("agent_prompt_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)

print("== 模板结构 ==")
expected = (
    "目标", "约束与偏好", "关键决策", "工作进展",
    "下一步", "涉及的文件与路径", "执行过的命令", "注意事项",
)
check("section list matches the template",
      mod.COMPACT_SUMMARY_SECTIONS == expected, mod.COMPACT_SUMMARY_SECTIONS)
for name in expected:
    check(f"template has ## {name}", f"## {name}" in mod.COMPACT_PROMPT, name)
check("work progress covers three states",
      all(k in mod.COMPACT_PROMPT for k in ("已完成：", "进行中：", "受阻：")))
check("next move is an ordered list", "1. 按顺序" in mod.COMPACT_PROMPT)
check("relevant files capped", "最多 15 个" in mod.COMPACT_PROMPT)
check("length limit stated", f"{mod.COMPACT_SUMMARY_CHARS} 字" in mod.COMPACT_PROMPT)

print("\n== 规则要点 ==")
rules = [
    ("tells the model to omit system instructions", "系统指令"),
    ("tells the model to keep paths verbatim", "原样保留"),
    ("tells the model to drop resolved questions", "已被后续对话解决"),
    ("tells the model not to call tools", "不要调用工具"),
    ("tells the model not to mention compaction", "不要提到"),
    ("forbids a preamble", "不要加开头说明"),
]
for label, needle in rules:
    check(label, needle in mod.COMPACT_PROMPT, needle)

print("\n== 不做运行时格式校验 ==")
check("no nudge constant", not hasattr(mod, "COMPACT_NUDGE"))
check("no format validator", not hasattr(mod, "_compact_summary_filled"))
check("headings helper still documents the sections",
      len(mod._compact_headings()) == len(expected))


class Recorder(BaseHTTPRequestHandler):
    replies: list[str] = []
    requests: list[list[dict]] = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        Recorder.requests.append(body["messages"])
        index = len(Recorder.requests) - 1
        content = Recorder.replies[index] if index < len(Recorder.replies) else "## 目标\n- 兜底"
        payload = json.dumps({
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = HTTPServer(("127.0.0.1", 0), Recorder)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = mod.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=10)
workspace = mod.Workspace(tempfile.mkdtemp(prefix="benben-prompt-"))

try:
    print("\n== 端到端：散文回复也照收，不额外发请求 ==")
    Recorder.replies = ["散文总结，没有小节。"]
    Recorder.requests = []
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "尾巴"},
        {"role": "assistant", "content": "尾巴答"},
    ]
    result = mod.compact_history(client, history, None, keep=1, context_window=1_000_000)
    check("prose is accepted without a retry", len(Recorder.requests) == 1,
          len(Recorder.requests))
    check("prose is kept as the summary", "散文总结" in result["summary"],
          result["summary"])

    print("\n== 端到端：滚动压缩的合并说明 ==")
    Recorder.replies = ["## 目标\n- 合并结果"]
    Recorder.requests = []
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "[历史对话摘要]\n\n## 目标\n旧目标",
         "_compact": True, "_compact_keep": 1},
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
        {"role": "user", "content": "尾巴"},
        {"role": "assistant", "content": "尾巴答"},
    ]
    mod.compact_history(client, history, None, keep=1, context_window=1_000_000)
    prompt = Recorder.requests[0][0]["content"]
    check("rolling prompt states newer history wins",
          "新对话优先于旧摘要" in prompt, prompt[-400:])
    check("rolling prompt asks to drop resolved items",
          "已被新对话解决" in prompt)
    check("rolling prompt carries the old summary once",
          prompt.count("已有摘要：") == 1, prompt.count("已有摘要："))
    check("rolling prompt does not duplicate the summary body",
          prompt.count("旧目标") == 1, prompt.count("旧目标"))
    check("rolling prompt includes the new conversation",
          "新问题" in prompt)
    check("rolling compaction also uses one request",
          len(Recorder.requests) == 1, len(Recorder.requests))

finally:
    server.shutdown()
    workspace.close()
    shutil.rmtree(workspace.root, ignore_errors=True)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all compaction prompt checks passed")
