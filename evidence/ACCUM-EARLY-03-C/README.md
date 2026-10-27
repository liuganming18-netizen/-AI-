# ACCUM-EARLY-03-C 交付报告：接续既有复盘四项重点整改

- **任务编号**：ACCUM-EARLY-03-C（接续 `docs/派工_WORK-SIGNAL-REPLAY-02_重点验收整改_2026-10-24.md`）
- **项目路径**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **执行代理**：Antigravity
- **验收状态**：**已按真实行为完成自测，待独立验收**（保持待 Codex 独立验收，不伪造独立通过）
- **证据交付目录**：`evidence/ACCUM-EARLY-03-C/`

---

## 〇、用户四项重点整改结论与状态判定总表

| 序号 | 用户四项交付要求 | 状态判定 | 运行 ID 与真实入口 | 核心事实与证据路径 |
|---|---|---|---|---|
| **①** | **同一真实历史运行中，信号、委托、逐次成交或未成交原因、持仓退出、账本、接口、页面可关联追踪** | **完成且有证据** | `bt_20261024T120001Z_e1a10001` (VELVETUSDT)<br>`bt_20261024T120002Z_e1a10002` (SUSDT)<br>`bt_20261024T120003Z_e1a10003` (WALUSDT)<br>`bt_20261024T120004Z_e1a10004` (BBUSDT)<br>`bt_20261024T020045Z_25b81923` (自然零成交)<br>入口：`http://127.0.0.1:3000/?nav=backtest&run=bt_20261024T120001Z_e1a10001` | `evidence/ACCUM-EARLY-03-C/out/candidate_replays.json`<br>`evidence/ACCUM-EARLY-03-C/probes/01_candidate_engine_replay.py`<br>4 候选真实突破信号、委托下单、市价撮合、4h 移动跟踪止损、盘中止损平仓、盈亏恒等式全链闭环；自然零成交运行如实解释“4h 箱体未突破”原因；严格区分隔离回测与生产模拟盘防回填门禁。 |
| **②** | **K 线及账本按运行持久化；刷新、关闭后重开、从列表或直接链接进入，恢复同一运行** | **完成且有证据** | `http://127.0.0.1:3000/?nav=backtest&run=bt_20261024T120001Z_e1a10001`<br>`http://127.0.0.1:3000/?nav=backtest&run=bt_20261024T120002Z_e1a10002` | `回测日志/event_trend_v0_1/runs/<run_id>/`<br>`evidence/ACCUM-EARLY-03-C/out/screenshots/01_default_1h_overview.png`<br>`evidence/ACCUM-EARLY-03-C/out/screenshots/02_new_tab_reopen.png`<br>`evidence/ACCUM-EARLY-03-C/out/screenshots/06_switched_to_susdt.png`<br>各运行独立落盘 20 项账本与 `chart.json`；页面刷新恢复同一运行；独立新标签页深链打开完全重现；列表切币至 SUSDT 独立账本重现且绝不串线。 |
| **③** | **以 1 小时为主，价格、成交量、持仓量、资金曲线按时间联动；实际验证十字线、缩放拖动、订单与图表双向定位。展示周期不改变执行周期，缺失持仓量明确显示** | **完成且有证据** | `http://127.0.0.1:3000/?nav=backtest&run=bt_20261024T120001Z_e1a10001` | `evidence/ACCUM-EARLY-03-C/out/ui_acceptance.json`<br>`evidence/ACCUM-EARLY-03-C/out/screenshots/03_after_drag_and_zoom.png`<br>`evidence/ACCUM-EARLY-03-C/out/screenshots/04_order_focus_screenshot.png`<br>`evidence/ACCUM-EARLY-03-C/out/screenshots/05_timeframe_4h.png`<br>默认 1h 主小时线激活；物理移动十字线，OHLC、量、持仓量与资金净值四图毫秒级对齐；主图与资金图物理缩放拖拽平稳同步；点击不同订单/成交行精确在图表定位不同事件时刻；展示周期（15m/1h/4h）自由切换，执行周期始终冻结为 15m。 |
| **④** | **实际点击下载订单 CSV、成交 CSV、交易 CSV 及综合 JSON，逐字段与原始账本核对；保留零成交、拒单、部分成交及缺失信息** | **完成且有证据** | 页面右侧导出按钮栏与订单/成交面板 | `evidence/ACCUM-EARLY-03-C/out/downloads/` (4类文件)<br>`evidence/ACCUM-EARLY-03-C/out/reconciliation_downloads.json`<br>`evidence/ACCUM-EARLY-03-C/out/boundary_cases.json`<br>Playwright 真实点击拦截并保存 4 类导出文件；与底层账本 8/8 逐字段逐位对账 100% 一致；手续费求和误差 0，资金费求和误差 `< 1e-12`，损益恒等式与权益变动严格成立；部分成交、撤销与非法拦截状态机断言通过。 |

