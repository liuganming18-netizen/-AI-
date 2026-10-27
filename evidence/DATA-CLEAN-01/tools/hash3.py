#!/usr/bin/env python3
"""三方全量内容哈希：对每个相对路径算 sha256，输出对比结果。

只读。使用线程池并发读盘。
用法: hash3.py live.json s1.json s2.json outdir [workers]
"""
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

BASE = "/Users/boos/Desktop/威科夫因子共振趋势/数据/研究输入/WyckoffMaster"
TREES = {
    "live": BASE,
    "s1": os.path.join(BASE, "_backup/20261014T190050Z"),
    "s2": os.path.join(BASE, "_backup/20261014T192054Z"),
}


def sha256_of(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def job(args):
    tree, rel = args
    p = os.path.join(TREES[tree], rel)
    try:
        return tree, rel, sha256_of(p)
    except Exception as e:
        return tree, rel, f"ERR:{type(e).__name__}"


def main():
    live_p, s1_p, s2_p, outdir = sys.argv[1:5]
    workers = int(sys.argv[5]) if len(sys.argv) > 5 else 8
    scans = {k: json.load(open(v, encoding="utf-8")) for k, v in
             zip(["live", "s1", "s2"], [live_p, s1_p, s2_p])}

    common = set(scans["live"]) & set(scans["s1"]) & set(scans["s2"])
    print(f"三方共有路径 {len(common)} 个，开始哈希（并发 {workers}）")
    tasks = [(t, rel) for rel in sorted(common) for t in TREES]
    res = {"live": {}, "s1": {}, "s2": {}}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for tree, rel, h in ex.map(job, tasks):
            res[tree][rel] = h
            done += 1
            if done % 1000 == 0:
                print(f"  {done}/{len(tasks)}")

    # 三方比对
    diff = []
    for rel in sorted(common):
        a, b, c = res["live"][rel], res["s1"][rel], res["s2"][rel]
        if not (a == b == c):
            diff.append({"path": rel, "live": a[:16], "s1": b[:16], "s2": c[:16],
                         "live_eq_s1": a == b, "live_eq_s2": a == c, "s1_eq_s2": b == c})

    os.makedirs(outdir, exist_ok=True)
    out = {"total": len(common), "identical_all": len(common) - len(diff),
           "diff_count": len(diff), "diffs": diff,
           "hashes": {t: res[t] for t in TREES}}
    with open(os.path.join(outdir, "hash3.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\n三方全同: {out['identical_all']} / {len(common)}")
    print(f"存在差异: {len(diff)} 个")
    for d in diff[:20]:
        print(f"  {d['path']}  live=s1:{d['live_eq_s1']} live=s2:{d['live_eq_s2']} s1=s2:{d['s1_eq_s2']}")


if __name__ == "__main__":
    main()
