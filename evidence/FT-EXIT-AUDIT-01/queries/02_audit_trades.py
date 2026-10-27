# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 02：逐笔核对（退出原因 / 止损价 / 风险预算 / 名义帽 / 拒单 / 资金费 / 权益）

只读 FT-EXIT-01 的产物，不改它。每个数字都从 trades CSV + 回测包 JSON + 确认表重算。
"""
from __future__ import annotations

import csv
import glob
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
OUT.mkdir(parents=True, exist_ok=True)

INIT = 2000.0
MIN_STAKE_ABS = 5.0  # binance USDT 永续最小名义额（用于识别"被强行放大"的下限）
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
SCHEMES = {"S": "Wyckoff_C1_S_P0", "P": "Wyckoff_C1_P_Stop20"}


def read_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eid(tag: str) -> str:
    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def load_trades(scheme: str, suf: str):
    p = EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"
    if not p.exists():
        return None
    rows = read_csv(p)
    if not rows:
        return []
    cl = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows)
    for r in rows:
        o = int(r["open_timestamp"])
        r["_eq"] = INIT + sum(pa for ct, pa in cl if ct <= o)
        r["_entry"] = float(r["open_rate"])
        r["_stop"] = float(r["stop_loss_abs"])
        r["_amt"] = float(r["amount"])
        r["_stake"] = float(r["stake_amount"])
        r["_pnl"] = float(r["profit_abs"])
        r["_fund"] = float(r["funding_fees"]) if r.get("funding_fees") not in (None, "") else 0.0
        r["_dur_h"] = float(r["trade_duration"]) / 60.0
        r["_risk"] = r["_amt"] * (r["_entry"] - r["_stop"])       # 正数=正常风险
        r["_risk_pct"] = r["_risk"] / r["_eq"] * 100.0
        r["_notional"] = r["_amt"] * r["_entry"]
        r["_notional_pct"] = r["_notional"] / r["_eq"] * 100.0
    return rows


def load_zip_summary(scheme: str, suf: str):
    p = EXP / f"out/backtest-result-{SCHEMES[scheme]}_{suf}.zip"
    if not p.exists():
        return None
    with zipfile.ZipFile(p) as z:
        names = [n for n in z.namelist() if n.endswith(".json") and not n.endswith(".meta.json")]
        if not names:
            return None
        d = json.load(z.open(names[0]))
    st = d.get("strategy", {})
    key = next(iter(st), None)
    if key is None:
        return None
    m = st[key]
    return {k: m.get(k) for k in
            ("total_trades", "final_balance", "profit_total_abs", "profit_total",
             "max_drawdown_account", "starting_balance", "funding_fees", "winrate")}


def load_p0(scheme: str, suf: str):
    """从确认表拿每个事件的原始 P0（S 组止损必须精确等于它）"""
    p = EXP / f"out/audit_confirmation_{SCHEMES[scheme]}_{suf}.csv"
    if not p.exists():
        return {}
    out = {}
    for r in read_csv(p):
        if r.get("orig_P0") not in (None, ""):
            try:
                out[r["event_id"]] = float(r["orig_P0"])
            except ValueError:
                pass
    return out


def log_rejections(scheme: str, suf: str):
    p = EXP / f"logs/{SCHEMES[scheme]}_{suf}_backtest.log"
    if not p.exists():
        return None
    txt = p.read_text(encoding="utf-8", errors="ignore")
    n = txt.count("超出风险预算硬性拒单")
    ms = re.findall(r"\[(\S+?)\] raw_stake=([\d.]+) < min_stake=([\d.]+)", txt)
    return {"拒单条数": n,
            "样例": [{"pair": a, "raw_stake": float(b), "min_stake": float(c)} for a, b, c in ms[:5]]}


RESULT = {"S": {}, "P": {}}
PER_TRADE = []

for scheme in ("S", "P"):
    for suf in SUF:
        rows = load_trades(scheme, suf)
        if rows is None:
            RESULT[scheme][suf] = {"状态": "缺产物"}
            continue
        if not rows:
            RESULT[scheme][suf] = {"状态": "空"}
            continue

        z = load_zip_summary(scheme, suf)
        p0map = load_p0(scheme, suf)
        rej = log_rejections(scheme, suf)

        exits = Counter(r["exit_reason"] for r in rows)
        STOP_LABELS = ("stop_loss", "trailing_stop_loss")  # 自定义止损触发的两种标签
        illegal = {k: v for k, v in exits.items() if k not in STOP_LABELS + ("force_exit",)}

        # 止损价核对
        sl_dev = []
        sl_moved = 0
        sl_above_entry = 0
        p0_exact = p0_mismatch = 0
        p0_missing = 0
        p0_maxgap = 0.0
        p_moved_gap = 0.0
        for r in rows:
            dev = (r["_stop"] - r["_entry"]) / r["_entry"]
            sl_dev.append(dev)
            if dev > 1e-9:
                sl_above_entry += 1
            if scheme == "P":
                # 止损 = 成交价 × 0.80，freqtrade 对多头按 ROUND_UP 取整，允许向上不超过 1 个 tick
                raw = str(r.get("stop_loss_abs", ""))
                dec = len(raw.split(".")[1]) if "." in raw else 0
                tick = 10.0 ** (-dec) if dec else 0.0
                gap = r["_stop"] - r["_entry"] * 0.80
                if not (-1e-12 <= gap <= max(tick, r["_entry"] * 1e-9) * 1.0000001):
                    sl_moved += 1
                p_moved_gap = max(p_moved_gap, gap / r["_entry"] * 100)
            else:
                key = eid(r.get("enter_tag", ""))
                if key in p0map:
                    # freqtrade 对多头止损按 ROUND_UP 取整到最小价位：允许向上不超过 1 个 tick
                    raw = str(r.get("stop_loss_abs", ""))
                    dec = len(raw.split(".")[1]) if "." in raw else 0
                    tick = 10.0 ** (-dec) if dec else 0.0
                    gap = r["_stop"] - p0map[key]
                    p0_maxgap = max(p0_maxgap, gap / p0map[key] if p0map[key] else 0)
                    if -1e-12 <= gap <= max(tick, p0map[key] * 1e-9) * 1.0000001:
                        p0_exact += 1
                    else:
                        p0_mismatch += 1
                else:
                    p0_missing += 1

        # 风险预算
        over_risk = [r for r in rows if r["_risk_pct"] > 0.5 + 1e-6]
        neg_risk = [r for r in rows if r["_risk"] <= 0]
        over_notional = [r for r in rows if r["_notional_pct"] > 30.0 + 1e-6]
        below_min = [r for r in rows if r["_stake"] < MIN_STAKE_ABS]

        # 期末结算
        closed = [r for r in rows if r["exit_reason"] in STOP_LABELS]
        still = [r for r in rows if r["exit_reason"] == "force_exit"]

        # 权益
        sum_pnl = sum(r["_pnl"] for r in rows)
        eq_calc = INIT + sum_pnl
        fb = z["final_balance"] if z else None

        fund_sum = sum(r["_fund"] for r in rows)
        fund_nonzero = sum(1 for r in rows if abs(r["_fund"]) > 1e-12)

        durs = sorted(r["_dur_h"] for r in rows)
        info = {
            "状态": "ok",
            "笔数": len(rows),
            "退出原因": dict(exits),
            "非法退出原因": illegal,
            "止损退出笔数": sum(exits.get(x, 0) for x in STOP_LABELS),
            "标为stop_loss": exits.get("stop_loss", 0),
            "标为trailing_stop_loss": exits.get("trailing_stop_loss", 0),
            "期末结算笔数": exits.get("force_exit", 0),
            "止损距成交价_中位%": round(sorted(sl_dev)[len(sl_dev) // 2] * 100, 4),
            "止损距成交价_最小%": round(min(sl_dev) * 100, 4),
            "止损距成交价_最大%": round(max(sl_dev) * 100, 4),
            "止损高于成交价笔数": sl_above_entry,
            "止损被上移笔数(P组口径)": sl_moved,
            "P组止损相对入场×0.8最大上偏%": round(p_moved_gap, 6),
            "S组止损=P0_精确匹配": p0_exact,
            "S组止损≠P0": p0_mismatch,
            "S组P0缺失": p0_missing,
            "S组止损相对P0最大上偏%": round(p0_maxgap * 100, 6),
            "风险超0.5%笔数": len(over_risk),
            "风险≤0笔数": len(neg_risk),
            "风险占权益_中位%": round(sorted(r["_risk_pct"] for r in rows)[len(rows) // 2], 4),
            "风险占权益_最大%": round(max(r["_risk_pct"] for r in rows), 4),
            "名义超30%笔数": len(over_notional),
            "名义占权益_中位%": round(sorted(r["_notional_pct"] for r in rows)[len(rows) // 2], 2),
            "名义占权益_最大%": round(max(r["_notional_pct"] for r in rows), 2),
            "stake<5USDT笔数": len(below_min),
            "stake最小": round(min(r["_stake"] for r in rows), 4),
            "min_stake拒单": rej,
            "已平仓净额": round(sum(r["_pnl"] for r in closed), 2),
            "期末浮盈亏": round(sum(r["_pnl"] for r in still), 2),
            "期末占用名义": round(sum(r["_notional"] for r in still), 2),
            "期末占用stake": round(sum(r["_stake"] for r in still), 2),
            "全口径净额": round(sum_pnl, 2),
            "资金费合计": round(fund_sum, 6),
            "资金费非零笔数": fund_nonzero,
            "权益_2000+Σpnl": round(eq_calc, 6),
            "权益_回测包final_balance": round(fb, 6) if fb is not None else None,
            "权益差": round(eq_calc - fb, 6) if fb is not None else None,
            "回测包max_drawdown_account%": round(z["max_drawdown_account"] * 100, 2) if z and z.get("max_drawdown_account") is not None else None,
            "持仓中位h": round(durs[len(durs) // 2], 1),
            "持仓最大h": round(max(durs), 1),
            "持仓>240h笔数": sum(1 for x in durs if x > 240),
            "持仓>720h笔数": sum(1 for x in durs if x > 720),
            "已平仓胜率%": round(sum(1 for r in closed if r["_pnl"] > 0) / len(closed) * 100, 2) if closed else None,
            "全口径胜率%": round(sum(1 for r in rows if r["_pnl"] > 0) / len(rows) * 100, 2),
            "总R": round(sum(r["_pnl"] / (r["_eq"] * 0.005) for r in rows), 4),
            "已实现R": round(sum(r["_pnl"] / (r["_eq"] * 0.005) for r in closed), 4),
            "未实现R": round(sum(r["_pnl"] / (r["_eq"] * 0.005) for r in still), 4),
            "平均R": round(sum(r["_pnl"] / (r["_eq"] * 0.005) for r in rows) / len(rows), 6),
        }
        RESULT[scheme][suf] = info

        for r in rows:
            PER_TRADE.append({
                "方案": scheme, "组": suf, "pair": r["pair"], "enter_tag": eid(r.get("enter_tag", "")),
                "open_time": r["open_date"], "close_time": r["close_date"],
                "entry": r["_entry"], "stop": r["_stop"], "close": float(r["close_rate"]),
                "exit_reason": r["exit_reason"], "dur_h": round(r["_dur_h"], 2),
                "amount": r["_amt"], "stake": round(r["_stake"], 4),
                "notional_pct": round(r["_notional_pct"], 4),
                "risk_usdt": round(r["_risk"], 6), "risk_pct": round(r["_risk_pct"], 6),
                "equity_at_entry": round(r["_eq"], 4),
                "profit_abs": round(r["_pnl"], 6), "funding_fees": round(r["_fund"], 8),
                "R": round(r["_pnl"] / (r["_eq"] * 0.005), 6),
            })

# ---------- 打印 ----------
print("=" * 110)
print("【A】逐组逐笔核对")
print("=" * 110)
hdr = (f"{'方案':<3}{'组':<10}{'笔数':>5}{'止损':>5}{'期末':>5}{'非法退出':>8}"
       f"{'止损距价%中位':>13}{'止损>成交':>9}{'风险>0.5%':>10}{'风险中位%':>10}"
       f"{'名义最大%':>10}{'净额':>11}{'浮盈亏':>9}{'权益差':>10}")
print(hdr)
for scheme in ("S", "P"):
    for suf in SUF:
        d = RESULT[scheme][suf]
        if d.get("状态") != "ok":
            print(f"{scheme:<3}{suf:<10}  {d.get('状态')}")
            continue
        print(f"{scheme:<3}{suf:<10}{d['笔数']:>5}{d['止损退出笔数']:>5}{d['期末结算笔数']:>5}"
              f"{len(d['非法退出原因']):>8}{d['止损距成交价_中位%']:>13.2f}{d['止损高于成交价笔数']:>9}"
              f"{d['风险超0.5%笔数']:>10}{d['风险占权益_中位%']:>10.3f}"
              f"{d['名义占权益_最大%']:>10.1f}{d['全口径净额']:>11.2f}{d['期末浮盈亏']:>9.2f}"
              f"{str(d['权益差']):>10}")

print()
print("=" * 110)
print("【B】关键项汇总（S 组 / P 组）")
print("=" * 110)
for scheme in ("S", "P"):
    ok = [d for d in RESULT[scheme].values() if d.get("状态") == "ok"]
    if not ok:
        print(f"  {scheme} 组：无完成数据")
        continue
    print(f"  ── {scheme} 组（{len(ok)}/9 组已完成）──")
    print(f"    笔数 {sum(d['笔数'] for d in ok)}  止损退出 {sum(d['止损退出笔数'] for d in ok)}  "
          f"期末结算 {sum(d['期末结算笔数'] for d in ok)}")
    print(f"    非法退出原因合计: {sum(len(d['非法退出原因']) for d in ok)} 类")
    print(f"    止损高于成交价: {sum(d['止损高于成交价笔数'] for d in ok)} 笔")
    print(f"    风险超 0.5%: {sum(d['风险超0.5%笔数'] for d in ok)} 笔   风险≤0: {sum(d['风险≤0笔数'] for d in ok)} 笔")
    print(f"    名义超 30%: {sum(d['名义超30%笔数'] for d in ok)} 笔")
    print(f"    stake<5USDT: {sum(d['stake<5USDT笔数'] for d in ok)} 笔")
    print(f"    资金费合计 {sum(d['资金费合计'] for d in ok):.6f} USDT（非零 {sum(d['资金费非零笔数'] for d in ok)} 笔）")
    print(f"    全口径净额 {sum(d['全口径净额'] for d in ok):.2f} USDT")
    print(f"    总 R {sum(d['总R'] for d in ok):+.2f}（已实现 {sum(d['已实现R'] for d in ok):+.2f} / "
          f"未实现 {sum(d['未实现R'] for d in ok):+.2f}）  平均 R {sum(d['平均R'] for d in ok)/len(ok):+.4f}")
    if scheme == "S":
        print(f"    S组止损=P0 精确匹配 {sum(d['S组止损=P0_精确匹配'] for d in ok)} 笔，"
              f"≠P0 {sum(d['S组止损≠P0'] for d in ok)} 笔，P0缺失 {sum(d['S组P0缺失'] for d in ok)} 笔")
    else:
        print(f"    止损被上移 {sum(d['止损被上移笔数(P组口径)'] for d in ok)} 笔（应为 0）")

(OUT / "02_逐笔核对.json").write_text(json.dumps(RESULT, ensure_ascii=False, indent=2),
                                encoding="utf-8")
with open(OUT / "02_逐笔核对明细.csv", "w", encoding="utf-8-sig", newline="") as f:
    if PER_TRADE:
        w = csv.DictWriter(f, fieldnames=list(PER_TRADE[0].keys()))
        w.writeheader()
        w.writerows(PER_TRADE)
print(f"\n写出 {OUT/'02_逐笔核对.json'} 与 02_逐笔核对明细.csv（{len(PER_TRADE)} 行）")
