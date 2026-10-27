# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜公共定义（只读上游 BT-01 产物，不修改）。

上游一律只读：
· `evidence/WORK-DYNAMIC-BT-01/out/raw_results/backtest-result-<策略>.zip`（**引擎原始导出**）
· `evidence/WORK-DYNAMIC-BT-01/out/trades_<策略>.csv`（BT-01 由原始 JSON 展平）
· `evidence/WORK-DYNAMIC-BT-01/out/14_订单与逐次成交_全部.csv.gz`（BT-01 订单层）
· `evidence/WORK-DYNAMIC-BT-01/replay/<策略>.jsonl.gz`（BT-01 复盘包：4h/1h/15m K 线）
· `evidence/WORK-DYNAMIC-BT-01/out/stage_trace_unified/stage_trace_<策略>.csv`（止损/切档轨迹）
· `evidence/WORK-DYNAMIC-BT-01/out/audit_confirmation_<策略>.csv`（策略侧确认审计）
· `回测日志/event_trend_v0_1/runs_work_dynamic_bt_01/<run_id>/`（BT-01 登记的 run）

本单所有新产物只写 `evidence/WORK-DYNAMIC-BT-02/`。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BT01 = BASE / "evidence/WORK-DYNAMIC-BT-01"
BT02 = BASE / "evidence/WORK-DYNAMIC-BT-02"
OUT = BT02 / "out"
OUT.mkdir(parents=True, exist_ok=True)

DATA = BASE / "evidence/FT-FACTOR-03/user_data/data/binance/futures"
BATCH = BASE / "回测日志/event_trend_v0_1/runs_work_dynamic_bt_01"
REPLAY = BT01 / "replay"
TRACE = BT01 / "out/stage_trace_unified"

# 白名单顺序：freqtrade 回测按 (入场时刻, 白名单顺序) 递增分配 trade.id
_WL = json.loads((BT01 / "user_data/config.json").read_text(encoding="utf-8"))
WL_ORDER: Dict[str, int] = {p: i for i, p in enumerate(_WL["exchange"]["pair_whitelist"])}

TFS = ("4h", "1h", "15m")


def utc_iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pair_to_slug(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def market_file(pair: str, tf: str) -> Path:
    return DATA / f"{pair_to_slug(pair)}-{tf}-futures.feather"


def load_index() -> pd.DataFrame:
    return pd.read_csv(BT01 / "out/02_运行索引.csv")


def raw_zip(strategy: str) -> Path:
    return BT01 / "out/raw_results" / f"backtest-result-{strategy}.zip"


def read_raw_result(strategy: str) -> dict:
    """读引擎原始导出 JSON（**全精度**，不经任何 CSV 中转）。"""
    z = zipfile.ZipFile(raw_zip(strategy))
    name = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
    return json.loads(z.read(name))


def raw_block(strategy: str) -> dict:
    d = read_raw_result(strategy)
    st = d["strategy"]
    key = [k for k in st if k.startswith("Wyckoff")][0]
    return st[key]


def raw_trades(strategy: str) -> List[dict]:
    return raw_block(strategy)["trades"]


def derive_trade_ids(trades: List[dict]) -> List[dict]:
    """按引擎规则还原 trade_id：`(open_timestamp, 白名单索引)` 升序 ⇒ 1..n。

    引擎导出**不带** trade_id（见 `Trade.to_json(minified=True)` 无该字段），
    所以这是**还原值**而非原始值，必须标 derived。
    """
    order = sorted(
        range(len(trades)),
        key=lambda i: (int(trades[i]["open_timestamp"]), WL_ORDER.get(trades[i]["pair"], 10**6)),
    )
    out = [dict(t) for t in trades]
    for rank, i in enumerate(order, start=1):
        out[i]["_trade_id"] = rank
    return out


def iter_replay(strategy: str):
    """逐行产出复盘包记录（含 4h/1h/15m K 线、stop_trace、stage_events）。"""
    p = REPLAY / f"{strategy}.jsonl.gz"
    if not p.exists():
        return
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            rec = json.loads(line)
            rec["_replay_file"] = str(p)
            rec["_replay_line"] = lineno
            yield rec
