# PR2-WO-02 信号可执行范围 Fail-Closed 加固 — 施工与验证记录

- 施工方：WorkBuddy
- 施工时间：2026-10-29
- 隔离工作树：`/private/tmp/pr2-wo-02-work`
- 隔离分支：`pr2-wo-02-fail-closed`，基线 `9c8929d5`
- 代码提交：`f348ff0f`（4 个文件，**未推送、未合并、未开 PR**）
- 对照基线树：`/private/tmp/pr2-wo-01-baseline`（同一 commit `9c8929d5`，除测试所需参数视图外未改动）
- 状态：**施工与隔离测试完成，等待 Gemini 独立验收**

---

## 一、结论摘要

四项缺陷全部**在施工前实测复现**，修复后全部转为 fail-closed；正常信号行为**逐字段不变**；全量回归**零新增失败**。

| 缺陷 | 施工前实测 | 修复后实测 |
|---|---|---|
| A 共振评估抛异常 | `executable_state=enterable`、`resonance_score=60.0`、`position_multiplier=1.0`、payload 无 `approved` 键、**挂单 1 笔** | `blocked`、`None`、`None`、`approved=False`、**挂单 0 笔** |
| B 时间倒挂 / `available_at` 缺失 | 进可执行范围，登记挂单 | `blocked` + 扫描层 SQL 双层拒绝 |
| C 测试 UID `sig_ui_test_*` | `registered=1`、**挂单 1 笔** | `registered=0`、挂单 0 笔 |
| C 历史来源 `historical_research` + 未传 origin | `registered=1`、**挂单 1 笔** | `registered=0`、`blocked_reason=origin_scope_required` |
| C 方向字段缺失 | `registered=1`、写入 `('long','pending')`（**替用户猜了方向**） | `registered=0`、无挂单 |
| D 水位不可用 | 抛 `RuntimeError: db_unreadable` | `blocked_reason=live_watermark_unavailable`、`quality.status=no_watermark`、`quality.reason` 明确 |

---

## 二、施工前后代码差异

### 2.1 判据集中化（本次施工的骨架决策）

改动前，「当前区能不能看到」由 `live_api.py` 里硬编码的字符串决定，「路由能不能扫到」由 `unified_runtime._scan_actionable` 里另一套字符串决定。两套判据一旦分叉，就会出现「页面看不到、账本却在开仓」或反过来——这正是本工单要堵的 fail-open 的结构性根因。

现在判据只在 `live_api.py` 定义一次，`unified_runtime.py` **引用同一批常量与同一个 SQL 片段构造器**：

```python
# unified_runtime.py:41
import live_api as _SCOPE          # PR2-WO-02：可执行范围判据的唯一来源（读写同源）
```

依赖方向安全：`live_api` 是轻量只读模块（只依赖 stdlib + `live_store`/`paper_engine`），由 `unified_runtime` 引用，反向不成立，不形成循环导入。

### 2.2 差异规模

```
框架/backend/live_api.py                     | 168 ++++--
框架/backend/unified_runtime.py              | 404 ++++++++++++++---
框架/backend/tests/test_pr2_wo_02_fail_closed.py | 569 +++++++++++++++++++++
框架/backend/tests/pr2_wo_02_probe.py        | 144 ++++++
4 files changed, 1203 insertions(+), 82 deletions(-)
```

### 2.3 修复 A：共振失败不再伪造分数

改动前（`unified_runtime.py` 原 `persist_candidates`）：

```python
try:
    res = wyckoff_factor_resonance(...)
except Exception:
    res = {"score": 60.0, "multiplier": 1.0, "grade": "基础"}   # ← 伪造一个「看起来正常」的分
...
exec_state = "enterable"                                       # ← 字面量，与评估结果无关
```

缺陷本质：**「评估失败」这件事在数据上完全看不出来**。分数被伪造成中性分 60，状态被写成字面量 `enterable`，payload 里也没有 `approved=false`，于是信号照样进可执行扫描、照样登记挂单。

修复后（`unified_runtime.py:2193` `evaluate_resonance_guarded`）：

```python
def evaluate_resonance_guarded(c) -> dict:
    """共振评估的 fail-closed 包装。ok=False 时 score/multiplier 恒为 None。"""
```

