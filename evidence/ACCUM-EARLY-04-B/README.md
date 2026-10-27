# ACCUM-EARLY-04-B｜提前入场策略接入（甲乙丙）

> 交付时间：2026-10-26T15:07:59Z｜代码版本：`accum_early_04b_v1`｜配置格式版本：`accum-early-04/v1`

## 一句话结论

**接线做完了，策略好不好还不知道。**

具体说：甲乙丙三套提前入场方案（工程代号 4a／4b／4c）已经接进现有的观察、候选、模拟执行链路，
能取数、能观察、能记候选、能开仓成交、能上移止损、能退出、能进独立账本；
**但三组阈值全部没有你的批准**，所以现在**开不了新仓**——缺配置就阻塞开仓，这是按工单要求做的。

另外要明确一句：**本单没有验证甲乙丙赚不赚钱**，也没有宣称任何一组更优。
所有跑出来的成交都在 `fixture` 测试配置下，只证明「管道通」，不构成绩效结论。

## 一、入口普查：哪些是占位、哪些能跑

先说人话：项目里原来只有「账本和配置校验」的壳子，**没有任何一处能推进甲乙丙的观察状态**。
所以本单必须补一个观察推进器，但**没有另建引擎**——成交、止损、退出全部复用原有模拟执行模块。

| 文件 | 行数 | 判定 |
|---|---|---|
| `accum_research.py` | 286 | 占位：入口与账本在，行情读取/状态推进/下单调用三者全无 |
| `accum_pending.py` | 436 | 占位：纯数据表，不推进任何状态 |
| `observe_store.py` | 377 | 可复用：负责「记录」，不负责「取数」与「下单」 |
| `research_15m.py` | 626 | 可运行：只读，03-B 已把成交/退出接上 |
| `paper_engine.py` | 2712 | 可运行：03-B 已在隔离账本里跑通三条真实事件 |
| `state_machine.py（冻结包）` | 683 | 不可直接表达甲乙丙：迁移条件写死在冻结包里，而甲乙丙的起点与确认方式不同；改它 = 改冻结规则 ⇒ 本单不改 |
| `paper_loop.py` | 347 | 只跑 3a–3d：研究臂 4a–4c 不在其循环与接口内（本单不改） |
| `unified_runtime.py` | 3156 | 只跑 3a–3d：研究臂 4a–4c 不在其循环与接口内（本单不改） |
| `live_api.py` | 2176 | 只跑 3a–3d：研究臂 4a–4c 不在其循环与接口内（本单不改） |

要点三条：

1. `accum_research.py` 与 `accum_pending.py` 是**占位**（2 个），只有配置表与账本 DDL，不读行情、不推进状态、不下单。
2. `observe_store.py`／`research_15m.py`／`paper_engine.py` 这 3 个是**能跑**的，分别管「记录」「取数」「执行」；其余 7 个里除了冻结包，都是只跑 3a–3d 的生产入口，本单不碰。
3. 冻结包的状态机**不能**直接表达甲乙丙：它的迁移写死在冻结包里（必须先跌破箱体下沿才算扫单），而甲乙丙的起点与确认方式不同。**改它等于改冻结规则**，所以本单不动它，改在研究侧另建观察器。

## 二、最小修改清单与代码差异

一共动了 2 个文件（新增 1 个、修改 1 个），另有 5 个文件（或文件组）只复用不改。

| 文件 | 类型 | 做了什么 |
|---|---|---|
| `框架/backend/accum_early_entry.py` | 新增 | 配置闸门（36 个必填字段、无默认值）+ 1h/4h/15m 取数 + 甲乙丙观察状态机+ 候选条件表 + 委托桥 + 隔离断言 |
| `框架/backend/live_store.py` | 修改（3 处） | ① 新增 RESEARCH_ARMS=('4a','4b','4c')；② ledger_path() 允许研究臂，但**强制**要求显式 PAPER_LEDGER_DIR，否则拒绝（默认目录=生产）；③ 缺库提示里 ARMS.index(arm) 加保护，避免研究臂抛 ValueError 而不是 LedgerUnavailable |
| `框架/backend/observe_store.py` | 未改 | 直接复用（scheme 列 + 隔离断言 + 事件/候选/迁移/缺口表） |
| `框架/backend/research_15m.py` | 未改 | 用 MergedReader('1h') 直接取 1h；MergedMarket 只暴露 15m/4h 也不影响 |
| `框架/backend/paper_engine.py` | 未改 | 只调用 open_position / check_position_exit_on_bar / advance_position_trailing |
| `策略/WyckoffMaster/event_trend_v0_1/state_machine.py` | 未改 | 只读复用箱体门槛参数与 ATR/ER 特征函数 |
| `框架/backend/paper_loop.py / unified_runtime.py / live_api.py / api/*` | 未改（协调项） | 生产循环与 HTTP 接口只认 ARMS=3a–3d ⇒ 研究臂没有端点、前端不展示 |

