# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 06：成交归因与「假跟踪」标签归因

两个目的：
 A. 把「确认了但没成交」的每一笔，按当时槽位占用情况归因。
    —— 若被跳过的信号在计划执行时刻 3 个槽位都占着，就能证明「长期持仓真实占用槽位」；
       若槽位没满却被跳过，则说明还有别的原因（例如最小下单额拒单），必须单列，不能混算。
 B. 核对 freqtrade 的 exit_reason 标签：
    `trailing_stop_loss` 的真实语义是「止损价曾被上调过」（trade_model.py:896），
    不是「用了跟踪退出」。本方案止损终生固定，所以只要出现这个标签，就必须证明
    它只是「最小价位取整」造成的机械上移，而不是真的把止损抬上去了。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
SCHEMES = {"S": "Wyckoff_C1_S_P0", "P": "Wyckoff_C1_P_Stop20"}
BAR_MS = 15 * 60 * 1000
EQUITY0 = 2000.0
RISK = 0.005


def read_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def tick_of(raw: str) -> float:
    """按账面止损价的书写小数位推最小价位（freqtrade 已把止损价取整到该精度）。"""
    raw = (raw or "").strip()
    if "." not in raw:
        return 1.0
    return 10.0 ** (-len(raw.split(".")[1]))


def event_id_of(enter_tag: str) -> str:
    # enter_tag 形如 cand_C1_ev_FT_ENTRY_02_binance_BCHUSDT_1639209600000_1639432800000
    t = enter_tag.split("cand_C1_", 1)[-1]
    t = t.split("_1639")[0] if False else t
    # 事件 id = ev_..._<symbol>_<窗口起点毫秒>
    parts = t.split("_")
    return "_".join(parts[:7]) if len(parts) >= 7 else t


R = {}
print("=" * 108)
print("【A】确认信号 → 成交归因（按当时槽位占用判定）")
print("=" * 108)
print(f"{'方案':<3}{'组':<9}{'确认':>5}{'成交':>5}{'跳过':>5}"
      f"{'槽位满':>9}{'同币持仓':>13}{'其他':>8}{'成交率%':>8}{'入场时点=确认+15m':>17}")

