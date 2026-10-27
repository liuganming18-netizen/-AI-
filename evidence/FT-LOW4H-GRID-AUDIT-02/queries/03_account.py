# -*- coding: utf-8 -*-
"""03 账户与指标独立重算：净收益、费用、资金费、含未平仓盈亏的 15m 盯市权益回撤。

主口径 MTM : equity(t) = 2000 + Σ已实现(close_ms≤t)
                       + Σ未平仓[(P15(t) − open_rate)×amount − 开仓费 − 资金费(开仓→t)]
对照 A     : 不含资金费分摊的纯价差 MTM
对照 B     : 资金费在开仓时一次性全扣
对照 C     : 已实现曲线（仅对照，不得用于结论）
资金费口径与 FT-STOP-GRID-AUDIT-02 完全一致（已在那轮 2,216 笔上逐笔零误差复现）。
"""
from __future__ import annotations

import json
import zipfile

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
INIT = L.INIT_EQUITY
R_UNIT = INIT * L.RISK_RATE

RES: dict = {"段": {}}
_PX: dict = {}
_FUND: dict = {}
_MARK: dict = {}


def add(sec: str, key: str, val, ok=None, note: str = "") -> None:
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES.setdefault("判据", []).append({"段": sec, "项": key, "通过": bool(ok), "说明": note})


