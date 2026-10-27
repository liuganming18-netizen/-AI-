# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜03 真实案例（潜在成功 / 假突破 / 未触发）。

选例规则**先写死再跑**，不许事后挑好看的：

  · 潜在成功 = 研究区间内按确认时刻排序，**第一个** R ≥ 2 且由跟踪止损退出的成交；
  · 假突破   = 按确认时刻排序，**第一个** R ≤ −0.99、初始止损退出、且持仓 ≤ 24 小时的成交；
  · 未触发   = 按原策略候选时刻排序，**第一个**「覆盖该时刻的甲组事件终局不是确认」的候选。

第三条上一版写错过：一开始只按「三组都没覆盖」取，结果抓到的是**另一段时间**的事件，
而且那条事件其实已经确认（CONFIRMED），把「已确认但没成交」说成了「未触发」。
现在改成按「箱体区间是否覆盖候选时刻」匹配，并要求终局 ∈ {NO_RESISTANCE, INVALIDATED, EXPIRED}。

每个案例输出**逐时点可见信息**：在那一根 1h 收盘时，规则能看到的
S_loc / 是否跌破 S_loc / 是否收回 / 主动买占比与相对基准 / 压制位 R / 是否收上 R。
不补未来数据，不写「事后看这里该涨」。

输出：out/03_真实案例.json + cases/*.md
"""
from __future__ import annotations

import calendar
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parents[1] / "rules"))
sys.path.insert(0, str(ROOT / "框架" / "backend"))

import accum_early_rules as R                      # noqa: E402
import research_15m as RM                          # noqa: E402

HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = ROOT / "数据" / "市场数据" / "parquet"
OUT = HERE.parents[1] / "out"
CASES = HERE.parents[1] / "cases"
MS_H = 3_600_000
DAY = 86_400_000

NOT_CONFIRMED = {"NO_RESISTANCE", "INVALIDATED", "EXPIRED"}


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def to_ms(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000


def load(tf, sym, a, b):
    m = RM.Merged15m(str(HOT), str(COLD), timeframe=tf)
    return m.load(sym, a, b)[0]


def params():
    return R.resolve_params({"slippage_bps": 3.0, "risk_config": "research_r_only",
                             "participation_cap": 0.001})


def rerun(symbol, p, w0, w1):
    b4 = load("4h", symbol, w0 - 400 * DAY, w1 + 40 * DAY)
    b1 = load("1h", symbol, w0 - 30 * DAY, w1 + 40 * DAY)
    pre4 = R.precompute_4h(b4, p)
    boxes = R.list_box_candidates(b4, p, (w0, w1), pre4)
    out = {sch: R.run_scheme(symbol, boxes, b1, sch, p, R.precompute_1h(b1, p))
           for sch in ("4a", "4b", "4c")}
    return b4, b1, boxes, out


def window_of(bars_1h, a_ms, b_ms):
    """返回 [i0, i1)：decided 落在 [a_ms, b_ms] 的 1h 下标区间。"""
    idx = [i for i, b in enumerate(bars_1h)
           if a_ms <= int(b["open_time"]) + MS_H <= b_ms]
    return (idx[0], idx[-1] + 1) if idx else (0, 0)


def timeline(bars_1h, box, p, pre1, a_ms, b_ms, r_loc=None, sweep_ms=None,
             confirm_ms=None):
    """逐根 1h 摊开「当时能看到的」：支撑、扫低条件、承接、压制位、收上与否。"""
    i0, i1 = window_of(bars_1h, a_ms, b_ms)
    rows = []
    for i in range(i0, i1):
        b = bars_1h[i]
        decided = int(b["open_time"]) + MS_H
        c = b["close"]
        s = R.local_support(bars_1h, i, p)
        ab = R.absorption(bars_1h, i, p)
        sw = R.detect_sweep(bars_1h, i, box, p, pre1) if s is not None else None
        row = {
            "decided_utc": iso(decided),
            "open": b["open"], "high": b["high"], "low": b["low"], "close": c,
            "tbr": ab.get("tbr"), "tbr_ratio": ab.get("tbr_ratio"),
            "承接过门槛": bool(ab.get("pass")),
            "S_loc": s,
            "跌破S": (c is not None and s is not None and b["low"] < s),
            "满足扫低收回": sw is not None,
            "R_loc": r_loc if (sweep_ms and decided >= sweep_ms) else None,
            "收上R": (c is not None and r_loc is not None
                      and decided >= (sweep_ms or 0) and c > r_loc),
            "事件": "",
        }
        if sweep_ms and decided == sweep_ms:
            row["事件"] = "扫低收回成立"
        if confirm_ms and decided == confirm_ms:
            row["事件"] = "收上压制位 → 确认"
        rows.append(row)
    return rows


TABLE_HEAD = ("| 1h 收盘时刻 | 开 | 高 | 低 | 收 | 主动买占比 | 相对基准 | 承接 | "
              "局部支撑 | 跌破 | 扫低收回 | 压制位 | 收上R | 事件 |")
TABLE_SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def table(rows):
    out = [TABLE_HEAD, TABLE_SEP]
    for r in rows:
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["decided_utc"], r["open"], r["high"], r["low"], r["close"],
            r["tbr"], r["tbr_ratio"],
            "是" if r["承接过门槛"] else "否",
            r["S_loc"], "是" if r["跌破S"] else "否",
            "是" if r["满足扫低收回"] else "否",
            r["R_loc"] or "", "是" if r["收上R"] else "否", r["事件"] or ""))
    return out


def main() -> int:
    p = params()
    fp = OUT / "01_共同观察范围_research_final.json"
    if not fp.exists():
        fp = OUT / "01_共同观察范围_research.json"
    rep = json.loads(fp.read_text(encoding="utf-8"))
    w0, w1 = rep["window"]["from_ms"], rep["window"]["to_ms"]

    trades = []
    for r in rep["per_symbol"]:
        for sch in ("4a", "4b", "4c"):
            for t in (((r.get("schemes") or {}).get(sch) or {}).get("trades") or []):
                if t.get("status") == "filled":
                    trades.append(t)
    trades.sort(key=lambda t: t.get("confirm_ms") or 0)

    win = next((t for t in trades
                if t["r_multiple"] >= 2.0 and "trailing" in (t["exit_reason"] or "")), None)
    fake = next((t for t in trades
                 if t["r_multiple"] <= -0.99
                 and t["exit_reason"] == "initial_stop_loss"
                 and (t.get("held_hours") or 999) <= 24), None)

    # 未触发：原策略候选 + 覆盖该时刻的甲组事件终局不是确认
    cmp = json.loads((OUT / "04_三组对照.json").read_text(encoding="utf-8"))
    cands = []
    for tag in ("research_final", "research"):
        if tag in cmp.get("windows", {}):
            cands = cmp["windows"][tag]["baseline"].get("没覆盖的候选明细") or []
            break
    cands = sorted(cands, key=lambda x: x["candidate_utc"])

    rep_out = {"probe": "03_真实案例", "generated_at_utc": iso(time.time() * 1000),
               "选例规则": {
                   "潜在成功": "按确认时刻排序，第一个 R≥2 且由跟踪止损退出",
                   "假突破": "按确认时刻排序，第一个 R≤−0.99、初始止损、持仓≤24h",
                   "未触发": ("按原策略候选时刻排序，第一个「覆盖该时刻的甲组事件终局"
                              " ∈ {NO_RESISTANCE, INVALIDATED, EXPIRED}」的候选"),
                   "为什么这样选": "先定规则再跑，避免挑好看的；三个案例都不保证「典型」",
               }, "cases": []}
    CASES.mkdir(parents=True, exist_ok=True)

    # ── 案例 1 / 2：成交的两类 ──
    for name, t, why in (("潜在成功", win, "跟踪止损退出且 R≥2"),
                         ("假突破", fake, "初始止损、24 小时内出局")):
        if not t:
            rep_out["cases"].append({"类型": name, "skip": "研究区间内没有满足条件的成交"})
            continue
        sym, sch = t["symbol"], t["scheme"]
        _, b1, _, evs = rerun(sym, p, w0, w1)
        ev = next((e for e in evs[sch]
                   if (e.get("confirm") or {}).get("decided_ms") == t["confirm_ms"]), None)
        if ev is None:
            rep_out["cases"].append({"类型": name, "symbol": sym, "scheme": sch,
                                     "skip": "重跑未能复现该事件"})
            continue
        sw_ms = (ev.get("sweep") or {}).get("decided_ms")
        cf_ms = ev["confirm"]["decided_ms"]
        r_loc = (ev.get("resistance") or {}).get("R")
        rows = timeline(b1, ev["box"], p, R.precompute_1h(b1, p),
                        (sw_ms or cf_ms) - 12 * MS_H, cf_ms + 8 * MS_H,
                        r_loc=r_loc, sweep_ms=sw_ms, confirm_ms=cf_ms)
        rec = {
            "类型": name, "为什么算这一类": why,
            "symbol": sym, "scheme": sch, "方案名": R.SCHEMES[sch]["label"],
            "箱体": {"H": t["H"], "L": t["L"], "A": t["A"],
                     "created_utc": iso(t["box_created_ms"]),
                     "宽度_ATR": round((t["H"] - t["L"]) / max(1e-12, t["A"]), 2)},
            "扫低": {"S_loc": (ev.get("sweep") or {}).get("S_loc"),
                     "时刻": iso(sw_ms),
                     "超破_ATR": (ev.get("sweep") or {}).get("overshoot_atr"),
                     "收回_ATR": (ev.get("sweep") or {}).get("recovery_atr")},
            "压制位": {"R_loc": r_loc,
                       "取得方式": (ev.get("resistance") or {}).get("method"),
                       "合格性": (ev.get("resistance") or {}).get("reason_q"),
                       "比箱顶低_ATR": (round((t["H"] - r_loc) / max(1e-12, t["A"]), 2)
                                    if r_loc else None)},
            "承接": {"扫低根相对基准": (ev.get("absorption") or {}).get("tbr_ratio"),
                     "门槛": p["absorption_taker_mult"]},
            "确认": {"时刻": iso(cf_ms), "阶段": ev["confirm"].get("stage"),
                     "收盘": ev["confirm"].get("close")},
            "成交": {"最早可成交": iso(t.get("earliest_ms")),
                     "实际成交根": iso(t.get("entry_bar_open_ms")),
                     "成交价": t.get("entry_px"), "初始止损": t.get("stop0")},
            "退出": {"时刻": iso(t.get("exit_ms")), "原因": t.get("exit_reason"),
                     "价格": t.get("exit_px"), "持仓小时": t.get("held_hours"),
                     "跟踪止损上调次数": t.get("trail_updates")},
            "结果": {"净收益": t.get("net_ret"), "R倍数": t.get("r_multiple")},
            "逐时点": rows,
        }
        rep_out["cases"].append(rec)

        L = [f"- 标的：`{sym}`　方案：{R.SCHEMES[sch]['label']}（{sch}）",
             f"- 为什么算这一类：{why}", "",
             "## 当时能看到什么", "",
             f"- **{iso(t['box_created_ms'])}** 4h 箱体成立：下沿 L={t['L']:.8g}、"
             f"上沿 H={t['H']:.8g}、ATR A={t['A']:.8g}，宽 "
             f"{(t['H'] - t['L']) / max(1e-12, t['A']):.2f}A。"
             "此刻只知道这是一个横盘区，**不知道**后面会怎么走。"]
        sw = ev.get("sweep") or {}
        if sw:
            L.append(f"- **{iso(sw_ms)}** 这根 1h 跌破前 {p['local_support_bars']} 根"
                     f"最低价 S={sw['S_loc']:.8g}（最低 {sw['low']:.8g}，"
                     f"超破 {sw['overshoot_atr']:.2f}A ≤ {p['sweep_max_overshoot_atr']}A），"
                     f"收盘回到 S+{sw['recovery_atr']:.2f}A（要求 ≥"
                     f"{p['sweep_min_recovery_atr']}A）⇒ 判为扫低收回。"
                     f"这一根主动买占比是前 {p['absorption_baseline_bars']} 根中位数的 "
                     f"{(ev.get('absorption') or {}).get('tbr_ratio')} 倍"
                     f"（门槛 {p['absorption_taker_mult']}）。")
        if r_loc:
            L.append(f"- 同一根定出局部压制位 R={r_loc:.8g}"
                     f"（方式 `{(ev.get('resistance') or {}).get('method')}`，"
                     f"合格性 `{(ev.get('resistance') or {}).get('reason_q')}`）。"
                     f"R 比箱顶低 {(t['H'] - r_loc) / max(1e-12, t['A']):.2f}A ——"
                     "「提前」的全部来源就在这里：不等突破箱顶，收上 R 就算转强。")
        L.append(f"- **{iso(cf_ms)}** 1h 收盘 {ev['confirm'].get('close'):.8g} "
                 f"首次满足确认条件（`{ev['confirm'].get('stage')}`）。")
        L.append(f"- 最早可成交 = 确认后第一根 15m 开盘 **{iso(t.get('earliest_ms'))}**"
                 f"（不回填确认根）；实际成交根 {iso(t.get('entry_bar_open_ms'))}，"
                 f"成交价 {t.get('entry_px'):.8g}，初始止损 {t.get('stop0'):.8g}。")
        L += ["", "## 逐时点（每根 1h 收盘时规则能看到的）", ""] + table(rows)
        L += ["", "## 结果", "",
              f"- 退出 {iso(t.get('exit_ms'))}　原因 `{t.get('exit_reason')}`　"
              f"价格 {t.get('exit_px'):.8g}　持仓 {t.get('held_hours')} 小时　"
              f"跟踪止损上调 {t.get('trail_updates')} 次",
              f"- 净收益 {t.get('net_ret'):.4%}　R 倍数 **{t.get('r_multiple'):.3f}**"]
        (CASES / f"{name}_{sym}_{sch}.md").write_text(
            f"# 案例：{name}｜{sym}｜{R.SCHEMES[sch]['label']}\n\n"
            + "\n".join(L) + "\n", encoding="utf-8")

    # ── 案例 3：未触发 ──
    target = None
    for c in cands:
        ms = to_ms(c["candidate_utc"])
        _, b1, _, evs = rerun(c["symbol"], p, ms - 30 * DAY, ms + 5 * DAY)
        hit = next((e for e in evs["4a"]
                    if e["box"]["created_ms"] <= ms <= (e.get("end_ms") or 0)), None)
        if hit is not None and hit["state"] in NOT_CONFIRMED:
            target = (c, ms, b1, hit)
            break
    if target is None:
        rep_out["cases"].append({"类型": "未触发",
                                 "skip": "候选明细里没有找到终局为非确认的甲组事件"})
    else:
        c, ms, b1, hit = target
        sym = c["symbol"]
        sw_ms = (hit.get("sweep") or {}).get("decided_ms")
        r_loc = (hit.get("resistance") or {}).get("R")
        rows = timeline(b1, hit["box"], p, R.precompute_1h(b1, p),
                        ms - 48 * MS_H, ms + 6 * MS_H,
                        r_loc=r_loc, sweep_ms=sw_ms)
        rec = {"类型": "未触发", "symbol": sym,
               "原策略候选时刻": c["candidate_utc"],
               "甲组事件": {"终局": hit["state"], "原因": hit.get("why"),
                            "箱体建档": iso(hit["box"]["created_ms"]),
                            "箱体结束": iso(hit.get("end_ms")),
                            "S_loc": (hit.get("sweep") or {}).get("S_loc"),
                            "扫低时刻": iso(sw_ms),
                            "R_loc": r_loc,
                            "R合格性": (hit.get("resistance") or {}).get("reason_q")},
               "逐时点": rows}
        rep_out["cases"].append(rec)

        L = [f"- 标的：`{sym}`　原策略在 **{c['candidate_utc']}** 出过候选。", "",
             "## 甲组为什么不成交", "",
             f"- 覆盖这个时刻的甲组箱体建档于 {iso(hit['box']['created_ms'])}，"
             f"事件在 {iso(hit.get('end_ms'))} 结束，终局 `{hit['state']}`，"
             f"原因 `{hit.get('why')}`。"]
        if hit["state"] == "NO_RESISTANCE":
            L.append(f"- 它**确实扫低收回过**（S={(hit.get('sweep') or {}).get('S_loc')}，"
                     f"时刻 {iso(sw_ms)}），但当时算出的压制位 R={r_loc} 不合格"
                     f"（`{(hit.get('resistance') or {}).get('reason_q')}`）——"
                     "R 必须落在 L+0.5A 与 H−0.5A 之间，否则「提前」就变成了「追高」。")
        elif hit["state"] == "INVALIDATED":
            L.append(f"- 它在 `{hit.get('why')}` 处被否掉，没有走到确认。")
        else:
            L.append(f"- 它在观察期内（{p['observe_deadline_hours']} 小时）没等到"
                     f"「扫低收回 + 收上压制位」，按 `{hit.get('why')}` 过期。")
        L.append("- 也就是说：**不是没看到，是看到了但没过门槛**。"
                 "原策略这条候选对甲组不构成一次交易。")
        L += ["", "## 逐时点（每根 1h 收盘时规则能看到的）", ""] + table(rows)
        (CASES / f"未触发_{sym}.md").write_text(
            f"# 案例：未触发｜{sym}\n\n" + "\n".join(L) + "\n", encoding="utf-8")

    (OUT / "03_真实案例.json").write_text(
        json.dumps(rep_out, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(json.dumps([{k: v for k, v in
                       {"类型": x.get("类型"), "symbol": x.get("symbol"),
                        "scheme": x.get("scheme"), "结果": x.get("结果"),
                        "甲组事件": x.get("甲组事件"), "skip": x.get("skip"),
                        "逐时点行数": len(x.get("逐时点") or [])}.items()
                       if v is not None}
                      for x in rep_out["cases"]], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
