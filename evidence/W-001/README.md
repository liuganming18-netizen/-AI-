# W-001 · V3.1 新产物路径 ↔ 前端现存旧路径 映射对照清单

| 项 | 值 |
|---|---|
| 工单 | W-001 |
| 接收人 | Luna |
| 执行人 | Work |
| 分支 | `wyckoff-v3.1-main` |
| 基线提交 | `9e3bc14b` |
| 路径基准 | 工程根 `<ROOT>` = `威科夫因子共振趋势/`（下文所有路径均相对它） |
| 实测复核 | `verify_paths.py` → `out/PATH-CHECK.json` / `out/PATH-CHECK.md` |

**一句话结论**：数据产物的权威路径已全部迁到工程内（`数据/paper`、`数据/市场数据/db`、`数据/研究输入`），**旧目录 `框架/最新唐奇安威科夫接通与收尾_2026-10-11/` 只剩纸面账本四个旧库文件**；代码里仍有 **14 个文件、24 行**命中旧路径，其中 **1 个死常量、3 条死路径、2 处注释与事实相反**，前端有 **3 处路径文案**（1 处数字过期、1 处只给文件名、1 处正确）。

**交付物（4 个文件，均在 `evidence/W-001/`）**

| 文件 | 用途 |
|---|---|
| `README.md` | 本文件：人读版对照清单 |
| `paths_map.json` | 机器可读对照表（A 数据 / B 代码 / C 文案 / D 接口四段） |
| `verify_paths.py` | 实测复核器：逐条 `lstat`，只读 |
| `out/PATH-CHECK.json`、`out/PATH-CHECK.md` | 复核结果（本次 31 条：存在 25 / 缺失 6） |

---

## 0. 本次复核规模

`verify_paths.py` 逐条 `lstat` 了 **31** 条具体路径（不打开、不写、不删）：

| 结果 | 条数 |
|---|---|
| 存在 | 25 |
| 缺失 | 6 |
| 异常 | 0 |

6 条缺失全部是**有意义**的缺失，不是笔误：

| 缺失项 | 说明 |
|---|---|
| `数据/paper/ledger_identity.json` | 账本身份权威声明文件不存在（代码按它做身份核对） |
| `框架/…/5000U收尾与回测条件终验6/10_研究参数生效视图_v2_2_user5000_shared.json` | 旧根下确实没有（新根下有，1,194 B） |
| `框架/…/5000U收尾与回测条件终验6/回测输入收口/quote_sidecar` | `wx_daily_375.py:66` 指向它 |
| `框架/…/接线路整改3/运行绑定_G01_G05.json` | `wx_gate.py:82` 指向它 |
| `回测日志/回测资源改造_2026-10-14/ledgers_ra` | `live_store.py:61` 期望的冻结回测账本目录 |
| `回测日志/全池真实费用回测_2026-10-13/ledgers` | `live_store.py:63` 同上 |

---

## A. 数据产物路径对照

