# ACCUM-EARLY-06-W2 / 02 —— 修正版本交付与原输入回归（06-04）

> 一句话结论：**原 05-E 的 7 个事件、两段行情范围、同一份测试配置下，修前 `d96ad60e…` 与修后 `3c570edc…` 的 7 条事件轨迹逐字段一致（6 条 `INVALIDATED` + 1 条 `NOT_FOUND`）；修正的真实影响落在原清单之外的 SOLUSDT 第二个箱体上——修前出 2 条候选、修后 0 条，机制是承接判定从「确认根」换到「扫低根」。本单未开仓、未成交、不改共享文件、不宣称策略有效。**

- 目录：`evidence/ACCUM-EARLY-06-W2/02/`（脚本 `scripts/`、产物 `out/`、配置 `config/`、运行目录 `runs/`）
- 上一单：`evidence/ACCUM-EARLY-06-W2/01/`（06-03 承接判定时点修正，本单依赖它）
- 生产代码唯一改动仍只有 `框架/backend/accum_early_entry.py`（06-03 改的；**本单没再改**，`mtime` 早于本单开始）

---

## 1. 逐要求回执

| # | 派工要求 | 状态 | 证据 |
|---|---|---|---|
| 1 | 依赖 06-03 完成；保留 05-E 原 7 事件、行情范围与测试配置，除显式列明的接口适配外不换输入、不找新案例替代 | **完成** | `out/00_inputs_and_versions.json`（I1 事件 7/7 同序、I2 范围逐字相同、I3 配置逐字节相同、I4 三版身份）；接口适配只有 3 条（见 §2） |
| 2 | 逐事件输出修前修后轨迹、候选数、失效原因；允许合法结果为零候选，原本未找到的事件不能写成已失效 | **完成** | `out/02_event_compare.json` A 段（7 条逐条）、A2 段（`NOT_FOUND` 归因）、C 段（候选 2→0 机制）、D 段（归因钉死到承接、不记到压制位） |
| 3 | 保留批准闸门、重复数据缺口去重、经济口径冲突阻断等已有保护；必要回归复用 05-B 脚本 | **完成** | `out/03_protection_regression.json`：R1 原样调用 05-B `04_配置闸门.py` 11/11；R2 原样调用 05-B `07_经济口径一致性校验.py` 5/5；R3 去重函数级 + 端到端 |
| 4 | 交付冻结代码哈希、测试配置、差异说明和调用接口给窗口五、六 | **完成** | `out/04_handoff.json`、`out/04_code_diff.patch` |
| 5 | 不得再改共享文件而不通知下游；不宣称自然成交或策略有效 | **完成** | R4：10 个共享文件 `mtime` 全部早于本日 00:00Z；R5：生产账本目录无研究臂文件；`open_candidates=False` ⇒ 无账本 |

---

## 2. 输入未换（I1–I4），以及**唯一的接口适配**

`scripts/00_输入与版本核对.py`，四项全部回读原始文件，不靠记忆：

| 组 | 内容 | 结果 |
|---|---|---|
| I1 | 从 `05-E/out/03_event_trace.json` 回读 7 个事件号，与固化清单**逐条同序**比对 | 7/7 相同 |
| I2 | 用正则从 `05-E-R1/scripts/02_retest_original_events.py` 源码抽出时间区间 / `max_boxes` / `warmup_days`，与本单 `RUNS_SPEC` 比对 | 逐字相同 |
| I3 | 测试配置 `config/fixture_rules_05.json` 与 05-B 原文件**逐字节相同**，sha = `f322a940cea1e69d6ebe048f100b1e0b3b8234c1d42ef45a1f9b25bfd55152bd` | 相同 |
| I4 | 修前 `d96ad60eca61f7e53e273414196100ac21b33650a19480beafb0eb60c4d41a52`（113875 字节）、修后 `3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017`（117711 字节） | 与期望一致 |

两段行情范围（与 05-E-R1 逐字相同）：

