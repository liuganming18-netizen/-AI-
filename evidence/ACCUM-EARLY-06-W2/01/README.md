# ACCUM-EARLY-06-W2/01 · 06-03 承接判定时点的最小修正

- 工单来源：`docs/派工_ACCUM-EARLY-06_六窗口十二单_2026-10-27.md` §窗口二 第一单 06-03
- 工程根：`/Users/boos/Desktop/威科夫因子共振趋势`
- 状态口径：**执行方自报，未经 Codex 独立验收**。不得写成「已验收通过」。
- 判定：**工程行为通过（修前必失败／修后通过，三类用例齐全）**；**策略效果未验证**；**开仓仍被未批准参数阻塞**（本单未解除、也未绕过）。

---

## 1. 一句话结论

修前 `_absorption(...)` 唯一调用点在 `_confirm_actions` 里、传的是**确认突破根**下标 `i`；
本单把它改成「进入 SWEEP 时在**扫低发生根**求值一次，确认阶段只读这一份结果」。
五处编辑共 **+62 / −6** 行，仅限 `框架/backend/accum_early_entry.py`；
三类用例在修前全部不符预期、修后全部相符；未改任何阈值、倍率、经济口径、冷却或冻结规则。

---

## 2. 逐要求回执

| # | 工单要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | 先记录当前调用轨迹、构造失败用例 | **完成** | `out/00_basis_and_pre_fix_trace.json`（AST 静态调用点 + 运行时逐次调用轨迹）、`out/01_pre_fix_fail.json` |
| 2 | 修改依赖 06-01 的对应依据 | **部分完成（依据改由本窗直取原文）** | 06-01（`evidence/ACCUM-EARLY-06-W1/`）**未交付**，不存在可引用的上游产物；本窗改为直接读 04-A / 05-A-R1 原文并**逐行读回校验**，见 §3 |
| 3 | 核对 `_absorption` 位于 `_confirm_actions` 的情况 | **完成（确证）** | 修前 AST：`absorption_enclosing = [["_confirm_actions"]]`、`absorption_in_sweep_check = false`；运行时：三类用例承接判定下标全部为确认根 |
| 4 | 按现有方案语义使承接判断读取扫低发生根的数据 | **完成** | 修后承接判定下标 `[60]`（= 扫低根），基准窗口右端 = 扫低根前一根收盘 |
| 5 | 突破确认不得偷换为另一根的承接判断 | **完成** | `_confirm_actions` 内已无 `_absorption` 调用（AST 复核）；只剩 `self.sweep_absorption`；若该状态缺失则直接 `DATA_INSUFFICIENT`，**不回退到确认根重算** |
| 6 | 保留所有未批准倍率与规则的配置闸门 | **完成** | 未批准配置：`ready=false`、`open_allowed=false`、承接**一次都不求值**、0 候选；fixture 档缺显式环境变量仍 `open_allowed=false`；必填仍 **49** 项、待决映射闭合 |
| 7 | 不借修时序改阈值／经济参数／冷却／正式策略 | **完成** | 测试配置与 05-B fixture 逐项 deep-diff **只差 `approval_ref` 一句**；`FIXED_RULES` 8 项逐项核对；本单未碰 `paper_engine` / 冻结包 / 冷却字段 |
| 8 | 来源确有矛盾时只对该项报阻塞 | **完成** | 唯一阻塞项：`sweep_recovery_scope = multi_bar` 时「扫低根」取哪一根原文未规定（且该档未批准）⇒ 该情形标 `multi_bar_scope_unverified=true`，**不声称已验证**；其余 6 条依据无冲突 |
| 9 | 保存修前必失败、修后通过的调用时点证据（三类） | **完成** | `out/01_pre_fix_fail.json` / `out/02_post_fix_pass.json`，同一套判据（`_w2.CASES`） |
| 10 | 不得只直接调用单体函数证明状态机已接上 | **完成** | 三类用例全部走 `SchemeObserver.on_bar` + `observe_store` 迁移表；另有 `scan_symbol` 端到端（建档→推进→候选落 `accum05_candidate`） |
| 11 | 验收：实际状态迁移、bar 时间、基准窗口、失败原因逐根可解释 | **完成** | §6 逐根数字表 + §7 截断证据 |
| 12 | 未来数据截断反例仍被挡 | **完成** | 截断到扫低根：0 候选、被触碰最大下标 = 60、前缀动作逐条相同；并配负向对照证明该测试能分辨 |