三类情况分别覆盖：
- **异常**：`except Exception` → `ok=False`，不伪造分
- **缺数据**（`candidate_input_reasons`，`unified_runtime.py:2142`）：`missing_fields` 非空、四个必需数值缺失/非法、`box_high_h <= box_low_l`、`box_atr_a <= 0`、OI 输入全空
- **非法结果**（`resonance_result_reasons`，`unified_runtime.py:2171`）：非 dict、`score` 缺失/非数/越界、`multiplier` 缺失/非数/`<= 0`

判定原则写进代码注释：**只降不升**，任何一项说不清就落 `blocked`，绝不猜一个默认分。

**边界克制**：`candidate_input_reasons` 明确**不**检查外部微观指标（巨鲸成本 / Taker / 多空比）是否可读。那些缺失是 `wyckoff_factor_resonance` 的设计内情形（按中性分处理，不拒杀），把它们算成「缺数据」会让正常信号在指标断流时被全量阻断——那是超出本工单的行为变更。

### 2.4 修复 B：信号时钟因果顺序

`unified_runtime.py:2242` `validate_signal_time_chain`，判据为：

```
event_time <= confirmed_at <= available_at
```

**明确不含 `read_at_ms` / `matched_at_ms` 作为强制项**：这两个属于「读取/撮合」阶段，未撮合的候选本来就没有 `matched_at`，把它写进准入判据会把正常候选全部误杀（工单 B 的明确约束，测试 06 专测此点）。若调用方传入了这两个值，则只在其存在时校验单调性，缺失不构成阻断理由。

SQL 侧同源：`live_api.py:257` `SIGNAL_TIME_CHAIN_SQL` 三条片段（三个字段非空 + 两条顺序）。

### 2.5 修复 C：历史与测试信号隔离

`live_api.py:240-245`：

```python
ACTIONABLE_ORIGINS = ("live", "recovered")
NON_ACTIONABLE_ORIGINS = ("historical_research", "replay")
TEST_SIGNAL_UID_PREFIXES = ("sig_closeloop_", "sig_ui_test_")
```

`live_api.py:289` `actionable_scope_where()` 返回读写两侧共用的 WHERE 片段：

```
executable_state='enterable'
AND COALESCE(json_extract(payload_json,'$.approved'),1)<>0
AND signal_uid NOT LIKE 'sig_closeloop_%' AND signal_uid NOT LIKE 'sig_ui_test_%'
AND origin IN (?,?)          -- live / recovered
AND direction='long'
AND <时间链三条>
```

`origin` 缺失的处理（工单 C「安全默认但不得视为实时」）：
- 路由侧：`_scan_actionable_ex(con_live, origin)` 的 `origin` 改为**必填**，缺失直接返回 `origin_scope_required`，扫 0 条
- SQL 侧：`origin IN ('live','recovered')` 天然排除空串与 `NULL`

**API 展示状态与模拟路由准入状态一致**：两边调用同一个 `actionable_scope_where()`，不存在两套字符串（测试 14 专测此点）。

### 2.6 修复 D：水位门槛

`live_api.py:310` `resolve_live_floor_ms()` 返回 `(floor_ms | None, reason | None)`，三种失败（抛异常 / 返回 0 / 返回负值）统一收敛为 `live_watermark_unavailable`。

改动前：`effective_floor_ms()` 抛异常会直接 500；若改成 `or 0` 又会静默变成「不限下限」。改动后：`live_signals()` 返回 `missing_reason` 含原因码、`quality.status='no_watermark'`、`quality.reason=live_watermark_unavailable`，**不再用空列表冒充「本轮扫过、没有信号」**。

接口返回类型**未改**：仍用既有的 `missing_reason` + `quality` 字段承载原因，没有新增顶层键、没有改公共返回结构。

---

## 三、修改文件及行号

### 3.1 `框架/backend/live_api.py`（+168 / −?）