| 编号 | 产物 | 新路径（权威） | 旧路径（现存引用） | 现状 |
|---|---|---|---|---|
| D-01 | 纸面账本四臂 | `数据/paper/paper_{3a,3b,3c,3d}.sqlite3`（各 163,840 B） | `框架/最新唐奇安威科夫接通与收尾_2026-10-11/量价信号接入与模拟历史并行43/纸面模拟43/paper_*.sqlite3`（各 151,552 B，2026-10-15） | **旧路径仍在磁盘但已不被读取** |
| D-02 | 账本身份声明 | `数据/paper/ledger_identity.json` | — | **目标缺失** |
| D-03 | 实时信号库 | `框架/backend/live/live_signals.sqlite3`（1,507,328 B） | 同目录 `live_signals_legacy_backup.sqlite3`（93,130,752 B，2026-10-15） | 新库在用；旧备份并存不读 |
| D-04 | 热层行情库 | `数据/市场数据/db/market_v2.sqlite3`（实测 14,050,938,880 B，另带约 434 MB `-wal`） | 旧绝对路径已清理，不再作默认 | **在用** |
| D-05 | 冻结参数生效视图 v2.2 | `数据/研究输入/最新唐奇安威科夫接通与收尾_2026-10-11/5000U收尾与回测条件终验6/10_研究参数生效视图_v2_2_user5000_shared.json`（1,194 B） | 同串放到 `框架/` 根下（不存在） | **在用（同名不同根，见坑 1）** |
| D-06 | 研究输入（外部迁移件） | `数据/研究输入/外部迁移_2026-10-16/`（6 个子目录） | 原外部工作树检出目录 | **在用** |
| D-07 | quote_sidecar | 无 | `框架/…/5000U收尾与回测条件终验6/回测输入收口/quote_sidecar` | **死路径（目标不存在）** |
| D-08 | 375 逐日名单 | 无 | `框架/…/375逐日名单与实际输入接通27/02_375逐日名单_{panel}.json` | **死路径（目标不存在）** |
| D-09 | G01–G05 运行绑定台账 | 无 | `框架/…/接线路整改3/运行绑定_G01_G05.json` | **死路径（目标不存在）** |
| D-10 | 旧 v3.1 核心逐字节封存 | `策略/WyckoffMaster/_legacy_archive_v31/`（16 项，含 `ARCHIVE_MANIFEST.json`、`execution.py` 58,397 B） | — | **在用（只读保留）** |
| D-11 | 运行时状态与锁 | `框架/backend/locks/`（`paper_runtime_3a_observe.json` 实测 1,995,201 B） | — | 在用；注释里的量级过期（坑 4） |
| D-12 | 行情快照 | `框架/backend/快照/public_extras/`、`框架/backend/快照/smart_money/` | — | 在用 |
| D-13 | 新开仓闸门 | `框架/backend/live/paper_new_entry_gate.json`（307 B） | — | 在用 |
| D-14 | 冻结历史回测账本 | `回测日志/回测资源改造_2026-10-14/ledgers_ra`、`回测日志/全池真实费用回测_2026-10-13/ledgers` | — | **两个目录都不存在**（代码自带「不存在 ⇒ 缺失态」语义） |

**权威解析链**（改环境变量即可换根，不需要改代码）：

| 变量 | 缺省值 | 定义位置 |
|---|---|---|
| `PAPER_LEDGER_DIR` | `<ROOT>/数据/paper` | `live_store.py:48-54` |
| `LIVE_DIR` / `LIVE_DB` | `框架/backend/live` / `…/live_signals.sqlite3` | `live_store.py:66-67` |
| `MARKET_DB` | `<ROOT>/数据/市场数据/db/market_v2.sqlite3` | `live_store.py:70-71` |
| `EFFECTIVE_VIEW` | `<ROOT>/数据/研究输入/…/10_研究参数生效视图…json` | `project_paths.py:77-78` |
| `RESEARCH_INPUT_DIR` | `<ROOT>/数据/研究输入` | `project_paths.py:57-58` |
| `PAPER_GATE_FILE` | `框架/backend/live/paper_new_entry_gate.json` | `paper_engine.py:252` |
| `LEGACY_SITE_DIR` | 未设 ⇒ 不注入任何工程外路径 | `live_worker.py:33`、`live_signal_engine.py:52` |

---

## B. 后端源码里的旧路径引用（24 行命中 / 14 个文件，逐文件全列）

