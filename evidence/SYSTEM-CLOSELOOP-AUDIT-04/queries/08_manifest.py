# -*- coding: utf-8 -*-
"""08 产物清单：本轮 evidence/SYSTEM-CLOSELOOP-AUDIT-04 下每个文件的 SHA-256。

产物：05_文件清单与哈希.csv（同目录）
"""
from __future__ import annotations

import csv
import hashlib
import pathlib

EVID = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-CLOSELOOP-AUDIT-04")


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> None:
    rows = []
    for p in sorted(EVID.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "05_文件清单与哈希.csv":
            continue
        st = p.stat()
        rows.append({"文件": p.relative_to(EVID).as_posix(), "字节": st.st_size,
                     "sha256": sha(p)})
    out = EVID / "05_文件清单与哈希.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["文件", "字节", "sha256"])
        w.writeheader()
        w.writerows(rows)
    total = sum(r["字节"] for r in rows)
    print(f"产物 {len(rows)} 个，合计 {total/1024:.1f} KB → {out}")


if __name__ == "__main__":
    main()
