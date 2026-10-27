# UI-LINK-01 · 工作台与画布真实接线（交付）

> 施工范围：**只动前端 + 本工单测试**。策略、撮合、账本、后端接口一行未改。
> 判定纪律：只有 **通过 / 失败 / 未验证** 三态；HTTP 200 不作为业务通过的依据；未验证不计入通过。

---

## 0. 一句话结论

| 任务 | 结论 | 一句话依据 |
|---|---|---|
| 1 工作台与画布读同一组真实接口 | **通过** | 同一时刻数值对账 4/4 全等（监控池 549=549、信号 2=2、挂单 0=0、**持仓 1=1**）；画布 20 张卡片全部按业务字段判定，不按 HTTP 码判定 |
| 2 开始／暂停真改服务端总闸 | **通过** | 隔离环境实测控制文件被写（307 B，`sha256=a45f443d…` / 暂停态 `0433c07a…`）；暂停后真发一次开仓得到 **HTTP 409** 且账本挂单数不变；AST 枚举 14 处读闸点，维护类 **0 处** |
| 3 隔离环境真实工作台端到端 | **通过** | 选信号 → 挂单 → 成交 → 持仓 → 止损轨迹 → 平仓，在真实浏览器里**跑了两轮**（BTC、ETH），账本逐笔可复算；第二轮跑在代码最终版上 |
| 4 挂单方向与来源臂 / 待确定 | **通过** | 界面原文「来源: 实时信号触发 · 3a」「方向: 多」「数量与成交价待实际成交时确定。」 |
| 5 回测详情与对账表同一 run_id | **通过（限范围）** | 7 个 run 级端点顶层 `run_id` 全一致；页面「结果身份」渲染出同一 id；缺字段一律「—」。**逐项对账明细表在当前数据里不存在（0/40 条 run 有该对象）⇒ 未验证** |
| 6 画布显示实测状态、给工作台入口 | **通过** | 四态 + 每卡深链；写死绿点与三条结论性文案已全部删除；4 个深链实测全部落地 |

**系统整体：仍不通过。** 本工单只证明「前端接线与显示口径」，不证明策略有效性、不证明真实挂钟自动成交、不证明生产可交付。

---

## 1. 改了什么（源码）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `UI界面/web/public/system-flow.html` | **整体重写**（359 行 → 46208 B，`sha256=9cf3f3f0…`） | 原文件 13 个节点全是写死的绿点 + 结论性文案（「已接通 / 当前运行臂3a / 监控全池526币」）。改为「每卡一个真实端点 + 一个判定函数 + 一个工作台深链」，共 20 张卡片。 |
| `UI界面/web/src/App.tsx` | ① `PAPER_ARMS` 提模块级；② `isGateRunning` 进 `handleOpenQuickPosition` 依赖；③ 新增 `?nav=` 深链（白名单校验） | ①+② 是**陈旧闭包缺陷**：依赖数组漏了 `isGateRunning`，只因 `ARMS` 每帧新建数组导致回调每帧重建而「偶然兜住」，一旦 memo 化立刻显形。③ 是任务 6「提供工作台对应入口」的前提——`currentNav` 原本只是组件内部状态，没有任何 URL 表达。 |
| `UI界面/web/src/components/BacktestWorkbench.tsx` | ①「结果身份」面板改读 `config.config.actual_config`；② 新增 `fmtHashLeaf()`，修掉「口径与审计」视图把对象当文本渲染的崩溃 | ①**真实缺陷**：后端返回 `initial_equity=2000 / fee_rate=0.0005 / slippage_bps=3`，面板却三格全显示「—」，看起来像缺字段，实际是路径少了一层。修后实测显示 `$2,000.00`。②**真实缺陷（会白屏）**：`/quality` 的 `hashes` 是分组对象 `{code:{combined_sha256,files,missing},…}`，原代码 `Object.entries(hashes).map(([k,v]) => <KV v={v}/>)` 把对象塞进 `<span>`，React 抛 `Objects are not valid as a React child`，**整个「口径与审计」视图被错误边界打崩**——而任务 5 的对账表就在这个视图里。现按「合并摘要前 16 位 · files=N · missing=N」渲染，完整 JSON 放 `title`。 |
| `UI界面/web/src/components/backtest/MetricStrip.tsx` | 「收益因子 / 盈亏比」缺失原因改按盈亏笔数判定 | **真实缺陷**：原先只要值缺失就写「缺少盈利样本」，于是在「盈 1 笔、亏 0 笔」的 run 上出现自相矛盾——同一行胜率写着「盈 1 · 亏 0」，下一行却说没有盈利样本。现区分「无盈利样本」与「无亏损样本（分母为 0）」。缺还是缺，但**缺失原因不能编**。 |
| `UI界面/web/src/api/useLiveTrading.ts` | **未改** | 核查后确认取数编排（互斥串行 + 补跑）已正确。 |
| `UI界面/web/src/api/liveTrading.ts` | **未改** | — |
| `UI界面/web/src/components/PositionPanel.tsx` | 「执行守护」徽标不再拿总闸状态顶替；未知时「推进周期」显示「—」而不是 0 | **真实缺陷（自相矛盾）**：原代码 `executionDaemon?.status \|\| (isGateRunning ? 'running' : 'idle')`。账本还在加载、守护状态还没取到的那几秒里 `executionDaemon` 是 `null`，于是徽标显示 **running**，而同一张卡下面的「阻断原因」写着「执行守护未运行 / 进程离线」。而接口 `/paper/execution_daemon/status` 明确返回 `execution_daemon.status="unknown"`、`runtime_identity.status="not_started"`。**总闸开启只代表"允许新开仓"，不代表有守护进程在推进。** 现改为 `未注册`。 |

**构建产物已更新**：`npm run lint`（`tsc --noEmit`）通过；`npm run build` 通过。

