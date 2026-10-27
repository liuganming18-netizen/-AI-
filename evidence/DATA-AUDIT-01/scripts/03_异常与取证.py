# -*- coding: utf-8 -*-
"""DATA-AUDIT-01 / 03：异常抽样 + 持仓量开关取证 + 受影响回测清单（只读）。

产出：
  out/13_异常抽样.csv        抽样币的重复/零值/空值/间隔异常
  out/14_OI开关取证.csv      开关位置、默认值、调用链、能否从外部打开
  out/15_受影响回测.csv      FT-LOW4H 全部 run 的 OI 开关与资格
  out/16_当前运行路径.json   活进程 + 状态文件 + 漏斗计数

纪律：抽样只读 parquet/sqlite，不写业务库；抽样清单固定（可复现）。
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HOT = ROOT / "数据/市场数据/db/market_v2.sqlite3"
PARQ = ROOT / "数据/市场数据/parquet"
OUT = ROOT / "evidence/DATA-AUDIT-01/out"
RUNS = ROOT / "回测日志/event_trend_v0_1/runs"

# 固定抽样：3 个大币 + 3 个中币 + 4 个新币（覆盖不同上线年份）
SAMPLE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AAVEUSDT", "CVCUSDT", "SLPUSDT",
          "0GUSDT", "CTUSDT", "1000CATUSDT", "WALUSDT"]


def iso(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    rows = []

    # ── 1. OI：重复 ts / 零值 / 间隔异常 ──
    for sym in SAMPLE:
        files = sorted(glob.glob(str(PARQ / f"oi/binance/um/{sym}/*.parquet")))
        if not files:
            rows.append(["OI", sym, "无文件", "", "", "", ""])
            continue
        df = pd.concat([pq.read_table(f, columns=["ts", "open_interest"]).to_pandas()
                        for f in files], ignore_index=True)
        df = df.sort_values("ts")
        dup = int(len(df) - df.ts.nunique())
        zero = int((pd.to_numeric(df.open_interest, errors="coerce") == 0).sum())
        nanv = int(pd.to_numeric(df.open_interest, errors="coerce").isna().sum())
        d = df.ts.drop_duplicates().diff().dropna()
        irregular = int((d % 300000 != 0).sum() + (d > 300000).sum())
        rows.append(["OI", sym, f"{len(df)}", f"重复ts={dup}", f"零值={zero}",
                     f"空值={nanv}", f"非5m/断档点={irregular}"])

    # ── 2. metrics：零持仓 / 重复 / 空值 ──
    for sym in SAMPLE:
        files = sorted(glob.glob(str(PARQ / f"metrics/binance/um/{sym}/*.parquet")))
        if not files:
            rows.append(["metrics", sym, "无文件", "", "", "", ""])
            continue
        df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
        dup = int(len(df) - df.ts.nunique())
        z = int((df.sum_open_interest == 0).sum())
        na = int(df.sum_open_interest.isna().sum())
        # 纯别名检测：两列逐值相同
        alias = ""
        try:
            if df.retail_account_ls_ratio.equals(df.count_long_short_ratio):
                alias += "retail=count_long_short;"
            if df.top_position_ls_ratio.equals(df.sum_toptrader_long_short_ratio):
                alias += "top_pos=sum_toptrader;"
        except Exception:                                        # noqa: BLE001
            pass
        rows.append(["metrics", sym, f"{len(df)}", f"重复ts={dup}",
                     f"零持仓={z}", f"空持仓={na}", f"纯别名={alias or '无'}"])

    # ── 3. 冷档 K 线：taker_buy 空串 / trade_count 零 ──
    for sym in SAMPLE[:5]:
        for tf in ("4h", "15m"):
            files = sorted(glob.glob(str(PARQ / f"candles/binance/um/{sym}/{tf}/*.parquet")))
            if not files:
                continue
            df = pd.concat([pq.read_table(f, columns=["open_time", "trade_count",
                                                      "taker_buy_volume", "available_at"])
                            .to_pandas() for f in files], ignore_index=True)
            dup = int(len(df) - df.open_time.nunique())
            empty = int((df.taker_buy_volume.astype(str) == "").sum())
            tc0 = int((pd.to_numeric(df.trade_count, errors="coerce") == 0).sum())
            avail = int(df.available_at.notna().sum()) if "available_at" in df else 0
            rows.append(["candles", f"{sym}@{tf}", f"{len(df)}", f"重复open_time={dup}",
                         f"taker空串={empty}", f"trade_count=0:{tc0}",
                         f"available_at非空={avail}"])

    with open(OUT / "13_异常抽样.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["数据类型", "标的", "行数", "重复", "零/空1", "零/空2", "其他"])
        w.writerows(rows)
    print(f"  → 13_异常抽样.csv ({len(rows)} 行)")

    # ── 4. OI 开关取证 ──
    ur = ROOT / "框架/backend/unified_runtime.py"
    src = ur.read_text(encoding="utf-8").split("\n")
    ev = []

    def line_of(pat, start=0):
        for i in range(start, len(src)):
            if pat in src[i]:
                return i + 1
        return None

    ev.append(["开关定义", "unified_runtime.py",
               line_of("def __init__(self, symbol: str = \"BTCUSDT\", enable_oi_filter"),
               "RealSignalEngine 构造参数默认值 = False"])
    ev.append(["开关定义", "unified_runtime.py",
               line_of("def __init__(self, symbols: List[str], enable_oi_filter"),
               "MultiSymbolSignalEngine 构造参数默认值 = False"])
    ev.append(["开关定义", "unified_runtime.py",
               line_of("lock_dir: Optional[Path] = None, enable_oi_filter"),
               "UnifiedRuntime 构造参数默认值 = False"])
    ev.append(["生产调用", "unified_runtime.py",
               line_of("runtime = UnifiedRuntime(arm=args.arm"),
               "main() 构造运行时**不传** enable_oi_filter ⇒ 取默认 False"])
    sm = ROOT / "策略/WyckoffMaster/event_trend_v0_1/state_machine.py"
    sm_lines = sm.read_text(encoding="utf-8").split("\n")
    sm_ln = next((i + 1 for i, l in enumerate(sm_lines)
                  if "enable_oi_filter: bool = True" in l), "")
    ev.append(["状态机", "策略/WyckoffMaster/event_trend_v0_1/state_machine.py", sm_ln,
               "EventStateMachine 自身默认 True；B 组语义由上层显式传 False 触发"])
    ev.append(["回测分支", "策略/WyckoffMaster/event_trend_v0_1/runner.py",
               620, "FT-LOW4H 分支写死 RealSignalEngine(enable_oi_filter=False)，且 sm_a = sm_b"])
    ev.append(["实时喂数", "unified_runtime.py", 557,
               "process_1h_bar 的 oi_timestamps/oi_values 恒传空数组 ⇒ 实时链路根本没有 OI"])
    ev.append(["命令行", "unified_runtime.py", 2836,
               "argparse 只有 --arm/--symbol/--interval/--once/--status，**没有 OI 开关**"])
    with open(OUT / "14_OI开关取证.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["类别", "文件", "行号", "事实"])
        w.writerows(ev)
    print(f"  → 14_OI开关取证.csv ({len(ev)} 行)")

    # ── 5. 受影响回测 ──
    rows = []
    for p in sorted(RUNS.glob("*/summary.json")):
        try:
            j = json.loads(p.read_text())
        except Exception:                                        # noqa: BLE001
            continue
        rule = j.get("execution_rule")
        oi = j.get("oi_filter_enabled")
        if rule != "FT-LOW4H" and oi is None:
            continue
        st = p.stat()
        rows.append([p.parent.name, rule or "", oi, j.get("variant"),
                     j.get("pit_qualified"), j.get("formal_pass_allowed"),
                     (j.get("funnel") or {}).get("candidates"),
                     (j.get("funnel") or {}).get("oi_unavailable"),
                     iso(st.st_mtime * 1000)])
    with open(OUT / "15_受影响回测.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["run", "执行规则", "OI开关", "变体", "PIT合格", "允许正式通过",
                    "候选数", "OI不可用次数", "产物最后写入"])
        w.writerows(rows)
    print(f"  → 15_受影响回测.csv ({len(rows)} 行)")

    # ── 6. 当前运行路径 ──
    st = json.loads((ROOT / "框架/backend/locks/paper_runtime_3a_status.json").read_text())
    out = {
        "status_file": "框架/backend/locks/paper_runtime_3a_status.json",
        "runtime_identity": st.get("runtime_identity"),
        "signal_engine": {k: v for k, v in (st.get("signal_engine") or {}).items()
                          if k not in ("bootstrap", "per_symbol_state")},
        "bootstrap_head": {k: v for k, v in (st.get("signal_engine", {}).get("bootstrap") or {}).items()
                           if k != "discarded_detail"},
        "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "process_cmdline": None,
    }
    pid = (st.get("runtime_identity") or {}).get("pid")
    try:
        import psutil
        if pid and psutil.pid_exists(pid):
            out["process_cmdline"] = psutil.Process(pid).cmdline()
    except Exception:                                            # noqa: BLE001
        pass
    (OUT / "16_当前运行路径.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("  → 16_当前运行路径.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
