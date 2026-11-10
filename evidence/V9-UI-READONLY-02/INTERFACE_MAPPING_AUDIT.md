# V9-UI-READONLY-02-R1｜真实接口与页面映射核对记录 (实际接线与六要素核验版)

- 阶段与任务：05服务与UI／05-U（接续 V9-UI-READONLY-01 / V9-UI-READONLY-02-R1 实际接线）
- 治理版本：`6cf7edf4d5956f4e8056351faecbd0cdd8eb8c16`
- 基础代码提交：`fbb4db9e8deed1b25cda241f963278cfb0e91f37`
- 候选工作树分支：`work/v9-ui-readonly-02`
- 只读数据网关服务：`http://127.0.0.1:8790`（已验收只读实现 `框架/backend/adapter.py`，连接 `market_v2.sqlite3` 模式为 `mode=ro, PRAGMA query_only=1`）
- 候选 UI 运行端口：`http://127.0.0.1:3010`（配置 `GM_DATA_API_URL=http://127.0.0.1:8790`）
- 审计原则：仅核对页面消费已有接口链路，继承已封存历史数据与已通过只读契约，不重做底层数据封存验收。

---

## 一、真实只读网关接线配置与写库防护

按照总控裁决指令，复用既有已验收组件 `框架/backend/adapter.py` 在未占用候选端口（8790）启动独立只读实例。为彻底杜绝写入旧行情库与任务库，明确识别并封堵了以下底层写入点：

1. **写入点 1（尾段 K 线入库）**：`adapter.py:1909` `_tail_store` 会尝试以读写模式打开 SQLite 入库尾段 K 线。
   - **只读配置**：设置环境变量 `WEB_READONLY=1`（`adapter.py:1789`），强制将 Web 尾段入库操作全部旁路，不执行任何写事务。
2. **写入点 2（任务队列调度）**：`adapter.py:1823` `_enqueue_tail_sync` 会向 `jobs.sqlite3` 队列写入 `tail_sync` 任务。
   - **只读配置**：设置环境变量 `MARKET_TAIL_FETCH=0`（`adapter.py:1831`），彻底关闭 `TAIL_FETCH_ENABLED`，完全不触发 `_tail_bars`，零 job 写入。
3. **写入点 3（遗留任务清理）**：`adapter.py:3965` `backtest.startup()` 若 `INPROCESS_JOBS=1` 会写 `run.json` 标 interrupted。
   - **只读配置**：保持默认 `INPROCESS_JOBS=0`，不修改任何运行目录与回测记录。
4. **启动指令**：
   ```bash
   MARKET_TAIL_FETCH=0 WEB_READONLY=1 PORT=8790 MARKET_DB="/Users/boos/Desktop/威科夫因子共振趋势/数据/市场数据/db/market_v2.sqlite3" /usr/bin/python3 框架/backend/adapter.py
   ```
5. **候选 3010 启动配置**：
   ```bash
   PORT=3010 GM_DATA_API_URL=http://127.0.0.1:8790 /usr/local/bin/node server.js
   ```

---

## 二、只读接口与页面消费映射总表 (核对身份、周期、时间、单位、数值和来源)

