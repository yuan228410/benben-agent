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


def make_executable(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, 0o755)
    return path


def make_script(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.chmod(path, 0o755)
    return path


def which_from(mapping):
    return lambda name: mapping.get(name)


root = tempfile.mkdtemp(prefix="shell-test-")
win = os.path.join(root, "win")
pwsh = make_executable(os.path.join(win, "pwsh.exe"))
powershell = make_executable(os.path.join(win, "powershell.exe"))
cmd = make_executable(os.path.join(win, "cmd.exe"))
git = make_executable(os.path.join(win, "Git", "cmd", "git.exe"))
git_bash = make_executable(os.path.join(win, "Git", "bin", "bash.exe"))
wsl_bash = make_executable(os.path.join(win, "System32", "bash.exe"))
win_env = {
    "SystemRoot": win,
    "COMSPEC": cmd,
    "ProgramFiles": win,
    "LOCALAPPDATA": win,
}
win_map = {
    "pwsh": pwsh,
    "powershell": powershell,
    "cmd": cmd,
    "git": git,
}

# ---------- Windows order ----------
state = agent.detect_shell_state(
    "auto", platform_name="win32", which=which_from(win_map), environ=win_env
)
check("windows pwsh first", state.current.name == "pwsh", state.describe())
check(
    "windows order",
    [item.name for item in state.available] == ["pwsh", "powershell", "git-bash", "cmd"],
    state.describe(),
)

win_map.pop("pwsh")
state = agent.detect_shell_state(
    "auto", platform_name="win32", which=which_from(win_map), environ=win_env
)
check("windows powershell second", state.current.name == "powershell", state.describe())

win_map.pop("powershell")
state = agent.detect_shell_state(
    "auto", platform_name="win32", which=which_from(win_map), environ=win_env
)
check("windows git bash third", state.current.name == "git-bash", state.describe())

win_map.pop("git")
os.unlink(git_bash)
state = agent.detect_shell_state(
    "auto", platform_name="win32", which=which_from(win_map), environ=win_env
)
check("windows cmd last", state.current.name == "cmd", state.describe())
check("wsl bash ignored", "bash" not in [item.name for item in state.available], state.describe())

try:
    agent.select_shell("bash", platform_name="win32", which=which_from(win_map), environ=win_env)
    check("windows bash does not fall back to wsl", False)
except agent.ShellError:
    check("windows bash does not fall back to wsl", True)

try:
    agent.select_shell(
        "git-bash", platform_name="win32", which=which_from(win_map), environ=win_env
    )
    check("missing git bash raises", False)
except agent.ShellError:
    check("missing git bash raises", True)

# ---------- POSIX order ----------
posix_root = os.path.join(root, "posix")
bash = make_executable(os.path.join(posix_root, "bash"))
zsh = make_executable(os.path.join(posix_root, "zsh"))
sh = make_executable(os.path.join(posix_root, "sh"))
fish = make_executable(os.path.join(posix_root, "fish"))
posix_map = {"bash": bash, "zsh": zsh, "sh": sh, "fish": fish}

state = agent.detect_shell_state(
    "auto",
    platform_name="linux",
    which=which_from(posix_map),
    environ={"SHELL": zsh},
)
check("shell env wins on linux", state.current.name == "zsh", state.describe())
check("shell env marked", state.available[0].source == "SHELL", state.available[0].source)

state = agent.detect_shell_state(
    "auto", platform_name="linux", which=which_from(posix_map), environ={}
)
check("linux fallback bash", state.current.name == "bash", state.describe())

state = agent.detect_shell_state(
    "auto", platform_name="darwin", which=which_from(posix_map), environ={}
)
check("macos fallback zsh", state.current.name == "zsh", state.describe())

state = agent.detect_shell_state(
    "auto",
    platform_name="linux",
    which=which_from(posix_map),
    environ={"SHELL": fish},
)
check("fish not auto", state.current.name == "bash", state.describe())

state = agent.detect_shell_state(
    "auto",
    platform_name="darwin",
    which=which_from(posix_map),
    environ={"SHELL": bash},
)
check("shell env beats macos zsh", state.current.name == "bash", state.describe())

state = agent.detect_shell_state(
    "auto",
    platform_name="linux",
    which=which_from(posix_map),
    environ={"SHELL": "/bin/sh"},
)
check("shell env sh", state.current.name == "sh", state.describe())

try:
    agent.select_shell(
        "nope", platform_name="linux", which=which_from(posix_map), environ={}
    )
    check("unknown shell raises", False)
except agent.ShellError:
    check("unknown shell raises", True)

selected, _available = agent.select_shell(
    "zsh", platform_name="linux", which=which_from(posix_map), environ={}
)
check("explicit zsh", selected.name == "zsh" and selected.path == zsh)
selected, _available = agent.select_shell(
    zsh, platform_name="linux", which=which_from(posix_map), environ={}
)
check("explicit path", selected.name == "zsh")

# ---------- argv and encoding ----------
posix_info = agent.ShellInfo(sh, "sh", "posix")
check("posix argv", posix_info.command_argv("echo hi") == [sh, "-c", "echo hi"])
powershell_info = agent.ShellInfo(pwsh, "pwsh", "powershell")
argv = powershell_info.command_argv("Get-Date")
check("powershell argv", argv[1:5] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command"], argv)
check("powershell utf8 prefix", "[Console]::OutputEncoding" in argv[-1], argv[-1])
cmd_info = agent.ShellInfo(cmd, "cmd", "cmd")
cmd_argv = cmd_info.command_argv("dir")
check("cmd argv", cmd_argv[1:4] == ["/d", "/s", "/c"], cmd_argv)
check("cmd utf8 prefix", "chcp 65001>nul" in cmd_argv[-1], cmd_argv[-1])
check("powershell encoding", powershell_info.encoding == "utf-8")
check("cmd encoding", cmd_info.encoding == "utf-8")

check(
    "decode gbk fallback",
    agent.decode_output("中文".encode("gbk"), ["utf-8", "gbk"]) == "中文",
)
check(
    "decode utf8 bom",
    agent.decode_output("\ufeff中文".encode("utf-8"), ["utf-8"]) == "中文",
)
check(
    "decode utf16",
    agent.decode_output("中文".encode("utf-16-le"), ["utf-8", "utf-16-le"]) == "中文",
)
check(
    "decode invalid bytes",
    "\ufffd" in agent.decode_output(b"\xff\xfe\xff", ["utf-8"]),
)

# ---------- read-only and prompt ----------
check("posix read only", agent.is_read_only_command("ls -la"))
check("powershell never read only", not agent.is_read_only_command("Get-ChildItem", "powershell"))
check("cmd never read only", not agent.is_read_only_command("dir", "cmd"))
check("posix multi", agent.is_read_only_command("ls | grep x"))
check("shell mismatch posix", agent._shell_mismatch("posix", 127, "", "foo: command not found"))
check("shell mismatch posix localized", agent._shell_mismatch("posix", 127, "", "foo: 未找到命令"))
check("shell mismatch real 127", not agent._shell_mismatch("posix", 127, "output", "done"))
check(
    "shell mismatch cmd chinese",
    agent._shell_mismatch("cmd", 1, "", "'foo' 不是内部或外部命令，也不是可运行的程序或批处理文件。"),
)
check(
    "shell mismatch powershell chinese",
    agent._shell_mismatch("powershell", 1, "", "无法将“foo”项识别为 cmdlet、函数、脚本文件或可运行程序的名称。"),
)

posix_state = agent.ShellState(posix_info, [posix_info])
prompt = agent.build_system_prompt(root, {}, posix_state)
check("prompt current shell", "当前 shell：sh" in prompt)
check("prompt available", "可用 shell（按优先顺序）：sh" in prompt)
check("prompt posix commands", "grep/awk/sed" in prompt)

powershell_state = agent.ShellState(powershell_info, [powershell_info])
ps_prompt = agent.build_system_prompt(root, {}, powershell_state)
check("prompt powershell commands", "Get-ChildItem" in ps_prompt)
check("prompt powershell no posix commands", "优先使用 grep/awk/sed" not in ps_prompt)

cmd_state = agent.ShellState(cmd_info, [cmd_info])
cmd_prompt = agent.build_system_prompt(root, {}, cmd_state)
check("prompt cmd commands", "findstr" in cmd_prompt)

# ---------- Workspace and tools ----------
ws_root = tempfile.mkdtemp(prefix="shell-ws-")
real_sh = agent.ShellInfo("/bin/sh", "sh", "posix")
ws = agent.Workspace(ws_root, shell_state=agent.ShellState(real_sh, [real_sh]))
out = ws.run_command("echo shell-ok")
check("workspace run_command", "exit code: 0" in out and "shell-ok" in out, out)
check("workspace shell result", "shell: sh (" in out, out)
check("workspace list_shells", ws.list_shells().startswith("current: sh"))
switched = ws.switch_shell("sh")
check("workspace switch", "switched shell to: sh" in switched, switched)
check("workspace dirty", ws.shell_state.dirty)
tool_names = {tool["function"]["name"] for tool in agent.tool_schemas(ws)}
check("shell tools advertised", {"list_shells", "switch_shell"} <= tool_names, tool_names)
check("dispatch list_shells", ws.dispatch("list_shells", {}).startswith("current: sh"))
check("dispatch switch_shell", "switched shell" in ws.dispatch("switch_shell", {"name": "sh"}))

# ---------- automatic fallback ----------
fallback_root = os.path.join(root, "fallback")
fail1 = make_script(
    os.path.join(fallback_root, "fail1"),
    '#!/bin/sh\nif [ "$1" = "-c" ]; then echo "fail1: command not found" >&2; exit 127; fi\n',
)
fail2 = make_script(
    os.path.join(fallback_root, "fail2"),
    '#!/bin/sh\nif [ "$1" = "-c" ]; then echo "fail2: command not found" >&2; exit 127; fi\n',
)
good = make_script(
    os.path.join(fallback_root, "good"),
    '#!/bin/sh\necho fallback-ok\n',
)
bad = make_script(
    os.path.join(fallback_root, "bad"),
    '#!/bin/sh\nif [ "$1" = "-c" ]; then echo "syntax error" >&2; exit 2; fi\n',
)
shell_fail1 = agent.ShellInfo(fail1, "fail1", "posix")
shell_fail2 = agent.ShellInfo(fail2, "fail2", "posix")
shell_good = agent.ShellInfo(good, "good", "posix")
shell_bad = agent.ShellInfo(bad, "bad", "posix")

fallback_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="fallback-ws-"),
    shell_state=agent.ShellState(shell_fail1, [shell_fail1, shell_good]),
)
out = fallback_ws.run_command("definitely-missing")
check("fallback succeeds", "fallback-ok" in out, out)
check("fallback noted", "[fallback]" in out and "retried with good" in out, out)

