# D03-R1 契约与最小必要实现

## 一、先回答一个前置问题：为什么是「最小必要实现」而不是「合入补丁」

D03-R1 的**物理补丁在整个工作区不存在**。上一轮（`D05-FRONTEND-API-INTEGRATION`）
已做过全工作区 + Git 历史分支 + 外部隔离目录比对，结论是
`MISSING_PHYSICAL_ARTIFACT / UNVERIFIED`。本轮复核仍是同一结论：
`evidence/` 下无任何 `D03*` 目录，仓库内无 `D03-R1` 差异文件。

所以本轮**不伪造补丁**，改为按验收契约做**最小必要实现**。

**同时明确一条纪律**（工单原话：不得用资金费函数代替 D03-R1 验收）：
`paper_engine.py` 里存在 `funding_events` / `_funding_truth` / `_settle_funding`
**不能**作为「D03-R1 已集成」的证据 —— 那是资金费结算，属另一条契约。

---

## 二、五条契约：做之前的实测状态

| 契约 | 改之前 | 依据 |
|---|---|---|
| ① 交易 API（已平仓逐笔） | **缺失**。只有 `/paper/orders`，把「入场单 / 平仓成交 / 挂单」三类混在一个响应里，没有「已平仓交易 + 净盈亏」的独立口径 | `paper_orders()` |
| ② 零交易契约 | **缺失**。`paper_orders` 对未知臂返回 **200 + `quality.status=bad_request`**，状态码层面无法区分「账户不存在」和「账户在但零成交」 | `live_api.paper_orders` 第 1008–1012 行 |
| ③ 404 契约 | `live_api.py` 内 **0 处 404**（adapter 里只有 `/paper/stops/{id}` 一处） | `grep -E "404" live_api.py` = 空 |
| ④ 503 契约 | **已存在** 3 处：`signal_store_unavailable`(1183)、`_ledger_unavailable`(1278/1304) | `grep -E "503" live_api.py` |
| ⑤ 权益曲线 | 只在 `backtest_review` 里有 `equity_curve`(2291)，**账户维度没有** | `grep equity_curve` |

---

## 三、本轮补了什么（只读、加性、不改交易行为）

### 新增两个端点

| 端点 | 作用 |
|---|---|
| `GET /api/v1/paper/trades` | 已平仓逐笔（来源 `paper_realized`），支持 `arm` / `position_id` / `limit` |
| `GET /api/v1/paper/equity` | 权益曲线（来源 `paper_equity`，按 `ts` 升序），`format=jsonl` 附 NDJSON 文本 |

### 状态码语义（这是 D03-R1 的核心）

| 情形 | 状态码 | 判据 |
|---|---|---|
| 账户在、有成交 | **200** | `items[].total > 0` |
| 账户在、**零成交** | **200** + `no_trades=true` | `items[].total == 0` 且该臂账本可读 |
| **未知臂** | **404** + `quality.note=unknown_arm` | `arm` 不在 `live_store.ARMS` |
| **未知持仓号** | **404** + `quality.note=unknown_position_id` | 指定了 `position_id` 但该臂查不到 |
| **账本不可用** | **503** | 四臂全部 `LedgerUnavailable` / `LedgerIdentityMismatch` |

**为什么零交易必须是 200 而不是 404**：账户是存在的，只是还没有平仓记录。
用 404 表达「还没成交」会让前端把正常空态渲染成「账户不存在」，
这正是上一轮记录的「用空列表冒充接口坏了」的反向错误。

### 新旧两套路由都登记

`框架/backend/api/routes.py`（默认路由表）与 `框架/backend/adapter.py`（`LEGACY_ROUTER=1` 回退长链）
**同时**加了这两条。原因是：只改一处，一旦有人用环境变量回退，
就会出现「新路由能用、旧路由 404」的分叉。两处都改，回滚路径才一致。

---

## 四、两轮实测（互相交叉，不是重复）

### 第一轮：函数级（`verify_d03r1.py`）→ **9 / 9 通过**

隔离方式：把生产账本 `数据/paper/paper_3a.sqlite3` **复制**到临时目录，
用 `PAPER_LEDGER_DIR` 指向副本。

