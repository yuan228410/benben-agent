from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


root = tempfile.mkdtemp(prefix="shell-choice-")
ws_root = os.path.join(root, "ws")
os.makedirs(ws_root, exist_ok=True)

try:
    detected = agent.detect_shell_state("auto")
except agent.ShellError:
    detected = None

available = detected.available if detected else []
by_name = {item.name: item for item in available}
default_shell = detected.current if detected else None
posix = next((item for item in available if item.kind == "posix"), None)
other = next(
    (
        item
        for item in available
        if item.kind != "posix" and item.name != default_shell.name
    ),
    None,
) if default_shell else None

if not posix:
    print("skip  no POSIX shell on this host; command level checks skipped")

ws = agent.Workspace(
    ws_root,
    shell_state=agent.ShellState(default_shell, available) if detected else None,
)
ws.interactive = False


# ---------- the shell hint ----------
hint = agent.shell_choice_hint(ws.shell_state)
check("hint mentions the default shell", default_shell.name in hint, hint)
check("hint lists the available shells", "available:" in hint, hint)
check("hint explains the parameter", "omit this parameter" in hint, hint)
check("hint explains no fallback", "no other shell is tried" in hint, hint)
check("hint for no state", agent.shell_choice_hint(None) == "")


# ---------- the tool schema ----------
tools = {tool["function"]["name"]: tool for tool in agent.tool_schemas(ws)}
check("run_command advertised", "run_command" in tools, list(tools))
run_schema = tools["run_command"]
properties = run_schema["function"]["parameters"]["properties"]
check("run_command has a shell property", "shell" in properties, list(properties))
check("shell property is a string", properties["shell"]["type"] == "string")
check(
    "shell property description lists the shells",
    default_shell.name in properties["shell"]["description"],
    properties["shell"]["description"],
)
check(
    "run_command description mentions the shells",
    default_shell.name in run_schema["function"]["description"],
    run_schema["function"]["description"],
)
check(
    "run_command still requires a timeout",
    run_schema["function"]["parameters"]["required"] == ["command", "timeout_seconds"],
    run_schema["function"]["parameters"]["required"],
)
check(
    "switch_shell points at the run_command parameter",
    "shell parameter of run_command" in tools["switch_shell"]["function"]["description"],
    tools["switch_shell"]["function"]["description"],
)
check(
    "list_shell description mentions the run_command parameter",
    "run_command" in tools["list_shells"]["function"]["description"],
    tools["list_shells"]["function"]["description"],
)

# the schema is rebuilt per workspace, so a different shell shows up
if other is not None:
    other_ws = agent.Workspace(
        ws_root,
        shell_state=agent.ShellState(other, [other]),
    )
    other_tools = {tool["function"]["name"]: tool for tool in agent.tool_schemas(other_ws)}
    other_props = other_tools["run_command"]["function"]["parameters"]["properties"]
    check(
        "schema follows the current shell",
        other.name in other_props["shell"]["description"],
        other_props["shell"]["description"],
    )


# ---------- unknown shell ----------
try:
    ws.run_command("echo hi", shell="definitely-not-a-shell", timeout_seconds=10)
    check("unknown shell rejected", False, "no error raised")
except agent.ToolError as exc:
    message = str(exc)
    check("unknown shell rejected", "unknown shell" in message, message)
    check(
        "unknown shell lists the available shells",
        default_shell.name in message,
        message,
    )


# ---------- an explicit shell does not change the default ----------
if posix is not None and posix.path != default_shell.path:
    before = ws.shell_state.current.path
    out = ws.run_command("echo choice-ok", shell=posix.name, timeout_seconds=10)
    check("explicit shell is used", f"shell: {posix.name} (" in out, out)
    check("explicit shell keeps the default", ws.shell_state.current.path == before)
    check("explicit shell ran the command", "choice-ok" in out, out)
else:
    out = ws.run_command("echo choice-ok", timeout_seconds=10)
    check("default shell runs the command", "choice-ok" in out, out)


# ---------- an explicit shell disables the automatic fallback ----------
# A command no shell knows forces the fallback path; naming the shell must
# switch that off.
out = ws.run_command("definitely-missing-command", timeout_seconds=20)
check(
    "fallback is tried without an explicit shell",
    "[fallback]" in out,
    out,
)
out = ws.run_command(
    "definitely-missing-command", shell=default_shell.name, timeout_seconds=20
)
check(
    "explicit shell skips the fallback",
    "[fallback]" not in out,
    out,
)
check(
    "explicit shell failure explains the fix",
    "did not recognize the command" in out,
    out,
)
check(
    "explicit shell failure suggests another shell",
    "retry with a different shell" in out,
    out,
)


# ---------- read-only judgement follows the selected shell ----------
check("posix cat is read only", agent.is_read_only_command("cat file", "posix") is True)
check(
    "powershell cat is read only",
    agent.is_read_only_command("cat file", "powershell") is True,
)
check(
    "powershell remove is not read only",
    agent.is_read_only_command("Remove-Item x", "powershell") is False,
)
check("cmd dir is not read only", agent.is_read_only_command("dir", "cmd") is False)

if posix is not None:
    confirm_ws = agent.Workspace(
        tempfile.mkdtemp(prefix="shell-choice-confirm-"),
        confirm=True,
        shell_state=agent.ShellState(default_shell, available),
    )
    confirm_ws.interactive = False
    # a read-only command in a posix shell needs no approval
    out = confirm_ws.run_command(
        "echo readonly", shell=posix.name, timeout_seconds=10
    )
    check("posix read-only command needs no approval", "readonly" in out, out)
    # a mutating command is still judged by the selected shell
    try:
        confirm_ws.run_command(
            "echo x > blocked.txt", shell=posix.name, timeout_seconds=10
        )
        check("posix mutating command needs approval", False, "no error raised")
    except agent.ToolError as exc:
        check(
            "posix mutating command needs approval",
            "permission denied" in str(exc),
            str(exc),
        )
    check(
        "denied command did not run",
        not os.path.isfile(os.path.join(confirm_ws.root, "blocked.txt")),
    )


# ---------- the system prompt ----------
prompt = agent.build_system_prompt(ws_root, {}, ws.shell_state)
check("prompt mentions the shell parameter", "shell 参数" in prompt, prompt[-800:])
check(
    "prompt explains the shell syntax rule",
    "命令语法由 shell 决定" in prompt,
    prompt[-800:],
)
check(
    "prompt tells the model to pick a shell",
    "选择合适的 shell 是你的职责" in prompt,
    prompt[-800:],
)
check(
    "prompt says the system prompt is not rewritten",
    "系统提示只在启动时生成" in prompt,
    prompt[-800:],
)


# ---------- CLI ----------
home = tempfile.mkdtemp(prefix="shell-choice-cli-")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=os.path.join(home, "config.json"))
result = subprocess.run(
    [sys.executable, AGENT, "--help"],
    text=True,
    capture_output=True,
    env=env,
    timeout=30,
)
check("cli help exit", result.returncode == 0, result.returncode)
check("cli help mentions the shell parameter", "--shell" in result.stdout)

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(home, ignore_errors=True)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
