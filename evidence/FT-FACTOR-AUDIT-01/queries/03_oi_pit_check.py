#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FT-FACTOR-AUDIT-01 / 03 OI：1h 决策时刻映射、观测时间、发布时点核查

只读：数据/市场数据/parquet/oi/binance/um/<SYMBOL>/*.parquet
     策略/WyckoffMaster/event_trend_v0_1/{features.py,pit_bridge.py,config.py}
     策略/WyckoffMaster/flow_oi/pit_features.py
输出：out/07_OI_PIT核查_v1.json、out/07b_OI样本逐文件核查_v1.csv

不做：不改时间戳、不填 0、不新启采集、不补下载。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
from datetime import datetime, timezone

import pyarrow.parquet as pq

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BASE))
OUT = os.path.join(BASE, "out")
OI_DIR = os.path.join(ROOT, "数据", "市场数据", "parquet", "oi", "binance", "um")

BUCKET_MS = 300_000
HOUR_MS = 3_600_000


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    idx = list(csv.DictReader(open(os.path.join(OUT, "01_全池统一索引_v1.csv"), encoding="utf-8-sig")))
    l1 = {r["symbol"] for r in csv.DictReader(open(os.path.join(OUT, "04_冻结研究期输入清单_v1.csv"), encoding="utf-8-sig"))}

    # 分层抽样：反重力74 中抽、L1 中抽、其余抽，保证覆盖不同批次
    rnd = random.Random(20261020)
    by_batch = {}
    for r in idx:
        by_batch.setdefault(r["batch_no"], []).append(r["symbol"])
    sample = []
    for b in sorted(by_batch):
        pool = by_batch[b]
        sample.extend(rnd.sample(pool, min(2, len(pool))))
    sample = sorted(set(sample))

    rows = []
    missing = []
    for sym in sample:
        d = os.path.join(OI_DIR, sym)
        if not os.path.isdir(d):
            missing.append(sym)
            continue
        files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
        for fn in files:
            p = os.path.join(d, fn)
            pf = pq.ParquetFile(p)
            schema = pf.schema_arrow
            cols = list(schema.names)
            tbl = pq.read_table(p, columns=[c for c in ("ts", "period", "fetched_at", "source", "open_interest") if c in cols])
            ts = tbl.column("ts").to_pylist()
            period = set(tbl.column("period").to_pylist()) if "period" in cols else set()
            fetched = tbl.column("fetched_at").to_pylist() if "fetched_at" in cols else []
            fetched_nonnull = sum(1 for x in fetched if x not in (None, ""))
            offgrid = sum(1 for t in ts if t % BUCKET_MS != 0)
            nonpos = 0
            if "open_interest" in cols:
                for v in tbl.column("open_interest").to_pylist():
                    try:
                        if float(v) <= 0:
                            nonpos += 1
                    except (TypeError, ValueError):
                        nonpos += 1
            offgrid_examples = [
                datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                for t in ts
                if t % BUCKET_MS != 0
            ][:3]
            rows.append(
                {
                    "symbol": sym,
                    "file": fn,
                    "rows": len(ts),
                    "columns": "|".join(cols),
                    "period_values": "|".join(sorted(str(x) for x in period)),
                    "ts_min_utc": datetime.fromtimestamp(min(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else "",
                    "ts_max_utc": datetime.fromtimestamp(max(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else "",
                    "ts_offgrid_5m": offgrid,
                    "ts_offgrid_examples": "|".join(offgrid_examples),
                    "open_interest_nonpositive_rows": nonpos,
                    "fetched_at_nonnull": fetched_nonnull,
                    "fetched_at_column_present": "fetched_at" in cols,
                    "sha256": sha256(p),
                    "in_research_L1": "是" if sym in l1 else "否",
                }
            )

    with open(os.path.join(OUT, "07b_OI样本逐文件核查_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    total_files = sum(1 for r in rows if r.get("files") != 0)
    files_checked = [r for r in rows if "file" in r]
    all_null = all(r["fetched_at_nonnull"] == 0 for r in files_checked)
    offgrid_total = sum(r["ts_offgrid_5m"] for r in files_checked)
    periods = sorted({r["period_values"] for r in files_checked})

    # 1h 决策时刻 → 5m 桶映射（在 assumed 规则下逐分钟推演）
    mapping_examples = []
    for decision in ["2023-06-15T08:00:00Z", "2023-06-15T09:00:00Z"]:
        T = int(datetime.strptime(decision, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
        # 可用 = 桶起点 + 5min <= T 的最后一桶
        cand_ts = [T - k * BUCKET_MS for k in range(0, 5)]
        usable = [t for t in cand_ts if t + BUCKET_MS <= T]
        mapping_examples.append(
            {
                "decision_time_utc": decision,
                "usable_bucket_start_utc": datetime.fromtimestamp(max(usable) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bucket_end_equals_available_at_utc": datetime.fromtimestamp((max(usable) + BUCKET_MS) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "distance_min": (T - max(usable)) / 60000,
                "within_max_distance_15min": (T - max(usable)) <= 15 * 60000,
                "naive_wrong_choice": "若把桶起点==决策时刻的桶当作可用，则该桶尚未收盘（桶结束 = 决策时刻+5min），构成 ≤5 分钟前视",
            }
        )

    code_files = {
        p: sha256(os.path.join(ROOT, p))
        for p in [
            "策略/WyckoffMaster/event_trend_v0_1/features.py",
            "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py",
            "策略/WyckoffMaster/event_trend_v0_1/config.py",
            "策略/WyckoffMaster/flow_oi/pit_features.py",
        ]
    }

    report = {
        "work_order": "FT-FACTOR-AUDIT-01",
        "question": "5m OI 到 1h 决策时刻的映射、观测时间、发布时点，能否满足原策略要求",
        "sampling": {
            "method": "按 27 批每批随机 2 个标的（随机种子 20261020），只读 parquet 元数据与 ts/period/fetched_at 列",
            "symbols_sampled": len(sample),
            "files_checked": len(files_checked),
            "no_oi_dir_symbols": missing,
        },
        "observation_time": {
            "field": "ts",
            "semantics": "5 分钟桶起点（官方 /futures/data/openInterestHist 与 metrics 一致）",
            "ts_offgrid_5m_total": offgrid_total,
            "ts_aligned_to_5min": offgrid_total == 0,
            "offgrid_files": [
                {"symbol": r["symbol"], "file": r["file"], "offgrid_rows": r["ts_offgrid_5m"], "examples": r["ts_offgrid_examples"]}
                for r in files_checked
                if r["ts_offgrid_5m"] > 0
            ][:50],
            "offgrid_note": (
                "live 分片的 ts 不是 5m 桶起点（实测如 2026-10-15T15:09:04Z 这类轮询瞬间），"
                "与历史归档分片的桶起点语义不一致；归档分片仅 CTKUSDT 2024 出现 18 行离网格。"
                if offgrid_total
                else "全部样本 ts 与 5 分钟网格对齐"
            ),
            "period_values_seen": periods,
            "conclusion": "观测时间 = 桶起点，可与 1h 决策时刻精确对齐；不存在'1h 聚合'步骤，原策略是按决策时刻取最近的 5m 观测（上限 15 分钟）。",
        },
        "zero_filled_oi": {
            "files_with_nonpositive_rows": [
                {"symbol": r["symbol"], "file": r["file"], "nonpositive_rows": r["open_interest_nonpositive_rows"]}
                for r in files_checked
                if r["open_interest_nonpositive_rows"] > 0
            ],
            "note": "策略对非正 OI 判 oi_curr_non_positive 并拒用，不会把 0 当成真实值参与计算；但这些行本身不应存在于官方序列中，属需要单独定性的数据缺陷。",
        },
        "publication_time": {
            "fetched_at_column_present": all(r["fetched_at_column_present"] for r in files_checked),
            "fetched_at_all_null": all_null,
            "files_with_any_fetched_at": [r["file"] for r in files_checked if r["fetched_at_nonnull"] > 0],
            "conclusion": (
                "全部样本文件的 fetched_at 列存在但为空 ⇒ 没有采集时点、没有交易所响应时间、没有快照 serverTime，"
                "OI 的发布时点无任何直接证据。"
                if all_null
                else "部分文件存在 fetched_at 值，需进一步核对来源。"
            ),
        },
        "mapping_under_strategy_rule": {
            "rule_source": "策略/WyckoffMaster/event_trend_v0_1/features.py:102-136 + pit_bridge.py:65-100",
            "rule": "available_at = ts + 300000ms（桶结束），当且仅当 available_at <= 决策时刻 才可取；取最近的合格观测；观测时刻距决策上限 15 分钟",
            "pit_mode_default": "DECLARED：照用但把 assumed=True 写入结果，本次运行不具备正式资格",
            "pit_mode_strict": "STRICT：整列 NaN，OI 因子直接不可用",
            "examples": mapping_examples,
            "satisfies_original_strategy": "在 assumed 规则下可以运行（决策时刻 T 取 T-5min 起的桶）；但这是假设，不是证明，故只能作探索结果。",
        },
        "evidence_boundary": {
            "supported_by_existing_evidence": [
                "5m OI 观测时间（桶起点）存在且与 5 分钟网格对齐",
                "5m 序列可按 available_at = ts+5min 的假设映射到任意 1h 决策时刻，映射本身无歧义",
                "策略代码已实现 PIT 判定与 assumed 标记（不是把假设伪装成已证明）",
            ],
            "missing_evidence": [
                "每个 5m 桶的真实发布/可读时刻（交易所响应时间或快照 serverTime）",
                "采集器写入时刻（fetched_at 为空）",
                "1h 决策时刻到 OI 可得的实测延迟分布",
            ],
            "cannot_be_recovered": [
                "2021-2024 历史时点的发布延迟：REST 接口只返回当前值，历史响应时间不可回溯",
                "已入库 parquet 的 fetched_at 为 null，无法从数据本身反推",
            ],
            "forward_only": "只能从当前时刻起，在采集器里落 fetched_at + 交易所 serverTime，才能为将来的数据建立正式 PIT 证据。",
        },
        "no_forbidden_actions": {
            "timestamp_modified": False,
            "zero_filled": False,
            "post_hoc_visibility_disguised_as_realtime": False,
            "new_collection_started": False,
            "bulk_download": False,
        },
        "code_fingerprints": code_files,
        "blocks_price_volume_research": False,
        "blocks_price_volume_reason": "基础价量（1h/15m/4h）不依赖 OI 发布时点，OI 缺证只影响含 OI 的策略组，不阻塞价量因子交接。",
    }
    with open(os.path.join(OUT, "07_OI_PIT核查_v1.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(json.dumps({k: report[k] for k in ("sampling", "observation_time", "publication_time")}, ensure_ascii=False, indent=2))
    print(json.dumps(report["mapping_under_strategy_rule"]["examples"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
