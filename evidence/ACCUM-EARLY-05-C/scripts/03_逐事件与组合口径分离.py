# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-C｜03 逐事件盈亏与组合账户结果的分离

任务第 3 条原文：
    「明确逐事件盈亏与组合账户结果的区别。
      没有并发资金和仓位约束模拟，不得把盈亏叠加称组合回撤。」

本脚本要证明的不是「04-C 算错了加法」，而是更根本的一件事：
**把 51 笔 realized_net 顺序累加出来的曲线，不具备账户曲线的性质。**
因此由它算出的「最大回撤 346.235886 / 6.761%」不能叫组合回撤。

三段证据
--------
1. **复现**：用 02 的逐事件净额按 04-C 的算法复算，确认那个数字是怎么来的（可复现 ⇒ 不是抄错）；
2. **顺序敏感性**：同一批 51 个数字，换一种排列，伪回撤就变。
   账户回撤是账户的属性，不该随「你先算哪笔」而变 ⇒ 它不是账户属性；
3. **并发重叠**：实测这 51 笔的持仓区间有重叠、同时在仓峰值 > 1。
   顺序累加隐含「任何时刻只有 1 笔在仓、且每笔都占用完整本金」，
   与事实矛盾 ⇒ 它不是账户曲线。

结论只写一件事：组合回撤 = UNVERIFIED（不是「0」，也不是「346」）。
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"
SRC = OUT_DIR / "02_逐事件基线.json"
UPD_04C = ROOT / "evidence" / "ACCUM-EARLY-04-C" / "out" / "02_原策略基线结果.json"

N_PERM = 2000
SEED = 20261026


def pseudo_drawdown(nets, initial_equity):
    """完全照抄 04-C 的算法，用来复现它的数字（不是用来当结论）。

    04-C 的做法：equity_curve = [initial_equity] 然后逐笔 append(initial_equity + 累计和)，
    再对这个序列求峰值回撤。问题在于这个序列不是时间序列，是「候选列表顺序」。
    """
    curve = [float(initial_equity)]
    cum = 0.0
    for x in nets:
        cum += float(x)
        curve.append(float(initial_equity) + cum)
    peak = curve[0]
    dd_amt = 0.0
    dd_pct = 0.0
    for eq in curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        pct = (dd / peak) * 100.0 if peak > 0 else 0.0
        dd_amt = max(dd_amt, dd)
        dd_pct = max(dd_pct, pct)
    return dd_amt, dd_pct, curve


def max_concurrent(intervals):
    """按时间扫描最大同时在仓数。intervals = [(start_ms, end_ms), ...]"""
    ev = []
    for s, e in intervals:
        ev.append((s, 1))
        ev.append((e, -1))
    ev.sort(key=lambda x: (x[0], -x[1]))   # 同一时刻先开后关
    cur = 0
    best = 0
    at = None
    for t, d in ev:
        cur += d
        if cur > best:
            best = cur
            at = t
    return best, at


