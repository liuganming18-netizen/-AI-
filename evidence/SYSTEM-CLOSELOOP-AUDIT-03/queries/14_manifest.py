#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""14_manifest.py —— 生成本轮全部产物的逐文件 SHA-256 清单。

工单要求：「开始与结束核对文件、配置和运行进程版本」。
00_freeze.py / 12_end_version.py 已对**被审对象**（148 个工程文件）做了开工/收工两次快照；
本脚本补的是**本轮审计产物自身**的指纹，用于事后证明「报告引用的证据文件没被替换」。

输出：05_文件清单与哈希.csv（UTF-8 with BOM，Excel 可直接打开）
列：相对路径, 字节数, 修改时间, SHA-256, 类别
"""
import csv
import hashlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "05_文件清单与哈希.csv"
SELF = Path(__file__).resolve()

# 排除：本清单自己（自指哈希无意义）、缓存目录
SKIP_NAMES = {OUT.name}
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", ".git"}


def kind(rel: str) -> str:
    if rel.startswith("queries/"):
        return "脚本"
    if rel.startswith("out/"):
        return "原始数据"
    if rel.startswith("shots/"):
        return "截图"
    if rel.endswith(".md"):
        return "报告"
    if rel.endswith(".csv"):
        return "清单"
    return "其他"


def main() -> None:
    rows = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if p.name in SKIP_NAMES:
            continue
        data = p.read_bytes()
        rows.append({
            "相对路径": rel,
            "字节数": len(data),
            "修改时间": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "SHA-256": hashlib.sha256(data).hexdigest(),
            "类别": kind(rel),
        })

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["相对路径", "字节数", "修改时间", "SHA-256", "类别"])
        w.writeheader()
        w.writerows(rows)

    # ── 控制台摘要（报告要引用，必须可复算）────────────────────
    by_kind = {}
    for r in rows:
        by_kind[r["类别"]] = by_kind.get(r["类别"], 0) + 1
    total = sum(r["字节数"] for r in rows)
    print(f"产物文件 {len(rows)} 个，合计 {total:,} 字节")
    for k in sorted(by_kind):
        n = by_kind[k]
        b = sum(r["字节数"] for r in rows if r["类别"] == k)
        print(f"  {k:<6} {n:>3} 个  {b:>10,} 字节")
    print(f"已写出 {OUT}")

    # 自检：清单里必须能查到五问对应的关键证据文件
    need = [
        "00_报告.md",
        "06_报告自检.md",
        "out/02_因果链.json",
        "out/03_跟踪止损与退出.json",
        "out/04_回测与模拟对账.json",
        "out/05_隔离与恢复.json",
        "out/07_UI核心操作.json",
        "out/09_账本身份.json",
        "out/10_59条逐项.json",
        "out/12_结束版本核对.json",
        "out/13_报告对账.json",
        "queries/10_checklist.py",
    ]
    have = {r["相对路径"] for r in rows}
    miss = [n for n in need if n not in have]
    print("关键证据文件缺失：" + ("无" if not miss else str(miss)))


if __name__ == "__main__":
    main()
