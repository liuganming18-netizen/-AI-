# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 09_登记

把本轮看过的样本登记进系统（EV_OBSERVE，append-only + 哈希链）。
样本键 = 标的|事件ID（不含档位与参数组，因为四档共用同一批事件）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, str(BASE_DIR / "框架/backend"))

from experiment_registry import ExperimentRegistry  # noqa: E402

from _lib import OUT, equity_curve, load_all  # noqa: E402


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)
    keys = sorted({f"{p}|{e}" for p, e in zip(d["pair"], d["事件ID"])})
    reg = ExperimentRegistry()
    st = reg.observe(
        sample_set_id="FT-STOP-GRID-AUDIT-01/四档止损36组",
        sample_keys=keys,
        rows=int(len(d)),
        by="system",
        purpose="四档固定止损独立审核（研究/比较）",
        note=("36 组 = 4 档(5/10/15/20%) × 9 个 C1 入场参数组，"
              f"{len(keys)} 个独立入场事件、{len(d)} 笔成交；只读上游，未改策略/撮合/账本"),
    )
    return {"样本键数": len(keys), "行数": int(len(d)), "缺失组": missing,
            "登记回执": st}


if __name__ == "__main__":
    r = main()
    p = OUT / "09_登记回执.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"样本键 {r['样本键数']} 个，行数 {r['行数']}")
    print(f"已写出 {p}")
    print(json.dumps({k: v for k, v in r["登记回执"].items() if k != "keys"}, ensure_ascii=False, default=str)[:600])
