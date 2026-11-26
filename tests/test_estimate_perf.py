"""检查 token 估算的性能改造没有改变结果。

两处改动：
1. `_estimate_text_tokens` 用预编译正则数宽字符，替代逐字符 ord() 比较。
   收益 8 倍，因为这个函数在每次模型调用后、以及网页每 5 秒一次的
   /api/runtime 里都会跑到。
2. `context_usage` 只算一次本地估算并传给 `project_context_tokens`，之前
   算了两次，/api/runtime 单次 202ms。

估算值本身必须完全不变——它决定上下文百分比、自动压缩的触发和压缩预算。
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("agent_estimate_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)


def naive_text_tokens(text: str) -> int:
    """改造前的逐字符实现，作为对照基准。"""
    wide = 0
    for char in text:
        code = ord(char)
        if (
            0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7AF
            or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE4F
            or 0xFF00 <= code <= 0xFFEF
        ):
            wide += 1
    return wide + max(1, (len(text) - wide) // 4)


def naive_message_tokens(message: dict) -> int:
    body, images = mod._content_text(message)
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        body += "\n" + str(function.get("arguments") or "")
    return 4 + naive_text_tokens(body) + 800 * images


# ---------- 1. 单段文本的估算必须逐字节等价 ----------
print("== _estimate_text_tokens 与旧实现等价 ==")
samples = [
    "",
    "a",
    "ab",
    "中文",
    "中文测试内容",
    "abc中文def",
    "これは日本語です",
    "한국어테스트",
    "，。！？（）【】",
    "ＡＢＣ全角",
    "abc" * 500,
    "中文" * 500,
    "混合mixed中英文text" * 80,
    "def foo(x):\n    return x + 1  # 中文注释\n" * 40,
    '{"文件": "a.py", "内容": "测试"}' * 50,
    "😀🎉 emoji 测试",
    "\n\n\n",
    "   ",
    "a中" * 333,
]
mismatch = [s for s in samples if mod._estimate_text_tokens(s) != naive_text_tokens(s)]
check("every sample matches the old implementation", not mismatch,
      [s[:20] for s in mismatch])
check("regex counts CJK as one token",
      mod._estimate_text_tokens("中文测试") == naive_text_tokens("中文测试"),
      mod._estimate_text_tokens("中文测试"))
check("regex counts latin as four chars per token",
      mod._estimate_text_tokens("abcdefgh") == 2,
      mod._estimate_text_tokens("abcdefgh"))
check("empty text still counts as one token",
      mod._estimate_text_tokens("") == 1, mod._estimate_text_tokens(""))

# ---------- 2. 消息级估算必须等价 ----------
print("\n== estimate_tokens 与旧实现等价 ==")
messages = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "问题" * 100},
    {"role": "assistant", "content": ""},
    {"role": "tool", "content": "结果" * 100, "tool_call_id": "c1"},
    {"role": "user", "content": [{"type": "text", "text": "多段内容"},
                                 {"type": "image_url", "image_url": {"url": "data:..."}}]},
    {"role": "assistant", "content": "答复", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "run_command", "arguments": '{"command": "ls -la"}'}}]},
]
expected = sum(naive_message_tokens(x) for x in messages)
check("estimate_tokens matches the old sum", mod.estimate_tokens(messages) == expected,
      f"{mod.estimate_tokens(messages)} != {expected}")
check("image counts as 800 tokens",
      mod.estimate_tokens([{"role": "user", "content": [
          {"type": "image_url", "image_url": {"url": "x"}}]}]) == 4 + 1 + 800,
      mod.estimate_tokens([{"role": "user", "content": [
          {"type": "image_url", "image_url": {"url": "x"}}]}]))

# 真实会话
history = [{"role": "system", "content": "x"}]
try:
    mod.load_session("503d4d8d", history)
    if len(history) > 10:
        check("real session matches the old sum",
              mod.estimate_tokens(history) == sum(naive_message_tokens(x) for x in history),
              "mismatch")
        print(f"     (实测 {len(history)} 条消息)")
except Exception:
    pass

# ---------- 3. context_usage 只算一次本地估算 ----------
print("\n== context_usage 不再重复计算 ==")


class FakeClient:
    last_prompt_tokens = 130_000
    last_prompt_estimate = 130_000
    last_usage = {"prompt_tokens": 130_000, "completion_tokens": 500,
                  "total_tokens": 130_500}


info = mod.ModelRegistry().resolve("custom", "test-model", "openai-chat-completions", None)
client = FakeClient()

calls = {"n": 0}
real_estimate = mod.estimate_tokens


def counting_estimate(msgs):
    calls["n"] += 1
    return real_estimate(msgs)


mod.estimate_tokens = counting_estimate
try:
    usage = mod.context_usage(messages, info, client)
finally:
    mod.estimate_tokens = real_estimate
check("context_usage scans the history once", calls["n"] == 1, calls["n"])

# 结果仍与逐条计算一致
local = sum(naive_message_tokens(x) for x in messages)
expected_tokens = max(0, client.last_prompt_tokens + (local - client.last_prompt_estimate))
check("projected tokens match the manual calculation",
      usage["tokens"] == expected_tokens, f"{usage['tokens']} != {expected_tokens}")
check("percent derives from the window",
      usage["percent"] == min(100, int(expected_tokens * 100 / max(1, info.context_window))),
      usage["percent"])

# project_context_tokens 接受外部传入的 local，不再自己算
calls["n"] = 0
mod.estimate_tokens = counting_estimate
try:
    mod.project_context_tokens(client, messages, local)
finally:
    mod.estimate_tokens = real_estimate
check("project_context_tokens reuses a supplied local value", calls["n"] == 0, calls["n"])

# 不传 local 时仍能自己算（向后兼容）
check("project_context_tokens still works without local",
      mod.project_context_tokens(client, messages) == expected_tokens,
      mod.project_context_tokens(client, messages))

# 没有基线时返回 None
class EmptyClient:
    last_prompt_tokens = None
    last_prompt_estimate = None


check("no baseline yields None",
      mod.project_context_tokens(EmptyClient(), messages) is None)

# ---------- 4. 性能不回退 ----------
print("\n== 性能 ==")
bench_history = [{"role": "system", "content": "x"}]
try:
    mod.load_session("503d4d8d", bench_history)
except Exception:
    pass
if len(bench_history) > 100:
    t = time.perf_counter()
    for _ in range(5):
        mod.estimate_tokens(bench_history)
    elapsed = (time.perf_counter() - t) / 5 * 1000
    check(f"estimate_tokens stays under 30ms ({len(bench_history)} msgs)",
          elapsed < 30, f"{elapsed:.1f}ms")
    print(f"     {elapsed:.1f} ms")

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all estimate checks passed")