| 编号 | 文件:行 | 引用的是什么 | 旧路径 | 现状 |
|---|---|---|---|---|
| C-01 | `live_worker.py:38,40` | `LEGACY_SITE_DIR` 条件注入（`ROOT` / `W43`） | `框架/最新唐奇安…/`、`…/量价信号接入与模拟历史并行43` | 仅条件路径：未设环境变量时 `W43=None`，注入处有 None 跳过守卫，**不会崩** |
| C-02 | `live_signal_engine.py:8,55,57` | docstring + 同一套条件注入 | 同上 | **死路径 + 潜在崩溃点**：`:528` `_sha256_file(W43/'_warmup43.py')`、`:658` `W43/"12_实时结构提示_43.jsonl"` 直接拼接，**无 None 守卫** ⇒ `LEGACY_SITE_DIR` 未设时走到即 `TypeError`（本单未实跑触发） |
| C-03 | `live_store.py:31` | 模块常量 `W43` | `…/量价信号接入与模拟历史并行43` | **死常量**：定义后全仓无第二处引用 |
| C-04 | `live_store.py:11,35` | docstring（`:11` 写 `纸面模拟43/paper_{arm}.sqlite3`）+ 历史缺陷说明（`:35`） | `纸面模拟43/…` | **文案过期**：账本权威路径已改为 `数据/paper`（见 D-01） |
| C-05 | `paper_engine.py:32` | docstring | `纸面模拟43/paper_{arm}.sqlite3` | **文案过期**，同上 |
| C-06 | `project_paths.py:75` | `EFFECTIVE_VIEW_RELPATH` | `最新唐奇安…/5000U收尾…/10_研究参数生效视图…json` | **新根**（`RESEARCH_INPUT_DIR / 该相对路径`），仅目录名沿用旧名 |
| C-07 | `live_pool.py:122` | `FROZEN_VIEW` 缺省值 | 同上 | **新根**：`parents[2]/数据/研究输入/…`，且 `EFFECTIVE_VIEW` 可覆盖 |
| C-08 | `strategy_contracts.py:134` | `DEFAULT_VIEW_PATH` | 同上 | **新根**：`PROJECT_ROOT/数据/研究输入/…` |
| C-09 | `strategies/wx_daily_375.py:66-67` | `SIDECAR`（报价旁路） | `框架/…/5000U收尾与回测条件终验6/回测输入收口/quote_sidecar` | **死路径（目标不存在）** |
| C-10 | `strategies/wx_daily_375.py:792` | `daily_doc_path()` | `框架/…/375逐日名单与实际输入接通27/02_375逐日名单_{panel}.json` | **死路径（目标不存在）** |
| C-11 | `strategies/wx_gate.py:78` | `SUITE_DIR`（G01–G05 台账根） | `框架/最新唐奇安…/` | **死路径**：下游 `接线路整改3/运行绑定_G01_G05.json` 不存在 ⇒ 代码自带「不存在即全 pending」 |
| C-12 | `strategies/registry.py:162,186` | 对外输出的 `legacy_archive` 字符串 | `策略/WyckoffMaster/_legacy_archive_v31` | **在用**：该目录实际存在（16 项），字符串与磁盘一致 |
| C-13 | `legacy_retirement.py:63` | `LEGACY_SOURCE_DIRS` 元组成员（只读保留） | 同上 | **在用**：路径存在 |
| C-14 | `verify_exec_wiring.py:97-99` | 阻塞项的 `unblocked_by` 建议文案 | `框架/最新唐奇安…/` | 仅提示文案，不影响执行 |
| C-15 | `accum_research.py:144-145` | `COST_MODELS.effective_view.source` 溯源字符串 | `数据/研究输入/最新唐奇安…/…生效视图…json` | **在用**：已是新根，写进结果供溯源 |
| C-16 | `replay_calc.py:58-59` | `CALIBERS.生效视图_5bps.来源` 溯源字符串 | 同上 | **在用**：已是新根 |

> 编号到 C-16 是为了覆盖 14 个文件的 24 行；其中 C-01/C-02 各占同一文件多行，合计即 24 行。

**顺带查出的一个隐患**：`live_signal_engine.py` 第 69/73 行赋值的 `LEGACY_PIPELINE_AVAILABLE`，全文件**再无读取点**——是写入型标志。第 48 行注释写「fail-soft（见下方 `LEGACY_PIPELINE_AVAILABLE`）」，但这个变量没有任何消费者。

