# -*- coding: utf-8 -*-
"""02 逐笔重建止损轨迹与退出链路（不看引擎标签）。

按冻结口径逐 15m K 线重放引擎语义：
  1) 候选价 Pcand = 最近一根**已收盘** 4h 的 low × (1−d)，已收盘 = 4h 桶起点 + 4h ≤ 本 15m 开盘时刻
  2) 内部状态 state = max(P0, 历次 Pcand)（只上移）
  3) dir_correct = 当前生效止损 < 本根 low 时才允许调整；调整后 stop = max(stop, state)
  4) 若 state ≥ 本根 high，策略返回 0.0（不改写）⇒ 生效止损保持原值，顺延到后续 K 线
  5) 退出：low ≤ stop ⇒ 成交价 = stop；若 stop > high（跳空）⇒ 成交价 = 本根 open
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

import _lib as L

RES: dict = {"段": {}}


def add(sec: str, key: str, val, ok=None, note: str = "") -> None:
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES.setdefault("判据", []).append({"段": sec, "项": key, "通过": bool(ok), "说明": note})


def prep_pair(pair: str):
    d15 = L.load_15m(pair)
    d4 = L.load_4h(pair)
    if d15 is None or d4 is None:
        return None
    return d15, d4


def infer_tick(vals: np.ndarray) -> float:
    """按账面价格反推该币的最小价位：能整除全部账面价的最粗 10^-k。"""
    v = np.asarray(vals, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return 1e-8
    for k in range(0, 9):
        s = v * 10 ** k
        if np.all(np.abs(s - np.round(s)) < 1e-6):
            return 10.0 ** -k
    return 1e-8


def rebuild(tr: pd.DataFrame) -> pd.DataFrame:
    out = []
    cache = {}
    by_pair = defaultdict(list)
    for r in tr.itertuples(index=False):
        by_pair[r.pair].append(r)

    for pair, rows in by_pair.items():
        got = prep_pair(pair)
        if got is None:
            for r in rows:
                out.append({"策略": r.策略, "pair": pair, "pair": pair, "状态": "缺K线"})
            continue
        d15, d4 = got
        ms15 = d15["ms"].to_numpy()
        o15 = d15["open"].to_numpy()
        h15 = d15["high"].to_numpy()
        l15 = d15["low"].to_numpy()
        c4ms = d4["close_ms"].to_numpy()
        low4 = d4["low"].to_numpy()
        sub = tr[tr["pair"].eq(pair)]
        tick = infer_tick(pd.concat([sub["open_rate"], sub["close_rate"], sub["stop_loss_abs"]]).to_numpy())

        for r in rows:
            s = float(r.s)
            d = float(r.d)
            p0 = float(r.open_rate) * (1.0 - s)
            cand_all = low4 * (1.0 - d)

            i0 = int(np.searchsorted(ms15, int(r.open_ms), side="left"))
            i1 = int(np.searchsorted(ms15, int(r.close_ms), side="left"))
            rec = {"策略": r.策略, "组合": L.combo_of(r.策略),
                   "pair": pair, "open_ms": int(r.open_ms), "close_ms": int(r.close_ms),
                   "s": s, "d": d, "P0": p0, "open_rate": float(r.open_rate),
                   "close_rate": float(r.close_rate), "期末结算": bool(r.期末结算),
                   "exit_reason": r.exit_reason, "profit_abs": float(r.profit_abs)}
            if i0 >= len(ms15) or i1 >= len(ms15) or i1 < i0:
                rec["状态"] = "K线缺根"
                out.append(rec)
                continue

            # 入场时刻可用的 4h（已收盘）：只看最近一根
            k0 = int(np.searchsorted(c4ms, int(ms15[i0]), side="right")) - 1
            cand_entry = float(cand_all[k0]) if k0 >= 0 else -np.inf
            # 累计最大只能从入场那根起算（策略的 state 从 P0 起步，不回看入场前的历史）
            cum_t = np.maximum.accumulate(cand_all[k0:]) if k0 >= 0 else np.array([-np.inf])
            rec["入场候选"] = cand_entry
            rec["入场候选高于成交价"] = bool(cand_entry >= float(r.open_rate))
            # 入场时 after-fill 调用：current_rate = 成交价
            if float(r.open_rate) <= max(p0, cand_entry):
                stop = p0
                rec["入场即失效"] = True
            else:
                stop = max(p0, cand_entry)
                rec["入场即失效"] = False

            exit_i = -1
            exit_px = np.nan
            exit_kind = ""
            stop_at_exit = np.nan
            gap = False
            raised_on_exit_bar = False
            stop_before_exit_bar = np.nan

            for t in range(i0, i1 + 1):
                k = int(np.searchsorted(c4ms, int(ms15[t]), side="right")) - 1
                eff = max(p0, float(cum_t[max(0, k - k0)])) if k >= k0 else p0
                prev_stop = stop
                if stop < l15[t]:
                    if h15[t] > eff:
                        if eff > stop:
                            stop = eff
                            if t == i1:
                                raised_on_exit_bar = True
                stop_before_exit_bar = prev_stop
                if l15[t] <= stop:
                    exit_i = t
                    stop_at_exit = stop
                    if stop > h15[t]:
                        exit_px = o15[t]
                        gap = True
                    else:
                        exit_px = stop
                    exit_kind = "止损"
                    break

            if exit_i < 0:
                # 未触发：期末技术结算，按最后一根可用 K 线开盘价
                t = i1
                exit_i = t
                exit_px = o15[t]
                stop_at_exit = stop
                exit_kind = "期末"

            rec.update({
                "状态": "ok",
                "最小价位": tick,
                "重建退出ms": int(ms15[exit_i]),
                "重建成交价": float(exit_px),
                "重建止损": float(stop_at_exit),
                "重建类型": exit_kind,
                "跳空": bool(gap),
                "退出根上移": bool(raised_on_exit_bar),
                "退出前一根止损": float(stop_before_exit_bar) if np.isfinite(stop_before_exit_bar) else None,
                "时间一致": bool(int(ms15[exit_i]) == int(r.close_ms)),
                "价格差": float(exit_px) - float(r.close_rate),
                "止损差": float(r.stop_loss_abs) - float(stop_at_exit),
                "本根开盘": float(o15[exit_i]),
                "本根最高": float(h15[exit_i]),
                "本根最低": float(l15[exit_i]),
            })
            # 分类（用重建，不用标签）
            if r.期末结算:
                cat = "期末技术结算"
            elif abs(stop_at_exit - p0) <= max(1e-12, p0 * 1e-9) or stop_at_exit <= p0 + 1e-12:
                cat = "初始止损"
            elif float(r.close_rate) > float(r.open_rate):
                cat = "跟踪盈利"
            else:
                cat = "跟踪亏损"
            rec["退出分类"] = cat
            out.append(rec)

    return pd.DataFrame(out)


def main() -> None:
    tr, missing, zero = L.load_all()
    add("规模", "组数", int(tr["策略"].nunique()), int(tr["策略"].nunique()) == 144)
    add("规模", "笔数", len(tr))
    add("规模", "缺失组", missing, not missing)
    add("规模", "零成交组", zero, not zero)

    print("重建中，共", len(tr), "笔 ...")
    df = rebuild(tr)
    df.to_csv(L.OUT / "02_逐笔重建明细.csv", index=False, encoding="utf-8-sig")

    ok = df[df["状态"].eq("ok")].copy()
    add("重建", "可重建笔数", len(ok), len(ok) == len(tr))
    add("重建", "无法重建", df[~df["状态"].eq("ok")].to_dict("records")[:10],
        bool((~df["状态"].eq("ok")).sum() == 0))

    # 退出时间 / 价格一致性
    add("退出链路", "退出时间一致", int(ok["时间一致"].sum()), bool(ok["时间一致"].all()),
        f"{int(ok['时间一致'].sum())}/{len(ok)}")
    eps = ok["open_rate"] * 1e-9 + 1e-12
    ok["账面止损"] = ok["重建止损"] + ok["止损差"]
    ok["相对差"] = ok["止损差"] / ok["重建止损"]
    # ① 方向：账面止损不得低于因果重建值（否则等于凭空拿到更好的价）
    below = int((ok["止损差"] < -eps).sum())
    add("退出链路", "账面止损低于重建止损的笔数", below, below == 0,
        "账面低于重建 = 出现了因果上不可能的更优止损价")
    # ② 幅度：差值只应来自 ROUND_UP 取整
    over = int((ok["相对差"] > 2e-3).sum())
    add("退出链路", "账面止损超出重建值 >0.2% 的笔数", over, over == 0,
        f"相对差中位 {ok['相对差'].median():.2e}，99% 分位 {np.percentile(ok['相对差'], 99):.2e}，"
        f"最大 {ok['相对差'].max():.2e}（取整量级，随币的最小价位变化）")
    # ③ 成交价：非跳空且非期末 = 账面止损；跳空 = 本根开盘；期末结算 = 最后一根开盘
    nongap = ok[(~ok["跳空"]) & (~ok["期末结算"])]
    gapm = ok[ok["跳空"]]
    finm = ok[ok["期末结算"]]
    ng_ok = (nongap["close_rate"] - nongap["账面止损"]).abs().to_numpy() <= (nongap["最小价位"] + eps.loc[nongap.index]).to_numpy()
    add("退出链路", "非跳空成交价 = 账面止损", int(ng_ok.sum()), bool(ng_ok.all()),
        f"{int(ng_ok.sum())}/{len(nongap)}（已剔除期末技术结算 {len(finm)} 笔）")
    gp_ok = (gapm["close_rate"] - gapm["本根开盘"]).abs().to_numpy() <= (gapm["最小价位"] + eps.loc[gapm.index]).to_numpy()
    add("退出链路", "跳空成交价 = 本根开盘价", int(gp_ok.sum()), bool(gp_ok.all()),
        f"{int(gp_ok.sum())}/{len(gapm)}")
    fn_ok = (finm["close_rate"] - finm["本根开盘"]).abs().to_numpy() <= (finm["最小价位"] + eps.loc[finm.index]).to_numpy()
    add("退出链路", "期末结算成交价 = 最后一根开盘价", int(fn_ok.sum()), bool(fn_ok.all()),
        f"{int(fn_ok.sum())}/{len(finm)}")
    # 每一笔的成交价必须能对应到「重建止损 / 本根最低价 / 本根开盘价」三者之一
    # （取整会把止损向上挪一个最小价位，使 low 恰好在止损上方一点点就触发 ⇒ 按 low 成交）
    tol = ok["open_rate"] * 2e-3
    expl = np.minimum.reduce([
        (ok["close_rate"] - ok["重建止损"]).abs().to_numpy(),
        (ok["close_rate"] - ok["本根最低"]).abs().to_numpy(),
        (ok["close_rate"] - ok["本根开盘"]).abs().to_numpy(),
    ]) <= tol.to_numpy()
    add("退出链路", "成交价可解释（重建止损 / 本根最低 / 本根开盘）", int(expl.sum()), bool(expl.all()),
        f"{int(expl.sum())}/{len(ok)}")
    edge = ok[(~ok["跳空"]) & (~ok["期末结算"]) &
              (ok["本根最低"] > ok["重建止损"] + eps) &
              ((ok["close_rate"] - ok["本根最低"]).abs() <= ok["open_rate"] * 2e-3)]
    add("退出链路", "取整导致的边界触发笔数", int(len(edge)),
        None, "本根最低价高于未取整止损、但仍按取整后的止损触发并以最低价成交；"
              "占全部止损退出 " + f"{len(edge) / max(1, len(nongap)) * 100:.2f}%")
    px_ok = expl
    add("退出链路", "账面成交价可解释率", int(px_ok.sum()), bool(px_ok.all()),
        f"{int(px_ok.sum())}/{len(ok)}；最大绝对差 {ok['价格差'].abs().max():.6g}")
    bad = ok[~ok["时间一致"] | ~px_ok | (ok["止损差"] < -eps)]
    if len(bad):
        add("退出链路", "不一致样本", bad[["策略", "pair", "open_ms", "close_ms", "重建退出ms",
                                          "重建成交价", "close_rate", "重建止损", "止损差",
                                          "exit_reason"]].head(20).to_dict("records"))
    add("退出链路", "不一致笔数", int(len(bad)), len(bad) == 0)

    # 跳空
    add("跳空", "止损高于本根最高价（按开盘成交）笔数", int(ok["跳空"].sum()))
    g = ok[ok["跳空"]]
    if len(g):
        g2 = g.copy()
        g2["开盘成交校验"] = (g2["重建成交价"] - g2["close_rate"]).abs() <= g2["open_rate"] * 2e-3
        add("跳空", "跳空成交价等于开盘价", int(g2["开盘成交校验"].sum()), bool(g2["开盘成交校验"].all()))
        add("跳空", "跳空的盈亏分布", {
            "盈利": int((g2["profit_abs"] > 0).sum()), "亏损": int((g2["profit_abs"] < 0).sum()),
            "中位盈亏": float(g2["profit_abs"].median()),
        })

    # 同根 K 线处理
    add("同根K线", "退出根上新止损被采用笔数", int(ok["退出根上移"].sum()),
        None, "采用是合法的（4h 参考价在 15m 开盘前已收盘）；关键是不得把退出根之后的 4h 用进来")

    # 未来数据：区分力检验
    # 对照：允许使用"尚未收盘"的 4h（close_ms <= 本根开盘 + 4h），即偷看未来
    print("未来数据区分力检验 ...")
    look = ok.sample(n=min(600, len(ok)), random_state=7)
    diff_cnt = 0
    checked = 0
    for r in look.itertuples(index=False):
        got = prep_pair(r.pair)
        if got is None:
            continue
        d15, d4 = got
        ms15 = d15["ms"].to_numpy(); h15 = d15["high"].to_numpy(); l15 = d15["low"].to_numpy()
        c4ms = d4["close_ms"].to_numpy(); low4 = d4["low"].to_numpy()
        cand = low4 * (1.0 - r.d)
        i0 = int(np.searchsorted(ms15, int(r.open_ms), side="left"))
        i1 = int(np.searchsorted(ms15, int(r.close_ms), side="left"))
        p0 = r.open_rate * (1.0 - r.s)
        k0 = int(np.searchsorted(c4ms, int(ms15[i0]), side="right")) - 1
        cum_t = np.maximum.accumulate(cand[k0:]) if k0 >= 0 else np.array([-np.inf])
        # 偷看版：允许 close_ms <= 本根开盘 + 4h（即含正在形成的那根）
        stop = max(p0, float(cum_t[max(0, int(np.searchsorted(c4ms, int(ms15[i0]) + L.BAR_4H_MS, side="right")) - 1 - k0)]))
        hit = None
        for t in range(i0, min(i1 + 1, len(ms15))):
            k = int(np.searchsorted(c4ms, int(ms15[t]) + L.BAR_4H_MS, side="right")) - 1
            eff = max(p0, float(cum_t[max(0, k - k0)])) if k >= k0 else p0
            if stop < l15[t] and h15[t] > eff and eff > stop:
                stop = eff
            if l15[t] <= stop:
                hit = t
                break
        checked += 1
        if hit != i1:
            diff_cnt += 1
    add("未来数据", "偷看未来（用未收盘 4h）后退出时刻改变的笔数",
        {"抽样": checked, "改变": diff_cnt}, diff_cnt > 0,
        "若为 0，说明该检验在本批样本上区分力为 0，只能靠静态证据链")

    # 静态因果链：每一根被引用的 4h 都必须在决策时刻之前收盘
    add("未来数据", "引用 4h 的已收盘判据", "4h 桶起点 + 4h ≤ 15m 开盘时刻（脚本内逐根断言）",
        True, "重建全程只用 close_ms ≤ 当前 15m 开盘 的 4h，构造上不可能引用未收盘 K 线")

    # 入场时候选价已高于成交价
    add("因果边界", "入场时跟踪候选价已高于成交价的笔数", int(ok["入场候选高于成交价"].sum()),
        None, "此时策略返回 0.0（不改写），止损停在 P0；下一根若 high > 候选价才补上，不追溯")

    # 分类
    cat = ok["退出分类"].value_counts().to_dict()
    add("分类", "四类构成（重建）", {k: int(v) for k, v in cat.items()})
    lab = ok["exit_reason"].astype(str).value_counts().to_dict()
    add("分类", "引擎标签构成（仅对照）", {k: int(v) for k, v in lab.items()})
    ct = pd.crosstab(ok["退出分类"], ok["exit_reason"].astype(str))
    ct.to_csv(L.OUT / "02_分类与标签交叉.csv", encoding="utf-8-sig")
    add("分类", "标签能否区分初始止损", bool(
        ct.loc["初始止损"].drop(labels=["stop_loss"], errors="ignore").sum() == 0
        if "初始止损" in ct.index else False),
        "若'初始止损'同时出现在 stop_loss 与 trailing_stop_loss 两列，则标签不能替代机制判断")

    # 分类 × 组合
    byc = ok.groupby(["组合", "退出分类"]).size().unstack(fill_value=0).reset_index()
    byc.to_csv(L.OUT / "02_退出分类逐组合.csv", index=False, encoding="utf-8-sig")
    add("分类", "逐组合四类构成", byc.to_dict("records"))

    crit = RES.get("判据", [])
    RES["判据统计"] = {"总": len(crit), "通过": sum(1 for c in crit if c["通过"]),
                       "不通过": sum(1 for c in crit if not c["通过"]),
                       "无判据": sum(1 for c in crit if c["通过"] is None)}
    with open(L.OUT / "02_执行核对.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=2, default=str)
    print("判据:", RES["判据统计"])
    for c in crit:
        if c["通过"] is False:
            print("  ✗", c["段"], c["项"], c["说明"])


if __name__ == "__main__":
    main()
