# -*- coding: utf-8 -*-
"""BT-LEDGER-FIX-03｜产物清单 + 上游不可变证明。

上游不可变的证明方式（不靠声明）：
  · 对 BT-01 / BT-02 / run 目录三棵树逐文件算 (相对路径, 字节数, mtime_ns, sha256) 的摘要；
  · 断言三棵树里**没有任何文件的 mtime 晚于本单证据目录的创建时刻** ⇒ 本单开工后没人动过上游。
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
ME = cm.ME


def tree_digest(root: Path) -> dict:
    import hashlib
    h = hashlib.sha256()
    n = 0
    total = 0
    max_mtime = 0.0
    per_file = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        st = p.stat()
        rel = str(p.relative_to(root))
        sha = cm.sha256_file(p)
        n += 1
        total += st.st_size
        max_mtime = max(max_mtime, st.st_mtime)
        h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\0{sha}\n".encode())
        per_file[rel] = {"bytes": st.st_size, "mtime": st.st_mtime, "sha256": sha}
    return {"root": str(root), "n_files": n, "total_bytes": total,
            "merkle_sha256": h.hexdigest(),
            "max_mtime": max_mtime,
            "max_mtime_utc": datetime.fromtimestamp(max_mtime, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ") if max_mtime else None,
            "files": per_file}


def main() -> None:
    me_created = ME.stat().st_mtime
    trees = {}
    for name, root in [("WORK-DYNAMIC-BT-01", cm.BT01),
                       ("WORK-DYNAMIC-BT-02", cm.BT02),
                       ("runs_work_dynamic_bt_01", cm.RUNS)]:
        d = tree_digest(root)
        d["all_mtime_before_ticket_start"] = bool(d["max_mtime"] < me_created)
        trees[name] = d
        print(f"{name}: {d['n_files']} files, max_mtime={d['max_mtime_utc']}, "
              f"before_ticket={d['all_mtime_before_ticket_start']}")

    snap = {
        "ticket_start_utc": datetime.fromtimestamp(me_created, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule": "上游只读：本单所有写入都在 evidence/BT-LEDGER-FIX-03/ 内",
        "trees": {k: {kk: vv for kk, vv in v.items() if kk != "files"} for k, v in trees.items()},
        "verdict": ("PASS" if all(t["all_mtime_before_ticket_start"] for t in trees.values())
                    else "FAIL"),
        "per_file_sha256": {k: v["files"] for k, v in trees.items()},
    }
    (OUT / "15_上游不可变快照.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    # 产物清单
    files = []
    for p in sorted(ME.rglob("*")):
        if p.is_file() and p.name != "14_产物清单.json":
            files.append({"path": str(p.relative_to(ME)), "bytes": p.stat().st_size,
                          "sha256": cm.sha256_file(p)})
    manifest = {
        "ticket": "BT-LEDGER-FIX-03",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_files": len(files),
        "files": files,
        "rerun_all": [
            "P=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python",
            "$P code/01_field_inventory.py     # → out/01,02",
            "$P code/02_reconcile_45.py        # → out/03,04,05,06,07",
            "$P code/03_missing_plan.py        # → out/08,09",
            "evidence/FT-BASELINE-01/.venv/bin/python code/04_exporter_dryrun.py  # → out/10（需引擎 venv）",
            "$P code/05_contract_index.py      # → out/11,12,13 + 字段契约_反重力.md",
            "$P code/06_manifest.py            # → out/14,15",
        ],
        "upstream_verdict": snap["verdict"],
    }
    (OUT / "14_产物清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print("artifacts:", len(files), "upstream:", snap["verdict"])


if __name__ == "__main__":
    main()
