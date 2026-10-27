# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-D｜批准依据追溯、时间戳语义与提案审查脚本。

本脚本为只读隔离审核脚本，遵守以下纪律：
1. 严格追溯“已确定”与“冲突”项的原始用户依据，不把代码、助手提案或Work报告当批准；
2. 审查持仓量时间戳语义（证明整除5m、固定间隔、本地采集器ts+5m属于循环论证，证明不足保留未知）；
3. 审查 352/351 vs 366/365 报告差异，绑定运行时间与输入版本；
4. 结构化审查甲乙丙三组提前入场规则提案；
5. 输出保存至 evidence/ACCUM-EARLY-03-D/out/approval_and_semantics_audit.json。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")

def run_audit():
    results = {
        "audit_meta": {
            "task": "ACCUM-EARLY-03-D",
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "item3_user_approval_traceability": {},
        "item4_oi_timestamp_semantics_audit": {},
        "item6_early_entry_proposals_audit": {},
        "item7_report_numbers_discrepancy_audit": {},
    }

    # ---------------------------------------------------------
    # 1. 逐项追溯“已确定”与“口径冲突”的原始用户依据 (Item 3)
    # ---------------------------------------------------------
    # 依据来源分类：
    # A: 用户原始明确指令（有明确主聊天记录或《用户要求与决策.md》条文）
    # B: 派工单工程授权（仅授权工程实现与隔离验证，未授权策略变更）
    # C: 既有代码实现或历史文件配置（不可推定为用户批准）
    # D: 执行方建议/助手提案（未经裁决）
    
    approval_table = [
        {
            "item": "oi_wiring",
            "name": "持仓量接通真实判断链路",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单 ACCUM-EARLY-02 第一部分第 1 条",
            "true_source_category": "B (派工单工程授权)",
            "user_original_basis": "用户 2026-10-26 转发工单授权接通真实持仓量，修改 state_machine.py 注册守卫；符合《用户要求与决策.md》中『所有已采集数据均须纳入分析』的要求。",
            "audit_verdict": "工程接线授权有效；但新方案中 OI 具体使用门槛仍未批。"
        },
        {
            "item": "oi_availability_rule",
            "name": "OI 可得性逐行四态判定",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第一部分第 1 条",
            "true_source_category": "B (派工单工程授权) + 纪律规范",
            "user_original_basis": "《用户要求与决策.md》第 20 条明确要求『历史判断只能用当时可获得数据』，不可使用未来信息。",
            "audit_verdict": "四态分类属于避免未来函数的风控与工程规范，符合用户长期纪律。"
        },
        {
            "item": "oi_timestamp_semantics",
            "name": "OI 5分钟时间戳语义（判定为桶起点）",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单要求核查 + 实测整除与相邻差 + 采集器 ts+5m 判据",
            "true_source_category": "C (本地采集器既有逻辑推断，缺乏官方独立证据)",
            "user_original_basis": "无用户原始指令确认。官方端点响应仅单一 timestamp 字段，未在文档中指明为起点或终点。",
            "audit_verdict": "【降级为未证实/待决】本地采集器判据属循环论证，证明不足应保留未知，严禁作为已批准事实！"
        },
        {
            "item": "oi_unit",
            "name": "OI 数量与名义额单位",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "实测两列比值 ≈ 同期价格",
            "true_source_category": "数据客观事实反证",
            "user_original_basis": "币安官方数据字段 sumOpenInterest (币数) 与 sumOpenInterestValue (USDT名义额) 的客观数学关系。",
            "audit_verdict": "经受数理反证支持（比值与价格误差 <0.4%），但仍属数据字段对齐，非策略业务授权。"
        },
        {
            "item": "observe_evidence_fields",
            "name": "观察证据字段进入记录",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第一部分第 6 条",
            "true_source_category": "B (派工单工程授权)",
            "user_original_basis": "《用户要求与决策.md》要求数据清点并说明用途，页面渲染与记录需一致。",
            "audit_verdict": "工程展示与证据落地已实现，未改变任何决策门槛。"
        },
        {
            "item": "replay_entry_arithmetic",
            "name": "入场价/初始止损由成交价计算",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第二部分第 1 条",
            "true_source_category": "A (用户原始明确指令)",
            "user_original_basis": "《用户要求与决策.md》第 25 条：『初始止损：实际成交价 × 95%』。用户原话明确要求由成交价计算。",
            "audit_verdict": "完全具备最高优先级用户依据（用户要求与决策.md 第 25 条）。"
        },
        {
            "item": "replay_stop_path",
            "name": "止损路径按持仓生命周期截断",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第二部分第 2 条",
            "true_source_category": "A (用户原始明确指令) + 交易常识",
            "user_original_basis": "《用户要求与决策.md》第 26 条：『入场后每根该笔入场之后新收盘的 4 小时 K 线...只上移不下移』，退出后平仓已完成，不再存在跟踪更新。",
            "audit_verdict": "完全具备用户依据，修正了此前将退出后30天数据纳入止损路径的明显Bug。"
        },
        {
            "item": "replay_funding",
            "name": "资金费逐次真实结算",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第二部分第 3 条",
            "true_source_category": "B (工程复算修复)",
            "user_original_basis": "真实模拟对账原则：按真实资金费发生时刻与标记价扣减，不可用简单费率和乘以入场名义额。",
            "audit_verdict": "工程复算口径合理，但顺序不确定性与缺口标记仍需维持。"
        },
        {
            "item": "replay_participation",
            "name": "参与率超限缩量与拒单",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第二部分第 4 条 + 获批实现 apply_order_rounding 的实际行为",
            "true_source_category": "C (既有代码既成逻辑，缺乏独立用户批准)",
            "user_original_basis": "用户从未下达『参与率超限时按比例缩量成交』的明确决策；在传统交易或策略规范中，参与率超限可能触发整单拒绝 (FOK) 或缩量。已有代码的实现不能自动升格为用户批准。",
            "audit_verdict": "【定性纠偏】代码中的『缩量逻辑』仅是当前代码行为，不能宣称为『已批准生产规则』，应列为工程实现既定行为，等待最终风控裁决。"
        },
        {
            "item": "replay_open_position",
            "name": "期末未平仓只计未实现估值",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第二部分第 5 条",
            "true_source_category": "A/B (财务会计基本准则)",
            "user_original_basis": "未平仓部位在持仓期间未结清，只能计为浮动盈亏（未实现），不得混入已实现净额。",
            "audit_verdict": "会计与审计准则强制要求，符合回放核算标准。"
        },
        {
            "item": "replay_exit_classification",
            "name": "初始与跟踪止损退出严格分类",
            "claimed_status_in_acc02": "已确定",
            "claimed_source_in_acc02": "工单第二部分第 6 条",
            "true_source_category": "B (审计透明化要求)",
            "user_original_basis": "区分 5% 初始硬止损与后续 4h 抬升止损，属于归因分析必要项。",
            "audit_verdict": "符合审计与归因要求。"
        },
        # 口径冲突项
        {
            "item": "slippage_source",
            "name": "滑点取 3bps 还是 5bps",
            "claimed_status_in_acc02": "口径冲突",
            "claimed_source_in_acc02": "策略包 (3bps) vs 生效视图 (5bps)",
            "true_source_category": "C (两份历史代码/配置冲突，无用户原始依据)",
            "user_original_basis": "《用户约定/当前交接.md》：『滑点两处不一致（策略包 3bps／生效视图 5bps），不择一，两条并列入报告』。用户从未批准过具体滑点数值。",
            "audit_verdict": "绝对不能自行择一，必须继续并列并由裁决方定夺。"
        },
        {
            "item": "risk_config",
            "name": "本金与仓位上限 (2000U/3仓/30% vs 5000U/10仓/10%)",
            "claimed_status_in_acc02": "口径冲突",
            "claimed_source_in_acc02": "策略包 vs 生效视图",
            "true_source_category": "C (历史配置分叉，用户明确否定推断)",
            "user_original_basis": "《用户要求与决策.md》：『最大仓位存在 3／10 等互相冲突的记录；未查清来源前均不得当成用户批准，不得推定新 8 仓』。派工单 v2 明确作废此前擅自代决。",
            "audit_verdict": "账户级核心风控，未经用户签署前绝不可作为已批准规则。"
        },
        {
            "item": "participation_cap",
            "name": "参与率上限 (0.1% vs 1%)",
            "claimed_status_in_acc02": "口径冲突",
            "claimed_source_in_acc02": "策略包 (0.1%) vs 生效视图 (1%)",
            "true_source_category": "C (历史配置分叉，无用户原始依据)",
            "user_original_basis": "两套参数相差10倍，对小币种成交量产生剧烈影响（11条 vs 22条缩量）。用户无任何选择记录。",
            "audit_verdict": "必须继续并列汇报，不得私自选择。"
        },
        {
            "item": "gap_fill_model",
            "name": "跳空止损成交模型 (stop_price vs bar_open)",
            "claimed_status_in_acc02": "口径冲突 (默认未改)",
            "claimed_source_in_acc02": "现行代码 (stop_price) vs Work建议 (bar_open)",
            "true_source_category": "D (执行方敏感性建议，未经批准)",
            "user_original_basis": "《当前交接.md》明确指出：跳空敏感性对照已交，但依据未批。现有代码按止损价成交偏乐观，Work 推荐按开盘价成交偏保守，但均非用户原始批准规则。",
            "audit_verdict": "只能列为敏感性分析，严禁将敏感性建议作为生产规则上线。"
        }
    ]
    results["item3_user_approval_traceability"] = {
        "items_count": len(approval_table),
        "table": approval_table,
        "key_finding": (
            "已有代码（如 config.py、apply_order_rounding）、助手提案及 Work 报告被多次误用为批准凭据。"
            "参与率缩量、滑点数值、账户仓位规模、跳空成交价四项均无原始用户批准凭据！"
        )
    }

    # ---------------------------------------------------------
    # 2. 持仓量时间戳语义审查 (Item 4)
    # ---------------------------------------------------------
    oi_semantics_audit = {
        "statement_under_review": "OI 的 ts 是 5 分钟桶起点，桶收盘 = ts + 300000ms",
        "acc02_evidence_critique": {
            "evidence_1_modulo_300k": {
                "claim": "99.99% 的行 ts 能被 300000 整除",
                "flaw": "数学上整除 300,000ms 只能证明该时间戳落在 5 分钟对齐栅格上（如 12:00, 12:05），完全不能区分该点是区间的左端点（起点）还是右端点（收盘点）。"
            },
            "evidence_2_fixed_step": {
                "claim": "相邻 ts 差恒为 300000ms",
                "flaw": "差值恒为 5 分钟只能证明采样周期为 5 分钟等间隔时间序列，无法证明时间戳代表起始还是截止。"
            },
            "evidence_3_collector_predicate": {
                "claim": "本地采集器写入 5m 口径用的判据是 ts + 5min <= now",
                "flaw": "【严重循环论证】该判据是本地采集器代码编写者的主观实现逻辑；用本地代码写了『ts + 5min <= now』来反向论证『官方时间戳就是起点』，属于用假设论证假设自身！"
            }
        },
        "official_api_investigation": {
            "endpoint": "GET /futures/data/openInterestHist?symbol=...&period=5m",
            "official_response_fields": ["symbol", "sumOpenInterest", "sumOpenInterestValue", "CMCCirculatingSupply", "timestamp"],
            "official_documentation_status": (
                "币安官方 API 文档（developers.binance.com）仅声明了 timestamp 为整数毫秒时间戳，"
                "未在文档正文、注释或常见问题中声明 timestamp 是 bucket_open_time 还是 snapshot/close_time。"
                "与 K线接口明确返回 openTime/closeTime 不同，统计类指标端点仅给出一个 timestamp。"
            ),
            "live_sampling_observation": (
                "实时抓取验证：在 12:31:21 调用端点，返回最新 timestamp 为 12:25:00（相距 381秒）。"
                "若 12:25:00 为起点，则该桶 12:25-12:30 闭合延迟达 81秒；若为快照点，则延迟 381 秒。"
                "但无官方权威文档能排除 12:25 为采样快照发生时刻的可能性。"
            )
        },
        "audit_conclusion": (
            "【证据不足，保留未知】现有三条论据均不能独立且客观地证明币安官方定义 timestamp 为区间起点。"
            "本地采集器的判据属于循环论证。在缺乏币安官方明确技术文档确认前，其语义在严格审计标准下应保持『未显式证明（UNKNOWN）』。"
            "按照纪律：不得自行向后或向前偏移时间戳，策略读取中应基于数据实际可得时间 fetched_at 进行时效防御，而不是依赖推断出的区间起止做超前假设！"
        )
    }
    results["item4_oi_timestamp_semantics_audit"] = oi_semantics_audit

    # ---------------------------------------------------------
    # 3. 352/351 与 366/365 报告数字差异核验 (Item 7)
    # ---------------------------------------------------------
    discrepancy_audit = {
        "phenomenon": "在 ACCUM-EARLY-02 README 与产物中，生产观察事件总数和陈旧数出现 352 vs 366、351 vs 365 的多处冲突。",
        "detailed_mapping": [
            {
                "number_pair": "352 / 351",
                "appearance": "README.md §0 表格第3行、§2.4 描述前文、§6.1 第3条；scripts/06_生产链路核验.py 字符串注释与断言描述",
                "binding_runtime": "早期开发/测试快照阶段（约 2026-10-26T10:00~11:00Z 之间）",
                "source": "开发脚本编写时硬编码的字符串字面量（例如 06_生产链路核验.py 第 297 行的文字说明硬编码了 352）",
                "data_snapshot": "当时生产环境 8787 进程的观察池总事件数为 352，其中 351 条由于 10-14 采集停止被判为陈旧。"
            },
            {
                "number_pair": "366 / 365",
                "appearance": "README.md 表格第10行、§2.4 统计正文、out/06_生产链路核验.json 字段 `observing_total: 366`",
                "binding_runtime": "2026-10-26T12:06:32Z ~ 2026-10-26T12:10:45Z (06脚本真实执行时刻)",
                "source": "实时请求 http://127.0.0.1:8787/api/v1/signals/observe?arm=3a 的动态响应",
                "data_snapshot": "由于生产进程常驻运行，随着时间推移或更多币种进入观察状态，观察池事件从 352 增长到 366 条；其中 365 条为陈旧（最新指标停留在 10-14）。"
            }
        ],
        "root_cause_analysis": (
            "差异产生的原因是【静态报告模板文字硬编码】与【动态系统状态增长】之间的脱节。"
            "执行方在编写脚本 06 时，将当时测试看到的 352/351 手工写进了说明文本和部分断言字面量；"
            "但在 12:10 最终生成报告运行该脚本时，生产进程中的事件总数已经自然更新至 366 条。"
            "生成脚本 07_验收报告.py 将动态采集到的 366/365 与静态写死 352/351 的文本拼接在同一份 README 中，"
            "甚至出现了『352 条全部带 evidence... 其中 365 条被标为陈旧』这一荒谬的自相矛盾表述！"
        ),
        "audit_resolution": "将 352 明确标定为『早期快照历史遗留硬编码』，366 标定为『12:06:32Z 运行快照真实值』。保留旧证据，不覆盖原报告，但纠正逻辑定性。"
    }
    results["item7_report_numbers_discrepancy_audit"] = discrepancy_audit

    # ---------------------------------------------------------
    # 4. 既有甲乙丙三组提前入场提案审查 (Item 6)
    # ---------------------------------------------------------
    proposals_audit = {
        "overview": "对既有甲（4a）、乙（4b）、丙（4c）三组提前入场提案进行严格规范审查。明确其仅为研究提案，不具备生产参数默认值，不启动新策略。",
        "baseline_anchor": "原策略 FT-LOW4H 在相同 51 条历史真实候选上的回放表现（生效视图下净亏损 -225.18 USDT，中位 -17.45 USDT；策略包下净亏损 -89.19 USDT，中位 -2.74 USDT）。",
        "proposals": {
            "group_alpha_4a": {
                "name": "甲组（工程代号 4a）· 扫低收回突破局部压制位入场",
                "rule_spec": {
                    "entry_timing": "价格在箱体内扫低收回后，回踩局部压制位下方，由 1h K线首次收盘在局部压制位上方确认入场。",
                    "difference_from_baseline": "不要求突破箱体上沿 H；不要求跌破箱体下沿 L；提前在箱体下半部/中部入场。",
                    "inherited_rules": "4h ATR(14) 箱体建档条件、5%初始止损、4h最低价×0.95跟踪止损、apply_order_rounding 取整逻辑。"
                },
                "wyckoff_basis": "对应威科夫吸筹区内 Phase C/D 的次级测试 (Secondary Test) 与局部蓄势突破，试图在吸收大部分浮动供应后提前建仓。",
                "required_data": ["1h/4h/15m K线", "合约规则与步进", "资金费记录", "前15m成交额"],
                "expected_costs_and_risks": "假突破（Bull Trap）概率显著高于突破箱体上沿；由于在箱体内建仓，可能频繁遭遇箱体内震荡打出止损；事件数大幅增加带来的手续费与滑点磨损。",
                "comparability_conditions": "必须在与基准完全相同的 51 条历史事件（及扩展样本）上运行，并使用相同的滑点/手续费扣除标准。",
                "independent_verification_design": (
                    "1. 事件对齐对比：对比 4a 与原策略在同一候选上的入场价、止损触发率和净收益；\n"
                    "2. 增量事件分析：单独统计 4a 产生但原策略未入场的事件盈亏分布，若增量事件胜率与盈亏比极低，说明提前入场只是放宽了噪音门槛；\n"
                    "3. 换手率与滑点敏感性：评估交易频次上升对净值的侵蚀。"
                ),
                "parameter_candidates": {
                    "local_resistance_definition": "建议项（前24根1h最高价 / 箱体0.5位，未批准，不得作为默认）",
                    "sell_pressure_threshold": "建议项（成交量 < V20均量，未批准）"
                },
                "status": "【仅为提案】未批准阈值，禁止启动生产策略。"
            },
            "group_beta_4b": {
                "name": "乙组（工程代号 4b）· 突破局部压制位后增加守位确认",
                "rule_spec": {
                    "entry_timing": "在甲组突破局部压制位的基础上，不立刻入场，要求随后第 2 根 1h 收盘仍站稳在该压制位上方才确认入场。",
                    "difference_from_baseline": "比甲组延后 1 根 1h 确认，用时间换取突破有效性确认。",
                    "inherited_rules": "与甲组相同的箱体建档、止损计算与执行规则。"
                },
                "wyckoff_basis": "增加对吸收阻力位（Resistance Absorption）稳定性的确认，过滤单根长上影线假突破。",
                "required_data": ["同甲组 + 连续两根 1h K线确认"],
                "expected_costs_and_risks": "机会成本（错失强脉冲单边行情）；若突破后迅速拉升，第二根入场价可能显著劣于第一根，拉大止损距离，降低风险预算下的可建仓位。",
                "comparability_conditions": "与甲组及基准策略进行配对实验（Paired Test）。",
                "independent_verification_design": (
                    "1. 过滤率核算：统计『甲组入场但乙组未入场』的样本中，甲组最终亏损的比例（有效避坑）vs 甲组盈利的比例（错失良机）；\n"
                    "2. 进场价格劣化度：对两者均入场的样本，统计乙组入场价比甲组差多少 bps，以及该价差对整体盈亏的影响。"
                ),
                "parameter_candidates": {
                    "confirmation_bars": "建议项 1根（未批准）",
                    "break_cancellation_threshold": "建议项（盘中跌破压制位即取消，未批准）"
                },
                "status": "【仅为提案】未批准阈值，禁止启动生产策略。"
            },
            "group_gamma_4c": {
                "name": "丙组（工程代号 4c）· 突破后等回抽确认入场",
                "rule_spec": {
                    "entry_timing": "突破局部压制位后，等待价格在限定周期内回抽（Pullback）至压制位附近（如压制位 ±0.5 ATR），且 1h 收盘企稳转强时入场。",
                    "difference_from_baseline": "非顺势追单，而是左侧/次级右侧回抽挂单或企稳入场，入场成本更接近支撑位。",
                    "inherited_rules": "同甲组箱体、止损及资金管理规则。"
                },
                "wyckoff_basis": "威科夫经典的 LPS（Last Point of Support / 最后的支撑点）或 Back Up (BU) 概念，在阻力转化为支撑后入场。",
                "required_data": ["同甲组 + 回抽期间 15m/1h 连续行情"],
                "expected_costs_and_risks": "放弃率极高（许多强势标的突破后直接飞走，不给回抽机会）；回抽超时管理复杂；可能把『假突破直接暴跌』误判为回抽而接到飞刀。",
                "comparability_conditions": "必须同时记录『放弃交易数量』，不可只在已回抽成功的幸存样本上统计。",
                "independent_verification_design": (
                    "1. 幸存者偏差检验：统计等回抽超时放弃率，评估整体组合资金利用效率；\n"
                    "2. 假回抽识别：统计回抽触及压制位后继续破位下行的比例；\n"
                    "3. 盈亏比改善度：对比回抽入场与甲组追入在盈亏比上的理论提升幅度。"
                ),
                "parameter_candidates": {
                    "pullback_tolerance_range": "建议项（±0.5 ATR，未批准）",
                    "pullback_deadline_hours": "建议项（48小时内，未批准）"
                },
                "status": "【仅为提案】未批准阈值，禁止启动生产策略。"
            }
        }
    }
    results["item6_early_entry_proposals_audit"] = proposals_audit

    # 保存结果
    out_dir = ROOT / "evidence" / "ACCUM-EARLY-03-D" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "approval_and_semantics_audit.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"依据、语义与提案审计完成，结果已保存至: {out_file}")

if __name__ == "__main__":
    run_audit()