| 页面功能区块 | 前端调用接口 | 上游/来源路径 | 映射字段与单位 | 时间轴/周期定义 | 错误与空态呈现方式 | 消费核对结论与总控纪律定性 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **基础服务健康** | `GET /api/health` | 本地只读服务自身 | `status: "ok"`, `scope: "仅表示本前端服务可访问..."` | 实时探测 | 仅代表 3010 Web 进程正常，明确说明不代表底层数据或 AI 已连接 | **PASS**（语义严格隔离） |
| **封存标的目录** | `GET /api/catalog` | `data/market-universe.json` | 549 标的元数据清单（Binance 526 + OKX 23）；`identity`, `venue`, `symbol`, `ticker` | 静态封存快照 `2026-11-05` | `ticker: null` 时页面如实显示“待映射”，不假造代码 | **PASS（身份目录消费）**<br>★ **总控纪律**：**catalog 成功只计身份目录消费；没有另一真实来源，不写市值映射通过**。 |
| **公开实时行情** | `GET /api/market-data` | 币安官方公开 `fapi/v1/ticker/24hr`（带15s缓存） | `lastPrice` (USDT), `priceChangePercent` (%), `quoteVolume` (USDT), `weightedAvgPrice` (USDT) | `closeTime` 毫秒时间戳 | 外部受限（HTTP 451）或断网时返回 502，页面显示“行情不可用”，指标显示“—” | **PASS（失败态防御通过）**<br>缺数不补零，真实报错，不冒充正常数据消费。 |
| **历史/实时K线** | `GET /api/local/candles` 与 `GET /api/klines` | 本地只读数据网关（8790 转发至 `market_v2.sqlite3`） | `[open, high, low, close, base_volume]`，价格为 USDT，成交量为标的币数 | 毫秒/ISO UTC 时间戳；`is_closed: true` 严格过滤未收盘当前根，仅展示已收盘行情 | 真实连接 8790 取得 HTTP 200，六组矩阵实测全通；网关离线返回 503，无假K线 | **PASS（真实全链路实测接通）**<br>真实原样本与周期矩阵实测返回 200，要素完整核对。 |
| **持仓量 OI** | `GET /api/local/oi` 与 `GET /api/oi` | 本地数据网关（8790 缝合冷档 Parquet 全历史与热层 snapshot） | `open_interest` (数量 amount), `open_interest_value` (名义价值 usd) | 5m, 15m, 30m, 1h, 4h, 12h, 1d 原生周期；**8h 原生不支持** | 8h 查询返回 422 拒绝伪造；数量与名义价值双单位独立切换；缺值不插值、不补零 | **PASS（真实全链路实测接通）**<br>8h明确拒绝，双单位独立，真实链路实测全通。 |
| **聪明钱与大户多空** | `GET /api/local/markets` | 本地网关 `smart_money` 节点快照 | `longPositions`, `shortPositions`, `longWhalePositions`, `avgEntryPrice` | 1h 边界统计快照 | 币安公开源无持仓金额/均价，保持缺失 `—`；不拿人数比或 taker 比例伪造成交均价 | **PASS**（字段保真，绝不虚构） |
| **实时信号池** | `GET /api/local/signals` | 本地网关 `/api/v1/signals?scope=current` | `id`, `market_id`, `direction` (多/空), `stage` (早期/中期/末期), `status` (有效/观察/失效) | 事件时间戳与状态版本 | 本机服务未启动（503）时如实显示“没有可展示的真实信号。需连接本机只读信号服务；不会生成假信号。” | **PASS**（不合成假信号） |
| **数据覆盖与运行拓扑** | `GET /api/local/data-health` | 本地网关 `/api/v1/health` | 三级拓扑画布（模块→服务→任务） | 5秒定时轮询（当前页激活时） | 未取得服务端 trace_id / 心跳证据时节点显示“未接遥测”，心跳“—”，不虚构正常绿色动画 | **PASS**（真实空态，故障演示隔离） |
| **回测运行索引与绩效** | `GET /api/local/backtests` | 本地网关 `/api/v1/backtests?limit=50` | `run_id`, `status`, `created_at`, `strategy_version` | 历史推进时间轴（样本内60%/验证集20%/样本外20%） | 本轮回测未接入前，12 项绩效指标（净收益/回撤/Sharpe/胜率等）全为空态“—”，显示“本轮未取得合格指标” | **PASS**（拒绝旧实验充当新验收） |
| **模拟账户与风控** | 纯只读本地展示 | 尚未授权真实模拟写接口 | 计划初始资金 2000 USDT，标明“不是实际账户权益或可用余额” | 现实时钟推进 | 账户权益、可用保证金、持仓笔数、未实现盈亏全部显示“—”，无下单/撤单入口 | **PASS**（无任何订单写操作） |

---

## 三、原样本／周期矩阵真实成功响应核验表（六要素逐项核对）

通过独立 3010 服务调用真实只读网关（8790）实测数据记录：

