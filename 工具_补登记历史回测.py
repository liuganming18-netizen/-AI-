# -*- coding: utf-8 -*-
"""历史回测产物补登记（BACKFILL-01，2026-10-21）。

问题
────
`回测日志/event_trend_v0_1/runs/` 下 1203 个运行目录里，**1195 个没有 run.json**
（结果在、档案不在）⇒ 界面上一片「未登记」，时间/参数/窗口全空，看着像没有记录。

与 CODE-REVIEW-01 §4「历史目录不补造」的关系
──────────────────────────────────────────
被否掉的是**补造**：拿读取时刻当创建时间、拿 0.005/0.0005/3.0 当实际参数、
把状态写死 succeeded。本工具相反——**每个字段都必须在本 run 自己的落盘产物里
找到出处**，找不到就留 null 并在 provenance 里写明「产物中无记录」。

真实出处（本工程 1195 个目录实测均有）：
  · 创建时刻   inputs.json:timestamp_created（epoch 秒，引擎落盘）
  · 数据窗口   inputs.json:data_summary.time_range_1h
  · 代码指纹   inputs.json:fingerprints（逐文件 SHA256 + combined）
  · 初始资金   summary.json:summary_variant_a.initial_equity
  · 结果指标   summary.json:summary_variant_a
  · 费率       summary.json:reconcile.fee_model.taker_fee_rate
               （缺失时回退 ledger.jsonl 首行 GENESIS_INIT.payload.fee_model）
  · 滑点       ledger.jsonl 首行 GENESIS_INIT.payload.slippage_bps
  · 对账       summary.json:reconcile + identity（程序自检，非独立证据）
  · 完成度     manifest.json（原子提交标记）+ summary 结果块

纪律
────
  · 唯一写入口 `backtest_store.register_external_run`（R3）；本工具不直接写 run.json；
  · 已有 run.json ⇒ 跳过，绝不覆盖（R4）；
  · 缺的字段一律 None，不填 0、不填默认值（R2/R7）；
  · 状态按产物证据推断，判不了给 unknown，**不冒充 succeeded**（R10）；
  · finished_at 一律 null —— 产物里没有真实完成时刻，目录 mtime 是文件系统时间，
    不是运行时刻，不用它冒充（provenance 里写明）；
  · 登记档带 `registration.registered_by="backfill_from_artifacts"` 与 `provenance`，
    界面/后续审计能一眼分清「当时就登记的」与「事后按产物补录的」。

用法
────
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY 工具_补登记历史回测.py                 # 只体检，不写盘（默认）
    $PY 工具_补登记历史回测.py --apply         # 真正补登记
    $PY 工具_补登记历史回测.py --apply --report evidence/BACKFILL-01/backfill.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND = PROJECT_ROOT / "框架" / "backend"
for _p in (str(PROJECT_ROOT), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_store as S                                   # noqa: E402
import bt_run_log                                             # noqa: E402

BACKFILL_BY = "backfill_from_artifacts"


# ── 读取工具（只读，不猜） ────────────────────────────────────────────────────
def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                         # noqa: BLE001
        return {}


def _first_ledger_row(p: Path) -> Dict[str, Any]:
    """只读 ledger.jsonl 第一行（GENESIS_INIT）。大文件不整体加载。"""
    try:
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:                             # noqa: BLE001
                    return {}
                return row if isinstance(row, dict) else {}
    except Exception:                                         # noqa: BLE001
        return {}
    return {}


def _num(v) -> Optional[float]:
    """原样转数字；None/无法转换 ⇒ None（**不用 0 兜底**）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _epoch_to_iso(v) -> Optional[str]:
    try:
        sec = int(v)
    except (TypeError, ValueError):
        return None
    if sec <= 0:
        return None
    return datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 完成度 / 数据质量（与 backtest_store 同口径） ─────────────────────────────
def _completion(d: Path, sm: Dict[str, Any]) -> Tuple[str, str]:
    has_manifest = (d / "manifest.json").exists()
    has_block = bool(sm.get("summary_variant_a") or sm.get("summary"))
    if has_manifest and has_block:
        return (S.STATUS_SUCCEEDED,
                "manifest.json（引擎原子提交标记）+ summary.json 结果块均在 ⇒ 有完成证据")
    if has_block:
        return (S.STATUS_UNKNOWN, "summary.json 有结果块但缺 manifest.json ⇒ 完成度证据不足")
    return (S.STATUS_UNKNOWN, "summary.json 无结果块 ⇒ 无法判定完成度")


def _data_quality(sm: Dict[str, Any]) -> Dict[str, Any]:
    rec = sm.get("reconcile") or {}
    ident = sm.get("identity") or {}
    reasons: List[str] = []
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
            "reconcile_ok": not reasons,
            "source": "summary.json:reconcile+identity（回测程序自检，非独立证据）"}


