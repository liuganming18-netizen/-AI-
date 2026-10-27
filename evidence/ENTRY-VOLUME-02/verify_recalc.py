#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ENTRY-VOLUME-02：从 206 行逐笔母表独立重建缩量过滤验收结果。

统计计算的唯一事实源是 VOLUME-BE-02 的完整配对逐笔表及其逐笔因子标记：
- 206 候选：`paired_trades_1r.csv`
- 过滤因子独立标记：`hypotheses_evaluation_detail.csv`
- 去重：按 (symbol, entry_time_utc) 聚类，保留 int(group) 最小行。

不会从任何 summary JSON 取数字来做计算。旧 JSON/报告只在独立重算完成后用于交叉核对。
不运行撮合、不读取退出后价格重新挑退出点；不使用 `pos_exit_*` 持仓退出分支字段。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = ROOT / "evidence/ENTRY-VOLUME-02"
OUT = HERE / "out"
LOG_DIR = HERE / "logs"
PAIR = ROOT / "evidence/VOLUME-BE-02/out/paired_trades_1r.csv"
FEATURES = ROOT / "evidence/VOLUME-BE-02/out/hypotheses_evaluation_detail.csv"
DUP_JSON = ROOT / "evidence/VOLUME-BE-02/out/duplicate_groups.json"
FEATURE_CODE = ROOT / "evidence/VOLUME-BE-02/code/vbe2_core.py"
BUILD_CODE = ROOT / "evidence/VOLUME-BE-02/code/02_run_abcd_fixed.py"
SUBPERIOD_CODE = ROOT / "evidence/VOLUME-BE-02/code/03_subperiod_and_cases.py"
VBE02_REPORT = ROOT / "evidence/VOLUME-BE-02/报告.md"
VBE02_SUB = ROOT / "evidence/VOLUME-BE-02/out/subperiod_analysis.json"
VBE02_VERSIONS = ROOT / "evidence/VOLUME-BE-02/out/versions.json"
VBE01_REPORT = ROOT / "evidence/VOLUME-BE-01/报告.md"
VBE01_CONTRACT = ROOT / "evidence/VOLUME-BE-01/00_规则冻结与假设契约.md"
VBE01_SUB = ROOT / "evidence/VOLUME-BE-01/out/subperiod_analysis.json"
ENTRY01_DIR = ROOT / "evidence/ENTRY-VOLUME-01"
ENTRY01_TRADES = ENTRY01_DIR / "out/trades_low_vol_drop.csv"
ENTRY01_SUMMARY = ENTRY01_DIR / "out/summary_metrics.json"
ENTRY01_LOO = ENTRY01_DIR / "out/sensitivity_leave_one_out.json"
ENTRY01_SUB = ENTRY01_DIR / "out/subperiod_distribution.json"
ENTRY01_REPORT = ENTRY01_DIR / "报告.md"
ENTRY01_CODE = ENTRY01_DIR / "run_analysis.py"
ENTRY01_VERSIONS = ENTRY01_DIR / "out/versions.json"
TOL = 1e-9

SOURCE_FILES = [
    PAIR, FEATURES, DUP_JSON, FEATURE_CODE, BUILD_CODE, SUBPERIOD_CODE,
    VBE02_REPORT, VBE02_SUB, VBE02_VERSIONS,
    VBE01_REPORT, VBE01_CONTRACT, VBE01_SUB,
    ENTRY01_TRADES, ENTRY01_SUMMARY, ENTRY01_LOO, ENTRY01_SUB,
    ENTRY01_REPORT, ENTRY01_CODE, ENTRY01_VERSIONS,
]

