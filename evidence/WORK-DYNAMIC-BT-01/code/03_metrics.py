# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜账户级指标（含持仓浮盈的统一权益曲线）。

为什么不能直接用引擎的 Sharpe
──────────────────────────────
freqtrade 的 `Sharpe (daily wallet balance)` 只按**已平仓**钱包余额算日收益，
持仓浮盈不进序列。本单要求「包含持仓浮盈的统一账户日收益」，因此这里重建：

    equity(T) = 初始权益
              + Σ_{已平仓且 close_ts <= T} profit_abs
              + Σ_{T 时仍持仓} [ amount×(P(T) − open_rate)
                                 − amount×open_rate×fee_open
                                 − amount×P(T)×fee_close
                                 − funding_total×(已过时长/总时长) ]

口径
────
· T 取 **UTC 日界 00:00**；P(T) = T 之前最后一根已收盘 1h K 线的 close。
· 年化 365 日；无风险利率假设 rf = 0（**假设**，不是事实）。
· 回撤/夏普/索提诺/卡玛全部基于**同一条**含浮盈的日权益曲线。
· 日频采样会漏掉日内峰值 ⇒ 回撤是**日频口径**，比逐笔更宽松，报告里明写。
· 该式在 P=close_rate 时能复现引擎 `profit_abs`（本脚本逐笔校验并输出最大残差）。

不做的事
────────
· 不重算盈亏（盈亏来自真实引擎），只做「按日盯市」的重排；
· 缺 K 线时**不补值**：用该标的最后一个已知收盘价并计数，如实报告缺口。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"
OUT = EXP / "out"
DATA = BASE / "evidence/FT-FACTOR-03/user_data/data/binance/futures"
INIT_EQUITY = 2000.0
DAYS_PER_YEAR = 365

WIN_START_MS = 1638316800000          # 2021-12-01 00:00 UTC

# 白名单顺序：freqtrade 回测按 (入场时刻, 白名单顺序) 递增分配 trade.id。
# 导出 CSV 不带 trade_id，必须用**同一排序规则**还原，否则同一根 K 线上的并发入场会串位
# （实测 Vs04_Z06 有 3 组并发入场，按 open_timestamp 单键排序会错配 6 笔）。
_WL = json.loads((EXP / "user_data/config.json").read_text(encoding="utf-8"))
WL_ORDER = {p: i for i, p in enumerate(_WL["exchange"]["pair_whitelist"])}
WIN_END_MS = 1735689600000            # 2025-01-01 00:00 UTC
DAY_MS = 86_400_000


# ────────────────────────── 价格源 ──────────────────────────

_PRICE_CACHE: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}


