#!/usr/bin/env python3
"""校验废纸篓内的文件与移动前清单逐一对齐（数量 + 字节 + 存在性）。"""
import json
import os
import sys

T = "/Users/boos/.Trash"
EV = "/Users/boos/Desktop/威科夫因子共振趋势/evidence/DATA-CLEAN-01"

BATCHES = [
    ("第一批", os.path.join(T, "DATA-CLEAN-01_20261019T102236/20261014T192054Z"),
     os.path.join(EV, "scan_s2.json"), None),
    ("第二批", os.path.join(T, "DATA-CLEAN-01_20261019T102334/ohlcv"),
     os.path.join(EV, "scan_live.json"), "ohlcv/"),
    ("第三批", os.path.join(T, "DATA-CLEAN-01_20261019T102404/oi_metrics"),
     os.path.join(EV, "scan_live.json"), "oi_metrics/"),
]

all_ok = True
for name, dest, manifest, prefix in BATCHES:
    src = json.load(open(manifest, encoding="utf-8"))
    if prefix:
        expect = {k[len(prefix):]: v["size"] for k, v in src.items() if k.startswith(prefix)}
    else:
        expect = {k: v["size"] for k, v in src.items()}

    missing, size_bad, ok = [], [], 0
    for rel, size in expect.items():
        p = os.path.join(dest, rel)
        try:
            st = os.stat(p)
            if st.st_size != size:
                size_bad.append((rel, size, st.st_size))
            else:
                ok += 1
        except FileNotFoundError:
            missing.append(rel)
        except Exception as e:
            missing.append(f"{rel} ({type(e).__name__})")

    status = "通过" if not missing and not size_bad else "不通过"
    if missing or size_bad:
        all_ok = False
    print(f"{name}: 期望 {len(expect)} 个 → 对齐 {ok} 个, 缺失 {len(missing)}, 大小不符 {len(size_bad)}  [{status}]")
    for m in missing[:5]:
        print(f"    缺失: {m}")
    for r, a, b in size_bad[:5]:
        print(f"    大小不符: {r} 期望{a} 实际{b}")

print()
print("总判定:", "全部对齐，移动未丢任何文件" if all_ok else "存在不一致，需人工复核")
sys.exit(0 if all_ok else 1)
