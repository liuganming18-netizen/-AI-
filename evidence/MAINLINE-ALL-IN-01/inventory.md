# 11 类对象逐项盘点与定性

**判定纪律**：不凭目录名称、不凭提交记录判断「已合入」。
每一类都要落到「具体文件 + 具体符号 + 可复跑的实测」三件套，缺一件就记为**待裁决**。

---

## 汇总表

| # | 对象 | 定性 | 判定依据（可复跑） |
|---|---|---|---|
| 1 | 主线 `wyckoff-v3.1-main` | **已前进** | `git rev-parse wyckoff-v3.1-main` = `8d3d8a12`；`ls-remote` 一致 |
| 2 | PR2 分支（`pr2-integration-exec-01` / `pr2-recovered-harden-01` / `pr2-wo-02-fail-closed` / `pr2-wo-01-display-semantics`） | **已覆盖** | fail-closed 八个常量在 HEAD 全在（见下）；WO-01 前端改动已提交 |
| 3 | 9 个 worktree（`/private/tmp/*`） | **已移除** | `git worktree list` 只剩主仓库；逐个见 `worktree_disposal.md` |
| 4 | `implement_agent_guardrails` 分支 | **已覆盖（且盘上版本更新）** | 盘上 `agent_gate.py` 904 行 vs 分支 239 行；自测 71 passed 后提交 |
| 5 | `evidence_correction_and_review` 分支/worktree | **已覆盖** | 独有提交 0；三个独有 evidence 目录与主仓库 `diff -rq` **完全相同** |
| 6 | `work04-backtest-workbench` 分支 | **不采用（路径错误 + 已被覆盖）** | 该分支把组件放在仓库根 `src/components/…`（错位），主线已有 `UI界面/web/src/components/BacktestReviewView.tsx` 47.6 KB，远大于分支的 416 行 |
| 7 | 10R 原始目录 | **无独立目录，成果已在主线** | `accum_early_entry.py` 的 `gap_model` 10 处、`same_bar_execution`、`cur_stop=initial_stop` 均在 HEAD |
| 8 | C02-R1 原始目录 | **无独立目录，成果已在主线** | `unified_runtime.py` 的 `oi_preseed_rows` 3 处、`fetched_at_backfilled`；`runner.py` 的 `_read_oi_frame` 同读 `fetched_at/source` |
| 9 | D03-R1 报告/代码/API | **原补丁物理缺失 → 按契约最小实现** | 见 `d03r1_integration.md`；函数级 9/9 + HTTP 8/8 |
| 10 | 其他隔离目录独有修改 | **全部核对完毕**，无遗漏 | 见下「逐项细节」 |
| 11 | 主仓库已修改与未跟踪文件 | **分三类处理**（代码已提交 / 运行态不提交 / 证据已回收） | 见下「逐项细节」 |

---

## 逐项细节

### 2) PR2 分支：为什么判「已覆盖」

fail-closed 判据是**读写两侧唯一来源**，只要这些常量在 HEAD 的 `live_api.py` 里齐全，
就说明「当前区能不能看到」与「路由能不能扫到」用的是同一批字符串，不会分叉：

```
ACTIONABLE_ORIGINS                7 处
NON_ACTIONABLE_ORIGINS            1 处
TEST_SIGNAL_UID_PREFIXES          3 处
LIVE_WATERMARK_UNAVAILABLE        3 处
SIGNAL_TIME_CHAIN_SQL             2 处
FLOOR_RIGHT_EDGE_SQL              2 处
APPROVED_SQL                      3 处
EXEC_STATE_ENTERABLE              2 处
```

`unified_runtime.py` 侧：`_scan_actionable` 5 处、`actionable_scope_where` 3 处。

`pr2-recovered-harden-01` / `pr2-wo-02-fail-closed` 分支里的
`test_pr2_wo_02_fail_closed.py` 只有 **139 行**，主线本轮已合入 **708 行**的完整版
⇒ 分支版本是**子集**，不是替代品。

### 4) `implement_agent_guardrails`：分支被盘上版本超越

- 分支侧：`框架/ops/agent_gate.py` **239 行**（4 个提交，落后主线 37 个提交）
- 盘上：`框架/ops/agent_gate.py` **40,847 字节 / 904 行**，`tests/test_agent_gate.py` 778 行
- 处理：**不合并分支**（会把 904 行退回 239 行），改为把盘上更新版**提交**，
  且必须先跑通自测（`pytest tests/test_agent_gate.py` → **71 passed**）才提交。

### 10) 其他隔离目录独有修改（逐个核对结果）

| 目录 | 独有内容 | 判定 |
|---|---|---|
| `gm-pra-verify` | 5 个前端组件 + `AGENTS.md` + `.github/` + `tests/` | 5 个组件与主线 **逐字相同**；workflow 相同；已移除 |
| `gm-ts-baseline-fix` | 同上 5 个前端组件 | **逐字相同**；已移除 |
| `pr2-delivery-verify-02` | 后端 6 个 py 的「独有」改动 | 实为 **10R/C02-R1 修复前的旧版本残留**，非新成果；已移除 |
| `pr2-integration-exec-01` | `evidence/PR2-INTEGRATION-EXEC-01/`（204 K） | **已复制入主线**后再移除 |
| `backtest-repair-01` | `evidence/BACKTEST-REPAIR-01/`（308 K）+ 5 个 py | 证据**已复制入主线**；5 个 py 已三方合并 |
| `pr2-paper-entry-harden` | `test_pr2_paper_entry_harden.py` | 已合入主线 |
| `pr2-wo-01-baseline` | 仅 `node_modules` + 前端测试目录 | 无独有代码；已移除 |
| `pr2-wo-02-work` | `unified_runtime.py` 等 | 与 `pr2-recovered-harden-01` 同源，已覆盖；已移除 |
| `evidence_correction_and_review`（反重力） | 3 个 evidence 目录 | `diff -rq` 与主线**完全相同**；已移除 |

### 11) 主仓库已修改与未跟踪文件：分三类

**A. 代码 —— 已提交（2 个提交）**

- 三方合并：`runner.py`、`backtest_runner.py`、`oi_feed.py`、`instrument_spec.py`（`engine_adapter.py` 合并后与基线一致）
- 新增：`funding_economic_sidecar.py`、3 个 `test_backtest_repair_01_*.py`、4 个 `test_pr2_*.py`
- D03-R1：`live_api.py`（+206 行）、`api/routes.py`（+23 行）
- 前端：`adapters.ts`、`ObservePanel.tsx`、`web/tests/display-semantics.test.tsx`
- 门禁：`框架/ops/agent_gate.py`、`tests/test_agent_gate.py`、`project_state.json`

**B. 运行态数据 —— 明确不提交**

`框架/backend/快照/public_extras/*.json`（约 90 个）、`快照/smart_money/*.json`（约 60 个）、
`locks/paper_runtime_3a_*.json`：这些是采集器/运行进程周期性重写的快照，
提交它们只会让每次运行都产生「脏文件」噪声，且**不是成果**。

**C. 审计证据 —— 已回收**

`evidence/BACKTEST-REPAIR-01/`、`evidence/PR2-INTEGRATION-EXEC-01/` 原先只在 worktree 里，
**先复制入主线再删 worktree**（这是删 worktree 的前置条件，不是事后补）。
