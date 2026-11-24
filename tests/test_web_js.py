from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
HARNESS = Path(__file__).resolve().with_name("web_js_harness.js")
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


node = shutil.which("node")
if node is None:
    print("skip web js runtime (node not found)")
    print()
    print("FAILURES: none")
    raise SystemExit(0)

text = AGENT.read_text(encoding="utf-8")
scripts = re.findall(r"<script>\n(.*?)\n</script>", text, re.S)
check("single inline script", len(scripts) == 1, len(scripts))
js = scripts[-1]

base = tempfile.mkdtemp(prefix="web-js-")
js_path = str(Path(base) / "benben-web.js")
Path(js_path).write_text(js, encoding="utf-8")

result = subprocess.run(
    [node, str(HARNESS), js_path],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=60,
)
output = result.stdout + result.stderr
check(
    "web js executes without runtime errors",
    result.returncode == 0 and "JS_RUNTIME_OK" in result.stdout,
    output[-2000:],
)

# Make sure the harness actually catches use-before-initialization, which is
# what node --check cannot detect.
broken_path = str(Path(base) / "broken.js")
Path(broken_path).write_text(
    "useSkillGroup();\n"
    'const skillGroupEl = document.getElementById("skill-group");\n'
    "function useSkillGroup() { return skillGroupEl; }\n",
    encoding="utf-8",
)
broken = subprocess.run(
    [node, str(HARNESS), broken_path],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=60,
)
broken_output = broken.stdout + broken.stderr
check(
    "harness catches TDZ errors",
    broken.returncode != 0
    and "Cannot access" in broken_output,
    broken_output[-1000:],
)

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("FAILURES: none")