---

## 3. 依据（06-01 未交付的处理方式）

工单写「修改依赖 06-01 的对应依据」。**06-01 目录不存在**，所以本窗不引用任何上游结论，
改为直取原始文档并**当场读回该行校验**（`must_contain` 命中才算数，`basis_all_verified=true`）。

| ID | 主张 | 出处（文件:行） | 读回校验 |
|---|---|---|---|
| B1 | 承接/卖压的判断时点 = **扫低根 1h 收盘** | `evidence/ACCUM-EARLY-04-A/README.md:53` | ✅ |
| B2 | 基准 = **扫低根之前 20 根 1h** 的中位数 | `evidence/ACCUM-EARLY-04-A/02_规则接口.md:134` | ✅ |
| B3 | 承接是**硬门槛**（不满足不进 ARMED） | `evidence/ACCUM-EARLY-04-A/03_决策表.md:28` | ✅ |
| B4 | 参考实现在**扫低成立那一根**就调用 `absorption`，不通过记 `SWEEP_REJECTED` | `evidence/ACCUM-EARLY-04-A/rules/accum_early_rules.py:1065` | ✅ |
| B5 | `sweep_recovery_scope = single_bar`（扫低与收回同根） | `evidence/ACCUM-EARLY-04-A/02_规则接口.md:128` | ✅ |
| B6 | `sell_pressure.require` 仍是「建议未批准」 | `evidence/ACCUM-EARLY-05-A-R1/03_完整方案参数附件表.md:48` | ✅ |
| B7 | 局部压制位也定义为「扫低根之前 48 根」 | `evidence/ACCUM-EARLY-04-A/README.md:52` | ✅（本单**不修**，见 §8） |

**无冲突项**：承接时点（B1/B4 一致）、基准窗口（B2 唯一口径）、承接是门槛（B3/B6 一致：门槛存在，但倍率未批准）。
**唯一阻塞项**：`multi_bar` 情形下「扫低根」的定义（B5 只覆盖 `single_bar`）。处理方式：按 scope 窗口内最低价那一根取，
并在迁移 payload 写 `multi_bar_scope_unverified=true`；**不把该路径计入已验证**。

---

## 4. 修前缺陷（不是「看起来不对」，是轨迹）

1. **静态**：`_absorption` 全文件仅 1 个调用点，位于 `_confirm_actions`，实参 `i`（= 当前确认根）。
2. **运行时**：包装 `_absorption` 后跑完整状态机，三类用例的承接判定下标全部是 **61 / 61 / 61**
   （A、B 的确认根 61；C 的确认根 86，修前它在 61–86 每一根都重算了一次，最后一次是 86）。
3. **后果**：
   - A（扫低根 0.7 通过、突破根 0.3 不通过）⇒ **修前不出候选**；
   - B（扫低根 0.3 不通过、突破根 0.7 通过）⇒ **修前出候选**（用突破根的承接给扫低根背书）；
   - C（扫低根缺 `taker_buy_quote_volume`）⇒ **修前照样出候选**（数据不足被"绕过"）。
4. 基准窗口也随之下移：修前基准取「确认根之前 20 根」，把扫低根之后的 bar 也算进基准。

---

## 5. 修了什么（改动可证，不靠自报）

**唯一改动的生产文件**：`框架/backend/accum_early_entry.py`
（本工程 git HEAD 远落后工作树，`git status` 不能当基线 ⇒ 用 sha256 + 逆向重建证明。）

| 项 | 值 |
|---|---|
| 修前 sha256 | `d96ad60eca61f7e53e273414196100ac21b33650a19480beafb0eb60c4d41a52` |
| 修前字节 | 113875 |
| 修后 sha256 | `3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017` |
| 改动 | +62 / −6 行（68 行变动），5 处 |
| 逆向重建 | 把 5 处编辑逆向替换回去，sha256 **逐位等于修前值** ⇒ 改动范围被哈希证明，不是声明 |

