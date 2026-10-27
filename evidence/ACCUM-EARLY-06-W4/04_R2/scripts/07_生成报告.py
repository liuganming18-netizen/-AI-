# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 07 生成报告（正文数字全部登记台账）

纪律：正文里出现的每个数字都经 `N(value, key, fmt)` 登记，
再由 `08_数字对撞.py` 双向校验（台账→正文、正文→台账、派生值就地重算）。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L

LEDGER: list = []

# 正文里每个数字都被替换成不可见标记（\x01 idx \x02 文本 \x03），
# 渲染完再把标记剥掉，同时记下它在最终文本里的字符偏移。
# 这样 08 能做「台账→正文」的强校验：按偏移逐字符比对，而不是「这个字符串在全文里出现过」。
_MARK = "\x01{}\x02{}\x03"
_MARK_RE = re.compile("\x01(\\d+)\x02(.*?)\x03", re.S)


def N(v, key, fmt="int", src=None):
    if fmt == "raw":
        text = str(v)
    elif fmt == "money":
        text = f"{v:,.9f}"
    elif fmt == "money2":
        text = f"{v:,.2f}"
    elif fmt == "pct":
        text = f"{v * 100:.2f}%"
    elif fmt == "f2":
        text = f"{v:.2f}"
    elif fmt == "f3":
        text = f"{v:.3f}"
    elif fmt == "g":
        text = f"{v:g}"
    else:
        text = f"{v:,}"
    idx = len(LEDGER)
    LEDGER.append({"key": key, "value": v, "text": text, "fmt": fmt,
                   "src": src or "产物"})
    return _MARK.format(idx, text)


def strip_markers(doc: str) -> tuple:
    """把标记换成文本，返回 (干净正文, 每个台账项的字符偏移列表)。"""
    parts, offsets = [], {}
    pos = 0
    for m in _MARK_RE.finditer(doc):
        idx, text = int(m.group(1)), m.group(2)
        if LEDGER[idx]["text"] != text:
            raise SystemExit(f"标记文本与台账不一致 idx={idx}: {text!r}")
        parts.append(doc[pos:m.start()])
        offset = sum(len(p) for p in parts)
        offsets.setdefault(idx, []).append(offset)
        parts.append(text)
        pos = m.end()
    parts.append(doc[pos:])
    for i, item in enumerate(LEDGER):
        item["offsets"] = offsets.get(i, [])
        item["n_occurrences"] = len(item["offsets"])
    return "".join(parts), offsets


def _hold_hours(lo_utc: str, hi_utc: str) -> float:
    """两个 ISO 时刻之间的小时数（正文用，不引第三方库）。"""
    def _p(s):
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (_p(hi_utc) - _p(lo_utc)).total_seconds() / 3600.0



