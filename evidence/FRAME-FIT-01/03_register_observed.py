#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-01 步骤三：把 227 条已被反复研究的样本登记为「已观察」。

**这一步是工单第 4 条的落地**：

  「当前 227 条反复研究过的样本只能登记为已观察研究样本，
    不能重新命名为未见留出集。」

做法：
  1. 从 `evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet` 读 227 行；
  2. 每条生成稳定样本键 `symbol|decision_ms`（成交取 entry_time_ms，
     未成交取 snapshot_time_ms —— 两者都有来源，不猜）；
  3. 写进**本目录独立登记库** `registry/`（不写生产 `框架/backend/data/experiments`）；
  4. 同时导出一份人可核对的清单 `03_已观察样本清单.csv`。

写完后任何实验若把留出段设成覆盖这些样本的区间，`unseal_holdout` 会拒绝 ——
这是 `test_holdout_containing_observed_samples_blocked` 测的那条。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

import experiment_registry as E   # noqa: E402

HANDOFF = ROOT / "evidence" / "ENTRY-AUDIT-04" / "handoff" / "ledger_handoff.parquet"
REG_ROOT = HERE / "registry"
SAMPLE_SET_ID = "EA04_227"
OUT_CSV = HERE / "03_已观察样本清单.csv"
OUT_JSON = HERE / "03_register_observed.json"


def main() -> int:
    import pandas as pd

    if not HANDOFF.exists():
        print(f"缺少交接包：{HANDOFF}", file=sys.stderr)
        return 2
    df = pd.read_parquet(HANDOFF)
    df["decision_ms"] = df["entry_time_ms"].where(
        df["entry_time_ms"].notna(), df["snapshot_time_ms"])
    df["sample_key"] = df["symbol"].astype(str) + "|" + df["decision_ms"].astype("Int64").astype(str)

    keys = df["sample_key"].tolist()
    dup = df["sample_key"].duplicated(keep=False)
    n_dup = int(dup.sum())

    reg = E.ExperimentRegistry(REG_ROOT)
    before = len(reg.state()["observed"].get(SAMPLE_SET_ID, {}).get("keys", set()))
    already = SAMPLE_SET_ID in reg.state()["observed"]

    # 重复登记同一批是幂等的（observe 会累加键集合），但为可复现仍允许重跑
    st = reg.observe(
        SAMPLE_SET_ID, keys, rows=len(df), by="FRAME-FIT-01",
        purpose="反复查看 / 跨实验比较 / 参与调参（含 PIT 复核、规格复核、撞号分析）",
        note=("来自 evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet；"
              "这批样本**已经**被多轮研究看过，永久不得作为「未见留出集」"),
    )

    # 人可核对清单
    cols = ["sample_key", "candidate_id", "symbol", "generation",
            "window_start", "window_end", "decision_ms", "executed",
            "net_pnl", "entry_time_ms", "exit_time_ms", "holding_hours"]
    out = df[[c for c in cols if c in df.columns]].copy()
    out["decision_utc"] = out["decision_ms"].map(
        lambda ms: datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        .strftime("%Y-%m-%d %H:%M:%S"))
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    payload = {
        "audit": "FRAME-FIT-01",
        "sample_set_id": SAMPLE_SET_ID,
        "source": str(HANDOFF.relative_to(ROOT)),
        "rows": int(len(df)),
        "executed": int((df["executed"] == True).sum()),
        "unfilled": int((df["executed"] == False).sum()),
        "generations": df["generation"].value_counts().to_dict(),
        "duplicate_sample_keys": n_dup,
        "registered_keys_total": len(st["keys"]),
        "registry_root": str(REG_ROOT.relative_to(ROOT)),
        "registry_verify": reg.verify(),
        "already_registered_before_this_run": already,
        "keys_before": before,
        "note": ("227 条已观察样本。**不得**在任何实验里声明为未见留出；"
                 "重新命名（例如换个 sample_set_id 再登记一次）不改变「已观察」事实。"),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(f"样本 {len(df)} 行（成交 {payload['executed']} / 未成交 {payload['unfilled']}）")
    print(f"重复样本键 {n_dup} 个")
    print(f"登记集合 {SAMPLE_SET_ID}：本次键 {len(keys)}，库内累计 {len(st['keys'])}")
    print(f"哈希链：ok={reg.verify()['ok']} 事件数={reg.verify()['count']}")
    print(f"-> {OUT_CSV.name} / {OUT_JSON.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
