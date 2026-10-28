# SIGNAL-EFFECTIVE-01-WORK｜策略生效边界与信号正常响应

- 日期：2026-10-27（北京时间）
- 执行方：Work
- 状态：**已交付，待主代理验收**（Work 自报；未经独立复核前不构成通过）
- 证据目录：`evidence/SIGNAL-EFFECTIVE-01-WORK/`
- 生产影响：**信号库 289 行数据修复已生效**；**6 处代码修复尚未生效**（未重载进程，见 §9）

---

## 0. 一句话结论

**「信号没有」不是接线断了，是两件事被混成了一件。**

第十九次更新为了让页面有信号可看，把 289 条**回测算出来的历史信号**改成了「当前可交易」的样子
（改了来源、改了可执行态、还把「可得时刻」直接填成「确认时刻」），顺手把持仓量门槛也默认关了。
页面于是有了 289 行，但这 289 行是 6 月到 10 月的回测结果，不是今天产生的信号。

本单做的是**把冒充去掉**，不是把信号造出来：289 条已回到研究身份（页面上照样能逐条看，只是不再冒充当前），
同时补上四条本该在代码里、却只写在文案里的判据。**真正让信号出不来的原因另有一条**（§5.4），
这条不是接线错误，需要你拍板。

---

## 1. 逐项状态

| 工单要求 | 状态 | 说明 |
|---|---|---|
| 三.1 只读基线快照（进程/版本/配置/持仓/候选/289 条/账本关联/哈希） | **完成** | `baseline/BASELINE.json` + 5 个一致性快照 + `uid_289.csv` + `ledger_linkage.json` |
| 三.2 复用并核验现有生效时刻 | **完成** | `out/effective_epoch.json`；查出 1 处实现与文档不符（差 9 天 8 小时） |
| 四.1 历史预热可用历史数据 | **完成** | 结构形成早于 epoch 不失效，实测无阻断 |
| 四.2 确认必须发生在边界之后 | **完成（修复后）** | 修复前有 17 条落在 9 天窗口里能穿过门禁，已修 |
| 四.3 生效前已确认不追单 | **完成（修复后）** | A/B 对照见 §5.2 |
| 四.4 迟到与回放不能伪装实时 | **完成（修复后）** | 289 条 `available_at` 已回到 `NULL`/`unproven` |
| 四.5 真实新信号正常响应 | **完成（隔离验证）** | 合法新信号正常登记挂单；**自然运行尚未出现新信号** |
| 四.6 区分规则未满足与接线错误 | **完成** | `out/rule_vs_wiring_matrix.csv` 逐层判定 |
| 四.7 误实现可最小修复 / 规则问题只列待决 | **完成** | 6 处代码修复 + 3 项待决（§9） |
| 五 共振评分与仓位倍数准入审计 | **完成（审计）** | 4 项因子**均无批准来源**；已切断对下单量的影响 |
| 六 依赖/所有权/禁止事项 | **遵守** | 零真实交易、零清账、零强平、未改冻结参数、未停采集 |
| 七 隔离验证 8 类 | **完成 8/8** | `out/iso_verify.json` + `out/iso_route_repro.json` |
| 七 自然市场行为 | **完成（结论为 0）** | `out/natural_run_evidence.json` |
| 八.1–8.9 交付物 | **完成 9/9** | 见 §10 清单 |

---

## 2. 基线快照（改动之前）

用 SQLite 在线备份 API 取**一致性快照**（含 WAL 已提交内容），不是复制主文件、也不是拿文件哈希冒充：

| 库 | 快照大小 | 完整性检查 |
|---|---|---|
| `live_signals.sqlite3` | 1,867,776 B | ok |
| `paper_3a.sqlite3` | 163,840 B | ok |
| `paper_3b.sqlite3` | 163,840 B | ok |
| `paper_3c.sqlite3` | 163,840 B | ok |
| `paper_3d.sqlite3` | 163,840 B | ok |

快照时刻、查询语句、路径、sha256 全部在 `baseline/BASELINE.json`。

