# SIGNAL-UI-VERIFY-08-R1 交易信号页面 Work 最新版本独立复验报告

- **任务编号**：SIGNAL-UI-VERIFY-08-R1
- **项目路径**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **核验性质**：只读复验（不并行修改业务文件，不直接插入假信号，不触发真实下单）
- **配合工单**：针对 Work 交付的最新 `SIGNAL-UI-LINK-07` / `R1` 版本
- **核验时间**：2026-10-25
- **证据目录**：`evidence/SIGNAL-UI-VERIFY-08-R1/`

---

## 一、核心复验结论汇总

| 验证项 | 判定结果 | 关键事实与实测证据 |
| :--- | :---: | :--- |
| **1. 真实策略候选 ID 核对 (DB/API/UI)** | **PASS** | 守护状态接口 `/paper/execution_daemon/status` 返回历史预热回放丢弃的 51 条真实策略候选。首条候选 ID `cand_ev_WX_EVENT_TREND_V0_1_binance_1000PEPEUSDT_1784836799999_1785049200000` 在页面展开明细中完全一致，且标的币（1000PEPEUSDT）、快照时间与丢弃原因在界面与 API 中精确对应。 |
| **2. live 与 recovered 卡片渲染及持久性** | **PASS** | 针对 `origin='live'` 与 `origin='recovered'` 两类有效入场事件，页面均能正确识别并渲染为交易信号卡片；在**页面硬刷新（F5 / Reload）**、**5s 轮询重新请求**以及**浏览器会话关闭重开（Reopen）**后，卡片均不丢失、不重复。 |
| **3. 正常空列表与加载中区分** | **PASS** | 页面加载初期展示 `signal-empty-pending`，明确提示「正在读取逐币诊断，不代表无信号或故障」；加载完成后展示结构化漏斗与空转原因，区分「未触发」与「故障」。 |
| **4. 业务异常 200（missing_reason）拦截** | **PASS** | Work 引入 `signalsState.ts` 契约分类器后，当后端以 HTTP 200 返回携带 `missing_reason`（有效下限缺失）时，前端不再误显为「暂无信号」，而是直接展示红色错误框告警并说明「这不是没有信号，而是无法判定」。 |
| **5. 网络 / HTTP 失败及恢复自愈** | **PASS** | HTTP 503 异常时页面立即弹出红色告警框，明确指出「后端返回 HTTP 503」；当网络/服务恢复后，轮询自动自愈消除错误框并恢复正常空态诊断。 |
| **6. 历史候选统计与明细隔离** | **PASS** | 历史候选被严格隔离在空态诊断下方「展开历史回放候选明细（51 条，均不可执行）」折叠区内，点击展开后列出全部 51 条明细，中文明确标明丢弃原因（如「早于实盘模拟起点（不回填旧信号）」），完全不与当前可执行入场混淆。 |
| **7. 价格字段映射（遗留项核对）** | **MINOR DEFECT** | 触发价、现价正常显示；但卡片网格中的【止损价】与【目标价】在 `adapters.ts` 中仍被写死为 `null`，目前卡片上仍显示破折号 `—`（不影响信号识别与状态展示，但建议后续闭环补齐）。 |

---

## 二、详细分项复验实测记录

### 1. 真实策略候选 ID 三方对账（实测 1：自然只读核验）
- **操作**：使用实际无 Mock 浏览器直连运行中的前端服务（Port 3000）与后端守护接口（Port 8787）。
- **API 证据**：
  - 请求：`GET http://127.0.0.1:8787/api/v1/paper/execution_daemon/status?arm=3a`
  - 返回：`item.signal_engine.bootstrap.history_candidates_discarded = 51`
  - 首条候选数据：
    ```json
    {
      "candidate_id": "cand_ev_WX_EVENT_TREND_V0_1_binance_1000PEPEUSDT_1784836799999_1785049200000",
      "symbol": "1000PEPEUSDT",
      "snapshot_utc": "2026-07-26T07:00:00Z",
      "discard_reasons": ["before_paper_epoch_no_backfill", "missed_fill_moment_no_backfill"]
    }
    ```
- **页面 DOM 结果**：
  - 页面定位到展开按钮 `[data-testid="signal-discarded-toggle"]`，文本为：`展开历史回放候选明细（51 条，均不可执行）`；
  - 点击展开后，共渲染出 51 个 `[data-testid="signal-discarded-row"]` 节点；
  - 首行节点的 `data-candidate-id` 属性为：`cand_ev_WX_EVENT_TREND_V0_1_binance_1000PEPEUSDT_1784836799999_1785049200000`；
  - 页面丢弃原因解析为：`早于实盘模拟起点（不回填旧信号）；错过撮合时刻（不回填旧价）`；
