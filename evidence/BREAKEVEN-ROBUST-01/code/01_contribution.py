#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01_contribution.py —— BREAKEVEN-ROBUST-01 收益来源与集中度分析

做什么
----
只读 `evidence/BREAKEVEN-01/out/paired_trades.csv`（206 个候选级配对记录），
按与 BREAKEVEN-01 的 `06_dedup_sensitivity.py` **完全相同**的规则
（同 symbol + 同 entry_time_utc，保留 run_idx 最小者）收成 **201 个事件级样本**，
然后：

1. 逐笔列出所有相对 A 发生变化的交易（B=1R 保本 / C=2R 保本），
   核对「少亏 / 错失盈利 / 净差额」三项对账；
2. 与 BREAKEVEN-01 已公布的候选级、事件级数字交叉核对（防止本单自己算错）；
3. 集中度：正负贡献的最大值占比、top-k 占比、赫希曼指数（平方份额和）；
4. 移除情景：去掉最大正贡献 / 最大负贡献 / 二者同时去掉 / 2R 那笔唯一增益；
5. 刀切法（leave-one-out）：逐个移除发生变化的事件，看 B/C 相对 A 的差额是否变号；
6. 分期分档：按 A 的最大浮盈（MFE）分档、按入场年份、按是否触发保本。

明确不做的事
----------
- 不重跑撮合、不改任何退出点、不试新参数（1R/2R 规则与仓位口径全部沿用 BREAKEVEN-01）；
- 不使用退出后的走势重新选择退出点：本单所有输入都是 BREAKEVEN-01 已经用
  「退出前可见数据」实现出来的 A/B/C 成交记录；
  MFE 分档只做事后描述性归类，不参与任何退出决策。
