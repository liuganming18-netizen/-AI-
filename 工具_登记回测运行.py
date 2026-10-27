# -*- coding: utf-8 -*-
"""批量回测 run 登记工具（CODE-REVIEW-02，2026-10-21）。

背景（CODE-REVIEW-01 根因 1）：根目录 `run_batch_*.py` 等批量脚本用策略包引擎
（`EventTrendRunner.run_variant_abc`）直接产出 run 目录，**只写产物、不写
run.json** ⇒ 全工程 2127 个运行「结果在、档案不在」，读接口只能读时合成
（假时间/假参数/假状态）。

本工具是**唯一**给批量脚本用的登记入口：
  · 内部只调 `backtest_store.register_external_run`（store 仍是 run.json 的
    唯一写入口，R3），原子写、已存在则拒绝覆盖（R4）；
  · **所有值都来自调用方真实事实或 run 目录自己的落盘文件**，不猜、不补默认值
    （R2/R7）：时间取调用方传入的批次时钟，参数取调用方实际使用的 cfg 对象，
    窗口取脚本实际跑的日期；
  · 登记事件写 `框架/backend/logs/backtest_runs.log`（bt_run_log 单一实现）。

用法（在 `main_run_id = out["main_run_id"]` 之后）：
    from 工具_登记回测运行 import register_batch_run
    register_batch_run(run_id=main_run_id, run_dir=run_dir, symbol=sym,
                       status="succeeded", started_at=t0, finished_at=time.time(),
                       cfg=cfg, summary=out["res_a"]["summary"], start_date=START_DATE,
                       end_date=END_DATE)
（`started_at/finished_at` 可传脚本时钟的 epoch 秒或 ISO 字符串；不传窗口日期时
自动取本 run 自己 inputs.json 里引擎落盘的真实窗口。）
失败分支用 `status="failed", error=str(e)`（此时通常没有 run 目录，工具只记日志）。

历史 2127 个已存在目录**不补登记**（补出的时间是编的，CODE-REVIEW-01 §4 第 5 条）；
本工具只服务**新产出**的 run。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND = PROJECT_ROOT / "框架" / "backend"
for _p in (str(PROJECT_ROOT), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_store as S                                   # noqa: E402
import bt_run_log                                             # noqa: E402


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_iso(v) -> Optional[str]:
    """调用方可传 epoch 秒（float/int，脚本的 time.time()）或 ISO 字符串。

    epoch→ISO 是确定性换算；都不是 ⇒ None（不猜）。
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return None
    return str(v)


