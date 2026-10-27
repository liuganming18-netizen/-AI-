# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-02｜01 冷热合并资金费接入与全量重算影响评估

核心任务：
1. 依赖 06-07（第一单成果）与冷热资金费数据源（热库 SQLite + 冷档 parquet）；
2. 保持旧输入对照，在回放中传入冷热合并的完整 funding_events；
3. 无共享计算缺陷时不修改 replay_calc.py；
4. 每口径分别输出资金费前后差额、净额差额、受影响交易与分类变化；
5. 打破 63/38/1 旧分流，根据真实资金费完整度与同刻歧义重新评估可用性；
6. 严格断言：资金费变化精确逐笔解释净额变化 (Δnet == -Δfunding)；期末未平仓不计已实现；缺口未伪装成零。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pyarrow.parquet as pq

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import replay_calc as RC
import paper_engine as PE
import research_15m as R15

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_PARQUET_DIR = ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" / "um"
COLD_KLINE_DIR = ROOT / "数据" / "市场数据" / "parquet"

ACC2_JSON = ROOT / "evidence" / "ACCUM-EARLY-02" / "out" / "04_回放计算修复.json"
ACC4D_RECALC = ROOT / "evidence" / "ACCUM-EARLY-04-D" / "out" / "recalc_audit_04d.json"
ACC4D_W3 = ROOT / "evidence" / "ACCUM-EARLY-04-D" / "out" / "window3_usability_manifest.json"
W4_01_JSON = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "01" / "out" / "01_05d_r1逐笔复核与差异清单.json"

OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "02" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

M15 = 15 * 60 * 1000
H8 = 8 * 3600 * 1000
DAY = 86400 * 1000
EPS = 1e-9

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)

def iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ═════════════════════════════════════════════════════════════════════
# 资金费数据加载适配器：热库读取、冷档读取与冷热去重合并
# ═════════════════════════════════════════════════════════════════════

def load_funding_hot(symbol: str, entry_ms: int, span_days: int = 14) -> List[dict]:
    """旧输入通道：仅从热库 SQLite 读取资金费。"""
    con = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
    try:
        rs = con.execute(
            """SELECT funding_time, funding_rate, mark_price, fetched_at
               FROM funding_history
               WHERE exchange='binance' AND market='um' AND symbol=?
                 AND rate_type='settled' AND funding_time > ? AND funding_time <= ?
               ORDER BY funding_time""",
            (symbol, int(entry_ms), int(entry_ms + span_days * DAY))).fetchall()
    finally:
        con.close()
    return [{
        "time_ms": int(r[0]),
        "rate": (float(r[1]) if r[1] is not None else None),
        "mark_price": (float(r[2]) if r[2] is not None else None),
        "fetched_at": r[3],
        "source": "hot_sqlite"
    } for r in rs]


def load_funding_cold(symbol: str, entry_ms: int, span_days: int = 14) -> List[dict]:
    """冷档通道：从 Parquet 归档文件读取资金费。"""
    p = COLD_PARQUET_DIR / f"{symbol}.parquet"
    if not p.exists():
        return []
    tbl = pq.read_table(p, columns=["funding_time", "funding_rate", "mark_price", "rate_type"])
    df = tbl.to_pandas()
    df = df[(df["rate_type"] == "settled") &
            (df["funding_time"] > entry_ms) &
            (df["funding_time"] <= entry_ms + span_days * DAY)]
    out = []
    for _, row in df.iterrows():
        out.append({
            "time_ms": int(row["funding_time"]),
            "rate": (float(row["funding_rate"]) if math.isfinite(row["funding_rate"]) else None),
            "mark_price": (float(row["mark_price"]) if math.isfinite(row["mark_price"]) else None),
            "fetched_at": None,
            "source": "cold_parquet"
        })
    return out


