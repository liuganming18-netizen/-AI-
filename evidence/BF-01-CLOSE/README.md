# BF-01-CLOSE · 干净 HEAD 测试收集阻断修复

- 执行时间：2026-10-30（北京时间）
- 合并前主线：`bcd72806`
- **合并后主线：`7c625c51`**（已普通推送，远程一致）
- 改动：**1 个文件、183 行新增 + 2 行删除 = 185 行变更**

---

## 一句话结论

**已修复，PASS。** 主线此前提交了测试、漏提交了被测实现，导致干净 HEAD 上策略测试集**连收集都过不了**。本次只补入那段缺失实现（`engine_adapter.py` 一个文件），干净隔离检出上：**收集 175 项 0 错误、策略测试 175 通过 0 失败、接口回归 11/11、代理门禁 71 + 路径门禁 1**。旧 13 个 summary 字段实测**一字未改**，冻结参数与冻结规则文档 SHA **未变**。

---

## 1. BF-01 根因及函数依赖

**根因**：`26ec8aef` 提交了 `tests/test_backtest_repair_01_metrics.py`，它第 34 行 `from ... import _merge_summary, compute_summary_metrics`；这两个名字在 `engine_adapter.py` 里**从未被提交**。pytest 在收集阶段执行顶层 import ⇒ 直接 `ImportError` ⇒ 默认行为是中断整轮，**一条用例都跑不起来**。

不是「实现写错」，是**漏提交**：下游 `框架/backend/backtest_runner.py:866-910` 早就在读 `wins`/`losses`/`win_rate_pct`/`max_drawdown_pct`/`profit_factor`/… 这些字段（并兼容旧名 `win_count`/`loss_count`），生产方却不存在。

**函数依赖闭包**（全部在 `engine_adapter.py`）：

```
compute_summary_metrics(trades, equity_curve, initial_equity, final_equity)   # 480
  ├─ _num            # 444  None/NaN/inf 一律 None，脏值不入合计
  ├─ _mean           # 457  空列表返回 None
  ├─ _max_drawdown_pct  # 461  少于 2 点返回 None（不是 0）
  └─ 6 个口径常量 + _METRIC_EPS  # 433-441，只写进返回值供页面解释

_merge_summary(base, trades, equity_curve, initial_equity, final_equity)      # 582
  └─ compute_summary_metrics(...) → out.setdefault(k, v)   # 只加不改

接线：run_event_trend_backtest 的 summary 改为经 _merge_summary 产出（921 行、936 行）
```

模块级依赖只有 `math` 与 `typing.{Optional, List, Tuple}` —— **这三个 import 改动前就在文件里，本次没有新增任何 import**。

详细契约核对（M01–M09 逐条对应实现）见 `root_cause_and_dependencies.md`。

## 2. 实际纳入的代码行数

| 项 | 数量 |
|---|---|
| 改动文件 | **1 个**（`策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py`） |
| 新增行 | **183** |
| 删除行 | **2**（两行接线改写） |
| 变更行合计 | **185** |
| 新增 import | 0 |
| 是否包含其他窗口成果 | 否（该文件未提交差异全部属于同一件事，无夹杂） |

只搬函数不接线，会留下一段永不执行的死代码，下游照旧读到 `None` —— 那仍然是不一致，只是把「导入失败」换成「永远拿不到值」。所以接线一并纳入。

## 3. 未提交成果保护证明

| 证明项 | 结果 |
|---|---|
| 本次提交只含 1 个文件 | `git show --stat HEAD` → `1 file changed, 183 insertions(+), 2 deletions(-)` |
| 暂存区是否夹带其他文件 | 否，`git diff --cached --numstat` 自始至终只有 engine_adapter 一行 |
| 原作者内容是否被改写 | **否**：提交版本 SHA256 `4b28ecd2…` 与提交前工作区**完全一致**，一个字节没动 |
| 工作区剩余未提交项 | 159 项（126 个运行态文件改动 + 33 个未跟踪 evidence 目录），**既未暂存也未回退** |
| `数据` / `evidence` 已跟踪改动 | 0（隔离运行没有污染真实仓库） |
| 禁用手法 | 未用 `reset --hard`、未强推、未 `git add .` |
| 测试完整性 | 未删除、未跳过任何测试；`skipped` 计数为 0 |

## 4. 干净 HEAD 测试收集结果

