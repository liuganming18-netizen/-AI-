# PR2-INTEGRATION-EXEC-01 · 四项已验收成果统一集成 · 施工报告

- 工单：PR2-INTEGRATION-EXEC-01（P0，执行方 WorkBuddy）
- 报告时刻：2026-10-29T06:30Z（本机 14:30 CST）
- 性质：**未提交的集成成果 + 本地证据**。本单不授权 commit / push / merge / 改 PR 状态 / 部署，已严格未做。
- 一句话结论：**四项成果已按准确差异整合进同一棵树，逐字节与各自来源相同，未发生任何文本冲突；前端 30 项专项断言全过、后端 88 项测试 87 过 1 失败（失败已归因为沙箱环境，与集成无关）、8 项跨模块反例全过。集成成果本身达到可独立验收状态；但「日常部署可交付」仍不通过，因为真实挂钟自动成交未验证，且开仓三重阻塞未决——这些不在本单授权范围内。**

---

## 一、集成工作树

| 项 | 值 |
|---|---|
| 路径 | `/private/tmp/pr2-integration-exec-01` |
| 分支 | `pr2-integration-exec-01`（以固定基线新建） |
| HEAD | `9c8929d50a60ffa897496d028bff11649bfdbcfd`（**与工单指定基线一致，未产生任何提交**） |
| 工作树状态 | 5 个已跟踪文件被修改（未暂存）+ 35 个新增未跟踪文件（10 测试/探针 + 25 证据）+ 1 个 `node_modules` 符号链接 |
| 暂存区 | **空**（全程未执行 `git add`，未使用 `git add -A`） |
| 提交 | **0 个新提交** |

`node_modules` 说明：裸工作树里没有前端依赖（`node_modules` 被 `.gitignore` 排除）。为能跑 `tsx / tsc / vite`，在 `UI界面/web/node_modules` 建了指向主仓库同名目录的**符号链接**，与 `pr2-wo-01-work` 同样做法。它是未跟踪项，不会被误提交（本单也没做任何 add）。

四个来源工作树全部**只读**：只做了 `git rev-parse` / `git status` / `shasum` / `git diff`，未执行 `reset`、`clean`、`checkout`、`apply` 或任何写操作。来源树的未提交差异原样保留（见 `out/06_跨树哈希比对.txt` 第一节）。

---

## 二、每项成果的准确来源与哈希

### 2.1 WO-01 前端显示语义最终版

- 来源：提交 `7993d859b1b8bb4fddc36ac0774ca769bbe8aedb`（父提交 = 集成基线 `9c8929d5`）
- 来源树：`/private/tmp/pr2-wo-01-work`，分支 `pr2-wo-01-display-semantics`，**工作树干净**（除 `node_modules` 链接）
- 引入方式：按提交取**准确八文件**，不是整目录覆盖
- 八文件哈希（sha256 前 16 位，与来源树逐位一致）：

| 文件 | 增/删 | sha256(16) |
|---|---|---|
| `UI界面/web/src/api/adapters.ts` | +5 / −1 | `d7f23a14bb539a71` |
| `UI界面/web/src/components/ObservePanel.tsx` | +164 / −23 | `ae426fa634b03afb` |
| `UI界面/web/tests/README-PR2-WO-01.md` | +57 | `2efd605fb3234fec` |
| `UI界面/web/tests/display-semantics.dom.harness.tsx` | +115 | `ad1a1b69613c3066` |
| `UI界面/web/tests/display-semantics.dom.test.mjs` | +131 | `00ab287b169e8af6` |
| `UI界面/web/tests/display-semantics.pure.test.ts` | +259 | `30eccf655f1155d4` |
| `UI界面/web/tests/pr2-before-after-probe.mjs` | +60 | `07f3ad6ea20aba90` |
| `UI界面/web/tests/pr2-trigger-probe.ts` | +18 | `29809bd48a4bb3dd` |

**未把原工作树里的旧稿当成 WO-01**：主工作树 `evidence/PR2-WO-01/README.md`（未跟踪）描述的是早期旧稿（3 文件 / 单测试文件 / 无 DOM 层），本单**未引用、未搬入**，另写 `evidence/PR2-WO-01-VERSION-CORRECTION/README.md` 做只追加的版本更正。

