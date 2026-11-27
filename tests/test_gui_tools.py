"""Screen capture and GUI tools.

The capture path is stubbed so the test runs without a display server; only the
tool plumbing is under test here (schemas, approval gate, the ToolImage result,
and the extra user message run_turn appends after a tool call).
"""

import json
import os
import re
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import importlib.util

spec = importlib.util.spec_from_file_location(
    "agent", os.path.join(ROOT, "benben-agent.py")
)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name} {detail}")
        failures.append(name)


# Hand-built PNG: 1x1 white pixel, enough for the size parser and mime check.
PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\xa75\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)

# ---------- capability detection ----------

real_display = os.environ.get("DISPLAY")
try:
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)
    check("no display means no capture", not agent.gui_capture_available())
    check("no display means no input", not agent.gui_input_available())
    try:
        agent._gui_unavailable_reason(need_input=False)
        reason = ""
    except Exception as exc:  # pragma: no cover - defensive
        reason = str(exc)
    check(
        "missing display explains itself",
        "display" in agent._gui_unavailable_reason(need_input=False).lower(),
        reason,
    )
finally:
    if real_display is not None:
        os.environ["DISPLAY"] = real_display

# ---------- ToolImage result plumbing ----------

base = tempfile.mkdtemp(prefix="gui-tools-")
ws = agent.Workspace(root=base, confirm=False, allow_gui=True)

fake_png = os.path.join(base, "shot.png")
with open(fake_png, "wb") as handle:
    handle.write(PNG_1PX)

original_capture = agent.capture_screen_png
original_capture_available = agent.gui_capture_available
try:
    agent.gui_capture_available = lambda: True
    agent.capture_screen_png = lambda path, timeout=20.0: (
        open(path, "wb").write(PNG_1PX) or None
    )
    result = ws.view_screen(save_path=fake_png, note="unit")
    check("view_screen returns ToolImage", isinstance(result, agent.ToolImage), type(result))
    check("ToolImage carries a data url", result.data_url.startswith("data:image/png;base64,"))
    check("ToolImage text names the file", os.path.basename(fake_png) in result.text, result.text)
    check("ToolImage text reports dimensions", "1x1px" in result.text, result.text)
    check("ToolImage keeps the note", "unit" in result.text, result.text)
finally:
    agent.capture_screen_png = original_capture
    agent.gui_capture_available = original_capture_available

# ---------- schemas are gated on what the machine can do ----------

saved_capture = agent.gui_capture_available
saved_input = agent.gui_input_available
try:
    agent.gui_capture_available = lambda: True
    agent.gui_input_available = lambda: True

    # 默认关闭：即使机器完全支持，没开 --gui-use 也不暴露工具。
    ws_off = agent.Workspace(root=base)
    off_names = [t["function"]["name"] for t in agent.tool_schemas(ws_off)]
    check(
        "gui tools are off by default",
        not (set(off_names) & agent.GUI_TOOL_NAMES),
        off_names,
    )
    check("default_settings turns gui off", agent.default_settings()["gui"] is False)
    try:
        ws_off.dispatch("view_screen", {})
        check("dispatch refuses gui while off", False)
    except agent.ToolError as exc:
        check(
            "dispatch refuses gui while off",
            "disabled" in str(exc) and "--gui-use" in str(exc),
            str(exc),
        )
    try:
        ws_off.dispatch("click", {"x": 1, "y": 1})
        check("dispatch refuses click while off", False)
    except agent.ToolError as exc:
        check("dispatch refuses click while off", "disabled" in str(exc), str(exc))

    agent.gui_capture_available = lambda: False
    agent.gui_input_available = lambda: False
    names = [t["function"]["name"] for t in agent.tool_schemas(ws)]
    check("no gui tools without a screen", not (set(names) & agent.GUI_TOOL_NAMES), names)

    agent.gui_capture_available = lambda: True
    agent.gui_input_available = lambda: True
    names = [t["function"]["name"] for t in agent.tool_schemas(ws)]
    check("all gui tools with a screen", set(agent.GUI_TOOL_NAMES) <= set(names), names)

    ws_readonly = agent.Workspace(root=base, confirm=False, allow_gui=True)
    ws_readonly.read_only = True
    readonly_names = [t["function"]["name"] for t in agent.tool_schemas(ws_readonly)]
    check(
        "read-only run gets capture but no input",
        "view_screen" in readonly_names
        and "list_windows" in readonly_names
        and "click" not in readonly_names,
        readonly_names,
    )

    ws_scheduled = agent.Workspace(root=base, confirm=False, allow_gui=True)
    ws_scheduled.scheduled_run = True
    scheduled_names = [t["function"]["name"] for t in agent.tool_schemas(ws_scheduled)]
    check(
        "scheduled runs never get gui tools",
        not (set(scheduled_names) & agent.GUI_TOOL_NAMES),
        scheduled_names,
    )
    try:
        ws_scheduled.read_only = True
        ws_scheduled.dispatch("view_screen", {})
        check("scheduled view_screen blocked", False)
    except agent.ToolError as exc:
        check(
            "scheduled view_screen blocked",
            "scheduled run" in str(exc),
            str(exc),
        )
