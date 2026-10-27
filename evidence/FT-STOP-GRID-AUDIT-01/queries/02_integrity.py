# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 02_交付完整性核对

逐笔核对（全部从上游 trades_*.csv 与日志重算，不采信上游汇总）：
  ① 36 组是否齐全、各组笔数
  ② 实际成交价与初始止损：stop_loss_abs 是否 = open_rate × (1-pct)，差几个最小价位
  ③ 数量与风险预算：实际风险占权益是否 = 0.5%，超发/欠发笔数
  ④ 退出标签与真实触发机制：标签是不是标签、机制是不是机制
  ⑤ 账目闭合：profit_abs 是否 = 价差×数量 − 手续费 + 资金费
  ⑥ 结构性问题：没有止盈/超时/跟踪时，已实现笔能不能有盈利
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from _lib import (ALL, OUT, STOP_LABELS, TIER_PCT, TIERS, UP, equity_curve,
                  load_all, tick_of)

pd.set_option("display.width", 200)


def main() -> dict:
    df, missing = load_all()
    res: dict = {"一_完整性": {}, "二_止损价": {}, "三_风险预算": {},
                 "四_退出机制": {}, "五_账目闭合": {}, "六_结构性问题": {}}

    res["一_完整性"] = {
        "应有组数": len(ALL),
        "实际有结果组数": len(ALL) - len(missing),
        "缺失组": missing,
        "总笔数": int(len(df)) if len(df) else 0,
        "各组笔数": {n: int((df["策略"] == n).sum()) if len(df) else 0 for n in ALL},
    }
    if df.empty:
        return res

    d = equity_curve(df)

    # ---------- 二 实际成交价与初始止损 ----------
    rows = []
    for _, r in d.iterrows():
        pct = r["止损比例"]
        target = float(r["open_rate"]) * (1 - pct)
        stop = float(r["stop_loss_abs"]) if pd.notna(r["stop_loss_abs"]) else np.nan
        init = float(r["initial_stop_loss_abs"]) if pd.notna(r["initial_stop_loss_abs"]) else np.nan
        tk = tick_of(r["stop_loss_abs"])
        gap = (stop - target) / tk if tk > 0 and np.isfinite(stop) else np.nan
        rows.append({
            "策略": r["策略"], "档位": r["档位"], "pair": r["pair"], "事件ID": r["事件ID"],
            "open_rate": r["open_rate"], "stop_loss_abs": stop, "目标止损": target,
            "最小价位": tk, "上偏tick": gap,
            "initial_stop_loss_abs": init, "initial是否等于1%哨兵": bool(
                np.isfinite(init) and abs(init - float(r["open_rate"]) * 0.01) <=
                max(tk, abs(float(r["open_rate"])) * 1e-4) * 1.0000001),
            "exit_reason": r["exit_reason"], "close_rate": r["close_rate"],
        })
    stopdf = pd.DataFrame(rows)
    stopdf["止损价合规"] = stopdf["上偏tick"].abs() <= 1.0000001
    res["二_止损价"] = {
        "笔数": int(len(stopdf)),
        "止损价等于成交价×(1-pct)（容差1个最小价位）": int(stopdf["止损价合规"].sum()),
        "不合规笔数": int((~stopdf["止损价合规"]).sum()),
        "上偏tick分布": {str(k): int(v) for k, v in stopdf["上偏tick"].round(6).value_counts().head(10).items()},
        "最大上偏tick": float(stopdf["上偏tick"].abs().max()) if len(stopdf) else None,
        "initial_stop_loss_abs等于1%哨兵笔数（哨兵=成交价×1%，因stoploss=-0.99）": int(stopdf["initial是否等于1%哨兵"].sum()),
        "说明": "initial_stop_loss_abs 是 freqtrade 默认哨兵值，自定义止损实际值在 stop_loss_abs",
    }
    stopdf.to_csv(OUT / "02_止损价核对明细.csv", index=False, encoding="utf-8-sig")

    # ---------- 三 风险预算 ----------
    risk_rows = []
    for t in TIERS:
        g = d[d["档位"] == t]
        if g.empty:
            continue
        risk_rows.append({
            "档位": t,
            "止损比例": TIER_PCT[t],
            "笔数": int(len(g)),
            "名义占权益_中位": round(float(g["名义占权益"].median()), 6),
            "理论名义占权益": round(0.005 / TIER_PCT[t], 6),
            "实际风险占权益_中位": round(float(g["实际风险占权益"].median()), 6),
            "实际风险占权益_最小": round(float(g["实际风险占权益"].min()), 6),
            "实际风险占权益_最大": round(float(g["实际风险占权益"].max()), 6),
            "风险覆盖比_中位": round(float(g["风险覆盖比"].median()), 4),
            "风险覆盖比_最小": round(float(g["风险覆盖比"].min()), 4),
            "风险覆盖比_5%分位": round(float(g["风险覆盖比"].quantile(0.05)), 4),
            "超发笔_风险>0.5%": int((g["实际风险占权益"] > 0.005 * 1.0001).sum()),
            "被截断笔_覆盖率<0.95": int((g["风险覆盖比"] < 0.95).sum()),
            "被截断笔占比%": round(float((g["风险覆盖比"] < 0.95).mean() * 100), 2),
        })
    riskdf = pd.DataFrame(risk_rows)
    res["三_风险预算"] = {
        "各档": risk_rows,
        "结论": "逐档看名义占权益是否等于 0.5%/pct，实际风险是否守住 0.5%",
        "总超发笔": int((d["实际风险占权益"] > 0.005 * 1.0001).sum()),
        "总被截断笔_覆盖率<0.95": int((d["风险覆盖比"] < 0.95).sum()),
        "说明": "覆盖率<1 主要来自合约最小变动单位取整（lot 量化）；<0.95 视为被流动性上限或单仓上限截断",
    }
    riskdf.to_csv(OUT / "02_风险预算核对.csv", index=False, encoding="utf-8-sig")

    # ---------- 四 退出标签 vs 真实触发机制 ----------
    lab = d["exit_reason"].astype(str)
    mech = {}
    for lbl in sorted(lab.unique()):
        g = d[lab == lbl]
        mech[lbl] = {
            "笔数": int(len(g)),
            "档位分布": {t: int((g["档位"] == t).sum()) for t in TIERS},
            "已实现": int((~g["是否期末结算"]).sum()),
        }
    # 止损类标签：收盘价是否真的贴着账面止损价
    s = d[lab.isin(STOP_LABELS)].copy()
    s["偏离tick"] = (pd.to_numeric(s["close_rate"], errors="coerce") - pd.to_numeric(s["stop_loss_abs"], errors="coerce")) / \
                    s["stop_loss_abs"].map(tick_of)
    s["贴止损"] = s["偏离tick"].abs() <= 2.0
    # 上偏：账面止损相对 open_rate×(1-pct)
    s["止损上偏tick"] = (pd.to_numeric(s["stop_loss_abs"], errors="coerce") -
                        pd.to_numeric(s["open_rate"], errors="coerce") * (1 - s["止损比例"])) / \
                       s["stop_loss_abs"].map(tick_of)

    trail = s[s["exit_reason"].astype(str) == "trailing_stop_loss"]
    res["四_退出机制"] = {
        "标签分布": mech,
        "非法标签": [x for x in lab.unique() if x not in ("stop_loss", "trailing_stop_loss", "force_exit")],
        "止损类标签笔数": int(len(s)),
        "收盘价贴着账面止损价（容差2个最小价位）": int(s["贴止损"].sum()),
        "不贴的笔数": int((~s["贴止损"]).sum()),
        "trailing_stop_loss标签笔数": int(len(trail)),
        "trailing标签的止损上偏tick_最大": float(trail["止损上偏tick"].abs().max()) if len(trail) else None,
        "trailing标签中上偏≤1个最小价位（即常数止损被取整标成跟踪）": int((trail["止损上偏tick"].abs() <= 1.0000001).sum()) if len(trail) else 0,
        "说明": "trailing_stop_loss 真语义=止损价曾被上调过一次；常数止损因 ROUND_UP 取整也会被标成它",
        "force_exit是否单列": bool(d["是否期末结算"].any()),
        "force_exit笔数": int(d["是否期末结算"].sum()),
    }
    s[["策略", "档位", "pair", "事件ID", "exit_reason", "open_rate", "stop_loss_abs",
       "close_rate", "偏离tick", "贴止损", "止损上偏tick", "R"]].to_csv(
        OUT / "02_退出机制核对明细.csv", index=False, encoding="utf-8-sig")

    # ---------- 五 账目闭合 ----------
    chk = d.copy()
    chk["重算profit_abs"] = chk["价差盈亏USDT"] - chk["手续费USDT"] + chk["资金费USDT"]
    chk["闭合误差"] = chk["重算profit_abs"] - pd.to_numeric(chk["profit_abs"], errors="coerce")
    chk["fee理论"] = chk["名义USDT"] * 0.0005 + \
                     pd.to_numeric(chk["amount"], errors="coerce") * pd.to_numeric(chk["close_rate"], errors="coerce") * 0.0005
    chk["fee误差"] = chk["手续费USDT"] - chk["fee理论"]
    res["五_账目闭合"] = {
        "笔数": int(len(chk)),
        "profit_abs闭合误差绝对最大值": float(chk["闭合误差"].abs().max()),
        "闭合误差>0.01USDT笔数": int((chk["闭合误差"].abs() > 0.01).sum()),
        "闭合误差>0.001USDT笔数": int((chk["闭合误差"].abs() > 0.001).sum()),
        "手续费误差绝对最大值": float(chk["fee误差"].abs().max()),
        "资金费非零笔数": int((chk["资金费USDT"].abs() > 1e-12).sum()),
        "资金费合计USDT": round(float(chk["资金费USDT"].sum()), 6),
        "资金费合计R": round(float(chk["资金费R"].sum()), 6),
        "手续费合计USDT": round(float(chk["手续费USDT"].sum()), 6),
        "手续费合计R": round(float(chk["手续费R"].sum()), 6),
        "价差盈亏合计USDT": round(float(chk["价差盈亏USDT"].sum()), 6),
        "毛利合计R": round(float(chk["毛利R"].sum()), 6),
        "profit_abs合计USDT": round(float(pd.to_numeric(chk["profit_abs"], errors="coerce").sum()), 6),
        "is_open取值": sorted(chk["is_open"].astype(str).unique().tolist()),
        "说明": ("fee_open/fee_close 是费率不是金额；funding_fees 已含在 profit_abs 内，"
                 "此处只做闭合校验，不得再扣一次"),
    }

    # ---------- 六 结构性问题 ----------
    realized = d[~d["是否期末结算"]]
    struct = {
        "已实现笔数": int(len(realized)),
        "已实现盈利笔数": int((realized["profit_abs"] > 0).sum()),
        "已实现亏损笔数": int((realized["profit_abs"] < 0).sum()),
        "已实现胜率": round(float((realized["profit_abs"] > 0).mean() * 100), 4) if len(realized) else None,
        "各档已实现盈利笔数": {t: int(((realized["档位"] == t) & (realized["profit_abs"] > 0)).sum()) for t in TIERS},
        "已实现R合计": round(float(realized["R"].sum()), 6),
        "期末结算笔数": int(d["是否期末结算"].sum()),
        "期末结算R合计": round(float(d[d["是否期末结算"]]["R"].sum()), 6),
        "总R": round(float(d["R"].sum()), 6),
    }
    # 若无止盈/超时/跟踪，已实现盈利笔必须为 0；否则说明有未预期的出场通道
    struct["已实现盈利笔为0_符合设计"] = struct["已实现盈利笔数"] == 0
    struct["若不为0的笔"] = realized[realized["profit_abs"] > 0][
        ["策略", "pair", "exit_reason", "profit_abs", "R"]].to_dict("records")[:20]
    res["六_结构性问题"] = struct

    return res


if __name__ == "__main__":
    r = main()
    p = OUT / "02_交付完整性核对.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    print(json.dumps({k: v for k, v in r.items() if k != "一_完整性"}, ensure_ascii=False, indent=2, default=str)[:4000])
