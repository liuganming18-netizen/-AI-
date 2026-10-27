# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜拒单原因全量分布（不受 top-5 截断影响）。

`06_拒单审计与真实委托分离.csv` 里只留了每个策略的 top-5 原因，合计不等于总数。
本脚本把 45 份 `audit_confirmation_*.csv` 的**全部** reason 逐条汇总，保证
Σ(原因计数) = 策略侧拒绝总数，可对账。
"""
from __future__ import annotations

import collections
import importlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
BT01 = cm.BT01


def main() -> None:
    rows = []
    tot = collections.Counter()
    n_rows = n_conf = n_rej = 0
    for p in sorted(BT01.glob("out/audit_confirmation_*.csv")):
        strategy = p.stem.replace("audit_confirmation_", "")
        d = pd.read_csv(p)
        n_rows += len(d)
        n_conf += int((d["decision"] == "CONFIRMED").sum())
        n_rej += int((d["decision"] == "REJECTED").sum())
        rej = d[d["decision"] == "REJECTED"]
        for k, v in rej["reason"].value_counts().items():
            key = "volume_exceeded_*" if str(k).startswith("volume_exceeded") else str(k)
            tot[key] += int(v)
            rows.append({"strategy": strategy, "reason": key, "count": int(v)})

    df = pd.DataFrame(rows)
    agg = (df.groupby("reason", as_index=False)["count"].sum()
             .sort_values("count", ascending=False))
    agg["share_of_rejected_pct"] = (agg["count"] / n_rej * 100).round(3)
    agg.to_csv(OUT / "06b_拒单原因分布.csv", index=False, encoding="utf-8-sig")
    df.to_csv(OUT / "06c_拒单原因分布_分策略.csv", index=False, encoding="utf-8-sig")

    chk = {
        "审计总行数": n_rows,
        "CONFIRMED": n_conf,
        "REJECTED": n_rej,
        "CONFIRMED+REJECTED=总行数": n_conf + n_rej == n_rows,
        "原因计数合计": int(agg["count"].sum()),
        "原因合计=REJECTED": int(agg["count"].sum()) == n_rej,
        "说明": ("`06_拒单审计与真实委托分离.csv` 的 rejected_reason_top 只保留每策略 top-5，"
                 "合计会小于总数；本表为全量，可对账。"),
    }
    import json
    (OUT / "06d_拒单原因核对.json").write_text(json.dumps(chk, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(chk, ensure_ascii=False, indent=2))
    print(agg.to_string(index=False))


if __name__ == "__main__":
    main()
