# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜06 生成《规则接口说明》（人读版）。

为什么自动生成而不是手写
------------------------
手写会漂移：参数改了、文档没改，下一个人就按旧文档做。
本脚本从 `rules/accum_early_rules.py` 的 `PARAMS` 与函数签名现读现写，
再加一张**数据字段对应表**（每个判定用哪个字段、来自哪张表、什么时候可得）。

输出：02_规则接口.md
"""
from __future__ import annotations

import inspect
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "rules"))
import accum_early_rules as R                      # noqa: E402

OUT = HERE.parents[1]


def iso(ms=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime((ms or time.time() * 1000) / 1000))


#: 判定 → 真实字段（这是工单第 1 条要求的东西，必须逐条落到列名）
FIELD_MAP = [
    ("吸筹观察形成（4h 箱体）",
     "`high` / `low` / `close`（4h）",
     "`数据/市场数据/parquet/candles/binance/um/<标的>/4h/*.parquet`；"
     "热库 `candles` 视图（= candle_versions JOIN candle_heads，头版本）",
     "4h 收盘（决策时刻 = open_time + 4h）",
     "冷档 `available_at` 填充率 **0%** ⇒ 只能声明「计算不超前」，"
     "不能声明「已证明可得时点」"),
    ("局部支撑 S_loc",
     "前 24 根 `low`（1h）的最小值",
     "同上，`1h`",
     "1h 收盘",
     "同上"),
    ("扫低收回",
     "当根 `low` / `close`（1h）",
     "同上，`1h`",
     "1h 收盘",
     "同上"),
    ("局部压制位 R_loc",
     "扫低根之前 48 根 `high`（1h）；摆动高点判定还要看左右各 3 根的 `high`",
     "同上，`1h`",
     "1h 收盘（**只用扫低根之前**的 bar）",
     "同上"),
    ("承接（主动买占比）",
     "`taker_buy_quote_volume` / `quote_volume`（1h）",
     "同上，`1h`",
     "1h 收盘",
     "两个字段都是交易所原始列；`taker_sell = quote_volume − taker_buy_quote_volume` "
     "是**推导量**"),
    ("卖压减弱",
     "`quote_volume − taker_buy_quote_volume`（1h，推导）",
     "同上，`1h`",
     "1h 收盘",
     "同「承接」"),
    ("确认",
     "`close` / `open`（1h）",
     "同上，`1h`",
     "1h 收盘",
     "同上"),
    ("成交",
     "`open`（15m）",
     "同上，`15m`；热库 15m 从 2026-09-12 起，更早只能读冷档",
     "确认后第一根 15m 的 open_time",
     "热库 `available_at` 约半填充；冷档 0%"),
    ("跟踪止损",
     "`low`（4h）",
     "同上，`4h`",
     "4h 收盘，且必须**严格早于**当前 15m 决策时刻",
     "若用当根 4h 的最低价去判当根 15m 是否止损 = 偷看同一瞬间"),
    ("持仓量（可选，当前不进门槛）",
     "`open_interest`（5m，币数）；`open_interest_value`（USDT 名义额）",
     "`oi_history`（热库）/ `parquet/oi`（冷档）",
     "桶收盘 = ts + 300000ms，再叠加发布延迟",
     "`fetched_at` 实测 100% 空 ⇒ 可得性 `unproven`；"
     "**本单不把 OI 放进任何门槛**"),
    ("5m 主动买卖 / 多空比（当前不进门槛）",
     "`sum_taker_long_short_vol_ratio` 等 5 列",
     "`parquet/metrics/binance/um/<标的>/<年>.parquet`",
     "只有 `ts`（5m 桶起点），**无发布时点、无抓取时点**",
     "`unproven`；且实测最新一根停在 2026-10-14T23:55Z ⇒ 只作证据，不进决策"),
]

FUNCS = [
    ("build_box", "在某一根 4h 上尝试建档", "BoxView 或 None"),
    ("precompute_4h", "一次算完 4h 侧滚动量（ATR / 窗口最高最低 / ER）", "dict"),
    ("precompute_1h", "一次算完 1h 侧滚动量（前 N 根最低）", "dict"),
    ("local_support", "局部支撑 S_loc", "float 或 None"),
    ("detect_sweep", "扫低收回判定", "SweepView 或 None"),
    ("local_resistance", "局部压制位 R_loc", "ResistanceView"),
    ("resistance_qualified", "压制位是否合格（离箱底/箱顶各有空间）", "(bool, 原因)"),
    ("absorption", "承接与卖压判定", "AbsorptionView"),
    ("confirm", "甲/乙/丙三组各自的确认判定", "ConfirmView 或 None"),
    ("invalidation", "结构失效判定", "原因 或 None"),
    ("earliest_fill_ms", "确认后最早可成交时刻", "int（毫秒）"),
    ("entry_gate", "成交前的跳空/偏离闸门", "(bool, 原因)"),
    ("list_box_candidates", "列出箱体候选池（三组共用）", "list[BoxView]"),
    ("baseline_price_chain", "原策略价格链（研究区间基线用，不含 OI）", "dict"),
    ("run_scheme", "在候选池上跑一条方案的生命周期", "list[EventTrace]"),
    ("simulate_trade", "按已确定止损规则撮合 15m", "dict"),
]


def main() -> int:
    md = []
    md.append("# ACCUM-EARLY-04-A｜可执行规则接口 v1")
    md.append("")
    md.append(f"- 生成时刻（UTC）：{iso()}")
    md.append(f"- 代码：`evidence/ACCUM-EARLY-04-A/rules/accum_early_rules.py`")
    md.append(f"- 版本：`{R.RULES_VERSION}`（这个串变了就必须整批重算）")
    md.append("- **本文件不是批准书。** 参数分四类：已确定 / 沿用 / 推荐未批准 / 待决，"
              "逐项标在参数表里。")
    md.append("")
    md.append("## 1 一句话说清这条规则")
    md.append("")
    md.append("4h 上先认出一个横盘箱体（吸筹观察）→ 1h 上等一次「跌破局部支撑又收回」"
              "（扫低收回）→ 在同一时刻定出一个**低于箱顶的**局部压制位 → "
              "1h 收盘收上该位即确认 → 下一根 15m 开盘成交。")
    md.append("")
    md.append("三组的差别只在确认那一步：")
    md.append("")
    for k, v in R.SCHEMES.items():
        md.append(f"- **{v['label']}（`{k}`）**：{v['confirm']}")
    md.append("")
    md.append("## 2 时点语义（四个时刻必须分开）")
    md.append("")
    md.append("| 时刻 | 定义 | 用途 |")
    md.append("|---|---|---|")
    md.append("| 区间起点 | 4h/1h/15m 的 `open_time` | 只用来定位 bar，**不是**决策时刻 |")
    md.append("| 收盘时刻 | `open_time + 周期` | bar 走完 |")
    md.append("| 决策时刻 | 收盘时刻（本项目定义：收盘后第一个时刻） | 允许读哪些数据的分界 |")
    md.append("| 成交时刻 | 确认后第一根 15m 的 `open_time` | 12:00 确认 ⇒ 12:15 成交，"
              "**不回填 12:00** |")
    md.append("")
    md.append("## 3 状态机")
    md.append("")
    md.append("```")
    md.append("OBSERVE ──扫低收回──> SWEEP ──定出压制位──> ARMED")
    md.append("   │                     │                    │")
    md.append("   │                     └──压制位不合格──> NO_RESISTANCE（不是失败，是没空间）")
    md.append("   ├──结构破位（破 L−0.5A 或连续 2 根收盘破 L）──> INVALIDATED")
    md.append("   └──超过观察期限──> EXPIRED")
    md.append("")
    md.append("ARMED ──1h 收盘收上 R──> CONFIRMED（甲）")
    md.append("      ├──再守一根仍在上方──> CONFIRMED（乙）")
    md.append("      ├──回抽到 R+0.25A 附近后再度收上──> CONFIRMED（丙）")
    md.append("      └──收上后又连续 2 根跌回 R−0.1A 下方──> INVALIDATED")
    md.append("")
    md.append("CONFIRMED ──下一根 15m 开盘──> FILLED")
    md.append("          └──跳空超 0.5A 或偏离超 1.5%──> CANCELLED")
    md.append("```")
    md.append("")
    md.append("## 4 每个判定对应的真实字段")
    md.append("")
    md.append("| 判定 | 用哪些字段 | 来自哪 | 什么时候可得 | 可得性说明 |")
    md.append("|---|---|---|---|---|")
    for a, b, c, d, e in FIELD_MAP:
        md.append(f"| {a} | {b} | {c} | {d} | {e} |")
    md.append("")
    md.append("## 5 函数一览（纯函数，无 IO）")
    md.append("")
    md.append("| 函数 | 做什么 | 返回 |")
    md.append("|---|---|---|")
    for fn, desc, ret in FUNCS:
        obj = getattr(R, fn, None)
        sig = str(inspect.signature(obj)) if obj else "()"
        md.append(f"| `{fn}{sig}` | {desc} | {ret} |")
    md.append("")
    md.append("调用顺序（最短可跑示例）：")
    md.append("")
    md.append("```python")
    md.append("p = R.resolve_params({'slippage_bps': 3.0, 'risk_config': 'x',")
    md.append("                       'participation_cap': 0.001})   # 待决项必须显式给")
    md.append("pre4 = R.precompute_4h(bars_4h, p)")
    md.append("boxes = R.list_box_candidates(bars_4h, p, (w0, w1), pre4)")
    md.append("evs = R.run_scheme('BTCUSDT', boxes, bars_1h, '4a', p)")
    md.append("tr = R.simulate_trade(bars_15m, bars_4h, ev['confirm'], ev['box'], p, 3.0)")
    md.append("```")
    md.append("")
    md.append("## 6 参数表（43 项，按状态分组）")
    md.append("")
    for st in ("已确定", "沿用", "推荐未批准", "待决"):
        items = [(k, v) for k, v in R.PARAMS.items() if v["状态"] == st]
        md.append(f"### 6.{'一二三四'[('已确定','沿用','推荐未批准','待决').index(st)]} "
                  f"{st}（{len(items)} 项）")
        md.append("")
        md.append("| 参数 | 值 | 含义 | 依据 | 改变的行为 | 风险 |")
        md.append("|---|---|---|---|---|---|")
        for k, v in items:
            md.append("| `{}` | `{}` | {} | {} | {} | {} |".format(
                k, v["值"], v["含义"], v["依据"], v["改变的行为"], v["风险"]))
        md.append("")
    md.append("## 7 这个接口**不能**拿来做什么")
    md.append("")
    md.append("1. 不能拿它宣称「提前入场策略已实现」——20 项参数只有推荐没有批准；")
    md.append("2. 不能拿研究区间的 R 合计当绩效——样本是全池、多头、含大量亏损笔，"
              "只说明「这套规则在这个区间不赚钱」；")
    md.append("3. 不能把它当生产代码——它不写库、不下单、不做步进取整、不算资金费；")
    md.append("4. 不能引用它的 ATR 说「与生产逐位一致」——Wilder ATR 是递推，"
              "起点不同会有 <0.5% 残差，已在 02 号脚本里量化。")
    md.append("")
    (OUT / "02_规则接口.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("written", OUT / "02_规则接口.md", len("\n".join(md)), "chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