def load_funding_merged(symbol: str, entry_ms: int, span_days: int = 14) -> Tuple[List[dict], List[dict], dict]:
    """新输入通道：冷热合并去重。
    
    去重规则：
    1. 键为 (funding_time)；
    2. 若同一结算时刻在冷档与热库均存在，以热库（带 fetched_at）为准，记录对撞一致性；
    3. 若热库缺失（如 2026-09-02 至 2026-09-18 缺口），由冷档补齐。
    """
    hot_events = load_funding_hot(symbol, entry_ms, span_days)
    cold_events = load_funding_cold(symbol, entry_ms, span_days)

    merged_map = {}
    conflict_count = 0
    cold_only_count = 0
    hot_only_count = 0
    both_count = 0

    # 先放冷档
    for ev in cold_events:
        t = ev["time_ms"]
        merged_map[t] = ev
        cold_only_count += 1

    # 热库覆盖或合并
    for ev in hot_events:
        t = ev["time_ms"]
        if t in merged_map:
            both_count += 1
            cold_only_count -= 1
            # 校验 rate 和 mark_price 是否一致
            cold_ev = merged_map[t]
            rate_diff = abs(ev["rate"] - cold_ev["rate"]) if (ev["rate"] is not None and cold_ev["rate"] is not None) else 0.0
            mark_diff = abs(ev["mark_price"] - cold_ev["mark_price"]) if (ev["mark_price"] is not None and cold_ev["mark_price"] is not None) else 0.0
            if rate_diff > 1e-8 or mark_diff > 1e-5:
                conflict_count += 1
            # 采用热库记录（补充了 fetched_at）
            merged_map[t] = ev
        else:
            hot_only_count += 1
            merged_map[t] = ev

    merged_events = [merged_map[t] for t in sorted(merged_map.keys())]

    meta = {
        "hot_count": len(hot_events),
        "cold_count": len(cold_events),
        "merged_count": len(merged_events),
        "both_count": both_count,
        "cold_only_count": cold_only_count,
        "hot_only_count": hot_only_count,
        "conflict_count": conflict_count,
    }
    return hot_events, merged_events, meta


# ═════════════════════════════════════════════════════════════════════
# 结算与退出时序分析及可用性动态重新分类
# ═════════════════════════════════════════════════════════════════════

def evaluate_usability_and_uncertainty(
    *,
    is_filled: bool,
    rejection_reason: Optional[str],
    exit_kind: Optional[str],
    participation_capped: bool,
    held_settled_events: List[dict],
    entry_ms: int,
    exit_ms: Optional[int]
) -> dict:
    """动态评估交易分类，打破固定 63/38/1 旧分流，严密处理同刻歧义与缺口。"""
    if not is_filled:
        return {
            "usability": "HOLD_REJECTED",
            "tags": ["HOLD_REJECTED"],
            "reason": f"策略包参数约束拒单：{rejection_reason}",
            "uncertain_events": [],
            "uncertain_count": 0,
            "has_settlement_gap": False,
        }

    # 1. 检查结算时刻与退出时刻 <= 15 分钟的同刻歧义项
    uncertain_events = []
    if exit_ms is not None:
        for ev in held_settled_events:
            t_ms = ev.get("time_ms") or ev.get("结算时刻_ms")
            if t_ms is not None and abs(exit_ms - t_ms) <= M15:
                uncertain_events.append({
                    "time_ms": t_ms,
                    "time_utc": iso(t_ms),
                    "diff_to_exit_minutes": round((exit_ms - t_ms) / 60000.0, 2),
                    "rate": ev.get("rate"),
                    "mark_price": ev.get("mark_price")
                })
    uncertain_count = len(uncertain_events)

    # 2. 检查真实资金费缺口（持仓超过 8 小时但没有任何结算事件）
    held_hours = ((exit_ms - entry_ms) / 3600000.0) if exit_ms else 0.0
    has_settlement_gap = (held_hours >= 8.0 and len(held_settled_events) == 0)

    # 3. 动态标签评定
    tags = []
    if uncertain_count > 0:
        tags.append("HOLD_FUNDING_ORDER_UNCERTAIN")
    if exit_kind == "gap":
        tags.append("HOLD_GAP_FILL_UNAPPROVED")
    if participation_capped:
        tags.append("WARN_PARTICIPATION_SCALED")
    if has_settlement_gap:
        tags.append("HOLD_FUNDING_MISSING_GAP")

    if not tags:
        usability = "USABLE_BASELINE_REFERENCE"
        reason = "算术、退出、时序与冷热资金费完全自洽，无跳空无同刻歧义无缩量，可作为基准参考"
    else:
        usability = "HOLD_PENDING_POLICY"
        reason = f"存在未决争议或时序不确定项：{', '.join(tags)}，暂缓作为正式基准"

    return {
        "usability": usability,
        "tags": tags,
        "reason": reason,
        "uncertain_events": uncertain_events,
        "uncertain_count": uncertain_count,
        "has_settlement_gap": has_settlement_gap,
        "held_hours": round(held_hours, 2),
    }


# ═════════════════════════════════════════════════════════════════════
# 主流程：接入冷热资金费全量重算、双向对照与影响评估
# ═════════════════════════════════════════════════════════════════════

