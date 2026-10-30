# MAINLINE-FINAL-CLOSE-01 · 历史分支与测试失败最终收口

- 执行时间：2026-10-30（北京时间）
- 主线基线：`wyckoff-v3.1-main` HEAD `26ec8aef`
- 旧门禁分支：`origin/fix/architecture-guardrails-20261028`（tip `24c3203d`，相对主线 **90 个独有提交**、落后 7 个）
- 本轮代码提交：`10584329`（普通推送，已同步）

---

## 一句话结论

90 个提交**全部定性完毕**；4 项失败**全部找到根因且全是「测试写的是旧规则」**，已按冻结规则修好，策略测试集 **175 通过 / 0 失败**；旧分支**没有可以直接合入的生产代码**，唯一有真实行为差异的那一项属于「跳空成交价模型待批」，交用户裁决。
**但主线自身有一个阻断项：HEAD 提交了引用未提交代码的测试，干净 HEAD 上测试集连收集都过不了（见第 6 节）。**

---

## 1. 90 个提交是否全部定性

**是，90/90。**

| 分类 | 数量 | 说明 |
|---|---|---|
| 已被主线覆盖 | 7 | 新增实质行 100% 已在主线同名文件 |
| 已被新实现替代 | 35 | 主线用另一套写法实现了同一语义（多数是「散在 6 处的字符串判据」被收成一处 `signal_admission_reasons` + 常量） |
| 仅为测试或文档 | 47 | 45 个只改 `tests/`，2 个只改 `docs/`（另 1 个 `AGENTS.md` 已覆盖） |
| 存在冲突或需要裁决 | 1 | `4331157d` |
| **合计** | **90** | 与 `git rev-list --count` 一致 |

逐条明细：`90_commits_classification.csv`（含每条的完整 SHA、改动文件、判定依据、主线命中率）。

判定方法：先按提交取「新增实质行」，逐行到主线同名文件里比对得到命中率；命中率低的再逐条读代码确认语义是否已由主线新实现承担。**没有做任何 cherry-pick。**

## 2. 是否仍存在未合并的有效代码

**有一项，但它不是「遗漏」，是「待裁决」。**

`4331157d`：多单跳空穿过止损时，分支把成交价改成 `min(bar_open, cur_stop)`；主线 `paper_engine` 仍按 `cur_stop` 成交、只改原因标签。

为什么不能直接合：
- 改的是模拟盘**成交价与盈亏**，不是纯技术修复；
- 研究侧 `accum_early_entry` 已经按 `bar_open` 计价（10R 修复），两处口径目前不一致 —— 这个不一致是真的；
- 但它属于 06-W1-R1 待决清单里的「跳空成交价模型与滑点」，要用户拍板。

其余：45 个测试提交实测在主线代码上 **47 失败 / 22 通过**，合入即新增 47 项失败，故不合入；17 个 CI 提交指向分支专属测试文件，照搬会让 CI 报文件不存在；2 个文档提交里的字段名（`economic_entry_at` / `actual_fill_at`）主线根本没有，照搬会与代码矛盾。

## 3. 4 项失败是否全部找到根因

**是，4/4，全部 A 类（旧测试期望与冻结规则不一致），0 个生产实现缺陷。**

| # | 用例 | 真因一句话 |
|---|---|---|
| 1 | `test_adversarial_candidate_unique_binding_and_field_mapping` | 期望手数还用「候选里的 100.1 当止损 + 双边手续费」算；冻结规则改成「入场价×0.95 当止损、分母不含手续费」 |
| 2 | `test_max_holding_10_days_expiry_exit` | 冻结规则已取消 10 天自动平仓，到期退出不再发生（实测改 264 小时才被末段强平） |
| 3 | `test_exit_failure_keeps_position_open_and_marks_run_invalid` | 0.004 的价在 0.01 网格上让 5% 止损退化成 0，**入场就被拒、根本没持仓**，所以「持仓为空」是对的；原场景不可达 |
| 4 | `test_fingerprints_cover_rules_code_config_inputs` | 指纹里 `max_holding_days` 写死 10，冻结规则是 `None` |

修法：**没改一行生产代码、没动一个冻结参数、没放宽一条断言**。第 2 项拆成「默认不得到期平仓」+「显式覆盖 10 天机制仍生效」两段；第 3 项换成可达场景（0.019 入场、跳空到 0.004），断言一字未改。逐项证据见 `four_failures_root_cause.md`。

