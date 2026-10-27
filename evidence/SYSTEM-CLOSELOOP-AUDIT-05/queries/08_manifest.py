# -*- coding: utf-8 -*-
"""08 产物清单：本轮 evidence/SYSTEM-CLOSELOOP-AUDIT-05 下每个文件的 SHA-256。

产物：05_文件清单与哈希.csv（同目录）
说明：只列本目录实际落盘文件；不含被审代码（被审代码指纹见 out/00_开工冻结.json）。
"""
from __future__ import annotations

import csv
import hashlib
import pathlib

EVID = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-CLOSELOOP-AUDIT-05")

# 冻结前遗留/需标注的产物：防止复核者把旧文件当正式结果引用
NOTES = {
    "out/03_行情防线.json": "作废：00:48 旧命名且跑在 00:50:30 冻结之前，正式结果见 out/05_行情防线.json",
    "out/03_iso_adapter.log": "第1项用的隔离 adapter 日志",
    "out/05_iso_adapter.log": "第5项用的隔离 adapter 日志",
    "out/07_自检.json": "报告数值机械复算结果（全量明细）",
    "05_文件清单与哈希.csv": "本清单自身（不计入哈希）",
}


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
        rel = p.relative_to(EVID).as_posix()
        rows.append({"文件": rel, "字节": st.st_size, "sha256": sha(p),
                     "说明": NOTES.get(rel, "")})
    out = EVID / "05_文件清单与哈希.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["文件", "字节", "sha256", "说明"])
        w.writeheader()
        w.writerows(rows)
    total = sum(r["字节"] for r in rows)
    void = sum(1 for r in rows if r["说明"].startswith("作废"))
    print(f"产物 {len(rows)} 个（其中作废 {void} 个），合计 {total/1024:.1f} KB → {out}")


if __name__ == "__main__":
    main()
