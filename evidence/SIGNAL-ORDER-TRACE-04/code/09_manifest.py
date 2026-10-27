# -*- coding: utf-8 -*-
"""09｜交付清单（manifest.json）：逐文件 SHA-256 + 关键输入/输出指纹。

纪律（项目既有）：
  · 版本只认文件 SHA-256，不认「我改过了」这种说法；
  · 哈希声明会当场失效 ⇒ manifest 自带 `generated_at_utc`，结论绑定时刻；
  · 生产侧只做**只读指纹**（账本 SHA + runs 目录条目数），证明本单没动生产。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

SKIP_DIRS = {"__pycache__"}


def main():
    files = []
    total = 0
    for root, dirs, names in os.walk(cm.ME):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            p = Path(root) / n
            if p.name == "manifest.json":
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            files.append({"path": str(p.relative_to(cm.ME)), "bytes": size,
                          "sha256": cm.sha256_file(p)})
            total += size

    prod_ledger = cm.PROD_LEDGER_DIR / f"paper_{cm.PROD_ARM}.sqlite3"
    prod_runs = cm.BASE / "回测日志" / "event_trend_v0_1" / "runs"

    out = {
        "generated_at_utc": cm.utc_now(),
        "ticket": "SIGNAL-ORDER-TRACE-04",
        "base": str(cm.ME),
        "n_files": len(files),
        "total_bytes": total,
        "files": files,
        "key_fingerprints": {
            "isolated_market_db": {
                "path": str(cm.ISO_MARKET_DB),
                "sha256": cm.sha256_file(cm.ISO_MARKET_DB) if cm.ISO_MARKET_DB.exists() else None,
            },
            "isolated_ledgers": {
                sub: (cm.sha256_file(cm.ISO_LEDGER_DIR / sub / f"paper_{cm.PROD_ARM}.sqlite3")
                      if (cm.ISO_LEDGER_DIR / sub / f"paper_{cm.PROD_ARM}.sqlite3").exists()
                      else None)
                for sub in ("prod_epoch", "replay", "gate_closed",
                            "duplicate_open", "duplicate_gate")
            },
            "production_ledger_readonly": {
                "path": str(prod_ledger),
                "sha256": cm.sha256_file(prod_ledger) if prod_ledger.exists() else None,
                "note": "只读核对；本单不写生产账本（见 07 的 before/after）",
            },
            "production_runs_dir": {
                "path": str(prod_runs),
                "n_entries": len(list(prod_runs.iterdir())) if prod_runs.is_dir() else None,
                "note": "只读；本单 run 目录全部落在 out/iso_runs",
            },
        },
        "runtime": {
            "python": sys.version.split()[0],
            "executable": sys.executable,
        },
    }
    p = cm.ME / "manifest.json"
    p.write_text(__import__("json").dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"manifest: {len(files)} 个文件, {total / 1e6:.1f} MB -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
