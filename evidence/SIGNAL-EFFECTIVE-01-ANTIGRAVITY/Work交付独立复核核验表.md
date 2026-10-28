# Work 交付独立复核核验表（SIGNAL-EFFECTIVE-01）

- **复核方**：反重力（独立只读复核方）
- **日期**：2026-10-27（北京时间）
- **被复核对象**：Work 工单交付材料 `evidence/SIGNAL-EFFECTIVE-01-WORK/`
- **复核原则**：代码穿透、数据全量比对、跨进程隔离重现、独立 HTTP API 只读探针验证。

---

## 一、 核心整改项逐项核验结果

| # | 检查项 | Work 自报状态 | 反重力独立复核实测结论 | 判定 |
| :-: | :--- | :---: | :--- | :-: |
| **1** | **289 条历史 UID 身份恢复** | 已恢复 | 生产信号库 `live_signals.sqlite3` 289 条记录：<br>• `origin`：100% 为 `historical_research`；<br>• `executable_state`：100% 为 `historical_not_executable`；<br>• `payload.role`：100% 为 `research_only`；<br>• `payload.approved`：100% 为 `false`；<br>• 带有完整 `payload.identity_repair` 审计回溯留痕。 | **通过** |
| **2** | **伪造可得时间清除** | 已清除 | `available_at IS NULL` 为 289 条（100.0%）；`available_at == confirmed_at` 为 0 条。彻底清除了人为伪造的时间戳，如实回归 `unproven` 状态。 | **通过** |
| **3** | **生产纸面账本零污染** | 0 关联 | 生产账本 `paper_3a.sqlite3` ~ `3d` 行数保持不变（`paper_order: 13, paper_position: 3, paper_pending: 3, paper_fill: 6, paper_realized: 3`），零删行、零改动、零强制平仓，未发生历史追补坏账成交。 | **通过** |
| **4** | **生效下限边界修复** | 代码已改 | `paper_engine.py:1083` 将默认 epoch 从 `get_paper_epoch_ms()` 修正为 `effective_floor_ms()`（`max(live_start, paper_epoch)`），彻底补齐了 9 天 8 小时的生效真空，消除了历史信号穿透挂单的漏洞。 | **通过** |
| **5** | **动态仓位乘数切断** | 代码已切断 | `paper_engine.py:1226` 严格增加 `_pm_src.get("position_multiplier_approved") is True` 前置守卫；由于当前全池信号均未获批，`pos_mult` 被硬性强制锁定为 `1.0`（冻结口径 0.5%/笔），彻底阻断对实际下单量的篡改。 | **通过** |
| **6** | **代码级 approved 守卫补充** | 代码已增加 | `live_api.py:348` 与 `unified_runtime.py:2220` 均补充了 `COALESCE(json_extract(payload_json,'$.approved'),1)<>0` 判据，保证 `approved=false` 的研究信号在 API 当前区与路由扫单层被双重硬拦截。 | **通过** |
| **7** | **OI 门槛默认恢复策略配置** | 代码已恢复 | `unified_runtime.py:2467` 恢复 `enable_oi_filter` 默认遵循策略配置（默认为 True），撤销了此前越权关闭 OI 门槛的改动。 | **通过** |
| **8** | **当前交易区接口（scope=current）** | 0 笔（如实空态） | 实测 `curl -s "http://127.0.0.1:8787/api/v1/signals?scope=current"` 返回 `total: 0, items: []`；虚假放行的 14 张卡片已彻底移出当前区，如实回归诚实空态。 | **通过** |
| **9** | **历史研究折叠区接口** | 289 笔完整展示 | 实测 `GET /api/v1/signals?scope=all&origin=historical_research` 返回 289 笔；带有完整的 `research` 审计子对象，明确标明 `approved=false` 与 `available_at_status=unproven`。 | **通过** |

---

## 二、 隔离与时序复现断言独立核验

反重力独立复跑 Work 提供的 8 类隔离场景脚本（`scripts/05_iso_verify_repair.py`）：
1. **场景 C1a（代码防御性证明）**：在数据库身份未修但代码修复生效、且运行臂强行设为 `4a`（模拟破坏臂号屏障）的极限测试下，挂单登记数为 **0**（改动前同条件下会有 12 笔被错误挂单）；
2. **场景 C1b（生效边界 A/B 对照）**：确认时间落在启动前的信号，在旧默认值下会错误入单，在新 `effective_floor` 下被 `before_paper_epoch_no_backfill` 正确拒单；
3. **场景 C2（合法新信号响应）**：生效后确认的合法新信号正常登记为 `pending_future_fill`，证明正常信号响应链路未被破坏；
4. **场景 C3（289 条全量修复验证）**：两臂挂单数均为 0，289 条全部分类为研究不可执行态；
5. **场景 C4（幂等性断言）**：连续执行多次修复与路由，新增挂单与影响均为 0；
6. **场景 C5（既有挂单保护）**：已有合法挂单生命周期不受影响（before=1, after=1, kept=1）；
7. **场景 C6（确定性回滚）**：通过备份镜像可 100% 还原；
8. **场景 C7（无可得时间健壮性）**：`available_at` 传 `None` 时不再被强填收盘时间，如实推进。

**全项 8/8 独立断言 100% 通过！**

---

## 三、 自然行情与零候选客观事实核实

通过独立检查当前生产进程（API pid 68764、UnifiedRuntime pid 68802）：
1. **运行状态**：整体健康评级 `overall_health: healthy`，水位于 `1793106000000`（1h）与 `1793088000000`（4h）完全对齐，全池 526 标的无任何落后；
2. **自然候选为 0 根因已完全证实**：
   - 核心瓶颈在于反转策略自身的苛刻门槛（1h 回踩突破箱体上沿仅 4.2%）以及实时抓取环境持仓量数据的到达时延（中位数 52 分钟，超出 15 分钟规则要求）；
   - **0 候选是当前自然行情与已批准规则下的客观事实，绝非代码接线断裂**。Work 纠正了第十九次更新中“以修改历史标签制造信号”的错误做法，回归了实事求是的量化研究底线。

---

## 四、 最终独立复核裁决

**复核判定：全项核验通过。**
Work 在本次工单中严格遵守了只修复实现错误、不擅改业务规则、不侵入生产账本的纪律，彻底清除了历史数据冒充实时信号的技术债，并切断了未授权动态仓位对风控下单的污染。
建议主代理**予以正式终审验收**，并按照 Work 提出的待决事项组织下一步工作。
