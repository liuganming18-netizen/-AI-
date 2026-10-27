# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜产物清单（含 SHA-256，供独立审核逐个核对）。"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

BT02 = cm.BT02
OUT = cm.OUT


def main() -> None:
    files = []
    for p in sorted(BT02.rglob("*")):
        if p.is_dir() or p.name.startswith("."):
            continue
        rel = str(p.relative_to(BT02))
        if rel.endswith("16_产物清单.json"):
            continue
        files.append({"path": rel, "bytes": p.stat().st_size, "sha256": cm.sha256_file(p)})
    idx = pd.read_csv(OUT / "10_逐笔复盘索引.csv")
    man = {
        "核对时刻_utc": cm.utc_iso(int(pd.Timestamp.now("UTC").timestamp() * 1000)),
        "根目录": str(BT02),
        "文件数": len(files),
        "总字节": sum(f["bytes"] for f in files),
        "核心计数": {
            "runs": int(idx["run_id"].nunique()),
            "trades": int(len(idx)),
            "symbols": int(idx["symbol"].nunique()),
            "orders": 6790,
            "stop_updates": int(idx["stop_updates"].sum()),
            "trades_with_stage_events": int((idx["n_stage_events"] > 0).sum()),
        },
        "文件": files,
    }
    (OUT / "16_产物清单.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"产物清单：{len(files)} 个文件，{man['总字节']/1e6:.2f} MB")
    for f in files:
        print(f"  {f['bytes']:>10,d}  {f['path']}")


if __name__ == "__main__":
    main()
