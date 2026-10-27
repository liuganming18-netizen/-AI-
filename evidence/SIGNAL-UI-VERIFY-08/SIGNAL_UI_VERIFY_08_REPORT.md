# SIGNAL-UI-VERIFY-08 交易信号页面只读核验报告

- **任务编号**：SIGNAL-UI-VERIFY-08
- **项目路径**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **核验性质**：只读核验（不与 Work 并行修改业务代码）
- **配合工单**：Work `SIGNAL-UI-LINK-07`
- **核验时间**：2026-10-25
- **证据目录**：`evidence/SIGNAL-UI-VERIFY-08/`

---

## 1. 核心三问最终结论

| 核心关注项 | 最终结论 | 关键事实依据 |
| :--- | :---: | :--- |
| **有效信号是否真正显示** | **部分正常 / 存在字段缺陷** | 符合三要素（`origin=live`、`executableState=enterable`、`role=enterable_arm_entry`）的当前有效信号能正确渲染卡片，卡片包含代币、方向、时间、形态及触发价；**但止损价与目标价恒为破折号（`—`）**，原因是后端接口未下发对应价格字段，前端适配器写死为 `null`。 |
| **刷新后是否仍在** | **是（PASS）** | 页面硬刷新（F5 / Reload）与 5s 轮询重新请求后，数据层会重新拉取并重新匹配，有效信号卡片依然正确显示，不会丢失，不会产生重复卡片。 |
| **没有信号时是否能看懂原因** | **否（FAIL - 存在严重掩盖）** | 页面为空时一律显示无歧义解释的静态文本「暂无实时信号」；**无法区分「未触发」、「被拒绝」还是「接口异常」**；更严重的是，当后端返回带 `missing_reason` 的错误时，前端未解析，直接被误报为「暂无实时信号」。 |

---

## 2. 独立核验检查点详析（不依赖 Work 交付）

### 2.1 检查点一：筛选、字段映射、轮询、分页、缓存与错误提示核对

