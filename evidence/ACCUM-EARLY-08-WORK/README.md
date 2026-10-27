# ACCUM-EARLY-08-WORK｜V3.1 环境准备与信号接入（接口层）

- 出具方：Work
- 日期：2026-10-27（北京时间）
- 分支：`wyckoff-v3.1-main`
- 对应工单：`docs/派工_V3.1_Work环境与信号接入_2026-10-27.md`

> **状态口径（先读这一行）**
> 本文全部结论为**执行方（Work）自报**，**未经 Codex 独立验收**。
> 不得写成「已验收通过」。判定只有三态：通过／不通过／未验证；未验证不计入通过。
> 本单**只交环境准备与接口证据**，**没有接入本次回测信号**（顺序门槛未满足，见 §九）。

---

## 一句话结论

环境是活的、健康的、隔离可证的，V3.1 的真实路由与信号落点已经确认到行号级；
**信号从候选到 API 的幂等链路在隔离库上跑通（15/15）**；
但**「研究信号在现有信号页面显示」这一层结构上不成立**（页面只请求 `scope=current`，
当前区只放行 `origin∈{live,recovered}`），且**配置身份自检为 false、G01–G05 全 pending**，
所以本次回测信号**不接入**，先把契约和裁决项交主代理。

---

## 一、逐项完成状态

| 工单要求 | 状态 | 依据 |
|---|---|---|
| 现行环境/服务状态与启动方式 | **完成** | `out/01_环境与服务状态.json`；§二、§三 |
| 隔离证明（正反向） | **完成** | `out/03_隔离与空链路.json`；§四（15/15） |
| 实际 V3.1 路由/策略身份证据 | **完成** | `out/02_路由与身份.json`；§五（8/11，3 项是配置身份/门禁，不是路由） |
| 与回测侧共同接口契约 | **完成（草案，待主代理核对）** | `接口契约.md`；§六 |
| 所改文件清单 | **完成** | `out/05_改动清单.json`；§八（生产改动 0） |
| 空链路验证：候选→幂等持久化→现有 API | **完成** | `out/03_隔离与空链路.json`；§四 |
| 空链路验证：→ 现有信号页面显示 | **阻塞（结构性不成立）** | `out/04_页面落点.json`；§七 |
| 本次回测信号接入 | **未开始（按工单顺序门槛）** | §九 |

---

## 二、现行环境与服务状态（只读采集，2026-10-27 16:36–16:50 北京时间）

启动器身份：`框架/backend/start.sh` → `service_manager.py`，配置
`port=8787 / paper_enabled=false / arm=3a / symbol=all`。

| 服务 | PID | 状态 | 身份核对 | 备注 |
|---|---|---|---|---|
| API（`adapter.py`） | 91940 | running | recorded=listen=91940 ✔ | `/api/health` HTTP 200 |
| 回测 Worker | 91943 | running | ✔ | |
| 实时采集器 | 42278 | running | 不由本启动器管理 | 健康探测通过 |
| 统一运行时（`unified_runtime.py`，arm 3a） | 91947 | running | ✔ | 启动于 2026-10-26T11:09:13Z |

`overall_health = healthy`。运行时自报：信号引擎 `active`、全池 **526/526** 就绪、
1h 水位 `2026-10-27T07:00:00Z`、数据源 `merged_cold_hot`、启动恢复候选 **0**、
历史候选丢弃 **0**、扫描错误 **0**。

**信号与观察的真实条数（不是「大概」）：**

- `live_signal` 表 **0 行** ⇒ 当前**没有任何自然实时信号**（这与既有结论一致，不是本单造成）。
- `/api/v1/signals?scope=current` **total=0**，`quality.status=ok`（0 条是真实结论，不是故障）。
- `/api/v1/signals/observe` **334 条**观察中事件（BOX 302 / SWEEP 11 / RETEST 21），
  `observing_total=334`、`data_not_ready=0`、`scan_errors_total=0`、
  `writer_pid_alive=true`、`age_seconds≈60~130s`、`stale=false`、`universe=526`。

