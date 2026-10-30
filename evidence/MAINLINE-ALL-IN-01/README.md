# MAINLINE-ALL-IN-01 · 全部成果合并与项目瘦身

- **工单**：`MAINLINE-ALL-IN-01`
- **基线**：`f05b6d63`（= 上一轮 FINAL-INTEGRATION-01 主线）
- **本轮落点**：`8d3d8a12`（2 个提交，已推送）
- **执行日期**：2026-10-30
- **结论**：**部分通过（PASS WITH GAPS）**

---

## 一、一句话结论

11 类对象全部盘点完毕，**没有一类是靠目录名或提交记录直接判定的**；
真正合入的是「BACKTEST-REPAIR-01 路径可移植 + PR2 经济侧账 + PR2-WO-01 显示语义 + 多代理门禁」四块，
D03-R1 因原补丁物理缺失、按验收契约做了**最小必要实现**并做了两轮实测（函数级 9/9、HTTP 往返 8/8）；
其余分支/目录判定为**已被主线覆盖**，未强行覆盖主线。
**磁盘目标 50–60 GB 未达成**，实测可用 20 GiB → 24 GiB，原因见第五节（剩下的大头全是明令禁删的正式数据）。

---

## 二、执行闸四行

```
本轮授权：把 11 类散落成果全部合入主线、同步 GitHub、回收磁盘，并交付 11 个文件
不碰：冻结策略参数、历史 PIT 口径、生产账本、真实交易行为；禁 force push、禁删唯一回滚备份
完成判据：11 类对象逐项定性 + 三方比较 + 回归实测 + 远程 HEAD 校验 + 清理日志 + 待裁决清单
停手点：最终报告输出后立即停手
```

---

## 三、关键判定表

| 项 | 结果 | 证据 |
|---|---|---|
| 主线是否前进 | **是**。`wyckoff-v3.1-main` 快进到 `8d3d8a12`（`--ff-only`，无合并提交） | `github_sync_result.json` |
| GitHub 远程 HEAD 是否一致 | **一致**。`ls-remote` 直读 `8d3d8a12…` = 本地 | 同上 |
| 推送方式 | **普通 push**，未使用 `--force` | 同上 |
| 回归 通过 / 失败 / 跳过 | 策略测试集 **171 / 4 / 0**；接口回归 **11 / 0 / 0**；D03-R1 函数级 **9 / 0**、HTTP **8 / 0** | `regression_results.json` |
| 4 项失败是否为回归 | **不是**。基线 `f05b6d63` 上同样失败 4 项、清单逐字相同 | 同上 |
| 冻结配置是否被动 | **没有**。`config.py` = `122683f2b489c742…`，本轮 diff **0 行** | `regression_results.json` |
| D03-R1 是否集成 | **已做最小必要实现并通过两轮实测**（原补丁物理缺失，非「凭资金费函数判定」） | `d03r1_integration.md` |
| 9 个 worktree | **全部按 `git worktree remove` 流程移除**（先验脏文件/独有提交/证据落点） | `worktree_disposal.md` |
| 清理前后可用磁盘 | 20 GiB → **24 GiB**（逻辑移除约 9.5 GB） | `disk_cleanup_log.md` |
| 目标 50–60 GB | **未达成**，见第五节 | 同上 |

---

## 四、本轮真正合入的四块成果

### 1) BACKTEST-REPAIR-01 · 历史数据路径可移植（三方合并，1 处冲突已解）

`策略/WyckoffMaster/event_trend_v0_1/runner.py` 引入：

- `_default_base_dir()`：`WX_PROJECT_ROOT` → `DATA_HUB_PROJECT` → 由文件位置上溯推导，
  **全程不读 `pwd`、不含本机绝对路径** ⇒ 换机器/换检出目录/隔离工作树都能跑；
- `data_paths_report()`：把「行情 parquet / OI 热库 / 合约快照」三条路径的来源与存在性
  **一次性摊开**，替代原来「各自静默兜底、跑完只看到没有候选」；
- 任务 B 用 `run_state_machine_simulation` + parquet OI 的独立路径。

`框架/backend/backtest_runner.py`、`oi_feed.py`、`instrument_spec.py` 同步合入；
`engine_adapter.py` 三方合并结果与基线**逐字一致**，等于无改动。

### 2) PR2 · 资金费经济侧账

`框架/backend/funding_economic_sidecar.py`（431 行）—— 本轮唯一未被主线覆盖的新后端文件。

### 3) PR2-WO-01 · 前端显示语义（原为工作树未提交改动）

