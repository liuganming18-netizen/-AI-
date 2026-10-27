# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜吸筹阶段提前入场：**可执行规则接口 v1**（纯函数，无 IO）。

为什么要有这个文件
------------------
前两轮（ACCUM-EARLY-01/02）把「三组提案」写成了自然语言，于是每个窗口对
「局部压制位」「承接」「确认」的理解都不一样。本文件把它们钉成**函数签名 +
字段契约 + 状态机**，让别的窗口可以直接依赖，而不是各自再解释一遍。

三条不可越的线
--------------
1. **本文件不是批准书。** 每个参数的 `状态` 只允许三种：
   `已确定`（用户/工单已定）、`沿用`（冻结规则已有，逐字照搬）、
   `推荐未批准`（本单给建议值 + 依据 + 风险，但**没有人批准**）。
   `待决` 表示连推荐都给不出（缺证据），调用方必须显式传值，否则拒跑。
2. **没有兜底默认值。** `resolve_params()` 只接受「已确定 / 沿用 / 推荐未批准」
   三类键；传 `待决` 键会抛错。要跑必须先有人拍板。
3. **不碰生产。** 本文件不 import 生产策略包，不读库、不写库、不下载。
   数据由调用方喂进来（`bars_4h` / `bars_1h` / `bars_15m` 都是 dict 列表）。

时点语义（四个时刻必须分开，混用就会偷看未来）
--------------------------------------------
· 4h 箱体：`open_time` 是区间起点，**决策时刻 = open_time + 4h**（= close_time + 1ms）
· 1h 事件：决策时刻 = open_time + 1h
· 确认：用**已收盘**的 1h 收盘价，确认时刻 = 该根 1h 的决策时刻
· 最早成交：确认时刻之后**第一根** 15m 的 `open_time`（12:00 确认 ⇒ 12:15 开盘），
  **不回填确认根**、不用确认根的收盘价成交。

状态机
------
    OBSERVE ──扫低收回──> SWEEP ──定出局部压制位──> ARMED
      │                     │                        │
      │                     └──压制位不合格──> NO_RESISTANCE
      ├──结构破位──────────> INVALIDATED
      └──超时──────────────> EXPIRED
    ARMED ──1h 收盘收上压制位──> CONFIRMED(甲) / 再守一根(乙) / 回抽后转强(丙)
    CONFIRMED ──下一根 15m 开盘──> FILLED
              └──跳空超限/无 15m──> CANCELLED

字段契约见 `EventTrace` 的 docstring；`RULES_VERSION` 变了就必须整批重算。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

RULES_VERSION = "ACCUM-EARLY-RULES-v1"

MS_H = 3_600_000
MS_4H = 4 * MS_H
MS_15M = 900_000

#: 三组方案（工程名沿用 ACCUM-EARLY-01/02，**不是**批准的策略编号）
SCHEMES = {
    "4a": {"label": "甲", "confirm": "1h 首次收盘收上局部压制位即确认"},
    "4b": {"label": "乙", "confirm": "收上后再多守一根 1h 收盘（仍在该位上方）才确认"},
    "4c": {"label": "丙", "confirm": "收上后等回抽到该位附近，再由 1h 收盘转强确认"},
}

#: 事件状态（字符串常量，写进证据，别用数字）
ST_OBSERVE = "OBSERVE"
ST_SWEEP = "SWEEP"
ST_ARMED = "ARMED"
ST_CONFIRMED = "CONFIRMED"
ST_FILLED = "FILLED"
ST_INVALIDATED = "INVALIDATED"
ST_EXPIRED = "EXPIRED"
ST_NO_RESISTANCE = "NO_RESISTANCE"
ST_CANCELLED = "CANCELLED"


# ══════════════════════════════════════════════════════════════════════════
# 参数表：每个参数都必须说清「谁定的 / 值 / 依据 / 改了会怎样 / 风险」
# ══════════════════════════════════════════════════════════════════════════
#
# 状态口径：
#   已确定 = 用户或工单已明确（含《用户要求与决策.md》里的止损决定）
#   沿用   = 冻结规则 WX_EVENT_TREND_V0_1 已有，逐字照搬（只对原策略生效的，
#            在新方案里仍属「推荐未批准」，见各自 note）
#   推荐未批准 = 本单给建议值 + 真实数据依据 + 风险，**尚未获批准**
#   待决   = 给不出推荐（缺证据或纯属商业判断），调用方必须显式传值

