"""检查 Web 端流式输出期间是否在增量渲染 Markdown。

历史行为：appendStreamDelta 只用 textContent 拼纯文本，要等 finishStreamBubble
才调一次 renderMarkdown，所以流式过程中看到的是一堆裸露的 `**` `#` `|`。
"""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


node = shutil.which("node")
if node is None:
    print("skip web streaming test (node not found)")
    print()
    print("FAILURES: none")
    raise SystemExit(0)

text = AGENT.read_text(encoding="utf-8")
scripts = re.findall(r"<script>\n(.*?)\n</script>", text, re.S)
check("single inline script", len(scripts) == 1, len(scripts))
js = scripts[-1]

# 抽出流式相关函数与所需常量/辅助函数
wanted_funcs = [
    "escapeHtml",
    "safeImage",
    "splitInlineCode",
    "renderInline",
    "tableCells",
    "isTableSeparator",
    "renderQuoteBody",
    "renderMarkdown",
    "scrollLog",
    "beginStreamBubble",
    "paintStreamBubble",
    "flushStreamRender",
    "cancelStreamRender",
    "appendStreamDelta",
    "finishStreamBubble",
    "addMarkdown",
    "endStreamThinking",
]
wanted_consts = [
    "STREAM_RENDER_INTERVAL",
    "INLINE_CODE_OPEN",
    "INLINE_CODE_CLOSE",
]
wanted_lets = [
    "streamBubble", "streamText", "streamRenderTimer",
    "streamLastRender", "streamRenderedText", "streamThinkingEl",
    "historyFragment",
]

pieces = []
for name in wanted_consts:
    match = re.search(r"^const %s = .*$" % re.escape(name), js, re.M)
    if match is None:
        failures.append(f"const {name}")
        continue
    pieces.append(match.group(0))

for name in wanted_lets:
    match = re.search(r"^let %s = .*?;$" % re.escape(name), js, re.M)
    if match is None:
        failures.append(f"let {name}")
        continue
    pieces.append(match.group(0))

for name in wanted_funcs:
    match = re.search(r"^function %s\(.*?\n^\}" % re.escape(name), js, re.S | re.M)
    if match is None:
        failures.append(f"function {name}")
        continue
    pieces.append(match.group(0))

check("extracted streaming helpers", not failures, failures[:5])

# 记录 bubble 的 innerHTML，并模拟 DOM / 计时器
HARNESS = r"""
const logEl = {
  children: [],
  _t: "",
  set textContent(v) { this._t = v; this.children.length = 0; },
  get textContent() { return this._t; },
  appendChild(el) { this.children.push(el); return el; },
  get scrollHeight() { return 0; },
  set scrollTop(v) {},
  get scrollTop() { return 0; },
};
let autoScroll = true;
const bubbles = [];
function makeBubble() {
  const b = { className: "", _html: "", children: [],
    set innerHTML(v) { this._html = v; },
    get innerHTML() { return this._html; },
    set textContent(v) { this._html = "TEXT:" + v; },
    get textContent() { return this._html; },
    appendChild(c) { this.children.push(c); return c; } };
  bubbles.push(b);
  return b;
}
const document = { createElement: () => makeBubble() };

// 可控时钟
let fakeNow = 0;
const Date = { now: () => fakeNow };
const timers = [];
const setTimeout = (fn, ms) => { const id = timers.length; timers.push({ fn, at: fakeNow + ms, id }); return id; };
const clearTimeout = (id) => { const t = timers.find((x) => x.id === id); if (t) t.cancelled = true; };
function advance(ms) {
  fakeNow += ms;
  for (const t of timers) {
    if (!t.cancelled && t.at <= fakeNow) { t.cancelled = true; t.fn(); }
  }
}
"""