| 标的 | 起 | 止 | `max_boxes` | `warmup_days` | 实际装载 |
|---|---|---|---|---|---|
| SOLUSDT | 2026-08-01T00:00:00Z | 2026-09-05T00:00:00Z | 2 | 60 | 4h **570** 根 / 1h **2280** 根 |
| AVAXUSDT | 2026-09-12T00:00:00Z | 2026-10-25T00:00:00Z | 2 | 60 | 4h **618** 根 / 1h **2472** 根 |

**接口适配（显式列明，只有 3 条，都不碰规则/阈值/状态机）**：

1. 修前代码作为**独立模块**加载（模块名 `accum_early_entry_pre06`），不覆盖在用的 `accum_early_entry`；
2. 只改它 4 个模块级路径常量（`PROJECT` / `RESEARCH_ROOT` / `DEFAULT_CONFIG_PATH` / `OBSERVE_DB`）——因为修前文件放在 `runs/` 下，`parents[2]` 解析不到工程根；
3. 加载后立刻核 sha256 必须等于 `d96ad60e…`，否则拒绝运行（`_w2b.load_pre_fix`）。

修前文件来源：06-03 的 `01/out/04_pre_fix_reconstructed.py`（把 5 处编辑逆向替换回去后，sha256 逐位等于修前值），本单复制到 `runs/pre_fix_accum_early_entry.py`，sha 仍为 `d96ad60e…`。

---

## 3. 同输入双版本回放（扫描级）

`scripts/01_双版本回放.py` → `out/01_pre_run.json` / `out/01_post_run.json` / `out/01_replay_summary.json`。
两个版本各跑一遍 `scan_symbol`（单次时间推进，与生产同一入口），`open_candidates=False`。

| 指标 | 修前 `d96ad60e` | 修后 `3c570edc` |
|---|---|---|
| 建档事件数 | 12 | 12 |
| 迁移条数 | 38 | 36 |
| 候选条数 | **2** | **0** |
| 缺口行数 | 2 | 0 |
| 终态分布 | `INVALIDATED` 9 / `EXPIRED` 1 / `RETEST` 1 / `SWEEP` 1 | `INVALIDATED` 12 |
| SOLUSDT `boxes_formed` / `boxes_used` / `actions` | 146 / 2 / 14 | 146 / 2 / 12 |
| AVAXUSDT `boxes_formed` / `boxes_used` / `actions` | 195 / 2 / 12 | 195 / 2 / 12 |
| 配置 `ready` / `open_scope` | true / `fixture_chain_selfcheck` | true / `fixture_chain_selfcheck` |

`same_config` / `same_ready` / `same_open_scope` 三项均为 true ⇒ 两版本的**配置闸门状态完全相同**，差异不是配置造成的。

---

## 4. 原 7 事件逐条（A 段）

`out/02_event_compare.json` → `A_original_7_events`。`identical` 的判据是「迁移序列（from/to/at/reason）+ 终态 + 失效原因 + 候选数」**逐字段**相等，不是「看起来差不多」。

| # | 方案 | 标的 | 箱体建档时刻 | 修前终态 | 修后终态 | 候选 前→后 | 对比 |
|---|---|---|---|---|---|---|---|
| 1 | 4a | SOLUSDT | 2026-08-01T03:59:59Z | `INVALIDATED` | `INVALIDATED` | 0 → 0 | 一致 |
| 2 | 4b | SOLUSDT | 2026-08-01T03:59:59Z | `INVALIDATED` | `INVALIDATED` | 0 → 0 | 一致 |
| 3 | 4c | SOLUSDT | 2026-08-01T03:59:59Z | `INVALIDATED` | `INVALIDATED` | 0 → 0 | 一致 |
| 4 | 4a | AVAXUSDT | 2026-09-12T03:59:59Z | `INVALIDATED` | `INVALIDATED` | 0 → 0 | 一致 |
| 5 | 4b | AVAXUSDT | 2026-09-12T03:59:59Z | `INVALIDATED` | `INVALIDATED` | 0 → 0 | 一致 |
| 6 | 4c | AVAXUSDT | 2026-09-12T03:59:59Z | `INVALIDATED` | `INVALIDATED` | 0 → 0 | 一致 |
| 7 | 4a | AVAXUSDT | 2026-09-15T11:59:59Z | `NOT_FOUND` | `NOT_FOUND` | 0 → 0 | 一致 |