PARAMS: Dict[str, dict] = {
    # ── 一、吸筹观察（箱体）───────────────────────────────────────────────
    "box_bars_4h": {
        "状态": "沿用", "值": 42, "类型": "int",
        "含义": "箱体窗口 = 最近 42 根已收盘 4h K 线（7 天）",
        "依据": "冻结规则 event_trend_v0_1/config.py::box_bars_4h=42",
        "改变的行为": "窗口越长箱体越宽、建档越少",
        "风险": "改动即改「吸筹」的定义，三组与基线不再可比",
    },
    "atr_period_4h": {
        "状态": "沿用", "值": 14, "类型": "int",
        "含义": "4h Wilder ATR 周期",
        "依据": "冻结规则 atr_period_4h=14；首值 = 前 14 根 TR 算术平均，之后递推",
        "改变的行为": "影响箱体宽度门槛与全部 ATR 距离量纲",
        "风险": "同 box_bars_4h",
    },
    "atr_warmup_bars": {
        "状态": "沿用", "值": 200, "类型": "int",
        "含义": "至少 200 根完整 4h 才允许建档",
        "依据": "冻结规则 atr_warmup_bars=200",
        "改变的行为": "决定新上市币多久后才可能出信号",
        "风险": "调小会让新币提前进入，历史样本不可比",
    },
    "er_threshold": {
        "状态": "沿用", "值": 0.30, "类型": "float",
        "含义": "42 根 4h 收盘的方向效率比 ER ≤ 0.30",
        "依据": "冻结规则 er_threshold=0.30；ER = |末-首| / Σ|相邻差|",
        "改变的行为": "ER 越小越「横」，放宽会显著增加箱体数",
        "风险": "放宽等于把趋势行情也当吸筹，是本策略最容易自我欺骗的地方",
    },
    "box_min_width_atr": {
        "状态": "沿用", "值": 2.0, "类型": "float",
        "含义": "箱体宽度下限 = 2.0 × ATR",
        "依据": "冻结规则 box_min_width_atr=2.0",
        "改变的行为": "太窄的箱体没有提前入场的空间",
        "风险": "调低会让「几乎没波动」的区间也算吸筹",
    },
    "box_max_width_atr": {
        "状态": "沿用", "值": 8.0, "类型": "float",
        "含义": "箱体宽度上限 = 8.0 × ATR",
        "依据": "冻结规则 box_max_width_atr=8.0",
        "改变的行为": "太宽的箱体里压制位离箱底太远，5% 止损容不下",
        "风险": "调高会让止损被噪声打掉的概率上升",
    },
    "cooling_hours": {
        "状态": "沿用", "值": 24, "类型": "int",
        "含义": "同标的事件终结后冷却 24 小时",
        "依据": "冻结规则 cooling_hours=24",
        "改变的行为": "决定同币重复计数的密度",
        "风险": "取消冷却会让同一段行情被反复计数，虚增样本",
    },

    # ── 二、局部支撑与「扫低收回」─────────────────────────────────────────
    "sweep_object": {
        "状态": "推荐未批准", "值": "local_support", "类型": "str",
        "含义": ("扫低的对象：`local_support` = 扫破前 N 根 1h 的最低价（局部支撑，"
                 "甲乙丙原文口径）；`box_low` = 扫破箱体下沿 L（原策略口径）"),
        "依据": ("本单在研究区间上把两种口径都跑了：`local_support` 甲组 3,209 笔、"
                 "R 合计 −192.78；`box_low` 甲组只有 266 笔、R 合计 −34.03"
                 "（乙 192 笔 −35.93 / 丙 170 笔 −14.90）。产物 "
                 "out/01_共同观察范围_research_final.json 与 _sens_sweep_boxlow.json。"
                 "**这是本单最重要的一个待决项**"),
        "改变的行为": ("改成 box_low 会把机会量砍掉一个数量级，同时把「提前入场」"
                       "变成「原策略换一个更早的触发位」"),
        "风险": ("local_support 口径下新增机会的 R 合计为负（甲 -220.4 / 乙 -246.2 / "
                 "丙 -195.4）⇒ 放宽事件链起点是亏的；但这条结论依赖本单的止损与"
                 "跟踪规则，换止损口径可能变"),
    },
    "local_support_bars": {
        "状态": "推荐未批准", "值": 24, "类型": "int",
        "含义": ("局部支撑 S_loc = 扫低根之前 24 根 1h 的**最低价**；"
                 "要求 S_loc > 箱体下沿 L（否则这条就是原策略的破 L 扫低，不属本方案）"),
        "依据": ("24 根 1h = 1 个交易日。本单只跑了 12 根与 24 根两档：12 根时甲组 "
                 "6,108 笔、R −294.84，明显更差（out/01_共同观察范围_sens_support12.json）；"
                 "**48 根未跑**，是纸面候选、不是已验证值"),
        "改变的行为": "N 越小支撑越「新」、扫低越频繁；N 越大越接近箱底",
        "风险": "N 太小会把普通回调当扫低；N 太大会退化成原策略",
    },
    "sweep_max_overshoot_atr": {
        "状态": "沿用", "值": 0.5, "类型": "float",
        "含义": "扫低最低价不得低于 S_loc − 0.5A（再深就是真破位，不是插针）",
        "依据": "冻结规则 sweep_max_overshoot_atr=0.5（语义从「L−0.5A」平移到「S_loc−0.5A」）",
        "改变的行为": "放宽会多抓深插针，也多吃真破位",
        "风险": "0.5A 是原策略对 L 的容差，平移到局部支撑上**没有独立验证过**",
    },
    "sweep_min_recovery_atr": {
        "状态": "沿用", "值": 0.1, "类型": "float",
        "含义": "扫低根收盘必须回到 S_loc + 0.1A 以上（收回才算承接）",
        "依据": "冻结规则 sweep_min_recovery_atr=0.1",
        "改变的行为": "调高要求更强的收回，事件变少",
        "风险": "同 overshoot：平移未独立验证",
    },
    "sweep_recovery_scope": {
        "状态": "推荐未批准", "值": "single_bar", "类型": "str",
        "含义": "扫低与收回必须在**同一根** 1h 内完成（不允许多根拼）",
        "依据": ("与原策略同构（单根完成）。**本单未做双根实现的对照**，"
                 "因此这是「没有数据支持的建议」，只是为了让规则可执行；"
                 "风险栏写明放宽后会怎样"),
        "改变的行为": "放宽到 2 根会显著增加事件数",
        "风险": "多根完成时，「收回」可能只是下一根反弹，承接证据被稀释",
    },

    # ── 三、局部压制位（本单最核心的未定项）───────────────────────────────
    "local_resistance_definition": {
        "状态": "推荐未批准",
        "值": "swing_high_k3_lookback48",
        "类型": "str",
        "含义": ("R_loc = 扫低根**之前** 48 根 1h 内、**最后一个**左右各 3 根都不高于它的"
                 "1h 最高价（摆动高点）；找不到则回退为该 48 根的最高价"),
        "依据": ("本单在研究区间上对比了 4 种定义，甲组成交 / R 合计分别为："
                 "摆动高点 3,209 笔 −192.78；前 24 根最高 2,409 笔 −121.27；"
                 "箱体中位 6,698 笔 −349.54；箱内收盘 70% 分位 3,731 笔 −39.86。"
                 "产物 out/01_共同观察范围_sens_R_*.json，汇总见 03_决策表.md 第 4 节。"
                 "**这 4 档全在研究区间内，挑最低那一档就是拟合**"),
        "改变的行为": "直接决定入场触发点高低：定义越低入场越早、假突破越多",
        "风险": ("摆动高点对参数 k 敏感；k 变大后摆动点变少、回退到「区间最高」的概率上升，"
                 "那时它等于「前 48 根最高」，提前量会被吃掉"),
    },
    "resistance_swing_k": {
        "状态": "推荐未批准", "值": 3, "类型": "int",
        "含义": "摆动高点判定：该根 high 高于左右各 3 根 high",
        "依据": ("3 根 ≈ 半个交易日的噪声容忍。**本单未跑 k=2 / k=5 的对照**，"
                 "这是没有数据支持的建议值，只是为了让规则可执行"),
        "改变的行为": "k 越大摆动点越少、回退越多",
        "风险": "k=5 时 48 根窗口内常常找不到摆动点 ⇒ 静默退化成「区间最高」",
    },
    "resistance_lookback_bars": {
        "状态": "推荐未批准", "值": 48, "类型": "int",
        "含义": "在扫低根之前多少根 1h 内找压制位",
        "依据": "48 根 = 2 个交易日，覆盖箱体内最近一轮反弹的高度",
        "改变的行为": "窗口越长压制位越可能取到箱体上沿 H（提前量消失）",
        "风险": "与 box_bars_4h 的 7 天窗口不一致，可能取到箱体形成之前的价位",
    },
    "resistance_min_gap_atr": {
        "状态": "推荐未批准", "值": 0.5, "类型": "float",
        "含义": ("压制位必须同时满足 L + 0.5A ≤ R_loc ≤ H − 0.5A；"
                 "不满足则本事件记 NO_RESISTANCE（**不是**失败，是「没有提前入场的空间」）"),
        "依据": ("下界保证 R_loc 离箱底有 0.5A（5% 止损容得下）；"
                 "上界保证 R_loc 比箱体上沿低 0.5A（否则与原策略突破同义）。"
                 "本单对比 0.5A / 1.0A 两档"),
        "改变的行为": "门槛越高事件越少、提前量越大",
        "风险": "1.0A 会把大部分事件判成 NO_RESISTANCE（箱体只有 2~8A 宽）",
    },

    # ── 四、承接 / 卖压（用 K 线自带的主动买卖量，不用指标 parquet）────────
    "absorption_source": {
        "状态": "推荐未批准", "值": "bar_taker_buy_quote",
        "类型": "str",
        "含义": ("主动买占比 = taker_buy_quote_volume / quote_volume，逐根 1h 自带；"
                 "主动卖出额 = quote_volume − taker_buy_quote_volume（推导量）"),
        "依据": ("`metrics_feed.py` 的 5m 指标 parquet **只有 ts、没有发布/抓取时点**，"
                 "按项目 PIT 规则只能是 unproven，不得进决策；而 K 线的主动买卖量"
                 "随该根收盘即可知。两者同源不同层，选可判定的那一层"),
        "改变的行为": "换回 metrics parquet 会让承接判定变成「用未来数据」",
        "风险": ("K 线的 taker_buy_quote_volume 在冷档 `available_at` 填充率为 0%，"
                 "只能声明「计算不超前」，不能声明「已证明可得时点」"),
    },
    "absorption_baseline_bars": {
        "状态": "推荐未批准", "值": 20, "类型": "int",
        "含义": "承接与卖压的比较基准 = 扫低根之前 20 根 1h 的中位数",
        "依据": "与冻结规则 V20（前 20 根成交量中位数）同窗口，避免多立一套基准",
        "改变的行为": "窗口越短越敏感、越容易被单根异常带偏",
        "风险": "中位数对「连续放量」不敏感，趋势末段可能仍判为「卖压减弱」",
    },
    "absorption_taker_mult": {
        "状态": "推荐未批准", "值": 1.0, "类型": "float",
        "含义": ("扫低根主动买占比 ≥ 前 20 根中位数 × 1.0，且收盘位于当根振幅上半区"
                 "（close ≥ low + 0.5 × (high−low)）"),
        "依据": ("本单只跑了 1.0 与 1.2 两档（**1.1 未跑**）。1.2 档甲组 1,098 笔、"
                 "R +6.89，丙组 747 笔、R +21.47 —— **这一档能把研究区间的结论从负翻成正**"
                 "（out/01_共同观察范围_sens_taker12.json）。正因翻转幅度这么大，"
                 "它只能当敏感性看，不能据此把 1.2 定成推荐值"),
        "改变的行为": "阈值越高事件越少、单笔质量越高（样本不足以证明）",
        "风险": "1.0 等于「不比平时差」，证据强度弱；1.2 会把样本砍到无法统计",
    },
    "sell_pressure_mult": {
        "状态": "推荐未批准", "值": 0.8, "类型": "float",
        "含义": "扫低根主动卖出额 ≤ 前 20 根中位数 × 0.8（卖压确实减弱）",
        "依据": "与冻结规则 retest_vol_multiplier=0.8 同倍数，语义从「总量」平移到「主动卖出额」",
        "改变的行为": "阈值越低越严格",
        "风险": "主动卖出额是推导量，交易所不直接提供，口径错会整段偏",
    },
    "absorption_is_gate": {
        "状态": "推荐未批准", "值": True, "类型": "bool",
        "含义": "承接条件是**硬门槛**（不满足不进 ARMED），不只是证据",
        "依据": ("策略命题是「发现承接后提前入场」；若只作证据，三组与原策略的差别"
                 "就只剩触发价位，命题不成立"),
        "改变的行为": "关掉后事件数大增，等于做「无承接的提前入场」",
        "风险": "硬门槛会把「承接发生在扫低之后几根」的情形全部排除",
    },

    # ── 五、确认 / 失效 / 期限 ───────────────────────────────────────────
    "confirm_timeframe": {
        "状态": "已确定", "值": "1h", "类型": "str",
        "含义": "确认只看 1h 收盘",
        "依据": "《用户要求与决策.md》已确认：1h 确认、15m 执行",
        "改变的行为": "换成 15m 确认会把噪声当信号",
        "风险": "无（用户已定）",
    },
    "confirm_requires_bull_close": {
        "状态": "推荐未批准", "值": True, "类型": "bool",
        "含义": "确认根必须收阳（close ≥ open）",
        "依据": ("与原策略 RETEST 的「收阳」一致。**本单未跑「不要求收阳」的对照**，"
                 "这是没有数据支持的建议值"),
        "改变的行为": "关掉后确认变多，含「低开高走仍收阴」的根",
        "风险": "强反弹里第一根常是低开，收阳条件会漏掉一部分",
    },
    "pullback_zone_atr": {
        "状态": "推荐未批准", "值": 0.25, "类型": "float",
        "含义": "丙组回抽判定：1h 最低价 ≤ R_loc + 0.25A（回到压制位附近）",
        "依据": ("0.25A 是原策略 retest_max_zone_atr=0.5 的一半，因为这里等的是"
                 "「回抽」不是「回踩箱底」。**本单未跑 0.5A 的对照**，只给单点值与丙组放弃率"),
        "改变的行为": "区间越窄越难等到",
        "风险": "丙组的放弃率对这一个数极其敏感，本单只给单点值 + 放弃率，不寻优",
    },
    "pullback_deadline_hours": {
        "状态": "推荐未批准", "值": 48, "类型": "int",
        "含义": "丙组收上压制位后等回抽的期限",
        "依据": "48 小时 = 2 个交易日；超过则视为「直接走了」放弃",
        "改变的行为": "期限越长越可能等到，但入场价优势被时间成本吃掉",
        "风险": "只影响丙组，三组对比时必须单独报放弃率",
    },
    "armed_fail_bars": {
        "状态": "推荐未批准", "值": 2, "类型": "int",
        "含义": "收上压制位后若连续 2 根 1h 收盘跌回 R_loc − 0.1A 下方 ⇒ 观察失效",
        "依据": ("与原策略「连续两根收盘破关键位即失效」同构；"
                 "**未做 1 根/2 根/3 根的对照**，这是没有数据支持的建议"),
        "改变的行为": "改 1 根会更早放弃（漏掉再突破）；改 3 根会更晚",
        "风险": "本单未验证该阈值，属工程上必须有一个值才能跑，不等于已验证",
    },
    "armed_fail_buffer_atr": {
        "状态": "推荐未批准", "值": 0.1, "类型": "float",
        "含义": "失效判定留 0.1A 缓冲，避免在压制位上下反复擦边",
        "依据": "与冻结规则 retest_recovery_atr=0.1 同倍数",
        "改变的行为": "缓冲越大越宽容",
        "风险": "同 armed_fail_bars：未单独验证",
    },
    "observe_deadline_hours": {
        "状态": "推荐未批准", "值": 336, "类型": "int",
        "含义": "事件总观察期（自 4h 箱体建档起）",
        "依据": "沿用原策略 336 小时（14 天）的总期限，便于与基线对齐；本单对比 168/336",
        "改变的行为": "期限越长事件越多、越容易等到确认",
        "风险": "「沿用」只是形式一致；新方案的状态更少，同样 336h 的实际含义不同",
    },
    "invalidate_close_below_l": {
        "状态": "推荐未批准", "值": 2, "类型": "int",
        "含义": "连续 2 根 1h 收盘 < 箱体下沿 L ⇒ 失效（沿用原策略）",
        "依据": "冻结规则「连续两根收盘破 L」",
        "改变的行为": "改为 1 根会更早失效、事件更少",
        "风险": "沿用原策略的失效语义，但新方案入场更早、更容易在失效前就已入场",
    },
    "invalidate_l_overshoot_atr": {
        "状态": "推荐未批准", "值": 0.5, "类型": "float",
        "含义": "任一根 1h 最低价 < L − 0.5A ⇒ 失效",
        "依据": "冻结规则 sweep_max_overshoot_atr（失效侧）",
        "改变的行为": "放宽会容忍更深的假破",
        "风险": "同「平移未独立验证」",
    },
    "intrabar_break_cancel": {
        "状态": "推荐未批准", "值": False, "类型": "bool",
        "含义": "观察期内**盘中**跌破不作为取消依据，只看 1h 收盘（与原策略一致）",
        "依据": ("原策略全部判定都用收盘价。**本单未跑「允许盘中破位即取消」的对照**，"
                 "这是没有数据支持的建议值"),
        "改变的行为": "打开会让候选在盘中消失，回放时无法用收盘复算",
        "风险": "盘中真破位要等到收盘才认，止损可能更差",
    },

    # ── 六、执行（已确定，不重新询问）─────────────────────────────────────
    "execution_timeframe": {
        "状态": "已确定", "值": "15m", "类型": "str",
        "含义": "成交与止损撮合在 15m 上做",
        "依据": "《用户要求与决策.md》：按既有 15 分钟执行规则",
        "改变的行为": "—", "风险": "无（用户已定）",
    },
    "entry_delay_15m_bars": {
        "状态": "已确定", "值": 1, "类型": "int",
        "含义": "确认后取**严格更晚**的下一根 15m 开盘成交（12:00 确认 ⇒ 12:15）",
        "依据": "《用户要求与决策.md》原文：「确认于 12:00 时，最早 12:15，不回填 12:00」",
        "改变的行为": "改成 0 就是拿确认根收盘价成交（偷看）",
        "风险": "无（用户已定）",
    },
    "initial_stop_pct": {
        "状态": "已确定", "值": 0.05, "类型": "float",
        "含义": "初始止损 = 实际成交价 × 95%",
        "依据": "《用户要求与决策.md》：「初始止损：实际成交价 × 95%」",
        "改变的行为": "改它会改全部 R 口径",
        "风险": "无（用户已定）",
    },
    "trailing_low_pct": {
        "状态": "已确定", "值": 0.05, "类型": "float",
        "含义": ("入场后每根**新收盘**的 4h K 线：候选止损 = 该根最低价 × 95%；"
                 "实际止损取初始/此前/本次三者最高值，只上移不下移"),
        "依据": "《用户要求与决策.md》原文",
        "改变的行为": "—", "风险": "无（用户已定）",
    },
    "trailing_trigger_r": {
        "状态": "已确定", "值": 0.0, "类型": "float",
        "含义": "入场即开始跟踪（无 2R 门槛）",
        "依据": "《用户要求与决策.md》：「不设盈利到初始风险两倍才启动跟踪」",
        "改变的行为": "—", "风险": "无（用户已定）",
    },
    "max_holding_days": {
        "状态": "已确定", "值": None, "类型": "int?",
        "含义": "不设持仓天数上限（只靠止损退出）",
        "依据": "《用户要求与决策.md》：「不设持仓 10 天自动平仓」",
        "改变的行为": "—", "风险": "无（用户已定）",
    },
    "max_gap_up_atr": {
        "状态": "推荐未批准", "值": 0.5, "类型": "float",
        "含义": "成交根开盘价 > 确认收盘 + 0.5A ⇒ 放弃（跳空过大不追）",
        "依据": "冻结规则 max_gap_up_atr=0.5（原策略用于突破入场）",
        "改变的行为": "放宽会追高，收紧会漏掉强势",
        "风险": "新方案的确认位比箱体上沿低，同样的 0.5A 在相对幅度上更严",
    },
    "entry_price_deviation_cap": {
        "状态": "推荐未批准", "值": 0.015, "类型": "float",
        "含义": "成交价相对确认收盘价的偏离上限 1.5%（超过则放弃）",
        "依据": ("1.5% 是工程上先定的单点值，**不是从分布分位选出来的**。"
                 "研究区间里甲组因超过该门槛被拒的成交 63 笔"
                 "（out/01_共同观察范围_research_final.json 的 `deviation_exceeds_0.015`）"),
        "改变的行为": "越松成交越多、越容易买到滑点",
        "风险": ("1.5% 没有任何寻优依据；换一段行情，被它拒掉的笔数会明显变化，"
                 "而本单没有验证过这个数的影响"),
    },

    # ── 七、成本与仓位（口径冲突，不择一）─────────────────────────────────
    "taker_fee_bps": {
        "状态": "已确定", "值": 5.0, "类型": "float",
        "含义": "单边手续费 5bps",
        "依据": "两套口径一致（策略包与生效视图都是 5bps）",
        "改变的行为": "—", "风险": "无（两套一致）",
    },
    "slippage_bps": {
        "状态": "待决", "值": None, "类型": "float",
        "含义": "单边滑点：策略包 3bps / 生效视图 5bps",
        "依据": "两份文件写着不同值，无批准依据",
        "改变的行为": "改它改所有净额口径",
        "风险": "本单对比里**两套都算**，报告并列，不择一",
    },
    "risk_config": {
        "状态": "待决", "值": None, "类型": "str",
        "含义": "本金与仓位上限：策略包 2000/3仓/30% / 生效视图 5000/10仓/10%",
        "依据": "两份文件写着不同值",
        "改变的行为": "决定名义额与最大持仓数",
        "风险": "本单用 **R 倍数** 做对比，绕开这个冲突；正式回测必须定下来",
    },
    "participation_cap": {
        "状态": "待决", "值": None, "类型": "float",
        "含义": "订单名义额占前一根已闭合 15m 成交额的上限：0.1% / 1%",
        "依据": "两份文件写着不同值",
        "改变的行为": "决定这笔单在真实市场能不能成交",
        "风险": "本单不模拟名义额，只报「触发了多少笔」；可成交性在正式回测里判",
    },
}