五处编辑：

1. `_absorption`：`detail` 增加 `bar_open_ms` / `bar_close_ms`（判的是哪一根，写进证据）；
2. `SchemeObserver.__init__`：新增 `sweep_i` / `sweep_bar_ms` / `sweep_baseline_window` / `sweep_absorption`；
3. `on_bar`（BOX→SWEEP）：按 scope 窗口最低价定位**扫低发生根**，在那里求值一次承接，
   并把 `sweep_bar_i`、`absorption_bar_close_ms`、`absorption_baseline_window`、`absorption{ok,detail,conditions}`
   写进迁移 payload；
4. `_confirm_actions`：删掉 `_absorption(bars_1h, i, self.cfg)`，改为只读 `self.sweep_absorption`；
   该状态缺失时**直接 `DATA_INSUFFICIENT`**，拒绝用确认根替代；
5. `_confirm_actions`：`DATA_INSUFFICIENT` 的原因从「承接证据缺字段」改成「承接在扫低根（时刻）无法判定：原因」。

完整 diff：`out/04_code_diff.patch`；逆向重建的修前文件：`out/04_pre_fix_reconstructed.py`。

**未改动**（按文件修改时刻核，不用 `git status`——HEAD 远落后工作树）：
`paper_engine.py`（10-26 21:23）、`replay_calc.py`（10-26 22:11）、`observe_store.py`（10-26 17:24）、
`research_15m.py`（10-26 21:04）、`live_store.py`（10-26 22:14）、
冻结包 `state_machine.py`（10-26 18:29）/ `config.py`（10-26 13:45）、采集器、前端、配置模板。
今天只有 `accum_early_entry.py`（10-27 01:56）被动过。

---

## 6. 修后通过（同一套判据）

同一套判据下的两个结果：**修前必失败：三类全部不符预期**；修后三类全部相符。
静态复核（修后）：`in_on_bar=true`、`in_confirm_actions=false`、`arg_i=i_sweep`（`_absorption` 已不在确认函数里）。

| 用例 | 期望 | 修前 | 修后 | 状态迁移 |
|---|---|---|---|---|
| A 扫低承接通过 / 突破根不通过 | 出候选 | **0 候选（不符）** | **1 候选（WAIT_ENTRY）** | BOX → SWEEP(2026-06-03T12:59:59Z) → 候选(2026-06-03T13:59:59Z) |
| B 扫低不通过 / 突破根通过 | 不出候选 | **1 候选（不符）** | **0 候选** | BOX → SWEEP(2026-06-03T12:59:59Z)，收上压制位但承接不达标 ⇒ 继续观察 |
| C 扫低根数据不足 | 不出候选 + `DATA_INSUFFICIENT` | **1 候选（不符）** | **0 候选 + DATA_INSUFFICIENT** | BOX → SWEEP(2026-06-03T12:59:59Z) → DATA_INSUFFICIENT(2026-06-04T14:59:59Z) |

逐根数字（**独立重算**，不走 `_absorption`；合成 1h，ATR=1.0，箱体 L=100 / H=110）：

| 用例 | 扫低根 60（12:59:59Z） | 突破根 | 判定读的根 |
|---|---|---|---|
| A | ratio 0.7 ≥ 基准 0.5 × 1.0 = 0.5；卖额 300 ≤ 400；上半区 ✅ | 61（13:59:59Z）ratio 0.3、卖额 700 ⇒ 不通过 | **60** |
| B | ratio 0.3 < 0.5；卖额 700 > 400 ⇒ 不通过 | 61（13:59:59Z）ratio 0.7、卖额 300 ⇒ 通过 | **60** |
| C | `taker_buy_quote_volume` 缺失 ⇒ 无法判定 | 86（06-04T14:59:59Z）ratio 0.7 ⇒ 通过 | **60** |

