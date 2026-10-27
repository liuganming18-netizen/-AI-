# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 对照分析（03）

把原 9 组 C1（基线）与 20% 止损方案逐组、逐笔对照。

尺度问题：两套方案单笔风险预算都是 0.5% 权益，但名义仓位差约 5~8 倍，
所以**绝对金额不可直接比**。统一用 R 倍数：
    R_i = profit_abs_i / (开仓时权益 × 0.005)
R 对两套方案同口径，可直接比。

四类影响归因（按 event_id 候选匹配）：
  1 避免过早止损 —— 基线被 ATR 止损、20% 方案同笔未被止损
  2 延迟确认亏损 —— 20% 方案超时退出且亏损
  3 仓位缩小     —— 同一笔的名义额比值
  4 账户路径变化 —— 只在一方出现的 event_id（仓位被占/释放差异）
"""
import csv
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "FT-STOP-20-AUDIT-01" / "out"
FT09 = ROOT / "evidence" / "FT-ENTRY-09" / "out"
S20 = ROOT / "evidence" / "FT-STOP-20-01" / "out"
GROUPS = [f"C1_Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
INIT_EQUITY = 2000.0
RISK = 0.005


def read_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def event_id(tag):
    import re

    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def load(p):
    """返回 {event_id: 记录}，并保留顺序；权益按平仓时间推进。"""
    rows = read_csv(p)
    closes = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows)
    out = {}
    for r in rows:
        o = int(r["open_timestamp"])
        eq = INIT_EQUITY + sum(pa for ct, pa in closes if ct <= o)
        entry = float(r["open_rate"])
        amount = float(r["amount"])
        try:
            sl = float(r["stop_loss_abs"])
        except Exception:
            sl = None
        risk_amt = amount * (entry - sl) if sl else None
        out[event_id(r["enter_tag"])] = {
            "pair": r["pair"],
            "entry": entry,
            "close": float(r["close_rate"]),
            "amount": amount,
            "notional": amount * entry,
            "equity_at_entry": eq,
            "stop_abs": sl,
            "stop_pct": (sl - entry) / entry if sl else None,
            "risk_amt": risk_amt,
            "risk_pct": risk_amt / eq if risk_amt and eq else None,
            "exit_reason": r["exit_reason"],
            "profit_abs": float(r["profit_abs"]),
            "profit_ratio": float(r["profit_ratio"]),
            # freqtrade 的 trade_duration 单位是「分钟」（BCH 那笔 14400 分钟 = 240 小时）
            "dur_h": float(r["trade_duration"]) / 60.0,
            "min_rate": float(r["min_rate"]),
            "max_rate": float(r["max_rate"]),
            "open_ts": o,
            "close_ts": int(r["close_timestamp"]),
            "R": float(r["profit_abs"]) / (eq * RISK) if eq > 0 else None,
        }
    return out


def metrics(d):
    rows = list(d.values())
    if not rows:
        return None
    wins = [r for r in rows if r["profit_abs"] > 0]
    losses = [r for r in rows if r["profit_abs"] < 0]
    gp = sum(r["profit_abs"] for r in wins)
    gl = abs(sum(r["profit_abs"] for r in losses))
    # 权益曲线（按平仓时间）
    seq = sorted(rows, key=lambda r: r["close_ts"])
    eq = INIT_EQUITY
    peak = eq
    mdd = 0.0
    for r in seq:
        eq += r["profit_abs"]
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0)
    durs = sorted(r["dur_h"] for r in rows)
    Rs = [r["R"] for r in rows if r["R"] is not None]
    return {
        "笔数": len(rows),
        "胜率%": round(len(wins) / len(rows) * 100, 4),
        "净额": round(sum(r["profit_abs"] for r in rows), 6),
        "收益率%": round(sum(r["profit_abs"] for r in rows) / INIT_EQUITY * 100, 4),
        "毛盈": round(gp, 6),
        "毛亏": round(gl, 6),
        "盈亏比": round(gp / gl, 4) if gl > 0 else None,
        "PF": round(gp / gl, 4) if gl > 0 else None,
        "总R": round(sum(Rs), 4) if Rs else None,
        "平均R": round(sum(Rs) / len(Rs), 4) if Rs else None,
        "最大单笔亏损USDT": round(min(r["profit_abs"] for r in rows), 6),
        "最大单笔亏损%权益": round(
            min(r["profit_abs"] / r["equity_at_entry"] for r in rows) * 100, 6
        ),
        "最大单笔盈利USDT": round(max(r["profit_abs"] for r in rows), 6),
        "最大回撤%": round(mdd * 100, 4),
        "持仓小时_中位": round(durs[len(durs) // 2], 3),
        "持仓小时_均值": round(sum(durs) / len(durs), 3),
        "名义占权益%_中位": round(
            sorted(r["notional"] / r["equity_at_entry"] for r in rows)[len(rows) // 2] * 100, 4
        ),
        "风险占权益%_中位": round(
            sorted(r["risk_pct"] for r in rows if r["risk_pct"])[
                len([r for r in rows if r["risk_pct"]]) // 2
            ]
            * 100,
            6,
        )
        if any(r["risk_pct"] for r in rows)
        else None,
        "退出原因分布": {k: sum(1 for r in rows if r["exit_reason"] == k) for k in sorted({r["exit_reason"] for r in rows})},
    }


group_rows = []
all_attr = []
pair_rows = []
for g in GROUPS:
    bp = FT09 / f"trades_Wyckoff_{g}.csv"
    sp = S20 / f"trades_Wyckoff_C1_Stop20_{g.replace('C1_', '')}.csv"
    if not bp.exists() or not sp.exists():
        group_rows.append({"组": g, "状态": "缺文件", "基线存在": bp.exists(), "20方案存在": sp.exists()})
        continue
    B = load(bp)
    S = load(sp)
    mb, ms = metrics(B), metrics(S)
    common = set(B) & set(S)
    only_b = set(B) - set(S)
    only_s = set(S) - set(B)

    # ---- 四类归因 ----
    saved = []          # 1 避免过早止损
    delayed_loss = []   # 2 延迟确认亏损
    stake_ratio = []    # 3 仓位缩小
    for eid in common:
        b, s = B[eid], S[eid]
        stake_ratio.append(s["notional"] / b["notional"] if b["notional"] else None)
        if b["exit_reason"] == "stop_loss" and s["exit_reason"] != "stop_loss":
            saved.append(
                {
                    "组": g, "event_id": eid, "pair": b["pair"],
                    "基线出场": b["exit_reason"], "基线盈亏": round(b["profit_abs"], 4),
                    "基线R": round(b["R"], 4),
                    "20方案出场": s["exit_reason"], "20方案盈亏": round(s["profit_abs"], 4),
                    "20方案R": round(s["R"], 4),
                    "救回后是否盈利": s["profit_abs"] > 0,
                }
            )
        if s["exit_reason"] == "timeout_240h" and s["profit_abs"] < 0:
            delayed_loss.append(
                {
                    "组": g, "event_id": eid, "pair": b["pair"],
                    "基线出场": b["exit_reason"], "基线盈亏": round(b["profit_abs"], 4),
                    "基线R": round(b["R"], 4), "基线持仓h": round(b["dur_h"], 1),
                    "20方案出场": s["exit_reason"], "20方案盈亏": round(s["profit_abs"], 4),
                    "20方案R": round(s["R"], 4), "20方案持仓h": round(s["dur_h"], 1),
                }
            )

    sr = [x for x in stake_ratio if x]
    sr_sorted = sorted(sr)
    group_rows.append(
        {
            "组": g,
            "状态": "ok",
            "基线": mb,
            "20方案": ms,
            "共同候选": len(common),
            "仅基线": len(only_b),
            "仅20方案": len(only_s),
            "名义缩放倍数_中位": round(sr_sorted[len(sr_sorted) // 2], 4) if sr_sorted else None,
            "避免过早止损笔数": len(saved),
            "救回后盈利笔数": sum(1 for x in saved if x["救回后是否盈利"]),
            "延迟确认亏损笔数": len(delayed_loss),
            "R变化(20方案-基线)": round((ms["总R"] or 0) - (mb["总R"] or 0), 4),
        }
    )
    all_attr += saved + delayed_loss
    for eid in sorted(common):
        b, s = B[eid], S[eid]
        pair_rows.append(
            {
                "组": g, "event_id": eid, "pair": b["pair"],
                "基线出场": b["exit_reason"], "基线盈亏": round(b["profit_abs"], 4),
                "基线R": round(b["R"], 4), "基线名义": round(b["notional"], 4),
                "基线持仓h": round(b["dur_h"], 2),
                "20出场": s["exit_reason"], "20盈亏": round(s["profit_abs"], 4),
                "20R": round(s["R"], 4), "20名义": round(s["notional"], 4),
                "20持仓h": round(s["dur_h"], 2),
                "名义比": round(s["notional"] / b["notional"], 4) if b["notional"] else None,
                "盈亏比(20/基线)": round(s["profit_abs"] / b["profit_abs"], 4)
                if b["profit_abs"] else None,
            }
        )

ok = [r for r in group_rows if r["状态"] == "ok"]

print(f"{'组':<12}{'基笔':>5}{'基胜%':>8}{'基净':>11}{'基总R':>9}"
      f"{'20笔':>5}{'20胜%':>8}{'20净':>11}{'20总R':>9}{'缩放':>7}{'救回':>5}{'延亏':>5}")
for r in ok:
    b, s = r["基线"], r["20方案"]
    print(
        f"{r['组']:<12}{b['笔数']:>5}{b['胜率%']:>8.2f}{b['净额']:>11.2f}{b['总R']:>9.3f}"
        f"{s['笔数']:>5}{s['胜率%']:>8.2f}{s['净额']:>11.2f}{s['总R']:>9.3f}"
        f"{r['名义缩放倍数_中位']:>7.2f}{r['避免过早止损笔数']:>5}{r['延迟确认亏损笔数']:>5}"
    )

(OUT / "03_对照.json").write_text(
    json.dumps({"组": group_rows, "归因明细": all_attr}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
if pair_rows:
    with open(OUT / "03_逐笔对照.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(pair_rows[0].keys()))
        w.writeheader()
        w.writerows(pair_rows)
if all_attr:
    fields = []
    for r in all_attr:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(OUT / "03_四类归因明细.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_attr)

print(f"\n完成 {len(ok)}/{len(GROUPS)} 组")