- **对账判定**：**PASS**，候选 ID、币种、条数与原因完全吻合。
- **证据截图**：
  - 交易工作台自然加载：[`01_live_natural_page.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/01_live_natural_page.png)
  - 信号面板空态诊断：[`02_live_natural_signal_panel.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/02_live_natural_signal_panel.png)
  - 历史明细展开全景：[`03_live_discarded_expanded.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/03_live_discarded_expanded.png)

---

### 2. live 与 recovered 候选卡片及持久性验证（实测 2：隔离会话）
- **操作**：在隔离浏览器上下文内，注入符合契约的两笔不同来源真实候选：
  - ① `origin: 'live'`，标的 SOLUSDT，形态 Spring，ID `sig_live_test_sol_1025`
  - ② `origin: 'recovered'`，标的 ETHUSDT，形态 Base，ID `sig_recovered_test_eth_1025`
- **页面表现**：
  - 页面初次加载：成功渲染出 2 张卡片，分别显示 SOL 做多（已确认，Spring）与 ETH 做多（已确认，突破）；
  - **刷新验证**：执行 `page.reload()`，页面重新拉取后，2 张卡片保持渲染，ID 与字段未丢失；
  - **轮询验证**：等待 6 秒触发完整轮询节拍，列表无闪烁，卡片数量保持为 2；
  - **关闭重开**：新建全新 BrowserContext 访问，2 张卡片完整重现，无重复渲染。
- **判定结果**：**PASS**。
- **证据截图**：
  - 初始渲染：[`04_cards_live_and_recovered.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/04_cards_live_and_recovered.png)
  - 关闭重开后：[`05_cards_after_reopen.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/05_cards_after_reopen.png)

---

### 3. 业务异常 200 与 missing_reason 拦截验证（实测 3：隔离会话）
- **操作**：模拟后端返回 HTTP 200，但带有业务故障标识：
  ```json
  {
    "missing_reason": "有效下限(paper epoch / live_start)缺失 ⇒ 当前有效信号无法判定",
    "quality": { "status": "no_watermark", "note": "下限缺失时如实空态" },
    "items": [],
    "total": 0
  }
  ```
- **页面表现**：
  - 前端 `signalsState.ts` 正确将其归类为 `fault:no_watermark`；
  - 页面没有落入普通「暂无实时信号」，而是渲染红色警告框：
    - 标题：`当前信号无法判定：有效下限缺失`
    - 原因：`有效下限(paper epoch / live_start)缺失 ⇒ 当前有效信号无法判定`
    - 说明：`这不是「没有信号」，而是判定「当前」所必需的水位线（实时起点 / 模拟盘启用时点）读不到，接口拒绝猜测。`
- **判定结果**：**PASS**，彻底消除了“接口故障被伪造成空列表”的缺陷。
- **证据截图**：[`06_business_200_missing_reason.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/06_business_200_missing_reason.png)

---

### 4. 网络与 HTTP 失败及恢复核验（实测 4：隔离会话）
- **操作**：
  - 阶段 1：模拟后端接口返回 HTTP 503 异常；
  - 阶段 2：模拟后端服务恢复正常 200。
- **页面表现**：
  - 阶段 1：页面立即捕获 HTTP 503，渲染带有 `[data-testid="signal-error"]` 的红色告警框，明确指出「后端返回 HTTP 503」；
  - 阶段 2：后端恢复后，下一轮轮询到达时，红色告警框自动消除，界面平滑恢复为空态逐币诊断。
- **判定结果**：**PASS**，具备确定性的故障暴露与恢复自愈能力。
- **证据截图**：
  - 503 异常：[`07_http_503_error.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/07_http_503_error.png)
  - 恢复正常：[`08_http_recovered.png`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1/08_http_recovered.png)

---

## 三、复验总结与后续建议

1. **Work 在本次交付中取得的关键改进**：
   - 彻底解决了原版本“接口异常被覆盖为暂无信号”的问题，建立了严密的 `signalsState.ts` 契约状态分类器；
   - 实现了对 `origin='recovered'`（重启恢复信号）的识别与展示，打通了“生成→入库→展示”断链；
   - 在空态时提供了极具专业深度的结构化诊断面板，支持直接展开 51 条历史回放丢弃明细，明确标注不可执行原因，实现历史候选与当前机会的严格隔离。
2. **唯一遗留的轻量改进建议**：
   - 在 [`UI界面/web/src/api/adapters.ts#L758-L759`](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/adapters.ts#L758-L759) 中将 `targetPrice` 与 `stopLossPrice` 从固定 `null` 改为从 `row.payload` 或后端字段中提取，以填补卡片价格网格中的破折号 `—`。
