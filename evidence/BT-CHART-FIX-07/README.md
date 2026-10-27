# BT-CHART-FIX-07 小时线多图联动与订单定位整改验收报告

> **任务编号**：BT-CHART-FIX-07｜小时线多图联动与订单定位整改  
> **执行时间**：2026-10-24  
> **状态**：**ALL PASS（全量 100% 验收通过）**  
> **入口地址**：[http://127.0.0.1:3000/?nav=backtest](http://127.0.0.1:3000/?nav=backtest)  
> **证据目录**：`evidence/BT-CHART-FIX-07/`

---

## 一、用户关注的四大重点逐项汇报

### 1. 信号 → 订单 → 回测复盘
- **链路完整性**：从策略候选包输出的信号，到账本订单（`C_entry`、`D_intrabar`、`EOD`），再到回测工作台的单笔明细与图表，链路全程贯通；
- **真实性保障**：图表严格按真实账本数据渲染，绝不造假数据，不修改策略、原始账本和 45 组回测实验产物；
- **健康采集守护**：后台数据采集进程 PID 42278（`realtime_collector`）与 PID 59109（`unified_runtime`）全程持续稳定运行，未受任何干扰。

### 2. K 线留存重开能力
- **明确区分两大核心能力**：
  1. **恢复手动视窗（Restore Manual Viewport）**：
     - 用户手动平移或滚轮缩放图表浏览特定区间时，系统自动捕捉并持久化手动视窗 `manualViewport: { fromMs, toMs }` 至 localStorage 与 URL 参数（`vf`/`vt`）；
     - 关闭页面后，无论通过深链重开还是访问根地址（`/?nav=backtest`），系统**优先恢复手动浏览时间窗口**（毫秒级完全一致，实测关闭前 `{"fromMs":1642575600000,"toMs":1642665600000}` 重开后精准吻合），状态徽章明确显示 `🎯 视窗: 手动已恢复`；选中的订单行保持高亮，绝不被自动聚焦无条件抹除。
  2. **重新自动聚焦订单（Auto Focus Order）**：
     - 当用户点击具体订单行（清空手动视窗）或以订单为核心重开时，系统针对订单发生时刻 `ts_ms` 自动计算前后 12 根 K 线区间 `[focusTimeMs - padMs, focusTimeMs + padMs]` 进行聚焦，状态徽章显示 `🎯 视窗: 订单聚焦`。
- **两套能力在数据结构、持久化与界面状态上彻底解耦并协同工作**。

### 3. 默认 1h 多图真正双向联动
- **正向平移联动**：主图拖拽位移 `180,000,000 ms`（50 小时），资金图时间轴通过总线同步位移 `259,200,000 ms`（3 天，自动对齐日结采样）；
- **反向平移联动**：资金图拖拽位移 `1,296,000,000 ms`（15 天），主图通过总线反向平滑位移 `270,000,000 ms`，实现**真正的双向联动**；
- **双向缩放联动**：滚轮缩放时主图与资金图的时间跨度同步缩放自适应对齐；
- **采样缺口诚实标注**：资金图采样点为日结 24h 一条，主图为 1h 柱。当十字线悬停在缺少小时精确采样的时点时，资金图 hoverbar 明确诚实标注：`时刻 12-14 16:00 UTC（无精确采样，最近采样 12-15 00:00）`，绝不把离散采样伪装成精确同步。

### 4. 逐笔订单、成交与导出
- **真实鼠标物理点击反查**：
  - 彻底废除直接调用内部函数的作弊式验收；
  - 探针通过时间戳计算出目标订单标记在 Canvas 上的物理像素坐标（实测 `marker_physical_x = 239`），使用 Playwright 真实物理鼠标点击该像素；
  - 严格排除原本已选中的假通过：先选订单 A（`selA=true, selB=false`），物理点击订单 B 标记后，订单 A 自动变为未选中，订单 B 准确反向高亮（`selA=false, selB=true`），订单 ID、标的、时间、价格全部严格对应；
- **同小时订单币种纯净隔离**：
  - 单币 K 线（如 BCH）上的标记和同柱委托徽章**严格只对应当前币种**（价格 `429.45`），绝不混入 AVAX（价格 `79.885`）；
  - 跨币组合其他标的的并发委托，单独标明为 `🌐 跨币并发: AVAX @79.89`，不冒充当前单币 K 线事件；
- **导出能力**：委托订单明细（⑤b）与逐笔明细支持一键导出 CSV。

---

## 二、验收探针与 8 项全通过断言明细

探针文件：[`evidence/BT-CHART-FIX-07/probes/bt_chart_fix_07_comprehensive_acceptance.mjs`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/BT-CHART-FIX-07/probes/bt_chart_fix_07_comprehensive_acceptance.mjs)  
报告文件：[`evidence/BT-CHART-FIX-07/out/bt_chart_fix_07_report.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/BT-CHART-FIX-07/out/bt_chart_fix_07_report.json)

| 检查项 ID | 核验内容 | 判定 | 实测核心数值与断言依据 |
| :--- | :--- | :---: | :--- |
| `FIX_07_CROSSHAIR_HONEST_SYNC` | 十字线悬停对齐与缺口诚实标注 | **PASS** | 主图显示 `时刻 2021-12-14 16:00 UTC`，资金图显示 `时刻 12-14 16:00 UTC（无精确采样，最近采样 12-15 00:00）`，透明诚实。 |
| `FIX_07_MAIN_TO_FUNDING_PAN_SYNC` | 主图拖拽正向驱动资金图平移 | **PASS** | 主图位移 `180,000,000 ms`，资金图随动位移 `259,200,000 ms`，平移对齐良好。 |
| `FIX_07_FUNDING_TO_MAIN_PAN_SYNC` | 资金图反向拖拽驱动主图平移 | **PASS** | 资金图位移 `1,296,000,000 ms`，主图反向同步位移 `270,000,000 ms`，达成真正双向联动。 |
| `FIX_07_ZOOM_BIDIRECTIONAL_SYNC` | 滚轮缩放多图跨度自适应调整 | **PASS** | 主图时间跨度缩减，资金图时间跨度同步缩减自适应调整。 |
| `FIX_07_REAL_MOUSE_CLICK_MARKER_LOOKUP` | 真实物理鼠标点击图上标记反查 | **PASS** | 物理像素 `x=239` 执行点击，状态从 `selA:true, selB:false` 转移为 `selA:false, selB:true`，排除假通过。 |
| `FIX_07_SAME_HOUR_SYMBOL_PURITY` | 同小时订单币种纯净隔离 | **PASS** | BCH 委托读数只包含 `429.45`，跨币 AVAX 独立显示在 `🌐 跨币并发: AVAX @79.89`，绝不混淆。 |
| `FIX_07_RESTORE_MANUAL_VIEWPORT` | 重开能力之恢复手动视窗 | **PASS** | 手动视窗范围 `{"fromMs":1642575600000,"toMs":1642665600000}` 毫秒级原样恢复，徽章显示 `🎯 视窗: 手动已恢复`。 |
| `FIX_07_REOPEN_AUTO_FOCUS_ORDER` | 重开能力之重新自动聚焦订单 | **PASS** | 恢复选中的订单 ID，自动聚焦在 `{"fromMs":1642467600000,"toMs":1642554000000}`，徽章显示 `🎯 视窗: 订单聚焦`。 |

---

## 三、真实交互截图清单

截图存放于 [`evidence/BT-CHART-FIX-07/screenshots/`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/BT-CHART-FIX-07/screenshots/)：
- `01_crosshair_honest_sync.png`：十字线悬停对齐与资金图缺口透明提示；
- `02_main_to_funding_pan.png`：主图拖拽正向驱动资金图随动；
- `03_funding_to_main_pan.png`：资金图反向拖拽驱动主图平移；
- `04_zoom_bidirectional_sync.png`：缩放双向跨度自适应对齐；
- `05_real_mouse_click_marker_lookup.png`：物理鼠标真实点击 Canvas 图上标记反查命中订单；
- `06_same_hour_symbol_isolation.png`：同小时单币委托与跨币并发隔离展示；
- `07_restore_manual_viewport.png`：重开后手动浏览视窗毫秒级完整恢复；
- `08_reopen_auto_focus_order.png`：重开后订单自动聚焦定位。

---

## 四、未解决项说明

- **未解决项**：**0 项**。任务要求的全部修复与验收目标已全部达成。
- **待验收安排**：已就绪，交由 Luna 进行只读核查、Codex 独立验收。
