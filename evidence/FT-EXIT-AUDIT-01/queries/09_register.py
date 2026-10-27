# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 09：把本次审核看到的 914 笔样本登记进系统事件链

纪律：
 - 只走 `ExperimentRegistry.observe`（append-only + 哈希链），不直接写 events.jsonl
 - 样本键统一用「标的|入场毫秒」，这样后续留出段污染检查能按时间判定
 - 先跑 `--dry` 看清单，确认后再真写
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, str(ROOT / "框架/backend"))

from experiment_registry import ExperimentRegistry  # noqa: E402

EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
SCHEMES = {"S": "Wyckoff_C1_S_P0", "P": "Wyckoff_C1_P_Stop20"}

SET_ID = "FT-EXIT-AUDIT-01/仅止损914笔"
DRY = "--dry" in sys.argv


def keys() -> tuple[list[str], int]:
    """返回（去重后的样本键, 原始笔数）。

    样本键 = 标的|入场毫秒。9 组参数共用同一批确认事件，所以 914 笔只落在
    少量独立入场事件上；登记「已观察」要按事件登记，不能按笔数登记。
    """
    all_keys, ks = 0, []
    for scheme in ("S", "P"):
        for suf in SUF:
            p = EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    all_keys += 1
                    ks.append(f"{r['pair']}|{int(r['open_timestamp'])}")
    return sorted(set(ks)), all_keys


ks, n_rows = keys()
print(f"交易 {n_rows} 笔，独立入场事件（标的|入场毫秒）{len(ks)} 个")

reg = ExperimentRegistry()
st = reg.state()
print(f"登记库当前：事件数 {st['meta']['count'] if 'meta' in st else '?'}")
try:
    print(f"  链头 {st['observed'] and ''}hash 已读")
except Exception:
    pass

if DRY:
    print("干跑模式，不写入。")
    for k in ks[:5]:
        print("  ", k)
    raise SystemExit(0)

res = reg.observe(
    SET_ID, ks, rows=n_rows,
    by="audit:FT-EXIT-AUDIT-01",
    purpose="研究/比较/调参（仅止损方案 S/P 的逐笔结果审核）",
    note=("审核 evidence/FT-EXIT-01 的 18 组仅止损回测：S 组 671 笔、P 组 243 笔，"
          "窗口 2021-12-01~2024-12-31，74 币。结论：两套止损均不成立。"
          "键格式=标的|入场毫秒；914 笔落在 %d 个独立入场事件上（9 组参数共用同一批确认事件）。" % len(ks)),
)
print(f"已登记 sample_set_id={SET_ID}")
print(f"  行数 {res.get('rows')}，键数 {len(res.get('keys', []))}")

# 落盘一份回执
import json  # noqa: E402

(OUT / "09_登记回执.json").write_text(json.dumps({
    "sample_set_id": SET_ID, "rows": res.get("rows"),
    "keys": len(res.get("keys", [])), "first_seen_at": res.get("first_seen_at"),
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"写出 {OUT/'09_登记回执.json'}")