### 2.2 WO-02 + REWORK-01 Fail-Closed

| 段 | 来源 | 内容 |
|---|---|---|
| WO-02 基线成果 | 提交 `f348ff0f`（代码 + 测试）+ `acb5c2b2`（证据文档） | `live_api.py` +136/−32、`unified_runtime.py` +354/−50、`tests/pr2_wo_02_probe.py` +144、`tests/test_pr2_wo_02_fail_closed.py` +569、`evidence/PR2-WO-02/README.md` +391（共 4 文件 +1203/−82，另 1 文件 +391） |
| REWORK-01 | `/private/tmp/pr2-wo-02-work` 三份**未提交**差异 | `框架/backend/unified_runtime.py`、`框架/backend/tests/test_paper_live_01.py`、`框架/backend/tests/test_pr2_wo_02_fail_closed.py` |

REWORK-01 补丁身份（归档在 `evidence/PR2-RECOVERED-HARDEN-01/out/前置_REWORK-01补丁.patch`）：

- 行数 345，sha256 `1a9070842e9fdf7b6bc3b8afa1e9a2972fd4b5f99bb26715609a3439e28f5ea6`
- 引入方式：`git diff -- <三个文件> > patch` 后 `git apply -p1`，**从未在来源树里 checkout / reset**

三份差异落到集成树后的哈希：

| 文件 | REWORK-01 后 sha256(16) | 最终（再叠 Recovered 后） |
|---|---|---|
| `框架/backend/unified_runtime.py` | `32d231ec3eefbeb3` | `4e3a7fd249635734` |
| `框架/backend/tests/test_paper_live_01.py` | `54852ec63f66cc17` | 不变 |
| `框架/backend/tests/test_pr2_wo_02_fail_closed.py` | `33103501554e616f` | 不变 |

WO-02 自带的探针文件 `框架/backend/tests/pr2_wo_02_probe.py` 不在 REWORK-01 范围内，哈希 `e6027c6796d9ffa2`（与来源树一致）。

### 2.3 RECOVERED-HARDEN-01

- 来源树：`/private/tmp/pr2-recovered-harden-01`，分支 `pr2-recovered-harden-01`，HEAD `acb5c2b2`（= WO-02 + REWORK-01 之上）
- 生产代码只动一个文件：`框架/backend/unified_runtime.py`
- 落地哈希：`4e3a7fd249635734dbcf8bf3bf0a93e2c3272f09ec9583145c686339581f7263`（sha256 前 16 位 `4e3a7fd249635734`，与来源树逐位一致）
- 新增测试：`框架/backend/tests/test_pr2_recovered_harden.py`，sha256(16) `146935881c2c7179`
- 附带证据整目录引入：`evidence/PR2-RECOVERED-HARDEN-01/`（README + 8 个 out + 2 个 scripts，共 11 个文件）

### 2.4 PAPER-ENTRY-HARDEN-01

- 来源树：`/private/tmp/pr2-paper-entry-harden`，HEAD `acb5c2b2`
- 生产代码只动一个文件：`框架/backend/live_api.py`
- 落地哈希：`768941b2eda61e73b19d7ca7cfa9ee13dd30bfc5568cd0665d9b155b35eb776a`（sha256 前 16 位 `768941b2eda61e73`，与来源树逐位一致）
- 新增测试：`框架/backend/tests/test_pr2_paper_entry_harden.py`，sha256(16) `80524a93ce00a59a`（14 用例）

> **关键判断**：Paper Entry 树里的 `unified_runtime.py` 与 WO-02+REWORK-01 版哈希相同（`32d231ec3eefbeb3`），即它**不含** Recovered 改动 ⇒ `unified_runtime.py` 只需引入 Recovered 一组的差异，两单在同一个文件上没有交叠，不存在需要人工合并的冲突。

---

## 三、文件级集成清单

### 3.1 生产代码（白名单内，共 4 个）

