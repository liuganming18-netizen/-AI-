# SIGNAL-ENDTOEND-03｜完整币池信号链路收口与双向定位闭环

> **派工来源**：`docs/派工_SIGNAL-ENDTOEND-03_完整币池链路收口_2026-10-24.md`  
> **执行角色**：Antigravity（反重力）  
> **验收机制**：整体交付待 **Codex 独立验收**  
> **核心边界**：526 完整获批币池（不缩小为四币）；不干预 `WORK-DYNAMIC-BT-01`；无自然信号时保留等待不造信号；隔离工程、历史回放与自然行情分开汇报。

---

## 一、用户四个重点完成状态与核心证据

| 用户重点项 | 状态 | 验证探针 / 脚本 | 核心交付产物 | 验收核心证据与指标 |
| :--- | :---: | :--- | :--- | :--- |
| **重点 1：链路跑通**<br>完整获批币池扫描、水位推进、逐币漏斗、模拟订单全链路 | **PASS** | `probes/01_live_pool_scan_and_funnel.py`<br>`probes/02_isolated_engine_pipeline.py` | `out/live_pool_scan.json`<br>`out/isolated_pipeline.json` | 1. 真实运行中 PID 72929 覆盖完整 526 标的，调度周期从 cycle 1419 连续跨越推进至 1422；<br>2. 逐币漏斗统计：BOX: 379, IDLE: 114, RETEST: 19, SWEEP: 14；新自然候选为 0，如实记录等待自然行情，严禁造信号；<br>3. 隔离流水线 P01-P14 全部 PASS：引擎预热、信号落库、查验、幂等、路由挂单、挂单查验、挂单幂等、撮合成交、开仓、4h跟踪抬高（0.05037->0.05130）、价格击穿退出、平仓归档、门禁关闭防御。 |
| **重点 2：K线持久留存可重开**<br>回测复盘全要素持久化与新窗口深链重开秒级恢复 | **PASS** | `probes/03_browser_ui_verification.mjs` (B16) | `out/ui_acceptance.json`<br>`screenshots/10_persistence_reopen_success.png` | 新标签页独立会话直接访问深链 `http://127.0.0.1:3000/?nav=backtest&run=bt_20261024T120001Z_e1a10001`，无需重新计算，秒级恢复完整 997 根 K 线、指标、开平仓、委托订单、逐次成交与资金净值曲线。 |
| **重点 3：1h主图多图真正同步、订单双向定位**<br>解决上一轮 Hover 错位、补缩放拖动与图上标记反查 | **PASS** | `probes/03_browser_ui_verification.mjs` (B01-B15)<br>`LabChartPanel.tsx`<br>`BacktestWorkbench.tsx` | `out/ui_acceptance.json`<br>`screenshots/01~09.png` | 1. **根治错位**：针对上轮 15:15 对应 hover 05:00、05:15 对应 hover 19:00 缺陷，引入 `programmaticFocusLockRef` 程序化平移锁并按 1h 秒归桶；<br>2. **开仓订单点击**：精确对齐 15:00 归桶 + `🎯 定位事件 2026-10-02 15:15 UTC` 徽章 + `data-selected="true"` 高亮；<br>3. **平仓退出点击**：精确对齐 05:00 归桶 + `🎯 定位事件 2026-10-03 05:15 UTC` 徽章 + `data-selected="true"` 高亮；<br>4. **成交行点击**：精确定位 15:00 归桶 + 15:15 成交时刻；<br>5. **多图时间同步**：K线主图、成交量、OI、资金净值图时间完全对齐；<br>6. **缩放拖动联动**：主图平移（fromMs 1790913600000 -> 1790902800000），净值图视窗同步精确平移；<br>7. **图上标记反查**：点击图表整点 bar，自动展开委托订单面板并高亮对应订单行。 |
| **重点 4：逐笔订单和成交实际导出逐字段按 ID 关联对账**<br>真实下载 4 类文件，保留缺失语义，按 ID 关联核验 | **PASS** | `probes/03_browser_ui_verification.mjs` (C01-C04)<br>`probes/04_reconcile_downloads_with_ledger.py` | `out/downloads/*`<br>`out/reconciliation_downloads.json` | 1. 真实通过浏览器触发下载并保存 4 类文件：`orders_*.csv` (782B), `fills_*.csv` (601B), `trades_*.csv` (673B), `backtest_*_all.json` (9412B)；<br>2. **保留缺失语义**：空值/None 保持 None，绝不强制转 0.0；<br>3. **按 ID 关联**：按 order_id / fill_id / trade_id 关联对账，状态、价格、数量、费用、退出原因及时间戳逐位一致；<br>4. **恒等式闭环**：<br>  - 逐次成交手续费之和 == 交易手续费 (0.50972922 == 0.50972922)；<br>  - 资金费事件之和 == 交易资金费 (0.501640634 == 0.501640634)；<br>  - 损益恒等式严格成立：20.74436 - 0.50972922 - 0.501640634 == 19.7329901455；<br>  - 账户净值变动 == 净损益 (5019.73299 - 5000.0 == 19.7329901455)。 |