for scheme in ("S", "P"):
    R[scheme] = {}
    for suf in SUF:
        tp = EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"
        cp = EXP / f"out/audit_confirmation_{SCHEMES[scheme]}_{suf}.csv"
        if not tp.exists() or not cp.exists():
            continue
        trades = read_csv(tp)
        conf = read_csv(cp)

        # 成交表：event_id → 交易
        tr = {}
        time_ok = 0
        for r in trades:
            eid = event_id_of(r["enter_tag"])
            tr[eid] = r
        # 确认表：只取 CONFIRMED（同一 event_id 可能重复出现，去重）
        confirmed = {}
        for c in conf:
            if c["decision"] != "CONFIRMED":
                continue
            confirmed.setdefault(c["event_id"], c)

        # 入场时点核对：open_timestamp 是否 = confirm_time_ms + 1 根
        for eid, r in tr.items():
            c = confirmed.get(eid)
            if c and int(r["open_timestamp"]) == int(c["confirm_time_ms"]) + BAR_MS:
                time_ok += 1

        # 权益曲线（按平仓时间累计，与 02 脚本同口径）
        closed = sorted(
            ((int(r["close_timestamp"]), float(r["profit_abs"])) for r in trades),
            key=lambda x: x[0],
        )

        def equity_at(t: int) -> float:
            return EQUITY0 + sum(p for ts, p in closed if ts <= t)

        matched = [e for e in confirmed if e in tr]
        missing = [e for e in confirmed if e not in tr]

        occ_full = occ_free = 0
        free_samples = []
        est = []
        same_pair = other = 0
        for eid in missing:
            c = confirmed[eid]
            t_exec = int(c["confirm_time_ms"]) + BAR_MS
            occ = sum(
                1 for r in trades
                if int(r["open_timestamp"]) <= t_exec < int(r["close_timestamp"])
            )
            if occ >= 3:
                occ_full += 1
                continue
            occ_free += 1
            # 槽位没满却被跳过：先看同一标的是否已经持仓（freqtrade 不允许同币双开）
            pfx = c["symbol"].replace("USDT", "") + "/USDT:USDT"
            dup = any(
                r["pair"] == pfx
                and int(r["open_timestamp"]) <= t_exec < int(r["close_timestamp"])
                for r in trades
            )
            if dup:
                same_pair += 1
            else:
                other += 1
                if len(free_samples) < 5:
                    free_samples.append({
                        "event_id": eid, "symbol": c["symbol"],
                        "confirm_time": c["confirm_time"], "占用槽位": occ,
                    })
            # 估算这笔若放行会有多大下单额（用于判断会不会撞最小下单额）
            px = float(c["confirm_close"])
            if scheme == "P":
                rd = px * 0.20
            else:
                p0 = float(c["orig_P0"])
                rd = px - p0
            if rd > 0:
                eq = equity_at(t_exec)
                est.append(eq * RISK / rd * px)

        n_conf = len(confirmed)
        R[scheme][suf] = {
            "确认数": n_conf, "成交数": len(matched), "跳过数": len(missing),
            "跳过_槽位已满": occ_full,
            "跳过_槽位未满_同币已持仓": same_pair,
            "跳过_槽位未满_其他": other,
            "成交率%": round(len(matched) / n_conf * 100, 1) if n_conf else None,
            "入场时点=确认+15m": f"{time_ok}/{len(tr)}",
            "其他原因样例": free_samples,
            "跳过_估算下单额中位USDT": round(sorted(est)[len(est) // 2], 2) if est else None,
            "跳过_估算下单额最小USDT": round(min(est), 2) if est else None,
        }
        v = R[scheme][suf]
        print(f"{scheme:<3}{suf:<9}{n_conf:>5}{len(matched):>5}{len(missing):>5}"
              f"{occ_full:>9}{same_pair:>13}{other:>8}{v['成交率%']:>8}"
              f"{v['入场时点=确认+15m']:>17}")

print()
print("=" * 108)
print("【A2】汇总：跳过信号的归因")
print("=" * 108)
for scheme in ("S", "P"):
    ok = [v for v in R[scheme].values() if "确认数" in v]
    if not ok:
        continue
    n_conf = sum(v["确认数"] for v in ok)
    n_fill = sum(v["成交数"] for v in ok)
    n_skip = sum(v["跳过数"] for v in ok)
    full = sum(v["跳过_槽位已满"] for v in ok)
    dup = sum(v["跳过_槽位未满_同币已持仓"] for v in ok)
    oth = sum(v["跳过_槽位未满_其他"] for v in ok)
    print(f"  {scheme} 组（{len(ok)}/9）：确认 {n_conf}、成交 {n_fill}、跳过 {n_skip}"
          f"（槽位已满 {full} = {full/n_skip*100:.1f}%，同币已持仓 {dup}，其他 {oth}）")
    print(f"     成交率 {n_fill/n_conf*100:.1f}%")

print()
print("=" * 108)
print("【B】`trailing_stop_loss` 标签归因：是「取整机械上移」还是「真把止损抬上去」")
print("=" * 108)
print(f"{'方案':<3}{'组':<9}{'笔数':>5}{'标签trailing':>12}{'止损>目标价':>12}"
      f"{'最大上偏%':>11}{'最大上偏(价位数)':>17}")

for scheme in ("S", "P"):
    R.setdefault("_标签", {})[scheme] = {}
    for suf in SUF:
        tp = EXP / f"out/trades_{SCHEMES[scheme]}_{suf}.csv"
        cp = EXP / f"out/audit_confirmation_{SCHEMES[scheme]}_{suf}.csv"
        if not tp.exists() or not cp.exists():
            continue
        trades = read_csv(tp)
        conf = {c["event_id"]: c for c in read_csv(cp) if c["decision"] == "CONFIRMED"}
        n_lab = n_up = n_over1 = 0
        max_dev_pct = 0.0
        max_dev_ticks = 0.0
        detail = []
        for r in trades:
            if r["exit_reason"] == "trailing_stop_loss":
                n_lab += 1
            stop = float(r["stop_loss_abs"])
            entry = float(r["open_rate"])
            if scheme == "P":
                target = entry * 0.80
            else:
                eid = event_id_of(r["enter_tag"])
                c = conf.get(eid)
                if c is None:
                    continue
                target = float(c["orig_P0"])
            tk = tick_of(r["stop_loss_abs"])
            dev = stop - target
            if dev > 1e-12:
                n_up += 1
                pct = dev / target * 100
                ticks = dev / tk
                if ticks > 1.0000001:
                    n_over1 += 1
                if pct > max_dev_pct:
                    max_dev_pct = pct
                if ticks > max_dev_ticks:
                    max_dev_ticks = ticks
                if len(detail) < 3:
                    detail.append({"pair": r["pair"], "stop": stop, "target": round(target, 8),
                                   "上偏%": round(pct, 6), "上偏价位数": round(ticks, 3),
                                   "exit_reason": r["exit_reason"]})
        R["_标签"][scheme][suf] = {
            "笔数": len(trades), "标签trailing": n_lab, "止损高于目标价笔数": n_up,
            "上偏超1个最小价位笔数": n_over1,
            "最大上偏%": round(max_dev_pct, 6), "最大上偏价位数": round(max_dev_ticks, 4),
            "样例": detail,
        }
        print(f"{scheme:<3}{suf:<9}{len(trades):>5}{n_lab:>12}{n_up:>12}"
              f"{max_dev_pct:>11.4f}{max_dev_ticks:>17.3f}")

print()
print("=" * 108)
print("【B2】汇总")
print("=" * 108)
for scheme in ("S", "P"):
    ok = [v for v in R["_标签"][scheme].values() if "笔数" in v]
    if not ok:
        continue
    n = sum(v["笔数"] for v in ok)
    lab = sum(v["标签trailing"] for v in ok)
    up = sum(v["止损高于目标价笔数"] for v in ok)
    ov = sum(v["上偏超1个最小价位笔数"] for v in ok)
    mx = max(v["最大上偏%"] for v in ok)
    mt = max(v["最大上偏价位数"] for v in ok)
    print(f"  {scheme} 组：{n} 笔，标 trailing_stop_loss {lab} 笔（{lab/n*100:.1f}%），"
          f"止损高于目标价 {up} 笔；其中上偏超过 1 个最小价位 {ov} 笔；"
          f"最大上偏 {mx:.4f}%（{mt:.2f} 个最小价位）")

(OUT / "06_成交归因.json").write_text(
    json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\n写出 {OUT/'06_成交归因.json'}")

# ----------------------------------------------------------------------------
# 【C】最小下单额拒单：直接数回测日志里的原文
# 注意：一次拒单 = 一次下单额计算被挡回；同一信号可能在连续多根 K 线上重复尝试，
#       所以「拒单次数」≥「被挡掉的信号数」，两者不可混用。
# ----------------------------------------------------------------------------
LOGS = ROOT / "evidence/FT-EXIT-01/logs"
KEY = "超出风险预算硬性拒单"
print()
print("=" * 108)
print("【C】最小下单额拒单（日志原文逐条计数）")
print("=" * 108)
print(f"{'方案':<3}{'组':<10}{'拒单次数':>9}{'涉及币种':>28}{'最小下单额范围':>20}")
C = {}
for scheme in ("S", "P"):
    C[scheme] = {}
    for suf in SUF:
        f = LOGS / f"{SCHEMES[scheme]}_{suf}_backtest.log"
        if not f.exists():
            continue
        pairs, mins = [], []
        n = 0
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if KEY not in line:
                continue
            n += 1
            m = re.search(r"\[([^\]]+)\] raw_stake=([\d.]+) < min_stake=([\d.]+)", line)
            if m:
                pairs.append(m.group(1))
                mins.append(float(m.group(3)))
        C[scheme][suf] = {
            "拒单次数": n,
            "涉及币种": sorted(set(pairs)),
            "最小下单额最小": min(mins) if mins else None,
            "最小下单额最大": max(mins) if mins else None,
        }
        ps = ",".join(sorted(set(p.split("/")[0] for p in pairs))) or "—"
        print(f"{scheme:<3}{suf:<10}{n:>9}{ps[:28]:>28}"
              f"{(f'{min(mins):.2f}~{max(mins):.2f}' if mins else '—'):>20}")
print()
for scheme in ("S", "P"):
    ok = [v for v in C[scheme].values() if "拒单次数" in v]
    tot = sum(v["拒单次数"] for v in ok)
    ps = sorted({p for v in ok for p in v["涉及币种"]})
    print(f"  {scheme} 组：拒单 {tot} 次，涉及 {len(ps)} 个币种"
          + (f"（{', '.join(p.split('/')[0] for p in ps)}）" if ps else ""))
R["_拒单日志"] = C
(OUT / "06_成交归因.json").write_text(
    json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
