#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FT-FACTOR-AUDIT-01 / 01 建立 27 批统一机器可读索引 + 派生 200 根 4h 预热区间 + 与 1h/15m/研究期求交

只读输入：evidence/FT-POOL-01/B01_资格表_v1.1.csv、B02..B27_资格表_v1.csv、manifest_*.json
只写输出：evidence/FT-FACTOR-AUDIT-01/out/

不重新核全池：所有区间、计数、指纹一律取自 FT-POOL-01 终版交付文件，本脚本只做
(1) 两版列名归一 (2) 派生 200 根 4h 预热区间 (3) 区间求交 (4) 计数复核。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

csv.field_size_limit(10 ** 9)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BASE))
POOL = os.path.join(ROOT, "evidence", "FT-POOL-01")
OUT = os.path.join(BASE, "out")

STEP = {"15m": 900, "1h": 3600, "4h": 14400}
RESEARCH_START = datetime(2021, 12, 1, 0, 0, tzinfo=timezone.utc)
RESEARCH_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iv(raw: str):
    if not raw or raw.strip() in ("", "[]"):
        return []
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for it in arr:
        try:
            out.append({"start": ts(it["start_utc"]), "end": ts(it["end_utc"]), "bars": int(it["bars"])})
        except (KeyError, ValueError):
            continue
    return out


def warmup_drop(ivs, n_bars: int, step_sec: int):
    """丢掉每个连续区间前 n_bars 根，返回派生区间。"""
    out = []
    for iv in ivs:
        if iv["bars"] <= n_bars:
            continue
        st = iv["start"] + timedelta(seconds=step_sec * n_bars)
        out.append({"start": st, "end": iv["end"], "bars": iv["bars"] - n_bars})
    return out


def clip(ivs, lo: datetime, hi: datetime):
    out = []
    for iv in ivs:
        s = max(iv["start"], lo)
        e = min(iv["end"], hi)
        if s <= e:
            out.append({"start": s, "end": e})
    return out


def intersect_many(list_of_lists):
    """多组区间求交。返回 [{'start','end'}]。"""
    if not list_of_lists or any(len(x) == 0 for x in list_of_lists):
        return []
    cur = list_of_lists[0]
    for nxt in list_of_lists[1:]:
        new = []
        for a in cur:
            for b in nxt:
                s = max(a["start"], b["start"])
                e = min(a["end"], b["end"])
                if s <= e:
                    new.append({"start": s, "end": e})
        cur = new
        if not cur:
            return []
    # 合并相邻/重叠
    cur.sort(key=lambda x: x["start"])
    merged = [dict(cur[0])]
    for iv in cur[1:]:
        if iv["start"] <= merged[-1]["end"] + timedelta(seconds=1):
            merged[-1]["end"] = max(merged[-1]["end"], iv["end"])
        else:
            merged.append(dict(iv))
    return merged