| 文件 | 来源 | 最终 sha256(16) |
|---|---|---|
| `UI界面/web/src/api/adapters.ts` | WO-01 | `d7f23a14bb539a71` |
| `UI界面/web/src/components/ObservePanel.tsx` | WO-01 | `ae426fa634b03afb` |
| `框架/backend/live_api.py` | WO-02 代码 + Paper Entry 差异 | `768941b2eda61e73` |
| `框架/backend/unified_runtime.py` | WO-02 代码 + REWORK-01 差异 + Recovered 差异 | `4e3a7fd249635734` |

### 3.2 测试与探针（10 个）

前端（来自 WO-01 提交，6 个）：`tests/README-PR2-WO-01.md`、`tests/display-semantics.pure.test.ts`、`tests/display-semantics.dom.test.mjs`、`tests/display-semantics.dom.harness.tsx`、`tests/pr2-before-after-probe.mjs`、`tests/pr2-trigger-probe.ts`

后端（4 个）：`tests/test_pr2_wo_02_fail_closed.py`、`tests/pr2_wo_02_probe.py`、`tests/test_pr2_recovered_harden.py`、`tests/test_pr2_paper_entry_harden.py`

另有 1 个已跟踪测试文件被修改：`tests/test_paper_live_01.py`（REWORK-01，+106 / −7）。

### 3.3 证据（25 个，分四处）

- `evidence/PR2-WO-02/README.md`（WO-02 原文）
- `evidence/PR2-RECOVERED-HARDEN-01/`（11 个，原文）
- `evidence/PR2-WO-01-VERSION-CORRECTION/README.md`（本单新写，只追加更正）
- `evidence/PR2-INTEGRATION-EXEC-01/`（本单新写：README + 4 脚本 + 7 产物，共 12 个）

### 3.4 禁改项核对（应全空，实测全空）

`框架/backend/paper_engine.py`、`live_store.py`、`live_signal_engine.py`、`research_signal_import.py`、`strategy_contracts.py`、`策略/`、`project_state.json`、`数据/`、`框架/backend/locks` —— `git status --porcelain` 对这些路径**零输出**（证据见 `out/05_文件清单与哈希.txt` 第四节）。

运行时参数身份未漂移：`paper_engine.approved_params()` 的 `view_sha256 = 82dec362c182`（与生效视图 `10_研究参数生效视图_v2_2_user5000_shared.json` 一致），`short_enabled = False`，`max_positions = 10`。

---

## 四、实际差异统计

相对基线 `9c8929d5`（已跟踪文件）：

```
 5 files changed, 1189 insertions(+), 181 deletions(-)
```

| 文件 | 增 | 删 |
|---|---|---|
| `UI界面/web/src/api/adapters.ts` | 5 | 1 |
| `UI界面/web/src/components/ObservePanel.tsx` | 164 | 23 |
| `框架/backend/live_api.py` | 204 | 83 |
| `框架/backend/tests/test_paper_live_01.py` | 106 | 7 |
| `框架/backend/unified_runtime.py` | 710 | 67 |
| **合计** | **1189** | **181** |

新增未跟踪文件 35 个（10 测试/探针 + 25 证据），合计 7,794 行（含补丁与测试日志等产物），不含 `node_modules` 链接。

完整补丁归档：`out/00_集成差异.patch`（1,796 行，sha256 `fcd4eab436572b1a…`）。

---

## 五、冲突处理记录

**结论：文本层面零冲突，每次引入前都先 `git apply --check`，全部一次通过，无需人工合并任何一行。**

（前提事实：WO-01 提交 `7993d859` 与 WO-02 提交 `f348ff0f` 的**父提交都是集成基线 `9c8929d5`**，属两条兄弟分支，且改动文件集合不交叠——前者只碰前端，后者只碰后端两个文件。REWORK-01 / Recovered / Paper Entry 三份未提交差异各自只在上游成果之上追加，因此四份来源可以串行叠加而互不覆盖。）

之所以没有冲突，是因为四个来源的改动文件集合在「时间上串行、空间上不交叠」：

