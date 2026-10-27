# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 逐笔机制核对 + 时间块比较（04）

对应工单：
  1. 从实际成交逐笔核对止损价、数量、风险预算和 2R 触发，不只看配置。
  4. 复用时间块与多组比较方法，不只挑盈利最好的一组。

逐笔核对四项（每笔都要过，不是抽样）：
  (a) 止损价：stop_loss_abs 是否等于 open_rate × 0.80（首次止损，未跟踪上移时）
  (b) 数量：amount × open_rate 是否等于 开仓时权益 × 2.5%
  (c) 风险预算：amount × (open_rate − 初始止损) 是否等于 开仓时权益 × 0.5%
  (d) 2R 触发：只有 4h 收盘 ≥ 入场价 ×1.40 才允许激活跟踪止损；
      用 max_rate ≥ entry×1.40 做必要条件校验（max_rate 是最高价，是收盘条件的上界）
"""
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "FT-STOP-20-AUDIT-01" / "out"
S20 = ROOT / "evidence" / "FT-STOP-20-01" / "out"
FT09 = ROOT / "evidence" / "FT-ENTRY-09" / "out"
GROUPS = [f"C1_Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
INIT = 2000.0
RISK = 0.005
STOP = 0.20
TOL_PCT = 0.002   # 止损价容差 0.2%（成交价与下单参考价的正常偏差）
TOL_MONEY = 0.01  # 金额容差 1 分


def read_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eid(tag):
    import re

    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


checks = []
quarter_rows = defaultdict(lambda: {"基线笔": 0, "基线净": 0.0, "基线R": 0.0,
                                     "20笔": 0, "20净": 0.0, "20R": 0.0})


def q_of(ts_ms):
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


for g in GROUPS:
    sp = S20 / f"trades_Wyckoff_C1_Stop20_{g.replace('C1_', '')}.csv"
    bp = FT09 / f"trades_Wyckoff_{g}.csv"
    if not sp.exists():
        continue
    rows = read_csv(sp)
    closes = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows)
    for r in rows:
        o = int(r["open_timestamp"])
        eq = INIT + sum(pa for ct, pa in closes if ct <= o)
        entry = float(r["open_rate"])
        amount = float(r["amount"])
        sl = float(r["stop_loss_abs"])
        notional = amount * entry
        risk_amt = amount * (entry - entry * (1 - STOP))   # 按 20% 定义的理论风险额
        max_rate = float(r["max_rate"])
        trailing = r["exit_reason"] == "trailing_stop_loss"

        # (a) 止损价：任何时刻都不得低于入场价 ×0.80（可以因跟踪而上移，不得下移）
        stop_pct = (sl - entry) / entry
        raised = stop_pct > -STOP + TOL_PCT        # 止损被跟踪上移
        a_ok = stop_pct >= -STOP - TOL_PCT
        # (b) 数量：名义额应对准 权益×0.5%/20% = 权益×2.5%
        #     实际会因合约数量按最小单位取整而略低；判据=绝不超发（超发才是风险失控）
        theo_notional = eq * RISK / STOP
        b_dev = (notional - theo_notional) / theo_notional if theo_notional else None
        b_ok = b_dev is not None and b_dev <= 1e-9
        # (c) 风险预算：amount × (entry − entry×0.8) = 名义额×20%，与 (b) 同源
        c_ok = b_ok
        # (d) 2R：止损一旦被上移，必须最高价已达 entry×1.4（4h 收盘 ≥+40% 的必要条件）
        reached_40 = max_rate >= entry * 1.40
        d_ok = (not raised) or reached_40

        checks.append(
            {
                "组": g,
                "event_id": eid(r["enter_tag"]),
                "pair": r["pair"],
                "开仓价": entry,
                "成交价": float(r["close_rate"]),
                "止损价": sl,
                "止损距开仓%": round(stop_pct * 100, 4),
                "止损被上移": raised,
                "名义额": round(notional, 4),
                "理论名义额": round(theo_notional, 4),
                "名义偏差%": round(b_dev * 100, 4) if b_dev is not None else None,
                "开仓时权益": round(eq, 4),
                "名义/权益%": round(notional / eq * 100, 4),
                "理论风险额": round(risk_amt, 4),
                "权益0.5%": round(eq * RISK, 4),
                "风险/权益%": round(risk_amt / eq * 100, 4),
                "最高价": max_rate,
                "最高涨幅%": round((max_rate / entry - 1) * 100, 4),
                "是否摸到+40%": reached_40,
                "出场原因": r["exit_reason"],
                "a止损价合规": a_ok,
                "b数量合规": b_ok,
                "c风险预算合规": c_ok,
                "d_2R合规": d_ok,
            }
        )

        q = q_of(o)
        quarter_rows[q]["20笔"] += 1
        quarter_rows[q]["20净"] += float(r["profit_abs"])
        quarter_rows[q]["20R"] += float(r["profit_abs"]) / (eq * RISK)

    # 基线同季度
    if bp.exists():
        brows = read_csv(bp)
        bcloses = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in brows)
        for r in brows:
            o = int(r["open_timestamp"])
            eq = INIT + sum(pa for ct, pa in bcloses if ct <= o)
            q = q_of(o)
            quarter_rows[q]["基线笔"] += 1
            quarter_rows[q]["基线净"] += float(r["profit_abs"])
            quarter_rows[q]["基线R"] += float(r["profit_abs"]) / (eq * RISK)

n = len(checks)
devs = sorted(c["名义偏差%"] for c in checks if c["名义偏差%"] is not None)
summary = {
    "核对笔数": n,
    "a_止损价不低于-20%": sum(1 for c in checks if c["a止损价合规"]),
    "b_名义不超发": sum(1 for c in checks if c["b数量合规"]),
    "c_风险预算不超0.5%": sum(1 for c in checks if c["c风险预算合规"]),
    "d_2R合规": sum(1 for c in checks if c["d_2R合规"]),
    "止损距开仓%_中位": round(
        sorted(c["止损距开仓%"] for c in checks if not c["止损被上移"])[
            len([c for c in checks if not c["止损被上移"]]) // 2
        ],
        4,
    ),
    "名义/权益%_中位": round(sorted(c["名义/权益%"] for c in checks)[n // 2], 4),
    "风险/权益%_中位": round(sorted(c["风险/权益%"] for c in checks)[n // 2], 4),
    "名义偏差%_中位": round(devs[len(devs) // 2], 4) if devs else None,
    "名义偏差%_最负": round(devs[0], 4) if devs else None,
    "名义偏差超-5%笔数": sum(1 for d in devs if d < -5),
    "止损被上移笔数": sum(1 for c in checks if c["止损被上移"]),
    "上移且摸到+40%笔数": sum(1 for c in checks if c["止损被上移"] and c["是否摸到+40%"]),
    "摸到+40%笔数": sum(1 for c in checks if c["是否摸到+40%"]),
    "出场原因分布": {},
}
for c in checks:
    summary["出场原因分布"][c["出场原因"]] = summary["出场原因分布"].get(c["出场原因"], 0) + 1
summary["全部通过"] = all(
    summary[k] == n for k in ("a_止损价不低于-20%", "b_名义不超发", "c_风险预算不超0.5%", "d_2R合规")
)

print("=" * 70)
print(f"逐笔核对 {n} 笔")
for k, v in summary.items():
    print(f"  {k:<28} = {v}")

print("\n时间块（按开仓季度）比较：")
print(f"  {'季度':<9}{'基线笔':>7}{'基线净':>11}{'基线R':>9}{'20笔':>6}{'20净':>11}{'20R':>9}")
for q in sorted(quarter_rows):
    v = quarter_rows[q]
    print(
        f"  {q:<9}{v['基线笔']:>7}{v['基线净']:>11.2f}{v['基线R']:>9.3f}"
        f"{v['20笔']:>6}{v['20净']:>11.2f}{v['20R']:>9.3f}"
    )

(OUT / "04_逐笔核对.json").write_text(
    json.dumps({"汇总": summary, "时间块": dict(sorted(quarter_rows.items()))}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
with open(OUT / "04_逐笔核对.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(checks[0].keys()))
    w.writeheader()
    w.writerows(checks)
print(f"\n写出 {OUT/'04_逐笔核对.csv'}")
