#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BREAKEVEN-ROBUST-02：从配对逐笔CSV独立复算保本贡献并溯源总额差异。

所有盈亏和贡献只从 paired_trades CSV 的 A_net/B_net/C_net 列读取；
上游汇总 JSON 仅作审计对照，不作为任何金额的计算来源。
不读取、不计算持仓缩量退出费用分支。
"""
from __future__ import annotations

import csv
import hashlib
import json
import platform
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence/BREAKEVEN-ROBUST-02/out"
SCRIPT_PATH = Path(__file__).resolve()
TOL = Decimal("0.000000001")

PRIMARY = "evidence/BREAKEVEN-01/out/paired_trades.csv"
BE02 = "evidence/BREAKEVEN-02/out/paired_trades.csv"
VOLUME_1R = "evidence/VOLUME-BE-02/out/paired_trades_1r.csv"
HASH_PATHS = [
    PRIMARY,
    "evidence/BREAKEVEN-01/code/06_dedup_sensitivity.py",
    "evidence/BREAKEVEN-01/out/dedup_sensitivity.json",
    "evidence/BREAKEVEN-01/out/versions.json",
    "evidence/BREAKEVEN-ROBUST-01/code/01_contribution.py",
    "evidence/BREAKEVEN-ROBUST-01/out/contribution_summary.json",
    "evidence/BREAKEVEN-ROBUST-01/out/versions.json",
    "evidence/BREAKEVEN-ROBUST-01/报告.md",
    BE02,
    "evidence/BREAKEVEN-02/out/paired_fixed.csv",
    "evidence/BREAKEVEN-02/code/06_dedup_sensitivity.py",
    "evidence/BREAKEVEN-02/code/02_run_abc.py",
    "evidence/BREAKEVEN-02/code/be_core.py",
    "evidence/BREAKEVEN-02/out/dedup_sensitivity.json",
    "evidence/BREAKEVEN-02/out/versions.json",
    "evidence/BREAKEVEN-02/00_样本与规则契约.md",
    VOLUME_1R,
    "evidence/VOLUME-BE-02/out/summary_1r.json",
    "evidence/VOLUME-BE-02/code/02_run_abcd_fixed.py",
    "evidence/VOLUME-BE-02/out/versions.json",
    "evidence/VOLUME-BE-02/报告.md",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_fingerprint(relative: str) -> dict:
    path = ROOT / relative
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def read_csv(relative: str) -> list[dict[str, str]]:
    with (ROOT / relative).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def dec(row: dict[str, str], field: str) -> Decimal:
    return Decimal(row[field])


def event_key(row: dict[str, str]) -> tuple[str, str]:
    return row["symbol"], row["entry_time_utc"]


def deduplicate_run_idx(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[event_key(row)].append(row)
    kept, dropped = [], []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda row: int(row["run_idx"]))
        kept.append(members[0])
        dropped.extend(members[1:])
    return kept, dropped


def deduplicate_flag(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("is_event_dedup", "").strip().lower() in {"true", "1"}]


def totals(rows: list[dict[str, str]]) -> dict[str, Decimal]:
    return {field: sum((dec(row, field) for row in rows), Decimal("0")) for field in ("A_net", "B_net", "C_net")}


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def text_decimal(value: Decimal) -> str:
    return format(value, "f")


def main() -> int:
    # 上游稳定性审计起点：在解析任何逐笔数值前先做快照。
    started = utc_now()
    script_relative = SCRIPT_PATH.relative_to(ROOT).as_posix()
    script_start = file_fingerprint(script_relative)
    start_hashes = {path: file_fingerprint(path) for path in HASH_PATHS}

    primary_candidates = read_csv(PRIMARY)
    primary_events, dropped = deduplicate_run_idx(primary_candidates)
    if len(primary_candidates) != 206 or len({r["signal_id"] for r in primary_candidates}) != 206:
        raise AssertionError("主输入须为 206 行且 signal_id 唯一")
    if len(primary_events) != 201 or len(dropped) != 5:
        raise AssertionError("主输入事件级去重数量应为 201，剔重 5 行")

    be02_candidates = read_csv(BE02)
    be02_events, be02_dropped = deduplicate_run_idx(be02_candidates)
    volume_candidates = read_csv(VOLUME_1R)
    volume_events = deduplicate_flag(volume_candidates)
    if len(be02_candidates) != 206 or len(be02_events) != 201 or len(be02_dropped) != 5:
        raise AssertionError("BREAKEVEN-02 对照源的候选/事件数量不符")
    if len(volume_candidates) != 206 or len(volume_events) != 201:
        raise AssertionError("VOLUME-BE-02 对照源须有 206 候选、201 个 is_event_dedup 事件")

    primary_totals = totals(primary_events)
    be02_totals = totals(be02_events)
    volume_totals = {field: sum((dec(row, field) for row in volume_events), Decimal("0"))
                     for field in ("A_net", "B_net")}
    if set(map(event_key, primary_events)) != set(map(event_key, be02_events)):
        raise AssertionError("BREAKEVEN-01 与 BREAKEVEN-02 事件身份集合不一致")
    if set(map(event_key, be02_events)) != set(map(event_key, volume_events)):
        raise AssertionError("BREAKEVEN-02 与 VOLUME-BE-02 事件身份集合不一致")

    # 全体 201 个事件逐笔表：净额和贡献均由 primary paired_trades.csv 重算。
    contribution_rows = []
    for row in sorted(primary_events, key=lambda item: (item["entry_time_utc"], item["symbol"])):
        a, b, c = (dec(row, field) for field in ("A_net", "B_net", "C_net"))
        db, dc = b - a, c - a
        contribution_rows.append({
            "symbol": row["symbol"], "entry_time_utc": row["entry_time_utc"],
            "run_idx": row["run_idx"], "signal_id": row["signal_id"],
            "A_net_U": text_decimal(a), "B_1R_net_U": text_decimal(b), "C_2R_net_U": text_decimal(c),
            "B_minus_A_U": text_decimal(db), "B变化": "改善" if db > TOL else "恶化" if db < -TOL else "不变",
            "C_minus_A_U": text_decimal(dc), "C变化": "改善" if dc > TOL else "恶化" if dc < -TOL else "不变",
        })
    write_csv(OUT / "逐笔贡献与变化.csv", list(contribution_rows[0]), contribution_rows)

    b_deltas = [(dec(row, "B_net") - dec(row, "A_net"), row) for row in primary_events]
    c_deltas = [(dec(row, "C_net") - dec(row, "A_net"), row) for row in primary_events]
    b_changed = [(delta, row) for delta, row in b_deltas if abs(delta) > TOL]
    b_positive = [(delta, row) for delta, row in b_changed if delta > TOL]
    b_negative = [(delta, row) for delta, row in b_changed if delta < -TOL]
    c_changed = [(delta, row) for delta, row in c_deltas if abs(delta) > TOL]
    if (len(b_changed), len(b_positive), len(b_negative), len(c_changed)) != (28, 12, 16, 1):
        raise AssertionError("变化笔数与预期不符：需核查输入/口径，不自动改样本")

    b_delta = primary_totals["B_net"] - primary_totals["A_net"]
    c_delta = primary_totals["C_net"] - primary_totals["A_net"]
    b_saved = sum((delta for delta, _ in b_positive), Decimal("0"))
    b_cost = sum((-delta for delta, _ in b_negative), Decimal("0"))
    c_saved = sum((delta for delta, _ in c_changed if delta > TOL), Decimal("0"))
    c_cost = sum((-delta for delta, _ in c_changed if delta < -TOL), Decimal("0"))
    if abs((b_saved - b_cost) - b_delta) > Decimal("0.0000001"):
        raise AssertionError("B 少亏-代价与 B-A 未对平")
    if abs((c_saved - c_cost) - c_delta) > Decimal("0.0000001"):
        raise AssertionError("C 少亏-代价与 C-A 未对平")

    # 最大正/负贡献移除及逐笔 leave-one-out；只做事后敏感性分析。
    max_positive = max(b_positive, key=lambda item: item[0])
    max_negative = min(b_negative, key=lambda item: item[0])
    removal_rows = [{"情景": "基线", "移除事件": "", "B_minus_A_after_U": text_decimal(b_delta)}]
    for label, item in (("移除B最大正贡献", max_positive), ("移除B最大负贡献", max_negative)):
        removal_rows.append({"情景": label, "移除事件": f"{item[1]['symbol']}@{item[1]['entry_time_utc']}",
                             "B_minus_A_after_U": text_decimal(b_delta - item[0])})
    write_csv(OUT / "B贡献移除敏感性.csv", ["情景", "移除事件", "B_minus_A_after_U"], removal_rows)

    jackknife_rows = []
    sign_flips = []
    for delta, row in sorted(b_changed, key=lambda item: (item[1]["entry_time_utc"], item[1]["symbol"])):
        after = b_delta - delta
        flipped = (b_delta < 0 < after) or (b_delta > 0 > after)
        if flipped:
            sign_flips.append(row)
        jackknife_rows.append({
            "symbol": row["symbol"], "entry_time_utc": row["entry_time_utc"],
            "B_minus_A_removed_trade_U": text_decimal(delta),
            "B_minus_A_after_removal_U": text_decimal(after), "相对基线变号": str(flipped).lower(),
        })
    write_csv(OUT / "逐笔移除变号复核.csv", list(jackknife_rows[0]), jackknife_rows)

    # C 组唯一增益逐笔身份与净额，以及移除后的差额。
    c_gain = [(delta, row) for delta, row in c_changed if delta > TOL]
    if len(c_gain) != 1 or len(c_changed) != 1:
        raise AssertionError("C 必须恰有一笔相对 A 净额变化且为正")
    c_gain_delta, c_gain_row = c_gain[0]
    c_after_removal = c_delta - c_gain_delta
    write_csv(OUT / "C唯一增益交易.csv",
              ["symbol", "entry_time_utc", "A_net_U", "C_2R_net_U", "C_minus_A_U", "移除后C_minus_A_U"],
              [{"symbol": c_gain_row["symbol"], "entry_time_utc": c_gain_row["entry_time_utc"],
                "A_net_U": c_gain_row["A_net"], "C_2R_net_U": c_gain_row["C_net"],
                "C_minus_A_U": text_decimal(c_gain_delta), "移除后C_minus_A_U": text_decimal(c_after_removal)}])

    # 对照同事件键的两个上游 paired 数据版本，不引用任何汇总值计算差额。
    source_maps = {
        "BREAKEVEN-01 paired_trades.csv": {event_key(row): row for row in primary_events},
        "BREAKEVEN-02 paired_trades.csv": {event_key(row): row for row in be02_events},
        "VOLUME-BE-02 paired_trades_1r.csv (is_event_dedup=true)": {event_key(row): row for row in volume_events},
    }
    diff_rows = []
    selected_id_mismatch_be02 = 0
    selected_id_mismatch_volume = 0
    for key in sorted(source_maps["BREAKEVEN-01 paired_trades.csv"]):
        first = source_maps["BREAKEVEN-01 paired_trades.csv"][key]
        second = source_maps["BREAKEVEN-02 paired_trades.csv"][key]
        third = source_maps["VOLUME-BE-02 paired_trades_1r.csv (is_event_dedup=true)"][key]
        selected_id_mismatch_be02 += first.get("signal_id", "") != second.get("signal_id", "")
        selected_id_mismatch_volume += first.get("signal_id", "") != third.get("signal_id", "")
        be02_diffs = {field: dec(second, field) - dec(first, field) for field in ("A_net", "B_net", "C_net")}
        volume_diffs = {field: dec(third, field) - dec(first, field) for field in ("A_net", "B_net")}
        if any(value != 0 for value in be02_diffs.values()) or first.get("signal_id", "") != second.get("signal_id", ""):
            diff_rows.append({
                "symbol": key[0], "entry_time_utc": key[1],
                "BREAKEVEN-01_signal_id": first.get("signal_id", ""),
                "BREAKEVEN-02_signal_id": second.get("signal_id", ""),
                "VOLUME-BE-02_signal_id": third.get("signal_id", ""),
                "A_BE01_U": text_decimal(dec(first, "A_net")), "B_BE01_U": text_decimal(dec(first, "B_net")), "C_BE01_U": text_decimal(dec(first, "C_net")),
                "A_BE02_U": text_decimal(dec(second, "A_net")), "B_BE02_U": text_decimal(dec(second, "B_net")), "C_BE02_U": text_decimal(dec(second, "C_net")),
                "B_delta_BE02_minus_BE01_U": text_decimal(be02_diffs["B_net"]),
                "B_delta_VOLUME_minus_BE01_U": text_decimal(volume_diffs["B_net"]),
                "B_precision_VOLUME_minus_BE02_U": text_decimal(dec(third, "B_net") - dec(second, "B_net")),
                "A_delta_BE02_minus_BE01_U": text_decimal(be02_diffs["A_net"]),
                "C_delta_BE02_minus_BE01_U": text_decimal(be02_diffs["C_net"]),
            })
    write_csv(OUT / "来源事件差异.csv",
              ["symbol", "entry_time_utc", "BREAKEVEN-01_signal_id", "BREAKEVEN-02_signal_id", "VOLUME-BE-02_signal_id",
               "A_BE01_U", "B_BE01_U", "C_BE01_U", "A_BE02_U", "B_BE02_U", "C_BE02_U",
               "B_delta_BE02_minus_BE01_U", "B_delta_VOLUME_minus_BE01_U", "B_precision_VOLUME_minus_BE02_U",
               "A_delta_BE02_minus_BE01_U", "C_delta_BE02_minus_BE01_U"], diff_rows)

    # 数值只计算自上述三份配对 CSV；在计算之后再读取已公布值用于对照。
    comparison_rows = []
    source_info = [
        ("BREAKEVEN-01 paired_trades.csv / run_idx最小事件", PRIMARY, primary_events, primary_totals, "独立复算主口径", "C=2R保本"),
        ("BREAKEVEN-02 paired_trades.csv / run_idx最小事件", BE02, be02_events, be02_totals, "独立复算另一版本", "C=2R保本"),
        ("VOLUME-BE-02 paired_trades_1r.csv / is_event_dedup=true", VOLUME_1R, volume_events, volume_totals, "独立复算衍生逐笔表；仅用A/B列", "C列是缩量过滤，不等于2R；不纳入比较"),
    ]
    for label, path, rows, sums, role, third_group_semantics in source_info:
        comparison_rows.append({
            "对象": label, "路径": path, "方法/角色": role, "n": len(rows),
            "A_net_U": text_decimal(sums["A_net"]), "B_net_U": text_decimal(sums["B_net"]),
            "第三组净额_U": text_decimal(sums["C_net"]) if "C_net" in sums else "",
            "第三组口径": third_group_semantics,
            "B_minus_A_U": text_decimal(sums["B_net"] - sums["A_net"]),
            "B_sha256": start_hashes[path]["sha256"],
        })
    write_csv(OUT / "52.4299_vs_52.5423对照.csv",
              ["对象", "路径", "方法/角色", "n", "A_net_U", "B_net_U", "第三组净额_U", "第三组口径", "B_minus_A_U", "B_sha256"],
              comparison_rows)

    # 最后再次对上游输入/代码/对照文件做哈希快照。
    ended = utc_now()
    end_hashes = {path: file_fingerprint(path) for path in HASH_PATHS}
    script_end = file_fingerprint(script_relative)
    upstream_stable = all(start_hashes[path] == end_hashes[path] for path in HASH_PATHS)
    script_stable = script_start == script_end
    stable = upstream_stable and script_stable
    fingerprint = {
        "audit_started_utc": started, "audit_finished_utc": ended,
        "python": platform.python_version(), "calculation_source": "BREAKEVEN-01 paired_trades.csv 原始 A_net/B_net/C_net 逐笔列；Decimal十进制累加",
        "reference_json_role": "只用于已有报告/版本的对照，不提供任何贡献计算输入",
        "script": {"path": script_relative, "start": script_start, "end": script_end, "stable": script_stable},
        "upstream_files": {path: {"start": start_hashes[path], "end": end_hashes[path], "stable": start_hashes[path] == end_hashes[path]} for path in HASH_PATHS},
        "all_upstream_hashes_stable": upstream_stable,
    }
    (OUT / "输入版本指纹.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "n_candidates": len(primary_candidates), "n_events": len(primary_events), "n_dropped_duplicates": len(dropped),
        "dedup_key": ["symbol", "entry_time_utc"], "retention_rule": "同键按 run_idx 数值升序保留最小者；不按收益挑选",
        "A_net_U": text_decimal(primary_totals["A_net"]), "B_1R_net_U": text_decimal(primary_totals["B_net"]),
        "C_2R_net_U": text_decimal(primary_totals["C_net"]), "B_minus_A_U": text_decimal(b_delta),
        "C_minus_A_U": text_decimal(c_delta), "B_changed_n": len(b_changed), "B_improved_n": len(b_positive),
        "B_worsened_n": len(b_negative), "B_unchanged_n": len(primary_events) - len(b_changed),
        "B_improvement_sum_U": text_decimal(b_saved), "B_deterioration_sum_U": text_decimal(-b_cost),
        "B_net_contribution_sum_U": text_decimal(b_saved - b_cost),
        "B_max_positive": {"symbol": max_positive[1]["symbol"], "entry_time_utc": max_positive[1]["entry_time_utc"], "delta_U": text_decimal(max_positive[0])},
        "B_max_negative": {"symbol": max_negative[1]["symbol"], "entry_time_utc": max_negative[1]["entry_time_utc"], "delta_U": text_decimal(max_negative[0])},
        "B_positive_top1_share_pct": text_decimal((max_positive[0] / b_saved * Decimal("100")).quantize(Decimal("0.01"))),
        "B_negative_top1_share_pct": text_decimal(((-max_negative[0]) / b_cost * Decimal("100")).quantize(Decimal("0.01"))),
        "B_minus_A_after_remove_max_positive_U": text_decimal(b_delta - max_positive[0]),
        "B_minus_A_after_remove_max_negative_U": text_decimal(b_delta - max_negative[0]),
        "B_jackknife_changed_trades_n": len(jackknife_rows), "B_jackknife_sign_flip_n": len(sign_flips),
        "B_jackknife_sign_flip_events": [f"{r['symbol']}@{r['entry_time_utc']}" for r in sign_flips],
        "C_changed_n": len(c_changed), "C_unique_gain": {"symbol": c_gain_row["symbol"], "entry_time_utc": c_gain_row["entry_time_utc"],
            "A_net_U": c_gain_row["A_net"], "C_net_U": c_gain_row["C_net"], "contribution_U": text_decimal(c_gain_delta),
            "C_minus_A_after_removal_U": text_decimal(c_after_removal)},
        "source_event_difference_rows": len(diff_rows),
        "selected_signal_id_mismatch_BE02_vs_BE01_n": selected_id_mismatch_be02,
        "selected_signal_id_mismatch_VOLUME_vs_BE01_n": selected_id_mismatch_volume,
        "source_event_difference_B_sum_BE02_minus_BE01_U": text_decimal(be02_totals["B_net"] - primary_totals["B_net"]),
        "source_event_difference_B_sum_VOLUME_minus_BE01_U": text_decimal(volume_totals["B_net"] - primary_totals["B_net"]),
        "volume_vs_BE02_B_csv_precision_delta_U": text_decimal(volume_totals["B_net"] - be02_totals["B_net"]),
        "volume_vs_BE02_B_rows_with_different_serialized_decimal": sum(
            dec(volume_sources, "B_net") != dec(be02_sources, "B_net")
            for volume_sources, be02_sources in zip(
                sorted(volume_events, key=lambda row: event_key(row)),
                sorted(be02_events, key=lambda row: event_key(row)))
        ),
        "all_upstream_hashes_stable": stable,
    }
    (OUT / "independent_results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"开始UTC={started} 结束UTC={ended}")
    print(f"输入哈希稳定={stable}（{len(HASH_PATHS)}/{len(HASH_PATHS)} 文件逐字节稳定）")
    print(f"主输入 {PRIMARY}：206 候选 -> 201 事件，剔重 5；A={primary_totals['A_net']} B={primary_totals['B_net']} C={primary_totals['C_net']}")
    print(f"B-A={b_delta}；变化 {len(b_changed)} 笔：改善 {len(b_positive)} / 恶化 {len(b_negative)}；改善贡献 +{b_saved} / 恶化贡献 -{b_cost}")
    print(f"B正侧top1占比={(max_positive[0]/b_saved*100):.2f}% 负侧top1占比={((-max_negative[0])/b_cost*100):.2f}%")
    print(f"最大正贡献移除后 B-A={b_delta-max_positive[0]}；最大负贡献移除后 B-A={b_delta-max_negative[0]}；逐笔变号 {len(sign_flips)}/{len(jackknife_rows)}")
    print(f"C唯一增益={c_gain_row['symbol']}@{c_gain_row['entry_time_utc']}，A={c_gain_row['A_net']} C={c_gain_row['C_net']}，移除后 C-A={c_after_removal}")
    print(f"BE02 与 BE01 逐事件净额有差异的事件数={len(diff_rows)}；B总差={be02_totals['B_net']-primary_totals['B_net']}")
    print("结果由逐笔CSV独立重建；未读取/计算 pos_low_vol_exit 或 pos_exit_* 金额")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