all_fail_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="fallback-ws-"),
    shell_state=agent.ShellState(shell_fail1, [shell_fail1, shell_fail2]),
)
out = all_fail_ws.run_command("definitely-missing")
check("fallback all fail", "tried:" in out and "reason:" in out, out)
check("fallback all fail details", "fail1:" in out and "fail2:" in out, out)

normal_fail_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="fallback-ws-"),
    shell_state=agent.ShellState(shell_bad, [shell_bad, shell_good]),
)
out = normal_fail_ws.run_command("anything")
check("normal failure no fallback", "fallback-ok" not in out and "syntax error" in out, out)

missing_shell = agent.ShellInfo("/definitely/not/a/shell", "missing", "posix")
launch_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="fallback-ws-"),
    shell_state=agent.ShellState(missing_shell, [missing_shell, shell_good]),
)
out = launch_ws.run_command("anything")
check("launch error falls back", "fallback-ok" in out and "retried with good" in out, out)

all_launch_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="fallback-ws-"),
    shell_state=agent.ShellState(
        missing_shell,
        [missing_shell, agent.ShellInfo("/also/missing", "missing2", "posix")],
    ),
)
out = all_launch_ws.run_command("anything")
check("launch errors explain", "could be started" in out and "missing:" in out, out)

class FakeClient:
    def __init__(self):
        self.last_reasoning_ignored = False
        self.last_usage = {}
        self.last_finish_reason = "stop"
        self.seen = None

    def chat(self, messages, tools):
        self.seen = [dict(message) for message in messages]
        return {"role": "assistant", "content": "ok"}


fake = FakeClient()
messages = [{"role": "system", "content": "old"}, {"role": "user", "content": "hi"}]
ws.shell_state.dirty = True
check("run_turn ok", agent.run_turn(fake, ws, messages, 2))
check("prompt refreshed", "当前 shell" in messages[0]["content"], messages[0])
check("dirty cleared", ws.shell_state.dirty is False)

# ---------- CLI ----------
home = tempfile.mkdtemp(prefix="shell-cli-")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=os.path.join(home, "config.json"))
result = subprocess.run(
    [sys.executable, AGENT, "--shell", "/definitely/not/a/shell", "hi"],
    text=True,
    capture_output=True,
    env=env,
    timeout=30,
)
check("cli bad shell exit", result.returncode == 2, result.returncode)
check("cli bad shell message", "shell not found" in result.stderr, result.stderr[-200:])

result = subprocess.run(
    [sys.executable, AGENT, "--shell", "sh"],
    input="/exit\n",
    text=True,
    capture_output=True,
    env=env,
    timeout=30,
)
check("cli shell banner", "shell: sh (" in result.stdout, result.stdout[-500:])

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(ws_root, ignore_errors=True)
shutil.rmtree(home, ignore_errors=True)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