| 行号 | 内容 |
|---|---|
| 227–326 | 新增「可执行范围判据」区块（100 行）：常量 + 4 个构造器 |
| 240 | `ACTIONABLE_ORIGINS` |
| 243 | `NON_ACTIONABLE_ORIGINS` |
| 245 | `TEST_SIGNAL_UID_PREFIXES` |
| 247–248 | `EXEC_STATE_ENTERABLE` / `EXEC_STATE_BLOCKED` |
| 251 | `LIVE_WATERMARK_UNAVAILABLE` |
| 257–261 | `SIGNAL_TIME_CHAIN_SQL` |
| 269–271 | `FLOOR_RIGHT_EDGE_SQL`（与基线 339–340 行逐字相同） |
| 274 | `APPROVED_SQL` |
| 277 | `is_test_signal_uid()` |
| 285 | `test_signal_uid_sql()` |
| 289 | `actionable_scope_where()` |
| 310 | `resolve_live_floor_ms()` |
| 418–430 | `live_signals()`：改用 `resolve_live_floor_ms()`，失败返回原因码 |
| 432–443 | `live_signals()`：当前区条件改由 `actionable_scope_where()` 组装，删除原先各自硬编码的 approved / 测试 UID 两行 |
| 513–527 | `live_signals()`：`quality` 增加 `reason` 键 |
| 547–551 | `excluded_breakdown`：参数化 `ACTIONABLE_ORIGINS`、`test_signal_uid_sql()`，新增 `time_chain_violation` 计数 |

### 3.2 `框架/backend/unified_runtime.py`（+404 / −82）

| 行号 | 内容 |
|---|---|
| 21 | `import math` |
| 41 | `import live_api as _SCOPE` |
| 2113–2351 | 新增「2.5 可执行范围 Fail-Closed 判据」区块（239 行） |
| 2127 | `RESONANCE_REQUIRED_NUMERIC` |
| 2130 | `_as_finite_number()`（排除 bool/str/NaN/inf） |
| 2142 | `candidate_input_reasons()` — 缺数据 |
| 2171 | `resonance_result_reasons()` — 非法结果 |
| 2193 | `evaluate_resonance_guarded()` — 异常，`ok=False` 时 score/mult 恒 None |
| 2242 | `validate_signal_time_chain()` |
| 2298 | `signal_admission_reasons()` — 逐行语义复核（SQL 粗筛之后） |
| 2362–2452 | `persist_candidates()` 重写：接 guarded 评估 + 时间链校验；blocked 时 payload 加 `approved=False` / `executable_reason` / `blocked_reasons`，`exec_state` 落 `blocked`；**正常信号 payload 不新增任何键** |
| 2454–2508 | `_scan_actionable_ex()` 新增：`origin` 必填、拒不可执行来源、水位失败给原因码、SQL 用共享判据 + 逐行走 `signal_admission_reasons` |
| 2510–2513 | `_scan_actionable()` 改为兼容包装 |
| 2515–2578 | `route_pending_orders()`：改用 `_scan_actionable_ex`，`stats` 新增 `blocked_reason` / `scanned` / `filtered`；`open_position` 的 `direction` 改用行内真实值 + `direction != "long"` 二次守卫 |
| 3129–3141 | `step_once()`：`summary` 新增 `routed_blocked_reason` |

### 3.3 新增文件

- `框架/backend/tests/test_pr2_wo_02_fail_closed.py`（569 行，14 条行为测试）
- `框架/backend/tests/pr2_wo_02_probe.py`（144 行，只打印的对照探针）

---

## 四、每项风险 → 行为测试映射

全部 14 条测试**都是真实调用**（建临时库、插行、跑 `persist_candidates` / `route_pending_orders` / `live_signals`、再查库与账本），**没有一条只检查源码字符串**。

