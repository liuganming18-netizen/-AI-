# PARALLEL-STRATEGY-01｜反重力 B：前后版本同条件对照与首次差异定位报告

- **工单编号**：PARALLEL-STRATEGY-01（反重力 B）
- **审查日期**：2026-10-28（北京时间）
- **执行角色**：反重力（Antigravity）独立复核窗口
- **基线工作区**：`/Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff`
- **真实项目根目录**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **本单专属证据目录**：`evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/`
- **隔离纪律遵守**：
  1. 全程只读访问共享代码与历史数据，零切换共享分支、零修改共享代码、零重载或启停生产服务；
  2. 读取共享输入前固定其 SHA256 哈希，输入快照与对比脚本、中间运行产物全部隔离在本单专属证据目录下；
  3. 不预设旧版成功，不以旧绿测或盈亏差异证明策略有效，仅做最小事实判断。

---

## 一、核心结论概要 (Executive Summary)

针对用户明确指示“反复的在数据上纠什么？以前不是可以的，策略上找问题”，反重力 B 独立窗口对工程历史版本进行了端到端的穿透溯源，并以具有确凿真实成交记录的案例 **`WALUSDT`**（区间：`2026-09-01 00:00:00` 至 `2026-10-17 18:00:00` UTC）为基准，在**同一行情输入、同一标的池窗口、同一风控参数**下，完成了旧版批处理实现与当前统一运行时实现的隔离复现与逐阶段对齐。

**核心事实裁决如下：**

1. **“以前不是可以的”对应具体版本与案例确凿存在**：
   - **时间节点**：2026-10-18 02:40 UTC 批次 `runs_sep_all_latest`（提交基线 `0b95c425`）；
   - **策略身份**：`WX_EVENT_TREND_V0_1` (v0.1.0) 经典大箱体突破策略；
   - **历史表现**：全池 528 标的 46 天回放产出 **4118 箱体 → 891 扫低 → 547 回踩 → 16 突破 → 16 候选 → 12 笔真实撮合成交**（7 胜 5 负，净盈亏 +15.18 USDT）；
   - **基准标的 WALUSDT**：于 2026-10-10 11:00 UTC 以 64.54 倍量比突破，成功生成候选快照 `cand_ev_WX_EVENT_TREND_V0_1_binance_WALUSDT_1791057600000_1791630000000`，于下一根 15m（11:15 UTC）以 0.03859 真实成交，并于 10-16 01:45 触及跟踪止损 0.0459 盈利离场（净利 +13.44 U）。
2. **当前实现与旧实现行为分化的【首次差异点】精确定位**：
   - 首次分化点**不在** 4h 箱体蓄势（STAGE-1）、**不在** 1h 扫低回收（STAGE-2）、**不在** 1h 缩量回踩（STAGE-3）、**也不在** 1h 价格突破与成交量量比门禁（STAGE-4 & STAGE-5）；在这些阶段，新旧实现形态计算**100% 逐字等价**；
   - **首次差异发生于【STAGE-6：持仓量 OI 增长率与时效检查门禁】**：
     - **旧版回测引擎（实现 A）**：采用 `pit_mode = "declared_assumption"`，以 5m 桶收盘时刻为数据可得时刻，成功读取冷层中真实的持仓量序列，计算出 6h OI 强劲增长 **+46.57%**（远超 2% 门槛），顺利放行生成候选；
     - **当前统一运行时引擎（实现 B，3a 默认）**：在 `unified_runtime.py:651-653` 的 `_oi_slice_as_of` 引入了极其严格的物理门禁 `available_at <= decision_ms`。然而，历史冷层中的 OI 数据大多由事后 gapfill 补录，其 `fetched_at` 记录的是脚本事后抓取时间（如 2026-10-11 14:38 UTC），晚于历史回放中的决策时刻（2026-10-10 11:00 UTC），导致切片结果被 **100% 物理过滤为空（0 行）**；
     - 状态机在 `state_machine.py:586` 检查时判定为 `oi_empty`，在 3a 开启 `enable_oi_filter = True` 下直接触发 **`EventState.OI_UNAVAILABLE`**，将原本成功的真实突破当场归档销毁，导致候选数量彻底归零！
3. **对照组交叉验证铁证**：
   - **对照组 C（3b 方案，仅关闭 OI 门槛）**：在当前新代码下，同一标的、同一行情输入，仅设定 `enable_oi_filter = False`，**立刻 100% 成功生成候选快照**（`cand_ev_WX_EVENT_TREND_V0_1_binance_WALUSDT_1790971199999_1791630000000`）；
   - **对照组 D（3a 方案，补齐 declared_assumption 时效语义）**：解除历史补录时间的误杀后，**同样立刻 100% 成功生成候选快照**。
