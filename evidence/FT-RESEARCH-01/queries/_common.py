# -*- coding: utf-8 -*-
"""FT-RESEARCH-01｜后续样本与因果检查可行性 — 公共只读模块。

被复用对象（全部只读）：
- evidence/FT-FACTOR-AUDIT-01/out/04_冻结研究期输入清单_v1.csv  → L1=241 资格链
- evidence/FT-POOL-01/                                          → 682/526/0(OI) 资格清单
- evidence/FT-ENTRY-03/user_data/config.json                    → 70 币 A 路线白名单
- evidence/FULL_SWEEP_200x100/runs.jsonl                        → 19496 次运行接触记录
- evidence/OOS-READY-01/out/01_已观察范围_*.csv, 02_验证数据资格表.csv → 已观察范围/S0~S7

边界：
- 只读上游；只在本目录 out/ 与 logs/ 写；
- 不下载、不改时间戳、不打开 2026 留出数据、不跑任何回测；
- 不宣布任何新因子/策略有效。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
EV = os.path.join(ROOT, "evidence")
AUDIT = os.path.join(EV, "FT-RESEARCH-01")
OUT = os.path.join(AUDIT, "out")

# 上游只读源
L1_CSV = os.path.join(EV, "FT-FACTOR-AUDIT-01/out/04_冻结研究期输入清单_v1.csv")
L1_SUMMARY = os.path.join(EV, "FT-FACTOR-AUDIT-01/out/06_分层资格汇总_v1.json")
POOL_SUMMARY = os.path.join(EV, "FT-POOL-01/06_全池核验摘要_v1.json")
CFG_ENTRY03 = os.path.join(EV, "FT-ENTRY-03/user_data/config.json")
FS_RUNS = os.path.join(EV, "FULL_SWEEP_200x100/runs.jsonl")
OOS_COINS = os.path.join(EV, "OOS-READY-01/out/01_已观察范围_币种.csv")
OOS_BATCH = os.path.join(EV, "OOS-READY-01/out/01_已观察范围_批次.csv")
OOS_SEG = os.path.join(EV, "OOS-READY-01/out/02_验证数据资格表.csv")
ENTRY04_OUT = os.path.join(EV, "FT-ENTRY-04/out")

# 冻结口径（照录上游，不新造）
RESEARCH_START = "2021-12-01"
RESEARCH_END = "2024-12-31"
HOLDOUT_START = "2026-04-15"
HOLDOUT_END = "2026-10-15"

# A 路线 9 格（与 FT-ENTRY-03 一致，仅供标注，不跑）
CELLS_A = [
    ("A01", "Wyckoff_A_L06_V08", 0.6, 0.8),
    ("A02", "Wyckoff_A_L06_V10", 0.6, 1.0),
    ("A03", "Wyckoff_A_L06_V12", 0.6, 1.2),
    ("A04", "Wyckoff_A_L04_V08", 0.4, 0.8),
    ("A05", "Wyckoff_A_L04_V10", 0.4, 1.0),
    ("A06", "Wyckoff_A_L04_V12", 0.4, 1.2),
    ("A07", "Wyckoff_A_L02_V08", 0.2, 0.8),
    ("A08", "Wyckoff_A_L02_V10", 0.2, 1.0),
    ("A09", "Wyckoff_A_L02_V12", 0.2, 1.2),
]
BASELINE = "WyckoffBaseline"


def norm_pair(s: str) -> str:
    """BINANCE/USDT:USDT → BINANCEUSDT；PUMPBTC/USDT:USDT → PUMPBTCUSDT。"""
    s = str(s).upper()
    for suf in ("/USDT:USDT", "/USDT", ":USDT"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.replace("/", "").replace(":", "") + "USDT"


def read_csv(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def jdump(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def write_csv(df: pd.DataFrame, name: str) -> str:
    p = os.path.join(OUT, name)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def load_l1() -> pd.DataFrame:
    d = read_csv(L1_CSV)
    if d.empty:
        return d
    d["symbol"] = d["symbol"].astype(str)
    return d


def load_whitelist_70() -> set:
    import json as _j
    with open(CFG_ENTRY03, encoding="utf-8") as f:
        cfg = _j.load(f)
    pw = cfg.get("exchange", {}).get("pair_whitelist") or cfg.get("pairs") or []
    return set(norm_pair(x) for x in pw)


def load_fullsweep_runs() -> pd.DataFrame:
    rows: List[dict] = []
    with open(FS_RUNS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str)
    df["s"] = pd.to_datetime(df["start"])
    df["e"] = pd.to_datetime(df["end"])
    for c in ("candidates", "trades", "wins", "bars15", "bars1h"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    for c in ("net", "gross", "fees", "funding", "slip"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df
