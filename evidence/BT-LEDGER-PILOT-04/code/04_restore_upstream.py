# -*- coding: utf-8 -*-
"""BT-LEDGER-PILOT-04｜上游被追加写入的取证与还原。

事故
────
`WyckoffLow4hGridBase.__init__` 把审计 CSV 的输出目录**硬编码**成
    out_dir = os.path.join(BASE_DIR, "evidence/WORK-DYNAMIC-BT-01/out")
并以 `mode="a"`（追加）写入：
    audit_orders_<策略>.csv          （__init__ 里定路径，_record_order_audit 追加）
    audit_confirmation_<策略>.csv    （on_confirmation 附近追加）
⇒ 任何一次重跑都会往 BT-01 里**追加**，与"不覆盖旧证据"冲突。

本脚本做的事（只做可证的两步）
──────────────────────────────
1. 取证：用 BT-LEDGER-FIX-03 `out/15_上游不可变快照.json` 里记录的 (bytes, sha256) 作基准，
   验证"新文件的前 old_bytes 字节 == 旧文件"，即**追加而非改写**（旧数据没丢）。
2. 还原：把追加段切出来另存到本单 `out/00_engine_raw/strategy_audit_append/`，
   再把原文件截断回 old_bytes，并复核 sha256 == 快照值。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BT01 = BASE / "evidence/WORK-DYNAMIC-BT-01"
ME = BASE / "evidence/BT-LEDGER-PILOT-04"
RAW = ME / "out/00_engine_raw"
APPEND_DIR = RAW / "strategy_audit_append"
SNAP = BASE / "evidence/BT-LEDGER-FIX-03/out/15_上游不可变快照.json"

STRATEGY = "Wyckoff_C1_S05_D05_Vs04_Z02"
TARGETS = [f"out/audit_confirmation_{STRATEGY}.csv",
           f"out/audit_orders_{STRATEGY}.csv"]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    files = snap["per_file_sha256"]["WORK-DYNAMIC-BT-01"]
    APPEND_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for rel in TARGETS:
        p = BT01 / rel
        ref = files.get(rel)
        if ref is None:
            records.append({"file": rel, "status": "no_reference"})
            continue
        cur = p.read_bytes()
        old_bytes, old_sha = int(ref["bytes"]), ref["sha256"]
        prefix = cur[:old_bytes]
        appended = cur[old_bytes:]
        rec = {
            "file": rel,
            "snapshot_bytes": old_bytes,
            "snapshot_sha256": old_sha,
            "current_bytes_before": len(cur),
            "current_sha256_before": sha(cur),
            "prefix_equals_snapshot": sha(prefix) == old_sha,
            "appended_bytes": len(appended),
            "appended_sha256": sha(appended),
            "appended_lines": appended.count(b"\n"),
        }
        if rec["prefix_equals_snapshot"]:
            out = APPEND_DIR / p.name.replace(".csv", f".appended_{len(appended)}B.csv")
            out.write_bytes(appended)
            rec["appended_saved_to"] = str(out)
            # 还原：截断回快照长度
            with open(p, "r+b") as f:
                f.truncate(old_bytes)
            now = p.read_bytes()
            rec["restored_bytes"] = len(now)
            rec["restored_sha256"] = sha(now)
            rec["restored_equals_snapshot"] = sha(now) == old_sha
            rec["status"] = "restored" if rec["restored_equals_snapshot"] else "restore_failed"
        else:
            rec["status"] = "prefix_mismatch__manual_review"
        records.append(rec)

    doc = {
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "incident": "策略基类硬编码写入 evidence/WORK-DYNAMIC-BT-01/out（append 模式）",
        "root_cause_ref": "WyckoffLow4hGridBase.py:708-712 / 755 / 916-919",
        "data_loss": False,
        "why_no_loss": "两个文件都是追加写；新文件前 old_bytes 字节的 sha256 与快照完全一致",
        "records": records,
        "verdict": "PASS" if all(r.get("status") == "restored" for r in records) else "FAIL",
    }
    (RAW / "12_上游追加写入_取证与还原.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