**运行身份（快照时）**：runtime pid 68802 / worker 68775 / api 68764，全部 running；
臂 `3a`，标的 `all(526)`，策略 `FT-LOW4H-EXP-V2.2`，参数哈希 `82dec362c182`，
新开仓闸门 `allow_new_entries=true`（`user_ui_resumed`）。

---

## 3. 生效水位（复用现有实现，未另建时钟）

| 层 | 值 | 来源 | 作用 |
|---|---|---|---|
| `live_start_ms` | **2026-10-23T09:47:24Z** | `live_run.live_start_ms` | 展示/因果水位 |
| `paper_epoch_ms` | **2026-10-14T01:00:00Z** | `live_run.paper_epoch_ms`，代码常量逐位相等 | 用户授权的本轮启用边界 |
| `state_anchor_ms` | 2026-06-27T06:45:15Z | `live_run.state_anchor_ms` | **不是水位**，只决定状态重建起点 |
| `effective_floor_ms` | **2026-10-23T09:47:24Z** | `max(live_start, paper_epoch)` | 自动开仓 / 挂单推进 / 当前区 API 共用 |

四条核对结论：

- **E-1 一致**：代码常量 `PAPER_EPOCH_MS` 与库内持久值逐位相等，`ensure_*` 只允许不降。
- **E-2 一致**：`live_store.effective_floor_ms()` 是唯一实现，四臂/挂单/开仓/API 都引用它，**没有第二套时钟**。
- **E-3 边界口径**：库里 `close_time` 存「闭区间右端 −1ms」，比较时用 `+1` 的真实右边界，原值不改。
- **E-4 缺陷（已修）**：`open_position` 的 epoch 默认值取的是 **`paper_epoch`（10-14）**，不是
  `effective_floor`（10-23）。**两者相差 9 天 8 小时**，落在这段的信号能穿过门禁①被登记成真实挂单。
  `live_store` 的注释白纸黑字写着「四臂、挂单推进、自动/手工开仓、当前信号 API 使用同一有效下限」——
  代码没照文档做。这是**实现错接**，不是规则冲突，已按文档改回（只会更严，不会更松）。
- **E-5 未验证**：策略版本/配置变化时是否自动开新边界——代码里没有这条路径，只能人工落定。本单不改。

---

## 4. 289 条逐 UID 影响

### 4.1 账本关联：**0 条**

四本账本（`paper_pending` / `paper_order` / `paper_fill` / `paper_position` / `paper_realized` / `paper_processed`）
逐表比对：**289 个目标 UID 一个都没有出现在任何账本里**。

账本里现存的 3 条挂单、3 个持仓、13 条订单，全部是 `origin='manual'` 的手工/测试信号
（`sig_closeloop_*`、`sig_ui_test_*`），且 `voided=1` 或已平仓，与本单无关。
⇒ **没有待执行挂单需要失效，没有持仓需要处理，不需要撤单，也不需要强平。**

**决定（明示）**：**不**给这 289 条往账本里加黑名单行。理由是三重阻断已经成立
（账本零关联 + 身份已恢复 + 代码判据已补），再加 289 行 `paper_processed` 只会污染账本、
且违反「账本保留原样」的纪律。若日后发现任何一条真的进过账本，再按「保留原行 + 标记失效」处理。

### 4.2 但「离真下单只差一个臂号」——隔离复现已证实

拿同一份快照，把运行臂从 `3a` 改成 `4a`（只是让 `payload.arm` 与运行臂匹配），
**其它什么都不动**，直接调用生产同一个路由函数：

| 组 | 扫描 | 登记挂单 | 终态拒单 |
|---|---|---|---|
| arm=3a（生产实际臂） | 0 | **0** | 0 |
| arm=4a（唯一屏障被移除） | 105 | **12** | 93 |

那 12 条会在 15 分钟后按**今天的价格**成交——用 10 月 15 日到 10 月 26 日的信号。
这就是为什么本单不能只写一句「被臂号挡住了，没事」。

### 4.3 逐 UID 分类

| 分类 | 条数 | 含义 |
|---|---|---|
| 身份被改但被 epoch 挡住 | 275 | 确认时刻早于 10-14，被门禁① 终态拒单 |
| 进当前区、被臂号挡住（唯一屏障） | 14 | 确认时刻晚于 10-23，能穿过门禁① |
| 有账本关联 | 0 | — |

