"""检查后台任务和 HTTP 请求这两个工具。

后台任务不是 v0.31.x 删掉的那套（follow 模式 + 实时推送 + Web 任务卡片，
1600 多行）。模型只需要三个原语：起、读尾部、停，所以这里只验证这三件事
以及防止泄漏的两个硬约束（数量上限、自动超时）。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
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


spec = importlib.util.spec_from_file_location("agent_jobs_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)

ws = mod.Workspace(tempfile.mkdtemp(prefix="benben-jobs-"))

try:
    print("== 工具注册 ==")
    names = [t["function"]["name"] for t in mod.TOOL_SCHEMAS]
    for tool in ("job_output", "job_cancel", "job_list", "http_request"):
        check(f"{tool} is exposed", tool in names, names)
    bg = next(
        (t for t in mod.TOOL_SCHEMAS if t["function"]["name"] == "run_command"), None
    )
    check("run_command accepts background",
          bg is not None and "background" in bg["function"]["parameters"]["properties"])

    print("\n== 后台任务：起、读、停 ==")
    started = ws.run_command(
        "for i in 1 2 3; do echo line$i; sleep 0.2; done",
        timeout_seconds=5,
        background=True,
    )
    check("start returns a job id", "started job-" in started, started[:80])
    job_id = started.split()[1]
    check("job_list shows it running", "running" in ws.job_list(), ws.job_list())
    time.sleep(1.2)
    out = ws.job_output(id=job_id)
    check("job_output has the output", "line1" in out and "line3" in out, out[:160])
    check("job_output reports status", "exited" in out, out[:120])
    time.sleep(0.3)
    constrained = ws.run_command("sleep 30", timeout_seconds=5, background=True)
    slow_id = constrained.split()[1]
    cancelled = ws.job_cancel(id=slow_id)
    check("job_cancel kills it", "killed" in cancelled, cancelled)

    print("\n== tail 截断 ==")
    tail_job = ws.run_command(
        "seq 1 500", timeout_seconds=5, background=True
    ).split()[1]
    time.sleep(0.8)
    short = ws.job_output(id=tail_job, tail_chars=100)
    check("tail_chars limits output", len(short) < 400, len(short))
    check("tail keeps the end", "500" in short, short[-80:])

    print("\n== 防泄漏：数量上限 ==")
    held = []
    for _ in range(mod.MAX_BACKGROUND_JOBS):
        result = ws.run_command("sleep 60", timeout_seconds=5, background=True)
        held.append(result.split()[1])
    try:
        ws.run_command("sleep 60", timeout_seconds=5, background=True)
        check("exceeding the job limit is refused", False, "no error raised")
    except mod.ToolError as exc:
        check("exceeding the job limit is refused",
              "already running" in str(exc), str(exc)[:120])
    ws.background_jobs.stop_all()
    time.sleep(0.3)
    statuses = ws.job_list()
    check("stop_all kills everything",
          "running" not in statuses, statuses[:200])
    check("stop_all records the exit status",
          "killed" in statuses, statuses[:200])

    print("\n== 防泄漏：自动超时 ==")
    old_timeout = mod.BACKGROUND_JOB_TIMEOUT
    mod.BACKGROUND_JOB_TIMEOUT = 0.5
    try:
        timeout_ws = mod.Workspace(tempfile.mkdtemp(prefix="benben-jobs-to-"))
        tid = timeout_ws.run_command(
            "sleep 60", timeout_seconds=5, background=True
        ).split()[1]
        time.sleep(1.2)
        state = timeout_ws.job_output(id=tid)
        check("jobs are killed after the timeout", "timed out" in state, state[:120])
        timeout_ws.background_jobs.stop_all()
    finally:
        mod.BACKGROUND_JOB_TIMEOUT = old_timeout

    print("\n== 未知 id ==")
    try:
        ws.job_output(id="job-9999")
        check("unknown job id is rejected", False)
    except mod.ToolError as exc:
        check("unknown job id is rejected", "unknown job" in str(exc), str(exc)[:80])

    print("\n== HTTP 请求 ==")
    requests_seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.end_headers()
                return
            if self.path == "/missing":
                payload = b'{"error":"not found"}'
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", "replace")
            requests_seen["body"] = body
            requests_seen["ctype"] = self.headers.get("Content-Type")
            payload = json.dumps({"echo": body}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        got = ws.http_request(url=base + "/thing")
        check("GET returns the status", "HTTP 200" in got, got[:100])
        check("GET returns the body", '{"ok":true}' in got, got[:200])

        posted = ws.http_request(url=base + "/post", method="POST", body='{"a":1}')
        check("POST returns the status", "HTTP 201" in posted, posted[:100])
        check("POST sends the body", requests_seen.get("body") == '{"a":1}',
              requests_seen.get("body"))
        check("POST defaults to a JSON content type",
              "application/json" in (requests_seen.get("ctype") or ""),
              requests_seen.get("ctype"))

        missing = ws.http_request(url=base + "/missing")
        check("a 4xx is returned instead of raised",
              "HTTP 404" in missing and "not found" in missing, missing[:160])

        followed = ws.http_request(url=base + "/redirect")
        check("redirects are followed by default",
              "HTTP 200" in followed, followed[:120])
        not_followed = ws.http_request(url=base + "/redirect", follow_redirects=False)
        check("redirects can be disabled",
              "HTTP 302" in not_followed and "Location" in not_followed,
              not_followed[:160])

        for bad in ("file:///etc/passwd", "not-a-url", ""):
            try:
                ws.http_request(url=bad)
                check(f"rejects {bad!r}", False)
            except mod.ToolError:
                check(f"rejects {bad!r}", True)
    finally:
        server.shutdown()

    print("\n== Cookie 处理 ==")
    cookie_seen = {}

    class CookieHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/login":
                self.send_response(302)
                self.send_header("Set-Cookie", "sid=secret123; Path=/")
                self.send_header("Set-Cookie", "theme=dark; Path=/")
                self.send_header("Location", "/home")
                self.end_headers()
                return
            if self.path == "/direct":
                payload = b"ok"
                self.send_response(200)
                self.send_header("Set-Cookie", "sid=secret123; Path=/")
                self.send_header("Set-Cookie", "theme=dark; Path=/")
                self.send_header("WWW-Authenticate", 'Basic realm="x"')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            cookie_seen["value"] = self.headers.get("Cookie")
            payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    cookie_server = HTTPServer(("127.0.0.1", 0), CookieHandler)
    threading.Thread(target=cookie_server.serve_forever, daemon=True).start()
    cookie_base = f"http://127.0.0.1:{cookie_server.server_port}"
    try:
        ws.http_request(url=cookie_base + "/login")
        check("cookies are sent through a redirect chain",
              cookie_seen.get("value") == "sid=secret123; theme=dark",
              cookie_seen.get("value"))

        direct = ws.http_request(url=cookie_base + "/direct")
        check("repeated Set-Cookie headers are both kept",
              direct.lower().count("set-cookie") == 2, direct[:300])
        check("cookie values are redacted",
              "secret123" not in direct and "dark" not in direct, direct[:300])
        check("cookie names are still shown",
              "sid=" in direct and "theme=" in direct, direct[:300])
        check("WWW-Authenticate is surfaced", "WWW-Authenticate" in direct, direct[:200])
    finally:
        cookie_server.shutdown()

finally:
    ws.close()
    shutil.rmtree(ws.root, ignore_errors=True)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all background job and http checks passed")
