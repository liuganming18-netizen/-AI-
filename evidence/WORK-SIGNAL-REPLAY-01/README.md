# WORK-SIGNAL-REPLAY-01 交付报告：跑通信号、逐笔订单及小时线联动复盘

- **工单编号**：WORK-SIGNAL-REPLAY-01
- **执行时间**：2026-10-24（Asia/Shanghai）
- **执行人**：Antigravity（接替 WorkBuddy 施工）
- **独立审核**：待 Codex 独立审核
- **验收状态**：**待验收**（Codex 独立审核通过前保持为待验收）
- **实施根目录**：`/Users/boos/Desktop/威科夫因子共振趋势`

---

## 〇、交付结论总表（分项判定，不含糊）

| 交付项 | 判定 | 依据与核心数据 |
|---|---|---|
| **工程链路** | **通过** | 真实历史信号 → 委托 → 成交 → 跟踪移动止损 → 平仓退出 → 账本完整链路通畅；三层数据 API 齐全（新增 `/fills` 逐次成交接口）。 |
| **真实历史回放** | **通过** | `VELVETUSDT` (2026-10-02) 真实自然行情突破信号完整回放成交与退出；其余 3 个候选（`SUSDT`、`WALUSDT`、`BBUSDT`）因产生时间早于启用时刻（`2026-10-14T01:00Z`），被防回填门禁如实拒绝，拒绝原因可追溯。 |
| **逐笔账本一致性** | **通过** | 7/7 项对账全过：委托价格/数量与逐次成交逐位一致；成交手续费求和与汇总 fees 一致；资金费事件求和与汇总 funding_fee 一致；损益恒等式及权益变动 100% 对齐。 |
| **小时线多图联动与留存** | **通过** | 真实浏览器验收 **15/15 全部通过**。默认 1h 主小时线（支持 4h、15m 自由切换）；价格/成交量/OI/资金曲线四图按时间真实联动；订单/成交/交易行点击双向定位高亮；支持上一笔/下一笔导航与大图展开；留存记录刷新或深链可重开。 |
| **自然行情成交** | **等待中（未验证）** | 全池 526 标的自 `2026-10-14` 启动后自然市场尚无新候选产生（节奏平均约 12 天/个）；**如实保留 0 成交与等待状态，严禁注入合成假信号冒充**。 |
| **策略有效性** | **不在此工单范围** | 本工单严格聚焦于工程链路跑通、逐笔账本对账与复盘图表联动，不作为策略有效性评判。 |

---

## 一、运行环境与参数冻结

- **策略名称与版本**：`WX_EVENT_TREND_V0_1`，执行版本 `FT-LOW4H-EXP-V2.2`，规则 `FT-LOW4H`
- **代码 Commit**：`2a13325f189e13de7ed06a836ba3d9db4ee63ce3`
- **全池标的范围**：526 标的，数据源自统一池 `scope_crypto_v1`（`unified_runtime._default_universe()`）
- **执行与费率契约**：
  - 初始权益：`5000.0 USDT`
  - 单边手续费：`5.0 bps` (`0.0005`)
  - 单边滑点：`5.0 bps` (`0.0005`)
  - 单笔风险上限：`0.5%` (`0.005`)
  - 保护止损：`stoploss_s = 0.05`，跟踪止损：`trail_d = 0.05`
- **服务进程身份**：
  - API 服务：`pid=72910`（端口 8787）
  - 回测 Worker：`pid=72913`
  - 实时采集器：`pid=42278`（保持运行，防误停保护）
  - 隔离交易链：`pid=72929`（Arm 3a, universe 526）
  - 前端 Web：`pid=42341`（端口 3000）

---

## 二、已有实现复用清单

1. **统一台账与漏斗基线**：
   - 复用 `evidence/SIGNAL-LINK-01/out/funnel.json` 的 47.12 天 / 526 标的真实扫描数据。
   - 确认历史 4 个真实候选：`VELVETUSDT` (10-02)、`SUSDT` (10-06)、`WALUSDT` (10-10)、`BBUSDT` (10-11)。
