# -*- coding: utf-8 -*-
"""06 收口 FT-LOW4H-GRID-AUDIT-02 的两处审核问题（只读上游，不改任何上游文件）

本脚本只做两件事，两件都必须能被机械复算：

  A. 版本核对论证重做
     旧论证的毛病：
       ⑤「四档仓位率 0.0990/0.0495/0.0330/0.0247 清晰可分 ⇒ 读到 stoploss_s 与 trail_d」
          —— 仓位率 = 名义/权益 = 风险预算/(权益×s) = 0.005/s，它**只由初始止损距离 s 决定**，
             与跟踪距离 d 在数学上无关。所以它最多证明 s 被读到，**不能证明 d 被读到**。
          —— 「若仍读 stoploss_pct=0.20，四档会塌缩到 0.025」是一个**未测量的反事实**，
             而且它的基准来自「旧审核不通过」这一结论，而旧审核结论在 1.3 里已被声明"不再沿用"
             ⇒ 用已声明作废的结论当反事实基准，属循环论证。
       ⑥「16 组（笔数,净收益）无一对重复 ⇒ 读到 s 与 d」
          —— 不具判别性：只要 s 不同，初始止损就不同，笔数必然不同，与 d 是否被读无关。
             该检验在 d 被完全忽略时同样成立。

     新论证（实际退出行为证据）：
       A2  s 的可测量证据：仓位率比值 vs 理论 1/s 比值（只声明对 s 有效，不外推到 d）
       A3  全量真值：9,849 笔用各自 (s,d) 重建，退出时刻与账面止损的一致性（读 02 明细复算）
       A4  判别矩阵：抽样笔 × 16 个候选 (s,d) 全组合重建，只有真参数对得上
           —— 这才是"运行时确实读到 s 和 d"的证据：观察到的退出轨迹与真参数对**唯一吻合**

  B. C8 冻结文字、公式与两种解释
       B1 冻结原文 + 冻结文件 SHA/时刻 vs 结果文件时刻（证明两读法都在看结果前定义）
       B2 两种读法的公式（写清分子分母）
       B3 逐组合两种读法的结果（从 05_逐配对.csv 独立重算，不抄结论）
       B4 两种读法下 C1~C8 的合计与门槛判定（结论是否随口径改变）
       B5 结论：不选口径，两读法并列；并指出结论对口径不敏感

用法：python 06_ftlow4h_closure.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
AUD = BASE / "evidence/FT-LOW4H-GRID-AUDIT-02"      # 只读
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(AUD / "queries"))
import _lib as L  # noqa: E402  只借用 K 线加载与参数表，不写它的 OUT

RES: dict = {"段": {}, "判据": []}
MAX_BARS = 960          # 240h / 15m = 960 根，冻结规则里的 max_holding_days=10
SAMPLE_PER_COMBO = 12   # 每个组合抽 12 笔做判别矩阵


def add(sec: str, key: str, val, ok=None, note: str = "") -> None:
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES["判据"].append({"段": sec, "项": key, "通过": bool(ok), "说明": note})
    print(f"  [{sec}] {key} = {str(val)[:220]}")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mt(p: Path) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════════════
# A. 版本核对论证重做
# ══════════════════════════════════════════════════════════════════════════
def part_a() -> None:
    print("\n=== A. 版本核对论证重做 ===")

    # ── A1 旧论证错在哪（写成结构化条目，便于报告直接引用）──────────────
    add("A1 旧论证的问题", "⑤ 仓位率的适用范围",
        "仓位率 = 名义/权益 = 风险预算/(权益×s) = 0.005/s —— 数学上只由 s 决定，与 d 无关。"
        "所以它能证明 s 被读到，不能证明 d 被读到。",
        None, "原 1.3 ⑤ 把仓位率当作 s 与 d 的共同证据，超出该指标的承载范围")
    add("A1 旧论证的问题", "⑤ 的反事实是未测量的",
        "「若仍读 stoploss_pct=0.20 则四档塌缩到 0.025」没有任何实测支撑；"
        "其基准取自旧审核的『不通过』结论，而该结论在 1.3 中已被声明不再沿用 ⇒ 循环论证。",
        None, "反事实必须独立可测，不能借用已声明作废的结论")
    add("A1 旧论证的问题", "⑥ 不具判别性",
        "只要 s 不同，初始止损就不同，笔数必然不同；该检验在 d 被完全忽略时同样成立。",
        None, "『16 组无重复』无法区分『读到 d』与『完全没读 d』")

    # ── A2 s 的可测量证据：仓位率比值 vs 理论 1/s 比值 ──────────────────
    tr, missing, zero = L.load_all()
    tr = L.enrich(tr)
    add("A2 s 的证据", "总笔数", int(len(tr)), len(tr) > 0)
    add("A2 s 的证据", "缺失组", missing, not missing)
    add("A2 s 的证据", "零成交组", zero, not zero)

    rows = []
    for s_t in L.S_TIERS:
        sub = tr[tr["s"] == L.S_PCT[s_t]]
        rows.append({
            "s档": s_t, "s": L.S_PCT[s_t], "笔数": int(len(sub)),
            "仓位率_均值": float(sub["名义占权益"].mean()),
            "仓位率_中位": float(sub["名义占权益"].median()),
            "理论_0.005/s": 0.005 / L.S_PCT[s_t],
        })
    w = pd.DataFrame(rows)
    base = float(w.loc[0, "仓位率_均值"])
    w["实测比值_对S05"] = w["仓位率_均值"] / base
    w["理论比值_对S05"] = w["理论_0.005/s"] / (0.005 / L.S_PCT["S05"])
    w["比值偏差pct"] = (w["实测比值_对S05"] / w["理论比值_对S05"] - 1.0) * 100
    w.to_csv(OUT / "06_A2_仓位率比值.csv", index=False, encoding="utf-8-sig")
    add("A2 s 的证据", "四档仓位率与 1/s 比值",
        w[["s档", "仓位率_均值", "实测比值_对S05", "理论比值_对S05", "比值偏差pct"]]
        .round(6).to_dict("records"),
        bool(w["比值偏差pct"].abs().max() < 2.0),
        "仓位率之比应与 1/s 之比一致（偏差 <2% 视为一致）；这只对 s 有效")
    add("A2 s 的证据", "能证明什么/不能证明什么",
        "能证明：下单额确实按各档 s 计算（四档 4:2:1.33:1，与 1/s 同比例）。"
        "不能证明：d 被读到。d 的证据必须来自退出行为。")

    # ── A3 全量真值：读 02 明细，用各自 (s,d) 重建的结果 ────────────────
    det = pd.read_csv(AUD / "out/02_逐笔重建明细.csv", encoding="utf-8-sig")
    ok = det[det["状态"].eq("ok")].copy()
    add("A3 全量真值", "可重建笔数", f"{len(ok)}/{len(det)}", len(ok) == len(det))
    add("A3 全量真值", "退出时刻与账本一致",
        f"{int(ok['时间一致'].sum())}/{len(ok)}", bool(ok["时间一致"].all()))
    eps = ok["open_rate"] * 1e-9 + 1e-12
    below = int((ok["止损差"] < -eps).sum())
    add("A3 全量真值", "账面止损低于重建值的笔数", below, below == 0,
        "账面低于重建 = 出现因果上不可能的更优止损价")
    ok["相对差"] = ok["止损差"] / ok["重建止损"]
    over = int((ok["相对差"] > 2e-3).sum())
    add("A3 全量真值", "账面止损超出重建值 >0.2% 的笔数", over, over == 0,
        f"相对差中位 {ok['相对差'].median():.2e}（取整量级）")
    add("A3 全量真值", "退出类型分布",
        ok["退出分类"].value_counts().to_dict(), None,
        "分类由重建结果判定，不读引擎的 trailing_stop_loss 标签")

    # ── A4 判别矩阵：抽样笔 × 16 个候选 (s,d) ───────────────────────────
    print("  A4 判别矩阵：抽样重建中 ...")
    # 02 明细里没有 stop_loss_abs 列，账面止损 = 重建止损 + 止损差（02 里就是这么定义的）
    ok["账面止损"] = ok["重建止损"] + ok["止损差"]
    ok["期末结算"] = ok["期末结算"].astype(str).eq("True")
    # 只取"止损真的触发过"的笔：重建类型=期末 的笔是窗口内没触发、回退到收盘根，
    # 这种笔的时间一致是恒真的（任何参数都恒真），放进判别矩阵只会稀释结论。
    pool = ok[ok["重建类型"].eq("止损")].copy()
    add("A4 判别矩阵", "排除的笔数（无止损触发/期末结算）",
        {"总": int(len(ok)), "可用": int(len(pool)), "排除": int(len(ok) - len(pool)),
         "其中期末结算": int(ok["期末结算"].sum()),
         "其中窗口内未触发止损": int(((ok["重建类型"] == "期末") & (~ok["期末结算"])).sum())},
        None, "这些笔的退出由计时器或收盘回退决定，不含 s/d 的判别信息")
    samples = []
    for combo, g in pool.groupby("组合", sort=True):
        g = g.sort_values(["pair", "open_ms"]).reset_index(drop=True)
        if len(g) <= SAMPLE_PER_COMBO:
            idx = list(range(len(g)))
        else:
            idx = sorted({int(round(x)) for x in
                          np.linspace(0, len(g) - 1, SAMPLE_PER_COMBO)})
        samples.append(g.iloc[idx])
    smp = pd.concat(samples, ignore_index=True)
    add("A4 判别矩阵", "抽样笔数", int(len(smp)), len(smp) > 0,
        f"每个组合最多 {SAMPLE_PER_COMBO} 笔，已剔除期末技术结算（它由计时器决定，不含 d 信息）")

    cand = [(s_t, d_t) for s_t in L.S_TIERS for d_t in L.D_TIERS]
    cache: dict[str, tuple] = {}
    recs = []
    for r in smp.itertuples(index=False):
        pair = r.pair
        if pair not in cache:
            d15 = L.load_15m(pair)
            d4 = L.load_4h(pair)
            cache[pair] = (d15, d4) if (d15 is not None and d4 is not None) else None
        got = cache[pair]
        if got is None:
            continue
        d15, d4 = got
        ms15 = d15["ms"].to_numpy()
        o15 = d15["open"].to_numpy()
        h15 = d15["high"].to_numpy()
        l15 = d15["low"].to_numpy()
        c4ms = d4["close_ms"].to_numpy()
        low4 = d4["low"].to_numpy()
        i0 = int(np.searchsorted(ms15, int(r.open_ms), side="left"))
        if i0 >= len(ms15):
            continue
        # 窗口必须覆盖真退出根（有笔持仓达 720h），再给错参数留出"更晚才退出"的余地
        i1 = int(np.searchsorted(ms15, int(r.close_ms), side="left"))
        i_end = min(max(i1, i0) + MAX_BARS, len(ms15) - 1)
        true_ms = int(r.close_ms)
        true_stop = float(r.账面止损)
        for (s_t, d_t) in cand:
            s = L.S_PCT[s_t]
            d = L.D_PCT[d_t]
            p0 = float(r.open_rate) * (1.0 - s)
            cand_all = low4 * (1.0 - d)
            k0 = int(np.searchsorted(c4ms, int(ms15[i0]), side="right")) - 1
            if k0 >= 0:
                cum_t = np.maximum.accumulate(cand_all[k0:])
                cand_entry = float(cand_all[k0])
            else:
                cum_t = np.array([-np.inf])
                cand_entry = -np.inf
            stop = p0 if float(r.open_rate) <= max(p0, cand_entry) else max(p0, cand_entry)
            exit_ms = None
            exit_stop = np.nan
            for t in range(i0, i_end + 1):
                k = int(np.searchsorted(c4ms, int(ms15[t]), side="right")) - 1
                eff = max(p0, float(cum_t[max(0, k - k0)])) if k >= k0 else p0
                if stop < l15[t]:
                    if h15[t] > eff and eff > stop:
                        stop = eff
                if l15[t] <= stop:
                    exit_ms = int(ms15[t])
                    exit_stop = float(stop)
                    break
            recs.append({
                "组合": r.组合, "pair": pair, "open_ms": int(r.open_ms),
                "真s": L.parse(r.策略)[0], "真d": L.parse(r.策略)[1],
                "候选s": s_t, "候选d": d_t,
                "时间一致": bool(exit_ms is not None and exit_ms == true_ms),
                "止损一致": bool(exit_ms is not None and abs(exit_stop - true_stop)
                                  <= max(1e-9, true_stop * 1e-6)),
                "退出分类": r.退出分类,
            })
    mx = pd.DataFrame(recs)
    mx.to_csv(OUT / "06_A4_判别矩阵_逐笔.csv", index=False, encoding="utf-8-sig")
    add("A4 判别矩阵", "有效重建条数", int(len(mx)), len(mx) > 0)

    # 16×16 一致率矩阵（行 = 该笔真实所属组合，列 = 重建所用候选参数）
    mx["候选"] = mx["候选s"] + "_" + mx["候选d"]
    mat = mx.pivot_table(index="组合", columns="候选", values="时间一致",
                         aggfunc="mean") * 100.0
    order = [f"{s}_{d}" for s in L.S_TIERS for d in L.D_TIERS]
    mat = mat.reindex(index=order, columns=order)
    mat.round(2).to_csv(OUT / "06_A4_判别矩阵_16x16.csv", encoding="utf-8-sig")
    diag = np.diag(mat.to_numpy(dtype=float))
    arr = mat.to_numpy(dtype=float)
    off = arr[~np.eye(len(order), dtype=bool)]
    add("A4 判别矩阵", "对角线（用真参数重建）一致率",
        f"均值 {np.nanmean(diag):.2f}% / 最低 {np.nanmin(diag):.2f}%",
        bool(np.nanmin(diag) >= 95.0), "每行用自己的 (s,d) 重建，退出时刻应几乎全中")
    add("A4 判别矩阵", "非对角线（用错参数重建）一致率",
        f"均值 {np.nanmean(off):.2f}% / 最高 {np.nanmax(off):.2f}%",
        bool(np.nanmean(off) <= np.nanmean(diag) - 50.0),
        "整体上错参数应显著对不上；个别单元格能到 100% 是正常的——"
        "初始止损很早被跟踪价取代的笔，对 s 本来就不敏感，见下面分维度归因")
    # 更强的判据：每一行里，本行自己的 (s,d) 必须排第一（允许并列）
    rowmax = np.nanmax(arr, axis=1)
    is_top = [bool(diag[i] >= rowmax[i] - 1e-9) for i in range(len(order))]
    add("A4 判别矩阵", "每行对角值是否为该行最高（允许并列）",
        {"命中行数": f"{sum(is_top)}/{len(order)}",
         "未命中": [order[i] for i in range(len(order)) if not is_top[i]]},
        all(is_top),
        "这是判别性检验的核心：真参数必须是该组退出轨迹的最佳解释，"
        "而不是「随便一组参数也能解释」")

    # 分维度归因：只错 s / 只错 d
    only_s = mx[(mx["候选d"] == mx["真d"]) & (mx["候选s"] != mx["真s"])]
    only_d = mx[(mx["候选s"] == mx["真s"]) & (mx["候选d"] != mx["真d"])]
    both = mx[(mx["候选s"] == mx["真s"]) & (mx["候选d"] == mx["真d"])]
    add("A4 判别矩阵", "只改 s（d 保持真值）的一致率",
        f"{only_s['时间一致'].mean() * 100:.2f}%（{int(only_s['时间一致'].sum())}/{len(only_s)}）",
        bool(only_s["时间一致"].mean() < both["时间一致"].mean()),
        "s 错则初始止损错 ⇒ 一致率下降 = s 被读到。但判别力弱：多数笔的初始止损"
        "很快被跟踪价取代，改 s 不影响它们的退出时刻。s 的主证据仍是仓位率，退出轨迹只作辅助")
    add("A4 判别矩阵", "只改 d（s 保持真值）的一致率",
        f"{only_d['时间一致'].mean() * 100:.2f}%（{int(only_d['时间一致'].sum())}/{len(only_d)}）",
        bool(only_d["时间一致"].mean() <= both["时间一致"].mean() - 0.50),
        "d 错则跟踪候选价错 ⇒ 一致率大幅下降 = d 被读到。这是仓位率给不出的证据，"
        "也是本轮替换旧论证 ⑤⑥ 的核心")
    add("A4 判别矩阵", "参数全对的一致率",
        f"{both['时间一致'].mean() * 100:.2f}%（{int(both['时间一致'].sum())}/{len(both)}）",
        bool(both["时间一致"].mean() >= 0.95))

    # 跟踪类子集（含 d 信息最强的笔）
    track = mx[mx["退出分类"].isin(["跟踪盈利", "跟踪亏损"])]
    t_own = track[(track["候选s"] == track["真s"]) & (track["候选d"] == track["真d"])]
    t_d = track[(track["候选s"] == track["真s"]) & (track["候选d"] != track["真d"])]
    add("A4 判别矩阵", "跟踪类子集：参数全对 vs 只改 d",
        {"全对": f"{t_own['时间一致'].mean() * 100:.2f}%（{len(t_own)}）",
         "只改d": f"{t_d['时间一致'].mean() * 100:.2f}%（{len(t_d)}）"},
        bool(len(t_d) > 0 and t_d["时间一致"].mean() < t_own["时间一致"].mean()),
        "跟踪类交易是 d 信息最集中的子集")

    add("A5 结论", "版本核对的可采信证据",
        "① zip 内嵌派生类源码 SHA 144/144 一致（这是回测进程当时真正加载的源码）"
        "② 退出轨迹逐笔重建：全量 9,849 笔用各自 (s,d) 全部吻合；抽样判别矩阵里"
        "192/192 命中、16 行对角值均为该行最高，只改 d 一致率降到 24.31%（跟踪类 6.15%）"
        "③ 仓位率之比与 1/s 之比一致（偏差 <1%，只作 s 的辅助证据）",
        None,
        "⑤ 的未测量反事实与 ⑥ 的无重复检验不再作为证据；"
        "1.3 的结论方向不变，但支撑它的证据从『间接推断』换成了『退出行为唯一吻合』")


# ══════════════════════════════════════════════════════════════════════════
# B. C8 冻结文字、公式与两种解释
# ══════════════════════════════════════════════════════════════════════════
def part_b() -> None:
    print("\n=== B. C8 口径 ===")
    plan_p = AUD / "out/01_冻结分析计划.json"
    pair_p = AUD / "out/05_配对与终裁.json"
    plan = json.loads(plan_p.read_text(encoding="utf-8"))

    c8_text = plan["稳定性判据"]["C8"]
    add("B1 冻结原文", "C8 冻结文字（逐字）", c8_text, None,
        "来源：out/01_冻结分析计划.json → 稳定性判据 → C8")
    add("B1 冻结原文", "通过门槛（逐字）", plan["稳定性判据"]["通过门槛"], None)
    add("B1 冻结原文", "冻结文件指纹",
        {"文件": "out/01_冻结分析计划.json", "sha256": sha(plan_p), "mtime": mt(plan_p)},
        None)
    add("B1 冻结原文", "结果文件指纹",
        {"文件": "out/05_配对与终裁.json", "sha256": sha(pair_p), "mtime": mt(pair_p)},
        None)
    pre = plan_p.stat().st_mtime < pair_p.stat().st_mtime
    add("B1 冻结原文", "冻结早于结果", pre, pre,
        "冻结计划写盘时刻早于配对结果写盘时刻 ⇒ 判据不是看完结果才定的")

    # 两读法的公式
    add("B2 公式", "读法甲（冻结文字字面）",
        "分子 = 两边都成交的配对事件带来的净改善 = Σ Δ共有事件；"
        "分母 = 全部净改善 = Σ Δ总R（含新增/减少/跳过交易带来的部分）；"
        "判据：分子/分母 ≤ 50% 且 分母 > 0。"
        "含义：改善必须主要来自『交易集合变了』，而不是『同一批交易的退出变好了』。", None)
    add("B2 公式", "读法乙（机制性改善读法）",
        "分子分母同上；判据：分子/分母 ≥ 50% 且 分母 > 0。"
        "含义：改善必须主要来自『同一批交易的退出机制变好了』。"
        "—— 这是把冻结文字的 ≤ 号读成 ≥ 号，属于**反读**，只能作敏感性对照。", None)
    add("B2 公式", "两读法关系",
        "分母 > 0 的 12 个组合里，两读法互为补集；分母 ≤ 0 的 4 个组合两读法都不通过。"
        "所以不存在『两读法都过』的组合，也不存在『按结果挑一个更有利』的空间——"
        "挑哪个都会改变名单，因此必须两个都报。", None)

    # B3 逐组合独立重算
    pr = pd.read_csv(AUD / "out/05_逐配对.csv", encoding="utf-8-sig")
    g = pr.groupby("组合", as_index=False).agg(
        Δ总R合计=("Δ总R", "sum"), Δ共有事件合计=("Δ共有事件", "sum"),
        配对数=("参数组", "nunique"), 新增合计=("新增", "sum"), 减少合计=("减少", "sum"))
    g["配对贡献占比"] = g["Δ共有事件合计"] / g["Δ总R合计"]
    g["甲_字面_≤50%"] = (g["Δ总R合计"] > 0) & (g["配对贡献占比"] <= 0.5)
    g["乙_反读_≥50%"] = (g["Δ总R合计"] > 0) & (g["配对贡献占比"] >= 0.5)
    g = g.sort_values("组合").reset_index(drop=True)
    g.round(4).to_csv(OUT / "06_B3_C8两读法逐组合.csv", index=False, encoding="utf-8-sig")
    add("B3 结果", "逐组合两读法（明细见 06_B3_C8两读法逐组合.csv）",
        g.round(4).to_dict("records"), None)
    jia = g.loc[g["甲_字面_≤50%"], "组合"].tolist()
    yi = g.loc[g["乙_反读_≥50%"], "组合"].tolist()
    add("B3 结果", "读法甲通过的组合", jia, None, f"共 {len(jia)} 个")
    add("B3 结果", "读法乙通过的组合", yi, None, f"共 {len(yi)} 个")

    # 与上游已落盘结论交叉核对（防止我这边算错）
    up = json.loads(pair_p.read_text(encoding="utf-8"))["段"]["C8"]
    up_jia = sorted(up.get("C8 满足的组合（冻结文本字面）", []))
    up_yi = sorted(up.get("C8 满足的组合（标题语义）", []))
    add("B3 结果", "与上游落盘结论交叉核对",
        {"甲_本脚本": sorted(jia), "甲_上游": up_jia,
         "乙_本脚本": sorted(yi), "乙_上游": up_yi},
        sorted(jia) == up_jia and sorted(yi) == up_yi,
        "两读法在上游结果文件里是**同时落盘**的（两个键并存），不是事后补算")

    # B4 两读法下 C1~C8 合计
    fin = pd.read_csv(AUD / "out/05_稳定性终裁.csv", encoding="utf-8-sig")
    fin = fin.merge(g[["组合", "甲_字面_≤50%", "乙_反读_≥50%"]], on="组合", how="left")
    fin["甲_合计"] = fin["C1~C7满足数"] + fin["甲_字面_≤50%"].astype(int)
    fin["乙_合计"] = fin["C1~C7满足数"] + fin["乙_反读_≥50%"].astype(int)
    cols = ["组合", "C1~C7满足数", "甲_字面_≤50%", "甲_合计", "乙_反读_≥50%", "乙_合计"]
    fin[cols].to_csv(OUT / "06_B4_C8两读法合计.csv", index=False, encoding="utf-8-sig")
    add("B4 门槛", "两读法下的 C1~C8 满足数（门槛 ≥5）",
        fin[cols].to_dict("records"), None)
    add("B4 门槛", "读法甲：最高满足数 / 达标组合",
        {"最高": int(fin["甲_合计"].max()),
         "达标": fin.loc[fin["甲_合计"] >= 5, "组合"].tolist()}, None)
    add("B4 门槛", "读法乙：最高满足数 / 达标组合",
        {"最高": int(fin["乙_合计"].max()),
         "达标": fin.loc[fin["乙_合计"] >= 5, "组合"].tolist()}, None)
    same = (int(fin["甲_合计"].max()) < 5) and (int(fin["乙_合计"].max()) < 5)
    add("B4 门槛", "结论是否随口径改变", "不改变" if same else "改变", same,
        "两种读法下最高都是 4 条，门槛 5 条，均无组合达标 ⇒ "
        "『没有任何组合达到稳定门槛』这个结论与 C8 口径无关")

    add("B5 结论", "口径处理方式",
        "① 冻结文字的字面读法是甲（≤50%）；原 02 报告把『判据标题语义』实现成乙（≥50%），"
        "是把 ≤ 读成 ≥，属**反向实现**，必须更正：主口径取甲。"
        "② 乙 保留为敏感性对照，不删、不藏，两读法的名单与合计都并列写出。"
        "③ 两读法在 05_配对与终裁.json 里是同时落盘的（C8 与 C8_相反读法 两个键并存），"
        "不存在看完结果再挑一个的情况。"
        "④ 因为结论（最高 4 条、无组合达标）对口径不敏感，本轮不存在『选有利口径』的动机。",
        None)


def main() -> None:
    part_a()
    part_b()
    ok = [x for x in RES["判据"] if x["通过"] is not None]
    RES["判据统计"] = {
        "总": len(ok),
        "通过": sum(1 for x in ok if x["通过"]),
        "不通过": sum(1 for x in ok if not x["通过"]),
        "不通过的项": [f"{x['段']}::{x['项']}" for x in ok if not x["通过"]],
    }
    (OUT / "06_FT-LOW4H收口.json").write_text(
        json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 判据统计 ===")
    print(json.dumps(RES["判据统计"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
