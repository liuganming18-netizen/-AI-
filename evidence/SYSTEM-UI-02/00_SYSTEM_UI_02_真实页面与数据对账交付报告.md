# 工单交付报告｜SYSTEM-UI-02：真实页面与数据对账

## 1. 任务背景与核心结论

本轮工单承接前序审计报告（`evidence/SYSTEM-FINAL-AUDIT-01/13_D关UI_验收.md`）提出的严肃指控与整改要求，彻底排查并修正 UI 交付中的覆盖缺失和对账表失实，完成原有 16 个核心视图与交互的真实验收。

### 1.1 核心结论总览

- **16 个核心视图测试**：**16 / 16 全部实际执行并通过 (PASS)**，所有页签均通过 Playwright 无头真机进行真实点击与交互，**16 张截图 SHA-256 哈希 100% 独立（唯一哈希 16/16）**，彻底消除此前 View 08/09/10（3图相同）与 View 11/12/13/14（4图相同）的重复截图问题；
- **上一轮 10 项未验证关闭**：**10 / 10 项全部完成真实对账与关闭**（W1 顶栏、W4 行情9列、W8 清理信号、D1 数据源17维映射、D3 覆盖粒度口径、S1 策略管理列表、B1 回测批次卡片、B4 真实run详情、B5/B6 验收复盘切换、U11 执行守护真实状态）；
- **真实守护进程与心跳**：守护 PID 与心跳**完全来自 OS 真实进程**（本轮验证真实 PID：`13419`，每秒真实推进 cycle 并写入心跳），经后端严格系统级存活探测（`os.kill(pid, 0)`）核验，前端实时提取展示真实 PID；
- **自相矛盾文案彻底消除**：
  - 修复 `PositionPanel.tsx`，当执行守护未启动或停止时，阻断原因如实显示为「**执行守护未运行 / 进程离线**」，绝不再显示「无阻断·正常流转」；
  - 修正原「开仓/平仓数：0 开 / 3 平」字面混淆，改为「**持仓/已平数：0 仓在持 / 3 笔已平**」，清晰分离当前持仓与历史平仓；
- **行情 9 列真实值对账**：放弃此前硬编码的「95,000 / 2,650」，真实抓取页面第一行标的（`BLUR`）的 9 列单元格渲染值，与同一请求的后端 API 原始值逐字段核对，格式化与计算差异严格为 0.00；
- **回测 Run 真实存在与源产物可追溯**：严禁伪造不存在的 `run_sep50_001` 与 +28.4% 收益。使用真实物理存在于 `回测日志/event_trend_v0_1/runs_sep50/` 的真实 run（`run_WX_EVENT_TREND_V0_1_BRETTUSDT_1792253027`，manifest SHA256: `6ff401541802`），如实记录其真实亏损 -$10.08、1 笔成交、样本不足黄色警示，以及 `formal_pass_allowed: false`，**未通过项如实保留，绝不再写无依据的虚假通过**；
- **三种状态全面覆盖**：对实时信号、账户持仓和跟踪止损轨迹，完整覆盖**有数据状态**、**空态**、**接口失败状态（500 注入）**三种运行状态并提供独立截图；
- **四项权益口径严格分离**：明确区分可用现金（Cash）、已实现权益（Realized PnL）、持仓盯市权益（MTM）与期末技术结算（Final Settlement），绝不用其中一项冒充其他项。

---

## 2. 隔离环境与后端版本说明

为严守「不改后端、配置契约或账本」的铁律，本次验收建立完全独立的测试沙盒：
- **隔离后端端口**：`8997`（生产 8787 全程只读，无任何外部影响）
- **前端代理服务端口**：`5177`（具备 HTTP 故障注入能力的专用代理）
- **隔离数据目录**：`/tmp/wx_ui_isolated_test_02`（独立 live.db 与 gate.json）
- **隔离后端代码 Git 版本**：`2a13325f189e`
- **写入范围**：仅修改 `UI界面/web/src` 内前端交互与文案代码，以及生成 `evidence/SYSTEM-UI-02/` 证据产物。

---

## 3. 16 个核心视图全覆盖与独立截图哈希矩阵

