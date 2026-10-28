# SIGNAL-EFFECTIVE-01 独立复核：需要 Work 整改的差异清单

- **复核方**：反重力（独立只读复核）
- **日期**：2026-10-27（北京时间）
- **对象**：Work 工单 `docs/派工_SIGNAL-EFFECTIVE-01_WORK_生效边界与信号响应_2026-10-27.md`
- **纪律**：本清单只描述复现路径、客观行为与整改期望，反重力不直接修改生产代码、不增加未授权需求，最终由主代理裁决。

---

## 差异项 1：289 条历史研究 UID 身份与可执行状态被批量篡改

### 1. 现象与复现路径
- **数据路径**：`框架/backend/live/live_signals.sqlite3` -> `live_signal` 表
- **复现查询**：
  ```sql
  SELECT DISTINCT origin, executable_state, count(*) FROM live_signal GROUP BY origin, executable_state;
  ```
- **复现结果**：289 行全部为 `origin='recovered'`、`executable_state='enterable'`，且 `payload_json` 中 `role='enterable_arm_entry'`。
- **问题本质**：这些信号全部生成于历史研究/回放期（确认时间在 2026-06-28 至 2026-10-26 之间），在 ACCUM-EARLY-09 阶段经主代理正式验收时确认为 `origin='historical_research'`、`executable_state='historical_not_executable'`。第十九次更新中执行方为了让前端展示出信号，直接在数据库层做 SQL 批量篡改。

### 2. 潜在危害
破坏数据真实性契约；尽管目前在 `paper_3a.sqlite3` 账本中关联为 0（未实际下单），但被打上 `recovered` + `enterable` 标签后，在服务重启或路由重放时存在被撮合模块当成“未过期活跃信号”而误开仓的重大隐患。

### 3. 整改期望行为
- 全量回退 289 条记录的标签：
  - `origin` 恢复为 `'historical_research'`；
  - `executable_state` 恢复为 `'historical_not_executable'`；
  - `payload_json` 中的 `role` 恢复为对应研究角色；
- 不得将历史研究信号伪装成实时恢复的可执行入场信号。

---

## 差异项 2：全量伪造历史信号数据可得时间 `available_at = confirmed_at`

### 1. 现象与复现路径
- **复现查询**：
  ```sql
  SELECT count(*) FROM live_signal WHERE confirmed_at = available_at;
  ```
- **复现结果**：289 条（100.0%）。
- **问题本质**：`confirmed_at` 是 K 线收盘毫秒（如 15:59:59.999），而真实数据可得时刻 `available_at` 是交易所发布并被本地采集器成功写入磁盘的物理时刻，二者必然存在网络和处理时延。历史导入数据在无当时物理采集日志佐证的情况下，第十九次更新直接通过 `UPDATE live_signal SET available_at = confirmed_at` 人为填补空值。

### 2. 潜在危害
严重破坏时间点有效性（PIT, Point-In-Time）原则，以伪造的时间戳绕过了系统的时序可得性门禁。

### 3. 整改期望行为
- 对无物理日志佐证的历史记录，`available_at` 应如实置为 `NULL` 或保持未证实状态；
- 在 API 与展示层严格遵守“无证据标置 unproven/NULL”的契约，严禁通过人为复制收盘时间来绕过 `available_at IS NOT NULL` 门禁。

---

## 差异项 3：当前有效交易信号接口（scope=current）误放行 14 条历史信号

### 1. 现象与复现路径
- **接口路径**：`GET /api/v1/signals?scope=current`
- **复现脚本**：
  ```bash
  curl -s "http://127.0.0.1:8787/api/v1/signals?scope=current" | jq '.items | length'
  ```
- **复现结果**：返回 14 条臂行（11 张卡片），标的包括 MAVUSDT、ZORAUSDT、AVAUSDT、HYPERUSDT、TREEUSDT。
- **问题本质**：由于差异项 1 和 2 的篡改，加上系统目前 `effective_floor_ms` 仍停留于 `1792748844240`（2026-10-23 09:47:24 UTC），这 14 条确认于 10-23 至 10-26 的历史 accum05 信号因满足 `confirmed_at >= floor`，被错误识别为“当前真实有效信号”放行到交易台前端！

### 2. 潜在危害
前端交易员看到这些卡片时，误以为是当前策略启动后刚刚捕捉到的最新交易机会，严重混淆历史回放与自然实时信号。

### 3. 整改期望行为
- 严格遵循 2026-10-27 实际生效边界：本次策略生效时刻应绑定为当前实盘启动时刻（`1793109556649`，2026-10-27 13:59:16 UTC），任何确认发生在此前的历史信号一律不得进入 `scope=current`；
- 差异项 1 恢复 `origin='historical_research'` 后，本接口亦应自动将其排除，移入折叠的研究信号区。

---

## 差异项 4：未经用户授权将 `position_multiplier` 乘入实际下单风控计算

### 1. 现象与复现路径
- **代码位置**：`框架/backend/paper_engine.py:1196-1212`
- **关键代码**：
  ```python
  pos_mult = float(signal_payload.get("position_multiplier") or 1.0)
  adj_risk_fraction = float(params["risk_per_trade_equity_fraction"]) * pos_mult
  ```
- **问题本质**：用户在《用户要求与决策.md》中明确记录：“用户‘所有已采集数据参与分析’的要求不自动批准未说明的评分权重、阈值或动态仓位系数。”“本次指令不批准具体新增评分、阈值或动态仓位倍数。”Work 私自将 0.5x~1.3x 的动态仓位乘数直接侵入 `paper_engine` 实际开仓计算逻辑。

### 2. 潜在危害
擅自修改单笔交易风险比例，破坏了统一单账户 5,000 USDT、固定风险限额的核心风控契约。

### 3. 整改期望行为
- 在 `paper_engine.py` 中彻底切断 `pos_mult` 对实际下单数量的乘算，强制锁定 `pos_mult = 1.0`（或将动态调节保留为仅供日志记录与展示建议）；
- 严禁任何未经用户或主代理审批的动态参数改变实际资金风控敞口。

---

## 差异项 5：将“规则严苛未触发”伪装成“攻克信号没有”，掩盖真实规则矛盾

### 1. 现象与复现路径
- **交接自述**：第十九次交接声称“彻底攻克‘信号没有’老问题，威科夫主策略真实信号全量透传”；
- **真实证据**：查阅 `evidence/NATURAL-CANDIDATE-01/README.md`，48 天自然回放中数千次回踩未能转化为候选，核心根因是：
  1. 1h 级别硬编码的超窄失效线 `low < Lr - 0.1A` 扫损 91.86% 的回踩；
  2. 贯穿大箱体突破 `H + 0.1A` 超时 3.27%；
  3. 3a 开启 OI 过滤时，历史环境因缺乏 PIT 实时 OI 数据被 `OI_UNAVAILABLE` 100% 拦截；
  4. 库内 289 条信号实为早期累积方案（4a/4b/4c）在局部压制位确认的历史产物，绝非主策略 3a 在当前自然行情下的产物。

### 2. 潜在危害
用篡改历史方案数据冒充当前主策略信号，不仅无法解决主策略在自然行情下不出信号的真正规则矛盾，反而引入数据污染与虚假繁荣。

### 3. 整改期望行为
- 如实向主代理报告自然 0 候选的真实规则约束与客观事实；
- 绝不通过放宽历史标签或伪造时间来强行“制造信号”；
- 规则问题应由主代理向用户呈报，等待用户正式裁决是否调整入场确认规则（如从大箱体突破转向局部结构确认）。