前 6 条的失效原因完全相同：`连续 2 根 1h 收盘跌破箱体下沿 72.26 ⇒ 失效`（SOLUSDT）/ `连续 2 根 1h 收盘跌破箱体下沿 7.27 ⇒ 失效`（AVAXUSDT 第 1 条箱体）、`NO_RESISTANCE:too_close_to_H`（AVAXUSDT 第 2 条箱体）。

**为什么零差异是合理结果，不是「没生效」**：这 6 条事件都在 `SWEEP` 阶段就因「连续 2 根收盘跌破下沿」失效，**从未走到确认根**；06-03 改的是「确认时承接读哪一根」，这些事件根本没进入确认分支，所以修前修后必然一致。真正的差异要到「会走到确认」的箱体上才出现（见 §7、§8）。

**合法结果为零候选**：修后在这份输入上产出 **0** 条候选。这不是「跑挂了」——12 条事件全部正常建档、正常终结，只是没有一条满足确认条件。

---

## 5. 第 7 条事件：`NOT_FOUND` 的归因（不是「已失效」）

`out/02_event_compare.json` → `A2_not_found_attribution`。两版本各算一遍归因，结论相同（`same_verdict=true`）。

| 检查项 | 结果 |
|---|---|
| 箱体是否存在于 `build_boxes` | 是（`box_row_exists=true`） |
| 箱体是否达标 | 是（`formed=true`，`width_atr=6.465870233608587`，`er=0.2542669584245077`，`atr=0.13362470460806056`） |
| 是否被同方案前序事件占位 | **是**：`ev_4a_binance_AVAXUSDT_box_AVAXUSDT_1789185599999_…` 从 2026-09-12T03:59:59Z 活到 2026-09-16T00:59:59Z，覆盖了本箱体建档时刻 2026-09-15T11:59:59Z |
| 箱体配额 | `boxes_used=2` / `max_boxes=2`（已用满） |
| 归因结论 | **箱体达标但被同方案前序事件占位（当根三个方案都不空闲 ⇒ 未 spawn）** |

**标签纪律**：`NOT_FOUND` 只表示「本次运行没有按原身份建档」，**不等于** `INVALIDATED`。本单在 `out/02_event_compare.json` 里对该事件写 `"is_invalidated": false`，正文任何地方都没有把它写成失效。

---

## 6. 第三方对照：05-E-R1（版本 `f7056980`，与本单两版都不同）

`out/02_event_compare.json` → `A3_third_party_05E_R1`。05-E-R1 的结论产生在 `f7056980e0cef0cac7be31ee76fee19357ce33b7704b37add8dc4de83816c8c6` 上，该版本文件已不存在，**本单不重跑**，只读它已落盘的 `out/02_original_events_retest.json` 作对照列。

| 事件 | 05-E-R1 终态 | 修前终态 | 修后终态 | 终态三方一致 | 迁移序列三方一致 |
|---|---|---|---|---|---|
| 7 条（逐条） | 6 `INVALIDATED` + 1 `NOT_FOUND` | 同 | 同 | **7/7** | **7/7** |

**必须标注的限制**：这是三个**不同版本**上的巧合一致，不能当成「同输入同版本对照」。它能说明的是「这 7 条事件的命运在三次代码演进里没变」，不能说明「修正无效」——原因见 §4（这些事件没走到确认分支）。

---

## 7. 范围外差异（B 段）：影响面到底落在哪

`out/02_event_compare.json` → `B_out_of_scope_diff`。这些事件**不属于** 05-E 原 7 事件清单，它们是同一段行情、同一份配置下 `scan_symbol` 顺带建出来的箱体（SOLUSDT 第二个箱体，建档时刻 2026-08-01T19:59:59Z）。列出来只为说明影响面，**不作为原 7 事件的结论，也不替代原输入**。

