# -*- coding: utf-8 -*-
"""03 指标独立重算 + 含未平仓盈亏的 15m 盯市权益回撤。

冻结口径（见 out/01_冻结分析计划.json）：
  主口径 MTM : equity(t) = 2000 + Σ已实现(close_ms≤t)
                          + Σ未平仓[(P15(t) − open_rate)×amount − 开仓费 − 资金费(开仓→t)]
  对照 A     : 不含资金费分摊的纯价差 MTM（回撤下界）
  对照 B     : 资金费在开仓时一次性全扣（回撤上界）
  对照 C     : 已实现曲线回撤（仅作对照，不得用于结论）

资金费事件：fee = −Σ(rate × mark × amount)，事件窗口 [open_ms, close_ms]
（与 freqtrade _calculate_funding_fees 的 searchsorted(left/right) 完全一致），
并对每一笔用逐笔 funding_fees 列做闭合对账。
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
INIT = L.INIT_EQUITY
R_UNIT = INIT * L.RISK_RATE          # 10 USDT = 初始 1R

# ---------- 数据缓存 ----------
_PX: dict[str, tuple[np.ndarray, np.ndarray]] = {}
_FUND: dict[str, tuple[np.ndarray, np.ndarray]] = {}
_MARK: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def _sym(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def _ms_of_series(s: pd.Series) -> np.ndarray:
    return ((s - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy(dtype=np.int64)


def price_15m(pair: str) -> tuple[np.ndarray, np.ndarray] | None:
    if pair in _PX:
        return _PX[pair]
    p = L.DATA / f"{_sym(pair)}-15m-futures.feather"
    if not p.exists():
        return None
    df = pd.read_feather(p, columns=["date", "close"])
    out = (_ms_of_series(df["date"]), df["close"].to_numpy(dtype=np.float64))
    _PX[pair] = out
    return out


def funding_8h(pair: str) -> tuple[np.ndarray, np.ndarray] | None:
    if pair in _FUND:
        return _FUND[pair]
    p = L.DATA / f"{_sym(pair)}-8h-funding_rate.feather"
    if not p.exists():
        return None
    df = pd.read_feather(p, columns=["date", "funding_rate"])
    out = (_ms_of_series(df["date"]), df["funding_rate"].to_numpy(dtype=np.float64))
    _FUND[pair] = out
    return out


def mark_1h(pair: str) -> tuple[np.ndarray, np.ndarray] | None:
    if pair in _MARK:
        return _MARK[pair]
    p = L.DATA / f"{_sym(pair)}-1h-mark.feather"
    if not p.exists():
        return None
    df = pd.read_feather(p, columns=["date", "close"])
    out = (_ms_of_series(df["date"]), df["close"].to_numpy(dtype=np.float64))
    _MARK[pair] = out
    return out


def _ffill_at(series: tuple[np.ndarray, np.ndarray] | None, t: np.ndarray) -> np.ndarray:
    """取 t 时刻及之前最近一个已知值（不偷看未来）。"""
    if series is None:
        return np.full(len(t), np.nan)
    ms, v = series
    idx = np.searchsorted(ms, t, side="right") - 1
    ok = idx >= 0
    out = np.full(len(t), np.nan)
    out[ok] = v[idx[ok]]
    return out


def funding_events(pair: str, open_ms: int, close_ms: int, amount: float):
    """返回 (事件时刻数组, 逐事件费用数组, 合计)。

    口径（在 708/708 笔上逐笔零误差复现上游 funding_fees，见 03_资金费对账.csv）：
      1) 资金费时间戳带亚秒级抖动（16:00:00.011 / 15:59:59.999 等），先归一到整秒；
      2) 事件窗口 = [开仓时刻, 平仓时刻]（闭区间，含退出那根 15m K 线所属的资金事件）；
      3) 标记价取事件时刻之前最近一个**非空**值（标记价文件存在整段缺口，直接取会得 NaN）。
    费用 = −Σ(rate × mark × amount)，多头付费为负。
    """
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
    mv = pd.Series(mv).ffill().to_numpy()          # 跳过 NaN 前向填充
    k = np.searchsorted(mms, ms[first:last], side="right") - 1
    mkpx = np.where(k >= 0, mv[np.clip(k, 0, None)], np.nan)
    fee = -(ev_rate * mkpx * amount)               # 多头：付费为负
    ok = np.isfinite(fee)
    if not ok.all():
        ev_ms, fee = ev_ms[ok], fee[ok]
    return ev_ms, fee, float(fee.sum())


# ---------- MTM 曲线 ----------
def _merge_intervals(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
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
    """返回四种口径的 (grid, equity) 以及回撤指标。"""
    g = g.sort_values("open_ms").reset_index(drop=True)
    trs = g.to_dict("records")
    for t in trs:
        ev_ms, ev_fee, tot = funding_events(t["pair"], int(t["open_ms"]), int(t["close_ms"]),
                                           float(t["amount"]))
        t["_ev_ms"] = ev_ms
        t["_ev_fee"] = ev_fee
        t["_fund_total"] = tot
        t["_cum_ms"] = np.concatenate([[0], np.cumsum(ev_fee)]) if len(ev_fee) else np.array([0.0])
        t["_cum_ms_t"] = np.concatenate([[int(t["open_ms"])], ev_ms.astype(np.int64)]) if len(ev_fee) \
            else np.array([int(t["open_ms"])], dtype=np.int64)
        t["_open_fee"] = float(t["amount"]) * float(t["open_rate"]) * float(t["fee_open"])
        t["_px"] = price_15m(t["pair"])

    # 已实现阶梯：所有逐笔在 close_ms 一次性入账
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
        add_mtm = np.zeros(len(grid))
        add_a = np.zeros(len(grid))
        add_b = np.zeros(len(grid))
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
            # 资金费累计（事件时刻 <= t）
            k = np.searchsorted(t["_cum_ms_t"], grid[m], side="right") - 1
            fund = t["_cum_ms"][np.clip(k, 0, len(t["_cum_ms"]) - 1)]
            add_mtm[m] += base + fund
            add_a[m] += base
            add_b[m] += base + t["_fund_total"]
        grids.append(grid)
        eq_mtm.append(real + add_mtm)
        eq_a.append(real + add_a)
        eq_b.append(real + add_b)

    if not grids:
        return {"ok": False}

    grid = np.concatenate(grids)
    # 补一个期末观测点（最后一笔平仓时刻，全部已实现），使期末权益可与自报闭合
    t_end = int(g["close_ms"].max())
    final_eq = INIT + float(g["profit_abs"].sum())
    grid = np.append(grid, t_end)
    eq_mtm = [np.concatenate(eq_mtm), np.array([final_eq])]
    eq_a = [np.concatenate(eq_a), np.array([final_eq])]
    eq_b = [np.concatenate(eq_b), np.array([final_eq])]

    res = {"ok": True, "grid": grid}
    for key, arr in (("MTM", eq_mtm), ("A", eq_a), ("B", eq_b)):
        eq = np.concatenate(arr)
        if key == "MTM":
            res["缺失价点"] = int(np.isnan(eq).sum())
        keep = np.isfinite(eq)
        if not keep.all():
            eq = eq[keep]
            gg = grid[keep]
        else:
            gg = grid
        peak = np.maximum.accumulate(eq)
        dd = peak - eq
        i = int(np.argmax(dd))
        j = int(np.argmax(eq[: i + 1])) if i >= 0 else 0
        res[key] = {
            "峰值权益": float(peak[i]),
            "谷值权益": float(eq[i]),
            "最大回撤USDT": float(dd[i]),
            "最大回撤占峰值pct": float(dd[i] / peak[i] * 100) if peak[i] > 0 else None,
            "最大回撤R": float(dd[i] / R_UNIT),
            "峰时刻": int(gg[j]),
            "谷时刻": int(gg[i]),
            "期末权益": float(eq[-1]),
            "最小权益": float(eq.min()),
            "最大权益": float(eq.max()),
        }

    # 对照 C：已实现曲线（按平仓时刻）
    if len(c_ms):
        eqc = np.concatenate([[INIT], c_val])
        peak = np.maximum.accumulate(eqc)
        ddc = peak - eqc
        i = int(np.argmax(ddc))
        res["C"] = {
            "最大回撤USDT": float(ddc[i]),
            "最大回撤占峰值pct": float(ddc[i] / peak[i] * 100) if peak[i] > 0 else None,
            "最大回撤R": float(ddc[i] / R_UNIT),
            "期末权益": float(eqc[-1]),
        }
    return res


# ---------- 单组指标 ----------
def group_metrics(g: pd.DataFrame) -> dict:
    real = g[~g["是否期末结算"]]
    fin = g[g["是否期末结算"]]
    n = len(g)
    win = real[real["profit_abs"] > 0]
    loss = real[real["profit_abs"] < 0]
    gp = float(win["profit_abs"].sum())
    gl = float(-loss["profit_abs"].sum())
    pct = float(g["止损比例"].iloc[0])

    rec = {
        "笔数": n,
        "已实现笔数": len(real),
        "期末结算笔数": len(fin),
        "标的数": int(g["pair"].nunique()),
        "已实现R": float(real["R"].sum()),
        "期末R": float(fin["R"].sum()),
        "总R": float(g["R"].sum()),
        "已实现USDT": float(real["profit_abs"].sum()),
        "期末USDT": float(fin["profit_abs"].sum()),
        "总USDT": float(g["profit_abs"].sum()),
        "已实现胜率pct": float(len(win) / len(real) * 100) if len(real) else None,
        "已实现盈利笔数": len(win),
        "PF已实现": (gp / gl) if gl > 0 else None,
        "中位持仓小时": float(g["持仓小时"].median()),
        "已实现中位持仓小时": float(real["持仓小时"].median()) if len(real) else None,
        "手续费USDT": float(g["手续费USDT"].sum()),
        "资金费USDT": float(g["资金费USDT"].sum()),
        "价差USDT": float(g["价差盈亏USDT"].sum()),
        "手续费R": float(g["手续费R"].sum()),
        "资金费R": float(g["资金费R"].sum()),
        "单笔最大R": float(g["R"].max()),
        "单笔最小R": float(g["R"].min()),
        "名义占权益中位pct": float(g["名义占权益"].median() * 100),
        "风险覆盖比中位": float(g["风险覆盖比"].median()),
        "止损退出笔数": int(g["exit_reason"].astype(str).isin(L.STOP_LABELS).sum()),
        "trailing标签笔数": int(g["exit_reason"].astype(str).eq("trailing_stop_loss").sum()),
        "期末结算R占已实现R比": None,
    }
    # 集中度：按币汇总 R
    by_pair = g.groupby("pair")["R"].sum().sort_values(ascending=False)
    tot_pos = float(g.loc[g["R"] > 0, "R"].sum())
    rec["R前1币占比pct"] = float(by_pair.iloc[0] / tot_pos * 100) if tot_pos > 0 and len(by_pair) else None
    rec["R前3币占比pct"] = float(by_pair.iloc[:3].sum() / tot_pos * 100) if tot_pos > 0 else None
    rec["R前5币占比pct"] = float(by_pair.iloc[:5].sum() / tot_pos * 100) if tot_pos > 0 else None
    rec["最大单币R"] = float(by_pair.iloc[0]) if len(by_pair) else None
    rec["最大单币"] = str(by_pair.index[0]) if len(by_pair) else None
    # 极端盈利依赖：单笔最大 R 占已实现 R 的比例、正 R 里第一笔的占比
    rec["最大单笔R占已实现R比"] = (rec["单笔最大R"] / rec["已实现R"]) if rec["已实现R"] > 0 else None
    pos_r = g.loc[g["R"] > 0, "R"].sort_values(ascending=False)
    rec["R前1笔占比pct"] = float(pos_r.iloc[0] / pos_r.sum() * 100) if len(pos_r) else None

    # 2R 激活率（用 4h 重建）
    act = 0
    lifts = []
    for t in g.to_dict("records"):
        r = L.trailing_path(t["pair"], float(t["open_rate"]), pct,
                            int(t["open_ms"]), int(t["close_ms"]))
        if r.get("ok") and r.get("activated"):
            act += 1
            lifts.append((r["final_stop"] - r["initial_stop"]) / r["initial_stop"] * 100)
    rec["2R激活笔数"] = act
    rec["2R激活率pct"] = float(act / n * 100) if n else None
    rec["上移幅度中位pct"] = float(np.median(lifts)) if lifts else None
    rec["上移幅度最大pct"] = float(np.max(lifts)) if lifts else None
    return rec


def main() -> None:
    df, missing = L.load_all()
    d = L.equity_curve(df) if not df.empty else pd.DataFrame()
    zero = df.attrs.get("零成交组", [])

    rows, mtm_rows, fund_rows = [], [], []
    for name in L.ALL:
        g = d[d["策略"] == name] if not d.empty else pd.DataFrame()
        if g.empty:
            rows.append({"策略": name, "档位": L.tier_of(name), "参数组": L.suffix_of(name),
                         "状态": "未落盘" if name in missing else "零成交"})
            continue
        rec = {"策略": name, "档位": L.tier_of(name), "参数组": L.suffix_of(name), "状态": "ok"}
        rec.update(group_metrics(g))

        # 资金费逐笔闭合对账
        diffs, n_ev = [], 0
        for t in g.to_dict("records"):
            _, fee, tot = funding_events(t["pair"], int(t["open_ms"]), int(t["close_ms"]),
                                         float(t["amount"]))
            n_ev += len(fee)
            diffs.append(abs(tot - float(t["资金费USDT"])))
        fund_rows.append({
            "策略": name,
            "逐笔资金费合计": float(g["资金费USDT"].sum()),
            "重算合计": float(sum(
                funding_events(t["pair"], int(t["open_ms"]), int(t["close_ms"]), float(t["amount"]))[2]
                for t in g.to_dict("records"))),
            "最大逐笔绝对误差": float(max(diffs)) if diffs else 0.0,
            "超1e-6笔数": int(sum(1 for x in diffs if x > 1e-6)),
            "资金事件数": n_ev,
        })

        m = mtm_curves(g)
        if m.get("ok"):
            for key in ("MTM", "A", "B", "C"):
                if key not in m:
                    continue
                mtm_rows.append({
                    "策略": name, "档位": L.tier_of(name), "参数组": L.suffix_of(name),
                    "口径": key, **m[key],
                })
            rec["MTM最大回撤USDT"] = m["MTM"]["最大回撤USDT"]
            rec["MTM最大回撤pct"] = m["MTM"]["最大回撤占峰值pct"]
            rec["MTM最大回撤R"] = m["MTM"]["最大回撤R"]
            rec["MTM期末权益"] = m["MTM"]["期末权益"]
            rec["A最大回撤USDT"] = m["A"]["最大回撤USDT"]
            rec["B最大回撤USDT"] = m["B"]["最大回撤USDT"]
            rec["C最大回撤USDT"] = m.get("C", {}).get("最大回撤USDT")
            rec["期末权益差USDT"] = m["MTM"]["期末权益"] - (INIT + rec["总USDT"])
        rows.append(rec)

    per = pd.DataFrame(rows)
    per.to_csv(OUT / "03_逐组指标.csv", index=False, encoding="utf-8-sig")
    mt = pd.DataFrame(mtm_rows)
    mt.to_csv(OUT / "03_MTM回撤明细.csv", index=False, encoding="utf-8-sig")
    fu = pd.DataFrame(fund_rows)
    fu.to_csv(OUT / "03_资金费对账.csv", index=False, encoding="utf-8-sig")

    ok = per[per["状态"] == "ok"] if "状态" in per else pd.DataFrame()
    tier_sum = {}
    for t in L.TIERS:
        s = ok[ok["档位"] == t] if not ok.empty else pd.DataFrame()
        if s.empty:
            continue
        tier_sum[t] = {
            "组数": int(len(s)),
            "已实现R中位": float(s["已实现R"].median()),
            "期末R中位": float(s["期末R"].median()),
            "总R中位": float(s["总R"].median()),
            "总USDT中位": float(s["总USDT"].median()),
            "MTM最大回撤USDT中位": float(s["MTM最大回撤USDT"].median()),
            "MTM最大回撤pct中位": float(s["MTM最大回撤pct"].median()),
            "MTM最大回撤R中位": float(s["MTM最大回撤R"].median()),
            "已实现胜率pct中位": float(s["已实现胜率pct"].median()),
            "PF已实现中位": float(s["PF已实现"].median()) if s["PF已实现"].notna().any() else None,
            "笔数中位": float(s["笔数"].median()),
            "中位持仓小时": float(s["中位持仓小时"].median()),
            "2R激活率pct中位": float(s["2R激活率pct"].median()),
            "资金费USDT中位": float(s["资金费USDT"].median()),
            "手续费USDT中位": float(s["手续费USDT"].median()),
            "R前3币占比pct中位": float(s["R前3币占比pct"].median()),
            "A最大回撤USDT中位": float(s["A最大回撤USDT"].median()),
            "B最大回撤USDT中位": float(s["B最大回撤USDT"].median()),
            "C最大回撤USDT中位": float(s["C最大回撤USDT"].median())
            if s["C最大回撤USDT"].notna().any() else None,
            "已实现盈利笔数中位": float(s["已实现盈利笔数"].median()),
            "期末结算笔数中位": float(s["期末结算笔数"].median()),
            "上移幅度中位pct中位": float(s["上移幅度中位pct"].median())
            if s["上移幅度中位pct"].notna().any() else None,
            "单笔最大R中位": float(s["单笔最大R"].median()),
            "名义占权益pct中位": float(s["名义占权益中位pct"].median()),
            "风险覆盖比中位": float(s["风险覆盖比中位"].median()),
            "标的数中位": float(s["标的数"].median()),
            "R前1笔占比pct中位": float(s["R前1笔占比pct"].median())
            if s["R前1笔占比pct"].notna().any() else None,
            "最大单笔R占已实现R比中位": float(s["最大单笔R占已实现R比"].median())
            if s["最大单笔R占已实现R比"].notna().any() else None,
        }

    res = {
        "工单": "FT-STOP-GRID-AUDIT-02 / 03 指标独立重算",
        "组数_已落盘": int(len(ok)),
        "组数_未落盘": len(missing),
        "零成交组": zero,
        "分档汇总（9 个参数组取中位）": tier_sum,
        "资金费闭合对账": {
            "组数": int(len(fu)),
            "最大逐笔绝对误差": float(fu["最大逐笔绝对误差"].max()) if len(fu) else None,
            "误差超1e-6的笔数合计": int(fu["超1e-6笔数"].sum()) if len(fu) else None,
            "资金事件总数": int(fu["资金事件数"].sum()) if len(fu) else None,
        },
        "MTM 期末权益与自报闭合": {
            "组数": int(ok["期末权益差USDT"].notna().sum()) if not ok.empty else 0,
            "最大绝对差USDT": float(ok["期末权益差USDT"].abs().max()) if not ok.empty else None,
        },
        "回撤口径说明": {
            "R归一化": "回撤R = 回撤USDT / 10（10 USDT = 初始权益 2000 × 0.5%）；权益变动后 1R 会漂移，故 R 只用于量级对比",
            "C口径": "已实现曲线回撤，仅作对照，不得用于结论",
        },
    }
    with open(OUT / "03_指标重算.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(f"已落盘组数={len(ok)}  未落盘={len(missing)}  零成交={len(zero)}")
    for t, v in tier_sum.items():
        print(f"{t}: 已实现R中位={v['已实现R中位']:.2f} 总R中位={v['总R中位']:.2f} "
              f"MTM回撤中位={v['MTM最大回撤USDT中位']:.1f}USDT "
              f"({v['MTM最大回撤pct中位']:.1f}%) 已实现曲线回撤中位="
              f"{v['C最大回撤USDT中位']:.1f}USDT")
    if len(fu):
        print("资金费对账 最大误差=", float(fu["最大逐笔绝对误差"].max()),
              " 超差笔数=", int(fu["超1e-6笔数"].sum()))
    if not ok.empty:
        print("MTM期末权益最大绝对差=", float(ok["期末权益差USDT"].abs().max()))


if __name__ == "__main__":
    main()