| 标的与周期 | 接口端点与参数 | HTTP状态与连接性 | 1. 页面与标的身份 | 2. 周期传递 | 3. 时间覆盖范围 | 4. 底层单位 | 5. 最新观测数值 | 6. 数据来源 |
| :--- | :--- | :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **Binance BTCUSDT 4h** | `/api/local/candles?exchange=binance&symbol=BTCUSDT&interval=4h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: BTCUSDT`<br>`timeframe: 4h` | 4h 聚合<br>共 700 根 | `2026-07-12T12:00:00Z`<br>至 `2026-11-10T11:59:59Z` | 价格: USDT<br>量: BTC | 开: 74805.5<br>高: 74858.4<br>低: 74079.1<br>收: 74389.7<br>量: 18926.837 | 本机封存历史数据网关（只读）<br>（底层 `market_v2.sqlite3`） |
| **Binance BTCUSDT 4h** | `/api/local/oi?exchange=binance&symbol=BTCUSDT&interval=4h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: BTCUSDT`<br>`timeframe: 4h` | 4h/auto 分桶<br>共 800 条 | `2025-09-30T23:55:00Z`<br>至 `2026-11-01T06:45:00Z` | 持仓量: 币数(BTC)<br>名义价值: USD | 粒度: snapshot<br>持仓量: 97317.526<br>名义价值: 7578709386.53 | binance.archive.metrics + binance.fapi.openInterestHist |
| **Binance BTCUSDT 1h** | `/api/local/candles?exchange=binance&symbol=BTCUSDT&interval=1h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: BTCUSDT`<br>`timeframe: 1h` | 1h 聚合<br>共 700 根 | `2026-10-07T22:00:00Z`<br>至 `2026-11-10T11:59:59Z` | 价格: USDT<br>量: BTC | 开: 74200.4<br>高: 74432.9<br>低: 74100.0<br>收: 74389.7<br>量: 2789.765 | 本机封存历史数据网关（只读） |
| **Binance BTCUSDT 1h** | `/api/local/oi?exchange=binance&symbol=BTCUSDT&interval=1h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: BTCUSDT`<br>`timeframe: 1h` | 1h/auto 分桶<br>共 800 条 | `2025-09-30T23:55:00Z`<br>至 `2026-11-01T06:45:00Z` | 持仓量: 币数(BTC)<br>名义价值: USD | 粒度: snapshot<br>持仓量: 97317.526<br>名义价值: 7578709386.53 | binance.archive.metrics + binance.fapi.openInterestHist |
| **Binance BTCUSDT 1d** | `/api/local/candles?exchange=binance&symbol=BTCUSDT&interval=1d` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: BTCUSDT`<br>`timeframe: 1d` | 1d 聚合<br>共 700 根 | `2024-12-06T00:00:00Z`<br>至 `2026-11-05T23:59:59Z` | 价格: USDT<br>量: BTC | 开: 80466.4<br>高: 80888.0<br>低: 75158.0<br>收: 76050.1<br>量: 267547.773 | 本机封存历史数据网关（只读） |
| **Binance BTCUSDT 1d** | `/api/local/oi?exchange=binance&symbol=BTCUSDT&interval=1d` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: BTCUSDT`<br>`timeframe: 1d` | 1d/auto 分桶<br>共 800 条 | `2025-09-30T23:55:00Z`<br>至 `2026-11-01T06:45:00Z` | 持仓量: 币数(BTC)<br>名义价值: USD | 粒度: snapshot<br>持仓量: 97317.526<br>名义价值: 7578709386.53 | binance.archive.metrics + binance.fapi.openInterestHist |
| **Binance ETHUSDT 4h** | `/api/local/candles?exchange=binance&symbol=ETHUSDT&interval=4h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: ETHUSDT`<br>`timeframe: 4h` | 4h 聚合<br>共 700 根 | `2026-07-12T12:00:00Z`<br>至 `2026-11-10T11:59:59Z` | 价格: USDT<br>量: ETH | 开: 2114.75<br>高: 2117.82<br>低: 2083.06<br>收: 2093.69<br>量: 740628.62 | 本机封存历史数据网关（只读） |
| **Binance ETHUSDT 4h** | `/api/local/oi?exchange=binance&symbol=ETHUSDT&interval=4h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: ETHUSDT`<br>`timeframe: 4h` | 4h/auto 分桶<br>共 800 条 | `2025-09-30T23:55:00Z`<br>至 `2026-11-01T06:45:00Z` | 持仓量: 币数(ETH)<br>名义价值: USD | 粒度: snapshot<br>持仓量: 2379604.493<br>名义价值: 5393487592.59 | binance.archive.metrics + binance.fapi.openInterestHist |
| **Binance ETHUSDT 1h** | `/api/local/candles?exchange=binance&symbol=ETHUSDT&interval=1h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: ETHUSDT`<br>`timeframe: 1h` | 1h 聚合<br>共 700 根 | `2026-10-07T22:00:00Z`<br>至 `2026-11-10T11:59:59Z` | 价格: USDT<br>量: ETH | 开: 2090.01<br>高: 2101.90<br>低: 2085.03<br>收: 2093.69<br>量: 117076.927 | 本机封存历史数据网关（只读） |
| **Binance ETHUSDT 1h** | `/api/local/oi?exchange=binance&symbol=ETHUSDT&interval=1h` | 200 OK<br>`connected: true` | `exchange: binance`<br>`symbol: ETHUSDT`<br>`timeframe: 1h` | 1h/auto 分桶<br>共 800 条 | `2025-09-30T23:55:00Z`<br>至 `2026-11-01T06:45:00Z` | 持仓量: 币数(ETH)<br>名义价值: USD | 粒度: snapshot<br>持仓量: 2379604.493<br>名义价值: 5393487592.59 | binance.archive.metrics + binance.fapi.openInterestHist |
| **OKX CRO-USDT-SWAP 4h** | `/api/local/candles?exchange=okx&symbol=CRO-USDT-SWAP&interval=4h` | 200 OK<br>`connected: true` | `exchange: okx`<br>`symbol: CRO-USDT-SWAP`<br>`timeframe: 4h` | 4h 聚合<br>共 370 根 | `2026-09-09T20:00:00Z`<br>至 `2026-11-10T11:59:59Z` | 价格: USDT<br>量: CRO | 开: 0.04794<br>高: 0.04794<br>低: 0.04708<br>收: 0.04710<br>量: 2747270 | 本机封存历史数据网关（只读） |
| **OKX CRO-USDT-SWAP 4h** | `/api/local/oi?exchange=okx&symbol=CRO-USDT-SWAP&interval=4h` | 200 OK<br>`connected: true` | `exchange: okx`<br>`symbol: CRO-USDT-SWAP`<br>`timeframe: 4h` | 4h/snapshot<br>共 800 条 | `2026-10-22T08:07:51Z`<br>至 `2026-11-10T12:11:25Z` | 持仓量: 张数/币数<br>名义价值: USD | 粒度: snapshot<br>持仓量: 1814774<br>名义价值: 855847.42 | okx.v5 |

