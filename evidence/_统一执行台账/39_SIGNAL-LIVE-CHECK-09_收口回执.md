# 39｜SIGNAL-LIVE-CHECK-09 收口回执

- 工单：`SIGNAL-LIVE-CHECK-09｜生产信号链持续核对与缺陷修复`
- 证据：`evidence/SIGNAL-LIVE-CHECK-09/`（`probes/` 6 个脚本、`out/` 若干产物、`README.md`、`DELIVERY-隔离入口.md`）
- 观测窗口：`2026-10-25T11:50Z` 起，跨 2 根新增 1h（`11:00Z`、`12:00Z`）
- 状态：**通过（本窗口内，后端）**——逐币记录通过、隔离回放逐币零差异、三类链路缺陷未出现、隔离入口六项齐全；
  **版本一致性只能记「后端通过、前端 1 个文件不符」**（`SignalListPanel.tsx` 于 `13:21:41Z` 被外部改动，非本单）
- 真实交易：**保持禁用**（`live_signal` 0 行、四本账本有效成交 0 笔）
- 冻结规则 md `1f1167de…`、门禁 `f4691db2…`、`live_store.py` `1e1218ea…`、`adapter.py` `6218416f…`、`signalsState.ts` `f2d40893…`、`useMarketData.ts` `b1289ce6…`、`App.tsx` `07aefc6b…` **sha 全部未变**；
  **`SignalListPanel.tsx` 已变**（`bf8eb3ce…` → `546fdab0…`，`13:21:41Z`，外部改动，已登记未回滚）

## 一句话结论

这一轮生产链路**没有病**：11:00Z 那根小时线进来后 526 个币逐币阶段与同水位隔离回放**零差异**，
46 个换阶段的币每一条迁移都能和漏斗计数一一对上，候选 0/入库 0/接口 0 三段一致且接口三态正确。
**唯一的真缺陷是「文件改了但进程还跑旧代码」**（`adapter.py` mtime 晚于进程启动），已用受控重启修掉。
另外抓到并修掉 3 处**我自己探针的口径错误** + 6 个**探针代码缺陷**——它们每一个都会把正常记账误报成生产缺陷。
**还有一件事必须记住**：收口后自查发现前端 `SignalListPanel.tsx` 在 `13:21:41Z` 被别人改过，
所以要求 1 只能说「后端通过、前端 1 个文件不符」，不能说整体通过。

## 六条要求逐条回执

| 工单要点 | 回执 |
|---|---|
| 一、运行版本 = 07-R1 交付版本 | **后端通过；前端 1 个文件不符**（不是整体通过）。以 `13:29:40Z` 那次核对为准（`out/22_version_check_afterfix.json`，`ok=false`）：**通过项**——`adapter.py` `6218416f…`、`signalsState.ts` `f2d40893…`、`useMarketData.ts` `b1289ce6…`、`App.tsx` `07aefc6b…` 四个交付文件 sha 逐个相同；后端文件 mtime 全部早于进程启动（API `38655` 11:46:35Z / Runtime `38664` 11:46:38Z，落后文件 0 个）；守卫文件三个 sha 未变。**唯一失败项**——`SignalListPanel.tsx` `546fdab0…` ≠ 交付 `bf8eb3ce…`（mtime `13:21:41Z`、+865 字节，外部改动，见下节）。三条路径实现文件（`live_signal_engine.py` / `unified_runtime.py` / `live_api.py`）已立为本单基线 |
| 二、跨 ≥2 根新增小时线逐币记录 | **通过**。见下「逐根记录」表；逐币明细在 `out/50_sample_analysis.json`、条件与门槛在 `out/60_changed_symbols_bar*.md` |
| 三、同段行情隔离回放对照 | **通过**。`out/35_iso_replay_bar1_final.json`：526 币逐币 **0 差异**、候选 0/0、丢弃明细 51/51 逐条相同、漏斗合计相同、水位相同、生产文件未动 |
| 四、三类缺陷出现就修 | **三类均未出现**（判据写死在 `50_analyze_samples.py`，只在同 pid 相邻段比较）；另修掉 1 处版本一致性缺陷 |
| 五、隔离入口六项 | **通过**。`DELIVERY-隔离入口.md`：启动方法 / 端口（live 8799、recovered 8798）/ 候选 ID / 数据库位置 / 版本指纹 / 清理方法；实测就绪 3.1s / 3.4s；复用 07-R1 的 `iso_live`、`iso_recovered` 样例，**未另造候选** |
| 六、前端改动先登记 | **本单无前端改动；已登记 1 处外部改动**。本单 12:15Z 核对时四个前端文件 sha 全等于 07-R1 交付值；`13:21:41Z` `SignalListPanel.tsx` 被外部改成 `546fdab0…`，已按「改前先登记」的要求登记到 `DELIVERY-隔离入口.md` 第四节 + `out/23_frontend_drift_1321Z.json`。新增文件全在 `evidence/` 下，不参与前端构建 |

