# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜逐笔订单/fills、切档轨迹、持久 K 线、复盘登记。

产出（全部在 evidence/WORK-DYNAMIC-BT-01/ 之内）
──────────────────────────────────────────────
1. `14_订单与逐次成交_全部.csv.gz`
   orders 层（每笔 trade 的 buy/sell 订单）+ fills 层（每笔订单的成交价/量/成本/时刻），
   与引擎 `trades` 三层可互相关联：strategy + trade_id + order_seq。
2. `15_入场审计_通过与拒单_全部.csv.gz`
   策略 `confirm_trade_entry` 的 PASSED/REJECTED 记录（含准入窗口、冻结 H、成交价），
   未成交/拒单**照样保留**，不丢。
3. `16_切档轨迹_全部.csv.gz`
   每笔持仓每根已收盘 4h 的 d / 候选止损 / 生效止损 / 是否切档，全量留痕。
4. `replay/<strategy>.jsonl.gz`
   逐笔复盘包：4h 背景 + 1h 主图 + 15m 执行周期 K 线，信号/确认/成交/退出标记，
   生效止损轨迹与切档点。**每笔一条**，持久保存，供多图联动读取。
5. `replay/_index.json`
   复盘包索引（策略 → 文件、笔数、时间范围、指纹）。
6. `回测日志/event_trend_v0_1/runs_work_dynamic_bt_01/<run_id>/`
   复盘登记：run.json / summary.json / inputs.json / manifest.json /
   trades.jsonl / orders.jsonl / equity_curve.jsonl / candidates.jsonl / chart.json
   **新建独立批次目录**，不覆盖、不改动任何既有 run 目录或索引。

不做的
──────
· 不重算盈亏（引擎给什么就存什么）；
· 不改公共前端/后端；`chart.json` 的 K 线按现有契约给（多标的组合无法塞进单标的
  图表契约，故本单在 `chart_blocked_reason` 里写明，并由 replay 包另行承载）。
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"
OUT = EXP / "out"
REPLAY = EXP / "replay"
DATA = BASE / "evidence/FT-FACTOR-03/user_data/data/binance/futures"
BATCH_DIR = BASE / "回测日志/event_trend_v0_1/runs_work_dynamic_bt_01"
INIT_EQUITY = 2000.0
WIN_START_MS = 1638316800000
WIN_END_MS = 1735689600000

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}

_CACHE: Dict[tuple, pd.DataFrame] = {}