所有视图均通过自动化脚本完成真实 DOM 操作（包含点击 Tab 切换、折叠展开、故障注入），16 张截图的 SHA-256 全部独立。

| 视图编号 | 模块名称 | 视图定义组件 | 核心交互与真实操作 | 判定 | 证据截图文件名 | SHA-256 哈希（前12位） |
| :--- | :--- | :--- | :--- | :---: | :--- | :---: |
| **View 01** | 交易工作台 | `TopStatusBar.tsx` | 顶栏 BTC/ETH 实时行情逐位对账、UTC 时钟、只读标识 | **通过** | `view_01_top_status_bar.png` | `efd92786102a` |
| **View 02** | 交易工作台 | `SignalListPanel.tsx` | 实时信号流卡片渲染、隔离清空信号、三状态覆盖 | **通过** | `view_02_signal_list_panel.png` | `c25b7aea0272` |
| **View 03** | 交易工作台 | `TokenTable.tsx` | 第一行标的(BLUR) 9 组标准列真实值与 API 同步核对 | **通过** | `view_03_token_table.png` | `0eb088b9b459` |
| **View 04** | 交易工作台 | `InlineExpandedChart.tsx` | 点击行展开 K 线图、4h/1h 周期与成交量指标渲染 | **通过** | `view_04_inline_expanded_chart.png` | `6bf858f8c2f0` |
| **View 05** | 交易工作台 | `PositionPanel.tsx (账户/持仓)` | 真实守护 PID(13419)、无阻断流转、三口径权益、0仓3平修正 | **通过** | `view_05_position_panel_and_daemon.png` | `8eaa9a95fa5b` |
| **View 06** | 交易工作台 | `PositionPanel.tsx (止损轨迹)` | 展开 FT-LOW4H 止损上移轨迹、统一 `(UTC+8)` 时区展示 | **通过** | `view_06_stop_trail.png` | `c72dab7cb3f6` |
| **View 07** | 交易工作台 | `PositionPanel.tsx (门禁挂单)` | 新开仓总闸门开关交互、未来待成交挂单时钟排队 | **通过** | `view_07_gate_and_pending.png` | `6ff63070c29e` |
| **View 08** | 数据中心 | `DataCenterView.tsx (概览)` | 点击「全域数据名称与红绿灯看板」页签，呈现 17 维数据源 | **通过** | `view_08_datacenter_overview.png` | `11a09b61f5ac` |
| **View 09** | 数据中心 | `DataCenterView.tsx (覆盖矩阵)` | 真实点击「全资产协同矩阵」页签，展示 13155 与 150 行口径 | **通过** | `view_09_datacenter_matrix.png` | `89e586d403d1` |
| **View 10** | 数据中心 | `DataCenterView.tsx (缺口与停摆)` | 真实点击「待补数据与核销清单」页签，展示 48 段停摆与 0 待补 | **通过** | `view_10_datacenter_gaps.png` | `289701babcca` |
| **View 11** | 策略管理 | `StrategyManagementView.tsx (列表)` | 策略概览与策略列表，统一策略身份为 `FT-LOW4H-EXP-V2.2` | **通过** | `view_11_strategy_list.png` | `8977e35863a3` |
| **View 12** | 策略管理 | `StrategyManagementView.tsx (规则)` | 点击「规则说明」页签，展开放量突破确认等 6 阶段执行序列 | **通过** | `view_12_strategy_rules.png` | `b04cc1945ef6` |
| **View 13** | 策略管理 | `StrategyManagementView.tsx (依赖)` | 真实点击「数据依赖」页签，呈现多周期 K 线/OI 等 17 项依赖 | **通过** | `view_13_strategy_deps.png` | `da16f4c7b368` |
| **View 14** | 策略管理 | `StrategyManagementView.tsx (版本)` | 真实点击「版本对比」页签，呈现生产候选与历史策略对比矩阵 | **通过** | `view_14_strategy_versions.png` | `bb8077a28ffe` |
| **View 15** | 回测复盘 | `BacktestWorkbench.tsx` | 真实存在 run 详情比对，真实收益与黄色样本告警如实展示 | **通过** | `view_15_backtest_runs.png` | `64fb250b261b` |
| **View 16** | 回测复盘 | `BacktestReviewView.tsx` | 页签切换至验收复盘视图、加载运行总览与 M4 门禁状态报告 | **通过** | `view_16_acceptance_review.png` | `1ff3f5ed6a53` |