- 基准窗口（三类一致）：`from_ms=1780416000000` → `to_ms=1780487999999`，`n=20`，右端 = 扫低根**前一根**收盘。
- 承接判定调用下标：修前 `[61]/[61]/[61,62,…,86]`；修后三类均 `[60]`（每类只求值一次）。
- 端到端（`scan_symbol`）：`4a` 落 `accum05_candidate` 1 行，`confirm_close_ms` = 13:59:59Z；
  `4b/4c` 按各自确认方式进入 `RETEST`（未在两根内完成，符合预期）。
  箱体来源用已知箱体替身（不替代状态机），这一点在 §9 列为限制。

---

## 7. 未来数据截断 / 配置闸门 / 参数未动

**T 截断**：只喂到扫低根（第 60 根）为止 —— 0 候选；被触碰的最大 bar 下标 = **60**（不越界）；
截断版与全量版在「≤ 扫低根」的动作**逐条相同**；全量版与「再多给 100 根」在「≤ 确认根」的动作也逐条相同。
负向对照：同一根序列，扫低根 ratio 0.7 / 确认根 0.3 **不同** ⇒ 该测试确实能分辨「读了哪一根」，不是空转。

**G 闸门**：
- 未批准配置（05-B 空白表单）：`ready=false`、`open_allowed=false`、缺 **48** 项、承接调用 **0** 次、
  0 候选、事件停在 `BOX` 并记「配置未批准（不是数据不足）」；
- fixture 档但缺 `ACCUM05_FIXTURE_OK=1`：`open_allowed=false`，`blocked_by=[not_approved, fixture_without_explicit_env]`；
- 正向对照：带显式开关的 fixture `open_allowed=true`，且**真能**跑到 SWEEP 之后（1 候选，承接下标 `[60]`）；
- 必填 **49** 项、`mapping_check()` 闭合（待决 ↔ 配置字段无漏无多）。

**P 参数未动**：本窗测试配置 vs 05-B fixture 的 deep-diff **只有 1 条**，路径是 `approval_ref`（说明文字）；
`sell_pressure` 仍是 `baseline=v20_median, buy_ratio_mult=1.0, n=20, require=true, require_upper_half=true, sell_pressure_mult=0.8`；
`FIXED_RULES` 8 项（1h 确认 / 15m 执行 / 延迟 15 分钟 / 止损 0.95 / 跟踪 0.95 / 入场即跟踪 / 无持仓上限 / 5bps）逐项对上。

---

## 8. 范围外缺陷（只交证据，未修）

`_resistance` 与 `_absorption` 是**同一类时序问题**：规则说压制位取「扫低根之前 48 根」
（B7），但 `_confirm_actions` 传的是**确认根**下标，窗口整体右移。

证据（`out/03_truncation_gates_params.json:R_adjacent_defect`）：扫低根之后插一根高点 105 的尖峰、
但不收上压制位；到第 65 根（尖峰后第 4 根，此时尖峰已是合法摆动高点）才收上 103.95。

- 扫低根相对窗口的压制位 = **103.9**；确认根相对窗口的压制位 = **105.0**；
- 现状 0 候选；若按规则（103.9）该根应确认（`would_confirm_under_sweep_relative = true`）。

**本单不改**（工单 06-03 只点名 `_absorption`；改它会改「哪些事件能确认」，属于会改行为的口径变更）。
建议由 06-04 或后续单独立项，并需要用户裁决。

---

## 9. 未验证 / 阻塞 / 不能说的话

**未验证**
- 真实行情下的修前/修后差异：本单只用**合成 1h 序列**（数字写死、可逐根复算），目的是隔离时序语义；
  原输入回归（05-E 7 事件、真实行情范围）属 **06-04**，本单未跑、不代替。
- `multi_bar` 扫低口径：未批准、原文未定义 ⇒ 未验证（payload 已标 `multi_bar_scope_unverified`）。
- 端到端 `scan_symbol` 里**箱体来源用已知箱体替身**（`build_boxes` 被替换），只证明「状态机→候选落库」这一段。
- 合成数据的 `available_at` 为空 ⇒ 成交根一律 `not_proven`（如实记录，不补造）。
- 自然行情成交、策略绩效、真实挂钟：均未涉及。

**阻塞（不是本单能解的）**
- 49 项规则参数未批准、两套经济口径未择一、13 项待决未定 ⇒ **开仓仍阻塞**，本单只保证「不再用错 bar」，不保证能开仓。
- 依据侧唯一阻塞项：`multi_bar` 情形下的「扫低根」定义。