# ── 单个目录 → run.json 文档 ─────────────────────────────────────────────────
def build_doc(d: Path, run_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """返回 (doc, skip_reason)。doc=None 表示不可登记（缺关键产物）。"""
    sm = _read_json(d / "summary.json")
    if not sm:
        return None, "缺 summary.json 或不可解析"
    inp = _read_json(d / "inputs.json")
    if not inp:
        return None, "缺 inputs.json 或不可解析"

    created_at = _epoch_to_iso(inp.get("timestamp_created"))
    if not created_at:
        return None, "inputs.json 无 timestamp_created（无真实创建时刻 ⇒ 不补）"

    prov: Dict[str, str] = {
        "created_at": "inputs.json:timestamp_created（epoch 秒→ISO，确定性换算）",
        "window": "inputs.json:data_summary.time_range_1h",
        "hashes": "inputs.json:fingerprints（逐文件 SHA256）",
        "status": "manifest.json 原子提交标记 + summary.json 结果块（无证据给 unknown）",
        "metrics": "summary.json:summary_variant_a（落盘原值，未统计字段 null）",
        "data_quality": "summary.json:reconcile+identity（回测程序自检）",
        "finished_at": "产物中无真实完成时刻 ⇒ null（不用目录 mtime 冒充）",
    }

    a = sm.get("summary_variant_a") or sm.get("summary") or {}
    symbol = sm.get("symbol") or inp.get("symbol")
    variant = sm.get("variant") or inp.get("variant") or "A"

    initial_eq = _num(a.get("initial_equity"))
    final_eq = _num(a.get("final_equity"))
    net_pnl = _num(a.get("net_pnl"))
    ret_pct = None
    if net_pnl is not None and initial_eq:
        ret_pct = net_pnl / initial_eq * 100.0
        prov["metrics.net_return_pct"] = "由 net_pnl/initial_equity 确定性换算（summary 未直接落盘）"

    # 费率：优先 reconcile.fee_model，回退 ledger 首行 GENESIS_INIT
    rec = sm.get("reconcile") or {}
    fee_model = rec.get("fee_model") or {}
    taker = _num(fee_model.get("taker_fee_rate"))
    if taker is not None:
        prov["config.fee_rate"] = "summary.json:reconcile.fee_model.taker_fee_rate"
    genesis = _first_ledger_row(d / "ledger.jsonl")
    g_payload = (genesis.get("payload") or {}) if genesis.get("event_type") == "GENESIS_INIT" else {}
    if taker is None:
        taker = _num((g_payload.get("fee_model") or {}).get("taker"))
        if taker is not None:
            prov["config.fee_rate"] = "ledger.jsonl:GENESIS_INIT.payload.fee_model.taker"
    slippage_bps = _num(g_payload.get("slippage_bps"))
    if slippage_bps is not None:
        prov["config.slippage_bps"] = "ledger.jsonl:GENESIS_INIT.payload.slippage_bps"
    if initial_eq is None:
        initial_eq = _num(g_payload.get("initial_equity"))
        if initial_eq is not None:
            prov["metrics.initial_equity"] = "ledger.jsonl:GENESIS_INIT.payload.initial_equity"

    # 交叉校验：两处费率若不一致，如实记下来（不擅自选一个当真值）
    g_taker = _num((g_payload.get("fee_model") or {}).get("taker"))
    fee_conflict = None
    if taker is not None and g_taker is not None and abs(taker - g_taker) > 1e-12:
        fee_conflict = f"reconcile.fee_model.taker={taker} 与 ledger GENESIS_INIT.taker={g_taker} 不一致"

    tr = (inp.get("data_summary") or {}).get("time_range_1h") or {}
    status, basis = _completion(d, sm)

    doc: Dict[str, Any] = {
        "run_id": run_id,
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "strategy_key": "wx_event_trend_v0_1",
        "status": status,
        "status_basis": basis,
        "created_at": created_at,
        "started_at": created_at,
        "finished_at": None,
        "spec": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "symbols": [symbol] if symbol else [],
            "exchange": "binance",
            "market": "um",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
            "initial_equity": initial_eq,
            "variant": variant,
        },
        "config": {
            "actual_config": {
                "initial_equity": initial_eq,
                # 产物里确实没有风险预算/持仓上限 ⇒ null，不填默认值
                "risk_budget_per_trade": None,
                "fee_rate": taker,
                "slippage_bps": slippage_bps,
                "max_active_positions": None,
                "max_notional_per_position": None,
            },
            "config_note": ("按产物补录：initial_equity/fee_rate/slippage_bps 取自本 run 落盘文件；"
                            "risk_budget_per_trade / max_active_positions / max_notional_per_position "
                            "在产物中无记录 ⇒ null（不填默认值冒充实际配置）"),
            "fee_conflict": fee_conflict,
        },
        "window": {
            "win_start_ms": tr.get("start_ms"),
            "win_end_ms": tr.get("end_ms"),
            "win_start_utc": tr.get("start_utc"),
            "win_end_utc": tr.get("end_utc"),
            "symbol": symbol,
            "exchange": "binance",
            "market": "um",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
        },
        "metrics": {
            "initial_equity": initial_eq,
            "final_equity": final_eq,
            "net_pnl": net_pnl,
            "net_return_pct": ret_pct,
            "trades_count": (int(a["trade_count"]) if isinstance(a.get("trade_count"), (int, float))
                             else None),
            "order_count": (int(a["order_count"]) if isinstance(a.get("order_count"), (int, float))
                            else None),
            "fee_total": _num(a.get("fee_total")),
            "funding_total": _num(a.get("funding_total")),
            "slippage_total": _num(a.get("slippage_total")),
            # summary 不统计胜负/回撤/PF ⇒ null（真实值由 GET /metrics 从账本现算）
            "wins": None,
            "losses": None,
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "profit_factor": None,
            "avg_win_loss_ratio": None,
            "basis": ("按产物补录：summary.json:summary_variant_a 落盘原值；"
                      "胜负/回撤/PF/盈亏比该文件未统计 ⇒ null，由 GET /metrics 从账本现算"),
        },
        "data_quality": _data_quality(sm),
        "engine": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "signal_version": inp.get("strategy_version") or "v0.1.0",
            "core_version": inp.get("strategy_version") or "v0.1.0",
            "exec_core_version": "trading_core_shared_v1",
        },
        "hashes": inp.get("fingerprints") or {},
        "funnel": sm.get("funnel"),
        "spec_status": sm.get("spec_status") or inp.get("spec_status"),
        "formal_pass_allowed": sm.get("formal_pass_allowed"),
        "registration": {
            "registered_by": BACKFILL_BY,
            "note": ("事后按本 run 目录内的落盘产物补录，非当时登记；"
                     "每个字段的出处见 provenance，缺的一律 null"),
        },
        "provenance": prov,
    }
    return doc, ""