| 工单要求 | 风险 | 测试 | 断言要点 |
|---|---|---|---|
| A | 共振抛异常仍可执行 | `test_01_resonance_exception_blocks` | `executable_state='blocked'`、`score is None`、`mult is None`、`approved=False`、挂单 0 |
| A | 缺数据时伪造中性分 | `test_02_resonance_missing_data_blocks` | blocked，且**断言打分函数未被调用**（`call_count==0`） |
| A | 返回非法结果被放行 | `test_03_resonance_illegal_result_blocks` | 4 个变体（非 dict / score 越界 / mult=0 / score 非数）全 blocked |
| B | 时间倒挂 | `test_04_inverted_clock_blocks` | `available_at < confirmed_at` ⇒ blocked、挂单 0 |
| B | `available_at` 缺失 | `test_05_available_at_missing_blocks` | 写入 blocked；扫描层同样拒绝（SQL + 语义双层） |
| B | 误把未撮合候选当不合格 | `test_06_unmatched_candidate_needs_no_matched_at` | 无 `matched_at_ms` 的候选**必须**正常通过，不得因缺它被拒 |
| C | 历史回放冒充实时 | `test_07_historical_signal_not_routable` | 即使 origin 被改成 live、状态改成 enterable，也不得路由；实测错误为 `origin_mismatch` + `origin_not_actionable` |
| C | 测试信号入库可执行 | `test_08_test_uid_not_routable` | `sig_closeloop_*` / `sig_ui_test_*` 无条件排除 |
| C | `origin` 缺失被当实时 | `test_09_origin_missing_is_not_live` | `origin=''`（NOT NULL 列）⇒ `origin_scope_required`、registered=0 |
| D | 水位不可用静默空列表 | `test_10_watermark_unavailable_reason` | `blocked_reason=live_watermark_unavailable`、`quality.status='no_watermark'`、`quality.reason` 同码 |
| D | 擅自放宽/收紧边界 | `test_11_floor_boundary_plus_1ms_kept` | 恰好压在右边界（`max(av,cf)+1 == floor`）的信号必须保留；边界前 1ms 必须排除 |
| — | 修复误伤正常信号 | `test_12_normal_signal_unchanged` | 正常信号仍 registered=1，payload **不新增键**，字段逐项不变 |
| — | 方向字段被猜成 long | `test_13_illegal_direction_no_order` | 方向缺失/非法 ⇒ registered=0、无挂单 |
| — | 展示与准入两套判据 | `test_14_api_and_router_agree` | 同一批行在 `live_signals()` 与 `route_pending_orders()` 下判定完全一致（每行不同 symbol，避免展示层 `_merge_same_buy_point` 合并） |

---

## 五、测试命令、退出码与完整结果

解释器：`/usr/bin/python3`（3.9.6，numpy 2.0.2）。**注意**：PATH 上的 `python3`（3.13.12）无 numpy，`import unified_runtime` 会失败，必须用绝对路径。

### 5.1 本轮新增 14 条行为测试

```
cd /private/tmp/pr2-wo-02-work/框架/backend
/usr/bin/python3 -m unittest tests.test_pr2_wo_02_fail_closed -v
```

结果：`Ran 14 tests in 2.014s` / `OK` / 退出码 0

### 5.2 全量回归（工作树 vs 基线树）

```
cd /private/tmp/pr2-wo-02-work/框架/backend
/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"      # → Ran 464 tests, failures=8, errors=12, skipped=78
cd /private/tmp/pr2-wo-01-baseline/框架/backend
/usr/bin/python3 -m unittest discover -s tests -p "test_*.py"      # → Ran 450 tests, failures=8, errors=12, skipped=78
```

失败集合逐条比对：

```
=== 只在工作树（新增失败）===
（空）
=== 只在基线（被修复掉的失败）===
（空）
=== 共同失败 ===
20 条，两棵树完全相同
```

**结论：零新增失败、零意外修复。** 工作树多出的 14 项正是本轮新增测试，全部通过。

20 条共同失败均为**既有环境类问题**（真实子进程/端口绑定/flock 单实例锁/沙盒路径），与本次改动无关，典型如：
- `test_02_unified_start_default_safe_mode`：`'port_conflict' != 'running'`
- `test_01_isolation_mode_rejects_missing_paths`：报缺 `jobs_db` 而非 `market_db`
- `test_03_isolated_end_to_end_trade_closure`：`必须成功将信号登记为挂单 0 != 1`

### 5.3 过程中出现过一次「新增错误」，已归因

第二轮全量曾出现 1 条 `ERROR: test_02_unified_start_default_safe_mode`（`worker_already_running`）。归因过程与结论：

1. `lsof` 定位到 PID 41998，cwd 在隔离工作树，日志目录 `/var/folders/.../T/startup_test_3g_8se2t/logs/worker-59527.log`（已被系统清理）⇒ 是**首轮全量回归里该测试拉起的 worker，测试结束未被回收**，一直占着 `数据/任务队列/jobs.sqlite3.lock` 的 `fcntl.flock`。
2. 在**未改动的基线树**上跑同一条测试，报**同一类错误**（`worker_already_running`，指向它自己的锁文件）。
3. 清掉两棵树的残留持锁进程后重跑，两棵树该测试均为同一结果（共同失败集合内）。