| 方案 | 修前终态 | 修后终态 | 候选 前→后 | 关键差异 |
|---|---|---|---|---|
| 4a | `SWEEP`（停在扫低，未终结） | `INVALIDATED`（2026-08-09T20:59:59Z，`NO_RESISTANCE:too_close_to_H`） | **1 → 0** | 修前 2026-08-08T05:59:59Z 生成候选；修后不确认，继续观察到结构不合格 |
| 4b | `RETEST`（2026-08-08T05:59:59Z 进 RETEST，之后停住） | `INVALIDATED`（同上） | **1 → 0** | 修前 2026-08-08T06:59:59Z 生成候选；修后连 RETEST 都没进 |
| 4c | `EXPIRED`（2026-08-10T06:59:59Z，`丙：回抽等待超期`） | `INVALIDATED`（同上） | 0 → 0 | 修前进了 RETEST 才超期；修后不确认，直接在不合格处终结 |

修前 `EXPIRED` 1 条、`RETEST` 1 条、`SWEEP` 1 条 ⇒ 修后全部变成 `INVALIDATED`，这就是终态分布从 4 类收敛成 1 类的原因。

---

## 8. 候选 2 → 0 的机制证据（C 段）

`out/02_event_compare.json` → `C_candidate_mechanism`。两条数据都取自**两版本自己写进库里的记录**，不是本脚本重算：

- 修前：`accum05_candidate.conditions_json`（4a）/ `observe_transition.payload_json`（4b 的「进 RETEST」那条，因为乙的候选在 RETEST 之后才落库）；
- 修后：`observe_transition.payload_json`（进 `SWEEP` 时求值的那一份）。

**同一个箱体、同一个承接规则、两根不同的 bar**：

| 判据 | 修前（确认根 2026-08-08T05:59:59Z） | 修后（扫低根 2026-08-03T03:59:59Z） |
|---|---|---|
| 买占比 | 0.5053721277 ≥ 基准 0.4954198579 × 1.0 ⇒ **pass** | 0.4202349981761731 < 基准 0.5041088746024686 × 1.0 ⇒ **fail** |
| 收盘在上半区 | 74.64 ≥ 74.59 ⇒ **pass** | 72.85 < 72.855 ⇒ **fail** |
| 卖压额 | 15341824.2882 ≤ 15619522.64 ⇒ **pass** | 17132168.3386 > 10927109.81 ⇒ **fail** |
| 结论 | 三项全过 ⇒ 出候选 | 三项全不过 ⇒ 不确认 |

修后的基准窗口：`from_ms=1785654000000` → `to_ms=1785725999999`，`n=20`；右端 = 扫低根**前一根**的收盘（`bar_open_ms=1785726000000`，`bar_close_ms=1785729599999`），与 06-03 定下的口径一致。

**所以「修后 0 候选」不是「更严格」，而是「换了判定 bar」**：修前把确认根的承接值拿来当扫低根的用（正是 06-03 修掉的偷换），修后扫低根承接不达标 ⇒ 不确认、继续观察，随后结构转为不合格 ⇒ `NO_RESISTANCE`。

### 8.1 归因钉死：这条差异**不能**记到压制位头上

06-01 已判定 `_resistance` 传确认根是**另一个未修缺陷**（C-04），并明确要求「06-04 回归时 `_resistance` 造成的差异不得算成承接修正的效果」。所以本单用**修后代码自己的函数**在修前确认的那一根 bar（`2026-08-08T05:59:59Z`，1h 下标 1613）上把四项全算了一遍：

| 判据 | 修后代码在**确认根**上的结果 | 是否成立 |
|---|---|---|
| 局部压制位 | 74.06（`swing_high_k3_lookback48`，窗口 48 根） | 有值 |
| 压制位合格性 | 合格（`why=ok`，合格区间 70.97159716364895 ~ 77.01840283635106，`min_gap_atr=0.5`，ATR 0.9231943272978799） | **成立** |
| 突破压制位 | 收盘 74.64 > 74.06（`1h_close` 模式，`buffer_atr` 为 0） | **成立** |
| 阳线收盘 | 74.64 ≥ 开盘 74.61 | **成立** |
| 承接（确认根） | `ok=true`（买占比 0.5053721276710232 ≥ 0.49541985793468124） | **成立** |
| 承接（扫低根 1491） | `ok=false`（买占比 0.4202349981761731 < 0.5041088746024686） | **不成立** |

