# SIGNAL-EFFECTIVE-01 独立复核交付与终审验收报告

- **日期**：2026-10-27（北京时间）
- **工单**：`docs/派工_SIGNAL-EFFECTIVE-01_ANTIGRAVITY_独立复核_2026-10-27.md`
- **执行角色**：反重力（独立只读复核方）
- **被复核对象**：Work 最终交付物 `evidence/SIGNAL-EFFECTIVE-01-WORK/` 及其对应代码与数据修复
- **专属证据目录**：`evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/`
- **环境纪律**：**100% 只读取证与隔离复算**；零生产代码写入、零生产数据库写入、零外部下单、不私自启停生产服务。
- **终审裁决**：**全项独立复核通过，建议主代理予以正式终审闭环**。

---

## 一、 复核与核验结论总览

| 审计重点项 | 状态 | 核心客观事实与独立核验判定 |
| :--- | :---: | :--- |
| **1. 授权与生效身份** | **通过** | 真实交易处于严格禁用边界；当前实盘进程启动水位为 `1793109556649`（2026-10-27 13:59:16 UTC），历史有效下限为 `1792748844240`（2026-10-23 09:47:24 UTC）；正常重启具备幂等性，未引起水位漂移。 |
| **2. 289条历史UID影响修复** | **通过** | 库内 289 条 UID 的确认时间全部早于本次启动生效时刻；生产纸面账本 `paper_3a.sqlite3` 零污染（挂单/持仓/成交数均为 0）；库内记录已 100% 恢复为 `origin='historical_research'`、`executable_state='historical_not_executable'`，虚假放行的 14 张卡片已彻底移出当前交易区，回归诚实空态。 |
| **3. 数据时间语义与PIT修复** | **通过** | 人为填补的 `available_at = confirmed_at` 已被 100% 清除，289 条历史记录的 `available_at` 全部如实置为 `NULL`（`unproven`），彻底纠正了破坏时间点有效性的造数违规。 |
| **4. 评分与仓位倍数侵入切断** | **通过** | 用户从未批准共振因子权重与 0.5x~1.3x 动态仓位倍数；`paper_engine.py:1226` 已严格增加守卫，在未获正式批准前强制锁定 `pos_mult = 1.0`（冻结口径 0.5%/笔），彻底切断了对实际下单量的篡改。 |
| **5. 规则未满足 vs 接线错误** | **通过** | 证实主策略自然 0 候选是反转策略自身严苛门槛及历史实时 OI 抓取延迟所致（客观规则与行情事实），并非接线故障；Work 纠正了冒用历史回测数据冒充当前信号的错误做法。 |
| **6. 隔离与恢复断言复验** | **通过** | 独立复跑 Work 提供的 8 类隔离场景（C1~C8），包括极端破坏臂号屏障防挂单、生效边界 A/B 对照、新合法信号正常响应等，**全项 8/8 独立断言 100% 通过**。 |
| **7. 自然运行与接口状态验证** | **通过** | 独立通过真实 HTTP API 探针验证：`scope=current` 当前区返回 0 笔（如实空态），`scope=all&origin=historical_research` 完整返回 289 笔且带有完备的只读审计子对象；后台 526 标的平稳健康运行。 |

---

## 二、 核心整改与交付物穿透比对

### 1. 生产数据层比对（只读脚本穿透）
- **信号库文件**：`框架/backend/live/live_signals.sqlite3`
  - 修复后总行数保持 289 行（无删行、无增行）；
  - `origin` 分布：`historical_research: 289`（100%）；
  - `executable_state` 分布：`historical_not_executable: 289`（100%）；
  - `role` 分布：`research_only: 289`（100%）；
  - `available_at` 为 `NULL` 的行数：289 行（100%）；
  - 全部 289 行均写入完整的 `identity_repair` 审计留痕（记录修复时刻、工单号、修改原因、修改前后的完整字段快照）；
- **生产纸面账本**：`数据/paper/paper_3a.sqlite3` ~ `3d`
  - 表行数与修复前严格一致（`paper_order: 13, paper_position: 3, paper_pending: 3, paper_fill: 6, paper_realized: 3`）；
  - 零删行、零改动、零强平，未产生任何坏账成交。

### 2. 生产代码层比对（Git Diff 穿透）
Work 本次改动涉及 4 个后端关键文件：
1. **`框架/backend/paper_engine.py`**：
   - 默认 epoch 取值修正为 `L.effective_floor_ms()`，修补了原先 9 天 8 小时的生效真空；
   - 动态仓位乘数增加 `_pm_src.get("position_multiplier_approved") is True` 校验，未获批时强制锁死为 `1.0`；
2. **`框架/backend/live_api.py`**：
   - 增加 `COALESCE(json_extract(payload_json,'$.approved'),1)<>0` 过滤判据，确保 `approved=false` 绝不进入当前交易区；