def _sym(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def _series(pair: str, tf: str, col: str, cache: dict):
    if pair in cache:
        return cache[pair]
    p = L.DATA / f"{_sym(pair)}-{tf}.feather"
    if not p.exists():
        return None
    df = pd.read_feather(p, columns=["date", col])
    out = (L.ms_of_series(df["date"]), df[col].to_numpy(dtype=np.float64))
    cache[pair] = out
    return out


def price_15m(pair):
    return _series(pair, "15m-futures", "close", _PX)


def funding_8h(pair):
    return _series(pair, "8h-funding_rate", "funding_rate", _FUND)


def mark_1h(pair):
    return _series(pair, "1h-mark", "close", _MARK)


def _ffill_at(series, t: np.ndarray) -> np.ndarray:
    if series is None:
        return np.full(len(t), np.nan)
    ms, v = series
    idx = np.searchsorted(ms, t, side="right") - 1
    ok = idx >= 0
    out = np.full(len(t), np.nan)
    out[ok] = v[idx[ok]]
    return out


def funding_events(pair: str, open_ms: int, close_ms: int, amount: float):
    f = funding_8h(pair)
    mk = mark_1h(pair)
    if f is None:
        return np.array([], dtype=np.int64), np.array([]), 0.0
    ms, rate = f
    fs = (ms // 1000) * 1000
    first = int(np.searchsorted(fs, open_ms, side="left"))
    last = int(np.searchsorted(fs, close_ms, side="right"))
    if last <= first:
        return np.array([], dtype=np.int64), np.array([]), 0.0
    ev_ms = fs[first:last]
    ev_rate = rate[first:last]
    if mk is None:
        return ev_ms, np.zeros(len(ev_ms)), 0.0
    mms, mv = mk
    mv = pd.Series(mv).ffill().to_numpy()
    k = np.searchsorted(mms, ms[first:last], side="right") - 1
    mkpx = np.where(k >= 0, mv[np.clip(k, 0, None)], np.nan)
    fee = -(ev_rate * mkpx * amount)
    ok = np.isfinite(fee)
    if not ok.all():
        ev_ms, fee = ev_ms[ok], fee[ok]
    return ev_ms, fee, float(fee.sum())


def _merge_intervals(iv):
    if not iv:
        return []
    iv = sorted(iv)
    out = [list(iv[0])]
    for a, b in iv[1:]:
        if a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(int(a), int(b)) for a, b in out]


def mtm_curves(g: pd.DataFrame) -> dict:
    g = g.sort_values("open_ms").reset_index(drop=True)
    trs = g.to_dict("records")
    for t in trs:
        ev_ms, ev_fee, tot = funding_events(t["pair"], int(t["open_ms"]), int(t["close_ms"]),
                                           float(t["amount"]))
        t["_ev_ms"] = ev_ms
        t["_fund_total"] = tot
        t["_cum"] = np.concatenate([[0.0], np.cumsum(ev_fee)]) if len(ev_fee) else np.array([0.0])
        t["_cum_t"] = np.concatenate([[int(t["open_ms"])], ev_ms.astype(np.int64)]) if len(ev_fee) \
            else np.array([int(t["open_ms"])], dtype=np.int64)
        t["_open_fee"] = float(t["amount"]) * float(t["open_rate"]) * float(t["fee_open"])
        t["_px"] = price_15m(t["pair"])
        t["_fund_rep"] = float(t["funding_fees"])

    closed = g.groupby("close_ms", as_index=False)["profit_abs"].sum().sort_values("close_ms")
    c_ms = closed["close_ms"].to_numpy(dtype=np.int64)
    c_val = INIT + np.cumsum(closed["profit_abs"].to_numpy(dtype=np.float64))

    ivs = _merge_intervals([(int(t["open_ms"]), int(t["close_ms"])) for t in trs])
    grids, eq_mtm, eq_a, eq_b = [], [], [], []
    for a, b in ivs:
        if b <= a:
            continue
        grid = np.arange(a, b, L.BAR_15M_MS, dtype=np.int64)
        if len(grid) == 0:
            continue
        idx = np.searchsorted(c_ms, grid, side="right") - 1
        real = np.where(idx >= 0, c_val[np.clip(idx, 0, None)], INIT)
        add_m = np.zeros(len(grid)); add_a = np.zeros(len(grid)); add_b = np.zeros(len(grid))
        for t in trs:
            o, c = int(t["open_ms"]), int(t["close_ms"])
            if c <= a or o >= b:
                continue
            lo, hi = max(o, a), min(c, b)
            m = (grid >= lo) & (grid < hi)
            if not m.any():
                continue
            px = _ffill_at(t["_px"], grid[m])
            spread = (px - float(t["open_rate"])) * float(t["amount"])
            base = spread - t["_open_fee"]
            k = np.searchsorted(t["_cum_t"], grid[m], side="right") - 1
            fund = t["_cum"][np.clip(k, 0, len(t["_cum"]) - 1)]
            add_m[m] += base + fund
            add_a[m] += base
            add_b[m] += base + t["_fund_total"]
        grids.append(grid); eq_mtm.append(real + add_m)
        eq_a.append(real + add_a); eq_b.append(real + add_b)

    if not grids:
        return {"ok": False}
    grid = np.concatenate(grids)
    t_end = int(g["close_ms"].max())
    final_eq = INIT + float(g["profit_abs"].sum())
    grid = np.append(grid, t_end)
    eq_mtm = [np.concatenate(eq_mtm), np.array([final_eq])]
    eq_a = [np.concatenate(eq_a), np.array([final_eq])]
    eq_b = [np.concatenate(eq_b), np.array([final_eq])]

    res = {"ok": True}
    for key, arr in (("MTM", eq_mtm), ("A", eq_a), ("B", eq_b)):
        eq = np.concatenate(arr)
        keep = np.isfinite(eq)
        if key == "MTM":
            res["缺失价点"] = int((~keep).sum())
        gg = grid[keep] if not keep.all() else grid
        eq = eq[keep]
        dd, pct, j, i = L.max_dd(eq)
        res[key] = {"最大回撤USDT": dd, "最大回撤占峰值pct": pct, "最大回撤R": dd / R_UNIT,
                    "峰时刻": int(gg[j]), "谷时刻": int(gg[i]),
                    "期末权益": float(eq[-1]), "最小权益": float(eq.min())}
    if len(c_ms):
        eqc = np.concatenate([[INIT], c_val])
        dd, pct, _, _ = L.max_dd(eqc)
        res["C"] = {"最大回撤USDT": dd, "最大回撤占峰值pct": pct, "最大回撤R": dd / R_UNIT,
                    "期末权益": float(eqc[-1])}
    # 资金费复现误差
    res["资金费复现最大误差"] = float(max((abs(t["_fund_total"] - t["_fund_rep"]) for t in trs),
                                        default=0.0))
    return res


def engine_dd(name: str):
    z = L.UP / f"out/backtest-result-{name}.zip"
    if not z.exists():
        return None, None
    try:
        with zipfile.ZipFile(z) as zf:
            jn = [n for n in zf.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
            d = json.load(zf.open(jn[0]))
            sres = d.get("strategy", {}).get(name, {})
            return float(sres.get("max_drawdown_abs", 0.0)), \
                float(sres.get("max_drawdown_account", 0.0)) * 100
    except Exception:
        return None, None


def slots(g: pd.DataFrame) -> dict:
    """槽位占用：同一时刻先平后开（引擎先查退出再看入场）。"""
    ev = []
    for t in g.itertuples(index=False):
        ev.append((int(t.open_ms), +1))
        ev.append((int(t.close_ms), -1))
    ev.sort(key=lambda x: (x[0], x[1]))          # -1 先处理
    cur = mx = 0
    for _, d in ev:
        cur += d
        mx = max(mx, cur)
    # 并发名义 / 风险峰值（风险按「当时已实现权益」为分母）
    ivs = sorted([(int(t.open_ms), int(t.close_ms), float(t.amount) * float(t.open_rate),
                   float(t.amount) * float(t.open_rate) * float(t.s)) for t in g.itertuples(index=False)])
    closed = g.loc[~g["期末结算"], ["close_ms", "profit_abs"]].groupby("close_ms", as_index=False).sum()
    c_ms = closed["close_ms"].to_numpy(dtype=np.int64)
    c_val = INIT + np.cumsum(closed["profit_abs"].to_numpy(dtype=np.float64))

    def eq_at(ms: int) -> float:
        k = int(np.searchsorted(c_ms, ms, side="right")) - 1
        return float(c_val[k]) if k >= 0 else INIT

    peak_notional = peak_risk = 0.0
    peak_t = None
    peak_risk_pct = 0.0
    for a, b, notional, risk in ivs:
        if b <= a:
            continue
        cur_n = cur_r = 0.0
        for a2, b2, n2, r2 in ivs:
            if a2 <= a < b2:
                cur_n += n2
                cur_r += r2
        if cur_n > peak_notional:
            peak_notional = cur_n
        if cur_r > peak_risk:
            peak_risk = cur_r
            peak_t = a
            peak_risk_pct = cur_r / eq_at(a) if eq_at(a) > 0 else 0.0
    return {"最大并发": mx, "并发名义峰值": peak_notional, "并发风险峰值": peak_risk,
            "并发风险峰值占当时权益": peak_risk_pct, "峰值时刻": peak_t}


def main() -> None:
    tr, missing, zero = L.load_all()
    df = L.enrich(tr)
    df["期末结算"] = df["期末结算"].astype(bool)

    rows = []
    curves = []
    fund_rows = []
    for name, g in df.groupby("策略", sort=False):
        g = g.sort_values("open_ms").reset_index(drop=True)
        real = g[~g["期末结算"]]
        fin = g[g["期末结算"]]
        r = mtm_curves(g)
        sl = slots(g)
        dd_abs, dd_pct = engine_dd(name)
        win = real[real["profit_abs"] > 0]
        loss = real[real["profit_abs"] < 0]
        gp = float(win["profit_abs"].sum()); gl = float(-loss["profit_abs"].sum())
        rows.append({
            "策略": name, "组合": L.combo_of(name), "参数组": L.param_of(name),
            "s": float(g["s"].iloc[0]), "d": float(g["d"].iloc[0]),
            "笔数": len(g), "已实现笔数": len(real), "期末结算笔数": len(fin),
            "已实现R": float(real["R"].sum()), "期末R": float(fin["R"].sum()), "总R": float(g["R"].sum()),
            "已实现USDT": float(real["profit_abs"].sum()), "期末USDT": float(fin["profit_abs"].sum()),
            "总USDT": float(g["profit_abs"].sum()),
            "盈利笔数": len(win), "亏损笔数": len(loss),
            "胜率pct": float(len(win) / len(real) * 100) if len(real) else None,
            "PF已实现": (gp / gl) if gl > 0 else None,
            "手续费USDT": float(g["手续费USDT"].sum()),
            "资金费USDT": float(g["资金费USDT"].sum()),
            "价差盈亏USDT": float(g["价差盈亏USDT"].sum()),
            "中位持仓小时": float(g["持仓小时"].median()),
            "MTM回撤USDT": r["MTM"]["最大回撤USDT"], "MTM回撤pct": r["MTM"]["最大回撤占峰值pct"],
            "MTM回撤R": r["MTM"]["最大回撤R"],
            "对照A回撤USDT": r["A"]["最大回撤USDT"], "对照B回撤USDT": r["B"]["最大回撤USDT"],
            "已实现曲线回撤USDT": r.get("C", {}).get("最大回撤USDT"),
            "引擎回撤USDT": dd_abs, "引擎回撤pct": dd_pct,
            "最大并发": sl["最大并发"], "并发名义峰值": sl["并发名义峰值"],
            "并发风险峰值": sl["并发风险峰值"],
            "并发风险峰值占当时权益": sl["并发风险峰值占当时权益"],
            "风险覆盖比中位": float(g["风险覆盖比"].median()),
            "风险覆盖比最大": float(g["风险覆盖比"].max()),
            "计划风险USDT合计": float(g["计划风险USDT"].sum()),
            "实际成交亏损USDT合计": float(g["实际成交亏损USDT"].sum()),
            "资金费复现误差": r["资金费复现最大误差"],
        })
        curves.append({"策略": name, "组合": L.combo_of(name),
                       "MTM期末权益": r["MTM"]["期末权益"],
                       "闭合差": r["MTM"]["期末权益"] - (INIT + float(g["profit_abs"].sum()))})
        for t in g.itertuples(index=False):
            ev_ms, ev_fee, tot = funding_events(t.pair, int(t.open_ms), int(t.close_ms), float(t.amount))
            fund_rows.append({"策略": name, "pair": t.pair, "事件数": len(ev_ms),
                              "重算资金费": tot, "账面funding_fees": float(t.funding_fees),
                              "差": tot - float(t.funding_fees)})

    per = pd.DataFrame(rows)
    per.to_csv(OUT / "03_逐组指标.csv", index=False, encoding="utf-8-sig")
    cur = pd.DataFrame(curves)
    cur.to_csv(OUT / "03_MTM期末闭合.csv", index=False, encoding="utf-8-sig")
    fr = pd.DataFrame(fund_rows)
    fr.to_csv(OUT / "03_资金费对账.csv", index=False, encoding="utf-8-sig")

    add("完整性", "组数", len(per), len(per) == 144)
    add("完整性", "总笔数", int(per["笔数"].sum()), int(per["笔数"].sum()) == 9849)
    add("账户", "MTM 期末权益与 2000+Σprofit_abs 闭合", float(cur["闭合差"].abs().max()),
        float(cur["闭合差"].abs().max()) < 1e-6)
    add("账户", "资金费逐笔复现最大误差", float(fr["差"].abs().max()),
        float(fr["差"].abs().max()) < 1e-6, f"{len(fr)} 笔")
    # 手续费恒等式
    a = pd.to_numeric(df["amount"], errors="coerce")
    fcalc = a * (pd.to_numeric(df["open_rate"], errors="coerce") +
                 pd.to_numeric(df["close_rate"], errors="coerce")) * L.FEE
    add("账户", "手续费 = 数量×(开+平)×0.0005 最大误差", float((fcalc - df["手续费USDT"]).abs().max()),
        float((fcalc - df["手续费USDT"]).abs().max()) < 1e-9)
    add("账户", "手续费合计", float(per["手续费USDT"].sum()))
    add("账户", "资金费合计", float(per["资金费USDT"].sum()))
    add("账户", "期末技术结算单列", {"笔数": int(per["期末结算笔数"].sum()),
                                    "USDT": float(per["期末USDT"].sum()),
                                    "R": float(per["期末R"].sum())})

    # 槽位与风险
    add("槽位与风险", "最大并发持仓（全组最大值）", int(per["最大并发"].max()), int(per["最大并发"].max()) <= 3,
        "上限 max_open_trades=3")
    add("槽位与风险", "超槽组数", int((per["最大并发"] > 3).sum()), int((per["最大并发"] > 3).sum()) == 0)
    add("槽位与风险", "并发名义峰值占权益（最大）", float((per["并发名义峰值"] / INIT).max()),
        float((per["并发名义峰值"] / INIT).max()) <= 0.90, "冻结上限 max_total_notional=90%")
    add("槽位与风险", "并发风险峰值占当时权益（最大）", float(per["并发风险峰值占当时权益"].max()),
        float(per["并发风险峰值占当时权益"].max()) <= 0.015 + 1e-9, "冻结上限 max_total_risk_budget=1.5%")
    add("槽位与风险", "并发风险峰值占初始 2000（最大，仅对照）", float((per["并发风险峰值"] / INIT).max()),
        None, "分母用初始本金会高估；正式判据用当时权益")
    add("槽位与风险", "风险覆盖比（计划风险/预算）区间",
        [float(per["风险覆盖比中位"].min()), float(per["风险覆盖比中位"].max())],
        float(per["风险覆盖比中位"].max()) <= 1.0 + 1e-9, "≤1 表示单笔计划风险从不超 0.5% 预算")

    # 引擎回撤口径 vs MTM
    per["回撤低估pct"] = (per["MTM回撤USDT"] - per["引擎回撤USDT"]) / per["MTM回撤USDT"] * 100
    per["已实现曲线低估pct"] = (per["MTM回撤USDT"] - per["已实现曲线回撤USDT"]) / per["MTM回撤USDT"] * 100
    add("回撤口径", "引擎回撤 < MTM 回撤的组数", int((per["引擎回撤USDT"] < per["MTM回撤USDT"] - 1e-9).sum()),
        None, f"共 {len(per)} 组")
    add("回撤口径", "引擎回撤低估幅度（中位 / 最大）",
        [float(per["回撤低估pct"].median()), float(per["回撤低估pct"].max())])
    add("回撤口径", "已实现曲线低估幅度（中位 / 最大）",
        [float(per["已实现曲线低估pct"].median()), float(per["已实现曲线低估pct"].max())])
    add("回撤口径", "MTM 回撤 USDT（144 组）区间",
        [float(per["MTM回撤USDT"].min()), float(per["MTM回撤USDT"].median()), float(per["MTM回撤USDT"].max())])
    add("回撤口径", "引擎回撤 USDT（144 组）区间",
        [float(per["引擎回撤USDT"].min()), float(per["引擎回撤USDT"].median()), float(per["引擎回撤USDT"].max())])

    # 16 组合三层次
    agg = per.groupby("组合").agg(
        组数=("策略", "size"),
        L3合计USDT=("总USDT", "sum"),
        L2中位USDT=("总USDT", "median"),
        L1最小USDT=("总USDT", "min"), L1最大USDT=("总USDT", "max"),
        L3合计R=("总R", "sum"), L2中位R=("总R", "median"),
        盈利组数=("总USDT", lambda x: int((x > 0).sum())),
        笔数=("笔数", "sum"),
        MTM回撤中位=("MTM回撤USDT", "median"),
        MTM回撤最大=("MTM回撤USDT", "max"),
        引擎回撤中位=("引擎回撤USDT", "median"),
        手续费合计=("手续费USDT", "sum"),
        资金费合计=("资金费USDT", "sum"),
        中位持仓小时=("中位持仓小时", "median"),
    ).reset_index()
    agg.to_csv(OUT / "03_十六组合三层次.csv", index=False, encoding="utf-8-sig")
    add("三层次", "16 组合 L3/L2/L1 表", agg.round(4).to_dict("records"))

    crit = RES.get("判据", [])
    RES["判据统计"] = {"总": len(crit), "通过": sum(1 for c in crit if c["通过"]),
                       "不通过": sum(1 for c in crit if not c["通过"]),
                       "无判据": sum(1 for c in crit if c["通过"] is None)}
    with open(OUT / "03_账户与指标.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=2, default=str)
    print("判据:", RES["判据统计"])
    for c in crit:
        if c["通过"] is False:
            print("  ✗", c["段"], c["项"], c["说明"])


if __name__ == "__main__":
    main()