结论：`divergence_attributable_to_absorption=true` —— 除「承接判定读哪一根」之外，其余四项在修后代码下**都成立**；`resistance_unchanged_between_versions=true`（`_resistance` 函数源码两版本逐字相同）。因此「修前出 2 条候选、修后 0 条」只能记在承接判定的 bar 上，**与 `_resistance`（C-04）无关**。C-04 仍是未修缺陷，本单没动它。

---

## 9. 已有保护回归（R1–R5）

`scripts/03_保护回归.py` → `out/03_protection_regression.json`。**每条都给正向对照**，避免「拦住了」其实是恒真。

| 编号 | 保护 | 做法 | 结果 |
|---|---|---|---|
| R1 | 批准闸门 | **原样调用** 05-B `04_配置闸门.py`（只把落盘目录指到本窗，断言一行不改） | **11/11** 用例通过：N1 缺文件 / N2 空白表单（缺 48）/ N3 半填 / N4 格式版本 / N5 未批准 / N6 未开开关 / N7 取值未实现 / N8 经济口径冲突 / N9 语义被改 全部拦住，P1/P2 放行；`required_fields=49`；`open_probe`（阻塞时 `paper_engine` 调用 0 次、放行时 1 次）通过 |
| R2 | 经济口径冲突阻断 | **原样调用** 05-B `07_经济口径一致性校验.py` | **5/5** 通过：一致配置放行且真调 `paper_engine`；滑点 / 风控 / 参与率三个维度各自不一致时 `open_allowed=false` 且**不调** `paper_engine`；缺配置可读数据可记录但不开仓 |
| R3 | 重复数据缺口去重 | `add_gap_once` 函数级 + 同事件重跑端到端 | 函数级：三次同键返回 `[true, false, false]`、表里**1 行**；换 `to_ms` ⇒ 真新增（**2 行**，证明不是恒假）；绕过包装直接 `add_data_gap` 三次 ⇒ **3 行**（证明去重来自调用侧）。端到端：同一事件连跑两轮，缺口行数 **1 → 1**（不增长），候选 **1 → 1**（第 2 轮命中既有候选 ⇒ `dedup`，不再落库） |
| R4 | 共享文件未动 | 10 个文件的 sha256 + mtime | **10/10** mtime 早于本日 00:00Z：冻结包 `state_machine.py` 2026-10-26T10:29:24Z / `config.py` 2026-10-26T05:45:20Z、`paper_engine.py` 13:23:40Z、`observe_store.py` 09:24:54Z、`research_15m.py` 13:04:44Z、`replay_calc.py` 14:11:50Z、`live_store.py` 14:14:48Z、`realtime_collector.py` 2026-10-18T05:59:38Z、`ObservePanel.tsx` 11:47:15Z、05-B 空白表单 16:33:43Z |
| R5 | 写域 | 生产账本目录 + 本窗 `runs/` 清单 | 生产 `数据/paper/` 下研究臂文件 **0** 个；本单所有观察库/账本都在 `evidence/ACCUM-EARLY-06-W2/02/runs/` |

R3 的端到端用例用**合成行情**（06-03 的合成序列），原因：本单修后在这份真实输入上 0 候选 ⇒ 不产生缺口，端到端去重在该输入上不可观测（0 行 vs 0 行不算验证）。合成用例只作「保护仍在」的正向对照，**不替代原 7 事件**。

---

## 10. 冻结交付（给窗口五、窗口六）

`scripts/04_交付冻结.py` → `out/04_handoff.json` + `out/04_code_diff.patch`。

**D1 身份**

