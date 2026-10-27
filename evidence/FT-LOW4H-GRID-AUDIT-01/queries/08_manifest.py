# -*- coding: utf-8 -*-
"""08 产物清单：本轮目录下所有文件的相对路径、字节数、SHA-256。"""
from __future__ import annotations

import hashlib
import json

import _lib as L


def main() -> None:
    rows = []
    for p in sorted(L.HERE.rglob("*")):
        if p.is_dir():
            continue
        if p.name == "08_清单.json":
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        rows.append({
            "相对路径": str(p.relative_to(L.HERE)),
            "字节": p.stat().st_size,
            "SHA256": h.hexdigest(),
        })
    out = {"工单": "FT-LOW4H-GRID-AUDIT-01",
           "文件数": len(rows),
           "总字节": sum(r["字节"] for r in rows),
           "文件": rows}
    (L.OUT / "08_清单.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"产物 {out['文件数']} 个，共 {out['总字节']} 字节")
    for r in rows:
        print(f"  {r['字节']:>9}  {r['相对路径']}")


if __name__ == "__main__":
    main()
