# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-01 / 01：只读 parquet **元数据**，产出逐币逐数据集覆盖。

边界：
  · 只读 ParquetFile.metadata（行数、行组统计的 min/max、null_count）；
  · **不读取任何 open/high/low/close/volume/open_interest 数值**；
  · 不写原始数据、不改时间戳、不下载。

输出：out/01_数据覆盖_逐币.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import os
import sys

import pyarrow.parquet as pq

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
BASE = os.path.join(ROOT, "数据/市场数据/parquet")
OUT = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01/out/01_数据覆盖_逐币.csv")


def _ms_to_iso(ms):
    if ms is None:
        return ""
    try:
        return dt.datetime.utcfromtimestamp(int(ms) / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def _stat_of_file(path: str, time_col: str, extra_null_col: str | None = None):
    """返回 (rows, min_ms, max_ms, extra_null_count)；全部来自文件元数据。"""
    md = pq.ParquetFile(path).metadata
    rows = md.num_rows
    mn = mx = None
    nulls = None
    for i in range(md.num_row_groups):
        rg = md.row_group(i)
        for j in range(rg.num_columns):
            col = rg.column(j)
            name = col.path_in_schema
            if name == time_col and col.statistics is not None:
                s = col.statistics
                if s.has_min_max:
                    mn = s.min if mn is None else min(mn, s.min)
                    mx = s.max if mx is None else max(mx, s.max)
            if extra_null_col and name == extra_null_col and col.statistics is not None:
                nc = col.statistics.null_count
                if nc is not None:
                    nulls = (nulls or 0) + int(nc)
    return rows, mn, mx, nulls


def scan_dataset(files: list[str], time_col: str, extra_null_col: str | None = None):
    rows = 0
    mn = mx = None
    nulls = None
    for f in files:
        try:
            r, a, b, nl = _stat_of_file(f, time_col, extra_null_col)
        except Exception as exc:  # 单文件异常不掩盖整体
            print("  ! 元数据读取失败 %s: %s" % (os.path.basename(f), exc), file=sys.stderr)
            continue
        rows += r
        if a is not None:
            mn = a if mn is None else min(mn, a)
        if b is not None:
            mx = b if mx is None else max(mx, b)
        if nl is not None:
            nulls = (nulls or 0) + nl
    return rows, mn, mx, nulls


def main():
    candle_dir = os.path.join(BASE, "candles/binance/um")
    oi_dir = os.path.join(BASE, "oi/binance/um")
    fund_dir = os.path.join(BASE, "funding/binance/um")

    symbols = sorted(d for d in os.listdir(candle_dir) if os.path.isdir(os.path.join(candle_dir, d)))
    print("K 线标的目录数：%d" % len(symbols))

    out_rows = []
    for k, sym in enumerate(symbols, 1):
        rec = {"symbol": sym}
        for tf, col in (("4h", "open_time"), ("1h", "open_time"), ("15m", "open_time")):
            files = sorted(glob.glob(os.path.join(candle_dir, sym, tf, "*.parquet")))
            r, mn, mx, _ = scan_dataset(files, col)
            rec["%s_文件数" % tf] = len(files)
            rec["%s_行数" % tf] = r
            rec["%s_首根" % tf] = _ms_to_iso(mn)
            rec["%s_末根" % tf] = _ms_to_iso(mx)
        # 4h 末根要用 close_time 更稳：再扫一次 close_time 最大值
        files4 = sorted(glob.glob(os.path.join(candle_dir, sym, "4h", "*.parquet")))
        _, _, mx4c, _ = scan_dataset(files4, "close_time")
        rec["4h_末根收盘"] = _ms_to_iso(mx4c)

        oi_files = sorted(glob.glob(os.path.join(oi_dir, sym, "*.parquet")))
        r, mn, mx, nulls = scan_dataset(oi_files, "ts", "fetched_at")
        rec["OI_文件数"] = len(oi_files)
        rec["OI_行数"] = r
        rec["OI_首"] = _ms_to_iso(mn)
        rec["OI_末"] = _ms_to_iso(mx)
        rec["OI_fetched_at_空值数"] = "" if nulls is None else nulls

        fund_files = sorted(glob.glob(os.path.join(fund_dir, "%s.parquet" % sym)))
        r, mn, mx, nulls = scan_dataset(fund_files, "funding_time", "mark_price")
        rec["funding_文件数"] = len(fund_files)
        rec["funding_行数"] = r
        rec["funding_首"] = _ms_to_iso(mn)
        rec["funding_末"] = _ms_to_iso(mx)
        rec["funding_mark_空值数"] = "" if nulls is None else nulls
        out_rows.append(rec)
        if k % 100 == 0:
            print("  已扫 %d/%d" % (k, len(symbols)), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cols = list(out_rows[0].keys())
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)
    print("写出 %s（%d 行）" % (OUT, len(out_rows)))


if __name__ == "__main__":
    main()
