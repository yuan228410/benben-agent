"""检查压缩：单次请求、超预算丢最旧。

旧实现按 48000 字符分块，每块单独请求模型，且每块的 prompt 前缀都不同
（第二块起会带上"已有摘要"），所以 prompt cache 从不命中。一个 950 条消息
的会话要 9 次串行请求、每次重新 prefill 约 47000 字符，总共 6-9 分钟。

现在改成：整段历史一次请求；放不下就丢掉最早的对话（与 opencode 一致），
而不是分块滚动总结。请求数从 N 降到 1。
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("agent_compact_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)

prompts: list[str] = []


class Handler(BaseHTTPRequestHandler):
    overflow_times = 0
    fail_auth = False
    sizes: list[int] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        text = "\n".join(str(m.get("content") or "") for m in body.get("messages", []))
        prompts.append(text)
        Handler.sizes.append(len(text))

        if Handler.fail_auth:
            payload = json.dumps({
                "error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}
            }).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if Handler.overflow_times > 0:
            Handler.overflow_times -= 1
            payload = json.dumps({
                "error": {
                    "message": "This model's maximum context length is 128000 tokens. "
                               "However, your messages resulted in 200000 tokens.",
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded",
                }
            }).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        payload = json.dumps({
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "## 摘要\n- 做完了"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": len(text) // 3, "completion_tokens": 10,
                      "total_tokens": len(text) // 3 + 10},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = mod.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=10)


def build_history(turns: int, body: str = "x" * 400) -> list[dict]:
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"请求{i}：{body}"})
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"c{i}",
                "type": "function",
                "function": {"name": "run_command",
                             "arguments": json.dumps({"command": "ls"})},
            }],
        })
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "out\n" * 100})
        msgs.append({"role": "assistant", "content": f"结论{i}：" + "y" * 200})
    return msgs


try:
    # ---------- 1. 单次请求 ----------
    prompts.clear()
    history = build_history(60)
    result = mod.compact_history(client, history, None, keep=2, context_window=1_000_000)
    check("compaction uses a single request", len(prompts) == 1, len(prompts))
    check("summary recorded", bool(result["summary"]))
    check("history actually shrank",
          result["after_messages"] < result["before_messages"],
          f"{result['before_messages']} -> {result['after_messages']}")
    check("keep=2 tail is preserved",
          result["kept_messages"] >= 2, result["kept_messages"])

    # 旧实现同样输入会切成多段（取足够大的历史才看得出差别）
    big = build_history(200)
    prefix = big[1:mod._compact_split(big, 2)]
    entries = [t for t in (mod._summary_text(m) for m in prefix) if t]
    total_chars = sum(len(e) + 1 for e in entries)
    old_chunks = max(1, -(-total_chars // 48_000))
    check("old chunking would have needed multiple requests",
          old_chunks > 1, f"{total_chars} chars / 48000 = {old_chunks}")

    # ---------- 2. 超预算丢最旧 ----------
    prompts.clear()
    history = build_history(200)
    result = mod.compact_history(client, history, None, keep=2, context_window=8_000)
    check("oversized history still uses one request", len(prompts) == 1, len(prompts))
    check("dropped count is reported", result["dropped"] > 0, result["dropped"])
    sent = prompts[0]
    # 丢弃的是最早的对话：被压缩的中间部分必须还在（最后 2 轮留作 tail）
    check("newest compacted turns survive trimming",
          "请求197" in sent or "结论197" in sent, sent[-300:])
    check("oldest turns are the ones dropped",
          "请求0：" not in sent, sent[:300])
    check("trimmed prompt fits the budget",
          mod._estimate_text_tokens(sent) < 8000 + 500,
          mod._estimate_text_tokens(sent))

    # ---------- 3. 窗口足够时不丢弃 ----------
    prompts.clear()
    history = build_history(5)
    result = mod.compact_history(client, history, None, keep=2, context_window=1_000_000)
    check("nothing dropped when it fits", result["dropped"] == 0, result["dropped"])

    # ---------- 4. 刷新时承接旧摘要 ----------
    history = build_history(10)
    first = mod.compact_history(client, history, None, keep=2, context_window=1_000_000)
    prompts.clear()
    # 再压一次：这次历史开头是上一次的摘要
    history.extend(build_history(10)[1:])
    second = mod.compact_history(client, history, None, keep=2, context_window=1_000_000)
    check("second compaction reuses carried summary",
          "已有摘要" in prompts[0], prompts[0][:200])
    check("second compaction keeps the old summary text",
          "做完了" in prompts[0], prompts[0][:200])

    # ---------- 5. 边界 ----------
    try:
        mod.compact_history(client, [{"role": "user", "content": "hi"}], None, keep=2)
        check("rejects history without system prompt", False)
    except mod.CompactError as exc:
        check("rejects history without system prompt", "system prompt" in str(exc), str(exc))

    try:
        mod.compact_history(
            client,
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "a"}],
            None,
            keep=2,
        )
        check("rejects too-short history", False)
    except mod.CompactError as exc:
        check("rejects too-short history", "too short" in str(exc), str(exc))

    # keep=0 应保留零条尾巴
    history = build_history(10)
    result = mod.compact_history(client, history, None, keep=0, context_window=1_000_000)
    check("keep=0 keeps no tail messages", result["kept_messages"] == 0,
          result["kept_messages"])

    # ---------- 6. 预算计算 ----------
    fit = mod._fit_compaction_transcript
    # 三条 100 字符英文各 26 token；固定开销 157，所以预算 200 只留得下最后一条
    text, dropped = fit(["a" * 100, "b" * 100, "c" * 100], 200)
    check("fit drops from the front", dropped == 2, dropped)
    check("fit keeps only what fits",
          "a" * 100 not in text and "c" * 100 in text,
          f"dropped={dropped} len={len(text)}")

    # 预算连固定开销都不够：全部丢弃，但已有摘要仍然保留
    text, dropped = fit(["a" * 100, "b" * 100], 100)
    check("fit drops everything when budget is below overhead",
          dropped == 2 and text == "", f"dropped={dropped} len={len(text)}")

    text, dropped = fit(["a" * 100], 10)
    check("fit truncates when one entry exceeds budget",
          dropped == 1 or len(text) < 200, f"dropped={dropped} len={len(text)}")

    text, dropped = fit([], 1000)
    check("fit handles empty input", text == "" and dropped == 0)

    text, dropped = fit(["x" * 50], 10_000, carried="旧摘要内容")
    check("carried summary is always kept", "旧摘要内容" in text, text[:80])

    # ---------- 6b. 固定开销算进预算 ----------
    # COMPACT_PROMPT 本身就占约 145 token，漏算会让请求在窗口边缘被拒
    overhead = mod._compaction_fixed_overhead()
    check("fixed overhead is counted",
          overhead >= mod._estimate_text_tokens(mod.COMPACT_PROMPT),
          overhead)
    check("fixed overhead covers the prompt", overhead > 100, overhead)

    # 拼出的完整 prompt 必须真的落在预算内（端到端核对，不只看中间值）
    for window in (8_000, 32_000, 128_000):
        history = build_history(40)
        prefix = history[1:mod._compact_split(history, 2)]
        items = [t for t in (mod._summary_text(m) for m in prefix) if t]
        reserve = min(mod.COMPACT_OUTPUT_RESERVE, max(256, window // 5))
        budget = max(1, window - reserve)
        tr, _ = fit(items, budget)
        full = f"{mod.COMPACT_PROMPT}\n\n待压缩的对话：\n{tr}"
        actual = mod._estimate_text_tokens(full) + mod.COMPACT_MESSAGE_OVERHEAD
        check(f"prompt fits budget at window {window}",
              actual <= budget, f"{actual} > {budget}")

    # ---------- 7. 估算改成 token，不再用固定字符比 ----------
    # 纯中文按 1 字/token（非宽字符部分至少算 1，所以是 6+1）
    check("cjk counts one token per char",
          mod._estimate_text_tokens("中文测试内容") == 7,
          mod._estimate_text_tokens("中文测试内容"))
    check("latin counts four chars per token",
          mod._estimate_text_tokens("abcdefgh") == 2,
          mod._estimate_text_tokens("abcdefgh"))
    check("no fixed chars-per-token constant remains",
          not hasattr(mod, "COMPACT_CHARS_PER_TOKEN"))

    cjk, _ = fit(["中" * 400], 100)
    check("cjk budget measured in tokens not chars",
          len(cjk) < 400 * 3, f"kept {len(cjk)} chars for a 100-token budget")

    # ---------- 8. 乐观估算 + 缩减重试 ----------
    # 让服务器前 N 次返回"上下文超长"，之后正常回复
    Handler.overflow_times = 2
    Handler.sizes.clear()
    history = build_history(60)
    result = mod.compact_history(
        client, history, None, keep=2, context_window=20_000
    )
    check("shrinks and retries on overflow", Handler.overflow_times == 0,
          Handler.overflow_times)
    check("attempts counter reports the retries", result["attempts"] == 2,
          result["attempts"])
    check("each retry sends less than the last",
          len(Handler.sizes) == 3 and
          Handler.sizes[1] < Handler.sizes[0] and Handler.sizes[2] < Handler.sizes[1],
          Handler.sizes)
    check("summary still produced after retries", bool(result["summary"]))

    # 非超长错误不该重试，应直接抛出
    Handler.overflow_times = 0
    Handler.fail_auth = True
    Handler.sizes.clear()
    try:
        mod.compact_history(client, build_history(10), None, keep=2,
                            context_window=1_000_000)
        check("auth error is not retried", False)
    except mod.ChatError as exc:
        check("auth error is not retried", len(Handler.sizes) == 1, Handler.sizes)
    Handler.fail_auth = False

    # 一直超长时重试次数有上限，不会无限循环
    Handler.overflow_times = 99
    Handler.sizes.clear()
    try:
        mod.compact_history(client, build_history(60), None, keep=2,
                            context_window=1_000_000)
        check("gives up after retry limit", False)
    except mod.ChatError as exc:
        check("gives up after retry limit",
              len(Handler.sizes) <= len(mod.COMPACT_SHRINK_STEPS) + 1,
              len(Handler.sizes))
    check("shrink steps are gradual, not halved",
          mod.COMPACT_SHRINK_STEPS == (0.85, 0.7, 0.5, 0.35),
          mod.COMPACT_SHRINK_STEPS)
    check("_looks_like_context_overflow matches provider wording",
          mod._looks_like_context_overflow(
              mod.ChatError("HTTP 400: maximum context length is 128000 tokens")) and
          not mod._looks_like_context_overflow(mod.ChatError("HTTP 401: invalid api key")))

finally:
    server.shutdown()
print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all compaction checks passed")