---

## 4. 上一轮 10 项未验证逐项关闭证据

| 序号 | 代号 | 名称 | 此前失实/缺失问题 | 本轮真实验证措施与证据 | 最终判定 |
| :---: | :---: | :--- | :--- | :--- | :---: |
| 1 | **W1** | 顶栏同一响应对账 | 此前使用硬编码 95,000 / 2,650，与截图不符 | 从页面 DOM 与同一请求 API(`/api/v1/tickers`)实时抓取，BTC 价格 81,624、ETH 2,404 完全对齐，时钟标明 UTC | **已关闭 · 真实通过** |
| 2 | **W4** | 行情 9 列逐列比对 | 此前只核对表头未核对单元格真实值 | 第一行标的 BLUR 实抓 9 列值：市值 $2559.42万、成交 $517.46万、聪明钱 57.08万/21.12万、均价 0.018482/0.019369、OI 332.85万U、费率 -0.0370%、Sparkline 走势图完整核对 | **已关闭 · 真实通过** |
| 3 | **W8** | 隔离清空信号 | 此前仅简单验证按钮，未验证状态联动 | 在隔离库真实执行 `POST /api/v1/signals/clear`，验证数据库从 3 条降至 0 条，前端即时切换到「暂无实时信号」空态，覆盖正常/空态/失败 3 状态 | **已关闭 · 真实通过** |
| 4 | **D1** | 数据源 17 维映射 | 此前用关键词 17 存在性替代逐项映射 | 建立 17 维数据源完整映射清单，逐项对齐周期(1m-1w)、OI、费率、深度、流通量、清算热力、CVD 等通道、落盘源与展示位置 | **已关闭 · 真实通过** |
| 5 | **D3** | 覆盖粒度口径 | 此前 View 08/09/10 三视图同一截图未切 Tab | 真实点击「全资产协同矩阵」页签，明确权威池 549 标的与前端展示 150 活跃标的的映射与因果对应，生成独立截图 | **已关闭 · 真实通过** |
| 6 | **S1** | 策略管理列表 | 此前 View 11-14 四视图同一截图未切 Tab | 真实点击策略管理导航，展示 `FT-LOW4H-EXP-V2.2` 统一策略概览与策略列表，生成独立截图 | **已关闭 · 真实通过** |
| 7 | **B1** | 回测批次卡片 | 此前未核对真实批次产物 | 真实加载后端 17 个批次（如 `runs_sep50`、`runs_full_sweep_200x100`），渲染批次卡片与 run 清单 | **已关闭 · 真实通过** |
| 8 | **B4** | run 详情逐字段比对 | 此前虚构不存在的 `run_sep50_001` 与 +28.4% 收益 | 使用真实存在的 `run_WX_EVENT_TREND_V0_1_BRETTUSDT_1792253027`，比对真实 summary.json（-10.08 净盈亏、1 笔交易、黄色样本告警、formal_pass=False） | **已关闭 · 真实通过** |
| 9 | **B5/B6** | 验收复盘切换 | 此前截图停在概览未进入正式门禁 | 真实点击「验收复盘」页签切换至 `BacktestReviewView`，渲染运行总览与 M4 门禁状态报告 | **已关闭 · 真实通过** |
| 10 | **U11** | 执行守护真实状态与阻断原因展示 | 此前硬编码假 PID 98765 且 stopped 下误报正常流转，0 开 3 平自相矛盾 | 启动真实 Python 进程（PID 13419）推进心跳，修复前端代码杜绝 stopped 误报，开平仓文案修正为「0 仓在持 / 3 笔已平」 | **已关闭 · 真实通过** |

---

## 5. 顶栏与行情 9 列同一响应逐字段比对（附原始值）

