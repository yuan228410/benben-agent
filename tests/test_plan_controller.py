"""检查 PlanModeController：CLI 与 Web 共用的计划模式状态迁移。

三个操作（切换 / 保存 / 恢复）原本在 main 和 run_web 里各写了一份，
相似度最高到 93%。这类复制最容易出问题的方式是两边慢慢分叉：改了一边
忘了另一边，行为就不一致了。这里直接测共用实现本身。
"""
from pathlib import Path
import importlib.util
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


spec = importlib.util.spec_from_file_location("agent_plan_mod", AGENT)
mod = importlib.util.module_from_spec(spec)
sys.argv = ["benben-agent.py"]
spec.loader.exec_module(mod)

tmp = tempfile.mkdtemp(prefix="plan-ctl-")
try:
    ws = mod.Workspace(tmp)
    ws.plan_mode = False
    ws.plan_guard = "off"

    refreshed = []
    session_box = {"uuid": None}

    ctl = mod.PlanModeController(
        ws,
        lambda: session_box["uuid"],
        lambda: refreshed.append(1),
    )

    check("defaults captured from workspace",
          ctl.defaults == {"mode": False, "guard": "off"}, ctl.defaults)

    # --- apply ---
    ctl.apply(True, "approve")
    check("apply sets plan_mode", ws.plan_mode is True)
    check("apply sets guard", ws.plan_guard == "approve", ws.plan_guard)
    check("apply refreshes the prompt", len(refreshed) == 1, refreshed)

    # 切模式必须清掉挂起的计划，否则会把上一个模式的计划带到新模式
    ws.plan_result = "stale plan"
    ctl.apply(False)
    check("apply clears pending plan result", ws.plan_result is None, ws.plan_result)
    check("apply without guard keeps guard",
          ws.plan_guard == "approve", ws.plan_guard)

    # --- save / restore ---
    session_box["uuid"] = "aaaa1111"
    ws.plan_mode = True
    ws.plan_guard = "deny"
    ws.plan_result = "plan A"
    ctl.save_current()

    check("save_current stores under the current session",
          "aaaa1111" in ws.plan_sessions, list(ws.plan_sessions))

    # 切到另一个会话：应回到默认值
    session_box["uuid"] = "bbbb2222"
    ctl.restore("bbbb2222")
    check("restore falls back to defaults for unknown session",
          ws.plan_mode is False and ws.plan_guard == "off",
          f"{ws.plan_mode}/{ws.plan_guard}")
    check("restore clears stale plan result", ws.plan_result is None, ws.plan_result)

    # 切回有状态的会话：应还原
    before = len(refreshed)
    ctl.restore("aaaa1111")
    check("restore brings back plan_mode", ws.plan_mode is True)
    check("restore brings back guard", ws.plan_guard == "deny", ws.plan_guard)
    check("restore brings back plan result", ws.plan_result == "plan A", ws.plan_result)
    check("restore refreshes the prompt", len(refreshed) > before)

    # --- save 只对有效 uuid 生效 ---
    session_box["uuid"] = None
    ws.plan_sessions.clear()
    ctl.save_current()
    check("save_current ignores a missing session id",
          ws.plan_sessions == {}, ws.plan_sessions)

    # --- update_defaults ---
    ctl.update_defaults(True, "approve")
    check("update_defaults changes the fallback",
          ctl.defaults == {"mode": True, "guard": "approve"}, ctl.defaults)
    ctl.restore("no-such-session")
    check("restore uses updated defaults",
          ws.plan_mode is True and ws.plan_guard == "approve",
          f"{ws.plan_mode}/{ws.plan_guard}")

    # --- 两个控制器互不干扰（模拟 CLI 与 Web 各自持有）---
    ws2 = mod.Workspace(tempfile.mkdtemp(prefix="plan-ctl2-"))
    ctl2 = mod.PlanModeController(ws2, lambda: None, lambda: None)
    ctl2.apply(True, "deny")
    check("controllers do not share state",
          ws.plan_guard == "approve" and ws2.plan_guard == "deny",
          f"{ws.plan_guard}/{ws2.plan_guard}")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all plan mode controller checks passed")