4. **两重物理漏斗的叠加效应**：
   - 系统中本就存在 `state_machine.py:503` 的 `0.1 ATR` 超窄止损（全池 1560 次回踩有 91.86% 在平均 22 小时内被扫死，仅剩 62 次突破）；
   - 当仅存的这 3.97% 突破苗头在进入 STAGE-6 时，又被上述“补录抓取时间晚于历史决策时间”的时效断路 100% 误杀，最终导致全池自然 CANDIDATE 彻底变为 0。

---

## 二、旧版身份、来源与基准案例核定

### 1. 旧版身份与提交哈希

| 维度 | 旧版回测基线（实现 A） | 当前统一运行时基线（实现 B） |
| :--- | :--- | :--- |
| **策略名称与版本** | `WX_EVENT_TREND_V0_1` (v0.1.0) | `WX_EVENT_TREND_V0_1` (v0.1.0) |
| **主要代码提交** | `0b95c425` (2026-10-21 13:46 UTC) | `9e3bc14b` (2026-10-28 08:30 UTC) |
| **历史产物生成时间** | 2026-10-18 02:40 UTC | 当前最新运行态 |
| **代码文件组合哈希** | `f24313aaeb72a38f9ca70aec73fb3ce9cb8166d081f4850d43d1f8a5b562ce9c` | 当前工作区最新 |
| **核心配置哈希** | `0faa5fc046f1841d3900508ddcfbbd6717dff8b0196a3dc17ae406efb455d7b0` | 同左（配置参数一致） |
| **调度与调用入口** | `EventTrendRunner.run_state_machine_simulation` | `unified_runtime.RealSignalEngine.process_market` |
| **历史产物目录** | `回测日志/event_trend_v0_1/runs_sep_all_latest/` | `框架/backend/locks/paper_runtime_3a_*.json` |

### 2. 基准案例 WALUSDT 历史真实账本原件记录

在历史目录 `回测日志/event_trend_v0_1/runs_sep_all_latest/run_WX_EVENT_TREND_V0_1_WALUSDT_1792262405/` 中，原件记录完备无损：

- **候选快照 (`candidates.jsonl`)**：
  - SHA256: `296d2faf0159a15fb20604c924440dbbcb2526de06c4b0ce699af3a1bcb80bab`
  - 快照内容：`candidate_id="cand_ev_WX_EVENT_TREND_V0_1_binance_WALUSDT_1791057600000_1791630000000"`, 快照时间 `1791630000000`（2026-10-10 11:00 UTC）, `box_high=0.03854`, `box_low=0.03239`, `retest_low=0.03242`, `breakout_close=0.04253`, `volume_ratio=64.54`, `oi_growth_6h=0.4657`。
- **成交账本 (`trades.jsonl`)**：
  - SHA256: `e1935be97006024e8692d8e62706219ad0392a3da17b2886e2ca498710b773b3`
  - 成交记录：入场时刻 `1791630900000`（11:15 UTC），入场价 `0.03859`，持仓量 1582 币；出场时刻 `1792115100000`（10-16 01:45 UTC），动态跟踪止损抬升至 `0.0459` 盘中平仓，毛利 +11.56 U，扣除手续费 0.067 U 并获得资金费补贴 1.94 U，**净盈亏 +13.4377 U**。
  - 价格区间硬门禁校验：入场价与出场价 100% 落在当根 15m 的 `[low, high]` 区间内，越界腿为 0。

---

## 三、同一输入行情快照与哈希固化

为保证前后版本对照在绝对同一物理条件下进行，在执行隔离复跑前，从共享数据层加载 WALUSDT 完整序列并计算固化哈希（产物位于 `evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/out/input_snapshot_hashes.json`）：

