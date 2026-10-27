# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 05_跳过信号与仓位竞争归因

冻结计划第四节。核心问题：四档成交的样本本来就不是同一批事件，
所以不能只比成交后的赢家输家。

漏斗（每个策略单独算）：
  确认表总行数 → CONFIRMED → 进入订单审计 → 订单审计 PASSED → 实际成交
缺口归因：
  · 槽位已满（max_open_trades=3）
  · 同币已有持仓
  · 最小下单额拒单（日志 raw_stake < min_stake）
  · 价格边界 / fill≥H 拒单（订单审计）
  · 其他/未归因
影子收益：对「窄档成交、宽档未成交」的事件，用窄档实际 R 作替代观测（明确标注非反事实）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from _lib import ALL, OUT, TIERS, UP, equity_curve, load_all

REJECT_RE = re.compile(r"\[([A-Z0-9]+/USDT:USDT)\]\s+raw_stake=([0-9.]+)\s+<\s+min_stake=([0-9.]+)")


def sym_to_pair(sym: str) -> str:
    """确认表 symbol 是 'BCHUSDT'，逐笔表 pair 是 'BCH/USDT:USDT'。"""
    base = sym[:-4] if sym.endswith("USDT") else sym
    return f"{base}/USDT:USDT"


def open_at(trades: pd.DataFrame, t: int) -> tuple[int, set[str]]:
    m = (trades["open_ms"] <= t) & (trades["close_ms"] > t)
    g = trades[m]
    return int(len(g)), set(g["pair"].unique())


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)

    funnels, rejects = [], []
    for name in ALL:
        conf_p = UP / f"out/audit_confirmation_{name}.csv"
        order_p = UP / f"out/audit_orders_{name}.csv"
        log_p = UP / f"logs/{name}_backtest.log"
        row = {"策略": name, "档位": name.split("_")[2], "参数组": "_".join(name.split("_")[3:])}

        conf = pd.read_csv(conf_p, encoding="utf-8-sig") if conf_p.exists() and conf_p.stat().st_size > 0 else None
        orders = pd.read_csv(order_p, encoding="utf-8-sig") if order_p.exists() and order_p.stat().st_size > 0 else None
        tr = d[d["策略"] == name]

        row["确认表行数"] = int(len(conf)) if conf is not None else None
        if conf is not None:
            row["CONFIRMED"] = int((conf["decision"] == "CONFIRMED").sum())
            row["确认阶段REJECTED"] = int((conf["decision"] == "REJECTED").sum())
            row["确认阶段拒绝原因"] = conf[conf["decision"] == "REJECTED"]["reason"].value_counts().to_dict()
        else:
            row["CONFIRMED"] = row["确认阶段REJECTED"] = None
            row["确认阶段拒绝原因"] = None

        row["订单审计行数"] = int(len(orders)) if orders is not None else 0
        if orders is not None:
            row["订单审计去重候选数"] = int(orders["candidate_id"].nunique())
            row["订单PASSED"] = int((orders["decision"] == "PASSED").sum())
            row["订单REJECTED"] = int((orders["decision"] == "REJECTED").sum())
            row["订单拒绝原因"] = orders[orders["decision"] == "REJECTED"]["reason"].value_counts().to_dict()
        else:
            row["订单PASSED"] = row["订单REJECTED"] = 0
            row["订单拒绝原因"] = {}

        row["实际成交"] = int(len(tr))

        # 日志里的最小下单额拒单
        n_min, mins = 0, []
        if log_p.exists():
            txt = log_p.read_text(errors="ignore")
            for m in REJECT_RE.finditer(txt):
                n_min += 1
                mins.append({"pair": m.group(1), "raw_stake": float(m.group(2)), "min_stake": float(m.group(3))})
        row["最小下单额拒单次数"] = n_min
        row["最小下单额拒单样例"] = mins[:5]

        # 缺口归因：CONFIRMED 但没进订单审计 → 在 custom_stake_amount 之前就被挡
        if conf is not None and row["CONFIRMED"]:
            cc = conf[conf["decision"] == "CONFIRMED"].copy()
            cc["pair"] = cc["symbol"].map(sym_to_pair)
            cc["事件ID"] = cc["event_id"]
            entered = set(zip(tr["pair"], tr["事件ID"]))
            audited = set()
            if orders is not None:
                for _, o in orders.iterrows():
                    cid = str(o["candidate_id"])
                    audited.add((o["pair"], "_".join(cid.split("cand_C1_", 1)[-1].split("_")[:7])))
            cls = {"槽位已满": 0, "同币持仓": 0, "其他未归因": 0, "进入审计": 0}
            detail = []
            for _, c in cc.iterrows():
                k = (c["pair"], c["事件ID"])
                if k in audited:
                    cls["进入审计"] += 1
                    continue
                n_open, pairs_open = open_at(tr, int(c["confirm_time_ms"]))
                if n_open >= 3:
                    cls["槽位已满"] += 1
                    detail.append({"pair": c["pair"], "事件ID": c["事件ID"], "原因": "槽位已满", "当时持仓数": n_open})
                elif c["pair"] in pairs_open:
                    cls["同币持仓"] += 1
                    detail.append({"pair": c["pair"], "事件ID": c["事件ID"], "原因": "同币持仓", "当时持仓数": n_open})
                else:
                    cls["其他未归因"] += 1
                    detail.append({"pair": c["pair"], "事件ID": c["事件ID"], "原因": "其他未归因", "当时持仓数": n_open})
            row["缺口归因"] = cls
            row["缺口归因样例"] = detail[:10]
            row["成交率%"] = round(len(tr) / row["CONFIRMED"] * 100, 2) if row["CONFIRMED"] else None
        else:
            row["缺口归因"] = None
            row["缺口归因样例"] = []
            row["成交率%"] = None

        funnels.append(row)

    fd = pd.DataFrame(funnels)
    fd.to_csv(OUT / "05_事件漏斗.csv", index=False, encoding="utf-8-sig")

    # ---------- 币池差集 ----------
    pool = {t: set(d[d["档位"] == t]["pair"].unique()) for t in TIERS}
    common = set.intersection(*pool.values()) if all(pool.values()) else set()
    pool_info = {
        "各档标的数": {t: len(v) for t, v in pool.items()},
        "四档交集标的数": len(common),
        "仅Stop05有": sorted(pool.get("Stop05", set()) - common),
        "仅Stop20有": sorted(pool.get("Stop20", set()) - common),
        "各档相对Stop05的差集大小": {t: len(pool.get("Stop05", set()) - v) for t, v in pool.items()},
    }

    # ---------- 影子收益：窄档成交、宽档未成交的事件 ----------
    # 必须按参数组分开算：不同参数组的候选集不同，混在一起会把同一事件重复计 9 次
    ev = d[["档位", "参数组", "pair", "事件ID", "R", "是否期末结算"]].copy()
    evk = ev.groupby(["档位", "参数组", "pair", "事件ID"], as_index=False).agg(
        R=("R", "sum"), 期末=("是否期末结算", "sum"))

    shadow: dict = {}
    for narrow, wide in [("Stop05", "Stop10"), ("Stop05", "Stop15"), ("Stop05", "Stop20"),
                         ("Stop10", "Stop15"), ("Stop10", "Stop20"), ("Stop15", "Stop20")]:
        per_group = []
        for s in sorted(evk["参数组"].unique()):
            sub = evk[evk["参数组"] == s]
            sn = set(zip(sub[sub["档位"] == narrow]["pair"], sub[sub["档位"] == narrow]["事件ID"]))
            sw = set(zip(sub[sub["档位"] == wide]["pair"], sub[sub["档位"] == wide]["事件ID"]))
            only = sn - sw
            rmap = {(p, e): float(r) for p, e, r in
                    zip(sub[sub["档位"] == narrow]["pair"], sub[sub["档位"] == narrow]["事件ID"],
                        sub[sub["档位"] == narrow]["R"])}
            vals = [rmap[k] for k in only if k in rmap]
            if vals:
                per_group.append({"参数组": s, "事件数": len(vals),
                                  "R合计": round(float(np.sum(vals)), 6),
                                  "R中位": round(float(np.median(vals)), 6),
                                  "盈利事件数": int(sum(1 for v in vals if v > 0)),
                                  "最大单笔R": round(float(np.max(vals)), 6),
                                  "最小单笔R": round(float(np.min(vals)), 6)})
        if per_group:
            shadow[f"{narrow}有_{wide}无"] = {
                "参数组数": len(per_group),
                "事件数中位": float(np.median([x["事件数"] for x in per_group])),
                "R合计中位": round(float(np.median([x["R合计"] for x in per_group])), 6),
                "R中位的中位": round(float(np.median([x["R中位"] for x in per_group])), 6),
                "R合计为正的组数": int(sum(1 for x in per_group if x["R合计"] > 0)),
                "逐组": per_group,
                "说明": "替代观测：用窄档在这些事件上的实际结果，不是若换成宽档止损的反事实",
            }

    return {
        "一_事件漏斗": fd.to_dict("records"),
        "二_币池差集": pool_info,
        "三_影子收益": shadow,
        "四_缺失组": missing,
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "05_跳过与仓位竞争.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    fd = pd.DataFrame(r["一_事件漏斗"])
    cols = ["档位", "参数组", "确认表行数", "CONFIRMED", "订单审计行数", "订单PASSED", "订单REJECTED",
            "实际成交", "最小下单额拒单次数", "成交率%"]
    print(fd[cols].to_string(index=False))
    print("\n缺口归因汇总：")
    print(json.dumps(r["一_事件漏斗"][0]["缺口归因"], ensure_ascii=False) if r["一_事件漏斗"] else "")
    print("\n币池差集：", json.dumps(r["二_币池差集"], ensure_ascii=False)[:800])
    print("\n影子收益：", json.dumps(r["三_影子收益"], ensure_ascii=False, indent=1))
