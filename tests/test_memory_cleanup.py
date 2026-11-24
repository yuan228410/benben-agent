"""Long-term memory upkeep: duplicate detection and budget-driven merging.

There is deliberately no time-based deletion: a year-old preference is often
still valid while yesterday's note may already be stale, so compaction asks
the model what still holds and always leaves a backup.
"""
import importlib.util
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

AGENT = str(Path(__file__).resolve().parents[1] / "benben-agent.py")
spec = importlib.util.spec_from_file_location("agent", AGENT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


class FakeClient:
    """Stands in for the summarizer; returns whatever the test needs."""

    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.last_usage = {}
        self.last_finish_reason = "stop"
        self.last_reasoning_ignored = False
        self.requests = []

    def chat(self, messages, tools):
        self.requests.append(messages)
        if self.error is not None:
            raise self.error
        return {"role": "assistant", "content": self.content}


def make_memory():
    base = tempfile.mkdtemp(prefix="memory-cleanup-")
    memory = agent.MemoryService(SimpleNamespace(model="m"), base_dir=base)
    os.makedirs(memory.base_dir, exist_ok=True)
    return memory


def seed(memory, lines):
    with open(memory.memory_path, "w", encoding="utf-8") as handle:
        handle.write("# 长期记忆\n\n" + "\n".join(lines) + "\n")


def backups(memory):
    folder = os.path.dirname(memory.memory_path)
    name = os.path.basename(memory.memory_path)
    return sorted(
        entry for entry in os.listdir(folder) if entry.startswith(name + ".bak-")
    )


# -- dedupe --------------------------------------------------------------- #
memory = make_memory()
seed(
    memory,
    [
        "- [2026-11-01] alpha",
        "- [2026-11-02] beta",
        "- [2026-11-03] alpha",
        "- [2026-11-04] gamma",
    ],
)

dry = memory.dedupe_memory(dry_run=True)
check("dedupe dry stats", dry["before"] == 4 and dry["removed"] == 1, dry)
check("dedupe dry keeps file", len(memory._memory_entries()) == 4)
deduped = memory.dedupe_memory(dry_run=False)
check("dedupe stats", deduped["before"] == 4 and deduped["after"] == 3, deduped)
entries = memory._memory_entries()
check("dedupe latest kept", [e for e in entries if e[2] == "alpha"][0][1] == "2026-11-03")

# Punctuation and spacing are noise when deciding whether something is a
# repeat, so these three count as one entry.
seed(
    memory,
    [
        "- [2026-11-01] 用户偏好简洁的中文回答",
        "- [2026-11-02] 用户，偏好简洁的中文回答！",
        "- [2026-11-03] 用户 偏好 简洁的中文回答。",
    ],
)
normalized = memory.dedupe_memory(dry_run=False)
check("dedupe normalized", normalized["after"] == 1, normalized)
check(
    "dedupe normalized keeps newest",
    memory._memory_entries()[0][1] == "2026-11-03",
    memory._memory_entries(),
)

# -- remember rejects near-duplicates -------------------------------------- #
memory = make_memory()
first = memory.remember("用户偏好简洁的中文回答", "偏好")
second = memory.remember("用户，偏好简洁的中文回答！", "偏好")
check("remember first", first.startswith("remembered in MEMORY.md"), first)
check("remember duplicate", second.startswith("already remembered"), second)
check("remember single entry", len(memory._memory_entries()) == 1, memory._memory_entries())

# -- compact: merge the old tail, keep the newest verbatim ----------------- #
memory = make_memory()
seed(
    memory,
    [
        "- [2026-11-01] [偏好] 用户偏好简洁的中文回答",
        "- [2026-11-02] [环境] 工作区使用 pwsh",
        "- [2026-11-03] 项目用 Flask 重构",
        "- [2026-11-04] 项目已改为 FastAPI",
        "- [2026-11-20] [项目] benben-agent 单文件",
        "- [2026-11-21] [项目] 只使用标准库",
    ],
)
client = FakeClient(
    "- [2026-11-01] [偏好] 用户偏好简洁的中文回答\n"
    "- [2026-11-02] [环境] 工作区使用 pwsh\n"
    "- [2026-11-04] 项目已改为 FastAPI\n"
)
memory._make_client = lambda: client
stats = memory.compact_memory(keep_recent=2)
check("compact before", stats["before"] == 6, stats)
check("compact after", stats["after"] == 5, stats)
check("compact merged", stats["merged"] == 3, stats)
check("compact not skipped", stats["skipped"] is False, stats)
check("compact wrote backup", os.path.exists(stats["backup"]), stats)
check("compact single backup", len(backups(memory)) == 1, backups(memory))
after_entries = memory._memory_entries()
check("compact entry count", len(after_entries) == 5, after_entries)
check(
    "compact drops superseded",
    all("Flask" not in text for _i, _d, text in after_entries),
    after_entries,
)
check(
    "compact keeps recent verbatim",
    [text for _i, _d, text in after_entries][-2:] == ["benben-agent 单文件", "只使用标准库"],
    after_entries,
)
check(
    "compact keeps tags",
    "- [2026-11-20] [项目] benben-agent 单文件" in memory._read_text(memory.memory_path),
    memory._read_text(memory.memory_path),
)
check(
    "compact prompt has tags",
    "[偏好]" in client.requests[0][0]["content"],
    client.requests[0][0]["content"][:200],
)
check(
    "compact header kept",
    memory._read_text(memory.memory_path).startswith("# 长期记忆"),
    memory._read_text(memory.memory_path)[:40],
)

# -- compact: nothing to do ------------------------------------------------ #
memory = make_memory()
seed(memory, ["- [2026-11-01] only", "- [2026-11-02] two"])
memory._make_client = lambda: FakeClient("- [2026-11-01] only")
skipped = memory.compact_memory(keep_recent=5)
check("compact skipped", skipped["skipped"] is True, skipped)
check("compact skipped no backup", backups(memory) == [], backups(memory))
check("compact skipped keeps entries", len(memory._memory_entries()) == 2)

# -- compact: model refuses to shorten ------------------------------------- #
memory = make_memory()
seed(memory, ["- [2026-11-01] a", "- [2026-11-02] b", "- [2026-11-03] c"])
memory._make_client = lambda: FakeClient(
    "- [2026-11-01] a\n- [2026-11-02] b\n- [2026-11-03] c"
)
before_text = memory._read_text(memory.memory_path)
no_gain = memory.compact_memory(keep_recent=1)
check("compact no gain skipped", no_gain["skipped"] is True, no_gain)
check("compact no gain keeps file", memory._read_text(memory.memory_path) == before_text)
check("compact no gain no backup", backups(memory) == [], backups(memory))

# -- compact: unusable reply keeps the file intact ------------------------- #
memory = make_memory()
seed(memory, ["- [2026-11-01] a", "- [2026-11-02] b", "- [2026-11-03] c"])
memory._make_client = lambda: FakeClient("这些记忆都还有效，不需要合并。")
before_text = memory._read_text(memory.memory_path)
try:
    memory.compact_memory(keep_recent=1)
    check("compact unusable raises", False)
except agent.ToolError:
    check("compact unusable raises", True)
check("compact unusable keeps file", memory._read_text(memory.memory_path) == before_text)
check("compact unusable no backup", backups(memory) == [], backups(memory))

# -- automatic compaction once the file passes the budget ------------------ #
memory = make_memory()
seed(
    memory,
    [
        "- [2026-11-01] alpha",
        "- [2026-11-02] beta",
        "- [2026-11-03] gamma",
        "- [2026-11-04] delta",
    ],
)
memory._make_client = lambda: FakeClient("- [2026-11-01] alpha\n- [2026-11-02] beta")
agent.MEMORY_COMPACT_CHARS = 10
result = memory.remember("新的偏好事实", "偏好")
check("auto compact reported", "compacted MEMORY.md" in result, result)
check("auto compact ran", len(backups(memory)) == 1, backups(memory))
check(
    "auto compact keeps new entry",
    any("新的偏好事实" in text for _i, _d, text in memory._memory_entries()),
    memory._memory_entries(),
)

# -- automatic compaction never breaks remember ---------------------------- #
memory = make_memory()
seed(memory, ["- [2026-11-01] alpha", "- [2026-11-02] beta"])
memory._make_client = lambda: FakeClient(error=agent.ChatError("gateway down"))
survived = memory.remember("仍然要记住的这一条", "")
check("auto compact failure tolerated", survived.startswith("remembered in MEMORY.md"), survived)
check(
    "auto compact failure keeps entry",
    any("仍然要记住的这一条" in text for _i, _d, text in memory._memory_entries()),
    memory._memory_entries(),
)

# -- hard delete is gone --------------------------------------------------- #
check("prune_memory removed", not hasattr(memory, "prune_memory"))
check("prune_episodes removed", not hasattr(memory, "prune_episodes"))

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