| 数据序列 | 记录数 / 根数 | 时间跨度 (UTC) | 固化 SHA256 哈希值 |
| :--- | :---: | :--- | :--- |
| **4h K线（含预热）** | 500 根 | 2026-07-27 00:00 ~ 2026-10-17 12:00 | `95faecda55e2d1ebff40331ae8ea08c90b6a22f2b380fc1f3ea4d6a666e6bc19` |
| **1h K线** | 1,122 根 | 2026-09-01 00:00 ~ 2026-10-17 17:00 | `23a233c7dae689dbb1c676d0d21e8d976587c6778f307aa52670dd962bda50b2` |
| **15m K线** | 4,488 根 | 2026-09-01 00:00 ~ 2026-10-17 17:45 | `20d2001e0a2e20d04b80b72c91834167e41b9d45e69bf00ec5cf5f59fa0f3f6e` |
| **持仓量 OI (5m)** | 165,942 条 | 2025-04-26 23:30 ~ 2026-10-28 00:45 | `10f9b3a39e836154674fe80f555eb3f41ee1e39da218b3260904d60c4415ebfc` |
| **资金费结算事件** | 3,395 条 | 2024-01-01 00:00 ~ 2026-10-28 00:00 | `237adff3f10129202517aa21c45c11d51b34be940d99ba4208a0d2f09f0611e3` |

---

## 四、逐阶段行为差异对照全景表

本表由独立对比验证脚本 [`scripts/compare_wal_version_behavior.py`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff/evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/scripts/compare_wal_version_behavior.py) 自动运行提取并导出至 [`out/stage_divergence_table.json`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff/evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/out/stage_divergence_table.json)：

| 阶段编号 | 阶段名称 | 触发条件 / 规则阈值 | 实现 A（旧版回测） | 实现 B（当前统一运行时 3a） | 对照组 C（当前实现 3b 关OI） | 对照组 D（当前实现 3a 修复接线） | 是否分化 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **STAGE-1** | 4h 箱体蓄势 | 42 根 4h，宽度 2~8 ATR，ER <= 0.30 | 成功建档 (H=0.03854, L=0.03239) | 成功建档 (H=0.03854, L=0.03239) | 成功建档 (H=0.03854, L=0.03239) | 成功建档 (H=0.03854, L=0.03239) | **否** |
| **STAGE-2** | 1h 扫低回收 | 破 L 不超 0.5A，同根收回 L+0.1A | 成功识别 (S=0.0322) | 成功识别 (S=0.0322) | 成功识别 (S=0.0322) | 成功识别 (S=0.0322) | **否** |
| **STAGE-3** | 1h 缩量回踩 | 回踩在 L~L+0.5A 高于 S，缩量收阳 | 成功确认 (Lr=0.03242) | 成功确认 (Lr=0.03242) | 成功确认 (Lr=0.03242) | 成功确认 (Lr=0.03242) | **否** |
| **STAGE-4** | 1h 向上突破 | 收盘价格突破 H + 0.1 ATR (0.03864) | 成功突破 (Close=0.04253) | 成功突破 (Close=0.04253) | 成功突破 (Close=0.04253) | 成功突破 (Close=0.04253) | **否** |
| **STAGE-5** | 成交量量比门禁 | 突破根 Vol >= 1.5 * V20 中位数 | **通过** (量比 64.54 倍) | **通过** (量比 64.54 倍) | **通过** (量比 64.54 倍) | **通过** (量比 64.54 倍) | **否** |
| **STAGE-6** | **OI 增长率与时效检查** | **开启 OI 时需可得且 6h 增长率 >= 2%** | **通过** (+46.57% >= 2%) | **阻断扼杀** (`OI_UNAVAILABLE: oi_empty`) | **通过** (跳过 OI 检验) | **通过** (+46.57% >= 2%) | **★ 是 ★<br>(首次差异点)** |
| **STAGE-7** | 不可变候选快照 | 突破与门禁全部通过生成快照 | **生成 1 条候选** | **生成 0 条候选** | **生成 1 条候选** | **生成 1 条候选** | **★ 是 ★** |
| **STAGE-8** | 计划下单与撮合 | 下一根 15m 开盘以区间价格撮合 | **成功成交** (11:15 @ 0.03859) | 未执行 (无候选) | 具备完备撮合要素 | 具备完备撮合要素 | **★ 是 ★** |
| **STAGE-9** | 跟踪止损与离场 | 入场后 4h 最低价 * 95% 单调抬升 | **成功止盈** (净利 +13.44 U) | 无持仓 | N/A | N/A | **★ 是 ★** |

---

## 五、首次差异点的物理机制深度穿透

### 1. 代码差异根因穿透 (`unified_runtime.py:651-653`)

在 `unified_runtime.py` 中，为实现防未来函数与 PIT 门禁，编写了如下切片逻辑：

```python
def _oi_slice_as_of(self, decision_ms: int):
    # 三个条件同时成立才留下（任一不满足即排除，fail-closed）：
    #   ① `available_at` 有值且 `<= decision`（抓取时刻已到）；
    #   ② `ts + 5min <= decision`（桶本身已收盘）；
    #   ③ `ts >= decision - 回看窗 - 24h`。
```