逐 UID 的原始身份、四个时刻、epoch 比较、账本关联、处理决定、幂等键 →
`out/uid_effect_ledger.csv`（289 行）。

---

## 5. 时序规则与信号响应

### 5.1 五个隔离层被拆了三层半

| 层 | 应有 | 改动前实际 | 判定 |
|---|---|---|---|
| L1 来源 | `historical_research` | `recovered`（289/289） | **实现错误** |
| L2 可执行态 | `historical_not_executable` | `enterable`（289/289），而同表 `executable_reason` 原文写着「只展示、不可执行」 | **自相矛盾** |
| L3 角色 | `research_only` | `enterable_arm_entry`（289/289） | **实现错误** |
| L4 可得时刻 | `NULL` + `unproven` | `= confirmed_at`（289/289），而 payload 里 `available_at_ms` 仍是 `null` | **伪造数据** |
| L5 配置批准 | `approved=false` 不得执行 | **代码里没有任何一处读 approved**，只写在文案里 | **实现缺口** |
| L6 臂号 | `payload.arm == 运行臂` | 4a/4b/4c vs 3a，不匹配 | 有效，但**是数据值不是闸门** |

L1–L4 全坏、L5 从未存在，只剩 L6 一条数据值在挡。

### 5.2 生效边界 A/B（同一条信号，两种默认值）

造一条「落在 9 天窗口里」的信号（确认时刻 2026-10-20T00:00:00Z），只改 epoch 默认值：

| 默认值 | 结果 |
|---|---|
| 旧 `paper_epoch`（10-14） | `pending_future_fill` ⇒ **登记成挂单**，15 分钟后按今天价成交 |
| 新 `effective_floor`（10-23） | `before_paper_epoch_no_backfill` ⇒ **拒单** |

这条就是工单要的「启动前确认不补单」，修复前是**不成立的**。

### 5.3 合法新信号仍然正常（链路没被修坏）

- 生效之后的新信号（确认时刻 = 现在 − 5 分钟）→ `pending_future_fill`，正常登记挂单。
- `available_at` 传 `None` → 同样正常推进，且**不再被 `confirmed_at` 顶替**。
- 同一 UID 连跑两次路由 → 不重复登记（`paper_processed` / `paper_pending` / `paper_position` 三重去重）。

### 5.4 自然运行：生效水位之后**真的没有候选**

| 项 | 值 |
|---|---|
| 运行时状态 | active，526 标的全部预热完成（ready=526） |
| 扫描水位 | 1h = **2026-10-27T14:00:00Z**；4h = 2026-10-27T08:00:00Z |
| bootstrap 回放出的历史候选 | 51 条，**全部** `before_paper_epoch_no_backfill` + `missed_fill_moment_no_backfill` |
| 重启恢复候选 | 0 |
| 已登记挂单 | **0**（`orders_routed_total=0`，`current_pending_in_db=0`） |

所以当前区 0 条是「**真的没有**」，不是「被过滤掉了」。

**为什么会没有**——这条要单独说清，因为它不是接线问题：

- FT-LOW4H 是**反转**策略（箱体 → 跌破下沿扫低 → 回踩 → 突破上沿）。最近 18 天全池 337 次回踩里，
  只有 **1 次**收盘突破箱体上沿，其余 319 次在突破前就失效了。
- 全池 121 天口径：10529 个箱体 / 2510 次扫低 / 1533 次回踩 / 65 次突破（4.2%）。
- 开持仓量门槛时 121 天 **0 个候选**；关掉门槛 56 个——那 56 个全部死在「持仓量不可用」。
  实测持仓量抓取延迟中位 **52 分钟**，而规则要求 15 分钟内可得，**99.7% 不满足**。
- 结论：**信号层的瓶颈在「持仓量数据到得太晚」和「策略本身要求高」，不在身份接线。**
  修身份不会变出信号；这条需要你决定（见 §9）。

