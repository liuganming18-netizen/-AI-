# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 机制分析（02）

在 20% 方案回测完成前，先算清三件「不需要重跑就能确定」的结构性事实：
  A. 仓位缩放：20% 止损 + 0.5% 风险预算 ⇒ 名义 = 2.5% 权益；现方案实测 20.57% ⇒ 缩小 8.23 倍。
  B. 2R 门槛抬升：现方案 2R = +4.86%（实测止损中位×2）；20% 方案 2R = +40%。
     用基线账本的 max_rate 看有多少笔在观察窗内真的摸到 +40%。
  C. 尾部风险：每笔最大亏损 = 止损幅度 × 名义占权益。
     现方案 ≈ 0.5% 权益；20% 方案在正常触发时也是 0.5%，但若跳空穿越可超额，
     而名义只有 2.5% ⇒ 全损上限 2.5% 权益（远低于现方案名义 20.57%）。

重要限制（必须如实写进报告）：
  基线账本的 min_rate / max_rate 只覆盖「该笔在基线里存活的那段时间」。
  被 ATR 止损打掉的笔，我们只看到它跌到 −2.4% 为止，看不到之后的路径。
  所以「20% 止损下这笔会不会活下来」无法从基线推断 —— 只能靠新回测。
  本脚本对这类笔一律标记 UNKNOWN，不猜测。
