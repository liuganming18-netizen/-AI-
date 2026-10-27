# -*- coding: utf-8 -*-
"""
2026 年 9 月 15 日至 10 月 16 日随机 50 标的规范批量回测

规格与规范：
1. 随机抽样 50 个具备完整 9-15 ~ 10-16 行情/OI/资金费的有效标的；
2. 初始权益：2000 USDT；
3. 区间：2026-09-15 00:00:00 至 2026-10-16 23:59:59 (UTC)；
4. 独立落盘全套账本：summary.json, chart.json, trades.jsonl, fills.jsonl, orders.jsonl, inputs.json 等；
5. 自动汇总真实成交逐笔明细，并增量合并至前端 UI public/all_trades.json。
"""

import os
import sys
import json
import time
import datetime
import pandas as pd
import numpy as np

PROJECT_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if f"{PROJECT_ROOT}/策略" not in sys.path:
    sys.path.insert(0, f"{PROJECT_ROOT}/策略")
if f"{PROJECT_ROOT}/框架/backend" not in sys.path:
    sys.path.insert(0, f"{PROJECT_ROOT}/框架/backend")

from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
import backtest_chart as _BC

START_DATE = "2026-09-15"
END_DATE = "2026-10-16"

SYMBOLS_FILE = os.path.join(PROJECT_ROOT, "selected_50_symbols_2026_sep_oct.json")
with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
    TARGET_50_SYMBOLS = json.load(f)


def generate_chart(symbol: str, run_dir: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, initial_equity: float):
    return _BC.generate_chart_payload(symbol, run_dir, df_1h, df_15m, initial_equity)