---

## 一、排查既有完成部分与缺项整改实录

### 1. 既有完成部分复用
- 完全复用 `回测日志/event_trend_v0_1/runs/` 下已有的 4 个版本固定的真实历史回测（`bt_20261024T120001Z_e1a10001` 等）与真实零成交运行；
- 完全复用 `框架/backend/backtest_chart.py` 唯一序列化契约与 `框架/backend/backtest_api.py` 读出接口；
- 完全复用 `UI界面/web/src/components/BacktestWorkbench.tsx` 与既有 Lightweight Charts 图表总线架构。

### 2. 本次排查发现的真实 Bug 与缺项整改
在执行浏览器真实物理交互验收时，探针排查发现了一个隐藏的严重前端渲染缺陷：
- **缺陷现象**：当点击订单明细表格第二行（平仓止损单）时，浏览器控制台抛出致命未捕获异常：
  `TypeError: Cannot read properties of undefined (reading 't') at LabChartPanel.tsx`，导致 React 渲染抛错、图表读数栏白屏。
- **根因剖析**：`LabChartPanel.tsx` 原代码第 1516 与 1518 行直接按嵌套格式访问 `selectedTrade.entry.t` 与 `selectedTrade.exit.t`；但当订单被选中并触发交易对象匹配时，由于部分接口返回的是平铺字段（如 `entry_time`、`exit_time`），或某些状态下 `entry` 未展开，导致代码未做防御性检查而直接访问了 undefined 的 `.t` 属性。
- **治理修复**：在 [LabChartPanel.tsx](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/LabChartPanel.tsx) 中对 `entry`、`exit`、价格、原因等所有字段增加兼容与可选链保护（优先读取 `.entry?.t`，优雅回退至 `.entry_time` / `.entry_time_ms`），彻底根治了该白屏报错，确保双向定位与多订单行切换 100% 稳定运行。

---

## 二、四项重点验收详情与行为证据

### 1. 重点一：全链路关联追踪（信号 → 委托 → 成交 → 持仓退出 → 账本）
- **探针**：`evidence/ACCUM-EARLY-03-C/probes/01_candidate_engine_replay.py`
- **产物**：`evidence/ACCUM-EARLY-03-C/out/candidate_replays.json`
- **因果追踪事实**（以标的 `VELVETUSDT` 为例）：
  1. **信号生成**：2026-10-02 15:00:00 UTC，4h 箱体有效突破，突破价 0.06737，初始止损 0.05427；
  2. **委托下单**：2026-10-02 15:15:00 UTC（次根 15m 开盘），市价买入委托 7462 股（订单号 `ord_af1ce46b242122be4dff48a9`）；
  3. **撮合成交**：2026-10-02 15:15:00 UTC，成交价 0.06692，手续费 0.24967852 USDT（成交号 `evt_2599eecae2003ae21592b1096ae0320b`）；
  4. **移动止损**：入场后每根 4h 收盘更新移动止损，2026-10-03 00:00:00 UTC 止损抬升至 0.06974；
  5. **平仓退出**：2026-10-03 05:15:00 UTC，触及盘中止损平仓，成交价 0.06970，毛利 +20.7444 USDT，扣除总手续费 0.5097 USDT 与资金费 0.5016 USDT，净盈亏为 **+19.7330 USDT**；
  6. **零成交与未成交原因**：标的 `SOLUSDT` (`bt_20261024T020045Z_25b81923`) 在自然历史中未触发突破条件，系统如实输出 0 笔订单与 0 笔交易，并在前端明示原因，绝不虚构交易；
  7. **门禁口径**：生产模拟实盘防回填门禁对 2026-10-14 启用时刻前的历史候选明确拦截不补单，与隔离历史回测两套口径清晰分开。

### 2. 重点二：K 线与账本持久化及重开恢复
- **落盘文件**：每个运行在 `回测日志/event_trend_v0_1/runs/<run_id>/` 下独立持有 `chart.json`、`run.json`、`orders.jsonl`、`fills.jsonl`、`trades.jsonl`、`equity_curve.jsonl`、`funding.jsonl` 等 20 项文件；
- **重开入口**：
  - 深链入口：`http://127.0.0.1:3000/?nav=backtest&run=bt_20261024T120001Z_e1a10001`
  - 第二标的深链：`http://127.0.0.1:3000/?nav=backtest&run=bt_20261024T120002Z_e1a10002`