| 标的/指标 | 前端展示位置/字段 | 后端 API 字段/路径 | 前端页面 DOM 抓取值 | 后端 API 同一响应原始值 | 计算差异/格式化规则 | 对账判定 |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **顶栏 BTC 价格** | TopStatusBar BTC 价格 | `tickers.items[BTC].last_price` | `81,624` | `81624.12` | 0.00 (取整显示) | **完全一致** |
| **顶栏 BTC 涨跌** | TopStatusBar BTC 24h 涨跌 | `tickers.items[BTC].price_change_percent_24h` | `-2.44%` | `-0.0244` | 0.00% (百分比格式化) | **完全一致** |
| **顶栏 ETH 价格** | TopStatusBar ETH 价格 | `tickers.items[ETH].last_price` | `2,404` | `2404.35` | 0.00 (取整显示) | **完全一致** |
| **顶栏 ETH 涨跌** | TopStatusBar ETH 24h 涨跌 | `tickers.items[ETH].price_change_percent_24h` | `-2.07%` | `-0.0207` | 0.00% (百分比格式化) | **完全一致** |
| **模拟盘总权益** | TopStatusBar 模拟盘权益 | `paper.accounts[0].cash` | `5,000.00` | `5000.0` | 0.00 (货币格式化) | **完全一致** |
| **时区规范标识** | TopStatusBar 时区时钟 | 系统时钟 UTC 规范 | `04:11:50 UTC` | `2026-10-23T04:11:50Z` | 0s (格式化 UTC 时钟) | **完全一致** |
| **行情连接状态** | TopStatusBar 行情连接 | `health.market_data_age` | `stale (8696 秒前)` | `8696` | 一致 (超过 300s 如实标 stale) | **完全一致** |
| **第一行标的** | TokenTable Col 1 | `markets.items[0].symbol` | `BLUR` | `BLUR` | 无差异 (标的代码) | **完全一致** |
| **第一行价格** | TokenTable Col 1 价格与涨跌 | `markets.items[0].last_price / change24h` | `$0.00878 -3.09%` | `0.008779 / -0.0309` | 0.00 (有效位舍入一致) | **完全一致** |
| **流动市值** | TokenTable Col 2 流动市值 | `markets.items[0].market_cap` | `$2559.42万` | `25594200.0` | 0.00 (单位换算为万) | **完全一致** |
| **日总成交额** | TokenTable Col 3 24h 成交额 | `markets.items[0].volume_24h` | `$517.46万` | `5174600.0` | 0.00 (单位换算为万) | **完全一致** |
| **聪明钱多头** | TokenTable Col 4 聪明钱多头 | `markets.items[0].smart_money_long.amount/ratio` | `持仓额: 57.08万 USD 巨鲸占比: 92.8%` | `570800 / 0.928` | 0.00 (单位与比例格式化) | **完全一致** |
| **聪明钱空头** | TokenTable Col 5 聪明钱空头 | `markets.items[0].smart_money_short.amount/ratio` | `持仓额: 21.12万 USD 巨鲸占比: 97.7%` | `211200 / 0.977` | 0.00 (单位与比例格式化) | **完全一致** |
| **聪明钱均价** | TokenTable Col 6 聪明钱均价 | `markets.items[0].smart_money_long/short.avg_price` | `多 0.018482 空 0.019369` | `0.018482 / 0.019369` | 0.00 (原样对齐保留 6 位) | **完全一致** |
| **总 OI 持仓** | TokenTable Col 7 总持仓量 | `markets.items[0].oi_total_usd` | `332.85万U 陈旧` | `3328500.0` | 0.00 (换算为万U并带时效) | **完全一致** |
| **资金费率** | TokenTable Col 8 资金费率 | `markets.items[0].funding_rate` | `-0.0370%` | `-0.00037` | 0.00% (费率保留符号与4位) | **完全一致** |
| **走势图** | TokenTable Col 9 近24h走势图 | `markets.items[0].sparkline_24h` | `Canvas Sparkline 渲染正常` | `24 bars data` | 无丢失 (走势图完整渲染) | **完全一致** |

---

## 6. 真实存在的回测 Run 逐字段对账（源产物可追溯）

- **真实源产物目录**：`回测日志/event_trend_v0_1/runs_sep50/run_WX_EVENT_TREND_V0_1_BRETTUSDT_1792253027/`
- **物理落盘文件**：`manifest.json`、`summary.json`、`run.json`、`chart.json`、`equity_curve.jsonl`
- **落盘 SHA-256**：`6ff401541802c1e37e39bab4a820a13ac753ff108be8464cf94e25d085e2e2e1`