改动量：**3 处、+18 行、−2 行**。
改前副本在 `baseline/live_store.py.before_04b`，差异在 `baseline/live_store_04b.diff`。

> 说明为什么基线不是 `git show HEAD:`：仓库当前提交（37474 字节）比工作树（45926 字节）落后很远，
> 前几单的改动都没提交。拿它当基线会把别人几十处改动算进本单。所以基线用「反向还原本单 3 处编辑」得到，
> 还原前逐条断言了 3 处标记各出现 1 次，还原后断言 `RESEARCH_ARMS` 不再出现——不满足就报错，不静默通过。

## 三、配置要求：缺一项就不许开仓

配置是**显式必填**的：36 个字段，**一个默认值都不给**。
字段名与待决表里的 13 项一一对应（`mapping_check()` 校验，当前 13 项全部对上）。

- `kind` 只能是 `approved`（你批准的）或 `fixture`（工程自测）；`approved` 必须带批准依据，`fixture` 必须显式设 `ACCUM04_FIXTURE_OK=1` 才允许开仓。
- 另有 15 个字段带**取值白名单**：写了白名单以外的取值，直接算「不支持」，同样阻塞开仓——避免「配置写了但代码没实现」被当成通过。
- 还有 3 个字段属于**口径冲突**（滑点用策略包的 3bps 还是生效视图的 5bps、风控用哪套、参与率上限取哪个），本单只要求显式填写，**不替你选**。

要填的时候直接看这两份：

- `config/批准配置_空白表单.json` —— 36 格全 null，照着填。
- `配置字段表.md` —— 每一格是谁定的、代码支持哪些取值、对应哪条待决问题（21 格能追到待决表或冲突表，15 格是格式/开关类）。

> 填完不等于批准：还要把 `kind` 改成 `approved`、`approved` 改成 `true`、在 `approval_ref` 写明依据。三处缺一处，照样阻塞开仓。

阻塞行为（实测，6／6 项通过）：

| 判据 | 结果 |
|---|---|
| 开仓被阻塞 | 通过 |
| 阻塞时未创建任何研究账本 | 通过 |
| 阻塞时未调用 paper_engine | 通过 |
| 数据读取仍然完成 | 通过 |
| 观察与记录仍然完成 | 通过 |
| 隔离边界仍可核对 | 通过 |

具体三组场景：

- **完全不给配置**：36 个字段全缺，创建了 9 条观察事件、全部阻塞在配置闸门（9 条），候选 0 条、研究账本文件 0 个 —— 即「数据照读、记录照记、开仓不干」。
- **只缺一个字段**：精确报出缺的是 `shared.local_resistance.mode`，不笼统报「配置错误」。
- **给了 fixture 但没开环境开关**：报 `not_approved、fixture_without_explicit_env`，仍然不许开仓。

## 四、真实事件追踪

用两个真实行情窗口跑，一个只能用冷档、一个走热库，确认两条取数路都能推进：

| 方案 | 候选条数 |
|---|---|
| 4a | 16 |
| 4b | 13 |
| 4c | 15 |

合计 200 个观察事件、519 条状态迁移、44 条候选（入库 44 条，逐条对齐）。
终态分布：过期 113、失效 43、回抽观察 28、扫单观察 16。

每个候选都带条件真值表，不是只给一个分数：共 487 条条件判定，其中
成立 386 条（79.3%）、本方案不要求 60 条、
**证据不足 41 条**（8.4%）。
证据不足的 41 条全是同一条判据「成交 bar 的可得时刻有没有证据」，
按成交 bar 分层是：冷档 27 条、热库 14 条、其它 0 条 ——
**不是只有冷档缺**，冷热两层的合并读取都没有 `available_at`。这类缺口只记「证据不足」，
不记「成立」，也不补造。

