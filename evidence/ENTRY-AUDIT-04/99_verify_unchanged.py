# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-04 / 99 · 收尾自检：本轮只新增，不动任何既有产物

四件事
------
1. 上游研究产物 + 策略代码 + 合约规格证据：与 ENTRY-AUDIT-03 的冻结哈希逐位比对
2. 反重力产物（FACTOR-ENTRY-01~06）：与 FACTOR-ENTRY-06 自己冻结的 hashes.json 逐位比对
3. 反重力目录当前哈希快照：写进本文件，供 FE07 交付后核对「本窗口没改过它」
4. 声明写入边界：本轮新增文件只落在 evidence/ENTRY-AUDIT-04/

产出：99_verify_result.json
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-04"
A3 = ROOT / "evidence" / "ENTRY-AUDIT-03"
FE06 = ROOT / "evidence" / "FACTOR-ENTRY-06"


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(job) -> dict:
    rel, frozen = job
    p = ROOT / rel
    if not p.exists():
        return {"path": rel, "status": "missing", "sha256_frozen": frozen}
    cur = sha256_file(str(p))
    return {"path": rel, "status": "ok", "sha256_frozen": frozen,
            "sha256_now": cur, "same": cur == frozen}


def main() -> None:
    # ── 1) 上游 + 代码 + 合约（ENTRY-AUDIT-03 冻结清单里的严格项）──
    man = json.loads((A3 / "00_freeze_manifest.json").read_text(encoding="utf-8"))
    strict = [(f["path"], f["sha256"]) for f in man["files"]
              if f["category"] in ("upstream", "code", "contracts")]

    # ── 2) 反重力产物（FE06 自冻结）──
    fe06_h = json.loads((FE06 / "hashes.json").read_text(encoding="utf-8"))
    fe06_items = []
    for group in ("inputs", "outputs"):
        for rel, meta in (fe06_h.get(group) or {}).items():
            fe06_items.append((rel, meta.get("sha256")))

    jobs = strict + fe06_items
    with ProcessPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(check, jobs, chunksize=4))

    s = res[:len(strict)]
    f = res[len(strict):]

    s_changed = [r["path"] for r in s if r.get("same") is False]
    f_changed = [r["path"] for r in f if r.get("same") is False]
    missing = [r["path"] for r in res if r.get("status") == "missing"]

    # ── 3) 反重力目录当前快照（供 FE07 交付后核对）──
    snap = {}
    for p in sorted((ROOT / "evidence").iterdir()):
        if p.is_dir() and p.name.startswith("FACTOR-ENTRY-"):
            for q in sorted(p.rglob("*")):
                if q.is_file() and "__pycache__" not in q.parts:
                    rel = str(q.relative_to(ROOT))
                    snap[rel] = {"sha256": sha256_file(str(q)), "bytes": q.stat().st_size}

    result = {
        "audit": "ENTRY-AUDIT-04",
        "checked_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "upstream_code_contracts": {
            "source": "evidence/ENTRY-AUDIT-03/00_freeze_manifest.json",
            "count": len(s),
            "unchanged": int(sum(1 for r in s if r.get("same"))),
            "changed": s_changed,
            "verdict": "零改动" if not s_changed else "有改动（必须查）",
        },
        "antigravity_artifacts": {
            "source": "evidence/FACTOR-ENTRY-06/hashes.json（反重力自己冻结的输入与输出）",
            "count": len(f),
            "unchanged": int(sum(1 for r in f if r.get("same"))),
            "changed": f_changed,
            "verdict": "零改动" if not f_changed else "有改动（必须查）",
        },
        "antigravity_dir_snapshot": {
            "why": ("FE07 交付后若这些哈希变了，说明变化发生在 FE07 那一侧，"
                    "不是本窗口改的；本窗口只读不写"),
            "files": len(snap),
            "snapshot": snap,
        },
        "missing_files": missing,
        "write_scope": {
            "declared": "本轮新增文件全部在 evidence/ENTRY-AUDIT-04/ 内",
            "outside_writes": [],
            "not_modified": ["原账本", "反重力产物", "正式策略", "共享撮合",
                             "UI", "API", "采集器", "交易闸门"],
            "no_rerun": "本轮未重跑任何回测，只读 ENTRY-AUDIT-03 的既有产物",
        },
        "hard_verdict": {
            "upstream_unchanged": not s_changed,
            "antigravity_unchanged": not f_changed,
            "no_missing": not missing,
            "pass": (not s_changed and not f_changed and not missing),
        },
    }
    (OUT / "99_verify_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: result[k] for k in ("upstream_code_contracts", "antigravity_artifacts",
                                             "hard_verdict")}, ensure_ascii=False, indent=1))
    print(f"反重力目录快照：{len(snap)} 个文件")


if __name__ == "__main__":
    main()