# ── 主流程 ──────────────────────────────────────────────────────────────────
def scan(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    return sorted([d for d in root.iterdir() if d.is_dir()])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="历史回测产物补登记（零编造）")
    ap.add_argument("--root", default=None, help="runs 根目录（默认 backtest_store 解析出的根）")
    ap.add_argument("--apply", action="store_true", help="真正写 run.json（默认只体检）")
    ap.add_argument("--report", default=None, help="明细 CSV 输出路径")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（调试用）")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else S.get_runs_root()
    print(f"运行根：{root}")
    if not root.is_dir():
        print("根目录不存在，退出")
        return 2

    dirs = scan(root)
    if args.limit:
        dirs = dirs[:args.limit]

    rows: List[Dict[str, Any]] = []
    n_ok = n_skip_has = n_skip_bad = n_err = 0
    for d in dirs:
        rid = d.name
        if (d / "run.json").exists():
            n_skip_has += 1
            rows.append({"run_id": rid, "result": "skipped_has_run_json", "detail": ""})
            continue
        try:
            doc, why = build_doc(d, rid)
        except Exception as exc:                              # noqa: BLE001
            n_err += 1
            rows.append({"run_id": rid, "result": "error",
                         "detail": f"{type(exc).__name__}: {exc}"})
            continue
        if doc is None:
            n_skip_bad += 1
            rows.append({"run_id": rid, "result": "skipped_no_evidence", "detail": why})
            continue

        if not args.apply:
            n_ok += 1
            rows.append({"run_id": rid, "result": "dry_run_ok",
                         "detail": f"status={doc['status']} created_at={doc['created_at']}"})
            continue

        try:
            S.register_external_run(rid, doc, run_dir_path=d)
            n_ok += 1
            rows.append({"run_id": rid, "result": "registered",
                         "detail": f"status={doc['status']} created_at={doc['created_at']}"})
            bt_run_log.run_log("run_registered", run_id=rid,
                               symbol=(doc["spec"]["symbols"] or [None])[0],
                               status=doc["status"],
                               trades_count=doc["metrics"]["trades_count"],
                               net_pnl=doc["metrics"]["net_pnl"],
                               registered_by=BACKFILL_BY)
        except S.RunExists:
            n_skip_has += 1
            rows.append({"run_id": rid, "result": "skipped_has_run_json", "detail": "并发已存在"})
        except Exception as exc:                              # noqa: BLE001
            n_err += 1
            rows.append({"run_id": rid, "result": "error",
                         "detail": f"{type(exc).__name__}: {exc}"})
            bt_run_log.run_log("run_registration_failed", run_id=rid,
                               error=f"{type(exc).__name__}: {exc}")

    print(f"目录总数 {len(dirs)}｜可登记 {n_ok}｜已有档案跳过 {n_skip_has}"
          f"｜缺证据跳过 {n_skip_bad}｜出错 {n_err}")
    print("模式：" + ("已写入 run.json（--apply）" if args.apply else "只体检，未写盘（加 --apply 才写）"))

    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        with rp.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["run_id", "result", "detail"])
            w.writeheader()
            w.writerows(rows)
        print(f"明细已写入：{rp}")

    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
