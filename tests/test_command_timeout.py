import importlib.util
import os
import tempfile
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent", str(Path(__file__).resolve().parents[1] / "benben-agent.py")
)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


schema = next(
    item for item in agent.TOOL_SCHEMAS
    if item["function"]["name"] == "run_command"
)
check(
    "timeout required",
    "timeout_seconds" in schema["function"]["parameters"]["required"],
    schema,
)
check(
    "timeout description",
    "build/test/install"
    in schema["function"]["parameters"]["properties"]["timeout_seconds"]["description"],
    schema["function"]["parameters"]["properties"]["timeout_seconds"],
)
check(
    "prompt timeout rule",
    "timeout_seconds" in agent.SYSTEM_PROMPT and "tail -f" in agent.SYSTEM_PROMPT,
)
check(
    "timeout result guidance",
    "partial output is included below"
    in Path(agent.__file__).read_text(encoding="utf-8"),
)

workspace_dir = tempfile.mkdtemp(prefix="command-timeout-")
workspace = agent.Workspace(workspace_dir)
started = time.monotonic()
result = workspace.run_command("echo before-timeout; sleep 30", timeout_seconds=1)
elapsed = time.monotonic() - started
check("run_command timed out quickly", elapsed < 8, elapsed)
check("timeout note", "[timeout]" in result and "timed out after 1s" in result, result)
check("partial output kept", "before-timeout" in result, result)

if os.name != "nt":
    pid_path = os.path.join(workspace_dir, "child.pid")
    script = (
        "sleep 30 & "
        f"echo $! > {pid_path}; "
        "wait"
    )
    rc, stdout, stderr, truncated, timed_out = agent._capture_process(
        ["bash", "-c", script],
        workspace_dir,
        1,
    )
    check("capture timed_out", timed_out is True, (rc, stdout, stderr, truncated, timed_out))
    check("child pid file", os.path.exists(pid_path), (stdout, stderr))
    if os.path.exists(pid_path):
        child_pid = int(open(pid_path, encoding="utf-8").read().strip())
        alive = True
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                alive = False
                break
            except PermissionError:
                break
            time.sleep(0.1)
        check("child process killed", alive is False, child_pid)

print()
print("FAILURES:", failures if failures else "none")
if failures:
    raise SystemExit(1)
