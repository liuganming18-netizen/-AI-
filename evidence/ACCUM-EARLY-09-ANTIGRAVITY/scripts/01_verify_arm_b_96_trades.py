# -*- coding: utf-8 -*-
"""ACCUM-EARLY-09 任务 A：ARM B 4a 96 笔成交样本逐行核对与 88/96 缺口归因。

严格规则：
1. 样本源：ARM B 4a 隔离原始底层账本 (paper_4a.sqlite3::paper_realized) 共 96 笔已实现平仓。
2. 逐行对齐市场微观指标 (Top L/S, Taker Vol Ratio)。
3. 当时可得性确证缺失，一律 available_at=null, availability="unproven"。
4. 明确归因 8 笔未进入 Taker 分层的原因并逐笔列示。
5. 严格对账：分母、分组计数、分组净额与未归组净额。
"""

import os
import sqlite3
import csv
import json
import datetime
import pyarrow.parquet as pq
import numpy as np

REPO_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
WORKTREE_ROOT = "/Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/evidence_correction_and_review"
DB_PATH = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/paper/paper_4a.sqlite3")
PARQUET_ROOT = os.path.join(REPO_ROOT, "数据/市场数据/parquet/metrics/binance/um")
OUT_CSV = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-09-ANTIGRAVITY/arm_b_96笔逐行核对.csv")
OUT_JSON = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-09-ANTIGRAVITY/out/arm_b_96笔核对摘要.json")

