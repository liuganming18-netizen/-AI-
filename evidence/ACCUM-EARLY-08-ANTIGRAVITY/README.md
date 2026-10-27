# [ACCUM-EARLY-08-ANTIGRAVITY] V3.1 反重力隔离研究回测交付报告

- **工单编号**：`ACCUM-EARLY-08-ANTIGRAVITY`
- **执行角色**：反重力（独立回测执行方）
- **项目路径**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **代码分支**：`wyckoff-v3.1-main`（基线 Commit：`9e3d60cf`，包含 FIX-001、FIX-002 与 AUDIT-003-PATCH 补丁）
- **执行原则**：
  1. 严格独占 `evidence/ACCUM-EARLY-08-ANTIGRAVITY/` 隔离目录，输入只读，零写入生产与历史账本。
  2. 优先复现既有研究基线（40 标的、121 天窗口、两套跳空配置），不擅自扩大施工范围或篡改参数。
  3. 严格执行“数据到决策”深度清点与追踪，明确区分“回测运行完成”与“完整数据参与”。

---

## 总体完成度与状态结论

| 审查维度 | 判定状态 | 核心依据与说明 |
|---|---|---|
| **回测链路执行** | **【已完成】** | 两臂（ARM A 现行默认 vs ARM B 合法对照）真实组合回测均顺利跑通，零代码崩溃。 |
| **物理环境隔离** | **【已完成】** | 账本、实时库、观察库与产物全部落于本单独占目录，跑前跑后共享代码哈希 0 漂移 (`code_drift=[]`)。 |
| **交易时序与槽位释放** | **【已完成】** | 统一时间轴生效，平仓同刻即时释放槽位，彻底消除“仅成交前 10 个标的”的旧陷阱。 |
| **价格门禁与阻断机制** | **【已完成】** | ARM A 跳空越界被 100% 拦截并打为 `FAILED` 阻断绩效；ARM B 合法区间 0 越界放行。 |
| **完整数据参与要求** | **【未完成】** | 全域 17 项数据流中，仅 4 周期 K 线、合约规格与资金费参与决策，OI、CVD、多空比、聪明钱等 11 项未进入判断链。 |
| **综合验收结论** | **【回测运行完成／完整数据参与未完成】** | 依据用户约定，**不得据此宣布策略整体验收通过**；本报告交付主代理独立验收。 |

---

## 一、可复跑命令与运行身份锁定

### 1. 运行环境与共享代码身份
- **Python 运行时**：`/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python` (Python 3.13.12)
- **代码 Commit**：`9e3d60cf`
- **8 项核心共享文件哈希校验**：跑前跑后完全一致，代码漂移清单 `code_drift = []`
  - `框架/backend/accum_early_entry.py`
  - `框架/backend/replay_calc.py`
  - `框架/backend/paper_engine.py`
  - `框架/backend/live_store.py`
  - `框架/backend/research_15m.py`
  - `框架/backend/accum_research.py`
  - `框架/backend/observe_store.py`
  - `框架/backend/pool_scope.py`

### 2. 标的名单与时间窗口
- **标的池**：既有基线 40 标的（`explicit_symbols`，详见 `00_symbols.txt`）
  `XTZUSDT, FLUXUSDT, DOLOUSDT, ZORAUSDT, GTCUSDT, POLYXUSDT, ONGUSDT, ZETAUSDT, TREEUSDT, SKYUSDT, LISTAUSDT, VIRTUALUSDT, METUSDT, HOTUSDT, ARKUSDT, AXLUSDT, SENTUSDT, CTRUSDT, IOTAUSDT, ZENUSDT, API3USDT, MINAUSDT, CFXUSDT, IDUSDT, JOEUSDT, UMAUSDT, MAVUSDT, SEIUSDT, WAXPUSDT, LSKUSDT, PIXELUSDT, POPCATUSDT, RPLUSDT, HMSTRUSDT, DIAUSDT, ACTUSDT, AVAUSDT, VTHOUSDT, HYPERUSDT, HYPEUSDT`
- **时间窗口**：`2026-06-27T08:00:00Z` $\rightarrow$ `2026-10-26T09:00:00Z`（共 121.0 天，预热 60 天）
- **单标的箱体上限**：`max_boxes = 40`（实测触顶率 0.0%，无截断）
- **成本底座生效视图**：`82dec362c182`（FT-LOW4H-EXP-V2.2，初始权益 5000 USDT，单名上限 10%，总多头 80%，现金预留 20%，参与率 1%，双边滑点 3bps/5bps）