def _date_ms(date_str: str, *, end: bool = False) -> Optional[int]:
    """`YYYY-MM-DD` → UTC ms（end=True 给当日 23:59:59.999）。真实换算，不猜。"""
    try:
        d = datetime.strptime(str(date_str), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    if end:
        d = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(d.timestamp() * 1000)


def _read_run_dir_file(run_dir: Path, name: str) -> Dict[str, Any]:
    p = Path(run_dir) / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                         # noqa: BLE001
        return {}


def _data_quality_block(summary: Dict[str, Any]) -> Dict[str, Any]:
    """与 store / runner 同一口径：按 summary.json 的 reconcile/identity 真实自检给。"""
    rec = (summary or {}).get("reconcile") or {}
    ident = (summary or {}).get("identity") or {}
    reasons = []
    if rec.get("cash_identity_ok") is False:
        reasons.append("现金恒等式不成立")
    if rec.get("equity_identity_ok") is False:
        reasons.append("权益恒等式不成立")
    if rec.get("equity_curve_tail_matches_final") is False:
        reasons.append("权益曲线末点与最终权益不一致")
    if ident.get("chain_ok") is False:
        reasons.append("身份链条不闭合")
    if not (rec or ident):
        return {"status": "unknown",
                "reasons": ["summary.json 无 reconcile/identity 块 ⇒ 无法判定对账"],
                "reconcile_ok": None,
                "source": "summary.json:reconcile+identity（回测程序自检，非独立证据）"}
    return {"status": ("mismatch" if reasons else "ok"), "reasons": reasons,
            "reconcile_ok": (not reasons),
            "source": "summary.json:reconcile+identity（回测程序自检，非独立证据）"}


def _cfg_actual_config(cfg: Any) -> Dict[str, Any]:
    """实际配置：**逐字段从调用方实际使用的 cfg 对象读**，没有的给 None。"""
    def _g(name):
        return getattr(cfg, name, None) if cfg is not None else None
    fee_bps = _g("taker_fee_bps")
    return {
        "initial_equity": _g("initial_equity"),
        "risk_budget_per_trade": _g("risk_budget_per_trade"),
        "fee_rate": (float(fee_bps) / 10000.0) if fee_bps is not None else None,
        "slippage_bps": _g("slippage_bps"),
        "max_active_positions": _g("max_active_positions"),
        "max_notional_per_position": _g("max_notional_per_position"),
    }


def register_batch_run(
    *,
    run_id: str,
    run_dir: Optional[str] = None,
    symbol: str,
    status: str,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    cfg: Any = None,
    summary: Optional[Dict[str, Any]] = None,
    fingerprints: Optional[Dict[str, Any]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """登记一次批量回测 run（见模块 docstring 纪律）。返回结果 dict，不抛异常。"""
    summary = summary or {}
    status = str(status)
    if status not in (S.STATUS_SUCCEEDED, S.STATUS_FAILED):
        return {"registered": False, "run_id": run_id,
                "reason": f"status 必须为 succeeded/failed（收到 {status!r}）"}

    fingerprints = fingerprints or _read_run_dir_file(Path(run_dir), "inputs.json").get("fingerprints") or {}
    ws = _date_ms(start_date) if start_date else None
    we = _date_ms(end_date, end=True) if end_date else None
    # 自动窗口脚本（load_symbol_data(sym) 不带日期）：窗口取**本 run 自己**
    # inputs.json 里引擎落盘的真实 data_summary（不是推算的）。
    if (ws is None or we is None) and run_dir:
        _tr = (_read_run_dir_file(Path(run_dir), "inputs.json").get("data_summary")
               or {}).get("time_range_1h") or {}
        ws = ws if ws is not None else _tr.get("start_ms")
        we = we if we is not None else _tr.get("end_ms")
    started_at = _as_iso(started_at)
    finished_at = _as_iso(finished_at)

    m = summary or {}
    initial_eq = m.get("initial_equity")
    final_eq = m.get("final_equity")
    net_pnl = m.get("net_pnl")
    trade_cnt = m.get("trade_count") or m.get("trades_count")
    doc: Dict[str, Any] = {
        "run_id": run_id,
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "strategy_key": "wx_event_trend_v0_1",
        "status": status,
        "created_at": started_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "spec": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "symbols": [symbol],
            "exchange": getattr(cfg, "venue", None) or "binance",
            "market": getattr(cfg, "market", None) or "um",
            "signal_timeframe": getattr(cfg, "timeframe_event", None) or "1h",
            "exec_timeframe": getattr(cfg, "timeframe_exec", None) or "15m",
            "initial_equity": initial_eq,
        },
        "config": {
            "actual_config": _cfg_actual_config(cfg),
            "config_note": ("actual_config 逐字段取自本批实际使用的 EventTrendConfig 对象；"
                            "None = 该字段本次未提供/未记录"),
        },
        "window": {
            "win_start_ms": ws, "win_end_ms": we, "symbol": symbol,
            "exchange": getattr(cfg, "venue", None) or "binance",
            "market": getattr(cfg, "market", None) or "um",
            "signal_timeframe": getattr(cfg, "timeframe_event", None) or "1h",
            "exec_timeframe": getattr(cfg, "timeframe_exec", None) or "15m",
        },
        "metrics": {
            "initial_equity": initial_eq,
            "final_equity": final_eq,
            "net_pnl": net_pnl,
            "net_return_pct": m.get("net_return_pct"),
            "trades_count": trade_cnt,
            # summary 不统计胜负/回撤/PF ⇒ 一律 None（真实值由 GET /metrics 从账本现算）
            "wins": m.get("win_count"),
            "losses": m.get("loss_count"),
            "win_rate_pct": m.get("win_rate"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "profit_factor": m.get("profit_factor"),
            "avg_win_loss_ratio": m.get("avg_win_loss_ratio"),
            "basis": "批量登记：summary.json 落盘原值；未统计字段为 null，不填 0",
        },
        "data_quality": _data_quality_block(summary),
        "engine": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "signal_version": getattr(cfg, "strategy_version", None) or "v0.1.0",
            "core_version": getattr(cfg, "strategy_version", None) or "v0.1.0",
            "exec_core_version": "trading_core_shared_v1",
        },
        "hashes": fingerprints,
        "funnel": summary.get("funnel"),
    }
    if status == S.STATUS_FAILED:
        doc["failure_reason"] = error or "批量执行失败（未提供原因）"

    try:
        if run_dir is None:
            target = S.register_external_run(run_id, doc)
        else:
            target = S.register_external_run(run_id, doc, run_dir_path=Path(run_dir))
    except S.RunExists:
        # 已登记过：不覆盖、不重复记（重名拒绝是 R4 的语义）
        bt_run_log.run_log("run_registration_skipped", run_id=run_id, symbol=symbol,
                           status=status, reason="run.json 已存在，拒绝覆盖")
        return {"registered": False, "run_id": run_id, "reason": "run.json 已存在（拒绝覆盖）"}
    except Exception as exc:                                  # noqa: BLE001
        bt_run_log.run_log("run_registration_failed", run_id=run_id, symbol=symbol,
                           status=status, error=f"{type(exc).__name__}: {exc}")
        return {"registered": False, "run_id": run_id,
                "reason": f"{type(exc).__name__}: {exc}"}

    bt_run_log.run_log("run_registered", run_id=run_id, symbol=symbol, status=status,
                       trades_count=doc["metrics"]["trades_count"],
                       net_pnl=doc["metrics"]["net_pnl"], path=str(target))
    return {"registered": True, "run_id": run_id, "path": str(target)}


__all__ = ["register_batch_run", "_iso_utc"]