| 文件 | 被哪些来源改 | 处理方式 |
|---|---|---|
| `adapters.ts`、`ObservePanel.tsx`、前端 6 个测试 | 仅 WO-01 | 直接按提交取文件 |
| `live_api.py` | WO-02（提交）→ Paper Entry（未提交差异） | 串行：先落 WO-02 版 `a890caf5`，再 apply Paper Entry 差异 → `768941b2` |
| `unified_runtime.py` | WO-02（提交）→ REWORK-01（未提交差异）→ Recovered（未提交差异） | 串行：WO-02 版 → apply REWORK-01 → `32d231ec` → apply Recovered → `4e3a7fd2` |
| `test_paper_live_01.py`、`test_pr2_wo_02_fail_closed.py` | WO-02（提交）→ REWORK-01（未提交差异） | 串行；Paper Entry 树里这两个文件与 REWORK-01 版哈希相同，不重复引入 |

**交叉依赖处理**：

1. `unified_runtime.py` 需要 `live_api.py` 的 `actionable_scope_where()` / `resolve_live_floor_ms()`；`live_api.py` 只在函数内部延迟 `import unified_runtime`。这个**单向依赖方向被完整保留**，没有形成循环导入。
2. `live_api.py` 里两组防护都保留：
   - WO-02 组（20 处匹配）：`ACTIONABLE_ORIGINS` / `NON_ACTIONABLE_ORIGINS` / `actionable_scope_where` / `APPROVED_SQL` / `LIVE_WATERMARK_UNAVAILABLE` / `resolve_live_floor_ms`
   - Paper Entry 组：`external_clock_forbidden`(1158) / `external_origin_forbidden`(1162) / `signal_store_unavailable`(1183) / `origin_not_actionable`(1203) / `manual_cannot_reference_signal`(1205) / `arm_mismatch_with_signal`(1214)
3. `unified_runtime.py` 里两组防护都保留：
   - REWORK-01 组：`origin_unknown:{declared}`（2720 注释 / 2742 代码）
   - Recovered 组：`RC_WINDOW_EXPIRED`(84) / `CF_UPGRADE_REFUSED`(93) / `validate_recovered_freshness`(2411) / `classify_uid_conflict`(2475)

**未恢复 short 可执行入口**（工单明令）。核验见 `out/04_short仅展示层核验.txt`：`live_api.py` 里 `short` 共 4 处匹配（2237 / 2253 / 2255 / 2269），全部落在 `_backtest_review_legacy`（回测复盘只读载荷）的展示字段与收益率符号计算里，**没有任何 short 开仓分支**。可执行入口仍受三道独立拦截：准入层 `direction_not_long:short`、开仓前二次守卫（`direction != "long"` 即 `continue`）、引擎层 `short_enabled=False` → `short_disabled_by_frozen_view_only_long`。

**未引入 PR #1 根目录死文件**：本树相对基线只多出上面列的 35 个文件，根目录无新增。

**未整包合并 PR #2**：没有用任何工作树的完整目录覆盖另一棵树，全部按文件级准确差异引入；跨树 16 个文件哈希逐一比对全部「一致」（`out/06_跨树哈希比对.txt`）。

---

## 六、前后端全部测试：命令、退出码、数量

一键脚本：`sh scripts/02_统一回归.sh /private/tmp/pr2-integration-exec-01 out/02_统一回归.txt`
环境：Python 3.9.6（`/usr/bin/python3`）、Node v22.22.2、macOS，跑测时刻 2026-10-29 14:19:51 CST。

### 6.1 前端（30 项专项断言，5 项全过）

| # | 项 | 命令 | 退出码 | 结果 |
|---|---|---|---|---|
| 1 | 纯逻辑 18 条 | `./node_modules/.bin/tsx tests/display-semantics.pure.test.ts` | 0 | `18/18 通过` |
| 2 | DOM 渲染 12 条 | `node tests/display-semantics.dom.test.mjs`（rolldown 打包 + 真实 Chromium） | 0 | `12/12 通过` |
| 3 | TypeScript | `./node_modules/.bin/tsc --noEmit` | 0 | 无输出（无错） |
| 4 | Lint | `./node_modules/.bin/tsc --noEmit -p tsconfig.json`（仓库 lint 脚本就是 tsc，无 ESLint 依赖） | 0 | 无输出 |
| 5 | 构建 | `VITE_CACHE_DIR=/tmp/pr2-int-vite-cache ./node_modules/.bin/vite build` | 0 | `✓ built in 462ms` |

### 6.2 后端（88 项，87 过 1 失败）

