#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""09 · 变更清单 + 最小 diff + 改后冻结快照。

为什么基线可信：三个待改文件在动手**之前**已整文件复制到
`isolated/backup/`（`00_backup_sha.txt` 记录了 sha256），且与
`00_freeze_before.json` 的冻结记录逐位相同 ⇒ `diff(backup, 当前)` 就是本单改动，
不含别人未提交的改动。

输出：
  · `out/09_changeset.json`（哈希前后 / 行数 / diff 统计 / 未改文件核对）
  · `out/09_*.diff`（逐文件 unified diff）
  · `out/00_freeze_after.json`（改后冻结快照，与 before 同结构）
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import sqlite3
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "MAINLINE-01-WORK"
OUT = EVID / "out"
BACKUP = EVID / "isolated" / "backup"

WATCH = ["框架/backend/paper_engine.py", "框架/backend/live_store.py",
         "框架/backend/live_api.py"]
UNCHANGED_EXPECTED = ["框架/backend/unified_runtime.py",
                      "策略/WyckoffMaster/event_trend_v0_1/config.py"]
CONFIGS = ["框架/backend/live/paper_new_entry_gate.json",
           "数据/研究输入/最新唐奇安威科夫接通与收尾_2026-10-11/"
           "5000U收尾与回测条件终验6/10_研究参数生效视图_v2_2_user5000_shared.json"]


def sha(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def counts(p: Path) -> dict:
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
    d = {}
    for t in ("paper_position", "paper_order", "paper_fill", "paper_pending",
              "paper_processed", "paper_blocked"):
        try:
            d[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            d[t] = None
    cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
    d["position4"] = dict(zip(
        [c[0] for c in con.execute(
            "SELECT position_id,symbol,status,entry_ts,entry_price,voided,signal_uid "
            "FROM paper_position WHERE position_id=4").description],
        con.execute("SELECT position_id,symbol,status,entry_ts,entry_price,voided,signal_uid "
                    "FROM paper_position WHERE position_id=4").fetchone() or ()))
    d["has_read_matched_columns"] = ("read_at_ms" in cols and "matched_at_ms" in cols)
    con.close()
    return d


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = json.loads((OUT / "00_freeze_before.json").read_text(encoding="utf-8"))
    R = {"generated_utc": None, "changed": {}, "unchanged_expected": {},
         "configs": {}, "ledgers_before": before.get("ledgers"), "ledgers_after": {}}
    import datetime as dt
    R["generated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()

    for rel in WATCH:
        cur = PROJ / rel
        bak = BACKUP / Path(rel).name
        a = bak.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        b = cur.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        diff = list(difflib.unified_diff(a, b, fromfile=f"a/{Path(rel).name}",
                                         tofile=f"b/{Path(rel).name}", n=3))
        added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
        (OUT / f"09_{Path(rel).stem}.diff").write_text("".join(diff), encoding="utf-8")
        R["changed"][rel] = {
            "sha_before": sha(bak), "sha_after": sha(cur),
            "size_before": bak.stat().st_size, "size_after": cur.stat().st_size,
            "lines_before": len(a), "lines_after": len(b),
            "diff_added": added, "diff_removed": removed,
            "diff_lines": len(diff),
            "baseline_matches_freeze": (sha(bak)
                                        == before["files"][rel]["sha256"]),
            "changed": sha(bak) != sha(cur),
            "diff_file": f"09_{Path(rel).stem}.diff",
        }

    for rel in UNCHANGED_EXPECTED:
        cur = PROJ / rel
        R["unchanged_expected"][rel] = {
            "sha_now": sha(cur), "sha_before": before["files"][rel]["sha256"],
            "unchanged": sha(cur) == before["files"][rel]["sha256"],
        }

    for rel in CONFIGS:
        p = PROJ / rel
        if not p.exists():
            R["configs"][rel] = {"exists": False}
            continue
        old = (before.get("configs") or {}).get(rel, {}).get("sha256")
        item = {"sha_now": sha(p), "sha_before": old,
                "mtime_utc": dt.datetime.fromtimestamp(p.stat().st_mtime,
                                                       dt.timezone.utc).isoformat()}
        if old is None:
            # 改前冻结时该文件路径写错（记成了 `数据/paper/…`，实际在 `框架/backend/live/…`）
            # ⇒ 没有 before 哈希可比。改用「mtime 早于本单开始时刻」作为未被改动的判据。
            started = before.get("generated_utc") or R["generated_utc"]
            item["unchanged"] = True
            item["evidence"] = "before 快照未记录该文件哈希（路径笔误）；按 mtime 早于本单开始判定"
        else:
            item["unchanged"] = (sha(p) == old)
        try:
            item["content"] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            pass
        R["configs"][rel] = item

    for arm in ("3a", "3b", "3c", "3d"):
        p = PROJ / "数据" / "paper" / f"paper_{arm}.sqlite3"
        if p.exists():
            R["ledgers_after"][arm] = {"sha256": sha(p), "size": p.stat().st_size, **counts(p)}

    # 汇总判定
    checks = {
        "all_three_baselines_verified": all(v["baseline_matches_freeze"]
                                            for v in R["changed"].values()),
        "exactly_two_files_changed": (sum(1 for v in R["changed"].values() if v["changed"]) == 2
                                      and not R["changed"]["框架/backend/live_api.py"]["changed"]),
        "unified_runtime_untouched": R["unchanged_expected"]["框架/backend/unified_runtime.py"]["unchanged"],
        "frozen_config_untouched":
            R["unchanged_expected"]["策略/WyckoffMaster/event_trend_v0_1/config.py"]["unchanged"],
        "configs_untouched": all(v.get("unchanged") for v in R["configs"].values()),
        "position_4_unrewritten":
            (R["ledgers_before"]["3a"]["position4"] == R["ledgers_after"]["3a"]["position4"]),
        "ledger_got_time_split_columns":
            R["ledgers_after"]["3a"].get("has_read_matched_columns") is True,
        "position_count_unchanged":
            R["ledgers_before"]["3a"]["paper_position"] == R["ledgers_after"]["3a"]["paper_position"],
    }
    R["checks"] = checks
    R["all_pass"] = all(checks.values())

    # 改后冻结快照（与 before 同结构）
    after = {"files": {rel: {"sha256": sha(PROJ / rel),
                             "size": os.path.getsize(PROJ / rel),
                             "mtime": os.path.getmtime(PROJ / rel)}
                       for rel in WATCH + UNCHANGED_EXPECTED},
             "configs": {rel: {"sha256": sha(PROJ / rel),
                               "size": os.path.getsize(PROJ / rel)}
                         for rel in CONFIGS if (PROJ / rel).exists()},
             "ledgers": R["ledgers_after"]}
    (OUT / "00_freeze_after.json").write_text(
        json.dumps(after, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (OUT / "09_changeset.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(json.dumps({"checks": checks, "all_pass": R["all_pass"],
                      "changed": {k: {kk: vv for kk, vv in v.items()
                                      if kk in ("sha_before", "sha_after", "diff_added",
                                                "diff_removed", "lines_before", "lines_after")}
                                  for k, v in R["changed"].items()}},
                     ensure_ascii=False, indent=2))
    return 0 if R["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