⇒ 属**测试自身的进程卫生问题**（`backtest_worker.py:211` 的 `flock` 单实例锁 + 测试未回收子进程），**非本轮代码引入**。已在两棵树各清理一次后重跑，得到 5.2 的干净数字。

---

## 六、正常信号修改前后对照

测试 `test_12_normal_signal_unchanged` 的实测断言（同一夹具、同一时刻，仅换代码）：

| 观测项 | 改动前 | 改动后 |
|---|---|---|
| `persisted` | 1 | 1 |
| `executable_state` | `enterable` | `enterable` |
| `payload.resonance_score` | 实算值 | 同值 |
| `payload.position_multiplier` | 实算值 | 同值 |
| `payload` 键集合 | 基线集合 | **完全一致，未新增键** |
| `route_pending_orders.registered` | 1 | 1 |
| 挂单 `direction` / `status` | `long` / `pending` | `long` / `pending` |
| `stats.scanned` / `stats.filtered` | （旧实现无此键） | 1 / 0 |

**关键约束**：blocked 信号才写 `approved=False` / `executable_reason` / `blocked_reasons`；**正常信号 payload 一个键都没加**，避免下游按 payload 键集合做判断的地方出现行为漂移。

---

## 七、水位 `+1ms` 的证据

**结论：有真实数据依据，本轮只沿用、未放宽也未收紧。**

1. **口径来源（既有函数，本轮未改）**：`live_store.py:956-967` `signal_bar_right_ms()` 的 docstring 原文：

   > 为什么必须 +1：库里 `close_time` 存的是**闭区间右端 - 1ms**（如 09:00 收盘存 `1791939599999`）。若直接用 `confirmed_at >= epoch` 判断，会把「08:00~09:00 正常闭合」的信号误判为「边界前」而排除。故按真实右边界（`confirmed_at + 1`）比较，**原始值原样保留不改**。

2. **基线里已有同一表达式**：`/private/tmp/pr2-wo-01-baseline/框架/backend/live_api.py:339-340`：

   ```python
   where.append("(CASE WHEN available_at > confirmed_at THEN available_at "
                "ELSE confirmed_at END) + 1 >= ?")
   ```

3. **本轮 `FLOOR_RIGHT_EDGE_SQL`（`live_api.py:269-271`）与之逐字相同**，只是从内联字符串提成常量以便读写两侧共用。没有把 `+1` 改成 `+0`，也没有额外加宽。

4. **边界行为有测试兜底**：`test_11_floor_boundary_plus_1ms_kept` 断言「恰好压在右边界」的信号保留、「边界前 1ms」排除。

⇒ **无需裁决**，属既有口径的等价搬运。

---

## 八、原始工作区与未授权文件未受影响的证据

### 8.1 原始工作区（`/Users/boos/Desktop/威科夫因子共振趋势`，分支 `wyckoff-v3.1-main`）

```
git status --short -- "框架/backend/live_api.py" "框架/backend/unified_runtime.py"
→ （空）
```

文件指纹（均早于本单开始时间 2026-10-29 11:13）：

```
框架/backend/live_api.py
  94302c968efe2fe5ed1cb6fe8bdc6b035bd97010bd2ff61c656524d36b0f7fa2  mtime=2026-10-28T08:58:32Z  size=117757
框架/backend/unified_runtime.py
  e30e213538686466fb06a16e4773a435b8bcfeb4f7c1ddfcfe301f4475995470  mtime=2026-10-28T01:39:40Z  size=183290
```

原始工作区**不存在**本轮新增的两个测试文件（`tests/test_pr2_wo_02_fail_closed.py`、`tests/pr2_wo_02_probe.py`）。

### 8.2 未授权文件

| 禁止项 | 状态 |
|---|---|
| `paper_engine.py` | 未改动（成交价/资金费/止损/跟踪止损均未触碰） |
| 策略评分阈值与仓位参数 | 未改动 |
| 空头 | 未启用（`direction='long'` 写进判据，测试 13 兜底） |
| 模拟数据库 / 运行锁 / 历史快照 | 未改动（测试全部用 `TemporaryDirectory` + `ISOLATED_MODE=1`） |
| `project_state.json` | 未改动 |
| PR #1 / #2 状态 | 未改动 |
| 强制 Git 门禁 | 未启用 |
| PR2-WO-01 前端成果 | 未改动（`UI界面/web/src/api/adapters.ts`、`ObservePanel.tsx` 未触碰） |
| 提交/推送/合并 | 只在隔离分支本地提交 `f348ff0f`，**未推送、未合并、未开 PR** |