finally:
    agent.gui_capture_available = saved_capture
    agent.gui_input_available = saved_input

# ---------- approval gate follows --confirm ----------

approvals = []


def make_ws(confirm, approve_result=True):
    workspace = agent.Workspace(root=base, confirm=confirm, allow_gui=True)
    workspace.approval_handler = lambda action, detail: (
        approvals.append((action, detail)) or approve_result
    )
    return workspace


try:
    agent.gui_input_available = lambda: True
    movements = []
    agent._xdotool = lambda *args, **kwargs: movements.append(args) or ""

    ws_free = make_ws(confirm=False)
    check("click allowed when confirm is off", "click" in ws_free.click(10, 20), )
    check("no approval asked when confirm is off", approvals == [], approvals)
    check("click moves then clicks", movements and movements[0][0] == "mousemove", movements)

    approvals.clear()
    movements.clear()
    ws_ask = make_ws(confirm=True, approve_result=True)
    ws_ask.click(10, 20)
    check("approval asked when confirm is on", [a[0] for a in approvals] == ["click"], approvals)

    approvals.clear()
    ws_deny = make_ws(confirm=True, approve_result=False)
    try:
        ws_deny.click(10, 20)
        check("denied click raises", False)
    except agent.ToolError as exc:
        check("denied click raises", "denied" in str(exc), str(exc))

    # view_screen is read-only, so it never asks even with confirm on.
    approvals.clear()
    agent.capture_screen_png = lambda path, timeout=20.0: open(path, "wb").write(PNG_1PX) or None
    agent.gui_capture_available = lambda: True
    ws_shot = make_ws(confirm=True, approve_result=False)
    shot = ws_shot.view_screen(save_path=os.path.join(base, "shot2.png"))
    check("read-only capture skips approval", isinstance(shot, agent.ToolImage))
    check("read-only capture asked nothing", approvals == [], approvals)
finally:
    agent._xdotool = original_xdotool if "original_xdotool" in globals() else agent._xdotool

# ---------- argument validation ----------

try:
    agent.gui_input_available = lambda: True
    agent._xdotool = lambda *args, **kwargs: ""
    ws_free = make_ws(confirm=False)
    bad_calls = [
        (ws_free.click, {"x": -1, "y": 5}, "non-negative"),
        (ws_free.click, {"x": 1, "y": 5, "button": "wheel"}, "button must be"),
        (ws_free.click, {"x": 1, "y": 5, "count": 0}, "between 1 and 3"),
        (ws_free.press_key, {"keys": "ctrl; rm -rf /"}, "key names"),
        (ws_free.type_text, {"text": ""}, "non-empty"),
        (ws_free.focus_window, {"target": "  "}, "window id"),
    ]
    for func, kwargs, needle in bad_calls:
        try:
            func(**kwargs)
            check(f"rejects {func.__name__} {kwargs}", False)
        except agent.ToolError as exc:
            check(f"rejects {func.__name__} {kwargs}", needle in str(exc), str(exc))