def resolve_params(overrides: Optional[dict] = None) -> dict:
    """把 PARAMS 摊平成 {键: 值}；`待决` 键没有值，必须由调用方显式给。

    传了 `待决` 键但没有 overrides ⇒ 抛错（不许用默认值糊过去）。
    """
    out: Dict[str, object] = {}
    missing: List[str] = []
    for k, v in PARAMS.items():
        if v["状态"] == "待决":
            missing.append(k)
            continue
        out[k] = v["值"]
    overrides = dict(overrides or {})
    for k in list(overrides):
        if k not in PARAMS:
            raise KeyError(f"unknown_param: {k}")
    out.update(overrides)
    still = [k for k in missing if k not in overrides]
    if still:
        raise ValueError(
            "待决参数未提供，拒绝运行（不许用默认值）：" + ", ".join(still))
    return out


# ══════════════════════════════════════════════════════════════════════════
# 纯函数：箱体 / 支撑 / 扫低 / 压制位 / 承接 / 确认 / 失效
# ══════════════════════════════════════════════════════════════════════════

def wilder_atr(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float], period: int = 14) -> List[float]:
    """Wilder ATR，与冻结 `features.compute_wilder_atr_series` 逐位同构。"""
    n = len(highs)
    atr = [float("nan")] * n
    if n == 0:
        return atr
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    if n < period:
        return atr
    atr[period - 1] = sum(tr[:period]) / float(period)
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / float(period)
    return atr


