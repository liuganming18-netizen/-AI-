# ACCUM-EARLY-06-W5-R2 交付报告：依赖门禁与资金费逐笔独立复验 (接入 A-R2 正式收口)

**交付窗口**：`evidence/ACCUM-EARLY-06-W5/04_R2/`  
**角色**：独立行为复验窗口 (反重力/Work 独立会话，非代码修改者)  
**被验核心代码**：`框架/backend/accum_early_entry.py` (SHA256: `3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017`)  
**资金费源依赖**：`evidence/ACCUM-EARLY-06-06/out/funding_events_母集48.jsonl` (31,811 条事件，母集 48 标的完整覆盖)  
**上游交付输入**：`evidence/ACCUM-EARLY-06-W4/04_R2/out/06_机器可读_交付.json` (SHA256: `24b6f3c7e7b579541be58a0ebc81d33c8b417e23fe05aa5a3baae30ecfa9fe94`)  
**本单复验最终结论**：**`RE_AUDIT_PASSED`** (102 笔口径条目源事件独立累加复算差异精确为 0.0，净额恒等式 100% 成立)

---

## 1. 原运行记录与 Codex 审核整改溯源 (保留原报告与历史阻断记录)

### 1.1 Codex 独立复核指出的缺陷与 R2 根治措施
在 R1 阶段，Codex 独立复现指出了原门禁与复验脚本的严重缺陷：
1. **原门禁误判放行**：原 `01_dependency_hard_gate.py` 仅用 `glob("*.json")` 检查文件存在，即使空 JSON `{}` 亦返回 `READY_FOR_E2E_AUDIT`；
2. **原复验虚标通过**：原 `02_independent_funding_mapper.py` 仅检查目录存在即标 `VERIFIED`，未做实际逐笔运算。

在本次 R2 开发中，本窗口对门禁与复验引擎进行了彻底重构：
- 实装门禁深层 JSON 解析与 candidate_id 查重去重器；
- 编写 6 大真实负例（空 JSON、错 schema、缺候选、重复 ID、版本漂移、声明篡改）与 1 正例，全绿通过；
- 构建真正的源事件逐笔累加复验引擎，从 31,811 条源事件独立复算 102 笔条目成本与净额恒等式，并实装 2 大防篡改负例测试。

### 1.2 历史等待阻断记录保留
在 A 尚未交付 R2 之前，本窗口真实运行门禁并如实记录了阻断状态：
- 历史门禁判定：`BLOCKED_AWAITING_W4_04_R2`；
- 历史事实说明：上游工单 A 交付物未就绪时，门禁严格硬阻断，绝不伪造通过或自报完成。

---

## 2. 接续运行记录：A-R2 真实交付接入与最小字段适配

当前工单 A 已完成正式交付，产物路径为 `evidence/ACCUM-EARLY-06-W4/04_R2/out/06_机器可读_交付.json`。本窗口在隔离目录内完成最小字段适配，支持新 schema (`accum-early-06-w4-r2/v1`)。

### 2.1 字段对应关系台账 (严格报错，杜绝默认 0 或默认通过掩盖)

| 物理业务含义 | A-R2 交付字段 (`06_机器可读_交付.json`) | 复验引擎解析字段 (`02_independent_funding_mapper.py`) | 缺失时门禁行为 |
| :--- | :--- | :--- | :--- |
| **候选标识** | `candidates[].candidate_id` | `candidate_id` | 硬性抛出 `BLOCKED_MISSING_ITEM_FIELD` |
| **标的代号** | `candidates[].symbol` | `symbol` | 硬性抛出 `BLOCKED_MISSING_ITEM_FIELD` |
| **开平仓毫秒** | `candidates[].entry_ms` / `exit_ms` | `entry_ms` / `exit_ms` | 硬性抛出 `BLOCKED_MISSING_ITEM_FIELD` |
| **经济口径** | `candidates[].calibers` 键名 | `caliber` (`生效视图_5bps` / `策略包_3bps`) | 硬性抛出 `BLOCKED_MISSING_CALIBER` |
| **成交/拒单** | `calibers[cal].is_filled` / `status` | `is_filled` / `status` | 硬性抛出 `BLOCKED_MISSING_ITEM_FIELD` |
| **持仓数量** | `W4_R2_05_JSON` / `ACC2_JSON` 对应 `qty` | `qty` (两源交叉比对 0 差异) | 严格校验存在性，不填补默认值 |
| **资金费成本** | `calibers[cal].funding_new` | `funding_w4_cost` vs `independent_funding_cost` | 逐笔比对，误差 $> 10^{-5}$ 立即标 FAIL |
| **已实现净额** | `calibers[cal].net_new` / `delta_net` | `delta_net_w4` | 严格校验 $\Delta\text{Net} == -\Delta\text{Funding}$ 恒等式 |
| **数据完整性** | `calibers[cal].completeness` | `completeness_w4` (`NO_GAP_DETECTED`) | 保留 A 报告原值 |
| **官方证明分级** | `calibers[cal].claim` | `claim_w4` (`PROVEN` / `NOT_PROVEN` / `UNKNOWN`) | 保留 A 报告原值 |