---

## 二、上一轮缺陷根因剖析与工程修复

### 1. 悬停读数错位根因（05:00 / 19:00 错位覆盖）
- **现象**：上一轮点击开仓订单（15:15），图表虽然平移了，但 hover 读数显示 `05:00`；点击退出订单（05:15），hover 读数显示 `19:00`。
- **根因分析**：
  1. Lightweight Charts 在执行 `setViewport` 时，由于鼠标指针悬停在 canvas 上的绝对屏幕像素坐标未变，图表底层时间轴平移导致鼠标指针下方的相对 bar 发生了改变；
  2. 官方库的 `crosshairMove` 回调随即被被动平移触发，以鼠标指针下的旧绝对像素位置向 React 状态派发了新的 hover 时间；
  3. `syncSiblings` 与副图十字线总线未设置保护锁，平移被动事件直接冲刷并覆盖了刚刚程序化聚焦设置的目标订单时刻。
- **修复方案**：
  1. 在 [`UI界面/web/src/components/LabChartPanel.tsx`](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/LabChartPanel.tsx) 中引入 `programmaticFocusLockRef`（800ms 防覆盖锁）：在程序化执行聚焦定位时忽略由于画布移动引起的被动鼠标平移；
  2. 将定位聚焦参数严格对齐至 1h 归桶整点（`barSec = toChartSeconds(bar.timestamp)`），确保 Lightweight Charts 准确定位；
  3. 在 `Hoverbar` 增加 `🎯 定位事件 YYYY-MM-DD HH:mm UTC (1h归桶 YYYY-MM-DD HH:00)` 专属徽章，精准展示精确时间与归桶整点；
  4. 为订单行和成交行添加 `data-selected="true"` 和高亮样式；
  5. 在 `subscribeClick` 点击 bar 时通过 `orderByTime` 映射反查订单并触发 `onSelectOrder(orderId)`，实现双向标记反查。

---

## 三、三类场景独立汇报

按照派工单要求，隔离工程验证、历史回放和自然行情成交三者绝不混淆，独立汇报如下：

1. **真实自然行情扫描（无自然信号）**：
   - 守护进程：`unified_runtime.py --arm 3a --symbol all`（PID 72929）；
   - 币池范围：526 获批完整加密币池（排除指数标的，覆盖 526/526）；
   - 水位推进：采样期间 cycle 从 1419 跨越推进至 1422；
   - 漏斗分布：BOX: 379, IDLE: 114, RETEST: 19, SWEEP: 14；
   - 候选与成交状态：自启动以来新自然候选 0，保留未验证并等待自然行情，绝不修改策略参数或造信号。
2. **隔离工程链路流水线验证（P01-P14）**：
   - 运行方式：独立隔离沙盒（内存/临时 SQLite 库，严格与生产解耦）；
   - 验证结论：预热、信号落库、信号查验、信号幂等、路由挂单、挂单查验、挂单幂等、撮合成交、开仓、4h跟踪抬高、止损单调性、价格击穿退出、平仓归档、门禁防御 14 项全通过。
