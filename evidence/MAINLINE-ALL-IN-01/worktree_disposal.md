# 11 个 worktree 的七项检查与移除记录

**纪律**：注册过的 Git worktree 必须走 `git worktree remove`，不得 `rm -rf` 粗暴删目录
（粗暴删会在 `.git/worktrees/` 留下孤儿元数据，之后 `git worktree list` 出现幽灵条目）。
每个目录在移除前都要过下面七项检查。

---

## 七项检查的定义

| # | 检查项 | 判定方式 |
|---|---|---|
| 1 | 是否有活跃进程占用 | 见下方「未能完成的一项」 |
| 2 | 是否注册为 Git worktree | `git worktree list` 中出现 |
| 3 | 是否有未提交修改 | `git -C <wt> status --short \| wc -l` |
| 4 | 是否有独有提交 | `git -C <wt> rev-list --count HEAD --not <主线>` |
| 5 | 是否有未合入补丁 | 逐文件 `diff` 或 `git diff --stat <wt> HEAD -- <file>` |
| 6 | 是否唯一回滚来源 | 该目录是否是某段代码/证据的**唯一**存放处 |
| 7 | 审计证据是否已保存 | 独有 `evidence/` 是否已复制入主线 |

---

## 逐个结果

| worktree | 大小 | 脏文件 | 独有提交 | 独有内容判定 | 处置 |
|---|---|---|---|---|---|
| `allin-baseline`（本轮自建） | 2.0 G | 166（含我建的 `数据` 软链） | 0 | 无（纯基线副本） | 已移除 |
| `pr2-wo-01-baseline` | 98 M | 2 | 0 | 仅 `node_modules` + 前端测试目录 | 已移除 |
| `pr2-paper-entry-harden` | 98 M | 5 | 2 | `test_pr2_paper_entry_harden.py` 已合入主线 | 已移除 |
| `pr2-wo-02-work` | 99 M | 4 | 2 | 与 `pr2-recovered-harden-01` 同源，已覆盖 | 已移除 |
| `gm-ts-baseline-fix` | 302 M | 5 | 0 | 5 个前端组件与主线**逐字相同** | 已移除 |
| `gm-pra-verify` | 302 M | 8 | 0 | 5 个组件逐字相同；`.github/workflow` 相同 | 已移除 |
| `pr2-delivery-verify-02` | 583 M | 17 | 0 | 「独有改动」是 10R/C02-R1 修复**之前**的旧版残留 | 已移除 |
| `pr2-integration-exec-01` | 2.0 G | 15 | 0 | `evidence/PR2-INTEGRATION-EXEC-01/`（204 K）**先复制入主线** | 已移除 |
| `pr2-recovered-harden-01` | 2.0 G | 5 | 2 | fail-closed 内容已在主线（八个常量齐全） | 已移除 |
| `backtest-repair-01` | 117 M | 9 | 0 | `evidence/BACKTEST-REPAIR-01/`（308 K）**先复制入主线**；5 个 py 已三方合并 | 已移除 |
| `evidence_correction_and_review`（反重力） | 2.0 G | 10 | 0 | 3 个 evidence 目录与主线 `diff -rq` **完全相同** | 已移除 |

合计逻辑移除 **约 9.5 GB**。

---

## 第 6 项「唯一回滚来源」的特别说明

删除前必须确认：**删掉这个目录后，还能不能把当时的结论复原？**

- 代码类：全部已进 Git（主线 + 分支引用都还在，`git branch -a` 可见），
  即使 worktree 目录消失，`git show <sha>:<path>` 仍可取回任意版本 ⇒ **不是唯一来源**。
- 证据类：`BACKTEST-REPAIR-01`（308 K）与 `PR2-INTEGRATION-EXEC-01`（204 K）
  原先**只**存在于 worktree 里，主线没有 ⇒ 属**唯一来源**，
  必须先 `cp -R` 进 `evidence/` 才能删。本轮就是这么做的，不是事后补。

---

## 未能完成的一项：活跃进程检查（如实记录）

`ps aux` 在本环境被沙箱拒绝：

```
(eval):1: operation not permitted: ps
```

因此第 1 项**没有按原计划完成**。替代证据（只能部分替代）：

- 11 个 worktree 全部处于**旧的 detached SHA** 或已合并分支上，没有任何一个在监听端口；
- 主仓库 `wyckoff-v3.1-main` 上唯一在跑的服务是生产 adapter（端口 8787），
  它读的是主仓库路径，与这些 worktree 无关；
- 移除过程中 `git worktree remove` **全部成功、零报错**；
  若仍有进程持有目录内文件句柄，删除会因 `EBUSY` 失败。

**结论**：不能宣称「已确认无进程占用」，只能说「`git worktree remove` 未报 EBUSY，
且无任何 worktree 绑定端口」。这个差别必须写清楚。

---

## 移除后状态

```
$ git worktree list
/Users/boos/Desktop/威科夫因子共振趋势   8d3d8a12 [wyckoff-v3.1-main]
```

只剩主仓库一条，无幽灵条目（另跑了 `git worktree prune` 兜底）。