def slug(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def load_tf(pair: str, tf: str) -> pd.DataFrame:
    key = (pair, tf)
    if key in _CACHE:
        return _CACHE[key]
    f = DATA / f"{slug(pair)}-{tf}-futures.feather"
    if not f.exists():
        df = pd.DataFrame(columns=["open_ms", "o", "h", "l", "c", "v"])
    else:
        raw = pd.read_feather(f, columns=["date", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame({
            "open_ms": raw["date"].astype("int64").to_numpy(),
            "o": raw["open"].to_numpy(float), "h": raw["high"].to_numpy(float),
            "l": raw["low"].to_numpy(float), "c": raw["close"].to_numpy(float),
            "v": raw["volume"].to_numpy(float),
        })
    _CACHE[key] = df
    return df


def slice_bars(pair: str, tf: str, t0: int, t1: int, before: int, after: int) -> List[dict]:
    df = load_tf(pair, tf)
    if len(df) == 0:
        return []
    ms = TF_MS[tf]
    i0 = int(np.searchsorted(df["open_ms"].to_numpy(), t0, side="left")) - before
    i1 = int(np.searchsorted(df["open_ms"].to_numpy(), t1, side="right")) + after
    i0 = max(0, i0)
    i1 = min(len(df), i1)
    sub = df.iloc[i0:i1]
    return [{"t": int(r.open_ms), "o": float(r.o), "h": float(r.h),
             "l": float(r.l), "c": float(r.c), "v": float(r.v)} for r in sub.itertuples(index=False)]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_iso(ms: Optional[int] = None) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────── 1. orders / fills ───────────────────────

def build_orders(all_trades: pd.DataFrame) -> pd.DataFrame:
    rows: List[dict] = []
    for r in all_trades.itertuples(index=False):
        raw = getattr(r, "orders", None)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            orders = ast.literal_eval(raw)
        except Exception:                                       # noqa: BLE001
            continue
        for seq, o in enumerate(orders):
            rows.append({
                "strategy": r.strategy,
                "group": r.group,
                "trade_id": int(r.trade_id),
                "pair": r.pair,
                "enter_tag": r.enter_tag,
                "order_seq": seq,
                "ft_order_side": o.get("ft_order_side"),
                "ft_is_entry": bool(o.get("ft_is_entry")),
                "ft_order_tag": o.get("ft_order_tag"),
                "fill_ts_ms": int(o["order_filled_timestamp"]) if o.get("order_filled_timestamp") else None,
                "fill_time_utc": utc_iso(o.get("order_filled_timestamp")),
                "fill_price": float(o["safe_price"]) if o.get("safe_price") is not None else None,
                "fill_amount": float(o["amount"]) if o.get("amount") is not None else None,
                "fill_cost": float(o["cost"]) if o.get("cost") is not None else None,
                "trade_open_ts_ms": int(r.open_timestamp),
                "trade_close_ts_ms": int(r.close_timestamp),
                "trade_profit_abs": float(r.profit_abs),
                "exit_reason": r.exit_reason,
            })
    return pd.DataFrame(rows)


# ─────────────────────── 2. 入场审计 ───────────────────────

def collect_audits(index: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for _, m in index.iterrows():
        name = m["strategy"]
        for kind, fname in (("order", f"audit_orders_{name}.csv"),
                            ("confirmation", f"audit_confirmation_{name}.csv")):
            p = OUT / fname
            if p.exists():
                try:
                    d = pd.read_csv(p)
                    if len(d):
                        d["strategy"] = name
                        d["audit_kind"] = kind
                        frames.append(d)
                except Exception:                               # noqa: BLE001
                    continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ─────────────────────── 3/4. 切档轨迹 + 复盘包 ───────────────────────

def build_replay(index: pd.DataFrame, all_trades: pd.DataFrame) -> dict:
    REPLAY.mkdir(parents=True, exist_ok=True)
    idx_rows: List[dict] = []

    for _, m in index.iterrows():
        name = m["strategy"]
        if m["returncode"] != 0:
            continue
        tr = all_trades[all_trades["strategy"] == name]
        if len(tr) == 0:
            continue
        trace_p = OUT / "stage_trace_unified" / f"stage_trace_{name}.csv"
        trace = pd.read_csv(trace_p) if trace_p.exists() else pd.DataFrame()
        conf_p = OUT / f"audit_confirmation_{name}.csv"
        conf = pd.read_csv(conf_p) if conf_p.exists() else pd.DataFrame()

        out_path = REPLAY / f"{name}.jsonl.gz"
        n = 0
        t_min, t_max = None, None
        with gzip.open(out_path, "wt", encoding="utf-8") as f:
            for r in tr.itertuples(index=False):
                pair = str(r.pair)
                e_ms = int(r.open_timestamp)
                x_ms = int(r.close_timestamp)
                t_min = e_ms if t_min is None else min(t_min, e_ms)
                t_max = x_ms if t_max is None else max(t_max, x_ms)

                k4h = slice_bars(pair, "4h", e_ms, x_ms, 120, 24)
                k1h = slice_bars(pair, "1h", e_ms, x_ms, 200, 48)
                k15m = slice_bars(pair, "15m", e_ms, x_ms, 16, 16)

                tt = trace[trace["trade_id"] == int(r.trade_id)] if len(trace) else pd.DataFrame()
                stop_trace = []
                stage_events = []
                if len(tt):
                    prev = None
                    for b in tt.sort_values("bar_close_ms").itertuples(index=False):
                        if b.event != "BAR_UPDATE":
                            continue
                        stop = float(b.effective_stop)
                        if prev is None or stop != prev:
                            stop_trace.append({"t": int(b.bar_close_ms), "stop": stop,
                                               "reason": f"stage{b.stage}_d{b.d}"})
                            prev = stop
                        if bool(b.stage_changed):
                            stage_events.append({
                                "t": int(b.bar_close_ms),
                                "from_stage": int(b.stage) - 1,
                                "to_stage": int(b.stage),
                                "gain_vs_entry": float(b.gain_vs_entry),
                                "bar_close": float(b.bar_close),
                                "bar_low": float(b.bar_low),
                                "d": float(b.d),
                                "effective_stop_before": float(b.prev_effective_stop),
                                "effective_stop_after": float(b.effective_stop),
                            })

                sig_ms = None
                conf_ms = None
                if len(conf):
                    tag = str(r.enter_tag)
                    sub = (conf[conf["event_id"].astype(str).apply(lambda x: x in tag)]
                           if "event_id" in conf.columns else pd.DataFrame())
                    if len(sub) and "decision" in sub.columns:
                        ok = sub[sub["decision"].astype(str).str.upper() == "CONFIRMED"]
                        if len(ok):
                            sub = ok
                    if len(sub):
                        sig_ms = int(sub["orig_signal_time_ms"].iloc[-1]) if "orig_signal_time_ms" in sub.columns else None
                        conf_ms = int(sub["confirm_time_ms"].iloc[-1]) if "confirm_time_ms" in sub.columns else None

                rec = {
                    "strategy": name,
                    "group": str(m["group"]),
                    "param_group": str(m["param_group"]),
                    "trade_id": int(r.trade_id),
                    "pair": pair,
                    "symbol": pair.split("/")[0],
                    "candidate_id": str(r.enter_tag),
                    "entry": {"t": e_ms, "time_utc": utc_iso(e_ms), "price": float(r.open_rate),
                              "qty": float(r.amount), "stake": float(r.stake_amount)},
                    "exit": {"t": x_ms, "time_utc": utc_iso(x_ms), "price": float(r.close_rate),
                             "reason": str(r.exit_reason), "is_open_at_end": bool(r.is_open)},
                    "signal": {"t": sig_ms, "time_utc": utc_iso(sig_ms),
                               "confirm_t": conf_ms, "confirm_time_utc": utc_iso(conf_ms)},
                    "pnl": {"profit_abs": float(r.profit_abs), "profit_ratio": float(r.profit_ratio),
                            "fees": float(r.amount * r.open_rate * r.fee_open + r.amount * r.close_rate * r.fee_close),
                            "funding": float(r.funding_fees or 0.0)},
                    "stop_trace": stop_trace,
                    "stage_events": stage_events,
                    "timeframes": {"4h": k4h, "1h": k1h, "15m": k15m},
                    "markers": {
                        "signal_t": sig_ms, "confirm_t": conf_ms,
                        "entry_t": e_ms, "exit_t": x_ms,
                        "stop_events": [p["t"] for p in stop_trace],
                        "stage_events": [p["t"] for p in stage_events],
                    },
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1

        idx_rows.append({
            "strategy": name, "group": str(m["group"]), "param_group": str(m["param_group"]),
            "file": str(out_path.relative_to(EXP)), "trades": n,
            "first_entry_utc": utc_iso(t_min), "last_exit_utc": utc_iso(t_max),
            "bytes": out_path.stat().st_size,
            "sha256": sha256_file(out_path),
            "timeframes": "4h+1h+15m",
            "contains": "K线/信号/确认/成交/退出/生效止损轨迹/切档事件",
        })
        print(f"  replay {name}: {n} 笔, {out_path.stat().st_size/1e6:.1f} MB")

    pd.DataFrame(idx_rows).to_csv(OUT / "17_复盘包索引.csv", index=False, encoding="utf-8-sig")
    return {"strategies": len(idx_rows), "trades": int(sum(r["trades"] for r in idx_rows))}


# ─────────────────────── 5. 复盘登记 ───────────────────────

def register_runs(index: pd.DataFrame, all_trades: pd.DataFrame,
                  orders: pd.DataFrame, audits: pd.DataFrame,
                  metrics: pd.DataFrame) -> dict:
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for _, m in index.iterrows():
        name = m["strategy"]
        run_id = m["run_id"]
        d = BATCH_DIR / run_id
        if d.exists():
            skipped.append(run_id)          # 绝不覆盖
            continue
        if m["returncode"] != 0:
            continue
        d.mkdir(parents=True, exist_ok=False)

        tr = all_trades[all_trades["strategy"] == name].copy()
        met = metrics[metrics["strategy"] == name]
        met = met.iloc[0].to_dict() if len(met) else {}

        # trades.jsonl
        with open(d / "trades.jsonl", "w", encoding="utf-8") as f:
            for r in tr.itertuples(index=False):
                f.write(json.dumps({
                    "trade_id": f"trd_{name}_{int(r.trade_id):04d}",
                    "source_trade_id": int(r.trade_id),
                    "pair": r.pair, "symbol": str(r.pair).split("/")[0],
                    "direction": "long",
                    "entry_time_ms": int(r.open_timestamp),
                    "exit_time_ms": int(r.close_timestamp),
                    "entry_price": float(r.open_rate), "exit_price": float(r.close_rate),
                    "qty": float(r.amount), "stake_amount": float(r.stake_amount),
                    "fees": float(r.amount * r.open_rate * r.fee_open + r.amount * r.close_rate * r.fee_close),
                    "funding_fee": float(r.funding_fees or 0.0),
                    "gross_pnl": float(r.profit_abs),
                    "net_pnl": float(r.profit_abs),
                    "exit_reason": str(r.exit_reason),
                    "is_open": bool(r.is_open),
                    "holding_hours": round((int(r.close_timestamp) - int(r.open_timestamp)) / 3.6e6, 4),
                    "candidate_id": str(r.enter_tag),
                    "stoploss_s": 0.05,
                    "d_spec": m["d_spec"],
                }, ensure_ascii=False) + "\n")

        # orders.jsonl
        od = orders[orders["strategy"] == name]
        with open(d / "orders.jsonl", "w", encoding="utf-8") as f:
            for r in od.itertuples(index=False):
                f.write(json.dumps({
                    "order_id": f"ord_{name}_{int(r.trade_id):04d}_{int(r.order_seq)}",
                    "trade_id": f"trd_{name}_{int(r.trade_id):04d}",
                    "symbol": str(r.pair).split("/")[0], "pair": r.pair,
                    "side": r.ft_order_side, "is_entry": bool(r.ft_is_entry),
                    "ts_ms": r.fill_ts_ms, "created_at": r.fill_time_utc,
                    "price": r.fill_price, "qty": r.fill_amount, "cost": r.fill_cost,
                    "tag": r.ft_order_tag,
                }, ensure_ascii=False) + "\n")

        # candidates.jsonl（由 enter_tag 反推的真实候选；不造）
        seen = set()
        with open(d / "candidates.jsonl", "w", encoding="utf-8") as f:
            for r in tr.itertuples(index=False):
                tag = str(r.enter_tag)
                if tag in seen:
                    continue
                seen.add(tag)
                f.write(json.dumps({
                    "candidate_id": tag, "symbol": str(r.pair).split("/")[0],
                    "pair": r.pair,
                    "available_at_ms": int(r.open_timestamp),
                    "snapshot_time_ms": int(r.open_timestamp),
                    "initial_stop_price": float(r.open_rate) * 0.95,
                    "breakout_close": float(r.open_rate),
                    "note": "由引擎成交记录反推；精确候选时刻见 audit_confirmation_*.csv",
                }, ensure_ascii=False) + "\n")

        # equity_curve.jsonl（含浮盈的统一账户日权益，来自 03_metrics 的日曲线）
        eq_path = OUT / "04_账户日权益曲线_全部.csv.gz"
        if eq_path.exists():
            eq = pd.read_csv(eq_path)
            sub = eq[eq["strategy"] == name]
            with open(d / "equity_curve.jsonl", "w", encoding="utf-8") as f:
                for r in sub.itertuples(index=False):
                    f.write(json.dumps({
                        "timestamp": int(r.t_ms), "t": int(r.t_ms),
                        "cash_equity": float(r.cash), "total_equity": float(r.equity),
                        "unrealized_pnl": float(r.unrealized),
                        "open_positions_count": int(r.open_positions),
                    }, ensure_ascii=False) + "\n")

        # summary.json / manifest.json / inputs.json / run.json / chart.json
        net_total = float(met.get("net_total") or 0.0)
        summary = {
            "run_id": run_id, "strategy": name,
            "symbol": "PORTFOLIO_74PAIRS",
            "trade_count": int(met.get("trades_total") or len(tr)),
            "trades_closed": int(met.get("trades_closed") or 0),
            "trades_open": int(met.get("trades_open_at_end") or 0),
            "win_count": (None if met.get("win_rate_pct") is None
                          else round(float(met["win_rate_pct"]) / 100 * int(met.get("trades_closed") or 0))),
            "win_rate_pct": met.get("win_rate_pct"),
            "net_pnl": round(net_total, 4),
            "net_realized": met.get("net_realized"),
            "net_unrealized_end": met.get("net_unrealized_end"),
            "initial_equity": INIT_EQUITY,
            "final_equity": round(INIT_EQUITY + net_total, 4),
            "max_drawdown_pct": met.get("max_dd_pct"),
            "profit_factor": met.get("profit_factor"),
            "sharpe": met.get("sharpe"), "sortino": met.get("sortino"), "calmar": met.get("calmar"),
            "total_fees": met.get("fees_abs"), "total_funding": met.get("funding_abs"),
            "summary_variant_a": {"initial_equity": INIT_EQUITY,
                                  "final_equity": round(INIT_EQUITY + net_total, 4),
                                  "net_pnl": round(net_total, 4),
                                  "trade_count": int(met.get("trades_total") or len(tr))},
            "metrics_note": ("夏普/索提诺/卡玛/回撤均基于含持仓浮盈的 UTC 日界账户权益曲线，"
                             "365 日年化，rf=0（假设）；详见 out/03_分组指标总表.csv 与报告口径节"),
        }
        (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        inputs = {
            "run_id": run_id, "strategy_name": name, "timing_mode": "C1",
            "stoploss_s": 0.05, "d_spec": m["d_spec"],
            "param_group": m["param_group"], "group": m["group"],
            "study_period": "2021-12-01 ~ 2024-12-31",
            "initial_wallet": INIT_EQUITY, "risk_budget_pct": 0.005,
            "trailing_method": "4h_latest_low_trail",
            "data_summary": {"time_range_1h": {"start_ms": WIN_START_MS, "end_ms": WIN_END_MS}},
            "command": json.loads(m["command"]) if isinstance(m["command"], str) else m["command"],
            "fingerprints": {"strategy_sha256": m["strategy_sha256"],
                             "config_sha256": m["config_sha256"]},
            "engine": "freqtrade 2026.9 (evidence/FT-BASELINE-01/.venv)",
            "exploratory": True,
            "exploratory_note": ("研究期区间已被前序研究接触，本 run 为探索性对照，"
                                 "不构成独立样本外有效性证据"),
        }
        (d / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8")

        (d / "manifest.json").write_text(json.dumps({
            "run_id": run_id, "batch_id": "runs_work_dynamic_bt_01",
            "committed": True, "status": "SUCCEEDED",
            "trades_count": int(summary["trade_count"]),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        run_doc = {
            "run_id": run_id,
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "strategy_key": "wx_event_trend_v0_1",
            "batch_id": "runs_work_dynamic_bt_01",
            "status": "succeeded",
            "status_basis": "manifest.json + summary.json 均在（引擎回测真实完成）",
            "registered": True,
            "created_at": m.get("started_at"),
            "finished_at": m.get("finished_at"),
            "spec": {"strategy_id": "WX_EVENT_TREND_V0_1",
                     "symbols": ["PORTFOLIO_74PAIRS"], "exchange": "binance",
                     "market": "um", "signal_timeframe": "1h", "exec_timeframe": "15m",
                     "initial_equity": INIT_EQUITY},
            "config": {"actual_config": {"initial_equity": INIT_EQUITY,
                                         "risk_budget_per_trade": 0.005,
                                         "fee_rate": 0.0005,
                                         "slippage_bps": 0.0,
                                         "max_open_trades": 3,
                                         "stoploss_s": 0.05,
                                         "d_spec": m["d_spec"]},
                       "config_note": ("fee_rate 来自 config.json 的 0.0005；"
                                       "slippage_bps=0.0 是 freqtrade 回测默认（不单独建模滑点），"
                                       "27 组同口径 ⇒ 配对比较不受影响；已在报告写明")},
            "window": {"win_start_ms": WIN_START_MS, "win_end_ms": WIN_END_MS,
                       "symbol": "PORTFOLIO_74PAIRS", "exchange": "binance", "market": "um",
                       "signal_timeframe": "1h", "exec_timeframe": "15m"},
            "metrics": {
                "initial_equity": INIT_EQUITY,
                "final_equity": summary["final_equity"],
                "net_pnl": summary["net_pnl"],
                "net_return_pct": met.get("net_return_pct"),
                "trades_count": summary["trade_count"],
                "win_rate_pct": summary["win_rate_pct"],
                "max_drawdown_pct": summary["max_drawdown_pct"],
                "profit_factor": summary["profit_factor"],
                "sharpe": summary["sharpe"], "sortino": summary["sortino"],
                "calmar": summary["calmar"],
                "basis": "含持仓浮盈的 UTC 日界账户权益曲线；详见 03_分组指标总表.csv",
            },
            "data_quality": {"status": "unknown",
                             "reasons": ["OI 无发布时点证据（PIT 未证明）；本单只声明计算不超前"],
                             "source": "WORK-DYNAMIC-BT-01 口径说明"},
            "engine": {"strategy_id": "WX_EVENT_TREND_V0_1", "signal_version": "v0.1.0",
                       "core_version": "v0.1.0", "exec_core_version": "freqtrade 2026.9"},
            "hashes": {"strategy_sha256": m["strategy_sha256"],
                       "config_sha256": m["config_sha256"]},
            "exploratory": True,
        }
        (d / "run.json").write_text(json.dumps(run_doc, ensure_ascii=False, indent=2), encoding="utf-8")

        chart = {
            "symbol": "PORTFOLIO_74PAIRS",
            "exec_timeframe": "15m",
            "window": {"start_ms": WIN_START_MS, "end_ms": WIN_END_MS},
            "candles": [],
            "signals": [],
            "trades": [],
            "equity_curve": [],
            "funding_events": [],
            "benchmark": {"symbol": "PORTFOLIO_74PAIRS", "return_pct": None,
                          "price_only_return_pct": None, "net_return_pct": None,
                          "cost_model": "n/a（组合回测无单一基准）", "equity_series": []},
            "chart_blocked_reason": (
                "本 run 为 74 标的组合回测，现有复盘图表契约（backtest_chart.build_chart / "
                "GET /backtests/{id}/chart）以**单标的** symbol+window 取 K 线，无法承载组合；"
                "且公共复盘/接口正由 WORK-SIGNAL-REPLAY-02 改造中，本单不改公共源码。"
                "逐笔多周期 K 线已完整保存在 evidence/WORK-DYNAMIC-BT-01/replay/<strategy>.jsonl.gz。"),
            "replay_bundle": f"evidence/WORK-DYNAMIC-BT-01/replay/{name}.jsonl.gz",
            "stop_trace_source": "strategy stage_trace（4h 收盘驱动的生效止损轨迹，官方 t 毫秒）",
        }
        (d / "chart.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2), encoding="utf-8")

        created.append(run_id)

    return {"created": len(created), "skipped_existing": len(skipped),
            "batch_dir": str(BATCH_DIR)}


def main() -> None:
    index = pd.read_csv(OUT / "02_运行索引.csv")
    metrics = pd.read_csv(OUT / "03_分组指标总表.csv")
    all_trades = pd.read_csv(OUT / "05_逐笔对账底表_全部.csv.gz")
    all_trades["pair"] = all_trades["pair"].astype(str)
    all_trades["strategy"] = all_trades["strategy"].astype(str)

    print("=== 1/5 orders + fills ===")
    orders = build_orders(all_trades)
    orders.to_csv(OUT / "14_订单与逐次成交_全部.csv.gz", index=False, compression="gzip")
    print(f"  {len(orders)} 条订单/fill，覆盖 {orders['strategy'].nunique()} 组策略")

    print("=== 2/5 入场审计（含拒单）===")
    audits = collect_audits(index)
    if len(audits):
        audits.to_csv(OUT / "15_入场审计_通过与拒单_全部.csv.gz", index=False, compression="gzip")
        print(f"  {len(audits)} 条；decision 分布: {audits.get('decision').value_counts().to_dict() if 'decision' in audits else 'n/a'}")
    else:
        print("  无审计记录")

    print("=== 3/5 切档轨迹 ===")
    frames = []
    for p in sorted((OUT / "stage_trace_unified").glob("stage_trace_*.csv")):
        d = pd.read_csv(p)
        frames.append(d)
    if frames:
        st = pd.concat(frames, ignore_index=True)
        st.to_csv(OUT / "16_切档轨迹_全部.csv.gz", index=False, compression="gzip")
        print(f"  {len(st)} 行，{st['strategy'].nunique()} 组策略，切档事件 {int(st['stage_changed'].fillna(False).astype(bool).sum())} 次")

    print("=== 4/5 复盘包（逐笔多周期 K 线）===")
    rep = build_replay(index, all_trades)
    print("  ", rep)

    print("=== 5/5 复盘登记 ===")
    reg = register_runs(index, all_trades, orders, audits, metrics)
    print("  ", reg)

    (OUT / "18_产物清单.json").write_text(json.dumps({
        "replay": rep, "registration": reg,
        "orders_rows": int(len(orders)),
        "audit_rows": int(len(audits)) if len(audits) else 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
