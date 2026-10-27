# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜09 报告数字机械复算。

原则（本单纪律）：**报告里的每个关键数字都必须能从 `out/*.json` 的原始字段复算出来**，
人工抄写已经出过多次错。本脚本不「读报告再判断对错」，而是
  ① 从 `out/*.json` 取原始字段 → 按固定格式生成字符串；
  ② 断言该字符串**原样出现在报告里**；
  ③ 任一不成立即 FAIL（退出码 1）。

另外做一条「不得伪造」检查：报告里不得出现未经否定的过度声明。

产出：out/09_报告复算结果.json
"""
from __future__ import annotations

import csv
import importlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

REPORT = cm.ME / "00_报告_SIGNAL-HISTORY-FIX-01.md"


def load(name):
    return json.loads((cm.OUT / name).read_text(encoding="utf-8"))


def main():
    rpt = REPORT.read_text(encoding="utf-8")
    cov = load("01_覆盖审计.json")
    atr = load("02_ATR预热对照.json")
    fun = load("03_漏斗汇总.json")
    d_bef = load("04_缺陷复现_before.json")
    d_aft = load("04_缺陷复现_after.json")
    lnk = load("05_三路衔接对比.json")
    aft = load("06_修复后全池复核.json")
    led = load("07_执行链路核对.json")
    man = load("08_产物清单与上游快照.json")

    rows = list(csv.DictReader(open(cm.OUT / "01_逐币覆盖.csv", encoding="utf-8")))
    inner_gap = {tf: sum(int(float(r[f"{tf}_inner_gap_bars"] or 0)) for r in rows)
                 for tf in ("1h", "4h", "15m")}

    checks = []

    def chk(desc, value, fmt, src):
        checks.append({"desc": desc, "value": value, "expect_in_report": fmt,
                       "source": src, "pass": fmt in rpt})

    # ── §2 覆盖 ────────────────────────────────────────────────────────
    chk("池规模", cov["pool_n"], str(cov["pool_n"]), "01.pool_n")
    for tf in ("1h", "4h", "15m"):
        v = cov["pool"][tf]
        chk(f"{tf} 实际跨度", v["pool_span_days"], f"{v['pool_span_days']:.2f} 天",
            f"01.pool.{tf}.pool_span_days")
        chk(f"{tf} 距 180 天缺口", v["shortfall_days_vs_180"],
            f"{v['shortfall_days_vs_180']:.2f} 天", f"01.pool.{tf}.shortfall_days_vs_180")
        chk(f"{tf} 覆盖 180 天的标的数", v["symbols_covering_180d"],
            str(v["symbols_covering_180d"]), f"01.pool.{tf}.symbols_covering_180d")
        chk(f"{tf} 内部缺口合计", inner_gap[tf], str(inner_gap[tf]),
            f"01_逐币覆盖.csv.{tf}_inner_gap_bars.sum")
    chk("180 天数据是否足够", cov["data_sufficient_for_180d"],
        "**不满足 180 天**" if not cov["data_sufficient_for_180d"] else "满足", "01.data_sufficient_for_180d")

    # ── §3 ATR ────────────────────────────────────────────────────────
    im = atr["impact"]
    chk("ATR 评估时点数", im["bars_evaluated"], f"{im['bars_evaluated']:,}", "02.impact.bars_evaluated")
    chk("ATR 相对差中位", im["atr_rel_diff_median"],
        f"{im['atr_rel_diff_median'] * 100:.4f}%", "02.impact.atr_rel_diff_median")
    chk("ATR 相对差 p95", im["atr_rel_diff_p95"],
        f"{im['atr_rel_diff_p95'] * 100:.4f}%", "02.impact.atr_rel_diff_p95")
    chk("ATR 相对差最大", im["atr_rel_diff_max"],
        f"{im['atr_rel_diff_max'] * 100:.4f}%", "02.impact.atr_rel_diff_max")
    chk("ATR 相对差 >1% 根数", im["atr_rel_diff_gt_1pct"],
        f"{im['atr_rel_diff_gt_1pct']:,}", "02.impact.atr_rel_diff_gt_1pct")
    chk("ATR 相对差 >5% 根数", im["atr_rel_diff_gt_5pct"],
        f"{im['atr_rel_diff_gt_5pct']:,}", "02.impact.atr_rel_diff_gt_5pct")
    flip = im["width_verdict_flip_engine_in_bt_out"] + im["width_verdict_flip_engine_out_bt_in"]
    chk("宽度闸翻转次数", flip, f"{flip:,}", "02.impact.width_verdict_flip_* 之和")
    chk("宽度闸翻转率", im["flip_rate_of_evaluated"],
        f"{im['flip_rate_of_evaluated'] * 100:.4f}%", "02.impact.flip_rate_of_evaluated")
    chk("建档 engine_only", im["box_form_engine_only"], f"{im['box_form_engine_only']}",
        "02.impact.box_form_engine_only")
    chk("建档 bt_only", im["box_form_bt_only"], f"{im['box_form_bt_only']}",
        "02.impact.box_form_bt_only")

    # ── §4 漏斗 ───────────────────────────────────────────────────────
    tt = fun["transition_totals_in_replay"]
    for k in ("BOX", "SWEEP", "RETEST", "CANDIDATE", "INVALIDATED", "EXPIRED",
              "MISSED_WITHOUT_RETEST"):
        chk(f"历史迁移 {k}", tt[k], str(tt[k]), f"03.transition_totals_in_replay.{k}")
    chk("历史候选个数", fun["history_candidates_total"], str(fun["history_candidates_total"]),
        "03.history_candidates_total")
    chk("bootstrap ready", fun["bootstrap"]["ready"], str(fun["bootstrap"]["ready"]),
        "03.bootstrap.ready")
    chk("bootstrap skipped", fun["bootstrap"]["skipped"], str(fun["bootstrap"]["skipped"]),
        "03.bootstrap.skipped")
    for c in fun["candidates"]:
        chk(f"候选 {c['symbol']} 时刻", c["available_at_utc"], c["available_at_utc"],
            "03.candidates.available_at_utc")
    # 当前阶段（修复前脚本口径）
    sd = fun["current_stage_distribution"]
    for k, v in sd.items():
        chk(f"阶段 {k}", v, str(v), f"03.current_stage_distribution.{k}")

    # ── §5 衔接 ───────────────────────────────────────────────────────
    chk("三路保留标的数", lnk["symbols_picked_n"], str(lnk["symbols_picked_n"]),
        "05.symbols_picked_n")
    chk("三路全部通过", lnk["verdict_all_pass"], "ALL_PASS = True" if lnk["verdict_all_pass"]
        else "ALL_PASS = False", "05.verdict_all_pass")
    chk("三路状态差异数", lnk["state_comparison"]["P2_vs_P3"]["n_diff"],
        f"n_diff={lnk['state_comparison']['P2_vs_P3']['n_diff']}",
        "05.state_comparison.P2_vs_P3.n_diff")
    chk("重复 step 新候选（第2次）", lnk["duplicate_processing"]["second_run_same_t"],
        f"second_run_same_t={lnk['duplicate_processing']['second_run_same_t']}",
        "05.duplicate_processing.second_run_same_t")

    # ── §6 缺陷与修复 ─────────────────────────────────────────────────
    b1 = d_bef["defects"]["D1_bootstrap_watermark"]
    a1 = d_aft["defects"]["D1_bootstrap_watermark"]
    chk("D1 修复前带尾根标的数（隔离集）", b1["symbols_with_tail_gap"],
        f"{b1['symbols_with_tail_gap']} / {b1['symbols_checked']}",
        "04_before.D1.symbols_with_tail_gap")
    chk("D1 修复后带尾根标的数", a1["symbols_with_tail_gap"], str(a1["symbols_with_tail_gap"]),
        "04_after.D1.symbols_with_tail_gap")
    chk("D1 修复前 has_new 为真的标的数", b1["step_has_new_true_now"],
        f"{b1['step_has_new_true_now']} / {b1['symbols_checked']}",
        "04_before.D1.step_has_new_true_now")
    dp = aft["d1_full_pool"]
    chk("D1 全池带尾根标的数", dp["symbols_with_tail_gap_old_behavior"],
        f"**{dp['symbols_with_tail_gap_old_behavior']} / {dp['symbols_checked']}**",
        "06.d1_full_pool.symbols_with_tail_gap_old_behavior")
    chk("D1 全池阶段变化标的数", dp["symbols_whose_stage_changed_if_tail_bar_processed"],
        f"**{dp['symbols_whose_stage_changed_if_tail_bar_processed']} 个标的阶段发生变化**",
        "06.d1_full_pool.symbols_whose_stage_changed_if_tail_bar_processed")
    for s in dp["stage_changes"]:
        chk(f"D1 阶段变化 {s['symbol']}", s["symbol"], f"`{s['symbol']}`",
            "06.d1_full_pool.stage_changes")
    chk("修复后水位不一致标的数", aft["fix_effective"]["symbols_with_watermark_mismatch"],
        f"**{aft['fix_effective']['symbols_with_watermark_mismatch']}**",
        "06.fix_effective.symbols_with_watermark_mismatch")
    chk("修复后无回归", aft["regression"]["no_regression"],
        "无回归" if aft["regression"]["no_regression"] else "有回归",
        "06.regression.no_regression")
    chk("IDLE 坏死标的数", aft["idle_breakdown"]["counts"].get("IDLE_DEAD_BARS"),
        f"IDLE_DEAD_BARS={aft['idle_breakdown']['counts'].get('IDLE_DEAD_BARS')}",
        "06.idle_breakdown.counts.IDLE_DEAD_BARS")
    chk("IDLE 冷却标的数", aft["idle_breakdown"]["counts"].get("IDLE_COOLING"),
        f"IDLE_COOLING={aft['idle_breakdown']['counts'].get('IDLE_COOLING')}",
        "06.idle_breakdown.counts.IDLE_COOLING")
    chk("IDLE 等箱体标的数", aft["idle_breakdown"]["counts"].get("IDLE_WAIT_BOX"),
        f"IDLE_WAIT_BOX={aft['idle_breakdown']['counts'].get('IDLE_WAIT_BOX')}",
        "06.idle_breakdown.counts.IDLE_WAIT_BOX")
    chk("IDLE 合计", aft["idle_breakdown"]["idle_total"],
        f"合计 **{aft['idle_breakdown']['idle_total']}**",
        "06.idle_breakdown.idle_total")
    chk("D3 skipped（本窗口）", d_aft["defects"]["D3_bootstrap_hard_gate"]["skipped"],
        f"skipped=0", "04_after.D3.skipped")
    chk("D4 ATR=0 标的数", len(d_aft["defects"]["D4_dead_bars_atr_zero"]["atr_zero_symbols"]),
        str(len(d_aft["defects"]["D4_dead_bars_atr_zero"]["atr_zero_symbols"])),
        "04_after.D4.symbols_atr_zero")

    # ── §7 账本 ───────────────────────────────────────────────────────
    for t in ("live_signal", "paper_pending", "paper_order", "paper_position",
              "paper_fill", "paper_realized", "paper_blocked"):
        n = led["tables"].get(t, {}).get("n")
        chk(f"账本 {t} 行数", n, f"| `{t}` | **{n}** |" if t == "live_signal"
            else f"| `{t}` | {n} |", f"07.tables.{t}.n")
    chk("账本真实候选驱动行数", led["verdict"]["rows_attributable_to_real_candidate_anywhere"],
        "**0 行**" if led["verdict"]["rows_attributable_to_real_candidate_anywhere"] == 0
        else str(led["verdict"]["rows_attributable_to_real_candidate_anywhere"]),
        "07.verdict.rows_attributable_to_real_candidate_anywhere")

    # ── §开头 SHA ─────────────────────────────────────────────────────
    ch = man["changed_files"][0]
    chk("修复前 SHA", ch["sha256_before"], ch["sha256_before"], "08.changed_files.sha256_before")
    chk("修复后 SHA", ch["sha256_now"], ch["sha256_now"], "08.changed_files.sha256_now")
    chk("修复前字节数", ch["size_now"], f"{ch['size_now']:,} B", "08.changed_files.size_now")
    chk("冻结策略文件未改", man["frozen_strategy_all_unchanged"],
        "逐项相同" if man["frozen_strategy_all_unchanged"] else "有变动",
        "08.frozen_strategy_all_unchanged")

    # ── 不得伪造：过度声明必须带否定 ──────────────────────────────────
    FORBIDDEN = ["成交链路已打通", "4 个候选全部验证", "状态恢复已完成",
                 "逐次成交已补齐", "图表验收已全部通过", "零信号已解决"]
    NEG = ("不得", "禁止", "不应", "不算", "未验证", "不存在", "不可得", "不能",
           "没有", "未做", "未通过", "缺口", "不写成")
    forged = []
    for ln in rpt.split("\n"):
        for ph in FORBIDDEN:
            if ph in ln and not any(n in ln for n in NEG):
                forged.append({"line": ln.strip()[:160], "phrase": ph})

    res = {
        "ticket": "SIGNAL-HISTORY-FIX-01",
        "generated_at_utc": cm.utc_now(),
        "report": str(REPORT),
        "report_sha256": cm.sha256_file(REPORT),
        "checks_total": len(checks),
        "checks_pass": sum(1 for c in checks if c["pass"]),
        "checks_fail": [c for c in checks if not c["pass"]],
        "checks": checks,
        "forged_claims": forged,
        "verdict_all_pass": (all(c["pass"] for c in checks) and not forged),
    }
    p = cm.write_json("09_报告复算结果.json", res)

    print(f"checks: {res['checks_pass']} / {res['checks_total']} pass")
    for c in res["checks_fail"]:
        print("  FAIL:", c["desc"], "value=", c["value"], "expect=", c["expect_in_report"],
              "src=", c["source"])
    print("forged:", forged)
    print("VERDICT_ALL_PASS:", res["verdict_all_pass"], "->", p)
    return 0 if res["verdict_all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