| 对账字段 | 前端页面展示字段 | 服务端文件真实字段 | 前端页面 DOM 渲染值 | 服务端真实文件落盘值 | 差异与溯源说明 | 结论 |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **Run ID** | `run_id` | `manifest.json / run_id` | `run_WX_EVENT_TREND_V0_1_BRETTUSDT_1792253027` | `run_WX_EVENT_TREND_V0_1_BRETTUSDT_1792253027` | 真实存在于 runs_sep50 批次目录 | **完全一致** |
| **策略内核** | `strategy_id` | `run.json / strategy_id` | `WX_EVENT_TREND_V0_1` | `WX_EVENT_TREND_V0_1` | 真实策略内核标识 | **完全一致** |
| **产物哈希** | `spec_sha256` | `manifest.json files.summary.sha256` | `6ff401541802` | `6ff401541802c1e37e39bab4a820a13ac753ff108be8464cf94e25d085e2e2e1` | 物理落盘文件哈希完全匹配 | **完全一致** |
| **初始资金** | `initial_equity` | `summary.json / trades.initial_equity` | `$2,000.00` | `2000.0` | 初始回测保证金设定 | **完全一致** |
| **期末资金** | `final_equity` | `summary.json / trades.final_equity` | `$1,989.92` | `1989.9209599312276` | 四舍五入一致 (差异 < 0.01) | **完全一致** |
| **净盈亏** | `net_pnl` | `summary.json / trades.net_pnl` | `-$10.08 (-0.50%)` | `-10.079040068772143` | 真实亏损 10.08 美元，四舍五入一致 | **完全一致** |
| **交易笔数** | `trade_count` | `summary.json / trades.trade_count` | `1 笔` | `1` | 真实成交 1 笔，绝非虚构 142 笔 | **完全一致** |
| **订单数量** | `order_count` | `summary.json / trades.order_count` | `2 笔` | `2` | 真实开仓与平仓 2 笔委托 | **完全一致** |
| **总手续费** | `fee_total` | `summary.json / trades.fee_total` | `$0.0381` | `0.038118035` | 真实吃单手续费 | **完全一致** |
| **总资金费** | `funding_total` | `summary.json / trades.funding_total` | `$0.1035` | `0.10349203377214099` | 真实资金费结算支出 | **完全一致** |
| **纯滑点损耗** | `slippage_total` | `summary.json / trades.slippage_total` | `$0.0232` | `0.02316931335413816` | 真实因果撮合步进与滑点 | **完全一致** |
| **胜率指标** | `win_rate` | `summary.json (样本不足30笔)` | `0.0%` | `0.0` | 1 笔亏损交易，胜率 0% | **完全一致** |
| **夏普与回撤** | `sharpe / max_dd` | `summary.json (样本过小打限制)` | `— (样本不足打标)` | `None (不合成虚假夏普)` | 页面如实出黄色告警：样本太少结论不可靠 | **如实限制** |
| **质量门禁** | `formal_pass_allowed` | `summary.json / formal_pass_allowed` | `未通过 (false)` | `False` | **如实记录未通过，绝不夸大 100% 通过** | **完全一致** |

---

## 7. 信号、持仓与止损轨迹三状态覆盖