## 逐根记录（要求 2）

| 项 | 第 1 根（11:00Z） | 第 2 根（12:00Z） |
|---|---|---|
| 最新已收盘 1h | `2026-10-25T11:00:00Z`（收盘 `11:59:59Z`） | `2026-10-25T12:00:00Z`（收盘 `12:59:59Z`） |
| 采集器写入时刻 | `12:00:06Z` | `13:00:08Z` |
| 生产处理水位 1h | `10:00Z → 11:00Z`（+1 根） | `11:00Z → 12:00Z`（+1 根，第 2 根） |
| 生产处理水位 4h | `04:00Z → 08:00Z`（+1 根） | 仍 `08:00Z`（下一根 4h 是 `12:00Z` 开盘，要到 `16:00Z` 才收盘） |
| 逐币落后 | 526 币 `lag_1h=0` 全 | 526 币 `lag_1h=0` 全 |
| 阶段分布 | `192/300/16/18 → 171/314/24/17` | `171/314/24/17 → 172/313/25/16` |
| 换阶段币数 | 46（`IDLE→BOX` 26、`SWEEP→RETEST` 8、`BOX→SWEEP` 7、`BOX→IDLE` 5） | 3（`SWEEP→RETEST` 1、`SWEEP→IDLE` 1、`BOX→SWEEP` 1） |
| 迁移↔漏斗对撞 | 全部一致（BOX 26/26、SWEEP 7/7、RETEST 8/8、终止 5 = EXPIRED 2 + INVALIDATED 3） | 全部一致（BOX 0/0、SWEEP 1/1、RETEST 1/1、终止 1 = INVALIDATED 1） |
| 新候选 | 0 | 0 |
| 入库 | 0 行 | 0 行 |
| 接口 | current 0 / all 0 / history 0，`status=ok`、`missing_reason=null` | current 0 / all 0 / history 0，`status=ok`、`missing_reason=null` |
| 未进下一环原因 | 冷却 137 / 箱体条件未达 26 / 等 4h 收盘 8 / 数据质量阻断 6 / 扫描错误 0 | 冷却 136 / 箱体条件未达 27 / 等 4h 收盘 9 / 数据质量阻断 6 / 扫描错误 0 |

## 本单修掉的缺陷

| # | 缺陷 | 性质 | 证据 |
|---|---|---|---|
| 1 | `adapter.py` mtime `11:05:00Z` 晚于 API 进程启动 `11:03:51Z` ⇒ 文件改了但进程还跑旧代码 | **真缺陷**（版本一致性） | 修复前 `out/21_pre_restart_snapshot.json`；受控重启 API `32935→38655`、Worker `32940→38660`、Runtime `32944→38664`，采集器 `42278` 未动；重启后 `state_counter` 逐项相同 |
| 2 | 漏斗拿 `history` 单边比，误报「回放不一致」 | 探针口径错误 | 生产 `history` 是预热回放期计数（重启即冻住），增量在 `since_start`；差值恰好等于 `since_start`。改比 `history+since_start` 后 `funnel_delta` 为空 |
| 3 | 箱体字段照 `CandidateSnapshot` 的名字取（`box_atr_a`/`box_er`），全 null | 探针口径错误 | `EventDossier.box` 是 `contracts.BoxVersion`，真名 `high_h`/`low_l`/`atr_a`/`efficiency_ratio_er`/`box_width_atr` |
| 4 | 只取 `active_event`，`BOX→IDLE` 的 5 个币查不到终止原因 | 探针缺项 | 终止原因在 `sm.completed_events[-1].termination_reason`；补 `last_terminated_event` 后可读到「跌破L-0.5A或连续2根收盘破L」「建档后7天内未发生扫低」 |

## 二次自查：修掉本单新写探针自身的 6 个缺陷

收口后把 6 个探针逐个自查了一遍。**口径错是一类，代码错是另一类**，两类都会制造假缺陷：

