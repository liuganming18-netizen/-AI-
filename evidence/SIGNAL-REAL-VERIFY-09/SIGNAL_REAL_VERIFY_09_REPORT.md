# SIGNAL-REAL-VERIFY-09 真实候选独立端到端复验报告

- **任务编号**：SIGNAL-REAL-VERIFY-09
- **项目路径**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **核验性质**：只读端到端独立复验（不修改业务源码、不伪造接口候选、不向数据库插入记录、不触发生产下单）
- **配合工单**：基于 Work 已交付的 `SIGNAL-UI-LINK-07-R1` 隔离工具与被测版本
- **复验时刻**：2026-10-25
- **证据目录**：`evidence/SIGNAL-REAL-VERIFY-09/`

---

## 一、核心复验结论总览

| 关注项 | 结论 | 关键事实依据 |
| :--- | :---: | :--- |
| **1. 真实候选四处同一 ID 对齐 (引擎/DB/API/UI)** | **PASS** | 严格基于真实历史行情（`market_v2.sqlite3`，只读），通过正式策略引擎分别在 **新产生 live** 与 **重启恢复 recovered** 两条路径下产出同一真实候选：<br>`cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000`<br>币种 `VELVETUSDT`、方向 `long`、确认时刻 `2026-10-02T15:00:00Z`、可执行状态 `enterable`，在**引擎输出、隔离数据库、REST API、页面卡片**四处完全一致。 |
| **2. 真实刷新、轮询、关闭重开持久性** | **PASS** | 在直连隔离后端服务（Port 8795/8799）的无 Mock 真实浏览器环境中：<br>① 初次打开：1 张卡片，同一候选 ID；<br>② 页面硬刷新（F5 / Reload）：1 张卡片，字段保持；<br>③ 两轮轮询（连续等待 12s）：稳定保持 1 张，接口新增轮询请求，卡片不闪烁、不重复；<br>④ **完全关闭会话重开**：彻底销毁 Context 1，新建全新 Context 2 重新进入，卡片直接由真实后端服务与数据库恢复渲染，**完全不靠前端重新注入**。 |
| **3. 故障注入与异常隔离保护** | **PASS** | 故障注入仅在隔离测试中验证异常与恢复：<br>① 业务异常 200（`missing_reason` 触发 `fault:no_watermark`）：卡片立即清零，显示告警框，旧卡片不冒充；<br>② HTTP 503 异常：卡片清零，显示真实 503 报错；<br>③ 正常空态恢复：消除错误，平滑恢复逐币空态诊断。 |
| **4. 生产历史明细对账（51 条）** | **PASS** | 生产守护接口返回的 51 条历史预热丢弃候选，与页面展开明细逐 ID 完全吻合；不可执行原因明确标示（如「早于实盘模拟起点（不回填旧信号）」）；明确其与实时信号表分属不同来源，不冒充当前入场，生产实时库严格保持 0 行。 |
| **5. 针对前单结论的纠正声明** | **CONFIRMED** | 正式更正上一单（08-R1）结论：上一单实测 2 中的模拟候选案例（`sig_live_test_sol_1025` 等）现明确归档并标注为**「仅前端页面渲染与契约分类测试」**，不得计入真实策略候选端到端贯通证据。本单以全真实历史行情跑出的 `VELVETUSDT` 候选作为唯一端到端有效性判定依据。 |

---

## 二、被测代码版本与指纹清单

复用 Work 交付的身份检查工具，记录被测环境的确定性指纹：

### 1. 本次复验受测业务文件
| 相对路径 | 大小 | 行数 | SHA-256 (前 16 位) | 说明 |
| :--- | :---: | :---: | :---: | :--- |
| `UI界面/web/src/api/signalsState.ts` | 7,008 B | 162 | `f2d408931c502053` | 状态分类器（新增） |
| `UI界面/web/src/api/useMarketData.ts` | 56,031 B | 1,250 | `b1289ce6a2aa8d38` | 数据层信号拉取与故障分流 |
| `UI界面/web/src/components/SignalListPanel.tsx` | 46,388 B | 1,010 | `bf8eb3ce8df12718` | 信号面板与空态诊断展开 |
| `UI界面/web/src/App.tsx` | 21,206 B | 487 | `07aefc6b8f82a8a7` | 工作台装配与诊断状态透传 |
| `框架/backend/adapter.py` | 215,744 B | 3,967 | `6218416fae5f6dc3` | 信号路由与故障状态码透传 |