"""
from __future__ import annotations

import collections
import csv
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/BREAKEVEN-01/out/paired_trades.csv")
REF = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/BREAKEVEN-01/out/dedup_sensitivity.json")
OUT = HERE.parent / "out"
TOL = 1e-9  # 「发生变化」的判定阈值：逐位相同即 0


def f(row: dict, key: str) -> float:
    return float(row[key])


def stats(rows: list[dict]) -> dict:
    n = len(rows)
    A = sum(f(r, "A_net") for r in rows)
    B = sum(f(r, "B_net") for r in rows)
    C = sum(f(r, "C_net") for r in rows)
    b_deltas = [f(r, "B_net") - f(r, "A_net") for r in rows]
    c_deltas = [f(r, "C_net") - f(r, "A_net") for r in rows]
    b_saved = sum(d for d in b_deltas if d > TOL)
    b_cost = sum(-d for d in b_deltas if d < -TOL)
    c_saved = sum(d for d in c_deltas if d > TOL)
    c_cost = sum(-d for d in c_deltas if d < -TOL)
    return {
        "n": n,
        "A_net": A,
        "B_net": B,
        "C_net": C,
        "B_delta": B - A,
        "C_delta": C - A,
        "B_changed": sum(1 for d in b_deltas if abs(d) > TOL),
        "C_changed": sum(1 for d in c_deltas if abs(d) > TOL),
        "B_saved": b_saved,
        "B_cost": b_cost,
        "C_saved": c_saved,
        "C_cost": c_cost,
        "B_better": sum(1 for d in b_deltas if d > TOL),
        "B_worse": sum(1 for d in b_deltas if d < -TOL),
        "C_better": sum(1 for d in c_deltas if d > TOL),
        "C_worse": sum(1 for d in c_deltas if d < -TOL),
        "win_rate_A": 100.0 * sum(1 for r in rows if f(r, "A_net") > 0) / n,
        "win_rate_B": 100.0 * sum(1 for r in rows if f(r, "B_net") > 0) / n,
        "win_rate_C": 100.0 * sum(1 for r in rows if f(r, "C_net") > 0) / n,
        "mean_A": A / n,
        "mean_B": B / n,
        "mean_C": C / n,
    }


def t_stat(values: list[float]) -> dict:
    n = len(values)
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    sd = math.sqrt(var)
    return {"mean": m, "sd": sd, "t": m / (sd / math.sqrt(n)) if sd > 0 else None}


def sign_test(k_pos: int, n: int) -> float:
    """双侧符号检验 p 值（H0: 正负各半）。"""
    lo = min(k_pos, n - k_pos)
    tail = sum(math.comb(n, i) for i in range(0, lo + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def load_events() -> tuple[list[dict], list[dict]]:
    with SRC.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 206, f"预期 206 行候选级记录，实际 {len(rows)}"
    assert len({r["signal_id"] for r in rows}) == 206, "signal_id 不唯一"
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        groups[(r["symbol"], r["entry_time_utc"])].append(r)
    keep, dropped = [], []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda r: int(r["run_idx"]))
        keep.append(members[0])
        dropped.extend(members[1:])
    assert len(keep) == 201, f"预期事件级 201 行，实际 {len(keep)}"
    return keep, dropped


def changed_list(rows: list[dict], grp: str) -> list[dict]:
    out = []
    for r in rows:
        d = f(r, f"{grp}_net") - f(r, "A_net")
        if abs(d) > TOL:
            out.append({
                "symbol": r["symbol"],
                "entry_time_utc": r["entry_time_utc"],
                "signal_id": r["signal_id"],
                "direction": "多" if r["direction"] == "1" else "空",
                "A_net": round(f(r, "A_net"), 4),
                "B_net" if grp == "B" else "C_net": round(f(r, f"{grp}_net"), 4),
                "delta": round(d, 4),
                "kind": "少亏" if d > 0 else "错失盈利",
                "A_reason": r["A_reason"],
                "B_reason" if grp == "B" else "C_reason": r[f"{grp}_reason"],
                "A_exit_time_utc": r["A_exit_time_utc"],
                "B_exit_time_utc" if grp == "B" else "C_exit_time_utc": r[f"{grp}_exit_time_utc"],
                "B_trigger_utc" if grp == "B" else "C_trigger_utc": r[f"{grp}_trigger_utc"],
                "B_be_px" if grp == "B" else "C_be_px": r[f"{grp}_be_px"],
                "A_mfe_R": round(f(r, "A_mfe_R"), 3),
                "A_hold_h": round(f(r, "A_hold_h"), 1),
                "B_hold_h" if grp == "B" else "C_hold_h": round(f(r, f"{grp}_hold_h"), 1),
            })
    out.sort(key=lambda x: x["delta"])
    return out


def write_changed_csv(items: list[dict], path: Path, grp: str) -> None:
    cols = ["symbol", "entry_time_utc", "signal_id", "direction", "A_net", f"{grp}_net", "delta",
            "kind", "A_reason", f"{grp}_reason", "A_exit_time_utc", f"{grp}_exit_time_utc",
            f"{grp}_trigger_utc", f"{grp}_be_px", "A_mfe_R", "A_hold_h", f"{grp}_hold_h"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for it in items:
            w.writerow({c: it.get(c, "") for c in cols})


def concentration(deltas: list[float]) -> dict:
    """正负贡献集中度：只看发生变化的交易的 delta。"""
    pos = [d for d in deltas if d > TOL]
    neg = [d for d in deltas if d < -TOL]
    absd = [abs(d) for d in deltas if abs(d) > TOL]
    total_abs = sum(absd)
    srt = sorted(absd, reverse=True)

    def topk_share(k: int) -> float:
        return 100.0 * sum(srt[:k]) / total_abs if total_abs > 0 else 0.0

    def side_conc(vals: list[float]) -> dict:
        s = sorted([abs(v) for v in vals], reverse=True)
        tot = sum(s)
        if tot <= 0:
            return {"n": len(vals), "sum": 0.0, "top1_share": 0.0, "top3_share": 0.0,
                    "top1": 0.0, "hhi": 0.0}
        shares = [v / tot for v in s]
        return {
            "n": len(vals),
            "sum": round(tot, 4),
            "top1": round(s[0], 4),
            "top1_share": round(100.0 * shares[0], 2),
            "top3_share": round(100.0 * sum(shares[:3]), 2),
            "top5_share": round(100.0 * sum(shares[:5]), 2),
            "hhi": round(sum(sh * sh for sh in shares), 4),
        }

    return {
        "n_changed": len(pos) + len(neg),
        "gross_positive": round(sum(pos), 4),
        "gross_negative": round(sum(neg), 4),
        "gross_abs_total": round(total_abs, 4),
        "top1_share_of_gross": round(topk_share(1), 2),
        "top3_share_of_gross": round(topk_share(3), 2),
        "top5_share_of_gross": round(topk_share(5), 2),
        "hhi_of_gross": round(sum((d / total_abs) ** 2 for d in absd), 4) if total_abs else 0.0,
        "positive_side": side_conc(pos),
        "negative_side": side_conc(neg),
    }


def remove_scenario(rows: list[dict], drop_keys: set[tuple[str, str]], grp: str) -> dict:
    sub = [r for r in rows if (r["symbol"], r["entry_time_utc"]) not in drop_keys]
    s = stats(sub)
    return {
        "removed": [f"{k[0]}@{k[1]}" for k in sorted(drop_keys)],
        "n": s["n"],
        "A_net": round(s["A_net"], 4),
        "B_net": round(s["B_net"], 4),
        "C_net": round(s["C_net"], 4),
        "B_delta": round(s["B_delta"], 4),
        "C_delta": round(s["C_delta"], 4),
        "win_rate_A": round(s["win_rate_A"], 2),
        "win_rate_B": round(s["win_rate_B"], 2),
        "win_rate_C": round(s["win_rate_C"], 2),
    }


def jackknife(rows: list[dict], changed_keys: list[tuple[str, str]], grp: str) -> list[dict]:
    out = []
    for key in changed_keys:
        sub = [r for r in rows if (r["symbol"], r["entry_time_utc"]) != key]
        s = stats(sub)
        out.append({
            "symbol": key[0],
            "entry_time_utc": key[1],
            "A_net": round(s["A_net"], 4),
            "B_net": round(s["B_net"], 4),
            "C_net": round(s["C_net"], 4),
            "B_delta": round(s["B_delta"], 4),
            "C_delta": round(s["C_delta"], 4),
        })
    return out


def buckets(rows: list[dict]) -> dict:
    def bucket_of(mfe: float) -> str:
        if mfe < 1.0:
            return "0~1R"
        if mfe < 2.0:
            return "1~2R"
        if mfe < 3.0:
            return "2~3R"
        return ">=3R"

    def agg(sub: list[dict]) -> dict:
        s = stats(sub)
        return {
            "n": s["n"],
            "A_net": round(s["A_net"], 2),
            "B_net": round(s["B_net"], 2),
            "C_net": round(s["C_net"], 2),
            "B_delta": round(s["B_delta"], 4),
            "C_delta": round(s["C_delta"], 4),
            "B_changed": s["B_changed"],
            "B_saved": round(s["B_saved"], 2),
            "B_cost": round(s["B_cost"], 2),
            "C_saved": round(s["C_saved"], 2),
            "C_changed": s["C_changed"],
        }

    res: dict = {}
    mfe_order = ["0~1R", "1~2R", "2~3R", ">=3R"]
    mfe_groups = collections.defaultdict(list)
    for r in rows:
        mfe_groups[bucket_of(f(r, "A_mfe_R"))].append(r)
    res["by_mfe"] = {k: agg(mfe_groups[k]) for k in mfe_order if k in mfe_groups}

    year_groups = collections.defaultdict(list)
    for r in rows:
        year_groups[r["entry_time_utc"][:4]].append(r)
    res["by_year"] = {k: agg(year_groups[k]) for k in sorted(year_groups)}

    trig_groups = {"B_触发保本": [r for r in rows if r["B_be_triggered"] == "True"],
                   "B_未触发": [r for r in rows if r["B_be_triggered"] != "True"],
                   "C_触发保本": [r for r in rows if r["C_be_triggered"] == "True"],
                   "C_未触发": [r for r in rows if r["C_be_triggered"] != "True"]}
    res["by_trigger"] = {k: agg(v) for k, v in trig_groups.items()}

    # 发生变化的交易再按 MFE 分档（贡献集中度的时间/走势结构）
    chg = collections.defaultdict(list)
    for r in rows:
        d = f(r, "B_net") - f(r, "A_net")
        if abs(d) > TOL:
            chg[bucket_of(f(r, "A_mfe_R"))].append(d)
    res["changed_B_by_mfe"] = {
        k: {"n": len(v),
            "pos_n": sum(1 for x in v if x > 0),
            "pos_sum": round(sum(x for x in v if x > 0), 4),
            "neg_n": sum(1 for x in v if x < 0),
            "neg_sum": round(sum(x for x in v if x < 0), 4),
            "net": round(sum(v), 4)}
        for k in mfe_order if k in chg for v in [chg[k]]}
    return res


def group_profile(rows: list[dict], grp: str) -> dict:
    """两组（少亏组 / 错失组）的画像：笔数、A/B 合计、持有时间。"""
    d_key = f"{grp}_net"
    better = [r for r in rows if f(r, d_key) - f(r, "A_net") > TOL]
    worse = [r for r in rows if f(r, d_key) - f(r, "A_net") < -TOL]

    def prof(sub: list[dict], label: str) -> dict:
        if not sub:
            return {"label": label, "n": 0}
        return {
            "label": label,
            "n": len(sub),
            "A_sum": round(sum(f(r, "A_net") for r in sub), 4),
            "grp_sum": round(sum(f(r, d_key) for r in sub), 4),
            "delta_sum": round(sum(f(r, d_key) - f(r, "A_net") for r in sub), 4),
            "delta_mean": round(sum(f(r, d_key) - f(r, "A_net") for r in sub) / len(sub), 4),
            "delta_max": round(max(f(r, d_key) - f(r, "A_net") for r in sub), 4),
            "delta_min": round(min(f(r, d_key) - f(r, "A_net") for r in sub), 4),
            "A_hold_mean_h": round(sum(f(r, "A_hold_h") for r in sub) / len(sub), 1),
            "grp_hold_mean_h": round(sum(f(r, f"{grp}_hold_h") for r in sub) / len(sub), 1),
        }

    return {"better": prof(better, "少亏"), "worse": prof(worse, "错失盈利")}


def a_side_concentration(rows: list[dict]) -> dict:
    """A 基准自身的集中度（说明样本利润主要由少数交易贡献，与保本无关的背景事实）。"""
    pos = sorted([f(r, "A_net") for r in rows if f(r, "A_net") > 0], reverse=True)
    tot = sum(pos)
    srt = sorted([abs(f(r, "A_net")) for r in rows], reverse=True)
    return {
        "positive_n": len(pos),
        "positive_sum": round(tot, 4),
        "top1_trade": round(pos[0], 4),
        "top1_share_of_positive": round(100.0 * pos[0] / tot, 2),
        "top3_share_of_positive": round(100.0 * sum(pos[:3]) / tot, 2),
        "top1_share_of_net": round(100.0 * pos[0] / sum(f(r, "A_net") for r in rows), 2),
        "top1_trade_abs_share_of_all": round(100.0 * srt[0] / sum(srt), 2),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    events, dropped = load_events()

    # ---------- 1. 与 BREAKEVEN-01 已公布数字交叉核对 ----------
    ref = json.loads(REF.read_text(encoding="utf-8"))
    el = ref["event_level"]
    cl = ref["candidate_level"]
    with SRC.open(encoding="utf-8-sig", newline="") as fh:
        cand_rows = list(csv.DictReader(fh))
    s_ev, s_cd = stats(events), stats(cand_rows)
    checks = [
        ("事件级样本量", s_ev["n"], el["n"]),
        ("事件级 A 净额", round(s_ev["A_net"], 4), el["A_net"]),
        ("事件级 B 净额", round(s_ev["B_net"], 4), el["B_net"]),
        ("事件级 C 净额", round(s_ev["C_net"], 4), el["C_net"]),
        ("事件级 B 差额", round(s_ev["B_delta"], 4), el["B_delta"]),
        ("事件级 C 差额", round(s_ev["C_delta"], 4), el["C_delta"]),
        ("事件级 B 少亏", round(s_ev["B_saved"], 2), el["B_saved_loss"]),
        ("事件级 B 代价", round(s_ev["B_cost"], 2), el["B_cost_forgone"]),
        ("事件级 C 少亏", round(s_ev["C_saved"], 2), el["C_saved_loss"]),
        ("候选级 B 少亏", round(s_cd["B_saved"], 2), cl["B_saved_loss"]),
        ("候选级 B 代价", round(s_cd["B_cost"], 2), cl["B_cost_forgone"]),
        ("剔重行数", len(dropped), ref["duplicate_groups"]),
    ]
    cross_check = [{"项": n, "本单重算": a, "BREAKEVEN-01 公布": b, "一致": abs(a - b) < 5e-4}
                   for n, a, b in checks]
    assert all(c["一致"] for c in cross_check), f"交叉核对失败：{cross_check}"

    # 三项对账：少亏 − 代价 = 净差额（事件级）
    recon_B = round(s_ev["B_saved"] - s_ev["B_cost"], 4)
    recon_C = round(s_ev["C_saved"] - s_ev["C_cost"], 4)
    assert abs(recon_B - round(s_ev["B_delta"], 4)) < 5e-4, "B 三项对账不平"
    assert abs(recon_C - round(s_ev["C_delta"], 4)) < 5e-4, "C 三项对账不平"

    # ---------- 2. 变化交易清单 ----------
    chg_B = changed_list(events, "B")
    chg_C = changed_list(events, "C")
    write_changed_csv(chg_B, OUT / "changed_trades_B.csv", "B")
    write_changed_csv(chg_C, OUT / "changed_trades_C.csv", "C")

    b_deltas = [f(r, "B_net") - f(r, "A_net") for r in events]
    c_deltas = [f(r, "C_net") - f(r, "A_net") for r in events]

    # ---------- 3. 集中度 ----------
    conc_B = concentration(b_deltas)
    conc_C = concentration(c_deltas)

    # ---------- 4. 移除情景 ----------
    key_of = lambda r: (r["symbol"], r["entry_time_utc"])
    b_pos_keys = [key_of(r) for r in events
                  if f(r, "B_net") - f(r, "A_net") > TOL]
    b_neg_keys = [key_of(r) for r in events
                  if f(r, "B_net") - f(r, "A_net") < -TOL]
    b_pos_by_delta = sorted(
        [r for r in events if key_of(r) in set(b_pos_keys)],
        key=lambda r: -(f(r, "B_net") - f(r, "A_net")))
    b_neg_by_delta = sorted(
        [r for r in events if key_of(r) in set(b_neg_keys)],
        key=lambda r: (f(r, "B_net") - f(r, "A_net")))

    max_pos_key = key_of(b_pos_by_delta[0])   # B 最大正贡献（最大少亏）
    max_neg_key = key_of(b_neg_by_delta[0])   # B 最大负贡献（最大错失）
    c_keys = [key_of(r) for r in events if abs(f(r, "C_net") - f(r, "A_net")) > TOL]

    scenarios = {
        "baseline_201": remove_scenario(events, set(), "B"),
        "B_去掉最大正贡献": remove_scenario(events, {max_pos_key}, "B"),
        "B_去掉最大负贡献": remove_scenario(events, {max_neg_key}, "B"),
        "B_去掉最大正+最大负": remove_scenario(events, {max_pos_key, max_neg_key}, "B"),
        "B_去掉前3大|贡献|": remove_scenario(
            events,
            {key_of(r) for r in sorted(
                [r for r in events if abs(f(r, "B_net") - f(r, "A_net")) > TOL],
                key=lambda r: -abs(f(r, "B_net") - f(r, "A_net")))[:3]},
            "B"),
        "C_去掉唯一增益交易": remove_scenario(events, set(c_keys), "B"),
    }

    # ---------- 5. 刀切法 ----------
    jk_B = jackknife(events, b_pos_keys + b_neg_keys, "B")
    jk_C = jackknife(events, c_keys, "C")
    with (OUT / "jackknife_events.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(jk_B[0].keys()))
        w.writeheader()
        w.writerows(jk_B)
    jk_b_deltas = [x["B_delta"] for x in jk_B]
    jk_summary = {
        "B": {
            "n_removals": len(jk_B),
            "baseline_delta": round(s_ev["B_delta"], 4),
            "delta_min": round(min(jk_b_deltas), 4),
            "delta_max": round(max(jk_b_deltas), 4),
            "delta_median": round(sorted(jk_b_deltas)[len(jk_b_deltas) // 2], 4),
            "sign_flips": sum(1 for d in jk_b_deltas if d > 0),
            "sign_flip_trades": [x["symbol"] + "@" + x["entry_time_utc"]
                                 for x in jk_B if x["B_delta"] > 0],
        },
        "C": {
            "n_removals": len(jk_C),
            "baseline_delta": round(s_ev["C_delta"], 4),
            "delta_min": round(min(x["C_delta"] for x in jk_C), 4),
            "delta_max": round(max(x["C_delta"] for x in jk_C), 4),
        },
    }

    # ---------- 6. 分期分档 ----------
    buck = buckets(events)

    # ---------- 7. 显著性参考 ----------
    a_vals = [f(r, "A_net") for r in events]
    sig = {
        "A_t_stat_over_201": {k: (round(v, 4) if isinstance(v, float) else v)
                              for k, v in t_stat(a_vals).items()},
        "B_t_stat_over_201": {k: (round(v, 4) if isinstance(v, float) else v)
                              for k, v in t_stat([f(r, "B_net") for r in events]).items()},
        "C_t_stat_over_201": {k: (round(v, 4) if isinstance(v, float) else v)
                              for k, v in t_stat([f(r, "C_net") for r in events]).items()},
        "B_delta_t_stat_changed_only": {k: (round(v, 4) if isinstance(v, float) else v)
                                        for k, v in t_stat(
                                            [d for d in b_deltas if abs(d) > TOL]).items()},
        "sign_test_B_changed": {
            "n": s_ev["B_changed"], "pos": s_ev["B_better"], "neg": s_ev["B_worse"],
            "p_two_sided": round(sign_test(s_ev["B_better"], s_ev["B_changed"]), 4)},
    }

    payload = {
        "note": "事件级 201 样本；A=原始 B=1R保本 C=2R保本；来源=BREAKEVEN-01/out/paired_trades.csv（只读）",
        "event_level_summary": {k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in s_ev.items()},
        "candidate_level_crosscheck": {k: (round(v, 4) if isinstance(v, float) else v)
                                       for k, v in s_cd.items()},
        "cross_check_vs_breakeven01": cross_check,
        "reconciliation": {
            "B": {"saved_loss": round(s_ev["B_saved"], 4), "forgone": round(s_ev["B_cost"], 4),
                  "net_delta": round(s_ev["B_delta"], 4), "saved_minus_forgone": recon_B},
            "C": {"saved_loss": round(s_ev["C_saved"], 4), "forgone": round(s_ev["C_cost"], 4),
                  "net_delta": round(s_ev["C_delta"], 4), "saved_minus_forgone": recon_C},
        },
        "concentration_B": conc_B,
        "concentration_C": conc_C,
        "A_side_concentration": a_side_concentration(events),
        "changed_group_profiles": {"B": group_profile(events, "B"),
                                   "C": group_profile(events, "C")},
        "removal_scenarios": scenarios,
        "jackknife_summary": jk_summary,
        "buckets": buck,
        "significance_reference": sig,
        "changed_counts": {"B": len(chg_B), "C": len(chg_C)},
    }
    (OUT / "contribution_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"事件级 n={s_ev['n']}  A {s_ev['A_net']:+.4f}  B {s_ev['B_net']:+.4f}  C {s_ev['C_net']:+.4f}")
    print(f"B 差 {s_ev['B_delta']:+.4f}（少亏 {s_ev['B_saved']:.4f} / 代价 {s_ev['B_cost']:.4f}，"
          f"{s_ev['B_better']} 好 {s_ev['B_worse']} 坏）")
    print(f"C 差 {s_ev['C_delta']:+.4f}（少亏 {s_ev['C_saved']:.4f} / 代价 {s_ev['C_cost']:.4f}，"
          f"{s_ev['C_better']} 好 {s_ev['C_worse']} 坏）")
    print(f"B 集中度：gross {conc_B['gross_abs_total']:.2f}，top1 占 {conc_B['top1_share_of_gross']}%，"
          f"负侧 top1 占 {conc_B['negative_side']['top1_share']}%")
    print(f"刀切变号 {jk_summary['B']['sign_flips']} 次：{jk_summary['B']['sign_flip_trades']}")
    print(f"→ {OUT / 'contribution_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
