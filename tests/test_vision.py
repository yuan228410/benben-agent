from pathlib import Path
import ast
import importlib.util
import json
import os
import shutil
import struct
import tempfile
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

spec = importlib.util.spec_from_file_location("agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


def make_png(width, height, rgb):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# ---------- python 3.8 syntax gate ----------
ast.parse(open(str(Path(__file__).resolve().parents[1] / "benben-agent.py")).read(), feature_version=(3, 8))
check("python 3.8 syntax", True)

# ---------- mime sniffing ----------
mime = agent._image_mime
check("mime png", mime(make_png(1, 1, (255, 0, 0))) == "image/png")
check("mime jpeg", mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg")
check("mime gif", mime(b"GIF89a....") == "image/gif")
check("mime webp", mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp")
check("mime bmp", mime(b"BM\x00\x00") == "image/bmp")
check("mime unknown", mime(b"hello world") is None)

base = tempfile.mkdtemp(prefix="vision-")
png_path = os.path.join(base, "red.png")
with open(png_path, "wb") as f:
    f.write(make_png(32, 32, (255, 0, 0)))
txt_path = os.path.join(base, "x.txt")
with open(txt_path, "w") as f:
    f.write("hello")
big_path = os.path.join(base, "big.png")
with open(big_path, "wb") as f:
    f.truncate(agent.MAX_IMAGE_BYTES + 1)

url, name = agent.load_image(png_path)
check("load_image url", url.startswith("data:image/png;base64,") and name == "red.png")
check("load_image base64 decodes", __import__("base64").b64decode(url.split(",", 1)[1]) == open(png_path, "rb").read())
for label, path in [("missing", os.path.join(base, "nope.png")), ("unsupported", txt_path), ("oversize", big_path)]:
    try:
        agent.load_image(path)
        check(f"load_image {label} raises", False)
    except agent.ImageError as exc:
        check(f"load_image {label} raises", True, str(exc))

try:
    agent.load_images([png_path] * (agent.MAX_IMAGES + 1))
    check("too many images raises", False)
except agent.ImageError as exc:
    check("too many images raises", "too many" in str(exc), str(exc))

# ---------- build_user_message ----------
plain = agent.build_user_message("hello")
check("plain message is str", plain == {"role": "user", "content": "hello"})
with_img = agent.build_user_message("what", [agent.load_image(png_path)])
check("multimodal text part", with_img["content"][0] == {"type": "text", "text": "what"})
check("multimodal image part", with_img["content"][1]["type"] == "image_url" and with_img["content"][1]["image_url"]["url"].startswith("data:image/png"))
check("image names stored", with_img["_images"] == ["red.png"])
only_img = agent.build_user_message("", [agent.load_image(png_path)])
check("only image gets default text", only_img["content"][0]["text"] == "请描述这张图片")

# ---------- render_history handles list content ----------
history = agent.render_history(
    [{"role": "user", "content": with_img["content"], "_images": ["red.png"]},
     {"role": "assistant", "content": "红色"}],
    "m1", base,
)
check("history text", "what" in history and "红色" in history, history)
check("history image marker", "🖼 图片: red.png" in history, history)

# ---------- read_file binary guard ----------
ws = agent.Workspace(base)
try:
    ws.read_file("red.png")
    check("read_file binary guard", False)
except agent.ToolError as exc:
    check("read_file binary guard", "binary file" in str(exc), str(exc))
check("read_file text still works", "hello" in ws.read_file("x.txt"))


# ---------- server: verifies image_url and no underscore leak ----------
class Handler(BaseHTTPRequestHandler):
    seen = {"image": False, "leak": False}

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        for m in payload["messages"]:
            if any(k.startswith("_") for k in m):
                Handler.seen["leak"] = True
            if isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"]):
                Handler.seen["image"] = True
        self._send({"choices": [{"message": {"role": "assistant", "content": "done"}}]})


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = agent.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=5)
messages = [{"role": "system", "content": "s"}, agent.build_user_message("hi", [agent.load_image(png_path)])]
agent.run_turn(client, ws, messages, 2)
check("image_url reached server", Handler.seen["image"])
check("underscore keys stripped", not Handler.seen["leak"])
server.shutdown()

# ---------- images kept in memory and history ----------
user_message = next(m for m in messages if m.get("role") == "user")
check("image_url kept in memory", any(
    isinstance(p, dict) and p.get("type") == "image_url"
    for p in user_message["content"]
), user_message)
check("image names kept", user_message.get("_images") == ["red.png"], user_message)
view = agent.history_view(messages)
user_view = next(m for m in view if m["role"] == "user")
check("history view includes image", user_view.get("images") and user_view["images"][0].startswith("data:image/png"), user_view)

# ---------- check_image doesn't retain data ----------
check("check_image valid", agent.check_image(png_path) == "red.png")
for label, path in [("missing", os.path.join(base, "nope.png")), ("unsupported", txt_path), ("oversize", big_path)]:
    try:
        agent.check_image(path)
        check(f"check_image {label} raises", False)
    except agent.ImageError:
        check(f"check_image {label} raises", True)

shutil.rmtree(base)
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
