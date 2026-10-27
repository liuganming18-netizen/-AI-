# FRAME-FIT-02｜修复首次正式实验启动阻断

**结论先说**：首个正式实验现在能启动了。零运行记录、零试验记录的正式实验，
可以过真实 `backtest_api.create_run`（202）并进入 `backtest_runner.execute_run`，
跑完才具备「结果交付资格」。

根因不是权限不足，是**把两件不同的事混成了一个判断**：
「能不能开始跑」和「跑完的结果能不能当正式结论交付」。
原来的启动闸门直接调用交付资格判定，而交付资格要求「已有运行记录」——
于是任何首次正式实验都卡在「必须先跑过才能开跑」。

本单把这两件事拆成两个阶段，**没有放宽任何一条既有拦截**：

| | 启动许可（`STAGE_LAUNCH`） | 结果交付资格（`STAGE_DELIVERY`） |
|---|---|---|
| 问的问题 | 现在能不能开跑 | 跑完的结果能不能当正式结论 |
| 要求 | 已登记、已冻结、身份一致、分组已配置、留出未被污染、未放弃、设计无缺项 | 启动许可的**全部条件** + 真的有运行记录 + 每次运行都有结论 + 结论里有量化结果 |
| 是否要求已有 runs / trials | **不要求** | 要求 |
| 入口 | `guard_create` / `formal_launchable` | `formal_eligible` |

---

## 一、问题在哪一行

`框架/backend/experiment_registry.py::guard_create` 正式分支原来只有一句：

```python
ok, why = reg.formal_eligible(eid)      # ← 交付资格
```

而 `formal_eligible` 里有：

```python
if not exp["runs"]:
    reasons.append("尚无已登记运行（试验次数为 0，不能宣称有结果）")
```

运行记录只能在启动之后由执行器写入 ⇒ 首次正式实验永远拿不到 `runs` ⇒ 永远启动不了。
FRAME-VERIFY-03 已消除身份、窗口、分组三项混杂，排他性证实当时**只剩这一条**阻断原因。

---

## 二、改了什么（4 个文件，3 处实质改动）

| # | 文件 | 改动 | 行数 | sha256（前 16 位） |
|---|---|---|---|---|
| 1 | `框架/backend/experiment_registry.py` | 拆两阶段；新增 `launch_blockers` / `formal_launchable`；`formal_eligible` 改为交付资格；新增 `record_execution` | 1332 | `89d10a8a1320bea2` |
| 2 | `框架/backend/backtest_runner.py` | 执行结束后把 run + trial 写回登记（成功/失败都写；闸门拒绝不写） | 1479 | `27ada5b927a6f09e` |
| 3 | `框架/backend/tests/test_frame_fit_02.py` | **新增** 32 项测试 | 573 | `5f9b5990171e5054` |
| 4 | `evidence/FRAME-FIT-02/scripts/probe_launch_before_after.py` | **新增** 修复前后对照探针 | 157 | `dc28a536be2bb736` |

**没动**：`backtest_api.py`（它调的还是 `guard_create`，签名没变）、PIT 相关任何文件、
策略参数、撮合/费用/账本、生产运行库、采集器。

三处实质改动：

1. **`_launch_blockers()`**：只做静态检查（模式、设计缺项、分组自洽、冻结、冻结身份一致、
   未放弃、留出未被污染），**刻意不看 runs / trials**。
2. **`formal_eligible()`**：先取 `_launch_blockers()`，再叠加「有运行、有结论、结论有量化结果」。
   名字和行为对既有调用方保持不变 —— 它一直就是「交付」语义，只是以前被错用成「启动」。
3. **`record_execution()` + `execute_run` 收尾写入**：执行器跑完（成功或抛异常）后，
   把这次 run 绑进实验并记一条 trial；**闸门本身全程只读**，不会为了过闸而写记录。

---

## 三、必须一起说的三件事

**1. 启动通过 ≠ 可以宣称结果。**
探针实测：启动那一刻 `blockers=[]`，但 `delivery_eligible=false`，
原因照旧写着「尚无已登记运行」。跑完并写入结论后才变 `true`。
交付资格要求「结论里有量化结果」—— 失败的运行（没有 metrics）永远不满足这一条，
所以**失败的实验不能宣称正式交付通过**，只能如实报失败。