| 对象 | sha256 | 字节 |
|---|---|---|
| 修前代码（`runs/pre_fix_accum_early_entry.py`） | `d96ad60eca61f7e53e273414196100ac21b33650a19480beafb0eb60c4d41a52` | 113875 |
| 修后代码（`框架/backend/accum_early_entry.py`） | `3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017` | 117711 |
| 测试配置（`config/fixture_rules_05.json`） | `f322a940cea1e69d6ebe048f100b1e0b3b8234c1d42ef45a1f9b25bfd55152bd` | 与 05-B 逐字节相同 |

**D2 差异说明**：`out/04_code_diff.patch`（6674 字节，**5 个 hunk，+62 / −6 行**）。AST 结构比对：顶层函数/类**新增 0 个、删除 0 个、签名改动 0 个**；`load_config` / `open_store` / `scan_symbol` / `add_gap_once` / `record_candidate` / `try_open` / `drive_all_exits` 七个交付接口**签名完全未变**。

**D3 新增 payload 键**（判据是「该键在**指定函数体**里修前不存在、修后存在」，只按全文件字面量判会误报）：

| 键 | 所在函数 | 修前 | 修后 |
|---|---|---|---|
| `sweep_bar_i` | `SchemeObserver.on_bar` | 无 | 有 |
| `sweep_scope_mode` | `SchemeObserver.on_bar` | 无 | 有 |
| `multi_bar_scope_unverified` | `SchemeObserver.on_bar` | 无 | 有 |
| `absorption_bar_close_ms` | `SchemeObserver.on_bar` | 无 | 有 |
| `absorption_baseline_window` | `SchemeObserver.on_bar` | 无 | 有 |
| `bar_open_ms` | `_absorption` | 无（别处有，本函数内无） | 有 |
| `bar_close_ms` | `_absorption` | 无 | 有 |

**D4 下游注意**（`out/04_handoff.json` → `D4_downstream`）：

- 窗口五（06-09 / 06-10）：断言 `DATA_INSUFFICIENT` 的 `reason` 全文会失效——文案已变成「承接在扫低根（`<ISO时刻>`）无法判定：`<原因>`」，请改成匹配前缀；`SWEEP` 迁移 payload 多了 6 个键，旧断言按 `payload` 全等比较会红。
- 窗口六（06-12）：修后身份 `3c570edc…` / 117711 字节，修前 `d96ad60e…` / 113875 字节；测试配置未改一个字节；本单 `open_candidates=False`，账本 0 笔。
- 共同禁区：不宣称自然成交、不宣称策略有效、不宣称开仓闸门已放行、不把 `NOT_FOUND` 写成 `INVALIDATED`。

---

## 11. 保留的失败与未验证

| 项 | 状态 | 说明 |
|---|---|---|
| 原 7 事件上的「修好了」证据 | **无** | 7 条事件在两版本上逐字段一致；它们没走到确认分支，本来就不该有差异。拿它们当修正生效的证据是无效的 |
| 修后 0 候选 | **合法结果** | 12 条事件全部正常建档/终结，只是没有一条满足确认条件；不是崩溃、不是漏读 |
| 第 7 条事件 | `NOT_FOUND` | 箱体达标但被前序事件占位；**未失效**，也不能写成已失效 |
| 自然行情成交 | **未验证** | `open_candidates=False`，无账本、无订单、无成交 |
| 策略绩效 | **未验证** | 本单不产出绩效，不评价策略好坏 |
| 冷档 `available_at` | **未验证（PIT）** | 冷档 `available_at` 填充率 0；修后 0 候选所以没写缺口，但 4a 修前候选的 `entry_bar_available_at_proven` 是 `not_proven` —— 只能声称「计算不超前」，不能证明「当时可得」 |
| 05-E-R1 三方一致 | **巧合一致，非对照** | 三个版本不同，`f7056980` 已不存在，无法重跑 |
| `multi_bar` 扫低根定义 | **待裁决（06-03 遗留）** | 多根扫低时「扫低发生根」取最低价那根、并列取最早；`sweep_scope_mode` / `multi_bar_scope_unverified` 已落库标记，规则依据仍待批 |
| `_resistance` 传确认根（C-04） | **未修（本单不碰）** | 06-01 判定的另一个缺陷（扫低根相对 103.9 vs 确认根相对 105.0）；本单 §8.1 已证明它**不参与**本次差异，仍是未修缺陷 |

