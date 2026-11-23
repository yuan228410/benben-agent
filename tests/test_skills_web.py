from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
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
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _payload = json.loads(raw) if raw else {}
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
threading.Thread(target=model.serve_forever, daemon=True).start()
model_base = f"http://127.0.0.1:{model.server_port}/v1"


def start_web(home, extra_args):
    config_path = os.path.join(home, "config.json")
    workspace_dir = os.path.join(home, "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    log_path = os.path.join(home, "web.log")
    log_handle = open(log_path, "w+", encoding="utf-8")
    env = dict(
        os.environ,
        HOME=home,
        BENBEN_CONFIG=config_path,
        OPENAI_MODEL="env-model",
    )
    process = subprocess.Popen(
        [
            sys.executable, AGENT, "--web", "--port", "0",
            "--config", config_path,
            "--cwd", workspace_dir,
            "--base-url", model_base,
            "--api-key", "test-key",
            "-m", "m1",
        ] + extra_args,
        stdout=log_handle,
        stderr=log_handle,
        text=True,
        env=env,
    )
    url = None
    for _ in range(120):
        log_handle.flush()
        with open(log_path, encoding="utf-8") as reader:
            text = reader.read()
        match = re.search(r"http://\S+\?token=[\w-]+", text)
        if match:
            url = match.group(0)
            break
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if url is None:
        log_handle.flush()
        text = open(log_path, encoding="utf-8").read()
        process.kill()
        raise RuntimeError("web did not start: " + text[-1000:])
    return process, url, log_handle


def make_api(url):
    token = url.rsplit("token=", 1)[1]
    base = url.split("?", 1)[0]

    def api(path, body=None, expect_error=False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json", "X-Token": token},
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if expect_error:
                return exc.code, exc.read().decode("utf-8")
            detail = exc.read().decode("utf-8")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        return 200, text

    def api_json(path, body=None, expect_error=False):
        code, text = api(path, body, expect_error)
        if expect_error:
            return code, text
        return json.loads(text)

    def html():
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8")

    return api, api_json, html


home = tempfile.mkdtemp(prefix="skills-web-")
skills_dir = os.path.join(home, ".benben-agent", "skills")
os.makedirs(os.path.join(skills_dir, "seed-skill"))
with open(
    os.path.join(skills_dir, "seed-skill", "SKILL.md"),
    "w",
    encoding="utf-8",
) as handle:
    handle.write(
        "---\nname: seed-skill\ndescription: Seed skill.\n---\n# Seed\n"
    )

process, url, log_handle = start_web(home, ["--skill"])
api, api_json, get_html = make_api(url)

try:
    html = get_html()
    check("web html skill modal", 'id="skill-modal"' in html)
    check("web html skill open", 'id="skill-open"' in html)
    check("web html skill create", "saveSkillCreate" in html)
    check("web html skill install", "saveSkillInstall" in html)
    check("web html skill pending", "renderSkillPending" in html)
    check("web html skill detail", "openSkillDetail" in html)

    data = api_json("/api/skills")
    check("skills enabled", data["enabled"] is True, data)
    names = {row["name"] for row in data["skills"]}
    check("seed skill listed", "seed-skill" in names, names)

    created = api_json("/api/skills/create", {
        "name": "web-skill",
        "description": "Created from web.",
        "instructions": "# Web\n\nDo it.",
        "scope": "user",
    })
    check("web create", created["status"] == "created", created)
    data = api_json("/api/skills?q=web-skill&scope=user")
    check("web search", len(data["skills"]) == 1, data)
    check("web search name", data["skills"][0]["name"] == "web-skill", data)

    detail = api_json("/api/skills/detail?name=web-skill&scope=user")
    check("web detail body", "Do it." in detail["skill"]["body"], detail)
    check("web detail editable", detail["skill"]["editable"] is True, detail)

    updated = api_json("/api/skills/update", {
        "name": "web-skill",
        "scope": "user",
        "description": "Updated from web.",
        "instructions": "# Web v2\n\nDo more.",
    })
    check("web update", updated["status"] == "updated", updated)
    detail = api_json("/api/skills/detail?name=web-skill&scope=user")
    check("web update description", "Updated from web." in detail["skill"]["description"])
    check("web update body", "Do more." in detail["skill"]["body"])

    workspace_created = api_json("/api/skills/create", {
        "name": "workspace-web",
        "description": "Workspace skill.",
        "instructions": "# Workspace\n",
        "scope": "workspace",
    })
    check("web create workspace", workspace_created["scope"] == "workspace")
    data = api_json("/api/skills?scope=workspace")
    check("web scope filter", any(
        row["name"] == "workspace-web" for row in data["skills"]
    ), data)

    source = os.path.join(home, "source-low")
    os.makedirs(source)
    with open(os.path.join(source, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write(
            "---\nname: web-installed\ndescription: Web install.\n---\n# Installed\n"
        )
    installed = api_json("/api/skills/install", {
        "source": source,
        "scope": "user",
    })
    check("web install low", installed["status"] == "installed", installed)

    risky = os.path.join(home, "source-risky")
    os.makedirs(os.path.join(risky, "scripts"))
    with open(os.path.join(risky, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write(
            "---\nname: web-risky\ndescription: Needs approval.\n---\n# Risky\n"
        )
    with open(os.path.join(risky, "scripts", "run.sh"), "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\necho hi\n")
    pending = api_json("/api/skills/install", {
        "source": risky,
        "scope": "user",
    })
    check("web risky pending", pending["status"] == "approval_required", pending)
    pending_data = api_json("/api/skills/pending")
    check("web pending list", any(
        row["approval_id"] == pending["approval_id"]
        for row in pending_data["pending"]
    ), pending_data)
    approved = api_json("/api/skills/approve", {"id": pending["approval_id"]})
    check("web approve", approved["status"] == "installed", approved)
    data = api_json("/api/skills")
    check("web approved listed", any(
        row["name"] == "web-risky" for row in data["skills"]
    ), data)

    pending = api_json("/api/skills/install", {
        "source": risky,
        "scope": "user",
    })
    rejected = api_json("/api/skills/reject", {"id": pending["approval_id"]})
    check("web reject", rejected["status"] == "rejected", rejected)
    pending_data = api_json("/api/skills/pending")
    check("web rejected cleared", not any(
        row["approval_id"] == pending["approval_id"]
        for row in pending_data["pending"]
    ), pending_data)

    code, _text = api("/api/skills/remove", {
        "name": "web-skill",
        "scope": "user",
    }, expect_error=True)
    check("web remove needs confirm", code == 400, code)
    removed = api_json("/api/skills/remove", {
        "name": "web-skill",
        "scope": "user",
        "confirm": True,
    })
    check("web remove", removed["status"] == "removed", removed)
    data = api_json("/api/skills?q=web-skill")
    check("web removed gone", not any(
        row["name"] == "web-skill" for row in data["skills"]
    ), data)

    reloaded = api_json("/api/skills/reload", {})
    check("web reload", reloaded["status"] == "reloaded", reloaded)

    code, _text = api(
        "/api/skills/detail?name=missing-skill", None, expect_error=True
    )
    check("web detail missing", code == 400, code)
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
    log_handle.close()

# A second instance without --skill must not expose the skill API.
home2 = tempfile.mkdtemp(prefix="skills-web-off-")
process2, url2, log_handle2 = start_web(home2, [])
api2, api_json2, _html2 = make_api(url2)
try:
    data = api_json2("/api/skills")
    check("web disabled list", data["enabled"] is False, data)
    code, _text = api2("/api/skills/create", {
        "name": "nope",
        "description": "x",
        "instructions": "y",
    }, expect_error=True)
    check("web disabled create", code == 400, code)
    code, _text = api2("/api/skills/pending", None, expect_error=True)
    check("web disabled pending", code == 400, code)
finally:
    process2.terminate()
    try:
        process2.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process2.kill()
    log_handle2.close()

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("FAILURES: none")