`adapters.ts`：`triggerTime` 不再回退 `created_at`（建库时刻 ≠ 触发时刻），
两个真实时刻都缺就给 `null`，界面显示「触发时间未知」，不编造时间。
`ObservePanel.tsx` 同步六态语义（+72/−23）。

### 4) 多代理防漂移门禁（原分支 `implement_agent_guardrails`）

盘上版本已演进到 `框架/ops/agent_gate.py` 904 行 + `tests/test_agent_gate.py` 778 行，
**自测 71 passed 后**才提交。
**必须说清**：提交钩子自述「**门禁未启用**，本次输出不代表任何校验结论」，
所以本轮所有「通过」结论**都不来自门禁**，只来自各自的实测。

---

## 五、磁盘：为什么没到 50–60 GB

实测口径（`df -h /`）：**20 GiB → 24 GiB**。

逻辑移除量约 **9.5 GB**（11 个 worktree）+ `.git` 回收 37 MB，但 APFS 只归还了约 4 GB，
其余处于**延迟块回收**状态（上一轮同样现象：删除 20.9 GB 后当场只 +4 GB，次日自动补足）。
**这一条不是「已释放」，只能写「已逻辑删除、待系统回收」。**

剩下的大头与为何不能动：

| 目录 | 占用 | 为什么不能删 |
|---|---|---|
| `数据/市场数据/parquet` | 19 G | 正式历史行情 Parquet（工单明令禁删） |
| `数据/市场数据/db` | 16 G | 生产热库 `market_v2.sqlite3`（禁删） |
| `数据/研究输入/WyckoffMaster` | 9.0 G | 其中 `_backup/20261014T190050Z` 的 `ohlcv`(3.1G)、`oi_metrics`(5.5G) 在父目录**没有对应副本** ⇒ 唯一副本，禁删 |
| `数据/研究输入/WyckoffMaster_v31` | 8.0 G | `is`/`val` 研究数据集（唯一副本） |
| `数据/raw` + `raw_oi_metrics` | 16.5 G | 原始下载（含原始 ZIP，禁删） |
| `evidence` | 6.0 G | 审计证据（删了就无法复核历史结论） |
| `回测日志` | 1.6 G | 回测产物，属可复核证据 |

**结论**：在「禁删生产 SQLite / 正式 Parquet / 原始 ZIP / 账本 / 唯一副本」的前提下，
本项目侧**已无 30 GB 级别的安全删除对象**。要把可用空间做到 50–60 GB，
只能删上面表里的某一类，或清理系统级目录（`/private/var` 16 G、`~/Library` 6.6 G），
这两类都超出本轮授权，列为**待裁决**（见 `pending_decisions.md`）。

---

## 六、必须说清的边界（不得据此宣称）

- **不得说「磁盘已释放 9.5 GB」** —— 实测可用只 +4 GB，其余待 APFS 回收。
- **不得说「门禁通过」** —— 门禁未启用，输出自述不代表任何校验结论。
- **不得说「回归全绿」** —— 策略测试集仍有 4 项失败（既存，非本轮引入，见 `regression_results.json`）。
- **不得说「D03-R1 已端到端验收」** —— 本轮做的是**只读契约**（交易/零交易/404/503/权益曲线）
  在隔离账本副本上的验证；**真实挂钟自动成交仍未验证**（自然候选 0、账本 0 笔已平）。
- **不得说「策略有效/无效」** —— 本轮未做绩效评估，0 成交无统计意义。
- **研究回测与正式 PIT 认证必须分开** —— 本轮全部为研究/接口级验证，`pit_qualified` 未变。

---

## 七、交付物清单（11 个）

1. `README.md`（本文件）
2. `inventory.md` —— 11 类对象逐项盘点与定性
3. `three_way_merge.md` —— 同一文件多版本的三方比较记录
4. `branch_inventory.json` —— 分支级盘点（独有提交/落后/文件差异）
5. `worktree_disposal.md` —— 11 个 worktree 的七项检查与移除记录
6. `d03r1_integration.md` —— D03-R1 契约与最小必要实现
7. `regression_results.json` —— 回归通过/失败/跳过数量与失败清单定性
8. `disk_cleanup_log.md` —— 逐项清理日志与 APFS 实测
9. `github_sync_result.json` —— 推送前后远程 HEAD、ahead/behind
10. `pending_decisions.md` —— 待裁决项（含为何不强行覆盖主线）
11. `manifest.sha256` —— 上述文件与本轮核心代码的哈希清单

附：`out/d03r1_contract_verify.json`、`out/d03r1_http_verify.json`（两轮实测原始输出）、
`verify_d03r1.py`、`verify_d03r1_http.py`（可复跑脚本）。
