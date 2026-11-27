"""Stopping a streaming turn must actually stop it.

Two stalls matter, and both used to ignore the stop button:

  * the server sends a few events and then goes silent - the reader sits in
    readline() waiting for a line that never comes;
  * the server accepts the request but never sends response headers - the client
    is still inside urlopen() and has no response object to abort.

The second one is the worse case: with no cancel support in the request open,
the user waited out the whole stream idle timeout (300 seconds by default).
"""

import importlib.util
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


RELEASE = threading.Event()
MODE = {"value": "body"}


class StallingModel(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if MODE["value"] == "headers":
            # Accept the connection and answer nothing.
            RELEASE.wait(60)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(payload):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(payload), payload))
            self.wfile.flush()

        chunk(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n')
        RELEASE.wait(60)


server = ThreadingHTTPServer(("127.0.0.1", 0), StallingModel)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}/v1"


def run_stalled_turn(mode, wait_after_cancel=6.0):
    """Start a streaming turn that stalls, press stop, and report what happened."""
    MODE["value"] = mode
    RELEASE.clear()
    client = agent.ChatClient(f"{BASE}", "", "m", timeout=30)
    client.stream_idle_timeout = 30.0
    cancel = threading.Event()
    result = {}

    def run():
        started = time.monotonic()
        try:
            client.chat(
                [{"role": "user", "content": "hi"}],
                [],
                on_delta=lambda kind, text: None,
                cancel_event=cancel,
            )
            result["outcome"] = "completed"
        except agent.ChatCancelled:
            result["outcome"] = "cancelled"
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            result["outcome"] = f"{type(exc).__name__}: {str(exc)[:60]}"
        finally:
            result["elapsed"] = time.monotonic() - started

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    time.sleep(1.0)
    cancel.set()
    worker.join(timeout=wait_after_cancel)
    hung = worker.is_alive()
    return (
        "hung" if hung else result.get("outcome"),
        result.get("elapsed", wait_after_cancel),
    )


# ---------- a stall in the middle of the stream ----------
outcome, elapsed = run_stalled_turn("body")
check(
    "stop interrupts a silent stream",
    outcome == "cancelled" and elapsed < 3.0,
    f"outcome={outcome} elapsed={elapsed:.2f}s",
)
RELEASE.set()

# ---------- a stall before the response headers ----------
outcome, elapsed = run_stalled_turn("headers", wait_after_cancel=8.0)
check(
    "stop interrupts a request that never answers",
    outcome == "cancelled" and elapsed < 3.0,
    f"outcome={outcome} elapsed={elapsed:.2f}s",
)
RELEASE.set()

# ---------- a cancel during the retry backoff ----------
# A failing endpoint is retried with a growing delay; the stop must not wait it out.
attempts = {"count": 0}


class Failing(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        attempts["count"] += 1
        body = b'{"error":"boom"}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "5")
        self.end_headers()
        self.wfile.write(body)


fail_server = ThreadingHTTPServer(("127.0.0.1", 0), Failing)
threading.Thread(target=fail_server.serve_forever, daemon=True).start()
fail_client = agent.ChatClient(
    f"http://127.0.0.1:{fail_server.server_address[1]}/v1", "", "m", timeout=10
)
fail_client.max_retries = 3
fail_client.stream_idle_timeout = 10.0
cancel = threading.Event()
box = {}


def run_failing():
    started = time.monotonic()
    try:
        fail_client.chat(
            [{"role": "user", "content": "hi"}],
            [],
            on_delta=lambda kind, text: None,
            cancel_event=cancel,
        )
        box["outcome"] = "completed"
    except agent.ChatCancelled:
        box["outcome"] = "cancelled"
    except BaseException as exc:  # noqa: BLE001
        box["outcome"] = f"{type(exc).__name__}: {str(exc)[:60]}"
    finally:
        box["elapsed"] = time.monotonic() - started


worker = threading.Thread(target=run_failing, daemon=True)
worker.start()
time.sleep(1.0)          # first attempt fails, backoff begins
cancel.set()
worker.join(timeout=6.0)
check(
    "stop interrupts the retry backoff",
    not worker.is_alive()
    and box.get("outcome") == "cancelled"
    and box.get("elapsed", 99) < 3.0,
    f"outcome={box.get('outcome')} elapsed={box.get('elapsed', 99):.2f}s attempts={attempts['count']}",
)
fail_server.shutdown()

# ---------- a completed stream still reports normally ----------


class Healthy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for payload in (
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(payload), payload))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


ok_server = ThreadingHTTPServer(("127.0.0.1", 0), Healthy)
threading.Thread(target=ok_server.serve_forever, daemon=True).start()
ok_client = agent.ChatClient(
    f"http://127.0.0.1:{ok_server.server_address[1]}/v1", "", "m", timeout=10
)
ok_client.stream_idle_timeout = 10.0
deltas = []
try:
    reply = ok_client.chat(
        [{"role": "user", "content": "hi"}],
        [],
        on_delta=lambda kind, text: deltas.append(text),
        cancel_event=threading.Event(),
    )
    check("healthy stream still completes", reply.get("content") == "hi", reply)
    check("healthy stream still emits deltas", "".join(deltas) == "hi", deltas)
except BaseException as exc:  # noqa: BLE001
    check("healthy stream still completes", False, f"{type(exc).__name__}: {exc}")
ok_server.shutdown()

server.shutdown()

print()
if failures:
    print("FAILURES:", failures)
    raise SystemExit(1)
print("FAILURES: none")
