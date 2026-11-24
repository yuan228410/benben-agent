"""Web 端压缩设置：/api/compact/config 的读写与生效。"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


# --- 纯函数：apply_compression_overrides ---------------------------------- #

check(
    "no section -> None",
    agent.apply_compression_overrides(None, {"enabled": True, "limit": None}) is None,
)

current = {"enabled": True, "limit": None}
check(
    "merges enabled",
    agent.apply_compression_overrides({"enabled": False}, current)
    == {"enabled": False, "limit": None},
)
check(
    "merges limit",
    agent.apply_compression_overrides({"limit": 5000}, current)
    == {"enabled": True, "limit": 5000},
)
check(
    "limit null clears",
    agent.apply_compression_overrides(
        {"limit": None}, {"enabled": True, "limit": 5000}
    )["limit"]
    is None,
)
check(
    "empty limit clears",
    agent.apply_compression_overrides(
        {"limit": ""}, {"enabled": True, "limit": 5000}
    )["limit"]
    is None,
)

for bad, label in (
    ({"enabled": "yes"}, "enabled not bool"),
    ({"limit": "1000"}, "limit not int"),
    ({"limit": True}, "limit is bool"),
    ({"limit": 10}, "limit too small"),
    ({"limit": 10**9}, "limit too large"),
    ("nope", "not an object"),
):
    try:
        agent.apply_compression_overrides(bad, current)
        check(f"rejects {label}", False, "no ValueError")
    except ValueError:
        check(f"rejects {label}", True)


# --- 端到端：真实启动 --web ------------------------------------------------ #

home = tempfile.mkdtemp(prefix="web-compress-")
workspace = os.path.join(home, "workspace")
os.makedirs(workspace)
config_path = os.path.join(home, "config.json")
with open(config_path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "version": 2,
            "active": "test",
            "connections": {
                "test": {
                    "provider": "custom",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_format": "openai-chat-completions",
                    "model": "m1",
                    "api_key": "k",
                }
            },
            "mcpServers": {},
            "defaults": {"confirm": False},
        },
        handle,
    )

log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=config_path)
process = subprocess.Popen(
    [
        sys.executable,
        AGENT,
        "--web",
        "--port",
        "0",
        "--config",
        config_path,
        "--cwd",
        workspace,
        "-m",
        "m1",
    ],
    stdout=log_handle,
    stderr=log_handle,
    env=env,
)

url = None
try:
    for _ in range(200):
        log_handle.flush()
        with open(log_path, encoding="utf-8") as reader:
            text = reader.read()
        match = re.search(r"http://\S+\?token=[\w-]+", text)
        if match:
            url = match.group(0)
            break
        time.sleep(0.1)

    check("web server started", url is not None, text[-300:] if not url else "")

    if url:
        base, token = url.split("?token=", 1)

        def call(method, path, payload=None):
            data = None
            headers = {"X-Token": token}
            if payload is not None:
                data = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                base + path, data=data, headers=headers, method=method
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode() or "{}")

        got = call("GET", "/api/compact/config")
        check("GET returns compress", "compress" in got, list(got))
        compress = got.get("compress") or {}
        check("GET enabled defaults true", compress.get("enabled") is True, compress)
        check("GET limit is null", compress.get("limit") is None, compress)
        check(
            "GET exposes bounds",
            compress.get("min_limit") == agent.COMPRESS_MIN_LIMIT
            and compress.get("max_limit") == agent.COMPRESS_MAX_LIMIT,
            compress,
        )
        check(
            "GET resolved limit",
            compress.get("resolved_limit") == agent.MAX_TOOL_OUTPUT,
            compress,
        )

        resp = call(
            "POST",
            "/api/compact/config",
            {"compress": {"enabled": False, "limit": 7500}},
        )
        echoed = resp.get("compress") or {}
        check("POST echoes enabled", echoed.get("enabled") is False, echoed)
        check("POST echoes limit", echoed.get("limit") == 7500, echoed)

        got = call("GET", "/api/compact/config")
        check("setting persists in GET", (got.get("compress") or {}).get("limit") == 7500)

        resp = call(
            "POST", "/api/compact/config", {"compress": {"enabled": True, "limit": None}}
        )
        echoed = resp.get("compress") or {}
        check("POST clears limit", echoed.get("limit") is None, echoed)
        check(
            "resolved back to default",
            echoed.get("resolved_limit") == agent.MAX_TOOL_OUTPUT,
            echoed,
        )

        try:
            call("POST", "/api/compact/config", {"compress": {"limit": 5}})
            check("POST rejects tiny limit", False, "no HTTP 400")
        except urllib.error.HTTPError as exc:
            check("POST rejects tiny limit", exc.code == 400, exc.code)

        try:
            call("POST", "/api/compact/config", {"compress": {"enabled": "yes"}})
            check("POST rejects bad enabled", False, "no HTTP 400")
        except urllib.error.HTTPError as exc:
            check("POST rejects bad enabled", exc.code == 400, exc.code)

        resp = call("POST", "/api/compact/config", {"auto": {"enabled": False}})
        check(
            "POST without compress leaves it on",
            (resp.get("compress") or {}).get("enabled") is True,
            resp.get("compress"),
        )

        # persist 应把 compress 写进配置文件
        resp = call(
            "POST",
            "/api/compact/config",
            {"compress": {"enabled": True, "limit": 9000}, "persist": True},
        )
        check("persist reports saved", resp.get("saved") is True, resp)
        with open(config_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        block = (saved.get("defaults") or {}).get("compress") or {}
        check("config file has compress", block == {"enabled": True, "limit": 9000}, block)

        # 页面里有对应控件
        req = urllib.request.Request(base + "/?token=" + token)
        with urllib.request.urlopen(req, timeout=10) as resp2:
            html = resp2.read().decode("utf-8", "replace")
        check("page has compress checkbox", 'id="cc-compress-enabled"' in html)
        check("page has compress limit", 'id="cc-compress-limit"' in html)
        check("page labels the section", "工具输出压缩" in html)
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("all checks passed")
