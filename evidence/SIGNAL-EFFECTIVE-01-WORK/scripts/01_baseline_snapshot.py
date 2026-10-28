# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-01-WORK｜只读基线快照（一致性在线备份）。

纪律：
- **只用 sqlite3 在线备份 API**（`Connection.backup`）取一致快照；
  **不**单独复制主文件、**不**用文件哈希代替含 WAL 的一致快照。
- 对源库只读打开（不建表、不写、不改 WAL 内容）；写出的只是 `baseline/` 下的副本。
- 快照时刻、语句、路径、sha256 全部落 `baseline/BASELINE.json`。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJ / "evidence" / "SIGNAL-EFFECTIVE-01-WORK" / "baseline"
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "live_signals": PROJ / "框架/backend/live/live_signals.sqlite3",
    "paper_3a": PROJ / "数据/paper/paper_3a.sqlite3",
    "paper_3b": PROJ / "数据/paper/paper_3b.sqlite3",
    "paper_3c": PROJ / "数据/paper/paper_3c.sqlite3",
    "paper_3d": PROJ / "数据/paper/paper_3d.sqlite3",
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def consistent_copy(src: Path, dst: Path) -> dict:
    """在线备份 API 取一致快照（源库只读打开）。"""
    if not src.exists():
        return {"source": str(src), "status": "missing"}
    # WAL 库若没有 -shm，`mode=ro` 无法打开（备份 API 需要 shm）。
    # 故用普通连接 + `PRAGMA query_only=1` 兜底：任何误写都会直接报错，
    # 备份本身只读源库。副作用仅可能新建空的 -shm/-wal，不改任何数据页。
    con = sqlite3.connect(str(src), timeout=20.0)
    con.execute("PRAGMA query_only=1")
    try:
        bak = sqlite3.connect(str(dst))
        try:
            with bak:
                con.backup(bak)
        finally:
            bak.close()
        # 快照自证：能打开、能读 sqlite_master。
        # 备份出来的副本会继承 WAL 模式（无 -shm 时 `mode=ro` 打不开），
        # 故把**副本**转成单文件 DELETE 模式并做完整性检查（只动副本，不动源库）。
        chk = sqlite3.connect(str(dst), timeout=20.0)
        try:
            chk.execute("PRAGMA journal_mode=DELETE")
            tables = [r[0] for r in chk.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            chk.close()
    finally:
        con.close()
    return {
        "source": str(src),
        "snapshot": str(dst),
        "source_size": src.stat().st_size,
        "source_mtime_ms": int(src.stat().st_mtime * 1000),
        "snapshot_size": dst.stat().st_size,
        "snapshot_sha256": sha256_file(dst),
        "integrity_check": integrity,
        "tables": tables,
        "status": "ok",
    }


def main() -> int:
    now = int(time.time() * 1000)
    report = {
        "generated_at_ms": now,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now / 1000)),
        "method": "sqlite3 Connection.backup 在线备份（一致性快照，含 WAL 已提交内容）",
        "note": ("源库在运行中持续被写入；本快照只代表快照时刻。"
                 "**不使用**主文件单独复制或文件哈希作为一致性证据。"),
        "items": {},
    }
    for name, src in SOURCES.items():
        dst = OUT / f"{name}_snapshot.sqlite3"
        r = consistent_copy(src, dst)
        report["items"][name] = r
        print(f"{name:14s} {r['status']:7s} "
              f"{r.get('snapshot_size', 0):>10d} B  tables={len(r.get('tables', []))}  "
              f"{r.get('integrity_check', '')}")
    p = OUT / "BASELINE.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
