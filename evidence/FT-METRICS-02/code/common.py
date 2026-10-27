# -*- coding: utf-8 -*-
"""FT-METRICS-02 公共读取层

职责：只读定位并解析 FT-BASELINE-03 的回测产物，不做任何修改。
所有计算脚本共用本模块，确保口径唯一。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent                      # evidence/FT-METRICS-02
ROOT = BASE_DIR.parent.parent               # 项目根
TARGET = ROOT / "evidence" / "FT-BASELINE-03"
OUT = BASE_DIR / "out"
LOGS = BASE_DIR / "logs"


def sha256_file(p: Path, n: int = 16) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def sha256_bytes(b: bytes, n: int = 16) -> str:
    return hashlib.sha256(b).hexdigest()[:n]


def ts(p: Path) -> str:
    if not p.exists():
        return "-"
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except Exception:
        return str(p)


@dataclass
class ResultBundle:
    """一次回测运行的全部可核对产物。"""
    zip_path: Path
    payload: dict = field(default_factory=dict)
    strategy_name: str = ""
    trades: list = field(default_factory=list)
    wallet: list = field(default_factory=list)   # [{date, balance, total_quote}]
    embedded_config: dict = field(default_factory=dict)
    embedded_strategy: str = ""
    embedded_strategy_sha: str = ""

    @property
    def metrics(self) -> dict:
        return self.payload.get("strategy", {}).get(self.strategy_name, {})


def find_result_zips() -> list[Path]:
    d = TARGET / "user_data" / "backtest_results"
    if not d.exists():
        return []
    return sorted(d.glob("backtest-result-*.zip"), key=lambda p: p.stat().st_mtime)


def load_bundle(zip_path: Path) -> ResultBundle:
    """解析 freqtrade 导出 zip。缺件不抛错，由调用方判定为不可核对。"""
    b = ResultBundle(zip_path=zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        main = [n for n in names
                if n.endswith(".json") and "_config." not in n and "market_change" not in n]
        if not main:
            return b
        b.payload = json.loads(z.read(main[0]))
        strat = b.payload.get("strategy", {})
        b.strategy_name = next(iter(strat), "")
        b.trades = strat.get(b.strategy_name, {}).get("trades", [])

        cfg = [n for n in names if n.endswith("_config.json")]
        if cfg:
            b.embedded_config = json.loads(z.read(cfg[0]))

        py = [n for n in names if n.endswith(".py")]
        if py:
            raw = z.read(py[0])
            b.embedded_strategy = raw.decode("utf-8", "replace")
            b.embedded_strategy_sha = sha256_bytes(raw)

        wal = [n for n in names if n.endswith("_wallet.feather")]
        if wal:
            try:
                import pandas as pd
                df = pd.read_feather(io.BytesIO(z.read(wal[0])))
                keep = [c for c in ("date", "balance", "total_quote") if c in df.columns]
                b.wallet = df[keep].to_dict("records")
            except Exception:
                b.wallet = []
    return b


def initial_capital(cfg: dict, metrics: dict) -> float:
    """初始资金优先级：运行配置 > 结果内 starting_balance > 默认值。"""
    for v in (cfg.get("dry_run_wallet"), metrics.get("starting_balance"),
              metrics.get("dry_run_wallet")):
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return 2000.0


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def na(v, unit: str = "") -> str:
    """分母为零或不可算时的统一写法。"""
    return "不适用" if v is None else f"{v}{unit}"
