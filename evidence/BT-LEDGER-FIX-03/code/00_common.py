# -*- coding: utf-8 -*-
"""BT-LEDGER-FIX-03｜公共定义（上游一律只读，只写本单目录）。

只读上游
────────
· `evidence/WORK-DYNAMIC-BT-01/out/raw_results/backtest-result-<策略>.zip`
  └ 引擎原始导出（**唯一原始事件来源**）：`strategy.<策略>.trades[*]` / `trades[*].orders[*]`
· `evidence/WORK-DYNAMIC-BT-01/out/02_运行索引.csv`（45 组 run_id / 命令 / 策略哈希）
· `evidence/WORK-DYNAMIC-BT-01/out/14_订单与逐次成交_全部.csv.gz`（BT-01 订单层，待核）
· `evidence/WORK-DYNAMIC-BT-02/out/02_逐笔订单核对.csv.gz`（BT-02 订单层，待核）
· `evidence/WORK-DYNAMIC-BT-02/out/10_逐笔复盘索引.csv`（K 线关联，复用不重建）
· `回测日志/event_trend_v0_1/runs_work_dynamic_bt_01/<run_id>/orders.jsonl|trades.jsonl`
  （**平台自建导出层**，其中 order_id 为合成内部键，本单必须标注）

本单产物一律写 `evidence/BT-LEDGER-FIX-03/`，不覆盖任何旧证据。
"""
from __future__ import annotations

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
ME = BASE / "evidence/BT-LEDGER-FIX-03"
OUT = ME / "out"
OUT.mkdir(parents=True, exist_ok=True)

RUNS = BASE / "回测日志/event_trend_v0_1/runs_work_dynamic_bt_01"
FT_PKG = BASE / "evidence/FT-BASELINE-01/.venv/lib/python3.11/site-packages/freqtrade"

TFS = ("4h", "1h", "15m")

# 白名单顺序：引擎同一根 K 线内按 pairlist 顺序建仓（tie-break 依据，见报告 §3.3）
_WL = json.loads((BT01 / "user_data/config.json").read_text(encoding="utf-8"))
WL_ORDER: Dict[str, int] = {p: i for i, p in enumerate(_WL["exchange"]["pair_whitelist"])}


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


def run_index() -> pd.DataFrame:
    return pd.read_csv(BT01 / "out/02_运行索引.csv")


def raw_zip(strategy: str) -> Path:
    return BT01 / "out/raw_results" / f"backtest-result-{strategy}.zip"


def read_raw_block(strategy: str) -> dict:
    """读引擎原始导出 JSON（全精度），返回该策略的 stats 块。"""
    z = zipfile.ZipFile(raw_zip(strategy))
    name = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
    d = json.loads(z.read(name))
    st = d["strategy"]
    key = [k for k in st if k.startswith("Wyckoff")][0]
    return st[key]


def derive_trade_ids(trades: List[dict]) -> List[int]:
    """还原 trade_id：按 `(open_timestamp, 白名单索引)` 升序 ⇒ 1..n。

    **派生值，不是导出值**。引擎内部有 `trade_id_counter`（backtesting.py:149/1211/1213），
    但 `Trade.to_json` 的 trade_id 被 `BT_DATA_COLUMNS` 裁掉；且同刻并列时依赖
    「同一根 K 线内按 pairlist 顺序建仓」这一未观测前提 ⇒ 标 `derived_unverified`。
    """
    order = sorted(
        range(len(trades)),
        key=lambda i: (int(trades[i]["open_timestamp"]), WL_ORDER.get(trades[i]["pair"], 10**6)),
    )
    ids = [0] * len(trades)
    for rank, i in enumerate(order, start=1):
        ids[i] = rank
    return ids


# ── 字段来源分类（全单唯一口径，禁止混用）──────────────────────────────
PROV = {
    "raw_export": "引擎原始导出值（backtest-result json 内逐字存在）",
    "raw_position": "原始导出内的位置/索引（不是字段值，但由原始产物唯一确定）",
    "engine_computed": "引擎在导出时即时计算的派生字段（如 ft_is_entry）",
    "derived_from_raw": "由已导出原始字段确定性计算（公式可复算）",
    "derived_from_construction": "由引擎源码构造规则唯一确定，但导出里没有该值",
    "derived_unverified": "派生值，其与引擎内部真值的一致性依赖未观测前提",
    "unrecoverable_rerun": "引擎内存里存在，但导出裁剪，事后不可恢复 ⇒ 必须重跑才拿得到",
    "nonexistent_backtest": "回测环境不存在该概念，任何来源都补不出来",
    "internal_key": "本项目自建关联键，**不是交易所 ID / 不是引擎 ID**",
}