| # | 套件 | 命令 | 退出码 | 数量 | 结果 |
|---|---|---|---|---|---|
| 1 | WO-02 Fail-Closed | `python3 -m unittest tests.test_pr2_wo_02_fail_closed` | 0 | 20 | `OK` |
| 2 | Recovered 加固 | `python3 -m unittest tests.test_pr2_recovered_harden` | 0 | 22 | `OK` |
| 3 | Paper Entry 加固 | `python3 -m unittest tests.test_pr2_paper_entry_harden` | 0 | 14 | `OK` |
| 4 | Lifecycle 隔离 | `python3 -m unittest tests.test_lifecycle_isolation` | 0 | 18 | `OK` |
| 5 | Live Pool 准入 | `python3 -m unittest tests.test_live_pool_admission` | 0 | 9 | `OK` |
| 6 | Paper Live | `python3 -m unittest tests.test_paper_live_01` | **1** | 5 | `FAILED (failures=1)` |
| | **合计** | | | **88** | **87 通过 / 1 失败** |

### 6.3 唯一失败的定性：环境所致，与集成无关

失败用例：`test_paper_live_01.TestPaperLive01.test_02_lock_concurrency_and_pid_auto_sync`

```
AssertionError: None != 71282 : ServiceManager 应成功探测到正在运行的存活 PID
```

归因（证据 `out/03_既有失败归因.txt`，可复现）：

1. `ServiceManager._find_active_runtime_pid()` 要靠 `get_process_cmdline(pid)` 命中 `unified_runtime.py`（或隔离模式下的 `python` / `test`）才认身份；
2. 本沙箱里 `ps` 被直接拒绝——`ps -o command= -p $$` 返回 `operation not permitted: ps`；
3. `/usr/bin/python3` 没装 `psutil`（`No module named 'psutil'`）；
4. ⇒ `get_process_cmdline()` **恒为空串** ⇒ 三条兜底路径全部落空 ⇒ 返回 `None` ⇒ 断言必失败。

同时该失败在**上游基线树（WO-02 + REWORK-01，不含 Recovered / Paper Entry）上同样失败**，见 `evidence/PR2-RECOVERED-HARDEN-01/out/02_既有失败基线对照.txt` A 段（该单的基线树 `None != 65523`、修复后 `None != 65539`，两树同形——即修复前后都失败，与本单集成无关）。

同一份对照还记录了 `tests.test_system_startup_01` 的 5 条既有 error（起真实 API 子进程，受端口/环境抖动），两棵树各连跑 3 次均 `errors=5`。该套件不在本工单 88 项清单内，未计入。

---

## 七、跨模块反例结果（8/8 通过）

脚本：`python3 scripts/01_跨模块反例.py` → 退出码 0 → `out/01_跨模块反例.json`（`"ok": 8, "total": 8`，2026-10-29T06:19:19Z）

| # | 检查 | 实测结果 |
|---|---|---|
| 1 | live 信号从准入到隔离模拟挂单 | HTTP 200、`quality.status=pending`、`paper_pending` 新增 1 行 |
| 2 | recovered 过期信号不能进入模拟挂单 | 落库侧 `blocked` + `recovered_window_expired`；HTTP 侧 400，`note=executable_state_not_enterable:blocked,approved_false,before_effective_floor,approved_false` |
| 3 | historical_research / replay / test UID 不能经 HTTP 绕过 | 四条全部 400：`origin_not_actionable:historical_research` / `origin_not_actionable:replay` / `origin_not_actionable`(test) / `test_signal_uid` |
| 4 | 同 UID 冲突不能把 blocked 升级为 enterable | 写入被拒 `conflict_upgrade_refused:blocked->enterable`，库内仍 `executable_state=blocked` |
| 5 | 手动模拟订单仍可登记 | 200；新身份 `manual:a58890b7dcee4b4e9c37587f6f292447`、重试身份 `manual:1793254757449` 均可登记 |
| 6 | 前端不得把 K 线收盘价误标为实时最新价 | DOM 套件退出码 0，`12/12 通过` |
| 7 | 外部 HTTP 时钟注入被拒绝 | `now_ms` → 400 `external_clock_forbidden`；`requested_at` → 400 `external_clock_forbidden`；`origin=replay` / `origin=test` → 400 `external_origin_forbidden` |
| 8 | long-only 执行白名单保持不变 | `ACTIONABLE_ORIGINS = ("live","recovered")` 未扩大；short 信号准入原因 `direction_not_long:short`；HTTP 开仓 400、挂单 0 |

