"""检查上下文占用的实时显示：Web 端右上角指示器 + CLI 提示符百分比。

之前上下文百分比只显示在 Web 端侧边栏的 model-info 里，而且只在整轮
done 之后由 /api/runtime 刷新一次，流式过程中看不到上下文在涨。

现在从每次模型调用后的 usage 事件里解析 context=，实时更新输入框右上角
的指示器。CLI 端保持原样（用 /context、/status 查看）。
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


text = AGENT.read_text(encoding="utf-8")

# ---------- 静态检查：接线是否正确 ----------
check("ctx-meter element exists", 'id="ctx-meter"' in text)
check("ctx-meter lives in composer", '<div id="composer">\n      <span id="ctx-meter"' in text)
check(
    "status bar keeps its plain text usage",
    '    <div id="status"></div>' in text,
    "statusEl.textContent 有 90 多处调用，指示器不能放进 #status",
)
css = re.search(r"\.ctx-meter \{([^}]*)\}", text)
check("ctx-meter has css", css is not None)
if css:
    body = css.group(1)
    check("ctx-meter is block-level (own row)",
          "display: block" in body, body[:160])
    check("ctx-meter is right-aligned", "text-align: right" in body, body[:160])
    check("ctx-meter sits above the textarea", "margin: 0 0" in body, body[:160])
check("warn style targets ctx segment only",
      ".ctx-meter.warn .seg-ctx {" in text)
check("danger style targets ctx segment only",
      ".ctx-meter.danger .seg-ctx {" in text)
check("segment colours defined",
      ".ctx-meter .seg-inputs {" in text
      and ".ctx-meter .seg-hit {" in text
      and ".ctx-meter .seg-ctx {" in text)
check("narrow-screen rule hides inputs group",
      "max-width: 900px" in text and ".seg-inputs { display: none; }" in text)
check("no hover tooltip in code",
      'ctxMeterEl.removeAttribute("title")' in text)

delta = re.search(
    r'else if \(event\.type === "usage_live"\) \{(.*?)\n        \}', text, re.S
)
check("usage_live event handled", delta is not None)
if delta:
    body = delta.group(1)
    check("usage_live updates meter", "renderUsageMeter(event.text)" in body, body[:200])
    check("usage_live stays out of the transcript",
          "addLine" not in body, body[:200])

delta_full = re.search(
    r'else if \(event\.type === "usage"\) \{(.*?)\n        \}', text, re.S
)
check("final usage event still logged", delta_full is not None)
if delta_full:
    body = delta_full.group(1)
    check("final usage also feeds meter", "renderUsageMeter" in body, body[:200])
    check("final usage appended to transcript", "addLine" in body, body[:200])

check("usage_live emitted per model call",
      'report("usage_live"' in text)
check("usage_live excludes total",
      "format_usage(include_total=False)" in text)
check("cli ignores usage_live",
      'elif kind == "usage_live":' in text)


check("renderModelInfo feeds the meter",
      "renderContextMeter(context)" in text)

# ---------- 行为检查（node 跑页面里的真实函数） ----------
node = shutil.which("node")
if node is None:
    print("skip web meter runtime (node not found)")
else:
    scripts = re.findall(r"<script>\n(.*?)\n</script>", text, re.S)
    check("single inline script", len(scripts) == 1, len(scripts))
    js = scripts[-1]
    runner = tempfile.mkdtemp(prefix="ctx-meter-")
    runner_path = str(Path(runner) / "run.js")
    Path(runner_path).write_text(
        """
const fs = require("fs");
const vm = require("vm");
const js = fs.readFileSync(process.argv[2], "utf8");

function mkClassList() {
  const s = new Set();
  return {
    add(c) { s.add(c); }, remove(c) { s.delete(c); },
    toggle(c, v) {
      if (v === undefined) { s.has(c) ? s.delete(c) : s.add(c); }
      else { v ? s.add(c) : s.delete(c); }
      return s.has(c);
    },
    contains(c) { return s.has(c); },
    dump() { return [...s].sort(); },
  };
}
function el() {
  const e = {
    id: "", value: "", textContent: "", innerText: "",
    hidden: false, checked: false, disabled: false, open: false,
    scrollTop: 0, scrollHeight: 0, clientHeight: 0,
    children: [], childNodes: [], dataset: {}, style: {},
    addEventListener() {}, removeEventListener() {},
    appendChild(c) { return c; }, append() {}, prepend() {}, remove() {},
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    querySelector: () => el(), querySelectorAll: () => [],
    closest: () => null, contains: () => false,
    focus() {}, blur() {}, click() {},
    insertAdjacentHTML() {}, scrollIntoView() {},
  };
  // innerHTML 赋值要同步出来的纯文本，方便断言用 textContent 读；
  // 同时留一份原始 HTML（buildMeterLabel 会插分段 span）。
  let _html = "";
  Object.defineProperty(e, "innerHTML", {
    get() { return _html; },
    set(v) {
      _html = String(v);
      e.textContent = _html.replace(/<[^>]*>/g, "");
    },
    configurable: true,
  });
  e.classList = mkClassList();
  return e;
}
const meter = el();
const sandbox = {
  console, setTimeout, clearTimeout,
  setInterval: () => 0, clearInterval: () => {},
  fetch: () => new Promise(() => {}),
  location: { search: "?token=t", href: "" },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: {
    getElementById: (id) => (id === "ctx-meter" ? meter : el()),
    querySelector: () => el(), querySelectorAll: () => [],
    createElement: () => el(), createDocumentFragment: () => el(),
    addEventListener: () => {}, body: el(), hidden: false,
  },
  window: { addEventListener() {}, removeEventListener() {},
            matchMedia: () => ({ matches: false, addEventListener() {} }) },
  navigator: { clipboard: {} },
  requestAnimationFrame: (f) => setTimeout(f, 0),
  performance: { now: () => Date.now() },
  AbortController, TextDecoder, URLSearchParams, JSON, Math, Date,
  alert() {}, confirm: () => false, prompt: () => null,
};
const ctx = vm.createContext(sandbox);
vm.runInContext(js, ctx, { filename: "benben-web.js" });