finally:
    agent._xdotool = original_xdotool if "original_xdotool" in globals() else agent._xdotool

# ---------- image dimensions parser ----------

with open(fake_png, "wb") as handle:
    handle.write(PNG_1PX)
check("png size parsed", agent._image_dimensions(fake_png) == (1, 1), agent._image_dimensions(fake_png))
check("missing file has no size", agent._image_dimensions(os.path.join(base, "nope.png")) == (0, 0))

# ---------- run_turn appends the picture as a user message ----------


class Handler(BaseHTTPRequestHandler):
    calls = 0
    payloads = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        Handler.calls += 1
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        Handler.payloads.append(payload)
        if Handler.calls == 1:
            self._send(200, {"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "view_screen", "arguments": "{}",
                }}],
            }}]})
        else:
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": "saw it"}}]})


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

saved_capture = agent.capture_screen_png
saved_available = agent.gui_capture_available
saved_input2 = agent.gui_input_available
original_xdotool = agent._xdotool
try:
    agent.capture_screen_png = lambda path, timeout=20.0: (
        open(path, "wb").write(PNG_1PX) or None
    )
    agent.gui_capture_available = lambda: True
    agent.gui_input_available = lambda: True
    turn_ws = agent.Workspace(root=base, confirm=False, allow_gui=True)
    client = agent.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=5)
    messages = [{"role": "user", "content": "look"}]
    ok = agent.run_turn(client, turn_ws, messages, 3)

    check("turn finished", ok, messages[-1])
    roles = [m.get("role") for m in messages]
    check(
        "tool result then picture",
        roles == ["user", "assistant", "tool", "user", "assistant"],
        roles,
    )
    tool_message = messages[2]
    check("tool message is text only", isinstance(tool_message.get("content"), str), tool_message)
    picture = messages[3]
    check("picture message is flagged", picture.get("_tool_image") is True, picture.keys())
    parts = picture.get("content")
    check("picture message has a content array", isinstance(parts, list), type(parts))
    kinds = [part.get("type") for part in parts if isinstance(part, dict)]
    check("picture message holds an image part", "image_url" in kinds, kinds)

    sent = Handler.payloads[-1]["messages"]
    wire_roles = [m.get("role") for m in sent]
    check(
        "second request carries the picture",
        wire_roles == ["user", "assistant", "tool", "user"],
        wire_roles,
    )
    wire_picture = sent[3]
    wire_parts = wire_picture.get("content")
    url = ""
    if isinstance(wire_parts, list):
        for part in wire_parts:
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url") or ""
    check("picture reaches the wire as a data url", url.startswith("data:image/png;base64,"), url[:40])
    check("no local flag leaks to the wire", "_tool_image" not in wire_picture, wire_picture.keys())

    # 压缩切分不能把合成的 user 消息当成一个用户回合。
    # 这条消息链里有 2 个真实用户回合和 1 条图片消息；keep=2 时应该落到
    # 兜底值 1（第 0 条真实用户消息永远不被当作切点），如果图片消息被计数，
    # 切点会变成 3 - 那样会把整段工具调用切进保留区。
    split_messages = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "captured"},
        {"role": "user", "content": [{"type": "text", "text": "[view_screen]"}], "_tool_image": True},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    check(
        "compact split ignores picture messages",
        agent._compact_split(split_messages, 2) == 1,
        agent._compact_split(split_messages, 2),
    )
    # 反向断言：把标记去掉，切点就必须前移。证明这个标记真的在起作用。
    unmarked = [dict(m) for m in split_messages]
    unmarked[3].pop("_tool_image")
    check(
        "without the flag the picture would count as a turn",
        agent._compact_split(unmarked, 2) == 3,
        agent._compact_split(unmarked, 2),
    )
finally:
    agent.capture_screen_png = saved_capture
    agent.gui_capture_available = saved_available
    agent.gui_input_available = saved_input2
    agent._xdotool = original_xdotool
    server.shutdown()

shutil.rmtree(base, ignore_errors=True)

print()
if failures:
    print("FAILURES:", failures)
    raise SystemExit(1)
print("FAILURES: none")
