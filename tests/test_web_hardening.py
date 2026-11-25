"""检查 Web 服务的健壮性：畸形请求不能挂起线程，未预期异常要返回 500。

历史问题：
- do_POST 用 rfile.read(Content-Length) 直接阻塞读取，客户端声明一个很大的
  长度却不发送数据时，handler 线程会永久卡住。每个连接一个线程，几个这样的
  请求就能耗尽服务。
- do_GET / do_POST 没有顶层异常兜底，未预期的内部异常会让连接直接断开，
  客户端拿到 reset 而不是可读的错误。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
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


home = tempfile.mkdtemp(prefix="web-hardening-")
log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
# 缩短体读取超时，让测试跑得快
env = dict(
    os.environ,
    HOME=home,
    OPENAI_API_KEY="dummy",
    BENBEN_WEB_BODY_TIMEOUT="1",
)
process = subprocess.Popen(
    [sys.executable, str(AGENT), "--web", "--port", "0", "--host", "127.0.0.1"],
    stdout=log_handle,
    stderr=log_handle,
    env=env,
    cwd=home,
)

try:
    port = token = None
    for _ in range(600):
        log_handle.flush()
        text = open(log_path, encoding="utf-8").read()
        match = re.search(r"http://127\.0\.0\.1:(\d+)/\?token=([\w-]+)", text)
        if match:
            port, token = int(match.group(1)), match.group(2)
            break
        time.sleep(0.05)

    check("web server started", port is not None, open(log_path, encoding="utf-8").read()[:300])

    if port:
        def raw(method, path, body=b"", headers=None, timeout=10):
            sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
            hdrs = {"Host": "127.0.0.1", "X-Token": token}
            if headers:
                hdrs.update(headers)
            if body and "Content-Length" not in hdrs:
                hdrs["Content-Length"] = str(len(body))
            req = f"{method} {path} HTTP/1.1\r\n"
            req += "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
            req += "\r\n"
            try:
                sock.sendall(req.encode() + body)
                # 响应头和正文可能分多个 TCP 段到达，循环读到连接结束或
                # 攒够 Content-Length，避免只 recv 一次拿到空体。
                data = b""
                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    data += chunk
                    head, sep, rest = data.partition(b"\r\n\r\n")
                    if sep:
                        match = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
                        if match and len(rest) >= int(match.group(1)):
                            break
                        if not match:
                            break
            except socket.timeout:
                return None, b""
            finally:
                sock.close()
            if not data:
                return None, b""
            head, _, rest = data.partition(b"\r\n\r\n")
            status = head.split(b"\r\n")[0]
            return status, rest

        # --- Content-Length 撒谎必须被超时打断，而不是挂住线程 ---
        started = time.time()
        status, body = raw(
            "POST", "/api/chat", b"{'a':",
            headers={"Content-Length": "100", "Content-Type": "application/json"},
            timeout=20,
        )
        elapsed = time.time() - started
        check("lying Content-Length answers within timeout",
              status is not None, f"no response after {elapsed:.1f}s")
        if status:
            check("lying Content-Length returns 408",
                  b"408" in status, status)
            check("lying Content-Length explains why",
                  b"timed out" in body.lower(), body[:120])
        # 体读取超时是 1 秒；留出余量以吸收并行测试时的调度抖动
        check("lying Content-Length released the thread quickly",
              elapsed < 12, f"{elapsed:.1f}s")

        # --- 负数 Content-Length 直接拒绝 ---
        status, body = raw(
            "POST", "/api/chat", b"",
            headers={"Content-Length": "-1"},
        )
        check("negative Content-Length rejected", status is not None and b"400" in status, status)

        # --- 非数字 Content-Length ---
        status, body = raw(
            "POST", "/api/chat", b"",
            headers={"Content-Length": "abc"},
        )
        check("non-numeric Content-Length rejected",
              status is not None and b"400" in status, status)

        # --- 正常请求仍然通过（畸形参数应被业务校验拒绝，而不是连接层挂掉） ---
        status, body = raw(
            "POST", "/api/chat", json.dumps({"message": 123}).encode(),
            headers={"Content-Type": "application/json"},
        )
        check("normal request still works",
              status is not None and b"400" in status, f"{status} {body[:120]}")
        check("normal request returns a JSON error body",
              b"error" in body.lower(), body[:120])

        # --- 大量半开连接不应该拖死服务 ---
        idle = []
        for _ in range(15):
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                sock.sendall(
                    f"POST /api/chat HTTP/1.1\r\nHost: x\r\nX-Token: {token}\r\n"
                    f"Content-Length: 500000\r\n\r\n".encode()
                )
                idle.append(sock)
            except OSError:
                pass
        check("half-open sockets established", len(idle) >= 10, len(idle))
        started = time.time()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/health", headers={"X-Token": token}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                healthy = resp.status == 200
        except Exception as exc:  # noqa: BLE001
            healthy = False
            print(f"     health check failed: {exc!r}")
        check("server stays responsive with idle sockets",
              healthy, f"{time.time() - started:.1f}s")
        for sock in idle:
            sock.close()

        # --- 顶层异常兜底：handler 里应该定义了 handle_one_request ---
    source = AGENT.read_text(encoding="utf-8")
    check("handler overrides handle_one_request",
          "def handle_one_request(self)" in source)
    if "def handle_one_request(self)" in source:
        block = source.split("def handle_one_request(self)", 1)[1][:2000]
        check("catch-all handles arbitrary exceptions",
              "except Exception" in block, "")
        check("catch-all returns a 500", "500" in block, "")
        check("catch-all logs the traceback",
              "traceback.print_exc" in block, "")
        check("socket timeout set before dispatching",
              "settimeout(" in block, "")
    check("body reader rejects negative length",
          "if length < 0:" in source)
    check("body reader guards short reads",
          "incomplete request body" in source)

finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()
    log = open(log_path, encoding="utf-8").read()
    check("no unhandled traceback in server log",
          "Traceback" not in log,
          log[log.find("Traceback"):log.find("Traceback") + 500] if "Traceback" in log else "")
    shutil.rmtree(home, ignore_errors=True)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all web hardening checks passed")
