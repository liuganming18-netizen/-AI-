# -*- coding: utf-8 -*-
"""FT-METRICS-03 公共读取层

职责：只读定位并解析「唯一一次有效运行」的产物，不做任何修改。
锁定优先级：FT-BASELINE-04（若已产出结果包）> FT-BASELINE-03。
一次运行只认一个结果包，不跨版本拼接。
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent                      # evidence/FT-METRICS-03
ROOT = BASE.parent.parent               # 项目根
EVID = ROOT / "evidence"
OUT = BASE / "out"
LOGS = BASE / "logs"

CANDIDATE_DIRS = ["FT-BASELINE-04", "FT-BASELINE-03"]
DATA_SIDECAR = EVID / "FT-DATA-01" / "data" / "sidecar"
DATA_FUTURES = EVID / "FT-DATA-01" / "data" / "freqtrade" / "futures"
OLD_BASELINE_CSV = EVID / "FT-DATA-01" / "out" / "05_旧引擎基准成交.csv"
OLD_RUNS = ROOT / "回测日志" / "event_trend_v0_1" / "runs_2025aug100"


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


def result_zips(bdir: Path) -> list[Path]:
    d = bdir / "user_data" / "backtest_results"
    if not d.exists():
        return []
    return sorted(d.glob("backtest-result-*.zip"), key=lambda p: p.stat().st_mtime)


def last_result_pointer(bdir: Path) -> str:
    p = bdir / "user_data" / "backtest_results" / ".last_result.json"
    if not p.exists():
        return ""
    try:
        return json.loads(p.read_text()).get("latest_backtest", "")
    except Exception:
        return ""


def pick_target() -> tuple[Path | None, list[dict]]:
    """按优先级选目标基线目录。返回 (目录, 探测记录)。"""
    probe = []
    for name in CANDIDATE_DIRS:
        d = EVID / name
        zs = result_zips(d) if d.exists() else []
        probe.append({
            "目录": name,
            "存在": d.exists(),
            "结果包数": len(zs),
            "结果包": [z.name for z in zs],
            "指针": last_result_pointer(d) if d.exists() else "",
            "最近修改": ts(d) if d.exists() else "-",
        })
        if zs:
            return d, probe
    return None, probe


@dataclass
class Bundle:
    zip_path: Path
    payload: dict = field(default_factory=dict)
    strategy_name: str = ""
    trades: list = field(default_factory=list)
    wallet: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    embedded_strategy: str = ""
    embedded_strategy_sha: str = ""

    @property
    def metrics(self) -> dict:
        return self.payload.get("strategy", {}).get(self.strategy_name, {})


def load_bundle(zip_path: Path) -> Bundle:
    b = Bundle(zip_path=zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        main = [n for n in names
                if n.endswith(".json") and "_config." not in n and "market_change" not in n]
        if main:
            b.payload = json.loads(z.read(main[0]))
            strat = b.payload.get("strategy", {})
            b.strategy_name = next(iter(strat), "")
            b.trades = strat.get(b.strategy_name, {}).get("trades", [])
        cfg = [n for n in names if n.endswith("_config.json")]
        if cfg:
            b.config = json.loads(z.read(cfg[0]))
        py = [n for n in names if n.endswith(".py")]
        if py:
            raw = z.read(py[0])
            b.embedded_strategy = raw.decode("utf-8", "replace")
            b.embedded_strategy_sha = sha256_bytes(raw, 64)
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
    for v in (cfg.get("dry_run_wallet"), metrics.get("starting_balance")):
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


def fnum(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d
