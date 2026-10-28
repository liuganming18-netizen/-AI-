#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07_changeset.py —— 变更清单与最小 diff（只读，不修改任何源码）

为什么需要这个脚本：
  本单改了三个文件，但只有 `paper_engine.py` 与 `live_store.py` 有干净的改动前基线。
  `live_api.py` 在我动手之前就已经带着**其他工单**的未提交改动（git HEAD 的 blob
  哈希 `7175dae6…` ≠ 本单 00_freeze_before 记录的 `585c9727…`），所以
  `git diff HEAD` 会把别人的改动混进来，不能当本单证据。

做法：
  1. `live_store.py`：git HEAD 的 blob 哈希**等于** 00_freeze_before 记录的
     `02eaf41b…` ⇒ git diff HEAD 就是本单改动，直接采用。
  2. `live_api.py`：把工作区文件里**我自己加的那一处**按原文逐字回退，得到重建基线；
     若重建结果的 sha256 == `585c9727…`（00_freeze_before 记录值），则证明重建
     基线就是改动前的真实内容，此时 diff 可信。不相等就**不产出 diff**，只报事实。
  3. `paper_engine.py`：基线用反重力快照 `a99f8af0…`（已核等于 00_freeze_before）。
  4. `unified_runtime.py`：核对哈希未变（本单未改它）。

