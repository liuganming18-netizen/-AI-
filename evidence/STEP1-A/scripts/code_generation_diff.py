# -*- coding: utf-8 -*-
"""STEP1-A 只读核对：把每个「代码代际」与当前工作树逐文件比对。

用途：判断当前 HEAD/工作树是否还能复现某个批次的 run。
只读，不写 run 目录；输出 evidence/STEP1-A/runs/code_generation_diff.csv。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import OrderedDict
from typing import Dict, List

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
RUNS_CSV = os.path.join(ROOT, "evidence", "STEP1-A", "runs", "step1a_runs.csv")
OUT = os.path.join(ROOT, "evidence", "STEP1-A", "runs", "code_generation_diff.csv")


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    with open(RUNS_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # 每个代码代际取一个代表 run（优先 runs_sep_all_latest / 最大成交）
    by_gen: Dict[str, dict] = OrderedDict()
    for r in rows:
        gen = r.get("code_combined_sha256") or ""
        if not gen:
            continue
        if gen not in by_gen:
            by_gen[gen] = r

    cur_cache: Dict[str, str] = {}
    out_rows: List[dict] = []
    for gen, rep in by_gen.items():
        inp = os.path.join(rep["path"], "inputs.json")
        try:
            meta = json.load(open(inp, encoding="utf-8"))
        except Exception:
            continue
        files = ((meta.get("fingerprints") or {}).get("code") or {}).get("files") or {}
        changed, same, missing = [], [], []
        for rel, old in files.items():
            if rel not in cur_cache:
                p = os.path.join(ROOT, rel)
                cur_cache[rel] = sha256_file(p) if os.path.exists(p) else "MISSING"
            new = cur_cache[rel]
            if new == "MISSING":
                missing.append(rel)
            elif new == old:
                same.append(rel)
            else:
                changed.append(rel)
        # 当前树重算 combined
        cur_files = {rel: (None if cur_cache[rel] == "MISSING" else cur_cache[rel])
                     for rel in files}
        cur_combined = hashlib.sha256(
            json.dumps(cur_files, sort_keys=True).encode()).hexdigest()
        out_rows.append({
            "generation_sha256": gen,
            "rep_batch": rep["batch"],
            "rep_run": rep["run_dir_name"],
            "rep_symbol": rep.get("symbol", ""),
            "files_total": len(files),
            "files_same": len(same),
            "files_changed": len(changed),
            "files_missing": len(missing),
            "changed_list": ";".join(os.path.basename(x) for x in changed),
            "current_tree_combined": cur_combined,
            "current_tree_matches_this_gen": (cur_combined == gen),
        })

    keys = list(out_rows[0].keys()) if out_rows else []
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print(f"wrote {OUT} ({len(out_rows)} 代际)")
    for r in out_rows:
        print(f"{r['generation_sha256'][:12]} rep={r['rep_batch']}/{r['rep_symbol']} "
              f"same={r['files_same']}/{r['files_total']} changed={r['files_changed']} "
              f"now_matches={r['current_tree_matches_this_gen']} changed={r['changed_list']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