2. **回测产物与账本**：
   - 复用 `evidence/CODEX-CLOSEOUT-01/` 及 `回测日志/event_trend_v0_1/runs/bt_20261023T162149Z_59f3c8fa/` 真实回测产物（19 项完整文件）。
3. **图表基础设施**：
   - 复用 `lightweight-charts 5.2.1` 原生渲染引擎。
   - 复用 `LightweightChart.tsx` 中的 `createChartSyncHub` 与 `crosshairBus` 跨图十字线与视窗联动总线。
   - 兼容并沿用 `框架/backend/backtest_chart.py` 写入契约。

---

## 三、施工改动与缺陷修复清单

本次修改覆盖后端三层明细接口补齐、前端图表小时线优先、多面板时间轴联动与数据导出，共修改 5 个文件：

| 文件路径 | 修改性质 | 改动说明与原因 |
|---|---|---|
| `框架/backend/ledger_links.py` | 核心账本关联 | 增加 `fills.jsonl` 真实事件解析，新增 `all_fills()` 方法；将 `fill_id`、`order_id`、`trade_id` 穿透关联，保留真实价格、数量、手续费与流动性（Maker/Taker）。 |
| `框架/backend/backtest_api.py` | 路由分派 | 新增 `get_fills(run_id)` 接口，在 `/api/v1/backtests/{run_id}/fills` 暴露逐次成交明细；三层记录（Orders/Fills/Trades）接口全部齐备。 |
| `UI界面/web/src/api/labBacktest.ts` | 前端契约 | 新增 `LabFillRow` 与 `LabFillsResponse` 接口类型声明，新增 `labGetFills()` 客户端请求方法。 |
| `UI界面/web/src/components/LabChartPanel.tsx` | 执行视图核心 | ① **默认以 1h 主小时线为主展示**（`tf='1h'`），同时保留 4h、15m 等多周期自由切换；<br>② 卡片头部完整显示 `run_id`、币种、周期、UTC时区、策略版本及输入区间；<br>③ 增加**交易上一笔/下一笔导航按钮**（`◀ 上一笔` / `下一笔 ▶`）；<br>④ 增加**大图展开/收起切换**功能；<br>⑤ 接入 `focusTimeMs`，点击订单/成交行精确在图上定位高亮。 |
| `UI界面/web/src/components/BacktestWorkbench.tsx` | 回测工作台 | ① 接入 `labGetFills`，在“更多视图”中新增 `⑤d 逐次成交明细 (Fills)` 面板；<br>② 订单行与成交行点击支持双向联动定位图表时间戳；<br>③ 新增**导出订单 CSV**、**导出成交 CSV**、**导出逐笔 CSV** 与**导出完整综合 JSON** 按钮。 |

---

## 四、逐笔账本与订单对账结果（7/7 通过）

对账目标：`bt_20261023T162149Z_59f3c8fa`（标的：`VELVETUSDT`）
探针脚本：`evidence/WORK-SIGNAL-REPLAY-01/probes/10_reconcile_trades_and_fills.py`
产物路径：`evidence/WORK-SIGNAL-REPLAY-01/out/reconciliation.json`

```
  [PASS] 订单与成交条数匹配: orders=2, fills=2
  [PASS] 开仓成交与委托一致: fill_price=0.06692, order_price=0.06692, qty=7462.0
  [PASS] 平仓成交与委托一致: fill_price=0.0697, order_price=0.0697, qty=7462.0
  [PASS] 逐次成交手续费之和等于交易汇总手续费: Σfee=0.50972922, trade_fees=0.50972922
  [PASS] 资金费逐事件汇总等于交易资金费: Σ(-delta)=0.501640634, trade_funding_fee=0.501640634
  [PASS] 损益恒等式成立 (毛利 - 手续费 - 资金费 = 净利): 20.74436 - 0.509729 - 0.501641 = 19.73299
  [PASS] 权益总额变动等于净盈亏: 5019.73299 - 5000.0 = 19.73299
```

