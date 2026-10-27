# -*- coding: utf-8 -*-
"""
2026-08-01 ~ 2026-09-14 随机 100 标的批量回测（真实合约规格版）

与旧批次的区别（这是重点）：
    旧批次在缺合约规格时回退到 0.01 的默认价格步长，导致低价币的成交价
    被量化到 0.01 网格（如 0.0211 被推成 0.03），产生不可能成交的价格。
    本脚本走当前代码路径：由回测入口按标的解析真实最小价格变动单位，
    解析不到直接报错，不再回退默认值。

产物落在独立目录 `回测日志/event_trend_v0_1/runs_aug100/`，
不与旧的 576 个运行混在一起，避免污染既有统计。

用法：
    python run_batch_2026_aug_100.py            # 全量 100 个
    LIMIT=2 python run_batch_2026_aug_100.py    # 只跑前 2 个（先验证）
"""

import json
import os
import sys
import time

PROJECT_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
for p in (PROJECT_ROOT, f"{PROJECT_ROOT}/策略", f"{PROJECT_ROOT}/框架/backend"):
    if p not in sys.path:
        sys.path.insert(0, p)

from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
import backtest_chart as _BC

START_DATE = os.environ.get("START_DATE", "2026-08-01")
END_DATE = os.environ.get("END_DATE", "2026-09-14")   # 本地行情实际覆盖到的最后一日
INITIAL_EQUITY = 2000.0

SYMBOLS_FILE = os.path.join(PROJECT_ROOT, os.environ.get("SYMBOLS_FILE", "selected_100_symbols_2026_aug.json"))
with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
    SYMBOLS = json.load(f)

LIMIT = int(os.environ.get("LIMIT", "0") or 0)
if LIMIT:
    SYMBOLS = SYMBOLS[:LIMIT]

# 试跑用：PILOT=标的一,标的二 直接指定，便于先验证再全量
_pilot = os.environ.get("PILOT", "").strip()
if _pilot:
    SYMBOLS = [s.strip() for s in _pilot.split(",") if s.strip()]

OUT_DIR = os.path.join(PROJECT_ROOT, "evidence", "BACKTEST-LOSS-03",
                       os.environ.get("OUT_NAME", "aug100"))