**边界：**

- 后端 API **只绑回环**（`127.0.0.1:8787` 与 `[::1]:8787`），未对外暴露。
- 前端 dev 服务 `:3000` 绑 `*:3000`（**对外**）——属既有状态，本单未改，仅登记。
- `order_processing.new_entry_gate.allow_new_entries = true`（`reason=user_ui_resumed`，
  `set_by=user_ui_control`，自 `1792753532432`）。即：**当前纸面开仓闸门是开的**。
  本单未改它（工单禁止自行停服务/改闸门），但如实登记：若真有自然候选，纸面链会自动开仓。
  是否关闸请主代理/用户决定。
- `execution_daemon`：running，`open_positions=0`，`orders_routed_total=0`。

> 采集口径说明：本单期间 `框架/backend/快照/public_extras/*.json` 与
> `locks/paper_runtime_3a_observe.json` 被运行中的采集器与运行时持续重写，属正常生产行为，
> 不是本单写入（`out/05_改动清单.json` 已排除这些目录）。

---

## 三、启动方式（复用，不新建）

只复用既有入口，没有写任何平行启动器：

```sh
cd 框架/backend
sh start.sh status          # 状态全景（本单只用了这一条，只读）
sh start.sh                 # 默认安全启动：API(:8787) + Worker，不开自动交易
sh start.sh stop            # 只停本启动器管理的进程
```

环境变量口径（`service_manager.py` 已内置，隔离测试用）：
`PORT` / `SYS_PID_DIR` / `SYS_LOG_DIR` / `LIVE_DB` / `PAPER_LEDGER_DIR` / `PAPER_LOCK_DIR` /
`MARKET_DB` / `MARKET_ARCHIVE` / `JOBS_DB` / `ISOLATED_MODE=1`（隔离模式会强校验八个路径
**不得**落在 `数据/`、`backend/live`、`backend/locks`、`backend/logs`、`backend/` 之内）。

**本单没有启动、没有停止、没有重启任何进程。**

---

## 四、隔离证明与空链路验证（`scripts/03_隔离与空链路.py`）

做法：把生产信号库与四本纸面账本用 **SQLite 在线备份**取一致快照到
`evidence/ACCUM-EARLY-08-WORK/isolated/`，再用**干净环境变量**启动一个子进程跑真实代码
（`SignalPersistenceAndRouter.persist_candidates` + `adapter._v1_signals_merged`）。

**15 项判定全部通过：**

| 组 | 判据 | 结果 |
|---|---|---|
| 隔离成立 | `LIVE_DB` 不指向生产 / 账本目录不指向生产 | ✔ / ✔ |
| 探针有效 | SQL 级写探针的**负向对照**（可写连接上 `CREATE TABLE` 必须被抓到） | ✔ 抓到 |
| 幂等 | 同 `signal_uid` 写两次只一行；第二次记 `updated=1,new=0` | ✔ |
| 幂等 | `first_seen_at` 未被改写、`updated_at` 已推进 | ✔ / ✔ |
| 来源 | `origin` 落库为传入值（研究标识没被默认成 `live`） | ✔ |
| 角色 | `payload.role = enterable_arm_entry` | ✔ |
| API 审计通道 | `scope=all` 能读到研究信号 | ✔ |
| API 当前区 | `scope=current` **读不到**研究信号（`excluded_breakdown.not_current_origin=1`） | ✔ |
| 正向对照 | `origin=live` 的合成信号**能**进当前区（证明是白名单在挡，不是查询坏了） | ✔ |
| 生产零写入 | 生产信号库行数前后不变、生产库上 0 条写语句 | ✔ / ✔ |
| 生产零写入 | 生产信号库 + 四本账本 **sha256 与字节数逐位未变** | ✔ |

**合成信号的身份（必须说清楚，防止被当成自然产出）：**
`symbol=SELFTESTUSDT`（不存在的标的）、`event_id=ev08work_<ts>`、
`pit_assumption_note` 明写「隔离自测：非自然信号，仅验证链路」。
它**不是**自然实时信号，也**不是**本次回测产物。

