from pathlib import Path
import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=model.serve_forever, daemon=True).start()

# A valid 32x32 red PNG.
width = height = 32
raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(raw))
    + chunk(b"IEND", b"")
)
data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

home = tempfile.mkdtemp(prefix="web-images-")
env = dict(os.environ, HOME=home)
log_path = os.path.join(home, "web.log")
log_handle = open(log_path, "w+", encoding="utf-8")
web = subprocess.Popen(
    [sys.executable, AGENT, "--web", "--port", "0",
     "--base-url", f"http://127.0.0.1:{model.server_port}/v1",
     "--api-key", "t", "-m", "m1"],
    stdout=log_handle, stderr=log_handle, text=True, env=env,
)
url = None
for _ in range(100):
    log_handle.flush()
    with open(log_path, encoding="utf-8") as reader:
        text = reader.read()
    match = re.search(r"web ui: (http://\S+)", text)
    if match:
        url = match.group(1)
        break
    if web.poll() is not None:
        break
    time.sleep(0.1)
check("web started", url is not None, open(log_path, encoding="utf-8").read()[-300:])

if url:
    base = url.split("?", 1)[0]
    token = url.rsplit("token=", 1)[1]
    headers = {"Content-Type": "application/json", "X-Token": token}

    req = urllib.request.Request(
        base + "/api/chat",
        data=json.dumps({"message": "看图", "images": [{"name": "red.png", "data_url": data_url}]}).encode(),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        chat = json.loads(response.read())

    req = urllib.request.Request(base + "/api/state", headers={"X-Token": token})
    with urllib.request.urlopen(req, timeout=30) as response:
        state_raw = response.read().decode("utf-8")
    state = json.loads(state_raw)
    check("state has no base64", "data:image" not in state_raw, state_raw[:300])
    user_message = next((m for m in state["messages"] if m.get("role") == "user" and m.get("images")), None)
    check("lazy image ref", bool(user_message) and isinstance(user_message["images"][0], dict), user_message)
    image_id = user_message["images"][0]["id"] if user_message else ""
    check("image name", user_message["images"][0].get("name") == "red.png" if user_message else False, user_message)

    req = urllib.request.Request(base + "/api/image?id=" + urllib.parse.quote(image_id),
                                 headers={"X-Token": token})
    with urllib.request.urlopen(req, timeout=30) as response:
        image_bytes = response.read()
        content_type = response.headers.get("Content-Type")
    check("image bytes", image_bytes.startswith(b"\x89PNG\r\n\x1a\n"), image_bytes[:8])
    check("image content type", content_type == "image/png", content_type)

    try:
        req = urllib.request.Request(base + "/api/image?id=" + urllib.parse.quote(image_id),
                                     headers={"X-Token": "wrong"})
        urllib.request.urlopen(req, timeout=30)
        check("wrong token rejected", False)
    except urllib.error.HTTPError as exc:
        check("wrong token rejected", exc.code == 403, exc.code)

    try:
        req = urllib.request.Request(base + "/api/image?id=999:0", headers={"X-Token": token})
        urllib.request.urlopen(req, timeout=30)
        check("bad id rejected", False)
    except urllib.error.HTTPError as exc:
        check("bad id rejected", exc.code == 404, exc.code)

web.terminate()
try:
    web.wait(timeout=5)
except subprocess.TimeoutExpired:
    web.kill()
log_handle.close()
model.shutdown()
shutil.rmtree(home, ignore_errors=True)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
