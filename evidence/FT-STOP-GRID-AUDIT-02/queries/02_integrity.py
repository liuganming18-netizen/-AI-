# -*- coding: utf-8 -*-
"""02 交付完整性 + 2R 触发与止损上移逐笔重建（本轮核心）。

不采信 exit_reason 标签：用 4h 已收盘 K 线独立重建每笔的 2R 激活、棘轮轨迹与账面止损，
再与上游 stop_loss_abs / close_rate / 15m K 线交叉验证。
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
TIERS = L.TIERS
BAR15 = L.BAR_15M_MS


def rebuild_trade(row, pct: float) -> dict:
    """按上游源码逻辑重建一笔。返回重建结果与一致性标记。"""
    pair = row["pair"]
    open_rate = float(row["open_rate"])
    entry_ms = int(row["open_ms"])
    exit_open_ms = int(row["close_ms"])          # close_timestamp = 退出 K 线开盘时刻
    is_final = bool(row["是否期末结算"])

    initial_stop = open_rate * (1.0 - pct)
    actual_r = open_rate * pct
    trigger_2r = open_rate + 2.0 * actual_r

    df4 = L.load_4h(pair)
    if df4 is None:
        return {"ok": False, "reason": "no_4h"}

    tick = L.tick_of(f"{row['stop_loss_abs']:.10f}".rstrip("0"))
    tol = tick * 1.5

    def stop_at(decision_ms: int) -> tuple[float, bool, int | None, int]:
        """决策时刻 decision_ms 时，按 close_ms<=decision_ms 的 4h 计算的账面止损。"""
        sub = df4[(df4["close_ms"] >= entry_ms) & (df4["close_ms"] <= decision_ms)]
        if sub.empty:
            return initial_stop, False, None, 0
        closes = sub["close"].to_numpy(dtype=float)
        l10s = sub["l10"].to_numpy(dtype=float)
        cms = sub["close_ms"].to_numpy(dtype=np.int64)
        hit = np.where(closes >= trigger_2r)[0]
        act = bool(len(hit))
        if not act:
            return initial_stop, False, None, 0
        idx = int(hit[0])
        stop = initial_stop
        steps = 0
        for i in range(idx, len(l10s)):
            v = l10s[i]
            if math.isfinite(v) and v > stop:
                stop = v
                steps += 1
        return float(stop), True, int(cms[idx]), steps

    stop_exit, activated, act_ms, steps = stop_at(exit_open_ms)
    stop_prev, act_prev, _, _ = stop_at(exit_open_ms - BAR15)
    # 反事实：允许用"退出 K 线收盘之后才收完"的 4h（即偷看未来 15 分钟）
    stop_future, _, _, _ = stop_at(exit_open_ms + BAR15)
    stop_future4, act_future4, _, _ = stop_at(exit_open_ms + L.BAR_4H_MS)

    recorded_stop = float(row["stop_loss_abs"])
    # 账面止损只可能是两个值之一：
    #   a) 同根值 stop_exit —— 引擎在本根 15m 调用了 custom_stoploss（用的是本根开盘前已收完的 4h）
    #   b) 上一根值 stop_prev —— 本根开盘时止损已被击穿，interface.py:1552 的 dir_correct 为假，跳过调用
    # 因此"至少等于其中之一"才是正确判据；两者都不等才是异常。
    match_same = abs(recorded_stop - stop_exit) <= tol
    match_prev = abs(recorded_stop - stop_prev) <= tol
    if match_same:
        stop_ref, ref_kind = stop_exit, "同根"
    elif match_prev:
        stop_ref, ref_kind = stop_prev, "上一根"
    else:
        stop_ref, ref_kind = stop_exit, "都不符"
    # "真实上移" 必须由棘轮产生；ROUND_UP 取整最多造成 1 tick 的机械上偏，不算上移
    moved = steps > 0
    rounded_up = (recorded_stop - initial_stop) > tick * 0.5 and steps == 0

    res = {
        "ok": True,
        "initial_stop": initial_stop,
        "actual_r": actual_r,
        "trigger_2r": trigger_2r,
        "activated": activated,
        "activate_ms": act_ms,
        "ratchet_steps": steps,
        "stop_rebuilt": stop_ref,
        "stop_ref_kind": ref_kind,
        "stop_rebuilt_same_bar": stop_exit,
        "stop_rebuilt_prev_bar": stop_prev,
        "stop_recorded": recorded_stop,
        "stop_diff_ticks": (recorded_stop - stop_ref) / tick if tick > 0 else np.nan,
        "stop_match": bool(match_same or match_prev),
        "stop_match_same_bar": bool(match_same),
        "stop_match_prev_bar": bool(match_prev),
        "同根新4h本可上移tick": (stop_exit - stop_prev) / tick if tick > 0 else np.nan,
        "stop_moved": bool(moved),
        "stop_rounded_only": bool(rounded_up),
        "stop_vs_initial_pct": (recorded_stop / initial_stop - 1.0) * 100.0,
        "stop_prev": stop_prev,
        "stop_future_15m": stop_future,
        "stop_future_4h": stop_future4,
        "future_15m_matches": abs(recorded_stop - stop_future) <= tol,
        "future_4h_matches": abs(recorded_stop - stop_future4) <= tol,
        # 区分力指标：偷看 4 小时后结果是否真的会变（为 0 说明这条检验在本批样本上没有区分力）
        "future_4h_stop_differs": bool(abs(stop_future4 - stop_exit) > tol),
        "future_4h_activation_differs": bool(bool(act_future4) != activated),
        "tick": tick,
    }
    return res


def main() -> dict:
    df, missing = L.load_all()
    if df.empty:
        return {"错误": "没有任何逐笔结果"}
    d = L.equity_curve(df)

    # ---------- 1 组完整性 ----------
    groups = []
    for name in L.ALL:
        p = L.UP / f"out/trades_{name}.csv"
        if not p.exists():
            groups.append({"策略": name, "状态": "未落盘"})
            continue
        g = d[d["策略"] == name]
        groups.append({"策略": name, "档位": L.tier_of(name), "参数组": L.suffix_of(name),
                       "状态": "有成交" if len(g) else "零成交", "笔数": int(len(g)),
                       "文件字节": p.stat().st_size})
    gp = pd.DataFrame(groups)
    integ = {
        "组数": int(len(gp)),
        "未落盘": gp[gp["状态"] == "未落盘"]["策略"].tolist(),
        "零成交": gp[gp["状态"] == "零成交"]["策略"].tolist(),
        "总笔数": int(len(d)),
        "各档笔数": {t: int((d["档位"] == t).sum()) for t in TIERS},
    }

    # ---------- 2 初始止损 ----------
    tick_s = d["stop_loss_abs"].map(lambda v: L.tick_of(f"{v:.10f}".rstrip("0")))
    d["tick"] = tick_s
    theo_init = d["open_rate"] * (1 - d["止损比例"])
    d["初始止损偏差tick"] = (d["stop_loss_abs"] - theo_init) / tick_s
    sentinel = (d["initial_stop_loss_abs"] - d["open_rate"] * 0.01).abs() <= tick_s * 1.5
    integ["初始止损（公式 = 成交价×(1−档位比例)）"] = {
        "账面止损低于公式值超过 1 tick 的笔数（应为 0）": int((d["初始止损偏差tick"] < -1.0).sum()),
        "账面止损恰好等于公式值±1tick 的笔数（= 从未上移）": int((d["初始止损偏差tick"].abs() <= 1.0).sum()),
        "账面止损高于公式值超过 1 tick 的笔数（= 已上移）": int((d["初始止损偏差tick"] > 1.0).sum()),
        "向下最大偏差 tick": float(d["初始止损偏差tick"].min()),
    }
    integ["initial_stop_loss_abs 为 1% 哨兵"] = {
        "命中": int(sentinel.sum()), "总数": int(len(d)), "比例": float(sentinel.mean())}

    # ---------- 3 逐笔重建 ----------
    rows = []
    for _, r in d.iterrows():
        pct = float(r["止损比例"])
        rb = rebuild_trade(r, pct)
        rec = {"策略": r["策略"], "档位": r["档位"], "参数组": r["参数组"], "pair": r["pair"],
               "事件ID": r["事件ID"], "exit_reason": r["exit_reason"],
               "是否期末结算": bool(r["是否期末结算"]),
               "open_rate": float(r["open_rate"]), "close_rate": float(r["close_rate"]),
               "stop_loss_abs": float(r["stop_loss_abs"]),
               "open_ms": int(r["open_ms"]), "close_ms": int(r["close_ms"]),
               "profit_abs": float(r["profit_abs"]), "R": float(r["R"]),
               "持仓小时": float(r["持仓小时"])}
        rec.update({k: v for k, v in rb.items() if k != "ok"})
        rec["重建成功"] = bool(rb.get("ok", False))
        rows.append(rec)
    rb_df = pd.DataFrame(rows)

    # ---------- 4 退出链路一致性 ----------
    act = rb_df["activated"].fillna(False)
    moved = rb_df["stop_moved"].fillna(False)
    final = rb_df["是否期末结算"]
    lbl = rb_df["exit_reason"].astype(str)

    checks = {
        "重建成功": int(rb_df["重建成功"].sum()),
        "止损价与重建一致（≤1.5 tick）": int(rb_df["stop_match"].fillna(False).sum()),
        "止损价与重建不一致的笔数": int((~rb_df["stop_match"].fillna(False)).sum()),
        "未激活却被标 trailing": int((~act & (lbl == "trailing_stop_loss")).sum()),
        "已激活且真实上移、被标 trailing": int((act & moved & (lbl == "trailing_stop_loss")).sum()),
        "已激活但未真实上移、被标 trailing": int((act & ~moved & (lbl == "trailing_stop_loss")).sum()),
        "已激活且真实上移、却被标 stop_loss": int((act & moved & (lbl == "stop_loss")).sum()),
        "2R 激活笔数": int(act.sum()),
        "2R 激活率": float(act.mean()) if len(rb_df) else None,
    }
    checks["标签 vs 真实机制"] = {
        "trailing_stop_loss 总数": int((lbl == "trailing_stop_loss").sum()),
        "其中真实上移": int((moved & (lbl == "trailing_stop_loss")).sum()),
        "其中未真实上移（取整误标）": int((~moved & (lbl == "trailing_stop_loss")).sum()),
        "stop_loss 总数": int((lbl == "stop_loss").sum()),
        "其中其实已上移（漏标）": int((moved & (lbl == "stop_loss")).sum()),
        "force_exit 总数": int((lbl == "force_exit").sum()),
    }

    # ---------- 5 未来数据反例 ----------
    same_bar = rb_df["stop_match_same_bar"].fillna(False)
    prev_bar = rb_df["stop_match_prev_bar"].fillna(False)
    ref_bar = rb_df["stop_match"].fillna(False)
    both_diff = (rb_df["同根新4h本可上移tick"].fillna(0).abs() > 1.5)
    la = {
        "账面止损可解释（= 同根值 或 上一根值）": int(ref_bar.sum()),
        "账面止损两者都不符（异常）": int((~ref_bar).sum()),
        "按同根值解释的笔数": int(same_bar.sum()),
        "按上一根值解释的笔数（本根已被击穿，引擎跳过调用）": int((prev_bar & ~same_bar).sum()),
        "同根值与上一根值不同的笔数": int(both_diff.sum()),
        "上述笔数中账面取上一根值的笔数（未用新收完的 4h）": int((both_diff & prev_bar & ~same_bar).sum()),
        "上述笔数中账面取同根值的笔数（用了本根开盘前已收完的 4h）": int((both_diff & same_bar).sum()),
        "同根新4h本可上移的最小tick": float(rb_df.loc[both_diff, "同根新4h本可上移tick"].min()) if both_diff.any() else None,
        "同根新4h本可上移的最大tick": float(rb_df.loc[both_diff, "同根新4h本可上移tick"].max()) if both_diff.any() else None,
        "偷看 15 分钟后的重建值吻合笔数": int(rb_df["future_15m_matches"].fillna(False).sum()),
        "偷看 4 小时后的重建值吻合笔数": int(rb_df["future_4h_matches"].fillna(False).sum()),
        "偷看 4h 后止损值会改变的笔数（本检验的区分力）":
            int(rb_df["future_4h_stop_differs"].fillna(False).sum()),
        "偷看 4h 后激活状态会改变的笔数（本检验的区分力）":
            int(rb_df["future_4h_activation_differs"].fillna(False).sum()),
    }

    # ---------- 6 退出 K 线触及 ----------
    touch_rows = []
    for _, r in rb_df[~rb_df["是否期末结算"] & rb_df["重建成功"]].iterrows():
        k = L.load_kline(r["pair"], "15m")
        if k is None:
            continue
        ms = k["ms"].to_numpy()
        i = int(np.searchsorted(ms, int(r["close_ms"]), side="left"))
        if i >= len(ms) or ms[i] != int(r["close_ms"]):
            touch_rows.append({"pair": r["pair"], "策略": r["策略"], "ok": False, "原因": "退出K线缺失"})
            continue
        low, high = float(k["low"].iloc[i]), float(k["high"].iloc[i])
        stop = float(r["stop_loss_abs"])
        touch_rows.append({
            "pair": r["pair"], "策略": r["策略"], "exit_reason": r["exit_reason"],
            "ok": True, "low": low, "high": high, "stop": stop,
            "stop_in_bar": bool(low <= stop <= high),
            "stop_below_low": bool(stop < low),
            "stop_above_high": bool(stop > high),
            "close_eq_stop": bool(abs(float(r["close_rate"]) - stop) <= float(r["tick"]) * 1.5),
        })
    tch = pd.DataFrame(touch_rows)
    touch = {
        "检查笔数": int(len(tch)),
        "止损落在退出K线[low,high]内": int(tch["stop_in_bar"].sum()) if len(tch) else 0,
        "止损低于该K线最低（不可能成交）": int(tch["stop_below_low"].sum()) if len(tch) else 0,
        "止损高于该K线最高": int(tch["stop_above_high"].sum()) if len(tch) else 0,
        "成交价=账面止损": int(tch["close_eq_stop"].sum()) if len(tch) else 0,
    }

    # ---------- 7 按档位 ----------
    by_tier = []
    for t in TIERS:
        g = rb_df[rb_df["档位"] == t]
        if g.empty:
            continue
        by_tier.append({
            "档位": t, "笔数": int(len(g)),
            "2R激活率%": round(float(g["activated"].fillna(False).mean()) * 100, 2),
            "真实上移笔数": int(g["stop_moved"].fillna(False).sum()),
            "真实上移占比%": round(float(g["stop_moved"].fillna(False).mean()) * 100, 2),
            "上移幅度中位%": round(float(((g.loc[g["stop_moved"].fillna(False), "stop_loss_abs"] /
                                        g.loc[g["stop_moved"].fillna(False), "open_rate"] - 1) * 100).median()), 2)
            if g["stop_moved"].fillna(False).any() else None,
            "trailing标签笔数": int((g["exit_reason"] == "trailing_stop_loss").sum()),
            "止损价一致率%": round(float(g["stop_match"].fillna(False).mean()) * 100, 2),
        })
    tier_df = pd.DataFrame(by_tier)

    rb_df.to_csv(OUT / "02_逐笔跟踪重建明细.csv", index=False, encoding="utf-8-sig")
    tch.to_csv(OUT / "02_退出K线核对明细.csv", index=False, encoding="utf-8-sig")

    return {
        "一_组完整性": integ,
        "二_跟踪重建": checks,
        "三_未来数据反例": la,
        "四_退出K线": touch,
        "五_分档": by_tier,
        "六_未落盘组": missing,
        "七_零成交组": d.attrs.get("零成交组", []),
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "02_跟踪与完整性核对.json"
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: r[k] for k in ("一_组完整性", "二_跟踪重建", "三_未来数据反例", "四_退出K线")},
                     ensure_ascii=False, indent=1, default=str)[:4000])
    print(f"\n写出 {p}")