| # | 缺陷 | 位置 | 危害 | 修法 |
|---|---|---|---|---|
| N1 | 完全没有参数解析，`--out` 静默忽略 | `20_version_check.py` | 传了路径以为换了位置，实际写死并**覆盖上一次核对结果** ⇒「结论绑定时刻」在工具层失效（本单 12:15 那次把 11:50 那次冲掉了） | 加 `argparse`；写前按旧文件 `generated_real_utc` 归档成 `*_archived_<时刻>.json` |
| N2 | 同样没有参数解析 | `50_analyze_samples.py` | 同上（本单恰好传默认名才没暴露） | 加 `--in` / `--out` |
| N3 | 跨多根时「迁移↔漏斗对撞」必然假报「不一致」 | `50_analyze_samples.py` | 逐币阶段是首尾快照差；一段推进 2 根时中间迁移被吃掉，漏斗两笔都记 ⇒ 误报「计数器与状态机脱节」 | 只在 `advance_bars == 1` 时判，否则报「不可判」+ 原因；`70_verify_readme.py` 同步同一纪律 |
| N4 | 成交量用 `round(x, 2)` | `30_iso_replay.py` | 2.8e8 量级浮点 ulp ≈ 6e-8，round 无效，输出 `288483969.60000002`，看着像算错 | 取整到个位 |
| N5 | 大数用 `:.8f` 展示 | `60_changed_symbols.py` | 同上长尾 | ≥1000 改千分位 2 位 |
| N6 | 重复赋值 `d = item["delta_vs_prev"]` | `50_analyze_samples.py` | 无害，但掩盖「异常判定前必须先绑定」的意图 | 删掉后面那个 |

**N3 有自测**（`out/71_collision_guard_selftest.json`）：拿真实采样故意丢掉 `11:00Z` 整段，造出「一段推进 2 根」。
修复前算出 `SWEEP 逐币 6 vs 漏斗 8` 判「不一致」——**纯假缺陷**（那 2 个币在中间那根进了 SWEEP 又走了）。
修复后输出「不可判（本段推进 2 根 1h，逐币首尾差无法还原中间迁移 ⇒ 不做对撞）」。

**N1 已验证**：`--out` 生效，且目标已存在时先归档，历史不再被覆盖。

**N4/N5 不影响已产出结论**：`37_iso_replay_bar2_fixed.json` 用修好的脚本复跑，与旧产物 `36_` 的对照结论**逐项相同**
（`70_verify_readme.py` 有这条断言）。第一根 `35_` 没复跑——生产水位已到 `12:00Z`，再回放到 `11:59:59Z`
就不是同水位对照，硬跑只会得到 `watermark_equal=false` 的假失败；它的成交量 `949331.36` 是 6 位有效数字、无长尾，不受影响。

## 收口后自查暴露的第三方改动（要求 6 的另一半）

修完 N1（`20_version_check.py` 加参数解析）后顺手复跑，第 ① 层报 FAIL —— 这不是探针的错，是真漂移：

| 项 | 内容 |
|---|---|
| 文件 | `UI界面/web/src/components/SignalListPanel.tsx` |
| 时间线 | 本单 12:15Z 核对时仍是 `bf8eb3ce…`（= 交付值）⇒ 变化发生在本单开工之后 |
| 当前 | sha256 `546fdab0d66e98c0…`、`47,253` 字节、mtime `2026-10-25T13:21:41Z`（+865 字节） |
| 改了什么 | `git diff` 新增：模拟盘启停切换（`onToggleGate`/`isGateRunning`/`Play`/`Pause`）、清理信号库（`onClearSignals`/`Trash2`）、`ExecutionDaemonView` 类型 ⇒ **新增功能，不是格式化/工具写回** |
| 谁改的 | **不是本单**。本单从头到尾没碰前端；判断为并行任务（反重力）在同一时段修改 |
| 影响面 | 要求 1 对该文件**不再成立**（后端 4 个文件仍成立）；要求 2/3/4 全是后端行为，**不受影响** |
| 本单动作 | ①登记 `DELIVERY` 第四节；②改要求 1 结论；③明细落 `out/23_frontend_drift_1321Z.json`；④**不回滚、不改动**——别人的在途工作 |
| 给反重力的提醒 | 若前端测试跑在 `13:21:41Z` 之前，结论对应旧版本，需重跑；vite HMR 让新代码立即生效，页面不用重启 |

## 报告数字怎么核的（不靠人眼）

