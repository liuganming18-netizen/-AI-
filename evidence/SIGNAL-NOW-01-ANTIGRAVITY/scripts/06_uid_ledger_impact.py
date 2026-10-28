# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤6：UID ↔ 账本影响表（只读）。

回答一个问题：**289 条研究 UID 和 test_probe_succ，到底有没有在四臂账本里留下任何痕迹？**

做法：把 `live_signal` 的 UID 全集与四臂账本里出现过的所有 UID 做双向差集，
逐 UID 列出它在 6 张账本表（position / pending / order / fill / processed / request_anchor）
里的出现情况。全量比对，不抽样。

产物：out/06_UID与账本影响表.json
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
LIVE_DB = PROJ / "框架/backend/live/live_signals.sqlite3"
PAPER_DIR = PROJ / "数据/paper"
ARMS = ("3a", "3b", "3c", "3d")
TABLES = ("paper_position", "paper_pending", "paper_order", "paper_fill",
          "paper_processed", "paper_request_anchor")
UID_COL = {"paper_position": "signal_uid", "paper_pending": "signal_uid",
           "paper_order": "event_uid", "paper_fill": "signal_uid",
           "paper_processed": "signal_uid", "paper_request_anchor": "event_uid"}


def utc(ts=None):
    t = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ro(p: Path):
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=15)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    files = {"live_signals": LIVE_DB,
             **{f"paper_{a}": PAPER_DIR / f"paper_{a}.sqlite3" for a in ARMS}}
    before = {k: sha256_file(p) for k, p in files.items() if p.exists()}

    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "6 UID ↔ 账本影响表（只读）", "at_utc": utc()}

    # ── 信号侧：全量 UID ──
    con = ro(LIVE_DB)
    try:
        sig = {r["signal_uid"]: {"origin": r["origin"],
                                 "executable_state": r["executable_state"],
                                 "symbol": r["symbol"],
                                 "available_at": r["available_at"],
                                 "confirmed_at": r["confirmed_at"],
                                 "payload_json": r["payload_json"]}
               for r in con.execute(
                   "SELECT signal_uid,origin,executable_state,symbol,available_at,"
                   "confirmed_at,payload_json FROM live_signal")}
    finally:
        con.close()
    research_uids = {u for u, v in sig.items() if v["origin"] == "historical_research"}
    other_uids = {u for u in sig if u not in research_uids}
    R["signal_side"] = {"total": len(sig), "research": len(research_uids),
                        "non_research": len(other_uids),
                        "non_research_uids": sorted(other_uids)}

    # ── 账本侧：每个 UID 在 6 张表的出现次数 ──
    ledger_uids: dict[str, dict] = {}
    per_arm = {}
    for arm in ARMS:
        p = PAPER_DIR / f"paper_{arm}.sqlite3"
        con = ro(p)
        arm_hits = {}
        try:
            for t in TABLES:
                col = UID_COL[t]
                try:
                    rows = con.execute(f"SELECT {col}, COUNT(*) FROM {t} "
                                       f"WHERE {col} IS NOT NULL GROUP BY {col}").fetchall()
                except sqlite3.OperationalError:
                    continue
                for u, n in rows:
                    arm_hits.setdefault(u, {})[t] = n
        finally:
            con.close()
        per_arm[arm] = {"distinct_uids": len(arm_hits), "uids": sorted(arm_hits)}
        for u, d in arm_hits.items():
            ledger_uids.setdefault(u, {})[arm] = d

    R["ledger_side"] = per_arm
    R["ledger_uid_total"] = len(ledger_uids)
    R["ledger_uids"] = sorted(ledger_uids)

    # ── 双向差集 ──
    R["impact"] = {
        "research_in_ledger": sorted(research_uids & set(ledger_uids)),
        "research_in_ledger_count": len(research_uids & set(ledger_uids)),
        "ledger_uid_not_in_signal": sorted(set(ledger_uids) - set(sig)),
        "ledger_uid_not_in_signal_count": len(set(ledger_uids) - set(sig)),
        "signal_nonresearch_not_in_ledger": sorted(other_uids - set(ledger_uids)),
        "signal_nonresearch_not_in_ledger_count": len(other_uids - set(ledger_uids)),
    }

    # ── 逐 UID 明细（账本里出现过的全部 UID，数量很小，可全列）──
    detail = []
    for u in sorted(ledger_uids):
        s = sig.get(u)
        pm = {}
        try:
            pm = json.loads((s or {}).get("payload_json") or "{}")
        except Exception:                                    # noqa: BLE001
            pm = {}
        detail.append({
            "uid": u,
            "in_signal": s is not None,
            "origin": (s or {}).get("origin"),
            "executable_state": (s or {}).get("executable_state"),
            "payload_role": pm.get("role"),
            "payload_approved": pm.get("approved"),
            "available_at": (s or {}).get("available_at"),
            "is_research_row": u in research_uids,
            "arms": ledger_uids[u],
            "total_rows": sum(sum(d.values()) for d in ledger_uids[u].values()),
        })
    R["uid_detail"] = detail

    R["conclusion"] = {
        "research_rows_touched_ledger": R["impact"]["research_in_ledger_count"],
        "verdict": ("289 条研究 UID 在四臂账本里出现 **0** 次 ⇒ 研究信号没有产生任何持仓、"
                    "挂单、订单、成交或处理记录。"
                    if R["impact"]["research_in_ledger_count"] == 0 else
                    "有研究 UID 出现在账本里，必须逐条查。"),
        "test_probe_succ": next((d for d in detail if d["uid"] == "test_probe_succ"), None),
        "note": ("`ledger_uid_not_in_signal` 非空不代表异常：手工/测试单本来就没有对应的 "
                 "`live_signal` 行（它们直接调用开仓接口）。"),
    }

    after = {k: sha256_file(p) for k, p in files.items() if p.exists()}
    R["prod_untouched"] = {k: before[k] == after[k] for k in before}

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "06_UID与账本影响表.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("=== 信号侧 ===")
    print(f"  live_signal 总数={R['signal_side']['total']} "
          f"研究行={R['signal_side']['research']} 非研究={R['signal_side']['non_research']}")
    print(f"  非研究 UID：{R['signal_side']['non_research_uids']}")
    print("\n=== 账本侧 ===")
    for arm, d in per_arm.items():
        print(f"  {arm}: 不同 UID 数={d['distinct_uids']}  {d['uids']}")
    print("\n=== 双向差集 ===")
    print(f"  研究 UID 出现在账本里：{R['impact']['research_in_ledger_count']} 条")
    print(f"  账本 UID 不在 live_signal：{R['impact']['ledger_uid_not_in_signal_count']} 条 "
          f"→ {R['impact']['ledger_uid_not_in_signal']}")
    print(f"  非研究 UID 未进账本：{R['impact']['signal_nonresearch_not_in_ledger_count']} 条")
    print("\n=== 结论 ===")
    print("  " + R["conclusion"]["verdict"])
    tp = R["conclusion"]["test_probe_succ"]
    if tp:
        print(f"  test_probe_succ：in_signal={tp['in_signal']} arms={tp['arms']} "
              f"总行数={tp['total_rows']}")
    print("\n=== 生产库零写入 ===")
    for k, v in R["prod_untouched"].items():
        print(f"  {k}: 未变={v}")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