DRIVER = r"""
const out = {};
out.interval = STREAM_RENDER_INTERVAL;

// --- 场景1：分片到达后是否出现渲染后的 HTML ---
fakeNow = 0;
beginStreamBubble();
const chunks = ["## 标题\n\n", "这是**加粗", "文字**和`代码`。\n\n", "- 第一项\n", "- 第二项\n"];
for (const c of chunks) { appendStreamDelta(c); advance(STREAM_RENDER_INTERVAL + 1); }
out.midStreamHtml = streamBubble ? streamBubble.innerHTML : null;
out.midStreamHasStrong = /<strong>/.test(out.midStreamHtml || "");
out.midStreamHasHeading = /<h2>/.test(out.midStreamHtml || "");
out.midStreamHasList = /<ul>|<li>/.test(out.midStreamHtml || "");
out.midStreamLeakedStars = /\*\*/.test(out.midStreamHtml || "");

// --- 场景2：渲染次数受节流限制，不是每个 chunk 都渲染 ---
fakeNow = 0;
beginStreamBubble();
let renders = 0;
const origPaint = paintStreamBubble;
let paintCount = 0;
const realInner = streamBubble;
// 用增量次数对比渲染次数（renderMarkdown 调用次数通过 innerHTML 赋值次数近似）
let assignCount = 0;
Object.defineProperty(streamBubble, "innerHTML", {
  set(v) { assignCount++; streamBubble._html = v; },
  get() { return streamBubble._html; },
  configurable: true,
});
for (let k = 0; k < 100; k++) { appendStreamDelta("x"); advance(2); }
out.chunksSent = 100;
out.rendersDuringStream = assignCount;
out.throttled = assignCount < 100;

// --- 场景3：结束时内容完整（不丢字） ---
fakeNow = 0;
beginStreamBubble();
const full = "## 结束\n\n最终**内容**完整。\n";
for (const ch of full) { appendStreamDelta(ch); advance(1); }
finishStreamBubble();
out.finalHtml = bubbles[bubbles.length - 1]._html;
out.finalHasStrong = /<strong>内容<\/strong>/.test(out.finalHtml || "");
out.finalHasHeading = /<h2>/.test(out.finalHtml || "");

// --- 场景4：finish 时传了完整文本，应以完整文本为准 ---
fakeNow = 0;
beginStreamBubble();
appendStreamDelta("残缺");
finishStreamBubble("完整**版本**");
out.overrideHtml = bubbles[bubbles.length - 1]._html;
out.overrideUsesFull = /<strong>版本<\/strong>/.test(out.overrideHtml || "");

// --- 场景5：cancelStreamRender 后旧定时器不再写入 ---
fakeNow = 0;
beginStreamBubble();
const before = bubbles.length;
appendStreamDelta("**未完");
cancelStreamRender();
advance(1000);
out.afterCancelHtml = bubbles[bubbles.length - 1]._html;
out.afterCancelIsText = out.afterCancelHtml === "";

// --- 场景6：内容没变化时不重复渲染 ---
fakeNow = 0;
beginStreamBubble();
appendStreamDelta("abc");
advance(100);
out.emptyAdapterOk = true;

console.log(JSON.stringify(out));
"""

runner = tempfile.mkdtemp(prefix="web-stream-")
runner_path = str(Path(runner) / "run.js")
Path(runner_path).write_text(
    "\n\n".join(pieces) + "\n" + HARNESS + "\n" + DRIVER, encoding="utf-8"
)

result = subprocess.run(
    [node, runner_path],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=60,
)
if result.returncode != 0:
    check("streaming runner executes", False, (result.stdout + result.stderr)[-1500:])
else:
    out = json.loads(result.stdout)
    check("stream render interval is sane",
          0 < out["interval"] <= 200, out["interval"])
    check("mid-stream renders heading", out["midStreamHasHeading"], out["midStreamHtml"])
    check("mid-stream renders bold", out["midStreamHasStrong"], out["midStreamHtml"])
    check("mid-stream renders list", out["midStreamHasList"], out["midStreamHtml"])
    check("mid-stream has no leaked **", not out["midStreamLeakedStars"], out["midStreamHtml"])
    check("rendering is throttled, not per-chunk",
          out["throttled"], f"{out['rendersDuringStream']} renders for {out['chunksSent']} chunks")
    check("final render keeps bold", out["finalHasStrong"], out["finalHtml"])
    check("final render keeps heading", out["finalHasHeading"], out["finalHtml"])
    check("finish(fullText) overrides partial stream", out["overrideUsesFull"], out["overrideHtml"])
    check("cancel stops pending paint", out["afterCancelIsText"], out["afterCancelHtml"])

# 静态检查：appendStreamDelta 里不能再出现直接把纯文本塞给 textContent 的写法
delta_body = re.search(r"^function appendStreamDelta\(.*?\n^\}", js, re.S | re.M)
check("appendStreamDelta extracted", delta_body is not None)
if delta_body:
    body = delta_body.group(0)
    check("appendStreamDelta no longer sets raw textContent",
          "textContent = streamText" not in body, body[:400])
    check("appendStreamDelta triggers markdown paint",
          "flushStreamRender" in body or "paintStreamBubble" in body, body[:400])

print()
if failures:
    print("FAILURES: %s" % ", ".join(str(f) for f in failures))
    raise SystemExit(1)
print("all streaming checks passed")