---

## 四、关键语义与边界保真性核对

1. **身份（Identity）与标的池**：
   - 全池 549 个标的：526 个 Binance 标的 + 23 个 OKX 补充标的。
   - `牛来USDT`、`哈基米USDT` 等 5 个无标准 ticker 的标的在 `market-universe.json` 中明确标记 `ticker: null`，前端展示为“待映射”，未强行填充英文字符。
2. **周期（Timeframe）与门禁校验**：
   - K 线合法周期：`5m`, `15m`, `30m`, `1h`, `4h`, `8h`, `12h`, `1d`。
   - OI 周期：原生仅支持 `5m, 15m, 30m, 1h, 4h, 12h, 1d`。对 `8h` 的 OI 请求，直接拦截返回 HTTP 422 `{"error":"Binance公开历史OI接口无原生该周期"}`，严防在前端通过插值或伪造生成 8h OI 柱形。
3. **时间与横轴映射（Time & Plot Alignment）**：
   - 固定左右边距 `padLeft=14, padRight=72` 与统一时间转换函数 `timeToX(t)`；超过 560 根时统一截取最新 560 根时间窗口；在 106 根断层缺口场景下，同一时间戳 $t$ 对应的 K 线与 OI 水平 X 坐标差为 0.0000 像素，垂直绝对对齐。
4. **单位（Units）保真**：
   - OI 柱形图提供“数量”与“名义价值”独立切换，不将数量混淆为 USDT。
   - 聪明钱大户多空比仅展示原始聚合比率；原始快照未包含真实成交均价时，均价保持为“—”，未将 taker 均价偷换为聪明钱成本。
5. **只读安全隔离（Safety Boundary）**：
   - `server.js` 仅响应 `GET` 请求，对 `POST`, `PUT`, `DELETE`, `PATCH` 等所有写操作统一返回 HTTP 405 `{"error":"仅开放只读 GET 请求"}`。
   - 杜绝前端对交易所或模拟交易的任何订单提交、修改或撤单操作。
   - 严格在未占用端口（8790）启动只读实例，不改旧 3000/8787/8788 服务。