**2. 研究运行能跑、能留痕，但永远不具正式资格。**
`create_run` 在创建时会把实验一并冻结（`_freeze_design`），所以研究运行能过执行闸门、
也会写下 run + trial。这是对的：它确实跑过。但 `formal_eligible` 仍是 False，
第一条原因就是「研究模式运行，不具正式实验资格」。

> ⚠ 本单执行期间（02:56 与 02:58），`backtest_api.py` 与
> `tests/test_frame_fit_02.py` 被**本单之外的进程/窗口**同时修改：
> 前者加入「创建时冻结设计身份」与「留出段守卫」，后者把研究模式用例改成
> 「创建即冻结 ⇒ 能跑能留痕」。两处改动与本单的启动/交付拆分兼容，已一并验证通过。
> 详见 `01_版本与改动清单.md` 第四节。**本单没有修改 `backtest_api.py`。**

> ⚠ **本单有一处污点，必须让复验方先知道**：`框架/backend/data/experiments/` 的
> `events.jsonl` 从 1 条变成 9 条，多出的 8 条发生在 03:00:47~03:01:47，
> 走的是 `api:create_run` / `api:execute_run`。交付的测试文件与探针都已验证会把登记库
> 重定向到临时目录，但那 8 条**无法逐条归因**（本单定稿前确实反复调过真实入口，
> 同时段另一个窗口也刚在改 `create_run`）⇒ 按「待定」记录，**未清理**。
> 哈希链本身完好（`verify()` = ok，9 条），可以干净截断，但清理需用户明确指示。
> 状态原文见 `logs/production_registry_state.txt`，处理方案见 `06` 第五节。

**3. 三道更硬的阻断仍然照旧。**
`formal_pass_allowed` 仍为 False（历史规格 `verified` 0 条）、
`WX_EVENT_TREND_V0_1` 仍 Gate 未过 / 研究 FAIL / PIT UNPROVEN、
执行链仍把窗口写死 `2024-01-01 ~ 2024-06-30`（请求别的窗口照样被拒）。
本单**没有**让任何实验变得「可正式通过」，只让它**能开始跑**。

---

## 四、交付物

| 文件 | 内容 |
|---|---|
| `README.md` | 本文件 |
| `01_版本与改动清单.md` | 逐文件逐处改动、为什么改 |
| `02_启动许可与交付资格契约.md` | 两阶段的字段、不变式、判定顺序 |
| `03_真实入口调用链.md` | 谁调谁、一次真实执行的完整轨迹 |
| `04_测试与原始日志.md` | 32 项测试逐条对应工单要求 + 命令 + 日志 |
| `05_修复前后对照.md` | FRAME-VERIFY-03 场景的 before / after 实测 |
| `06_未完成项与范围外发现.md` | 没修的、没测的、范围外发现，一条不藏 |
| `00_自哈希.txt` | 交付物自哈希 |
| `reproduce.sh` | 一键复现（`--fast` 跳过全量后端测试；退出码 2 = 快照与工作区不一致） |
| `scripts/` | 对照探针 + 生产登记库只读导出 |
| `snapshot/` | 交付时点 4 个后端文件的合并后快照，供独立复验逐字节比对 |
| `logs/`、`runs/` | 原始日志、生产登记库状态原文与探针产物 |

---

## 五、复验入口

```bash
sh evidence/FRAME-FIT-02/reproduce.sh          # 全量，约 1~2 分钟
sh evidence/FRAME-FIT-02/reproduce.sh --fast   # 只跑本单新增 32 项 + 探针
```

退出码：`0` 全绿；`1` 测试或探针失败；`2` 快照与工作区文件不一致（说明交付后被并发改过，
以 `snapshot/` 为准，不以行号为准）。

单独跑：

```bash
cd 框架/backend
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
  -m pytest tests/test_frame_fit_02.py -q -p no:cacheprovider --basetemp=/tmp/ff02_t1
```

> 提交后等待反重力独立复验。本单**不自行宣布**整体正式实验通过。