### 3. 可复跑命令
```bash
# 1. 运行 ARM A（现行默认跳空配置，gap_model="stop_price"）
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/ACCUM-EARLY-07-W5/scripts/run_abc_pool.py \
  --config evidence/ACCUM-EARLY-06-W2/01/config/fixture_rules_06w2.json \
  --allow-fixture \
  --symbols "XTZUSDT,FLUXUSDT,DOLOUSDT,ZORAUSDT,GTCUSDT,POLYXUSDT,ONGUSDT,ZETAUSDT,TREEUSDT,SKYUSDT,LISTAUSDT,VIRTUALUSDT,METUSDT,HOTUSDT,ARKUSDT,AXLUSDT,SENTUSDT,CTRUSDT,IOTAUSDT,ZENUSDT,API3USDT,MINAUSDT,CFXUSDT,IDUSDT,JOEUSDT,UMAUSDT,MAVUSDT,SEIUSDT,WAXPUSDT,LSKUSDT,PIXELUSDT,POPCATUSDT,RPLUSDT,HMSTRUSDT,DIAUSDT,ACTUSDT,AVAUSDT,VTHOUSDT,HYPERUSDT,HYPEUSDT" \
  --start "2026-06-27T08:00:00Z" \
  --end "2026-10-26T09:00:00Z" \
  --max-boxes 40 \
  --run-id antigravity_08_arm_a \
  --out-dir evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_a \
  --runs-dir evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs

# 2. 运行 ARM B（敏感性对照配置，gap_model="bar_open"）
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/ACCUM-EARLY-07-W5/scripts/run_abc_pool.py \
  --config evidence/AUDIT-003-PATCH/config/fixture_rules_06w2_gap_bar_open.json \
  --allow-fixture \
  --symbols "XTZUSDT,FLUXUSDT,DOLOUSDT,ZORAUSDT,GTCUSDT,POLYXUSDT,ONGUSDT,ZETAUSDT,TREEUSDT,SKYUSDT,LISTAUSDT,VIRTUALUSDT,METUSDT,HOTUSDT,ARKUSDT,AXLUSDT,SENTUSDT,CTRUSDT,IOTAUSDT,ZENUSDT,API3USDT,MINAUSDT,CFXUSDT,IDUSDT,JOEUSDT,UMAUSDT,MAVUSDT,SEIUSDT,WAXPUSDT,LSKUSDT,PIXELUSDT,POPCATUSDT,RPLUSDT,HMSTRUSDT,DIAUSDT,ACTUSDT,AVAUSDT,VTHOUSDT,HYPERUSDT,HYPEUSDT" \
  --start "2026-06-27T08:00:00Z" \
  --end "2026-10-26T09:00:00Z" \
  --max-boxes 40 \
  --run-id antigravity_08_arm_b \
  --out-dir evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_b \
  --runs-dir evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs
```

---

## 二、双臂回测实测对账与行为证据

### 1. 真实运行主对账表

| 对账核验项 | ARM A：现行默认 (`gap_model=stop_price`) | ARM B：敏感性对照 (`gap_model=bar_open`) | 对比与自洽性说明 |
|---|---|---|---|
| **唯一运行标识 (`run_id`)** | `antigravity_08_arm_a` | `antigravity_08_arm_b` | 独立账本与目录隔离 |
| **配置文件** | `fixture_rules_06w2.json` (`approved=false`) | `fixture_rules_06w2_gap_bar_open.json` (`approved=false`) | 逐字节一致，仅差 `gap_model` |
| **候选信号数 (4a / 4b / 4c)** | **105 / 87 / 97** | **105 / 87 / 97** | **完全相同**，证明信号判定零分叉 |
| **实际开仓数 (4a / 4b / 4c)** | 65 / 82 / 88 | 97 / 25 / 26 | 槽位滚动复用生效，打破旧版 10 笔限制 |
| **退出推进事件数 (`n_exits`)** | 251 | 148 | 均完整执行逐 15m 跟踪推进 |
| **代码漂移检查 (`code_drift`)** | `[]`（无漂移） | `[]`（无漂移） | 运行时代码完整性核验通过 |
| **运行可信度状态 (`run_status`)** | **`FAILED`** | **`OK`** | **ARM A 被新价格门禁精准捕获** |
| **失效原因码 (`invalid_reasons`)** | `['EXIT_CLOSE_REJECTED']` | `[]`（空） | ARM A 平仓越界；ARM B 全量合法 |
| **价格越界拦截条数 (`run_problems`)** | **20 条**（涵盖 16 笔越界持仓） | **0 条** | 强门禁拦截虚高不可成交价格 |
| **绩效指标输出 (胜率 / 净额 / 回撤)** | **全部阻断为 `None` (`—`)** | 正常产出指标（甲 6.25% 胜率 / -528U 等） | **BLK-01 阻断漏洞已彻底修复** |
| **缺失规格静默放行数** | **0 笔** | **0 笔** | 0.01 兜底 tick 100% 消除 |