**不能说的话**
- 不能说「承接时序已验收通过」——本单是执行方自报，需 06-09/06-10 独立复验。
- 不能说「策略因此变好/变差」——本单没有任何绩效结论，事件数变化也未在真实数据上统计。
- 不能说「7 事件回归通过」——那是 06-04。

---

## 10. 运行与复跑

解释器：`/usr/bin/python3`（3.9.6，本工程依赖装在这个解释器下；托管 3.13 无 numpy）。

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
P=evidence/ACCUM-EARLY-06-W2/01/scripts
/usr/bin/python3 $P/00_依据取证与修前轨迹.py   # 依据读回校验 + 修前静态/运行时轨迹
/usr/bin/python3 $P/01_修前必失败.py           # 修前必失败（对代码哈希有防篡改守卫）
/usr/bin/python3 $P/02_修后通过.py             # 修后通过 + 端到端
/usr/bin/python3 $P/03_截断与闸门与参数.py      # 截断 / 闸门 / 参数未动 / 范围外缺陷
/usr/bin/python3 $P/04_改动范围可证.py          # 逆向重建 sha 对撞 + 生成 diff
/usr/bin/python3 $P/05_正文对撞.py              # 本文数字 ↔ 产物 双向对撞
```

**复跑顺序 = 00 → 01 → 02 → 03 → 04 → 05**（本单按此顺序复跑，六个脚本返回码全为 0）。

`00` / `01` 带**防覆盖守卫**：它们记录的是「修前」事实，一旦被测代码的 sha256 与产物里记录的修前值不符，
脚本打印 `{"skipped": true, ...}` 并**拒绝覆盖**（返回码 0，链式复跑不中断），
以免「修前必失败」被修后结果悄悄替换。要重跑修前证据，必须先把代码恢复到 `d96ad60e…`。

---

## 11. 身份

| 对象 | 值 |
|---|---|
| 生产文件（修后） | `框架/backend/accum_early_entry.py` `3c570edc1edd…c9017`，117711 字节 |
| 生产文件（修前） | 同文件 `d96ad60eca61…41a52`，113875 字节 |
| 测试配置 | `config/fixture_rules_06w2.json` sha256 `203d9b316583…40e58`（= 05-B fixture + 改 `approval_ref`） |
| 版本标识 | 模块 `accum_early_05b_v1` / 配置格式 `accum-early-05/v1` / 规则 `accum-early-05::v1`（**本单未升版本号**：语义变化只在调用时点，字段与格式未变；是否升版由 06-04 决定） |
| 脚本（sha256 前 12） | `00` `1379bc35925e`、`01` `a4b6094e9bd8`、`02` `dfd69ed8e0f5`、`03` `2422765ee6db`、`04` `4fc5f7e38588`、`05` `6461db353101`、`_w2.py` `18c5db351219` |

---

## 12. 交给下游

| 下游 | 文件 |
|---|---|
| **06-04**（修正版本交付与原输入回归） | 修后代码 `3c570edc1edd…`；`out/04_code_diff.patch`；`out/02_post_fix_pass.json`（三类行为基线）；`config/fixture_rules_06w2.json`；本文 §8 的 `_resistance` 缺陷（回归时不要把它的差异算成承接修正的效果） |
| **06-09**（验收用例与当前缺陷冻结） | `out/01_pre_fix_fail.json`（修前失败基线，可直接当负例）；`out/03_truncation_gates_params.json`（截断/闸门判据）；`_w2.py` 的 `Trace`（记录调用下标，可复用） |
| **06-10**（独立端到端复验） | 同上 + §10 复跑顺序；注意 `01` 的防篡改守卫，复验请在独立副本上跑 |

---

## 13. 本单的取证缺陷（自曝）

改代码前**没有先留修前副本**，因此没有「改前就在磁盘上的 diff」。
补救方式是 §5 的逆向重建（sha 逐位对上），它等价于证明了「只有这 5 处变了」，但**流程上应先后备份再改**。
下一单（06-04）若需 diff，请先 `cp` 一份到本窗 `runs/` 再动手。