**顺带查出的一个隐患**：`live_signal_engine.py` 第 69/73 行赋值的 `LEGACY_PIPELINE_AVAILABLE`，全文件**再无读取点**——是写入型标志。第 48 行注释写「fail-soft（见下方 `LEGACY_PIPELINE_AVAILABLE`）」，但这个变量没有任何消费者。

---

## C. 前端页面上的路径文案（3 处）

| 编号 | 位置 | 页面写的字 | 问题 | 应写成 |
|---|---|---|---|---|
| U-01 | `DataCenterView.tsx:2296` | `热层行情数据库：market_v2.sqlite3 (6.0 GB)` | 容量过期（实测约 13 GiB），且没给目录 | `数据/市场数据/db/market_v2.sqlite3`，容量不写死 |
| U-02 | `StrategyManagementView.tsx:287` | `实时库 live_signals.sqlite3 · 纸面账本 paper_*.sqlite3` | 只给文件名，旧根下也有同名 `paper_*.sqlite3`，无法据此定位 | `框架/backend/live/live_signals.sqlite3` · `数据/paper/paper_*.sqlite3` |
| U-03 | `StrategyManagementView.tsx:437` | `存储路径: 数据/市场数据/db/market_v2.sqlite3` | 无，与权威路径一致 | 保持 |

---

## D. 前端 API 调用 ↔ 后端路由 ↔ 数据文件

**前缀规则**：前端写相对路径，`client.ts:12` 的 `API_PREFIX='/api/v1'` 负责拼（`:42`、`:88`）；开发期由 `vite.config.ts:30-34` 把 `/api` 转发到 `API_PROXY_TARGET`（缺省 `http://127.0.0.1:8787`）。

**前端实际调用的 34 条**（完整逐条见 `paths_map.json` → `D_api.called`），按数据落点归并：

| 落点 | 前端调用 | 后端路由表位置 | 处理函数 |
|---|---|---|---|
| `数据/paper/paper_*.sqlite3` | `GET /paper/accounts`、`POST /paper/orders`、`GET /paper/stops/{id}` | `routes.py:261` / `adapter.py:3792` / `routes.py:282` | `live_api.paper_accounts` / `paper_order_post` / `_paper_stops` |
| `框架/backend/live/live_signals.sqlite3` | `GET /signals`、`POST /signals/clear`、`GET /accounts` | `routes.py:242` / `adapter.py:3810` / `routes.py:290` | `_v1_signals_merged` / `clear_signals` / `live_accounts` |
| `数据/市场数据/db/market_v2.sqlite3` | `GET /markets`、`/markets/{ex}/{mid}/candles|oi|funding`、`/pool`、`/pool/status`、`/data/status`、`/data/oi`、`/data/funding` | `routes.py:222,365,224,226,305,313,316` | `adapter.v1_*` |
| `框架/backend/locks/*.json` | `GET /signals/observe`、`GET /paper/execution_daemon/status`（别名 `/paper/status`） | `routes.py:247` / `routes.py:279,276` | `_signals_observe` / `_paper_daemon_status` |
| `框架/backend/快照/public_extras/` | `GET /markets/{ex}/{mid}/public-extras` | `routes.py:365` | `adapter.v1_public_extras` |
| `框架/backend/快照/smart_money/` | `GET /smart-money/{ex}/{mid}`、`GET /data/smart-money` | `routes.py:252,319` | `smart_money.v1_smart_money` |
| `框架/backend/live/paper_new_entry_gate.json` | `POST /paper/toggle_gate` | `live_api.py:1264` | `paper_toggle_gate` |
| 回测运行库 | `GET|POST /backtests` 及 `/backtests/{id}/…`、`/backtests/batches/…` | `routes.py:296-298` | `backtest_api.route` / `route_post` |
| 币安公开接口（**仅开发期**） | `/bapi/futures/v1/public/future/smart-money/signal/*` | `vite.config.ts:40-43` 转发到 `binance.bh` | 无后端参与；生产构建下不存在，前端降级空态 |

