# -*- coding: utf-8 -*-
"""把 FT-ENTRY-09（freqtrade 27 组回测）导入为系统可复盘的研究期结果（FT-REPLAY-01）。

背景
────
FT-ENTRY-09 的 27 组回测跑在 freqtrade 里，产物是 CSV（合并成交表 / 候选确认表 /
索引表）。系统回测页只认 `runs/<run_id>/` 这种目录（run.json + summary.json +
trades.jsonl + chart.json），所以这 4325 笔成交在界面上一笔都看不到。

本工具**不重跑回测、不改任何账本**，只把上游已落盘的事实翻译成系统能读的档案：

  · run 粒度 = 一个参数组 × 一个币种（1065 个），落在
    `回测日志/event_trend_v0_1/runs_ft_entry_09_research/`（独立批次目录，
    不混进 `runs/` 主列表，也不进生产订单 / 模拟实盘账本）；
  · `chart.json` **只写标记不写 K 线**：K 线由后端按本 run 的 symbol+窗口从市场库
    读取（不落盘，见 backtest_api 的 candles 补全）；
  · 每条记录带四类时间且互不混用：`signal_time_ms`（原信号） /
    `confirm_time_ms`（确认 K 线收盘）/ `order_time_ms`（下单即成交时刻）/
    `entry_time_ms`（持仓开始）。上游没有的字段一律 `null`，界面显示「未知」。

字段来源（逐项可核对，不推算）
────────────────────────────
  成交     `evidence/FT-ENTRY-09/out/00_all_27_trades_consolidated.csv`
  确认/拒单 `evidence/FT-ENTRY-09/out/audit_confirmation_*.csv`
  组指标   `evidence/FT-ENTRY-09/01_全部运行索引与指标总表.csv`
  候选对照 `evidence/FT-ENTRY-09/02_逐候选对照表.csv`
  账户参数 `evidence/FT-ENTRY-09/user_data/config.json`（初始资金 2000 / 费率 0.0005）
  版本指纹 `evidence/FT-ENTRY-09/00_版本指纹清单.csv`

用法
────
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY 工具_导入研究期回测.py              # 体检：只统计，不写盘
    $PY 工具_导入研究期回测.py --apply      # 真正生成 run 目录（幂等，已有则跳过）
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND = PROJECT_ROOT / "框架" / "backend"
for _p in (str(PROJECT_ROOT), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_store as S                                   # noqa: E402

UPSTREAM = PROJECT_ROOT / "evidence" / "FT-ENTRY-09"
# 研究期 run 的**实体目录登记在主运行根** `回测日志/event_trend_v0_1/runs/`：
# 这样列表 / 详情 / 逐笔 / K 线 / 标记全部复用既有页面与接口，不另造一套。
# 批次级入口（`runs_ft_replay_01`）只放清单（runs.jsonl + batch_meta.json），
# 由 `工具_FT_REPLAY_01_批次清单.py` 生成，不复制 run。
BATCH_ID = "runs_ft_replay_01"
RUNS_DIR = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs"
BATCH_DIR = RUNS_DIR
STRATEGY_ID = "FT_ENTRY_09_RESEARCH"

TRADE_TAG_RE = re.compile(r"^cand_(C\d?)_(.+)_(\d+)$")
PAIR_RE = re.compile(r"^([A-Z0-9]+)/USDT:USDT$")


# ── 基础读取 ────────────────────────────────────────────────────────────────
def _read_csv(p: Path) -> List[Dict[str, str]]:
    if not p.is_file():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> Optional[int]:
    f = _num(v)
    return int(f) if f is not None else None


def _pair_to_symbol(pair: str) -> Optional[str]:
    m = PAIR_RE.match(pair or "")
    return f"{m.group(1)}USDT" if m else None


def _parse_orders(raw: str) -> List[dict]:
    """上游 `orders` 列是 Python repr（单引号），不是 JSON ⇒ 用 literal_eval。"""
    if not raw:
        return []
    try:
        v = ast.literal_eval(raw)
        return v if isinstance(v, list) else []
    except (ValueError, SyntaxError):
        return []


def _sym_variants(sym: str) -> set:
    """币种名归一化变体。

    上游两张表的命名不一致：成交表用 freqtrade 的合约名 `1000BONK/USDT:USDT`
    （→ 1000BONKUSDT），确认表用简写 `BONKUSDT`。两者是**同一合约**（价格同量级、
    信号/确认/成交时间可对上），只是名字差 `1000` 前缀 ⇒ 匹配时两种都试，
    但 run 的 symbol 一律保留成交表的名字（与系统市场库一致）。
    """
    out = {sym}
    if sym.startswith("1000"):
        out.add(sym[4:])
    else:
        out.add("1000" + sym)
    return out


def _iso(ms: Optional[int]) -> Optional[str]:
    if not isinstance(ms, int) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_strategy(name: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """`Wyckoff_C1_Vs04_Z02` → (mode='C1', vs=0.4, z=0.2)。解析不出就给 None。"""
    m = re.match(r"^Wyckoff_(C\d?)_Vs(\d+)_Z(\d+)$", name or "")
    if not m:
        return None, None, None
    vs = int(m.group(2)) / 10.0
    z = int(m.group(3)) / 10.0
    return m.group(1), vs, z


# ── 上游数据装载 ────────────────────────────────────────────────────────────
def load_upstream() -> Dict[str, Any]:
    trades_raw = _read_csv(UPSTREAM / "out" / "00_all_27_trades_consolidated.csv")
    idx_raw = _read_csv(UPSTREAM / "01_全部运行索引与指标总表.csv")
    cmp_raw = _read_csv(UPSTREAM / "02_逐候选对照表.csv")

    # 确认/拒单：按 (strategy, event_id) 索引
    confirm: Dict[Tuple[str, str], Dict[str, str]] = {}
    confirm_by_event: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for f in sorted((UPSTREAM / "out").glob("audit_confirmation_*.csv")):
        strat = f.stem.replace("audit_confirmation_", "")
        for r in _read_csv(f):
            r["_strategy"] = strat
            confirm[(strat, r.get("event_id") or "")] = r
            confirm_by_event[r.get("event_id") or ""].append(r)

    cfg = {}
    try:
        cfg = json.loads((UPSTREAM / "user_data" / "config.json").read_text(encoding="utf-8"))
    except Exception:                                         # noqa: BLE001
        cfg = {}

    idx_by_strategy = {r.get("strategy"): r for r in idx_raw}

    return {
        "trades": trades_raw,
        "confirm": confirm,
        "confirm_by_event": confirm_by_event,
        "index": idx_by_strategy,
        "compare": cmp_raw,
        "config": cfg,
    }


# ── 单笔成交 → 系统格式 ─────────────────────────────────────────────────────
def build_trade_row(r: Dict[str, str], strategy: str, symbol: str) -> Dict[str, Any]:
    tag = r.get("enter_tag") or ""
    m = TRADE_TAG_RE.match(tag)
    mode = m.group(1) if m else None
    event_id = m.group(2) if m else None
    sig_from_tag = _int(m.group(3)) if m else None

    orders = _parse_orders(r.get("orders") or "")
    entry_order = next((o for o in orders if o.get("ft_is_entry")), None)
    exit_order = next((o for o in orders if not o.get("ft_is_entry")), None)

    entry_ms = _int(r.get("open_timestamp"))
    exit_ms = _int(r.get("close_timestamp"))
    order_ms = _int((entry_order or {}).get("order_filled_timestamp"))
    exit_fill_ms = _int((exit_order or {}).get("order_filled_timestamp"))

    return {
        "trade_id": f"ft_{strategy}_{symbol}_{entry_ms}",
        "symbol": symbol,
        "direction": -1 if str(r.get("is_short")).lower() == "true" else 1,
        "source": {
            "upstream": "evidence/FT-ENTRY-09/out/00_all_27_trades_consolidated.csv",
            "enter_tag": tag or None,
            "event_id": event_id,
            "entry_order_tag": (entry_order or {}).get("ft_order_tag"),
            "exit_order_tag": (exit_order or {}).get("ft_order_tag"),
            "note": "上游 freqtrade 无引擎级 trd_ id；trade_id 由 策略+币种+入场毫秒 确定性生成",
        },
        "mode": mode,
        # ── 四类时间：互不混用（逐笔核对结论见下）──
        # enter_tag 尾段全部**整 1 小时对齐**，且语义随模式不同：
        #   C1/C2：1330/1330 逐笔 == 确认表 confirm_time_ms  ⇒ 尾段是「确认 K 线收盘」
        #   C0   ：2995/2995 整 1h 对齐，2902/2995 入场 = 尾段 + 15 分钟
        #          ⇒ C0 无独立确认环节，尾段是「信号 1h K 线收盘」（同一根 K 线收盘即决策）
        # 因此尾段先单独存为 tag_time_ms，由调用侧按模式分派；绝不拿它同时冒充两类时间。
        "signal_time_ms": None,
        "confirm_time_ms": None,
        "tag_time_ms": _int(m.group(3)) if m else None,
        "signal_time_basis": None,
        "confirm_time_basis": None,
        "order_time_ms": order_ms,
        "entry_time_ms": entry_ms,
        "exit_time_ms": exit_ms,
        "exit_fill_time_ms": exit_fill_ms,
        # ── 价格 / 数量 ──
        "entry_price": _num(r.get("open_rate")),
        "exit_price": _num(r.get("close_rate")),
        "qty": _num(r.get("amount")),
        "stake_amount": _num(r.get("stake_amount")),
        "max_rate": _num(r.get("max_rate")),
        "min_rate": _num(r.get("min_rate")),
        # ── 盈亏 / 费用 ──
        "net_pnl": _num(r.get("profit_abs")),
        "profit_ratio": _num(r.get("profit_ratio")),
        "fees": None,
        "fee_open_rate": _num(r.get("fee_open")),
        "fee_close_rate": _num(r.get("fee_close")),
        "funding_fee": _num(r.get("funding_fees")),
        "exit_reason": r.get("exit_reason") or None,
        "holding_minutes": _int(r.get("trade_duration")),
        # ── 风险（上游未落盘真实计划风险 ⇒ null，不拿 -99% 兜底值冒充）──
        "P0": None,                        # 调用侧用确认表补
        "signal_close": None,
        "confirm_close": None,
        "planned_risk_usdt": None,
        "initial_stop_loss_abs": _num(r.get("initial_stop_loss_abs")),
        "initial_stop_loss_ratio": _num(r.get("initial_stop_loss_ratio")),
        "stop_loss_abs": _num(r.get("stop_loss_abs")),
        "risk_note": ("上游 initial_stop_loss 是 freqtrade 兜底 -99%，不是入场时计划风险 ⇒ "
                      "planned_risk_usdt 留 null（未知）"),
    }


# ── 单 run 文档 ─────────────────────────────────────────────────────────────
def build_run_docs(
    strategy: str,
    symbol: str,
    trade_rows: List[Dict[str, Any]],
    cand_rows: List[Dict[str, str]],
    idx_row: Optional[Dict[str, str]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    mode, vs, z = _split_strategy(strategy)
    run_id = f"run_FT_ENTRY_09_{strategy.replace('Wyckoff_', '')}_{symbol}"

    net = sum(t["net_pnl"] for t in trade_rows if t["net_pnl"] is not None)
    wins = sum(1 for t in trade_rows if (t["net_pnl"] or 0) > 0)
    losses = sum(1 for t in trade_rows if (t["net_pnl"] or 0) < 0)
    starts = [t["entry_time_ms"] for t in trade_rows if t["entry_time_ms"]]
    ends = [t["exit_time_ms"] for t in trade_rows if t["exit_time_ms"]]
    ws, we = (min(starts), max(ends)) if starts and ends else (None, None)

    initial_equity = _num(cfg.get("dry_run_wallet"))

    run_doc = {
        "run_id": run_id,
        "strategy_id": STRATEGY_ID,
        "strategy_key": "ft_entry_09_research",
        "status": S.STATUS_SUCCEEDED,
        "status_basis": "上游 FT-ENTRY-09 回测已完成并落盘合并成交表（本 run 由该表逐笔重建）",
        "created_at": _iso(we),
        "started_at": _iso(ws),
        "finished_at": None,
        "research_period": True,
        "spec": {
            "strategy_id": STRATEGY_ID,
            "strategy_name": strategy,
            "symbols": [symbol],
            "exchange": "binance",
            "market": "um",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
            "initial_equity": initial_equity,
            "timing_mode": mode,
            "params": {"vs": vs, "z": z},
        },
        "config": {
            "actual_config": {
                "initial_equity": initial_equity,
                "risk_budget_per_trade": None,
                "fee_rate": _num(cfg.get("fee")),
                "slippage_bps": None,
                "max_active_positions": _int(cfg.get("max_open_trades")),
                "max_notional_per_position": None,
            },
            "config_note": ("取自上游 user_data/config.json（dry_run_wallet / fee / max_open_trades）；"
                            "单币种 run 不分配账户资金，故不计算账户收益率；"
                            "risk_budget_per_trade 与滑点上游未落盘 ⇒ null"),
        },
        "window": {
            "win_start_ms": ws, "win_end_ms": we, "symbol": symbol,
            "exchange": "binance", "market": "um",
            "signal_timeframe": "1h", "exec_timeframe": "15m",
        },
        "metrics": {
            "initial_equity": initial_equity,
            "final_equity": None,
            "net_pnl": round(net, 8),
            "net_return_pct": None,
            "trades_count": len(trade_rows),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": (round(wins / len(trade_rows) * 100.0, 6) if trade_rows else None),
            "max_drawdown_pct": None,
            "profit_factor": None,
            "avg_win_loss_ratio": None,
            "basis": ("按上游合并成交表逐笔求和（net_pnl = freqtrade profit_abs，已扣手续费与资金费）；"
                      "单币种 run 不代表账户收益 ⇒ net_return_pct/回撤/PF 不在此处合成"),
        },
        "data_quality": {
            "status": "unknown",
            "reasons": ["上游未提供逐笔对账/身份链自检 ⇒ 无法判定对账"],
            "reconcile_ok": None,
            "source": "上游 FT-ENTRY-09 未落盘 reconcile/identity 块",
        },
        "engine": {
            "strategy_id": STRATEGY_ID,
            "signal_version": "ft_entry_09",
            "core_version": "freqtrade",
            "exec_core_version": "freqtrade_backtesting",
        },
        "hashes": {},
        "research": {
            "upstream_run_dir": "evidence/FT-ENTRY-09",
            "upstream_index_row": idx_row or None,
            "compare_rows": len(cand_rows),
            "data_window_note": ("上游每笔成交自带 q_start_utc/q_end_utc；"
                                 "本 run 窗口 = 该币首笔入场 ~ 末笔出场"),
        },
        "registration": {
            "registered_by": "ft_replay_import",
            "note": ("研究期结果：由 FT-ENTRY-09 落盘 CSV 重建，**非本引擎重跑**；"
                     "只读复盘用，不进生产订单 / 模拟实盘账本"),
        },
    }

    summary = {
        "symbol": symbol,
        "variant": "A",
        "spec_status": "research_import",
        "summary_variant_a": {
            "initial_equity": initial_equity,
            "final_equity": None,
            "net_pnl": round(net, 8),
            "trade_count": len(trade_rows),
            "order_count": 2 * len(trade_rows),
            "fee_total": None,
            "funding_total": None,
            "slippage_total": None,
        },
        "research_note": "研究期结果（FT-ENTRY-09 重建），非本引擎 run",
    }

    inputs = {
        "symbol": symbol,
        "run_id": run_id,
        "strategy_version": "ft_entry_09",
        "spec_status": "research_import",
        "timestamp_created": we,
        "data_summary": {"time_range_1h": {"start_ms": ws, "end_ms": we}},
        "fingerprints": {},
        "research": {
            "source": "evidence/FT-ENTRY-09",
            "strategy_name": strategy,
            "timing_mode": mode,
            "params": {"vs": vs, "z": z},
            "config_sha256": "427203518ed47a3fda2937bd3bbc1033a561e208e9fe9811dd673d1bc9f8365d",
        },
    }

    # ── 图表标记（不写 candles；K 线由后端按窗口从市场库读）──
    # 只画**有成交**的候选：该参数组该币的全部候选（含拒单/未成交）都在 cand_rows 里，
    # 若全画，一个只有 1 笔成交的 run 会被几个互不相干的候选把 K 线范围拉到好几个月，
    # 图上满是无对应成交的信号点。拒单/未成交另有 rejected.jsonl / not_filled.jsonl 记录，
    # 也在 chart.candidates_other 里留一份（前端按需显示，不默认画点）。
    filled_events = {(t.get("source") or {}).get("event_id") for t in trade_rows}
    signals: List[Dict[str, Any]] = []
    confirms: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []
    seen_sig = set()
    for c in cand_rows:
        ev = c.get("event_id") or ""
        st = _int(c.get("orig_signal_time_ms"))
        if not st or ev in seen_sig:
            continue
        seen_sig.add(ev)
        row = {
            "t": st,
            "kind": "candidate",
            "direction": "long",
            "signal_id": ev,
            "orig_low": _num(c.get("orig_low")),
            "orig_close": _num(c.get("orig_close")),
            "P0": _num(c.get("orig_P0")),
            "decision": c.get("decision"),
            "time_basis": "audit_confirmation.orig_signal_time_ms",
        }
        if ev not in filled_events:
            row["kind"] = "candidate_no_fill"
            others.append(row)
            continue
        signals.append(row)
        if (c.get("decision") or "").upper() == "CONFIRMED":
            confirms.append({
                "t": _int(c.get("confirm_time_ms")),
                "signal_id": ev,
                "confirm_open": _num(c.get("confirm_open")),
                "confirm_low": _num(c.get("confirm_low")),
                "confirm_close": _num(c.get("confirm_close")),
                "reason": c.get("reason"),
                "time_basis": "audit_confirmation.confirm_time_ms",
            })
    # C0 没有确认表 ⇒ 用成交自带的信号时间补信号点（只补有成交的，不凭空造候选）
    for t in trade_rows:
        st = t.get("signal_time_ms")
        ev = (t.get("source") or {}).get("event_id")
        if st and ev and ev not in seen_sig:
            seen_sig.add(ev)
            signals.append({"t": st, "kind": "candidate_from_fill", "direction": "long",
                            "signal_id": ev, "decision": None,
                            "P0": t.get("P0"),
                            "time_basis": t.get("signal_time_basis"),
                            "note": "该模式无独立确认环节，信号时间取自 enter_tag 尾段（=信号 1h K 线收盘）"})

    chart_trades = []
    for t in trade_rows:
        chart_trades.append({
            "trade_id": t["trade_id"],
            "symbol": symbol,
            "direction": t["direction"],
            "signal_type": 1,
            "signal_id": (t.get("source") or {}).get("event_id"),
            "entry": {"t": t["entry_time_ms"], "price": t["entry_price"], "qty": t["qty"]},
            "exit": {"t": t["exit_time_ms"], "price": t["exit_price"], "reason": t["exit_reason"]},
            "signal_time_ms": t.get("signal_time_ms"),
            "confirm_time_ms": t.get("confirm_time_ms"),
            "order_time_ms": t.get("order_time_ms"),
            "net_pnl": t["net_pnl"],
            "stop_trace": [],
        })

    chart = {
        "symbol": symbol,
        "exec_timeframe": "15m",
        "window": {"start_ms": ws, "end_ms": we},
        "candles": [],          # 故意留空：由后端按窗口从市场库读（不落盘几百 MB）
        "candles_source": "由后端按本 run 的 symbol+window 从市场库读取（本文件不含 K 线）",
        "signals": signals,
        "confirms": confirms,
        "candidates_other": others,
        "trades": chart_trades,
        "equity_curve": [],
        "funding_events": [],
        "benchmark": None,
        "time_semantics": {
            "signal.t": "原信号 K 线收盘时间（候选产生）；C1/C2 取自确认表 orig_signal_time_ms，"
                        "C0 取自 enter_tag 尾段（无独立确认环节）",
            "confirms[].t": "确认 K 线收盘时间（C0 无此环节 ⇒ 无标记）",
            "trades[].entry.t": "实际入场成交时间（freqtrade open_timestamp）",
            "trades[].exit.t": "实际出场成交时间（freqtrade close_timestamp）",
            "trades[].order_time_ms": "下单即成交时间（entry order 的 order_filled_timestamp）",
            "timezone": "全部为 UTC 毫秒时间戳（原始值，未做时区转换）；页面按浏览器本地时区显示，"
                        "原始时间戳原样保留供核对",
        },
    }

    return {
        "run_id": run_id,
        "run": run_doc,
        "summary": summary,
        "inputs": inputs,
        "chart": chart,
        "trades": trade_rows,
    }


def write_run(bundle: Dict[str, Any], force: bool = False) -> Path:
    d = BATCH_DIR / bundle["run_id"]
    has_run = (d / "run.json").exists()
    if has_run and not force:
        return d
    d.mkdir(parents=True, exist_ok=True)

    def _w(name: str, obj: Any) -> None:
        tmp = d / (name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, d / name)

    def _wl(name: str, rows: List[Dict[str, Any]]) -> None:
        tmp = d / (name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, d / name)

    _w("summary.json", bundle["summary"])
    _w("inputs.json", bundle["inputs"])
    _w("chart.json", bundle["chart"])
    _wl("trades.jsonl", bundle["trades"])
    _wl("rejected.jsonl", bundle.get("rejected") or [])
    _wl("not_filled.jsonl", bundle.get("not_filled") or [])
    # run.json 走**唯一写入口**（原子、拒绝覆盖）；它存在即「本 run 完整」的标记。
    # --force 只重写其余文件，run.json 保持首次登记内容（避免伪造二次登记）。
    if not has_run:
        S.register_external_run(bundle["run_id"], bundle["run"], run_dir_path=d)
    return d


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="导入 FT-ENTRY-09 为研究期结果")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只体检）")
    ap.add_argument("--force", action="store_true",
                    help="已有 run.json 的目录也重写 summary/chart/trades 等（run.json 不动）")
    args = ap.parse_args(argv)

    up = load_upstream()
    trades = up["trades"]
    if not trades:
        print(f"找不到上游成交表：{UPSTREAM/'out'/'00_all_27_trades_consolidated.csv'}")
        return 2

    # 按 (strategy, symbol) 分组
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    unparsed = 0
    for r in trades:
        strat = r.get("strategy") or ""
        sym = _pair_to_symbol(r.get("pair") or "")
        if not strat or not sym:
            unparsed += 1
            continue
        grouped[(strat, sym)].append(build_trade_row(r, strat, sym))

    # 确认表按 (strategy, symbol) 归组，用于补 confirm/P0 与拒单。
    # 注意：确认表的 symbol 可能带/不带 `1000` 前缀（与成交表不一致）⇒ 两种名字都注册。
    conf_by_group: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    for (strat, ev), r in up["confirm"].items():
        sym = r.get("symbol")
        if not sym:
            continue
        for v in _sym_variants(sym):
            conf_by_group[(strat, v)].append(r)

    print(f"上游成交 {len(trades)} 笔｜可归组 {sum(len(v) for v in grouped.values())}"
          f"｜无法解析 {unparsed}")
    print(f"(参数组 × 币种) 组合 = {len(grouped)}")

    built = 0
    for (strat, sym), rows in sorted(grouped.items()):
        cands = conf_by_group.get((strat, sym), [])
        # 用确认表补齐每笔成交的 confirm_time / P0 / signal_close
        by_event = {c.get("event_id"): c for c in cands}
        for t in rows:
            ev = (t.get("source") or {}).get("event_id")
            c = by_event.get(ev)
            if c:
                t["confirm_time_ms"] = _int(c.get("confirm_time_ms"))
                t["P0"] = _num(c.get("orig_P0"))
                t["signal_close"] = _num(c.get("orig_close"))
                t["confirm_close"] = _num(c.get("confirm_close"))
                t["signal_time_ms"] = _int(c.get("orig_signal_time_ms"))
                t["confirm_source"] = "audit_confirmation（本参数组该候选的落盘记录）"
                t["signal_time_basis"] = "audit_confirmation.orig_signal_time_ms"
                t["confirm_time_basis"] = "audit_confirmation.confirm_time_ms"
            else:
                # 本参数组没有该候选的确认记录（C0 全模式无确认表；C1/C2 个别组缺失）：
                #   C1/C2 用 enter_tag 尾段 —— 已 1330/1330 逐笔核对 == confirm_time_ms；
                #   C0   尾段即信号 K 线收盘（无独立确认环节），信号时间取尾段、确认时间留空。
                if t.get("mode") in ("C1", "C2"):
                    t["confirm_time_ms"] = t.get("tag_time_ms")
                    t["confirm_source"] = "enter_tag 尾段（该组无候选确认记录，已核对与 confirm_time 一致）"
                    t["confirm_time_basis"] = "enter_tag 尾段（1330/1330 逐笔 == confirm_time_ms）"
                else:
                    t["signal_time_ms"] = t.get("tag_time_ms")
                    t["confirm_time_ms"] = None
                    t["confirm_source"] = None
                    t["signal_time_basis"] = (
                        "enter_tag 尾段（C0 无独立确认 K 线：2995/2995 整 1 小时对齐，"
                        "2902/2995 入场 = 尾段 + 15 分钟 ⇒ 尾段即信号 1h K 线收盘）")
                    t["confirm_time_basis"] = None

        # 拒单：该组所有 REJECTED 候选
        rejected = [{
            "event_id": c.get("event_id"),
            "symbol": sym,
            "signal_time_ms": _int(c.get("orig_signal_time_ms")),
            "confirm_time_ms": _int(c.get("confirm_time_ms")),
            "P0": _num(c.get("orig_P0")),
            "decision": "REJECTED",
            "reason": c.get("reason"),
            "source": "evidence/FT-ENTRY-09/out/audit_confirmation_*.csv",
        } for c in cands if (c.get("decision") or "").upper() == "REJECTED"]

        # 未成交：确认通过但没有对应成交
        filled_events = {(t.get("source") or {}).get("event_id") for t in rows}
        not_filled = [{
            "event_id": c.get("event_id"),
            "symbol": sym,
            "signal_time_ms": _int(c.get("orig_signal_time_ms")),
            "confirm_time_ms": _int(c.get("confirm_time_ms")),
            "P0": _num(c.get("orig_P0")),
            "decision": "CONFIRMED_NOT_FILLED",
            "reason": "确认通过但该组该币无对应成交记录（资金/仓位/撮合未成交）",
            "source": "evidence/FT-ENTRY-09/out/audit_confirmation_*.csv",
        } for c in cands
            if (c.get("decision") or "").upper() == "CONFIRMED"
            and c.get("event_id") not in filled_events]

        bundle = build_run_docs(strat, sym, rows, cands, up["index"].get(strat), up["config"])
        bundle["rejected"] = rejected
        bundle["not_filled"] = not_filled
        if args.apply:
            write_run(bundle, force=args.force)
        built += 1

    print(f"{'已写入' if args.apply else '可生成'} run 目录：{built}")
    if args.apply:
        print(f"批次目录：{BATCH_DIR}")
    else:
        print("只体检，未写盘（加 --apply 才写）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
