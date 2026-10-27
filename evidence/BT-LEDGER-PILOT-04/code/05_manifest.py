# -*- coding: utf-8 -*-
"""BT-LEDGER-PILOT-04｜产物清单 + 上游不可变证明（含本单副作用还原后的复核）。

与 BT-03 的 `06_manifest.py` 的差别（必须说清楚，否则是自欺）：
  · BT-03 用「上游所有文件 mtime < 本单开工时刻」证明上游没被动过；
  · 本单**确实**动过上游（试点触发策略基类硬编码 append，见 12_上游追加写入_取证与还原.json），
    随后 truncate 还原。所以 mtime 判据在本单**必然失败**，不能再用。
  · 本单改用「内容判据」：逐文件 sha256 与 BT-03 快照比对 ⇒ 证明**内容零漂移**；
    mtime 变化只允许出现在 12 号文件登记过的那 2 个文件上，且必须写明原因。

判据分三层：
  C1 内容零漂移：三棵树逐文件 sha256 与 BT-03 快照完全一致（0 改值 / 0 新增 / 0 删除）
  C2 mtime 变化可归因：所有 mtime > 本单开工时刻的文件，必须全部出现在 12 号事故登记表里
  C3 策略/config 身份未变：strategy / base / config 三文件 sha256 == 运行前锁定值

产出：
  out/13_产物清单.json
  out/14_上游不可变快照_内容判据.json
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ME = BASE / "evidence/BT-LEDGER-PILOT-04"
OUT = ME / "out"
BT01 = BASE / "evidence/WORK-DYNAMIC-BT-01"

# BT-03 快照（基线）：内容判据的锚点。树根**直接读快照**，不手抄，避免抄错路径。
BT03_SNAP = BASE / "evidence/BT-LEDGER-FIX-03/out/15_上游不可变快照.json"
INCIDENT = OUT / "00_engine_raw/12_上游追加写入_取证与还原.json"
_SNAP0 = json.loads(BT03_SNAP.read_text(encoding="utf-8"))
TREE_MAP = {name: Path(t["root"]) for name, t in _SNAP0["trees"].items()}

LOCKED = {
    "strategy": BT01 / "user_data/strategies/Wyckoff_C1_S05_D05_Vs04_Z02.py",
    "base": BT01 / "user_data/strategies/WyckoffLow4hGridBase.py",
    "config": BT01 / "user_data/config.json",
}
EXPECTED_LOCKED = {
    "strategy": "4eb070b7892ac9008bc446b627f5b0fd5a41c141848a30a39158f37dfd9c8d2e",
    "base": "d5467a2e413ddb29a34774fecdfcce08fc04b07d58de4504ce468e7b50b03608",
    "config": "88c233f1dfa4676cee34c1453b86bff21d1521058ccdfe4053d0874eda2bc3b3",
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    # 开工时刻必须取**不可变**的登记值：run_meta.json 的 pre.started_at。
    # 不能用 ME.stat().st_mtime —— 目录 mtime 会随本单不断写文件而前移，
    # 一旦前移到事故之后，C2 判据就变成空转（mtime_after 恒 0），等于自欺。
    run_meta = json.loads((OUT / "00_engine_raw/run_meta.json").read_text(encoding="utf-8"))
    ticket_start = datetime.fromisoformat(
        run_meta["pre"]["started_at"].replace("Z", "+00:00")).timestamp()
    snap03 = json.loads(BT03_SNAP.read_text(encoding="utf-8"))
    inc = json.loads(INCIDENT.read_text(encoding="utf-8"))
    incident_files = {r["file"] for r in inc["records"]}   # 形如 out/audit_xxx.csv

    trees, c1, c2 = {}, True, True
    for name, root in TREE_MAP.items():
        base_files = snap03["per_file_sha256"].get(name, {})
        cur_files, changed, added, removed, mtime_after = {}, [], [], [], []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            st = p.stat()
            sha = sha256_file(p)
            cur_files[rel] = {"bytes": st.st_size, "mtime": st.st_mtime,
                              "mtime_utc": utc(st.st_mtime), "sha256": sha}
            if st.st_mtime > ticket_start:
                mtime_after.append(rel)
            old = base_files.get(rel)
            if old is None:
                added.append(rel)
            elif old["sha256"] != sha:
                changed.append({"path": rel, "snapshot_sha256": old["sha256"],
                                "current_sha256": sha})
        for rel in base_files:
            if rel not in cur_files:
                removed.append(rel)

        # C2：mtime 变了的文件必须全部是事故登记过的
        unattributed = [r for r in mtime_after if r not in incident_files]
        tree_ok = (not changed) and (not added) and (not removed)
        c1 = c1 and tree_ok
        c2 = c2 and (not unattributed)
        trees[name] = {
            "root": str(root),
            "n_files_snapshot": len(base_files),
            "n_files_current": len(cur_files),
            "content_changed": changed,
            "content_added": added,
            "content_removed": removed,
            "content_zero_drift": tree_ok,
            "mtime_after_ticket_start": mtime_after,
            "mtime_unattributed": unattributed,
        }
        print(f"{name}: files {len(base_files)}→{len(cur_files)} "
              f"changed={len(changed)} added={len(added)} removed={len(removed)} "
              f"mtime_after={len(mtime_after)} unattributed={len(unattributed)}")

    # C2 空转守卫：事故登记过 N 个文件，若这 N 个文件一个都没被 mtime 抓到，
    # 说明判据没咬到东西（典型原因：开工时刻取错、被目录 mtime 前移）⇒ 判据无效，不是通过。
    seen = {r for t in trees.values() for r in t["mtime_after_ticket_start"]}
    c2_vacuous = len(incident_files) > 0 and not (incident_files & seen)
    c2 = c2 and not c2_vacuous

    # C3：三文件身份
    locked = {}
    for k, p in LOCKED.items():
        cur = sha256_file(p)
        locked[k] = {"path": str(p), "sha256": cur,
                     "expected": EXPECTED_LOCKED[k], "equal": cur == EXPECTED_LOCKED[k]}
    c3 = all(v["equal"] for v in locked.values())

    snap = {
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticket_start_utc": utc(ticket_start),
        "baseline": str(BT03_SNAP),
        "baseline_sha256": sha256_file(BT03_SNAP),
        "why_not_mtime_only": (
            "本单试点触发了策略基类硬编码的 evidence/WORK-DYNAMIC-BT-01/out 追加写入，"
            "随后 truncate 还原 ⇒ mtime 必然变。故不能像 BT-03 那样只靠 mtime 判据，"
            "改用「内容 sha256 零漂移 + mtime 变化可归因」双判据。"
        ),
        "criteria": {
            "C1_content_zero_drift": {
                "pass": c1,
                "rule": "三棵树逐文件 sha256 与 BT-03 快照完全一致（0 改值 / 0 新增 / 0 删除）",
            },
            "C2_mtime_change_attributable": {
                "pass": c2,
                "rule": "所有 mtime > 本单开工时刻的文件，必须全部出现在 12 号事故登记表内",
                "ticket_start_source": "out/00_engine_raw/run_meta.json → pre.started_at（不可变登记值）",
                "not_ME_dir_mtime_because": (
                    "ME 目录 mtime 会随本单持续写文件而前移；一旦前移到事故之后，"
                    "本判据会恒返 mtime_after=0（空转），看着通过其实什么都没检查。"
                ),
                "incident_files": sorted(incident_files),
                "incident_files_caught_by_mtime": sorted(incident_files & seen),
                "vacuous": c2_vacuous,
            },
            "C3_identity_locked": {
                "pass": c3,
                "rule": "strategy / base / config 三文件 sha256 == 运行前锁定值",
            },
        },
        "trees": trees,
        "locked_files": locked,
        "verdict": "PASS" if (c1 and c2 and c3) else "FAIL",
    }
    (OUT / "14_上游不可变快照_内容判据.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 产物清单 ──
    files = []
    for p in sorted(ME.rglob("*")):
        if p.is_file() and p.name not in ("13_产物清单.json",):
            files.append({"path": str(p.relative_to(ME)), "bytes": p.stat().st_size,
                          "sha256": sha256_file(p)})
    manifest = {
        "ticket": "BT-LEDGER-PILOT-04",
        "scope": "单组（1/45）补证试点，不外推 45 组",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_files": len(files),
        "files": files,
        "rerun": [
            "# 全链路复现（顺序敏感：01 必须先跑，且必须用引擎 venv、cwd=项目根）",
            "cd /Users/boos/Desktop/威科夫因子共振趋势",
            "# 0) 先重定向策略审计输出目录，否则会再次刷新 BT-01 旧证据（见 12 号文件）",
            "evidence/FT-BASELINE-01/.venv/bin/python evidence/BT-LEDGER-PILOT-04/code/01_pilot_run.py",
            "P=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python",
            "$P evidence/BT-LEDGER-PILOT-04/code/02_compare.py    # → out/01..07",
            "$P evidence/BT-LEDGER-PILOT-04/code/03_export.py     # → out/06,08,09,10,11",
            "$P evidence/BT-LEDGER-PILOT-04/code/04_restore_upstream.py  # → out/00_engine_raw/12",
            "$P evidence/BT-LEDGER-PILOT-04/code/06_verify_report.py     # → out/15（报告数字机械复算）",
            "$P evidence/BT-LEDGER-PILOT-04/code/05_manifest.py   # → out/13,14（必须最后跑）",
        ],
        "upstream_verdict": snap["verdict"],
    }
    (OUT / "13_产物清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print("artifacts:", len(files), "upstream:", snap["verdict"])


if __name__ == "__main__":
    main()