#### 1) 筛选（Filter）
- **实现位置**：[SignalListPanel.tsx](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/SignalListPanel.tsx#L303-L329)
- **现状**：提供代币搜索框 `searchQuery` 和结构类型下拉框 `kindFilter`（ALL / base / spring / sc）。
- **缺陷**：
  1. 仅对前端内存中已通过 `isCurrentSignal` 过滤的有效信号进行二次纯客户端过滤；
  2. 没有任何状态筛选（如：全部 / 可开仓 / 待回踩 / 已被拒），不可执行候选完全没有被纳入筛选范围；
  3. 筛选状态仅保留在 React 本地组件状态，页面刷新后立即被重置。

#### 2) 字段映射（Field Mapping）
- **实现位置**：
  - 后端接口：[live_api.py](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/live_api.py#L418-L453)
  - 前端适配：[adapters.ts](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/adapters.ts#L754-L759)
  - 视图解析：[SignalListPanel.tsx](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/SignalListPanel.tsx#L151-L168)
- **缺陷铁证**：
  - `SignalListPanel.tsx` 底部设计了 4 列网格价格展示：【触发价】【现价】【止损价】【目标价】；
  - 但在 [adapters.ts:758-759](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/adapters.ts#L758-L759) 中，明确写死：
    ```ts
    targetPrice: null,
    stopLossPrice: null,
    ```
  - 后端接口 [live_api.py:418-453](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/live_api.py#L418-L453) 也完全未提取或返回 `stop_loss_price` 与 `target_price`；
  - **实测结果**：在 Case 4 测试中，即便有效信号成功渲染卡片，卡片上的止损价与目标价依然显示为 `—`，无法为交易员提供入场后的风控边界。

#### 3) 轮询机制（Polling）
- **实现位置**：[useMarketData.ts](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/useMarketData.ts#L671-L705)
- **现状**：工作台处于前台时以 5000ms（5秒）间隔轮询 `pullSignals()`，切换到非实时大类时切为 30000ms。
- **评价**：轮询定时器与页面生命周期正确挂载，卸载时清除；但未配合 HTTP ETag / If-None-Match 或时间戳 cursor，每次均全量重新拉取 500 条数据。

#### 4) 分页机制（Pagination）
- **实现位置**：[useMarketData.ts:681](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/useMarketData.ts#L681)
- **缺陷**：请求参数写死 `GET /signals?scope=current&limit=500`，页面完全没有分页控件、无限滚动或翻页逻辑；若当前信号量超过 500，后端虽然返回了 `has_more: true`，但前端完全丢弃，导致超出部分被截断。

#### 5) 缓存与状态保持（State Persistence）
- **实现位置**：[App.tsx:219-226](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/App.tsx#L219-L226)
- **表现**：用户点击选中的信号 `selectedSignal` 在每次 5s 轮询后会通过 `signals.find((s) => s.id === prev.id)` 刷新引用；若信号仍在，则保持选中状态；若信号失效或被移除，则安全置空，避免产生脏引用。

#### 6) 错误提示与降级（Error Handling & Degradation）
- **实现位置**：[useMarketData.ts:689-695](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/useMarketData.ts#L689-L695) 及 [SignalListPanel.tsx:516-524](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/SignalListPanel.tsx#L516-L524)
- **严重缺陷**：
  - 只有在 `fetch` 抛出网络异常或服务端返回 HTTP 4xx/5xx 时，前端才会进入 `catch` 块并将 `signalsError` 设为错误信息（实测 Case 2 验证 PASS，能看到红色警示块）；
  - **但是，当后端返回 HTTP 200，且 Envelope 根字段包含 `missing_reason`**（例如：`missing_reason: "有效下限(paper epoch / live_start)缺失 ⇒ 当前有效信号无法判定"` 或 `"实时信号库尚未初始化"` 时）：
    前端 `useMarketData.ts:687` 直接读取 `env.items`（为空），然后调用 `setSignals([])`，并紧接着执行：
    ```ts
    setSignalsError(null);
    ```
  - **实测 Case 1 铁证**：页面直接将后端明确报告的接口缺失原因覆盖，显示为普通的「暂无实时信号」！严重违反了“错误不冒充空态”的系统规范！

---

### 2.2 检查点二：页面是否错误隐藏有效候选，或把不可执行候选当成有效机会

#### 1) 错误隐藏候选与不可执行候选的逻辑断层
- **代码位置**：[SignalListPanel.tsx:223-227](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/SignalListPanel.tsx#L223-L227)
  ```tsx
  const isCurrentSignal = (s: CryptoSignal): boolean =>
    s.origin === 'live' &&
    s.executableState === 'enterable' &&
    s.role === 'enterable_arm_entry';
  const currentSignals = useMemo(() => signals.filter(isCurrentSignal), [signals]);
  ```
- **测试 Case 3 铁证**：
  - 当接口返回带有状态为 `waiting_retest`（等待回踩）、`blocked_gate`（闸门关闭阻断）或 `role: 'candidate'` 的真实策略候选时；
  - 前端由于执行了 `executableState === 'enterable'` 硬过滤，将这些候选**全部剔除**；
  - 剔除后由于 `currentSignals.length === 0`，页面直接落入：
    ```tsx
    <p className="text-xs text-neutral-400">暂无实时信号</p>
    ```
  - **缺陷定性**：这导致交易员根本看不到正在跟踪中的形态候选（如 Spring 正在等待 15m 确认或回踩），也看不到被风控总闸阻断的候选，直接把“被阻断/待确认的候选”隐藏成“无信号”。

#### 2) 缺少不可执行与历史候选明细入口
- 当前工作台整个左侧栏只有“交易信号”一个单一视口，完全没有设计“不可执行原因明细”、“被拒绝候选统计列表”或“历史候选对照”的切换 Tab、折叠抽屉或弹窗入口。
- 用户在此页面无法区分：
  - ① **未触发**：市场走势未满足威科夫/唐奇安特征；
  - ② **被拒绝**：已触发特征，但因 ATR 异常、闸门关闭、流动性不足或门禁被拒；
  - ③ **接口异常**：后端数据库未就绪或下限缺失。

---

## 3. Work 尚未提供隔离验证入口项的核验状态

按照任务书指令：**“Work尚未提供入口的项目标为‘待验证’，不空跑判PASS。不直接插入假信号，不伪造卡片，不触发下单，不把隔离回放当自然信号。”**

| 核验项目 | 状态 | 现场核查说明 |
| :--- | :---: | :--- |
| **真实策略产生的候选在接口与页面的同一候选ID核对** | **待验证** | Work 尚未交付隔离回放驱动器或回放候选直通入口；当前真实库为 0 条真实候选，待 Work 交付后核验。 |
| **实际浏览器卡片币种、方向、时间、状态与不可执行原因** | **待验证** | 现有页面代码在 `executableState!=='enterable'` 时直接隐藏整张卡片，无法展示不可执行原因；待 Work 重构后核验。 |
| **历史候选统计必须能进入对应明细，且明确标注不可执行** | **待验证** | 前端界面目前完全未提供历史候选明细入口；待 Work 完成 SIGNAL-UI-LINK-07 界面交付后核验。 |
| **隔离回放候选与自然信号标识隔离** | **待验证** | 待 Work 交付隔离验证环境与候选标识协议后核验。 |

---

## 4. 具体缺陷位置与最小修复建议清单

为配合 Work 的 `SIGNAL-UI-LINK-07`，提供如下精准到文件与行号的最小修复建议（本单只读核验，不修改源码）：

### 缺陷 1：Envelope 包含 missing_reason 时被前端清空并误显为「暂无实时信号」
- **文件**：[UI界面/web/src/api/useMarketData.ts](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/useMarketData.ts#L687-L695)
- **现状**：
  ```ts
  setSignals(adaptSignals(env, map));
  setSignalsError(null);
  ```
- **最小修复建议**：
  ```ts
  if (env?.missing_reason || env?.quality?.status === 'not_connected' || env?.quality?.status === 'no_watermark') {
    setSignals([]);
    setSignalsError(env.missing_reason || `信号接口异常: ${env.quality?.status}`);
  } else {
    setSignals(adaptSignals(env, map));
    setSignalsError(null);
  }
  ```

### 缺陷 2：卡片价格网格缺失止损价与目标价
- **文件**：
  1. [框架/backend/live_api.py](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/live_api.py#L418-L453)
  2. [UI界面/web/src/api/adapters.ts](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/api/adapters.ts#L758-L759)
- **最小修复建议**：
  - 后端在 `items.append` 中从 payload 读取并下发 `stop_loss_price` 与 `target_price`；
  - 前端在 `adaptSignal` 中将 `row.stop_loss_price` 和 `row.target_price` 映射到 `CryptoSignal`，不再直接赋为 `null`。

### 缺陷 3：isCurrentSignal 粗暴隐藏等待回踩与被拒绝候选
- **文件**：[UI界面/web/src/components/SignalListPanel.tsx](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/SignalListPanel.tsx#L223-L227) 及空态区 [L525-L531](file:///Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/src/components/SignalListPanel.tsx#L525-L531)
- **最小修复建议**：
  1. 将信号列表解构为两层或增加状态切换 Tab：【可开仓信号】与【观察候选 / 拦截明细】；
  2. 在空态提示中，读取后端的 `quality.excluded_n` 以及 `quality.note`，当列表无有效信号时，如实展示：
     - 若 `signalsError` 存在：显示接口异常原因；
     - 若存在被拦截/待回踩候选：显示「当前有 N 笔候选未满足入场条件（点击查看原因）」；
     - 仅当真正未产生任何形态时，才显示「当前市场无触发特征」。

---

## 5. 验收证据与测试日志索引

| 证据文件 | 说明 |
| :--- | :--- |
| [01_workbench_initial.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/01_workbench_initial.png) | 交易工作台初始加载完整浏览器截图 |
| [02_signal_panel_snapshot.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/02_signal_panel_snapshot.png) | 交易信号面板局部渲染截图 |
| [case1_missing_reason_response.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/case1_missing_reason_response.png) | Case 1 验证：missing_reason 误显为空态证据截图 |
| [case2_server_500_error.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/case2_server_500_error.png) | Case 2 验证：HTTP 500 正确展示红色异常提示截图 |
| [case3_blocked_candidate_hidden.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/case3_blocked_candidate_hidden.png) | Case 3 验证：不可执行候选被硬编码过滤隐藏截图 |
| [case4_valid_signal_card.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/case4_valid_signal_card.png) | Case 4 验证：有效信号正确显示卡片截图 |
| [case4_after_reload.png](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/case4_after_reload.png) | Case 4 验证：页面刷新后卡片依然存在截图 |
| [browser_probe_result.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/browser_probe_result.json) | 页面真实 DOM 探测与网络请求数据 |
| [test_cases_summary.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08/test_cases_summary.json) | 四组对比测试判定总结 |