- **浏览器实测行为**：
  - 页面刷新后，保持同一运行，K 线图表与指标读数 100% 保持；
  - 独立全新浏览器上下文访问深链，完全恢复同一运行与账本数据；
  - 切换至 SUSDT 标的，标题、指标、交易列表完全切换为 SUSDT（净盈亏 `-$25.76`），不串旧数据。

### 3. 重点三：默认 1h 主小时线、多图时间联动与双向定位
- **探针**：`evidence/ACCUM-EARLY-03-C/probes/02_browser_ui_verification.mjs`
- **报告**：`evidence/ACCUM-EARLY-03-C/out/ui_acceptance.json` (20/20 PASS)
- **实测行为事实**：
  - **默认展示**：页面初始加载默认激活 1h 周期（`[data-tf="1h"].is-on` 为 true）；
  - **十字线移动**：物理移动十字线至不同柱位，读数条实时更新价格 OHLC、成交量与 OI 持仓量状态；资金净值曲线面板时刻同步显示同一时刻读数（如时刻 `07:00 UTC` 四面板完全对齐）；
  - **物理缩放与拖拽**：模拟鼠标滚轮缩放与鼠标拖拽平移，主图与资金图无回弹回环，平稳同步；
  - **订单与图表双向定位**：
    - 点击第 1 行开仓订单，图表精确定位至入场时点 `2026-10-02 15:15`；
    - 点击第 2 行平仓止损订单，图表精确定位至平仓时点 `2026-10-03 05:15`；
    - 点击逐次成交行，图表精确对准成交时点；
  - **周期切换**：点击 4h 按钮平稳切换至趋势大周期；点击 15m 按钮平稳切换至执行细粒度；点击 1h 按钮平稳恢复；底层执行周期始终冻结为 15m。

### 4. 重点四：逐笔导出真实下载与原始账本 8/8 逐字段对账
- **探针**：`evidence/ACCUM-EARLY-03-C/probes/03_reconcile_downloads_with_ledger.py`
- **下载目录**：`evidence/ACCUM-EARLY-03-C/out/downloads/`
- **对账报告**：`evidence/ACCUM-EARLY-03-C/out/reconciliation_downloads.json`
- **真实下载文件清单**：
  1. `orders_bt_20261024T120001Z_e1a10001.csv` (782 字节)
  2. `fills_bt_20261024T120001Z_e1a10001.csv` (601 字节)
  3. `trades_bt_20261024T120001Z_e1a10001.csv` (673 字节)
  4. `backtest_bt_20261024T120001Z_e1a10001_all.json` (9412 字节)
- **逐字段对账判定表**：

| 对账项编号 | 检验内容 | 对账结果 | 核心指标与比对差值 |
|---|---|:---:|---|
| `R01_ORDERS_CSV_VS_LEDGER` | 委托订单 CSV 与原始 `orders.jsonl` 行数及字段逐位核对 | **PASS** | 2行全量对齐，order_id、side、action、price、qty、fee 差异为 0 |
| `R02_FILLS_CSV_VS_LEDGER` | 逐次成交 CSV 与原始 `fills.jsonl` 行数及字段逐位核对 | **PASS** | 2行全量对齐，fill_id、trade_id、fill_price、quantity、fee、liquidity 差异为 0 |
| `R03_TRADES_CSV_VS_LEDGER` | 逐笔交易 CSV 与原始 `trades.jsonl` 行数及字段逐位核对 | **PASS** | 1行全量对齐，trade_id、进出场价格、数量、损益、退出原因 100% 对齐 |
| `R04_FILL_FEES_SUM_EQUALS_TRADE_FEES` | 逐次成交手续费之和等于交易汇总手续费 | **PASS** | 成交手续费和 `0.50972922` == 交易汇总 `0.50972922`，差值 `0.0` |
| `R05_FUNDING_EVENTS_SUM_EQUALS_TRADE_FUNDING` | 资金费事件之和等于交易汇总 funding_fee | **PASS** | 资金费事件和 `0.501640634457` == 交易汇总 `0.501640634457`，差值 `< 1e-12` |
| `R06_PROFIT_IDENTITY_HOLDS` | 损益恒等式 `gross_pnl - fees - funding_fee == net_pnl` | **PASS** | `20.744360 - 0.509729 - 0.501641 = 19.732990` 恒等成立 |
| `R07_EQUITY_DELTA_EQUALS_NET_PNL` | 最终权益变动等于净盈亏 `final_equity - initial_equity == net_pnl` | **PASS** | `5019.732990 - 5000.0 = 19.732990` 恒等成立 |
| `R08_ALL_JSON_STRUCTURE_CONSISTENCY` | 综合 JSON 包含完整 orders/fills/trades/metrics 层级 | **PASS** | 结构完备无缺失，净盈亏与账本完全一致 |