| 产物 | 字节 | SHA-256（前 16） |
|---|---|---|
| `dist/assets/index-wL9k8dB2.js` | 878,256 | `94016b63ae755455` |
| `dist/assets/index-DvKzHyw2.css` | 121,872 | `c70b85defe577b6e` |
| `dist/system-flow.html` | 46,208 | `9cf3f3f03c150151` |

源码侧（`responses/build_and_source_hashes.json` 全量记录）：

| 源文件 | 字节 | SHA-256（前 16） |
|---|---|---|
| `public/system-flow.html` | 46,208 | `9cf3f3f03c150151` |
| `src/App.tsx` | 20,913 | `797c9e30b9c814e9` |
| `src/components/BacktestWorkbench.tsx` | 156,019 | `211240fe383a108b` |
| `src/components/backtest/MetricStrip.tsx` | 7,640 | `a1d07c8c321ae3b1` |
| `src/components/PositionPanel.tsx` | 60,828 | `de6484fc1223835b` |
| `src/api/useLiveTrading.ts`（未改） | 11,848 | `268a329674432cea` |
| `src/api/liveTrading.ts`（未改） | 22,785 | `c084dfb9cd3399c7` |

机械核对（`probes/build_manifest.py`，脚本算哈希、禁止手抄）：
- `public/system-flow.html` 与 `dist/system-flow.html` **逐字节一致**（同为 `9cf3f3f0…`，46,208 B）→ 通过
- 构建产物 mtime（13:31:15Z）**晚于**全部源码改动（最新 `PositionPanel.tsx` 13:31:01Z）→ 通过

---

## 2. 隔离环境（为什么可以反复切总闸）

工单要求「日常总闸不得为测试反复切换」。所以**全部测试跑在第二个后端上**，生产 8787 一次都没被写：

| 项 | 生产 | 隔离（本工单测试用） |
|---|---|---|
| 后端 | `127.0.0.1:8787` | `127.0.0.1:8799` **PID 55012** |
| 前端 | `:3000` | `127.0.0.1:3100` **PID 55420**（`API_PROXY_TARGET=8799`） |
| `LIVE_DIR` | `框架/backend/live` | `/tmp/ui-link-01/live` |
| `PAPER_LEDGER_DIR` | 生产账本 | `/tmp/ui-link-01/paper`（四臂各 5000，`PAPER_LEDGER_ALLOW_INIT=1` 新建） |
| `PAPER_GATE_FILE` | `框架/backend/live/paper_new_entry_gate.json` | `/tmp/ui-link-01/live/paper_new_entry_gate.json` |
| 行情库 | `数据/市场数据/db/market_v2.sqlite3`（**只读**） | 同一份（只读） |

隔离边界是**机械可验**的：画布实时快照里直接打印 `总闸文件` 的绝对路径——隔离环境显示 `/private/tmp/ui-link-01/…`，生产显示 `/Users/boos/Desktop/…`。这一行不同，就是"没有碰生产"最直接的证据。

---

## 3. 任务逐条结论

### 3.1 任务 1：工作台与画布读同一组真实接口

**画布**（`system-flow.html`）20 张卡片，每张的判定口径写在 `rule` 里、结果由 `judge()` 用业务字段算出。四态：

- `实测通过` 绿 —— 字段齐备且自洽
- `实测失败` 红 —— HTTP 200 但业务字段缺失/异常
- `未验证` 灰 —— 无样本 / 无法判定（**0 条数据既不算失败也不算通过**）
- `端点不存在 / 契约缺陷` 琥珀 —— 404 或 500

同一份页面跑两个后端，状态会变，说明判定函数真的在判、不是摆设：

| 后端 | 通过 | 失败 | 未验证 | 契约缺陷 |
|---|---|---|---|---|
| 隔离 8799（无运行时进程） | 13 | 1 | 5 | 1 |
| 生产 8787（运行时在跑，**只读**） | 14 | 1 | 4 | 1 |

隔离环境那次的 5 项未验证是**如实的**：`运行臂`（`not_started`，进程未注册）、`扫描状态`（`signal_engine.status=unknown`）、`总闸`（进程侧无 `new_entry_gate`，双来源无法交叉）、`挂单`（0 笔，无样本）、`数据覆盖率`（`coverage_ratio=null`）。1 项失败是 `data_feeds`：17 路采集里 4 路异常（`kline_5m`/`kline_15m`/`kline_30m` 报错告警、`funding` 停更）——这是真实的数据管道状态，不是页面判错。

翻转的正是该翻转的：`运行臂`（`not_started` → `running` PID 50390）、`扫描状态`（`unknown` → `active` universe 526/526）、`总闸`（进程侧未注册 → 双来源一致）、`退出`（0 笔 → 账本已平仓 3 笔）、`持仓`（0 条 → 1 条，见 3.3 第二轮）。

**工作台侧 8 项核心字段同源**（`/api/v1/paper/status.item`），生产实测：

| 显示项 | 字段 | 值 |
|---|---|---|
| 运行臂 | `runtime_identity.arm` / `symbol` / `pid` | `3a` / `all(526)` / `50390` |
| 监控池 | `signal_engine.universe` / `universe_declared` | 526 / 526 |
| 扫描状态 | `signal_engine.status` / `state_counter` / `last_cycle_ms` | `active` / BOX 358·IDLE 152·SWEEP 4·RETEST 12 / 20s 前 |
| 总闸 | `order_processing.new_entry_gate.allow_new_entries` | `true`（`user_ui_resumed`） |
| 挂单 | `order_processing.current_pending_in_db` + `paper/accounts.quality.n_pending_orders` | 0 / 0 |
| 持仓 | `execution_daemon.open_positions` + `paper/positions.items` | 0 / 0 |
| 止损 | `execution_daemon.trailing_updates_total` + `paper/stops/{id}` | 0 |
| 退出 | `execution_daemon.exits_triggered_total` + `items[].n_closed` | 0 / 3 笔 |

**同一时刻数值对账**（`probes/same_source_crosscheck.js`，工作台 DOM 与画布 DOM 各取一次；下表为**持仓非空**那一次，`responses/same_source_crosscheck_after_fill.json`）：