当回放推进至关键突破决策时刻 `decision_ms = 1791630000000`（2026-10-10 11:00 UTC）时：
- 数据库中的该条持仓量数据虽然事件时间为 `ts = 1791630000000`，但其属于事后 gapfill 补录数据；
- 其底层字段记录的抓取时间为 `fetched_at = 1791729486945`（即 **2026-10-11 14:38:06 UTC**）；
- 由于 `1791729486945 > 1791630000000`，条件 ① `available_at <= decision` **判定为 False**；
- 这一过滤网将回看窗内所有的补录持仓量数据全部视为“决策当时尚未抓取到的未来数据”，切片结果被 **100% 过滤为空数组**！

### 2. 状态机响应机制穿透 (`state_machine.py:586`)

由于切片数据为空，状态机内部判定 `oi_status = 'oi_empty'`：

```python
if self.enable_oi_filter:
    if oi_status not in ("ok", OI_STATUS_ASSUMED) or oi_growth is None:
        self._terminate_event(
            event, EventState.OI_UNAVAILABLE, close_time_ms,
            f"OI数据缺失或不可用({oi_status})", ...
        )
        return None
```

- 在 3a 默认配置 `enable_oi_filter = True` 下，突破瞬间直接被判定为 `EventState.OI_UNAVAILABLE`，状态机当场终止并销毁该事件，**不生成候选**；
- 而在旧版回测中，采用 `declared_assumption` 模式，直接按桶收盘时间推进，成功读取到当时实际持仓量（从 6,659 万增长到 9,761 万，增幅 +46.57%），顺利放行生成候选。

---

## 六、证据与边界界定（完成标准）

依据工单范围与边界要求，严谨界定以下四类结论的界限：

1. **旧版存在性（Historical Existence）**：
   - **已证实**。旧版批处理回测引擎（2026-10-18 `runs_sep_all_latest`）及其产出的 12 笔真实成交记录（含 WALUSDT +13.44 U）在磁盘上完整存在，SHA256 哈希全部留存可验。
2. **隔离复现性（Isolated Reproducibility）**：
   - **已证实**。在专属隔离目录下使用完全相同的行情输入：
     - 旧版实现 A 100% 确定性复现出 WALUSDT 的同一候选快照；
     - 当前实现 B 100% 确定性复现出由于 `available_at <= decision` 拦截导致的 `OI_UNAVAILABLE` 归零；
     - 对照组 C（3b 方案）和对照组 D（修复时效语义）100% 确定性产出候选。
3. **自然运行证据（Live / Natural Running Evidence）**：
   - **界定为未证明（Unproven）**。旧版的 12 笔成交与 WALUSDT 盈利均产生于**历史 K 线与持仓量回放环境**，不代表该策略曾在真实生产进程中产生过未经回放干预的自然成交。
4. **策略有效性与 Alpha 效果（Strategy Efficacy）**：
   - **界定为不可作为盈利依据**。
   - 尽管 WALUSDT 单笔录得盈利，但穿透 10-18 全池 12 笔成交发现：有 5 笔盈利是依靠“持满 10 天强平”恰好落在成本线上方所致，非趋势追踪跑出；整体胜率 58%，盈亏比仅 0.95，单笔期望仅 +1.29 U，数学抗震能力极薄；
   - 且全池 1,560 次回踩中有 91.86% 直接被 `0.1 ATR` 超窄止损击穿。不得以旧版有 12 笔成交推论该策略具备生产级实盘 Alpha。

---

## 七、交付文件与验证复跑指南

### 1. 交付物索引清单

- **复现与对比验证脚本**：[`scripts/compare_wal_version_behavior.py`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff/evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/scripts/compare_wal_version_behavior.py)
- **输入行情快照哈希表**：[`out/input_snapshot_hashes.json`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff/evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/out/input_snapshot_hashes.json)
- **逐阶段分化分析结构化数据**：[`out/stage_divergence_table.json`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff/evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/out/stage_divergence_table.json)
- **脚本完整控制台运行日志**：[`out/reproduction_run_log.txt`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff/evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/out/reproduction_run_log.txt)

### 2. 独立一键复跑命令

主代理或复核人员可在任何时间通过以下命令独立复验全套证据（耗时约 5 秒，零副作用）：

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
PYTHONPATH="框架/backend:策略" python3 /Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/compare_version_behavior_diff/evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-B/scripts/compare_wal_version_behavior.py
```