### 2. 生产关键受保护资产（前后对比未被改动）
| 受保护文件 | SHA-256 (前 16 位) | 验证状态 |
| :--- | :---: | :---: |
| `evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md` | `1f1167de35412cb7` | 未变（冻结） |
| `框架/backend/live/paper_new_entry_gate.json` | `f4691db2c7f66c81` | 未变 |
| `框架/backend/live/live_signals.sqlite3` | `91e0979b9458338f` | 未变（保持 0 行） |
| `数据/paper/paper_3a.sqlite3` | `19825112fecbdba7` | 未变 |
| `数据/paper/paper_3b.sqlite3` | `49d08d3923f7f3f7` | 未变 |
| `数据/paper/paper_3c.sqlite3` | `6c9fffd77e2da5b8` | 未变 |
| `数据/paper/paper_3d.sqlite3` | `b169aa30f19bfbf0` | 未变 |

---

## 三、真实候选双路径四方对账实测（Live vs Recovered）

依托正式策略引擎在只读历史行情（2026-10-02）上的真实运算，产出同一真实候选：
`cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000`

### 1. 双路径核心字段核对表
| 核验维度 | 新产生路径（Live） | 重启恢复路径（Recovered） |
| :--- | :--- | :--- |
| **触发机制** | 进程在线，由 `step_once(now=15:00)` 现场处理 1h bar | 进程停机后重启，由 bootstrap 尾部补齐 + 恢复路由落库 |
| **候选 ID** | `cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000` | `cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000` |
| **标的代币** | `VELVETUSDT` | `VELVETUSDT` |
| **交易方向** | `long`（做多） | `long`（做多） |
| **确认时间** | `2026-10-02T15:00:00Z` (1790953200000) | `2026-10-02T15:00:00Z` (1790953200000) |
| **可执行状态** | `enterable` | `enterable` |
| **数据库 origin** | `live` | `recovered` |
| **REST 接口输出** | 1 条，同一 ID，`status=ok` | 1 条，同一 ID，`status=ok` |
| **页面卡片渲染** | 1 张卡片，同一 ID，做多，确认时间一致 | 1 张卡片，同一 ID，做多，确认时间一致 |
| **页面刷新 (Reload)** | 保持 1 张，无数据丢失 | 保持 1 张，无数据丢失 |
| **两轮轮询 (12s)** | 保持 1 张，无重复追加 | 保持 1 张，无重复追加 |

### 2. 真实四处一致性对账结论
- **引擎计算层**：状态机推进至候选（CANDIDATE），入场门禁判定放行；
- **数据库持久层**：隔离数据库 `live_signals.sqlite3` 严格落库 1 行；
- **接口服务层**：`GET /api/v1/signals?scope=current` 精准输出 1 条；
- **前端页面层**：DOM 节点 `[data-testid="signal-row"]` 属性 `data-signal-uid` 严丝合缝。
- **对账判定**：**PASS**。

---

## 四、刷新、两轮轮询与关闭重开独立实测

- **测试脚本**：[`verify_reopen_and_persistence.mjs`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/verify_reopen_and_persistence.mjs)
- **环境条件**：隔离后端启动在 Port 8795，前端直连真实隔离后端，不使用任何 Mock 数据注入。
- **实测步骤与结果**：
  1. **步骤 1（初次加载）**：Context 1 进入工作台，成功渲染 1 张卡片，ID 吻合；
  2. **步骤 2（硬刷新 Reload）**：执行 `page.reload()`，页面重新拉取后，卡片持续显示，数量恒为 1；
  3. **步骤 3（两轮轮询）**：持续监控 12 秒（跨越两轮 5s 轮询），卡片稳定无闪烁，无重复；
  4. **步骤 4（彻底关闭会话重开）**：
     - 完全执行 `await ctx1.close()` 关闭第一个浏览器上下文；
     - 新建全新独立的 `ctx2`，重新打开工作台页面；
     - **页面卡片直接通过真实隔离 API 和数据库恢复渲染**，数量为 1，ID 依然为 `cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000`；
     - 证实系统不依赖浏览器内存中的旧注入状态，具备真实的跨会话恢复能力。
