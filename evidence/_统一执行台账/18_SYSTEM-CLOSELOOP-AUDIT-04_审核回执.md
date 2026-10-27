# 18｜SYSTEM-CLOSELOOP-AUDIT-04 审核回执

> 工单：SYSTEM-CLOSELOOP-AUDIT-04｜自动闭环优先复验
> 执行方：Work（独立审核方）
> 被审方：反重力（SYSTEM-CLOSELOOP-04 交付）
> 回执时刻：2026-10-22T23:38+08:00
> 交付目录：`evidence/SYSTEM-CLOSELOOP-AUDIT-04/`
> 主报告：`00_报告.md`；阶段回执：`01_第一关结论.md`；产物清单：`05_文件清单与哈希.csv`（33 个文件，1,917.5 KB）

---

## 一、本轮做到哪一步

按工单「先验证自动闭环、不先重做全量页面报告」执行：**第一关 7 项逐条亲自验证，结论不通过（通过 3 / 不通过 4）**。

- 冻结基准改用**审核方自采** 153 文件 SHA-256（`out/00_开工冻结.json`，23:31:37 采集）；23:34 收工重采，**变动 0**。
- 全部写操作只落 `/tmp/wx_audit04`；生产 8787 与 `数据/paper` 全程只读。
- 因第一关不通过，工单「第一关通过后补齐 UI 10 项」**未执行**，旧状态原样保留。
- 未重跑 144 组网格、未重跑历史回测（工单明确不要求）。

## 二、第一关 7 项结论

| # | 要求 | 结论 | 关键实测 |
|---|---|---|---|
| 1 | 运行入口启动后自动完成 挂单→成交→止损上移→退出 | **不通过** | 挂单成立；成交被拒 `missing_parameter_stoploss_s`；止损上移/退出**未执行** |
| 2 | 行情可追溯；假K线/未来数据/错标的/外部参数覆盖不能改执行状态 | **不通过** | 8 类注入 4 通过 4 不通过：假K线 **85→190.0**、未来K线 **85→190.0**、外部 `trail_d=0.001` **85→96.9** |
| 3 | 初始止损与跟踪距离来自明确配置，两组参数有预期区别，无静默兜底 | **不通过** | `approved_params()` 18 键**不含 `stoploss_s`/`trail_d`**；生效视图文件**不存在**。（两组参数区别成立、缺参数显式失败，这两条通过） |
| 4 | 4h 跟踪与明细周期退出分开；止损生效不追溯 | **通过** | 4h low 高于止损不退出 / 15m low 击穿退出；`effective_at` = 决策时刻 |
| 5 | 订单/成交/持仓/费用/权益闭合；幂等与重启 | **通过**（引擎直调路径） | 残差 **0.0**；重复退出 `position_not_open`；重启后成交 1 条、状态一致 |
| 6 | 止损轨迹正式路由可访问；页面与账本一致；失败如实报错 | **通过** | `GET /paper/stops/{id}` **200**（不存在持仓 404）；页面 `$85.0000 → $92.1000` 与账本逐行一致；500 → `⚠ 模拟接口失败…`，断网 → `⚠ Failed to fetch` |
| 7 | 起止代码/配置/运行版本一致 | **不通过** | 起止 153 文件变动 0（通过）；**8787 启动 23:13:40 < `live_api.py` 23:28:50 ⇒ 线上跑旧代码** |

**第 5、6 项通过 ≠ 自动闭环通过**：第 5 项是引擎函数直调（带持仓夹具），第 6 项是只读页面，两者都不经过「系统自己把单子成交掉」这一步——而这一步正是第 1 项断掉的地方。

## 三、最小复现（照抄可复跑）

```bash
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
cd /Users/boos/Desktop/威科夫因子共振趋势
$PY evidence/SYSTEM-CLOSELOOP-AUDIT-04/queries/01_env.py       # 隔离账本/信号库/闸门/合成行情
$PY evidence/SYSTEM-CLOSELOOP-AUDIT-04/queries/02_autoloop.py  # 起 worker，只观察不推进
```

时间线（`out/02_自动闭环.json`）：23:32:12 信号到达 → `paper_pending(pending_id=1, status=pending)`；23:32:13 起 `workers/paper_execution_worker.py`；23:32:18 插 15m 成交 bar；23:32:21 撮合结果 `paper_order(status=rejected, reject_reason=missing_parameter_stoploss_s)`，`paper_position` **0 行**、`paper_stop_history` **0 行**；之后挂单**仍 pending**（`paper_blocked` 去重让重试风暴在账本里看不见）。

## 四、责任位置（冻结版 SHA，精确到行）