> 上述三个数字（121 天 0 vs 56、337 次回踩 1 次突破、抓取延迟中位 52 分钟）的原始测量与复跑脚本在
> `evidence/W-002/README-SIGNAL.md` + `evidence/W-002/scripts/diag_ftlow4h.py` + `out/ftlow4h_diag.json`，
> 本单不重复跑，只引用。

---

## 6. 共振评分与仓位倍数准入审计

四项因子 + 基础分 + 分档 + 仓位倍数，逐项盘点见 `out/authorized_factor_inventory.csv`。

| 因子 | 权重/阈值 | 数据来源 | 当时可得性 | 批准来源 |
|---|---|---|---|---|
| OI 6h 增长 | −4~15 分，5%/2%/0%/−3% 四档 | `metrics_feed.read_metrics_tail` | **未取证** | **未找到** |
| 聪明钱阶段 VWAP | −4~15 分 | `metrics_feed.phase_avg_price` | **未取证** | **未找到** |
| Taker 主动买卖比 | −4~15 分 | `metrics_feed.latest_metrics` | **未取证** | **未找到** |
| 大户/散户多空比 | −4~15 分 | `metrics_feed.latest_metrics` | **未取证** | **未找到** |
| 基础形态分 | 固定 40 | 硬编码 | 不适用 | **未找到** |
| 仓位倍数 | 0.5 / 0.8 / 1.0 / 1.3 | 总分四档 | 不适用 | **未找到** |

**它实际影响到了什么**：

- 289 条历史研究行的 payload **已经带**共振分：`0.8x` 89 条、`1.0x` 200 条。
- 四项因子里 OI 289 行可得、聪明钱阶段均价 80 行可得、**Taker 与多空比 0 行可得**。
- 改动前，`paper_engine.open_position` 会把这个倍数**直接乘进风险预算**
  ⇒ 四项全缺失时算得 0.8x = **无依据缩仓 20%**。
- 本单处理：**保留计算与展示，切断对下单量的影响**——只有 payload 显式带
  `position_multiplier_approved=true` 才生效，当前一条都没有 ⇒ 恒等于 1.0（= 冻结口径 0.5%/笔）。
- 用户「所有已采集数据参与分析」的要求，**不自动批准**未说明的权重、阈值与动态仓位系数。

**缺口（如实报）**：289 条没有「逐 UID 逐因子可复算链」——payload 只存了结果
（`resonance_score` / `factor_details`），没有存输入快照与公式版本；且这批因子的读取**不是 PIT 验证过的**。
在因子获批之前，这条链不能作为决策依据。

---

## 7. 隔离验证（8/8）

全部在 `evidence/SIGNAL-EFFECTIVE-01-WORK/iso/` 的副本上做，**生产零写入**（信号库只读快照 + 独立账本目录 + 独立闸门文件）。

| # | 场景 | 结果 |
|---|---|---|
| C1a | 身份未修 + 代码已修，臂 4a | 挂单 **0**（改代码前同条件 = 12） |
| C1b | 启动前信号，新旧 epoch A/B | 旧=登记挂单 / 新=拒单 |
| C2 | 生效后合法新信号 | `pending_future_fill` 正常登记 |
| C3 | 289 条恢复身份后，臂 3a / 4a | 两臂挂单均 **0**；分组 = 289 行 `historical_research / historical_not_executable / NULL / research_only` |
| C4 | 幂等 | 第二次修复命中 0；路由连跑两次不重复登记 |
| C5 | 已有挂单不被误清 | before=1 after=1 kept=1 |
| C6 | 回滚 | 还原后回到 `recovered / enterable / available_at 非空`（217 个分组） |
| C7 | `available_at` 为空 | 不再被 `confirmed_at` 填补，仍正常推进 |
| C8 | 代码级断言（7 条） | 7/7 通过 |
| C9 | API 口径 | 身份未修：无判据 14 条冒充当前、有判据 0 条；身份已修：当前区 0、研究区 289 |

**跨进程**：每个场景都是独立子进程（环境变量先设再 import），`first_seen_at` 与 payload 哈希在多次读写后不变。

---

## 8. 修复后生产实测

