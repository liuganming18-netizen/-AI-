# -*- coding: utf-8 -*-
"""05 跳过的交易：槽位竞争、影子收益、币池差集。

回答"新增、减少或跳过的交易"里最难的部分：02 少成交的那笔，到底是
  (a) 账户被占满（槽位竞争）→ 纯路径效应；
  (b) 同一事件在别的档位/参数组成交了 → 只是排序变化；
  (c) 从未出现 → 信号层差异（本轮不应发生，若发生即为异常）。
影子收益按 (档位, 参数组) 分组统计，避免同一事件在 9 个组里被数 9 遍。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _lib as L
from importlib import import_module

OUT = L.OUT
p4 = import_module("04_pairing")


def slots_at(g: pd.DataFrame, t_ms: int) -> int:
    """g：同一组的逐笔；返回 t_ms 时刻的并发持仓数。"""
    return int(((g["open_ms"] <= t_ms) & (g["close_ms"] > t_ms)).sum())


def main() -> None:
    d2 = p4.add_r(p4.load_side(L.UP))
    d1 = p4.add_r(p4.load_side(p4.UP01))
    d2v = d2[d2["事件ID"].notna()]
    d1v = d1[d1["事件ID"].notna()]
    key = ["参数组", "pair", "事件ID"]

    rows = []
    for grp in L.SUFFIXES:
        for t in L.TIERS:
            a = d2v[(d2v["档位"] == t) & (d2v["参数组"] == grp)]
            b = d1v[(d1v["档位"] == t) & (d1v["参数组"] == grp)]
            if a.empty and b.empty:
                continue
            ka = set(map(tuple, a[key].astype(str).to_numpy()))
            kb = set(map(tuple, b[key].astype(str).to_numpy()))
            missed = kb - ka
            added = ka - kb
            # 02 该组全部逐笔（含其他档位？不，槽位是组内共享的，档位是组的一部分）
            g2 = a.sort_values("open_ms")
            slot_missed = []
            for _, r in b[b[key].astype(str).apply(tuple, axis=1).isin(missed)].iterrows():
                slot_missed.append(slots_at(g2, int(r["open_ms"])))
            # 01 侧：02 新增的事件在 01 里当时占几个槽
            g1 = b.sort_values("open_ms")
            slot_added = []
            for _, r in a[a[key].astype(str).apply(tuple, axis=1).isin(added)].iterrows():
                slot_added.append(slots_at(g1, int(r["open_ms"])))
            # 该事件在其他档位是否成交过（跨档位可得性）
            rows.append({
                "档位": t, "参数组": grp,
                "02笔数": len(a), "01笔数": len(b),
                "仅01有_笔数": len(missed), "仅02有_笔数": len(added),
                "仅01有_影子R": float(b[b[key].astype(str).apply(tuple, axis=1).isin(missed)]["R"].sum()),
                "仅02有_R": float(a[a[key].astype(str).apply(tuple, axis=1).isin(added)]["R"].sum()),
                "仅01有_当时02槽位满3占比": float(np.mean([s >= 3 for s in slot_missed]) * 100) if slot_missed else None,
                "仅01有_当时02槽位中位": float(np.median(slot_missed)) if slot_missed else None,
                "仅02有_当时01槽位满3占比": float(np.mean([s >= 3 for s in slot_added]) * 100) if slot_added else None,
                "仅02有_当时01槽位中位": float(np.median(slot_added)) if slot_added else None,
                "02标的数": int(a["pair"].nunique()), "01标的数": int(b["pair"].nunique()),
            })
    sk = pd.DataFrame(rows)
    sk.to_csv(OUT / "05_跳过与影子收益.csv", index=False, encoding="utf-8-sig")

    # 币池差集
    pool = []
    for grp in L.SUFFIXES:
        for t in L.TIERS:
            a = set(d2[(d2["档位"] == t) & (d2["参数组"] == grp)]["pair"])
            b = set(d1[(d1["档位"] == t) & (d1["参数组"] == grp)]["pair"])
            pool.append({"档位": t, "参数组": grp, "02币数": len(a), "01币数": len(b),
                         "仅02": len(a - b), "仅01": len(b - a), "共有": len(a & b)})
    pool_df = pd.DataFrame(pool)
    pool_df.to_csv(OUT / "05_币池差集.csv", index=False, encoding="utf-8-sig")

    res = {
        "工单": "FT-STOP-GRID-AUDIT-02 / 05 跳过与影子收益",
        "GRID02组数": int(d2["策略"].nunique()), "GRID02笔数": int(len(d2)),
        "分档汇总": {},
        "口径说明": "影子R = 02 没成交、01 成交了的那笔在 01 里的 R；按 (档位,参数组) 分组统计，不跨组重复计数",
    }
    for t in L.TIERS:
        s = sk[sk["档位"] == t]
        if s.empty:
            continue
        res["分档汇总"][t] = {
            "组数": int(len(s)),
            "仅01有_笔数中位": float(s["仅01有_笔数"].median()),
            "仅02有_笔数中位": float(s["仅02有_笔数"].median()),
            "仅01有_影子R中位": float(s["仅01有_影子R"].median()),
            "仅02有_R中位": float(s["仅02有_R"].median()),
            "仅01有时02满仓占比中位": float(s["仅01有_当时02槽位满3占比"].median())
            if s["仅01有_当时02槽位满3占比"].notna().any() else None,
            "仅02有时01满仓占比中位": float(s["仅02有_当时01槽位满3占比"].median())
            if s["仅02有_当时01槽位满3占比"].notna().any() else None,
        }
    with open(OUT / "05_跳过与影子收益.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)

    print("组数", d2["策略"].nunique(), "笔数", len(d2))
    for t, v in res["分档汇总"].items():
        print(f"  {t}: 仅01有={v['仅01有_笔数中位']:.0f}笔 影子R中位={v['仅01有_影子R中位']:.2f} "
              f"仅02有={v['仅02有_笔数中位']:.0f}笔 R中位={v['仅02有_R中位']:.2f} "
              f"02满仓占比={v['仅01有时02满仓占比中位']}")


if __name__ == "__main__":
    main()
