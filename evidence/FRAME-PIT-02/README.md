# FRAME-PIT-02｜封堵 FLOW-OI 实际因子入口旁路

**结论先说**：FLOW-OI 四组实验的**真实因子入口**已经接入 FRAME-PIT-01 的守卫，
未收盘 K 线、未结束 5m 桶、已结束但未发布的数据都读不到了；
覆盖状态与 PIT 资格分成两个字段；四组实验只能吃受控特征。
**但 FLOW-OI 四组结果永远不具备正式资格**——Metrics / OI 没有任何发布时点证据，
这不是修得不够，是数据本身证不出来。

---

## 一、为什么这一单必须做

FRAME-VERIFY-02 用真实入口 `evidence/FLOW-OI-01/generate_flow_oi_01.py::extract_features_for_candidate`
做了反例注入，实测：

| 注入 | 旧行为 | 现在 |
|---|---|---|
| 决策时刻刚开始、`is_closed=0` 的 1h K 线 | 整根吃进去：`bar_volume=8888888`、`bar_close=149` | 取上一根已收盘：`bar_volume=1000`、`bar_close=101` |
| `ts` 正好等于决策时刻的 5m 桶 | 吃进去：`oi_base=99999` | 取 `ts-5m` 桶：`oi_base=1100` |
| 覆盖状态 | 硬写 `COMPLETE` | 如实写 `MISSING_*` / `COMPLETE` |
| PIT 资格字段 | **完全没有** | `pit_status` / `pit_qualified` / `formal_eligible` / `formal_block_reasons` / `pit_sources` / `decision_clock` |

根因不是"作者不小心"，是取数写法本身：

```python
c_pit = df_c[df_c["open_time"] <= ts]     # open_time 是区间起点
m_pit = df_m[df_m["ts"] <= ts]            # ts 是 5m 桶起点
```

`ts`（决策时刻）恰好是整点，于是这两句**必然**选中"从决策时刻才开始"的那一根 / 那一桶，
整段内容都在决策之后。227 笔样本的 `snapshot_time_ms % 3600000 == 0`，一个不差。

---

## 二、改了什么（一句话版）

1. **新建** `策略/WyckoffMaster/flow_oi/pit_features.py`：唯一的受控取数实现，
   全部复用 `trading_core/pit_guard.py`（FRAME-PIT-01），**没有另写一套 PIT**。
2. **真实入口** `extract_features_for_candidate` 从"自己写取数"改成"交给受控实现"，
   公式、阈值、四组准入规则一字未改。
3. **四组实验入口** `evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py`：
   四组只吃受控特征；缺 PIT 字段的旧产物直接拒收；结论永远标"研究结论，不具备正式资格"。
4. **旧产物冻结**：`evidence/FLOW-OI-01/` 的 csv/parquet/md 一字未动；
   旧脚本字节码快照留在 `legacy/`，用来证明"旧实现确实泄露过"。

---

## 三、交付物

| 文件 | 内容 |
|---|---|
| `README.md` | 本文件：结论、根因、改动概览 |
| `01_改动清单.md` | 逐文件逐处改动、为什么改、改了会怎样 |
| `02_约束契约.md` | 四时刻口径、三类数据源的可用性规则、覆盖与资格的定义 |
| `03_实际调用链.md` | 真实调用链（谁调谁）、审计字段样例 |
| `04_测试与日志.md` | 20 项新增测试逐条对应工单要求、命令与原始日志 |
| `05_新旧行为对照.md` | 227 笔真实样本逐候选对照 |
| `06_未覆盖路径与防护边界.md` | 挡不住的地方、如实说明，不宣称完全隔离 |
| `07_正式资格状态.md` | 为什么 FLOW-OI 永远不具正式资格 |
| `00_自哈希.txt` | 交付物自哈希 |
| `reproduce.sh` | 一键复现 |
| `legacy/` | 旧实现字节码快照（行为对照用） |
| `logs/` | 原始日志 |
| `runs/`、`runs_v2/` | 对照明细与四组入口冒烟产物 |
| `scripts/` | 对照脚本 |

---

## 四、必须知道的三件事

**1. 覆盖 100% ≠ 可用时点可证。**
227 笔的 1h K 线 / Metrics / OI 数据确实齐全（`coverage_status=COMPLETE`），
但 Metrics 与 OI 的原始档案里只有桶起点 `ts`：

- OI：抽样 8 个标的 30 个文件共 1,779,127 行，`fetched_at` **100% 为空**；
- Metrics：2,343,888 行，**根本没有** `create_time` / `available_at` 列；
- 1h K 线：148,342 行，`available_at` **100% 为空**，`is_closed` 全为 1。

所以"桶结束时间"只是**假设**，不是证据。按 `declared_assumption` 口径照用，
但 `pit_qualified=False`、`formal_eligible=False`。

**2. 这一单不改变任何 Gate 结论。**
`formal_eligible` 恒为 False，四组对照结果只能当研究结论。
本单也没有重跑 227 笔四组回测（工单要求），只做了 3 笔入口冒烟。

**3. 不能宣称已完全隔离。**
同进程代码仍可直接 `pd.read_parquet` 绕过。守卫提供的是"受控入口 + 违规可检测"。
详见 `06_未覆盖路径与防护边界.md`。

---

## 五、复现

```bash
sh evidence/FRAME-PIT-02/reproduce.sh
```

约 6~8 分钟（大头是 227 笔新旧对照要读全历史 parquet）。
只跑测试约 20 秒：

```bash
cd 策略/WyckoffMaster && \
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
  -m pytest flow_oi/tests/test_frame_pit_02.py -q \
  --basetemp=/tmp/fp02_t1
```

> 提交后等待反重力独立复验。本单**不自行宣布**整体正式回测通过。