3. **历史回放与复盘账本对账（Target Run）**：
   - 回测标的：`bt_20261024T120001Z_e1a10001` (VELVETUSDT)；
   - 账本依据：真实底层账本 `orders.jsonl`, `fills.jsonl`, `trades.jsonl`, `funding.jsonl`, `run.json`；
   - 对账结论：4 类导出文件逐字段按 ID 关联对账全部吻合，损益恒等式严格闭环。

---

## 四、产物索引与目录清单

```
evidence/SIGNAL-ENDTOEND-03/
├── README.md                                # 本交付说明书
├── manifest.json                            # 结构化产物元数据清单
├── probes/                                  # 自动化验证探针脚本
│   ├── 01_live_pool_scan_and_funnel.py      # 探针 01：526 完整币池在线扫描与逐币漏斗
│   ├── 02_isolated_engine_pipeline.py       # 探针 02：隔离工程链路流水线与门禁防御
│   ├── 03_browser_ui_verification.mjs       # 探针 03：真实浏览器深度自动化验收 (21项)
│   └── 04_reconcile_downloads_with_ledger.py# 探针 04：导出文件按 ID 关联逐字段对账
├── out/                                     # 结构化数据报告
│   ├── live_pool_scan.json                  # 在线币池扫描与漏斗结果
│   ├── isolated_pipeline.json               # 隔离工程流水线 14 项报告
│   ├── ui_acceptance.json                   # 浏览器交互 21 项验收报告
│   ├── reconciliation_downloads.json        # 下载逐字段对账报告
│   └── downloads/                           # 浏览器实测下载落盘的 4 类文件
│       ├── orders_bt_20261024T120001Z_e1a10001.csv
│       ├── fills_bt_20261024T120001Z_e1a10001.csv
│       ├── trades_bt_20261024T120001Z_e1a10001.csv
│       └── backtest_bt_20261024T120001Z_e1a10001_all.json
└── screenshots/                             # 真实全幅浏览器操作截图 (10 张)
    ├── 01_overview_default_1h.png           # 默认 1h 主小时线全景
    ├── 02_order_click_focus_entry_1515.png  # 点击开仓订单精确对齐 15:00 归桶与 15:15 徽章
    ├── 03_order_click_focus_exit_0515.png   # 点击退出订单精确对齐 05:00 归桶与 05:15 徽章
    ├── 04_fill_click_focus_1515.png         # 点击成交行精确聚焦
    ├── 05_pan_zoom_after_drag.png           # 视窗平移拖拽后主图与净值图同步联动
    ├── 06_marker_inverse_lookup_order.png   # 图上条目点击反查自动展开并高亮订单
    ├── 07_timeframe_4h.png                  # 4h 趋势背景周期切换
    ├── 08_chart_expanded_large.png          # 大图展开全屏视图
    ├── 09_switched_to_susdt.png             # 切换至第二候选 (SUSDT) 不串旧数据
    └── 10_persistence_reopen_success.png    # 新窗口深链重开恢复
```

---

## 五、Codex 独立验收指引

Codex 独立验收员可直接运行以下命令进行快速、全闭环独立复验：

```bash
# 1. 验证 526 完整币池在线扫描与逐币漏斗
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/SIGNAL-ENDTOEND-03/probes/01_live_pool_scan_and_funnel.py

# 2. 验证隔离工程流水线全生命周期 (P01-P14)
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/SIGNAL-ENDTOEND-03/probes/02_isolated_engine_pipeline.py

# 3. 验证真实浏览器深度交互与双向定位 (21 项)
NODE_PATH=UI界面/web/node_modules /Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node evidence/SIGNAL-ENDTOEND-03/probes/03_browser_ui_verification.mjs

# 4. 验证真实下载导出文件逐字段按 ID 关联对账 (保留缺失语义)
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/SIGNAL-ENDTOEND-03/probes/04_reconcile_downloads_with_ledger.py
```