### 2. 核心行为证据解读
1. **ARM A 阻断真实有效（非代码 Bug，而是门禁生效的必然事实）**：
   - 现行默认配置中 `gap_model="stop_price"` 规定在 15m 开盘跳空跌穿止损时，仍按原来的止损价结算。
   - 底层价格门禁真实校验发现：这 16 笔交易的止损价**高于当根 K 线的真实最高价 (`high`) 3.58% $\sim$ 26.79%**。该价格在该时间戳根本未曾出现过，属于虚假成交。
   - 门禁坚决拒绝落账，并将 `run_status` 判定为 `FAILED`，跑分器直接阻断了所有收益率与胜率指标输出。**这充分证明了 AUDIT-003-PATCH 补丁消除了“带病出虚高报告”的系统漏洞**。
2. **ARM B 证明了门禁在合法行情下 100% 畅通放行**：
   - 当采用真实按跳空后开盘价成交的 `gap_model="bar_open"` 时，所有成交价均落在当根 K 线合法区间内，价格门禁 0 拦截，`run_status="OK"`。
   - 4b（乙）与 4c（丙）策略在开仓 25/26 笔后，触及 20% 账户回撤熔断闸门（`account_drawdown_locked` 拦截 61/70 笔），证明风险硬闸门执行有效。

---

## 三、全域数据到决策追踪矩阵（深度清点）

依据用户工单关于“全部数据参与分析与决策追踪”的要求，对系统现存的 17 项全域数据通道进行逐项审计：

| 序号 | 数据通道名称 | 存储位置与格式 | 时间戳与可得性证据 | 本次回测读取方式 | 能否改变策略决策与观察状态 | 实际参与状态判定 | 未接入原因与最小接线需求 |
|---|---|---|---|---|---|---|---|
| 1 | **4小时 K线** | `market_v2.sqlite3` / `candles_4h` | `open_time`, `close_time`, `available_at` 齐全 | `feed.load("4h")` | **能**：决定箱体构建、阻力位计算、放量吸收阳线判定与 4h 跟踪止损位 | **【深度参与】** | —（核心决策输入） |
| 2 | **1小时 K线** | `market_v2.sqlite3` / `candles_1h` | `open_time`, `close_time`, `available_at` 齐全 | `feed.load("1h")` | **能**：推进 Spring、Breakout、Retest 状态机，直接决定候选生成 | **【深度参与】** | —（核心决策输入） |
| 3 | **15分钟 K线** | `market_v2.sqlite3` / `candles_15m` | `open_time`, `close_time`, `available_at` 齐全 | `feed.window("15m")` | **能**：决定开仓撮合价、盘中止损击穿退出、跳空跌穿判定及价格区间合法性 | **【深度参与】** | —（核心决策输入） |
| 4 | **合约交易规范** | `contract_limits.csv` (exchangeInfo) | 单时点静态快照，历史变更证据 `unverified` | `PE.contract_limits()` | **能**：决定下单手数求解、价格步进取整、最小名义价值过滤（缺失即拒单） | **【深度参与】** | 历史多时点变更追溯尚未接入 |
| 5 | **前15m成交额** | `candles_15m.quote_volume` | 随 15m K 线同步闭合 | `bar_before()["quote_volume"]` | **能**：决定 1% 参与率上限，直接压减下单量或整单拒单 | **【深度参与】** | —（风险约束输入） |
| 6 | **资金费率事件** | `funding_history` + Parquet 冷档 | 8h 结算时点，缺口段已由 06-06 补齐对账 | `_settle_funding()` | **能**：持仓期间逐笔扣减/计入资金费，改变已实现净额与账户权益净值 | **【结算参与】** | 生产实时读取链尚未合并冷档 |
| 7 | **未平仓合约 (OI)** | `market_v2.sqlite3` / `open_interest` | 5m/15m 采集时点 | **未读取** | **否**：回测引擎未订阅该表，未作为箱体突破时的持仓量增减共振过滤 | **【未参与】** | 需在 `BoxObserver` 增加 `oi_delta` 突破验证接口 |
| 8 | **多空持仓比 (L/S)** | `market_v2.sqlite3` / `long_short_ratio` | 5m 采集时点 | **未读取** | **否**：未参与做多信号的情绪逆向或顺向过滤 | **【未参与】** | 需在 `try_open` 增加多空比拥挤度闸门 |
| 9 | **主动买卖量 (CVD)** | `candles_15m.taker_buy_volume` | 随 K 线原生携带 | **未读取** | **否**：仅读取了 `volume`，未读取 `taker_buy_volume` 用于 CVD 净主动吃单分析 | **【未参与】** | 现成数据已在表中，需在吸收阶段计算 CVD 积累 |
| 10 | **聪明钱/大户持仓** | `快照/smart_money/` JSON | 实时快照，**历史缺失无法补** | **未读取** | **否**：无历史回溯时序库，回测期间无法取得历史大户多空数据 | **【不可得未参与】** | 仅实盘挂钟有快照，历史无法造数 |
| 11 | **实时深度盘口** | `realtime_quotes` | 实时快照，无历史订单簿 | **未读取** | **否**：回测使用 K 线模拟撮合，未读取买一卖一挂单深度 | **【回测不适用】** | 历史盘口成本极高，回测用 slippage 替代 |
| 12 | **代币流通市值** | `symbol_pool` | 静态快照 | **未读取** | **否**：未根据市值大小做动态杠杆或仓位分级 | **【未参与】** | 需在配置中增加基于市值的仓位系数矩阵 |
| 13 | **板块概念字典** | `sectors.json` | 静态字典 | **未读取** | **否**：未用于板块协同共振或行业龙头轮动判断 | **【未参与】** | 需增加行业分类与板块指数联动接口 |
| 14 | **持仓量/市值比** | 派生指标 | 需计算 | **未读取** | **否**：未作为资产流动性挤压/爆仓风险指标 | **【未参与】** | 依赖 OI 与流通市值的联合计算链路 |
| 15 | **5m/30m/8h/12h/1d K线**| `market_v2.sqlite3` | 各周期原生闭合时间 | **未读取** | **否**：策略仅订阅 4h/1h/15m 三周期，其余原生周期未引入多周期共振 | **【未参与】** | 需在框架增加 12h/日线大趋势过滤接口 |