### 8.3 提交时的一处如实声明

提交时钩子提示：

```
⚠️ [Git Pre-Commit] 门禁脚本不存在：框架/ops/agent_gate.py
    （该脚本与 project_state.json 属分支 implement_agent_guardrails，本分支未合并）
    ⇒ 本次提交未做防漂移/范围门禁校验，按普通提交继续。
```

即：**本次提交没有经过自动化的范围/防漂移门禁校验**，改动范围合规性只能由人工与验收方核对（本文件第三、八节即为该核对的依据）。

---

## 九、未完成事项与残余风险

### 9.1 未完成（均超出本工单授权范围，未做）

1. **`fill_ts` 语义**（SIGNAL-NOW-01 遗留待裁决）：仍是「本轮循环起始时刻」而非「撮合完成时刻」。属 `paper_engine.py`，本单禁改。
2. **代码层无 `bar_close <= attempt_ms` 校验**（ANTIGRAVITY D4 实证）：属 `paper_engine.py`，本单禁改。
3. **挂单推进路径 `is_replay` 恒 True** 导致 `earliest_fill_before_first_request_clock` 从不执行：属 `paper_engine.py`，本单禁改。
4. **`live_api.py:806-809` 用户可见 `quality.note` 仍是旧公式**（与 `fill_policy.no_fill_before` 自相矛盾）：属文案层，且改动会触及成交政策表述，本单未授权，未改。
5. **`paper_engine.py:989-990` docstring 旧公式**：同上。

### 9.2 残余风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 只降不升可能过严 | 判据一旦收紧，任何说不清的正常信号也会被阻断。若线上出现「本该开仓却一直 blocked」，先看 `blocked_reasons` 具体是哪一条，再判断是判据过严还是数据真缺 | `blocked_reasons` 逐条留痕，可直接定位 |
| 外部微观指标缺失**不**阻断 | 巨鲸成本/Taker/多空比断流时信号仍按中性分通过。这是刻意保留的（否则指标一断全量阻断），但意味着「指标断流」这件事不会体现在准入上 | 与改动前行为一致，非本轮引入 |
| 扫描层 SQL 与语义层双重判据 | SQL 粗筛 + `signal_admission_reasons` 逐行复核，两层理论上可能分叉 | 两层都从 `_SCOPE` 取同一批常量；测试 05/08/13 覆盖「SQL 已筛掉时语义层仍给对原因」 |
| 20 条既有失败 | 真实子进程/端口/flock 类测试在本沙盒环境无法全绿 | 与基线逐条一致，非本轮引入；在正常环境应另行复核 |
| 门禁未生效 | 见 8.3，提交未过自动化范围门禁 | 依赖人工/Gemini 验收核对范围 |
| `recovered` 来源的语义 | `recovered` 被列入可执行白名单（沿用 SIGNAL-UI-LINK-07 的既有结论），本轮未重新论证其边界 | 属既有决策，未在本单范围内重审 |

---

## 十、复现命令汇总

```bash
# 1. 新增 14 条行为测试
cd /private/tmp/pr2-wo-02-work/框架/backend
/usr/bin/python3 -m unittest tests.test_pr2_wo_02_fail_closed -v

# 2. 施工前后对照探针（在基线树上跑可复现缺陷）
cd /private/tmp/pr2-wo-01-baseline/框架/backend
/usr/bin/python3 tests/pr2_wo_02_probe.py

# 3. 全量回归
cd /private/tmp/pr2-wo-02-work/框架/backend && /usr/bin/python3 -m unittest discover -s tests -p "test_*.py"
cd /private/tmp/pr2-wo-01-baseline/框架/backend && /usr/bin/python3 -m unittest discover -s tests -p "test_*.py"

# 4. 看改动
cd /private/tmp/pr2-wo-02-work && git show --stat f348ff0f
```

注：基线树 `数据/研究参数生效视图_v2_2_user5000_shared.json` 需从原始工作区只读复制（`数据/` 被 `.gitignore` 忽略，工作树不自带）。sha256 `82dec362c182…`，与原始一致。