| 项 | 工作台 | 画布 | 一致 |
|---|---|---|---|
| 监控池（币安 + 欧易补） | 549 | 549（526 + 23） | 是 |
| 当前信号条数 | 2 | 2 | 是 |
| 待成交挂单笔数 | 0 | 0 | 是 |
| 持仓条数 | **1** | **1** | 是 |

> 另有一次挂单非空时刻的对账（`responses/same_source_crosscheck.json`，11:49Z）：549=549、信号 1=1、挂单 1=1、持仓 0=0，同样 4/4 全等。
> 两次都覆盖到了「非零样本」，不是拿 0 对 0 凑数。

> **关键口径纠正**：`/api/v1/paper/status` 在**运行时未注册**时，`item` 只剩 `status:"unknown"` 占位（隔离环境实测 659 B），**不是**「接口没有这些字段」。之前把它当成"字段不存在"是误判——字段存在，只是没有进程在写。画布现在区分这两种情况：`unknown` ⇒ 未验证，不是失败也不是通过。

> **同一类错误的第二处（已修）**：工作台的「执行守护」徽标原写 `executionDaemon?.status || (isGateRunning ? 'running' : 'idle')`。账本慢（GAP-06）、守护状态还没取到的那几秒里，徽标就把**总闸的 running 顶了上去**，而同一张卡下面的「阻断原因」写着「执行守护未运行 / 进程离线」——同一屏里两个相反结论。接口本身返回的是 `execution_daemon.status="unknown"`、`runtime_identity.status="not_started"`。现已改为「未注册」，并把「推进周期」的 `?? '0'` 改成 `?? '—'`（0 次是一个事实，取不到不是 0）。
> 两个后端的原文对比见 `responses/daemon_status_two_backends.json`：隔离 `unknown / not_started` ⇒ 期望徽标「未注册」；生产 `running / running`（PID 50390，周期 3,485）⇒ 期望徽标「running」。**徽标现在由真实字段驱动，不再由总闸推断。**

### 3.2 任务 2：开始／暂停真改服务端总闸

隔离环境实测（`probes/gate_probe.py`，只写 `/tmp/ui-link-01`，路径不对直接拒绝执行）：

| 步骤 | 结果 |
|---|---|
| 切换前 | 控制文件**不存在** ⇒ fail-closed：`allow_new_entries=false`，`reason=gate_file_missing_or_unreadable` |
| 点「开始」 | 文件被创建：307 B，`sha256=a45f443d52d98f08…`，内容 `{allow_new_entries:true, reason:"user_ui_resumed", set_by:"user_ui_control", scope:"server_side_new_entry_only"}` |
| 点「暂停」 | 文件被改写：`sha256=0433c07a…`，`{allow_new_entries:false, reason:"user_ui_paused", …}` |
| 暂停下真发一次开仓 | **HTTP 409**，`reason=new_entry_blocked_pending_closure_review`，`gate.reason=user_ui_paused`，`ledger_n_pending_after=1`（**挂单数没变**，说明确实没进账本） |
| 账本侧读回 | `paper/accounts.quality.new_entry_gate` 与文件逐字段一致 |
| 进程侧读回 | 隔离环境无运行时 ⇒ 未注册（如实显示"无法交叉核对"，不假装一致） |

**「暂停只阻止新开仓、已有持仓维护继续」**——用 AST 机械枚举全部读闸点（`probes/gate_scope_probe.py`）：

- 后端 4 个文件里读总闸的地方共 **14 处**
- 落在**维护类函数**（`advance_position_trailing` / `advance_arm_trailing` / `close_position` / 退出检查）里的：**0 处**
- 落在新开仓路径 **6 处**，其他（状态展示、身份核对等）**8 处**
- 结论：暂停**不可能**停掉已有持仓的盯市与退出，因为那些代码根本没读闸

> 注：`advance_pending`（挂单推进）**故意**不列入"维护类"——它经由 `open_position` 间接受闸门约束，这是设计（自动 / 手工 / 挂单三路一致拦）。挂单不是「已有持仓」。

### 3.3 任务 3：真实工作台端到端（全链路）

全部用 playwright 打开 `127.0.0.1:3100`，**不注入任何夹具数据**，也不直接调业务接口改账本。

| # | 步骤 | 实测结果 | 证据 |
|---|---|---|---|
| 1 | 信号区 | `sig_uilink01_seed_btcusdt` · BTC · 做多 · `origin=live` · `executableState=enterable` · ATR 取真实 4h bar 振幅 444.9 | `e2e_01_select_open_pending.json` |
| 2 | 点「按选中信号开多」 | 按钮可点（`disabled=false`），服务端登记挂单 | 同上 |
| 3 | 挂单原文 | `#1 BTCUSDT 等待成交 · pending`／`来源: 实时信号触发 · 3a`／`方向: 多`／`等待原因: 等待最早可成交 15m 闭合（服务端时钟推进）`／`最早可执行 15m: 10-23 11:49Z`／`请求锚点时刻: 10-23 11:34Z`／**`数量与成交价待实际成交时确定。`** | 同上 |
| 4 | 未选信号点开仓 | 提示「请先在信号区选择一个信号：开仓需要该信号的 ATR 作为止损基数。」 | `e2e_02_fail_notice_no_signal.png` |
| 5 | 暂停态开仓 | 总闸按钮显示「已暂停」/`aria-pressed=false`；开多按钮 `disabled=true`，title「实盘模拟已暂停（服务端阻断新开仓）…」 | `e2e_03_gate_paused_ui.json` |
| 6 | 成交 | `filled=1` · `position_id=1` · `entry_price=81737.5` · `qty=0.006` · `stop_price=77650.6` · `fill_lag_ms=900000` | `e2e_04_advance_pending.json` |
| 7 | 持仓行 | 开仓价 `$81,737.50`／实时标记 `$81,471.20`／持仓金额 `490.4 U`／未实现 `-$1.60 (-0.33%)`／账本止损价 `$77,650.60`／止损基数 `$444.9000`／止损状态「现价在止损内」／信号身份 `sig_uilink01_seed_btcusdt` | `e2e_05_after_fill_position_stop_trail.json` + 截图 |
| 8 | 止损轨迹面板 | 展开后 **1 条真实记录**：`初始建仓止损 $77,650.60`／`引用4h: 2026-10-23 20:00`／`决策: 20:15`／`生效: 20:00` | 同上 |
| 9 | 止损推进（真实接口） | `ok=true`，`old_stop=77650.6`，`candidate_stop=77432.6`，`effective_stop=77650.6`，**`stop_updated=false`** —— 候选止损比现有更低，引擎按「只上移、不放宽」拒绝，`exit_triggered=false` | `e2e_06_advance_trailing.json` |
| 10 | 界面点「平仓」 | 持仓行消失；账户变为「0 仓在持 / 1 笔已平」，手续费 `$0.49`，已实现 `-$1.09` | `e2e_07_close_from_ui.json` + 截图 |
| 11 | 账本复算 | 见下方「平仓后账本逐笔」 | `e2e_08_after_close_snapshot.json` |

