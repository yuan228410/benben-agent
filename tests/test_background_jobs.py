import contextlib
import importlib.util
import io
import json
import os
import re
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


ws = agent.Workspace(
    tempfile.mkdtemp(prefix="background-jobs-"),
    background_after_seconds=1,
    job_follow=False,
    job_poll_seconds=1,
    job_follow_lines=2,
)

# ---------- auto background ----------
started = time.monotonic()
out = ws.run_command("echo before-job; sleep 4", timeout_seconds=15)
elapsed = time.monotonic() - started
check("auto background quick", elapsed < 5, elapsed)
check("auto background returns job", "started background job" in out, out)
match = re.search(r"job-[0-9a-f]+", out)
check("auto background id", bool(match), out)
job_id = match.group(0) if match else ""
if job_id:
    snap = ws.jobs.snapshot(job_id)
    check("auto background running", snap["status"] == "running", snap)
    time.sleep(0.2)
    check("auto background output", "before-job" in ws.jobs.output_text(job_id), ws.jobs.output_text(job_id))
    check("auto background finishes", ws.jobs.wait(job_id, 8) is True)
    snap = ws.jobs.snapshot(job_id)
    check("auto background exited", snap["status"] == "exited", snap)
    check("auto background code", snap["exit_code"] == 0, snap)

# ---------- cancel process tree ----------
cancel_out = ws.run_command("sleep 20", timeout_seconds=30, background=True)
cancel_match = re.search(r"job-[0-9a-f]+", cancel_out)
check("cancel starts", bool(cancel_match), cancel_out)
if cancel_match:
    cancel_id = cancel_match.group(0)
    cancelled = ws.jobs.cancel(cancel_id)
    check("cancel status", "cancelled" in cancelled, cancelled)
    check("cancel not running", ws.jobs.snapshot(cancel_id)["status"] == "cancelled")

# ---------- foreground Ctrl+C terminates ----------
foreground_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="foreground-jobs-"),
    background_after_seconds=5,
    job_follow=False,
    job_poll_seconds=1,
)
original_wait = foreground_ws.jobs.wait
wait_calls = []


def interrupt_first_wait(job_id, seconds):
    wait_calls.append(job_id)
    if len(wait_calls) == 1:
        raise KeyboardInterrupt()
    return original_wait(job_id, seconds)


foreground_ws.jobs.wait = interrupt_first_wait
try:
    foreground_ws.run_command("sleep 20", timeout_seconds=30)
    check("foreground ctrl+c raised", False)
except KeyboardInterrupt:
    check("foreground ctrl+c raised", True)
foreground_jobs = foreground_ws.jobs.list_snapshots()
check(
    "foreground ctrl+c cancelled job",
    bool(foreground_jobs) and foreground_jobs[0]["status"] == "cancelled",
    foreground_jobs,
)

# ---------- follow mode ----------
ws.prompt_mode = "interactive"
ws.job_follow = True
events = []
ws.job_event = lambda kind, text: events.append((kind, json.loads(text)))
follow_out = ws.run_command(
    "printf 'a\\nb\\nc\\nd\\ne\\nf\\n'; sleep 1",
    timeout_seconds=10,
    background=True,
)
ws.job_event = None
check("follow returns result", "job job-" in follow_out and "exited" in follow_out, follow_out)
check("follow start event", any(k == "job_start" for k, _ in events), events)
output_events = [payload for kind, payload in events if kind == "job_output"]
check("follow output event", bool(output_events), events)
check(
    "follow line limit",
    all(len(payload.get("lines", [])) <= 2 for payload in output_events),
    output_events,
)
check(
    "follow omitted lines",
    any(int(payload.get("omitted") or 0) >= 1 for payload in output_events),
    output_events,
)
check("follow done event", any(kind == "job_done" for kind, _ in events), events)

# ---------- quiet success notifications ----------
quiet_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="job-notify-"),
    background_after_seconds=0,
)
quiet_out = quiet_ws.run_command("true", timeout_seconds=5, background=True)
quiet_match = re.search(r"job-[0-9a-f]+", quiet_out)
if quiet_match:
    quiet_ws.jobs.wait(quiet_match.group(0), 3)
quiet_buffer = io.StringIO()
with contextlib.redirect_stdout(quiet_buffer):
    agent.print_job_notifications(quiet_ws)
check("success notification quiet", quiet_buffer.getvalue() == "", quiet_buffer.getvalue())

failed_out = quiet_ws.run_command("false", timeout_seconds=5, background=True)
failed_match = re.search(r"job-[0-9a-f]+", failed_out)
if failed_match:
    quiet_ws.jobs.wait(failed_match.group(0), 3)
failed_buffer = io.StringIO()
with contextlib.redirect_stdout(failed_buffer):
    agent.print_job_notifications(quiet_ws)
check("explicit background notification quiet", failed_buffer.getvalue() == "", failed_buffer.getvalue())

auto_ws = agent.Workspace(
    tempfile.mkdtemp(prefix="job-auto-notify-"),
    background_after_seconds=1,
    job_follow=False,
)
auto_out = auto_ws.run_command("sleep 3", timeout_seconds=10)
auto_match = re.search(r"job-[0-9a-f]+", auto_out)
if auto_match:
    auto_ws.jobs.wait(auto_match.group(0), 6)
auto_buffer = io.StringIO()
with contextlib.redirect_stdout(auto_buffer):
    agent.print_job_notifications(auto_ws)
check("auto-promoted notification shown", "[job]" in auto_buffer.getvalue(), auto_buffer.getvalue())

# ---------- list/clean ----------
check("list jobs", "background jobs:" in ws.jobs.list_text(), ws.jobs.list_text())
check("clean jobs", "cleaned" in ws.jobs.clean_finished())

# ---------- config defaults ----------
defaults = agent.default_settings()
check("default job follow", defaults["job_follow"] is True)
check("default job poll", defaults["job_poll_seconds"] == 2)
check("default job lines", defaults["job_follow_lines"] == 5)
check("default command timeout", defaults["command_timeout"] == 60)
check("default max command timeout", defaults["max_command_timeout"] == 600)

# ---------- CLI /jobs ----------
agent_path = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
home = tempfile.mkdtemp(prefix="jobs-cli-")
env = dict(os.environ, HOME=home, BENBEN_CONFIG=os.path.join(home, "config.json"))
result = __import__("subprocess").run(
    [
        __import__("sys").executable,
        agent_path,
        "--no-config",
        "--no-env",
        "--base-url",
        "http://127.0.0.1:1/v1",
        "--api-key",
        "k",
        "-m",
        "m",
        "--shell",
        "auto",
    ],
    input="/jobs\n/jobs poll 3\n/jobs lines 4\n/exit\n",
    text=True,
    capture_output=True,
    env=env,
    timeout=40,
)
check("cli jobs exit", result.returncode == 0, result.stderr[-800:])
check("cli jobs list", "no background jobs" in result.stdout, result.stdout[-1000:])
check("cli jobs poll", "job poll interval: 3s" in result.stdout, result.stdout[-1000:])
check("cli jobs lines", "job follow lines: 4" in result.stdout, result.stdout[-1000:])

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
