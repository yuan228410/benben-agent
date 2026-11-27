"""前后端 usage 协议一致性。

起因是一个真实缺陷：模型输出期间、多轮工具调用时，输入框右上角的上下文
指示器一直不动，直到整轮结束由 /api/runtime 刷新才突然跳变。

原因是两边各写各的字段名——后端 format_usage 输出 ``in:``/``ctx:``，
前端的正则却还在找 ``input=``/``context=``（旧格式）。四个 match 全部返回
null，``renderUsageMeter`` 在拿到空 label 后直接 return，状态条整个回合
一次都没被更新过。测试也没发现，因为测试样本用的是同一套过时格式，前端
和测试一起错，断言一直是绿的。

这里不写死样本，而是：
  1. 真的跑一次 run_turn（两次模型调用，中间夹一次工具调用），
     抓到后端实际发出的 usage_live 文本和结构化 data；
  2. 把页面的真实 JS 函数抽出来，喂这两份东西进去；
  3. 断言前端能拿到数字。

以后任何一边改字段名，这里都会立刻红，而不是等用户发现界面不动。
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("agent_proto_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)


# ---------- 1. 跑一次真实回合，抓到后端原始输出 ----------
class Handler(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        json.loads(self.rfile.read(length))
        Handler.calls += 1
        if Handler.calls == 1:
            message = {
                "role": "assistant", "content": "",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "done"}
            finish = "stop"
        payload = json.dumps({
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": 1000 + Handler.calls * 500,
                "completion_tokens": 50,
                "total_tokens": 1050 + Handler.calls * 500,
                "prompt_tokens_details": {"cached_tokens": 900},
            },
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = mod.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=10)
events: list[dict] = []
turn_messages = [{"role": "user", "content": "go"}]
try:
    mod.run_turn(
        client,
        mod.Workspace("/tmp"),
        turn_messages,
        5,
        emit=lambda k, t: events.append({"type": k, "text": t}),
        emit_data=lambda k, t, d: events.append({"type": k, "text": t, "data": d}),
        tools=[{"type": "function", "function": {
            "name": "list_dir", "description": "d",
            "parameters": {"type": "object", "properties": {}}}}],
        context_window=1_000_000,
    )
finally:
    server.shutdown()

live = [e for e in events if e["type"] == "usage_live"]
print("== 后端事件 ==")
check("one usage_live per model call", len(live) == Handler.calls == 2, len(live))
check("every usage_live carries data", all("data" in e for e in live))
check("usage_live text is non-empty", all(e["text"] for e in live))

snap = live[-1]["data"] if live else {}
for field in ("input", "cached", "new", "hit", "tokens", "window", "percent"):
    check(f"snapshot has {field}", field in snap, snap)
check("snapshot percent is a number",
      isinstance(snap.get("percent"), int), snap.get("percent"))
check("snapshot tokens grow between calls",
      len(live) == 2 and live[0]["data"]["tokens"] < live[1]["data"]["tokens"],
      [e["data"].get("tokens") for e in live])


# ---------- 2. 把真实输出喂给页面的真实函数 ----------
node = shutil.which("node")
if node is None:
    print("skip frontend protocol check (node not found)")
    print()
    if failures:
        print("FAILURES: %s" % ", ".join(failures))
        raise SystemExit(1)
    print("all usage protocol checks passed")
    raise SystemExit(0)

source = AGENT.read_text(encoding="utf-8")
scripts = re.findall(r"<script>\n(.*?)\n</script>", source, re.S)
print("\n== 前端解析真实输出 ==")
check("page has one inline script", len(scripts) == 1, len(scripts))
js = scripts[-1]

runner_js = r"""
const vm = require("vm");
const fs = require("fs");
const js = fs.readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