`probes/70_verify_readme.py` 做**双向对撞**：每个数字回一手字段重算（`50_sample_analysis.json` /
`37_iso_replay_bar2_fixed.json` / `36_iso_replay_bar2_final.json` / `22_version_check_afterfix.json` / `90_regression.txt`），
再反过来检查正文里写到的数字是否都能在复算结果里找到、复算出的关键数字是否都在正文里出现。
**20 项断言全过**，结果 `out/70_verify_readme.json`。
其中三条专盯版本一致性：①后端交付文件全等；②版本核对失败项**只有**前端 `SignalListPanel.tsx` 一个；
③漂移（交付 sha / 当前 sha / `13:21:41Z`）三份文档合计必须登记到。
另有一条**反向禁写**：正文不许再出现「交付版本 | **通过**」这种整体结论——那与 `13:29Z` 的核对结果矛盾。
迁移↔漏斗的对撞是**在脚本里重算的**，不是采信 JSON 里的 `all_equal` 字段。

## 必须记住的硬结论

1. **「漏斗不一致」先怀疑漏斗口径，别先怀疑生产。** `funnel.history` 是**预热回放期**计数，进程重启那一刻就冻住，之后不再增长；新增小时线的迁移记在 `funnel.since_start`。要跟「一次性回放到同一水位的回放」比，必须比 `history + since_start`。只比 `history` 会恰好差出一根线的迁移量（本单实测 BOX 差 26、SWEEP 差 7、RETEST 差 8、INVALIDATED 差 3、EXPIRED 差 2），看起来像缺陷，实际是**生产在正确记账**。

2. **`EventDossier.box` 是 `BoxVersion`，字段名和 `CandidateSnapshot` 不一样。** 取箱体条件要用 `high_h`/`low_l`/`atr_a`/`efficiency_ratio_er`/`box_width_atr`；用候选快照的 `box_high_h`/`box_atr_a` 只会得到一片 null，然后你会得出「条件值取不到」的错误结论。

3. **事件终止后 `active_event` 会被清空，原因只剩在 `completed_events` 里。** 只看 `active_event`，`BOX→IDLE` 这类迁移永远只有「无事件在跟踪」，答不出「被拒绝的具体原因」。

4. **「迁移 ↔ 漏斗」必须自动对撞，不能人眼看。** 漏斗说「进了几次某状态」，逐币说「谁从哪到哪」，两边必须一一对上。本单把它写成 `migration_vs_funnel`，每根线自动核；本根 4 项全对（26/26、7/7、8/8、5=2+3）。

5. **「生产 0 候选」与「回放 0 候选」是两条独立路径，必须两边都是 0 且原因相同才算正常。** 生产为 0 本身什么也证明不了——可能是被吃掉、可能是没处理到。本单同水位回放 526 币逐币零差异、丢弃明细 51 条逐条相同，才能说「是规则没触发」。

6. **接口的 0 要分「可信的 0」和「故障被显示成 0」。** 判据是 `quality.status` 与 `missing_reason`，**不是** `quality.note`（恒有值）。本单三个 scope 全 `status=ok` + `missing_reason=null`，所以这个 0 可信。

7. **「版本一致」这句话有保质期，而且必须逐文件说。** 本单 `11:50Z` 与 `12:15Z` 两次核对都是 5 个文件全等，`13:29Z` 第三次就有一个前端文件被外部改掉了。**一次核对不能宣布永久成立**；也不能说「版本一致」而不说清是哪些文件、在哪个时刻。工具层还要能保留历史（N1 那个「静默覆盖」的坑，正是把「结论绑定时刻」变成空话的东西）。

## 回归

全量 **15 失败 / 364 通过 / 79 跳过**（`out/90_regression.txt`，100.86s），与 37/38 号实测**逐项相同**，
失败项集合也相同（`test_system_isolation_03/04/05`、`test_system_startup_01`，原因是 `worker_already_running`
与端口占用——本单要求保留生产链运行，这些用例要独占 jobs 队列与端口）⇒ **本单零新增失败**。

本单**没有改动任何生产代码**（只在 `evidence/` 下新增探针），所以回归只作「没搞坏东西」的证据。
跑完后生产未受影响：API pid `38655` 仍在监听 8787，水位仍 `12:00Z`、`universe=526`、`scan_errors=0`。

## 遗留与下一步

- **未验证**：生产「有自然候选时能显示」。本窗口自然候选 0，没有可显示的样本。
- **未验证**：真实挂钟自动成交（沿用 06/07-R1 结论）。
- **观察（不结论）**：本窗口 2 根线共 46 次阶段迁移、0 个候选，属正常漏斗损耗（26 个新建档 → 7 个扫损 → 8 个回踩 → 0 个突破确认）。样本量不足以推断规则偏严。
- **不得引用**：`out/31_iso_replay_dryrun*.json` 的 `funnel_history_equal=False` 是**探针口径错误**的产物，结论以 `out/35_iso_replay_bar1_final.json` 的 `funnel_total_equal=true` 为准。