### 2.2 门禁防写死单元测试套件 (调用正式入口)

| 测试用例 ID | 测试名称 | 测试场景 | 预期判定 | 实测判定 | 测试结论 |
| :--- | :--- | :--- | :--- | :--- | :---: |
| `NEG_01_EMPTY_JSON` | 负例 1: 空 JSON 深度拦截 | 目录仅有 README 与空 JSON `{}` | `BLOCKED_MALFORMED_STRUCTURE` | `BLOCKED_MALFORMED_STRUCTURE` | **PASS** |
| `NEG_02_CORRUPTED_STRUCTURE` | 负例 2: 错 schema / 结构损坏拦截 | 缺少 `candidates` 核心结构 | `BLOCKED_MALFORMED_STRUCTURE` | `BLOCKED_MALFORMED_STRUCTURE` | **PASS** |
| `NEG_03_INSUFFICIENT_CANDIDATES` | 负例 3: 候选数量不足拦截 | `candidates` 仅 40 笔 (<51 候选) | `BLOCKED_INSUFFICIENT_CANDIDATES` | `BLOCKED_INSUFFICIENT_CANDIDATES` | **PASS** |
| `NEG_04_DUPLICATE_RECORD_IDS` | 负例 4: 存在重复 candidate_id 拦截 | 出现相同 candidate_id 重复项 | `BLOCKED_DUPLICATE_RECORD_IDS` | `BLOCKED_DUPLICATE_RECORD_IDS` | **PASS** |
| `NEG_05_CODE_HASH_DRIFT` | 负例 5: accum_early_entry.py 哈希漂移拦截 | 被测代码 SHA256 非冻结哈希 | `FAILED` (`HASH_MISMATCH`) | `FAILED` (`HASH_MISMATCH`) | **PASS** |
| `NEG_06_PAYLOAD_HASH_MISMATCH` | 负例 6: 上游声明依赖哈希篡改拦截 | JSON 中声明的被测代码 SHA256 被篡改 | `BLOCKED_CODE_HASH_MISMATCH` | `BLOCKED_CODE_HASH_MISMATCH` | **PASS** |
| `POS_07_VALID_DELIVERY` | 正向对照: A-R2 真实交付合规放行 | 51 候选 × 2 套口径 (102 条目) 深度校验通过 | `DEPENDENCY_READABLE` | `DEPENDENCY_READABLE` | **PASS** |

门禁单元测试通过率：**7 / 7 (100.0%)**。真实环境门禁评判成功放行至 **`DEPENDENCY_READABLE`**。

---

## 3. 资金费逐笔独立复算引擎与对账结果

复验引擎 `02_independent_funding_mapper.py` 从 06-06 原始源事件库直接读取 31,811 条事件，独立对 102 笔口径条目计算区间资金费。

### 3.1 复验引擎防篡改单元测试
在加载真实数据前，复验引擎执行了 2 项负例与 1 项正例：
1. `NEG_RECON_01_TAMPERED_COST`：人为篡改某候选生效视图资金费 (+10.0 美元)，复验引擎独立累加拦截，判定 `RE_AUDIT_FAILED` (**PASS**)；
2. `NEG_RECON_02_BROKEN_IDENTITY_EQUATION`：人为篡改 delta_net 打破恒等式，复验引擎拦截，判定 `RE_AUDIT_FAILED` (**PASS**)；
3. `POS_RECON_03_VALID_R2_DELIVERY`：A-R2 真实交付 102 笔条目全量对账，判定 `RE_AUDIT_PASSED` (**PASS**)。

### 3.2 两套经济口径独立复算与对账汇总台账

| 经济口径 | 候选数 | 成交数 | 拒单数 | 独立复算资金费总额 | 上游交付资金费总额 | 独立对账差额 | 净额恒等式符合度 | A 证明分级 (PROVEN / NOT_PROVEN / UNKNOWN) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **生效视图_5bps** | 51 | 51 | 0 | **-5.895109** | **-5.895109** | **0.000000** | 51/51 (100.0%) | 44 / 7 / 0 |
| **策略包_3bps** | 51 | 50 | 1 | **-2.672855** | **-2.672855** | **0.000000** | 51/51 (100.0%) | 43 / 7 / 1 |

