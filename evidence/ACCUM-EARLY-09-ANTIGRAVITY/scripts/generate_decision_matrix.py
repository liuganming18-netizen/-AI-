# -*- coding: utf-8 -*-
"""ACCUM-EARLY-09 任务 E：全域 17 维数据到决策矩阵。

严格规则：
1. 逐数据字段列出：来源/表或文件、时间语义、窗口覆盖、逐行可得性证据、实际读取位置、
   是否改变观察/候选/拒绝/入场/退出/结算决策、缺失及限制。
2. 明确区分：实际参与决策、仅结算参与、仅用于事后描述、不可得、未接入、不适用、尚未证实。
3. 任何 unavailable / unproven 都保留缺口，不伪造。不自行新增过滤门槛或策略定义。
"""

import os
import csv

WORKTREE_ROOT = "/Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/evidence_correction_and_review"
OUT_CSV = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-09-ANTIGRAVITY/数据到决策矩阵.csv")

fields_data = [
    {
        "字段/数据项": "4h K线 (OHLCV)",
        "底层来源/表或文件": "market_v2.sqlite3 (realtime_klines / candle_versions) + Parquet 冷档",
        "时间语义": "4小时桶起点 (ts)，桶收盘为 ts + 14,400,000 ms",
        "回测窗口覆盖": "100% 覆盖 (2026-06-27 至 2026-10-26)",
        "逐行可得性证据": "部分可得；历史冷档无 fetched_at，热库部分为实时归档，整体按 unproven 审慎标注",
        "代码实际读取位置": "accum_early_entry.py::LimitedFeed.load('4h', ...)",
        "参与决策环节": "实际参与决策",
        "决策影响机制": "识别 4h 级别底部箱体、阻力位、支撑位，决定是否生成积累区候选与突破观察",
        "缺失及限制说明": "无缺失；强依赖固定 4h 周期边界，存在跨周期收线对齐差异"
    },
    {
        "字段/数据项": "1h K线 (OHLCV)",
        "底层来源/表或文件": "market_v2.sqlite3 + Parquet 冷档",
        "时间语义": "1小时桶起点 (ts)，桶收盘为 ts + 3,600,000 ms",
        "回测窗口覆盖": "100% 覆盖",
        "逐行可得性证据": "部分可得；冷档无实时确证，标记为 unproven",
        "代码实际读取位置": "accum_early_entry.py::LimitedFeed.load('1h', ...)",
        "参与决策环节": "实际参与决策",
        "决策影响机制": "用于 1h 级别放量确认与突破信号触发，驱动生成入场候选信号",
        "缺失及限制说明": "无缺失；仅作技术形态确认"
    },
    {
        "字段/数据项": "15m K线 (OHLCV)",
        "底层来源/表或文件": "market_v2.sqlite3 + Parquet 冷档 (MergedReader)",
        "时间语义": "15分钟桶起点 (open_time)，收盘为 close_time (ts + 899,999 ms)",
        "回测窗口覆盖": "100% 覆盖",
        "逐行可得性证据": "部分可得；实盘具备实时流，历史冷档为批量归档，标记为 unproven",
        "代码实际读取位置": "accum_early_entry.py / paper_engine.py::close_at_price / check_trailing_stop",
        "参与决策环节": "实际参与决策与结算",
        "决策影响机制": "驱动入场模拟撮合 (bar_open)、跟踪止损逐 bar 步进检测、出场成交撮合与价格门禁区间校验 (bar_low, bar_high)",
        "缺失及限制说明": "撮合成交为模拟回放成交，按 bar_open 口径执行，非真实撮合挂单成交"
    },
    {
        "字段/数据项": "资金费率 (funding_rate)",
        "底层来源/表或文件": "market_v2.sqlite3::funding_history (生产) / funding_events_adapter.py (隔离)",
        "时间语义": "每 8h / 4h 结算时刻毫秒 (funding_time)",
        "回测窗口覆盖": "热库覆盖至 2026-10-26；冷档覆盖至 2026-10-14",
        "逐行可得性证据": "不可得证实；2026-10-15 前数据系 binance.archive 离线批量回补，fetched_at 为 2026-10-15，标记为 unproven",
        "代码实际读取位置": "paper_engine.py::_settle_funding (生产只读热库)",
        "参与决策环节": "仅结算参与",
        "决策影响机制": "仅在持仓期间计算资金费扣除 (amount = qty * mark * rate)，冲减现金与已实现净损益；不改变开仓、观察或出场信号",
        "缺失及限制说明": "生产读取链未接通冷档；冷热合并在 ARM B 4a 实际持仓中传导残差为 0.00 U"
    },
    {
        "字段/数据项": "标记价格 (mark_price)",
        "底层来源/表或文件": "funding_history 表中伴随资金费记录",
        "时间语义": "结算时刻标记价格",
        "回测窗口覆盖": "同资金费覆盖",
        "逐行可得性证据": "不可得证实；前期使用 1h 收盘价近似替代，标记为 unproven",
        "代码实际读取位置": "paper_engine.py::_settle_funding",
        "参与决策环节": "仅结算参与",
        "决策影响机制": "作为资金费结算名义乘数计算 funding_paid；不参与任何交易决策",
        "缺失及限制说明": "非真实撮合实时 mark_price，历史数据存在以 K 线价格替代口径"
    },
    {
        "字段/数据项": "合约规格 (07_526币保守执行近似规格.csv)",
        "底层来源/表或文件": "数据/研究输入/外部迁移_2026-10-16/本次离线决策_2026-10-12/07_526币保守执行近似规格.csv",
        "时间语义": "2026-10-11T12:48:51Z 单一时点快照",
        "回测窗口覆盖": "单点快照贯穿全程 (121天全区间固定)",
        "逐行可得性证据": "不可得时序；官方元数据明确标注“历史变化已证明=False，用途=近似回测”",
        "代码实际读取位置": "accum_early_entry.py::apply_order_rounding / min_notional",
        "参与决策环节": "实际参与决策",
        "决策影响机制": "对候选开仓订单的名义金额 (min_notional) 与下单数量精度 (stepSize) 进行硬性约束与过滤",
        "缺失及限制说明": "缺乏历史规格演化时间序列，历史规则变更未获证实"
    },
    {
        "字段/数据项": "前置成交额 (previous_15m_quote_cap)",
        "底层来源/表或文件": "15m K线 quote_volume",
        "时间语义": "入场前一根 15m K线",
        "回测窗口覆盖": "100% 覆盖",
        "逐行可得性证据": "随 15m K线，标记为 unproven",
        "代码实际读取位置": "paper_engine.py::economic_engine 规则定义",
        "参与决策环节": "实际参与决策 (风控限额)",
        "决策影响机制": "单笔入场名义金额不得超过前一根 15m 成交额的 1%，超过则截断或拒单",
        "缺失及限制说明": "仅作为流动性上限保护，不改变方向信号"
    },
    {
        "字段/数据项": "未平仓合约量 (OI / sum_open_interest)",
        "底层来源/表或文件": "数据/市场数据/parquet/metrics/binance/um/<SYMBOL>/2026.parquet + market_v2.sqlite3::oi_history",
        "时间语义": "5分钟桶起点 (ts)",
        "回测窗口覆盖": "parquet 覆盖至 2026-10-14 23:55；热库覆盖更长",
        "逐行可得性证据": "无逐行发布时点与抓取时点确证，依 metrics_feed 规范统一标置 available_at=null, availability='unproven'",
        "代码实际读取位置": "未在 accum_early_entry.py 或 paper_engine.py 中读取",
        "参与决策环节": "仅用于事后描述",
        "决策影响机制": "当前策略核心完全未接入 OI 数据，对观察、候选、入场、退出决策无任何影响",
        "缺失及限制说明": "08-R1 报告将 OI 列入“具备反事实分化能力”系事后统计推论，未接入实测，不得宣称已验证"
    },
    {
        "字段/数据项": "大户持仓量多空比 (top_position_ls_ratio)",
        "底层来源/表或文件": "Parquet metrics/<SYMBOL>/2026.parquet (sum_toptrader_long_short_ratio)",
        "时间语义": "5分钟桶起点 (ts)",
        "回测窗口覆盖": "覆盖至 2026-10-14 23:55；2026-10-15 之后停更",
        "逐行可得性证据": "无发布时点与实时抓取确证，available_at=null, availability='unproven'",
        "代码实际读取位置": "仅在 08-R1 事后诊断脚本中离线读取",
        "参与决策环节": "仅用于事后描述",
        "决策影响机制": "未接入策略决策链，不改变任何开仓或出场动作；事后中位数分层仅为描述性统计关联，严禁断言因果性",
        "缺失及限制说明": "2026-10-14 之后停更，后 8 笔开仓匹配到的数据存在高达数天滞后，不可得"
    },
    {
        "字段/数据项": "大户账户数多空比 (count_toptrader_long_short_ratio)",
        "底层来源/表或文件": "Parquet metrics/<SYMBOL>/2026.parquet",
        "时间语义": "5分钟桶起点 (ts)",
        "回测窗口覆盖": "覆盖至 2026-10-14 23:55",
        "逐行可得性证据": "无抓取确证，available_at=null, availability='unproven'",
        "代码实际读取位置": "未接入",
        "参与决策环节": "未接入",
        "决策影响机制": "完全不参与策略任何环节",
        "缺失及限制说明": "仅作为归档指标储备"
    },
    {
        "字段/数据项": "主动买卖量比 (sum_taker_long_short_vol_ratio)",
        "底层来源/表或文件": "Parquet metrics/<SYMBOL>/2026.parquet",
        "时间语义": "5分钟桶起点 (ts)",
        "回测窗口覆盖": "覆盖至 2026-10-14 23:50；2026-10-14 23:55 之后字段全为 NULL",
        "逐行可得性证据": "无抓取确证，available_at=null, availability='unproven'",
        "代码实际读取位置": "仅在 08-R1 事后诊断脚本中离线读取",
        "参与决策环节": "仅用于事后描述",
        "决策影响机制": "未接入策略决策链；事后分层仅为描述性关联，非散户行为，未构成 CVD 序列，严禁因果推论",
        "缺失及限制说明": "2026-10-14 之后该字段在本地 parquet 中为 NULL，导致 8 笔样本缺失而未进入 08-R1 Taker 分层"
    },
    {
        "字段/数据项": "聪明钱快照 (smart_money)",
        "底层来源/表或文件": "框架/backend/快照/smart_money/*.json",
        "时间语义": "2026-10-27 单点快照",
        "回测窗口覆盖": "仅为当前运行快照，无历史时间序列",
        "逐行可得性证据": "历史窗口完全不可得",
        "代码实际读取位置": "未接入策略决策",
        "参与决策环节": "未接入",
        "决策影响机制": "不参与回测决策",
        "缺失及限制说明": "无历史回溯时序数据"
    },
    {
        "字段/数据项": "盘口挂单/深度 (L2 OrderBook / Depth)",
        "底层来源/表或文件": "无本地持久化存储",
        "时间语义": "实时盘口",
        "回测窗口覆盖": "不可得 (0%)",
        "逐行可得性证据": "完全不可得",
        "代码实际读取位置": "未接入",
        "参与决策环节": "未接入",
        "决策影响机制": "回测采用 bar_open 模拟撮合，未接入盘口深度",
        "缺失及限制说明": "不支持真实挂单撮合还原"
    },
    {
        "字段/数据项": "全市场市值与板块资料 (Symbol Supply & Category)",
        "底层来源/表或文件": "market_v2.sqlite3::symbol_supply / symbol_classification",
        "时间语义": "静态配置快照",
        "回测窗口覆盖": "静态表格",
        "逐行可得性证据": "无历史变更版本追踪",
        "代码实际读取位置": "未接入 accum_early_entry.py 选币或开仓决策",
        "参与决策环节": "未接入",
        "决策影响机制": "不改变 40 标的池内的交易决策",
        "缺失及限制说明": "未参与动态选币过滤"
    },
    {
        "字段/数据项": "散户账户多空比 (retail_account_ls_ratio)",
        "底层来源/表或文件": "Parquet metrics/<SYMBOL>/2026.parquet",
        "时间语义": "5分钟桶起点 (ts)",
        "回测窗口覆盖": "覆盖至 2026-10-14 23:55",
        "逐行可得性证据": "无发布确证，available_at=null, availability='unproven'",
        "代码实际读取位置": "未接入",
        "参与决策环节": "未接入",
        "决策影响机制": "不参与任何决策",
        "缺失及限制说明": "未接入策略"
    },
    {
        "字段/数据项": "全局账户多空比 (count_long_short_ratio)",
        "底层来源/表或文件": "Parquet metrics/<SYMBOL>/2026.parquet",
        "时间语义": "5分钟桶起点 (ts)",
        "回测窗口覆盖": "覆盖至 2026-10-14 23:55",
        "逐行可得性证据": "无发布确证，available_at=null, availability='unproven'",
        "代码实际读取位置": "未接入",
        "参与决策环节": "未接入",
        "决策影响机制": "不参与任何决策",
        "缺失及限制说明": "未接入策略"
    },
    {
        "字段/数据项": "其他周期 K 线 (5m, 1d 等)",
        "底层来源/表或文件": "market_v2.sqlite3 + Parquet 冷档",
        "时间语义": "各周期桶起点",
        "回测窗口覆盖": "存在部分归档",
        "逐行可得性证据": "标记为 unproven",
        "代码实际读取位置": "未在 accum_early_entry.py 中调用",
        "参与决策环节": "未接入",
        "决策影响机制": "当前策略仅绑定 4h/1h/15m 三周期，其他周期不参与决策",
        "缺失及限制说明": "未进入决策链"
    }
]

with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(fields_data[0].keys()))
    writer.writeheader()
    writer.writerows(fields_data)

print(f"Generated Data to Decision Matrix CSV with {len(fields_data)} items: {OUT_CSV}")