**平仓后账本逐笔（可直接复算）**

```
paper_order  #3  buy  0.006 @ 81737.5   fee 0.2452125
paper_order  #4  sell 0.006 @ 81677.9   fee 0.2449112   exit_reason=manual_ui
paper_realized  exit_price=81637.06105  gross_pnl=-0.6026337
                entry_fee=0.2452125  exit_fee=0.2449112  net_pnl=-1.0927574
paper_state     cash=4998.907243  realized_pnl=-1.0927574  fees_paid=0.4901237
校验：5000 − 1.0927574 = 4998.9072426 ✓   0.2452125 + 0.2449112 = 0.4901237 ✓
```

界面「总资金 $4,998.91 / 手续费 $0.49 / 已实现 -$1.09」与账本逐位一致。

**第二轮（ETH，在代码最终版上重跑一遍全链路）** —— 因为第一轮之后又改了两个前端缺陷，所以在最终构建上再走一遍，避免「截图来自旧代码」：

| # | 步骤 | 实测结果 |
|---|---|---|
| 12 | 铺第二条信号并选中 | `sig_uilink01_seed_ethusdt` · ETH · 做多 · ATR 22.55（真实 4h bar 振幅） |
| 13 | 开仓 → 挂单 | `#2 ETHUSDT 等待成交 · pending`／`来源: 实时信号触发 · 3a`／`方向: 多`／`最早可执行 15m: 10-23 12:48Z`／`请求锚点: 12:33Z`／**`数量与成交价待实际成交时确定。`** |
| 14 | 成交 | `filled=1` · `position_id=2` · 数量 `0.207` · 成交价 `2403.34`（bar 开盘 `2402.13` + 3bps 滑点）· 止损 `2283.17` |
| 15 | 持仓行 | 开仓 `$2,403.34`／标记 `$2,397.95`／持仓 `497.5 U`／未实现 `-$1.12 (-0.22%)`／止损 `$2,283.17`／ATR `$22.5500`／止损状态「现价在止损内」 |
| 16 | 止损轨迹面板 | **1 条真实记录**：`初始建仓止损 $2,283.17`／`引用4h: 21:00`／`决策: 21:15`／`生效: 21:00` |
| 17 | 界面点「平仓」 | 持仓行消失；账户「0 仓在持 / 2 笔已平」，手续费 `$0.99`，已实现 `-$2.95` |

```
paper_order  #5  buy  0.207 @ 2403.34   fee 0.2487457
paper_order  #6  sell 0.207 @ 2397.95   fill 2396.751025  fee 0.2480637   exit_reason=manual_ui
paper_realized  gross_pnl=-1.3639178   net_pnl=-1.8607272
paper_state     累计 realized_pnl=-2.9534846  fees_paid=0.9869331  cash=4997.0465154
校验：-1.0927574 + (-1.8607272) = -2.9534846 ✓
      0.2452125+0.2449112+0.2487457+0.2480637 = 0.9869331 ✓
      5000 − 2.9534846 = 4997.0465154 ✓（界面 $4,997.05）
```

> **一个踩过的坑（写下来避免下次误判）**：平仓那一刻页面正在重拉账本，持仓行会先被清空再回填。探针原先只判「持仓行为 0」，于是**点击后 4 毫秒**就判定"已落账"并截图，抓到的其实是平仓前的旧帧。现改为「持仓行为 0 **且** 账户摘要写成『0 仓在持』」双条件，并单独补拍了一张平仓后状态图（`e2e_12_after_close_state.png`）。账本侧 `closed=2 / positions=0` 是硬的。

**成交时刻的说明（必须如实写清）**：真实系统里挂单由常驻 worker 的**进程时钟**推进（`advance_pending()`，成交价 = 最早可成交 15m 闭合 bar 的开盘价，错过则拒单不回填）。隔离环境没有常驻 worker，本工单用**同一个引擎函数** `advance_pending(T=…)` 推进，把「等真实挂钟」压缩成一次显式时钟注入；函数本身、成交价规则、拒单逻辑一行未改。

> 因此：**本段证明的是「引擎能按规则成交并落账」，不是「真实挂钟到点自动成交」**。后者需要常驻 worker，本工单未验证。

**另外验证到的一条正确拒绝**：同一信号再点一次开多，服务端按 `event_uid` 去重返回 **HTTP 409**，账本不新增挂单（`e2e_09_reopen_pending.json`）。这是防重复下单，不是缺陷。

### 3.4 任务 4：挂单方向与来源臂

- `PositionPanel.tsx` 的挂单行带 `data-arm` / `data-origin` / `data-status` / `data-direction`，验收脚本可直接读取，不靠肉眼
- 界面原文：「来源: 实时信号触发 · 3a」「方向: 多」「数量与成交价待实际成交时确定。」
- 数量与成交价**在成交前一律不显示具体数字**，因为成交价取决于尚未闭合的 15m bar
- 最新一次实测（ETH 那条）：`#2 ETHUSDT 等待成交 · pending`／`最早可执行 15m: 10-23 12:48Z`／`请求锚点时刻: 10-23 12:33Z`（`e2e_09_eth_pending.json`）

