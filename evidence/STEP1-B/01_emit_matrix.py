# -*- coding: utf-8 -*-
"""STEP1-B / 01 · 生成「数据与执行验收矩阵」与缺口清单。

输入：08_scan.json（00_scan.py 产出）+ 本轮只读核查结论。
输出：00_验收矩阵.csv / 04_测试清单.csv / 05_未覆盖与缺口.csv / 09_matrix_check.json

纪律
----
· 每行的「已有结果」都必须能指到具体文件；指不到就写 UNKNOWN；
· 不把「缓存 missing=0 / 哈希一致 / 进程退出 0」当成正式通过；
· 不扩大范围：只针对当前待验回测（WX_EVENT_TREND_V0_1 的 227 候选样本）。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "STEP1-B"

SCAN = json.loads((OUT / "08_scan.json").read_text(encoding="utf-8"))
S = SCAN["sample"]
C = SCAN["contracts"]
R = SCAN["runs"]
G = SCAN["gate"]
D = SCAN["data"]

# ── 固定事实（本轮只读核查得出，均可在 08_scan.json 或原文里复核）──
GATE_BASELINE = "93 passed（evidence/PRECISION-08/原始输出/pytest_既有93项_S0_基线.txt，2026-10-17 15:37:23）"
GATE_NOW = "当前收集 103 项（35+13+35+5+15）；无 103 项运行日志"
BACKEND_LOG = ("Ran 298 tests / OK (skipped=65)（evidence/BT-UI-02/日志/后端回归测试.log，"
               "2026-10-18 17:15:32）")
READPATH_LOG = ("45 items 全 PASSED（evidence/BT-UI-02/日志/pytest_bt_ui_回归.log，"
                "2026-10-18 17:36:47）")
BOUNDARY_LOG = "19 passed（evidence/PRECISION-08/原始输出/pytest_边界案例.txt）"


def row(cat, item, entry, evidence, test, result, source, gap, method):
    return {
        "类别": cat, "验收项": item, "实现入口": entry, "输入证据": evidence,
        "现成测试": test, "已有结果": result, "结果来源": source,
        "缺口": gap, "建议复验方法": method,
    }


ROWS: list[dict] = []

# ═══════════════ A. 数据读取链 ═══════════════
ROWS.append(row(
    "A 数据读取链", "4h K 线读取（环境周期）",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py:206-221 load_symbol_data；"
    "glob 数据/市场数据/parquet/candles/binance/um/{sym}/4h/*.parquet",
    f"数据/市场数据/parquet；COTIUSDT 4h 分片 {D.get('probe_profile', {}).get('4h', {}).get('files')} 个；"
    f"列 {len(D.get('candle_columns') or [])} 个",
    "test_event_trend_closure（SOL 真实 4h 冷层）/ test_run_artifacts",
    GATE_BASELINE,
    "evidence/PRECISION-08/原始输出/pytest_既有93项_S0_基线.txt",
    "只做 sort_values+drop_duplicates，**不校验 is_closed=1、不校验连续性/缺口**；"
    "与框架侧 backtest_runner._closed_contiguous 的「已收盘+连续+全窗覆盖」不同源",
    "对 175 个 run 的 df_4h 逐根验 open_time 等差 4h、close_time=open+4h-1ms、is_closed 全 1，"
    "并与 inputs.fingerprints.inputs.bar_4h 的 rows/sha256 比对"))

ROWS.append(row(
    "A 数据读取链", "1h K 线读取（事件周期）",
    "runner.py:224-237 读取 + :265-266 裁剪（open_time>=start 且 close_time<=end）",
    "同上；COTIUSDT 1h 分片 7 个、49157 行（本轮只读统计）",
    "test_event_trend_closure / test_run_artifacts",
    GATE_BASELINE, "同上",
    "裁剪用 open_time>=start_ms：窗口首根 1h 可能不是整点对齐（start 为 00:00 UTC 时成立，"
    "其他起点未校验）；不校验 is_closed",
    "抽样验窗口首末根的 open_time 是否落在 1h 网格、是否 = 期望起点；核对 data_summary.time_range_1h"))

ROWS.append(row(
    "A 数据读取链", "15m K 线读取（执行周期）",
    "runner.py:229-237；quote_volume 由 engine_adapter:390-392 强制要求",
    "COTIUSDT 15m 分片 7 个、199317 行；列含 quote_volume",
    "test_event_trend_closure:695 test_quote_volume_column_is_required",
    GATE_BASELINE, "同上",
    "缺 quote_volume 时 fail-closed 抛错（已有测试）；但**不校验 quote_volume>0**，"
    "参与率上限遇 0 成交额时按 0 处理还是拒绝 = UNKNOWN",
    "构造 quote_volume=0 的 15m bar，确认是拒单还是 0 名义额；再抽样核对参与率实际生效笔数"))

ROWS.append(row(
    "A 数据读取链", "OI 5m 序列读取",
    "runner.py:240-244 glob oi/binance/um/{sym}/*.parquet，取 columns=['ts','open_interest']",
    f"oi 目录 {D.get('oi_symbols')} 个标的；COTIUSDT 513164 行、period 全为 5m",
    "test_event_trend_closure:259 test_oi_ablation_and_pit_semantics（语义层，非数据层）",
    GATE_BASELINE, "同上",
    f"COTIUSDT 有 {189} 行 open_interest<=0（0.037%）；state_machine 对 <=0 判 oi_curr_non_positive "
    "⇒ 事件被 OI_UNAVAILABLE 终止（fail-closed，不会造假，但会漏候选）；全池零值比例 UNKNOWN",
    "全池统计 OI<=0 行数与落点，统计被 OI_UNAVAILABLE 终止的事件中有多少紧邻零值观测"))

ROWS.append(row(
    "A 数据读取链", "资金费结算事件读取",
    "runner.py:247-258 读 funding/binance/um/{sym}.parquet；trading_core/funding.py:26-59 规范化",
    "COTIUSDT 6996 行，rate_type 全 settled，mark_price 无 <=0，funding_rate 无空",
    "test_funding_audit_counterexamples / test_funding_interval_consumer（框架侧）",
    "框架侧有日志；策略侧无专项日志",
    "evidence/BT-UI-02/日志/后端回归测试.log",
    "runner **不过滤 rate_type**（只靠上游全为 settled）；全池是否存在 predicted 行 = UNKNOWN",
    "全池统计 rate_type 取值；若有非 settled，验证是否会被误当结算事件消费"))

ROWS.append(row(
    "A 数据读取链", "预热（warmup）入口与根数",
    "runner.py:288-292：idx=searchsorted(close_time,start_ms)；warmup_start=max(0,idx-220)",
    "config.py:27 atr_warmup_bars=200；代码实际取 220",
    "test_run_artifacts:172 指纹覆盖；无「预热根数=200」专项测试",
    GATE_BASELINE, "同上",
    "**配置声明 200、代码实取 220**，两处不一致且无测试锁定；1h/15m 无预热（从窗口起点开始）",
    "对 175 run 读 inputs.data_summary 与 run.json 的 warmup_4h_bars_before_window，"
    "确认是否恒 220、是否与 200 的差异影响 ATR 与箱体首日"))

ROWS.append(row(
    "A 数据读取链", "「正式读接口」与「回放读数据」是否同源",
    "回放：event_trend_v0_1/runner.py（直读 parquet）；"
    "读接口/建单：框架/backend/backtest_runner.py（MarketDataRepository=sqlite 热层+parquet 冷层）",
    "两条链都落到 数据/市场数据/parquet，但热层走 数据/市场数据/db/market_v2.sqlite3",
    "无「同标的两链取值一致」测试",
    "UNKNOWN",
    "—",
    "**同标的两条读取链从未逐值比对**；且 backtest_runner._run_event_trend:788-790 "
    "把窗口写死 2024-01-01~2024-06-30（见 G 类）",
    "选 3 个标的，用两链各取同窗口 1h/15m/OI/funding，逐值比对行数、首末时间与 sha256"))

ROWS.append(row(
    "A 数据读取链", "哪些数据只用于缓存/展示、不进决策",
    "缓存：backtest_api._fallback_chart_cached（内存 LRU，不写盘）；"
    "展示：backtest_metrics 现算指标、chart.json 的 candles/benchmark",
    "回测日志/event_trend_v0_1/runs*/**/chart.json",
    "test_bt_ui_01_read_path:162 test_window_equals_run_own_inputs_and_no_write",
    READPATH_LOG, "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
    "chart.json 里的 candles 是 15m 执行序列的**副本**，不是决策输入；"
    "但它是页面唯一可见的 K 线来源 ⇒ 图对不等于算对",
    "抽 3 个 run：把 chart.candles 与 parquet 15m 同窗口逐根比对；"
    "把 chart.trades 与 trades.jsonl 的 trade_id/价格逐笔比对"))

# ═══════════════ B. 字段、周期、时间口径 ═══════════════
ROWS.append(row(
    "B 字段与时间口径", "OHLCV 的物理存储类型",
    "parquet 落盘 open/high/low/close/volume/quote_volume/taker_buy_* 均为**字符串**；"
    "runner.py:211-213/234-237 用 pd.to_numeric 转 float",
    f"schema：{ {k: v for k, v in (D.get('candle_dtypes') or {}).items() if k in ('open', 'volume')} }",
    "无「字符串→float 不丢精度」专项测试",
    "UNKNOWN",
    "—",
    "若某处忘记 to_numeric，sum() 会做字符串拼接（FLOW-DATA-01 已记录同类坑）；"
    "event_trend 链已转，但 4h 的 atr 列由 close/high/low 计算，依赖转换先于计算",
    "全池抽 5 标的：断言 to_numeric 前后 max|diff|=0；并断言 atr 列无 NaN 头部污染"))

ROWS.append(row(
    "B 字段与时间口径", "时间单位与区间闭合方式",
    "全部毫秒 int64；K 线用 open_time 起点 + close_time 终点；"
    "funding 用 funding_time（毫秒，带毫秒抖动）；engine 断言单一 time_unit（engine.py:260-262）",
    "4h: open_time..close_time=+4h-1ms；funding_time 例 1615363200006",
    "test_12h_utc_boundary（框架侧）/ test_funding_interval_*",
    "框架侧有日志",
    "evidence/BT-UI-02/日志/后端回归测试.log",
    "资金费判据 `last_funding_ms < ev_ms <= t_ms` 用毫秒，不截秒（funding.py:88）——正确；"
    "但 OI 的 ts 与 1h close_time 的比较同为毫秒闭区间，未规定「等于收盘时刻的观测算不算已可得」",
    "取 20 个 OI 观测 ts 恰好等于 close_time 的样本，确认是否被当作已可得（当前 side='right' ⇒ 算）"))

ROWS.append(row(
    "B 字段与时间口径", "PIT 可用时点（决策时刻能否拿到该值）",
    "trading_core/pit.py 存在（annotate_oi_pit / PitPolicy），但**只被 "
    "oi_resonance_v1/core.py:253 调用**；event_trend 链未接入",
    "K 线 available_at 列实测全为 None；OI fetched_at 全为 None",
    "test_event_trend_closure:259 只验语义（oi_pit_proven 缺省 False）",
    "UNKNOWN（event_trend 链无 PIT 证据）",
    "—",
    "**PIT 未证明**：event_trend 从不调用 pit.py，K 线/OI 的 available_at 全空，"
    "候选的 available_at_ms 直接等于 close_time_ms（state_machine.py:541）⇒ 属「自证可得」，非证据",
    "接入 annotate_oi_pit 后重算候选，统计 oi_pit_proven=True 的比例；"
    "在拿到 OI 发布延迟前，结论只能是 UNPROVEN"))

ROWS.append(row(
    "B 字段与时间口径", "OI 的 as-of 对齐规则",
    "features.py:78-120 extract_real_oi_growth：searchsorted(ts, target, side='right')-1，"
    "距离上限 900s",
    "config.py:53-55 oi_lookback_hours=6 / oi_min_growth_pct=0.02 / oi_max_time_distance_sec=900",
    "test_event_trend_closure:259 / :306（消融与字段映射）",
    GATE_BASELINE, "同上",
    "取「<= target 的最近一条」而非「target 时刻已发布的最近一条」；"
    "900s 容差下，5m OI 的观测时刻与 close 最多差 15 分钟",
    "用 OI 观测时刻与 1h 收盘时刻的差值分布，统计有多少候选的 oi_curr 取自 close 前 0~900s"))

ROWS.append(row(
    "B 字段与时间口径", "历史合约规格来源与资格",
    "event_trend_v0_1/instrument_spec.py:1103 resolve_instrument_spec；"
    "证据目录 数据/contracts/旧标的池",
    f"期货快照 {C.get('futures_snapshot_count')} 份（均 2026-10-05）；"
    f"spec_changes 变更证据 {C.get('spec_changes_count')} 份",
    "test_spec_historical_applicability（35 项，含 test_real_evidence_dir_never_yields_verified）",
    GATE_BASELINE, "同上",
    f"**变更证据 0 份 ⇒ 全池不可能出现 verified**（交接包实测 "
    f"{S.get('handoff_spec_qualification')}）；快照时间 2026-10-05 晚于 2021-12~2026-08 的回放窗口",
    "重跑 test_real_evidence_dir_never_yields_verified；"
    "若要升级为 verified，必须先补 spec_changes_*.json 覆盖整窗（本单不做）"))

ROWS.append(row(
    "B 字段与时间口径", "版本标识是否随产物落盘",
    "runner.py:481-492 inputs_meta：strategy_version/variant/fingerprints(instrument_spec)；"
    "engine.py:451-463 写入 strategy_id/version/timeframe",
    "每个 run 的 inputs.json + summary.json；配置指纹 config_sha256",
    "test_run_artifacts:172 test_fingerprints_cover_rules_code_config_inputs",
    GATE_BASELINE, "同上",
    "rule_version / param_hash 未以独立字段出现（只有 config sha256 与 rules sha256），"
    "与项目 API 口径要求的 `rule_version`+`param_hash` 不齐",
    "读 175 个 run 的 inputs.json，统计缺失 strategy_version / rules.sha256 / config.sha256 的 run 数"))

# ═══════════════ C. 关联键 ═══════════════
ROWS.append(row(
    "C 关联键", "candidate_id 的生成式",
    "state_machine.py:527 `cand_{event_id}_{close_time_ms}`；"
    "event_id=state_machine.py:215 `ev_{strategy}_{venue}_{symbol}_{bar_4h_close_ms}`",
    "例 cand_ev_WX_EVENT_TREND_V0_1_binance_IOTXUSDT_1689192000000_1689274800000",
    "test_event_trend_closure:306（唯一绑定）",
    GATE_BASELINE, "同上",
    "candidate_id **不含 run_id / 窗口** ⇒ 同一标的同一箱体在不同回放代际会生成同名 id（见下一行）",
    "对 175 run 全量统计 candidate_id 重复：当前交接包 227 行 / 226 唯一信号，"
    "重复仅 COTI 一组（已单列）"))

ROWS.append(row(
    "C 关联键", "candidate → signal 的贯穿",
    "engine_adapter.py:292-338 build_signal_frame_from_candidates：signal_id 列直接承载 candidate_id；"
    "policy.resolve_candidate 只按 id 精确查（engine_adapter.py:172-175）",
    "candidates.jsonl / validation_results.jsonl 的 candidate_id 与 signal_id 同值",
    "test_event_trend_closure:306/:380/:421/:460（唯一绑定、顺序扰动、提前成交拒绝、缺身份拒绝）",
    GATE_BASELINE, "同上",
    "无缺口（这是 2026-10-17 修过的核心项）；但**不校验 snapshot_time 与 signal bar 的时间一致性之外的字段**",
    "对 175 run 断言 trades[].signal_id 全部命中 candidates[].candidate_id，且一一对应"))

ROWS.append(row(
    "C 关联键", "order_id 的生成式与跨代际撞号",
    "trading_core/order.py:69-71 make_order_id = 'ord_'+stable_hash(strategy_key,symbol,signal_id,action,…)",
    "交接包实测：213 条成交里 6 组 signal_id 重复、13 行；映射到 205 候选后重复 0",
    "无专项测试（EA04 是独立核对，不是单测）",
    "已登记（EA04 §1.8）",
    "evidence/ENTRY-AUDIT-04/报告.md §1.8",
    "order_id **不含 run_id** ⇒ 跨代际撞号是设计后果；任何按 order_id 单键关联都会静默取错代际",
    "所有下游关联强制 (run_id, order_id) 复合键；用 6 组撞号样本做回归"))

ROWS.append(row(
    "C 关联键", "order → fill → trade → position 的落盘关联",
    "engine._fill_order（engine.py:235-247）→ ledger ORDER_FILLED；"
    "_close_position（engine.py:1001-1058）产出 trade rec 带 signal_id/order_id/exit_order_id；"
    "runner._build_run_files 分文件落 orders/fills/positions/trades/ledger",
    "run 目录：orders.jsonl / fills.jsonl / positions.jsonl / trades.jsonl / ledger.jsonl",
    "test_run_artifacts:86（产物确定性完整）/ test_bt_ui_02（trade_id 缺失时从账本回填）",
    READPATH_LOG, "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
    "**trades.jsonl 本身不带 trade_id**（读层用 ledger_links 关联）；批次目录里 666 笔 trades.jsonl 全无 trade_id",
    "断言 trades.jsonl 每行能由 (run_id, order_id) 在 orders.jsonl 找到唯一开仓单；"
    "缺 trade_id 时不得按行号编造"))

ROWS.append(row(
    "C 关联键", "signal_key 的来源",
    "交接包自定义 `(symbol, snapshot_time_ms)`；event_trend 引擎内**无此字段**",
    "handoff/signal_index.csv 226 条",
    "无",
    "已登记（EA04 §1.3）",
    "evidence/ENTRY-AUDIT-04/报告.md",
    "event_id 不能当唯一信号键（窗口代际用 cand_exec_earliest_ms 而非快照时刻）；"
    "跨代际比对只能靠 signal_key",
    "凡跨代际比对一律用 signal_key；引擎侧若要支持，需新增字段（本单不改）"))

# ═══════════════ D. 费用、现金、持仓 ═══════════════
ROWS.append(row(
    "D 费用与现金", "手续费计算入口",
    "engine.py:265-267 fee=taker_fee_rate（=cfg.taker_fee_bps*1e-4）；"
    "入场 engine.py:423 FEE_CHARGED；出场 engine.py:411 fill_fee + :423；"
    "fill.py:298 fill_fee(fill_price,qty)",
    "config.py:86 taker_fee_bps=5.0（0.0005）；C 方案 10bps",
    "test_fill_precision:353（分账恒等式）/ test_work02_core_contract:test_fee_once",
    GATE_BASELINE, "同上",
    "只按 taker 计（maker_fee_rate 存在但策略从不使用）；" 
    "**入场费在开仓时已扣现金、平仓时不再扣**（_close_position 注释），"
    "净盈亏公式用 entry_fee+exit_fee 复算，两条路径必须一致",
    "对 175 run 断言 sum(entry_fee)+sum(exit_fee)=sum(fees)，且现金恒等式成立"))

ROWS.append(row(
    "D 费用与现金", "资金费计算入口",
    "funding.py:67-107 settle_funding：cost=(mark_price*qty)*rate*direction；"
    "engine.py:340-366 相位 A 结算并 emit FUNDING_FEE",
    "config/exec funding_model='real_events'；事件来自 funding.parquet",
    "test_funding_audit_counterexamples（13 项）/ test_work02_core_contract",
    "框架侧有日志；策略侧靠交接包对账",
    "evidence/BT-UI-02/日志/后端回归测试.log",
    "缺 rate/mark 时记 funding_gap 且**不补 0**（正确）；交接包实测资金费缺字段 11 次；"
    "**账本 FUNDING_FEE.payload.delta 是现金口径、与 funding_fee 符号相反**",
    "对 205 笔断言 cum_funding_fee == −Σdelta（仅 5 个探针 run 有事件留痕）；"
    "缺字段的 11 笔必须显式标缺，不得按 0 计"))

ROWS.append(row(
    "D 费用与现金", "滑点与取整",
    "fill.py:108-139 price_breakdown：原价→滑点（买卖方向相反）→取整（买 ceil/卖 floor）；"
    "顺序固定，Decimal 实现",
    "config.py:87 slippage_bps=3.0（0.0003）",
    "test_fill_precision:178 test_slippage_is_applied_before_rounding / :193 / :113-151",
    GATE_BASELINE, "同上",
    "**越界腿**：入场向上取整会撑大止损距离，成交价可能落到当根 15m bar 区间外；"
    "R7（零容差区间校验）未实现；62 腿中 8 条越界（滑点段 0、取整段 100%）",
    "对 175 run 的每笔成交取 bar 的 [low,high] 比对 fill；"
    "**禁止**用裁剪成交价的方式「修」（会把亏损算小）"))

ROWS.append(row(
    "D 费用与现金", "净盈亏公式",
    "engine.py:1047 net_pnl = gross − entry_fee − exit_fee − cum_funding_fee；"
    "engine.py:1015 gross=(fill−entry)*qty*direction",
    "交接包 net_pnl_formula 列逐行写入",
    "test_work02_core_contract / test_fill_precision:353",
    GATE_BASELINE, "同上",
    "滑点/取整已含在成交价里，**不再单独扣**；再扣一次会重复（全样本多扣 9.519034 USDT）",
    "对 205 笔复算 net_pnl=gross−fees−funding_fee（容差 1e-9），并断言 residual≈0"))

ROWS.append(row(
    "D 费用与现金", "现金与权益",
    "engine.py:313 cash=initial_equity；:720-724 equity_curve 逐 bar 写 "
    "cash_equity/unrealized_pnl/total_equity；:831-836 cash_identity_ok / equity_identity_ok",
    "equity_curve.jsonl / summary.json.reconcile",
    "test_metrics_contract（框架侧 30 项）/ 无策略侧专项",
    "框架侧有日志",
    "evidence/BT-UI-02/日志/后端回归测试.log",
    "现金恒等式 `cash == initial + Σgross − Σentry_fee − Σexit_fee − Σfunding` 只在引擎自检里；"
    "**没有独立复算器**（读层用 equity 序列算指标，不复算现金）",
    "用 trades.jsonl 独立复算 final_cash 并与 reconcile.final_cash 比（容差 1e-9）"))

ROWS.append(row(
    "D 费用与现金", "持仓与下单数量",
    "trading_core/position.py；risk.py:106-164 check_entry："
    "qty=min(risk_per_trade*mtm/unit_risk, 名义上限)；"
    "engine_adapter.py:225-238 再按单仓 30%/总 90% 截断",
    "config.py:69-75 initial_equity=2000 / risk 0.5% / 3 仓 / 30% / 90%",
    "test_work02_core_contract:test_gap_behavior 等 / test_fill_precision:238 qty floor + minNotional",
    GATE_BASELINE, "同上",
    "`max_qty`（交易所单笔最大量）**从未校验**（memory 已登记）；"
    "参与率上限用「已完成前一根 15m quote_volume × 0.1%」",
    "全池统计 max_qty 缺字段的标的比例；对 175 run 断言 qty 满足 step/minQty/minNotional"))

# ═══════════════ E. 测试 ═══════════════
ROWS.append(row(
    "E 测试", "策略侧 93/103 项门禁",
    "pytest 策略/WyckoffMaster/event_trend_v0_1/tests 策略/WyckoffMaster/test_work02_core_contract.py",
    "5 个测试文件（35+13+35+5+15）",
    "见 04_测试清单.csv",
    GATE_BASELINE + "；但当前代码收集 " + GATE_NOW,
    "evidence/PRECISION-08/原始输出/",
    "**记录在案的 93 passed 早于 test_fill_precision.py 的最后修改（16:12:55 > 15:37:23）** ⇒ "
    "对当前代码不成立；103 项从未跑过并留日志",
    "窗口①重跑 103 项并留原始输出；本轮不跑（避免写入与越权）"))

ROWS.append(row(
    "E 测试", "框架侧 298 项后端回归",
    "sh 框架/backend/run_tests.sh（unittest discover tests/test_*.py）",
    "19 个测试文件",
    "见 04_测试清单.csv",
    BACKEND_LOG,
    "evidence/BT-UI-02/日志/后端回归测试.log",
    "65 项 skipped（WORK-04B 退役契约 / 缺伴生库 / 缺样本）；"
    "**是任务自报，不是独立复验**",
    "窗口①用同一命令重跑并留日志；逐条登记 65 项 skip 原因"))

ROWS.append(row(
    "E 测试", "读取通道 15 项（BT-UI-01/02）",
    "pytest 框架/backend/tests/test_bt_ui_01_read_path.py test_bt_ui_02_chart_window.py",
    "8 + 7 项",
    "见 04_测试清单.csv",
    READPATH_LOG,
    "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
    "部分用例**依赖盘上真实 run 目录**（无样本即 skip）；样本变了结论就变",
    "在 175 run 的样本集上重跑；记录每个用例实际命中的 run_id"))

ROWS.append(row(
    "E 测试", "成交边界 19 项（PRECISION-08）",
    "pytest evidence/PRECISION-08/测试_边界案例.py --basetemp=临时目录",
    "表1_边界案例_输入与预期.csv",
    "见 04_测试清单.csv",
    BOUNDARY_LOG,
    "evidence/PRECISION-08/原始输出/pytest_边界案例.txt",
    "只测**纯函数** price_breakdown 与候选规则，不测端到端 run",
    "作为 R6/R7 整改的前置用例保留；端到端仍需新用例"))

ROWS.append(row(
    "E 测试", "「证据→结论」的可信度分级",
    "—",
    "本轮只读核查",
    "—",
    "策略侧：无 103 项日志；框架侧：有日志但为自报；"
    "样本侧：EA03/04/05/06 有独立复算与自哈希",
    "evidence/ENTRY-AUDIT-04/99_verify_result.json 等",
    "**没有任何一项是窗口①做的独立复验**；本单只做清点，不做复验",
    "窗口①对 E1~E4 各跑一次，产物落各自目录，禁止共用日志"))

# ═══════════════ F. 结果读取映射 ═══════════════
ROWS.append(row(
    "F 结果读取", "批次列表与批次汇总",
    "框架/backend/backtest_batches.py:39-53 BATCH_ROOT=回测日志/event_trend_v0_1，"
    "只认 runs / runs_*；:353 list_batches；backtest_api.route seg[0]=='batches'",
    f"{R.get('batch_count')} 个批次、{R.get('batch_runs_total')} 个 run",
    "test_bt_ui_01_read_path:71-94 / test_bt_ui_02",
    READPATH_LOG, "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
    "批次目录里混有**旧口径 run**（chart 窗口被写死在 2024-01-01~2024-06-30 的 83 个）；"
    "批次汇总的 runs_win/runs_loss 等由读层现算，不读 summary",
    "对每个批次断言 summary.runs_total == 目录数；逐笔行数与 runs 目录可枚举数一致"))

ROWS.append(row(
    "F 结果读取", "单 run 指标",
    "backtest_api.get_metrics:204-241 → backtest_metrics.compute_metrics(run_dir,run_id,doc)",
    "run 目录的 trades.jsonl + equity_curve.jsonl（**不是 summary.json**）",
    "test_metrics_contract（30 项，含夏普口径）",
    "框架侧有日志",
    "evidence/BT-UI-02/日志/后端回归测试.log",
    "summary.json 与现算指标**可能不同**（页面用现算、summary 只作对照）；"
    "0 成交 run 的夏普返回 None 而非 0（正确）",
    "抽 5 个 run：现算指标 vs summary 字段逐项比，差异必须能解释"))

ROWS.append(row(
    "F 结果读取", "图表（K 线/打点/基准）",
    "backtest_api.get_chart:883 → read_chart → _chart_window_reject_reason:841 → "
    "_fallback_chart_cached:873（内存重建，不写盘）",
    "run 目录 chart.json + inputs.json.data_summary.time_range_1h",
    "test_bt_ui_02_chart_window（7 项）",
    READPATH_LOG, "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
    "盘上 chart.json 的 window 与本 run 不符时**拒收并重建**；"
    "缺图 469 个（5 interrupted + 464 未落盘），464/464 可重建",
    "抽 10 个 run：断言 chart.window == inputs 窗口（容差 1 天）；"
    "断言 chart.trades[].trade_id 属于本 run 逐笔"))

ROWS.append(row(
    "F 结果读取", "逐笔明细与单笔详情",
    "backtest_api.get_trades:311 / get_trade:419 → ledger_links.load_ledger_facts",
    "orders.jsonl / positions.jsonl / trades.jsonl / ledger.jsonl",
    "test_bt_ui_01_read_path:60-99（trade_id、行数自洽、0 不为空）",
    READPATH_LOG, "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
    "批次目录的 trades.jsonl 无 trade_id（666 笔）⇒ 退 positions.jsonl 的 POSITION_CLOSED 按 "
    "(平仓时刻,净盈亏) 双键对齐；再退 chart.trades[]",
    "断言前端点开任一笔都能定位到唯一 trade_id，且不与相邻 run 串标的"))

ROWS.append(row(
    "F 结果读取", "实验列表（run 列表）",
    "backtest_api.list_experiments:73-108：S.list_runs(RUNS_ROOT) 后**只保留 "
    "strategy_id==WX_OI_RESONANCE_V1**",
    f"回测日志/oi_resonance_v1/runs 实测 {R.get('oi_resonance_runs')} 个 run",
    "test_business（部分）",
    "UNKNOWN（无专项日志）",
    "—",
    "**该列表恒空**：过滤键是 OI_RESONANCE，而该根目录 0 个 run；"
    "页面实际走批次接口，所以用户看不到这个空列表",
    "调 GET /api/v1/backtests?limit=10，确认 items 为空且 excluded_legacy 计数；"
    "再确认页面未使用该接口"))

ROWS.append(row(
    "F 结果读取", "建单入口（POST /backtests）",
    "backtest_api.create_run:947 → _runnable_gate:934 → strategies/registry.guard_runnable",
    f"registry.CURRENT_BACKTEST_ID={G.get('CURRENT_BACKTEST_ID')}；"
    f"bridge.STRATEGY_ID={G.get('bridge_STRATEGY_ID')}",
    "无端到端测试覆盖「建单成功」路径",
    f"guard(WX_EVENT_TREND_V0_1)={G.get('guard_WX_EVENT_TREND_V0_1')!r}；"
    f"guard(WX_OI_RESONANCE_V1)={G.get('guard_WX_OI_RESONANCE_V1')!r}",
    "08_scan.json.gate（本轮只读 import 实测）",
    "**键名错配**：闸门用 bridge 的 WX_OI_RESONANCE_V1 去查，注册表只认 WX_EVENT_TREND_V0_1 "
    "⇒ 任何 POST 都 409「未知策略键」。当前所有 run 都来自根目录批处理脚本",
    "直接调 create_run 看返回码；若要修，需把 _runnable_gate 的键与 registry 对齐（本单不改）"))

ROWS.append(row(
    "F 结果读取", "报告（人工阅读的结论层）",
    "evidence/ 下各目录的 报告.md / 00_acceptance_table.csv / 08_final_status.json",
    "EA03/EA04/EA05/EA06、FACTOR-ENTRY-07/08/09、FLOW-DATA-01、FLOW-OI-01、STEP1-C",
    "无（报告本身不是测试）",
    "EA06 结论：order_status=PASS_WITH_NOTE，历史缺陷 3 条未整改",
    "evidence/ENTRY-AUDIT-06/08_final_status.json",
    "报告层的数字**不保证与数据层同步**（EA05 就出现过陈旧表述）；"
    "FE07/FE08 报告哈希表 9/9、10/10 全错且未勘误",
    "任何报告数字都必须回指到 08_scan 这类机器可读产物；报告与数据不一致时以数据为准"))

# ═══════════════ G. 版本与范围对齐 ═══════════════
ROWS.append(row(
    "G 版本对齐", "待验回测的身份（窗口①必须用这一套）",
    "策略/WyckoffMaster/event_trend_v0_1/config.py；evidence/ENTRY-AUDIT-03/00_config_fingerprint.json",
    "strategy_name=WX_EVENT_TREND_V0_1 / version=v0.1.0 / engine_contract=trading_engine_v1.0.0 / "
    "config_sha256=0faa5fc046f1841d3900508ddcfbbd6717dff8b0196a3dc17ae406efb455d7b0",
    "test_run_artifacts:172（指纹覆盖）",
    "已冻结",
    "evidence/ENTRY-AUDIT-03/00_config_fingerprint.json",
    "冻结规则 SHA256 1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab "
    "写死在 config.py 注释里，**代码不校验**该文件是否被改",
    "窗口①开工前先算 数据/…/config.py、冻结规则.md 的 sha256，与上值比对；不一致即停"))

ROWS.append(row(
    "G 版本对齐", "待验样本的 run 与行数",
    "evidence/ENTRY-AUDIT-03/runs/（**不是** 回测日志/event_trend_v0_1/runs*）",
    f"磁盘 {S.get('ea03_run_dirs')} 个 run（A {S.get('ea03_generation', {}).get('A')} / "
    f"B {S.get('ea03_generation', {}).get('B')}）；交接包 {S.get('handoff_runs')} 个 run / "
    f"{S.get('handoff_rows')} 行",
    "EA03 02_replay_full.py / EA04 00_freeze_handoff.py",
    "EA05/EA06 已逐位复核",
    "evidence/ENTRY-AUDIT-05/01_fe08_recheck.json / ENTRY-AUDIT-06/04_data_invariance.json",
    f"**run.json 口径 {S.get('ea03_candidates_from_run_json')} 候选 / "
    f"{S.get('ea03_trades_from_run_json')} 成交，交接包只有 {S.get('handoff_rows')} / "
    f"{S.get('handoff_filled')}**；差 {S.get('per_run_candidate_gap_total')} 候选、"
    f"{S.get('per_run_trade_gap_total')} 笔成交，集中在 9 个 B 组 run（见 05 缺口清单）",
    "窗口①必须明确以哪一套为准：若以 227 为分母，需先说明 10 条候选为何排除；"
    "若以 237 为准，则 FE07~FE09 与 FLOW-OI-01 的分母都要重算"))

ROWS.append(row(
    "G 版本对齐", "数据版本",
    "数据/市场数据/parquet/_state.json",
    f"watermark_ms={D.get('data_watermark_ms')}；"
    f"reconcile_watermark={D.get('data_reconcile_watermark_iso')}",
    "无数据版本变更检测测试",
    "已记录（EA03 冻结输入指纹）",
    "evidence/ENTRY-AUDIT-03/00_freeze_manifest.json",
    "冷层仍在被归档任务改写（EA03 之后可能有新分片）；**数据版本变了则 175 run 不可复现**",
    "窗口①开工前记录 watermark_ms 与各标的 4h/1h/15m/OI/funding 分片清单+sha256；"
    "收工再记一次，两次必须相同"))

ROWS.append(row(
    "G 版本对齐", "正式批次与待验样本的关系",
    "回测日志/event_trend_v0_1/runs*（批次读接口的唯一来源）",
    f"{R.get('batch_count')} 个批次 / {R.get('batch_runs_total')} 个 run",
    "BT-UI-01/02 的读取通道测试",
    READPATH_LOG, "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
    "**两者不是同一批 run**：批次是历史批处理产物，待验样本是 EA03 的独立重放。"
    "批次只能验「读接口/图表」，不能验「回测算得对不对」",
    "把「读接口验收」与「回测算术验收」分成两张表，禁止用批次绿了推出回测通过"))

# ═══════════════ 写入 ═══════════════
def write_csv(name: str, rows: list[dict], cols: list[str]) -> None:
    with open(OUT / name, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


MATRIX_COLS = ["序号", "类别", "验收项", "实现入口", "输入证据", "现成测试",
               "已有结果", "结果来源", "缺口", "建议复验方法"]
matrix = [{"序号": i + 1, **r} for i, r in enumerate(ROWS)]
write_csv("00_验收矩阵.csv", matrix, MATRIX_COLS)

# ── 04 测试清单 ──
TEST_ROWS = [
    {"测试集": "策略侧门禁（5 文件）", "文件": "策略/WyckoffMaster/event_trend_v0_1/tests/*.py + "
     "策略/WyckoffMaster/test_work02_core_contract.py",
     "覆盖行为": "状态机/执行适配/成交精度/规格资格/产物原子性/核心契约",
     "命令": "python -m pytest 策略/WyckoffMaster/event_trend_v0_1/tests "
             "策略/WyckoffMaster/test_work02_core_contract.py",
     "当前收集": "103 项（35+13+35+5+15）",
     "最近结果": "93 passed（2026-10-17 15:37:23）",
     "日志": "evidence/PRECISION-08/原始输出/pytest_既有93项_S0_基线.txt",
     "性质": "任务自报；且**早于当前代码**（test_fill_precision.py 改于 16:12:55）"},
    {"测试集": "后端全量", "文件": "框架/backend/tests/test_*.py（19 个）",
     "覆盖行为": "读接口/批次/指标/图表/资金费/采集/业务契约/池准入",
     "命令": "sh 框架/backend/run_tests.sh",
     "当前收集": "298 项（65 skip）",
     "最近结果": "Ran 298 / OK (skipped=65)（2026-10-18 17:15:32）",
     "日志": "evidence/BT-UI-02/日志/后端回归测试.log",
     "性质": "任务自报；晚于最新测试文件改动（17:09），对本代码成立但未独立复验"},
    {"测试集": "读取通道", "文件": "test_bt_ui_01_read_path.py（8）+ test_bt_ui_02_chart_window.py（7）",
     "覆盖行为": "批次/默认根 run 解析、写边界不放宽、0 不为空、图表窗口守卫、缺费用不为 0",
     "命令": "python -m pytest 框架/backend/tests/test_bt_ui_01_read_path.py "
             "框架/backend/tests/test_bt_ui_02_chart_window.py",
     "当前收集": "15 项",
     "最近结果": "15/15 ok（含在 45 items 全 PASSED 内）",
     "日志": "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
     "性质": "任务自报；部分用例依赖盘上真实 run，无样本即 skip"},
    {"测试集": "指标契约", "文件": "test_metrics_contract.py（30）",
     "覆盖行为": "缺值/0 区分、交易所能力、轮转窗口、结转、夏普口径",
     "命令": "python -m pytest 框架/backend/tests/test_metrics_contract.py",
     "当前收集": "30 项",
     "最近结果": "全 PASSED（45 items 的一部分）",
     "日志": "evidence/BT-UI-02/日志/pytest_bt_ui_回归.log",
     "性质": "任务自报"},
    {"测试集": "成交边界", "文件": "evidence/PRECISION-08/测试_边界案例.py",
     "覆盖行为": "price_breakdown 边界、步长粗于价格、越界成交判定（纯函数）",
     "命令": "python -m pytest evidence/PRECISION-08/测试_边界案例.py --basetemp=<临时目录>",
     "当前收集": "19 项",
     "最近结果": "19 passed",
     "日志": "evidence/PRECISION-08/原始输出/pytest_边界案例.txt",
     "性质": "任务自报；只测纯函数，不测端到端"},
    {"测试集": "R6/R7 影子影响面", "文件": "evidence/PRECISION-08/工具_40_影响面.py",
     "覆盖行为": "若加「取整后区间校验」/「零容差区间校验」会打破哪些既有用例",
     "命令": "python evidence/PRECISION-08/工具_40_影响面.py",
     "当前收集": "影子，不改生产代码",
     "最近结果": "R6 前置 91/2、R6 后置 92/1、R7 代理 88/5、R6+R7 88/5",
     "日志": "evidence/PRECISION-08/原始输出/pytest_既有93项_S1~S4*.txt",
     "性质": "方案评估，不是当前门禁结果"},
    {"测试集": "FLOW-OI-01 一致性", "文件": "evidence/FLOW-OI-01/test_flow_oi_consistency.py",
     "覆盖行为": "样本行数/快照 ID 对齐、coverage 字段、买卖恒等式、嵌套逻辑",
     "命令": "python evidence/FLOW-OI-01/test_flow_oi_consistency.py",
     "当前收集": "UNKNOWN（未在报告中列出条数）",
     "最近结果": "UNKNOWN（报告未附运行日志）",
     "日志": "无",
     "性质": "**未留日志**；STEP1-C 已指出其漏检 60 分钟前视"},
]
write_csv("04_测试清单.csv", TEST_ROWS,
          ["测试集", "文件", "覆盖行为", "命令", "当前收集", "最近结果", "日志", "性质"])

# ── 05 缺口清单 ──
GAPS = [
    {"类型": "口径缺口", "事项": "227 vs 237：10 候选 / 8 笔成交未进交接包",
     "事实": f"EA03 磁盘 run.json 合计 {S.get('ea03_candidates_from_run_json')} 候选 / "
             f"{S.get('ea03_trades_from_run_json')} 成交；交接包 {S.get('handoff_rows')} 行 / "
             f"{S.get('handoff_filled')} 成交",
     "影响": "所有下游（FE07/08/09、FLOW-OI-01、STEP1-C）的分母都是 227；少 8 笔真实成交会改变净盈亏与胜率",
     "归属": "窗口①先定性；EA04 需补逐条说明",
     "建议": "逐 run 列出被排除的 candidate_id 与原因（设计内筛选 or 漏取），并给出两套分母的指标对照"},
    {"类型": "数据缺口", "事项": "OI 存在 <=0 的假观测",
     "事实": "COTIUSDT 513164 行中 189 行 open_interest<=0；state_machine 判 oi_curr_non_positive ⇒ 事件终止",
     "影响": "可能漏候选（fail-closed，不会造假）；全池比例 UNKNOWN",
     "归属": "窗口①统计，STEP1-B 只登记",
     "建议": "全池统计零值行数与被 OI_UNAVAILABLE 终止的事件邻接率"},
    {"类型": "未实现", "事项": "PIT 未接入 event_trend",
     "事实": "trading_core/pit.py 只被 oi_resonance_v1/core.py 调用；K 线 available_at 与 OI fetched_at 全为 None",
     "影响": "PIT 状态只能 UNPROVEN；不能证明决策时刻能拿到这些值",
     "归属": "研究层（本单不改）",
     "建议": "声明 OI 发布延迟后接 annotate_oi_pit；在拿到延迟前不得宣称 PIT 通过"},
    {"类型": "未实现", "事项": "R7 零容差区间校验 / R6 取整后区间校验",
     "事实": "62 腿中 8 条成交价越出当根 bar [low,high]；strict 只拦 1/8（被拦的全是零价）；R7 影响面无法影子测",
     "影响": "入场向上取整撑大止损距离 ⇒ 实际亏损可超预算；当前无一笔被拦",
     "归属": "PRECISION 系列（未整改）",
     "建议": "按 PRECISION-08 方案落地 R6/R7 并同改 2 条既有用例；**禁止**裁剪成交价"},
    {"类型": "未实现", "事项": "max_qty 从未校验",
     "事实": "engine.py 只校验 step/minQty/minNotional；max_qty 未进任何闸门",
     "影响": "大资金/低价币可能下出超交易所上限的量；回测不报错",
     "归属": "执行核心（未整改）",
     "建议": "统计全池 max_qty 覆盖率；在 check_quantity 增加 max_qty 分支并补用例"},
    {"类型": "未覆盖", "事项": "预热根数 200 vs 220 无测试锁定",
     "事实": "config.atr_warmup_bars=200；runner.py:290 实际取 220",
     "影响": "改任一处都不会红；窗口首日 ATR/箱体可用性可能悄悄变",
     "归属": "策略层",
     "建议": "加一条断言 warmup_4h_bars_before_window==220 的用例，或把配置改成唯一事实源"},
    {"类型": "未覆盖", "事项": "两条数据读取链从未逐值比对",
     "事实": "回放直读 parquet；读接口走 sqlite 热层 + parquet 冷层（MarketDataRepository）",
     "影响": "同标的两链若取值不同，页面与回测会各说各话",
     "归属": "窗口①",
     "建议": "选 3 标的做同窗口逐值比对（行数/首末/ sha256）"},
    {"类型": "缺陷", "事项": "建单闸键名错配 ⇒ POST 恒 409",
     "事实": f"_runnable_gate 用 {G.get('bridge_STRATEGY_ID')} 查注册表，注册表只认 "
             f"{G.get('CURRENT_BACKTEST_ID')}",
     "影响": "API 无法建单；所有 run 只能靠根目录脚本跑，无审计入口",
     "归属": "框架层（本单不改）",
     "建议": "对齐键名，并补一条 create_run 端到端用例（放行 + 拒绝各一）"},
    {"类型": "缺陷", "事项": "_run_event_trend 窗口写死",
     "事实": "backtest_runner.py:788-790 start_date='2024-01-01' end_date='2024-06-30'",
     "影响": "一旦建单闸放开，会用错误窗口产出 run（历史上已造成 83 个 run 图表窗口错）",
     "归属": "框架层（本单不改）",
     "建议": "改为读 spec 的窗口；补一条「请求窗口 == 产物窗口」用例"},
    {"类型": "未覆盖", "事项": "实验列表接口恒空",
     "事实": f"list_experiments 只保留 WX_OI_RESONANCE_V1，而该根 {R.get('oi_resonance_runs')} 个 run",
     "影响": "若有人改用该接口，会看到空列表并误判「没有回测」",
     "归属": "框架层",
     "建议": "页面与接口二选一并写进文档；或把过滤键改为 registry 的当前键"},
    {"类型": "计划未做", "事项": "5m 精细化流拆解 / 聪明钱历史 / 多空均价",
     "事实": "STEP1-C 已登记：5m 仅覆盖 2025-10 起；聪明钱无历史；官方无历史均价",
     "影响": "这三类特征无法进入 2021-2025 的回测",
     "归属": "研究层",
     "建议": "保持不做；若要做必须限定在 2025-10 之后的窗口，并与 227 样本分开"},
    {"类型": "证据缺口", "事项": "策略侧无 103 项运行日志",
     "事实": "当前收集 103 项；唯一日志是 93 项（早于代码改动）",
     "影响": "「门禁全绿」对当前代码不成立",
     "归属": "窗口①",
     "建议": "重跑并留原始输出；本单不跑（避免越权与写入）"},
    {"类型": "证据缺口", "事项": "FLOW-OI-01 一致性测试无日志",
     "事实": "test_flow_oi_consistency.py 存在，报告未附运行输出",
     "影响": "其「通过」不可复核；STEP1-C 已指出其漏检前视",
     "归属": "窗口①",
     "建议": "重跑并留日志；同时补 close_time<ts 的前视断言"},
]
write_csv("05_未覆盖与缺口.csv", [{"序号": i + 1, **g} for i, g in enumerate(GAPS)],
          ["序号", "类型", "事项", "事实", "影响", "归属", "建议"])

# ── 自检 ──
check = {
    "矩阵行数": len(matrix),
    "类别分布": {},
    "缺口行数": len(GAPS),
    "测试集行数": len(TEST_ROWS),
    "关键数字": {
        "EA03_run": S.get("ea03_run_dirs"),
        "run_json_候选": S.get("ea03_candidates_from_run_json"),
        "run_json_成交": S.get("ea03_trades_from_run_json"),
        "交接包行": S.get("handoff_rows"),
        "交接包成交": S.get("handoff_filled"),
        "候选差额": S.get("per_run_candidate_gap_total"),
        "成交差额": S.get("per_run_trade_gap_total"),
        "spec_changes": C.get("spec_changes_count"),
        "formal_pass_true": S.get("handoff_formal_pass_allowed_true"),
    },
    "自检": {},
}
for r in matrix:
    check["类别分布"][r["类别"]] = check["类别分布"].get(r["类别"], 0) + 1

check["自检"]["每行都有建议复验方法"] = all(r["建议复验方法"] for r in matrix)
check["自检"]["每行都有实现入口"] = all(r["实现入口"] for r in matrix)
check["自检"]["没有把 UNKNOWN 写成 0"] = all(
    r["已有结果"] != "0" for r in matrix)
check["自检"]["差额=10"] = S.get("per_run_candidate_gap_total") == 10
check["自检"]["差额=8"] = S.get("per_run_trade_gap_total") == 8
check["自检"]["spec_changes=0"] = C.get("spec_changes_count") == 0
check["自检"]["全部通过"] = all(check["自检"].values())

(OUT / "09_matrix_check.json").write_text(
    json.dumps(check, ensure_ascii=False, indent=2), encoding="utf-8")

print("矩阵", len(matrix), "行；缺口", len(GAPS), "条；测试集", len(TEST_ROWS), "行")
print("类别分布", check["类别分布"])
print("自检", check["自检"])