成交 bar 来源：冷档 27 条、热库 17 条 —— 冷热两路都通。

### 链路一：冷档窗口（SOLUSDT）

3 笔持仓全部平仓（3 笔 closed），
入场时点、初始止损、订单数、不许加仓、重复下单被拦、重复扫描不产生新单，**六项全过** —— 通过。
成交延迟 0 毫秒（即「确认后第一根已闭合 15 分钟 bar 的开盘价」，没有回填）。

### 链路二：热库窗口（AVAXUSDT）

3 笔持仓全部平仓（3 笔 closed），同上六项全过 —— 通过。
另外有 3 次重复开仓被 `position_already_open_no_add` 拒掉 —— 这是**正确行为**（单活跃事件，不加仓）。
成交延迟 0 毫秒。

### 链路三：真进程重启（换新进程，同一账本目录）

重启后订单数 6、成交数 6，与重启前**逐位相同**，没有重复下单。
更关键的一条：重启后身份**能不能从账本文件里读回来** —— 是。
三条持仓的委托号都从 `paper_position.signal_uid` 恢复，再回查候选，入场 bar 仍然逐位一致；
六项判据在重启后同样全过 —— 通过。

## 五、研究与生产隔离

隔离不是靠声明，是靠行为取证：11 项判据，11 项通过。

| 判据 | 结果 |
|---|---|
| 生产四本账本未被改动 | 通过 |
| 生产实时库未被改动 | 通过 |
| 生产账本目录没有研究臂文件 | 通过 |
| 隔离进程未以可写方式连生产库 | 通过 |
| 研究臂必须显式隔离目录 | 通过 |
| 未知臂仍被拒绝 | 通过 |
| 闸门越界被拒绝 | 通过 |
| 闸门在隔离根内可写（正向对照） | 通过 |
| 闸门未被写进生产账本目录 | 通过 |
| 本单研究账本只出现在本单 runs 目录 | 通过 |
| 早前工单的研究账本未被本单改动 | 通过 |

补充数字：隔离进程运行期共开了 3310 次数据库连接，
其中**以可写方式连生产账本目录／生产实时库的次数 = 0**（这是探针实测，不是「我们没写」的口头保证）。

生产四本账本的摘要：3a 未变、3b 未变、3c 未变、3d 未变；生产实时库未变；
行情库变了（采集器在写，属正常）。

本次隔离运行产生的 3 个研究账本全部落在 `evidence/ACCUM-EARLY-04-B/runs/` 之下；
早前工单（ACCUM-EARLY-01）那三本研究账本没被碰过（摘要与修改时刻都没变）。

负向测试也做了，而且带正向对照——否则「拒绝」可能只是恒抛异常：
不设 `PAPER_LEDGER_DIR` 时研究臂被拒；未知臂被拒；放行闸门指向生产目录被拒；
同样一次调用在隔离根目录内必须能成功写（证明前三条的「拒绝」是有条件的拒绝，不是永远抛错）。

## 六、工单要求逐项对照

| 工单要求 | 状态 | 证据 |
|---|---|---|
| 工作 1：普查入口、提交最小修改清单 | 完成 | `out/01_入口普查.json`，改动 2 个文件 |
| 工作 2：数据输入／观察状态／候选／失效原因／策略身份／账户隔离 | 完成 | `out/03_观察与候选.json`，44 条候选、6／6 项自检通过 |
| 工作 3：未批准阈值显式必填、缺配置阻塞开仓但其余照做 | 完成 | `out/02_配置闸门.json`，6／6 项通过 |
| 工作 4：等你转发的批准规则再绑定运行 | **未开始（等你的规则）** | 现在只有 `fixture` 自测配置，`approved=false` |
| 工作 5：接续冷热读取，只处理实际阻塞项 | 完成 | 复用 03-B 的合并读取，未改共享门面；`out/04_隔离全链.json` 冷热两路均通 |
| 工作 6：入场时间／已确认止损／重复扫描与恢复／不重复订单／研究生产隔离 | 完成 | `out/04_隔离全链.json` + `out/05_隔离边界.json`，隔离 11／11 |
| 工作 7：每次候选记录成立/未成立/缺口/拒绝原因 | 完成 | 每个候选一张条件真值表，487 条判定 |
| 纪律：不得把入口完成写成效果已验证 | 遵守 | 全文只声明「管道通」，未给任何绩效结论 |