### 3.5 任务 5：回测详情与对账表同一 run_id

`probes/backtest_run_binding.py` 机械核对 7 个 run 级端点：

```
selected_run_id = run_WX_EVENT_TREND_V0_1_BTCUSDT_1792350107
detail   200    7,115 B  run_id 一致
metrics  200    3,840 B  run_id 一致
equity   200 3,979,770 B run_id 一致（17,376 个权益点）
trades   200      982 B  run_id 一致
orders   200      502 B  run_id 一致
rejected 200      421 B  run_id 一致
quality  200    5,818 B  run_id 一致
```

界面侧取两条真实 run，分别覆盖「缺字段」与「有数据」两种情形（`probes/backtest_ui_probe.js`，深链 `?nav=backtest&run=<rid>`）：

**① `run_WX_EVENT_TREND_V0_1_BTCUSDT_1792350107`（0 笔成交）** —— 验证「缺就是缺，不伪填」

页面「结果身份」渲染出 `run_id` 与接口一致，窗口 `2024-01-01 → 2024-06-30`，初始资金（生效）`$2,000.00`。

| 指标 | 页面显示 | 依据 |
|---|---|---|
| 净收益 | **无成交** | `trades_count=0` ⇒ 拒绝显示 0.00% |
| 最大回撤 | `-0.00%`（含未平仓权益） | `max_drawdown_pct=0` |
| 夏普比率 | — 后端未返回该指标 | `sharpe_annualized_sqrt365=null` |
| 收益因子 | — 无盈利样本，无定义 | `profit_factor=null`、`wins=0` |
| 胜率 / 盈亏比 | 无成交 / — 无盈利样本，无定义 | `win_rate_pct=null` |
| 总费用 | — 无费用记录 | `total_fees=null` |
| 逐笔成交 | 本 run 无候选信号（4h 箱体未发生有效突破）⇒ 无逐笔成交。**这是策略的真实产出，不是记录缺失** | — |

**② `run_WX_EVENT_TREND_V0_1_AXSUSDT_1792274680`（1 笔成交）** —— 验证「有数据就显示真数」

| 指标 | 页面显示 |
|---|---|
| 净收益 | `+0.58%`（账户成本后收益） |
| 最大回撤 | `-0.56%` |
| 夏普比率 | `0.81` |
| 成交笔数 | `1`（权益点 35,040） |
| 胜率 | `100.0%`（盈 1 · 亏 0） |
| 收益因子 / 盈亏比 | — **无亏损样本（分母为 0），无定义** |
| 总费用 | `-0.34`（手续费 + 资金费） |

> 最后一行就是本工单修掉的第二个前端缺陷：改之前这里写的是「无盈利样本，无定义」，与上一行的「盈 1 · 亏 0」直接打架。

**「口径与审计」视图（对账表所在处）**：修掉哈希对象渲染崩溃后，两条 run 都能正常打开，页面无任何 `pageerror` / `console error`，质量报告显示「质量结论：通过（运行成功 + 质量 ok + 有质量明细）」「对账 ok = true」「指标差异：无」。

> **必须如实说明的边界**：这两条 run 的 `data_quality` 里**只有 `reconcile_ok` 布尔值，没有逐项 `reconcile` 明细对象**（实测扫描 40 条 succeeded run，带 `reconcile` 明细的为 0 条）。所以「逐项现金恒等式对账表」在当前数据里**不存在，无法验证**；本工单能验证到的是：**质量报告（含 `reconcile_ok`）与回测详情绑定同一个 `run_id`**，且该 `run_id` 与 7 个端点逐个对齐。

### 3.6 任务 6：画布显示实测状态 + 工作台入口

- 写死的绿点全部删除，改为 `data-state` 驱动四态
- 三条已写成结论的文案（`paper_orders_blocked` / `paper_stops` / `sig_panel`）改成「判定口径 + 本次实测证据」
- 每张卡片底部有「打开工作台 →」深链；4 个深链实测全部落地且标题与导航高亮同步（`probes/nav_deeplink_probe.js` → `all_pass: true`，逐张截图 `screenshots/nav_deeplink_*.png`）

| 深链 | 落地 testid | 顶栏标题 | 导航高亮 |
|---|---|---|---|
| `?nav=workbench` | `paper-panel` + `signal-panel` | 交易工作台 | 交易工作台 |
| `?nav=datacenter` | `datacenter-view` | 数据中心 | 数据中心 |
| `?nav=strategy` | `strategy-list-view` | 策略管理 | 策略管理 |
| `?nav=backtest` | `lab-workbench` | 回测复盘 | 回测复盘 |

**画布上被纠正的错误结论**（原先写死、实测不成立）：

| 原画布写的 | 实测 |
|---|---|
| 「OKX 官方无 8h 原生，返回 `is_native=false`」 | 补充集合内的标的（如 `AEON-USDT-SWAP`）8h 返回 **`is_native=true` 且 3 根数据**；`BTC-USDT-SWAP` 的真实回答是 `quality=out_of_scope` |
| 「接口映射：814 标的覆盖度」 | `/data/feeds` 是 **17 路采集源**；标的覆盖在 `/data/coverage`（13,155 个已入库组合，`coverage_ratio=null`） |
| 「48 段停摆历史核对」 | `/data/whitelist` 返回 `delisted_symbols` 3 个、`native_skip_symbols` 1 个，没有"48 段"这个数 |
| 「8 周期冷热分层与存储 / 4GB 冷档 / 1646 分片」 | `metrics_summary`：K线 115,166,716 行 / 4,385 组合、OI 5,659,094 条、资金费 2,079,332 次结算；分片 12,551（K线）/ 1,646（OI）/ 525（资金费） |

---

## 4. 接口缺口（提交给后端，本工单**未改后端**）

