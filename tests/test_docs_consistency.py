"""检查文档与代码是否一致。

这类不一致反复出现过：README 说 read_file 上限 1000 行而代码是 3000；
工具表写着 35 个工具，去掉后台任务后实际是 31 个；tests/README.md 只列了
38 个测试文件而实际有 51 个。都是改了代码忘改文档。

这里把几个能机械校验的点固定下来：任何一边改了而另一边没跟上，测试就会失败。
"""
from pathlib import Path
import importlib.util
import json
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
README = ROOT / "README.md"
TESTS_README = ROOT / "tests" / "README.md"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("agent_doc_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)

readme = README.read_text(encoding="utf-8")

# ---------- 1. 工具表与 tool_schemas 一致 ----------
# 全功能开启：把各子系统的开关都打开，取出全部工具名
ws = mod.Workspace(tempfile.mkdtemp(prefix="doc-check-"))
ws.allow_shell = True
ws.skills_enabled = True
ws.allow_skill_scripts = True
ws.skills = {
    "demo": {
        "name": "demo",
        "description": "demo",
        "has_resources": True,
        "has_scripts": True,
    }
}
ws.skill_manager = object()
ws.memory = object()
ws.scheduler = object()
ws.plan_mode = True
all_tools = {schema["function"]["name"] for schema in mod.tool_schemas(ws)}

# README 的工具表：以 "| `tool_name` |" 开头的行，且该行剩余部分像描述
tool_table = set()
in_tool_section = False
for line in readme.splitlines():
    if line.startswith("## 内置工具"):
        in_tool_section = True
        continue
    if in_tool_section and line.startswith("## "):
        break
    if not in_tool_section:
        continue
    match = re.match(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|", line)
    if match:
        tool_table.add(match.group(1))

check("README tool table is non-empty", len(tool_table) > 20, len(tool_table))
missing_in_readme = sorted(all_tools - tool_table)
extra_in_readme = sorted(tool_table - all_tools)
check("every tool is documented", not missing_in_readme, missing_in_readme)
check("no documented tool was removed", not extra_in_readme, extra_in_readme)

# ---------- 2. README 声明的工具总数 ----------
stated = re.search(r"全部功能开启时共 (\d+) 个工具", readme)
check("README states a tool count", stated is not None)
if stated:
    claimed = int(stated.group(1))
    check("stated tool count matches code", claimed == len(all_tools),
          f"README {claimed} vs code {len(all_tools)}")

# ---------- 3. read_file 行数上限 ----------
for value in (str(mod.MAX_FILE_LINES),):
    check(f"README mentions MAX_FILE_LINES={value}",
          value in readme, f"{value} not found in README")
# 旧的错误值不应再出现
check("README no longer claims 1000 lines",
      "单次最多 1000 行" not in readme and "硬上限 1000" not in readme)

# ---------- 4. tests/README.md 覆盖所有测试文件 ----------
actual_tests = {p.stem for p in (ROOT / "tests").glob("test_*.py")}
tests_readme = TESTS_README.read_text(encoding="utf-8")
documented = set(re.findall(r"test_[a-z0-9_]+", tests_readme))
undocumented = sorted(actual_tests - documented)
stale = sorted(t for t in documented - actual_tests if t.startswith("test_"))
check("every test file is listed in tests/README.md", not undocumented, undocumented)
check("tests/README.md has no removed test files", not stale, stale)

# ---------- 5. README 里的 --flag 都真实存在 ----------
import subprocess

try:
    help_text = subprocess.run(
        [sys.executable, str(AGENT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
    help_text = ""
    print(f"     could not run --help: {exc!r}")

check("--help produced output", bool(help_text), len(help_text))
if help_text:
    help_flags = set(re.findall(r"(--[a-z][a-z0-9-]+)", help_text))
    readme_flags = set(re.findall(r"`(--[a-z][a-z0-9-]+)", readme))
    # 允许 README 里举的例子（如 wget --spider、自定义 --prompt）
    allowed = {"--prompt", "--tool", "--spider", "--image"}
    ghost_flags = sorted(readme_flags - help_flags - allowed)
    check("README CLI flags all exist", not ghost_flags, ghost_flags)

# ---------- 6. 已移除的功能不应留下名字 ----------
removed = ["job_status", "job_output", "job_cancel", "list_jobs"]
leftovers = [name for name in removed if name in readme]
check("removed background-job tools are gone from README", not leftovers, leftovers)

# ---------- 7. 模型上下文的查找顺序与覆盖语义 ----------
# 这段文档曾经写错过：只说 config.json 里的 model_info，漏掉 models.json
# 会先命中，读者会以为没写 model_info 就走 fallback。
spec = importlib.util.spec_from_file_location("agent_docs_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)

# README 声明的查找步骤要按顺序出现（限定在「查找顺序」那一节内，
# 否则 "fallback" 会在文档别处先命中）
section_start = readme.find("查找顺序")
section_end = readme.find("当前兜底上下文为")
check("README has a model lookup section",
      section_start >= 0 and section_end > section_start,
      f"{section_start}..{section_end}")
section = readme[section_start:section_end] if section_start >= 0 else ""
steps = [
    "provider/model",
    "键名等于模型名",
    "前缀/模型名",
    "fallback",
    "model_info",
]
positions = [section.find(s) for s in steps]
check("README documents the model lookup order in order",
      all(p >= 0 for p in positions) and positions == sorted(positions),
      list(zip(steps, positions)))
check("README explains model_info is a per-field overlay",
      "逐字段覆盖" in readme, "missing 逐字段覆盖")
check("README warns models.json wins over an absent model_info",
      "没有 `model_info` 也不影响" in readme, "missing the models.json note")

# 文档里写的兜底数字必须等于代码里的
readme_fallback = re.search(r"兜底上下文为 \*\*(\d+) tokens\*\*", readme)
check("README states the fallback context", readme_fallback is not None)
if readme_fallback:
    stated = int(readme_fallback.group(1))
    info = mod.ModelRegistry(use_file=False).resolve("custom", "no-such-model")
    check("fallback context in README matches code",
          stated == info.context_window, f"{stated} != {info.context_window}")
    stated_out = re.search(r"最大输出 `(\d+)`", readme)
    if stated_out:
        check("fallback max output in README matches code",
              int(stated_out.group(1)) == info.max_output_tokens,
              f"{stated_out.group(1)} != {info.max_output_tokens}")

# 覆盖语义：model_info 只改给出的字段，其余沿用 models.json
with tempfile.TemporaryDirectory() as tmp:
    models_path = Path(tmp) / "models.json"
    models_path.write_text(json.dumps({
        "version": 1,
        "fallback": {"name": "fallback", "context_window": 111111,
                     "max_output_tokens": 8192},
        "models": {"custom/known-model": {"name": "Known",
                                          "context_window": 222222,
                                          "max_output_tokens": 4096}},
    }), encoding="utf-8")
    registry = mod.ModelRegistry(str(models_path))
    mixed = registry.resolve("custom", "known-model", "", {"max_output_tokens": 999})
    check("model_info overrides only the fields it sets",
          mixed.context_window == 222222 and mixed.max_output_tokens == 999,
          f"ctx={mixed.context_window} out={mixed.max_output_tokens}")

    # models.json 命中时，不传 model_info 也应取到该条目而不是 fallback
    plain = registry.resolve("custom", "known-model", "", None)
    check("models.json entry is used without any model_info",
          plain.context_window == 222222, plain.context_window)

    # 后缀匹配
    suffix = registry.resolve("other", "known-model", "", None)
    check("model name suffix match works",
          suffix.context_window == 222222, suffix.context_window)

    # 都没命中才用 fallback
    miss = registry.resolve("custom", "missing", "", None)
    check("fallback is used only when nothing matches",
          miss.context_window == 111111, miss.context_window)
    shutil.rmtree(tmp, ignore_errors=True)

# ---------- 8. 压缩文档的数值与代码一致 ----------
compact_checks = [
    ("窗口的 1/5，最多 8000 token", mod.COMPACT_OUTPUT_RESERVE == 8000),
    ("85% → 70% → 50% → 35%", mod.COMPACT_SHRINK_STEPS == (0.85, 0.7, 0.5, 0.35)),
    ("默认保留最近 2 个用户回合", mod.COMPACT_DEFAULT_KEEP == 2),
]
for label, ok in compact_checks:
    check(f"compaction README matches code: {label}", ok and label in readme)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all doc consistency checks passed")
