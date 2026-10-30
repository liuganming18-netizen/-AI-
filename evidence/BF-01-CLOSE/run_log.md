# BF-01-CLOSE 执行日志

按时间顺序记录实际执行的命令与关键输出，便于复核。所有命令在仓库根 `/Users/boos/Desktop/威科夫因子共振趋势` 下执行，另有说明的除外。

---

## 1. 摸清未提交改动

```
$ git diff --numstat -- 策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py
183  2  策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py
```

```
$ git diff -- .../engine_adapter.py | grep -E "^@@|^\+def "
@@ -415,6 +415,186 @@   ← 一个连续新增块
+def _num
+def _mean
+def _max_drawdown_pct
+def compute_summary_metrics
+def _merge_summary
@@ -738,7 +918,7 @@   ← 接线 1
@@ -753,5 +933,6 @@   ← 接线 2
```

结论：183 行新增、2 行删除，全部是同一件事（BACKTEST-REPAIR-01 任务 C 绩效汇总），没有夹杂其他窗口的成果。

## 2. 契约核对

```
$ pytest 策略/WyckoffMaster/event_trend_v0_1/tests/test_backtest_repair_01_metrics.py -q
11 passed in 0.64s
```

`M08 test_m08_runner_metrics_mapping` 要求 `框架/backend/backtest_runner.py` 里存在 11 个 `summary_data.get("xxx")` 映射 —— 该文件**已在主线**，逐条 grep 全部命中；缺的只有生产方。

## 3. 策略语义扫描

```
$ git diff -- .../engine_adapter.py | grep -E "^\+" \
    | grep -iE "stoploss|trail|risk_budget|max_holding|initial_equity *=|taker_fee|slippage_bps|oi_|participation|config\."
（无输出）
```

命中 0 条 ⇒ 未触碰冻结策略参数。

## 4. 旧字段零改动实测

```
$ python3 /tmp/dump_summary.py <HEAD 归档>      > /tmp/sum_head.json
$ python3 /tmp/dump_summary.py <工作区>          > /tmp/sum_wip.json
$ diff /tmp/sum_head.json /tmp/sum_wip.json
（只有 ">" 新增行，没有一行 "<" 被替换成不同数值）

HEAD 字段 13 / WIP 字段 36
共有字段 13，数值被改动的 0
新增字段 23
```

## 5. 建隔离分支、只提交一个文件

```
$ git checkout -b bf01-clean-head-fix
$ git add 策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py
$ git diff --cached --numstat
183  2  策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py      ← 暂存区只有这一个文件
$ git commit -m "fix(BF-01): ..."
[bf01-clean-head-fix 7c625c51] 1 file changed, 183 insertions(+), 2 deletions(-)
```

## 6. 干净隔离检出

```
$ ISO=$(mktemp -d /tmp/bf01-iso.XXXXXX)
$ git archive bf01-clean-head-fix 策略 框架 tests AGENTS.md docs | tar -x -C "$ISO"
$ ln -s <仓库>/数据 "$ISO/数据" ; ln -s <仓库>/evidence "$ISO/evidence"
$ mkdir -p "$ISO/回测日志"
$ grep -cE "_merge_summary|compute_summary_metrics" "$ISO/.../engine_adapter.py"
4
$ diff "$ISO/.../engine_adapter.py" <仓库>/.../engine_adapter.py
IDENTICAL
```

## 7. 隔离环境测试

```
$ cd $ISO && pytest 策略/WyckoffMaster/event_trend_v0_1/tests/ --co -q
175 tests collected in 0.95s          ← 0 error（修复前是 164 collected + 1 error）

$ pytest 策略/WyckoffMaster/event_trend_v0_1/tests/ -q
175 passed in 36.47s

$ pytest 策略/.../tests/test_backtest_repair_01_metrics.py -v
11 passed in 0.42s

$ cd $ISO/框架/backend && python3 ../../evidence/FINAL-INTEGRATION-01/verify_integration.py
"total": 11, "passed": 11, "failed": 0

$ cd $ISO && pytest tests/test_agent_gate.py -q
71 passed in 47.28s

$ cd $ISO && python3 -m unittest discover -s tests -p 'test_agent_paths.py'
Ran 1 test — OK      ← 首次 FileNotFoundError: AGENTS.md（归档未含），补入后 OK
```

## 8. 确认真实仓库未被污染

```
$ git status --short -- 数据 evidence | grep -cE "^( M|M | D|D )"
0
```

## 9. 合并与推送

```
$ git checkout wyckoff-v3.1-main
$ git merge --ff-only bf01-clean-head-fix
Fast-forward  1 file changed, 183 insertions(+), 2 deletions(-)

$ pytest 策略/WyckoffMaster/event_trend_v0_1/tests/ -q      ← 合并后再跑一次
175 passed in 33.68s

$ git push origin wyckoff-v3.1-main
bcd72806..7c625c51  wyckoff-v3.1-main -> wyckoff-v3.1-main

$ git rev-parse HEAD
7c625c51ad7f4a2a4ed1f086987dc7abed840ef0
$ git ls-remote origin refs/heads/wyckoff-v3.1-main
7c625c51ad7f4a2a4ed1f086987dc7abed840ef0
```

## 10. 保护范围自查

```
$ git show --stat HEAD
1 file changed, 183 insertions(+), 2 deletions(-)

$ git diff --stat HEAD -- .../engine_adapter.py
（空）        ← 工作区与提交版本一致，原作者内容没被改动

$ shasum -a 256 .../config.py
122683f2b489c742dd594ff465e916147dd097bd1afcb8a35cb4116c0eedeb46   ← 与 bcd72806 时相同

$ shasum -a 256 "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"
1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab   ← 与 bcd72806 时相同
```

全程未使用 `reset --hard`、`--force`、`git add .`；未删除、未跳过任何测试。
