"""检查 read_file_outline 的解析缓存。

ast.parse 在 3 万行的文件上约 0.5 s，而模型经常反复看同一个文件，缓存按
(realpath, mtime_ns, size) 失效。关心的点：

- 命中后结果与未缓存时完全一致
- 不同 max_items 之间不能串结果（缓存的是解析结果，不是最终字符串）
- 文件被修改后必须重新解析
- 缓存上限生效，不会无限增长
"""
from pathlib import Path
import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


def load_agent():
    spec = importlib.util.spec_from_file_location("benben_agent_mod", AGENT)
    module = importlib.util.module_from_spec(spec)
    sys.argv = ["benben-agent.py"]
    spec.loader.exec_module(module)
    return module


mod = load_agent()
tmp = tempfile.mkdtemp(prefix="outline-cache-")
ws = mod.Workspace(tmp)

try:
    check("cache helpers exist",
          hasattr(mod, "_outline_cache_get")
          and hasattr(mod, "_outline_cache_put")
          and hasattr(mod, "_outline_cache_clear"))

    # --- 基本命中 ---
    py = os.path.join(tmp, "sample.py")
    Path(py).write_text(
        "".join(f"def f{i}():\n    pass\n\n" for i in range(30)),
        encoding="utf-8",
    )
    mod._outline_cache_clear()
    first = ws.read_file_outline("sample.py")
    second = ws.read_file_outline("sample.py")
    check("first call parses", "30 declarations" in first, first[:120])
    check("second call returns identical text", first == second)
    check(
        "second call hits cache",
        second.endswith(first.split("\n", 1)[1]) and second.split("\n", 1)[0]
        == first.split("\n", 1)[0],
    )

    # --- max_items 不能串 ---
    small = ws.read_file_outline("sample.py", 5)
    large = ws.read_file_outline("sample.py", 400)
    small_again = ws.read_file_outline("sample.py", 5)
    check("max_items=5 trims to 5 declarations",
          small.count("def f") == 5, small.count("def f"))
    check("max_items=400 keeps all 30",
          large.count("def f") == 30, large.count("def f"))
    check("same max_items reproducible", small == small_again)
    check("different max_items differ", small != large)

    # --- 文件修改后失效 ---
    Path(py).write_text(
        "def only_one():\n    pass\n", encoding="utf-8"
    )
    updated = ws.read_file_outline("sample.py")
    check("modified file is re-parsed",
          "1 declaration" in updated, updated.split("\n")[0])

    # --- size 变化也算失效（mtime 精度不足时的兜底）---
    Path(py).write_text("def a():\n    pass\n", encoding="utf-8")
    a = ws.read_file_outline("sample.py")
    Path(py).write_text("def a():\n    pass\ndef b():\n    pass\n", encoding="utf-8")
    b = ws.read_file_outline("sample.py")
    check("size change invalidates", a != b, f"{a[:40]} vs {b[:40]}")

    # --- 上限淘汰 ---
    mod._outline_cache_clear()
    for i in range(mod._OUTLINE_CACHE_MAX + 25):
        p = os.path.join(tmp, f"m{i}.py")
        Path(p).write_text(f"def fn{i}():\n    pass\n", encoding="utf-8")
        ws.read_file_outline(f"m{i}.py")
    check(
        "cache respects max size",
        len(mod._OUTLINE_CACHE) <= mod._OUTLINE_CACHE_MAX,
        len(mod._OUTLINE_CACHE),
    )

    # --- 并发安全 ---
    mod._outline_cache_clear()
    conc = os.path.join(tmp, "conc.py")
    Path(conc).write_text(
        "".join(f"def c{i}():\n    pass\n\n" for i in range(50)),
        encoding="utf-8",
    )
    results = []
    errors = []

    def worker():
        try:
            results.append(ws.read_file_outline("conc.py"))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("concurrent calls do not raise", not errors, errors[:2])
    check("concurrent calls agree", len(set(results)) == 1, len(set(results)))

    # --- 目录 / 缺失文件仍按原样报错 ---
    try:
        ws.read_file_outline("no-such-file.py")
        check("missing file raises", False)
    except mod.ToolError:
        check("missing file raises", True)

    # --- 二进制文件仍然拒绝 ---
    binf = os.path.join(tmp, "bin.dat")
    with open(binf, "wb") as handle:
        handle.write(b"\x00\x01\x02binary")
    try:
        ws.read_file_outline("bin.dat")
        check("binary file rejected", False)
    except mod.ToolError as exc:
        check("binary file rejected", "binary" in str(exc), str(exc))

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all outline cache checks passed")
