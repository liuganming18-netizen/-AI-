from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path

import pyarrow.parquet as pq

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = BASE / "evidence/FT-POOL-01"
O = OUT
PARQUET = BASE / "数据/市场数据/parquet"
R = PARQUET
MARK_ROOT = BASE / "数据/研究输入/WyckoffMaster/mark1h"
BATCH_NUMBER = int(os.environ.get("FT_POOL_BATCH_NO", "2"))
if BATCH_NUMBER < 2:
    raise ValueError("FT_POOL_BATCH_NO must be 2 or greater")
BATCH_PREFIX = "B%02d" % BATCH_NUMBER
VERSION = "FT-POOL-01-HIST-v2.0.0-%s-v1.0.0" % BATCH_PREFIX
STEPS = {"1h": 3_600_000, "15m": 900_000, "4h": 14_400_000, "5m": 300_000}
PRICE_FIELDS = ("open", "high", "low", "close", "volume")
METRIC_FIELDS = ("sum_open_interest", "sum_taker_long_short_vol_ratio")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def H(path: Path) -> str:
    return sha256(path)


def utc(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def finite(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def make_runs(times, step):
    values = sorted(set(times))
    result = []
    if not values:
        return result
    start = previous = values[0]
    count = 1
    for value in values[1:]:
        if value - previous == step:
            count += 1
        else:
            result.append({"start_ms": start, "end_ms": previous, "bars": count})
            start, count = value, 1
        previous = value
    result.append({"start_ms": start, "end_ms": previous, "bars": count})
    return result


def display_runs(runs):
    return [{"start_utc": utc(x["start_ms"]), "end_utc": utc(x["end_ms"]), "bars": x["bars"]} for x in runs]


def grid_stats(times, step):
    values = sorted(set(times))
    segments = missing = offgrid = 0
    for left, right in zip(values, values[1:]):
        delta = right - left
        if delta > step:
            gaps = max(0, delta // step - 1)
            if gaps:
                segments += 1
                missing += gaps
            if delta % step:
                offgrid += 1
        elif delta != step:
            offgrid += 1
    return {"gap_segments": segments, "missing_grid_slots": missing, "offgrid_steps": offgrid}


def read_candles(symbol, timeframe, catalog):
    folder = PARQUET / "candles/binance/um" / symbol / timeframe
    paths = sorted(folder.glob("*.parquet")) if folder.exists() else []
    rows = {}
    file_log = []
    nulls = {field: 0 for field in PRICE_FIELDS + ("quote_volume", "taker_buy_quote_volume")}
    step = STEPS[timeframe]
    needed = PRICE_FIELDS + ("quote_volume", "taker_buy_quote_volume", "open_time", "close_time", "is_closed")
    compared = PRICE_FIELDS + ("quote_volume", "taker_buy_quote_volume", "close_time", "is_closed")
    duplicates = conflicts = 0
    for path in paths:
        parquet = pq.ParquetFile(path)
        columns = set(parquet.schema_arrow.names)
        table = parquet.read(columns=[field for field in needed if field in columns])
        relative = path.relative_to(PARQUET).as_posix()
        actual = sha256(path)
        expected = catalog.get("shards", {}).get(relative, {}).get("sha256")
        file_log.append({
            "path": relative, "sha256_actual": actual, "sha256_catalog": expected,
            "catalog_match": expected == actual if expected else None,
            "bytes": path.stat().st_size, "rows": table.num_rows, "columns": sorted(columns),
        })
        for row in table.to_pylist():
            timestamp = row.get("open_time")
            if timestamp is None:
                continue
            timestamp = int(timestamp)
            if timestamp in rows:
                duplicates += 1
                if any(rows[timestamp].get(field) != row.get(field) for field in compared):
                    conflicts += 1
            else:
                rows[timestamp] = row

    valid = []
    execution = []
    taker_flow = []
    for timestamp, row in sorted(rows.items()):
        core_ok = (
            row.get("is_closed") == 1
            and row.get("close_time") == timestamp + step - 1
            and all(finite(row.get(field)) for field in PRICE_FIELDS)
        )
        if core_ok:
            op, high, low, close, volume = (float(row[x]) for x in PRICE_FIELDS)
            core_ok = min(op, high, low, close) > 0 and volume >= 0 and high >= max(op, close, low) and low <= min(op, close, high)
        for field in nulls:
            if not finite(row.get(field)):
                nulls[field] += 1
        if core_ok:
            valid.append(timestamp)
        if core_ok and finite(row.get("quote_volume")) and float(row["quote_volume"]) >= 0:
            execution.append(timestamp)
        if (
            core_ok and finite(row.get("quote_volume")) and float(row["quote_volume"]) > 0
            and finite(row.get("taker_buy_quote_volume"))
            and 0 <= float(row["taker_buy_quote_volume"]) <= float(row["quote_volume"]) * 1.0000001
        ):
            taker_flow.append(timestamp)
    return {
        "files": file_log, "rows": len(rows), "valid": valid, "execution": execution,
        "taker_flow": taker_flow, "nulls": nulls, "grid": grid_stats(rows, step),
        "duplicates": duplicates, "conflicts": conflicts,
    }


def read_metrics(symbol, catalog):
    folder = PARQUET / "metrics/binance/um" / symbol
    paths = sorted(folder.glob("*.parquet")) if folder.exists() else []
    rows = {}
    file_log = []
    duplicates = conflicts = 0
    for path in paths:
        parquet = pq.ParquetFile(path)
        columns = set(parquet.schema_arrow.names)
        table = parquet.read(columns=[field for field in ("ts",) + METRIC_FIELDS if field in columns])
        relative = path.relative_to(PARQUET).as_posix()
        actual = sha256(path)
        expected = catalog.get("shards", {}).get(relative, {}).get("sha256")
        file_log.append({
            "path": relative, "sha256_actual": actual, "sha256_catalog": expected,
            "catalog_match": expected == actual if expected else None,
            "bytes": path.stat().st_size, "rows": table.num_rows, "columns": sorted(columns),
        })
        for row in table.to_pylist():
            if row.get("ts") is None:
                continue
            timestamp = int(row["ts"])
            if timestamp in rows:
                duplicates += 1
                if any(rows[timestamp].get(field) != row.get(field) for field in METRIC_FIELDS):
                    conflicts += 1
            else:
                rows[timestamp] = row
    fields = {}
    for field in METRIC_FIELDS:
        times = [timestamp for timestamp, row in rows.items() if finite(row.get(field))]
        fields[field] = {
            "nonnull": len(times), "null": len(rows) - len(times),
            "intervals": display_runs(make_runs(times, STEPS["5m"])),
        }
    return {"files": file_log, "rows": len(rows), "fields": fields, "grid": grid_stats(rows, STEPS["5m"]), "duplicates": duplicates, "conflicts": conflicts}


def read_funding(symbol):
    path = PARQUET / "funding/binance/um" / (symbol + ".parquet")
    if not path.exists():
        return {"path": "", "rows": 0, "rate_null": 0, "mark_null": 0, "first": "", "last": "", "sha256": "", "status": "missing", "sources": {}}
    rows = pq.read_table(path, columns=["funding_time", "funding_rate", "mark_price", "source"]).to_pylist()
    times = [x["funding_time"] for x in rows if x.get("funding_time") is not None]
    return {
        "path": str(path), "rows": len(rows),
        "rate_null": sum(not finite(x.get("funding_rate")) for x in rows),
        "mark_null": sum(not finite(x.get("mark_price")) for x in rows),
        "first": utc(min(times)) if times else "", "last": utc(max(times)) if times else "",
        "sha256": sha256(path), "status": "read_ok",
        "sources": dict(collections.Counter(x.get("source") or "" for x in rows)),
    }


def read_mark(symbol):
    path = MARK_ROOT / (symbol + ".parquet")
    if not path.exists():
        return {"path": "", "rows": 0, "valid": 0, "missing_slots": 0, "intervals": [], "sha256": "", "status": "missing"}
    rows = pq.read_table(path, columns=["open_time", "mark_open"]).to_pylist()
    valid = [int(x["open_time"]) for x in rows if x.get("open_time") is not None and finite(x.get("mark_open")) and float(x["mark_open"]) > 0]
    return {
        "path": str(path), "rows": len(rows), "valid": len(valid),
        "missing_slots": grid_stats(valid, STEPS["1h"])["missing_grid_slots"],
        "intervals": display_runs(make_runs(valid, STEPS["1h"])),
        "sha256": sha256(path), "status": "read_ok",
    }


history = json.load(open(OUT / "04_历史范围汇总_v2.json", encoding="utf-8"))
pool_rows = list(csv.DictReader(open(OUT / "01_历史确认池_v2.csv", encoding="utf-8-sig")))
pool = {row["symbol"]: row for row in pool_rows}
b01_symbols = set(json.load(open(OUT / "manifest_B01_v1.json", encoding="utf-8"))["batch_symbols"])
remaining = [symbol for symbol in sorted(pool) if symbol not in b01_symbols]
batch_offset = (BATCH_NUMBER - 2) * 25
batch = remaining[batch_offset:batch_offset + 25]
if not batch:
    raise ValueError("No symbols remain for requested FT-POOL batch")
FIRST_BATCH_INDEX = 51 + batch_offset
catalog = json.load(open(PARQUET / "_catalog.json", encoding="utf-8"))
logs = []
rows_out = []
interval_rows = []
all_shards = []

def add_interval(symbol, batch_index, factor, runs, qualification, warmup, reason):
    for run in runs or [None]:
        start_utc = run.get("start_utc", utc(run["start_ms"]) if "start_ms" in run else "") if run else ""
        end_utc = run.get("end_utc", utc(run["end_ms"]) if "end_ms" in run else "") if run else ""
        interval_rows.append({
            "batch_version": VERSION, "batch_index": batch_index, "symbol": symbol,
            "factor_scope": factor, "qualification": qualification if run else "无区间/未确认",
            "start_utc": start_utc, "end_utc": end_utc, "bar_count": run["bars"] if run else 0,
            "time_semantics": "UTC桶起点，首尾包含" if run else "", "warmup": warmup,
            "formal_PIT_or_gate": "未确认", "reason": reason,
        })

for batch_index, symbol in enumerate(batch, FIRST_BATCH_INDEX):
    candles = {tf: read_candles(symbol, tf, catalog) for tf in ("1h", "15m", "4h")}
    metric = read_metrics(symbol, catalog)
    fund = read_funding(symbol)
    mark = read_mark(symbol)
    for tf, data in candles.items():
        all_shards.extend(data["files"])
        logs.append({
            "batch_version": VERSION, "symbol": symbol, "dataset": "candle_" + tf,
            "action": "read_existing_parquet_read_only", "files": data["files"],
            "rows_unique": data["rows"], "valid_rows": len(data["valid"]),
            "field_null_nonfinite": data["nulls"], "grid": data["grid"],
            "duplicates": data["duplicates"], "conflicting_duplicates": data["conflicts"],
            "valid_intervals": display_runs(make_runs(data["valid"], STEPS[tf])),
        })
    all_shards.extend(metric["files"])
    logs.append({
        "batch_version": VERSION, "symbol": symbol, "dataset": "metrics_5m",
        "action": "read_existing_parquet_read_only", "files": metric["files"],
            "rows_unique": metric["rows"], "fields": metric["fields"], "grid": metric["grid"],
            "duplicates": metric["duplicates"], "conflicting_duplicates": metric["conflicts"],
        })
    logs.append({"batch_version": VERSION, "symbol": symbol, "dataset": "funding_settlement_events", "action": "read_existing_parquet_read_only", **fund})
    logs.append({"batch_version": VERSION, "symbol": symbol, "dataset": "mark1h", "action": "read_existing_parquet_read_only", **mark})

    h1, m15, h4 = candles["1h"], candles["15m"], candles["4h"]
    warm_runs = []
    for run in make_runs(h1["valid"], STEPS["1h"]):
        start = run["start_ms"] + 20 * STEPS["1h"]
        if start <= run["end_ms"]:
            warm_runs.append({"start_ms": start, "end_ms": run["end_ms"], "bars": (run["end_ms"] - start) // STEPS["1h"] + 1})
    m15_exec_runs = [
        {"start_ms": run["start_ms"], "end_ms": run["end_ms"] + STEPS["15m"] - 1}
        for run in make_runs(m15["execution"], STEPS["15m"])
    ]
    m15_exec_times = set(m15["execution"])
    base_times = []
    for run in warm_runs:
        for t in range(run["start_ms"], run["end_ms"] + 1, STEPS["1h"]):
            if t + STEPS["1h"] in m15_exec_times:
                base_times.append(t)
    base_runs = make_runs(base_times, STEPS["1h"])
    taker_times_set = set(h1["taker_flow"])
    taker_times = [t for run in warm_runs for t in range(run["start_ms"], run["end_ms"] + 1, STEPS["1h"]) if t in taker_times_set]
    taker_runs = make_runs(taker_times, STEPS["1h"])
    old4_runs = []
    for run in make_runs(h4["valid"], STEPS["4h"]):
        start = run["start_ms"] + 200 * STEPS["4h"]
        if start <= run["end_ms"]:
            old4_runs.append({"start_ms": start, "end_ms": run["end_ms"], "bars": (run["end_ms"] - start) // STEPS["4h"] + 1})
    oi5 = metric["fields"]["sum_open_interest"]["intervals"]
    ratio5 = metric["fields"]["sum_taker_long_short_vol_ratio"]["intervals"]

    add_interval(symbol, batch_index, "基础价量：1h信号+15m执行", base_runs, "研究数据候选", "1h连续20根预热，随后需要15m执行K线", "K线available_at无发布时点证据；不等于正式PIT")
    add_interval(symbol, batch_index, "主动成交：K线1h原始覆盖", display_runs(make_runs(h1["taker_flow"], STEPS["1h"])), "字段有值区间", "无额外预热", "taker买入额有效且quote_volume>0；缺值不填0")
    add_interval(symbol, batch_index, "主动成交：与基础候选相交", taker_runs, "研究数据候选", "1h预热20根+15m执行", "仅数据覆盖候选")
    add_interval(symbol, batch_index, "OI共振1h因子资格", [], "未确认", "依策略1h输入和OI滚动窗口", "只确认5m原始数据；1h映射及OI发布时间未证，不宣称合格")
    for run in oi5:
        interval_rows.append({"batch_version": VERSION, "batch_index": batch_index, "symbol": symbol, "factor_scope": "OI原始sum_open_interest 5m覆盖", "qualification": "字段数据存在", "start_utc": run["start_utc"], "end_utc": run["end_utc"], "bar_count": run["bars"], "time_semantics": "5m桶起点，首尾包含", "warmup": "不映射为1h因子预热", "formal_PIT_or_gate": "未确认", "reason": "不填0、不插值；无发布时点证据"})
    for run in ratio5:
        interval_rows.append({"batch_version": VERSION, "batch_index": batch_index, "symbol": symbol, "factor_scope": "扩展sum_taker_long_short_vol_ratio 5m覆盖", "qualification": "字段数据存在", "start_utc": run["start_utc"], "end_utc": run["end_utc"], "bar_count": run["bars"], "time_semantics": "5m桶起点，首尾包含", "warmup": "无额外预热", "formal_PIT_or_gate": "未确认", "reason": "缺值不填0；当前候选非必需字段"})
    add_interval(symbol, batch_index, "旧事件策略4h数据层", old4_runs, "数据层候选，不代表准入", "4h连续200根预热；规则另需42根箱体", "当前策略门禁未过")
    if fund["rows"]:
        start_ms = int(dt.datetime.strptime(fund["first"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        end_ms = int(dt.datetime.strptime(fund["last"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        add_interval(symbol, batch_index, "资金费实际结算事件（执行成本）", [{"start_ms": start_ms, "end_ms": end_ms, "bars": fund["rows"]}], "事件存在，空值另计", "逐事件；不假设固定8h", "rate缺%d、mark缺%d；不填0" % (fund["rate_null"], fund["mark_null"]))
    else:
        add_interval(symbol, batch_index, "资金费实际结算事件（执行成本）", [], "无本地数据/未确认", "", "不补下载或填0")
    for run in mark["intervals"]:
        interval_rows.append({"batch_version": VERSION, "batch_index": batch_index, "symbol": symbol, "factor_scope": "mark1h参考值", "qualification": "字段数据存在", "start_utc": run["start_utc"], "end_utc": run["end_utc"], "bar_count": run["bars"], "time_semantics": "小时桶起点；单值mark_open，不是OHLC", "warmup": "无", "formal_PIT_or_gate": "未确认", "reason": "不可替代信号OHLC行情"})

    identity = pool[symbol]
    input_files = [file for timeframe_data in candles.values() for file in timeframe_data["files"]] + metric["files"]
    for source in (fund, mark):
        if source["path"]:
            input_files.append({"path": source["path"], "sha256_actual": source["sha256"], "sha256_catalog": None, "catalog_match": None, "rows": source["rows"], "bytes": Path(source["path"]).stat().st_size, "columns": []})
    canonical = [{"path": file["path"], "sha256": file["sha256_actual"]} for file in sorted(input_files, key=lambda x: x["path"])]
    row = {
        "batch_version": VERSION, "batch_index": batch_index, "symbol": symbol, "historical_pool_version": history["historical_pool_version"], "historical_denominator": 682,
        "underlying_type": "COIN", "contract_type": "PERPETUAL", "quote_asset": "USDT", "status_latest_observed": identity["status_latest_observed"], "status_filter_applied": False,
        "first_official_snapshot_seen_utc": identity["first_official_snapshot_seen_utc"], "last_official_snapshot_seen_utc": identity["last_official_snapshot_seen_utc"], "official_onboard_time_utc": identity["official_onboard_time_earliest_utc"], "official_snapshots_seen": identity["official_snapshots_seen"],
        "1h_valid_rows": len(h1["valid"]), "1h_gap_segments": h1["grid"]["gap_segments"], "1h_missing_grid_slots": h1["grid"]["missing_grid_slots"], "1h_valid_intervals_json": json.dumps(display_runs(make_runs(h1["valid"], STEPS["1h"])), ensure_ascii=False, separators=(",", ":")),
        "15m_valid_rows": len(m15["valid"]), "15m_gap_segments": m15["grid"]["gap_segments"], "15m_missing_grid_slots": m15["grid"]["missing_grid_slots"], "15m_valid_intervals_json": json.dumps(display_runs(make_runs(m15["valid"], STEPS["15m"])), ensure_ascii=False, separators=(",", ":")),
        "4h_valid_rows": len(h4["valid"]), "4h_gap_segments": h4["grid"]["gap_segments"], "4h_missing_grid_slots": h4["grid"]["missing_grid_slots"], "4h_valid_intervals_json": json.dumps(display_runs(make_runs(h4["valid"], STEPS["4h"])), ensure_ascii=False, separators=(",", ":")),
        "base_1h_15m_research_candidate_intervals_json": json.dumps(display_runs(base_runs), ensure_ascii=False, separators=(",", ":")), "base_warmup_1h": 20,
        "oi_factor_1h_qualified_intervals_json": "[]", "oi_raw_5m_nonnull_rows": metric["fields"]["sum_open_interest"]["nonnull"], "oi_raw_5m_intervals_json": json.dumps(oi5, ensure_ascii=False, separators=(",", ":")), "oi_5m_missing_grid_slots": metric["grid"]["missing_grid_slots"], "oi_factor_mapping_status": "未确认；仅5m原始数据",
        "taker_ratio_5m_nonnull_rows": metric["fields"]["sum_taker_long_short_vol_ratio"]["nonnull"], "taker_ratio_5m_intervals_json": json.dumps(ratio5, ensure_ascii=False, separators=(",", ":")),
        "funding_event_rows": fund["rows"], "funding_rate_missing_rows": fund["rate_null"], "funding_mark_missing_rows": fund["mark_null"], "funding_first_utc": fund["first"], "funding_last_utc": fund["last"],
        "mark1h_rows": mark["rows"], "mark1h_missing_grid_slots": mark["missing_slots"], "mark1h_intervals_json": json.dumps(mark["intervals"], ensure_ascii=False, separators=(",", ":")), "mark1h_is_ohlc": False,
        "source_shard_count": len(canonical), "source_input_fingerprint_sha256": hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "local_data_present": any(data["rows"] for data in candles.values()) or metric["rows"] > 0 or fund["rows"] > 0 or mark["rows"] > 0,
        "unqualified_reasons": "OI 5m至1h映射/PIT未确认；K线available_at无发布时间证明；其他缺口见分字段统计",
    }
    rows_out.append(row)
    all_shards.extend(input_files)

status_counts = collections.Counter(row["status_latest_observed"] for row in rows_out)
summary = {
    "work_order": "FT-POOL-01", "batch_version": VERSION, "historical_pool_version": history["historical_pool_version"], "historical_denominator": 682,
    "batch_number": BATCH_NUMBER, "batch_size": len(batch), "symbol_index_first": FIRST_BATCH_INDEX, "symbol_index_last": FIRST_BATCH_INDEX + len(batch) - 1, "first_symbol": batch[0], "last_symbol": batch[-1], "status_counts": dict(status_counts),
    "symbols_with_any_local_data": sum(row["local_data_present"] for row in rows_out),
    "symbols_with_base_1h_15m_candidate": sum(row["base_1h_15m_research_candidate_intervals_json"] != "[]" for row in rows_out),
    "symbols_with_raw_OI_5m": sum(row["oi_raw_5m_nonnull_rows"] > 0 for row in rows_out), "OI_1h_factor_qualified_symbols": 0,
    "symbols_with_funding_events": sum(row["funding_event_rows"] > 0 for row in rows_out), "symbols_with_mark1h": sum(row["mark1h_rows"] > 0 for row in rows_out),
    "input_shards_read": len(all_shards), "log_records": len(logs), "symbols_remaining_after_batch": len(remaining) - batch_offset - len(batch),
    "limitations": ["历史分母是保存的官方快照至少一次显示为COIN/PERPETUAL/USDT的并集，快照间可能漏掉短命合约。", "K线available_at缺发布时点证据；区间只是数据候选。", "OI只列5m原始值，1h映射及PIT未确认。", "不填0，不下载数据。"],
}
inputs = []
for item in all_shards:
    inputs.append(item)
for row in rows_out:
    pass
fingerprint = {
    "batch_version": VERSION, "historical_pool_confirmed_denominator": 682,
    "history_pool_fingerprint_sha256": H(O / "04_历史范围汇总_v2.json"), "official_pool_file_sha256": H(O / "01_历史确认池_v2.csv"),
    "parquet_catalog_sha256": H(R / "_catalog.json"), "catalog_updated_at": catalog.get("updated_at"),
    "input_shards": all_shards, "input_shard_count": len(all_shards),
    "catalog_match_count": sum(x.get("catalog_match") is True for x in all_shards),
    "catalog_mismatch_count": sum(x.get("catalog_match") is False for x in all_shards),
    "not_catalogued_count": sum(x.get("catalog_match") is None for x in all_shards),
    "symbol_sha256": [{"symbol": row["symbol"], "sha256": row["source_input_fingerprint_sha256"], "input_shard_count": row["source_shard_count"]} for row in rows_out],
}

p_table = OUT / (BATCH_PREFIX + "_资格表_v1.csv")
with open(p_table, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows_out[0])); writer.writeheader(); writer.writerows(rows_out)
p_intervals = OUT / (BATCH_PREFIX + "_合格区间_v1.csv")
with open(p_intervals, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(interval_rows[0])); writer.writeheader(); writer.writerows(interval_rows)
p_logs = OUT / (BATCH_PREFIX + "_实际检查日志_v1.jsonl")
with open(p_logs, "w", encoding="utf-8") as f:
    for item in logs: f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
p_summary = OUT / (BATCH_PREFIX + "_数量汇总_v1.json")
json.dump(summary, open(p_summary, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
p_fp = OUT / (BATCH_PREFIX + "_数据指纹_v1.json")
json.dump(fingerprint, open(p_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
report = f"""# FT-POOL-01｜{BATCH_PREFIX}分批资格核验

批次版本：`{VERSION}`。以682个官方历史快照确认符号为范围，按symbol排序取第{FIRST_BATCH_INDEX}–{FIRST_BATCH_INDEX + len(batch) - 1}位，跳过已完成批次。只读已有本地行情、指标、资金费与mark文件，未下载或更改原始数据。

本批覆盖{len(batch)}币（{batch[0]}至{batch[-1]}），当前快照状态分布为{dict(status_counts)}。基础价量1h+15m数据候选{summary['symbols_with_base_1h_15m_candidate']}币；5m原始OI有值{summary['symbols_with_raw_OI_5m']}币，但1h OI因子合格数仍为0（映射与发布时间未确认）。资金费事件文件覆盖{summary['symbols_with_funding_events']}币，mark1h覆盖{summary['symbols_with_mark1h']}币。逐币字段、缺口和时间段见资格表、区间表及日志；缺失项不填0。

本批之后剩余历史池候选{summary['symbols_remaining_after_batch']}币。K线发布时间证据缺失，因此研究候选区间不等于正式PIT资格。"
"""
p_report = OUT / (BATCH_PREFIX + "_核验说明_v1.md"); p_report.write_text(report, encoding="utf-8")
outputs = [p_table, p_intervals, p_logs, p_summary, p_fp, p_report]
manifest = {"work_order": "FT-POOL-01", "batch_version": VERSION, "immutable": True, "scope_denominator": 682, "batch_symbols": batch, "files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": H(p)} for p in outputs]}
p_manifest = OUT / ("manifest_%s_v1.json" % BATCH_PREFIX); json.dump(manifest, open(p_manifest, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps({"batch_version": VERSION, "summary": summary, "outputs": [str(p) for p in outputs + [p_manifest]]}, ensure_ascii=False, indent=2))