**顺手查出的两个真实缺口（写进契约 §1）：**

1. 信号行的 `strategy_version` 落库值是 **`v0.1.0`**，而接口顶层 `strategy_version` 是
   **`FT-LOW4H-EXP-V2.2`** —— 两个口径，不是同一个字段的两种写法。
2. `payload_json`（31 个键）里**没有** `rule` / `model_version`；
   表里 `config_hash` / `data_version` 两列**持久化函数根本没写**（实测 `null`）。
   ⇒ 单看一条信号行，**无法自证它属于 FT-LOW4H 规则、用哪套配置**。

---

## 五、V3.1 实际路由与策略身份（`scripts/02_路由与身份.py`）

**三处对撞，8/11 通过；不通过的 3 项全部是「配置身份/门禁」，不是路由。**

路由链（每一环都有行号，见 `out/02_路由与身份.json`）：

```
unified_runtime.py:53   from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine
unified_runtime.py:68-72 EXCHANGE=binance / MARKET=um / TIMEFRAME=1h / STRATEGY_KEY=wyckoff_v0_1 / ARM_ENTRY_ROLE
state_machine.py:605    cand_id = f"cand_{event.event_id}_{close_time_ms}"      ← 幂等键
unified_runtime.py:2114 def persist_candidates(...)                            ← 唯一落库入口
unified_runtime.py:2146 ON CONFLICT(signal_uid) DO UPDATE ...                  ← 幂等语义
unified_runtime.py:2802 self.router.persist_candidates(new_cands, origin=origin, now_ms=now)
api/routes.py:242       Route("/api/v1/signals", ... ns["_v1_signals_merged"](...))
live_api.py:336         where.append("origin IN ('live','recovered')")         ← 当前区白名单
```

**旧链已排除（不是猜的）：** `unified_runtime.py` 对 `live_signal_engine` **0 处引用**；
只有 `live_worker.py:51` 引用它，而 `live_worker.py` 不在当前运行链里
（启动器起的是 `workers/backtest_worker.py` 与 `unified_runtime.py`）。

**运行事实与接口事实对得上：** 运行时 pid 91947 = pid 文件 91947；
观察明细文件的写者 `pid=91947`（同一进程）；接口返回
`strategy_version=FT-LOW4H-EXP-V2.2 / rule_version=FT-LOW4H / param_hash=82dec362c182 /
params_source=frozen_view_10_研究参数生效视图_v2_2_user5000_shared`。

**不通过的 3 项（这 3 项决定了「信号只能带研究标识」）：**

```
identity.view_sha256          = 82dec362c18230e1…（生效视图）
identity.expected_view_sha256 = 6d18d222d1991f99…（固定接受值）   ⇒ view_sha_ok = false
identity.registered_ok        = false   （registered_configs canonical hash 漂移）
identity.effective_ok         = false   （18 配置生效身份 canonical hash 漂移）
identity.identity_ok          = false
gate_binding.gates            = {G01..G05: pending}，all_pass = false
run_frozen                    = false
```

---

## 六、共同接口契约

见同目录 **`接口契约.md`（草案 v1）**。要点三条：

1. **`origin` 是唯一决定「进不进当前交易信号区」的字段**，库上没有 CHECK 约束，
   接入时**必须显式传**（默认值是 `live`，传漏了研究信号就会被当成可交易信号）。
2. **对账只能用 `signal_uid` + `param_hash`**，不能用 `strategy_version`
   （信号行是 `v0.1.0`，接口顶层是 `FT-LOW4H-EXP-V2.2`）。
3. **回测侧交付必须能回答**：运行身份、来源标签、幂等键、四个时刻（含真实的
   `available_at`）、批准状态（当前只能 `false`）、可执行状态与拒绝原因。缺任一项不接入。

契约需主代理核对后才生效；核对前双方不得据此实现。

---

## 七、页面落点（`scripts/04_页面落点.py`）