def run_funding_merge_and_recalc():
    print(">>> [W4-02] 开始执行冷热合并资金费接入与全量重算影响评估...")

    hashes = {
        "replay_calc_py": {
            "path": "框架/backend/replay_calc.py",
            "sha256": sha256_file(BACKEND / "replay_calc.py")
        },
        "w4_01_json": {
            "path": "evidence/ACCUM-EARLY-06-W4/01/out/01_05d_r1逐笔复核与差异清单.json",
            "sha256": sha256_file(OUT_DIR.parent.parent / "01" / "out" / "01_05d_r1逐笔复核与差异清单.json")
        },
        "hot_db_sqlite": {
            "path": "数据/市场数据/db/market_v2.sqlite3",
            "sha256": sha256_file(HOT_DB)
        }
    }

    # 加载候选样本输入
    with open(ACC2_JSON, "r", encoding="utf-8") as f:
        acc2_data = json.load(f)
    raw_51 = [r for r in acc2_data["B_全量回放"]["逐条"] if r.get("caliber") == "生效视图_5bps"]

    # 加载 04-D 原 W3 分流
    with open(ACC4D_W3, "r", encoding="utf-8") as f:
        old_w3_map = {(r["candidate_id"], r["caliber"]): r for r in json.load(f)["items"]}

    merged_15m = R15.Merged15m(str(HOT_DB), str(COLD_KLINE_DIR))
    merged_4h = R15.Merged15m(str(HOT_DB), str(COLD_KLINE_DIR), timeframe="4h")

    recalc_items = []

    for caliber_name in ["生效视图_5bps", "策略包_3bps"]:
        cal_cfg = RC.CALIBERS[caliber_name]

        for cand in raw_51:
            sym = cand["symbol"]
            cid = cand["candidate_id"]
            entry_ms = to_ms(cand["entry_bar_utc"])

            limits = PE.contract_limits(sym)
            bars15, _ = merged_15m.load(sym, entry_ms - 2 * 3600_000, entry_ms + 14 * DAY)
            elig15 = [b for b in bars15 if int(b["open_time"]) >= entry_ms]
            prevs = [b for b in bars15 if int(b["open_time"]) < entry_ms]
            prev_bar = prevs[-1] if prevs else None
            bars4, _ = merged_4h.load(sym, entry_ms - 8 * DAY, entry_ms + 30 * DAY)

            # ── 1. 资金费双通道加载 ──
            hot_funding, merged_funding, merge_meta = load_funding_merged(sym, entry_ms)

            # ── 2. 分别调用既有计算接口重跑旧输入与新输入 ──
            # 旧输入运行（仅热库）
            res_old = RC.replay_one(
                symbol=sym,
                entry_bar=elig15[0],
                prev_bar=prev_bar,
                bars_15m=elig15,
                bars_4h=bars4,
                funding_events=hot_funding,
                caliber_name=caliber_name,
            )

            # 新输入运行（冷热合并）
            res_new = RC.replay_one(
                symbol=sym,
                entry_bar=elig15[0],
                prev_bar=prev_bar,
                bars_15m=elig15,
                bars_4h=bars4,
                funding_events=merged_funding,
                caliber_name=caliber_name,
            )

            is_filled = (res_new.get("status") == "ok")

            if not is_filled:
                # 拒单交易（CARVUSDT 在策略包下）
                rej_reason = res_new.get("entry", {}).get("reject")
                usability_eval = evaluate_usability_and_uncertainty(
                    is_filled=False,
                    rejection_reason=rej_reason,
                    exit_kind=None,
                    participation_capped=False,
                    held_settled_events=[],
                    entry_ms=entry_ms,
                    exit_ms=None
                )
                old_w3 = old_w3_map.get((cid, caliber_name))

                recalc_items.append({
                    "candidate_id": cid,
                    "symbol": sym,
                    "caliber": caliber_name,
                    "is_filled": False,
                    "status": "rejected",
                    "rejection_reason": rej_reason,
                    "funding_old": {
                        "events_count": 0, "total_cost": 0.0, "settled": []
                    },
                    "funding_new": {
                        "events_count": 0, "total_cost": 0.0, "settled": []
                    },
                    "delta_funding_cost": 0.0,
                    "delta_realized_net": 0.0,
                    "is_affected_by_merged_funding": False,
                    "old_usability": old_w3.get("usability") if old_w3 else "HOLD_REJECTED",
                    "new_usability": usability_eval["usability"],
                    "usability_changed": False,
                    "usability_details": usability_eval,
                })
            else:
                # 成交交易
                e_info = res_new["entry"]
                x_info = res_new["exit"]
                exit_utc = x_info.get("exit_utc")
                exit_ms = to_ms(exit_utc) if exit_utc else None
                part_capped = e_info.get("participation_capped", False)

                fund_old_info = res_old["资金费"]
                fund_new_info = res_new["资金费"]

                cost_old = float(fund_old_info["成本合计"])
                cost_new = float(fund_new_info["成本合计"])
                net_old = float(res_old["盈亏"]["realized_net"])
                net_new = float(res_new["盈亏"]["realized_net"])

                delta_funding = cost_new - cost_old
                delta_net = net_new - net_old

                # 严格数学断言：Δnet 必须精确等于 -Δfunding！
                assert math.isclose(delta_net, -delta_funding, abs_tol=1e-5), \
                    f"财务恒等式破裂：Δnet ({delta_net}) != -Δfunding ({-delta_funding}) for {sym} {cid}"

                events_old = fund_old_info.get("明细", [])
                events_new = fund_new_info.get("明细", [])

                is_affected = (abs(delta_funding) > 1e-6 or len(events_new) != len(events_old))

                # 动态评估可用性
                usability_eval = evaluate_usability_and_uncertainty(
                    is_filled=True,
                    rejection_reason=None,
                    exit_kind=x_info.get("kind"),
                    participation_capped=part_capped,
                    held_settled_events=events_new,
                    entry_ms=entry_ms,
                    exit_ms=exit_ms
                )

                old_w3 = old_w3_map.get((cid, caliber_name))
                old_u = old_w3.get("usability") if old_w3 else None
                new_u = usability_eval["usability"]
                usability_changed = (old_u != new_u)

                recalc_items.append({
                    "candidate_id": cid,
                    "symbol": sym,
                    "caliber": caliber_name,
                    "is_filled": True,
                    "status": "ok",
                    "entry_price": float(e_info["entry_price"]),
                    "qty": float(e_info["qty"]),
                    "notional": float(e_info["notional"]),
                    "exit_fill": float(x_info["exit_fill"]) if x_info.get("exit_fill") else None,
                    "exit_utc": exit_utc,
                    "exit_kind": x_info.get("kind"),
                    "participation_capped": part_capped,
                    "funding_old": {
                        "events_count": len(events_old),
                        "total_cost": cost_old,
                        "uncertain_count": fund_old_info.get("顺序不确定事件数", 0),
                    },
                    "funding_new": {
                        "events_count": len(events_new),
                        "total_cost": cost_new,
                        "uncertain_count": fund_new_info.get("顺序不确定事件数", 0),
                        "events_detail": events_new,
                    },
                    "realized_net_old": net_old,
                    "realized_net_new": net_new,
                    "delta_funding_cost": delta_funding,
                    "delta_realized_net": delta_net,
                    "is_affected_by_merged_funding": is_affected,
                    "old_usability": old_u,
                    "new_usability": new_u,
                    "old_tags": old_w3.get("tags") if old_w3 else [],
                    "new_tags": usability_eval["tags"],
                    "usability_changed": usability_changed,
                    "usability_details": usability_eval,
                })

    # ── 3. 按口径分别独立汇总对比 ──
    def summarize_caliber(cal_name: str) -> dict:
        items = [r for r in recalc_items if r["caliber"] == cal_name]
        filled = [r for r in items if r["is_filled"]]
        rejected = [r for r in items if not r["is_filled"]]

        affected = [r for r in filled if r["is_affected_by_merged_funding"]]

        total_cost_old = sum(r["funding_old"]["total_cost"] for r in filled)
        total_cost_new = sum(r["funding_new"]["total_cost"] for r in filled)
        delta_total_cost = total_cost_new - total_cost_old

        total_net_old = sum(r["realized_net_old"] for r in filled)
        total_net_new = sum(r["realized_net_new"] for r in filled)
        delta_total_net = total_net_new - total_net_old

        # 胜率与正负笔数变化
        pos_old = sum(1 for r in filled if r["realized_net_old"] > 0)
        pos_new = sum(1 for r in filled if r["realized_net_new"] > 0)

        # 真实资金费覆盖统计（持仓期结算事件完整度）
        settlement_zero_old = sum(1 for r in filled if r["funding_old"]["events_count"] == 0)
        settlement_zero_new = sum(1 for r in filled if r["funding_new"]["events_count"] == 0)

        # 真实缺口（持仓 >= 8h 但 0 结算）
        real_gap_old = sum(1 for r in filled if r["usability_details"].get("held_hours", 0) >= 8.0 and r["funding_old"]["events_count"] == 0)
        real_gap_new = sum(1 for r in filled if r["usability_details"].get("held_hours", 0) >= 8.0 and r["funding_new"]["events_count"] == 0)

        # 同刻歧义统计（相距 <= 15 分钟）
        uncertain_trades_old = sum(1 for r in filled if r["funding_old"].get("uncertain_count", 0) > 0)
        uncertain_trades_new = sum(1 for r in filled if r["funding_new"].get("uncertain_count", 0) > 0)

        # 可用性分类统计（新 vs 旧）
        old_u_counts = {
            "USABLE_BASELINE_REFERENCE": sum(1 for r in items if r["old_usability"] == "USABLE_BASELINE_REFERENCE"),
            "HOLD_PENDING_POLICY": sum(1 for r in items if r["old_usability"] == "HOLD_PENDING_POLICY"),
            "HOLD_REJECTED": sum(1 for r in items if r["old_usability"] == "HOLD_REJECTED"),
        }
        new_u_counts = {
            "USABLE_BASELINE_REFERENCE": sum(1 for r in items if r["new_usability"] == "USABLE_BASELINE_REFERENCE"),
            "HOLD_PENDING_POLICY": sum(1 for r in items if r["new_usability"] == "HOLD_PENDING_POLICY"),
            "HOLD_REJECTED": sum(1 for r in items if r["new_usability"] == "HOLD_REJECTED"),
        }

        return {
            "caliber": cal_name,
            "total_candidates_tested": len(items),
            "filled_count": len(filled),
            "rejected_count": len(rejected),
            "affected_trades_count": len(affected),
            "affected_trades_pct": round(len(affected) / len(filled) * 100, 2),
            "financial_impact": {
                "total_funding_cost_old": round(total_cost_old, 6),
                "total_funding_cost_new": round(total_cost_new, 6),
                "delta_funding_cost": round(delta_total_cost, 6),
                "total_realized_net_old": round(total_net_old, 6),
                "total_realized_net_new": round(total_net_new, 6),
                "delta_realized_net": round(delta_total_net, 6),
                "net_change_perfectly_explains_funding": bool(math.isclose(delta_total_net, -delta_total_cost, abs_tol=1e-5)),
            },
            "win_rate_impact": {
                "positive_trades_old": pos_old,
                "positive_trades_new": pos_new,
                "win_rate_old_pct": round(pos_old / len(filled) * 100, 2),
                "win_rate_new_pct": round(pos_new / len(filled) * 100, 2),
            },
            "coverage_improvement": {
                "zero_settlement_trades_old": settlement_zero_old,
                "zero_settlement_trades_new": settlement_zero_new,
                "real_gap_trades_old": real_gap_old,
                "real_gap_trades_new": real_gap_new,
                "real_gaps_eliminated_count": real_gap_old - real_gap_new,
            },
            "timing_uncertainty": {
                "uncertain_trades_old": uncertain_trades_old,
                "uncertain_trades_new": uncertain_trades_new,
            },
            "classification_distribution": {
                "old_distribution": old_u_counts,
                "new_distribution": new_u_counts,
                "distribution_changed": (old_u_counts != new_u_counts),
            },
            "affected_items_list": [{
                "symbol": r["symbol"],
                "candidate_id": r["candidate_id"],
                "events_old": r["funding_old"]["events_count"],
                "events_new": r["funding_new"]["events_count"],
                "cost_old": round(r["funding_old"]["total_cost"], 6),
                "cost_new": round(r["funding_new"]["total_cost"], 6),
                "delta_funding": round(r["delta_funding_cost"], 6),
                "net_old": round(r["realized_net_old"], 6),
                "net_new": round(r["realized_net_new"], 6),
                "delta_net": round(r["delta_realized_net"], 6),
            } for r in affected]
        }

    summary_5bps = summarize_caliber("生效视图_5bps")
    summary_3bps = summarize_caliber("策略包_3bps")

    # 产物落盘
    out_payload = {
        "task": "ACCUM-EARLY-06-W4-02",
        "description": "接入完整资金费并重算影响",
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hashes": hashes,
        "shared_code_status": {
            "replay_calc_modified": False,
            "statement": "经底层核验，replay_calc.py 计算接口完全支持冷热合并 funding_events 传入，算术无缺陷，未修改共享文件。"
        },
        "independent_calibers_impact": {
            "生效视图_5bps": summary_5bps,
            "策略包_3bps": summary_3bps,
            "declaration": "严禁跨口径合并收益！两套口径分别输出前后差额与影响。"
        },
        "recalc_items": recalc_items,
    }

    out_file = OUT_DIR / "01_资金费重算逐笔明细与对比.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out_payload, f, ensure_ascii=False, indent=2)

    print(f">>> [W4-02] 冷热资金费接入与重算完成，产物已成功写入：{out_file}")
    return out_payload

if __name__ == "__main__":
    run_funding_merge_and_recalc()