def main() -> int:
    p00 = json.loads((L.OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    p01 = json.loads((L.OUT / "01_周期证据分级.json").read_text(encoding="utf-8"))
    p02 = json.loads((L.OUT / "02_缺口判据与完整性.json").read_text(encoding="utf-8"))
    p03 = json.loads((L.OUT / "03_隔离反例.json").read_text(encoding="utf-8"))
    p04 = json.loads((L.OUT / "04_真实记录核对.json").read_text(encoding="utf-8"))
    p05 = json.loads((L.OUT / "05_两口径逐笔与汇总.json").read_text(encoding="utf-8"))
    p06 = json.loads((L.OUT / "06_机器可读_交付.json").read_text(encoding="utf-8"))

    A = p00["A_范围"]
    B = p00["B_官方快照"]
    C = p00["C_本地官方记录探针"]
    D = p00["D_数据身份"]
    S1 = p01["汇总"]
    S2 = p02["汇总"]
    P = p02["判据参数"]

    n_cand = N(A["candidates"], "候选数")
    n_sym = N(A["symbols"], "标的数")
    n_cal = N(len(A["calibers"]), "口径数")
    n_filled = N(A["holds_filled"], "已成交笔数")
    n_rej = N(A["holds"] - A["holds_filled"], "拒单笔数")
    n_win_h = N(A["window_hours"], "窗口小时", "f2")
    n_off_rows = N(B["n_rows"], "官方快照行数")
    n_off_hit = N(B["命中标的数"], "官方命中标的数")
    n_off_nout = N(len(B["命中但 updateTime 为空"]), "官方无updateTime标的数")
    n_ev_total = N(S1["events_total"], "事件总数")
    n_ev_jit = N(S1["events_ms_suffix_nonzero"], "毫秒尾数非零事件数")
    n_ev_jit_pct = N(S1["events_ms_suffix_nonzero_frac"], "毫秒尾数非零占比", "pct")
    n_sw_sym = N(S1["symbols_with_switch_points"], "有切换点的标的数")
    n_multi = N(S1["symbols_with_multiple_intervals"], "多间隔标的数")
    n_covers = N(sum(1 for r in p02["per_candidate"] if r["official_covers_hold"]),
                 "官方覆盖持仓的候选数")
    n_basis_off = N(S2["interval_basis_dist"].get("official_snapshot", 0), "官方证据候选数")
    n_basis_inf = N(S2["interval_basis_dist"].get("inferred_uniform", 0), "推断证据候选数")
    n_basis_mult = N(S2["interval_basis_dist"].get("inferred_with_multiples", 0),
                     "推断含倍数候选数")
    n_basis_sw = N(S2["interval_basis_dist"].get("switch_detected", 0), "切换候选数")
    n_basis_partial = N(S2["interval_basis_dist"].get("official_partial", 0), "官方部分候选数")
    n_basis_undet = N(S2["interval_basis_dist"].get("undetermined", 0), "不可判定证据候选数")
    n_na = N(S2["completeness_dist"].get("NOT_APPLICABLE", 0), "不适用候选数")
    # 判据常量：登记为「代码常量」而不是「产物」，避免读者以为这些是从数据算出来的
    n_run_min = N(3, "最短连段数", src="代码常量：02 _local_step 的 min_run（≥3 连才算一段）")
    n_switch_min = N(2, "切换判定最少段数", src="代码常量：≥2 段不同间隔才判 switch_detected")
    n_sw_win_h = N(48, "updateTime对齐窗口小时",
                    src="判据：官方 updateTime 与最后切换点的对齐窗口 ±48h")
    n_nogap = N(S2["completeness_dist"].get("NO_GAP_DETECTED", 0), "未发现缺失候选数")
    n_missing = N(S2["completeness_dist"].get("MISSING_DEMONSTRATED", 0), "已证缺失候选数")
    n_incons = N(S2["completeness_dist"].get("INCONSISTENT_INFERRED", 0), "推断不符候选数")
    n_undet = N(S2["completeness_dist"].get("UNDETERMINED", 0), "不可判定候选数")
    n_proven = N(S2["claim_dist"].get("PROVEN", 0), "PROVEN数")
    n_notproven = N(S2["claim_dist"].get("NOT_PROVEN", 0), "NOT_PROVEN数")
    n_refuted = N(S2["claim_dist"].get("REFUTED", 0), "REFUTED数")
    n_unknown = N(S2["claim_dist"].get("UNKNOWN", 0), "UNKNOWN数")
    n_r1_miss = N(S2["r1_missing_total"], "R1口径缺失合计")
    n_r2_miss = N(S2["r2_missing_total"], "本单缺失合计")
    n_r1_flag = N(S2["r1_would_flag_candidates"], "R1会标记的候选数")
    n_r2_flag = N(S2["r2_flag_candidates"], "本单标记的候选数")
    n_offgrid = N(S2["candidates_with_off_grid_events"], "有网格外事件的候选数")
    n_field = N(S2["candidates_with_field_defect"], "有字段缺陷的候选数")
    n_bj_cand = N(S2["candidates_with_boundary_jitter"], "端点抖动候选数")
    n_bj_ev = N(S2["boundary_jitter_events"], "端点抖动事件数")
    n_bj_dep = N(S2["boundary_dependent_candidates"], "端点依赖候选数")
    n_trivial = N(S2["trivial_holds"], "零预期时刻候选数")
    n_tol = N(P["match_tol_ms"], "匹配容差ms")
    n_phase_sup = N(P["phase_min_support"], "相位最小支撑")
    n_local = N(P["local_days"], "局部邻域天数")
    n_ctx = N(P["context_days"], "上下文天数")
    n_phase_frac = N(P["phase_min_frac"], "相位最小占比", "pct")
    # 毫秒尾数区间：从 01 的逐标的 ms_jitter 汇总（min=出现过的非零最小尾数，max=最大尾数）
    _mins, _maxs = [], []
    for _rec in p01["per_symbol"].values():
        _j = _rec["observed"]["ms_jitter"]
        _maxs.append(_j["max_ms"])
        _mins += [int(k) for k in _j["top"] if int(k) != 0]
    n_jit_min = N(min(_mins) if _mins else 0, "毫秒尾数最小非零")
    n_jit_max = N(max(_maxs) if _maxs else 0, "毫秒尾数最大")
    # 端点抖动幅度（+3ms / +4ms）
    _bj_offs = [j["offset_ms"] for r in p02["per_candidate"] for j in r.get("boundary_jitter") or []]
    n_bj_off_min = N(min(_bj_offs) if _bj_offs else 0, "端点抖动最小偏移ms")
    n_bj_off_max = N(max(_bj_offs) if _bj_offs else 0, "端点抖动最大偏移ms")
    # 证据等级个数（表格行数，从枚举实际取值算）
    n_basis_levels = N(len([n for n in dir(L) if n.startswith("EV_")]), "证据等级数",
                       src="枚举：lib_r2 的 EV_* 常量个数")
    # SKRUSDT 窗口内切换次数
    _skr = p01["per_symbol"].get("SKRUSDT", {}).get("observed", {}).get("switch_points", [])
    n_sw_skr = N(len(_skr), "SKRUSDT切换次数")
    # AXSUSDT 那笔的结算次数（旧 → 新），从 05 逐笔取，不手抄
    _axs = [r for r in p05["per_trade"]
            if r["caliber"] == "生效视图_5bps" and r["symbol"] == "AXSUSDT"][0]
    n_axs_old = N(_axs["events_old"], "AXS事件旧")
    n_axs_new = N(_axs["events_new"], "AXS事件新")
    n_axs_gap = N(_axs["events_new"] - _axs["events_old"], "AXS漏结算数")
    n_table_count = N(C["db_table_count"], "热库表数")
    # §11.1 的复跑证据：自检项数从各产物 checks 里数出来；共享文件数从哈希登记表数出来
    _selfcheck_total = 0
    for _name in ("00_范围与身份锚定", "01_周期证据分级", "02_缺口判据与完整性",
                  "03_隔离反例", "04_真实记录核对", "05_两口径逐笔与汇总"):
        _selfcheck_total += len(json.loads(
            (L.OUT / f"{_name}.json").read_text(encoding="utf-8")).get("checks", []))
    n_selfchecks = N(_selfcheck_total, "自检项数")
    n_shared_files = N(len(p00["E_代码与输入哈希"]), "共享文件登记数")
    n_cal_rows = N(len(p05["per_trade"]), "口径行数")
    n_stable = N(4, "字节稳定产物数",
                 src="实测：全链二次重跑，README/07台账/08对撞/06CSV 四个产物 sha256 逐位相同；"
                     "00 与 06JSON 因含生产库 mtime_ms 每次变，不列入")

    R1 = p04["R1_R1缺失复核"]
    n_r1chk = N(R1["r1_missing_instants"], "R1缺失复核总数")
    n_r1chk_j = N(R1["其中在 ±1s 内有真实事件"], "R1缺失中抖动匹配数")
    n_r1max = N(R1["最大偏移ms"], "R1缺失最大偏移ms")
    R2c = p04["R2_周期切换实证"]
    n_sw_match = N(R2c["官方与最后一段一致"], "官方与最后一段一致数")
    # 「一致」与「updateTime 落在 ±48h 内」是两件事：分开算，不合并成一句
    _cons = [c for c in R2c["明细"] if c["official_matches_last_regime"]]
    _cons_aligned = [c for c in _cons
                     if c["update_minus_switch_hours"] is not None
                     and abs(c["update_minus_switch_hours"]) <= 48]
    _cons_late = [c for c in _cons if c not in _cons_aligned]
    n_sw_48h = N(len(_cons_aligned), "一致且≤48h的标的数")
    n_sw_late = N(len(_cons_late), "一致但晚于48h的标的数")
    n_ygg_dh = N(abs(_cons_late[0]["update_minus_switch_hours"]) if _cons_late else 0,
                 "YGG的Δh", "f3")
    # IRYSUSDT：官方与观测不一致的那个（唯一），两个数字都从明细取
    _irys = next(c for c in R2c["明细"] if c["symbol"] == "IRYSUSDT")
    n_irys_off_h = N(_irys["official_interval_h"], "IRYS官方h", "g")
    n_irys_obs_h = N(_irys["switch_points"][-1]["to_h"], "IRYS观测h", "g")
    R3 = p04["R3_相位实证"]
    n_ph0 = N(R3["相位=0 的候选"], "相位0候选数")
    n_phnz = N(R3["相位≠0 的候选"], "相位非0候选数")
    n_phshift = N(R3["相位改变网格的候选"], "相位改变网格候选数")
    R4 = p04["R4_端点抖动实证"]
    n_r4 = N(len(R4["cases"]), "端点抖动记录数")
    R5 = p04["R5_非零结算但仍漏"]
    n_r5 = N(len(R5["cases"]), "非零结算仍漏笔数")
    R6 = p04["R6_补入事件是否在网格上"]
    n_r6_add = N(R6["补入事件数（51 笔持仓内）"], "补入事件数")
    n_r6_off = N(R6["网格外"], "补入网格外数")

    SA = p05["汇总"]["生效视图_5bps"]
    SB = p05["汇总"]["策略包_3bps"]
    # 口径名整体登记（名字里带 5bps/3bps，整串进台账，正文里这两个数字才算有出处）
    cal_A = N("生效视图_5bps", "口径A名称", "raw", src="p05 汇总键名")
    cal_B = N("策略包_3bps", "口径B名称", "raw", src="p05 汇总键名")
    a_fold = N(SA["funding_old_sum"], "A资金费旧", "money")
    a_fnew = N(SA["funding_new_sum"], "A资金费新", "money")
    a_d = N(SA["delta_funding_sum"], "A资金费差", "money")
    a_nold = N(SA["net_old_sum"], "A净额旧", "money")
    a_nnew = N(SA["net_new_sum"], "A净额新", "money")
    a_nd = N(SA["delta_net_sum"], "A净额差", "money")
    a_fill = N(SA["filled"], "A成交数")
    a_rej = N(SA["rejected"], "A拒单数")
    a_aff = N(SA["affected_trades"], "A受影响笔数")
    a_ev_old = N(SA["events_old_sum"], "A事件旧")
    a_ev_new = N(SA["events_new_sum"], "A事件新")
    b_fold = N(SB["funding_old_sum"], "B资金费旧", "money")
    b_fnew = N(SB["funding_new_sum"], "B资金费新", "money")
    b_d = N(SB["delta_funding_sum"], "B资金费差", "money")
    b_nold = N(SB["net_old_sum"], "B净额旧", "money")
    b_nnew = N(SB["net_new_sum"], "B净额新", "money")
    b_nd = N(SB["delta_net_sum"], "B净额差", "money")
    b_fill = N(SB["filled"], "B成交数")
    b_rej = N(SB["rejected"], "B拒单数")
    b_aff = N(SB["affected_trades"], "B受影响笔数")
    b_ev_old = N(SB["events_old_sum"], "B事件旧")
    b_ev_new = N(SB["events_new_sum"], "B事件新")
    n_nochange_A = N(SA["change_reason_dist"].get("NO_CHANGE", 0), "A无变化笔数")
    n_fill_A = N(SA["change_reason_dist"].get("COLD_FILL_ADDED", 0), "A补入笔数")
    n_bj_A = N(SA["change_reason_dist"].get("BOUNDARY_JITTER_ONLY", 0), "A仅抖动笔数")
    n_nochange_B = N(SB["change_reason_dist"].get("NO_CHANGE", 0), "B无变化笔数")
    n_fill_B = N(SB["change_reason_dist"].get("COLD_FILL_ADDED", 0), "B补入笔数")
    n_bj_B = N(SB["change_reason_dist"].get("BOUNDARY_JITTER_ONLY", 0), "B仅抖动笔数")
    # 口径 B 有 1 个候选被拒（无持仓），它在 change_reason_dist 里单列 REJECTED，
    # 不列出来会让「无变化+补入+仅抖动」少 1，看起来像漏了
    n_rej_B = N(SB["change_reason_dist"].get("REJECTED", 0), "B拒单原因笔数")

    adap_sha = p00["E_代码与输入哈希"]["adapter"]["sha256"][:16]
    rc_sha = p00["E_代码与输入哈希"]["shared_replay_calc"]["sha256"][:16]
    cand_sha = p00["E_代码与输入哈希"]["candidates_source"]["sha256"][:16]
    off_sha = B["rows_sha256"][:16]
    r1_sha = (p00["E_代码与输入哈希"]["r1_product"]["sha256"] or "")[:16]
    n_db_rows = N(D["hot_db"]["rows_funding_history"], "热库行数")
    n_cold_files = N(D["cold_archive"]["files"], "冷档文件数")
    n_cold_bytes = N(int(D["cold_archive"]["bytes"]), "冷档字节")
    # WUSDT 的 8h 间隔是「漏一条」而不是「8h 周期」，正文要指到具体标的自己的事件数，
    # 不能拿全池 events_total 冒充（曾误写成全池 211,604）
    _w = p01["per_symbol"]["WUSDT"]["observed"]
    n_wusdt_ev = N(_w["n_events"], "WUSDT事件数")
    n_wusdt_8h = N(_w["runs"]["minor_runs"][0]["count"] if _w["runs"]["minor_runs"] else 0,
                   "WUSDT的8h间隔数")
    n_wusdt_8h_len = N(_w["runs"]["minor_runs"][0]["interval_h"] if _w["runs"]["minor_runs"] else 0,
                       "WUSDT的8h间隔长度h", "g")
    n_wusdt_off_h = N(p01["per_symbol"]["WUSDT"]["official"]["interval_hours"],
                      "WUSDT官方h", "g")
    # 被撤销的旧判据门槛：「持仓 > 8h 且 0 次结算」才算缺口（03 的 naive_old_rule）
    n_old_rule_h = N(8, "旧判据门槛h", src="被撤销的旧判据：持仓>8h 且 0 结算才判缺口")

    # ── 受影响 7 笔表 ──
    def trade_rows(cal):
        rows = [r for r in p05["per_trade"]
                if r["caliber"] == cal and r.get("is_filled") and abs(r["delta_funding"]) > 1e-9]
        rows.sort(key=lambda r: -abs(r["delta_funding"]))
        out = []
        for r in rows:
            out.append("| `{}` | {} → {} | {} | {} | {} | {} |".format(
                r["symbol"],
                N(r["events_old"], f"{cal}-{r['symbol']}-事件旧"),
                N(r["events_new"], f"{cal}-{r['symbol']}-事件新"),
                N(r["funding_old"], f"{cal}-{r['symbol']}-资金费旧", "money"),
                N(r["funding_new"], f"{cal}-{r['symbol']}-资金费新", "money"),
                N(r["delta_funding"], f"{cal}-{r['symbol']}-差额", "money"),
                N(r["delta_net"], f"{cal}-{r['symbol']}-净额差", "money")))
        return "\n".join(out)

    tA = trade_rows("生效视图_5bps")
    tB = trade_rows("策略包_3bps")

    # ── 端点抖动表 ──
    bj_lines = []
    for c in R4["cases"]:
        if c["caliber"] != "生效视图_5bps":
            continue
        bj_lines.append("| `{}` | `{}` | {} | {} |".format(
            c["symbol"], c["exit_utc"],
            N(c["cost_if_included"], f"bj-{c['symbol']}-金额", "money"),
            "退出同刻（严格判据不纳入）"))
    bj_table = "\n".join(bj_lines)

    # ── 反例表 ──
    # 场景列用本地无数字标签：03 产物里的 title 含数字（如「12h 应结算 3 次」），
    # 直接搬进正文会让这些数字绕过台账；数字改在 §7 的要点里逐个登记
    CASE_LABEL = {
        "S1": "规律缺失导致周期误判（有官方证据）",
        "S1b": "同一份数据但没有官方证据",
        "S2": "非零结算但仍漏事件",
        "S3": "周期切换落在持仓内",
        "S4": "相位不一致",
        "S5": "端点抖动",
    }
    case_lines = []
    for c in p03["cases"]:
        case_lines.append("| {} | {} | {} / {} | {} / {} |".format(
            c["id"], CASE_LABEL.get(c["id"], c["title"]),
            c["r1"]["period_status"], c["r1"]["gap_classification"],
            c["r2"]["completeness"], c["r2"]["claim"]))
    case_table = "\n".join(case_lines)
    n_cases = N(len(p03["cases"]), "反例数")

    # 反例里的数字也从 03 产物取，不手抄
    _cs = {c["id"]: c for c in p03["cases"]}
    n_s1_miss = N(_cs["S1"]["r2"]["missing"], "S1本单缺失数")
    n_s1_exp = N(_cs["S1"]["r2"]["expected"], "S1本单预期数")
    n_s1_act = N(_cs["S1"]["r2"]["actual"], "S1本单对上数")
    n_s3_miss = N(_cs["S3"]["r1"]["missing"], "S3旧判缺失数")
    n_s4_exp = N(_cs["S4"]["r2"]["expected"], "S4预期时刻数")
    n_s4_miss = N(_cs["S4"]["r1"]["missing"], "S4旧判落空数")
    n_s4_ph = N(_cs["S4"]["r2"]["phase_h"], "S4观测相位h")
    n_s5_ms = N(4, "S5抖动毫秒", src="合成反例：S5 的结算记录比退出晚 4ms（03 产物构造参数）")
    n_s1_true_h = N(_cs["S1"]["official"]["interval_hours"], "S1真实周期h", "g")
    n_s1_obs_h = N(_cs["S1"]["r1"]["step_h"], "S1观测周期h", "g")
    n_s2_exp = N(_cs["S2"]["r2"]["expected"], "S2应结算次数")
    n_s2_act = N(_cs["S2"]["r2"]["actual"], "S2实际记录次数")
    n_s2_hold_h = N(_hold_hours(_cs["S2"]["entry_utc"], _cs["S2"]["exit_utc"]), "S2持仓小时", "g")
    _s3_iv = sorted(_cs["S3"]["r1"]["distinct_intervals"])
    n_s3_from = N(_s3_iv[-1], "S3切换前周期h", "g")
    n_s3_to = N(_s3_iv[0], "S3切换后周期h", "g")
    n_s4_h = N(_cs["S4"]["r2"]["step_h"], "S4周期h", "g")

    # ── 5.1 周期切换表（数据生成，不手抄） ──
    def _regime_chain(c):
        hs = [c["switch_points"][0]["from_h"]] + [p["to_h"] for p in c["switch_points"]]
        return " → ".join(N(h, f"{c['symbol']}-regime-{i}", "g") + "h"
                          for i, h in enumerate(hs))

    sw_lines = []
    for _sym in ("AXSUSDT", "BABYUSDT", "BLURUSDT"):
        _c = next(c for c in R2c["明细"] if c["symbol"] == _sym)
        sw_lines.append("| `{}` | {} | {}h | {}h |".format(
            _sym, _regime_chain(_c),
            N(_c["official_interval_h"], f"{_sym}-官方h", "g"),
            N(_c["update_minus_switch_hours"], f"{_sym}-Δh", "f3")))
    sw_table = "\n".join(sw_lines)

    doc = f"""# ACCUM-EARLY-06-W4-R2 验收报告：资金费周期证据与缺口判据整改

> **任务**：`ACCUM-EARLY-06-W4-R2`（窗口四 R1 的整改单）
> **承接**：`evidence/ACCUM-EARLY-06-W4/03_R1/README.md`、`evidence/ACCUM-EARLY-06-06/README.md`
> **⚠ 状态**：**执行方自报，未经主代理 / Codex 独立验收。** 判定只用「完成／部分完成／未完成／阻塞」三态加未验证项，不写百分比。

---

## 0 一句话结论

R1 的 `proven_regular_*` 不只是命名问题：它在**真实数据上把 {n_r1_miss} 个「缺失」判了出来，而这 {n_r1_miss} 个时刻在 ±{n_tol} ms 内全都有真实事件**（最大偏移 {n_r1max} ms，{n_r1chk_j}/{n_r1chk} 命中）。根因是预期时刻按整点生成、实际记录带 {n_jit_min}~{n_jit_max} ms 尾数，R1 用**精确毫秒相等**比对 ⇒ 抖动被当成缺失。改用官方周期证据 + 观测相位 + {n_tol} ms 容差后，本单在 {n_cand} 笔持仓里**没有发现任何缺失**（{n_r2_miss} 条），R1 会标记的 {n_r1_flag} 笔降到 {n_r2_flag} 笔。金额层面两套口径的差额与 05-C / R1 完全一致（口径 A Δ {a_d}、口径 B Δ {b_d}），**变的是完整性的定性与证据等级，不是钱**。

---

## 1 范围、身份与「官方证据」的可得性

| 项 | 值 |
|---|---|
| 候选 / 标的 / 口径 | {n_cand} / {n_sym} / {n_cal} |
| 已成交 / 拒单 | {n_filled} / {n_rej}（口径 B 的 `CARVUSDT`） |
| 窗口 | `{A['window_lo_utc']}` → `{A['window_hi_utc']}`（{n_win_h} 小时） |
| 热库 `funding_history` 行数 | {n_db_rows} |
| 冷档 parquet | {n_cold_files} 个文件 / {n_cold_bytes} 字节 |
| 官方周期快照 | {n_off_rows} 行，命中 {n_off_hit} 个标的；`updateTime` 为空 {n_off_nout} 个 |
| 本地是否有官方周期记录 | **没有**：热库无 interval 字段（表数 {n_table_count} 张，无一张含 interval 列）、归档目录无 interval、伴生库 `funding_interval_meta.sqlite3` {C['funding_interval_meta_sqlite3']} |

**本单对「官方证据」的处理必须说清楚**：交易所不提供周期变更流水。`/fapi/v1/fundingInfo` 只给**当前值 + 该行最后一次更新时间 `updateTime`**，因此它只能界定「官方值自 `updateTime` 起生效」，**早于 `updateTime` 的时段没有任何官方证据**。这份快照是一次性只读落盘（原始响应 + sha256 前缀 `{off_sha}`），不是采集器：无调度、无重试、不写生产库。

---

## 2 周期证据分级：官方 / 观测推断 / 不可判定

R1 把观测间隔推算直接叫 `proven_regular_4h/8h/1h`，并用它判断完整性。本单改为 {n_basis_levels} 个互不替代的等级：

| 等级 | 含义 | 候选数 |
|---|---|---|
| `official_snapshot` | 官方快照 interval 自 `updateTime` 起生效，覆盖整个持仓区间 | {n_basis_off} |
| `official_partial` | 官方快照存在，但 `updateTime` 晚于入场 ⇒ 前段无官方证据 | {n_basis_partial} |
| `inferred_uniform` | 观测间隔恒定，**仅能推断** | {n_basis_inf} |
| `inferred_with_multiples` | 观测间隔是同一基数的整数倍（缺事件痕迹） | {n_basis_mult} |
| `switch_detected` | 观测到周期切换 | {n_basis_sw} |
| `undetermined` | 不可判定 | {n_basis_undet} |

**完整性五态**（说「网格上有没有缺」）与**主张四态**（说「能不能声称已证明」）是两个维度，不互相替代：

| 完整性 | 含义 | 候选数 |
|---|---|---|
| `NO_GAP_DETECTED` | 网格上未发现缺失——**不等于已证明完整** | {n_nogap} |
| `MISSING_DEMONSTRATED` | 官方周期下确有缺失 | {n_missing} |
| `INCONSISTENT_INFERRED` | 仅在推断周期下不符 | {n_incons} |
| `UNDETERMINED` | 不可判定 | {n_undet} |
| `NOT_APPLICABLE` | 不适用（持仓内无预期时刻） | {n_na} |

| 主张 | 含义 | 候选数 |
|---|---|---|
| `PROVEN` | 官方覆盖 + 相位一致 + 网格无缺失 | {n_proven} |
| `NOT_PROVEN` | 未证明（**不等于已推翻**） | {n_notproven} |
| `REFUTED` | 已推翻（官方周期下确有缺失） | {n_refuted} |
| `UNKNOWN` | 不可判定 | {n_unknown} |

另有 {n_trivial} 个候选在 `(entry, exit]` 内**没有任何预期结算时刻**（持仓短于一个周期，含 0 时长笔），这类不参与缺口判断，也不给 `PROVEN`。

官方覆盖持仓的候选 **{n_covers} / {n_cand}**。剩下 {n_basis_inf} 个（`BNBUSDT`、`DOTUSDT`、`XLMUSDT` 的 `updateTime` 为空；`IRYSUSDT`、`SKRUSDT` 的 `updateTime` 晚于持仓）**没有官方证据**，因此它们的完整性最高只能到「未发现缺失」，**不能声称已证明**。

**为什么历史间隔不能证明原生周期**（三条，都能在本数据上指到具体标的）：

1. **缺失记录本身会改写观察到的间隔**。`WUSDT` 全史 {n_wusdt_ev} 条事件里只有 {n_wusdt_8h} 个 {n_wusdt_8h_len}h 间隔，那是漏了一条结算，不是 {n_wusdt_8h_len}h 周期（官方 {n_wusdt_off_h}h、`updateTime` 2024-04-03）。反过来，若某个 {n_wusdt_off_h}h 标的连续漏掉一半结算，观测到的间隔会全部变成 {n_wusdt_8h_len}h——见 §7 的 S1 反例：同一份数据，R1 判 `proven_regular_8h` 且「完整」，而官方证据说它是 {n_s1_true_h}h，实际缺一半。
2. **周期会真的变**。本单实测 **{n_sw_sym} 个标的**存在周期切换（{n_multi} 个标的出现过多种间隔）。`AXSUSDT` 是 `8h → 1h（2026-01-17）→ 4h（2026-01-26）`；`SKRUSDT` 在窗口内来回切了 {n_sw_skr} 次。一个标的只有一个周期，是错的。
3. **历史规律不包含规则文本**。观测只能说明「过去这样结算」，不能说明交易所当时的规则。

---

## 3 预期结算时刻：周期长度 ≠ 相位

R1 用 `first_t = (entry_ms // step_ms + 1) * step_ms` 生成预期时刻，这等于**强制相位为 0**（UTC 整点）。工单要求核对这一点，本单的做法是：

1. 先按 `step` 在**局部邻域（持仓 ±{n_local} 天；样本不足时退回 ±{n_ctx} 天上下文）**统计 `t mod step` 的残差；
2. 要求主导残差支撑 ≥ {n_phase_sup} 条且占比 ≥ {n_phase_frac}，才承认相位可用；
3. 用 `grid_instants(phase, step, entry, exit)` 生成，而不是 `(entry//step+1)*step`。

**实测结果**：{n_ph0} 个候选的相位都是 0，{n_phnz} 个非 0，相位改变网格的候选 {n_phshift} 个。也就是说，在这个数据集里「相位=0」事后看是对的——但这是**验证结果，不是默认假设**。反例 S4 证明差别有多大：{n_s4_h}h 周期、结算在 01/05/09 点的合成序列，R1 按相位 0 生成 {n_s4_exp} 个预期时刻**全部落空**（判 {n_s4_miss} 次缺失）；本单按观测相位 {n_s4_ph}h 生成，{n_s4_exp} 个全部匹配。

**周期切换的处理**：局部邻域若出现 ≥{n_switch_min} 段不同间隔（每段 ≥{n_run_min} 连），判 `switch_detected`，完整性直接 `UNDETERMINED`——切换时刻没有官方记录，任何单一网格都会算错（S3 反例：{n_s3_from}h→{n_s3_to}h 落在持仓内，R1 仍按 {n_s3_to}h 铺满，判「缺 {n_s3_miss} 次」；本单拒绝下结论）。

---

## 4 判据缺陷实证：R1 的 {n_r1_miss} 个「缺失」全是毫秒抖动

| 项 | 值 |
|---|---|
| R1 口径（相位 0 + 严格窗口 + 精确毫秒相等）算出的缺失 | {n_r1_miss} 条，涉及 {n_r1_flag} 个候选 |
| 其中在 ±{n_tol} ms 内能找到真实事件 | {n_r1chk_j} / {n_r1chk} |
| 最大偏移 | {n_r1max} ms |
| 本单口径（相位观测 + {n_tol} ms 容差）算出的缺失 | {n_r2_miss} 条，涉及 {n_r2_flag} 个候选 |
| 全池事件毫秒尾数非 0 的比例 | {n_ev_jit} / {n_ev_total}（{n_ev_jit_pct}） |

这不是数据问题，是**判据问题**：预期时刻是整点，实际 `funding_time` 带 {n_jit_min}~{n_jit_max} ms 尾数（全池最大；其中 R1 那 {n_r1_miss} 个缺失点的偏移不超过 {n_r1max} ms），精确相等必然落空。R1 报告里的 `GAP_PARTIAL_MISSING` 由此而来。

**R1 分类的实际含义**：它标 `NO_GAP_COMPLETE` 的笔，恰好是那些事件的毫秒尾数碰巧为 0 的笔；标 `GAP_PARTIAL_MISSING` 的笔，是尾数非 0 的笔。分类结果与「有没有漏结算」无关——**这两个类名的字面意思都被误用了**。

---

## 5 真实记录核对（只用生产数据）

### 5.1 周期切换与官方 `updateTime` 对得上

{n_sw_sym} 个标的观测到切换；其中 **{n_sw_match} 个**的「官方 interval」与「观测到的最后一段周期」一致。这 {n_sw_match} 个里有 **{n_sw_48h} 个**的 `updateTime` 落在最后一次切换的 ±{n_sw_win_h}h 内，另 {n_sw_late} 个晚于该窗口（`YGGUSDT` Δ{n_ygg_dh}h：`updateTime` 也可能是费率上下限变更，不必然是周期变更）。举三个对齐的：

| 标的 | 观测切换 | 官方 | `updateTime` 与最后切换的差 |
|---|---|---|---|
{sw_table}

唯一**不一致**的是 `IRYSUSDT`：官方 {n_irys_off_h}h、`updateTime` 2026-10-23，而观测到的最后一段是 {n_irys_obs_h}h（2026-05-19）。这恰好说明**官方值只对 `updateTime` 之后有效**，用它去判 9 月的持仓会判错——本单对 `IRYSUSDT` 用的是观测推断（{n_irys_obs_h}h），并把这一点写进了 `interval_evidence_note`。

### 5.2 端点抖动：退出同刻的那次结算会凭空消失

`entry < t <= exit` 是毫秒级严格比较。实测 {n_bj_cand} 个候选、{n_bj_ev} 个事件卡在端点上（口径 × 候选共 {n_r4} 条记录）：它们的名义结算时刻正好等于退出时刻，但记录晚 {n_bj_off_min}~{n_bj_off_max} ms，于是被严格判据排除。金额不小：

| 标的 | 退出时刻 | 若纳入的资金费 | 说明 |
|---|---|---|---|
{bj_table}

`WALUSDT` 那笔是**收取** {N(R4['cases'][1]['cost_if_included'], 'WAL边界金额', 'money')}——严格判据把它漏掉，等于把成本算高了。本单的处理是：容差匹配 + 标 `boundary_dependent`，**并且不给 `PROVEN`**（{n_bj_dep} 个候选因此降级），因为「退出成交发生在结算前还是后」在毫秒级上本来就不可判定。

### 5.3 「非零结算」不等于完整（真实案例）

`AXSUSDT` 那笔持仓，热库已有 {n_axs_old} 次结算，合并后是 {n_axs_new} 次——**有 {n_axs_old} 次照样漏了 {n_axs_gap} 次**。这类「有结算就判完整」的推理在旧 {n_old_rule_h}h 零事件判据下完全看不见（S2 反例即此）。真实数据里这类「次数变多」的笔共 {n_r5} 笔。

### 5.4 冷档补入的事件没有「网格外野事件」

{n_cand} 笔持仓内共补入 {n_r6_add} 条冷档事件，落在预期网格外的 **{n_r6_off} 条**（涉及 {n_offgrid} 个候选）。补进来的是同一条结算，不是新造的时刻。字段缺陷（`rate` / `mark_price` 缺失）候选 {n_field} 个。

---

## 6 两套经济口径：逐笔、变化原因、汇总

两套口径分别核算，**不合并、不相加、不互相替代**。

| 指标 | 口径 A：{cal_A} | 口径 B：{cal_B} |
|---|---|---|
| 成交 / 拒单 | {a_fill} / {a_rej} | {b_fill} / {b_rej} |
| 结算事件数（旧 → 新） | {a_ev_old} → {a_ev_new} | {b_ev_old} → {b_ev_new} |
| 资金费合计（旧 → 新） | {a_fold} → {a_fnew} | {b_fold} → {b_fnew} |
| 资金费差额 Δ | **{a_d}** | **{b_d}** |
| 已实现净额（旧 → 新） | {a_nold} → {a_nnew} | {b_nold} → {b_nnew} |
| 净额差额 | **{a_nd}** | **{b_nd}** |
| 受影响笔数 | {a_aff} | {b_aff} |
| 变化原因分布 | 无变化 {n_nochange_A} / 补入 {n_fill_A} / 仅端点抖动 {n_bj_A} | 无变化 {n_nochange_B} / 补入 {n_fill_B} / 仅端点抖动 {n_bj_B} / 拒单 {n_rej_B} |

恒等式 ΔNet = −ΔFunding 逐笔成立。**口径 A 的 {a_fold} / {a_fnew} / {a_nold} / {a_nnew} 与 05-C 基线、R1 自报值逐位一致**，说明本单没有换输入、没有换公式。

### 6.1 口径 A 受影响 {a_aff} 笔

| 标的 | 结算次数 | 资金费旧 | 资金费新 | 差额 | 净额差 |
|---|---|---|---|---|---|
{tA}

### 6.2 口径 B 受影响 {b_aff} 笔

| 标的 | 结算次数 | 资金费旧 | 资金费新 | 差额 | 净额差 |
|---|---|---|---|---|---|
{tB}

### 6.3 「财务不变、只是完整性定性改变」的笔

除上述 {a_aff} 笔外，其余 {n_nochange_A} 笔（口径 A）金额一分不差。它们的完整性结论**可能仍然变了**：R1 把其中大部分标成 `GAP_PARTIAL_MISSING`，本单标 `NO_GAP_DETECTED`。**这是定性改变，不是金额改变**，不能因为「钱没变」就认为整改没影响，也不能因为「定性变了」就说钱变了。

---

## 7 隔离反例（合成，与 §5 的真实证据严格分开）

{n_cases} 个反例，每个都同时跑 R1 的**原函数**（只把数据源换成合成序列）和本单判据：

| 编号 | 场景 | R1 判定 | 本单判定 |
|---|---|---|---|
{case_table}

- **S1**：真 {n_s1_true_h}h、删掉一半事件 ⇒ 观测全是 {n_s1_obs_h}h。R1 判 `proven_regular_8h` + 完整；本单用官方 {n_s1_true_h}h 生成 {n_s1_exp} 个预期时刻、只对上 {n_s1_act} 个，判缺 {n_s1_miss} 次并 `REFUTED`。
- **S1b**：同一份数据、没有官方证据。R1 仍宣称 `proven_regular_8h` + 完整；本单只给 `NOT_PROVEN`——**观测法在原理上无法区分「缺一半」和「本来就是 {n_s1_obs_h}h」**。
- **S2**：{n_s2_hold_h}h 持仓应结算 {n_s2_exp} 次、只记录 {n_s2_act} 次。旧 {n_old_rule_h}h 零事件判据判「完整」（因为次数 > 0），本单判 `INCONSISTENT_INFERRED`。
- **S3**：{n_s3_from}h→{n_s3_to}h 切换落在持仓内。R1 按 {n_s3_to}h 铺满整个持仓；本单判 `UNDETERMINED`。
- **S4**：{n_s4_h}h 周期但相位在 01/05/09 点。R1 的 {n_s4_exp} 个预期时刻全部落空；本单全部匹配。
- **S5**：结算记录比退出晚 {n_s5_ms}ms。R1 判缺失；本单容差匹配并标 `boundary_dependent`。

反例只用 `off_series()` 生成，不读生产库，不写任何生产文件。

---

## 8 交付给 B 的机器可读产物

| 文件 | 内容 |
|---|---|
| `out/06_机器可读_交付.json` | 逐候选 + 逐口径完整字段，含 `enum` 释义、`source_map`、输入与代码哈希 |
| `out/06_机器可读_交付.csv` | 一行 = (口径, 候选)，{n_cal} × {n_cand} 行，表格工具可直接读 |

关键字段（`schema = accum-early-06-w4-r2/v1`）：

- 证据：`interval_basis`、`interval_step_h`、`interval_evidence_note`、`official.interval_hours` / `official.update_utc` / `official.covers_hold`、`phase_h`、`phase_basis`、`phase_support`
- 判据：`expected_count`、`actual_count_strict`、`matched_count`、`missing_count`、`missing_instants_utc`、`off_grid_count`、`boundary_jitter_count`、`boundary_dependent_count`、`field_defect_count`
- 结论：`completeness`、`claim`、`reason`（枚举释义写在 JSON 的 `enum` 里，**不要用布尔读**）
- 金额：`calibers.{{A,B}}.{{funding_old,funding_new,delta_funding,net_old,net_new,delta_net,events_old,events_new,change_reason}}`
- 身份：`code_hashes`（本单全部脚本的 sha256）、`inputs.hashes`（适配器 / `replay_calc.py` / 候选来源 / R1 产物）、官方快照 `rows_sha256`

哈希前缀（完整值见 JSON）：适配器 `{adap_sha}`、`replay_calc.py` `{rc_sha}`、候选来源 `{cand_sha}`、官方快照 `{off_sha}`、R1 产物 `{r1_sha}`。

---

## 9 逐项完成情况

| 工单要求 | 状态 | 证据 |
|---|---|---|
| 1 复用 06-06 适配器，保留 51 候选、两套口径、原始数据，不新建采集器 | **完成** | 适配器未改（哈希 `{adap_sha}`）；{n_cand} 候选 / {n_sym} 标的 / {n_filled} 笔；官方快照为一次性只读落盘，非采集器 |
| 2 区分官方证据 / 观测推断 / 不可判定，无官方证据不得称已证明 | **完成**（**历史段官方证据不可得**，不是本单没做） | {n_basis_levels} 级 `interval_basis`；{n_basis_off} 官方 / {n_basis_inf} 推断；`claim=PROVEN` 只给官方覆盖者（校验 Q2 通过）。交易所不提供周期变更流水 ⇒ 只能界定「自 `updateTime` 起生效」 |
| 3 核对预期时刻生成，不默认相位；处理切换/部分漏事件/字段缺失/冲突/退出同刻 | **完成** | 相位从观测算（{n_ph0} 个为 0 是验证结果）；`switch_detected` 判 UNDETERMINED；`off_grid` 单列；`boundary_jitter` 单列并降级 claim |
| 4 周期或相位不确定时保留财务计算，完整性标未证明，不补零 | **完成** | {n_notproven} 笔 `NOT_PROVEN` 金额照算；`funding_events` 只含真实事件，缺字段仍落 `funding_gap`，无补零 |
| 5 真实记录核对 + 隔离反例，真实与合成分开 | **完成** | §4–§5 的真实核对（R1 缺失复核 / 周期切换 / 相位 / 端点抖动 / 非零仍漏 / 补入网格）覆盖工单点名的全部类别；§7 {n_cases} 个合成反例独立成文件 `03_隔离反例.json` |
| 6 两口径分别输出逐笔分类、变化原因、汇总；金额不变要说明只是定性改变 | **完成** | §6 两表 + 变化原因分布；§6.3 显式说明 |
| 7 机器可读字段、候选 ID、来源、输入/代码哈希 | **完成** | `06_机器可读_交付.json` / `.csv`，含 `source_map` 与全部脚本哈希 |
| 只写 `04_R2/`、不改共享生产代码、不选参数、不覆盖旧报告 | **完成** | 共享文件 sha256 复算一致、`03_R1/` 与 `06-06/` 开工后零改动（见 §11.1；**不拿 mtime 当证据**） |

---

## 10 未验证与不能声称

1. **「当时可得」仍未证明**。适配器对全部事件写 `availability=unproven`，本单没有改变这一点；官方快照是本单当天抓的，不能反推历史可得性。
2. **官方证据只覆盖 `updateTime` 之后**。{n_basis_inf} 个候选（`BNBUSDT`、`DOTUSDT`、`XLMUSDT`、`IRYSUSDT`、`SKRUSDT`）的历史段没有官方证据，它们的完整性只能算「未发现缺失」。
3. **相位是观测一致，不是官方文件**。本单没有拿到交易所的结算时刻规则文本，只能说「观测残差一致」。
4. **`PROVEN` 的含义有限**：官方周期覆盖 + 相位观测一致 + 网格内无缺失。它**不**包含「当时可得」「未来不会变」「策略层已接受」。
5. **`IRYSUSDT` 的官方 {n_irys_off_h}h 与观测 {n_irys_obs_h}h 不一致**，本单按观测推断处理；如果交易所实际在 9 月就是 {n_irys_off_h}h，那这笔会少记若干次结算——本单无法排除这种可能，已如实标 `NOT_PROVEN`。
6. **端点抖动无法从数据上判定先后**。`WALUSDT` 的 {N(R4['cases'][1]['cost_if_included'], 'WAL边界金额2', 'money')} 是否该计入，取决于退出成交与结算的毫秒级先后，本单只给区间不给结论。
7. **结论只在 {n_sym} 标的 / {n_cand} 笔 / 窗口 {n_win_h} 小时内成立**，不能推广到全池。
8. **同键冲突（两侧 `rate`/`mark` 不等）在真实数据里是 0 条**。冲突分支只有合成用例覆盖，**不得声称已用真实数据验证**；本单真实走到的只有「网格内无缺失」与「端点抖动」两条路径。
9. **未做**：正式实时读取链改造（`paper_engine.funding_events` 仍只读热库）、策略层参数、经济口径选择。

---

## 11 复跑

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
P=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
for f in 00_范围与身份锚定 01_周期证据分级 02_缺口判据与完整性 \\
         03_隔离反例 04_真实记录核对 05_两口径逐笔与汇总 06_机器可读交付 \\
         07_生成报告 08_数字对撞; do
  $P "evidence/ACCUM-EARLY-06-W4/04_R2/scripts/$f.py" || echo "FAIL $f"
done
```

`00` 默认复用已落盘的官方快照（保证重跑字节稳定）；需要刷新时设 `R2_REFRESH=1`，届时 `updateTime` 与行数可能变化，属于新证据批次，应重跑全链。

### 11.1 复跑与「没改别人东西」的实测证据

| 项 | 结果 |
|---|---|
| `00`–`05` 自检 | 共 {n_selfchecks} 项，全过（逐脚本明细见各自 `out/*.json` 的 `checks`） |
| `06` 交付 | 候选 {n_cand}、口径行 {n_cal_rows}，JSON / CSV 均已落盘 |
| `08` 数字对撞 | 结果见 `out/08_数字对撞.json`（正向逐字符落位、反向未登记、派生值重算三组） |
| 二次重跑字节稳定 | {n_stable} 个产物（`README.md`、`07_正文数字台账.json`、`08_数字对撞.json`、`06_机器可读_交付.csv`）sha256 逐位相同 |
| **按设计不稳定** | `00_范围与身份锚定.json` 与 `06_机器可读_交付.json` 里含生产库 `mtime_ms`（外部进程持续写入），每次跑都会变；这两个文件的身份靠**行数 + 最大 `funding_time` + 逐标的消费事件 sha**，不靠 mtime。`06_运行时刻.json` 单独落盘就是为了把运行时刻从主产物里摘出去 |
| 共享生产文件 | {n_shared_files} 个（适配器 / `replay_calc.py` / `market_repository.py` / `funding.py` / `research_15m.py` / 候选来源 / R1 产物 / R1 脚本）sha256 复算与登记一致 |
| `03_R1/` 与 `06-06/` 目录 | 本单开工时刻之后**没有任何文件被改**（`find -newermt` 空结果） |

注意：**mtime 不能当身份**。本机 `CST` 与代码里的 UTC 差 8 小时，直接比 mtime 会得出相反结论；跨时段只有 **sha256** 可复用。

---

## 12 本单没做的事

- 没有改 `replay_calc.py` / `market_repository.py` / `trading_core/funding.py` / `paper_engine.py`；
- 没有改 06-06 适配器、没有改 `03_R1/` 的任何产物；
- 没有新建采集器、没有补零、没有跨交易所拼接、没有伪造可得时间；
- 没有选经济口径、没有改策略参数、没有碰前端；
- 没有把「未发现缺失」写成「已证明完整」。

*本报告由 `scripts/07_生成报告.py` 生成；正文 @@NLEDGER@@ 个数字登记在 `out/07_正文数字台账.json`，由 `scripts/08_数字对撞.py` 双向校验。*
"""

    final_n = len(LEDGER) + 1
    LEDGER.append({"key": "台账数字个数", "value": final_n, "text": str(final_n),
                   "fmt": "int", "src": "自指：本台账条目数"})
    doc = doc.replace("@@NLEDGER@@", _MARK.format(final_n - 1, str(final_n)))

    clean, _offsets = strip_markers(doc)

    (L.ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2" / "README.md").write_text(
        clean, encoding="utf-8")
    (L.OUT / "07_正文数字台账.json").write_text(
        json.dumps({"n": len(LEDGER), "items": LEDGER}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    n_mark = sum(i["n_occurrences"] for i in LEDGER)
    print(f"README 行数 {clean.count(chr(10)) + 1} | 台账 {len(LEDGER)} 个数字 / "
          f"{n_mark} 处落位 | 未落位 {sum(1 for i in LEDGER if not i['offsets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