def efficiency_ratio(closes: Sequence[float]) -> Optional[float]:
    if len(closes) < 2:
        return None
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path <= 1e-12:
        return None
    return net / path


def build_box(bars_4h: Sequence[dict], i: int, p: dict,
              pre: Optional[dict] = None) -> Optional[dict]:
    """在第 i 根 4h（含）上尝试建档。返回 BoxView 或 None。

    `bars_4h[i]` 必须是**已收盘**的那一根；决策时刻 = open_time + 4h。

    `pre` = `precompute_4h()` 的结果。不传就现场算一遍（慢，仅用于单点核对）；
    批量扫描必须传，否则每根 4h 都重算整条 ATR，复杂度 O(n²)。
    """
    need = max(int(p["box_bars_4h"]), int(p["atr_warmup_bars"]), int(p["atr_period_4h"]) + 1)
    if i + 1 < need:
        return None
    w = int(p["box_bars_4h"])
    if pre is None:
        pre = precompute_4h(bars_4h, p)
    a = pre["atr"][i]
    if a is None or not math.isfinite(a) or a <= 0:
        return None
    hh = pre["roll_max"][i]
    ll = pre["roll_min"][i]
    if hh is None or ll is None:
        return None
    width_atr = (hh - ll) / a
    if not (float(p["box_min_width_atr"]) <= width_atr <= float(p["box_max_width_atr"])):
        return None
    er = pre["er"][i]
    if er is None or er > float(p["er_threshold"]):
        return None
    bar = bars_4h[i]
    return {
        "box_index_4h": i,
        "H": float(hh), "L": float(ll), "A": float(a),
        "er": float(er), "width_atr": float(width_atr),
        "created_open_ms": int(bar["open_time"]),
        "created_ms": int(bar["open_time"]) + MS_4H,     # 决策时刻
        "bars_count": w,
    }