> **逐笔明细与恒等式核对结论**：
> 1. **资金费对账差额**：在全部 102 笔条目中，独立复算值与 A-R2 交付值绝对误差均 $< 10^{-6}$，总差额精确为 **0.000000**；
> 2. **财务恒等式**：全部 102 笔条目的 $\Delta\text{Realized\_Net} == -\Delta\text{Funding}$ 符合率达 **100.0% (102/102)**；
> 3. **拒单语义保真**：策略包下 `CARVUSDT` 拒单未成交（`status=rejected`），无持仓，资金费成本精确为 0.0，证明分级标记为 `UNKNOWN`；
> 4. 逐笔复验明细已导出至：[`out/02_逐笔资金费复验明细_102条.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W5/04_R2/out/02_逐笔资金费复验明细_102条.csv)。

---

## 4. 财务算术与资金费完整性证据分层验收说明

遵照用户指令要求，我们将复验严格分层，明确独立复核边界：
1. **第一层：财务算术逐笔实算层（本窗口 100% 独立实算验收）**
   - 源事件读取、开平仓区间 $[entry\_ms, exit\_ms]$ 匹配、每笔成本累加、$\Delta\text{Funding}$ 与 $\Delta\text{Net}$ 增量核算及净额恒等式核查，全部由本窗口引擎独立实算，**不依赖 A 的中间结论或计算缓存**。该层核验结论为 **`PASSED` (0 差异)**。
2. **第二层：资金费官方历史证据与时间容差层（明确引证 A 的论证证据）**
   - 本窗口独立核实了 06-06 源事件库在 102 笔持仓区间内的事件覆盖自洽性；
   - 关于币安官方快照历史文件 (`00_官方快照_fundingInfo.json`) 中 updateTime 的历史覆盖性证明、结算周期判定（4h/8h）、以及 $\pm 15$ 秒边界抖动容差规则，**明确属于工单 A 的专项研究论证范围**。本窗口引证其证明结论（生效视图 44 笔 PROVEN / 7 笔 NOT_PROVEN；策略包 43 笔 PROVEN / 7 笔 NOT_PROVEN / 1 笔 UNKNOWN），但不代表本窗口直接对币安历史服务器做一手原始取证。

---

## 5. 最终回执四大核心板块 (接续收口交付)

| 回执板块 | 状态标记 | 核心事实与依据文件 |
| :--- | :--- | :--- |
| **一、依赖读取** | **`DEPENDENCY_READABLE`** | 工单 W2 代码 (`accum_early_entry.py`) 哈希锁定 `3c570edc...017`；工单 W3 适配器及 31,811 条事件齐备；工单 A 真实交付 `06_机器可读_交付.json` 深度解析通过，102 笔条目字段完备。 |
| **二、实际复验** | **`RE_AUDIT_EXECUTED`** | 复验引擎实际加载 31,811 条原始源事件，对全部 51 候选 × 2 套口径 (102 笔条目) 实际执行持仓区间匹配与资金费累加复算。 |
| **三、通过/失败项** | **`RE_AUDIT_PASSED`**<br>(0 项失败) | 1. 门禁 6 负例 + 1 正例单元测试 7/7 全绿；<br>2. 复验引擎 2 负例 + 1 正例防篡改测试 3/3 全绿；<br>3. 102 笔条目资金费成本独立累加与 A 交付值对账差额为 0.0；<br>4. 102 笔条目财务净额恒等式 100% 成立；<br>5. 状态机承接锁定 i=60、48 未批准门禁、重扫幂等与跨进程隔离等工程基线全项成立；<br>6. 原 7 笔事件历史终态确认 6 INVALIDATED + 1 NOT_FOUND (0 候选)。 |
| **四、未覆盖项** | **`UNVERIFIED_OUT_OF_SCOPE`** | 1. 币安官方快照一手历史取证 (引证工单 A 论证)；<br>2. 自然行情挂钟真实成交 (回放/fixture 验证，正式策略未批准，严禁声称通过)；<br>3. 正式策略实盘经济绩效 (不在本单通过范围)。 |

---

## 6. 交接给 C 的准确产物路径与冻结身份

**交付物所在路径**：`evidence/ACCUM-EARLY-06-W5/04_R2/`  
**核心文件与冻结身份**：
- 门禁评判报告：[`out/01_dependency_hard_gate.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W5/04_R2/out/01_dependency_hard_gate.json)
- 逐笔对账明细：[`out/02_逐笔资金费复验明细_102条.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W5/04_R2/out/02_逐笔资金费复验明细_102条.csv)
- 复验引擎汇总：[`out/02_funding_mapper_status.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W5/04_R2/out/02_funding_mapper_status.json)
- 基线与分层证据：[`out/03_baseline_evidence_audit.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W5/04_R2/out/03_baseline_evidence_audit.json)
- 全项判定总表：[`out/summary_verification_verdicts.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W5/04_R2/out/summary_verification_verdicts.json)
- 双向对撞结果：[`out/05_数字对撞结果.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W5/04_R2/out/05_数字对撞结果.json)
- 被验代码哈希：`3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017`
- A 真实交付哈希：`24b6f3c7e7b579541be58a0ebc81d33c8b417e23fe05aa5a3baae30ecfa9fe94`

对账结论：**工单 A 的 R2 交付产物在 102 笔条目的财务算术与净额增量上 100% 准确，未发现任何算术或恒等式瑕疵！**