**后端存在但前端没有调用点的 17 条**（说明新产物有接口、页面上没有落点）：

`/overview`、`/strategies`、`/strategies/registered`、`/strategy`（旧单数）、`/strategies/{id}`、`/paper/identity`、`/paper/positions`、`/paper/funding-audit`、`/paper/legacy-audit`、`/live/health`、`/reviews/*`、`/data/coverage`、`/data/field-contract`、`/data/cold-archive-status`、`/system-flow/paper-reconciliation`、`/data/backfill`、`/markets/{ex}/{mid}/rule-snapshots`。

---

## 五个必须知道的坑

**坑 1 · 同名不同根。** 字符串 `最新唐奇安威科夫接通与收尾_2026-10-11` 现在有两层含义：作「旧工作区站点根」时在 `框架/` 下（只剩纸面账本旧库）；作「研究输入子目录」时在 `数据/研究输入/` 下（生效视图在这里）。看路径不能只看目录名，要看**根**。

**坑 2 · 前端硬编码绕过了统一前缀。** `PositionPanel.tsx:195` 直接 `fetch('/api/v1/paper/stops/…')`，没走 `client.ts` 的 `API_PREFIX`。今天结果等价，但将来改前缀时这一处不会跟着改。

**坑 3 · 注释与事实相反的两处。**
- `project_paths.py:79-81` 写「生效视图 v2.2 已随旧工作区一并删除，全盘无副本」——**文件在**（1,194 B），且其 sha256 前缀 `82dec362c182` 与实验身份里的 `view_sha256` 一致。
- `routes.py:236` 写「旧单数接口（前端 client.ts 仍在调用）」——`client.ts` 全文没有 `/strategy`，`src` 下也没有该调用点。

**坑 4 · 数字写死在页面上会烂掉。** 页面写 `6.0 GB`，实测已 13 GiB；`routes.py:78` 写观察明细「约 346 条、约 221 KB」，实测文件已 1,995,201 B。这类数字应改成从接口取，不该写进 JSX 或注释。

**坑 5 · 旧目录不是空的。** `框架/最新唐奇安威科夫接通与收尾_2026-10-11/` 下仍有 4 个 `paper_*.sqlite3`（各 151,552 B）+ 4 个 `-wal` + 4 个 `-shm`。它们**已不被任何代码读取**，但「目录还在、库还在」容易被当成「账本还是那四份」。实际权威账本是 `数据/paper/` 下那四份（各 163,840 B）。

---

## 判定口径与未验证项

- 本清单只回答「哪条路径是新的、哪条是旧的、旧路径还活不活」，**不判定任何业务是否通过**；不得据此宣称策略、回测、模拟盘任一环节通过。
- 「死路径」的判定依据是**目标不存在**（实测 `missing`），不是「没人调用」。
- 「死常量」的判定依据是**全仓 grep 无第二处引用**，不是「看起来没用」。
- **未验证**：C-02 的 `TypeError` 只在代码层推断（`W43=None` + 无 None 守卫），本单**没有实跑** `live_signal_engine` 触发它；触发与否取决于调用路径，需另单实跑确认。
- **未验证**：前端页面实际渲染出来的文案是否与源码一致（本单只读源码，未起前端抓 DOM）。
- 数字全部来自 `out/PATH-CHECK.json`，未手抄；`market_v2.sqlite3` 仍在被写入，重跑 `verify_paths.py` 会得到不同字节数，属正常。

## 复现

```bash
python3 evidence/W-001/verify_paths.py
# 输出 evidence/W-001/out/PATH-CHECK.json 与 PATH-CHECK.md
```

脚本只做 `lstat`，不打开、不写、不删任何被检查文件。