function mkClassList() {
  const s = new Set();
  return {
    add(c) { s.add(c); }, remove(c) { s.delete(c); },
    toggle(c, v) {
      if (v === undefined) { s.has(c) ? s.delete(c) : s.add(c); }
      else { v ? s.add(c) : s.delete(c); }
    },
    contains(c) { return s.has(c); },
    dump() { return Array.from(s).sort(); },
  };
}
const stubEl = () => {
  const el = {
    hidden: false, innerHTML: "", textContent: "", value: "", title: "",
    disabled: false, checked: false, scrollTop: 0, scrollHeight: 0,
    dataset: {}, style: {}, classList: mkClassList(),
    addEventListener() {}, removeEventListener() {}, appendChild() {},
    removeChild() {}, remove() {}, focus() {}, blur() {}, click() {},
    setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
    querySelector: () => null, querySelectorAll: () => [],
    insertBefore() {}, closest: () => null, contains: () => false,
    children: [], childNodes: [],
  };
  return el;
};
const meter = stubEl();
const sandbox = {
  location: { search: "", pathname: "/", hash: "" },
  document: { getElementById: () => meter, querySelector: () => null,
              querySelectorAll: () => [], addEventListener() {},
              createElement: () => stubEl(),
              createTextNode: (t) => ({ textContent: t }),
              body: { appendChild() {}, classList: mkClassList() } },
  window: { addEventListener() {}, location: { search: "", pathname: "/" } },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { clipboard: {} },
  requestAnimationFrame: (f) => setTimeout(f, 0),
  setInterval: () => 0,
  clearInterval() {},
  setTimeout: (f) => setTimeout(f, 0),
  clearTimeout() {},
  performance: { now: () => Date.now() },
  AbortController, TextDecoder, URLSearchParams, JSON, Math, Date,
  alert() {}, confirm: () => false, prompt: () => null,
};
const ctx = vm.createContext(sandbox);
vm.runInContext(js, ctx, { filename: "benben-web.js" });

const plain = (html) => html.replace(/<[^>]+>/g, "");
const out = { fromText: [], fromData: [], parsed: [] };
for (const text of payload.texts) {
  meter.classList = mkClassList();
  meter.innerHTML = ""; meter.textContent = "";
  ctx.renderUsageMeter(text);
  out.fromText.push({
    html: meter.innerHTML,
    text: plain(meter.innerHTML),
    classes: meter.classList.dump(),
  });
  out.parsed.push(ctx.parseUsageContext(text));
}
for (const data of payload.snapshots) {
  meter.classList = mkClassList();
  meter.innerHTML = ""; meter.textContent = "";
  ctx.renderContextMeter(data);
  out.fromData.push({
    html: meter.innerHTML,
    text: plain(meter.innerHTML),
    classes: meter.classList.dump(),
  });
}
process.stdout.write(JSON.stringify(out));
"""

result = shutil.which("node")
import subprocess
import tempfile

with tempfile.TemporaryDirectory(prefix="usage-proto-") as tmp:
    runner_path = Path(tmp) / "run.js"
    runner_path.write_text(runner_js, encoding="utf-8")
    # 页面脚本接近 1MB，走命令行参数会超出 ARG_MAX，改走文件。
    js_path = Path(tmp) / "benben-web.js"
    js_path.write_text(js, encoding="utf-8")
    payload_path = Path(tmp) / "payload.json"
    payload_path.write_text(json.dumps({
        "texts": [e["text"] for e in live],
        "snapshots": [e["data"] for e in live],
    }), encoding="utf-8")
    proc = subprocess.run(
        [result, str(runner_path), str(js_path), str(payload_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
if proc.returncode != 0:
    check("frontend runner executes", False, (proc.stdout + proc.stderr)[-800:])
else:
    out = json.loads(proc.stdout)

    # 主路径：结构化 data
    print("\n-- 结构化 data（新协议）--")

    def human(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1000:
            return f"{round(n / 1000)}k"
        return str(n)

    for idx, rendered in enumerate(out["fromData"]):
        check(f"data[{idx}] renders a non-empty meter",
              bool(rendered["text"]), rendered)
        check(f"data[{idx}] shows context percent",
              "ctx:%d%%" % live[idx]["data"]["percent"] in rendered["text"],
              rendered["text"])
        check(f"data[{idx}] shows the input size",
              "in:" + human(live[idx]["data"]["input"]) in rendered["text"],
              rendered["text"])
        check(f"data[{idx}] is not marked estimated",
              "estimate" not in rendered["classes"], rendered["classes"])

    # 兜底路径：只有文本
    print("\n-- 文本兜底（旧服务端）--")
    for idx, rendered in enumerate(out["fromText"]):
        check(f"text[{idx}] renders a non-empty meter",
              bool(rendered["text"]), rendered)
        check(f"text[{idx}] exposes a context percent",
              out["parsed"][idx] is not None,
              live[idx]["text"])
        check(f"text[{idx}] percent matches the snapshot",
              out["parsed"][idx]
              and out["parsed"][idx]["percent"] == live[idx]["data"]["percent"],
              (out["parsed"][idx], live[idx]["data"]["percent"]))

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all usage protocol checks passed")