## 4. 主线与 GitHub HEAD 是否一致

**一致。**

- 推送前：本地 `26ec8aef` = 远程 `26ec8aef`
- 推送后：`git ls-remote` 实测远程 = `10584329`（= 本地代码提交）
- 本文件随证据提交再推一次，远程 HEAD 会前移一格；三次推送的 SHA 与实测值记在 `out/github_sync_probe.json`（该文件自身所在的「回填提交」同样会使远程 HEAD 前移一格，其 SHA 见 `git log -1` 与回复正文）
- 全程普通推送：**没有强推、没有 `reset --hard`、没有 `git add .`**

## 5. 测试通过 / 失败 / 跳过数量

| 测试集 | 通过 | 失败 | 跳过 | 错误 |
|---|---|---|---|---|
| 策略测试集（修复前） | 171 | 4 | 0 | 0 |
| **策略测试集（修复后）** | **175** | **0** | **0** | **0** |
| 代理门禁 `test_agent_gate.py` | 71 | 0 | 0 | 0 |
| 架构门禁 `test_agent_paths.py` | 1 | 0 | 0 | 0 |
| 11 项接口回归 | 11 | 0 | 0 | 0 |
| Python 语法（11 个关键文件） | 11 | 0 | 0 | 0 |
| 核心模块导入 | 5 | 0 | 0 | 0 |
| 前端类型检查 / 生产构建 | 2 | 0 | 0 | 0 |
| **干净 HEAD 上的策略测试集** | — | — | — | **1（收集失败）** |

## 6. 是否达到代码收口 PASS

**不通过。** 理由不是本轮的改动，而是主线自身缺一个提交。

**BF-01（阻断）**：HEAD `26ec8aef` 里 `tests/test_backtest_repair_01_metrics.py` 已提交，它从 `engine_adapter` 导入 `_merge_summary` 与 `compute_summary_metrics`；但 HEAD 的 `engine_adapter.py` 里这两个名字都不存在。结果：**干净 HEAD 上策略测试集连收集都过不了**（`ImportError`，164 collected + 1 error）。

这两个函数只存在于工作区未提交改动（`engine_adapter.py` +185 行，BACKTEST-REPAIR-01 任务 C 绩效汇总，工作区 SHA `4b28ecd2…` vs HEAD `e59a2271…`）。查过 `origin/work04-backtest-workbench`、旧门禁分支、`backtest-repair-01`、`pr2-integration-exec-01`、`main`、`origin/main` —— 都没有，说明这份实现**从未提交到任何分支**。

按 AGENTS.md「不得擅自清理、覆盖其他窗口尚未提交的成果」，本轮**没有代提交**。两条路：
1. 由该改动的执行方自己提交（推荐，归属清晰）；
2. 或用户明确授权本轮代提交该文件后再同步。

补一条纪律事实：提交时 pre-commit 钩子输出「[门禁未启用] 默认模式不校验暂存区」，**这次钩子什么也没校验**，不能当门禁通过的证据；真正跑起来的门禁是 `test_agent_gate.py` 71 passed。

---

## 保护范围自查

| 项 | 是否触碰 |
|---|---|
| 生产 SQLite / WAL / SHM | 否 |
| 正式 Parquet | 否 |
| 原始 ZIP | 否 |
| 账本 | 否 |
| 唯一备份 | 否 |
| 其他窗口未提交成果（`engine_adapter.py` 等） | 否（只读、未提交、未回退） |
| 冻结策略参数（`config.py`） | 否，SHA 未变 |
| 冻结规则文档 | 否，SHA `1f1167de…`、13,315 字节未变 |
| 额外磁盘瘦身 / 大规模回测 | 未做 |

## 交付清单

| 文件 | 内容 |
|---|---|
| `README.md` | 本文件 |
| `90_commits_classification.csv` | 90 个提交逐条分类与判定依据 |
| `unmerged_effective_changes.json` | 未合入项（含 1 项待裁决 + 1 个阻断项） |
| `four_failures_root_cause.md` | 4 项失败逐项定性 |
| `integration_result.json` | 合入结果与冻结身份核对 |
| `regression_results.json` | 9 组回归实测明细 |
| `github_sync_result.json` | 同步过程与远程 HEAD |
| `manifest.sha256` | 上述文件的 SHA256 |
| `out/coverage_raw.json` | 90 提交逐条覆盖率的原始数据（判定依据的可复算底稿） |