def precompute_4h(bars_4h: Sequence[dict], p: dict) -> dict:
    """一次算完 4h 侧所有滚动量：ATR、窗口最高/最低、窗口 ER。

    与 `build_box` 的逐点计算**必须逐位一致**（见 scripts/02_逐位核对.py）：
    ATR 用同一条递推；最高/最低用单调队列；ER 用「前缀和 + 首末差」。
    """
    w = int(p["box_bars_4h"])
    n = len(bars_4h)
    highs = [b["high"] for b in bars_4h]
    lows = [b["low"] for b in bars_4h]
    closes = [b["close"] for b in bars_4h]
    atr = wilder_atr(highs, lows, closes, int(p["atr_period_4h"])) if n else []
    # 滚动最高/最低（单调双端队列，O(n)）
    roll_max: List[Optional[float]] = [None] * n
    roll_min: List[Optional[float]] = [None] * n
    dq_max, dq_min = [], []
    for i in range(n):
        hv = highs[i]
        if hv is not None:
            while dq_max and (highs[dq_max[-1]] is None or highs[dq_max[-1]] <= hv):
                dq_max.pop()
            dq_max.append(i)
            while dq_max and dq_max[0] <= i - w:
                dq_max.pop(0)
        lv = lows[i]
        if lv is not None:
            while dq_min and (lows[dq_min[-1]] is None or lows[dq_min[-1]] >= lv):
                dq_min.pop()
            dq_min.append(i)
            while dq_min and dq_min[0] <= i - w:
                dq_min.pop(0)
        if i >= w - 1 and dq_max and dq_min:
            roll_max[i] = float(highs[dq_max[0]])
            roll_min[i] = float(lows[dq_min[0]])
    # 滚动 ER：|close[i]-close[i-w+1]| / Σ|相邻差|
    er: List[Optional[float]] = [None] * n
    pref = [0.0] * n
    for i in range(1, n):
        if closes[i] is None or closes[i - 1] is None:
            pref[i] = pref[i - 1]
        else:
            pref[i] = pref[i - 1] + abs(closes[i] - closes[i - 1])
    for i in range(w - 1, n):
        a0, a1 = closes[i - w + 1], closes[i]
        if a0 is None or a1 is None:
            continue
        path = pref[i] - pref[i - w + 1]
        if path <= 1e-12:
            continue
        er[i] = abs(a1 - a0) / path
    return {"atr": atr, "roll_max": roll_max, "roll_min": roll_min, "er": er}


def local_support(bars_1h: Sequence[dict], i: int, p: dict,
                  pre: Optional[dict] = None) -> Optional[float]:
    """扫低根 i **之前** N 根 1h 的最低价。不足 N 根返回 None（不猜）。"""
    if pre is not None:
        v = pre["smin"][i]
        return None if v is None else float(v)
    n = int(p["local_support_bars"])
    if i - n < 0:
        return None
    lows = [bars_1h[k]["low"] for k in range(i - n, i)]
    if any(x is None for x in lows):
        return None
    return float(min(lows))


def precompute_1h(bars_1h: Sequence[dict], p: dict) -> dict:
    """1h 侧滚动量：`smin[i]` = min(low[i-N:i])（**不含当根**，单调队列）。"""
    n = int(p["local_support_bars"])
    m = len(bars_1h)
    smin: List[Optional[float]] = [None] * m
    dq = []
    for i in range(m):
        # 入队的是 i-1（当根不参与自己的支撑判定）
        j = i - 1
        if j >= 0 and bars_1h[j]["low"] is not None:
            lv = bars_1h[j]["low"]
            while dq and bars_1h[dq[-1]]["low"] >= lv:
                dq.pop()
            dq.append(j)
        while dq and dq[0] < i - n:
            dq.pop(0)
        if i >= n and dq:
            smin[i] = float(bars_1h[dq[0]]["low"])
    return {"smin": smin}


def detect_sweep(bars_1h: Sequence[dict], i: int, box: dict, p: dict,
                 pre: Optional[dict] = None) -> Optional[dict]:
    """判定第 i 根 1h 是否为「扫低收回」。

    四条同时成立（每条都只用**已收盘**的信息）：
      1) 存在局部支撑 S_loc，且 S_loc > L（否则就是原策略的破 L 扫低）；
      2) low < S_loc（真的破了）；
      3) low ≥ S_loc − 0.5A（不是真破位）；
      4) close ≥ S_loc + 0.1A（收回）。
    """
    if str(p.get("sweep_object", "local_support")) == "box_low":
        s_loc = float(box["L"])
    else:
        s_loc = local_support(bars_1h, i, p, pre)
        if s_loc is None:
            return None
        if s_loc <= box["L"]:
            return None
    a = box["A"]
    bar = bars_1h[i]
    low, close = bar["low"], bar["close"]
    if low is None or close is None:
        return None
    over = float(p["sweep_max_overshoot_atr"]) * a
    rec = float(p["sweep_min_recovery_atr"]) * a
    if not (low < s_loc):
        return None
    if low < s_loc - over:
        return None
    if close < s_loc + rec:
        return None
    return {
        "bar_index_1h": i,
        "bar_open_ms": int(bar["open_time"]),
        "decided_ms": int(bar["open_time"]) + MS_H,
        "S_loc": s_loc,
        "low": float(low), "high": float(bar["high"]), "close": float(close),
        "overshoot_atr": (s_loc - low) / a,
        "recovery_atr": (close - s_loc) / a,
    }


def _swing_highs(bars_1h: Sequence[dict], lo: int, hi: int, k: int) -> List[int]:
    """在 [lo, hi) 内找摆动高点下标：high 高于左右各 k 根。"""
    out = []
    for j in range(lo + k, hi - k):
        h = bars_1h[j]["high"]
        if h is None:
            continue
        ok = True
        for m in range(1, k + 1):
            a, b = bars_1h[j - m]["high"], bars_1h[j + m]["high"]
            if a is None or b is None or h <= a or h <= b:
                ok = False
                break
        if ok:
            out.append(j)
    return out


def local_resistance(bars_1h: Sequence[dict], i_sweep: int, box: dict, p: dict) -> dict:
    """定出局部压制位 R_loc（只用扫低根**之前**的 1h）。

    返回 dict：`R`（可能为 None）、`method`、`fallback`、`samples`、`reason`。
    合格性（L+gap ≤ R ≤ H−gap）单独判，见 `resistance_qualified`。
    """
    lb = int(p["resistance_lookback_bars"])
    k = int(p["resistance_swing_k"])
    method = str(p["local_resistance_definition"])
    lo = max(0, i_sweep - lb)
    hi = i_sweep
    if hi - lo < 4:
        return {"R": None, "method": method, "fallback": None,
                "samples": 0, "reason": "lookback_too_short"}
    win = [bars_1h[j] for j in range(lo, hi)]
    highs = [b["high"] for b in win if b["high"] is not None]
    if not highs:
        return {"R": None, "method": method, "fallback": None,
                "samples": 0, "reason": "no_high_in_window"}

    if method == "swing_high_k3_lookback48":
        idx = _swing_highs(bars_1h, lo, hi, k)
        if idx:
            j = idx[-1]
            return {"R": float(bars_1h[j]["high"]), "method": method,
                    "fallback": False, "samples": len(idx),
                    "swing_bar_open_ms": int(bars_1h[j]["open_time"]),
                    "reason": "last_swing_high"}
        return {"R": float(max(highs)), "method": method, "fallback": True,
                "samples": 0, "reason": "no_swing_high_fallback_to_window_max"}
    if method == "prior_high_24":
        w = min(24, len(highs))
        return {"R": float(max(highs[-w:])), "method": method, "fallback": False,
                "samples": w, "reason": "prior_high"}
    if method == "box_mid":
        return {"R": float((box["H"] + box["L"]) / 2.0), "method": method,
                "fallback": False, "samples": len(highs), "reason": "box_mid"}
    if method == "box_close_pct70":
        cls = sorted(b["close"] for b in win if b["close"] is not None)
        if not cls:
            return {"R": None, "method": method, "fallback": None,
                    "samples": 0, "reason": "no_close"}
        pos = min(len(cls) - 1, int(round(0.70 * (len(cls) - 1))))
        return {"R": float(cls[pos]), "method": method, "fallback": False,
                "samples": len(cls), "reason": "close_pct70"}
    raise ValueError(f"unknown_resistance_definition: {method}")


def resistance_qualified(r: Optional[float], box: dict, p: dict) -> Tuple[bool, str]:
    if r is None:
        return False, "no_resistance"
    gap = float(p["resistance_min_gap_atr"]) * box["A"]
    if r < box["L"] + gap:
        return False, "too_close_to_L"
    if r > box["H"] - gap:
        return False, "too_close_to_H"
    return True, "ok"