## 七、剩余阻塞（都还没解决，不许当已解决）

1. **三组阈值未批准**（最主要）。没有你的规则，开仓一直是阻塞状态。本单只把「阻塞」做成可验证的，没绕过它。
2. **HTTP 接口与前端不认研究臂**。实测 `live_api.paper_order_post(arm="4a")` 返回 400「未知臂」；
   前端与生产循环仍只认 3a–3d。属跨界协调项，本单按工单不改，已登记具体调用与返回。
3. **合并读取的 15m bar 没有 `available_at`**（冷档与热库都没有，不只是冷档）。
   所以 41 条判定只能记「证据不足」，不能记「成立」。这是历史数据缺失，**不许补造**。
4. **03-B 登记的三处自写 SQL 仍只读热库**（`paper_loop.execute_cycle`、`unified_runtime.execute_cycle`、
   `live_api.paper_advance_trailing_post`）。本单只走自己这条链，没有改它们，也没有顺手重构。
5. **重复候选上限、冷却期未批准**：`repeat_candidate_limit` 属待决项，本单按「冷却 = 0」运行并如实标注，
   这会让候选比正式规则更密——**不能拿本单候选数量推断正式规则的信号频率**。

## 八、复跑说明

见 `scripts/复跑说明.md`。所有产物都在 `out/`，每个数字都能从对应的 json 里找到出处；
本文件由 `scripts/06_报告生成.py` 渲染，数字不手写；`scripts/07_对撞.py` 会从原始 json 独立重算一遍再比对。
配置表单与字段表由 `scripts/08_配置模板.py` 生成（字段清单直接从代码里的必填清单取，不手抄）。