| # | 位置 | 问题 |
|---|---|---|
| R1 | `paper_engine.py:958-982` | 初始止损只认入参或 `params["stoploss_s"]`，都没有就拒单；`paper_pending` **无该列** ⇒ 走挂单队列必然丢 s |
| R2 | `paper_engine.py:213-261` | `approved_params()` 18 键不含 `stoploss_s`/`trail_d`；`_source=fallback_frozen_v2_2_conservative`，生效视图文件不存在 |
| R3 | `paper_loop.py:93`+`:150` | 守护自动路径传 `trail_d=None` ⇒ 每轮 `missing_parameter_trail_d`，**止损永不上移** |
| R4 | `paper_engine.py:1899-1940` | `validate_market_candle` 不校验 K 线是否来自行情库；`now_ms is None` 时未来数据检查整段跳过 |
| R5 | `live_api.py:1030-1031,1051` | `candle_4h` 与 `trail_d` 均来自请求体，且不传 `now_ms` ⇒ 假K线/未来K线/参数覆盖三条同时成立 |
| R6 | `live_api.py:960,978` | `stoploss_s` 也来自请求体 ⇒ 「来自明确配置」实际是「来自调用方」 |
| R7 | `启动系统.command`/`start.sh`/`collector.sh`/`adapter.py` | 三个启动脚本零命中 worker/`paper_loop`；adapter 无 `PaperExecutionDaemon`/`start_background()` ⇒ **统一入口不拉起自动闭环** |
| R8 | `paper_loop.py:29-30` | `LOCK_DIR` 硬编码无环境变量出口 ⇒ 隔离实例与日常实例抢同一把锁 |
| R9 | `paper_loop.py:33,244-257` | `_STATUS` 是进程内字典 ⇒ 跨进程读不到。实测 worker 在跑时接口仍返回 `status=not_started, cycle_count=0` |

## 五、对方交付为什么不能当通过

`SYSTEM-CLOSELOOP-04/test_automated_closeloop.py` 确实跑通了，但它做的是：

- 下单走 `paper_order_post({..., "stoploss_s":0.05, "trail_d":0.05})`（L234-235）——参数来自**请求体**；
- 每个周期都是 `execute_cycle(now_ms=<注入时刻>, trail_d=0.05)`（L250/268/282/296/355/370/392）——**时钟由脚本注入、周期由脚本逐次调用**。

这正好是工单第 1 项禁止的两件事（审核脚本不得手工推进交易、外部参数覆盖不能改变执行状态），只是换成了 daemon 的方法名。

另：对方 `01_自动运行入口配置与进程身份.md` 给出的响应示例 `pid=40532, status=running, cycle_count=1420` 与本轮实测矛盾——40532 是 **adapter 自己的 PID**，同一时刻该接口返回 `{"status":"not_started","running":false,"cycle_count":0}`（`out/03_行情防线.json`）。该示例不可能是这个接口真实产生过的响应。

## 六、回测与模拟对齐（本轮新增第 6 条差异）

AUDIT-03 已登记 5 条：初始止损推导、**退出粒度（最实质）**、取整、「已收盘」差 1ms、`d=0.05` 双边硬编码。
本轮新增：**初始止损 `s` 在回测里是类属性、在模拟里是请求体参数**，来源不同。共 6 条，本轮未重做回测。

## 七、交付可部署性

| 判据 | 结论 |
|---|---|
| 隔离版本能自动闭环 | **不能**（第 1 项断在成交） |
| 隔离版本注入防线可信 | **不能**（假K线/未来K线/参数覆盖均可改状态） |
| 隔离版本账本闭合 | **能**（残差 0.0，幂等/重启通过） |
| 止损轨迹可查且与账本一致 | **能**（200/404、页面逐行一致、失败如实报错） |
| 日常实例 | **未验证**（8787 跑 23:13:40 的旧代码，磁盘已到 23:28:50） |

## 八、阻塞项

| 编号 | 卡点 | 谁能解 |
|---|---|---|
| B-01 | 挂单队列无 `stoploss_s` 列 + `approved_params()` 无该键 ⇒ 自动成交必然拒单 | 反重力 |
| B-02 | 守护自动路径 `trail_d=None` ⇒ 止损永不上移 | 反重力 |
| B-03 | 统一入口不拉起 worker ⇒ 「从运行入口启动」不成立 | 反重力 |
| B-04 | `validate_market_candle` 不校验行情库来源；`now_ms=None` 跳过未来检查 | 反重力 |
| B-05 | `LOCK_DIR` 硬编码 ⇒ 隔离/日常抢锁 | 反重力 |
| B-06 | 8787 需重启才能加载 23:28 代码（会中断日常实例） | **用户** |
| B-07 | 前向验证合格独立区间 = 0（延续 OOS-READY-01） | 需先裁决前向方案 |

## 九、边界

- 未改任何业务代码、未删任何账本记录、未替用户批准清理范围。
- 未选生产参数、未启动前向验证、未改冻结规则 SHA `1f1167de…9a8ab`。
- **隔离通过不写成日常实例通过；工程通过不写成策略有效。** 本回执不评价 FT-LOW4H 赚不赚钱。
- 本轮 53 个关键数字经 `07_selfcheck.py` 回到 `out/*.json` 原始字段机械复算，**53/53 一致**。
- 审核期间被审代码仍在并发改动（23:13:26 首采后 23:14:49~23:28:50 又改 3 个文件，`paper_engine.py` 改两次）⇒ **结论只对 23:31:37 的冻结 SHA 有效**；已按工单作废受影响检查并全部重跑。