os.makedirs(os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-09-ANTIGRAVITY/out"), exist_ok=True)

con = sqlite3.connect(DB_PATH)
cur = con.cursor()
rows = cur.execute("""
    SELECT r.id, r.account_id, r.position_id, r.symbol, r.qty, r.entry_price, r.exit_price,
           r.gross_pnl, r.entry_fee, r.exit_fee, r.funding_paid, r.net_pnl,
           r.entry_ts, r.exit_ts, r.exit_reason, p.signal_uid
    FROM paper_realized r
    LEFT JOIN paper_position p ON r.position_id = p.position_id
    ORDER BY r.position_id
""").fetchall()
con.close()

results = []
top_ls_values = []

for r in rows:
    (r_id, acc_id, pos_id, sym, qty, en_p, ex_p,
     gross, en_fee, ex_fee, funding, net,
     en_ts, ex_ts, reason, sig_uid) = r
    
    parquet_file = os.path.join(PARQUET_ROOT, sym, "2026.parquet")
    top_ls = None
    taker_vol = None
    metric_ts = None
    metric_utc = None
    stale_hours = None
    exclude_reason = None
    
    if not os.path.exists(parquet_file):
        exclude_reason = "PARQUET_FILE_NOT_FOUND"
    else:
        tbl = pq.read_table(parquet_file)
        ts_arr = tbl["ts"].to_numpy()
        idx = np.where(ts_arr <= en_ts)[0]
        if len(idx) == 0:
            exclude_reason = "NO_METRIC_BEFORE_ENTRY"
        else:
            match_idx = idx[-1]
            metric_ts = int(ts_arr[match_idx])
            metric_utc = datetime.datetime.fromtimestamp(metric_ts / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            top_ls = tbl["top_position_ls_ratio"][match_idx].as_py()
            taker_vol = tbl["sum_taker_long_short_vol_ratio"][match_idx].as_py()
            diff_hours = (en_ts - metric_ts) / 3600000.0
            stale_hours = round(diff_hours, 2)
            
            # 判断排除原因
            if taker_vol is None:
                if diff_hours > 2.0:
                    exclude_reason = f"TAKER_VOL_NULL_AND_STALE_{stale_hours}H"
                else:
                    exclude_reason = "TAKER_VOL_NULL_IN_METRIC"
    
    en_utc = datetime.datetime.fromtimestamp(en_ts / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ex_utc = datetime.datetime.fromtimestamp(ex_ts / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # 公式核对：net_pnl = gross - en_fee - ex_fee - funding_paid
    calc_net = gross - en_fee - ex_fee - funding
    residual = round(net - calc_net, 10)
    
    if top_ls is not None:
        top_ls_values.append(top_ls)
        
    results.append({
        "position_id": pos_id,
        "symbol": sym,
        "signal_uid": sig_uid or f"accum05_4a_{sym}_{en_ts}",
        "entry_utc": en_utc,
        "exit_utc": ex_utc,
        "entry_price": en_p,
        "exit_price": ex_p,
        "qty": qty,
        "gross_pnl": round(gross, 4),
        "entry_fee": round(en_fee, 4),
        "exit_fee": round(ex_fee, 4),
        "funding_paid": round(funding, 4),
        "net_pnl": round(net, 4),
        "calc_net_pnl": round(calc_net, 4),
        "residual": residual,
        "exit_reason": reason,
        "top_position_ls_ratio": top_ls,
        "sum_taker_long_short_vol_ratio": taker_vol,
        "metric_observed_utc": metric_utc,
        "metric_stale_hours": stale_hours,
        "available_at": None,            # 无确凿发布/抓取时点证据，严格填 null
        "availability": "unproven",      # 离线归档，无实时可得证明
        "taker_group_status": "included" if taker_vol is not None else "excluded",
        "taker_exclude_reason": exclude_reason
    })

# 中位数计算与分组
median_top_ls = float(np.median(top_ls_values))

# 写入 CSV
fieldnames = list(results[0].keys()) + ["top_ls_group", "taker_vol_group"]

for r in results:
    if r["top_position_ls_ratio"] is not None:
        r["top_ls_group"] = "top_ls_gte_median" if r["top_position_ls_ratio"] >= median_top_ls else "top_ls_lt_median"
    else:
        r["top_ls_group"] = "unassigned"
        
    if r["sum_taker_long_short_vol_ratio"] is not None:
        r["taker_vol_group"] = "taker_gte_1" if r["sum_taker_long_short_vol_ratio"] >= 1.0 else "taker_lt_1"
    else:
        r["taker_vol_group"] = "excluded_missing"

with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)

# 汇总重算与对账
def strat_stats(group_rows):
    cnt = len(group_rows)
    if cnt == 0:
        return {"count": 0, "win_rate": 0, "sum_pnl": 0, "avg_pnl": 0}
    wins = sum(1 for x in group_rows if x["net_pnl"] > 0)
    sum_p = sum(x["net_pnl"] for x in group_rows)
    return {
        "count": cnt,
        "wins": wins,
        "win_rate": round(wins / cnt, 6),
        "sum_pnl": round(sum_p, 4),
        "avg_pnl": round(sum_p / cnt, 4)
    }

top_gte = [x for x in results if x["top_ls_group"] == "top_ls_gte_median"]
top_lt = [x for x in results if x["top_ls_group"] == "top_ls_lt_median"]

taker_gte = [x for x in results if x["taker_vol_group"] == "taker_gte_1"]
taker_lt = [x for x in results if x["taker_vol_group"] == "taker_lt_1"]
taker_excl = [x for x in results if x["taker_vol_group"] == "excluded_missing"]

summary = {
    "total_sample_size": len(results),
    "total_net_pnl": round(sum(x["net_pnl"] for x in results), 4),
    "top_position_ls_ratio_median": median_top_ls,
    "top_ls_stratification": {
        "gte_median": strat_stats(top_gte),
        "lt_median": strat_stats(top_lt),
        "sum_check": round(sum(x["net_pnl"] for x in top_gte) + sum(x["net_pnl"] for x in top_lt), 4),
        "count_check": len(top_gte) + len(top_lt)
    },
    "taker_vol_stratification": {
        "gte_1_0": strat_stats(taker_gte),
        "lt_1_0": strat_stats(taker_lt),
        "excluded_gap_8_samples": strat_stats(taker_excl),
        "reconciliation": {
            "sum_groups_plus_gap": round(sum(x["net_pnl"] for x in taker_gte) + sum(x["net_pnl"] for x in taker_lt) + sum(x["net_pnl"] for x in taker_excl), 4),
            "total_net_pnl": round(sum(x["net_pnl"] for x in results), 4),
            "residual": round((sum(x["net_pnl"] for x in taker_gte) + sum(x["net_pnl"] for x in taker_lt) + sum(x["net_pnl"] for x in taker_excl)) - sum(x["net_pnl"] for x in results), 6),
            "count_reconciliation": f"{len(taker_gte)} + {len(taker_lt)} + {len(taker_excl)} = {len(taker_gte) + len(taker_lt) + len(taker_excl)}"
        }
    },
    "gap_8_details": [
        {
            "position_id": x["position_id"],
            "symbol": x["symbol"],
            "entry_utc": x["entry_utc"],
            "net_pnl": x["net_pnl"],
            "top_ls": x["top_position_ls_ratio"],
            "taker_vol": x["sum_taker_long_short_vol_ratio"],
            "metric_observed_utc": x["metric_observed_utc"],
            "stale_hours": x["metric_stale_hours"],
            "reason": x["taker_exclude_reason"]
        } for x in taker_excl
    ]
}

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

print(f"Verified {len(results)} trades.")
print(f"Top L/S Median: {median_top_ls:.4f}")
print(f"Top L/S Groups: GTE={len(top_gte)} (Sum={sum(x['net_pnl'] for x in top_gte):.2f}), LT={len(top_lt)} (Sum={sum(x['net_pnl'] for x in top_lt):.2f})")
print(f"Taker Groups: GTE={len(taker_gte)} (Sum={sum(x['net_pnl'] for x in taker_gte):.2f}), LT={len(taker_lt)} (Sum={sum(x['net_pnl'] for x in taker_lt):.2f}), Excluded={len(taker_excl)} (Sum={sum(x['net_pnl'] for x in taker_excl):.2f})")
print(f"Reconciliation Residual: {summary['taker_vol_stratification']['reconciliation']['residual']}")