全部测试使用隔离临时数据库、模拟账本、临时锁目录与只读合约规格（`CONTRACT_SPEC_CSV` 指向真实工程目录的只读 CSV），未碰生产库、未启服务。

---

## 八、与已验收版本的行为差异

**单个模块层面：零差异。** 四个生产文件与各自来源树**逐字节相同**（`out/06_跨树哈希比对.txt`，16 个文件全部「一致」）。集成过程没有对来源内容做任何改写、删减或补丁式修补，所以每个已验收成果的行为与它单独验收时完全一样。

**组合层面：新增的是「叠加后的顺序」，不是新逻辑。** 值得记录的三点：

1. **recovered 信号现在要连过两道时效门**：落库时按实际处理时刻查窗（Recovered 的 `validate_recovered_freshness`），HTTP 准入时再按读取时刻查窗（WO-02 的 `signal_admission_reasons` + `resolve_live_floor_ms`）。两者独立，任一道不过即拒。反例 2 实测两道都生效（写入端 `recovered_window_expired`、读取端 `executable_state_not_enterable:blocked`）。
2. **手动下单同时受两组防护约束**：WO-02 的准入判据（来源白名单、UID 前缀、approved）先判，Paper Entry 的请求体防护（外部时钟、外部来源、`manual` 不得引用信号、臂不一致、符号不一致）随后判。两组都通过才走到 `PE.open_position`。
3. **`unified_runtime` 的自动开仓多了一道方向二次守卫**（`direction != "long"` 即 `continue`），这是 REWORK-01 引入的，与 Recovered 的恢复加固不冲突。

**未改变**：15 分钟执行时序、OI 规则、风险参数、`FILL_POLICY`、跟踪止损口径、`paper_engine.py` 一行未动、数据库结构未动。

---

## 九、未解决风险

按严重度排序，全部**如实记为未解决**，不写成「已通过」。

### 9.1 阻塞「日常部署可交付」的（本单无权处置）

1. **真实挂钟自动成交仍未验证**：自然候选 0、账本 0 笔已平。集成只保证「信号到了会按规则拦或放」，不保证「真实时间里会自然产生信号」。
2. **开仓三重阻塞未决**：① `06-W1-R1` 待决 21 项；② 两套经济口径（3bps/5bps、2000/5000、0.1%/1.0%）未择一，运行时取生效视图那侧（5000 / 10 仓 / 5bps / 1.0%，`view_sha256=82dec362c182`）；③ 跳空成交价模型与滑点待批。
3. **`FILL_POLICY` 仍有 5 项待裁决**（均属改代码，本单禁止）：`fill_ts` 语义（是循环起始时刻而非撮合完成时刻）、缺 `bar_close <= attempt_ms` 校验、挂单推进路径上 `is_replay` 恒 True 致因果校验从不执行、`live_api.py:806-809` 用户可见 `quality.note` 仍是旧公式（同一响应内与 `fill_policy.no_fill_before` 自相矛盾）、`paper_engine.py:989-990` docstring 旧公式。

### 9.2 集成层面新观察到的（轻微，仅记录）

4. **准入原因码会重复出现**：`approved_false` 与 `origin_not_actionable` 在 `note` 里各出现两次（`signal_admission_reasons` 与 `live_api.py` 的补充判据都追加了一遍）。语义无错、拒单行为正确，只是原因串冗余，前端若做去重统计会重复计数。
5. **前端 `node_modules` 是符号链接**：裸工作树不带依赖，验收方在新机器上复跑前必须自己建链接或 `npm i`，否则前端 5 项跑不了（脚本会先检查并明确报「致命」退出码 2）。

### 9.3 环境层面

6. **本沙箱 `ps` 被拒 + 无 `psutil`** ⇒ 任何依赖「读进程命令行」的断言（如 `test_02_lock_concurrency_and_pid_auto_sync`、`test_system_startup_01`）在本机必然失败。这类失败**不能当作代码缺陷**，但也不能当作通过——记为「本环境不可验证」。