| 状态 | 收集结果 |
|---|---|
| 修复前 `bcd72806` | 164 collected, **1 error**（`ImportError: _merge_summary`）→ 中断 |
| 修复后 `7c625c51` | **175 collected, 0 error** |

恢复的 11 项就是 `test_backtest_repair_01_metrics.py` 的 M01–M09 + M04b + M05b。

隔离方式：`git archive` 出分支的 `策略/框架/tests/AGENTS.md/docs` 到 `/tmp/bf01-iso.*`，`数据` 与 `evidence` 用符号链接指向真实仓库（只读输入，已逐文件核对写操作全落在 tempfile/tmp_path）。

## 5. 完整回归统计

| 套件 | 通过 | 失败 | 跳过 | 错误 |
|---|---|---|---|---|
| 策略测试集（隔离检出） | 175 | 0 | 0 | 0 |
| 策略测试集（真实仓库，合并后） | 175 | 0 | 0 | 0 |
| metrics 专项（11 项逐条） | 11 | 0 | 0 | 0 |
| 11 项接口回归（隔离检出） | 11 | 0 | 0 | 0 |
| 代理门禁 `test_agent_gate.py` | 71 | 0 | 0 | 0 |
| 架构门禁 `test_agent_paths.py` | 1 | 0 | 0 | 0 |
| **合计（不重复计 metrics）** | **258** | **0** | **0** | **0** |

另有一项专项核对：同一确定性回测在改动前后各跑一次，`summary` 共 13 个旧字段、**数值改动 0 处**，新增 23 个字段。

## 6. 合并前后 HEAD

| 阶段 | HEAD |
|---|---|
| 合并前 | `bcd728069456a05fb2386f92ed97fa63d912e0ff` |
| 隔离分支提交 | `7c625c51ad7f4a2a4ed1f086987dc7abed840ef0` |
| 合并后（快进，0 冲突） | `7c625c51ad7f4a2a4ed1f086987dc7abed840ef0` |

## 7. GitHub 远程 HEAD

```
$ git push origin wyckoff-v3.1-main
bcd72806..7c625c51  wyckoff-v3.1-main -> wyckoff-v3.1-main

$ git rev-parse HEAD
7c625c51ad7f4a2a4ed1f086987dc7abed840ef0
$ git ls-remote origin refs/heads/wyckoff-v3.1-main
7c625c51ad7f4a2a4ed1f086987dc7abed840ef0
```

本地 = 远程。本证据目录的提交会让远程再前移一格，最终值见 `out/sync_probe.json` 与回复正文。

## 8. SHA256 与执行日志

| 文件 | SHA256 |
|---|---|
| `engine_adapter.py` | `4b28ecd25234101cdaddda456133f661fdf78a28e345c6be3f93ab047a1c672f` |
| `config.py`（冻结参数） | `122683f2b489c742dd594ff465e916147dd097bd1afcb8a35cb4116c0eedeb46` |
| 冻结规则文档 | `1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab`（13,315 字节） |

`engine_adapter.py` 的 SHA 与提交前工作区一致 ⇒ 提交的就是原作者写的那份；后两个 SHA 与 `bcd72806` 时相同 ⇒ 冻结身份未漂移。

逐条命令与输出：`run_log.md`。

---

## 是否触碰冻结策略语义

**没有。** 新增行做关键词扫描（`stoploss` / `trail` / `risk_budget` / `max_holding` / `initial_equity =` / `taker_fee` / `slippage_bps` / `oi_` / `participation` / `config.`）**命中 0 条**；只读账本产物（`trades` / `equity_curve`），不写库、不下单、不改持仓；旧字段实测零改动。属报表层加性扩展。

## 交付清单

| 文件 | 内容 |
|---|---|
| `README.md` | 本文件 |
| `root_cause_and_dependencies.md` | 根因、函数依赖闭包、契约逐条核对 |
| `isolation_proof.json` | 纳入/排除范围、未提交成果保护、安全自查 |
| `regression_results.json` | 收集结果与全部回归实测 |
| `sync_result.json` | 合并前后 HEAD、远程 HEAD、SHA256 |
| `run_log.md` | 逐条命令与关键输出 |
| `manifest.sha256` | 上述文件的 SHA256 |
| `out/sync_probe.json` | 远程 HEAD 实测回填 |
