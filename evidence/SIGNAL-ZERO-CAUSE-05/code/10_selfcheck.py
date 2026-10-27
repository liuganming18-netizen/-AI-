# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜10 报告数字自检（每个声称都回到产物文件复算）。

纪律（项目长期）：报告里的关键数字必须能被脚本机械复算，人工抄写已多次出错。
本脚本不重新跑回放，只读 `out/` 里的产物 + 生产 API，逐条核对主报告声称的数字。

产出：out/10_报告数字自检.json
用法：python code/10_selfcheck.py
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "out"


def rj(name):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> int:
    cfg = cm.cfg()
    rows = []
    j01 = rj("01_规范与实现对照.json")
    j02 = rj("02_汇总.json")
    j02b = rj("02_汇总_修复前.json")
    j03 = rj("03_三场景对照.json")
    j03b = rj("03_三场景对照_修复前.json")
    j04 = rj("04_闸门对照.json")
    j05 = rj("05_修复后parity.json")
    j06 = rj("06_生产核验_对比.json")
    j06p = rj("06_生产核验_post.json")
    j06r = rj("06_生产核验_pre.json")
    j07 = rj("07_当前卡点汇总.json")
    j07a = rj("07_应触发复算.json")
    j07b = rj("07_不应触发核对.json")
    j08 = rj("08_用户入口.json")

    def ck(claim, expected, actual, src):
        ok = (expected == actual)
        if isinstance(expected, float) and isinstance(actual, (int, float)):
            ok = abs(expected - actual) < 1e-6
        rows.append({"claim": claim, "expected": expected, "actual": actual,
                     "pass": bool(ok), "source": src})

    # ── 冻结身份 ────────────────────────────────────────────────────────────
    ck("冻结规则 SHA-256 未变",
       "1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab",
       (j01 or {}).get("identity", {}).get("spec_sha256"), "01_规范与实现对照.json")
    ck("冻结参数 atr_warmup_bars", 200, cfg.atr_warmup_bars, "make_ft_low4h_config()")
    ck("冻结参数 box_bars_4h", 42, cfg.box_bars_4h, "make_ft_low4h_config()")
    ck("冻结参数 breakout_max_total_hours", 336, cfg.breakout_max_total_hours,
       "make_ft_low4h_config()")
    ck("冻结参数 cooling_hours", 24, cfg.cooling_hours, "make_ft_low4h_config()")
    ck("冻结参数 risk_budget_per_trade", 0.005, cfg.risk_budget_per_trade,
       "make_ft_low4h_config()")
    ck("冻结参数 max_active_positions", 3, cfg.max_active_positions,
       "make_ft_low4h_config()")

    # ── 规范 vs 实现量化 ────────────────────────────────────────────────────
    q = (j01 or {}).get("quantification", {})
    ck("窗口内 4h 根数", 290, q.get("bars_4h_in_window"), "01.quantification")
    ck("旧口径有效事件窗口（天）", 15.0, q.get("current_impl_effective_event_days"),
       "01.quantification")
    ck("规范口径有效事件窗口（天）", 41.33, q.get("spec_impl_effective_event_days"),
       "01.quantification")
    ck("被吃掉的天数", 26.33, q.get("lost_event_days"), "01.quantification")
    ck("最长事件链（天）", 24.0, q.get("worst_case_event_chain_days"),
       "01.quantification")
    ck("旧口径装不下最长事件链", False,
       q.get("current_impl_can_fit_worst_case_chain"), "01.quantification")

    # ── 闸门隔离对照（04）────────────────────────────────────────────────────
    s4 = (j04 or {}).get("summary", {})
    ck("04 旧口径箱体数", 1598, s4.get("total_boxes_current"), "04_闸门对照.json")
    ck("04 只改闸门箱体数", 3688, s4.get("total_boxes_b1"), "04_闸门对照.json")
    ck("04 规范口径箱体数", 4257, s4.get("total_boxes_spec"), "04_闸门对照.json")
    ck("04 旧口径候选数", 0, s4.get("total_candidates_current"), "04_闸门对照.json")
    ck("04 只改闸门候选数", 17, s4.get("total_candidates_b1"), "04_闸门对照.json")
    ck("04 规范口径候选数", 17, s4.get("total_candidates_spec"), "04_闸门对照.json")

    # ── 三场景（03）─────────────────────────────────────────────────────────
    d3b = ((j03b or {}).get("summary") or {}).get("n_diff_vs_S1", {})
    d3 = ((j03 or {}).get("summary") or {}).get("n_diff_vs_S1", {})
    ck("修复前 窗口移 60h 状态差异", 32, d3b.get("S2_初始化后接60h新行情"),
       "03_三场景对照_修复前.json")
    ck("修复后 窗口移 60h 状态差异", 6, d3.get("S2_初始化后接60h新行情"),
       "03_三场景对照.json")
    ck("修复后 严格同长度 60h 重建差异", 0, d3.get("S5_同长度_60h处重建"),
       "03_三场景对照.json")
    ck("修复前 不重启候选数", 0,
       ((j03b or {}).get("summary") or {}).get("total_candidates", {}).get(
           "S1_一次启动不重启"), "03_三场景对照_修复前.json")
    ck("修复前 初始化后接60h候选数", 2,
       ((j03b or {}).get("summary") or {}).get("total_candidates", {}).get(
           "S2_初始化后接60h新行情"), "03_三场景对照_修复前.json")
    _tc = (j03 or {}).get("summary", {}).get("total_candidates") or {}
    ck("修复后 S1 不重启候选数", 17, _tc.get("S1_一次启动不重启"),
       "03_三场景对照.json")
    ck("修复后 S2 接60h候选数（增量非丢失）", 20, _tc.get("S2_初始化后接60h新行情"),
       "03_三场景对照.json")

    # ── parity（05）─────────────────────────────────────────────────────────
    s5 = (j05 or {}).get("summary", {})
    ck("parity 通过数", 526, s5.get("n_parity_ok"), "05_修复后parity.json")
    ck("parity 不一致数", 0, s5.get("n_mismatch"), "05_修复后parity.json")
    ck("生产/诊断候选 ID 集合相同", True, s5.get("candidate_ids_equal"),
       "05_修复后parity.json")
    ck("修复后箱体数", 4257, s5.get("total_boxes_prod"), "05_修复后parity.json")

    # ── 生产（06）───────────────────────────────────────────────────────────
    ck("生产核验 PASS 数", 17, (j06 or {}).get("n_pass"), "06_生产核验_对比.json")
    ck("生产核验 全通过", True, (j06 or {}).get("all_pass"), "06_生产核验_对比.json")
    se = ((j06p or {}).get("api") or {}).get("signal_engine") or {}
    ck("生产 history BOX", 4262, (se.get("funnel") or {}).get("history", {}).get("BOX"),
       "06_生产核验_post.json")
    ck("生产 history CANDIDATE", 18,
       (se.get("funnel") or {}).get("history", {}).get("CANDIDATE"),
       "06_生产核验_post.json")
    ck("历史候选丢弃数", 18, (se.get("funnel") or {}).get("history_candidates_discarded"),
       "06_生产核验_post.json")
    ck("生产建档口径", "atr_warmup", (se.get("atr_caliber") or {}).get("box_gate_mode"),
       "06_生产核验_post.json")
    ck("生产闸门判据", "atr_bars_seen", (se.get("atr_caliber") or {}).get("gate_metric"),
       "06_生产核验_post.json")
    ck("生产 since_start 候选", 0,
       (se.get("funnel") or {}).get("since_start", {}).get("CANDIDATE"),
       "06_生产核验_post.json")

    # ── 双向案例（07）───────────────────────────────────────────────────────
    ck("应触发候选数", 17, (j07a or {}).get("n_candidates"), "07_应触发复算.json")
    ck("六项条件全通过数", 17, (j07a or {}).get("n_all_checks_pass"),
       "07_应触发复算.json")
    ck("负例数", 28, (j07b or {}).get("n_symbols_unmet"), "07_不应触发核对.json")
    ck("负例违规数", 0, (j07b or {}).get("n_violations"), "07_不应触发核对.json")
    ck("RETEST 距突破中位数", 16.1582,
       ((j07 or {}).get("retest_gap_to_breakout_pct") or {}).get("median"),
       "07_当前卡点汇总.json")
    ck("当前 BOX 数", 320, ((j07 or {}).get("state_distribution") or {}).get("BOX"),
       "07_当前卡点汇总.json")
    ck("当前 RETEST 数", 34, ((j07 or {}).get("state_distribution") or {}).get("RETEST"),
       "07_当前卡点汇总.json")

    # ── 生产 vs 离线同截止阶段分布（06 post vs 07）──────────────────────────
    _se6 = (j06p or {}).get("api", {}).get("signal_engine") or {}
    ck("生产阶段计数 == 离线同截止", (j07 or {}).get("state_distribution"),
       _se6.get("state_counter"), "06_生产核验_post.json vs 07_当前卡点汇总.json")
    ck("生产自报新信号数", 0, _se6.get("signals_generated_total"),
       "06_生产核验_post.json")
    _bb = (j07 or {}).get("blocked_by_distribution") or {}
    ck("卡点-冷却中币数", 139, _bb.get("冷却"), "07_当前卡点汇总.json")
    ck("卡点-无箱体币数", 28, _bb.get("无箱体"), "07_当前卡点汇总.json")
    ck("卡点-等扫低币数", 320, _bb.get("等扫低(7天内)"), "07_当前卡点汇总.json")
    ck("卡点-等突破币数", 34, _bb.get("等突破(H+0.1A)"), "07_当前卡点汇总.json")
    ck("卡点-等缩量回踩币数", 5, _bb.get("等缩量回踩(72h内)"), "07_当前卡点汇总.json")
    _gap = (j07 or {}).get("retest_gap_to_breakout_pct") or {}
    ck("RETEST 距突破最小百分比", 6.9196, _gap.get("min"), "07_当前卡点汇总.json")
    ck("RETEST 距突破最大百分比", 32.9729, _gap.get("max"), "07_当前卡点汇总.json")

    # ── 逐币归因可回溯（02）─────────────────────────────────────────────────
    import csv
    p = OUT / "02_逐币归因.csv"
    n_rows = 0
    if p.exists():
        with open(p, encoding="utf-8-sig") as f:
            n_rows = sum(1 for _ in csv.DictReader(f))
    ck("逐币归因行数", 526, n_rows, "02_逐币归因.csv")
    ck("逐币归因箱体合计", 4257, (j02 or {}).get("total_boxes_formed"), "02_汇总.json")
    ck("逐币归因候选合计", 17, (j02 or {}).get("total_candidates"), "02_汇总.json")

    # ── 修复前/后 对照数字（主报告 Q2/Q3 两张表）────────────────────────────
    ck("修复前 被预热门槛拦掉的根次", 61962,
       ((j02b or {}).get("box_attempt_totals") or {}).get("条件全满足·被预热门槛拦掉"),
       "02_汇总_修复前.json")
    ck("修复前 阶段分布", {"IDLE": 161, "BOX": 323, "RETEST": 35, "SWEEP": 7},
       (j02b or {}).get("state_distribution"), "02_汇总_修复前.json")
    ck("修复后 阶段分布（离线）", {"IDLE": 169, "BOX": 318, "RETEST": 34, "SWEEP": 5},
       (j02 or {}).get("state_distribution"), "02_汇总.json")
    _fh_pre = ((j06r or {}).get("api", {}).get("signal_engine") or {}).get("funnel") or {}
    _fh_post = ((j06p or {}).get("api", {}).get("signal_engine") or {}).get("funnel") or {}
    for _k, _pre, _post in (("BOX", 1598, 4262), ("CANDIDATE", 0, 18),
                            ("SWEEP", 489, 1085), ("RETEST", 278, 649),
                            ("INVALIDATED", 1044, 2325), ("EXPIRED", 184, 1536)):
        ck(f"修复前 回放期 {_k}", _pre, (_fh_pre.get("history") or {}).get(_k),
           "06_生产核验_pre.json")
        ck(f"修复后 回放期 {_k}", _post, (_fh_post.get("history") or {}).get(_k),
           "06_生产核验_post.json")

    # ── 汇总可回溯到逐币记录（11）───────────────────────────────────────────
    j11 = rj("11_回溯抽样.json")
    ck("回溯抽样样本数", 8, (j11 or {}).get("n_picked"), "11_回溯抽样.json")
    ck("回溯比对项数", 44, (j11 or {}).get("n_comparisons"), "11_回溯抽样.json")
    ck("回溯未比到项数", 0, (j11 or {}).get("n_missing"), "11_回溯抽样.json")
    ck("回溯不符项数", 0, (j11 or {}).get("n_failed"), "11_回溯抽样.json")
    ck("回溯全通过（非空转）", True, (j11 or {}).get("all_match"),
       "11_回溯抽样.json")
    ck("回溯截止锚定 02 的截止时刻", (j02 or {}).get("upto_utc"),
       (j11 or {}).get("upto_utc"), "11_回溯抽样.json vs 02_汇总.json")

    # ── 策略测试（13）────────────────────────────────────────────────────────
    import re as _re
    _t13 = OUT / "13_测试_策略.txt"
    _txt = _t13.read_text(encoding="utf-8") if _t13.exists() else ""
    _m = _re.search(r"(\d+) passed", _txt)
    ck("策略测试通过数", 115, int(_m.group(1)) if _m else None, "13_测试_策略.txt")

    # ── 开闸时刻实测（14）────────────────────────────────────────────────────
    j14 = rj("14_开闸时刻实测.json")
    ck("旧口径最后被拦 4h", "2026-10-09T23:59:59Z", (j14 or {}).get("last_blocked_utc"),
       "14_开闸时刻实测.json")
    ck("旧口径首次建出箱体（=开闸时刻）", "2026-10-10T03:59:59Z",
       (j14 or {}).get("gate_open_utc"), "14_开闸时刻实测.json")
    _sp = (j14 or {}).get("spec_mode") or []
    ck("规范口径窗口第一根即可建档", {"2026-09-06T23:59:59Z"},
       {x.get("first_formable_utc") for x in _sp} or None, "14_开闸时刻实测.json")
    ck("规范候选早于开闸的个数", 14, (j14 or {}).get("candidates_before_gate"),
       "14_开闸时刻实测.json")
    ck("规范候选晚于开闸的个数", 3, (j14 or {}).get("candidates_after_gate"),
       "14_开闸时刻实测.json")
    ck("晚于开闸的候选名单", ["BBUSDT", "METISUSDT", "WALUSDT"],
       sorted(x["symbol"] for x in ((j14 or {}).get("candidates_after_gate_list") or [])),
       "14_开闸时刻实测.json")

    # ── 跨新增小时线（12 + 06_新增K线观察）──────────────────────────────────
    j12 = rj("12_新增K线效果.json")
    _a = (j12 or {}).get("A_actually_processed") or {}
    _b = (j12 or {}).get("B_stage_changes") or {}
    _c = (j12 or {}).get("C_candidate_result") or {}
    _d = (j12 or {}).get("D_ledger_and_real_trading") or {}
    ck("新增小时线 水位前进", True, _a.get("watermark_advanced"), "12_新增K线效果.json")
    ck("新增小时线 逐币滞后", 0, _a.get("lag_1h_bars"), "12_新增K线效果.json")
    ck("新增小时线 lag>1 的币数", 0, _a.get("n_lag_1h_gt1"), "12_新增K线效果.json")
    ck("新增小时线 逐币阶段变化数", 0, _b.get("n_state_changed"), "12_新增K线效果.json")
    ck("新增小时线 空转自证（水位不同的币数）", 526, _b.get("n_last_processed_1h_differs"),
       "12_新增K线效果.json")
    ck("新增小时线 生产新候选", 0, _c.get("prod_new_candidates"), "12_新增K线效果.json")
    ck("新增小时线 账本未变", True, _d.get("ledger_unchanged"), "12_新增K线效果.json")
    ck("新增小时线 真实交易仍关闭", True, (j12 or {}).get("verdict", {}).get(
        "real_trading_still_off"), "12_新增K线效果.json")
    j02n = rj("02_汇总_upto1792904399999.json")
    ck("新截止离线箱体数", 4262, (j02n or {}).get("total_boxes_formed"),
       "02_汇总_upto1792904399999.json")
    ck("新截止离线候选数", 18, (j02n or {}).get("total_candidates"),
       "02_汇总_upto1792904399999.json")

    # ── UI（08）─────────────────────────────────────────────────────────────
    blocks = (j08 or {}).get("blocks") or []
    ck("UI 四块全可见", True,
       all(b.get("visible") for b in blocks) and len(blocks) == 4, "08_用户入口.json")
    ck("UI 数字 parity 全通过", True,
       all((b.get("parity") or {}).get("pass") for b in blocks), "08_用户入口.json")
    ck("UI console 错误数", 0, len((j08 or {}).get("console_errors") or []),
       "08_用户入口.json")

    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "n_checks": len(rows),
        "n_pass": sum(1 for r in rows if r["pass"]),
        "all_pass": all(r["pass"] for r in rows),
        "rows": rows,
        "note": ("只读产物复算，不重跑回放；每条都写明 expected / actual / 来源文件。"
                 "任一 FAIL 都说明主报告的数字与产物不一致，必须改报告或改脚本。"),
    }
    cm.dump("10_报告数字自检.json", res)
    for r in rows:
        flag = "PASS" if r["pass"] else "FAIL"
        if not r["pass"]:
            print(f"  {flag}  {r['claim']}: 期望 {r['expected']} 实际 {r['actual']}"
                  f" ({r['source']})")
    print(f"[selfcheck] {res['n_pass']}/{res['n_checks']} pass, all_pass={res['all_pass']}")
    return 0 if res["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
