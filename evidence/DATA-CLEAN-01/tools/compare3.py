#!/usr/bin/env python3
"""三方清单对比：路径集合差异 + 大小差异 + 硬链接/符号链接检查。

用法: compare3.py live.json s1.json s2.json outdir
输出 JSON + 人可读摘要。
"""
import json
import os
import sys
from collections import defaultdict


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def key_of(rel):
    """按顶层子目录分组，便于定位。"""
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "(根)"


def main():
    live_p, s1_p, s2_p, outdir = sys.argv[1:5]
    live, s1, s2 = load(live_p), load(s1_p), load(s2_p)
    names = [("live", live), ("s1", s1), ("s2", s2)]

    res = {"counts": {n: len(d) for n, d in names}, "groups": {}}

    # 路径集合
    all_paths = set(live) | set(s1) | set(s2)
    only = {"live": [], "s1": [], "s2": []}
    for p in sorted(all_paths):
        inl, i1, i2 = p in live, p in s1, p in s2
        if inl and i1 and i2:
            continue
        if inl and not i1 and not i2:
            only["live"].append(p)
        elif i1 and not inl and not i2:
            only["s1"].append(p)
        elif i2 and not inl and not i1:
            only["s2"].append(p)
        else:
            # 部分存在
            only["live" if inl else ("s1" if i1 else "s2")].append(p)
    res["only"] = {k: v for k, v in only.items()}

    # 大小差异（三方共有路径）
    common = set(live) & set(s1) & set(s2)
    size_diff = []
    for p in sorted(common):
        a, b, c = live[p].get("size"), s1[p].get("size"), s2[p].get("size")
        if not (a == b == c):
            size_diff.append({"path": p, "live": a, "s1": b, "s2": c})
    res["size_diff"] = size_diff

    # 硬链接 / 符号链接
    hard, sym = [], []
    for nm, d in names:
        for p, v in d.items():
            if v.get("nlink", 1) > 1:
                hard.append({"tree": nm, "path": p, "nlink": v["nlink"], "ino": v["ino"]})
            if v.get("is_link"):
                sym.append({"tree": nm, "path": p})
    res["hardlinks"] = hard
    res["symlinks"] = sym

    # 跨树 inode 相同（说明是硬链接，共享磁盘块，删一处不释放空间）
    ino_map = defaultdict(list)
    for nm, d in names:
        for p, v in d.items():
            if v.get("nlink", 1) > 1:
                ino_map[v["ino"]].append((nm, p))
    shared = {str(k): v for k, v in ino_map.items() if len({t for t, _ in v}) > 1}
    res["cross_tree_shared_inode"] = shared

    # 按顶层分组统计
    groups = defaultdict(lambda: {"files": 0, "bytes": 0})
    for p, v in live.items():
        g = key_of(p)
        groups[g]["files"] += 1
        groups[g]["bytes"] += v.get("size", 0)
    res["live_groups"] = {k: v for k, v in sorted(groups.items(), key=lambda x: -x[1]["bytes"])}

    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "compare3.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    print("=== 条目数 ===")
    for k, v in res["counts"].items():
        print(f"  {k}: {v}")
    print("\n=== 仅单侧存在 ===")
    for k, v in res["only"].items():
        print(f"  {k}: {len(v)} 个")
        for p in v[:10]:
            print(f"      {p}")
    print("\n=== 三方共有但大小不同 ===")
    print(f"  共 {len(size_diff)} 个")
    for r in size_diff[:15]:
        print(f"      {r['path']}  live={r['live']} s1={r['s1']} s2={r['s2']}")
    print("\n=== 硬链接 / 符号链接 ===")
    print(f"  硬链接条目 {len(hard)} 个, 符号链接 {len(sym)} 个")
    print(f"  跨树共享 inode {len(shared)} 组")
    print("\n=== 正式目录按顶层分组 ===")
    for k, v in res["live_groups"].items():
        print(f"  {k:16s} {v['files']:>6d} 文件  {v['bytes']/1024/1024:>9.1f} MB")


if __name__ == "__main__":
    main()
