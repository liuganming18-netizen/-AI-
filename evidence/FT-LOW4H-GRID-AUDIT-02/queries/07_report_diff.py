# -*- coding: utf-8 -*-
"""07 上游报告数字逐项复核：哪些对、哪些错、错在哪。

只读上游报告文本与原始账本，不改上游任何文件。
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
RES: dict = {"段": {}}


def add(sec, key, val, ok=None, note=""):
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES.setdefault("判据", []).append({"段": sec, "项": key, "通过": bool(ok), "说明": note})


def parse_table() -> dict:
    p = L.UP / "00_4小时最低价跟踪退出综合研究报告.md"
    txt = p.read_text(encoding="utf-8")
    out = {}
    for ln in txt.splitlines():
        m = re.match(r"\|\s*\*\*(S\d\d_D\d\d)\*\*\s*\|(.*)\|\s*$", ln)
        if not m:
            continue
        combo = m.group(1)
        cells = [c.strip().replace("**", "") for c in m.group(2).split("|")]
        # 列：s, d, 总成交笔数, 净收益, 中位, 盈利组数, 胜率, 初始止损净盈亏,
        #     跟踪盈利, 跟踪亏损, 期末持有, 最大回撤, 平均持仓
        def num(x):
            x = x.replace(",", "").replace("%", "").replace("h", "").split("(")[0].strip()
            try:
                return float(x)
            except Exception:
                return None
        out[combo] = {
            "笔数": num(cells[2]), "净收益": num(cells[3]), "中位": num(cells[4]),
            "盈利组数": num(cells[5]), "胜率": num(cells[6]), "初始止损净盈亏": num(cells[7]),
            "跟踪盈利": num(cells[8]), "跟踪亏损": num(cells[9]), "期末持有": num(cells[10]),
            "最大回撤pct": num(cells[11]), "平均持仓": num(cells[12]),
        }
    return out


def main() -> None:
    rep = parse_table()
    add("解析", "从报告表格解析到的组合数", len(rep), len(rep) == 16)

    tr, _, _ = L.load_all()
    df = L.enrich(tr)
    cls = pd.read_csv(OUT / "02_逐笔重建明细.csv")
    cls = cls[cls["状态"].eq("ok")][["策略", "pair", "open_ms", "退出分类"]]
    df = df.merge(cls, on=["策略", "pair", "open_ms"], how="left")
    per = pd.read_csv(OUT / "03_逐组指标.csv")

    rows = []
    for combo, g in df.groupby("组合"):
        r = rep.get(combo, {})
        grp = per[per["组合"].eq(combo)]
        cat = g.groupby("退出分类")["profit_abs"].sum()
        cnt = g.groupby("退出分类").size()
        rows.append({
            "组合": combo,
            "笔数_报告": r.get("笔数"), "笔数_重算": len(g),
            "净收益_报告": r.get("净收益"), "净收益_重算": round(float(g["profit_abs"].sum()), 2),
            "中位_报告": r.get("中位"), "中位_重算": round(float(g.groupby("策略")["profit_abs"].sum().median()), 2),
            "盈利组数_报告": r.get("盈利组数"),
            "盈利组数_重算": int((g.groupby("策略")["profit_abs"].sum() > 0).sum()),
            "胜率_报告": r.get("胜率"),
            "胜率_重算": round(float((g["profit_abs"] > 0).mean() * 100), 2),
            "初始止损_报告": r.get("初始止损净盈亏"),
            "初始止损_重算": round(float(cat.get("初始止损", 0.0)), 2),
            "跟踪盈利_报告": r.get("跟踪盈利"), "跟踪盈利_重算": round(float(cat.get("跟踪盈利", 0.0)), 2),
            "跟踪亏损_报告": r.get("跟踪亏损"), "跟踪亏损_重算": round(float(cat.get("跟踪亏损", 0.0)), 2),
            "期末持有_报告": r.get("期末持有"), "期末持有_重算": int(cnt.get("期末技术结算", 0)),
            "最大回撤_报告pct": r.get("最大回撤pct"),
            "引擎回撤最大pct": round(float(grp["引擎回撤pct"].max()), 2),
            "MTM回撤最大pct": round(float(grp["MTM回撤pct"].max()), 2),
            "平均持仓_报告": r.get("平均持仓"),
            "平均持仓_重算": round(float(g["持仓小时"].mean()), 1),
        })
    # 替代分类：上游用 profit_abs>0 判跟踪盈利，我方用 close_rate>open_rate
    alt = []
    for combo, g in df.groupby("组合"):
        tr_ = g[g["退出分类"].isin(["跟踪盈利", "跟踪亏损"])]
        alt.append({"组合": combo,
                    "跟踪盈利_按金额": round(float(tr_[tr_["profit_abs"] > 0]["profit_abs"].sum()), 2),
                    "跟踪亏损_按金额": round(float(tr_[tr_["profit_abs"] <= 0]["profit_abs"].sum()), 2),
                    "跟踪盈利_按笔数": int((tr_["profit_abs"] > 0).sum()),
                    "分类口径差笔数": int((tr_["profit_abs"] > 0).sum() -
                                    (tr_["退出分类"] == "跟踪盈利").sum())})
    alt = pd.DataFrame(alt)
    cmp = pd.DataFrame(rows).merge(alt, on="组合")
    cmp.to_csv(OUT / "07_报告数字复核.csv", index=False, encoding="utf-8-sig")

    # 逐列判定
    def close(a, b, tol=0.02):
        if a is None or b is None or (isinstance(a, float) and np.isnan(a)):
            return None
        return abs(float(a) - float(b)) <= tol

    for col, tol in [("笔数", 0), ("净收益", 0.02), ("中位", 0.02), ("盈利组数", 0),
                     ("胜率", 0.05), ("初始止损", 0.02), ("跟踪盈利", 0.02),
                     ("跟踪亏损", 0.02), ("期末持有", 0), ("平均持仓", 0.15)]:
        a = cmp[f"{col}_报告"].tolist()
        b = cmp[f"{col}_重算"].tolist()
        okc = [close(x, y, tol) for x, y in zip(a, b)]
        bad = [c for c, o in zip(cmp["组合"], okc) if o is False]
        add("逐列", f"{col} 不一致的组合", bad,
            None if col in ("跟踪盈利", "跟踪亏损") else (len(bad) == 0),
            f"{len(bad)}/16 不一致（容差 {tol}）" + ("；口径差异，见下条" if col in ("跟踪盈利", "跟踪亏损") else ""))

    # 回撤：报告用的是引擎（已实现曲线）口径
    dd_bad = cmp[(cmp["最大回撤_报告pct"] - cmp["引擎回撤最大pct"]).abs() > 0.02]["组合"].tolist()
    add("逐列", "最大回撤与引擎口径一致", dd_bad, len(dd_bad) == 0,
        "报告的回撤 = freqtrade max_drawdown_account（相对峰值，已实现曲线），不是含未平仓的权益回撤")
    alt_bad = cmp[(cmp["跟踪盈利_报告"] - cmp["跟踪盈利_按金额"]).abs() > 0.02]["组合"].tolist()
    add("逐列", "改用 profit_abs>0 分类后跟踪盈利是否复现", alt_bad, len(alt_bad) == 0,
        "若复现，说明差异只来自分类定义（出场价 vs 净金额），不是数据错")
    add("逐列", "两种分类口径的笔数差合计", int(cmp["分类口径差笔数"].sum()), None,
        "出场价高于入场但扣费后为负（或相反）的笔数")
    add("逐列", "回撤口径替换", {
        "报告口径（引擎/已实现）": cmp.set_index("组合")["最大回撤_报告pct"].to_dict(),
        "MTM 口径（含未平仓）": cmp.set_index("组合")["MTM回撤最大pct"].to_dict(),
    }, None, "报告未说明回撤口径；按 MTM 口径需替换")

    # 报告正文的定性断言
    txt = (L.UP / "00_4小时最低价跟踪退出综合研究报告.md").read_text(encoding="utf-8")
    claims = []
    s05d20 = df[df["组合"].eq("S05_D20")]
    c = s05d20.groupby("退出分类").size()
    alt82 = int((s05d20[s05d20["退出分类"].isin(["跟踪盈利", "跟踪亏损"])]["profit_abs"] > 0).sum())
    claims.append({"断言": "S05_D20 仅 82 笔跟踪盈利",
                   "按出场价口径": int(c.get("跟踪盈利", 0)),
                   "按净金额口径": alt82,
                   "一致": alt82 == 82,
                   "说明": "上游用 profit_abs>0 判'跟踪盈利'，我方用出场价>入场价；两者差 6 笔"})
    tp = float(s05d20[s05d20["退出分类"].eq("跟踪盈利")]["profit_abs"].sum())
    tp_alt = float(s05d20[(s05d20["退出分类"].isin(["跟踪盈利", "跟踪亏损"])) &
                          (s05d20["profit_abs"] > 0)]["profit_abs"].sum())
    claims.append({"断言": "S05_D20 跟踪盈利 +6,094.65 USDT",
                   "按出场价口径": round(tp, 2), "按净金额口径": round(tp_alt, 2),
                   "一致": abs(tp_alt - 6094.65) < 1.0,
                   "说明": "同上，口径差导致 6 USDT 级差异"})
    for combo in ["S10_D05", "S15_D05", "S20_D05"]:
        cc = df[df["组合"].eq(combo)].groupby("退出分类").size()
        claims.append({"断言": f"{combo} 初始止损笔数为 0",
                       "实测": int(cc.get("初始止损", 0)),
                       "一致": int(cc.get("初始止损", 0)) == 0})
    s15d05 = per[per["组合"].eq("S15_D05")]
    claims.append({"断言": "S15_D05 最大回撤 3.34%（66.82 USDT）",
                   "实测引擎": round(float(s15d05["引擎回撤USDT"].max()), 2),
                   "实测MTM": round(float(s15d05["MTM回撤USDT"].max()), 2),
                   "一致": abs(float(s15d05["引擎回撤USDT"].max()) - 66.82) < 0.02})
    add("正文断言", "逐条核对", claims)
    add("正文断言", "不一致条数", sum(1 for x in claims if not x["一致"]),
        sum(1 for x in claims if not x["一致"]) == 0)

    # 上游 144 表的 fees/funding 恒 0
    up = pd.read_csv(L.UP / "out/01_144组单组宏观运行全景表.csv", encoding="utf-8-sig")
    add("上游表缺陷", "144 表 fees_total 非零行数", int((up["fees_total"].abs() > 1e-12).sum()),
        int((up["fees_total"].abs() > 1e-12).sum()) == 0, "全表为 0：读的是不存在的 fee_open_cost 列")
    add("上游表缺陷", "144 表 funding_total 非零行数", int((up["funding_total"].abs() > 1e-12).sum()),
        int((up["funding_total"].abs() > 1e-12).sum()) == 0, "全表为 0")
    add("上游表缺陷", "真实手续费合计（我方重算）", round(float(per["手续费USDT"].sum()), 4))
    add("上游表缺陷", "真实资金费合计（我方重算）", round(float(per["资金费USDT"].sum()), 4))

    crit = RES.get("判据", [])
    RES["判据统计"] = {"总": len(crit), "通过": sum(1 for c in crit if c["通过"]),
                       "不通过": sum(1 for c in crit if not c["通过"]),
                       "无判据": sum(1 for c in crit if c["通过"] is None)}
    with open(OUT / "07_报告复核.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=2, default=str)
    print("判据:", RES["判据统计"])
    for c in crit:
        if c["通过"] is False:
            print("  ✗", c["段"], c["项"], "|", c["说明"][:70])


if __name__ == "__main__":
    main()