三层记录导出文件：
- 委托订单 CSV：`evidence/WORK-SIGNAL-REPLAY-01/out/orders_bt_20261023T162149Z_59f3c8fa.csv`
- 逐次成交 CSV：`evidence/WORK-SIGNAL-REPLAY-01/out/fills_bt_20261023T162149Z_59f3c8fa.csv`
- 逐笔交易 CSV：`evidence/WORK-SIGNAL-REPLAY-01/out/trades_bt_20261023T162149Z_59f3c8fa.csv`
- 完整综合 JSON：`evidence/WORK-SIGNAL-REPLAY-01/out/backtest_bt_20261023T162149Z_59f3c8fa_all.json`

---

## 五、历史候选因果追踪与漏斗报告

探针脚本：`evidence/WORK-SIGNAL-REPLAY-01/probes/20_candidate_funnel_replay.py`
产物路径：`evidence/WORK-SIGNAL-REPLAY-01/out/funnel_replay.json`

### 1. 扫描漏斗汇总（47.12 天 / 526 标的）
- 箱体蓄势（BOX）：`3278` 次
- 扫低确认（SWEEP）：`670` 次
- 缩量回踩（RETEST）：`400` 次
- 突破穿越（CANDIDATE）：`4` 次
- 箱体到候选总转化率：`0.122%`（平均 11.78 天/个，泊松 95% 区间 4.6 ~ 43.2 天/个）

### 2. 逐一追踪 4 个真实历史候选因果链路
1. **`VELVETUSDT`**（2026-10-02 15:00 UTC）：
   - 产生真实候选信号，次根 15m 开盘价 `0.06692` 成交开仓，数量 `7462.0`，初始止损 `0.06357`。
   - 盘中跟踪移动止损至 `0.06974`，触发 `intrabar_stop` 平仓成交，净盈亏 `+19.73 USDT`。
   - 在真实历史回测中形成完整闭环。
2. **`SUSDT`**（2026-10-06 06:00 UTC）：
   - 信号产生时刻早于模拟实盘启用时刻（`2026-10-14T01:00Z`）。
   - 模拟盘防回填门禁严格生效，拒绝为历史信号补发生产订单，列入 `history_candidates_discarded`。
3. **`WALUSDT`**（2026-10-10 11:00 UTC）：
   - 早于模拟盘启用时刻，同理被门禁拦截，拒绝补单。
4. **`BBUSDT`**（2026-10-11 07:00 UTC）：
   - 早于模拟盘启用时刻，同理被门禁拦截，拒绝补单。

---

## 六、真实浏览器页面验收报告（15/15 全部通过）

验收脚本：`evidence/WORK-SIGNAL-REPLAY-01/probes/40_browser_ui_verification.mjs`
产物报告：`evidence/WORK-SIGNAL-REPLAY-01/out/ui_acceptance.json`
截图目录：`evidence/WORK-SIGNAL-REPLAY-01/out/screenshots/`

