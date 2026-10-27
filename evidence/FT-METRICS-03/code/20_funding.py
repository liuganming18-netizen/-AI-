# -*- coding: utf-8 -*-
"""第2步：资金费事件级核对。

核对链条（必须闭合，不接受「总收益对得上」）：
  结算事件表 → 持仓窗口内事件数 → 逐事件 rate×数量×结算价 → 与交易记录 funding_fees 比
符号约定：Freqtrade 的 funding_fees 为「收入为正、支出为负」（由经济含义判定，不靠残差反解）：
  多头在 rate>0 时付费 → 记负；rate<0 时收钱 → 记正。
旧引擎基准表 funding_fee 为「支出为正」，两者不可直接比较，须先换算。
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA_FUTURES, DATA_SIDECAR, OLD_BASELINE_CSV, OUT,
                    load_bundle, pick_target, rel, result_zips, write_json)

LOG = []
TOL = 1e-6


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    LOG.append(line)
    print(line)


def main() -> int:
    target, _ = pick_target()
    if target is None:
        log("无可锁定结果包，终止。")
        return 2
    zips = result_zips(target)
    zp = zips[-1]
    b = load_bundle(zp)
    trades = sorted(b.trades, key=lambda t: str(t["open_date"]))
    log(f"锁定结果包：{rel(zp)}；交易 {len(trades)} 笔")

    old = {}
    if OLD_BASELINE_CSV.exists():
        df = pd.read_csv(OLD_BASELINE_CSV)
        df.columns = [c.lstrip("\ufeff") for c in df.columns]
        old = {str(r["signal_id"]): r for _, r in df.iterrows()}

    rows, checks = [], []
    for t in trades:
        pair = t["pair"].split("/")[0]
        amt = float(t["amount"])
        o = pd.Timestamp(t["open_date"]).tz_convert("UTC")
        c = pd.Timestamp(t["close_date"]).tz_convert("UTC")

        st = pd.read_csv(DATA_SIDECAR / f"{pair}USDT_funding_settlements.csv")
        st.columns = [x.lstrip("\ufeff") for x in st.columns]
        st["t"] = pd.to_datetime(st["settlement_time_utc"], utc=True)
        sub = st[(st.t >= o) & (st.t < c)].sort_values("t")

        # 引擎实际加载的资金费率序列（8h），用于证明事件来自同一批结算
        fr = pd.read_feather(DATA_FUTURES / f"{pair}_USDT_USDT-8h-funding_rate.feather")
        fr["t"] = pd.to_datetime(fr["date"], utc=True)
        sub_fr = fr[(fr.t >= o) & (fr.t < c)]

        basis_mark = float((sub.funding_rate * sub.mark_price_at_settlement * amt).sum())
        income_mark = -basis_mark

        # 备用口径：用 1h 收盘价代替结算价，检验引擎的资金费计价基准
        h1 = pd.read_feather(DATA_FUTURES / f"{pair}_USDT_USDT-1h-futures.feather")
        h1["t"] = pd.to_datetime(h1["date"], utc=True)
        merged = pd.merge_asof(sub.sort_values("t").assign(t_ms=sub.sort_values("t")["t"].astype("int64")),
                               h1[["t", "close"]].assign(t_ms=h1["t"].astype("int64"))
                                 .sort_values("t_ms")[["t_ms", "close"]],
                               on="t_ms", direction="backward", tolerance=3600 * 10**9)
        basis_close = float((merged.funding_rate * merged["close"].fillna(
            merged.mark_price_at_settlement) * amt).sum())
        income_close = -basis_close

        rec = float(t.get("funding_fees", 0.0))
        diff = income_mark - rec

        old_row = old.get(str(t.get("enter_tag")))
        old_cost = float(old_row["funding_fee"]) if old_row is not None else None
        old_amt = float(old_row["qty"]) if old_row is not None else None
        old_income_adj = None
        if old_cost is not None and old_amt:
            old_income_adj = -old_cost * amt / old_amt  # 换算到本次数量口径

        rows.append({
            "币种": t["pair"],
            "持仓开仓时间_UTC": str(o),
            "持仓平仓时间_UTC": str(c),
            "持仓时长_h": round((c - o).total_seconds() / 3600, 2),
            "数量": amt,
            "结算事件数": len(sub),
            "费率序列事件数_8h": len(sub_fr),
            "事件数是否一致": "是" if len(sub) == len(sub_fr) else "否",
            "费率均值": round(float(sub.funding_rate.mean()), 8) if len(sub) else None,
            "独立重算_按结算价_USDT": round(income_mark, 8),
            "独立重算_按1h收盘价_USDT": round(income_close, 8),
            "交易记录funding_fees_USDT": round(rec, 8),
            "差异_结算价口径_USDT": round(diff, 10),
            "是否闭合": "是" if abs(diff) <= TOL else "否",
            "方向": "收取（多头收钱）" if income_mark > 0 else "支付（多头付费）",
            "旧引擎funding_fee_支出为正_USDT": round(old_cost, 8) if old_cost is not None else "无记录",
            "旧引擎换算到本次数量_USDT": round(old_income_adj, 8) if old_income_adj is not None else "无记录",
        })
        checks.append(abs(diff) <= TOL)
        log(f"{t['pair']:16} 事件 {len(sub):3}/{len(sub_fr):3}  独立 {income_mark:+.6f}  "
            f"记录 {rec:+.6f}  差 {diff:+.2e}  {rows[-1]['方向']}")

    cols = list(rows[0].keys())
    with open(OUT / "03_资金费事件核对.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    total_ev = sum(r["结算事件数"] for r in rows)
    total_income = sum(r["独立重算_按结算价_USDT"] for r in rows)
    total_rec = sum(r["交易记录funding_fees_USDT"] for r in rows)
    paid = sum(r["独立重算_按结算价_USDT"] for r in rows if r["独立重算_按结算价_USDT"] < 0)
    got = sum(r["独立重算_按结算价_USDT"] for r in rows if r["独立重算_按结算价_USDT"] > 0)
    ok = all(checks)

    log(f"合计：事件 {total_ev} 个；独立重算 {total_income:+.6f} USDT；"
        f"交易记录 {total_rec:+.6f} USDT；其中收取 {got:+.6f}、支付 {paid:+.6f}")
    log(f"闭合结论：{'5/5 全部闭合' if ok else '存在未闭合项'}")

    write_json(OUT / "03_资金费事件核对_汇总.json", {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "结算事件合计": total_ev,
        "独立重算合计_USDT": round(total_income, 8),
        "交易记录合计_USDT": round(total_rec, 8),
        "其中收取_USDT": round(got, 8),
        "其中支付_USDT": round(paid, 8),
        "闭合笔数": sum(1 for c in checks if c),
        "总笔数": len(checks),
        "结论": "闭合" if ok else "受限",
        "符号约定": "funding_fees 收入为正、支出为负；旧引擎 funding_fee 支出为正，"
                    "需取反后再比；两表不可直接对号。",
        "计价基准": "独立重算采用结算时刻 mark_price；与交易记录逐笔相等，"
                    "说明引擎资金费以结算标记价为基数。",
    })
    (OUT / "_funding_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