> 本节所有数字由 `probes/gap_reverify.py` 机械复算，输出见 `responses/gap_reverify_8799.json`（四项判定全为 `true`）。

### GAP-01 `GET /api/v1/paper/stops/{pos_id}` 非法入参返回 500（应 400/404）

```
/api/v1/paper/stops/notanint?arm=3a  →  HTTP 500（502 B）   ← 应为 400
/api/v1/paper/stops/999?arm=3a       →  HTTP 404（434 B）   ← 正确
/api/v1/paper/stops/1?arm=3a         →  HTTP 200（733 B）   ← 正确
```

`adapter.py:3284` 用 `len(seg)==3` 匹配路由后把 `pos_id` 传进 `int()`；非整数抛 `ValueError`，被外层 `except Exception` 兜成 500。
**影响**：前端 `PositionPanel.toggleStopHistory` 直接 `fetch` 该地址，任何一次 id 变形都会得到 500 —— 在 UI 上 500 等于"服务端坏了"，而不是"这个持仓没有止损记录"，用户会被误导去重启服务。
**建议**：入参非整数返回 400；持仓不存在返回 404；两者都带 `missing_reason`。

### GAP-02 行情端点交易所路径**大小写敏感**，写错不报错、静默变"没数据"

```
/markets/binance/BTCUSDT/candles?interval=4h  →  200  bars=3  quality=ok
/markets/BINANCE/BTCUSDT/candles?interval=4h  →  200  bars=0  quality=missing  missing_reason="无已入库K线"
```

生产 8787 与隔离 8799 表现**完全一致**，可复现。
**影响**：任何按大写交易所拼路径的调用方都会得到"这个标的没数据"的结论，而不是"路径写错了"。这正是「HTTP 200 被当业务结论」的典型入口。
**建议**：交易所段统一大小写归一（或非法值返回 400）。

### GAP-03 `GET /api/v1/markets` 的 `limit` 参数不生效

传 `limit=3` 仍返回全部 **549 行、约 6.07 MB**（实测 6,066,087 B）。前端 `TokenTable` 每 5 秒轮询一次全量 ⇒ 约 1.1 MB/s 的常驻流量。
**建议**：要么让 `limit` 生效，要么明确文档化为"始终全量"，让前端改成低频轮询 + 本地增量。

### GAP-04 `/api/v1/strategies` 的 `quality.note` 与 `current` 数组口径打架

- `quality.note`：「当前可运行策略**仅 WX_OI_RESONANCE_V1**（`wx_oi_resonance_v1`）」
- `current`：只有一个元素 `wx_event_trend_v0_1`，`runnable=true` 但 `formal_run_gate.formal_run_allowed=false`、G04/G05 `pending`

**影响**：照 `note` 渲染会把策略名说错；照 `current` 渲染又会把"能跑回测"说成"可运行放行"。画布现在两者都显示并显式标出矛盾。

### GAP-05 同一 run 的 `trades_count` / `net_return_pct`：列表与详情不一致

```
GET /api/v1/backtests?limit=5          →  trades_count=0    net_return_pct=0.0
GET /api/v1/backtests/<同一 run_id>     →  trades_count=null net_return_pct=null
```

**前端未被误导**（指标条按 `trades_count===0` 显示「无成交」），但接口层不一致仍在：任何直接拿列表字段渲染的地方都会把「无数据」显示成「0 收益」。

### GAP-06（本工单新发现，**导致右栏账本首屏要等 20 秒以上**）

**现象**：打开工作台后右栏账本长时间空白，`data-connected="0"`，但接口最终都能返回 200。

**机械证据**（`probes/market_index_probe.py` → `responses/gap06_market_index.json`）：

```
行情库  数据/市场数据/db/market_v2.sqlite3   10,753,265,664 B（约 10.0 GB）
candle_versions 总行数      8,428,561
其中 15m 行数               2,335,426
BTCUSDT 15m 行数                5,212
candle_versions 上的索引    只有 sqlite_autoindex_candle_versions_1（主键）
执行计划：  SCAN candle_versions
            USE TEMP B-TREE FOR ORDER BY
单次查询耗时（同一句、连跑三次）  5,448 ms / 8,591 ms / 8,209 ms
```

**根因**：`paper_engine.last_closed_15m()` 的 SQL 是
`WHERE symbol=? AND timeframe='15m' ORDER BY close_time DESC LIMIT 1`，
没有 `(symbol, timeframe, close_time)` 可用索引 ⇒ 每次**全表扫描 842 万行**并建临时 B 树排序。
而 `snapshot_all()` 对 **4 个臂、每个持仓**各调一次 ⇒ 一次 `/api/v1/paper/positions` 就要跑好几次全表扫描。

**浏览器侧实测**（`probes/ledger_latency_probe.js` → `responses/gap06_ledger_latency.json`）：

```
/paper/accounts   发出 1,183 ms → 完成 24,276 ms   HTTP 200  29,827 B   耗时 23,093 ms
右栏就绪（持仓行出现）时刻：24,438 ms
```

**结论**：不是前端卡死，也不是请求丢了（`requestfailed` 为空、`console` 无错），是后端慢。
**影响**：右栏账本首屏 20–25 秒；`useLiveTrading` 的 `pull()` 是互斥串行，`fetchPaperLedger` 慢会把它后面的 `fetchLiveAccounts` / `fetchPaperDaemonStatus` 一起排队。
**建议（本工单未改）**：给 `candle_versions` 建 `(symbol, timeframe, close_time)` 索引，或给 `last_closed_15m` 结果加短 TTL 缓存。**这属于行情库与引擎读取路径，不在本工单「不改策略/撮合/账本」范围，故只登记不施工。**

---

## 5. 未验证 / 不做的事

