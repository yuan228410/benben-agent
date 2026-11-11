from pathlib import Path
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
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


base = tempfile.mkdtemp(prefix="skills-")
workspace = os.path.join(base, "workspace")
project_skills = os.path.join(workspace, ".agents", "skills")
project_claude = os.path.join(workspace, ".claude", "skills")
project_codex = os.path.join(workspace, ".codex", "skills")
home_skills = os.path.join(base, "home-skills")
cross_skills = os.path.join(base, "cross-skills")
home_claude = os.path.join(base, "home-claude")
home_codex = os.path.join(base, "home-codex")
for path in (project_skills, project_claude, project_codex, home_skills, cross_skills, home_claude, home_codex):
    os.makedirs(path)
agent.HOME_SKILLS_DIR = home_skills
agent.CROSS_SKILLS_DIR = cross_skills
agent.CLAUDE_SKILLS_DIR = home_claude
agent.CODEX_SKILLS_DIR = home_codex
agent.CODEX_ADMIN_SKILLS_DIR = os.path.join(base, "codex-admin")

skill = os.path.join(project_skills, "log-triage")
os.makedirs(os.path.join(skill, "scripts"))
os.makedirs(os.path.join(skill, "references"))
with open(os.path.join(skill, "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("""---
name: log-triage
description: Triage logs by time and error signature.
license: MIT
compatibility: Requires python3
metadata:
  author: test
  version: "1.0"
allowed-tools: Read Bash
---
# Log triage

Use scripts/hello.py and references/REF.md.
""")
with open(os.path.join(skill, "references", "REF.md"), "w", encoding="utf-8") as f:
    f.write("reference body\n")
with open(os.path.join(skill, "scripts", "hello.py"), "w", encoding="utf-8") as f:
    f.write("""import os, sys
print("cwd=" + os.getcwd())
print("args=" + "|".join(sys.argv[1:]))
print("skill=" + os.environ.get("BENBEN_SKILL_DIR", ""))
print("ws=" + os.environ.get("BENBEN_WORKSPACE", ""))
""")
with open(os.path.join(skill, "scripts", "shebang.sh"), "w", encoding="utf-8") as f:
    f.write("#!/bin/sh\necho shebang-ok\n")
os.chmod(os.path.join(skill, "scripts", "shebang.sh"), 0o755)
with open(os.path.join(skill, "scripts", "run.sh"), "w", encoding="utf-8") as f:
    f.write("echo shell-ok\n")
with open(os.path.join(skill, "scripts", "weird.xyz"), "w", encoding="utf-8") as f:
    f.write("hello\n")
with open(os.path.join(skill, "blob.bin"), "wb") as f:
    f.write(b"\x00\x01\x02")

# invalid skills
bad = os.path.join(project_skills, "Bad_Name")
os.makedirs(bad)
with open(os.path.join(bad, "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("---\nname: Bad_Name\ndescription: bad\n---\n")
nofm = os.path.join(project_skills, "nofm")
os.makedirs(nofm)
with open(os.path.join(nofm, "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("# no frontmatter\n")

# Claude allows the folder name to be the skill name (no `name` field)
claude_skill = os.path.join(project_claude, "claude-skill")
os.makedirs(claude_skill)
with open(os.path.join(claude_skill, "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("---\ndescription: Claude style skill without name.\n---\n# Claude\n")

# Claude allows a frontmatter name that differs from the folder
mismatch = os.path.join(project_claude, "mismatched-dir")
os.makedirs(mismatch)
with open(os.path.join(mismatch, "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("---\nname: declared-name\ndescription: Declared name differs.\n---\n")

# Codex legacy project directory
codex_skill = os.path.join(project_codex, "codex-legacy")
os.makedirs(codex_skill)
with open(os.path.join(codex_skill, "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("---\nname: codex-legacy\ndescription: Codex legacy skill.\n---\n")

# Claude and Codex user directories
for root, name, desc in (
    (home_claude, "home-claude-skill", "Claude home skill."),
    (home_codex, "home-codex-skill", "Codex home skill."),
):
    path = os.path.join(root, name)
    os.makedirs(path)
    with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: {desc}\n---\n")

# duplicate name, lower precedence
dup = os.path.join(home_skills, "log-triage")
os.makedirs(dup)
with open(os.path.join(dup, "SKILL.md"), "w", encoding="utf-8") as f:
    f.write("---\nname: log-triage\ndescription: home version\n---\n")

# duplicate in project should override home
skills = agent.discover_skills(workspace)
print("skills:", sorted(skills))
check("valid skill found", "log-triage" in skills)
check("project precedence", skills["log-triage"]["description"] == "Triage logs by time and error signature.")
check("invalid names skipped", all(n not in skills for n in ("Bad_Name", "nofm", "mismatch")))
check("claude skill without name", "claude-skill" in skills)
check("claude declared name", "declared-name" in skills)
check("codex legacy project skill", "codex-legacy" in skills)
check("claude home skill", "home-claude-skill" in skills)
check("codex home skill", "home-codex-skill" in skills)
check("only own roots", agent.skill_roots(workspace, only_own=True) == [agent.HOME_SKILLS_DIR])
own_skills = agent.discover_skills(workspace, only_own=True)
print("own skills:", sorted(own_skills))
check("only own skills", set(own_skills) == {"log-triage"}, sorted(own_skills))
check("only own hides claude", "claude-skill" not in own_skills)
check("metadata parsed", skills["log-triage"]["metadata"] == {"author": "test", "version": "1.0"})
check("scripts detected", skills["log-triage"]["has_scripts"])
check("resources detected", skills["log-triage"]["has_resources"])

prompt = agent.build_system_prompt(workspace, skills)
check("prompt lists skill", "log-triage" in prompt and "Triage logs" in prompt)
check("prompt no skills", "可用技能" not in agent.build_system_prompt(workspace, {}))

ws = agent.Workspace(workspace, skills=skills)
tools = {tool["function"]["name"] for tool in agent.tool_schemas(ws)}
check("load_skill advertised", "load_skill" in tools)
check("resource tool advertised", "read_skill_resource" in tools)
check("script tool advertised", "run_skill_script" in tools)
ws_no_scripts = agent.Workspace(workspace, skills=skills, allow_skill_scripts=False)
check("scripts can be disabled", "run_skill_script" not in {t["function"]["name"] for t in agent.tool_schemas(ws_no_scripts)})

loaded = ws.load_skill("log-triage")
check("load_skill content", "# Log triage" in loaded)
try:
    ws.load_skill("nope")
    check("unknown skill rejected", False)
except agent.ToolError:
    check("unknown skill rejected", True)

check("resource read", "reference body" in ws.read_skill_resource("log-triage", "references/REF.md"))
for label, path in [("escape", "../SKILL.md"), ("absolute", "/etc/passwd"), ("skill.md", "./SKILL.md"), ("binary", "blob.bin")]:
    try:
        ws.read_skill_resource("log-triage", path)
        check(f"resource {label} rejected", False)
    except agent.ToolError as exc:
        check(f"resource {label} rejected", True, str(exc))

out = ws.run_skill_script("log-triage", "hello.py", ["a", "b"])
check("python script runs", "exit code: 0" in out and "args=a|b" in out, out)
check("script cwd is workspace", f"cwd={os.path.realpath(workspace)}" in out, out)
check("skill env", f"skill={os.path.realpath(skill)}" in out or f"skill={skill}" in out, out)
check("workspace env", f"ws={os.path.realpath(workspace)}" in out, out)
out = ws.run_skill_script("log-triage", "shebang.sh")
check("shebang script runs", "shebang-ok" in out, out)
out = ws.run_skill_script("log-triage", "run.sh")
check("shell extension runs", "shell-ok" in out, out)
try:
    ws.run_skill_script("log-triage", "weird.xyz")
    check("unknown script type rejected", False)
except agent.ToolError:
    check("unknown script type rejected", True)
try:
    ws.run_skill_script("log-triage", "../SKILL.md")
    check("script escape rejected", False)
except agent.ToolError:
    check("script escape rejected", True)
try:
    ws_no_scripts.run_skill_script("log-triage", "hello.py")
    check("disabled scripts rejected", False)
except agent.ToolError:
    check("disabled scripts rejected", True)

strict = agent.Workspace(workspace, confirm=True, skills=skills)
strict.interactive = False
try:
    strict.run_skill_script("log-triage", "hello.py")
    check("script asks approval", False)
except agent.ToolError as exc:
    check("script asks approval", "permission denied" in str(exc), str(exc))

# run_turn sends skill tools and can load a skill
class Handler(BaseHTTPRequestHandler):
    seen_tools = []
    seen_result = ""

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
        Handler.seen_tools = [t["function"]["name"] for t in payload.get("tools", [])]
        if not any(m.get("role") == "tool" for m in payload["messages"]):
            self._send({"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "load_skill", "arguments": json.dumps({"name": "log-triage"})}}],
            }}]})
        else:
            tool_msg = next(m for m in payload["messages"] if m.get("role") == "tool")
            Handler.seen_result = tool_msg["content"]
            self._send({"choices": [{"message": {"role": "assistant", "content": "done"}}]})


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = agent.ChatClient(f"http://127.0.0.1:{server.server_port}/v1", "", "m", timeout=5)
messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
ok = agent.run_turn(client, ws, messages, 3, tools=agent.tool_schemas(ws))
check("run_turn ok", ok)
check("skill tool sent to model", "load_skill" in Handler.seen_tools and "run_skill_script" in Handler.seen_tools)
check("loaded skill reached model", "# Log triage" in Handler.seen_result, Handler.seen_result[:80])
server.shutdown()

# disabled skills: no scanning, no tools
empty_ws = agent.Workspace(workspace, skills={})
empty_tools = {t["function"]["name"] for t in agent.tool_schemas(empty_ws)}
check("no skill tools when disabled", not ({"load_skill", "read_skill_resource", "run_skill_script"} & empty_tools))
check("no skill in prompt when disabled", "可用技能" not in agent.build_system_prompt(workspace, {}))

shutil.rmtree(base)
print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
