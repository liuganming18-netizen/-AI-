# -*- coding: utf-8 -*-
"""06 K 线抽核：每档随机 20 笔共 80 笔（种子 20261022）。

核 6 件事：
  1 入场价落在入场那根 15m 的 [low, high] 内
  2 初始止损 = 入场价 ×(1 − 档位比例)
  3 2R 激活所用的 4h K 线，其收盘时刻 ≤ 决策时刻
  4 账面止损与按 4h 重建的棘轮止损一致（≤1.5 tick）
  5 止损退出：该根 15m 的 low ≤ 止损 ≤ high，且上一根 low > 止损（首次触及）
  6 期末结算：成交价 = 该根 15m 的开盘价
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
SEED = 20261022
PER_TIER = 20


def bar_at(df: pd.DataFrame, ms: int):
    i = int(np.searchsorted(df["ms"].to_numpy(), ms, side="left"))
    if i >= len(df) or int(df["ms"].iloc[i]) != ms:
        return None, i
    return df.iloc[i], i


def main() -> None:
    df, missing = L.load_all()
    d = L.equity_curve(df)
    rng = np.random.default_rng(SEED)

    rows = []
    for t in L.TIERS:
        s = d[d["档位"] == t]
        if s.empty:
            continue
        n = min(PER_TIER, len(s))
        pick = s.sample(n=n, random_state=int(rng.integers(0, 2 ** 31 - 1)))
        for _, r in pick.iterrows():
            pct = float(r["止损比例"])
            k = L.load_kline(r["pair"], "15m")
            rec = {
                "策略": r["策略"], "档位": t, "pair": r["pair"],
                "open_date": r["open_date"], "close_date": r["close_date"],
                "exit_reason": r["exit_reason"],
                "open_rate": float(r["open_rate"]), "close_rate": float(r["close_rate"]),
                "amount": float(r["amount"]),
            }
            if k is None:
                rec["状态"] = "无K线"
                rows.append(rec)
                continue
            b_open, i_open = bar_at(k, int(r["open_ms"]))
            b_exit, i_exit = bar_at(k, int(r["close_ms"]))
            rec["入场价在K线内"] = bool(b_open is not None and
                                   float(b_open["low"]) - 1e-12 <= rec["open_rate"] <= float(b_open["high"]) + 1e-12)
            if b_open is not None:
                rec["入场K线low"] = float(b_open["low"])
                rec["入场K线high"] = float(b_open["high"])
                rec["入场价距low_bp"] = float((rec["open_rate"] - float(b_open["low"])) / float(b_open["low"]) * 1e4)
                rec["入场价距high_bp"] = float((float(b_open["high"]) - rec["open_rate"]) / float(b_open["high"]) * 1e4)

            init_stop = rec["open_rate"] * (1.0 - pct)
            rec["初始止损公式值"] = init_stop
            tick = L.tick_of(str(r["open_rate"]))
            rec["最小价位"] = tick

            tp = L.trailing_path(r["pair"], rec["open_rate"], pct, int(r["open_ms"]), int(r["close_ms"]))
            rec["2R激活"] = bool(tp.get("activated"))
            rec["2R触发价"] = tp.get("trigger_2r")
            rec["1R距离"] = tp.get("actual_r")
            rec["棘轮步数"] = tp.get("ratchet_steps")
            rec["重建止损"] = tp.get("final_stop")
            rec["账面止损"] = float(r["stop_loss_abs"])
            rec["止损偏差tick"] = (float(r["stop_loss_abs"]) - float(tp.get("final_stop") or init_stop)) / tick \
                if tick else None
            # 2R 激活所用 4h 的可用性
            act_ms = tp.get("activate_ms")
            rec["激活4h收盘时刻"] = act_ms
            rec["激活4h收盘≤退出K线开盘"] = bool(act_ms is None or act_ms <= int(r["close_ms"]))

            if b_exit is not None:
                lo, hi = float(b_exit["low"]), float(b_exit["high"])
                rec["退出K线low"] = lo
                rec["退出K线high"] = hi
                rec["退出K线open"] = float(b_exit["open"])
                rec["止损落在退出K线内"] = bool(lo - 1e-12 <= float(r["stop_loss_abs"]) <= hi + 1e-12)
                # 首次触及：上一根 low 必须 > 账面止损
                if i_exit > 0:
                    prev = k.iloc[i_exit - 1]
                    rec["上一根low"] = float(prev["low"])
                    rec["上一根未破止损"] = bool(float(prev["low"]) > float(r["stop_loss_abs"]))
                if str(r["exit_reason"]) == "force_exit":
                    rec["期末结算价等于K线开盘"] = bool(abs(rec["close_rate"] - float(b_exit["open"])) < 1e-12)
                else:
                    rec["期末结算价等于K线开盘"] = None
            rec["状态"] = "ok"
            rows.append(rec)

    det = pd.DataFrame(rows)
    det.to_csv(OUT / "06_抽核明细.csv", index=False, encoding="utf-8-sig")

    ok = det[det["状态"] == "ok"]
    res = {
        "工单": "FT-STOP-GRID-AUDIT-02 / 06 K 线抽核",
        "种子": SEED, "每档笔数": PER_TIER,
        "抽核笔数": int(len(ok)), "分档": ok["档位"].value_counts().to_dict() if len(ok) else {},
        "1_入场价落在入场K线内": {
            "通过": int(ok["入场价在K线内"].sum()), "总": int(len(ok)),
            "最大距low_bp": float(ok["入场价距low_bp"].min()) if len(ok) else None,
            "最大距high_bp": float(ok["入场价距high_bp"].min()) if len(ok) else None,
        },
        "2_初始止损公式": {
            "说明": "账面止损 ≥ 公式值 − 1 tick；已上移的笔必然高于公式值",
            "低于公式值超1tick笔数": int((ok["止损偏差tick"] < -1).sum()),
            "等于公式值±1tick笔数": int((ok["止损偏差tick"].abs() <= 1).sum()),
            "高于公式值超1tick笔数": int((ok["止损偏差tick"] > 1).sum()),
        },
        "3_2R激活与棘轮": {
            "激活笔数": int(ok["2R激活"].sum()),
            "激活4h收盘≤退出K线开盘": int(ok["激活4h收盘≤退出K线开盘"].sum()),
            "激活4h收盘>退出K线开盘": int((~ok["激活4h收盘≤退出K线开盘"].astype(bool)).sum()),
            "账面止损与重建一致(≤1.5tick)": int((ok["止损偏差tick"].abs() <= 1.5).sum()),
            "账面止损与重建不一致": int((ok["止损偏差tick"].abs() > 1.5).sum()),
        },
        "4_止损退出K线": {
            "止损退出笔数": int(ok["exit_reason"].astype(str).isin(L.STOP_LABELS).sum()),
            "止损落在退出K线内": int(ok["止损落在退出K线内"].fillna(False).sum()),
            "上一根未破止损(首次触及)": int(ok["上一根未破止损"].fillna(False).sum()),
            "上一根已破止损(异常)": int((ok["上一根未破止损"] == False).sum()),
        },
        "5_期末结算": {
            "期末笔数": int((ok["exit_reason"] == "force_exit").sum()),
            "结算价=K线开盘": int(ok["期末结算价等于K线开盘"].fillna(False).sum()),
        },
        "不一致清单": ok[(ok["止损偏差tick"].abs() > 1.5) |
                    (ok["入场价在K线内"] == False) |
                    (ok["上一根未破止损"] == False)][
            ["策略", "pair", "open_date", "close_date", "exit_reason", "止损偏差tick",
             "入场价在K线内", "上一根未破止损"]].to_dict("records") if len(ok) else [],
    }
    with open(OUT / "06_K线抽核.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)

    print(f"抽核 {len(ok)} 笔")
    print(" 入场价在K线内:", res["1_入场价落在入场K线内"])
    print(" 初始止损:", res["2_初始止损公式"])
    print(" 2R:", res["3_2R激活与棘轮"])
    print(" 止损退出:", res["4_止损退出K线"])
    print(" 期末结算:", res["5_期末结算"])
    print(" 不一致:", len(res["不一致清单"]))


if __name__ == "__main__":
    main()