def overlap_stats(intervals):
    n = len(intervals)
    pairs = 0
    overlap_ms = 0
    total_ms = 0
    for i in range(n):
        s1, e1 = intervals[i]
        total_ms += max(0, e1 - s1)
        for j in range(i + 1, n):
            s2, e2 = intervals[j]
            lo, hi = max(s1, s2), min(e1, e2)
            if hi > lo:
                pairs += 1
                overlap_ms += hi - lo
    return pairs, overlap_ms, total_ms, n * (n - 1) // 2


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    d2 = json.loads(SRC.read_text(encoding="utf-8"))
    d04c = json.loads(UPD_04C.read_text(encoding="utf-8"))["historical_replayed_baselines"]

    out = {
        "task": "ACCUM-EARLY-05-C",
        "probe": "03_逐事件与组合口径分离",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(SRC.relative_to(ROOT)),
        "three_layer_definition": {
            "L1_逐事件独立盈亏": {
                "含义": "每一笔在「独立假设」下完整回放的已实现净额：入场时账户有完整初始本金、"
                        "零已占用敞口、按该口径的风险比例与参与率上限算数量。",
                "合法用法": [
                    "逐笔盈亏分布（胜率、盈亏比、均值、中位数）",
                    "逐事件求和（= 累加净额），用于同口径横向比较两套策略",
                    "按象限（两者都入场/仅原/仅新/都未入）分组求和",
                ],
                "不合法用法": [
                    "当作账户收益或账户收益率",
                    "由它算最大回撤、夏普、年化",
                ],
            },
            "L2_顺序累加伪资金曲线": {
                "含义": "把 L1 的净额按某个顺序依次累加到初始本金上，得到一条曲线。",
                "性质": "曲线形状取决于排列顺序，不是账户在任何真实时间点的权益 ⇒ 无经济含义。",
                "合法用法": ["仅用于复现上一单的数字是怎么来的"],
                "不合法用法": ["称它为「资金曲线」「组合回撤」「净值」"],
            },
            "L3_组合账户结果": {
                "含义": "在真实时间轴上推进：账户权益随已实现盈亏滚动、同时持仓受 3 仓/总风险/总名义约束、"
                        "同一时刻多笔候选需排队、被挤掉的候选不成交。",
                "本单状态": "UNVERIFIED（未实现）",
                "为什么本单不实现": [
                    "并发上限：冻结规则写 3 仓，但 05-A 决策 5 仍在待批（另有 10 仓的历史说法）；"
                    "未批准就实现 = 自定阈值",
                    "多笔同时到信号时的先后取舍规则：没有任何已批准文件规定",
                    "被挤掉的候选如何处理（丢弃/顺延/部分成交）：无规定",
                    "账户权益滚动后，每笔的风险预算基数也随之变化 ⇒ 会改变数量与盈亏，"
                    "这是口径变更，不是「加个约束」",
                ],
                "技术可行性": "replay_one 已经接受 equity / gross_open_notional / portfolio_risk_open 三个入参，"
                              "说明算术层支持组合约束；缺的是被批准的调度规则，不是代码能力。",
            },
        },
        "calibers": {},
        "errors": [],
    }

    for cal_name, block in d2["calibers"].items():
        per = block["per_event"]
        initial_equity = float(block["caliber_params"]["initial_equity"])

        closed = [e for e in per if e["status"] == "ok" and not e["is_open_at_window_end"]
                  and e["realized_net"] is not None]
        rejected = [e for e in per if e["status"] != "ok"]
        still_open = [e for e in per if e["status"] == "ok" and e["is_open_at_window_end"]]

        # 02 里 per_event 已按上游候选顺序（= 入场时间升序）排列
        base_nets = [e["realized_net"] for e in closed]

        # ── 1. 复现 04-C 的伪回撤 ─────────────────────────────────────
        dd_orig, dd_orig_pct, _ = pseudo_drawdown(base_nets, initial_equity)
        ref = d04c.get(cal_name, {})
        repro = {
            "本单复现值": {"amount": round(dd_orig, 6), "pct": round(dd_orig_pct, 4)},
            "04C产物值": {"amount": ref.get("max_drawdown_amount"),
                          "pct": ref.get("max_drawdown_pct")},
            "可复现": (ref.get("max_drawdown_amount") is not None
                       and abs(dd_orig - float(ref["max_drawdown_amount"])) < 1e-5),
            "含义": "能复现 ⇒ 确认这个数字就是「顺序累加曲线的峰值回撤」，不是抄错。",
        }

        # ── 2. 顺序敏感性 ─────────────────────────────────────────────
        order_entry = sorted(closed, key=lambda e: e["entry_ms"])
        order_exit = sorted(closed, key=lambda e: (e["exit_ms"] or 0))
        order_net_desc = sorted(closed, key=lambda e: -e["realized_net"])

        def dd_of(seq):
            a, p, _ = pseudo_drawdown([e["realized_net"] for e in seq], initial_equity)
            return round(a, 6), round(p, 4)

        variants = {
            "按入场时间升序（= 上游候选顺序）": dd_of(order_entry),
            "按退出时间升序": dd_of(order_exit),
            "按单笔净额从大到小": dd_of(order_net_desc),
        }

        rng = random.Random(SEED)
        perm_amts = []
        perm_pcts = []
        arr = list(base_nets)
        for _ in range(N_PERM):
            rng.shuffle(arr)
            a, p, _ = pseudo_drawdown(arr, initial_equity)
            perm_amts.append(a)
            perm_pcts.append(p)
        perm_amts.sort()
        perm_pcts.sort()

        def q(lst, frac):
            if not lst:
                return None
            idx = min(len(lst) - 1, max(0, int(round(frac * (len(lst) - 1)))))
            return lst[idx]

        sensitivity = {
            "同一批数字、不同排列": variants,
            "随机置换": {
                "次数": N_PERM,
                "seed": SEED,
                "amount_min": round(perm_amts[0], 6),
                "amount_p50": round(q(perm_amts, 0.50), 6),
                "amount_p95": round(q(perm_amts, 0.95), 6),
                "amount_max": round(perm_amts[-1], 6),
                "pct_min": round(perm_pcts[0], 4),
                "pct_p50": round(q(perm_pcts, 0.50), 4),
                "pct_max": round(perm_pcts[-1], 4),
            },
            "判据": "若同一批净额换顺序后伪回撤显著变化，则该数字是排列的函数，不是账户属性。",
        }
        amts = [v[0] for v in variants.values()] + perm_amts
        sensitivity["极差"] = round(max(amts) - min(amts), 6)
        sensitivity["结论"] = ("伪回撤随排列变化，极差 "
                               f"{sensitivity['极差']:.6f} USDT；"
                               "它不能被称为账户回撤。")

        # ── 3. 并发重叠 ───────────────────────────────────────────────
        intervals = [(e["entry_ms"], e["exit_ms"]) for e in closed if e["exit_ms"]]
        peak, peak_at = max_concurrent(intervals)
        pairs, ov_ms, tot_ms, possible = overlap_stats(intervals)
        concurrency = {
            "样本": len(intervals),
            "同时在仓峰值": peak,
            "峰值出现时刻_utc": iso(peak_at),
            "存在重叠的笔对": pairs,
            "可能笔对总数": possible,
            "重叠笔对占比_pct": (round(pairs / possible * 100, 2) if possible else None),
            "重叠总时长_小时": round(ov_ms / 3600000.0, 2),
            "持仓总时长_小时": round(tot_ms / 3600000.0, 2),
            "判据": "顺序累加隐含「任何时刻只有 1 笔在仓」。实测同时在仓峰值 > 1 ⇒ 前提不成立。",
            "冻结规则的上限": "max_active_positions=3（但 05-A 决策 5 仍在待批，另有 10 仓的历史说法）",
        }

        # ── 4. 逐事件分布（这是 L1 层唯一合法的统计）─────────────────
        vals = sorted(base_nets)
        dist = {
            "n_closed": len(vals),
            "n_rejected": len(rejected),
            "n_open_at_window_end": len(still_open),
            "sum": round(sum(vals), 6),
            "mean": round(sum(vals) / len(vals), 6) if vals else None,
            "median": round(vals[len(vals) // 2], 6) if vals else None,
            "min": round(vals[0], 6) if vals else None,
            "max": round(vals[-1], 6) if vals else None,
            "note": "这是逐事件分布，可合法报告；不要把它换算成「账户收益率」。",
        }

        out["calibers"][cal_name] = {
            "initial_equity": initial_equity,
            "reproduce_04c_pseudo_drawdown": repro,
            "order_sensitivity": sensitivity,
            "concurrency": concurrency,
            "per_event_distribution": dist,
            "verdict": {
                "组合回撤": "UNVERIFIED",
                "组合收益率": "UNVERIFIED",
                "可报告的": ["逐事件盈亏分布", "逐事件累加净额（同口径横向比较用）",
                             "拒单清单", "期末持仓清单"],
                "禁止的": ["把 04-C 的 max_drawdown_amount / max_drawdown_pct 当账户回撤引用",
                           "把累加净额除以初始本金当账户收益率"],
            },
        }

    dst = OUT_DIR / "03_逐事件与组合口径分离.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✔ 已落盘 {dst.relative_to(ROOT)}")
    for cal, b in out["calibers"].items():
        r = b["reproduce_04c_pseudo_drawdown"]
        s = b["order_sensitivity"]
        c = b["concurrency"]
        print(f"\n[{cal}]")
        print(f"  复现伪回撤 {r['本单复现值']} vs 04C {r['04C产物值']} 可复现={r['可复现']}")
        print(f"  顺序极差 {s['极差']} USDT；置换 p50={s['随机置换']['amount_p50']} "
              f"max={s['随机置换']['amount_max']}")
        print(f"  同时在仓峰值 {c['同时在仓峰值']}；重叠笔对 {c['存在重叠的笔对']}/{c['可能笔对总数']}"
              f"（{c['重叠笔对占比_pct']}%）；重叠时长 {c['重叠总时长_小时']}h")


if __name__ == "__main__":
    main()
