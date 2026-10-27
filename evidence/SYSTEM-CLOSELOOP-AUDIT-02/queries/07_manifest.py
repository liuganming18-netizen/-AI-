# -*- coding: utf-8 -*-
"""07 产物清单 —— 本轮交付了哪些文件，各自 SHA-256，便于第三方独立核对。

产物：out/07_产物清单.json、out/07_产物清单.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import pathlib

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def kind(p: pathlib.Path) -> str:
    s = p.suffix.lower()
    if s == ".py":
        return "脚本"
    if s == ".json":
        return "结论数据"
    if s == ".md":
        return "报告"
    if s == ".csv":
        return "表格"
    return "其他"


def main() -> None:
    rows = []
    for p in sorted(HERE.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(HERE).as_posix()
        if rel.startswith("out/07_"):
            continue
        st = p.stat()
        rows.append({"相对路径": rel, "类别": kind(p), "字节": st.st_size,
                     "sha256": sha(p),
                     "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")})
    cnt: dict[str, int] = {}
    for r in rows:
        cnt[r["类别"]] = cnt.get(r["类别"], 0) + 1
    res = {"生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "文件数": len(rows), "按类别": cnt, "明细": rows}
    (HERE / "out/07_产物清单.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(HERE / "out/07_产物清单.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["相对路径", "类别", "字节", "sha256", "mtime"])
        w.writeheader()
        w.writerows(rows)

    print(f"=== 产物清单 ===  共 {len(rows)} 个文件")
    for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print("\n关键文件：")
    for r in rows:
        if r["相对路径"] in ("00_报告.md",):
            print(f"  {r['相对路径']}  {r['字节']} 字节  sha256={r['sha256'][:16]}")


if __name__ == "__main__":
    main()
