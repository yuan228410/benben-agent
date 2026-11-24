"""Tests for list_dir size rendering, ordering and compatibility."""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PATH = HERE.parent / "benben-agent.py"


def load_agent():
    spec = importlib.util.spec_from_file_location("benben_agent_under_test", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = load_agent()

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(("ok   " if ok else "FAIL ") + label + ((" -> " + detail) if detail and not ok else ""))


def human_size_cases():
    cases = {
        0: "0 B",
        63: "63 B",
        1023: "1023 B",
        1024: "1.00 KB",
        1067: "1.04 KB",
        84162: "82.2 KB",
        1074181: "1.02 MB",
    }
    for size, expected in cases.items():
        got = agent._human_size(size)
        check(f"human_size {size} -> {expected}", got == expected, got)


def build_tree(root):
    os.makedirs(os.path.join(root, "sub"))
    os.makedirs(os.path.join(root, "zzz_empty"))
    for name, size in (("big.bin", 5000), ("small.txt", 10), ("mid.txt", 1500)):
        with open(os.path.join(root, name), "wb") as handle:
            handle.write(b"x" * size)
    for index in range(3):
        with open(os.path.join(root, "sub", f"f{index}.txt"), "w") as handle:
            handle.write("y")


def list_dir_cases():
    root = tempfile.mkdtemp()
    try:
        build_tree(root)
        workspace = agent.Workspace(root=root)

        default_output = workspace.list_dir(".")
        check(
            "default shows human-readable size",
            "big.bin (4.88 KB)" in default_output,
            default_output,
        )
        check(
            "default shows directory entry count",
            "sub/ (3 items)" in default_output,
            default_output,
        )
        check(
            "default is name order",
            default_output.splitlines()[0].startswith("big.bin"),
            default_output,
        )

        sized = workspace.list_dir(".", sort_by="size")
        lines = [line for line in sized.splitlines() if not line.endswith("/") and "items" not in line]
        sizes_desc = [line.split("(")[0].strip() for line in lines]
        check("size order puts largest first", sizes_desc[0] == "big.bin", sized)
        check("size order is descending", sizes_desc == ["big.bin", "mid.txt", "small.txt"], sized)
        check(
            "directories last in size order",
            sized.splitlines()[-1].startswith("sub/") or sized.splitlines()[-1].startswith("zzz_empty/"),
            sized,
        )

        plain = workspace.list_dir(".", with_size=False)
        check("with_size False hides sizes", "big.bin" in plain and "KB" not in plain, plain)
        check("with_size False keeps dir marker", "sub/" in plain, plain)

        try:
            workspace.list_dir(".", sort_by="bogus")
            check("invalid sort_by rejected", False, "no error raised")
        except agent.ToolError as exc:
            check("invalid sort_by rejected", "sort_by" in str(exc), str(exc))

        missing = os.path.join(root, "nope")
        try:
            workspace.list_dir(missing)
            check("missing directory rejected", False, "no error raised")
        except agent.ToolError:
            check("missing directory rejected", True)
    finally:
        shutil.rmtree(root)


def empty_dir_case():
    root = tempfile.mkdtemp()
    try:
        workspace = agent.Workspace(root=root)
        check("empty directory message", workspace.list_dir(".") == "(empty directory)")
    finally:
        shutil.rmtree(root)


def truncation_case():
    root = tempfile.mkdtemp()
    try:
        for index in range(305):
            open(os.path.join(root, f"f{index:03d}.txt"), "w").close()
        workspace = agent.Workspace(root=root)
        output = workspace.list_dir(".")
        check("truncates at 300 entries", "entries total" in output, output[-120:])
        check("truncation reports real count", "305" in output, output[-120:])
    finally:
        shutil.rmtree(root)


def schema_case():
    root = tempfile.mkdtemp()
    try:
        workspace = agent.Workspace(root=root)
        schemas = {item["function"]["name"]: item for item in agent.tool_schemas(workspace)}
        params = schemas["list_dir"]["function"]["parameters"]["properties"]
        check("schema has with_size", "with_size" in params)
        check("schema has sort_by", "sort_by" in params)
    finally:
        shutil.rmtree(root)


human_size_cases()
list_dir_cases()
empty_dir_case()
truncation_case()
schema_case()

failures = [label for label, ok, _ in results if not ok]
print()
if failures:
    print(f"FAILURES: {len(failures)}")
    for label in failures:
        print("  -", label)
    sys.exit(1)
print("FAILURES: none")