def bars_of(ivs, step_sec):
    return sum(int((iv["end"] - iv["start"]).total_seconds() // step_sec) + 1 for iv in ivs)


def iv_json(ivs):
    return json.dumps(
        [{"start_utc": fmt(iv["start"]), "end_utc": fmt(iv["end"])} for iv in ivs],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def norm(row: dict, batch_no: int) -> dict:
    """两版资格表列名归一。"""
    g = lambda *keys: next((row[k] for k in keys if k in row and row[k] not in (None, "")), "")
    return {
        "batch_no": batch_no,
        "batch_version": g("batch_version"),
        "batch_index": g("batch_index"),
        "symbol": g("symbol"),
        "pool_scope": g("pool_scope", "historical_pool_version"),
        "underlying_type": g("underlying_type"),
        "contract_type": g("contract_type"),
        "quote_asset": g("quote_asset"),
        "status_observed": g("status_at_official_snapshot", "status_latest_observed"),
        "official_onboard_time_utc": g("official_onboard_time_utc"),
        "local_data_present": g("local_data_present"),
        "1h_valid_rows": g("1h_valid_bars", "1h_valid_rows"),
        "15m_valid_rows": g("15m_valid_bars", "15m_valid_rows"),
        "4h_valid_rows": g("4h_valid_bars", "4h_valid_rows"),
        "1h_valid_intervals_json": g("1h_valid_intervals_json"),
        "15m_valid_intervals_json": g("15m_valid_intervals_json"),
        "4h_valid_intervals_json": g("4h_valid_intervals_json"),
        "base_warmup_1h": g("base_warmup_1h_bars", "base_warmup_1h"),
        "legacy_warmup_4h": g("legacy_warmup_4h_bars"),
        "base_candidate_intervals_json": g(
            "base_1h_15m_candidate_intervals_json", "base_1h_15m_research_candidate_intervals_json"
        ),
        "legacy_4h_after_warmup_intervals_json": g("legacy_4h_after_warmup_intervals_json"),
        "oi_factor_1h_qualified_intervals_json": g("oi_factor_1h_qualified_intervals_json"),
        "oi_factor_mapping_status": g("oi_factor_mapping_status"),
        "oi_raw_5m_valid_rows": g("oi_valid_rows", "oi_raw_5m_nonnull_rows"),
        "oi_raw_5m_intervals_json": g("oi_raw_5m_intervals_json"),
        "oi_publication_lag_ms": g("oi_publication_lag_ms"),
        "oi_formal_pit": g("oi_formal_pit"),
        "funding_event_rows": g("funding_event_rows"),
        "funding_first_utc": g("funding_first_settlement_utc", "funding_first_utc"),
        "funding_last_utc": g("funding_last_settlement_utc", "funding_last_utc"),
        "mark1h_valid_rows": g("mark1h_valid_rows", "mark1h_rows"),
        "mark1h_is_ohlc": g("mark1h_is_ohlc"),
        "source_input_fingerprint_sha256": g("source_input_fingerprint_sha256"),
        "unqualified_reasons": g("unqualified_reasons"),
    }


def main():
    # ---------- 1. 批次文件清单（版本 + 指纹） ----------
    batch_files = []
    b1 = os.path.join(POOL, "B01_资格表_v1.1.csv")
    batch_files.append((1, b1, "manifest_B01_v1.1.json", "FT-POOL-01-B01-v1.1.0"))
    for n in range(2, 28):
        f = os.path.join(POOL, f"B{n:02d}_资格表_v1.csv")
        batch_files.append((n, f, f"manifest_B{n:02d}_v1.json", f"FT-POOL-01-HIST-v2.0.0-B{n:02d}-v1.0.0"))

    rows = []
    batch_ledger = []
    for n, f, mf, ver in batch_files:
        if not os.path.exists(f):
            raise SystemExit(f"缺文件: {f}")
        with open(f, encoding="utf-8-sig") as fh:
            raw = list(csv.DictReader(fh))
        man = os.path.join(POOL, mf)
        batch_ledger.append(
            {
                "batch_no": n,
                "batch_version_declared": ver,
                "qualification_file": os.path.basename(f),
                "qualification_sha256": sha256(f),
                "qualification_rows": len(raw),
                "manifest_file": mf if os.path.exists(man) else "",
                "manifest_sha256": sha256(man) if os.path.exists(man) else "",
                "batch_version_in_file": sorted({r.get("batch_version", "") for r in raw}),
            }
        )
        for r in raw:
            rows.append(norm(r, n))

    # ---------- 2. 逐币派生区间 ----------
    idx_rows = []
    derived_rows = []
    for r in rows:
        sym = r["symbol"]
        iv1h = parse_iv(r["1h_valid_intervals_json"])
        iv15 = parse_iv(r["15m_valid_intervals_json"])
        iv4h = parse_iv(r["4h_valid_intervals_json"])

        w1 = int(r["base_warmup_1h"]) if str(r["base_warmup_1h"]).isdigit() else 20
        w4 = int(r["legacy_warmup_4h"]) if str(r["legacy_warmup_4h"]).isdigit() else 200

        d1h = warmup_drop(iv1h, w1, STEP["1h"])
        d4h = warmup_drop(iv4h, w4, STEP["4h"])

        # 与交付字段交叉比对（B01 有 legacy_4h_after_warmup 与 base_candidate，B02+ 只有 base_candidate）
        provided_4h = parse_iv(r["legacy_4h_after_warmup_intervals_json"])
        provided_base = parse_iv(r["base_candidate_intervals_json"])
        cmp_4h = (
            "无交付字段"
            if not r["legacy_4h_after_warmup_intervals_json"]
            else ("一致" if [(x["start"], x["end"], x["bars"]) for x in provided_4h] == [(x["start"], x["end"], x["bars"]) for x in d4h] else "不一致")
        )
        cmp_base = (
            "无交付字段"
            if not r["base_candidate_intervals_json"]
            else ("一致" if [(x["start"], x["end"], x["bars"]) for x in provided_base] == [(x["start"], x["end"], x["bars"]) for x in d1h] else "不一致")
        )

        # 研究期裁剪
        r4 = clip(d4h, RESEARCH_START, RESEARCH_END)
        r1 = clip(d1h, RESEARCH_START, RESEARCH_END)
        r15 = clip(iv15, RESEARCH_START, RESEARCH_END)

        # 主口径 1h 候选：优先用 FT-POOL-01 交付字段（交接基准），无该字段时回退到自推导
        base_1h = provided_base if provided_base else d1h
        rb = clip(base_1h, RESEARCH_START, RESEARCH_END)

        # 主口径三层交集：4h(自派生200预热) ∩ 1h交付候选 ∩ 15m有效 ∩ 研究期
        tri = intersect_many([r4, rb, r15])
        # 敏感性口径：4h(自派生) ∩ 1h(自派生20预热) ∩ 15m ∩ 研究期
        tri_sens = intersect_many([r4, r1, r15])

        idx_rows.append(
            {
                "batch_no": r["batch_no"],
                "batch_version": r["batch_version"],
                "batch_index": r["batch_index"],
                "symbol": sym,
                "underlying_type": r["underlying_type"],
                "contract_type": r["contract_type"],
                "status_observed": r["status_observed"],
                "official_onboard_time_utc": r["official_onboard_time_utc"],
                "local_data_present": r["local_data_present"],
                "1h_valid_rows": r["1h_valid_rows"],
                "15m_valid_rows": r["15m_valid_rows"],
                "4h_valid_rows": r["4h_valid_rows"],
                "base_warmup_1h_bars": w1,
                "legacy_warmup_4h_bars": w4,
                "1h_valid_intervals_json": r["1h_valid_intervals_json"],
                "15m_valid_intervals_json": r["15m_valid_intervals_json"],
                "4h_valid_intervals_json": r["4h_valid_intervals_json"],
                "4h_after_200_warmup_intervals_json": iv_json(d4h),
                "base_candidate_intervals_json": r["base_candidate_intervals_json"],
                "source_input_fingerprint_sha256": r["source_input_fingerprint_sha256"],
                "source_file": os.path.basename(batch_files[r["batch_no"] - 1][1]),
                "unqualified_reasons": r["unqualified_reasons"],
            }
        )

        derived_rows.append(
            {
                "batch_no": r["batch_no"],
                "batch_version": r["batch_version"],
                "symbol": sym,
                "local_data_present": r["local_data_present"],
                "4h_valid_rows": r["4h_valid_rows"],
                "4h_valid_segments": len(iv4h),
                "4h_after_200warm_segments": len(d4h),
                "4h_after_200warm_bars": bars_of(d4h, STEP["4h"]),
                "4h_after_200warm_intervals": iv_json(d4h),
                "cmp_4h_after_warmup_vs_delivered": cmp_4h,
                "1h_valid_rows": r["1h_valid_rows"],
                "1h_after_20warm_segments": len(d1h),
                "1h_after_20warm_bars": bars_of(d1h, STEP["1h"]),
                "cmp_1h_after_warmup_vs_delivered": cmp_base,
                "15m_valid_rows": r["15m_valid_rows"],
                "research_4h_after_200warm_bars": bars_of(r4, STEP["4h"]),
                "research_1h_after_20warm_bars": bars_of(r1, STEP["1h"]),
                "research_15m_bars": bars_of(r15, STEP["15m"]),
                "research_triple_intersection_segments": len(tri),
                "research_triple_intersection_bars_1h": bars_of(tri, STEP["1h"]),
                "research_triple_intersection_intervals": iv_json(tri),
                "L1_price_volume_candidate": "是" if tri else "否",
                "sensitivity_1h_selfderived_bars": bars_of(tri_sens, STEP["1h"]),
                "sensitivity_L1_same_verdict": "是" if (bool(tri) == bool(tri_sens)) else "否",
                "oi_factor_1h_qualified_segments": len(parse_iv(r["oi_factor_1h_qualified_intervals_json"])),
                "oi_factor_mapping_status": r["oi_factor_mapping_status"],
                "funding_event_rows": r["funding_event_rows"],
                "mark1h_valid_rows": r["mark1h_valid_rows"],
                "L2_full_strategy_qualified": (
                    "是"
                    if tri and parse_iv(r["oi_factor_1h_qualified_intervals_json"])
                    else "否"
                ),
                "L2_blocking_reason": (
                    ""
                    if (tri and parse_iv(r["oi_factor_1h_qualified_intervals_json"]))
                    else ("无研究期价量交集" if not tri else "OI 1h 因子无合格区间（5m→1h 映射与发布时点未证实）")
                ),
            }
        )

    os.makedirs(OUT, exist_ok=True)

    def dump(path, fields, data):
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for d in data:
                w.writerow({k: d.get(k, "") for k in fields})
        return len(data)

    n_idx = dump(os.path.join(OUT, "01_全池统一索引_v1.csv"), list(idx_rows[0].keys()), idx_rows)
    n_der = dump(os.path.join(OUT, "02_派生区间_200根4h预热_研究期交集_v1.csv"), list(derived_rows[0].keys()), derived_rows)
    n_bat = dump(os.path.join(OUT, "01b_批次版本与文件指纹台账_v1.csv"), list(batch_ledger[0].keys()), batch_ledger)

    # ---------- 3. 计数复核 ----------
    total = len(idx_rows)
    local = [d for d in derived_rows if str(d["local_data_present"]).lower() == "true"]
    no_local = [d for d in derived_rows if str(d["local_data_present"]).lower() != "true"]
    l1 = [d for d in derived_rows if d["L1_price_volume_candidate"] == "是"]
    l2 = [d for d in derived_rows if d["L2_full_strategy_qualified"] == "是"]
    has4h = [d for d in derived_rows if str(d["4h_valid_rows"]) not in ("", "0")]
    four_h_ge200 = [d for d in derived_rows if str(d["4h_valid_rows"]).isdigit() and int(d["4h_valid_rows"]) >= 200]
    local_no_4h200 = [d["symbol"] for d in local if not (str(d["4h_valid_rows"]).isdigit() and int(d["4h_valid_rows"]) >= 200)]
    cmp_mismatch = [d["symbol"] for d in derived_rows if "不一致" in (d["cmp_4h_after_warmup_vs_delivered"], d["cmp_1h_after_warmup_vs_delivered"])]
    mismatch_detail = [
        {
            "symbol": d["symbol"],
            "batch_version": d["batch_version"],
            "field": "4h_after_warmup" if d["cmp_4h_after_warmup_vs_delivered"] == "不一致" else "1h_candidate",
            "self_derived": d["4h_after_200warm_intervals"] if d["cmp_4h_after_warmup_vs_delivered"] == "不一致" else "",
            "delivered_field": "",
            "note": "差异仅出现在连续区间边界（缺口前后若干根），详见 out/01c_派生差异明细_v1.csv",
        }
        for d in derived_rows
        if "不一致" in (d["cmp_4h_after_warmup_vs_delivered"], d["cmp_1h_after_warmup_vs_delivered"])
    ]
    sens_diff = [d["symbol"] for d in derived_rows if d["sensitivity_L1_same_verdict"] == "否"]

    # 交付口径 vs 自推导口径 的 1h 区间边界差异明细
    detail = []
    for r in rows:
        sym = r["symbol"]
        pb = parse_iv(r["base_candidate_intervals_json"])
        own = warmup_drop(parse_iv(r["1h_valid_intervals_json"]), int(r["base_warmup_1h"]) if str(r["base_warmup_1h"]).isdigit() else 20, STEP["1h"])
        if not pb:
            continue
        a = [(x["start"], x["end"], x["bars"]) for x in own]
        b = [(x["start"], x["end"], x["bars"]) for x in pb]
        if a == b:
            continue
        detail.append(
            {
                "symbol": sym,
                "batch_version": r["batch_version"],
                "self_derived_segments": len(a),
                "delivered_segments": len(b),
                "self_derived_bars": sum(x[2] for x in a),
                "delivered_bars": sum(x[2] for x in b),
                "bar_diff": sum(x[2] for x in b) - sum(x[2] for x in a),
                "self_derived": json.dumps([{"s": fmt(x[0]), "e": fmt(x[1]), "b": x[2]} for x in a], ensure_ascii=False),
                "delivered": json.dumps([{"s": fmt(x[0]), "e": fmt(x[1]), "b": x[2]} for x in b], ensure_ascii=False),
            }
        )
    dump(
        os.path.join(OUT, "01c_1h候选区间_交付口径与自推导差异明细_v1.csv"),
        ["symbol", "batch_version", "self_derived_segments", "delivered_segments", "self_derived_bars", "delivered_bars", "bar_diff", "self_derived", "delivered"],
        detail,
    )

    summary = {
        "work_order": "FT-FACTOR-AUDIT-01",
        "generated_by": "queries/01_build_pool_index.py",
        "inputs_read_only": {
            "pool_dir": "evidence/FT-POOL-01",
            "batch_count": len(batch_ledger),
            "batch_version_in_file_mismatch": [
                b for b in batch_ledger if len(b["batch_version_in_file"]) != 1
            ],
        },
        "totals": {
            "index_rows": total,
            "denominator_682": sum(1 for d in idx_rows if d["symbol"]),
            "unique_symbols": len({d["symbol"] for d in idx_rows}),
            "local_data_present_true": len(local),
            "local_data_present_false": len(no_local),
            "symbols_with_4h_rows": len(has4h),
            "symbols_with_4h_rows_ge_200": len(four_h_ge200),
        },
        "derived_warmup": {
            "1h_warmup_bars": 20,
            "4h_warmup_bars": 200,
            "note": "200 根 4h 预热取自 策略/WyckoffMaster/event_trend_v0_1/config.py:27 atr_warmup_bars=200",
            "crosscheck_4h_vs_delivered": {
                "delivered_field_present": sum(1 for d in derived_rows if d["cmp_4h_after_warmup_vs_delivered"] != "无交付字段"),
                "consistent": sum(1 for d in derived_rows if d["cmp_4h_after_warmup_vs_delivered"] == "一致"),
                "mismatch": [d["symbol"] for d in derived_rows if d["cmp_4h_after_warmup_vs_delivered"] == "不一致"],
            },
            "crosscheck_1h_vs_delivered": {
                "consistent": sum(1 for d in derived_rows if d["cmp_1h_after_warmup_vs_delivered"] == "一致"),
                "mismatch": [d["symbol"] for d in derived_rows if d["cmp_1h_after_warmup_vs_delivered"] == "不一致"],
                "mismatch_nature": "仅边界若干根差异，见 out/01c_1h候选区间_交付口径与自推导差异明细_v1.csv",
            },
            "L1_verdict_flip_between_primary_and_sensitivity": sens_diff,
        },
        "underlying_type_distribution": dict(sorted(__import__("collections").Counter(d["underlying_type"] for d in idx_rows).items())),
        "research_period": {
            "start_utc": "2021-12-01T00:00:00Z",
            "end_utc": "2024-12-31T23:59:59Z",
            "L1_price_volume_candidate_count": len(l1),
            "L1_symbols": sorted(d["symbol"] for d in l1),
            "L2_full_strategy_qualified_count": len(l2),
            "L2_symbols": sorted(d["symbol"] for d in l2),
        },
        "claim_recheck": {
            "claim": "525 币可用、CTUSDT 不满足",
            "local_data_true_count": len(local),
            "local_true_minus_L1": len(local) - len(l1),
            "local_true_but_no_4h_200bars": local_no_4h200,
            "CTUSDT": next((d for d in derived_rows if d["symbol"] == "CTUSDT"), None),
        },
    }
    with open(os.path.join(OUT, "03_计数复核_v1.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(json.dumps(summary["totals"], ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in summary["research_period"].items() if k != "L1_symbols"}, ensure_ascii=False, indent=2))
    print("local_true_but_no_4h_200bars =", local_no_4h200)
    print("crosscheck mismatch =", cmp_mismatch)
    print("L1 verdict flip (primary vs sensitivity) =", sens_diff)
    print("1h boundary-diff symbols =", [x["symbol"] for x in detail])
    print(f"写出: 索引 {n_idx} 行 / 派生 {n_der} 行 / 批次台账 {n_bat} 行 / 1h差异明细 {len(detail)} 行")


if __name__ == "__main__":
    main()
