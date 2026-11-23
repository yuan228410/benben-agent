import importlib.util
import os
import tempfile
import time
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


base = tempfile.mkdtemp(prefix="memory-cleanup-")
memory = agent.MemoryService(SimpleNamespace(model="m"), base_dir=base)
os.makedirs(memory.base_dir, exist_ok=True)
with open(memory.memory_path, "w", encoding="utf-8") as handle:
    handle.write(
        "# 长期记忆\n\n"
        "- [2026-11-01] alpha\n"
        "- [2026-11-02] beta\n"
        "- [2026-11-03] alpha\n"
        "- [2026-11-04] gamma\n"
    )

dry = memory.dedupe_memory(dry_run=True)
check("dedupe dry stats", dry["before"] == 4 and dry["removed"] == 1, dry)
check(
    "dedupe dry keeps file",
    len(memory._memory_entries()) == 4,
    memory._memory_entries(),
)
deduped = memory.dedupe_memory(dry_run=False)
check("dedupe stats", deduped["before"] == 4 and deduped["after"] == 3, deduped)
entries = memory._memory_entries()
check("dedupe result", len(entries) == 3, entries)
alphas = [entry for entry in entries if entry[2] == "alpha"]
check(
    "dedupe latest kept",
    len(alphas) == 1 and alphas[0][1] == "2026-11-03",
    entries,
)

pruned = memory.prune_memory(keep_count=2, dry_run=False)
check("prune keep count", pruned["before"] == 3 and pruned["after"] == 2, pruned)
check("prune count result", len(memory._memory_entries()) == 2, memory._memory_entries())

with open(memory.memory_path, "w", encoding="utf-8") as handle:
    handle.write(
        "# 长期记忆\n\n"
        "- [2020-01-01] old\n"
        "- [2026-01-01] newer\n"
        "- [2026-11-01] newest\n"
    )
aged = memory.prune_memory(keep_days=30, dry_run=True)
check("prune age dry", aged["removed"] >= 1, aged)
check("prune age keeps", len(memory._memory_entries()) == 3, memory._memory_entries())
memory.prune_memory(keep_days=30, dry_run=False)
check("prune age result", len(memory._memory_entries()) < 3, memory._memory_entries())

os.makedirs(memory.episodes_dir, exist_ok=True)
old_episode = os.path.join(memory.episodes_dir, "2020-01-01.md")
new_episode = os.path.join(memory.episodes_dir, time.strftime("%Y-%m-%d") + ".md")
with open(old_episode, "w", encoding="utf-8") as handle:
    handle.write("# old\n")
with open(new_episode, "w", encoding="utf-8") as handle:
    handle.write("# new\n")
episodes = memory.prune_episodes(30, dry_run=True)
check("episode prune dry", episodes["removed"] == 1, episodes)
check("episode prune keeps", os.path.exists(old_episode))
memory.prune_episodes(30, dry_run=False)
check("episode prune removes", not os.path.exists(old_episode))
check("episode prune keeps new", os.path.exists(new_episode))

if failures:
    print("\nFAILURES:", ", ".join(failures))
    raise SystemExit(1)
print("\nFAILURES: none")
