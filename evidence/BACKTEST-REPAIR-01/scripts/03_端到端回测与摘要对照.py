#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BACKTEST-REPAIR-01 · 端到端隔离回测 + 绩效字段对照。

做三件事：
  1. 走**官方入口** `backtest_runner.execute_run(run_id)`（含 FRAME-FIT-01 实验闸门）
     跑一次完整回测，记录状态、失败原因、指标、数据质量；
  2. 把落盘 `summary.json` 的字段清单打出来，做「旧 13 字段 / 新增字段」对照；
  3. 单独用**同一条等价路径**（状态机 → 执行核心）产出一份 summary 快照，
     供基线代码与当前代码逐字段比对（`--dump` 模式）。

为什么第 3 步要单独来：FT-LOW4H 分支在原代码里根本跑不出候选，
直接比 `run_variant_abc` 的 summary 只会得到「两边都是 0 笔」的无意义对照。
这里固定用「`run_state_machine_simulation` + `run_event_trend_backtest`」这条
**在两个版本上都存在且签名兼容**的路径，才能比出「同样的成交、旧字段数值是否一字不变」。

跑法：
    export MARKET_DB=… MARKET_ARCHIVE=… CONTRACTS_DIR=… EVENT_TREND_RUNS_DIR=…
    /usr/bin/python3 evidence/BACKTEST-REPAIR-01/scripts/03_端到端回测与摘要对照.py \
        --symbol NEARUSDT --start 2026-06-01 --end 2026-09-30
    # 只导摘要快照（供基线对照）：
    /usr/bin/python3 …/03_端到端回测与摘要对照.py --dump /path/to/summary.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
