"""Tests for search context lines and mode compatibility."""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PATH = HERE.parent / "benben-agent.py"


def load_agent():
    spec = importlib.util.spec_from_file_location("benben_agent_search_ctx", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = load_agent()

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(("ok   " if ok else "FAIL ") + label + ((" -> " + detail) if detail and not ok else ""))


SAMPLE = "".join(f"line {index}\n" for index in range(1, 21))
TARGET_LINE = 10


def build():
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "sample.txt"), "w", encoding="utf-8") as handle:
        handle.write(SAMPLE)
    return root


def no_context_case():
    root = build()
    try:
        workspace = agent.Workspace(root=root)
        out = workspace.search("line 10$", path=".")
        check("default has no context lines", out.count("sample.txt:") == 1, out)
        check("default keeps matched line", "sample.txt:10: line 10" in out, out)
    finally:
        shutil.rmtree(root)


def context_case():
    root = build()
    try:
        workspace = agent.Workspace(root=root)
        out = workspace.search("line 10$", path=".", context=2)
        for offset, mark in ((8, "<"), (9, "<"), (10, None), (11, ">"), (12, ">")):
            needle = f"sample.txt:{offset}: line {offset}"
            if mark:
                needle = f"{mark} {needle}"
            check(f"context includes line {offset}", needle in out, out)
        check("context marks match line without prefix", "sample.txt:10: line 10" in out, out)
        check("context excludes far lines", "sample.txt:7:" not in out, out)
        check("context excludes line 13", "sample.txt:13:" not in out, out)
    finally:
        shutil.rmtree(root)


def context_clamp_case():
    root = build()
    try:
        workspace = agent.Workspace(root=root)
        out = workspace.search("line 1$", path=".", context=5)
        check("context at file start does not underflow", "line 0:" not in out, out)
        check("context at file start includes line 1", "sample.txt:1: line 1" in out, out)
        out_end = workspace.search("line 20$", path=".", context=5)
        check("context at file end does not overflow", "line 21:" not in out_end, out_end)
        check("context at file end includes line 20", "sample.txt:20: line 20" in out_end, out_end)
    finally:
        shutil.rmtree(root)


def context_cap_case():
    root = build()
    try:
        workspace = agent.Workspace(root=root)
        out = workspace.search("line 10$", path=".", context=99)
        check("context capped at 20", "sample.txt:1:" in out, out)
    finally:
        shutil.rmtree(root)


def lines_mode_ignores_context():
    root = build()
    try:
        workspace = agent.Workspace(root=root)
        out = workspace.search("line 10$", path=".", output="lines", context=3)
        check("lines mode stays bare", out.strip() == "sample.txt:10", repr(out))
    finally:
        shutil.rmtree(root)


def multi_match_case():
    root = build()
    try:
        workspace = agent.Workspace(root=root)
        out = workspace.search(r"line (5|15)$", path=".", context=1)
        check("multiple matches each get context", out.count("sample.txt:5:") == 1 and out.count("sample.txt:15:") == 1, out)
        check("multi match context 4", "sample.txt:4: line 4" in out, out)
        check("multi match context 16", "sample.txt:16: line 16" in out, out)
    finally:
        shutil.rmtree(root)


def schema_case():
    root = tempfile.mkdtemp()
    try:
        workspace = agent.Workspace(root=root)
        schemas = {item["function"]["name"]: item for item in agent.tool_schemas(workspace)}
        props = schemas["search"]["function"]["parameters"]["properties"]
        check("schema exposes context", "context" in props, str(props.keys()))
    finally:
        shutil.rmtree(root)


def negative_context_case():
    root = build()
    try:
        workspace = agent.Workspace(root=root)
        out = workspace.search("line 10$", path=".", context=-5)
        check("negative context treated as zero", out.count("sample.txt:") == 1, out)
    finally:
        shutil.rmtree(root)


def dense_match_case():
    """Every line matching must not explode the output size."""
    root = tempfile.mkdtemp()
    try:
        path = os.path.join(root, "dense.txt")
        with open(path, "w", encoding="utf-8") as handle:
            for index in range(2000):
                handle.write(f"match here {index}\n")
        workspace = agent.Workspace(root=root)
        out = workspace.search("match", path=".", context=2, max_results=50)
        check("dense matches stay bounded", len(out) < 200_000, f"{len(out)} chars")
        check("dense matches stop at limit", "stopped at 50 matches" in out, out[-120:])
        fast = workspace.search("match", path=".", context=2, max_results=50)
        check("dense matches repeatable", fast.count("dense.txt:") < 500, str(fast.count("dense.txt:")))
    finally:
        shutil.rmtree(root)


def large_file_memory_case():
    """Streaming must not load the whole file into memory."""
    root = tempfile.mkdtemp()
    try:
        path = os.path.join(root, "near.txt")
        with open(path, "w", encoding="utf-8") as handle:
            index = 0
            while handle.tell() < 1_500_000:
                handle.write(f"line {index:06d} padding text here\n")
                index += 1
        workspace = agent.Workspace(root=root)
        import tracemalloc
        tracemalloc.start()
        out = workspace.search("padding", path=".", max_results=1)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        check("large file found", "near.txt:1:" in out, out[:80])
        check(
            "search memory stays flat",
            peak < 1_000_000,
            f"peak {peak / 1024 / 1024:.2f} MB",
        )
    finally:
        shutil.rmtree(root)


no_context_case()
context_case()
context_clamp_case()
context_cap_case()
lines_mode_ignores_context()
multi_match_case()
schema_case()
negative_context_case()
dense_match_case()
large_file_memory_case()

failures = [label for label, ok, _ in results if not ok]
print()
if failures:
    print(f"FAILURES: {len(failures)}")
    for label in failures:
        print("  -", label)
    sys.exit(1)
print("FAILURES: none")
