# ACCUM-EARLY-07-W5｜甲/乙/丙 全币池同口径回测跑分器（第一步交付）

- 日期：2026-10-27（北京时间）
- 范围：**只新增脚本**。未修改策略、`accum_early_entry.py`、`replay_calc.py`、`paper_engine.py`、采集器、前端、账本与统一用户决策文件。
- 状态：**脚本就绪，可以跑；但跑出来的表现在还不能用来拍板**（两个阻塞见第四节）。
- 执行方自报，未经独立验收。

## 一、脚本与用法

唯一入口：`scripts/run_abc_pool.py`

```bash
# 0) 解释器（本机唯一装了 duckdb 的环境；项目自带 venv）
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/wyckoff-framework/bin/python

# 1) 只数候选（不撮合，约 5 分钟）
$PY scripts/run_abc_pool.py --scan-only --max-boxes 40 --allow-fixture

# 2) 正式跑分（三组一起撮合、出对比表）
$PY scripts/run_abc_pool.py --max-boxes 40 --allow-fixture

# 3) 换成批准配置（W1~W4 交付后）
$PY scripts/run_abc_pool.py --config /path/approved_rules.json
```

跑分口径（脚本不自己发明，全部调用既有唯一实现）：

| 项 | 取值 | 来源 |
|---|---|---|
| 撮合/成本/滑点/手续费/参与率/资金费 | 引擎生效视图 `82dec362c182`（5000U、10 仓、单名 10%、1% 参与率、5bps 双边） | `paper_engine.approved_params()` |
| 观察→候选→委托→成交→退出 | `accum_early_entry.research_run()` | 唯一研究入口，未改一行 |
| 三组差异 | 仅 4a/4b/4c 的入场确认规则 | `schemes.4a/4b/4c.confirm` |
| 币池 | `scope_crypto_v1`，526 个（已排除 BTCDOMUSDT/ALLUSDT） | `pool_scope.binance_scope_symbols()` |
| 窗口 | 2026-06-27T08:00Z → 2026-10-26T09:00Z（121 天，预热 60 天） | 与 05-C 同一窗口 |
| 隔离 | 账本/观察库/实时库/闸门全在 `runs/<run_id>/`；指向生产目录直接拒跑 | `ledger_isolation()` |

## 二、已验证（有行为证据，不是声明）

| # | 验证项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 全池 526 标的、121 天能跑完且无异常 | 0 错误，耗时 215s（12 箱体）/ 282s（40 箱体） | `out/扫描_候选普查.json` |
| 2 | 候选产出（40 箱体上限，窗口覆盖完整） | 甲 342 / 乙 273 / 丙 295，来自 238 个标的 | 同上 |
| 3 | 完整链路能走到成交与退出 | 40 标的：候选 105/87/97 → 成交 10/10/10 → 退出 30 笔 → 指标表成形 | `out/回测_甲乙丙对比.{md,json,csv}` |
| 4 | 跑前跑后共享文件哈希一致 | 8 个文件全部无漂移 | `out/运行身份_*.json` |

### 顺带纠正两个旧认识

- **`max_boxes=3`（模块默认）会严重截断窗口**：12 个箱体时，中位标的最后一次活动距窗口末还有 41.7 天；`40` 时中位 1.9 天、525/525 个标的在末 30 天内仍有活动 ⇒ 正式跑必须用 40（触顶 0 个，说明已不绑定）。
- **3 个标的（BTC/ETH/SOL）跑出来 0 候选是样本问题**，不是链路坏了：全池有 910 个候选，但 288/526 个标的本身 0 候选。

## 三、两个阻塞（都影响「表能不能拍板」）

### 阻塞一：阈值未批准，现在只能跑 `fixture` 占位值

- 唯一字段齐全的配置是 `evidence/ACCUM-EARLY-06-W2/01/config/fixture_rules_06w2.json`：`kind=fixture`、`approved=false`，13 项待决参数（`sell_pressure_threshold`、`local_resistance_definition`、`observe_deadline_hours`、`pullback_deadline_hours` 等）用的是工程占位值。
- 正式位置 `数据/research/ACCUM-EARLY-05/approved_rules.json` **不存在**，`load_config()` 报 49 个字段全缺。
- ⇒ 用 fixture 跑出来的数字**不是甲乙丙的策略绩效**，报告里已强制打上 `UNVERIFIED_CONFIG_UNAPPROVED` 与红字声明。

### 阻塞二（新发现）：仓位槽位不滚动，全池只能成交 10 笔/组

`research_run()` 的顺序是「逐标的把整段窗口扫完 → 最后统一驱动一次退出」。后果：

1. 扫描期间账本里的持仓一直 `status='open'`（历史退出时刻要等最后才写回去）；
2. 引擎 `max_positions=10` 的槽位因此**永不释放**；
3. 实测（40 标的）：甲 105 个候选里 **69 个被 `max_positions_reached` 拒掉、26 个被 `position_already_open_no_add` 拒掉，只有前 10 个标的各成交 1 笔**；乙、丙同样是 10 笔。
4. 换到 526 标的结论不变：仍然是扫描顺序上的前 10 个候选成交 ⇒ **成绩表 = 10 笔的表**。

这不是配置问题，是「按标的全窗口推进 + 退出后置」这个编排方式的固有结果。要出真正的组合成绩（净收益/回撤/年化），必须把退出推进与 1h 扫描按时间交错，也就是把循环从「按标的」改成「按时间」——**属于 `accum_early_entry.py` 的语义改造，归窗口二（06-03/06-04）所有，本单按纪律不动它**。

可选的第三条路（需要用户点头）：放弃组合口径，按「逐笔口径」出表（每笔独立、不含仓位上限与现金约束）。这条路的数字不能叫组合收益、不能叫组合回撤——05-C/06-12 都明确禁止把逐笔累加写成组合绩效。

## 四、产物清单

```
scripts/run_abc_pool.py     跑分器（唯一入口，含指标汇总与卡点诊断）
scripts/_probe.py           链路冒烟探针（配置闸门 + 账本表结构）
scripts/_probe_cand.py      候选量探针（3 标的 / 5 标的对比）
out/扫描_候选普查.{md,json}  全池 526 标的候选普查（当前留存的是 max_boxes=40 那次）
out/回测_甲乙丙对比.{md,json,csv}  40 标的完整撮合自检（10 笔/组，作链路证据，不是成绩）
out/运行身份_<run_id>.json  每次运行的配置/引擎/代码身份与漂移检查
runs/<run_id>/              隔离账本、观察库、worker、job
```

## 五、身份（本次运行实际用的版本）

| 对象 | sha256 前 16 位 |
|---|---|
| `accum_early_entry.py` | `65b0ee70bb0283ff`（119,895 B，mtime 2026-10-27T11:09:40+08） |
| `replay_calc.py` | `c9fecd6a1e91273c` |
| `paper_engine.py` | `624474d31dedc79f` |
| 引擎生效视图 | `82dec362c182`（FT-LOW4H-EXP-V2.2） |
| fixture 配置 | `203d9b316583d4c1` |

> ⚠ 提醒：`accum_early_entry.py` 在本单开工期间被外部改动两次（11:05:04 → `3051d038`，11:09:40 → `65b0ee70`）。本单所有运行都在 `65b0ee70` 上完成且跑前跑后一致；但正式跑分前必须重新核对身份，若该文件再变，本单的候选数需要重跑。