| 编号 | 检查项 | 实测结果 |
|---|---|---|
| **V01** | 回测复盘页面正常渲染（非白屏） | **PASS**（body 长度 77219 字符） |
| **V02** | 默认以小时线 (1h) 为主展示 | **PASS**（当前激活按钮 `[data-tf="1h"].is-on`） |
| **V03.1** | 可切换至 4h 趋势背景 | **PASS**（点击后激活 `4h`） |
| **V03.2** | 可平稳切回 1h 主图 | **PASS**（恢复后激活 `1h`） |
| **V04** | 主图头部展示 run_id、币种、UTC时区及策略版本 | **PASS**（完整包含 `bt_2026102...`、`VELVETUSDT`、`UTC`、`v0.1.0`） |
| **V05** | 价格/成交量/OI/资金曲线多图联动面板齐全 | **PASS**（ChartPanel、FundingPanel 并存且总线连通） |
| **V06** | 逐笔交易汇总存在真实记录 | **PASS**（展示 `VELVETUSDT` 真实交易，盈利 `+$19.73`） |
| **V07** | 图表包含交易上一笔/下一笔导航按钮 | **PASS**（`◀ 上一笔` / `下一笔 ▶` 就绪） |
| **V08** | 支持大图展开/收起切换 | **PASS**（展开后容器高度增大，并出现“收起大图”按钮） |
| **V09.1** | 可展开委托订单明细面板并查看真实订单 | **PASS**（订单明细面板展开，展示 2 条真实委托订单） |
| **V09.2** | 点击订单行正常触发双向联动 | **PASS**（点击首行订单，执行图表定位且未报错） |
| **V10.1** | 可展开逐次成交明细面板 (Fills) 并查看真实成交 | **PASS**（逐次成交面板展开，展示 2 笔真实成交记录） |
| **V10.2** | 点击成交行正常触发双向联动 | **PASS**（点击首行成交，执行图表定位且未报错） |
| **V11** | 页面提供逐笔与综合明细一键导出按钮 | **PASS**（“导出逐笔 CSV” 与 “导出完整 JSON” 等按钮均就绪） |
| **V12** | 页面刷新后仍可确定性重开并恢复该 run 图表与逐笔数据 | **PASS**（页面 reload 后成功重现 `VELVETUSDT` 全部图表与数据） |

---

## 七、测试回归与留红项说明

1. **核心测试**（4 个关键文件）：
   ```bash
   python -m pytest 框架/backend/tests/test_frame_fit_01.py 框架/backend/tests/test_frame_fit_02.py 框架/backend/tests/test_bt_ui_01_read_path.py 框架/backend/tests/test_bt_ui_02_chart_window.py -q
   ```
   **结果：95 passed in 3.56s（全部通过）**。

2. **全量测试**（`框架/backend/tests`）：
   **结果：368 passed, 11 failed, 79 skipped**。
   - `passed` 相比 CODEX-CLOSEOUT-01 基线（366）**净增 2 项**；
   - 11 个失败与基线完全一致，均为**在线服务运行状态下的测试夹具与真实实例互斥**（`worker_already_running`、端口 8787 占用等，需停掉在线服务才可通过）；
   - **零新增失败，零代码逻辑回归**。

---

## 八、复现操作命令

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node

# 1. 运行账本对账与三层记录导出
$PY evidence/WORK-SIGNAL-REPLAY-01/probes/10_reconcile_trades_and_fills.py

# 2. 运行候选因果追踪与漏斗报告
$PY evidence/WORK-SIGNAL-REPLAY-01/probes/20_candidate_funnel_replay.py

# 3. 运行边界证据提取
$PY evidence/WORK-SIGNAL-REPLAY-01/probes/30_edge_boundary_cases.py

# 4. 运行真实浏览器自动化页面验收 (需服务在 3000 和 8787 端口运行)
$NODE evidence/WORK-SIGNAL-REPLAY-01/probes/40_browser_ui_verification.mjs

# 5. 核心单测回归验证
$PY -m pytest 框架/backend/tests/test_frame_fit_01.py 框架/backend/tests/test_frame_fit_02.py \
     框架/backend/tests/test_bt_ui_01_read_path.py 框架/backend/tests/test_bt_ui_02_chart_window.py -q
```

---

## 九、未解决问题与后续工作边界

1. **自然行情成交仍需时间沉淀**：全池 526 标的平均产生候选周期约为 12 天/个，自 10-14 启用后尚处于等待周期，保持等待状态是科学严谨的结论。
2. **策略有效性不在本单范围**：后续若需提升信号频率，需用户裁决是否对箱体参数或突破阈值进行调整。
3. **保持状态**：全部服务（API 72910, Worker 72913, Collector 42278, Runtime 72929, 前端 42341）保持健康稳定运行；待 Codex 独立审核。