输出 out/07_changeset.json + out/07_live_store.diff + out/07_live_api.diff
"""
from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "PARALLEL-STRATEGY-01-WORK" / "out"
BACKEND = ROOT / "框架" / "backend"

FREEZE = OUT / "00_freeze_before.json"
ANTIGRAVITY_SNAP = (ROOT / "evidence" / "PARALLEL-STRATEGY-01-ANTIGRAVITY-A"
                    / "snapshot" / "paper_engine.py")


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def git_show(relpath: str) -> str | None:
    try:
        r = subprocess.run(["git", "show", f"HEAD:{relpath}"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def udiff(a: str, b: str, name: str, fromfile: str, tofile: str) -> str:
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True),
        fromfile=fromfile, tofile=tofile, n=4))


# ── live_api.py 我改动的那一处：新文本 → 原文（逐字，含缩进）─────────────────
API_NEW = (
    '                             "fill_policy": {**PE.FILL_POLICY,\n'
    '                                             "effective_mode_runtime": PE.realtime_fill_mode()},\n'
    '                             "new_entry_gate": PE.new_entry_gate(),\n'
    '                             "legacy_ledger": _legacy_ledger_state(),\n'
    '                             "note": "服务端纸面账本（四臂各 5000U 初始）；成交模型见 "\n'
    '                                     "model_version。成交时钟：**执行点 = "\n'
    '                                     "max(信号确认时刻,信号可用时刻)+15m**（无信号的手工请求取"\n'
    '                                     "真实请求时刻）；执行点那根 15m bar 的 `open` 在 bar "\n'
    '                                     "**刚开盘**就从实时行情读到 ⇒ 撮合发生在执行点"\n'
    '                                     "（`reality_fill=1`）；执行点已过 ⇒ 拒单 "\n'
    '                                     "`fill_point_passed_no_backfill`，**不用闭 bar 的 open "\n'
    '                                     "冒充当时成交，也不顺延到下一根**；尚未到点 ⇒ 登记挂单"\n'
    '                                     "（`pending`），由真实进程时钟推进。"\n'
)
API_OLD = (
    '                             "fill_policy": PE.FILL_POLICY,\n'
    '                             "new_entry_gate": PE.new_entry_gate(),\n'
    '                             "legacy_ledger": _legacy_ledger_state(),\n'
    '                             "note": "服务端纸面账本（四臂各 5000U 初始）；成交模型见 "\n'
    '                                     "model_version。成交时钟：**第一根 `open_time >= "\n'
    '                                     "max(信号确认时刻,信号可用时刻,真实请求时刻)+15m` 的已闭合 "\n'
    '                                     "15m bar 的 open**；尚未到点 ⇒ 登记挂单（`pending`），由真实"\n'
    '                                     "进程时钟推进；错过成交时刻 ⇒ 拒单，**不用旧价回填**。"\n'
)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    rec = {x["path"]: x for x in freeze["write_targets"]}
    for grp in ("read_dependencies", "configs", "frozen_rule_candidates"):
        rec.update({x["path"]: x for x in freeze.get(grp, [])})

    res: dict = {"generated_at_utc": None, "files": {}, "notes": []}

    from datetime import datetime, timezone
    res["generated_at_utc"] = datetime.now(timezone.utc).isoformat()

    # ── 1) live_store.py：git HEAD == 冻结基线 ⇒ git diff 可信 ──────────────
    rel = "框架/backend/live_store.py"
    head = git_show(rel)
    cur = (ROOT / rel).read_text(encoding="utf-8")
    base_sha = rec[rel]["sha256"]
    head_sha = sha_text(head) if head is not None else None
    entry = {
        "path": rel,
        "baseline_source": "git HEAD blob",
        "baseline_sha256_recorded_in_freeze": base_sha,
        "baseline_sha256_actual": head_sha,
        "baseline_verified": head_sha == base_sha,
        "current_sha256": sha(ROOT / rel),
        "changed_by_this_ticket": head_sha == base_sha,
    }
    if head_sha == base_sha:
        d = udiff(head, cur, rel, f"a/{rel}", f"b/{rel}")
        (OUT / "07_live_store.diff").write_text(d, encoding="utf-8")
        entry["diff_lines"] = len(d.splitlines())
        entry["diff_added"] = sum(1 for L in d.splitlines()
                                  if L.startswith("+") and not L.startswith("+++"))
        entry["diff_removed"] = sum(1 for L in d.splitlines()
                                    if L.startswith("-") and not L.startswith("---"))
        entry["diff_file"] = "out/07_live_store.diff"
    res["files"][rel] = entry

    # ── 2) live_api.py：重建基线并核哈希 ────────────────────────────────────
    rel = "框架/backend/live_api.py"
    cur = (ROOT / rel).read_text(encoding="utf-8")
    base_sha = rec[rel]["sha256"]
    ok_found = API_NEW in cur
    rebuilt = cur.replace(API_NEW, API_OLD) if ok_found else None
    rebuilt_sha = sha_text(rebuilt) if rebuilt is not None else None
    entry = {
        "path": rel,
        "baseline_source": "重建（逐字回退本单那一处）",
        "baseline_sha256_recorded_in_freeze": base_sha,
        "baseline_sha256_rebuilt": rebuilt_sha,
        "baseline_verified": rebuilt_sha == base_sha,
        "current_sha256": sha(ROOT / rel),
        "my_hunk_found_in_current": ok_found,
        "git_head_sha256": sha_text(git_show(rel) or ""),
        "git_head_equals_freeze": sha_text(git_show(rel) or "") == base_sha,
        "note": ("本文件在改动前就带有其他工单的未提交改动 ⇒ 不能用 git diff HEAD 当本单证据；"
                 "只有「重建基线哈希 == 冻结记录」成立时才产出最小 diff。"),
    }
    if rebuilt_sha == base_sha:
        d = udiff(rebuilt, cur, rel, f"a/{rel} (改动前)", f"b/{rel} (本单改动后)")
        (OUT / "07_live_api.diff").write_text(d, encoding="utf-8")
        entry["diff_lines"] = len(d.splitlines())
        entry["diff_added"] = sum(1 for L in d.splitlines()
                                  if L.startswith("+") and not L.startswith("+++"))
        entry["diff_removed"] = sum(1 for L in d.splitlines()
                                    if L.startswith("-") and not L.startswith("---"))
        entry["diff_file"] = "out/07_live_api.diff"
    else:
        res["notes"].append("live_api.py 重建基线哈希与冻结记录不一致 ⇒ 未产出 diff（只报事实）。")
    res["files"][rel] = entry

    # ── 3) paper_engine.py：基线 = 反重力快照 ──────────────────────────────
    rel = "框架/backend/paper_engine.py"
    base_sha = rec[rel]["sha256"]
    snap_sha = sha(ANTIGRAVITY_SNAP)
    cur = (ROOT / rel).read_text(encoding="utf-8")
    base = ANTIGRAVITY_SNAP.read_text(encoding="utf-8")
    entry = {
        "path": rel,
        "baseline_source": "evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/snapshot/paper_engine.py",
        "baseline_sha256_recorded_in_freeze": base_sha,
        "baseline_sha256_actual": snap_sha,
        "baseline_verified": snap_sha == base_sha,
        "current_sha256": sha(ROOT / rel),
        "bytes_before": len(base.encode("utf-8")),
        "bytes_after": len(cur.encode("utf-8")),
        "lines_before": len(base.splitlines()),
        "lines_after": len(cur.splitlines()),
    }
    d = udiff(base, cur, rel, f"a/{rel} (改动前)", f"b/{rel} (本单改动后)")
    (OUT / "07_paper_engine.diff").write_text(d, encoding="utf-8")
    entry["diff_lines"] = len(d.splitlines())
    entry["diff_added"] = sum(1 for L in d.splitlines()
                              if L.startswith("+") and not L.startswith("+++"))
    entry["diff_removed"] = sum(1 for L in d.splitlines()
                                if L.startswith("-") and not L.startswith("---"))
    entry["diff_file"] = "out/07_paper_engine.diff"
    res["files"][rel] = entry

    # ── 4) unified_runtime.py：本单未改（核对）────────────────────────────
    rel = "框架/backend/unified_runtime.py"
    cur_sha = sha(ROOT / rel)
    res["files"][rel] = {
        "path": rel,
        "changed_by_this_ticket": False,
        "baseline_sha256_recorded_in_freeze": rec[rel]["sha256"],
        "current_sha256": cur_sha,
        "unchanged": cur_sha == rec[rel]["sha256"],
    }

    # ── 5) 策略规则文件：本单一个字都没动（核对）──────────────────────────
    rel = "策略/WyckoffMaster/event_trend_v0_1/config.py"
    res["files"][rel] = {
        "path": rel,
        "changed_by_this_ticket": False,
        "baseline_sha256_recorded_in_freeze": rec[rel]["sha256"],
        "current_sha256": sha(ROOT / rel),
        "unchanged": sha(ROOT / rel) == rec[rel]["sha256"],
        "note": "策略参数/阈值/确认条件/OI 门槛/风险参数一律未动",
    }

    res["summary"] = {
        "write_targets_changed": [p for p, e in res["files"].items()
                                  if e.get("changed_by_this_ticket")],
        "all_baselines_verified": all(e.get("baseline_verified", e.get("unchanged", True))
                                      for e in res["files"].values()),
    }
    (OUT / "07_changeset.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    for p, e in res["files"].items():
        print(f"  {p}\n    verified={e.get('baseline_verified', e.get('unchanged'))} "
              f"diff=+{e.get('diff_added','-')}/-{e.get('diff_removed','-')} "
              f"({e.get('diff_lines','-')} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
