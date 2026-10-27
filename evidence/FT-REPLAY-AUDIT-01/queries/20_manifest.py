# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 20：本目录哈希清单（最后跑）。"""
from __future__ import annotations

import csv
import hashlib
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
MANIFEST = "05_文件清单与哈希.csv"
EXTERNAL = "out/20_外部证据哈希.json"
SELF_LOG = "logs/20_manifest.log"

EXT = [
    ("台账页", os.path.join(ROOT, "evidence/_统一执行台账/07_FT-REPLAY-AUDIT-01_验收结论.md")),
    ("台账清单", os.path.join(ROOT, "evidence/_统一执行台账/05_文件清单与哈希.csv")),
    ("登记链事件表", os.path.join(ROOT, "框架/backend/data/experiments/events.jsonl")),
    ("登记链链头", os.path.join(ROOT, "框架/backend/data/experiments/head.json")),
    ("源合并表", os.path.join(ROOT, "evidence/FT-ENTRY-09/out/00_all_27_trades_consolidated.csv")),
    ("组级批次元数据", os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_ft_entry_09_research/batch_meta.json")),
    ("币级批次元数据", os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_ft_replay_01/batch_meta.json")),
    ("后端回测接口", os.path.join(ROOT, "框架/backend/backtest_api.py")),
    ("后端批次汇总", os.path.join(ROOT, "框架/backend/backtest_batches.py")),
    ("后端账本联结", os.path.join(ROOT, "框架/backend/ledger_links.py")),
    ("后端账本读取", os.path.join(ROOT, "框架/backend/backtest_store.py")),
    ("前端回测页", os.path.join(ROOT, "UI界面/web/src/components/BacktestWorkbench.tsx")),
]


def sha256(p):
    if not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    rows = []
    for dp, _, fs in os.walk(AUDIT):
        for fn in sorted(fs):
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, AUDIT)
            if rel in (MANIFEST, EXTERNAL, SELF_LOG) or "__pycache__" in rel:
                continue
            rows.append({"path": rel, "bytes": os.path.getsize(p), "sha256": sha256(p)})
    with open(os.path.join(AUDIT, MANIFEST), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "bytes", "sha256"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["path"]))

    ext = []
    for label, p in EXT:
        ext.append({"标签": label, "路径": os.path.relpath(p, ROOT),
                    "字节": os.path.getsize(p) if os.path.isfile(p) else None,
                    "sha256": sha256(p), "存在": os.path.isfile(p)})
    with open(os.path.join(AUDIT, EXTERNAL), "w", encoding="utf-8") as fh:
        json.dump({"说明": "本目录之外、承载本次验收结论的文件指纹", "项": ext},
                  fh, ensure_ascii=False, indent=2)

    print("本目录产物 %d 个" % len(rows))
    for r in sorted(rows, key=lambda x: x["path"]):
        print("  %-46s %8d  %s" % (r["path"], r["bytes"], r["sha256"][:16]))
    print("\n外部证据 %d 处（缺失 %d）" % (len(ext), sum(1 for x in ext if not x["存在"])))


if __name__ == "__main__":
    main()