| 项 | 结果 |
|---|---|
| 289 行字段 | `origin` 全 `historical_research`；`executable_state` 全 `historical_not_executable`；`payload.role` 全 `research_only`；`available_at` 全 `NULL` |
| 行数 | 289 → 289（**未删行**） |
| 不可变字段 | 逐 UID 对撞：行集合、`first_seen_at`、四个时刻、符号、策略、配置哈希 **全部未变** |
| 审计留痕 | 289 行都带 `payload.identity_repair`（前值/后值/原因/时刻） |
| 四本账本 | 逐表行数一致，**零改动** |
| 行情库 | 全程只读连接，未写入 |
| 接口（运行中进程，未重载） | `scope=current` → **0 条**，`excluded_n=289`，`not_current_origin=289`、`not_enterable=289`；`scope=all&origin=historical_research` → **289 条**，`has_more=false`，`research.approved=false`、`available_at_status=unproven` |

⇒ 页面现在会：当前区 0 行、研究折叠区 289 行（可逐条看，标明来源/角色/批准/不可执行原因）。
**这就是诚实状态**，不是「信号页坏了」。

---

## 9. 阻塞与待决（3 项）

1. **是否重载 8787，让 6 处代码修复生效。**
   现在跑的还是改动前加载的代码，所以数据修复已生效、代码修复未生效。
   当前 0 挂单、0 持仓，是重载风险最低的窗口。**需要你一句话。**
2. **OI 门槛要不要正式关闭。** 本单已还原为「默认开」（这是已批准策略参数）。
   若确实要关，属于改策略规则，需要明确批准；关了才有那 56 个历史候选，但那等于换了策略口径。
3. **共振评分与仓位倍数要不要批准。** 现状：照算、照展示、不改变下单量。批准需要给出权重/阈值/缺数据兜底。

**另有一条待你知情（不在本单范围）**：`exit.gap_model` 取 `stop_price` 已被证伪（16 笔平仓成交价高于当根 bar 最高价至少 3.58%），
`DEC-01` 仍未裁决，甲乙丙成绩仍不可引用。

---

## 10. 复跑指引与产物清单

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/01_baseline_snapshot.py   # 一致性快照
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/02_analyze.py              # 生成 4 张判据表
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/03_iso_route_repro.py      # 隔离复现「离下单只差一个臂号」
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/04_repair_signal_identity.py           # 预演
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/04_repair_signal_identity.py --apply   # 真写
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/05_iso_verify_repair.py    # 8 类隔离验证
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/06_post_verify.py          # 修复后生产核对
python3 evidence/SIGNAL-EFFECTIVE-01-WORK/scripts/07_natural_evidence.py     # 自然运行 + 共振影响
```

| 路径 | 内容 |
|---|---|
| `baseline/BASELINE.json` | 5 个快照的路径/大小/sha256/完整性 |
| `baseline/live_signals_snapshot.sqlite3` | 改动前信号库（一致性快照） |
| `baseline/live_signals_post_repair.sqlite3` | 改动后信号库（一致性快照） |
| `baseline/paper_3a–3d_snapshot.sqlite3` / `_post.sqlite3` | 四本账本前后快照 |
| `baseline/uid_289.csv` | 289 条逐 UID 原始字段 |
| `baseline/ledger_linkage.json` | UID ↔ 账本关联结果（0 条） |
| `baseline/repair_before.jsonl` | 289 行修复前完整镜像（回滚用） |
| `out/effective_epoch.json` | 生效水位逐层来源、调用链、一致性结论、定义 |
| `out/uid_effect_ledger.csv` | 289 条逐 UID 影响清单 |
| `out/rule_vs_wiring_matrix.csv` | 11 层规则 vs 实现判定 |
| `out/authorized_factor_inventory.csv` | 共振因子/仓位倍数批准来源盘点 |
| `out/iso_route_repro.json` | 隔离复现（3a vs 4a） |
| `out/iso_verify.json` | 8 类隔离验证原始结果 |
| `out/post_repair_verify.json` | 修复后生产核对 |
| `out/natural_run_evidence.json` | 自然运行证据 + 共振实际影响 |
| `out/repair_identity_*.json/csv` | 修复预演/执行/回滚记录 |
| `out/change_list.md` | 改动清单与回滚说明 |
| `logs/` | 每个隔离场景的子进程原始输出 |