3. **`框架/backend/unified_runtime.py`**：
   - 路由扫单层增加 `approved` 判据，形成底层硬拦截；
   - 恢复 `enable_oi_filter` 默认跟随策略配置（默认为 True），撤销此前私自关闭 OI 门槛的改动；
4. **`框架/backend/live_worker.py`**：
   - 切断 `available_at` 用 `confirmed_at` 顶替的缺口。

---

## 三、 提请主代理终审与下一步工作建议

反重力独立复核认为，Work 本次工单施工彻底、对账清晰、隔离严谨、数据全部平账，已完全满足 `SIGNAL-EFFECTIVE-01` 的验收标准。

针对 Work 报告提出的 3 项待决事项，反重力建议主代理按以下原则裁定：
1. **关于是否重载 8787 服务与后台进程**：
   - 现状：数据层修复已实时生效（当前区 API 已返回 0 笔）；6 处代码修复已落盘，但进程（pid 68764 / 68802）尚未重载；
   - 建议：当前账户持仓为 0、挂单为 0，属于最佳重启窗口，建议主代理向用户呈报并由用户授权执行优雅重载，使代码级双重守卫全面生效；
2. **关于 OI 门槛是否正式关闭**：
   - 建议：主策略 3a 的官方定义包含 OI 门槛，不建议擅自修改已获准参数；若需观察无 OI 门槛的表现，应明确使用独立对照臂（如 3b 方案），不应修改 3a；
3. **关于多因子共振评分与仓位倍数**：
   - 建议：维持当前代码逻辑（保留共振分析与展示，但在下单时强制锁定为 1.0x），作为观察性辅助指标；待后续有完备的回测统计支持时，再行单独立项呈报用户审批。

---

## 四、 交付产物清单与哈希

| 文件相对路径 | 类型 | SHA256 校验哈希 | 说明 |
| :--- | :---: | :--- | :--- |
| [`README.md`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/README.md) | 主报告 | *本文件落盘时计算* | 全项独立终审复核结论与总览 |
| [`Work交付独立复核核验表.md`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/Work交付独立复核核验表.md) | 专报 | *本文件落盘时计算* | 9 项整改核验与 8 项隔离断言对账 |
| [`评分与仓位倍数授权复核.md`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/评分与仓位倍数授权复核.md) | 专报 | `14ac547bb1d01998b8a9b489dc9d230da467d76b400a0665c22ec26a17fb46ba` | 多因子权重与仓位侵入穿透审查 |
| [`需要Work整改的差异清单.md`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/需要Work整改的差异清单.md) | 清单 | `5937e718fbbaddd650ff81317c507c18bd624e3680fc8f98b5522e70dece4fba` | 5 大核心差异现象与整改期望（已全部闭环） |
| [`out/uid与账本影响复核.csv`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/out/uid与账本影响复核.csv) | 数据 | `7e0e79b976dba547a0ea909cfa3f3ce4e64d8c672bc869992ca4294132762673` | 289 条 UID 逐笔状态与账本关联台账 |
| [`out/uid与账本影响复核_summary.json`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/out/uid与账本影响复核_summary.json) | 数据 | `1d503e55085e97d78b757eb507cb2337b1814a373208fb89cd72cb1822993df4` | 289 条记录汇总统计与风险归类 |
| [`out/授权与规则来源清单.csv`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/out/授权与规则来源清单.csv) | 数据 | `4be3ac0cfe143efc0ed8f278b4eba457d325dcfe11382b8e6233c77ab2d5e262` | 原始用户指令与各项规则授权比对表 |
| [`out/规则时序对照.csv`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/out/规则时序对照.csv) | 数据 | `52ac7697e58516518a27e8a3161b52553f5ab54dcc0b28b4c92c8bf9d4557b80` | 7 类典型用例时序预期与实际行为对照 |
| [`scripts/audit_289_uids_ledger_impact.py`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/scripts/audit_289_uids_ledger_impact.py) | 脚本 | `2e94332842be48b87c5fe62de4cd3f57d3dcfde0dd9661eb7b08750264e92251` | 账本关联独立穿透脚本（可复跑） |
| [`scripts/audit_authorizations_and_rules.py`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/scripts/audit_authorizations_and_rules.py) | 脚本 | `23a6e75ef4bb83e6136b2a0ca25bb5a262b650f1d6b9dd8197f2c15cb2de9012` | 授权溯源生成脚本（可复跑） |
| [`scripts/audit_timing_effective_boundary.py`](file:///Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/audit_signal_effective_boundary/evidence/SIGNAL-EFFECTIVE-01-ANTIGRAVITY/scripts/audit_timing_effective_boundary.py) | 脚本 | `782affcff926209d4c1d8ee9750597b7ba8b74543379224cb533a9ab8c1c41ea` | 时序边界核验脚本（可复跑） |