- **数据产物**：[`06_reopen_and_persistence_summary.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/06_reopen_and_persistence_summary.json)
- **对应截图**：
  - 初次加载：[`06_step1_initial_card.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/06_step1_initial_card.png)
  - 刷新后：[`07_step2_after_reload.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/07_step2_after_reload.png)
  - 两轮轮询后：[`08_step3_after_two_polls.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/08_step3_after_two_polls.png)
  - 关闭重开后：[`09_step4_after_reopen.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/09_step4_after_reopen.png)

---

## 五、生产历史明细独立核对（51 条）

- **数据来源**：生产实时接口 `GET /paper/execution_daemon/status?arm=3a` 下发字段 `item.signal_engine.bootstrap.discarded_detail`。
- **与实时信号表的严格隔离**：
  - 生产实时信号库 `live_signals.sqlite3` 严格为 **0 行**；
  - 当前可执行信号接口 `GET /signals?scope=current` 严格返回 **0 条**；
  - 历史丢弃候选是引擎预热回放历史期间（2026-07 至 2026-10-11）走过的候选，因早于生产启用时点（2026-10-14T01:00Z）或错过成交时效，按交易纪律严格丢弃；
  - 页面将其展示在空态诊断下方的折叠明细中，明确标示「（51 条，均不可执行）」，杜绝历史候选被误认为当前入场机会。
- **对账结果**：
  - API 明细 51 条 == 页面展开渲染节点 51 条；
  - 首条 ID `cand_ev_WX_EVENT_TREND_V0_1_binance_1000PEPEUSDT_1784836799999_1785049200000` 逐字相同；
  - 丢弃原因清晰转换为中文：`早于实盘模拟起点（不回填旧信号）；错过撮合时刻（不回填旧价）`。
- **数据产物**：[`02_history_reconcile.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/02_history_reconcile.json)

---

## 六、证据与产物文件索引

| 证据文件 | 说明 |
| :--- | :--- |
| [`01_codebase_version_manifest.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/01_codebase_version_manifest.json) | 被测代码版本与关键资产 SHA-256 指纹清单 |
| [`02_history_reconcile.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/02_history_reconcile.json) | 生产 51 条历史丢弃候选明细对账数据 |
| [`03_page_live.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/03_page_live.json) 与 [`03_page_live.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/03_page_live.png) | 真实 Live 候选页面级端到端实测数据与截图 |
| [`04_page_recovered.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/04_page_recovered.json) 与 [`04_page_recovered.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/04_page_recovered.png) | 真实 Recovered 候选页面级端到端实测数据与截图 |
| [`05_fault_recovery.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/05_fault_recovery.json) | 五态故障注入与恢复过程对账数据 |
| [`06_reopen_and_persistence_summary.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/06_reopen_and_persistence_summary.json) | 独立关闭重开与两轮轮询持久性复验结果 |
| [`06_step1_initial_card.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/06_step1_initial_card.png) | 独立关闭重开测试：初始真实候选卡片 |
| [`07_step2_after_reload.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/07_step2_after_reload.png) | 独立关闭重开测试：硬刷新后卡片保持截图 |
| [`08_step3_after_two_polls.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/08_step3_after_two_polls.png) | 独立关闭重开测试：两轮轮询后截图 |
| [`09_step4_after_reopen.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-REAL-VERIFY-09/09_step4_after_reopen.png) | 独立关闭重开测试：完全关闭 Context 1 并新建 Context 2 打开截图 |