### 3. 数据参与审计小结
- **现状定性**：目前回测系统是一套**纯粹基于价格行为（Price Action）与成交量（Volume）的量价因果模型**，结合了基础合约规格与资金费结算；
- **缺口说明**：用户要求的全域数据（特别是 **OI 持仓量变化、CVD 净主动买入力量、衍生品多空比**）在数据库中虽有物理存储，但**尚未接入策略状态机（`BoxObserver`）与入场过滤器（`try_open`）**。
- **纪律落实**：依据工单要求，“展示/存库不等于参与，未满足完整数据参与要求前，不得宣布策略整体验收通过”。因此，本单明确将状态标注为 **`【回测运行完成／完整数据参与未完成】`**。

---

## 四、未完成项与移交 Codex / 主代理建议

### 1. 待裁决项（DEC-01）
- **问题**：`exit.gap_model` 的最终选择。
- **事实依据**：
  - `stop_price`（ARM A）：物理不可行，越界 3.58% $\sim$ 26.79%，强门禁下恒判定为 `FAILED`，无法出成绩。
  - `bar_open`（ARM B）：物理真实可成交，门禁 0 越界放行，`run_status="OK"`。
- **建议**：请用户正式批准将策略跳空口径固化为 **`exit.gap_model = "bar_open"`**。

### 2. 最小接线需求（供 Work 与主代理排期）
若需实现用户要求的“全数据参与”，建议在下一阶段按最小改动接入以下三项高价值数据：
1. **CVD 主动买卖量接入**：直接在 `accum_early_entry._absorption()` 中读取 1h/15m K 线的 `taker_buy_volume`，计算吸筹阶段主动买单净占比（要求 CVD 必须为正且背离），增强吸收判定的可靠性。
2. **OI 持仓量共振接入**：在突破（Breakout）发生时刻，查询 `open_interest` 表，要求持仓量同步增加（伴随真突破），过滤无资金跟进的假突破。
3. **资金费率冷热合并接口打通**：将 06-06 验证通过的 `funding_events_adapter` 正式接入生产 `paper_engine._settle_funding()`，消除缺口期资金费漏扣。

---

## 交付产物索引
- **主交付报告**：[`evidence/ACCUM-EARLY-08-ANTIGRAVITY/README.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-08-ANTIGRAVITY/README.md)
- **ARM A 运行产物**：
  - 汇总报告：[`evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_a/回测_甲乙丙对比.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_a/回测_甲乙丙对比.md)
  - 机器账本：[`evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_a/回测_甲乙丙对比.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_a/回测_甲乙丙对比.json)
  - 运行身份：[`evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_a/运行身份_antigravity_08_arm_a.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_a/运行身份_antigravity_08_arm_a.json)
- **ARM B 运行产物**：
  - 汇总报告：[`evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_b/回测_甲乙丙对比.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_b/回测_甲乙丙对比.md)
  - 机器账本：[`evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_b/回测_甲乙丙对比.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_b/回测_甲乙丙对比.json)
  - 运行身份：[`evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_b/运行身份_antigravity_08_arm_b.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-08-ANTIGRAVITY/out_arm_b/运行身份_antigravity_08_arm_b.json)
- **原始隔离账本数据库**：
  - ARM A 账本：`evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_a/paper/`
  - ARM B 账本：`evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/paper/`

---
*本单完成，成果正式移交主代理进行独立验收。*