---

## 十、是否达到最终独立验收条件

**分两层回答，不含糊。**

**第一层：本单交付物（集成成果）——达到可独立验收状态。**

- 四项成果全部落地，来源可追溯到提交号 / 未提交差异 + 逐文件哈希；
- 与各自来源树逐字节相同（16 文件全「一致」），没有任何「集成时顺手改了一行」的隐患；
- 前端 30 项专项断言全过（18 纯逻辑 + 12 DOM），TypeScript / Lint / Build 三项退出码 0；
- 后端 88 项测试 87 过，唯一失败已用可复现的命令归因到沙箱缺 `ps`/`psutil`，且上游基线树同形；
- 8 项跨模块反例全过，含工单点名的每一条；
- 禁改项全空，运行时参数身份未漂移（`view_sha256=82dec362c182`）；
- 未 commit / 未 push / 未 merge / 未改 PR / 未部署，工作树停在可验收状态。

**第二层：系统「日常部署可交付」——仍不通过。**

理由与第九节 9.1 一致：真实挂钟自动成交未验证、开仓三重阻塞未决、`FILL_POLICY` 5 项待裁决。这些都在本单授权之外，本单**不具备也不宣称**处置权。

建议下一单 `PR2-FINAL-ACCEPT-01` 的验收口径：把「集成一致性」与「系统可交付性」分开判——前者可以判通过，后者只能判「未验证」或「不通过」，不要因为前者漂亮就顺带把后者写成通过。

---

## 附录 A · 证据文件索引（`evidence/PR2-INTEGRATION-EXEC-01/`）

| 文件 | 内容 |
|---|---|
| `scripts/01_跨模块反例.py` | 8 项跨模块反例，继承 `HardenBase`，隔离沙盒 |
| `scripts/02_统一回归.sh` | 前端 5 项 + 后端 6 套件一键跑 |
| `scripts/03_文件清单与哈希.sh` | 已跟踪/未跟踪文件清单 + 哈希 + 禁改项核对 |
| `scripts/04_跨树哈希比对.sh` | 集成树 vs 四个来源树逐文件比对（只读） |
| `out/00_集成差异.patch` | 相对基线的完整差异（1,796 行） |
| `out/01_跨模块反例.json` | 8/8 通过的结构化结果 |
| `out/02_统一回归.txt` | 前后端全部测试原始输出 |
| `out/03_既有失败归因.txt` | `test_02` 失败的沙箱归因（可复现） |
| `out/04_short仅展示层核验.txt` | 4 处 `short` 匹配逐处定性 + 三道拦截 |
| `out/05_文件清单与哈希.txt` | 文件清单、哈希、禁改项空证据 |
| `out/06_跨树哈希比对.txt` | 16 文件跨树「一致」+ 来源树状态快照 |

## 附录 B · 复现步骤

```bash
cd /private/tmp/pr2-integration-exec-01

# 1) 文件清单与禁改项核对
sh evidence/PR2-INTEGRATION-EXEC-01/scripts/03_文件清单与哈希.sh \
   /private/tmp/pr2-integration-exec-01 /tmp/05.txt

# 2) 跨树哈希比对（只读来源树）
sh evidence/PR2-INTEGRATION-EXEC-01/scripts/04_跨树哈希比对.sh /tmp/06.txt

# 3) 跨模块反例
cd /private/tmp/pr2-integration-exec-01
CONTRACT_SPEC_CSV="/Users/boos/Desktop/威科夫因子共振趋势/数据/研究输入/外部迁移_2026-10-16/本次离线决策_2026-10-12/07_526币保守执行近似规格.csv" \
  /usr/bin/python3 evidence/PR2-INTEGRATION-EXEC-01/scripts/01_跨模块反例.py

# 4) 统一回归（前端 5 项 + 后端 6 套件）
sh evidence/PR2-INTEGRATION-EXEC-01/scripts/02_统一回归.sh \
   /private/tmp/pr2-integration-exec-01 /tmp/02.txt
```

前置条件：`UI界面/web/node_modules` 需存在（本树是符号链接，指向主仓库同名目录）。

---

**施工完成，立即停止。等待主代理派发 PR2-FINAL-ACCEPT-01。**
