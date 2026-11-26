"""检查对话回合不持有全局会话锁。

历史问题：`_handle_chat_inner` 的 `with lock` 覆盖了 149 行，把整个
run_turn 和每一次 SSE 写入都锁在里面。一个回合可能持续几分钟，这期间
/api/open、/api/runtime（前端每 5 秒轮询）、/api/compact 全部阻塞；
_sse 往半开连接写入时还会阻塞到 socket 超时（30 秒），整个 Web 服务挂起。

现在锁只包住状态准备（追加用户消息、跑自动压缩、取快照）和收尾，
run_turn 与事件推送在锁外执行。并发安全靠 chat_state["active"]：同一时刻
只有一个回合，准备完成后 messages 不会被别的请求替换。
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


source = AGENT.read_text(encoding="utf-8")
tree = ast.parse(source)


def find_func(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


print("== 静态检查：锁的范围 ==")

inner = find_func("_handle_chat_inner")
check("_handle_chat_inner exists", inner is not None)

lock_blocks = []
run_turn_line = None
sse_lines = []
if inner is not None:
    for node in ast.walk(inner):
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.context_expr, ast.Name) and item.context_expr.id == "lock":
                    lock_blocks.append((node.lineno, node.end_lineno))
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "run_turn"):
            run_turn_line = node.lineno
        if (isinstance(node, ast.Attribute)
                and node.attr == "_sse"):
            sse_lines.append(node.lineno)

check("run_turn is called from _handle_chat_inner", run_turn_line is not None,
      run_turn_line)
if run_turn_line and lock_blocks:
    inside = [b for b in lock_blocks if b[0] <= run_turn_line <= b[1]]
    check("run_turn runs outside the session lock", not inside,
          f"line {run_turn_line} inside {inside}")

if sse_lines and lock_blocks:
    inside_sse = [ln for ln in sse_lines
                  if any(b[0] <= ln <= b[1] for b in lock_blocks)]
    check("SSE writes happen outside the session lock", not inside_sse,
          inside_sse)

if lock_blocks:
    spans = [b - a + 1 for a, b in lock_blocks]
    check("session lock block is narrow", max(spans) < 90,
          f"largest lock block spans {max(spans)} lines")

# 收尾阶段的 last_activity 仍在锁内（它是共享状态）
if inner is not None:
    seg = source.split("\n")
    lock_spans = lock_blocks
    found = False
    for a, b in lock_spans:
        block = "\n".join(seg[a - 1:b])
        if 'state["last_activity"]' in block:
            found = True
    check("last_activity update stays under the lock", found)

print("\n== 静态检查：替换 state 的端点要有忙保护 ==")
for path, fn_name in [
    ("/api/new", "start a new session"),
    ("/api/open", "open another session"),
    ("/api/delete", "delete a session"),
]:
    idx = source.find(f'parsed.path == "{path}"')
    check(f"{path} route exists", idx >= 0, idx)
    if idx >= 0:
        window = source[idx:idx + 400]
        check(f"{path} refuses while a turn runs",
              "require_idle(" in window, window[:160])

check("require_idle helper exists", "def require_idle(" in source)

print("\n== 运行时检查：对话进行中其他请求不被阻塞 ==")

home = tempfile.mkdtemp(prefix="benben-lock-")
log_path = Path(home) / "server.log"
log_handle = open(log_path, "w")
env = dict(**__import__("os").environ)
env["HOME"] = home
env["BENBEN_AGENT_HOME"] = str(Path(home) / ".benben-agent")


def _free_port() -> int:
    """Pick a port nothing is listening on.

    A fixed port made this test flaky: when another test in the suite held it,
    the server failed to bind and every runtime check below failed for a reason
    that had nothing to do with the lock.
    """
    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = _free_port()

process = subprocess.Popen(
    [sys.executable, str(AGENT), "--web", "--port", str(PORT),
     "--no-shell", "--plain"],
    stdout=log_handle, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env)

token = None
for _ in range(80):
    time.sleep(0.5)
    if log_path.exists():
        text = log_path.read_text(errors="replace")
        m = re.search(r"[?&]token=(\S+)", text)
        if m:
            token = m.group(1)
            break

BASE = f"http://127.0.0.1:{PORT}"


def call(path, body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"X-Token": token, "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return round((time.monotonic() - started) * 1000, 1), resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return round((time.monotonic() - started) * 1000, 1), exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        return -1, 0, repr(exc).encode()


try:
    check("server started with a token", bool(token),
          (log_path.read_text(errors="replace")[-400:]
           if log_path.exists() else "no log"))

    if token:
        print("      (busy 窗口由真实模型调用决定，可能极短；这里只验证")
        print("       端点可达且不发生锁死，不依赖抓到 busy 的瞬间)")
        # 先确认空闲时各端点都正常
        for path in ("/api/runtime", "/api/state", "/api/settings"):
            ms, status, _ = call(path)
            check(f"{path} responds when idle",
                  status == 200 and 0 <= ms < 3000, f"{ms} ms status={status}")

        # 打开一个不存在的会话：既验证路由可达，也验证 404 分支不会挂住
        ms, status, _ = call("/api/open", body={"session": "deadbeef"})
        check("/api/open answers (does not hang)", status in (404, 409),
              f"{status} in {ms} ms")

        # 发一个必然失败的回合（没有可用模型凭据），确认 worker 结束后
        # 仍能正常响应 —— 也就是 finally 里一定把 active 清掉了。
        started = time.monotonic()
        try:
            call("/api/chat/stream", body={"message": "hello"}, timeout=20)
        except Exception:  # noqa: BLE001
            pass
        # 等 worker 收尾
        for _ in range(60):
            time.sleep(0.2)
            _, _, payload = call("/api/runtime")
            if payload and b'"busy":false' in payload.replace(b" ", b""):
                break
        _, _, payload = call("/api/runtime")
        busy_after = b'"busy":true' in (payload or b"").replace(b" ", b"")
        check("worker clears busy after a failed turn", not busy_after,
              payload[:120] if payload else "no response")
        check("turn did not hang the server",
              time.monotonic() - started < 25,
              f"{time.monotonic() - started:.1f}s")

    log_handle.flush()
    log = log_path.read_text(errors="replace")
    check("no unhandled traceback in server log", "Traceback" not in log,
          log[log.find("Traceback"):][:400] if "Traceback" in log else "")
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()
    shutil.rmtree(home, ignore_errors=True)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all lock scope checks passed")