ROOT = EVIDENCE.parents[1]
for p in (str(ROOT / "框架" / "backend"), str(ROOT / "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _utc_ms(day: str) -> int:
    """`YYYY-MM-DD` → UTC 毫秒（当日 00:00Z，不受本机时区影响）。"""
    import calendar
    return calendar.timegm(time.strptime(day + " 00:00:00", "%Y-%m-%d %H:%M:%S")) * 1000


def _research_contract() -> dict:
    """研究运行的执行契约：**取自 `make_ft_low4h_config()` 的冻结参数**。

    本工作树里 `experiment_registry.default_execution_contract()` 返回 None
    （生产生效视图随旧工作区删除，`approved_params()` 取不到）——**不伪造视图**，
    改为把 FT-LOW4H 冻结参数显式声明成研究契约，并在报告里标明来源。
    """
    from WyckoffMaster.event_trend_v0_1.config import make_ft_low4h_config
    c = make_ft_low4h_config()
    return {
        "rule": "FT-LOW4H",
        "stoploss_s": float(c.stoploss_s),
        "trail_d": float(c.trail_d),
        "risk_per_trade_equity_fraction": float(c.risk_budget_per_trade),
        "fee_each_side": float(c.taker_fee_bps) / 10000.0,
        "slippage_each_side": float(c.slippage_bps) / 10000.0,
        "entry_participation_previous_15m_quote_cap":
            float(c.max_participation_of_prev_15m_quote),
        "initial_equity": float(c.initial_equity),
        "source": "make_ft_low4h_config()（研究声明；非生产生效视图）",
    }


def dump_summary(symbol: str, start: str, end: str, out_path: str) -> int:
    """基线/当前版本通用的摘要快照（只用两版本都有的签名）。"""
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
    from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
    from WyckoffMaster.event_trend_v0_1.config import make_ft_low4h_config

    r = EventTrendRunner()
    cfg = make_ft_low4h_config()
    d4, d1, d15, ots, ovs, fnd = r.load_symbol_data(
        symbol=symbol, start_date=start, end_date=end, allow_auto_window=False)
    # ⚠ 不传 cfg：基线版本没有这个参数；当前版本默认值 == make_ft_low4h_config()（逐字段相同）
    sm = r.run_state_machine_simulation(symbol, d4, d1, ots, ovs, enable_oi_filter=True)
    spec = r._resolve_spec(symbol, d15, cfg)
    res = run_event_trend_backtest(
        symbol=symbol, df_1h=d1, df_15m=d15, candidates=sm.candidates,
        funding_events=fnd, df_4h=d4, variant="A", initial_equity=5000.0,
        config=cfg, instrument_spec=spec)
    payload = {
        "symbol": symbol, "window": [start, end],
        "bar_4h": len(d4), "bar_1h": len(d1), "bar_15m": len(d15),
        "candidates": len(sm.candidates),
        "trade_count_engine": len(res.get("engine_result", {}).get("trades") or []),
        "summary": res["summary"],
        "summary_keys": sorted(res["summary"].keys()),
    }
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    print(f"摘要快照：{p}  候选={len(sm.candidates)} 成交={payload['trade_count_engine']}"
          f" summary字段={len(payload['summary_keys'])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NEARUSDT")
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--dump", default=None, help="只导出摘要快照到该路径")
    ap.add_argument("--out", default=str(EVIDENCE / "out" / "03_端到端回测.json"))
    args = ap.parse_args()

    if args.dump:
        return dump_summary(args.symbol, args.start, args.end, args.dump)

    import backtest_store as S
    import experiment_registry as EXP
    import backtest_runner as BTR

    spec = {
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "symbols": [args.symbol],
        "initial_equity": 5000.0,
        "window": {"mode": "explicit",
                   "start_ms": _utc_ms(args.start), "end_ms": _utc_ms(args.end)},
        "execution_contract": _research_contract(),
        "experiment": {"mode": "research"},
    }

    # ── FRAME-FIT-01 闸门：**不绕过**，按正规流程登记 + 冻结 ──
    # `execute_run` 要求 run 绑定「已登记且已冻结」的实验。这里把登记库指到
    # 本工作树内的隔离目录（`FRAME_FIT_REGISTRY`），登记一条**研究**实验并冻结。
    # 注意：冻结只是让闸门放行；`formal_eligible` 仍恒为 False
    # （研究模式运行不具正式资格），冻结不等于「正式通过」。
    reg_root = Path(os.environ.get("EVENT_TREND_RUNS_DIR")
                    or (ROOT / "回测日志" / "event_trend_v0_1" / "runs")) / "_bt01_registry"
    reg_root.mkdir(parents=True, exist_ok=True)
    os.environ["FRAME_FIT_REGISTRY"] = str(reg_root)

    scope = {"symbols": spec["symbols"], "window_start": None, "window_end": None,
             "source": "request"}
    policy = EXP.SplitPolicy.from_dict({})
    parts = EXP.current_design_parts(
        sample_scope=scope, split_policy=policy,
        metrics_spec=EXP.DEFAULT_METRICS_SPEC,
        execution_contract=spec["execution_contract"])
    reg = EXP.get_registry()
    # 实验 ID 由设计身份派生（确定性）⇒ 重复执行会撞同一个 ID。
    # 已存在就直接复用（必要时补冻结），**不覆盖**已有登记。
    try:
        exp = reg.register(design=parts, sample_scope=scope, split_policy=policy,
                           metrics_spec=EXP.DEFAULT_METRICS_SPEC)
    except Exception as exc:                                  # noqa: BLE001
        if "已存在" not in str(exc):
            raise
        exp = (reg.state()["experiments"] or {}).get(parts.get("experiment_id") or "")
        if exp is None:
            # ID 由登记逻辑内部派生，这里按设计身份反查
            for eid0, e in (reg.state()["experiments"] or {}).items():
                if e.get("design_identity") == EXP.compute_design_identity(parts):
                    exp = e
                    break
        if exp is None:
            raise
    eid = exp["experiment_id"]
    if not exp.get("frozen_at"):
        reg.freeze(eid, by="BACKTEST-REPAIR-01",
                   note="隔离研究登记：仅用于让 FRAME-FIT-01 闸门放行以验证管道可执行；"
                        "非正式实验资格，formal_eligible 恒 False")
    spec["experiment"] = {"mode": "research", "experiment_id": eid}

    # run_id 必须符合 `backtest_store.RUN_ID_RE`（bt_<UTC 秒级>_<8 位哈希>）
    import hashlib
    spec_hash = hashlib.sha256(
        json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    run_id = S.new_run_id(spec_hash)
    doc = {"run_id": run_id, "spec": spec, "experiment": spec["experiment"],
           "created_at": time.time()}

    report = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "run_id": run_id, "env": {k: os.environ.get(k) for k in
                                        ("MARKET_DB", "MARKET_ARCHIVE", "CONTRACTS_DIR",
                                         "EVENT_TREND_RUNS_DIR", "WX_RUNS_BASE")},
              "worktree": str(ROOT), "spec": spec}

    # 创建闸门（研究模式：允许运行，formal_eligible 恒 False）
    gate = EXP.guard_create(spec, run_id=run_id)
    report["guard_create"] = gate
    report["experiment_id"] = eid
    report["registry_root"] = str(reg_root)
    S.create_run(run_id, doc)

    t0 = time.time()
    out = BTR.execute_run(run_id)
    report["elapsed_s"] = round(time.time() - t0, 2)
    report["status"] = out.get("status")
    report["failure_reason"] = out.get("failure_reason")
    report["metrics"] = out.get("metrics")
    report["data_quality"] = out.get("data_quality")
    report["summary_funnel"] = out.get("summary")
    report["window"] = out.get("window")

    # 落盘 summary.json 的字段清单（旧 13 / 新增）
    run_dir = S.run_dir(run_id)
    report["run_dir"] = str(run_dir)
    try:
        sm_json = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        report["summary_json_keys"] = sorted(sm_json.keys())
        report["trading_summary_keys"] = sorted((sm_json.get("trading_summary") or {}).keys())
        report["trading_summary"] = sm_json.get("trading_summary")
        report["formal_pass_allowed"] = sm_json.get("formal_pass_allowed")
        report["formal_pass_block_reasons"] = sm_json.get("formal_pass_block_reasons")
        report["pit_qualified"] = sm_json.get("pit_qualified")
        report["pit_audit_sha256"] = sm_json.get("pit_audit_sha256")
    except Exception as exc:                                  # noqa: BLE001
        report["summary_json_error"] = f"{type(exc).__name__}: {exc}"

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")

    print("=" * 84)
    print(f"run_id = {run_id}")
    print(f"闸门   = mode={gate.get('mode')} ok={gate.get('ok')} "
          f"blockers={gate.get('blockers')} registered={gate.get('registered')}")
    print(f"状态   = {report['status']}  失败原因={report['failure_reason']}")
    print(f"耗时   = {report['elapsed_s']}s")
    m = report["metrics"] or {}
    if m:
        print("指标：")
        for k in ("initial_equity", "final_equity", "net_pnl", "net_return_pct",
                  "trades_count", "wins", "losses", "flats", "win_rate_pct",
                  "gross_pnl", "total_fees", "total_funding_fee", "total_slippage",
                  "avg_win", "avg_loss", "avg_win_loss_ratio", "profit_factor",
                  "max_drawdown_pct", "period_start_equity", "period_end_equity",
                  "equity_curve_points", "cost_identity_ok"):
            print(f"    {k} = {m.get(k)}")
        print(f"    口径说明 = {m.get('metrics_notes')}")
    print(f"数据质量 = {report['data_quality']}")
    print(f"漏斗     = {report['summary_funnel']}")
    print(f"正式资格 = {report.get('formal_pass_allowed')}  "
          f"阻断原因={report.get('formal_pass_block_reasons')}")
    print(f"trading_summary 字段数 = {len(report.get('trading_summary_keys') or [])}")
    print(f"报告：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
