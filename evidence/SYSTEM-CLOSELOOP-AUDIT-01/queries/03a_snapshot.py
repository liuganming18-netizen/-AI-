#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03a_snapshot.py —— 审核期账本/信号库/闸门快照（只读拷贝 + SHA-256）

为什么必须先快照
----------------
实测发现：**审核期间生产账本正被并发写入**——
  · `数据/paper/paper_3a.sqlite3` mtime = 2026-10-22 19:31:27（3b/3c/3d 仍停在 10-18）
  · `框架/backend/live/paper_new_entry_gate.json` mtime = 19:30:03，内容已被翻成
    `allow_new_entries: true`（`set_by: user_ui_control`）
  · `框架/backend/live/live_signals.sqlite3` 出现 3 条 `sig_closeloop_btc_*` 合成信号

⇒ 直接读活库会得到**移动靶**。本脚本把活库拷成冻结副本并落 SHA-256，
  后续 L1–L7 一律对副本复算；再取第二份快照比对，用**哈希变化本身**证明并发写入。

用法：python 03a_snapshot.py [tag]
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJ = ROOT.parent.parent
OUT = ROOT / "out"
SNAP = OUT / "snapshots"

TARGETS = [
    PROJ / "数据" / "paper" / f"paper_{a}.sqlite3" for a in ("3a", "3b", "3c", "3d")
] + [
    PROJ / "数据" / "paper" / "ledger_identity.json",
    PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3",
    PROJ / "框架" / "backend" / "live" / "paper_new_entry_gate.json",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_consistent(src: Path, dst: Path) -> str:
    """用 sqlite 在线备份接口取一致副本（避免拷到写一半的页）；非 sqlite 直接拷。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".sqlite3":
        try:
            con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                out = sqlite3.connect(str(dst))
                try:
                    con.backup(out)
                finally:
                    out.close()
            finally:
                con.close()
            return "sqlite_backup"
        except Exception as e:                                # noqa: BLE001
            shutil.copy2(src, dst)
            return f"copy2_fallback({type(e).__name__})"
    shutil.copy2(src, dst)
    return "copy2"


def row_counts(p: Path) -> dict:
    if p.suffix != ".sqlite3":
        return {}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}
        finally:
            con.close()
    except Exception as e:                                    # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now().strftime("%H%M%S")
    d = SNAP / tag
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for src in TARGETS:
        if not src.exists():
            rows.append({"源文件": str(src.relative_to(PROJ)), "存在": False})
            continue
        dst = d / src.name
        how = copy_consistent(src, dst)
        st = src.stat()
        rows.append({
            "源文件": str(src.relative_to(PROJ)),
            "存在": True,
            "字节": st.st_size,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "源SHA256": sha256(src),
            "快照SHA256": sha256(dst),
            "拷贝方式": how,
            "行数": row_counts(dst),
        })
    doc = {"快照时刻": dt.datetime.now().isoformat(timespec="seconds"), "tag": tag,
           "说明": "只读拷贝；源库可能正被并发写入，故用 sqlite 在线备份取一致副本",
           "文件": rows}
    (d / "_snapshot.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(json.dumps({"快照目录": str(d), "文件数": len(rows)}, ensure_ascii=False))
    for r in rows:
        if not r.get("存在"):
            print(f"  缺失 {r['源文件']}")
            continue
        print(f"  {r['源文件']:44s} {r['字节']:>9d}  {r['mtime']}  {r['源SHA256'][:16]}")
        nz = {k: v for k, v in (r.get("行数") or {}).items() if v}
        if nz:
            print(f"      非空表：{nz}")


if __name__ == "__main__":
    main()
