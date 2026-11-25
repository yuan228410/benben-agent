"""检查 read_files 和编辑后 diff 回显。

这两个能力都为了减少模型往返：
- read_files：一次读多个文件。以前每个文件一次 read_file，每次都是一个
  额外的模型轮次，还要各自把调用和结果塞进上下文。
- unified diff：edit_file / multi_edit 直接回显改了什么，省掉事后单独跑
  git diff 的那一轮。
"""
from pathlib import Path
import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("agent_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)

tmp = tempfile.mkdtemp(prefix="readfiles-")
ws = mod.Workspace(tmp)


def write(name, text):
    path = os.path.join(tmp, name)
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    Path(path).write_text(text, encoding="utf-8")
    return path


try:
    write("a.py", "def a():\n    return 1\n")
    write("b.py", "def b():\n    return 2\n")
    write("c.txt", "plain text\nsecond line\n")

    # ---------- read_files ----------
    out = ws.read_files(["a.py", "b.py"], limit=10)
    check("read_files includes both headers",
          "=== a.py ===" in out and "=== b.py ===" in out, out[:200])
    check("read_files shows content", "return 1" in out and "return 2" in out, out[:200])
    check("read_files numbers lines", "    1 | def a():" in out, out[:200])

    out = ws.read_files(["a.py", "b.py"], limit=10, numbered=False)
    check("read_files numbered=false drops numbers",
          "    1 | " not in out and "def a():" in out, out[:200])

    # 单文件失败不影响其他
    out = ws.read_files(["a.py", "missing.py", "b.py"], limit=5)
    check("read_files reports missing file inline",
          "error:" in out and "missing.py" in out, out[:300])
    check("read_files still reads the others",
          "return 1" in out and "return 2" in out, out[:300])

    # 目录当文件
    os.makedirs(os.path.join(tmp, "sub"), exist_ok=True)
    out = ws.read_files(["sub", "a.py"], limit=5)
    check("read_files reports directory inline",
          "error:" in out and "sub" in out, out[:300])

    # 非字符串路径
    out = ws.read_files(["a.py", 123], limit=5)  # type: ignore[list-item]
    check("read_files skips invalid path types", "invalid path" in out, out[:300])

    # 数量上限
    try:
        ws.read_files([f"f{i}.py" for i in range(mod.MAX_READ_FILES + 1)])
        check("read_files rejects too many paths", False)
    except mod.ToolError as exc:
        check("read_files rejects too many paths",
              str(mod.MAX_READ_FILES) in str(exc), str(exc))

    # 空列表
    try:
        ws.read_files([])
        check("read_files rejects empty list", False)
    except mod.ToolError:
        check("read_files rejects empty list", True)

    # 行预算在文件之间共享：一个巨大文件不能把后面的全挤没
    for i in range(4):
        write(f"big{i}.py", "\n".join(f"line{j}" for j in range(2000)))
    out = ws.read_files([f"big{i}.py" for i in range(4)], limit=2000)
    body_lines = out.count("\n") + 1
    check("read_files shares the line budget",
          body_lines <= mod.MAX_FILE_LINES + len([f for f in range(4)]) * 2 + 5,
          f"{body_lines} lines")
    check("read_files marks skipped files",
          "skipped" in out, out[-300:])

    # ---------- 不传 limit 时的动态分配 ----------
    for i in range(5):
        write(f"huge{i}.py", "\n".join(f"row{j}" for j in range(2000)))

    out = ws.read_files([f"huge{i}.py" for i in range(3)])
    per_file = []
    for i in range(3):
        seg = out.split(f"=== huge{i}.py ===")[1].split("===")[0]
        per_file.append(seg.count("\n"))
    # 3000 / 3 = 1000 行/文件，而不是旧的固定 100 行
    check("read_files distributes budget when limit omitted",
          all(n > 900 for n in per_file), per_file)
    check("read_files dynamic split stays within total budget",
          sum(per_file) <= mod.MAX_FILE_LINES + 10, sum(per_file))

    out = ws.read_files([f"huge{i % 5}.py" for i in range(20)])
    check("read_files respects MAX_FILE_LINES with 20 paths",
          out.count("\n") + 1 <= mod.MAX_FILE_LINES + 30, out.count("\n") + 1)

    out = ws.read_files(["huge0.py"])
    check("read_files single path uses the whole budget",
          out.count("\n") + 1 > 900, out.count("\n") + 1)

    out = ws.read_files(["huge0.py", "huge1.py"], limit=50)
    check("read_files explicit limit still wins",
          out.count("\n") + 1 <= 110, out.count("\n") + 1)

    # ---------- unified_diff_text ----------
    check("diff empty when identical",
          mod.unified_diff_text("abc", "abc", "f.py") == "")

    patch = mod.unified_diff_text("a\nb\nc\n", "a\nB\nc\n", "f.py")
    check("diff has file headers",
          "--- a/f.py" in patch and "+++ b/f.py" in patch, patch)
    check("diff shows removal", "-b" in patch, patch)
    check("diff shows addition", "+B" in patch, patch)
    check("diff keeps context", " a" in patch and " c" in patch, patch)

    # CRLF 不应该把 \r 漏到输出里
    patch = mod.unified_diff_text("a\r\nb\r\n", "a\r\nB\r\n", "f.py")
    check("diff strips carriage returns", "\r" not in patch, repr(patch))

    # 超大 diff 要截断而不是撑爆上下文
    big_before = "\n".join(f"line{i}" for i in range(800))
    big_after = "\n".join(f"LINE{i}" for i in range(800))
    patch = mod.unified_diff_text(big_before, big_after, "big.txt")
    check("huge diff is truncated",
          len(patch) <= mod.DIFF_MAX_CHARS + 120, len(patch))
    check("truncated diff says so", "truncated" in patch, patch[-120:])

    # ---------- edit_file 回显 diff ----------
    write("edit_me.py", "alpha = 1\nbeta = 2\n")
    result = ws.edit_file("edit_me.py", "alpha = 1", "alpha = 99")
    check("edit_file reports the edit", "edited" in result, result)
    check("edit_file includes a diff", "@@" in result, result)
    check("edit_file diff shows old line", "-alpha = 1" in result, result)
    check("edit_file diff shows new line", "+alpha = 99" in result, result)
    check("edit_file actually wrote the file",
          Path(os.path.join(tmp, "edit_me.py")).read_text(encoding="utf-8")
          == "alpha = 99\nbeta = 2\n")

    # replace_all
    write("multi.py", "x = 1\nx = 1\n")
    result = ws.edit_file("multi.py", "x = 1", "x = 2", replace_all=True)
    check("replace_all reports count", "2 replacements" in result, result)
    check("replace_all diff shows both hunks",
          result.count("-x = 1") >= 1, result)

    # ---------- multi_edit 回显 diff ----------
    write("m1.py", "one = 1\n")
    write("m2.py", "two = 2\n")
    result = ws.multi_edit(edits=[
        {"path": "m1.py", "old_string": "one = 1", "new_string": "one = 111"},
        {"path": "m2.py", "old_string": "two = 2", "new_string": "two = 222"},
    ])
    check("multi_edit reports both files",
          "m1.py" in result and "m2.py" in result, result[:300])
    check("multi_edit includes diffs", result.count("@@") >= 2, result)
    check("multi_edit diff shows each change",
          "+one = 111" in result and "+two = 222" in result, result)

    # 失败时不应写入任何文件，也不应有 diff
    write("keep.py", "keep = 1\n")
    try:
        ws.multi_edit(edits=[
            {"path": "keep.py", "old_string": "keep = 1", "new_string": "keep = 2"},
            {"path": "keep.py", "old_string": "no-such-text", "new_string": "x"},
        ])
        check("multi_edit rolls back on failure", False)
    except mod.ToolError:
        check("multi_edit rolls back on failure", True)
    check("multi_edit left the file untouched",
          Path(os.path.join(tmp, "keep.py")).read_text(encoding="utf-8") == "keep = 1\n")

    # ---------- schema / 注册 ----------
    tools = mod.tool_schemas(ws)
    names = [t["function"]["name"] for t in tools]
    check("read_files is exposed as a tool", "read_files" in names, names)
    check("read_files is read-only",
          "read_files" in mod.READ_ONLY_TOOL_NAMES, mod.READ_ONLY_TOOL_NAMES)
    rf = next(t for t in tools if t["function"]["name"] == "read_files")
    check("read_files requires paths",
          rf["function"]["parameters"]["required"] == ["paths"],
          rf["function"]["parameters"]["required"])
    check("read_file description mentions the limit",
          str(mod.MAX_FILE_LINES) in
          next(t for t in tools if t["function"]["name"] == "read_file")
          ["function"]["description"])
    check("read_file points at read_files",
          "read_files" in
          next(t for t in tools if t["function"]["name"] == "read_file")
          ["function"]["description"])

    # 只读模式下也应可见
    ws.read_only = True
    ro_names = [t["function"]["name"] for t in mod.tool_schemas(ws)]
    check("read_files available in read-only mode", "read_files" in ro_names, ro_names)
    ws.read_only = False

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all read_files / diff checks passed")
