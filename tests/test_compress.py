import importlib.util
import json
import re
from pathlib import Path

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


# --- Short input is returned untouched ------------------------------------- #

short = "hello world"
check("short text untouched", agent.compress_tool_output(short, 20000) == short)

empty = ""
check("empty stays empty", agent.compress_tool_output(empty, 20000) == "")

# --- Output never exceeds the limit ---------------------------------------- #

log = "\n".join(
    f"2026-11-24 10:00:{i % 60:02d} INFO step {i} completed in {i * 7}ms"
    for i in range(3000)
)
compressed = agent.compress_tool_output(log, 5000)
check("log stays under limit", len(compressed) <= 5000, len(compressed))
check("log is actually smaller", len(compressed) < len(log), f"{len(compressed)} vs {len(log)}")

# --- Error lines survive --------------------------------------------------- #

lines = [f"2026-11-24 10:00:00 INFO noise line {i}" for i in range(2000)]
lines[1000] = "2026-11-24 10:00:00 ERROR connection refused to db:5432"
lines[1500] = "Traceback (most recent call last): KeyError: 'user_id'"
payload = "\n".join(lines)
squeezed = agent.compress_tool_output(payload, 4000)
check("keeps ERROR line", "connection refused to db:5432" in squeezed)
check("keeps Traceback line", "KeyError: 'user_id'" in squeezed)
check("error payload under limit", len(squeezed) <= 4000, len(squeezed))

# --- Tail is preserved, unlike a plain head cut ---------------------------- #

check("keeps tail context", "noise line 1999" in squeezed, "tail missing")
check("keeps head context", "noise line 0" in squeezed, "head missing")

# --- Repeated lines are folded --------------------------------------------- #

repeats = "\n".join(["connection still open"] * 500 + ["final answer: 42"])
folded = agent.compress_tool_output(repeats, 2000)
check("folds repeats", "重复 500 次" in folded, folded[:200])
check("folded keeps tail", "final answer: 42" in folded)
check("folded under limit", len(folded) <= 2000, len(folded))

# --- JSON keeps its shape -------------------------------------------------- #

doc = {
    "items": [{"id": i, "name": f"item-{i}", "blob": "x" * 400} for i in range(200)],
    "total": 200,
    "ok": True,
}
raw_json = json.dumps(doc)
compressed_json = agent.compress_tool_output(raw_json, 3000)
check("json under limit", len(compressed_json) <= 3000, len(compressed_json))
check("json keeps key 'total'", '"total"' in compressed_json)
check("json keeps key 'ok'", '"ok"' in compressed_json)
check("json keeps item ids", '"id"' in compressed_json)
check("json marks omission", "项省略" in compressed_json or "键省略" in compressed_json)
parsed = json.loads(compressed_json)
check("json still parses", isinstance(parsed, dict))
check("json keeps first item name", parsed["items"][0]["name"] == "item-0")

# --- Content detection ----------------------------------------------------- #

check("detects json", agent.output_kind(raw_json) == "json")
check("detects log", agent.output_kind(log) == "log")
check(
    "detects code",
    agent.output_kind(
        "import os\nimport sys\n\ndef main():\n    return 0\n\nclass Thing:\n    pass"
    )
    == "code",
)
check("detects plain text", agent.output_kind("just some words here\nand more") == "text")

# --- Code keeps declarations ----------------------------------------------- #

source = "\n".join(
    [f"    x_{i} = compute({i})  # filler" for i in range(500)]
    + ["def important_function(a, b):", "    return a + b"]
)
compressed_code = agent.compress_tool_output(source, 2000)
check("code under limit", len(compressed_code) <= 2000, len(compressed_code))
check("code keeps def", "def important_function" in compressed_code)

# --- Very small limit degrades gracefully ---------------------------------- #

tiny = agent.compress_tool_output(log, 100)
check("tiny limit respects bound", len(tiny) <= 160, len(tiny))
check("tiny limit still returns text", bool(tiny))

# --- Report helper --------------------------------------------------------- #

report = agent.compression_report(raw_json, 3000)
check("report mentions kind", "类型=json" in report, report)
check("report mentions sizes", "原始=" in report and "压缩后=" in report, report)

# --- Runtime switch -------------------------------------------------------- #

agent.configure_compression(True, None)
check("enabled by default", agent.compression_enabled() is True)
check("default budget", agent.compression_limit() == agent.MAX_TOOL_OUTPUT)

long_text = "x" * 40000
compressed_on = agent._clip_tool_text(long_text)
check("compressor active", "截断" in compressed_on or "省略" in compressed_on, compressed_on[:80])
check("compressed under limit", len(compressed_on) <= agent.MAX_TOOL_OUTPUT)

agent.configure_compression(False)
disabled = agent._clip_tool_text(long_text)
check("disable uses hard clip", "truncated" in disabled, disabled[-60:])
check("hard clip keeps head", disabled.startswith("x" * 100))

# Turning compression back on must not reset the budget.
agent.configure_compression(True, 1500)
check("budget applied", agent.compression_limit() == 1500)
agent.configure_compression(False)
check("budget survives off", agent.compression_limit() == 1500)
agent.configure_compression(True)
check("budget survives on", agent.compression_limit() == 1500)
check("budget honoured", len(agent._clip_tool_text("y" * 9000)) <= 1500)

agent.configure_compression(None, None)
check("budget reset", agent.compression_limit() == agent.MAX_TOOL_OUTPUT)
check("still enabled after reset", agent.compression_enabled() is True)

# --- /compress command ----------------------------------------------------- #

class FakeArgs:
    compress_enabled = True
    compress_limit = None


args = FakeArgs()
agent.run_compress_command(args, ["/compress"])
agent.run_compress_command(args, ["/compress", "off"])
check("/compress off", agent.compression_enabled() is False)
agent.run_compress_command(args, ["/compress", "on"])
check("/compress on", agent.compression_enabled() is True)

agent.run_compress_command(args, ["/compress", "limit", "2000"])
check("/compress limit", agent.compression_limit() == 2000 and args.compress_limit == 2000)
agent.run_compress_command(args, ["/compress", "limit", "5"])
check("/compress rejects tiny limit", agent.compression_limit() == 2000)
agent.run_compress_command(args, ["/compress", "limit", "default"])
check("/compress limit default", agent.compression_limit() == agent.MAX_TOOL_OUTPUT)
agent.run_compress_command(args, ["/compress", "bogus"])
check("/compress survives bad action", agent.compression_limit() == agent.MAX_TOOL_OUTPUT)

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("all checks passed")
