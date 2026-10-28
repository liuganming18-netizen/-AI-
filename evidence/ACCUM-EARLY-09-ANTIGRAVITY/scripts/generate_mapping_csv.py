# -*- coding: utf-8 -*-
"""ACCUM-EARLY-09 任务：纠词与主张映射表生成。

严格规则：
1. 逐项列出 08-R1 原句/位置、问题类别、纠正文字、所依据原始证据路径、是否需要撤回数值或结论。
2. 涵盖 7 大类核心纠偏：
   - 回测撮合误称“真实成交”
   - bar_open 撮合误称“真实滑点”
   - 回撤闸门拦截候选误称“拦截 131 笔亏损”
   - 事后分层关联误称“因果反事实验证”
   - 主动买卖量误称“散户追多/CVD”
   - 时间序列未对齐与缺乏可得性证据
   - ARM A FAILED 带病账本横向对比与排名
   - 06-06 适配器误称仅 K 线适配器
"""

import os
import csv

WORKTREE_ROOT = "/Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/evidence_correction_and_review"
OUT_CSV = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-09-ANTIGRAVITY/纠词与主张映射.csv")

mapping_records = [
    {
        "序号": 1,
        "报告文件与位置": "08-R1/README.md 第19行 / 第105行",
        "原句/原始主张": "抽取了 ARM B 4a 方案中已实现的 96 笔真实成交样本，与当时可得的逐分时市场微观指标进行时间戳对齐",
        "问题类别": "概念失真 (把模拟成交误称真实成交)",
        "纠正文字": "抽取了 ARM B 4a 方案中模拟回放生成的 96 笔已实现模拟成交样本；相关微观指标因缺乏实时获取时间戳确证，当时可得性未获证实 (unproven)。",
        "所依据原始证据路径": "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/paper/paper_4a.sqlite3",
        "是否需要撤回数值或结论": "纠正用语边界；撤回‘真实成交’与‘当时可得’主张，更正为‘模拟回放成交’与‘可得性未证实’"
    },
    {
        "序号": 2,
        "报告文件与位置": "08-R1/README.md 第18行 / 第94行",
        "原句/原始主张": "ARM B (gap_model='bar_open') 按照真实跳空开盘价成交 ──> 承担真实物理滑点 ──> 账户真实反映亏损",
        "问题类别": "口径夸大 (把模拟开盘价撮合误称真实滑点)",
        "纠正文字": "ARM B (gap_model='bar_open') 仅表示按回测规则使用对应 15 分钟 bar 开盘价的模拟成交口径，历史 bar 价格仅能证明该模拟输入处于 bar 区间内，不能证明真实市场可成交或存在物理滑点保证。",
        "所依据原始证据路径": "evidence/AUDIT-003-PATCH/config/fixture_rules_06w2_gap_bar_open.json",
        "是否需要撤回数值或结论": "纠正用语边界；撤回‘真实物理滑点’定性，更正为‘bar_open 模拟撮合口径’"
    },
    {
        "序号": 3,
        "报告文件与位置": "08-R1/README.md 第41行 / 第44行 / 第94行",
        "原句/原始主张": "锁定保护，拦截后续 131 笔亏损开仓 (4b 熔断拦截 61 笔，4c 熔断拦截 70 笔)",
        "问题类别": "事实定性错误与重复计算 (把未执行候选断言为亏损，且跨方案简单相加)",
        "纠正文字": "回撤闸门在方案乙 (4b) 拦截了 61 笔委托候选，在方案丙 (4c) 拦截了 70 笔委托候选。两方案去重后唯一被拦截的候选信号为 95 笔（全三方案去重为 99 笔）。被拦截对象是未入场执行的委托候选，严禁断言为已发生亏损，亦不得分配虚构损益。",
        "所依据原始证据路径": "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/paper/paper_4b.sqlite3::paper_blocked / paper_4c.sqlite3::paper_blocked",
        "是否需要撤回数值或结论": "撤回数值与结论；撤回‘131 笔亏损’主张，更正为‘4b 与 4c 去重后 95 笔被拦截委托候选’"
    },
    {
        "序号": 4,
        "报告文件与位置": "08-R1/README.md 第19行 / 第113-114行 / diagnostics_metrics_counterfactual.json",
        "原句/原始主张": "避开拥挤期后策略回撤大幅收缩，亏损收敛 96.3% (-18.94 U vs 拥挤时 -509.75 U)。证实全域因子具备强分化能力。",
        "问题类别": "因果推论违规 (把事后描述性分组包装为策略反事实验证)",
        "纠正文字": "事后按大户多空比指标中位数进行样本分层，属于样本内描述性统计关联，不代表因果效应，更不等于已验证的反事实策略效果；不得仅凭分组差异推断策略引入该指标后必然能够降低亏损。",
        "所依据原始证据路径": "evidence/ACCUM-EARLY-08-ANTIGRAVITY/R1/diagnostics_metrics_counterfactual.json",
        "是否需要撤回数值或结论": "撤回因果结论；保留描述性统计数据，但彻底撤回‘证实全域因子反事实分化能力’与‘避开后回撤收敛’的因果断言"
    },
    {
        "序号": 5,
        "报告文件与位置": "08-R1/README.md 第115-117行 / diagnostics_metrics_counterfactual.json",
        "原句/原始主张": "散户主动追多 (Taker Vol Ratio >= 1.0) ... 散户追高阶段入场极易买在假突破拐点；主动买卖量比 (Taker Vol / CVD)",
        "问题类别": "术语越界与过度解读 (把主动买卖比称为散户行为和 CVD)",
        "纠正文字": "sum_taker_long_short_vol_ratio 仅代表交易所主动买入量与主动卖出量之比，反映主动吃单的方向偏向，不代表‘散户行为’；在未严格构造时序累积净主动买入量序列时，严禁称之为 CVD。",
        "所依据原始证据路径": "框架/backend/metrics_feed.py (TAKER_COLUMN 定义)",
        "是否需要撤回数值或结论": "纠正术语；撤回‘散户追多’与‘CVD’称谓，规范使用‘主动买卖量比’"
    },
    {
        "序号": 6,
        "报告文件与位置": "08-R1/diagnostics_metrics_counterfactual.json / README.md 第109行",
        "原句/原始主张": "全样本基线 96 笔；大户多空比 48+48=96 笔；Taker Vol 比 41+47=88 笔 (隐瞒 8 笔缺口)",
        "问题类别": "样本计数残差与隐匿缺失 (未解释 8 笔为何未入组)",
        "纠正文字": "全量样本 96 笔；Taker 分层中 41 笔 (>=1.0) 净亏损 -368.87 U，47 笔 (<1.0) 净亏损 -68.46 U；剩余 8 笔样本因发生在 2026-10-15 之后，本地 parquet 指标停更且字段为 NULL，产生数据缺失，未归组 8 笔净亏损 -91.37 U。三者合计 -528.69 U，残差 0.00 U 完全复算平账。",
        "所依据原始证据路径": "数据/市场数据/parquet/metrics/binance/um/XTZUSDT/2026.parquet 等 8 笔交易记录",
        "是否需要撤回数值或结论": "撤回不完整样本陈述；全面补充 8 笔缺口名单、缺失原因与逐笔平账对账表"
    },
    {
        "序号": 7,
        "报告文件与位置": "08-R1/README.md 第15行 / 第30-44行对比表",
        "原句/原始主张": "ARM A (FAILED) 与 ARM B (OK) 账本 100% 独立复算完成 ... 实现净盈亏 ARM A -646.69 U vs ARM B -528.69 U ... ARM B 现金结余更高",
        "问题类别": "推论边界违规 (将 FAILED 错误诊断账本用于策略业绩横向比较)",
        "纠正文字": "ARM A 因 20 笔退出成交价格高于当根 bar 最高价越界被拒，状态机判定为 FAILED，产生 10 笔未平仓残局。该账本属于带病损坏账本，仅用于定位 EXEC_PRICE_OUT_OF_RANGE 门禁阻断行为，严禁将其实现损益与 ARM B 进行横向对比、排名或用于策略选择。",
        "所依据原始证据路径": "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_a/raw.json::run_problems",
        "是否需要撤回数值或结论": "撤回横向比较结论；将 ARM A 隔离为纯粹的错误阻断诊断项，完全移出任何绩效评判矩阵"
    },
    {
        "序号": 8,
        "报告文件与位置": "08-R1/README.md 第53-54行",
        "原句/原始主张": "所谓‘06-06 隔离适配器接通冷档’仅适用于 K 线，资金费生产读取仍 100% 依赖热库",
        "问题类别": "事实认定错误 (将资金费适配器误称为 K 线适配器)",
        "纠正文字": "06-06 适配器明确为资金费冷热合并适配器 (funding_events_adapter.py)，并非仅 K 线适配器。它证明了在限定母集与窗口中读取器可合并热库与冷档并逐事件对账。08 报告误称其仅适用于 K 线系事实认定错误，现予纠正。",
        "所依据原始证据路径": "evidence/ACCUM-EARLY-06-06/adapter/funding_events_adapter.py",
        "是否需要撤回数值或结论": "纠正事实认定；明确 06-06 的资金费属性及隔离复算可行性，同时保留生产链尚未接通该适配器的客观事实"
    },
    {
        "序号": 9,
        "报告文件与位置": "08-R1/README.md 第40行 / 第43行",
        "原句/原始主张": "ARM B 4b -161.37 U (25 笔) 触发 20% 回撤熔断；ARM B 4c -174.99 U (26 笔) 触发 20% 回撤熔断",
        "问题类别": "计算依据说明缺失 (未交代回撤分母导致读者误解为本金亏损)",
        "纠正文字": "系统回撤分母定义为历史最高权益峰值 (Peak Equity)，4b 峰值达到 6048.52 U，随后最低跌至 4781.80 U，最大动态回撤达 20.05% (>= 20%)；4c 峰值达 6094.81 U，最低跌至 4752.03 U，最大动态回撤达 22.03% (>= 20%)。两方案均因从浮盈高位回撤触及 20% 阈值而触发熔断锁定，而非本金净亏损达到 20%。",
        "所依据原始证据路径": "框架/backend/paper_engine.py::_peak_equity / equity 及 runs/antigravity_08_arm_b/paper/paper_equity",
        "是否需要撤回数值或结论": "补充计算证明；澄清回撤触发的真实逐步时序与分母机制"
    }
]

with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(mapping_records[0].keys()))
    writer.writeheader()
    writer.writerows(mapping_records)

print(f"Generated 纠词与主张映射.csv with {len(mapping_records)} items: {OUT_CSV}")