| 用例 | 结果 |
|---|---|
| ① 交易 API·真实交易（arm=3a） | 200，4 笔，`closed_count=1`，`realized_pnl_sum=-21.743079223257155` |
| ② 零交易契约（arm=3b） | 200，`total=0`，`no_trades=true` |
| ③a 404·未知臂 | 404，`unknown_arm` |
| ③b 404·未知持仓号 | 404，`unknown_position_id` |
| ⑤a 权益曲线 | 200，8 个点，`ts` 升序 |
| ⑤b 权益 JSONL | 200，8 行，逐行 `json.loads` 通过，`content_type=application/x-ndjson` |
| ④a 503·交易 API 账本缺失 | 503 |
| ④b 503·权益 API 账本缺失 | 503 |
| 反例·404 与零交易不可混同 | 未知臂 404 vs 零交易 200，两者不同码 |

### 第二轮：真实 HTTP 往返（`verify_d03r1_http.py`）→ **8 / 8 通过**

起一个**只绑 127.0.0.1 随机端口**的 `ThreadingHTTPServer`，挂生产同一套 `adapter.Handler`
（不占 8787，不与生产服务抢端口），账本目录指向临时副本：

| 用例 | HTTP |
|---|---|
| ① 交易 API·真实交易 | **200**（4 笔） |
| ② 零交易契约 | **200**（`no_trades=true`） |
| ③a 404·未知臂 | **404** |
| ③b 404·未知持仓号 | **404** |
| ⑤a 权益曲线 | **200**（8 点） |
| ⑤b 权益 JSONL | **200**（8 行，`application/x-ndjson`） |
| ④a 503·交易 API | **503** |
| ④b 503·权益 API | **503** |

原始输出：`out/d03r1_contract_verify.json`、`out/d03r1_http_verify.json`。

---

## 五、过程中踩到的三个坑（写下来避免下次重犯）

### 坑 1：WAL 库只读打开需要 `-shm`

生产账本是 `journal_mode=wal`。把主文件复制走以后，
`sqlite3.connect("file:...?mode=ro")` 会直接报
`OperationalError: unable to open database file` —— 因为它要创建 `-shm` 而只读模式不允许。

处置：**副本**转成 `PRAGMA journal_mode=DELETE` 再读（只改副本，生产库不动）。

### 坑 2：删 `paper_realized` 造「零交易」会把账本身份搞坏

一开始用「复制 3a 再 `DELETE FROM paper_realized`」造零交易样本，
结果 `verify_ledger_identity` 发现 `paper_account.cash` 与已实现盈亏求和不自洽，
抛 `LedgerIdentityMismatch` ⇒ 变成 503，用例假失败。

处置：零交易样本改用**官方隔离建档路径**（`PAPER_LEDGER_ALLOW_INIT=1`）新建一个**自洽的空账户**。

### 坑 3：只改 `adapter.py` 不生效

默认路由表是 `框架/backend/api/routes.py`（`adapter.route_v1` → `_router().resolve`），
`adapter.py` 里那条长链只在 `LEGACY_ROUTER=1` 时走。
第一轮 HTTP 验证 8 个用例全 404 就是这个原因。
处置：两处都登记，并跑 `tools/route_parity.py` 确认新旧路由一致
（52 个路径比对，2 处差异均为**行情数据实时漂移**，与路由/契约无关）。

---

## 六、边界（不得据此宣称）

- **不得说「D03-R1 端到端验收通过」** —— 本轮验证的是**只读契约**（状态码 + 字段 + 数据来源），
  且在**隔离账本副本**上做；**真实挂钟自动成交仍未验证**（自然候选 0、账本 0 笔已平）。
- **不得说「真实交易已验证」** —— 「真实交易」指的是「账本里真有 4 笔已平仓记录、接口如实读出」，
  不是「策略在真实行情下开平仓成功」。
- **生产账本未被写入**：四个 `.sqlite3` 主文件 mtime 未变（3a `2026-10-30 08:30`、3b/3c/3d `2026-10-28 10:55`）。
  只读打开会生成 0 字节 `-wal` 并刷新 `-shm`，这是 SQLite 只读访问的正常副作用，不是写入。
- `initial_equity` 在 3a 的 `paper_account` 表里查不到（返回 `null`），
  属既有账本状态，本轮**未修改**、也未用默认值冒充。
