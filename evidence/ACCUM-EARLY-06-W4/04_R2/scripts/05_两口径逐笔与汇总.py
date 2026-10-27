# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 05 两套口径逐笔重算与汇总

保持原 51 候选、两套经济口径、原输入不变：
  · 旧基线 = 只读热库（`include_cold=False`，按 06-06 的 P2 严格剔除 prefer=hot 里混入的冷档行）
  · 新基线 = 06-06 适配器冷热合并事件
金额只由 `replay_calc.replay_one` 算，本脚本不改公式、不改参数、不补 0。

逐笔输出：口径、候选、资金费旧/新/差、净额旧/新/差、变化原因、
完整性结论（来自 02）、端点抖动金额敏感度。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L
import funding_events_adapter as FA
import replay_calc as RC
import research_15m as R15

CHECKS: list = []
DAY = L.DAY


def add(name: str, ok: bool, evidence) -> None:
    CHECKS.append({"检查": name, "通过": bool(ok), "证据": evidence})


def main() -> int:
    cands = L.load_candidates()
    p02 = json.loads((L.OUT / "02_缺口判据与完整性.json").read_text(encoding="utf-8"))
    rec_by_cid = {r["candidate_id"]: r for r in p02["per_candidate"]}

    m15 = R15.Merged15m(str(L.HOT_DB), str(L.ARCHIVE_ROOT))
    m4 = R15.Merged15m(str(L.HOT_DB), str(L.ARCHIVE_ROOT), timeframe="4h")
    repo = FA._get_repo(None, None)
    bar_cache: dict = {}

    rows = []
    for cal in L.CALIBERS:
        for c in cands:
            sym, cid = c["symbol"], c["candidate_id"]
            entry_ms = L.to_ms(c["entry_bar_utc"])
            # 键必须是候选：同标的可能有 2 个候选（51 候选 / 48 标的），
            # 用 symbol 做键会把上一个候选的 K 线窗口套到下一个候选上（曾导致负持仓时长）
            if cid not in bar_cache:
                b15, _ = m15.load(sym, entry_ms - 2 * 3600_000, entry_ms + 14 * DAY)
                elig = [b for b in b15 if int(b["open_time"]) >= entry_ms]
                prevs = [b for b in b15 if int(b["open_time"]) < entry_ms]
                b4, _ = m4.load(sym, entry_ms - 8 * DAY, entry_ms + 30 * DAY)
                bar_cache[cid] = (elig, prevs[-1] if prevs else None, b4)
            elig, prev_bar, b4 = bar_cache[cid]
            if not elig:
                rows.append({"caliber": cal, "candidate_id": cid, "symbol": sym,
                             "status": "no_bars", "is_filled": False})
                continue

            hot = FA.to_replay_events(
                FA.load_funding_events("binance", "um", sym, entry_ms + 1,
                                       entry_ms + 14 * DAY, repo=repo, include_cold=False))
            new = FA.to_replay_events(
                FA.load_funding_events("binance", "um", sym, entry_ms + 1,
                                       entry_ms + 14 * DAY, repo=repo))

            r_old = RC.replay_one(symbol=sym, entry_bar=elig[0], prev_bar=prev_bar,
                                  bars_15m=elig, bars_4h=b4, funding_events=hot,
                                  caliber_name=cal)
            r_new = RC.replay_one(symbol=sym, entry_bar=elig[0], prev_bar=prev_bar,
                                  bars_15m=elig, bars_4h=b4, funding_events=new,
                                  caliber_name=cal)
            rec = rec_by_cid[cid]

            if r_new.get("status") != "ok":
                rows.append({
                    "caliber": cal, "candidate_id": cid, "symbol": sym,
                    "status": "rejected", "is_filled": False,
                    "rejection_reason": r_new.get("entry", {}).get("reject"),
                    "completeness": L.CP_NA, "claim": L.CLAIM_UNKNOWN,
                    "interval_basis": rec["interval_basis"],
                    "funding_old": 0.0, "funding_new": 0.0, "delta_funding": 0.0,
                    "net_old": 0.0, "net_new": 0.0, "delta_net": 0.0,
                    "events_old": 0, "events_new": 0,
                    "change_reason": "REJECTED",
                })
                continue

            f_old = float(r_old["资金费"]["成本合计"])
            f_new = float(r_new["资金费"]["成本合计"])
            n_old = float(r_old["盈亏"]["realized_net"])
            n_new = float(r_new["盈亏"]["realized_net"])
            e_old = len(r_old["资金费"]["明细"])
            e_new = len(r_new["资金费"]["明细"])

            # 端点抖动事件的金额（严格判据不纳入；给出「若纳入」的差额）
            exit_ms = L.to_ms(r_new["exit"]["exit_utc"])
            qty = float(r_new["entry"]["qty"])
            bj_cost = 0.0
            bj_detail = []
            for b in rec["boundary_jitter"]:
                if b["strict_rule_includes"]:
                    continue
                ev = next((x for x in new if x["funding_time"] == b["funding_time"]), None)
                if ev and ev["rate"] is not None and ev["mark_price"] is not None:
                    c0 = ev["mark_price"] * qty * ev["rate"]
                    bj_cost += c0
                    bj_detail.append({"funding_utc": b["funding_utc"], "cost": round(c0, 9)})

            if abs(f_new - f_old) > 1e-9 or e_new != e_old:
                reason = "COLD_FILL_ADDED"
            elif rec["boundary_jitter_count"]:
                reason = "BOUNDARY_JITTER_ONLY"
            else:
                reason = "NO_CHANGE"

            rows.append({
                "caliber": cal, "candidate_id": cid, "symbol": sym,
                "status": "ok", "is_filled": True,
                "entry_utc": r_new["entry"].get("entry_utc") or c["entry_bar_utc"],
                "exit_utc": r_new["exit"]["exit_utc"],
                "hold_hours": round((exit_ms - entry_ms) / 3600000.0, 4),
                "qty": qty,
                "funding_old": round(f_old, 9), "funding_new": round(f_new, 9),
                "delta_funding": round(f_new - f_old, 9),
                "net_old": round(n_old, 9), "net_new": round(n_new, 9),
                "delta_net": round(n_new - n_old, 9),
                "events_old": e_old, "events_new": e_new,
                "change_reason": reason,
                "completeness": rec["completeness"], "claim": rec["claim"],
                "interval_basis": rec["interval_basis"],
                "boundary_jitter_cost_if_included": round(bj_cost, 9),
                "boundary_jitter_detail": bj_detail,
                "participation_capped": bool(r_new["entry"].get("participation_capped")),
            })

    def summary(cal: str) -> dict:
        rs = [r for r in rows if r["caliber"] == cal]
        ok = [r for r in rs if r["is_filled"]]
        from collections import Counter
        return {
            "caliber": cal,
            "candidates": len(rs),
            "filled": len(ok),
            "rejected": len(rs) - len(ok),
            "funding_old_sum": round(sum(r["funding_old"] for r in ok), 9),
            "funding_new_sum": round(sum(r["funding_new"] for r in ok), 9),
            "delta_funding_sum": round(sum(r["delta_funding"] for r in ok), 9),
            "net_old_sum": round(sum(r["net_old"] for r in ok), 9),
            "net_new_sum": round(sum(r["net_new"] for r in ok), 9),
            "delta_net_sum": round(sum(r["delta_net"] for r in ok), 9),
            "affected_trades": sum(1 for r in ok if abs(r["delta_funding"]) > 1e-9),
            "events_old_sum": sum(r["events_old"] for r in ok),
            "events_new_sum": sum(r["events_new"] for r in ok),
            "change_reason_dist": dict(Counter(r["change_reason"] for r in rs)),
            "completeness_dist": dict(Counter(r["completeness"] for r in ok)),
            "claim_dist": dict(Counter(r["claim"] for r in ok)),
            "boundary_jitter_trades": sum(1 for r in ok if r["boundary_jitter_cost_if_included"]),
            "boundary_jitter_cost_sum": round(
                sum(r["boundary_jitter_cost_if_included"] for r in ok), 9),
            "participation_capped_trades": sum(1 for r in ok if r["participation_capped"]),
        }

    sums = {cal: summary(cal) for cal in L.CALIBERS}

    # 与 R1 自报值对照（只登记，不采信）
    r1_claim = {
        "生效视图_5bps": {"funding_old": -6.5543, "funding_new": -5.8951,
                          "delta": 0.6592, "net_old": -225.1815, "net_new": -225.8407,
                          "affected": 7},
        "策略包_3bps": {"funding_old": -2.7220, "funding_new": -2.6729,
                        "delta": 0.0491, "net_old": -89.1891, "net_new": -89.2382,
                        "affected": 7},
    }

    out = {
        "工单": "ACCUM-EARLY-06-W4-R2",
        "口径说明": {
            "旧基线": "只读热库（include_cold=False，P2 严格剔除冷档混入行）",
            "新基线": "06-06 适配器冷热合并事件",
            "金额来源": "replay_calc.replay_one（共享代码，未改）",
            "两套口径不合并": "生效视图_5bps 与 策略包_3bps 分别汇总，禁止相加或互相替代",
        },
        "汇总": sums,
        "与R1自报值对照": r1_claim,
        "per_trade": rows,
        "checks": CHECKS,
    }

    for cal in L.CALIBERS:
        s = sums[cal]
        add(f"M1 {cal} 恒等式 ΔNet == -ΔFunding（逐笔）",
            all(abs(r["delta_net"] + r["delta_funding"]) <= 1e-6
                for r in rows if r["caliber"] == cal and r["is_filled"]),
            {"最大残差": max([abs(r["delta_net"] + r["delta_funding"])
                              for r in rows if r["caliber"] == cal and r["is_filled"]] or [0])})
        add(f"M2 {cal} 汇总等于逐笔之和",
            abs(s["delta_funding_sum"] - sum(r["delta_funding"] for r in rows
                                             if r["caliber"] == cal and r["is_filled"])) <= 1e-9,
            {"sum": s["delta_funding_sum"]})

    sA = sums["生效视图_5bps"]
    add("M3 口径 A 资金费新值复现 R1 自报 -5.8951（差 ≤1e-4）",
        abs(sA["funding_new_sum"] - r1_claim["生效视图_5bps"]["funding_new"]) <= 1e-4,
        {"本单": sA["funding_new_sum"], "R1自报": r1_claim["生效视图_5bps"]["funding_new"]})
    add("M4 口径 A 资金费旧值复现 05-C 基线 -6.5543（差 ≤1e-4）",
        abs(sA["funding_old_sum"] - r1_claim["生效视图_5bps"]["funding_old"]) <= 1e-4,
        {"本单": sA["funding_old_sum"], "05-C/R1": r1_claim["生效视图_5bps"]["funding_old"]})
    add("M5 口径 B 资金费新值复现 R1 自报 -2.6729（差 ≤1e-4）",
        abs(sums["策略包_3bps"]["funding_new_sum"]
            - r1_claim["策略包_3bps"]["funding_new"]) <= 1e-4,
        {"本单": sums["策略包_3bps"]["funding_new_sum"],
         "R1自报": r1_claim["策略包_3bps"]["funding_new"]})
    add("M6 受影响笔数两口径都是 7（与 05-C / 06-06 一致）",
        sA["affected_trades"] == 7 and sums["策略包_3bps"]["affected_trades"] == 7,
        {c: sums[c]["affected_trades"] for c in L.CALIBERS})
    add("M0 无负持仓时长（缓存键错会算出负时长）；0 时长为入场当根即止损，合法",
        all(r["hold_hours"] >= 0 for r in rows if r["is_filled"]),
        {"负时长": [{"caliber": r["caliber"], "symbol": r["symbol"],
                     "hold_hours": r["hold_hours"]}
                    for r in rows if r["is_filled"] and r["hold_hours"] < 0],
         "0 时长笔数": sum(1 for r in rows if r["is_filled"] and r["hold_hours"] == 0),
         "0 时长说明": "入场 15m 根内触发初始止损，退出与入场同一根 ⇒ (entry, exit] 为空，"
                       "不可能有结算，资金费必然 0"})
    add("M7 拒单笔数：口径 A 0 笔、口径 B 1 笔（CARVUSDT）",
        sA["rejected"] == 0 and sums["策略包_3bps"]["rejected"] == 1,
        {c: {"rejected": sums[c]["rejected"],
             "which": [r["symbol"] for r in rows
                       if r["caliber"] == c and not r["is_filled"]]} for c in L.CALIBERS})

    (L.OUT / "05_两口径逐笔与汇总.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = sum(1 for c in CHECKS if c["通过"])
    for cal in L.CALIBERS:
        s = sums[cal]
        print(f"[{cal}] 成交 {s['filled']} 拒 {s['rejected']} | 资金费 "
              f"{s['funding_old_sum']:.6f} → {s['funding_new_sum']:.6f}"
              f"（Δ {s['delta_funding_sum']:+.6f}）| 净额 "
              f"{s['net_old_sum']:.6f} → {s['net_new_sum']:.6f}"
              f" | 受影响 {s['affected_trades']} | 事件 {s['events_old_sum']}→{s['events_new_sum']}")
        print(f"           变化原因 {s['change_reason_dist']} | 完整性 {s['completeness_dist']}"
              f" | 边界抖动 {s['boundary_jitter_trades']} 笔 {s['boundary_jitter_cost_sum']:+.6f}")
    print(f"checks {ok}/{len(CHECKS)}")
    for c in CHECKS:
        if not c["通过"]:
            print("  FAIL", c["检查"], json.dumps(c["证据"], ensure_ascii=False)[:300])
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
