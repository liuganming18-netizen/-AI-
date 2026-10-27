# -*- coding: utf-8 -*-
"""LPS-PROPOSAL-VERIFY-01 · 只读复算

目的：对「震荡上行缩量回踩（LPS Trend Pullback）」提案里引用的账本数字，
用仓库内既有的逐笔母表独立复算，不重跑回测、不改任何上游文件。

输入（只读）：
  evidence/VOLUME-BE-01/out/hypotheses_evaluation_detail.csv   （206 候选，PIT 违规口径）
  evidence/VOLUME-BE-02/out/hypotheses_evaluation_detail.csv   （206 候选，PIT 修正口径）

运行：项目 venv python 01_复算.py
"""
import math
import pandas as pd

B1 = "../VOLUME-BE-01/out/hypotheses_evaluation_detail.csv"
B2 = "../VOLUME-BE-02/out/hypotheses_evaluation_detail.csv"


def stat(name, s: pd.Series) -> None:
    n = len(s)
    win = s[s > 0]
    loss = s[s < 0]
    print(f"{name:<34} n={n:>4}  净额={s.sum():>10.4f}  笔均={s.mean() if n else float('nan'):>8.4f}  "
          f"胜率={(len(win)/n*100 if n else float('nan')):>6.2f}%  盈{len(win)}笔/{win.sum():>9.4f}  "
          f"亏{len(loss)}笔/{loss.sum():>9.4f}")


def cp_ci(k: int, n: int, alpha: float = 0.05):
    """Clopper-Pearson 精确区间（二分求解，不依赖 scipy）。

    下界 p_L 满足 P(X >= k | p_L) = alpha/2（对 p 单调递增）；
    上界 p_U 满足 P(X <= k | p_U) = alpha/2（对 p 单调递减）。
    """
    def cdf(x, n, p):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(x + 1))

    lo_b, hi_b = 0.0, 1.0
    for _ in range(200):
        mid = (lo_b + hi_b) / 2
        if 1 - cdf(k - 1, n, mid) < alpha / 2:   # 下界偏小 → 右移
            lo_b = mid
        else:
            hi_b = mid
    p_lo = (lo_b + hi_b) / 2

    lo_b, hi_b = 0.0, 1.0
    for _ in range(200):
        mid = (lo_b + hi_b) / 2
        if cdf(k, n, mid) > alpha / 2:           # 上界偏小 → 右移
            lo_b = mid
        else:
            hi_b = mid
    p_hi = (lo_b + hi_b) / 2
    return p_lo, p_hi


def main() -> None:
    d1 = pd.read_csv(B1, encoding="utf-8-sig")
    d2 = pd.read_csv(B2, encoding="utf-8-sig")

    print("=" * 118)
    print("一、VOLUME-BE-01 口径（特征读未收盘 1h bar：df_1h[timestamp < entry_ms]）")
    print("=" * 118)
    stat("全集 206 候选", d1["A_net"])
    stat("放量阴线 110 笔（提案引用）", d1.loc[d1.is_entry_last_1h_high_vol_down == True, "A_net"])
    stat("缩量阴线 11 笔（B1 口径）", d1.loc[d1.is_entry_last_1h_vol_down == True, "A_net"])

    print()
    print("=" * 118)
    print("二、VOLUME-BE-02 口径（严格 close_time <= entry_ms，PIT 修正）")
    print("=" * 118)
    stat("全集 206 候选", d2["A_net"])
    stat("low_vol_drop 14 笔（提案引用）", d2.loc[d2.prev_1h_low_vol_drop == True, "A_net"])
    stat("high_vol_drop 24 笔", d2.loc[d2.prev_1h_high_vol_drop == True, "A_net"])
    stat("non_drop 168 笔", d2.loc[d2.prev_1h_non_drop == True, "A_net"])

    print()
    print("=" * 118)
    print("三、两版口径的身份一致性（同一批交易，特征却不同）")
    print("=" * 118)
    d1["k"] = list(zip(d1.symbol, d1.entry_time_ms))
    k2 = list(zip(d2.symbol, d2.entry_time_ms))
    hi110 = set(d1.loc[d1.is_entry_last_1h_high_vol_down == True, "k"])
    lo11 = set(d1.loc[d1.is_entry_last_1h_vol_down == True, "k"])
    lo14 = set(zip(d2.loc[d2.prev_1h_low_vol_drop == True, "symbol"],
                   d2.loc[d2.prev_1h_low_vol_drop == True, "entry_time_ms"]))
    print(f"母表身份匹配            : {len(set(k2) & set(d1.k))} / 206")
    print(f"缩量 11 笔 ∩ 缩量 14 笔 : {len(lo11 & lo14)} 笔（仅11: {len(lo11 - lo14)}，仅14: {len(lo14 - lo11)}）")
    print(f"缩量 14 笔 ∩ 放量 110 笔: {len(lo14 & hi110)} 笔（同一笔同时被判成放量与缩量）")

    print()
    print("=" * 118)
    print("四、提案口径复核：14 笔的利润占比与单笔期望")
    print("=" * 118)
    s14 = d2.loc[d2.prev_1h_low_vol_drop == True, "A_net"]
    net201, n201 = 105.439864, 201
    net206, n206 = d2["A_net"].sum(), len(d2)
    print(f"14 笔净额                 : {s14.sum():.4f} U")
    print(f"占 201 事件总净利 {net201:.2f}  : {s14.sum() / net201 * 100:.2f}%")
    print(f"占 206 候选总净利 {net206:.2f}  : {s14.sum() / net206 * 100:.2f}%  ← 同一笔钱换个分母就是 91%")
    print(f"201 事件口径基准笔均      : {net201 / n201:.4f} U（提案写的 +0.27 是 206 口径）")
    print(f"206 候选口径基准笔均      : {net206 / n206:.4f} U")
    print(f"14 笔笔均                 : {s14.mean():.4f} U")
    lo, hi = cp_ci(int((s14 > 0).sum()), len(s14))
    print(f"10 胜 4 负的 95% 置信区间 : [{lo*100:.1f}%, {hi*100:.1f}%]  基准胜率 40.29%（落在区间内）")


if __name__ == "__main__":
    main()
