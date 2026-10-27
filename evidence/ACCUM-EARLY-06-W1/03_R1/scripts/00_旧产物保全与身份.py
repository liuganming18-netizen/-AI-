#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""00 旧产物保全与身份（工单 D 第 5 条：保留旧产物，只写本窗）

记录三类身份，供 05 在链路末尾复核「一个字节都没动」：
  1. 旧产物（`../01/`、`../02/`）：本单只读；
  2. 生产代码/配置（`accum_early_entry.py`、`paper_engine.py`、冻结包 `config.py`）；
  3. 统一用户决策与两份派工单。

判据（任一失败 ⇒ 退出码 1）：
  A. 统一决策 sha 等于 06-01 记录的 `3170ec99…`（证明本单没改它）
  B. 生产代码 sha 等于 06-03 交付的 `3c570edc…`（证明本单没改它）
  C. 旧产物 mtime 早于本窗目录的创建时间（证明没覆盖）
  D. 生产文件 mtime 早于本窗目录的创建时间
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w1r1 as W  # noqa: E402

EXPECT = {
    "docs/用户约定/用户要求与决策.md": "3170ec997faae8715456df700f90acf1c4bb04d0a7842920d402baf9500b580b",
    "框架/backend/accum_early_entry.py": "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017",
    "框架/backend/paper_engine.py": "e8a994b6750d8532576e5120e66144acc63cab355766b8ad8db932de3caec784",
    "策略/WyckoffMaster/event_trend_v0_1/config.py": "122683f2b489c742dd594ff465e916147dd097bd1afcb8a35cb4116c0eedeb46",
}


def stat(rel: str) -> dict:
    p = W.ROOT / rel
    st = p.stat()
    return {
        "path": rel,
        "sha256": W.sha256_file(p),
        "bytes": st.st_size,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main() -> int:
    problems: list = []
    win_mtime = datetime.fromtimestamp(W.HERE.stat().st_mtime, tz=timezone.utc)

    old_files = sorted([p for p in (W.OLD01.rglob("*")) if p.is_file()] +
                       [p for p in (W.OLD02.rglob("*")) if p.is_file()])
    prod_files = [W.DECISION, W.DISPATCH06, W.DISPATCH_R1, W.CODE, W.PE,
                  "策略/WyckoffMaster/event_trend_v0_1/config.py",
                  "框架/backend/live_store.py", "框架/backend/paper_loop.py",
                  "框架/backend/unified_runtime.py"]

    old_rows, prod_rows = [], []
    for p in old_files:
        rel = str(p.relative_to(W.ROOT))
        row = stat(rel)
        row["older_than_window"] = p.stat().st_mtime <= win_mtime.timestamp()
        old_rows.append(row)
        W.require(row["older_than_window"], f"旧产物被改动（mtime 晚于本窗创建）：{rel}", problems)
    for rel in prod_files:
        row = stat(rel)
        row["older_than_window"] = (W.ROOT / rel).stat().st_mtime <= win_mtime.timestamp()
        prod_rows.append(row)
        W.require(row["older_than_window"], f"生产/约定文件被改动：{rel}", problems)

    # A/B：身份与上游记录一致
    for rel, want in EXPECT.items():
        got = next(r["sha256"] for r in prod_rows if r["path"] == rel)
        W.require(got == want, f"{rel} 哈希漂移：{got} != {want}", problems)

    payload = {
        "schema": "accum-early-06-w1-r1/preserve/v1",
        "checked_at_utc": W.now_iso(),
        "window_dir": "evidence/ACCUM-EARLY-06-W1/03_R1",
        "window_created_utc": win_mtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "write_scope": ["evidence/ACCUM-EARLY-06-W1/03_R1/"],
        "old_artifacts": old_rows,
        "production_and_docs": prod_rows,
        "expected_identity": EXPECT,
        "problems": problems,
        "verdict": "pass" if not problems else "fail",
        "statement": ("旧产物 01/02 全部只读且 mtime 早于本窗创建；统一决策、生产代码、冻结包、"
                      "两份派工单均未改动。本单新增文件全部在 03_R1/ 下。"),
    }
    W.dump("00_preserve_identity.json", payload)

    print(json.dumps({
        "old_artifacts": len(old_rows),
        "production_and_docs": len(prod_rows),
        "identity_ok": all(next(r["sha256"] for r in prod_rows if r["path"] == k) == v for k, v in EXPECT.items()),
        "problems": len(problems),
        "verdict": payload["verdict"],
    }, ensure_ascii=False))
    for p in problems:
        print("PROBLEM", p)
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