两处代码事实摆在一起：

- 页面请求：`useMarketData.ts` → `apiGet('/signals?scope=current&limit=500')`
- 后端放行：`live_api.py:336` → `origin IN ('live','recovered')`

⇒ **研究/回放信号不会出现在现有信号页面**。这不是缺陷，是防冒充的设计。
现有页面上唯一能承载研究/观察内容的只读通道是**观察区** `/signals/observe`
（页面已渲染 334 条观察事件）。

⇒ 「研究信号在现有信号页面显示」这一层**不能按原样成立**，需要裁决（见 §九）。

---

## 八、改动清单（`scripts/05_改动清单.py`）

- **生产代码 / 配置 / 策略包 / 前端源码改动：0 个**
  （用文件 mtime 核，排除运行中进程持续重写的 `快照/`、`locks/`、`logs/`；`git status` 不作基线）
- 本单新增（全部在 `evidence/ACCUM-EARLY-08-WORK/`）：
  `README.md`、`接口契约.md`、
  `scripts/{lib08,01_环境与服务状态,02_路由与身份,03_隔离与空链路,03b_隔离链路_worker,04_页面落点,05_改动清单}.py`、
  `out/01..05_*.json`、`isolated/`（隔离副本，可删可重建）

---

## 九、未验证 / 阻塞 / 需裁决

**未验证（不得算通过）：**

1. 自然实时信号：全池自然候选 **0**，账本 0 笔 ⇒ 「自然行情下能产出信号」**未验证**。
2. 页面渲染隔离信号：未做浏览器级验证（不启第二实例、不改前端）。
3. 真实挂钟自动成交：`open_positions=0`、`orders_routed_total=0`，本单未触及。
4. 隔离链路的 HTTP 层：用 `route` 表绑定的**同一个处理函数**取证，**未经过 HTTP 服务器**
   （避免起第二实例与生产争库）。HTTP 层本身另有证据：生产 8787 的
   `/api/v1/signals`、`/api/v1/signals/observe` 均实测 200 且字段一致。

**阻塞（按工单「出现即报告，不自行处理」）：**

1. **配置身份不成立**：`identity_ok=false`、G01–G05 全 pending、`run_frozen=false`
   ⇒ 任何信号只能标研究/未批准。
2. **研究信号的可视落点未定**：现有信号页面结构上只显示 `live/recovered`。
   要么只落审计通道（页面不可见），要么接到已有观察区（页面可见，只读）。
   两条路都不许本单擅自选（一条要改前端架构，工单禁止）。
3. **`origin` 是否新增 `research` 取值**：需改运行时代码，本单未改。
   未批准前，研究/回放信号应复用既有的 `replay` / `historical_research`。
4. **纸面开仓闸门当前是开的**（`allow_new_entries=true`）。是否在接入研究信号期间关闸，
   请主代理/用户决定；本单不自行改。

**未开始（顺序门槛）：**

本次回测信号接入。工单规定：反重力交付完整回测包 → 主代理独立检查运行身份、有效状态与
双方接口一致性 → Work 才按同一契约接入。**本单未见反重力回测包，故不接入。**

---

## 十、复跑说明

```sh
cd evidence/ACCUM-EARLY-08-WORK
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
$PY scripts/01_环境与服务状态.py     # 只读；约 1s（start.sh status 本身约 0.5s）
$PY scripts/02_路由与身份.py         # 只读；约 1s
$PY scripts/03_隔离与空链路.py       # 会重建 isolated/ 并写隔离库；约 1s
$PY scripts/04_页面落点.py           # 只读
$PY scripts/05_改动清单.py           # 只读
```

`03` 每次运行都会 `rm -rf isolated/` 再重建（**只在该目录内**），隔离库里的合成信号随之重建，
不影响生产。所有脚本退出码 0 为正常。

**依赖**：仅标准库 + 工程自身模块；不需要网络；不需要启动任何服务
（`01` 需要生产 API 在 8787 上活着才能取到接口事实）。