"""
import csv
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "FT-STOP-20-AUDIT-01" / "out"
FT09 = ROOT / "evidence" / "FT-ENTRY-09" / "out"
GROUPS = [f"C1_Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]


def read_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


rows = []
for g in GROUPS:
    p = FT09 / f"trades_Wyckoff_{g}.csv"
    if not p.exists():
        continue
    for t in read_csv(p):
        entry = float(t["open_rate"])
        try:
            mn = float(t["min_rate"])
            mx = float(t["max_rate"])
        except Exception:
            continue
        rows.append(
            {
                "组": g,
                "pair": t["pair"],
                "entry": entry,
                "exit_reason": t["exit_reason"],
                "profit_abs": float(t["profit_abs"]),
                "profit_ratio": float(t["profit_ratio"]),
                "min_ret": mn / entry - 1.0,          # 观察窗内最差收益
                "max_ret": mx / entry - 1.0,          # 观察窗内最好收益
                "dur_h": float(t["trade_duration"]) / 60.0,
                "hit_20pct_stop_observed": mn <= entry * 0.80,
                "reached_40pct": mx >= entry * 1.40,
                "stopped_by_atr": t["exit_reason"] == "stop_loss",
                "trailed": t["exit_reason"] == "trailing_stop_loss",
                "timeout": "timeout" in t["exit_reason"].lower(),
            }
        )

n = len(rows)


def pct(x, d):
    return round(x / d * 100, 4) if d else None


def quantile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


reason_count = {}
for r in rows:
    reason_count[r["exit_reason"]] = reason_count.get(r["exit_reason"], 0) + 1

# 只有「完整走完持有期」的笔，其 max/min 才是完整路径
full = [r for r in rows if r["timeout"]]
stopped = [r for r in rows if r["stopped_by_atr"]]
trailed = [r for r in rows if r["trailed"]]
other = [r for r in rows if not r["timeout"] and not r["stopped_by_atr"] and not r["trailed"]]

max_rets = [r["max_ret"] for r in rows]
full_max = [r["max_ret"] for r in full]


def group_hit_40(sub):
    if not sub:
        return {"笔数": 0, "摸到+40%": 0, "占比%": None}
    c = sum(1 for r in sub if r["reached_40pct"])
    return {"笔数": len(sub), "摸到+40%": c, "占比%": pct(c, len(sub))}


by_reason = {
    "stop_loss": group_hit_40(stopped),
    "trailing_stop_loss": group_hit_40(trailed),
    "timeout_240h": group_hit_40(full),
}

summary = {
    "基线总笔数": n,
    "退出原因分布": dict(sorted(reason_count.items(), key=lambda kv: -kv[1])),
    "纯ATR止损笔数": len(stopped),
    "跟踪止损退出笔数": len(trailed),
    "超时退出笔数": len(full),
    "其他退出笔数": len(other),
    "按退出原因看+40%触及": by_reason,
    "观察窗内摸到+40%笔数(全部)": sum(1 for r in rows if r["reached_40pct"]),
    "观察窗内摸到+40%占比(全部)%": pct(sum(1 for r in rows if r["reached_40pct"]), n),
    "观察窗内跌破-20%笔数(全部)": sum(1 for r in rows if r["hit_20pct_stop_observed"]),
    "观察窗内跌破-20%占比(全部)%": pct(
        sum(1 for r in rows if r["hit_20pct_stop_observed"]), n
    ),
    "路径完整笔数(走满240h)": len(full),
    "路径不完整笔数": n - len(full),
    "路径完整占比%": pct(len(full), n),
    "最大浮盈分布_中位%": round(quantile(max_rets, 0.5) * 100, 4),
    "最大浮盈分布_p90%": round(quantile(max_rets, 0.9) * 100, 4),
    "最大浮盈分布_p99%": round(quantile(max_rets, 0.99) * 100, 4),
    "最大浮盈分布_最大%": round(max(max_rets) * 100, 4),
    "走满240h_最大浮盈中位%": round(quantile(full_max, 0.5) * 100, 4) if full_max else None,
    "走满240h_最大浮盈_p90%": round(quantile(full_max, 0.9) * 100, 4) if full_max else None,
    "无法从基线判定存活笔数": len(stopped) + len(trailed),
    "无法判定原因": "基线已提前平仓，min_rate/max_rate 不覆盖之后的路径；只有走满 240h 的笔路径完整",
}

# 尾部风险：每笔最大亏损（按止损幅度 × 名义占权益）
NOTIONAL_CUR = 0.20572745   # 现方案实测名义占权益（来自 01）
NOTIONAL_20 = 0.025
STOP_CUR = 0.024304
STOP_20 = 0.20
tail = {
    "现方案_正常触发最大亏损%权益": round(STOP_CUR * NOTIONAL_CUR * 100, 6),
    "现方案_名义全损上限%权益": round(NOTIONAL_CUR * 100, 4),
    "20方案_正常触发最大亏损%权益": round(STOP_20 * NOTIONAL_20 * 100, 6),
    "20方案_名义全损上限%权益": round(NOTIONAL_20 * 100, 4),
    "20方案_跳空穿越止损_跌50%时亏损%权益": round(0.50 * NOTIONAL_20 * 100, 6),
    "20方案_跳空穿越止损_跌100%时亏损%权益": round(1.00 * NOTIONAL_20 * 100, 6),
    "现方案_跳空穿越止损_跌50%时亏损%权益": round(0.50 * NOTIONAL_CUR * 100, 6),
    "说明": "20% 方案名义只有 2.5% 权益，即使完全归零也只亏 2.5%；现方案名义 20.57%，归零亏 20.57%",
}

# 绝对收益缩放（若成交对象完全不变）
net_base = sum(r["profit_abs"] for r in rows)
scale = {
    "基线9组净额合计": round(net_base, 6),
    "仓位缩放倍数": round(NOTIONAL_CUR / NOTIONAL_20, 4),
    "若成交对象不变_20方案净额估计": round(net_base / (NOTIONAL_CUR / NOTIONAL_20), 6),
    "说明": "仅为机械缩放参考值；实际成交对象会因止损位改变而不同，不得当预测结果",
}

print(json.dumps({"summary": summary, "tail": tail, "scale": scale}, ensure_ascii=False, indent=2))

(OUT / "02_机制分析.json").write_text(
    json.dumps({"summary": summary, "tail": tail, "scale": scale}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

with open(OUT / "02_逐笔路径.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print(f"\n写出 {OUT/'02_机制分析.json'} 与 {OUT/'02_逐笔路径.csv'}")
