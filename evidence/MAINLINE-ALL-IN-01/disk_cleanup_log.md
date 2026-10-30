# 磁盘清理日志

## 一、结果先说

| 项 | 值 |
|---|---|
| 清理前可用 | **20 GiB** |
| 清理后可用 | **24 GiB** |
| 净增 | **+4 GiB** |
| 逻辑移除量 | **约 9.5 GB**（11 个 worktree）+ 37 MB（`.git` gc） |
| 目标 | 50–60 GB ⇒ **未达成** |
| 原因 | 剩余大头全部落在「禁删」清单内；且 APFS 延迟块回收只当场归还了约 4 GB |

---

## 二、逐项清理记录

| # | 路径 | 大小 | 性质 | 前置检查 | 处置 |
|---|---|---|---|---|---|
| 1 | `/private/tmp/allin-baseline` | 2.0 G | 本轮自建的基线对照 worktree | 独有提交 0 | `git worktree remove --force` |
| 2 | `/private/tmp/pr2-wo-01-baseline` | 98 M | 隔离 worktree | 独有提交 0，仅 node_modules | 同上 |
| 3 | `/private/tmp/pr2-paper-entry-harden` | 98 M | 隔离 worktree | 测试文件已合入主线 | 同上 |
| 4 | `/private/tmp/pr2-wo-02-work` | 99 M | 隔离 worktree | 与 recovered-harden 同源 | 同上 |
| 5 | `/private/tmp/gm-ts-baseline-fix` | 302 M | 隔离 worktree | 5 个前端组件与主线逐字相同 | 同上 |
| 6 | `/private/tmp/gm-pra-verify` | 302 M | 隔离 worktree | 同上 + workflow 相同 | 同上 |
| 7 | `/private/tmp/pr2-delivery-verify-02` | 583 M | 隔离 worktree | 「独有改动」是旧版残留 | 同上 |
| 8 | `/private/tmp/pr2-integration-exec-01` | 2.0 G | 隔离 worktree | 证据已复制入主线 | 同上 |
| 9 | `/private/tmp/pr2-recovered-harden-01` | 2.0 G | 隔离 worktree | fail-closed 常量主线齐全 | 同上 |
| 10 | `/private/tmp/backtest-repair-01` | 117 M | 隔离 worktree | 证据已复制入主线，代码已三方合并 | 同上 |
| 11 | `~/.gemini/antigravity/worktrees/…/evidence_correction_and_review` | 2.0 G | 反重力 worktree | 3 个 evidence 目录 `diff -rq` 完全相同 | 同上 |
| 12 | `.git` 对象库 | 449 M → 412 M | `git gc --prune=now` | — | 已回收 37 M |

**全部走 `git worktree remove`，没有一处 `rm -rf`。** 事后 `git worktree list` 只剩主仓库一条。

---

## 三、为什么没到 50–60 GB（算给你看）

清理后 `df -h /`：`228Gi 总量 / 204Gi 已用 / 24Gi 可用`。

本项目侧剩余占用与禁删依据：

| 目录 | 占用 | 禁删依据 |
|---|---|---|
| `数据/市场数据/parquet` | 19 G | 正式历史行情 Parquet |
| `数据/市场数据/db`（`market_v2.sqlite3`） | 16 G | 生产热库 |
| `数据/研究输入/WyckoffMaster` | 9.0 G | `_backup/20261014T190050Z` 下的 `ohlcv`(3.1 G)、`oi_metrics`(5.5 G) 在父目录**没有对应副本** ⇒ 唯一副本 |
| `数据/研究输入/WyckoffMaster_v31` | 8.0 G | `is`/`val` 研究数据集，唯一副本 |
| `数据/raw` | 9.6 G | 原始下载（含原始 ZIP） |
| `数据/raw_oi_metrics` | 6.9 G | 原始下载 |
| `evidence` | 6.0 G | 审计证据 |
| `回测日志` | 1.6 G | 回测产物/可复核证据 |
| **小计** | **76.1 G** | 全部不可删 |

也就是说：**在「禁删生产 SQLite / 正式 Parquet / 原始 ZIP / 账本 / 唯一副本」的前提下，
本项目侧不存在 30 GB 级别的安全删除对象。** 这是算术问题，不是没找。

要突破 50–60 GB，只有三条路，都需要你拍板（见 `pending_decisions.md`）：

1. 删 `数据/研究输入`（17 G）—— 但这会把两套研究数据集的唯一副本删掉；
2. 删 `数据/raw` + `raw_oi_metrics`（16.5 G）—— 这是原始下载，删了无法重跑历史回填；
3. 清系统级目录（`/private/var` 16 G、`~/Library` 6.6 G、`/private/var/folders` 12 G）—— 超出本轮授权范围。

---

## 四、APFS 延迟回收：这是实测，不是猜测

上一轮做过受控实验：写入 500 M **当场扣减**可用空间，删除同一份 500 M **当场不归还**。
上一轮逻辑删除 20.9 GB，当场只 +4 GB，**次日自动补足到 19 GiB**。

本轮同样现象：逻辑移除 9.5 GB，`df` 只 +4 GiB。
`tmutil listlocalsnapshots /` 输出为空（**没有本地快照占用**），
`sudo` 被拒，无法查 root 持有的文件句柄。

**所以本文件只能写「已逻辑删除约 9.5 GB、当场归还约 4 GB、其余待系统回收」，
不能写「已释放 9.5 GB」。**

---

## 五、没有动的东西（避免误伤）

- `数据/paper/*.sqlite3` 四本账 —— 只读打开过，主文件 mtime 未变
- `数据/市场数据/**` —— 一个字节没动
- `evidence/**` 既有目录 —— 只新增 `MAINLINE-ALL-IN-01/` 与两个复制进来的证据目录
- `回测日志/**` —— 未动
- `UI界面/web/node_modules` —— 未动（前端要能构建）