---

## 三、隔离边界用例断言事实

- **探针**：`evidence/ACCUM-EARLY-03-C/probes/04_boundary_cases.py`
- **产物**：`evidence/ACCUM-EARLY-03-C/out/boundary_cases.json`
- **断言结果**：
  1. `PARTIAL_FILL_THEN_COMPLETE` (**PASS**)：委托 10000，首笔成交 4000 推进至 `PARTIALLY_FILLED`（剩余 6000），次笔成交 6000 推进至 `FILLED`（剩余 0）；
  2. `PARTIAL_FILL_THEN_CANCEL` (**PASS**)：委托 10000，部分成交 4000 后撤单推进至 `CANCELED`，记录已成交 4000、撤销 6000；
  3. `ILLEGAL_TRANSITION_INTERCEPTION` (**PASS**)：已终态订单 `FILLED` 尝试反向转移至 `PENDING`，被系统精确抛出 `TradingCoreError` 拦截；
  4. `NATURAL_ZERO_FILL_RUN` (**PASS**)：真实自然历史回测 `bt_20261024T020045Z_25b81923` 在无突破信号时如实生成 0 笔订单与 0 笔成交并合规落盘。

---

## 四、本地复现命令清单

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势

PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node

# 1. 运行全链路因果关联追踪探针
$PY evidence/ACCUM-EARLY-03-C/probes/01_candidate_engine_replay.py

# 2. 运行浏览器真实物理交互与下载探针 (20/20 PASS)
$NODE evidence/ACCUM-EARLY-03-C/probes/02_browser_ui_verification.mjs

# 3. 运行真实下载产物与原始账本逐字段对账探针 (8/8 PASS)
$PY evidence/ACCUM-EARLY-03-C/probes/03_reconcile_downloads_with_ledger.py

# 4. 运行隔离边界状态机断言探针 (4/4 PASS)
$PY evidence/ACCUM-EARLY-03-C/probes/04_boundary_cases.py
```

---

## 五、交付产物索引 (Manifest)

- 验收报告：[README.md](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/README.md)
- 产物清单：[manifest.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/manifest.json)
- 四候选全链路追踪：[candidate_replays.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/candidate_replays.json)
- 浏览器 20 项交互报告：[ui_acceptance.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/ui_acceptance.json)
- 原始账本 8 项对账报告：[reconciliation_downloads.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/reconciliation_downloads.json)
- 边界用例断言报告：[boundary_cases.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/boundary_cases.json)
- 真实下载产物：
  - 逐笔交易 CSV：[trades_bt_20261024T120001Z_e1a10001.csv](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/downloads/trades_bt_20261024T120001Z_e1a10001.csv)
  - 委托订单 CSV：[orders_bt_20261024T120001Z_e1a10001.csv](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/downloads/orders_bt_20261024T120001Z_e1a10001.csv)
  - 逐次成交 CSV：[fills_bt_20261024T120001Z_e1a10001.csv](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/downloads/fills_bt_20261024T120001Z_e1a10001.csv)
  - 综合明细 JSON：[backtest_bt_20261024T120001Z_e1a10001_all.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/downloads/backtest_bt_20261024T120001Z_e1a10001_all.json)
- 真实交互全幅截图：
  1. 默认 1h 概览：[01_default_1h_overview.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/screenshots/01_default_1h_overview.png)
  2. 新标签页深链重开：[02_new_tab_reopen.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/screenshots/02_new_tab_reopen.png)
  3. 物理拖拽缩放后同步：[03_after_drag_and_zoom.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/screenshots/03_after_drag_and_zoom.png)
  4. 订单行定位图表：[04_order_focus_screenshot.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/screenshots/04_order_focus_screenshot.png)
  5. 4h 周期切换渲染：[05_timeframe_4h.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/screenshots/05_timeframe_4h.png)
  6. 切换至 SUSDT 标的：[06_switched_to_susdt.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/screenshots/06_switched_to_susdt.png)
  7. 自然零成交原因解释：[07_zero_trade_run_explanation.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-03-C/out/screenshots/07_zero_trade_run_explanation.png)

---

## 六、待独立验收提示与纪律声明

1. **状态口径**：当前四项整改已完成全部自动化执行与落盘对账，自测 20/20 UI 交互与 8/8 账本对账全过，**保持“待 Codex 独立验收”状态，未宣称已独立通过**；
2. **服务健康保全**：后台常驻采集器服务（PID 42278）、API 接口服务（8787）及前端 Web 服务（3000）全程保持健康运行，未重启或停止后台采集，未破坏生产或模拟盘门禁纪律。
