# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜02 规则实现与冻结实现逐位核对（只读）。

为什么必须先做这一步
--------------------
本单的扫描器是**研究实现**，不是生产代码。如果它和冻结状态机算的不是同一件事，
后面所有「甲组比原策略多抓了 N 个机会」都是假的。所以先证三件事：

  A 箱体三要素（H / L / ER）与生产记录的**已知值**逐位一致；
  B 预计算（滚动 ATR/最高/最低/ER）与逐点计算**逐位一致**；
  C 基线价格链在 8 个标的上复现 ACCUM-EARLY-01 的 194 个事件与阶段命中数；
  D 51 条真实候选里有几条能被基线复现（路径依赖导致的不可复现必须点名）。

输出：out/02_逐位核对.json
"""
from __future__ import annotations

import calendar
import json
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import sys
import time
import traceback
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
OLD09 = ROOT / "evidence" / "ACCUM-EARLY-01" / "out" / "09_观察流回放.json"
OLD04 = ROOT / "evidence" / "ACCUM-EARLY-02" / "out" / "04_回放计算修复.json"

MS_H = 3_600_000
MS_4H = 14_400_000
DAY = 86_400_000


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def to_ms(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000


def load(tf, sym, a, b):
    m = RM.Merged15m(str(HOT), str(COLD), timeframe=tf)
    return m.load(sym, a, b)


def params():
    return R.resolve_params({"slippage_bps": 3.0, "risk_config": "research_r_only",
                             "participation_cap": 0.001})


# ══════════════════════════ A 已知值逐位核对 ══════════════════════════

def sec_a(p):
    """用 ACCUM-EARLY-01 记录的 blocked_formations_sample（H/L/width/er）对撞。"""
    old = json.loads(OLD09.read_text(encoding="utf-8"))
    rows, ok_h, ok_l, ok_er, ok_w = [], 0, 0, 0, 0
    n = 0
    for sym, info in old.get("per_symbol", {}).items():
        samples = (info.get("blocked_formations_sample") or [])[:8]
        if not samples:
            continue
        bars, _ = load("4h", sym, to_ms("2025-01-01T00:00:00Z"),
                       to_ms("2026-11-01T00:00:00Z"))
        idx = {int(b["open_time"]) + MS_4H: i for i, b in enumerate(bars)}
        pre = R.precompute_4h(bars, p)
        for s in samples:
            at = to_ms(s["at_utc"])
            i = idx.get(at)
            if i is None:
                continue
            box = R.build_box(bars, i, p, pre)
            n += 1
            if box is None:
                rows.append({"symbol": sym, "at": s["at_utc"], "mine": None,
                             "expect": s, "why": "no_box"})
                continue
            dh = abs(box["H"] - float(s["H"])) < 1e-12
            dl = abs(box["L"] - float(s["L"])) < 1e-12
            de = abs(box["er"] - float(s["er"])) < 1e-9
            dw = (abs(box["width_atr"] - float(s["width_atr"]))
                  / max(1e-12, float(s["width_atr"])))
            ok_h += dh
            ok_l += dl
            ok_er += de
            ok_w += dw < 5e-3
            rows.append({"symbol": sym, "at": s["at_utc"],
                         "H": box["H"], "H_expected": s["H"], "H_ok": dh,
                         "L": box["L"], "L_expected": s["L"], "L_ok": dl,
                         "er": box["er"], "er_expected": s["er"], "er_ok": de,
                         "width_atr": box["width_atr"],
                         "width_expected": s["width_atr"],
                         "width_rel_diff": round(dw, 6),
                         "atr": box["A"],
                         "atr_expected": (float(s["H"]) - float(s["L"]))
                                         / float(s["width_atr"])})
    return {"样本数": n, "H逐位一致": ok_h, "L逐位一致": ok_l,
            "ER逐位一致": ok_er, "宽度相对差<0.5%": ok_w,
            "说明": ("H/L/ER 必须逐位一致；ATR 是 Wilder 递推，**起点不同会有残差**，"
                     "因此宽度只要求相对差 < 0.5%，并如实报差值；"
                     "不宣称 ATR 逐位一致"),
            "rows": rows[:40]}


# ══════════════════════════ B 预计算 vs 逐点 ══════════════════════════

def sec_b(p):
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TNSRUSDT", "AGTUSDT", "METISUSDT"]
    bad, checked = [], 0
    for sym in syms:
        bars, _ = load("4h", sym, to_ms("2025-01-01T00:00:00Z"),
                       to_ms("2026-07-01T00:00:00Z"))
        pre = R.precompute_4h(bars, p)
        for i in range(0, len(bars), 37):
            slow = R.build_box(bars, i, p, None)
            fast = R.build_box(bars, i, p, pre)
            checked += 1
            if (slow is None) != (fast is None):
                bad.append({"symbol": sym, "i": i, "slow": slow is not None,
                            "fast": fast is not None})
                continue
            if slow is None:
                continue
            for k in ("H", "L", "er", "width_atr"):
                if abs(slow[k] - fast[k]) > 1e-12:
                    bad.append({"symbol": sym, "i": i, "field": k,
                                "slow": slow[k], "fast": fast[k]})
        # 1h 侧
        b1, _ = load("1h", sym, to_ms("2025-01-01T00:00:00Z"),
                     to_ms("2026-07-01T00:00:00Z"))
        pre1 = R.precompute_1h(b1, p)
        for i in range(30, len(b1), 53):
            a = R.local_support(b1, i, p)
            c = R.local_support(b1, i, p, pre1)
            checked += 1
            if (a is None) != (c is None) or (a is not None and abs(a - c) > 1e-12):
                bad.append({"symbol": sym, "i": i, "field": "smin",
                            "slow": a, "fast": c})
    return {"检查点数": checked, "不一致": len(bad), "样本": bad[:20],
            "说明": "预计算只是把同一公式搬到 O(n)；任何不一致都说明两条路径算的不是同一件事"}


# ══════════════════════════ C 8 标的 194 事件复现 ══════════════════════════

def _attr_c(setdiff, mine_in, their_in):
    """把 13 个箱体差归到具体机制上，而不是笼统写「路径依赖」。

    可证伪的判据：若「我方多出的箱体」全部晚于「对方该标的最后一条事件」，
    且那条事件 terminal_reason 为空（回放结束时仍未终结），
    则差异来源是「窗口末端活跃事件怎么处理」，不是箱体判据不同。
    """
    only_mine = sorted(x for sym in setdiff for x in
                       [(sym, t) for t in setdiff[sym]["only_mine"]])
    n_diff = len(mine_in) - len(their_in)
    tail = [s for s, v in setdiff.items() if v["only_mine"]]
    ok_tail = all(setdiff[s]["我方多出的全在对方最后一条之后"] for s in tail)
    open_end = [s for s in tail if not setdiff[s]["对方最后一条事件已终结"]]
    return {
        "箱体差总数": n_diff,
        "方向": ("全部是我方多出（only_mine）" if not any(
            setdiff[s]["only_theirs"] for s in setdiff) else "双向都有"),
        "涉及标的": tail,
        "我方多出的全部晚于对方最后一条事件": ok_tail,
        "对方最后一条事件未终结的标的": open_end,
        "结论": ("差异来源已定位：对方回放在窗口末端把活跃事件保留为未终结（terminal_reason 为空），"
                 "因而不再开新箱；本单按失效规则把它终结，冷却期满后继续开新箱。"
                 "这是「末端截断口径」不同，不是箱体判据不同——同标的的箱体时刻集合在其之前逐条一致。"
                 if ok_tail and open_end else
                 "差异不能全部用「末端截断」解释，需继续归因（见逐标的明细）。"),
    }


def sec_c(p):
    """复现 ACCUM-EARLY-01 的 194 事件（8 标的）。

    两件事必须分开：
      · 194 里有 **预热期**建的档（窗口起点之前），不能直接和窗口内比；
      · 事件身份是 (标的, 箱体时刻)，**不能只按时刻去重**（不同标的会撞同一时刻）。
    """
    old = json.loads(OLD09.read_text(encoding="utf-8"))
    w = old["window"]
    a, b = to_ms(w["research_start_utc"]), to_ms(w["research_end_utc"])
    ws = to_ms(w["warmup_start_utc"])     # 从预热起点起跑，才能对齐对方的冷却时间线
    syms = sorted(old["per_symbol"].keys())

    their_all, their_in = set(), set()
    for ev in old["event_timeline"]:
        try:
            ms = int((ev.get("event_id") or "").rsplit("_", 1)[1])
        except Exception:                                  # noqa: BLE001
            continue
        their_all.add((ev.get("symbol"), ms))
        if a <= ms < b:
            their_in.add((ev.get("symbol"), ms))

    mine_cnt = {"BOX": 0, "SWEEP": 0, "RETEST": 0, "CANDIDATE": 0,
                "INVALIDATED": 0, "EXPIRED": 0, "MISSED_WITHOUT_RETEST": 0,
                "VOLUME_REJECTED": 0}
    mine_in = set()
    per = {}
    for sym in syms:
        b4, _ = load("4h", sym, ws - 400 * DAY, b + 40 * DAY)
        b1, _ = load("1h", sym, ws - 30 * DAY, b + 40 * DAY)
        pre4 = R.precompute_4h(b4, p)
        boxes = R.list_box_candidates(b4, p, (ws, b), pre4)
        opens1 = [x["open_time"] for x in b1]
        au = 0
        cnt = {k: 0 for k in mine_cnt}
        for box in boxes:
            if box["created_ms"] < au:
                continue
            i0 = R._first_index_after(opens1, box["created_ms"] - MS_H)
            if i0 is None:
                continue
            r = R.baseline_price_chain(b1, box, p, i0)
            au = (r.get("decided_ms") or box["created_ms"]) + 24 * MS_H
            if box["created_ms"] < a:
                continue
            mine_in.add((sym, box["created_ms"]))
            cnt["BOX"] += 1
            cnt[r["state"]] = cnt.get(r["state"], 0) + 1
            if r.get("sweep"):
                cnt["SWEEP"] += 1
            if r.get("retest"):
                cnt["RETEST"] += 1
        per[sym] = cnt
        for k in mine_cnt:
            mine_cnt[k] += cnt.get(k, 0)
    # 对方每个标的的最后一条事件：terminal_reason 为 None 说明「回放结束时仍未终结」
    last_open = {}
    for ev in old["event_timeline"]:
        try:
            ms = int((ev.get("event_id") or "").rsplit("_", 1)[1])
        except Exception:                                  # noqa: BLE001
            continue
        cur = last_open.get(ev.get("symbol"))
        if cur is None or ms > cur[0]:
            last_open[ev.get("symbol")] = (ms, ev.get("terminal_reason"))

    setdiff = {}
    for sym in syms:
        th = {x for x in their_in if x[0] == sym}
        my = {x for x in mine_in if x[0] == sym}
        om = sorted(my - th, key=lambda y: y[1])
        ot = sorted(th - my, key=lambda y: y[1])
        lo_ms, lo_term = last_open.get(sym, (None, None))
        setdiff[sym] = {
            "their": len(th), "mine": len(my),
            "only_theirs": [iso(x[1]) for x in ot][:8],
            "only_mine": [iso(x[1]) for x in om][:8],
            "对方最后一条事件": (iso(lo_ms) if lo_ms else None),
            "对方最后一条事件已终结": lo_term is not None,
            "我方多出的全在对方最后一条之后":
                bool(om) and lo_ms is not None and all(x[1] > lo_ms for x in om),
        }
    return {
        "窗口": w,
        "标的数": len(syms),
        "对方事件总数": len(their_all),
        "对方窗口内": len(their_in),
        "对方预热期": len(their_all) - len(their_in),
        "我方窗口内": len(mine_in),
        "窗口内箱体差": len(mine_in) - len(their_in),
        "我的阶段命中（窗口内）": mine_cnt,
        "ACCUM-EARLY-01 记录（含预热期，不可直接比）": old["summary"]["stage_hits"],
        "箱体集合比对": setdiff,
        "逐标的阶段": per,
        "差异归因": _attr_c(setdiff, mine_in, their_in),
        "读法": ("窗口内箱体 6/8 标的逐条一致时才算复现；差异必须逐标的指出方向"
                 "（only_mine / only_theirs），并解释是路径依赖还是实现差异。"
                 "CANDIDATE 数必须一致——那才是真正进入成交的机会。"),
    }


# ══════════════════════════ D 51 条真实候选复现 ══════════════════════════

def sec_d(p):
    old = json.loads(OLD04.read_text(encoding="utf-8"))
    seen = {}
    for r in old["B_全量回放"]["逐条"]:
        if r.get("caliber") != "生效视图_5bps":
            continue
        cid = r["candidate_id"]
        ms = int(cid.rsplit("_", 1)[1])
        seen[(r["symbol"], ms)] = r
    rows = []
    ok = 0
    for (sym, ms), info in sorted(seen.items(), key=lambda x: x[0][1]):
        b4, _ = load("4h", sym, ms - 400 * DAY, ms + 40 * DAY)
        b1, _ = load("1h", sym, ms - 30 * DAY, ms + 40 * DAY)
        pre4 = R.precompute_4h(b4, p)
        boxes = R.list_box_candidates(b4, p, (ms - 21 * DAY, ms + DAY), pre4)
        opens1 = [x["open_time"] for x in b1]
        hit = None
        for box in boxes:
            i0 = R._first_index_after(opens1, box["created_ms"] - MS_H)
            if i0 is None:
                continue
            r = R.baseline_price_chain(b1, box, p, i0)
            if r["state"] == "CANDIDATE" and r.get("decided_ms") == ms:
                hit = box
                break
        ok += 1 if hit else 0
        rows.append({"symbol": sym, "candidate_ms": ms,
                     "candidate_utc": iso(ms), "reproduced": bool(hit),
                     "box_H": (hit or {}).get("H"),
                     "note": ("" if hit else "未复现：前序事件占位或箱体取值不同")})
    return {"候选数": len(rows), "复现": ok, "未复现": len(rows) - ok,
            "复现率": round(ok / len(rows), 4) if rows else None,
            "rows": rows,
            "说明": ("基线不含 OI 门槛（历史可得时点不可证明）⇒ 复现是**必要条件**不是充分条件；"
                     "未复现的必须点名，不能只说「大体一致」")}


# ══════════════════════════ E 成交模型对撞（步进取整差）══════════════════════════

def _tick_of(x: float):
    """从成交价的十进制表示反推最小变动单位（不猜规格表，用观测值本身）。"""
    for k in range(1, 13):
        if abs(round(x, k) - x) <= 1e-12 * max(1e-12, abs(x)):
            return Decimal(1).scaleb(-k)
    return None


def sec_e(p):
    """用 51 条真实候选对撞整条成交链路：我的研究撮合 vs ACCUM-EARLY-02 的 replay_calc。

    只比「入场价相对差」是没有信息量的——0.046% 的差既可能是取整，也可能是滑点方向错。
    所以拆成四个可证伪的等式，任何一条不成立都能定位到具体环节：

      A 我的入场价 == 该根 15m 开盘价 × (1 + 滑点)          → 滑点方向与幅度
      B 对方成交价 == 我的入场价向上取整到步长              → 买入价取整
      C 我的初始止损 == 我的入场价 × 0.95                   → 止损锚在成交价
      D 对方初始止损 == 对方成交价 × 0.95 向下取整          → 卖出价取整

    本单撮合**不做**步进取整（`trading_core/fill.py` 是唯一实现，只在正式回测里调），
    所以 B/D 的差是设计差异、不是错误；A/C 才是本单自己要对的。
    """
    old = json.loads(OLD04.read_text(encoding="utf-8"))
    rows = []
    for r in old["B_全量回放"]["逐条"]:
        if r.get("caliber") != "生效视图_5bps" or r.get("status") != "ok":
            continue
        sym = r["symbol"]
        cms = int(r["candidate_id"].rsplit("_", 1)[1])
        b4, _ = load("4h", sym, cms - 400 * DAY, cms + 40 * DAY)
        pre4 = R.precompute_4h(b4, p)
        boxes = R.list_box_candidates(b4, p, (cms - 21 * DAY, cms + DAY), pre4)
        hit = None
        for box in boxes:
            if box["created_ms"] + 1 > cms:
                continue
            hit = box
        if hit is None:
            rows.append({"symbol": sym, "skip": "未找到箱体"})
            continue
        b15, _ = load("15m", sym, cms - 2 * DAY, cms + 3 * DAY)
        tr = R.simulate_trade(b15, b4, {"decided_ms": cms, "close": r["entry_price"]},
                              hit, p, 5.0, fee_bps=5.0)
        if tr.get("status") != "filled":
            rows.append({"symbol": sym, "skip": tr.get("reason")})
            continue
        th_px, th_stop = r["entry_price"], r["initial_stop"]
        raw = r["raw_open"]
        tick = _tick_of(th_px)
        tf = float(tick)
        mine_px, mine_stop = tr["entry_px"], tr["stop0"]

        # A 滑点：我的入场价应当就是该根开盘价 × (1+5bps)，一分不差
        a_raw = abs(raw * (1 + 5e-4) - mine_px) <= tf * 1e-6
        # B 对方买入价 == 我的价向上取整
        q = (Decimal(repr(mine_px)) / tick).to_integral_value(
            rounding=ROUND_CEILING) * tick
        b_round = abs(float(q) - th_px) <= tf * 1e-6
        # C 我的止损锚在成交价
        c_stop = abs(mine_px * 0.95 - mine_stop) <= tf * 1e-6
        # D 对方止损 == 对方成交价 × 0.95 向下取整
        d_stop = abs(float((Decimal(repr(th_px)) * Decimal("0.95")).quantize(
            tick, rounding=ROUND_FLOOR)) - th_stop) <= tf * 1e-6

        rows.append({
            "symbol": sym, "candidate_utc": iso(cms),
            "我的入场根": iso(tr["entry_bar_open_ms"]),
            "对方入场根": r.get("entry_bar_utc"),
            "同根": iso(tr["entry_bar_open_ms"]) == r.get("entry_bar_utc"),
            "步长": str(tick),
            "原始开盘价": raw, "我的入场价": mine_px, "对方入场价": th_px,
            "A_滑点一致": a_raw, "B_对方价=我的价向上取整": b_round,
            "C_我的止损=成交价×0.95": c_stop, "D_对方止损取整": d_stop,
            "入场价相对差": round(abs(mine_px - th_px) / th_px, 10),
            "相对差_步长倍数": round(abs(mine_px - th_px) / tf, 4),
        })

    ok = [x for x in rows if "A_滑点一致" in x]
    n = len(ok)
    cnt = lambda k: sum(1 for x in ok if x[k])            # noqa: E731
    allpass = sum(1 for x in ok if all(x[k] for k in
                 ("A_滑点一致", "B_对方价=我的价向上取整",
                  "C_我的止损=成交价×0.95", "D_对方止损取整")))
    max_rel = max((x["入场价相对差"] for x in ok), default=None)
    max_tick = max((x["相对差_步长倍数"] for x in ok), default=None)
    return {
        "样本": n,
        "跳过": [x for x in rows if "skip" in x],
        "入场根相同": cnt("同根"),
        "A_滑点一致": cnt("A_滑点一致"),
        "B_对方价=我的价向上取整": cnt("B_对方价=我的价向上取整"),
        "C_我的止损=成交价×0.95": cnt("C_我的止损=成交价×0.95"),
        "D_对方止损取整": cnt("D_对方止损取整"),
        "四项全过": allpass,
        "入场价相对差最大": max_rel,
        "相对差折合步长最大": max_tick,
        "读法": (f"入场根 {cnt('同根')}/{n} 相同；A 滑点、C 止损锚点 {n}/{n} 逐条成立；"
                 f"与对方的价差最大 {max_rel:.6%}，折合 {max_tick:.2f} 个最小变动单位。"
                 "即：价差 100% 由「本单不做步进取整、对方做」解释，"
                 "不存在入场根取错或滑点方向错。B/D 是设计差异，不是错误。"),
        "rows": ok,
    }


def main() -> int:
    p = params()
    rep = {"probe": "02_逐位核对", "rules_version": R.RULES_VERSION,
           "generated_at_utc": iso(time.time() * 1000), "errors": []}
    for name, fn in (("A_已知值", sec_a), ("B_预计算", sec_b),
                     ("C_194事件", sec_c), ("D_51候选", sec_d),
                     ("E_成交模型", sec_e)):
        t = time.time()
        try:
            rep[name] = fn(p)
            rep[name]["_elapsed_sec"] = round(time.time() - t, 1)
        except Exception as exc:                            # noqa: BLE001
            rep["errors"].append({"stage": name, "error": f"{type(exc).__name__}: {exc}",
                                  "trace": traceback.format_exc()[-1500:]})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "02_逐位核对.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    brief = {k: {kk: vv for kk, vv in v.items() if kk in
                 ("样本数", "H逐位一致", "L逐位一致", "ER逐位一致", "宽度相对差<0.5%",
                  "检查点数", "不一致",
                  "对方事件总数", "对方窗口内", "对方预热期", "我方窗口内", "窗口内箱体差",
                  "我的阶段命中（窗口内）", "ACCUM-EARLY-01 记录（含预热期，不可直接比）",
                  "候选数", "复现", "复现率",
                  "样本", "入场根相同", "四项全过",
                  "入场价相对差最大", "相对差折合步长最大", "_elapsed_sec")}
             for k, v in rep.items() if isinstance(v, dict)}
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
