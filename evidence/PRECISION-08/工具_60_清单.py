#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-08 工具 60：产物清单 + 任务前后差异。

两件事：
  1. 给本目录每个产物算 SHA256（验收方据此确认「读到的就是我算的」）；
  2. 对比 `基线_开工.json` 与 `基线_收工.json`，逐项报告
     —— 21 项输入有没有被改动、git 状态多了什么、少了什么。

用法：
  python3 工具_00_基线.py --label 收工
  python3 工具_60_清单.py
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SKIP_DIRS = {"tmp_pytest", "__pycache__", ".pytest_cache"}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_outputs() -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(HERE):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in ("产物SHA256清单.json", "产物SHA256清单.txt"):
                continue
            out.append(os.path.relpath(os.path.join(dirpath, fn), HERE))
    return sorted(out)


def compare_baseline() -> Dict[str, Any]:
    p0 = os.path.join(HERE, "基线_开工.json")
    p1 = os.path.join(HERE, "基线_收工.json")
    if not (os.path.isfile(p0) and os.path.isfile(p1)):
        return {"status": "缺基线文件，未比对"}
    a = json.load(open(p0, encoding="utf-8"))
    b = json.load(open(p1, encoding="utf-8"))

    changed, added, removed = [], [], []
    for k, v in a["inputs"].items():
        if k not in b["inputs"]:
            removed.append(k)
        elif b["inputs"][k]["sha256"] != v["sha256"]:
            changed.append({"path": k, "before": v["sha256"], "after": b["inputs"][k]["sha256"]})
    for k in b["inputs"]:
        if k not in a["inputs"]:
            added.append(k)

    sa, sb = set(a["git_status_others"]), set(b["git_status_others"])
    return {
        "status": "ok",
        "branch_same": a["branch"] == b["branch"],
        "head_before": a["head"], "head_after": b["head"],
        "head_same": a["head"] == b["head"],
        "inputs_count_before": a["inputs_count"], "inputs_count_after": b["inputs_count"],
        "inputs_changed": changed, "inputs_added": added, "inputs_removed": removed,
        "git_total_before": a["git_status_lines_total"],
        "git_total_after": b["git_status_lines_total"],
        "git_mine_before": a["git_status_mine"],
        "git_mine_after": b["git_status_mine"],
        "git_others_new": sorted(sb - sa),
        "git_others_gone": sorted(sa - sb),
    }


def verify_runs_against_p07() -> Dict[str, Any]:
    """把 31 个 run 的 186 个产物与 P07 收工时记录的哈希逐项比对。

    为什么用 P07 的记录而不是自己再采一次「开工」：本窗口开工时没有采集 run 产物，
    事后补采只能证明「现在 = 现在」。P07 在 2026-10-17T15:06 采过同样的 186 个文件，
    拿它的记录做锚点，才能证明「从 P07 收工到本单收工，run 产物一个字节没动」。
    """
    p07 = os.path.join(ROOT, "evidence", "PRECISION-07", "基线_收工.json")
    if not os.path.isfile(p07):
        return {"status": "P07 基线缺失"}
    rec = json.load(open(p07, encoding="utf-8"))
    runs = [x for x in rec["files"] if x.get("group") == "run"]
    same = changed = missing = 0
    diffs = []
    for x in runs:
        fp = os.path.join(ROOT, x["path"])
        if not os.path.isfile(fp):
            missing += 1
            diffs.append({"path": x["path"], "state": "missing"})
            continue
        h = sha256(fp)
        if h == x["sha256"]:
            same += 1
        else:
            changed += 1
            diffs.append({"path": x["path"], "before": x["sha256"], "after": h})
    return {"status": "ok", "anchor": "PRECISION-07/基线_收工.json",
            "anchor_utc": rec.get("generated_utc"), "files": len(runs),
            "same": same, "changed": changed, "missing": missing,
            "diffs": diffs[:20]}


def main() -> int:
    files = walk_outputs()
    doc = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "dir": HERE, "file_count": len(files),
           "files": {f: {"sha256": sha256(os.path.join(HERE, f)),
                         "bytes": os.path.getsize(os.path.join(HERE, f))}
                     for f in files},
           "baseline_compare": compare_baseline(),
           "runs_vs_p07": verify_runs_against_p07()}
    with open(os.path.join(HERE, "产物SHA256清单.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    L = [f"PRECISION-08 产物清单 · {len(files)} 个文件", "=" * 78]
    for f, v in doc["files"].items():
        L.append(f"{v['sha256']}  {v['bytes']:>9}  {f}")
    c = doc["baseline_compare"]
    L += ["", "任务前后差异", "=" * 78]
    if c.get("status") != "ok":
        L.append(f"  {c.get('status')}")
    else:
        L += [f"  分支未变        : {c['branch_same']}",
              f"  HEAD            : {c['head_before'][:12]} → {c['head_after'][:12]}"
              f"  （未变={c['head_same']}）",
              f"  输入项          : {c['inputs_count_before']} → {c['inputs_count_after']}",
              f"  输入哈希变化    : {len(c['inputs_changed'])} 项"]
        for x in c["inputs_changed"]:
            L.append(f"      !! {x['path']}\n         before={x['before'][:16]} after={x['after'][:16]}")
        L += [f"  输入新增/删除   : {len(c['inputs_added'])} / {len(c['inputs_removed'])}",
              f"  git status 行数 : {c['git_total_before']} → {c['git_total_after']}",
              f"  本窗口条目      : {c['git_mine_before']} → {c['git_mine_after']}",
              f"  他人新增条目    : {len(c['git_others_new'])} 条"]
        for x in c["git_others_new"][:20]:
            L.append(f"      + {x}")
        L += [f"  他人消失条目    : {len(c['git_others_gone'])} 条"]
        for x in c["git_others_gone"][:10]:
            L.append(f"      - {x}")
    r = doc["runs_vs_p07"]
    L += ["", "31 个 run 的 186 个产物 vs P07 收工锚点", "=" * 78]
    if r.get("status") != "ok":
        L.append(f"  {r.get('status')}")
    else:
        L += [f"  锚点时间        : {r['anchor_utc']}",
              f"  比对文件数      : {r['files']}",
              f"  一致 / 变化 / 缺失: {r['same']} / {r['changed']} / {r['missing']}"]
        for d in r["diffs"]:
            L.append(f"      !! {d}")
    txt = "\n".join(L)
    with open(os.path.join(HERE, "产物SHA256清单.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)

    ok = (c.get("status") == "ok" and not c["inputs_changed"]
          and not c["inputs_added"] and not c["inputs_removed"]
          and doc["runs_vs_p07"].get("changed") == 0
          and doc["runs_vs_p07"].get("missing") == 0)
    print(f"\n自检：21 项输入零改动 + 186 个 run 产物零改动 = {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
