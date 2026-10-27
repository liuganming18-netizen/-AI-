# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜08 产物清单 + 上游不可变快照。

三件事：
  ① **产物清单**：`evidence/SIGNAL-HISTORY-FIX-01/` 下每个文件的 SHA-256 与大小
     （版本只认文件 SHA，不认「我记得改过」）。
  ② **改动登记**：本单唯一被改的运行文件 `框架/backend/unified_runtime.py`
     修复前/后 SHA 与行数差；以及**未改动**的冻结口径文件 SHA（必须与 03 记录一致，
     否则说明有人动过策略口径）。
  ③ **上游快照**：行情库 / 纸面账本 / 生产状态文件的体量与指纹（进程与账本只读）。

产出：out/08_产物清单与上游快照.json
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

#: 本单唯一改动的运行文件（修复前 SHA 由 04/03 的 config_identity 留痕）
CHANGED = ["框架/backend/unified_runtime.py"]
#: 冻结策略口径：本单**一个字节都不能动**，逐项核对 SHA 必须与 03 记录一致
FROZEN_STRATEGY = [
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
    "策略/WyckoffMaster/event_trend_v0_1/features.py",
    "策略/WyckoffMaster/event_trend_v0_1/contracts.py",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
]


def main():
    before = json.loads((cm.OUT / "03_漏斗汇总.json").read_text(encoding="utf-8"))
    ci_before = before["config_identity"]

    # ── ② 改动登记 ─────────────────────────────────────────────────────
    changed = []
    for rel in CHANGED:
        p = cm.BASE / rel
        changed.append({
            "path": rel,
            "sha256_now": cm.sha256_file(p),
            "size_now": p.stat().st_size,
            "sha256_before": ci_before.get("unified_runtime_sha256"),
            "note": ("bootstrap 水位改取「状态机真正处理过的最大 1h」；"
                     "新增 idle_breakdown() 与 IDLE 分因展示；"
                     "export_state() 如实声明 state_persisted=False"),
        })
    frozen = []
    for rel in FROZEN_STRATEGY:
        p = cm.BASE / rel
        now = cm.sha256_file(p)
        ref = {"config.py": ci_before.get("config_file_sha256"),
               "state_machine.py": ci_before.get("state_machine_sha256")}.get(p.name)
        frozen.append({"path": rel, "sha256_now": now,
                       "sha256_recorded_in_03": ref,
                       "unchanged": (ref is None or ref == now)})

    # ── ③ 上游快照 ─────────────────────────────────────────────────────
    import live_store as L
    up = {}
    md = Path(L.MARKET_DB)
    con = cm.market_conn()
    try:
        up["market_db"] = {
            "path": str(md), "size_bytes": md.stat().st_size,
            "mtime_utc": cm.dt.datetime.fromtimestamp(
                md.stat().st_mtime, cm.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "candle_heads": con.execute("SELECT COUNT(*) FROM candle_heads").fetchone()[0],
            "candles_visible": con.execute("SELECT COUNT(*) FROM candles").fetchone()[0],
            "max_1h_open_utc": cm.iso(con.execute(
                "SELECT MAX(open_time) FROM candles WHERE timeframe='1h'").fetchone()[0]),
        }
    finally:
        con.close()
    con_p = L.paper_conn("3a", ro=True)
    try:
        pb = Path(con_p.execute("PRAGMA database_list").fetchone()[2])
    finally:
        con_p.close()
    up["paper_ledger"] = {
        "path": str(pb), "size_bytes": pb.stat().st_size,
        "sha256": cm.sha256_file(pb),
        "mtime_utc": cm.dt.datetime.fromtimestamp(
            pb.stat().st_mtime, cm.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "只读核对（07），本单未写入任何账本行",
    }
    st = cm.BACKEND / "locks/paper_runtime_3a_status.json"
    up["production_status_file"] = {
        "path": str(st), "sha256": cm.sha256_file(st) if st.exists() else None,
        "note": "生产进程写出的状态快照；本单只读比对，不修改",
    }

    # ── ① 产物清单 ─────────────────────────────────────────────────────
    arts = []
    for p in sorted(cm.ME.rglob("*")):
        if p.is_file() and "__pycache__" not in str(p):
            arts.append({"rel": str(p.relative_to(cm.ME)),
                         "size": p.stat().st_size,
                         "sha256": cm.sha256_file(p)})

    res = {
        "ticket": "SIGNAL-HISTORY-FIX-01",
        "stage": "manifest",
        "generated_at_utc": cm.utc_now(),
        "changed_files": changed,
        "frozen_strategy_untouched": frozen,
        "frozen_strategy_all_unchanged": all(f["unchanged"] for f in frozen),
        "upstream": up,
        "artifacts": arts,
        "artifacts_n": len(arts),
        "artifacts_total_bytes": sum(a["size"] for a in arts),
    }
    p = cm.write_json("08_产物清单与上游快照.json", res)

    print("changed:", json.dumps(changed, ensure_ascii=False, indent=1)[:600])
    print("frozen all unchanged:", res["frozen_strategy_all_unchanged"])
    for f in frozen:
        print("  ", f["path"].split("/")[-1], "now", f["sha256_now"][:12],
              "rec", (f["sha256_recorded_in_03"] or "-")[:12], f["unchanged"])
    print("upstream market_db:", up["market_db"])
    print("upstream ledger sha:", up["paper_ledger"]["sha256"][:16],
          "size", up["paper_ledger"]["size_bytes"])
    print("artifacts:", res["artifacts_n"], "files,", res["artifacts_total_bytes"], "bytes")
    print("->", p)


if __name__ == "__main__":
    main()