RUNS_SUBDIR = os.environ.get("RUNS_SUBDIR", "runs_aug100")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cfg = EventTrendConfig(initial_equity=INITIAL_EQUITY)
    runner = EventTrendRunner()
    runner.runs_base = os.path.join(PROJECT_ROOT, "回测日志", "event_trend_v0_1", RUNS_SUBDIR)
    os.makedirs(runner.runs_base, exist_ok=True)

    print("=" * 78)
    print("区间 %s ~ %s ｜ 标的 %d 个 ｜ 本金 %.0f" % (START_DATE, END_DATE, len(SYMBOLS), INITIAL_EQUITY))
    print("运行目录 %s" % runner.runs_base)
    print("=" * 78)

    results = []
    t_all = time.time()
    for idx, sym in enumerate(SYMBOLS, 1):
        t0 = time.time()
        print("[%03d/%d] %-16s" % (idx, len(SYMBOLS), sym), end=" ", flush=True)
        rec = {"symbol": sym, "run_id": "", "status": "FAIL", "trades": 0,
               "net_pnl": 0.0, "spec_status": "", "price_step": "", "elapsed_s": 0.0}
        try:
            df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events = runner.load_symbol_data(
                sym, start_date=START_DATE, end_date=END_DATE,
                # 严格按标称窗口跑。若放开自适应，短窗口（15m 不足 2000 根）会被静默替换成
                # 该币上线后 180 天，跑出来的交易日期跟窗口对不上。
                allow_auto_window=(os.environ.get("ALLOW_AUTO_WINDOW", "0") == "1"),
            )
            out = runner.run_variant_abc(
                symbol=sym, df_4h=df_4h, df_1h=df_1h, df_15m=df_15m,
                oi_ts=oi_ts, oi_vals=oi_vals, funding_events=funding_events,
                cfg=cfg, initial_equity=INITIAL_EQUITY, create_variant_runs=False,
            )
            run_id = out["main_run_id"]
            run_dir = os.path.join(runner.runs_base, run_id)

            # CODE-REVIEW-02：登记 run.json（历史批次只写产物不写 run.json
            # ⇒ 「结果在、档案不在」；原子写、已存在则拒绝覆盖，登记失败不影响回测结果）
            try:
                from 工具_登记回测运行 import register_batch_run
                register_batch_run(
                    run_id=run_id, run_dir=run_dir, symbol=sym, status="succeeded",
                    started_at=t0, finished_at=time.time(),
                    cfg=cfg, summary=out["res_a"]["summary"],
                    start_date=START_DATE, end_date=END_DATE,
                )
            except Exception as _reg_exc:  # noqa: BLE001
                print(f"  [登记失败，不影响回测结果] {_reg_exc}")
            rec["run_id"] = run_id

            # 前端图表 payload（门禁要按 K 线区间核对成交价，需要它）
            try:
                _BC.generate_chart_payload(sym, run_dir, df_1h, df_15m, INITIAL_EQUITY)
            except Exception as exc:
                rec["chart_error"] = str(exc)

            s = out["res_a"]["summary"]
            rec["status"] = "PASS"
            rec["trades"] = s.get("trade_count", 0)
            rec["net_pnl"] = s.get("net_pnl", 0.0)

            # 记录本次运行真正使用的规格（用来确认不是 0.01 兜底）
            spec_file = os.path.join(run_dir, "instrument_spec.json")
            if os.path.exists(spec_file):
                try:
                    spec = json.load(open(spec_file, encoding="utf-8"))
                    inner = spec.get("instrument_spec") or spec
                    rec["price_step"] = str(inner.get("price_step") or inner.get("tick") or "")
                    rec["spec_status"] = str(spec.get("spec_status") or inner.get("spec_status") or "")
                except Exception:
                    pass
            print("完成 %5.1fs 成交 %d 笔 净%+7.2f 步长 %s" % (
                time.time() - t0, rec["trades"], rec["net_pnl"], rec["price_step"] or "未记录"))
        except Exception as exc:
            rec["status"] = "FAIL: %s" % exc
            print("失败 %5.1fs %s" % (time.time() - t0, exc))
        rec["elapsed_s"] = round(time.time() - t0, 2)
        results.append(rec)

    total = time.time() - t_all
    ok = [r for r in results if r["status"] == "PASS"]
    traded = [r for r in ok if r["trades"] > 0]
    print("-" * 78)
    print("完成 %d/%d ｜ 有成交 %d 个 ｜ 成交合计 %d 笔 ｜ 净盈亏合计 %+.2f ｜ 用时 %.1f 分" % (
        len(ok), len(results), len(traded),
        sum(r["trades"] for r in ok), sum(r["net_pnl"] for r in ok), total / 60.0))

    out_file = os.path.join(OUT_DIR, "%s_summary.json" % os.environ.get("OUT_NAME", "aug100"))
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "window": {"start": START_DATE, "end": END_DATE},
            "initial_equity": INITIAL_EQUITY,
            "runs_base": runner.runs_base,
            "total_symbols": len(results),
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print("汇总写入 %s" % out_file)

    # 自动留痕：批次跑完立刻写回执（谁、何时、什么代码、产出什么）
    trade_total = sum(r["trades"] for r in ok)
    pnl_total = round(sum(r["net_pnl"] for r in ok), 4)
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from 工具_写批次回执 import write_receipt, append_batch_log

        receipt = write_receipt(
            out_file, "AI 助手（WorkBuddy）",
            "区间 %s~%s 随机 %d 标的，真实合约规格版" % (START_DATE, END_DATE, len(results)),
            extra={"成交笔数": trade_total, "净盈亏合计": pnl_total},
        )
        print("回执写入 %s" % receipt)

        # 成交价门禁 + 追加批次运行日志
        import subprocess
        gate_csv = os.path.join(OUT_DIR, "gate_check.csv")
        subprocess.run([sys.executable,
                        os.path.join(PROJECT_ROOT, "evidence", "BACKTEST-LOSS-03",
                                     "verify_fill_within_bar.py"),
                        "--runs-dir", runner.runs_base, "--csv", gate_csv],
                       capture_output=True, text=True)
        viol = 0
        if os.path.exists(gate_csv):
            with open(gate_csv, encoding="utf-8-sig") as f:
                viol = max(0, sum(1 for _ in f) - 1)
        log = append_batch_log(
            window_start=START_DATE, window_end=END_DATE,
            symbol_count=len(results), trade_count=trade_total, net_pnl=pnl_total,
            violation_legs=viol, runs_subdir=os.path.basename(runner.runs_base),
            operator="AI 助手（WorkBuddy）", summary_path=out_file,
            detail_lines=["回执：`%s`" % os.path.relpath(receipt, PROJECT_ROOT)],
        )
        print("批次日志追加 %s（越界腿 %d）" % (log, viol))

        # 前端「逐笔明细」同步（追加 + 去重，可重复执行）
        subprocess.run([sys.executable,
                        os.path.join(PROJECT_ROOT, "工具_同步前端逐笔.py"),
                        "--runs-dir", runner.runs_base],
                       capture_output=True, text=True)
        print("已同步前端逐笔明细")
    except Exception as exc:
        print("留痕失败：%s" % exc)


if __name__ == "__main__":
    main()