- **真实挂钟自动成交**：需要常驻 worker，隔离环境未起；本工单用引擎函数注入时钟验证了规则，但**不等于**「到点自动成交」已验证。
- **生产环境写操作**：一次都没做。生产只跑过只读探测（GET）与只读画布复验。
- **策略盈利有效性**：未验证，不在本工单范围。
- **移动端布局**：画布只验证了 1500/1680 宽度；窄屏用 CSS 断点降级，未实测。
- **逐项对账明细表**：`data_quality.reconcile` 在当前 40 条 succeeded run 里全为空（只有 `reconcile_ok` 布尔值），页面上的「现金恒等式对账」区块因此不渲染 ⇒ **未验证**，不是通过。
- **止损「上移」记录**：两条持仓的价格都离 2R 触发点很远，引擎按规则拒绝上移（`stop_updated=false`，候选止损 `77432.6` 低于现有 `77650.6`），所以轨迹面板每次都只有 1 条「初始建仓止损」。**这是真实结果，不是面板坏了**——面板读的是服务端 `/paper/stops/{id}`，接口返回 `total=1`，界面就显示 1 条。**「止损上移后轨迹多一条」这个场景未验证。**
- **GAP-06 未修复**：见第 4 节，只登记。
- **`/api/v1/paper/status` 在运行时未启动时返回 `unknown` 占位**：这是现状，不是缺陷，但意味着"接口 200 + item 有字段"不等于"系统在跑"。

---

## 6. 复现步骤

```bash
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node

# 1) 起隔离后端（PID 与端口会变，以输出为准）
$PY evidence/UI-LINK-01/probes/iso_backend.py --fresh

# 2) 在隔离 LIVE_DIR 建表 + 插一条真实 ATR 的可执行信号（可加 --uid/--symbol 铺第二条）
$PY evidence/UI-LINK-01/probes/seed_iso_signal.py
$PY evidence/UI-LINK-01/probes/seed_iso_signal.py --uid sig_uilink01_seed_ethusdt --symbol ETHUSDT

# 3) 起隔离前端（代理到 8799）
API_PROXY_TARGET=http://127.0.0.1:8799 VITE_CACHE_DIR=/tmp/ui-link-01/vite-cache \
  $NODE node_modules/vite/bin/vite.js --port=3100 --host=127.0.0.1

# 4) 画布实测（隔离 / 生产只读）
$NODE evidence/UI-LINK-01/probes/canvas_probe.js http://127.0.0.1:3100/system-flow.html out.png "" out.json
$NODE evidence/UI-LINK-01/probes/canvas_probe.js http://127.0.0.1:3100/system-flow.html out.png http://127.0.0.1:8787 out.json

# 5) 总闸切换 + 暂停下真发一次开仓 + 读闸范围枚举
$PY evidence/UI-LINK-01/probes/gate_probe.py start
$PY evidence/UI-LINK-01/probes/gate_probe.py pause
$PY evidence/UI-LINK-01/probes/gate_probe.py try-open
$PY evidence/UI-LINK-01/probes/gate_scope_probe.py

# 6) 工作台端到端（真实浏览器点；账本慢，给足等待）
E2E_SIGNAL_UID=sig_uilink01_seed_btcusdt E2E_LEDGER_TIMEOUT=180000 \
  $NODE evidence/UI-LINK-01/probes/e2e_ui.js select_open shot.png out.json
$PY evidence/UI-LINK-01/probes/e2e_driver.py advance-pending
$PY evidence/UI-LINK-01/probes/e2e_driver.py advance-trailing 3a 1
$NODE evidence/UI-LINK-01/probes/e2e_ui.js after_fill shot.png out.json
$NODE evidence/UI-LINK-01/probes/e2e_ui.js close shot.png out.json

# 7) 同源对账 / 深链 / 回测 run_id / 构建哈希 / 慢路径诊断
$NODE evidence/UI-LINK-01/probes/same_source_crosscheck.js
$NODE evidence/UI-LINK-01/probes/nav_deeplink_probe.js
$PY evidence/UI-LINK-01/probes/backtest_run_binding.py
$NODE evidence/UI-LINK-01/probes/backtest_ui_probe.js <run_id> shot.png out.json
$PY evidence/UI-LINK-01/probes/build_manifest.py
$PY evidence/UI-LINK-01/probes/market_index_probe.py
$NODE evidence/UI-LINK-01/probes/ledger_latency_probe.js http://127.0.0.1:3100 150000 out.json
```

> 三个本机坑：
> ① `HTTP_PROXY` 有但无 `no_proxy`，所有脚本一律 `urllib.request.build_opener(ProxyHandler({}))` 或 `route.fetch`，否则 `127.0.0.1` 会被发给代理。
> ② 账本首屏实测 20–25 秒（GAP-06）。`e2e_ui.js` 早期版本只等 5 秒就判定"无持仓"，是**探针等待过短造成的假阴性**，已改为等 `data-connected=1` 或持仓行出现（默认 180 秒）。`same_source_crosscheck.js` 有同样的问题（原来只等 6 秒），已一并改成等账本就绪。
> ③ 平仓后页面会先清空持仓行再回填。只判「持仓行为 0」会在**点击后几毫秒**就成立，截到的是平仓前的旧帧。必须同时判「账户摘要写成 0 仓在持」。

---

## 7. 证据文件