---

## 12. 运行命令与复跑

```bash
cd "/Users/boos/Desktop/威科夫因子共振趋势"
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python

$PY evidence/ACCUM-EARLY-06-W2/02/scripts/00_输入与版本核对.py   # 输入未换 + 三版身份
$PY evidence/ACCUM-EARLY-06-W2/02/scripts/01_双版本回放.py       # 两版本各跑一遍 scan_symbol
$PY evidence/ACCUM-EARLY-06-W2/02/scripts/02_逐事件对比.py       # 7 事件逐条 + NOT_FOUND 归因 + 机制
$PY evidence/ACCUM-EARLY-06-W2/02/scripts/03_保护回归.py         # R1–R5（内部会起子进程跑 05-B 脚本）
$PY evidence/ACCUM-EARLY-06-W2/02/scripts/04_交付冻结.py         # 身份 / diff / 接口 / 下游
$PY evidence/ACCUM-EARLY-06-W2/02/scripts/05_正文对撞.py         # 本文数字 ↔ 产物 双向对撞
```

执行顺序即编号顺序（`00 → 05`）。`03` 内部通过 `_regr_runner.py` 以子进程方式原样调用 05-B 的两个脚本，只把落盘目录指到本窗。

**隔离**：`PAPER_LEDGER_DIR` / `LIVE_DB` / `PAPER_GATE_FILE` 全部指到 `runs/<tag>/`；行情库只读；`WX_MARKET_MERGE=1`；`ACCUM05_FIXTURE_OK=1`（否则 fixture 配置按设计阻塞）。

---

## 13. 交给下游

| 下游 | 交什么 | 注意 |
|---|---|---|
| **窗口五**（06-09 验收用例与当前缺陷冻结 / 06-10） | `out/01_pre_run.json`、`out/01_post_run.json`（两版本全量轨迹）、`out/02_event_compare.json`（逐事件 + 归因 + 机制）、`out/03_protection_regression.json`、`out/04_code_diff.patch`、`runs/pre_fix_accum_early_entry.py`（可独立 import） | 原 7 事件零差异 ⇒ 不能当「已修好」的用例；`DATA_INSUFFICIENT` 文案已变；`SWEEP` payload 多 6 键 |
| **窗口六**（06-12） | `out/04_handoff.json`（身份 + 差异 + 接口 + 下游清单）、`out/04_code_diff.patch`、`config/fixture_rules_05.json` | 修后 `3c570edc…` / 117711 字节；配置未改；未开仓 |

---

## 14. 取证缺陷自曝（本单自己踩到的）

1. **`_regr_runner.py` 第一次跑 07 号脚本失败**：只改了 `OUT_DIR` 没先 `mkdir` ⇒ `FileNotFoundError`。已补 `mkdir`，重跑通过。这条留在 `runs/05b_regression/` 的空目录里可以看出来。
2. **`_w2b.not_found_reason` 第一版把 `close_time` 当箱体时刻**：`build_boxes` 的行里没有 `close_time` 键 ⇒ 输出 `null`。已改成 `formation_ms` 并输出 `formation_utc`。
3. **`payload_keys` 第一版按全文件字面量判**：`bar_open_ms` 在修前别处就有 ⇒ 误报「不是新增」。已改成按函数体切片判。
4. **`candidate_mechanism` 第一版对 4b 取不到承接值**：乙的候选在 RETEST 之后才落库，承接值不在候选条件表里。已改为回读「进 RETEST」那条迁移的 `payload.conditions`。
5. **R3 端到端第一版判据错**：只数 `accum05_candidate`，但 `run_observer` 路径不写这张表（写它的是 `scan_symbol._reap`）⇒ 第 2 轮候选数恒为 0，断言恒假。已补 `record_candidate` 调用（与 `_reap` 同一动作）。