const out = { parsed: [], rendered: [] };
for (const line of [
  "[usage] last: input=1000 cached=800 new=200 hit=80% | context=12345/256000 (4%)",
  "[usage] context=~5000/100000 (5%)",
  "[usage] last: input=100 (no context here)",
]) {
  out.parsed.push(ctx.parseUsageContext(line));
}
// 后端拼好的完整 usage 行（含 total）走 renderUsageMeter
for (const line of [
  "[usage] last: input=232488 cached=230912 new=1576 hit=99% | context=232835/1000000 (23%)",
  "[usage] last: input=1 cached=0 new=1 hit=0% | context=72000/100000 (72%) | total: calls=3 input=9 cached=0 new=9 hit=0% output=5 reasoning=0 tokens=14",
  "[usage] last: input=1 cached=0 new=1 hit=0% | context=~5000/100000 (5%)",
]) {
  meter.classList = mkClassList();
  meter.hidden = false;
  meter.innerHTML = "";
  meter.title = "";
  ctx.renderUsageMeter(line);
  out.live = out.live || [];
  out.live.push({
    text: meter.textContent,
    html: meter.innerHTML,
    classes: meter.classList.dump(),
    title: meter.title,
  });
}
for (const c of [
  { tokens: 12000, window: 100000, percent: 12 },
  { tokens: 70000, window: 100000, percent: 70 },
  { tokens: 95000, window: 100000, percent: 95 },
  { tokens: 5000, window: 100000, percent: 5, estimated: true },
  { tokens: 5000, window: 0 },
]) {
  meter.classList = mkClassList();
  meter.hidden = false;
  meter.innerHTML = "";
  meter.title = "";
  ctx.renderContextMeter(c);
  out.rendered.push({
    text: meter.textContent,
    html: meter.innerHTML,
    hidden: meter.hidden,
    classes: meter.classList.dump(),
    title: meter.title,
  });
}
console.log(JSON.stringify(out));
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, runner_path, str(Path(runner) / "benben-web.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # 上面把 js 当参数传，需要先落盘
    Path(str(Path(runner) / "benben-web.js")).write_text(js, encoding="utf-8")
    result = subprocess.run(
        [node, runner_path, str(Path(runner) / "benben-web.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        check("meter runner executes", False, (result.stdout + result.stderr)[-1200:])
    else:
        out = json.loads(result.stdout)
        parsed = out["parsed"]
        check("parse context line", parsed[0] == {
            "estimated": False, "tokens": 12345, "window": 256000, "percent": 4,
        }, parsed[0])
        check("parse estimated context", parsed[1] == {
            "estimated": True, "tokens": 5000, "window": 100000, "percent": 5,
        }, parsed[1])
        check("parse ignores line without context", parsed[2] is None, parsed[2])

        live = out.get("live") or []
        check("live meter shows hit rate",
              live and "hit:99%" in live[0]["text"], live[:1])
        check("live meter shows context percent",
              live and "ctx:23%" in live[0]["text"], live[:1])
        check("live meter shows input details",
              live and "in:232k" in live[0]["text"]
              and "cached:231k" in live[0]["text"], live[:1])
        check("live meter stays compact",
              live and len(live[0]["text"]) <= 100, live[:1])
        check("live meter drops total",
              live and "total:" not in live[0]["text"], live[:1])
        check("live meter separates segments",
              live and "·" in live[0]["html"], live[:1])
        check("live meter warns at 72%",
              "warn" in live[1]["classes"], live[1:2])
        check("live meter has no tooltip",
              not live[1].get("title"), live[1:2])
        check("live meter marks estimates",
              "estimate" in (live[2]["classes"] if len(live) > 2 else []), live[2:])

        rendered = out["rendered"]
        check("12% plain", "ctx:12%" in rendered[0]["text"]
              and rendered[0]["classes"] == [], rendered[0])
        check("70% warns",
              rendered[1]["classes"] == ["warn"], rendered[1])
        check("95% danger",
              rendered[2]["classes"] == ["danger"], rendered[2])
        check("estimated marked",
              rendered[3]["classes"] == ["estimate"], rendered[3])
        check("no window hides meter",
              rendered[4]["hidden"] is True and rendered[4]["text"] == "", rendered[4])

print()
if failures:
    print("FAILURES: %s" % ", ".join(str(f) for f in failures))
    raise SystemExit(1)
print("all context meter checks passed")