def update_all_trades_json(new_trades: list):
    all_trades_path = os.path.join(PROJECT_ROOT, "UI界面/web/public/all_trades.json")
    existing_items = []
    if os.path.exists(all_trades_path):
        try:
            with open(all_trades_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                existing_items = d.get("items", [])
        except Exception:
            existing_items = []

    seen_keys = set()
    merged = []
    for t in existing_items:
        key = (t.get("run_id"), t.get("trade_id") or t.get("order_id"))
        if key not in seen_keys:
            seen_keys.add(key)
            merged.append(t)

    for t in new_trades:
        key = (t.get("run_id"), t.get("trade_id") or t.get("order_id"))
        if key not in seen_keys:
            seen_keys.add(key)
            t["entry_time_ms"] = t.get("entry_time") or t.get("entry_time_ms")
            t["exit_time_ms"] = t.get("exit_time") or t.get("exit_time_ms")
            # ⚠ 禁止用 order_id 顶替 trade_id（2026-10-18 事故根因，详见 run_batch_2026_aug_20.py）
            t["order_id"] = t.get("order_id")
            t["trade_id"] = t.get("trade_id") or None
            merged.append(t)

    merged.sort(key=lambda x: x.get("entry_time_ms") or 0, reverse=True)

    with open(all_trades_path, "w", encoding="utf-8") as f:
        json.dump({
            "total": len(merged),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": merged
        }, f, indent=2, ensure_ascii=False)
    print(f"\n[前端同步] 全市场逐笔明细已同步至 {all_trades_path}，当前池中共有 {len(merged)} 笔真实成交！")


def main():
    print("=" * 80)
    print(f"=== 开始执行 2026-09-15 至 2026-10-16 随机 50 标的批量回测 ===")
    print(f"=== 标的总量: {len(TARGET_50_SYMBOLS)} | 账户本金: 2000 USDT ===")
    print(f"=== 区间设定: {START_DATE} ~ {END_DATE} (数据 100% 完备对齐) ===")
    print("=" * 80)

    cfg = EventTrendConfig(initial_equity=2000.0)
    runner = EventTrendRunner()

    results = []
    executed_trades = []
    start_all = time.time()

    for idx, sym in enumerate(TARGET_50_SYMBOLS, 1):
        t0 = time.time()
        print(f"[{idx:02d}/50] 正在回测 {sym:<14} ...", end=" ", flush=True)
        try:
            df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events = runner.load_symbol_data(
                sym, start_date=START_DATE, end_date=END_DATE
            )
            out = runner.run_variant_abc(
                symbol=sym,
                df_4h=df_4h,
                df_1h=df_1h,
                df_15m=df_15m,
                oi_ts=oi_ts,
                oi_vals=oi_vals,
                funding_events=funding_events,
                cfg=cfg,
                initial_equity=2000.0,
                create_variant_runs=False
            )
            main_run_id = out["main_run_id"]
            run_dir = os.path.join(runner.runs_base, main_run_id)

            # CODE-REVIEW-02：登记 run.json（历史批次只写产物不写 run.json
            # ⇒ 「结果在、档案不在」；原子写、已存在则拒绝覆盖，登记失败不影响回测结果）
            try:
                from 工具_登记回测运行 import register_batch_run
                register_batch_run(
                    run_id=main_run_id, run_dir=run_dir, symbol=sym, status="succeeded",
                    started_at=t0, finished_at=time.time(),
                    cfg=cfg, summary=out["res_a"]["summary"],
                    start_date=START_DATE, end_date=END_DATE,
                )
            except Exception as _reg_exc:  # noqa: BLE001
                print(f"  [登记失败，不影响回测结果] {_reg_exc}")

            # 生成前端图表载荷
            generate_chart(sym, run_dir, df_1h, df_15m, 2000.0)

            summary_a = out["res_a"]["summary"]
            net_pnl = summary_a.get("net_pnl", 0.0)
            trades_count = summary_a.get("trade_count", 0)
            ret_pct = (net_pnl / 2000.0) * 100.0
            elapsed = time.time() - t0

            # 读取真实逐笔成交
            trades_file = os.path.join(run_dir, "trades.jsonl")
            run_trades = []
            if os.path.exists(trades_file):
                with open(trades_file, "r", encoding="utf-8") as f_tr:
                    for l in f_tr:
                        if l.strip():
                            t_obj = json.loads(l)
                            t_obj["run_id"] = main_run_id
                            t_obj["symbol"] = sym
                            run_trades.append(t_obj)
                            executed_trades.append(t_obj)

            status_str = f"【成交 {trades_count} 笔】净盈亏:{net_pnl:+7.2f} U ({ret_pct:+6.2f}%)" if trades_count > 0 else "0 笔成交 (观望)"
            print(f"-> 完成 ({elapsed:.2f}s) | {status_str}")

            results.append({
                "symbol": sym,
                "run_id": main_run_id,
                "status": "PASS",
                "trades": trades_count,
                "net_pnl": net_pnl,
                "return_pct": ret_pct,
                "elapsed_s": round(elapsed, 2),
                "funnel_boxes": out["funnel_a"]["boxes"],
                "candidates": out["funnel_a"]["candidates"]
            })
        except Exception as e:
            elapsed = time.time() - t0
            print(f"-> 失败 ({elapsed:.2f}s): {e}")
            results.append({
                "symbol": sym,
                "run_id": "",
                "status": f"FAIL: {e}",
                "trades": 0,
                "net_pnl": 0.0,
                "return_pct": 0.0,
                "elapsed_s": round(elapsed, 2),
                "funnel_boxes": 0,
                "candidates": 0
            })

    total_time = time.time() - start_all
    print("\n" + "=" * 80)
    print(f"=== 50 标的回测全部执行完毕！总耗时: {total_time:.2f} 秒 ===")
    print("=" * 80)

    # 统计指标
    tot_trades = sum(r["trades"] for r in results)
    tot_pnl = sum(r["net_pnl"] for r in results)
    pass_runs = sum(1 for r in results if r["status"] == "PASS")
    trade_runs = sum(1 for r in results if r["trades"] > 0)
    tot_boxes = sum(r["funnel_boxes"] for r in results)
    tot_cands = sum(r["candidates"] for r in results)

    print(f"成功运行标的: {pass_runs} / 50")
    print(f"发现 4h 箱体总数: {tot_boxes} 个")
    print(f"突破候选总数: {tot_cands} 个")
    print(f"有成交标的数: {trade_runs} 个")
    print(f"总成交笔数: {tot_trades} 笔")
    print(f"总净盈亏 (简单累加): {tot_pnl:+.4f} USDT")

    if executed_trades:
        print("\n【真实成交逐笔清单】:")
        df_trades = pd.DataFrame(executed_trades)
        cols_show = [c for c in ["symbol", "entry_price", "exit_price", "net_pnl", "exit_reason", "run_id"] if c in df_trades.columns]
        print(df_trades[cols_show].to_string())
        # 同步更新前端
        update_all_trades_json(executed_trades)
    else:
        print("\n[结果总结] 本轮 50 个标的在此区间内共计 0 笔成交（均在震荡观察或漏斗过滤中）。")

    # 保存本批次汇总表
    out_csv = os.path.join(PROJECT_ROOT, "evidence/batch_2026_sep_oct_50_summary.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    pd.DataFrame(results).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n汇总表已落盘: {out_csv}")


if __name__ == "__main__":
    main()
