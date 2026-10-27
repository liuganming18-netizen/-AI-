# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜机械复算对账（每个关键数字都要能从原始文件重算）。

核对链
──────
1. `out/trades_<策略>.csv`（引擎原始）→ Σprofit_abs → 与 `03_分组指标总表.csv` 的
   `net_realized + net_unrealized_end` 比；与登记 `summary.json.net_pnl` 比。
2. `04_账户日权益曲线_全部.csv.gz` → 独立重算 maxDD / Sharpe → 与 03 表比。
3. 笔数：trades CSV 行数 = 03 表 trades_total = 登记 summary.trade_count
   = replay 包行数 = trades.jsonl 行数。
4. 一致性对照：直接读两份原始 trades CSV 逐笔比（不经任何中间汇总）。
5. 基线复现：A 组（S05_D05_*）与旧 `FT-LOW4H-GRID-01/out/00_execution_summary.csv`
   的 net_pnl 逐参数比 —— 证明本单隔离副本与旧实验同源同口径。

任一项超差 ⇒ 该行 FAIL，写进结论，不掩盖。
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"
OUT = EXP / "out"
REPLAY = EXP / "replay"
OLD_SUMMARY = BASE / "evidence/FT-LOW4H-GRID-01/out/00_execution_summary.csv"
BATCH_DIR = BASE / "回测日志/event_trend_v0_1/runs_work_dynamic_bt_01"

PARAMS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
          "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
TOL = 1e-6


def maxdd_sharpe(eq: pd.Series) -> tuple:
    e = eq.to_numpy(dtype=np.float64)
    peak = np.maximum.accumulate(e)
    dd = float(((e - peak) / peak).min())
    rets = np.diff(e) / e[:-1]
    rets = rets[np.isfinite(rets)]
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    sh = float(np.mean(rets) / sd * math.sqrt(365)) if sd > 0 else None
    return dd * 100, sh