def pair_to_slug(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def load_1h(pair: str) -> Tuple[np.ndarray, np.ndarray]:
    if pair in _PRICE_CACHE:
        return _PRICE_CACHE[pair]
    f = DATA / f"{pair_to_slug(pair)}-1h-futures.feather"
    if not f.exists():
        _PRICE_CACHE[pair] = (np.array([], dtype=np.int64), np.array([], dtype=np.float64))
        return _PRICE_CACHE[pair]
    df = pd.read_feather(f, columns=["date", "close"])
    ts = df["date"].astype("int64").to_numpy()
    cl = df["close"].to_numpy(dtype=np.float64)
    _PRICE_CACHE[pair] = (ts, cl)
    return ts, cl


def price_at(pair: str, t_ms: int) -> Tuple[Optional[float], bool]:
    """T 时刻之前最后一根已收盘 1h 的 close。返回 (价格, 是否用到了更早的陈旧价)。"""
    ts, cl = load_1h(pair)
    if len(ts) == 0:
        return None, True
    idx = int(np.searchsorted(ts + 3_600_000, t_ms, side="right")) - 1
    if idx < 0:
        return None, True
    stale = (t_ms - (ts[idx] + 3_600_000)) > 3_600_000
    return float(cl[idx]), stale


# ────────────────────────── 逐笔盯市 ──────────────────────────

def trade_mtm_value(row: pd.Series, price: float, elapsed_frac: float) -> float:
    """盯市净额。**资金费符号已按引擎实测校准**：

    freqtrade 导出的 `funding_fees` 是**带符号的现金流**（正 = 收到、负 = 支付），
    而 `profit_abs = amount×(close−open) − 双边手续费 + funding_fees`。
    （实测反例：AXS 一笔 funding_fees=+1.2307 为收入，若按「减」会差 2×1.2307。）
    本函数在 `P=close_rate, elapsed_frac=1` 处必须复现 `profit_abs`，见 `validate_mtm`。
    """
    amount = float(row["amount"])
    o = float(row["open_rate"])
    fo = float(row["fee_open"])
    fc = float(row["fee_close"])
    funding = float(row.get("funding_fees") or 0.0)
    return amount * (price - o) - amount * o * fo - amount * price * fc + funding * elapsed_frac


def validate_mtm(df: pd.DataFrame) -> dict:
    """在 P=close_rate、elapsed_frac=1 处校验盯市式能复现引擎 profit_abs。"""
    errs = []
    for _, r in df.iterrows():
        if r.get("is_open"):
            continue
        pred = trade_mtm_value(r, float(r["close_rate"]), 1.0)
        errs.append(abs(pred - float(r["profit_abs"])))
    if not errs:
        return {"n": 0, "max_abs_err": None, "mean_abs_err": None}
    return {"n": len(errs), "max_abs_err": round(float(max(errs)), 8),
            "mean_abs_err": round(float(np.mean(errs)), 8)}


def build_daily_equity(df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """按 UTC 日界 00:00 盯市，返回日权益表与诊断。"""
    days = list(range(WIN_START_MS, WIN_END_MS + DAY_MS, DAY_MS))
    days = [d for d in days if d <= WIN_END_MS]

    closed = df[~df["is_open"].astype(bool)].copy()
    opened = df.copy()

    rows = []
    stale_cnt = 0
    missing_cnt = 0
    for T in days:
        realized = float(closed.loc[closed["close_timestamp"] <= T, "profit_abs"].sum()) \
            if len(closed) else 0.0
        # T 时仍持仓：open <= T < close（含期末持仓）
        mask = (opened["open_timestamp"] <= T) & (opened["close_timestamp"] > T)
        live = opened.loc[mask]
        unreal = 0.0
        stake_open = 0.0
        for _, r in live.iterrows():
            p, stale = price_at(str(r["pair"]), T)
            if p is None:
                missing_cnt += 1
                continue
            if stale:
                stale_cnt += 1
            span = max(1, int(r["close_timestamp"]) - int(r["open_timestamp"]))
            frac = min(1.0, max(0.0, (T - int(r["open_timestamp"])) / span))
            unreal += trade_mtm_value(r, p, frac)
            stake_open += float(r["stake_amount"])
        eq = INIT_EQUITY + realized + unreal
        rows.append({"t_ms": T, "equity": eq, "cash": INIT_EQUITY + realized,
                     "unrealized": unreal, "open_positions": int(len(live)),
                     "open_stake": stake_open})
    out = pd.DataFrame(rows)
    diag = {"stale_marks": stale_cnt, "missing_marks": missing_cnt, "days": len(out)}
    return out, diag


# ────────────────────────── 指标 ──────────────────────────

def perf_metrics(eq: pd.DataFrame) -> dict:
    e = eq["equity"].to_numpy(dtype=np.float64)
    if len(e) < 3:
        return {"sharpe": None, "sortino": None, "calmar": None,
                "max_dd_pct": None, "annual_return_pct": None,
                "note": "样本天数不足 3，指标不可估"}
    rets = np.diff(e) / e[:-1]
    rets = rets[np.isfinite(rets)]
    mu = float(np.mean(rets)) if len(rets) else 0.0
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    downside = rets[rets < 0]
    dsd = float(np.sqrt(np.mean(downside ** 2))) if len(downside) else 0.0

    peak = np.maximum.accumulate(e)
    dd = (e - peak) / peak
    max_dd = float(dd.min())
    days = len(e) - 1
    ann = (e[-1] / e[0]) ** (DAYS_PER_YEAR / days) - 1.0 if e[0] > 0 and days > 0 else None

    sharpe = (mu / sd * math.sqrt(DAYS_PER_YEAR)) if sd > 0 else None
    sortino = (mu / dsd * math.sqrt(DAYS_PER_YEAR)) if dsd > 0 else None
    calmar = (ann / abs(max_dd)) if (ann is not None and max_dd < 0) else None

    return {
        "sharpe": (round(sharpe, 4) if sharpe is not None else None),
        "sortino": (round(sortino, 4) if sortino is not None else None),
        "calmar": (round(calmar, 4) if calmar is not None else None),
        "max_dd_pct": round(max_dd * 100, 4),
        "annual_return_pct": (round(ann * 100, 4) if ann is not None else None),
        "daily_ret_mean_pct": round(mu * 100, 6),
        "daily_ret_std_pct": round(sd * 100, 6),
        "n_days": days,
        "note": None,
    }


def trade_metrics(df: pd.DataFrame) -> dict:
    closed = df[~df["is_open"].astype(bool)]
    openp = df[df["is_open"].astype(bool)]
    realized = float(closed["profit_abs"].sum()) if len(closed) else 0.0
    unreal = float(openp["profit_abs"].sum()) if len(openp) else 0.0

    fees = float((df["amount"] * df["open_rate"] * df["fee_open"]
                  + df["amount"] * df["close_rate"] * df["fee_close"]).sum()) if len(df) else 0.0
    funding = float(df["funding_fees"].fillna(0).sum()) if len(df) else 0.0

    wins = closed[closed["profit_abs"] > 0]
    losses = closed[closed["profit_abs"] <= 0]
    gp = float(wins["profit_abs"].sum()) if len(wins) else 0.0
    gl = float(losses["profit_abs"].sum()) if len(losses) else 0.0
    pf = (gp / abs(gl)) if gl < 0 else None

    dur_h = (df["close_timestamp"] - df["open_timestamp"]) / 3_600_000.0

    # 费用 2 倍敏感性（解析重算，不改基础口径）
    fee2 = float((df["amount"] * df["open_rate"] * df["fee_open"] * 2
                  + df["amount"] * df["close_rate"] * df["fee_close"] * 2).sum()) if len(df) else 0.0
    net2 = float((df["profit_abs"] + (fees - fee2)).sum()) if len(df) else 0.0

    return {
        "trades_total": int(len(df)),
        "trades_closed": int(len(closed)),
        "trades_open_at_end": int(len(openp)),
        "net_realized": round(realized, 4),
        "net_unrealized_end": round(unreal, 4),
        "net_total": round(realized + unreal, 4),
        "fees_abs": round(fees, 4),
        "funding_abs": round(funding, 4),
        "profit_factor": (round(pf, 4) if pf is not None else None),
        "win_rate_pct": (round(len(wins) / len(closed) * 100, 4) if len(closed) else None),
        "avg_holding_hours": (round(float(dur_h.mean()), 3) if len(df) else None),
        "median_holding_hours": (round(float(dur_h.median()), 3) if len(df) else None),
        "net_total_at_double_fee": round(net2, 4),
        "fee_stress_note": "费用×2（10bps/边）解析重算；未改基础口径，属敏感性假设",
    }


def exposure_metrics(eq: pd.DataFrame, df: pd.DataFrame) -> dict:
    days = len(eq)
    flat_days = int((eq["open_positions"] == 0).sum())
    exp_cap = (eq["open_stake"] / eq["equity"]).replace([np.inf, -np.inf], np.nan).dropna()
    total_hours = (WIN_END_MS - WIN_START_MS) / 3_600_000.0
    pos_hours = float(((df["close_timestamp"] - df["open_timestamp"]) / 3_600_000.0).sum()) if len(df) else 0.0
    return {
        "flat_day_ratio_pct": round(flat_days / days * 100, 4) if days else None,
        "avg_open_positions": round(float(eq["open_positions"].mean()), 4),
        "max_open_positions": int(eq["open_positions"].max()),
        "avg_capital_usage_pct": round(float(exp_cap.mean()) * 100, 4) if len(exp_cap) else None,
        "position_time_ratio_pct": round(min(100.0, pos_hours / total_hours * 100), 4) if total_hours else None,
    }


def exit_distribution(df: pd.DataFrame, trace: Optional[pd.DataFrame]) -> dict:
    d = {"exit_reason_counts": {}}
    if len(df):
        d["exit_reason_counts"] = {str(k): int(v) for k, v in df["exit_reason"].value_counts().items()}
    if trace is not None and len(trace):
        bu = trace[trace["event"] == "BAR_UPDATE"]
        last_stage = bu.sort_values("bar_close_ms").groupby("trade_id").tail(1).set_index("trade_id")["stage"]
        stage_cnt = {int(k): int(v) for k, v in last_stage.value_counts().sort_index().items()}
        d["exit_stage_counts"] = stage_cnt
        d["stage_change_total"] = int(trace["stage_changed"].fillna(False).astype(bool).sum())
        n_tr = trace["trade_id"].nunique()
        d["trades_with_stage_change"] = int(
            trace[trace["stage_changed"].fillna(False).astype(bool)]["trade_id"].nunique()) if n_tr else 0
        d["stage2_hits"] = int((last_stage >= 2).sum()) if len(last_stage) else 0
        d["stage3_hits"] = int((last_stage >= 3).sum()) if len(last_stage) else 0
    return d


def attach_trade_ids(df: pd.DataFrame, trace: Optional[pd.DataFrame]) -> Tuple[pd.DataFrame, dict]:
    """引擎导出的 trades CSV **没有 trade_id**，而切档轨迹按 `trade.id` 记录。

    freqtrade 回测按**入场顺序**分配 `trade.id = 1..N`，导出顺序与之一致。
    这里：按 `open_timestamp` 稳定排序后赋 1..N，再用轨迹里的 `ENTRY` 行
    （`trade_id` + `pair` + 入场时刻）**逐条反查校验**；只要有一条对不上就如实报告，
    不静默按行号猜配。
    """
    d = df.copy()
    d["_wi"] = d["pair"].map(WL_ORDER).fillna(9999)
    d = d.sort_values(["open_timestamp", "_wi"], kind="stable").drop(columns=["_wi"])
    d = d.reset_index(drop=True)
    d["trade_id"] = np.arange(1, len(d) + 1)

    info = {"n": int(len(d)), "trace_trades": None, "pair_mismatch": None,
            "time_mismatch": None, "verified": None}
    if trace is None or len(trace) == 0:
        info["verified"] = "no_trace"
        return d, info

    ent = trace[trace["event"] == "ENTRY"][["trade_id", "pair", "event_time_ms"]]
    info["trace_trades"] = int(ent["trade_id"].nunique())
    m = d.set_index("trade_id").join(ent.set_index("trade_id"), rsuffix="_tr", how="left")
    pair_bad = int((m["pair"] != m["pair_tr"]).sum())
    dt = (m["open_timestamp"] - m["event_time_ms"]).abs()
    time_bad = int((dt > 900_000).sum())
    info["pair_mismatch"] = pair_bad
    info["time_mismatch"] = time_bad
    info["verified"] = bool(pair_bad == 0 and time_bad == 0
                            and info["trace_trades"] == info["n"])
    return d, info


def main() -> None:
    index = pd.read_csv(OUT / "02_运行索引.csv")
    rows: List[dict] = []
    eq_frames: List[pd.DataFrame] = []
    trade_frames: List[pd.DataFrame] = []
    val_rows: List[dict] = []
    id_rows: List[dict] = []

    for _, meta in index.iterrows():
        name = meta["strategy"]
        csv = OUT / f"trades_{name}.csv"
        if not csv.exists() or meta["returncode"] != 0:
            rows.append({"strategy": name, "group": meta["group"],
                         "status": "FAILED_OR_MISSING", "note": meta.get("note")})
            continue
        df = pd.read_csv(csv)
        if len(df) == 0:
            rows.append({"strategy": name, "group": meta["group"], "status": "ZERO_TRADES"})
            continue
        df["pair"] = df["pair"].astype(str)

        trace_p0 = OUT / "stage_trace_unified" / f"stage_trace_{name}.csv"
        trace0 = pd.read_csv(trace_p0) if trace_p0.exists() else None
        df, idinfo = attach_trade_ids(df, trace0)
        id_rows.append({"strategy": name, **idinfo})

        val = validate_mtm(df)
        val_rows.append({"strategy": name, **val})

        eq, diag = build_daily_equity(df)
        eq["strategy"] = name
        eq["group"] = meta["group"]
        eq_frames.append(eq)

        trace = trace0

        rec = {
            "strategy": name,
            "group": meta["group"],
            "param_group": meta["param_group"],
            "status": "OK",
            "d_spec": meta["d_spec"],
        }
        rec.update(trade_metrics(df))
        rec.update(perf_metrics(eq))
        rec.update(exposure_metrics(eq, df))
        rec.update({k: v for k, v in exit_distribution(df, trace).items()
                    if k not in ("exit_reason_counts",)})
        rec["exit_reason_counts"] = json.dumps(
            exit_distribution(df, trace).get("exit_reason_counts", {}), ensure_ascii=False)
        rec["final_equity"] = round(INIT_EQUITY + rec["net_total"], 4)
        rec["net_return_pct"] = round(rec["net_total"] / INIT_EQUITY * 100, 4)
        rec["mtm_max_abs_err"] = val.get("max_abs_err")
        rec["mtm_stale_marks"] = diag["stale_marks"]
        rec["mtm_missing_marks"] = diag["missing_marks"]
        rows.append(rec)

        # 逐笔对账底表
        df2 = df.copy()
        df2["strategy"] = name
        df2["group"] = meta["group"]
        if trace is not None and len(trace):
            bu = trace[trace["event"] == "BAR_UPDATE"]
            last = bu.sort_values("bar_close_ms").groupby("trade_id").tail(1)
            mp = dict(zip(last["trade_id"], last["stage"]))
            mp_stop = dict(zip(last["trade_id"], last["effective_stop"]))
            mp_d = dict(zip(last["trade_id"], last["d"]))
            nchg = trace[trace["stage_changed"].fillna(False).astype(bool)].groupby("trade_id").size().to_dict()
            df2["exit_stage"] = df2["trade_id"].map(mp)
            df2["exit_effective_stop"] = df2["trade_id"].map(mp_stop)
            df2["exit_d"] = df2["trade_id"].map(mp_d)
            df2["stage_change_count"] = df2["trade_id"].map(nchg).fillna(0).astype(int)
        trade_frames.append(df2)

    res = pd.DataFrame(rows)
    order = {"A": 0, "B": 1, "C": 2, "P1": 3, "P2": 4}
    res["_o"] = res["group"].map(order).fillna(9)
    res = res.sort_values(["_o", "param_group"]).drop(columns=["_o"])
    res.to_csv(OUT / "03_分组指标总表.csv", index=False, encoding="utf-8-sig")

    if eq_frames:
        pd.concat(eq_frames, ignore_index=True).to_csv(
            OUT / "04_账户日权益曲线_全部.csv.gz", index=False, compression="gzip")
    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_csv(
            OUT / "05_逐笔对账底表_全部.csv.gz", index=False, compression="gzip")
    if val_rows:
        pd.DataFrame(val_rows).to_csv(OUT / "06_盯市式复现校验.csv", index=False,
                                      encoding="utf-8-sig")
        v = pd.DataFrame(val_rows)
        print("盯市式复现引擎 profit_abs 最大绝对残差:", float(v["max_abs_err"].max()))

    if id_rows:
        idf = pd.DataFrame(id_rows)
        idf.to_csv(OUT / "06b_trade_id反查校验.csv", index=False, encoding="utf-8-sig")
        print("trade_id 反查校验通过组数:", int((idf["verified"] == True).sum()), "/", len(idf))
        bad = idf[idf["verified"] != True]
        if len(bad):
            print("  !! 未通过：")
            print(bad.to_string())

    print(res[["strategy", "group", "trades_total", "net_total", "net_realized",
               "net_unrealized_end", "max_dd_pct", "sharpe", "sortino", "calmar",
               "profit_factor", "win_rate_pct"]].to_string())


if __name__ == "__main__":
    main()