def absorption(bars_1h: Sequence[dict], i: int, p: dict) -> dict:
    """承接 / 卖压：用 1h K 线自带的主动买卖量（不读指标 parquet）。

    · 主动买占比 `tbr = taker_buy_quote_volume / quote_volume`
    · 主动卖出额 `sell = quote_volume − taker_buy_quote_volume`（推导量）
    · 基准 = 前 `absorption_baseline_bars` 根的中位数
    · 承接成立 = tbr ≥ 基准×mult 且 close 位于当根振幅上半区
    · 卖压减弱 = sell ≤ 基准×mult
    """
    n = int(p["absorption_baseline_bars"])
    if i - n < 0:
        return {"available": False, "reason": "baseline_too_short",
                "pass": False, "tbr": None}
    bar = bars_1h[i]
    qv, tbv = bar.get("quote_volume"), bar.get("taker_buy_quote_volume")
    if qv is None or tbv is None or qv <= 0:
        return {"available": False, "reason": "no_taker_fields", "pass": False,
                "tbr": None}
    tbr = float(tbv) / float(qv)
    sells = []
    tbrs = []
    for j in range(i - n, i):
        b = bars_1h[j]
        q, t = b.get("quote_volume"), b.get("taker_buy_quote_volume")
        if q is None or t is None or q <= 0:
            return {"available": False, "reason": "baseline_has_null", "pass": False,
                    "tbr": tbr}
        tbrs.append(float(t) / float(q))
        sells.append(float(q) - float(t))
    base_tbr = sorted(tbrs)[len(tbrs) // 2] if len(tbrs) % 2 else \
        0.5 * (sorted(tbrs)[len(tbrs) // 2 - 1] + sorted(tbrs)[len(tbrs) // 2])
    base_sell = sorted(sells)[len(sells) // 2] if len(sells) % 2 else \
        0.5 * (sorted(sells)[len(sells) // 2 - 1] + sorted(sells)[len(sells) // 2])
    hi, lo = bar["high"], bar["low"]
    rng = (hi - lo) if (hi is not None and lo is not None) else 0.0
    close_pos = ((bar["close"] - lo) / rng) if rng > 0 else 0.5
    sell = float(qv) - float(tbv)
    tbr_ok = tbr >= base_tbr * float(p["absorption_taker_mult"])
    sell_ok = sell <= base_sell * float(p["sell_pressure_mult"])
    pos_ok = close_pos >= 0.5
    return {
        "available": True, "tbr": round(tbr, 6),
        "base_tbr": round(base_tbr, 6),
        "tbr_ratio": round(tbr / base_tbr, 4) if base_tbr else None,
        "sell": round(sell, 2), "base_sell": round(base_sell, 2),
        "sell_ratio": round(sell / base_sell, 4) if base_sell else None,
        "close_pos": round(close_pos, 4),
        "tbr_ok": tbr_ok, "sell_ok": sell_ok, "pos_ok": pos_ok,
        "pass": bool(tbr_ok and sell_ok and pos_ok),
        "reason": ("ok" if (tbr_ok and sell_ok and pos_ok)
                   else ",".join(k for k, v in (("tbr", tbr_ok), ("sell", sell_ok),
                                                ("pos", pos_ok)) if not v)),
    }


def confirm(bars_1h: Sequence[dict], i: int, r_loc: float, box: dict,
            scheme: str, p: dict, ctx: dict) -> Optional[dict]:
    """在已收盘的第 i 根 1h 上判确认。返回 ConfirmView 或 None。

    · 甲：首次 close > R_loc（且收阳，若要求）
    · 乙：甲成立之后**下一根**也 close > R_loc
    · 丙：甲成立后等回抽（low ≤ R_loc + 0.25A），回抽之后再出现 close > R_loc 且收阳
    """
    bar = bars_1h[i]
    close, op = bar["close"], bar["open"]
    if close is None or op is None:
        return None
    bull = bool(p["confirm_requires_bull_close"])
    above = close > r_loc
    if scheme == "4a":
        if above and (close >= op or not bull):
            return {"decided_ms": int(bar["open_time"]) + MS_H, "close": float(close),
                    "stage": "first_close_above"}
        return None
    if scheme == "4b":
        if not ctx.get("first_above_ms"):
            if above and (close >= op or not bull):
                ctx["first_above_ms"] = int(bar["open_time"]) + MS_H
            return None
        if int(bar["open_time"]) + MS_H <= ctx["first_above_ms"]:
            return None
        if above:
            return {"decided_ms": int(bar["open_time"]) + MS_H, "close": float(close),
                    "stage": "second_close_holds"}
        ctx["broke_back_ms"] = int(bar["open_time"]) + MS_H
        return None
    if scheme == "4c":
        if not ctx.get("first_above_ms"):
            if above and (close >= op or not bull):
                ctx["first_above_ms"] = int(bar["open_time"]) + MS_H
            return None
        if int(bar["open_time"]) + MS_H <= ctx["first_above_ms"]:
            return None
        zone = r_loc + float(p["pullback_zone_atr"]) * box["A"]
        deadline = ctx["first_above_ms"] + int(p["pullback_deadline_hours"]) * MS_H
        if not ctx.get("pulled_back"):
            if bar["low"] is not None and bar["low"] <= zone:
                ctx["pulled_back"] = True
                ctx["pullback_ms"] = int(bar["open_time"]) + MS_H
            elif int(bar["open_time"]) + MS_H > deadline:
                ctx["pullback_timeout"] = True
            return None
        if int(bar["open_time"]) + MS_H <= (ctx.get("pullback_ms") or 0):
            return None
        if above and (close >= op or not bull):
            return {"decided_ms": int(bar["open_time"]) + MS_H, "close": float(close),
                    "stage": "pullback_then_turn_up"}
        return None
    raise ValueError(f"unknown_scheme: {scheme}")


def invalidation(bars_1h: Sequence[dict], i: int, box: dict, p: dict,
                 below_count: int) -> Optional[str]:
    """结构失效（与原策略同语义，只看 1h 收盘）。返回原因或 None。"""
    bar = bars_1h[i]
    a = box["A"]
    if bar["low"] is not None and bar["low"] < box["L"] - float(p["invalidate_l_overshoot_atr"]) * a:
        return "low_below_L_minus_overshoot"
    if below_count >= int(p["invalidate_close_below_l"]):
        return "consecutive_closes_below_L"
    return None


def earliest_fill_ms(confirm_decided_ms: int) -> int:
    """确认时刻之后第一根 15m 的 open_time（12:00 确认 ⇒ 12:15）。

    确认时刻本身就是整点（1h 收盘后第一个时刻），所以直接 +15m。
    """
    return int(confirm_decided_ms) + MS_15M


def entry_gate(open_15m: float, confirm_close: float, box: dict, p: dict) -> Tuple[bool, str]:
    """成交前的两道闸：跳空上限、偏离上限。都用**可观测**的 15m 开盘价。"""
    if open_15m is None:
        return False, "no_15m_open"
    if confirm_close is None:
        return False, "no_confirm_close"
    a = box["A"]
    if open_15m > confirm_close + float(p["max_gap_up_atr"]) * a:
        return False, "gap_up_exceeds_0.5A"
    if confirm_close > 0:
        dev = (open_15m - confirm_close) / confirm_close
        if dev > float(p["entry_price_deviation_cap"]):
            return False, f"deviation_exceeds_{p['entry_price_deviation_cap']}"
    return True, "ok"


# ══════════════════════════════════════════════════════════════════════════
# 共同观察范围：箱体候选池（三组与原策略**同一批**候选，不许各筛各的）
# ══════════════════════════════════════════════════════════════════════════

def list_box_candidates(bars_4h: List[dict], p: dict,
                        window: Optional[Tuple[int, int]] = None,
                        pre: Optional[dict] = None) -> List[dict]:
    """逐根 4h 检查建档条件，返回**原始**箱体候选（不做事件生命周期过滤）。

    为什么先列原始候选：三组方案如果各自在「自己的活跃事件」上建档，
    观察范围就各不相同，比较失去意义。先把候选池定死，再让每条规则在
    **同一个池子**里跑自己的生命周期，这样「新增机会」才是可数的。
    """
    if pre is None:
        pre = precompute_4h(bars_4h, p)
    out = []
    for bi in range(len(bars_4h)):
        box = build_box(bars_4h, bi, p, pre)
        if box is None:
            continue
        if window and not (window[0] <= box["created_ms"] < window[1]):
            continue
        out.append(box)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 原策略价格链（BOX→SWEEP(破L)→RETEST→BREAKOUT）——只用于研究区间基线
# ══════════════════════════════════════════════════════════════════════════

def baseline_price_chain(bars_1h: List[dict], box: dict, p: dict,
                         i0: int) -> dict:
    """冻结规则 §3 的**价格链 + 量能**，逐条照搬 state_machine.py。

    ⚠ 不含 OI 门槛：历史 OI 的可得时点不可证明（`fetched_at` 不完整），
    带 OI 门槛等于用一个不可证的量做闸门。此处如实声明为「B 组口径」。
    返回 {state, decided_ms, ...}；state ∈ CANDIDATE/SWEEP/INVALIDATED/EXPIRED/
    MISSED_WITHOUT_RETEST/VOLUME_REJECTED。
    """
    H, L, A = box["H"], box["L"], box["A"]
    below = 0
    sweep = None
    retest = None
    deadline_total = box["created_ms"] + int(p["observe_deadline_hours"]) * MS_H
    sweep_dl = box["created_ms"] + 168 * MS_H
    retest_dl = None
    breakout_dl = None

    def _ret(state, decided, why=None, **kw):
        """统一出口：**任何**终局都要带上 sweep/retest，否则「有过该状态」会被少数。"""
        d = {"state": state, "decided_ms": decided, "why": why,
             "sweep": sweep, "retest": retest}
        d.update(kw)
        return d

    for i in range(i0, len(bars_1h)):
        b = bars_1h[i]
        decided = int(b["open_time"]) + MS_H
        o, h, lo_, c, v = b["open"], b["high"], b["low"], b["close"], b["volume"]
        if c is None or o is None:
            continue
        if decided > deadline_total:
            return _ret("EXPIRED", decided, "total_deadline")
        below = below + 1 if c < L else 0
        if sweep is None:
            if decided > sweep_dl:
                return _ret("EXPIRED", decided, "no_sweep_7d")
            if lo_ is not None and (lo_ < L - 0.5 * A or below >= 2):
                return _ret("INVALIDATED", decided, "break_L")
            if (lo_ is not None and (L - 0.5 * A <= lo_ < L)
                    and c >= L + 0.1 * A and c <= H):
                sweep = {"decided_ms": decided, "low": lo_, "vol": v, "i": i}
                retest_dl = decided + 72 * MS_H
            continue
        if retest is None:
            if decided > retest_dl:
                return _ret("EXPIRED", decided, "no_retest_72h")
            if lo_ is not None and (lo_ < sweep["low"] - 0.1 * A or below >= 2):
                return _ret("INVALIDATED", decided, "break_S")
            if c > H + 0.1 * A:
                return _ret("MISSED_WITHOUT_RETEST", decided, "broke_H_before_retest")
            if (lo_ is not None and L <= lo_ <= L + 0.5 * A and lo_ > sweep["low"]
                    and c >= L + 0.1 * A and c >= o
                    and v is not None and sweep["vol"] and v <= 0.8 * sweep["vol"]):
                retest = {"decided_ms": decided, "low": lo_, "i": i}
                breakout_dl = decided + 168 * MS_H
            continue
        # RETEST → 等突破
        if decided > breakout_dl:
            return _ret("EXPIRED", decided, "no_breakout_7d")
        if lo_ is not None and (lo_ < retest["low"] - 0.1 * A or below >= 2):
            return _ret("INVALIDATED", decided, "break_Lr")
        prev_close = bars_1h[i - 1]["close"]
        level = H + 0.1 * A
        if prev_close is not None and prev_close <= level and c > level:
            vols = [x["volume"] for x in bars_1h[max(0, i - 20):i]
                    if x["volume"] is not None]
            v20 = sorted(vols)[len(vols) // 2] if vols else None
            if v20 is None or v is None or v < 1.5 * v20:
                return _ret("VOLUME_REJECTED", decided, "vol_below_1.5V20",
                            v20=v20, vol=v)
            return _ret("CANDIDATE", decided, None, close=c, level=level,
                        v20=v20, vol=v)
    return _ret("EXPIRED", None, "data_end")


# ══════════════════════════════════════════════════════════════════════════
# 单方案生命周期（单活跃事件 + 24h 冷却），在**同一个箱体候选池**上跑
# ══════════════════════════════════════════════════════════════════════════

def _first_index_after(opens: Sequence[int], ms: int) -> Optional[int]:
    import bisect
    i = bisect.bisect_right(opens, ms)
    return i if i < len(opens) else None


def run_scheme(symbol: str, boxes: List[dict], bars_1h: List[dict],
               scheme: str, p: dict, pre1h: Optional[dict] = None) -> List[dict]:
    """在给定箱体候选池上跑一条方案的生命周期，返回事件列表。

    生命周期与冻结状态机同构：**同一时刻只允许一个活跃事件**，
    事件终结后再冷却 `cooling_hours` 小时；冷却期内出现的箱体候选被跳过
    （记进 `skipped`，保证「候选池总数 = 跑过的事件 + 被跳过的」）。
    """
    if pre1h is None:
        pre1h = precompute_1h(bars_1h, p)
    opens = [b["open_time"] for b in bars_1h]
    events: List[dict] = []
    active_until = 0
    skipped = 0
    for box in boxes:
        if box["created_ms"] < active_until:
            skipped += 1
            continue
        i0 = _first_index_after(opens, box["created_ms"] - MS_H)
        if i0 is None:
            continue
        ev = {"event_id": f"ev_{symbol}_{scheme}_{box['created_ms']}",
              "symbol": symbol, "scheme": scheme,
              "box": box, "state": ST_OBSERVE,
              "state_history": [{"state": ST_OBSERVE, "ms": box["created_ms"]}]}
        deadline = box["created_ms"] + int(p["observe_deadline_hours"]) * MS_H
        below = 0
        sweep = res = absr = None
        ctx: dict = {}
        end_ms = deadline
        i = i0
        while i < len(bars_1h):
            b = bars_1h[i]
            decided = int(b["open_time"]) + MS_H
            if decided > deadline:
                ev["state"] = ST_EXPIRED
                ev["why"] = "observe_deadline"
                end_ms = deadline
                break
            c = b["close"]
            if c is None:
                i += 1
                continue
            below = below + 1 if c < box["L"] else 0
            inv = invalidation(bars_1h, i, box, p, below)
            if inv:
                ev["state"] = ST_INVALIDATED
                ev["why"] = inv
                end_ms = decided
                ev["state_history"].append({"state": ST_INVALIDATED, "ms": decided,
                                            "why": inv})
                break
            if sweep is None:
                sw = detect_sweep(bars_1h, i, box, p, pre1h)
                if sw is None:
                    i += 1
                    continue
                ab = absorption(bars_1h, i, p)
                if bool(p["absorption_is_gate"]) and not ab.get("pass"):
                    ev["state_history"].append(
                        {"state": "SWEEP_REJECTED", "ms": decided,
                         "why": ab.get("reason"), "tbr_ratio": ab.get("tbr_ratio")})
                    i += 1
                    continue
                sweep, absr = sw, ab
                rv = local_resistance(bars_1h, i, box, p)
                ok, why = resistance_qualified(rv.get("R"), box, p)
                rv["qualified"], rv["reason_q"] = ok, why
                res = rv
                ev["state_history"].append({"state": ST_SWEEP, "ms": decided,
                                            "S_loc": sw["S_loc"], "low": sw["low"],
                                            "absorb": ab.get("pass")})
                if not ok:
                    ev["state"] = ST_NO_RESISTANCE
                    ev["why"] = why
                    end_ms = decided
                    ev["state_history"].append({"state": ST_NO_RESISTANCE,
                                                "ms": decided, "why": why,
                                                "R": rv.get("R")})
                    break
                ev["state"] = ST_ARMED
                ev["state_history"].append({"state": ST_ARMED, "ms": decided,
                                            "R": rv["R"]})
                i += 1
                continue
            # 失效只在「曾经收上过压制位」之后才计：ARMED 之后价格本来就在 R 下方，
            # 若无条件计数，第一根就满足「跌回 R 下方」⇒ 全部事件立刻失效（本单踩过）。
            fail_buf = float(p["armed_fail_buffer_atr"]) * box["A"]
            if c > res["R"]:
                ctx["above_seen"] = True
            if ctx.get("above_seen") and c < res["R"] - fail_buf:
                ctx["armed_below"] = ctx.get("armed_below", 0) + 1
            elif c >= res["R"] - fail_buf:
                ctx["armed_below"] = 0
            if ctx.get("armed_below", 0) >= int(p["armed_fail_bars"]):
                ev["state"] = ST_INVALIDATED
                ev["why"] = "closes_below_R_after_armed"
                end_ms = decided
                ev["state_history"].append({"state": ST_INVALIDATED, "ms": decided,
                                            "why": "closes_below_R_after_armed"})
                break
            cf = confirm(bars_1h, i, res["R"], box, scheme, p, ctx)
            if cf:
                ev["state"] = ST_CONFIRMED
                ev["confirm"] = cf
                end_ms = cf["decided_ms"]
                ev["state_history"].append({"state": ST_CONFIRMED,
                                            "ms": cf["decided_ms"],
                                            "stage": cf["stage"]})
                break
            if ctx.get("pullback_timeout"):
                ev["state"] = ST_EXPIRED
                ev["why"] = "pullback_timeout"
                end_ms = deadline
                break
            if ctx.get("broke_back_ms") and scheme == "4b":
                ev["state"] = ST_INVALIDATED
                ev["why"] = "broke_back_below_R"
                end_ms = ctx["broke_back_ms"]
                break
            i += 1
        ev.update({"sweep": sweep, "resistance": res, "absorption": absr,
                   "end_ms": end_ms})
        events.append(ev)
        active_until = end_ms + int(p["cooling_hours"]) * MS_H
    if events:
        events[0]["_skipped_boxes_before_first"] = skipped
    return events


# ══════════════════════════════════════════════════════════════════════════
# 15m 成交与退出（已确定的止损规则，不重新询问）
# ══════════════════════════════════════════════════════════════════════════

def simulate_trade(bars_15m: List[dict], bars_4h: List[dict], confirm_close: float,
                   box: dict, p: dict, slippage_bps: float,
                   fee_bps: float = 5.0) -> dict:
    """按已确定规则撮合：下一根 15m 开盘成交，5% 初始止损，4h 低点×95% 只上移。

    · 成交价 = 该根 15m 开盘 × (1 + 滑点)
    · 初始止损 = **成交价** × 0.95（不是确认收盘价）
    · 跟踪：入场后每根**新收盘**的 4h，候选 = 该根最低价 × 0.95，取三者最高
    · 退出：15m 的 low ≤ 止损 ⇒ 按止损价成交（跳空时按该根开盘，见 `gap_model`）
    · 只用**入场之后**收盘的 4h（入场当根在入场后收盘也可用，按用户决定）
    """
    if not bars_15m:
        return {"status": "no_15m", "reason": "no_bars"}
    cms = int(confirm_close["decided_ms"])
    earliest = earliest_fill_ms(cms)
    entry_bar = None
    for b in bars_15m:
        if int(b["open_time"]) >= earliest:
            entry_bar = b
            break
    if entry_bar is None:
        return {"status": "no_15m", "reason": "no_bar_after_confirm"}
    ok, why = entry_gate(entry_bar["open"], confirm_close["close"], box, p)
    if not ok:
        return {"status": "cancelled", "reason": why,
                "earliest_ms": earliest,
                "bar_open_ms": int(entry_bar["open_time"])}
    slip = float(slippage_bps) / 10000.0
    entry_px = float(entry_bar["open"]) * (1.0 + slip)
    stop0 = entry_px * (1.0 - float(p["initial_stop_pct"]))
    stop = stop0
    trail_log = []
    # 4h 收盘序列（入场后）
    h4 = [b for b in bars_4h if int(b["open_time"]) + MS_4H > int(entry_bar["open_time"])]
    h4_ptr = 0
    exit_px = exit_ms = None
    exit_reason = None
    last_close = None
    for b in bars_15m:
        if int(b["open_time"]) < int(entry_bar["open_time"]):
            continue
        decided = int(b["open_time"]) + MS_15M
        # 先按「已收盘的 4h」上调止损（只上移）
        while h4_ptr < len(h4) and (int(h4[h4_ptr]["open_time"]) + MS_4H) < decided:
            cand = float(h4[h4_ptr]["low"]) * (1.0 - float(p["trailing_low_pct"]))
            if cand > stop:
                stop = cand
                trail_log.append({"ms": int(h4[h4_ptr]["open_time"]) + MS_4H,
                                  "stop": stop})
            h4_ptr += 1
        if b["low"] is not None and float(b["low"]) <= stop:
            # 跳空：开盘已在止损下方 ⇒ 按开盘成交（bar_open 模型，未批准）
            if float(b["open"]) < stop:
                exit_px = float(b["open"]) * (1.0 - slip)
                exit_reason = ("initial_stop_gap" if stop == stop0
                               else "trailing_stop_gap")
            else:
                exit_px = stop * (1.0 - slip)
                exit_reason = ("initial_stop_loss" if stop == stop0
                               else "trailing_stop_loss")
            exit_ms = decided
            break
        last_close = float(b["close"]) if b["close"] is not None else last_close
    if exit_px is None:
        exit_px, exit_ms = last_close, None
        exit_reason = "open_at_data_end"
    fee = float(fee_bps) / 10000.0
    gross = exit_px / entry_px - 1.0
    net = (exit_px * (1.0 - fee)) / (entry_px * (1.0 + fee)) - 1.0
    r_mult = net / float(p["initial_stop_pct"])
    return {
        "status": "filled", "earliest_ms": earliest,
        "entry_bar_open_ms": int(entry_bar["open_time"]),
        "entry_open": float(entry_bar["open"]), "entry_px": entry_px,
        "stop0": stop0, "final_stop": stop,
        "trail_updates": len(trail_log), "trail_log": trail_log[:8],
        "exit_px": exit_px, "exit_ms": exit_ms, "exit_reason": exit_reason,
        "gross_ret": gross, "net_ret": net, "r_multiple": r_mult,
        "held_hours": (None if exit_ms is None
                       else round((exit_ms - int(entry_bar["open_time"])) / MS_H, 2)),
        "slippage_bps": slippage_bps, "fee_bps": fee_bps,
    }


__all__ = ["RULES_VERSION", "SCHEMES", "PARAMS", "resolve_params", "wilder_atr",
           "efficiency_ratio", "build_box", "precompute_4h", "precompute_1h",
           "local_support", "detect_sweep",
           "local_resistance", "resistance_qualified", "absorption", "confirm",
           "invalidation", "earliest_fill_ms", "entry_gate",
           "list_box_candidates", "baseline_price_chain", "run_scheme",
           "simulate_trade", "ST_OBSERVE", "ST_SWEEP", "ST_ARMED",
           "ST_CONFIRMED", "ST_INVALIDATED", "ST_EXPIRED",
           "ST_NO_RESISTANCE", "ST_CANCELLED"]