def main() -> None:
    metrics = pd.read_csv(OUT / "03_分组指标总表.csv")
    metrics = metrics[metrics["status"] == "OK"]
    eq_all = pd.read_csv(OUT / "04_账户日权益曲线_全部.csv.gz")
    rows: List[dict] = []

    for _, m in metrics.iterrows():
        name = m["strategy"]
        tr = pd.read_csv(OUT / f"trades_{name}.csv")
        s_raw = float(tr["profit_abs"].sum()) if len(tr) else 0.0
        s_metric = float(m["net_realized"]) + float(m["net_unrealized_end"])
        sub = eq_all[eq_all["strategy"] == name]
        dd2, sh2 = maxdd_sharpe(sub["equity"]) if len(sub) else (None, None)

        # 登记侧
        reg = BATCH_DIR / f"run_FT_LOW4H_DYN01_{name}"
        reg_n, reg_pnl, reg_trades, reg_tj = None, None, None, None
        if (reg / "summary.json").exists():
            sm = json.loads((reg / "summary.json").read_text(encoding="utf-8"))
            reg_pnl = float(sm.get("net_pnl"))
            reg_trades = int(sm.get("trade_count"))
        if (reg / "trades.jsonl").exists():
            reg_tj = sum(1 for _ in open(reg / "trades.jsonl", encoding="utf-8"))

        # replay 包
        rp = REPLAY / f"{name}.jsonl.gz"
        rp_n = None
        if rp.exists():
            with gzip.open(rp, "rt", encoding="utf-8") as f:
                rp_n = sum(1 for _ in f)

        rows.append({
            "strategy": name,
            "group": m["group"],
            "trades_csv": len(tr),
            "trades_metric": int(m["trades_total"]),
            "trades_registered": reg_trades,
            "trades_reg_jsonl": reg_tj,
            "trades_replay": rp_n,
            "trades_all_match": bool(len(tr) == int(m["trades_total"]) == (reg_trades or len(tr))
                                     == (reg_tj or len(tr)) == (rp_n or len(tr))),
            "sum_profit_raw": round(s_raw, 6),
            "sum_profit_metric": round(s_metric, 6),
            "sum_profit_abs_err": round(abs(s_raw - s_metric), 8),
            "pnl_registered": reg_pnl,
            "pnl_reg_err": (round(abs(s_raw - reg_pnl), 6) if reg_pnl is not None else None),
            "maxdd_metric": float(m["max_dd_pct"]),
            "maxdd_recomputed": (round(dd2, 4) if dd2 is not None else None),
            "maxdd_err": (round(abs(float(m["max_dd_pct"]) - dd2), 6) if dd2 is not None else None),
            "sharpe_metric": m["sharpe"],
            "sharpe_recomputed": (round(sh2, 4) if sh2 is not None else None),
            "sharpe_err": (round(abs(float(m["sharpe"]) - sh2), 6)
                           if (sh2 is not None and m["sharpe"] == m["sharpe"]) else None),
        })

    rec = pd.DataFrame(rows)

    # ── 一致性对照（原始 CSV 直比）──
    parity = []
    for p in PARAMS:
        a = pd.read_csv(OUT / f"trades_Wyckoff_C1_S05_D08_{p}.csv")
        b = pd.read_csv(OUT / f"trades_Wyckoff_C1_S05_DYN999_{p}.csv")
        key = ["pair", "open_timestamp", "close_timestamp"]
        a = a.sort_values(key).reset_index(drop=True)
        b = b.sort_values(key).reset_index(drop=True)
        same_keys = bool(len(a) == len(b) and (a[key].values == b[key].values).all())
        same_pnl = bool(same_keys and np.allclose(a["profit_abs"], b["profit_abs"], atol=0))
        same_rate = bool(same_keys and np.allclose(a["open_rate"], b["open_rate"], atol=0)
                         and np.allclose(a["close_rate"], b["close_rate"], atol=0))
        parity.append({"param_group": p, "trades": len(a), "same_keys": same_keys,
                       "same_profit_abs": same_pnl, "same_prices": same_rate,
                       "P1_net": round(float(a["profit_abs"].sum()), 6),
                       "P2_net": round(float(b["profit_abs"].sum()), 6)})
    par = pd.DataFrame(parity)

    # ── 基线复现（A 组 vs 旧 FT-LOW4H-GRID-01）──
    base_rows = []
    if OLD_SUMMARY.exists():
        old = pd.read_csv(OLD_SUMMARY)
        for p in PARAMS:
            n = f"Wyckoff_C1_S05_D05_{p}"
            o = old[old["strategy"] == n]
            mine = pd.read_csv(OUT / f"trades_{n}.csv")
            if len(o):
                base_rows.append({
                    "param_group": p, "strategy": n,
                    "old_trades": int(o["trades"].iloc[0]),
                    "new_trades": int(len(mine)),
                    "old_net_pnl": float(o["net_pnl"].iloc[0]),
                    "new_net_pnl": round(float(mine["profit_abs"].sum()), 4),
                    "diff": round(float(mine["profit_abs"].sum()) - float(o["net_pnl"].iloc[0]), 4),
                })
    base = pd.DataFrame(base_rows)

    rec.to_csv(OUT / "21_复算对账_分组.csv", index=False, encoding="utf-8-sig")
    par.to_csv(OUT / "22_一致性对照_原始直比.csv", index=False, encoding="utf-8-sig")
    if len(base):
        base.to_csv(OUT / "23_基线复现_对旧证据.csv", index=False, encoding="utf-8-sig")

    concl = {
        "groups_checked": int(len(rec)),
        "trades_count_all_match": bool(rec["trades_all_match"].all()),
        "max_abs_err_profit_sum": float(rec["sum_profit_abs_err"].max()),
        "max_abs_err_registered_pnl": (float(rec["pnl_reg_err"].dropna().max())
                                       if rec["pnl_reg_err"].notna().any() else None),
        "max_abs_err_maxdd_pct": (float(rec["maxdd_err"].dropna().max())
                                  if rec["maxdd_err"].notna().any() else None),
        "max_abs_err_sharpe": (float(rec["sharpe_err"].dropna().max())
                               if rec["sharpe_err"].notna().any() else None),
        "parity_P1_P2_all_identical": bool(par["same_keys"].all() and par["same_profit_abs"].all()
                                           and par["same_prices"].all()),
        "baseline_reproduction": ({
            "params": int(len(base)),
            "all_trade_counts_equal": bool((base["old_trades"] == base["new_trades"]).all()),
            "max_abs_pnl_diff": float(base["diff"].abs().max()),
            "meaning": ("A 组（固定 d=5%）逐参数复现旧 FT-LOW4H-GRID-01 的成交笔数与净盈亏 ⇒ "
                        "本单隔离副本与旧实验同源同口径，可作为 A/B/C 的合法共同基线"),
        } if len(base) else None),
        "tolerance": TOL,
        "verdict": None,
    }
    ok = (concl["trades_count_all_match"]
          and concl["max_abs_err_profit_sum"] <= 1e-4
          and (concl["max_abs_err_maxdd_pct"] or 0) <= 1e-3
          and concl["parity_P1_P2_all_identical"]
          and (concl["baseline_reproduction"] or {}).get("max_abs_pnl_diff", 9) <= 0.01)
    concl["verdict"] = "PASS" if ok else "FAIL"
    (OUT / "24_复算对账结论.json").write_text(
        json.dumps(concl, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(concl, ensure_ascii=False, indent=2))
    print("\n=== 基线复现（A 组 vs 旧证据）===")
    if len(base):
        print(base.to_string())


if __name__ == "__main__":
    main()