```
evidence/UI-LINK-01/
├─ README.md                                      ← 本文件
├─ probes/
│  ├─ iso_backend.py                              起隔离后端（8799）
│  ├─ seed_iso_signal.py                          隔离 LIVE_DIR 建表 + 插可执行信号（支持 --uid/--symbol）
│  ├─ canvas_probe.js                             画布实测探针（可指定后端覆盖）
│  ├─ gate_probe.py                               总闸 show/start/pause/try-open
│  ├─ gate_scope_probe.py                         AST 枚举读闸点（14 处 / 维护类 0 处）
│  ├─ e2e_ui.js                                   真实浏览器操作（选信号/开多/持仓/止损轨迹/平仓/切闸）
│  ├─ e2e_driver.py                               隔离账本快照 / 挂单推进 / 止损推进 / 平仓
│  ├─ same_source_crosscheck.js                   工作台 vs 画布 同源数值对账
│  ├─ nav_deeplink_probe.js                       4 个 ?nav= 深链落地核对
│  ├─ backtest_run_binding.py                     7 个 run 级端点 run_id 核对
│  ├─ backtest_ui_probe.js                        结果身份 / 指标条 / 口径与审计视图取证
│  ├─ build_manifest.py                           源码/产物 SHA-256 与构建时序核对
│  ├─ gap_reverify.py                             GAP-01/02/03 逐条复现
│  ├─ market_index_probe.py                       GAP-06 行情库索引与执行计划证据
│  └─ ledger_latency_probe.js                     GAP-06 浏览器侧账本耗时实测
├─ screenshots/
│  ├─ canvas_isolated_8799.png                    画布·隔离（12/1/6/1，无持仓时刻）
│  ├─ canvas_isolated_pending.png                 画布·隔离（挂单非空，13/1/5/1）
│  ├─ canvas_isolated_position.png                画布·隔离（持仓非空，13/1/5/1，持仓卡通过）
│  ├─ canvas_production_8787_readonly.png         画布·生产只读（14/1/4/1）
│  ├─ nav_deeplink_workbench.png                  深链 ?nav=workbench 落地（paper-panel + signal-panel）
│  ├─ nav_deeplink_datacenter.png                 深链 ?nav=datacenter 落地
│  ├─ nav_deeplink_strategy.png                   深链 ?nav=strategy 落地
│  ├─ nav_deeplink_backtest.png                   深链 ?nav=backtest 落地
│  ├─ e2e_01_select_open_pending.png              选信号 + 开多 + 挂单（BTC）
│  ├─ e2e_02_fail_notice_no_signal.png            未选信号的失败提示
│  ├─ e2e_03_gate_paused_ui.png                   暂停态：按钮禁用 + 提示
│  ├─ e2e_05_after_fill_position_stop_trail.png   持仓行 + 止损轨迹面板（BTC）
│  ├─ e2e_07_close_from_ui.png                    界面点平仓后：0 仓在持 / 1 笔已平
│  ├─ e2e_09_eth_pending.png                      第二条挂单（方向/来源臂/待确定）
│  ├─ e2e_09_reopen_pending.png                   同一信号重复开仓被拒（HTTP 409）
│  ├─ e2e_11_eth_position_stop_trail.png          持仓行 + 止损轨迹面板（ETH，最终版代码）
│  ├─ e2e_12_after_close_state.png                平仓后状态：0 仓在持 / 2 笔已平
│  ├─ backtest_run_identity.png                   结果身份 + 指标条（0 成交 run，缺字段显示「—」）
│  ├─ backtest_run_identity_audit.png             口径与审计视图（该 run 无逐项对账明细）
│  ├─ backtest_run_identity_with_trades.png       结果身份 + 指标条（1 笔成交 run，显示真数）
│  ├─ backtest_run_identity_with_trades_audit.png 口径与审计视图（对账 ok=true，页面不再崩）
│  └─ gap06_ledger_latency.png                    右栏账本慢路径实测
└─ responses/
   ├─ canvas_isolated_8799.json                   20 张卡片逐条状态与证据（隔离·无持仓）
   ├─ canvas_isolated_8799_pending.json           同上（挂单非空时刻）
   ├─ canvas_isolated_8799_position.json          同上（持仓非空时刻）
   ├─ canvas_production_8787_readonly.json        同上（生产只读）
   ├─ same_source_crosscheck.json                 工作台 vs 画布 4 项对账（挂单非空时刻）
   ├─ same_source_crosscheck_after_fill.json      同上（持仓非空时刻）
   ├─ nav_deeplink.json                           4 个深链落地结果
   ├─ gate_start.json / gate_pause.json           总闸文件写入前后的字节与哈希
   ├─ gate_paused_try_open_rejected.json          暂停下开仓 → HTTP 409
   ├─ gate_resume_after_paused_ui.json            恢复后总闸文件内容
   ├─ gate_scope_ast.json                         14 处读闸点逐条
   ├─ e2e_01_select_open_pending.json             挂单行原始字段（BTC）
   ├─ e2e_03_gate_paused_ui.json                  暂停态 DOM
   ├─ e2e_04_advance_pending.json                 BTC 成交结果（价格/数量/止损/滞后）
   ├─ e2e_05_after_fill_position_stop_trail.json  BTC 持仓行 + 止损轨迹面板原文
   ├─ e2e_06_advance_trailing.json                止损推进：候选 vs 生效（拒绝放宽）
   ├─ e2e_07_close_from_ui.json                   BTC 界面平仓后的账户与持仓
   ├─ e2e_08_after_close_snapshot.json            BTC 平仓后四臂快照
   ├─ e2e_09_eth_pending.json                     ETH 挂单原文
   ├─ e2e_09_reopen_pending.json                  同一信号重复开仓 → HTTP 409
   ├─ e2e_10_eth_advance_pending.json             ETH 成交结果
   ├─ e2e_11_eth_position_stop_trail.json         ETH 持仓行 + 止损轨迹面板原文
   ├─ e2e_12_close_click.json                     界面点平仓的点击记录（DOM 帧为刷新中途，见 3.3 说明）
   ├─ e2e_12_after_close_state.json               平仓后状态（0 仓在持 / 2 笔已平）
   ├─ e2e_13_final_snapshot.json                  最终四臂快照（closed=2 / positions=0）
   ├─ backtest_run_binding_8799.json              7 端点 run_id 核对
   ├─ backtest_ui_identity.json                   页面渲染的 run_id 与指标原文（0 成交 run）
   ├─ backtest_ui_identity_with_trades.json       同上（1 笔成交 run）
   ├─ gap06_market_index.json                     GAP-06：行数/索引/执行计划/耗时
   ├─ gap06_ledger_latency.json                   GAP-06：浏览器侧请求耗时与就绪时刻
   ├─ gap_reverify_8799.json                      GAP-01/02/03 逐条复现结果
   ├─ daemon_status_two_backends.json             执行守护状态：隔离 unknown vs 生产 running
   └─ build_and_source_hashes.json                源码与构建产物 SHA-256 + 构建时序
```