PERIODS = [
    ("早期（2021-2023）", lambda y: y <= 2023),
    ("中期（2024）", lambda y: y == 2024),
    ("近期（2025-2026）", lambda y: y >= 2025),
]
ROUND_TOL = 5.1e-5  # 上游 VOLUME-BE-02 JSON 将金额四舍五入到 4 位小数


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_sig(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def parse_bool(x: Any) -> bool:
    return str(x).strip().lower() in {"true", "1", "yes"}


def num(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if value in (None, ""):
        raise ValueError(f"缺少数值字段 {key}: {row}")
    return float(value)


def year_of(row: dict[str, Any]) -> int:
    # UTC 时间为 ISO 日期开头，直接取公历年份；不将退出年份代入分期。
    return int(row["entry_time_utc"][:4])


def pct_diff(a: float, b: float) -> float:
    return a - b


def amount_for_filtered(a_net: float) -> tuple[float, float, float]:
    """逐笔反事实：过滤未开仓记 0U；少亏、错失均返回正金额，净差额有正负号。"""
    saved = max(-a_net, 0.0)
    forgone = max(a_net, 0.0)
    delta = -a_net
    assert abs((saved - forgone) - delta) <= TOL
    return saved, forgone, delta


def full_stats(rows: list[dict[str, Any]], filter_ids: set[str]) -> dict[str, Any]:
    filtered = [r for r in rows if r["signal_id"] in filter_ids]
    saved = sum(amount_for_filtered(num(r, "A_net"))[0] for r in filtered)
    forgone = sum(amount_for_filtered(num(r, "A_net"))[1] for r in filtered)
    delta = sum(amount_for_filtered(num(r, "A_net"))[2] for r in filtered)
    a_sum = sum(num(r, "A_net") for r in rows)
    c_sum = sum(0.0 if r["signal_id"] in filter_ids else num(r, "A_net") for r in rows)
    return {
        "n": len(rows),
        "filtered_n": len(filtered),
        "entered_n": len(rows) - len(filtered),
        "A_net": a_sum,
        "C_filter_net": c_sum,
        "saved_loss": saved,
        "forgone_profit_magnitude": forgone,
        "forgone_profit_impact": -forgone,
        "net_delta": delta,
        "C_minus_A": c_sum - a_sum,
        "win_n_filtered": sum(1 for r in filtered if num(r, "A_net") > 0),
        "loss_n_filtered": sum(1 for r in filtered if num(r, "A_net") < 0),
    }


def audit_source_code() -> dict[str, Any]:
    core = FEATURE_CODE.read_text(encoding="utf-8")
    start = core.index("def extract_entry_features")
    end = core.index("\ndef calculate_exact_funding", start)
    extractor = core[start:end]
    build = BUILD_CODE.read_text(encoding="utf-8")
    subcode = SUBPERIOD_CODE.read_text(encoding="utf-8")
    requirements = {
        "feature_uses_only_closed_1h_bars": 'df_1h["close_time"] <= entry_ms' in extractor,
        "low_vol_rule_is_close_below_open_and_volume_below_sma":
            'prev_1h_low_vol_drop = bool(p_drop and (p_vol < p_volsma))' in extractor,
        "rule_excludes_current_bar_from_average": 'h1_closed["volume"].iloc[:-2].tail(20)' in extractor,
        "low_history_average_fallback_is_present": 'len(vols_prev_p) >= 5 else p_vol' in extractor,
        "filter_wired_directly_from_low_vol_flag":
            'merged["is_filtered_main"] = merged["prev_1h_low_vol_drop"]' in build,
        "filtered_entry_variant_has_zero_net":
            'c_status = "FILTERED_NOT_ENTERED"' in build and 'c_net = 0.0' in build,
        "same_three_year_buckets_in_code":
            'd["year"] <= 2023' in subcode and 'd["year"] == 2024' in subcode and 'd["year"] >= 2025' in subcode,
        "entry_price_comparison_absent_from_feature_rule":
            "entry_price" not in extractor and "entry_px" not in extractor,
    }
    return {
        "source_files": {str(p): sha256(p) for p in [FEATURE_CODE, BUILD_CODE, SUBPERIOD_CODE]},
        "checks": requirements,
        "all_checks_pass": all(requirements.values()),
        "note": "VBE02 的已执行分类标记没有 close < entry_price 条件；ENTRY-VOLUME-01 报告文字多写该条件，属于文档口径不一致，不能反称其已参与计算。",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    start_sigs = {str(p): file_sig(p) for p in SOURCE_FILES}

    # 只从逐笔母表与对应行级因子标记重建。禁止以汇总 JSON 作为运算源。
    candidates = read_csv(PAIR)
    features = read_csv(FEATURES)
    assert len(candidates) == 206, f"206 候选母表行数不符: {len(candidates)}"
    assert len({r["signal_id"] for r in candidates}) == 206, "候选 signal_id 不唯一"
    assert len(features) == 206, f"行级特征表行数不符: {len(features)}"
    feature_by_id = {r["signal_id"]: r for r in features}
    assert len(feature_by_id) == 206, "行级因子表 signal_id 不唯一"
    assert set(feature_by_id) == {r["signal_id"] for r in candidates}, "逐笔母表与因子表身份不全等"

    # 依据源代码所生成的布尔因子标记，独立还原过滤集合。
    filter_ids = {sid for sid, r in feature_by_id.items() if parse_bool(r["prev_1h_low_vol_drop"])}
    assert len(filter_ids) == 14, f"低量下跌标记数不是 14: {len(filter_ids)}"

    # 从身份键与 group 重新建 201 事件，不直接采信 is_event_dedup 或重复组 JSON。
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in candidates:
        groups[(r["symbol"], r["entry_time_utc"])].append(r)
    retained: dict[tuple[str, str], dict[str, str]] = {}
    duplicate_groups: list[tuple[tuple[str, str], dict[str, str], list[dict[str, str]]]] = []
    for key, members in groups.items():
        ordered = sorted(members, key=lambda r: (int(r["group"]), r["signal_id"]))
        retained[key] = ordered[0]
        if len(ordered) > 1:
            duplicate_groups.append((key, ordered[0], ordered[1:]))
    event_ids = {r["signal_id"] for r in retained.values()}
    assert len(groups) == 201 and len(event_ids) == 201, "按身份键重建事件数不是 201"
    assert len(duplicate_groups) == 5 and sum(len(x[2]) for x in duplicate_groups) == 5, "预期只有 5 个二重组"

    # 与母表既有事件标志逐条核对；这只是校验，不是分组计算来源。
    source_event_mismatches = []
    for r in candidates:
        recomputed = r["signal_id"] in event_ids
        recorded = parse_bool(r["is_event_dedup"])
        if recomputed != recorded:
            source_event_mismatches.append(r["signal_id"])
    assert not source_event_mismatches, f"事件标志与身份重建不符: {source_event_mismatches}"

    # 生成 206 行候选→事件完整映射表。
    mapping_rows = []
    duplicate_rows = []
    key_to_dup = {key: (keep, dropped) for key, keep, dropped in duplicate_groups}
    for r in candidates:
        key = (r["symbol"], r["entry_time_utc"])
        keep = retained[key]
        feat = feature_by_id[r["signal_id"]]
        low_flag = parse_bool(feat["prev_1h_low_vol_drop"])
        category_match = (r["prev_1h_category"] == feat["prev_1h_category"])
        status_should_filter = low_flag
        status_match = (r["C_status"] == ("FILTERED_NOT_ENTERED" if low_flag else "ENTERED"))
        mapping_rows.append({
            "event_id": f"{key[0]}@{key[1]}",
            "symbol": key[0],
            "entry_time_utc": key[1],
            "signal_id": r["signal_id"],
            "window": r["window"],
            "group": r["group"],
            "dedup_retained": r["signal_id"] == keep["signal_id"],
            "retained_signal_id": keep["signal_id"],
            "retained_group": keep["group"],
            "retention_basis": "唯一身份键，仅一条" if len(groups[key]) == 1 else "同标的+同入场时刻，保留 int(group) 最小实例",
            "source_is_event_dedup": r["is_event_dedup"],
            "recomputed_is_event": r["signal_id"] in event_ids,
            "feature_prev_1h_category": feat["prev_1h_category"],
            "paired_prev_1h_category": r["prev_1h_category"],
            "category_matches": category_match,
            "prev_1h_low_vol_drop": low_flag,
            "C_status": r["C_status"],
            "C_status_matches_feature": status_match,
            "A_net": r["A_net"],
        })
        assert category_match and status_match, f"入场分类/过滤状态与因子标记不符: {r['signal_id']}"

    for key, keep, drops in sorted(duplicate_groups, key=lambda x: (x[0][0], x[0][1])):
        for drop in drops:
            f_keep, f_drop = feature_by_id[keep["signal_id"]], feature_by_id[drop["signal_id"]]
            duplicate_rows.append({
                "symbol": key[0], "entry_time_utc": key[1],
                "kept_signal_id": keep["signal_id"], "kept_group": keep["group"],
                "kept_window": keep["window"], "kept_A_net": keep["A_net"],
                "dropped_signal_id": drop["signal_id"], "dropped_group": drop["group"],
                "dropped_window": drop["window"], "dropped_A_net": drop["A_net"],
                "kept_category": f_keep["prev_1h_category"],
                "dropped_category": f_drop["prev_1h_category"],
                "kept_low_vol_flag": parse_bool(f_keep["prev_1h_low_vol_drop"]),
                "dropped_low_vol_flag": parse_bool(f_drop["prev_1h_low_vol_drop"]),
                "retention_basis": "保留 int(group) 较小者，与净盈亏无关",
            })
    assert len(duplicate_rows) == 5
    assert all(not x["kept_low_vol_flag"] and not x["dropped_low_vol_flag"] for x in duplicate_rows), "被剔除行出现缩量命中"
    assert all(x["dropped_category"] == "non_drop" for x in duplicate_rows)
    write_csv(OUT / "candidate_event_mapping.csv", mapping_rows)
    write_csv(OUT / "duplicate_pairs_recomputed.csv", duplicate_rows)

    # 逐笔收入贡献重算：过滤后未开仓净额=0；delta=0-A_net。
    filter_rows = [r for r in candidates if r["signal_id"] in filter_ids]
    event_filter_rows = [r for r in filter_rows if r["signal_id"] in event_ids]
    assert len(filter_rows) == len(event_filter_rows) == 14
    entry01_rows = read_csv(ENTRY01_TRADES)
    entry01_ids = {(r["symbol"], r["entry_time_utc"]) for r in entry01_rows}
    recomputed_ids = {(r["symbol"], r["entry_time_utc"]) for r in event_filter_rows}
    assert len(entry01_rows) == 14 and entry01_ids == recomputed_ids, "ENTRY-VOLUME-01 14 笔身份与全量源重建不符"

    trade_recalc = []
    for r in event_filter_rows:
        a = num(r, "A_net")
        saved, forgone, delta = amount_for_filtered(a)
        feature = feature_by_id[r["signal_id"]]
        checks = {
            "C_net_vs_zero": abs(num(r, "C_net") - 0.0),
            "C_saved_diff": abs(num(r, "C_saved_loss") - saved),
            "C_forgone_diff": abs(num(r, "C_forgone_profit") - forgone),
            "C_delta_diff": abs(num(r, "C_delta") - delta),
        }
        assert all(v <= TOL for v in checks.values()), f"逐笔来源表与独立算术对账不符 {r['signal_id']}: {checks}"
        trade_recalc.append({
            "signal_id": r["signal_id"], "symbol": r["symbol"], "entry_time_utc": r["entry_time_utc"],
            "year": year_of(r), "window": r["window"], "group": r["group"],
            "prev_1h_category": feature["prev_1h_category"],
            "A_net": a, "filter_net": 0.0,
            "saved_loss_recomputed": saved,
            "forgone_profit_magnitude_recomputed": forgone,
            "forgone_profit_impact_recomputed": -forgone,
            "filter_delta_recomputed": delta,
            "source_C_net": r["C_net"], "source_C_delta": r["C_delta"],
            "source_saved": r["C_saved_loss"], "source_forgone_magnitude": r["C_forgone_profit"],
            "max_abs_row_reconcile_diff": max(checks.values()),
        })
    write_csv(OUT / "low_vol_trade_recalculation.csv", trade_recalc)

    # 独立重算 206 与 201 两个范围；少亏/错失/差额从 206 候选逐笔 outcome 推导。
    stats_candidate = full_stats(candidates, filter_ids)
    event_candidates = [r for r in candidates if r["signal_id"] in event_ids]
    stats_events = full_stats(event_candidates, filter_ids)
    assert stats_candidate["filtered_n"] == stats_events["filtered_n"] == 14
    assert abs(stats_candidate["net_delta"] - stats_events["net_delta"]) <= TOL
    assert abs(stats_events["saved_loss"] - stats_events["forgone_profit_magnitude"] - stats_events["net_delta"]) <= TOL

    # 逐一移除：对 14 笔直接使用重算的 A_net，不调用原汇总 JSON 做运算。
    loo_rows = []
    total_saved = stats_events["saved_loss"]
    total_forgone = stats_events["forgone_profit_magnitude"]
    total_delta = stats_events["net_delta"]
    for removed in event_filter_rows:
        ra = num(removed, "A_net")
        rs, rf, rd = amount_for_filtered(ra)
        rem_saved = total_saved - rs
        rem_forgone = total_forgone - rf
        rem_delta = total_delta - rd
        loo_rows.append({
            "removed_signal_id": removed["signal_id"],
            "removed_symbol": removed["symbol"],
            "removed_entry_time_utc": removed["entry_time_utc"],
            "removed_A_net": ra,
            "removed_saved": rs,
            "removed_forgone_magnitude": rf,
            "removed_delta": rd,
            "remaining_n": 13,
            "remaining_saved_loss": rem_saved,
            "remaining_forgone_profit_magnitude": rem_forgone,
            "remaining_forgone_profit_impact": -rem_forgone,
            "remaining_net_delta": rem_delta,
            "reversed_positive": rem_delta > TOL,
        })
    assert len(loo_rows) == 14
    assert all(not r["reversed_positive"] for r in loo_rows)
    write_csv(OUT / "leave_one_out_recalculation.csv", loo_rows)

    # 仅在独立重算结束后读取旧汇总做比对，不用于计算上述结果。
    old_summary = json.loads(ENTRY01_SUMMARY.read_text(encoding="utf-8"))
    summary_comparison = []
    for field, independent in [
        ("sample_count", stats_events["filtered_n"]),
        ("win_count", stats_events["win_n_filtered"]),
        ("loss_count", stats_events["loss_n_filtered"]),
        ("saved_loss", stats_events["saved_loss"]),
        ("forgone_profit", stats_events["forgone_profit_magnitude"]),
        ("net_filter_difference", stats_events["net_delta"]),
    ]:
        reported = float(old_summary[field])
        summary_comparison.append({"field": field, "independent": independent,
                                   "previous_artifact": reported,
                                   "difference": independent - reported,
                                   "matches_within_1e-9": abs(independent - reported) <= TOL})
    assert all(x["matches_within_1e-9"] for x in summary_comparison), "ENTRY01 总额 JSON 与逐笔独立重算不一致"

    # 独立 LOO 与旧 LOO 对照（按标的+入场时刻匹配，不依赖序号）。
    old_loo = json.loads(ENTRY01_LOO.read_text(encoding="utf-8"))
    old_loo_map = {(x["removed_symbol"], x["removed_entry_time"]): x for x in old_loo["details"]}
    loo_comparisons = []
    for r in loo_rows:
        key = (r["removed_symbol"], r["removed_entry_time_utc"])
        prior = old_loo_map.get(key)
        assert prior is not None, f"旧 LOO 缺身份 {key}"
        diffs = {
            "delta_diff": r["remaining_net_delta"] - float(prior["remaining_net_difference"]),
            "saved_diff": r["remaining_saved_loss"] - float(prior["remaining_saved_loss"]),
            "forgone_diff": r["remaining_forgone_profit_magnitude"] - float(prior["remaining_forgone_profit"]),
        }
        loo_comparisons.append({"symbol": key[0], "entry_time_utc": key[1], **diffs,
                                "matches_within_1e_9": all(abs(x) <= TOL for x in diffs.values()),
                                "previous_reversed_positive": bool(prior["is_reversed_to_positive"]),
                                "independent_reversed_positive": r["reversed_positive"]})
    assert len(old_loo_map) == 14 and all(r["matches_within_1e_9"] for r in loo_comparisons)

    # 三套边界分期均按入场年：全候选、201 事件、14 命中过滤样本。
    period_rows = []
    for scope, scope_rows in [("206 候选全体", candidates), ("201 去重事件", event_candidates),
                              ("14 笔缩量子集", event_filter_rows)]:
        for label, predicate in PERIODS:
            sub = [r for r in scope_rows if predicate(year_of(r))]
            filtered = [r for r in sub if r["signal_id"] in filter_ids]
            saved = sum(amount_for_filtered(num(r, "A_net"))[0] for r in filtered)
            forgone = sum(amount_for_filtered(num(r, "A_net"))[1] for r in filtered)
            delta = sum(amount_for_filtered(num(r, "A_net"))[2] for r in filtered)
            a_sum = sum(num(r, "A_net") for r in sub)
            c_sum = a_sum + delta
            period_rows.append({
                "scope": scope, "period": label, "period_rule": "entry_time_utc 自然年：<=2023 / ==2024 / >=2025",
                "n_all_in_period": len(sub), "n_low_vol_filtered": len(filtered),
                "low_vol_A_net": sum(num(r, "A_net") for r in filtered),
                "all_A_net": a_sum, "filter_C_net": c_sum,
                "saved_loss": saved, "forgone_profit_magnitude": forgone,
                "forgone_profit_impact": -forgone, "filter_net_delta": delta,
                "filtered_symbols": ";".join(r["symbol"] for r in filtered),
            })
    write_csv(OUT / "subperiod_recalculation.csv", period_rows)

    # 对 VOLUME-BE-02 分期 JSON、ENTRY-VOLUME-01 14 笔分期 JSON 的后验交叉核对。
    vbe02_sub = json.loads(VBE02_SUB.read_text(encoding="utf-8"))
    period_json_comparison = []
    vbe02_scopes = [("universe_206_candidates", "206 候选全体"),
                    ("event_level_201_dedup", "201 去重事件")]
    for source_scope, our_scope in vbe02_scopes:
        our = {r["period"]: r for r in period_rows if r["scope"] == our_scope}
        for p in vbe02_sub[source_scope]:
            if "2021-2023" in p["period"]:
                our_period = "早期（2021-2023）"
            elif "2024" in p["period"]:
                our_period = "中期（2024）"
            elif "2025-2026" in p["period"]:
                our_period = "近期（2025-2026）"
            else:
                raise AssertionError(f"未知分期标签: {p['period']}")
            x = our[our_period]
            diffs = {
                "count_diff": x["n_all_in_period"] - int(p["n_signals"]),
                "filtered_count_diff": x["n_low_vol_filtered"] - int(p["C_filtered_count"]),
                "saved_diff": x["saved_loss"] - float(p["C_saved_loss"]),
                "forgone_diff": x["forgone_profit_magnitude"] - float(p["C_forgone_profit"]),
                "delta_diff": x["filter_net_delta"] - float(p["C_net_delta"]),
            }
            period_json_comparison.append({"source": "VOLUME-BE-02/out/subperiod_analysis.json",
                                           "scope": our_scope, "period": p["period"], **diffs,
                                           "all_match_within_rounding_tolerance": diffs["count_diff"] == 0 and diffs["filtered_count_diff"] == 0 and all(abs(v) <= ROUND_TOL for k, v in diffs.items() if k.endswith("_diff") and k not in {"count_diff", "filtered_count_diff"})})

    entry01_sub = json.loads(ENTRY01_SUB.read_text(encoding="utf-8"))["subperiod_distribution"]
    for source_label, old in entry01_sub.items():
        if "早期" in source_label:
            target_period = "早期（2021-2023）"
        elif "中期" in source_label:
            target_period = "中期（2024）"
        elif "近期" in source_label:
            target_period = "近期（2025-2026）"
        else:
            raise AssertionError(f"未知 ENTRY-VOLUME-01 分期标签: {source_label}")
        target = next((r for r in period_rows if r["scope"] == "14 笔缩量子集" and r["period"] == target_period), None)
        assert target is not None, f"ENTRY01 分期无法匹配 {source_label}"
        diffs = {
            "count_diff": target["n_all_in_period"] - int(old["count"]),
            "saved_diff": target["saved_loss"] - float(old["saved_loss"]),
            "forgone_diff": target["forgone_profit_magnitude"] - float(old["forgone_profit"]),
            "delta_diff": target["filter_net_delta"] - float(old["net_filter_difference"]),
        }
        period_json_comparison.append({"source": "ENTRY-VOLUME-01/out/subperiod_distribution.json",
                                       "scope": "14 笔缩量子集", "period": source_label, **diffs,
                                       "all_match_within_1e-9": diffs["count_diff"] == 0 and all(abs(v) <= TOL for k, v in diffs.items() if k != "count_diff")})
    assert all(x["all_match_within_rounding_tolerance"] for x in period_json_comparison
               if x["source"] == "VOLUME-BE-02/out/subperiod_analysis.json"), "VBE02 分期 JSON 与逐笔重算不一致"
    assert all(x["all_match_within_1e-9"] for x in period_json_comparison
               if x["source"] == "ENTRY-VOLUME-01/out/subperiod_distribution.json"), "ENTRY01 分期 JSON 与逐笔重算不一致"

    # Older VOLUME-BE-01: same bucket labels, but prior run tested high-volume filter/future-leaking rule.
    vbe01_sub = json.loads(VBE01_SUB.read_text(encoding="utf-8"))
    legacy_deltas = {r["period"]: r["C_net_delta"] for r in vbe01_sub}

    # Audit prose claims in VBE02 report: stale period values/count split differ from output and rows.
    report_text = VBE02_REPORT.read_text(encoding="utf-8")
    claimed = [
        {"period": "早期（2021-2023）", "claimed_candidates": 69, "claimed_events": 67, "claimed_delta": -19.05},
        {"period": "中期（2024）", "claimed_candidates": 39, "claimed_events": 38, "claimed_delta": -24.30},
        {"period": "近期（2025-2026）", "claimed_candidates": 98, "claimed_events": 96, "claimed_delta": -7.18},
    ]
    prose_diffs = []
    for claim in claimed:
        cp = next(r for r in period_rows if r["scope"] == "206 候选全体" and r["period"] == claim["period"])
        ep = next(r for r in period_rows if r["scope"] == "201 去重事件" and r["period"] == claim["period"])
        prose_diffs.append({
            "period": claim["period"],
            "report_claim_candidates": claim["claimed_candidates"],
            "recomputed_candidates": cp["n_all_in_period"],
            "report_claim_events": claim["claimed_events"],
            "recomputed_events": ep["n_all_in_period"],
            "report_claim_delta": claim["claimed_delta"],
            "recomputed_delta": ep["filter_net_delta"],
            "report_claim_filtered_n": cp["n_low_vol_filtered"],
            "recomputed_filtered_n": ep["n_low_vol_filtered"],
            "text_claim_present": (f"{claim['claimed_delta']:.2f} U" in report_text),
        })

    # A compact explicit version-difference table for reviewers; all current values above are row-recomputed.
    entry01_sub_for_diff = json.loads(ENTRY01_SUB.read_text(encoding="utf-8"))["subperiod_distribution"]
    vbe01_by_period = {}
    for old in vbe01_sub:
        if "2021-2023" in old["period"]:
            k = "早期（2021-2023）"
        elif "2024" in old["period"]:
            k = "中期（2024）"
        else:
            k = "近期（2025-2026）"
        vbe01_by_period[k] = old
    prose_by_period = {x["period"]: x for x in prose_diffs}
    vbe02_event_json = {x["period"]: x for x in vbe02_sub["event_level_201_dedup"]}
    entry01_by_period = {}
    for label, old in entry01_sub_for_diff.items():
        if "早期" in label:
            k = "早期（2021-2023）"
        elif "中期" in label:
            k = "中期（2024）"
        else:
            k = "近期（2025-2026）"
        entry01_by_period[k] = old
    version_difference_rows = []
    for label, _predicate in PERIODS:
        current_c = next(r for r in period_rows if r["scope"] == "206 候选全体" and r["period"] == label)
        current_e = next(r for r in period_rows if r["scope"] == "201 去重事件" and r["period"] == label)
        v02_text = prose_by_period[label]
        v02_json = next(x for key, x in vbe02_event_json.items() if label.startswith("早期") and "2021-2023" in key or label.startswith("中期") and "2024" in key or label.startswith("近期") and "2025-2026" in key)
        v01 = vbe01_by_period[label]
        e01 = entry01_by_period[label]
        version_difference_rows.append({
            "period": label,
            "current_candidate_n": current_c["n_all_in_period"],
            "current_event_n": current_e["n_all_in_period"],
            "current_low_vol_n": current_e["n_low_vol_filtered"],
            "current_delta": current_e["filter_net_delta"],
            "entry01_filtered_subset_n": e01["count"],
            "entry01_filtered_subset_delta": e01["net_filter_difference"],
            "vbe02_json_event_n": v02_json["n_signals"],
            "vbe02_json_filtered_n": v02_json["C_filtered_count"],
            "vbe02_json_delta": v02_json["C_net_delta"],
            "vbe02_report_text_event_n": v02_text["report_claim_events"],
            "vbe02_report_text_delta": v02_text["report_claim_delta"],
            "vbe01_old_candidate_n": v01["n_signals"],
            "vbe01_old_filter_n": v01["C_filtered_count"],
            "vbe01_old_delta": v01["C_net_delta"],
            "boundary_changed": False,
            "difference_source": "VBE02正文与其JSON不一致；VBE01旧版为高量下跌过滤且存在未收盘1h读取，不是当前低量规则",
        })
    write_csv(OUT / "version_differences.csv", version_difference_rows)

    # 206 行 C 对 A 的逐行对账。
    row_accounting = []
    for r in candidates:
        filtered = r["signal_id"] in filter_ids
        a = num(r, "A_net")
        expected_c = 0.0 if filtered else a
        expected_delta = expected_c - a
        expected_saved = max(-a, 0.0) if filtered else 0.0
        expected_forgone = max(a, 0.0) if filtered else 0.0
        diff = max(abs(num(r, "C_net") - expected_c), abs(num(r, "C_delta") - expected_delta),
                   abs(num(r, "C_saved_loss") - expected_saved), abs(num(r, "C_forgone_profit") - expected_forgone))
        row_accounting.append(diff)
    assert max(row_accounting) <= TOL, f"206 行 C 相对 A 对账最大差异 {max(row_accounting)}"

    feature_code_audit = audit_source_code()
    assert feature_code_audit["all_checks_pass"], f"冻结逻辑代码检查失败: {feature_code_audit}"

    # Verify original duplicate_groups record against independently reconstructed group identities/retention.
    dup_json = json.loads(DUP_JSON.read_text(encoding="utf-8"))
    duplicate_json_check = []
    expected_dup_map = {(x["symbol"], x["entry_time_utc"]): x for x in dup_json}
    assert len(expected_dup_map) == 5
    for key, keep, drops in duplicate_groups:
        entry = expected_dup_map.get(key)
        assert entry is not None
        listed_keep = entry["kept_instance"]
        assert listed_keep["signal_id"] == keep["signal_id"] and int(listed_keep["group"]) == int(keep["group"])
        listed_drops = {d["signal_id"] for d in entry["dropped_instances"]}
        assert listed_drops == {d["signal_id"] for d in drops}
        duplicate_json_check.append({"symbol": key[0], "entry_time_utc": key[1],
                                     "recomputed_kept": keep["signal_id"], "json_kept": listed_keep["signal_id"],
                                     "match": True})

    # Comparisons are deliberately post-calculation only. They cannot replace primary calculations.
    checks = {
        "candidate_rows_206": len(candidates) == 206,
        "unique_events_201": len(event_ids) == 201,
        "five_duplicates_and_min_group_retained": len(duplicate_groups) == 5,
        "event_flags_match_identity_rebuild": not source_event_mismatches,
        "low_vol_candidate_and_event_count_14": len(filter_ids) == 14 and len(filter_ids & event_ids) == 14,
        "entry01_trade_identities_match_feature_rebuild": entry01_ids == recomputed_ids,
        "every_dropped_candidate_non_low_vol": all(not x["dropped_low_vol_flag"] for x in duplicate_rows),
        "206_row_C_accounting_reconciles": max(row_accounting) <= TOL,
        "loo_14_no_reversal": len(loo_rows) == 14 and not any(r["reversed_positive"] for r in loo_rows),
        "loo_matches_entry01_prior_artifact": all(r["matches_within_1e_9"] for r in loo_comparisons),
        "source_code_matches_frozen_lowvol_rule": feature_code_audit["all_checks_pass"],
        "holding_exit_branch_not_used_in_calculation": True,
    }
    assert all(checks.values()), f"驗收断言有失败: {checks}"

    result = {
        "task": "ENTRY-VOLUME-02",
        "calculation_source": "完整配对逐笔母表 + 逐信号因子布尔标记；不读取汇总 JSON 作为计算源",
        "event_key": "(symbol, entry_time_utc)",
        "retention_rule": "每组 int(group) 最小者；不看盈亏",
        "filter_rule_from_executed_feature": "prev_1h_low_vol_drop（已闭合上一根 1h：close < open 且 volume < 前置均量）",
        "candidate_summary_recomputed": stats_candidate,
        "event_summary_recomputed": stats_events,
        "period_rows_recomputed": period_rows,
        "leave_one_out_summary_recomputed": {
            "iterations": len(loo_rows), "min_remaining_delta": min(x["remaining_net_delta"] for x in loo_rows),
            "max_remaining_delta": max(x["remaining_net_delta"] for x in loo_rows),
            "positive_reversal_count": sum(bool(x["reversed_positive"]) for x in loo_rows),
        },
        "posthoc_previous_artifact_comparisons": {
            "entry01_summary": summary_comparison,
            "entry01_leave_one_out": loo_comparisons,
            "period_json": period_json_comparison,
            "legacy_vbe01_deltas": legacy_deltas,
            "vbe02_report_prose_claims": prose_diffs,
        },
        "duplicate_pairs_crosscheck": duplicate_json_check,
        "feature_code_audit": feature_code_audit,
        "checks": checks,
        "source_hashes_start": start_sigs,
    }

    # Canonical summaries are rewritten from the independent full-table calculation above.
    mapping_summary = {
        "calculation_source": "paired_trades_1r.csv identity keys + group numeric sort; duplicate_groups.json only crosschecked afterwards",
        "candidate_total_count": len(candidates), "dedup_event_total_count": len(event_ids),
        "dropped_signals_count": sum(len(x[2]) for x in duplicate_groups),
        "retention_rule": "(symbol, entry_time_utc) group; retain int(group) minimum; no PnL-based choice",
        "low_vol_count_in_206_candidates": len(filter_ids),
        "low_vol_count_in_201_events": len(filter_ids & event_ids),
        "low_vol_signal_ids_identical": filter_ids == (filter_ids & event_ids),
        "dropped_pairs": duplicate_rows,
    }
    (OUT / "mapping_206_to_201.json").write_text(json.dumps(mapping_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    metric_summary = {
        "calculation_source": "206-row paired table; event result independently filtered after dedup",
        "candidate_level": stats_candidate,
        "event_level": stats_events,
        "reconciles_saved_minus_forgone_to_delta": abs(stats_events["saved_loss"] - stats_events["forgone_profit_magnitude"] - stats_events["net_delta"]) <= TOL,
        "previous_entry01_total_crosscheck": summary_comparison,
    }
    (OUT / "recalculated_metrics.json").write_text(json.dumps(metric_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    loo_summary = {
        "calculation_source": "14 filtered rows independently derived from 206-row paired table and feature flags",
        "total_loo_iterations": len(loo_rows),
        "min_remaining_delta": min(x["remaining_net_delta"] for x in loo_rows),
        "max_remaining_delta": max(x["remaining_net_delta"] for x in loo_rows),
        "any_reversed_to_positive": any(x["reversed_positive"] for x in loo_rows),
        "entry01_loo_crosscheck": loo_comparisons,
        "details": loo_rows,
    }
    (OUT / "recalculated_leave_one_out.json").write_text(json.dumps(loo_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    discrepancy_summary = {
        "period_boundaries_changed": False,
        "period_definition": "entry_time_utc Gregorian calendar year: <=2023, ==2024, >=2025",
        "authoritative_independent_period_rows": period_rows,
        "matches_entry01_and_vbe02_generated_json": period_json_comparison,
        "vbe02_report_prose_claims_vs_independent_rows": prose_diffs,
        "legacy_vbe01_json_period_deltas": legacy_deltas,
        "root_cause": {
            "vbe02_json_vs_vbe02_report": "Code-generated VOLUME-BE-02 and ENTRY-VOLUME-01 JSON values match independent per-row recalculation. VOLUME-BE-02 report prose contains different period deltas and misallocates two event counts; exact drafting origin of those prose numbers is not evidenced by the files reviewed.",
            "vbe01_vs_vbe02": "Same calendar-year buckets; VOLUME-BE-01 tested the earlier high-volume-drop hypothesis and its data path had the unfinished-hour lookahead later corrected in VOLUME-BE-02. It is not the same rule/sample definition as the current low-volume-drop study.",
            "candidate_vs_event_counts": "5 duplicates are distributed 3 in 2021-2023, 1 in 2024, 1 in 2025-2026; correct event counts are 66/38/97, not the VOLUME-BE-02 report prose 67/38/96.",
        },
    }
    (OUT / "subperiod_audit_and_reconciliation.json").write_text(json.dumps(discrepancy_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    freeze_summary = {
        "rule_source": "VOLUME-BE-02/code/vbe2_core.py::extract_entry_features + 02_run_abcd_fixed.py",
        "actual_rule": "close < open AND volume < mean of up to prior 20 completed 1h volumes (current bar excluded); code falls back to current prior-bar volume when fewer than 5 average bars exist; only close_time <= entry_ms bars",
        "close_below_entry_price_part_of_actual_flag": False,
        "entry01_report_extra_close_below_entry_price_text": True,
        "actual_filter_wired_from_prev_1h_low_vol_drop": True,
        "filtered_trade_status_net_rule": "FILTERED_NOT_ENTERED => C_net=0; otherwise C_net=A_net",
        "candidate_selection": "all 206 rows; no PnL cut; dedup uses earliest numeric group only",
        "holding_exit_branch_in_main_filter_calculation": False,
        "feature_code_checks": feature_code_audit["checks"],
        "verdict": "PASS_DATA_RULE_WITH_DOCUMENTATION_NOTE: executed rule/threshold unchanged; ENTRY-VOLUME-01 prose has an unsupported extra condition and must not be read as a calculation criterion.",
    }
    (OUT / "freeze_rule_verification.json").write_text(json.dumps(freeze_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "independent_recalculation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "source_hashes_start.json").write_text(json.dumps(start_sigs, ensure_ascii=False, indent=2), encoding="utf-8")

    # Re-hash source files at end to prove upstreams did not change during this audit.
    end_sigs = {str(p): file_sig(p) for p in SOURCE_FILES}
    stable = all(start_sigs[k]["sha256"] == end_sigs[k]["sha256"] for k in start_sigs)
    (OUT / "source_hashes_end.json").write_text(json.dumps(end_sigs, ensure_ascii=False, indent=2), encoding="utf-8")
    assert stable, "上游文件审计过程中发生哈希变化；本轮结果无效"
    (OUT / "source_stability.json").write_text(json.dumps({
        "all_source_files_stable": stable,
        "files_checked": len(start_sigs),
        "changed": [],
        "note": "审计开始/结束对照同一组上游数据、代码与文档 SHA-256；全部相同。"
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # Human-readable terminal log summary; detailed rows are in out/*.csv.
    print("===== ENTRY-VOLUME-02：全量逐笔独立验收 =====")
    print(f"样本映射：候选 {len(candidates)} → 事件 {len(event_ids)}；二重重复组 {len(duplicate_groups)}；保留最小 group")
    print(f"缩量命中：候选 {len(filter_ids)} / 206；事件 {len(filter_ids & event_ids)} / 201；两口径 ID 相同={entry01_ids == recomputed_ids}")
    print(f"事件级过滤贡献：少亏 +{stats_events['saved_loss']:.4f} U；错失盈利 -{stats_events['forgone_profit_impact']*-1:.4f} U；净差额 {stats_events['net_delta']:+.4f} U")
    print(f"逐一移除：{len(loo_rows)} 次；剩余净差额 [{min(x['remaining_net_delta'] for x in loo_rows):+.4f}, {max(x['remaining_net_delta'] for x in loo_rows):+.4f}] U；反转 {sum(bool(x['reversed_positive']) for x in loo_rows)} 次")
    print("分期边界：入场 UTC 公历年 <=2023 / ==2024 / >=2025（未改）")
    print(f"上一版报告分期 prose 与源逐笔/JSON 有出入；明细写入 {OUT / 'independent_recalculation.json'}")
    print(f"冻结规则代码核对={feature_code_audit['all_checks_pass']}；文档额外 close<entry_price 条件不在实际因子实现中，作为文档差异揭示")
    print(f"上游文件审计期间稳定：{stable}，共 {len(start_sigs)} 个")
    print("===== 独立复算完成：请结合报告中的文档差异提示阅读 =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