<!-- FACTS-BEGIN -->
```json
{
 "version": "accum_early_04b_v1",
 "config_schema": "accum-early-04/v1",
 "required_fields": 36,
 "pending_map": 13,
 "implemented_keys": 15,
 "conflict_fields": 3,
 "fields_mapped": 21,
 "fields_structural": 15,
 "schemes": "4a／4b／4c",
 "placeholder_count": 2,
 "placeholder_list": "accum_research.py、accum_pending.py",
 "runnable_count": 7,
 "runnable_core": 3,
 "changed_files": 2,
 "new_files": 1,
 "modified_files": 1,
 "unchanged_files": 5,
 "diff_hunks": 3,
 "diff_added": 18,
 "diff_removed": 2,
 "git_head_bytes": 37474,
 "current_bytes": 45926,
 "gateA_missing": 36,
 "gateA_events": 9,
 "gateA_blocked": 9,
 "gateA_candidates": 0,
 "gateA_ledger_files": 0,
 "gateB_missing_field": "shared.local_resistance.mode",
 "gateC_blocked_by": "not_approved、fixture_without_explicit_env",
 "gate_pass": 6,
 "gate_total": 6,
 "obs_events": 200,
 "obs_transitions": 519,
 "obs_candidates": 44,
 "obs_candidates_db": 44,
 "obs_4a": 16,
 "obs_4b": 13,
 "obs_4c": 15,
 "obs_gaps": 41,
 "cond_pass": 386,
 "cond_not_required": 60,
 "cond_not_proven": 41,
 "cond_total": 487,
 "cond_pass_pct": "79.3",
 "cond_not_proven_pct": "8.4",
 "bar_cold": 27,
 "bar_hot": 17,
 "obs_expired": 113,
 "obs_invalidated": 43,
 "obs_retest": 28,
 "obs_sweep": 16,
 "checks_pass": 6,
 "checks_total": 6,
 "cold_positions": 3,
 "cold_closed": 3,
 "cold_all_ok": true,
 "hot_positions": 3,
 "hot_closed": 3,
 "hot_all_ok": true,
 "hot_no_add": 3,
 "fill_lag_cold": 0,
 "fill_lag_hot": 0,
 "resume_orders": 6,
 "resume_fills": 6,
 "resume_no_dup": true,
 "resume_identity": true,
 "resume_all_ok": true,
 "iso_pass": 11,
 "iso_total": 11,
 "iso_conns": 3310,
 "iso_violations": 0,
 "iso_run_ledgers": 3,
 "prior_ledgers_unchanged": true,
 "ledger_3a": "未变",
 "ledger_3b": "未变",
 "ledger_3c": "未变",
 "ledger_3d": "未变",
 "live_db": "未变",
 "market_db": "变了（采集器在写，属正常）",
 "out_files": 7,
 "iso_list": "| 生产四本账本未被改动 | 通过 |\n| 生产实时库未被改动 | 通过 |\n| 生产账本目录没有研究臂文件 | 通过 |\n| 隔离进程未以可写方式连生产库 | 通过 |\n| 研究臂必须显式隔离目录 | 通过 |\n| 未知臂仍被拒绝 | 通过 |\n| 闸门越界被拒绝 | 通过 |\n| 闸门在隔离根内可写（正向对照） | 通过 |\n| 闸门未被写进生产账本目录 | 通过 |\n| 本单研究账本只出现在本单 runs 目录 | 通过 |\n| 早前工单的研究账本未被本单改动 | 通过 |",
 "np_cold": 27,
 "np_hot": 14,
 "np_other": 0,
 "cold_ok_text": "通过",
 "hot_ok_text": "通过",
 "resume_ok_text": "通过",
 "resume_dup_text": "没有重复下单",
 "resume_id_text": "是",
 "prior_text": "没被碰过",
 "gate_list": "| 开仓被阻塞 | 通过 |\n| 阻塞时未创建任何研究账本 | 通过 |\n| 阻塞时未调用 paper_engine | 通过 |\n| 数据读取仍然完成 | 通过 |\n| 观察与记录仍然完成 | 通过 |\n| 隔离边界仍可核对 | 通过 |",
 "entry_table": "| `accum_research.py` | 286 | 占位：入口与账本在，行情读取/状态推进/下单调用三者全无 |\n| `accum_pending.py` | 436 | 占位：纯数据表，不推进任何状态 |\n| `observe_store.py` | 377 | 可复用：负责「记录」，不负责「取数」与「下单」 |\n| `research_15m.py` | 626 | 可运行：只读，03-B 已把成交/退出接上 |\n| `paper_engine.py` | 2712 | 可运行：03-B 已在隔离账本里跑通三条真实事件 |\n| `state_machine.py（冻结包）` | 683 | 不可直接表达甲乙丙：迁移条件写死在冻结包里，而甲乙丙的起点与确认方式不同；改它 = 改冻结规则 ⇒ 本单不改 |\n| `paper_loop.py` | 347 | 只跑 3a–3d：研究臂 4a–4c 不在其循环与接口内（本单不改） |\n| `unified_runtime.py` | 3156 | 只跑 3a–3d：研究臂 4a–4c 不在其循环与接口内（本单不改） |\n| `live_api.py` | 2176 | 只跑 3a–3d：研究臂 4a–4c 不在其循环与接口内（本单不改） |",
 "change_table": "| `框架/backend/accum_early_entry.py` | 新增 | 配置闸门（36 个必填字段、无默认值）+ 1h/4h/15m 取数 + 甲乙丙观察状态机+ 候选条件表 + 委托桥 + 隔离断言 |\n| `框架/backend/live_store.py` | 修改（3 处） | ① 新增 RESEARCH_ARMS=('4a','4b','4c')；② ledger_path() 允许研究臂，但**强制**要求显式 PAPER_LEDGER_DIR，否则拒绝（默认目录=生产）；③ 缺库提示里 ARMS.index(arm) 加保护，避免研究臂抛 ValueError 而不是 LedgerUnavailable |\n| `框架/backend/observe_store.py` | 未改 | 直接复用（scheme 列 + 隔离断言 + 事件/候选/迁移/缺口表） |\n| `框架/backend/research_15m.py` | 未改 | 用 MergedReader('1h') 直接取 1h；MergedMarket 只暴露 15m/4h 也不影响 |\n| `框架/backend/paper_engine.py` | 未改 | 只调用 open_position / check_position_exit_on_bar / advance_position_trailing |\n| `策略/WyckoffMaster/event_trend_v0_1/state_machine.py` | 未改 | 只读复用箱体门槛参数与 ATR/ER 特征函数 |\n| `框架/backend/paper_loop.py / unified_runtime.py / live_api.py / api/*` | 未改（协调项） | 生产循环与 HTTP 接口只认 ARMS=3a–3d ⇒ 研究臂没有端点、前端不展示 |",
 "scheme_table": "| 4a | 16 |\n| 4b | 13 |\n| 4c | 15 |"
}
```
<!-- FACTS-END -->

