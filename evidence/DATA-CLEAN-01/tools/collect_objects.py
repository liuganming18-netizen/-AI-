#!/usr/bin/env python3
"""对指定对象采集：文件数 / 字节 / mtime 范围 / 数据时间范围（抽样）。"""
import glob
import json
import os
import sys
from datetime import datetime

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"

# (相对路径, 类别)  类别: dir / parquet_candles / parquet_oi / parquet_metrics / runs / raw
OBJS = [
    ("数据/市场数据/parquet/candles", "parquet_candles"),
    ("数据/市场数据/parquet/oi", "parquet_oi"),
    ("数据/市场数据/parquet/metrics", "parquet_metrics"),
    ("数据/市场数据/parquet/funding", "parquet_generic"),
    ("数据/市场数据/parquet/candle_heads", "parquet_generic"),
    ("数据/市场数据/parquet/sources", "parquet_generic"),
    ("数据/市场数据/db", "dir"),
    ("数据/raw", "raw"),
    ("数据/raw_oi_metrics", "raw"),
    ("数据/raw_rest_gap", "dir"),
    ("数据/archive", "dir"),
    ("数据/contracts", "dir"),
    ("数据/任务队列", "dir"),
    ("数据/paper", "dir"),
    ("数据/研究输入/WyckoffMaster", "dir"),
    ("数据/研究输入/WyckoffMaster/_backup/20261014T190050Z", "dir"),
    ("数据/研究输入/WyckoffMaster_v31", "dir"),
    ("数据/研究输入/外部迁移_2026-10-16", "dir"),
    ("回测日志/event_trend_v0_1/runs", "runs"),
    ("回测日志/event_trend_v0_1/runs_sep_all_latest", "runs"),
    ("回测日志/event_trend_v0_1/runs_2025aug100", "runs"),
    ("回测日志/event_trend_v0_1/runs_sep50_check", "runs"),
    ("回测日志/event_trend_v0_1/runs_sep50", "runs"),
    ("回测日志/event_trend_v0_1/runs_aug100", "runs"),
    ("回测日志/event_trend_v0_1/runs_sep50_full", "runs"),
    ("回测日志/event_trend_v0_1/runs_sep50_strict", "runs"),
    ("回测日志/event_trend_v0_1/reconstructed", "dir"),
    ("回测日志/oi_resonance_v1", "dir"),
    ("回测日志/batch_tests_archive", "dir"),
    ("回测日志/legacy_archive", "dir"),
    ("回测日志/历史下载状态", "dir"),
    ("evidence", "dir"),
    ("框架/backend", "dir"),
    ("框架/data_hub", "dir"),
    ("框架/最新唐奇安威科夫接通与收尾_2026-10-11", "dir"),
    ("UI界面/web", "dir"),
    ("docs", "dir"),
    ("策略", "dir"),
]


def walk_stats(p):
    n = 0
    b = 0
    mn = mx = None
    for dp, dns, fns in os.walk(p):
        dns[:] = [d for d in dns if d not in ("node_modules", ".git", "__pycache__")]
        for fn in fns:
            try:
                st = os.lstat(os.path.join(dp, fn))
            except OSError:
                continue
            n += 1
            b += st.st_size
            t = int(st.st_mtime)
            mn = t if mn is None or t < mn else mn
            mx = t if mx is None or t > mx else mx
    return n, b, mn, mx


def fmt(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else "-"


def parquet_range(p, col, limit=2):
    """抽样读 parquet 的 min/max 时间。"""
    import pandas as pd
    fs = sorted(glob.glob(os.path.join(p, "**", "*.parquet"), recursive=True))
    if not fs:
        return None
    picks = [fs[0], fs[-1]] if len(fs) > 1 else fs
    lo = hi = None
    for f in picks:
        try:
            d = pd.read_parquet(f, columns=[col])
            s = pd.to_numeric(d[col], errors="coerce").dropna()
            if len(s):
                a, b = int(s.min()), int(s.max())
                lo = a if lo is None or a < lo else lo
                hi = b if hi is None or b > hi else hi
        except Exception:
            pass
    if lo is None:
        return None
    return fmt(lo / 1000), fmt(hi / 1000)


def runs_range(p):
    fs = sorted(glob.glob(os.path.join(p, "*", "run.json")))
    if not fs:
        return None
    picks = [fs[0], fs[-1]] if len(fs) > 1 else fs
    out = []
    for f in picks:
        try:
            d = json.load(open(f, encoding="utf-8"))
            sp = d.get("spec") or {}
            out.append((sp.get("window_start"), sp.get("window_end")))
        except Exception:
            pass
    return out or None


def raw_range(p):
    """从文件名里提取 8 位日期。"""
    import re
    pat = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")
    lo = hi = None
    cnt = 0
    for dp, dns, fns in os.walk(p):
        for fn in fns:
            m = pat.search(fn)
            if m:
                s = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                lo = s if lo is None or s < lo else lo
                hi = s if hi is None or s > hi else hi
                cnt += 1
        if cnt > 20000:
            break
    return (lo, hi) if lo else None


def main():
    out = []
    for rel, kind in OBJS:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            out.append({"path": rel, "status": "不存在"})
            print(f"  跳过（不存在）: {rel}")
            continue
        n, b, mn, mx = walk_stats(p)
        rec = {"path": rel, "kind": kind, "files": n, "bytes": b,
               "mtime_min": fmt(mn), "mtime_max": fmt(mx)}
        if kind == "parquet_candles":
            rec["data_range"] = parquet_range(p, "open_time")
        elif kind == "parquet_oi":
            rec["data_range"] = parquet_range(p, "ts")
        elif kind == "parquet_metrics":
            rec["data_range"] = parquet_range(p, "ts")
        elif kind == "runs":
            rec["data_range"] = runs_range(p)
        elif kind == "raw":
            rec["data_range"] = raw_range(p)
        out.append(rec)
        print(f"  {rel:52s} {n:>6d} 文件 {b/1024/1024:>9.1f} MB  {rec['mtime_min']} ~ {rec['mtime_max']}")

    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {sys.argv[1]}")


if __name__ == "__main__":
    main()
