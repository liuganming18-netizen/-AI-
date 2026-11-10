# V9-UI-READONLY-02｜真实接口与页面映射核对记录

- 阶段与任务：05服务与UI／05-U（接续 V9-UI-READONLY-01）
- 治理版本：`6cf7edf4d5956f4e8056351faecbd0cdd8eb8c16`
- 基础代码提交：`fbb4db9e8deed1b25cda241f963278cfb0e91f37`
- 独立候选运行端口：`http://127.0.0.1:3010`
- 审计原则：仅核对页面消费已有接口链路，继承已封存历史数据与已通过只读契约，不重做底层数据封存验收。

---

## 一、只读接口与页面消费映射总表

| 页面功能区块 | 前端调用接口 | 上游/来源路径 | 映射字段与单位 | 时间轴/周期定义 | 错误与空态呈现方式 | 消费核对结论 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **基础服务健康** | `GET /api/health` | 本地只读服务自身 | `status: "ok"`, `scope: "仅表示本前端服务可访问..."` | 实时探测 | 仅代表 3010 Web 进程正常，明确说明不代表底层数据或 AI 已连接 | **PASS**（语义严格隔离） |
| **封存标的目录** | `GET /api/catalog` | `data/market-universe.json` | 549 标的元数据清单（Binance 526 + OKX 23）；`identity`, `venue`, `symbol`, `ticker` | 静态封存快照 `2026-11-05` | `ticker: null` 时页面如实显示“待映射”，不假造代码 | **PASS**（身份唯一，549行无截断） |
| **公开实时行情** | `GET /api/market-data` | 币安官方公开 `fapi/v1/ticker/24hr`（带15s缓存） | `lastPrice` (价格/USDT), `priceChangePercent` (24h涨跌幅/%), `quoteVolume` (24h成交量/USDT), `weightedAvgPrice` (VWAP/USDT) | `closeTime` 毫秒时间戳 | 外部受限（HTTP 451）或断网时返回 502，页面显示“行情不可用”，指标显示“—” | **PASS**（缺数不补零，真实报错） |
| **历史/实时K线** | `GET /api/klines` 与 `GET /api/local/candles` | 优先本地封存数据网关（8787），备用币安官方 `fapi/v1/klines` | `[open, high, low, close, volume]`，价格为 USDT，成交量为张数/币数 | 毫秒 UTC 时间戳；`isClosedBar` 严格过滤未收盘当前根，仅展示已收盘行情 | 8787 未启动返回 503，币安受限返回 502；图表显示真实错误文案，无假K线 | **PASS**（支持 8 种已批准周期） |
| **持仓量 OI** | `GET /api/oi` 与 `GET /api/local/oi` | 本地数据网关与币安官方 `openInterestHist` | `sumOpenInterest` (数量 amount), `sumOpenInterestValue` (名义价值 usd) | 5m, 15m, 30m, 1h, 4h, 12h, 1d 原生周期；**8h 原生不支持** | 8h 查询返回 422 拒绝伪造；数量与名义价值双单位独立切换；缺值不插值、不补零 | **PASS**（8h明确拒绝，单位独立） |
| **聪明钱与大户多空** | `GET /api/local/markets` | 本地网关 `smart_money` 节点快照 | `longPositions`, `shortPositions`, `longWhalePositions`, `avgEntryPrice` | 1h 边界统计快照 | 币安公开源无持仓金额/均价，保持缺失 `—`；不拿人数比或 taker 比例伪造成交均价 | **PASS**（字段保真，绝不虚构） |
| **实时信号池** | `GET /api/local/signals` | 本地网关 `/api/v1/signals?scope=current` | `id`, `market_id`, `direction` (多/空), `stage` (早期/中期/末期), `status` (有效/观察/失效) | 事件时间戳与状态版本 | 本机服务未启动（503）时如实显示“没有可展示的真实信号。需连接本机只读信号服务；不会生成假信号。” | **PASS**（不合成假信号） |
| **数据覆盖与运行拓扑** | `GET /api/local/data-health` | 本地网关 `/api/v1/health` | 三级拓扑画布（模块→服务→任务） | 5秒定时轮询（当前页激活时） | 未取得服务端 trace_id / 心跳证据时节点显示“未接遥测”，心跳“—”，不虚构正常绿色动画 | **PASS**（真实空态，故障演示隔离） |
| **回测运行索引与绩效** | `GET /api/local/backtests` | 本地网关 `/api/v1/backtests?limit=50` | `run_id`, `status`, `created_at`, `strategy_version` | 历史推进时间轴（样本内60%/验证集20%/样本外20%） | 本轮回测未接入前，12 项绩效指标（净收益/回撤/Sharpe/胜率等）全为空态“—”，显示“本轮未取得合格指标” | **PASS**（拒绝旧实验充当新验收） |
| **模拟账户与风控** | 纯只读本地展示 | 尚未授权真实模拟写接口 | 计划初始资金 2000 USDT，标明“不是实际账户权益或可用余额” | 现实时钟推进 | 账户权益、可用保证金、持仓笔数、未实现盈亏全部显示“—”，无下单/撤单入口 | **PASS**（无任何订单写操作） |

---

## 二、关键语义与边界保真性核对

1. **身份（Identity）与标的池**：
   - 全池 549 个标的：526 个 Binance 标的 + 23 个 OKX 补充标的。
   - `牛来USDT`、`哈基米USDT` 等 5 个无标准 ticker 的标的在 `market-universe.json` 中明确标记 `ticker: null`，前端展示为“待映射”，状态为“待映射”，未强行填充英文字符。

2. **周期（Timeframe）与 OI 拒绝门禁**：
   - K 线合法周期：`5m`, `15m`, `30m`, `1h`, `4h`, `8h`, `12h`, `1d`。
   - OI 周期：`server.js` 与 `assets/app.js` 严密限制仅原生允许 `5m, 15m, 30m, 1h, 4h, 12h, 1d`。对 `8h` 的 OI 请求，直接拦截返回 HTTP 422 `{"error":"Binance公开历史OI接口无原生该周期"}`，严防在前端通过插值或伪造生成 8h OI 柱形。

3. **单位（Units）保真**：
   - OI 柱形图提供“数量”与“名义价值”独立切换，不将数量混淆为 USDT。
   - 聪明钱大户多空比仅展示原始聚合比率；原始快照未包含真实成交均价时，均价保持为“—”，未将 taker 均价偷换为聪明钱成本。

4. **时间轴（Timeline）与闭合 K 线保护**：
   - 所有 K 线展示经 `isClosedBar` 检验：`bar.time + periodMs[tf] <= Date.now()`，未收盘的当根 K 线不进入历史指标计算。
   - 回测逐根播放器严格按时间递增推进（`cursor++`），禁止倒流或未来函数。

5. **只读安全隔离（Safety Boundary）**：
   - `server.js` 仅响应 `GET` 请求，对 `POST`, `PUT`, `DELETE`, `PATCH` 等所有写操作统一返回 HTTP 405 `{"error":"仅开放只读 GET 请求"}`。
   - 彻底杜绝前端对交易所或模拟交易的任何订单提交、修改或撤单操作。
