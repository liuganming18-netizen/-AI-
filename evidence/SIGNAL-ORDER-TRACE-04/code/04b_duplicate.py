# -*- coding: utf-8 -*-
"""04b｜重复输入不重复下单（工单第六条第三类场景）。

为什么要单独一个脚本：`04_iso_exec.py --pass duplicate` 只把候选列表收窄成 1 条，
**只提交一次**，等于没测重复。本脚本对同一 `signal_uid` 连续提交多次，逐次快照账本，
用「账本计数有没有变」来判定，不靠返回值自报。

三个子场景（各自独立进程 + 独立账本，避免引擎级缓存串味）：
  `open`  已成交候选：第 1 次成交；第 2 次同刻重复；第 3 次**推进时钟后**重复
          ⇒ 后两次必须是 `duplicate_signal_uid`，且 订单/成交/持仓/已实现 计数一字不变。
  `gate`  闸门关闭候选：连提两次 ⇒ 两次都是 `new_entry_blocked_pending_closure_review`，
          `paper_order` 仍只有 **1** 行（`INSERT OR IGNORE`），`paper_blocked` 仍只有 **1** 行。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

ARM = "3a"
ACCOUNT = f"paper43_{ARM}"
TABLES = ("paper_order", "paper_fill", "paper_position", "paper_realized",
          "paper_blocked", "paper_processed", "paper_pending", "paper_request_anchor")


def counts(ledger_dir: Path) -> dict:
    p = ledger_dir / f"paper_{ARM}.sqlite3"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    out = {}
    for t in TABLES:
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = None
    con.close()
    return out


def rows_for(ledger_dir: Path, event_uid: str) -> dict:
    p = ledger_dir / f"paper_{ARM}.sqlite3"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {
        "orders": [dict(r) for r in con.execute(
            "SELECT order_id,event_uid,status,reject_reason FROM paper_order WHERE event_uid=?",
            (event_uid,))],
        "blocked": [dict(r) for r in con.execute(
            "SELECT id,reason FROM paper_blocked WHERE event_uid=?", (event_uid,))],
        "processed": [dict(r) for r in con.execute(
            "SELECT signal_uid,action FROM paper_processed WHERE signal_uid=?", (event_uid,))],
    }
    con.close()
    return out


def pick_candidate(mode: str) -> dict:
    """从 04 回放结果里挑一个候选，并用 02 扫描结果补齐毫秒字段。

    04 的 case 只带 `*_utc` 字符串（落盘为可读），毫秒口径以 02 扫描为准；
    02 里找不到才退回 ISO 解析（不猜、不填 0）。
    """
    d = cm.read_json(cm.OUT / "04_replay.json")
    ok = [c for c in d["cases"] if c["open_ok"]]
    if mode == "open":
        if not ok:
            raise SystemExit("04_replay 无成交候选 ⇒ 无法测重复（不空转判通过）")
        picked = ok[0]
    else:
        bad = [c for c in d["cases"] if not c["open_ok"]]
        if not bad:
            raise SystemExit("04_replay 无拒单候选 ⇒ 无法测重复拒单")
        picked = bad[0]

    scan = cm.read_json(cm.OUT / "02_候选扫描.json")
    full = next((c for c in scan.get("candidates", [])
                 if c["candidate_id"] == picked["candidate_id"]), None)
    if full is None:
        full = dict(picked)
        full["snapshot_time_ms"] = cm.parse_iso(picked["snapshot_time_utc"])
        full["available_at_ms"] = cm.parse_iso(picked["available_at_utc"])
        full["_ms_source"] = "04_case_iso_parsed"
    else:
        full = dict(full)
        full["_ms_source"] = "02_scan"
    return full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, choices=["open", "gate"])
    a = ap.parse_args()

    cand = pick_candidate(a.case)
    sym = cand["symbol"]
    uid = cand["candidate_id"]
    sub = f"duplicate_{a.case}"
    cm.iso_env(gate_allow=(a.case == "open"), ledger_sub=sub)
    iso = cm.assert_isolated()

    import live_store as L                    # noqa: F401
    import paper_engine as PE

    ledger_dir = Path(iso["LEDGER_DIR"])
    con_mkt = L.market_conn()
    earliest = max(int(cand["snapshot_time_ms"]), int(cand["available_at_ms"])) + cm.M15
    bar = PE.bar_at_or_after(con_mkt, sym, earliest)
    if bar is None:
        raise SystemExit(f"{sym} 在 {cm.iso(earliest)} 之后无已闭合 15m bar")
    fill_bar_open = int(bar["open_time"])
    con_mkt.close()

    def submit(now_ms: int, requested_at: int) -> dict:
        return PE.open_position(
            ARM, sym, signal_uid=uid, direction="long",
            atr=float(cand["box_atr_a"]), stop_price=float(cand["initial_stop_price"]),
            now_ms=now_ms, event_time=int(cand["snapshot_time_ms"]),
            available_at=int(cand["available_at_ms"]),
            requested_at=requested_at, origin="replay",
            signal_payload=cand, epoch_ms=cm.parse_iso("2026-06-01T00:00:00Z"),
        )

    attempts = []
    snapshots = []

    def step(label: str, now_ms: int, requested_at: int) -> dict:
        r = submit(now_ms, requested_at)
        c = counts(ledger_dir)
        attempts.append({
            "label": label, "now_ms": now_ms, "now_utc": cm.iso(now_ms),
            "requested_at_ms": requested_at,
            "ok": bool(r.get("ok")), "reason": r.get("reason"),
            "position_id": r.get("position_id"), "order_id": r.get("order_id"),
            "ledger_counts_after": c,
        })
        snapshots.append(c)
        return r

    # ① 首次
    step("first", fill_bar_open, int(cand["available_at_ms"]))
    if a.case == "open" and not attempts[0]["ok"]:
        raise SystemExit(f"首次提交未成交（{attempts[0]['reason']}）⇒ 无法测重复")
    # ② 同刻重复
    step("repeat_same_clock", fill_bar_open, int(cand["available_at_ms"]))
    # ③ 推进时钟后重复（模拟「重启后同一候选又被喂进来」）
    step("repeat_later_clock", fill_bar_open + 4 * 3_600_000,
         int(cand["available_at_ms"]) + 4 * 3_600_000)

    detail = rows_for(ledger_dir, uid)

    # ── 判定（全部按账本计数，不看返回值自报）──────────────────────────
    first, second, third = snapshots
    if a.case == "open":
        expect_reason = "duplicate_signal_uid"
        verdict = {
            "first_ok": attempts[0]["ok"],
            "second_reason": attempts[1]["reason"],
            "third_reason": attempts[2]["reason"],
            "second_no_new_rows": second == first,
            "third_no_new_rows": third == first,
            "single_order": len(detail["orders"]) == 1,
            "single_position": first["paper_position"] == 1,
            "processed_one": len(detail["processed"]) == 1,
        }
        passed = (verdict["first_ok"]
                  and verdict["second_reason"] == expect_reason
                  and verdict["third_reason"] == expect_reason
                  and verdict["second_no_new_rows"] and verdict["third_no_new_rows"]
                  and verdict["single_order"] and verdict["single_position"])
    else:
        expect_reason = "new_entry_blocked_pending_closure_review"
        verdict = {
            "first_reason": attempts[0]["reason"],
            "second_reason": attempts[1]["reason"],
            "third_reason": attempts[2]["reason"],
            "all_rejected_same_reason": all(x["reason"] == expect_reason for x in attempts),
            "second_no_new_rows": second == first,
            "third_no_new_rows": third == first,
            "single_order_row": len(detail["orders"]) == 1,
            "single_blocked_row": len(detail["blocked"]) == 1,
            "no_fill": first["paper_fill"] == 0,
            "no_position": first["paper_position"] == 0,
        }
        passed = (verdict["all_rejected_same_reason"] and verdict["second_no_new_rows"]
                  and verdict["third_no_new_rows"] and verdict["single_order_row"]
                  and verdict["single_blocked_row"] and verdict["no_fill"]
                  and verdict["no_position"])

    out = {
        "case": a.case,
        "generated_at_utc": cm.utc_now(),
        "candidate_id": uid,
        "symbol": sym,
        "isolated_paths": iso,
        "expect_reason": expect_reason,
        "attempts": attempts,
        "ledger_rows_for_candidate": detail,
        "verdict": verdict,
        "passed": passed,
        "dedup_mechanism": {
            "open": ("`paper_engine.open_position` 幂等闸："
                     "`SELECT 1 FROM paper_processed WHERE signal_uid=?` 命中即返回 "
                     "`duplicate_signal_uid`（paper_engine.py:951-954），"
                     "写入口在 paper_engine.py:1219"),
            "gate": ("`_reject` 先 `INSERT OR IGNORE INTO paper_order`（event_uid UNIQUE），"
                     "再查 `paper_blocked` 是否已有同 event_uid 才插（paper_engine.py:1244-1259）"),
        },
    }
    cm.write_json(f"04b_重复输入_{a.case}.json", out)
    print(f"[duplicate/{a.case}] {sym} passed={passed}")
    for x in attempts:
        print(f"  {x['label']:>18}  ok={x['ok']}  reason={x['reason']}  "
              f"counts={x['ledger_counts_after']}")
    print(f"  verdict={json.dumps(verdict, ensure_ascii=False)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