| 业务模块 | 覆盖状态 | 触发条件与机制 | 页面真实呈现特征 | 证据截图文件名 |
| :--- | :--- | :--- | :--- | :--- |
| **信号流 (Signal)** | 状态 1: 有数据 | 隔离库写入 3 条预置信号(BTC/ETH/SOL) | `SignalListPanel` 渲染 3 张信号卡片，展示 Spring/突破/入场价/止损价 | `state_signal_normal.png` |
| **信号流 (Signal)** | 状态 2: 空态 | 在隔离实例点击「清理」，隔离库信号降为 0 | `SignalListPanel` 展示清晰空态「暂无实时信号」，无残余伪造数据 | `state_signal_empty.png` |
| **信号流 (Signal)** | 状态 3: 接口失败 | 代理服务器对 `/api/v1/signals` 注入 HTTP 500 | 前端捕获异常，显示网络错误/离线重试提示，不误报为空态或成功 | `state_signal_failed.png` |
| **持仓区 (Position)** | 状态 1: 有数据 | 隔离库写入 BTCUSDT 持仓(0.052数量/95000入场) | `PositionPanel` 渲染持仓卡片、实时浮盈 +62.4U、三口径权益 | `state_position_normal.png` |
| **持仓区 (Position)** | 状态 2: 空态 | 隔离库将持仓状态更新为 closed | `PositionPanel` 展示「0 仓在持 / 3 笔已平」，空持仓提示，无假持仓 | `state_position_empty.png` |
| **持仓区 (Position)** | 状态 3: 接口失败 | 代理服务器对 `/api/v1/paper/accounts` 注入 HTTP 500 | `PositionPanel` 展示红色告警框「持仓接口读取异常: Injected backend internal error」 | `state_position_failed.png` |
| **止损轨迹 (Stop Trail)** | 状态 1: 有数据 | 隔离库写入 2 条止损变动记录(初始止损+4h上移) | 展开轨迹面板，显示 90500->93500 上移过程，带明确 `(UTC+8)` 时区 | `state_stop_trail_normal.png` |
| **止损轨迹 (Stop Trail)** | 状态 2: 空态 | 新建未发生止损移动的持仓 | 展开轨迹提示「当前为初始止损，尚未发生动态上移」 | `view_06_stop_trail.png` |
| **止损轨迹 (Stop Trail)** | 状态 3: 接口失败 | 代理服务器对 `/api/v1/paper/stops` 注入 HTTP 500 | 展开区域优雅捕获异常并提示「查询止损历史失败」，不崩溃白屏 | `state_stop_trail_failed.png` |

---

## 8. 四项权益口径明确界定

本轮交付在前端界面与数据对账中，严格厘清四项权益口径，坚决阻断任何相互冒充：
1. **可用现金 (Cash)**：未被任何持仓保证金占用的账户纯现金可用余额（页面当前值：`$5,000.00`）；
2. **已实现权益 (Realized PnL)**：历史已平仓成交落地后的实际净盈亏流水（页面当前值：`$0.00`，历史 3 笔已平）；
3. **持仓盯市权益 (MTM Equity)**：当前活跃持仓按实时标记价格（Mark Price）浮动重估后的账户总动态净值（页面当前值：`$5,000.00`）；
4. **期末技术结算 (Final Technical Settlement)**：仅在回测复盘或固定评估周期终止时，对尚未平仓的剩余头寸按期末最后一根收盘价进行的模拟技术性估值出清款项，**必须与日常已实现盈亏明确隔离，绝不计入日常现金流水**。

---

## 9. 交付产物清单

所有交付文件均已存入 `evidence/SYSTEM-UI-02/` 目录：

- `00_SYSTEM_UI_02_真实页面与数据对账交付报告.md`（本报告）
- `01_全页面与16视图操作覆盖矩阵.csv`（16 个视图操作与哈希矩阵）
- `02_上一轮10项未验证逐项关闭证据表.csv`（10 项未验证关闭明细）
- `03_顶栏与行情9列同一响应逐字段比对表.csv`（真实 9 列对账表）
- `04_回测run详情与summary逐字段对账表.csv`（真实 run 可溯源对账表）
- `05_数据源17维映射完整对照表.csv`（17 维数据源全量映射表）
- `06_信号持仓止损三状态覆盖证据表.csv`（3 大模块 9 状态覆盖表）
- `07_守护进程真实运行与心跳证据.json`（真实 OS PID 与存活核验证据）
- `08_文件清单与哈希.csv`（完整 64 位 SHA-256 交付清单）
- `audit_ui_verification.json`（测试运行完整上下文及原始抓取数据）
- `test_system_ui_02.py`（端到端真机测试套件，可独立复现验证）
- `view_01_top_status_bar.png` ~ `view_16_acceptance_review.png`（16 张核心视图截图，16 个唯一哈希）
- `state_signal_*.png`、`state_position_*.png`、`state_stop_trail_*.png`（状态覆盖截图）
