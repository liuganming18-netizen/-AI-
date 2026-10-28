# 改动清单与回滚说明｜SIGNAL-EFFECTIVE-01-WORK

> 生成时间：2026-10-27（北京时间）
> 说明：工作区在本单开始前**已存在**未提交改动（第十九次更新的「多因子共振」一批，含
> `wyckoff_factor_resonance.py` 新文件与前端 6 个文件）。下表只列**本单自己改的**，
> 并单独标注「本单未动但与之相关」。

## 一、代码改动（4 个文件，6 处）

| # | 文件 | 位置（当前行号） | 改动 | 性质 | 回滚 |
|---|---|---|---|---|---|
| 1 | `框架/backend/paper_engine.py` | 1075–1087 | `open_position` 的 epoch 默认值：`L.get_paper_epoch_ms()` → `L.effective_floor_ms()` | **修实现**（代码与 `live_store` 文档「共用同一有效下限」不符，差 9 天 8 小时） | 把该行改回 `L.get_paper_epoch_ms()` |
| 2 | `框架/backend/paper_engine.py` | 1196–1216 | 仓位倍数必须 `payload.position_multiplier_approved is True` 才生效，否则恒 1.0 | **阻断未批准参数进入下单量** | 去掉该判断，恢复直接取值 |
| 3 | `框架/backend/unified_runtime.py` | 2213–2222 | `_scan_actionable` 增加 `COALESCE(json_extract(payload_json,'$.approved'),1)<>0` | **补上已批准规则的缺失实现** | 删掉该 where 项 |
| 4 | `框架/backend/unified_runtime.py` | 2467–2474 | `UnifiedRuntime.__init__` 的 `enable_oi_filter` 默认值恢复为「跟随策略配置」 | **撤销未批准的规则改动** | 恢复 `enable_oi = False if (arm=="3b" or WYCKOFF_RESONANCE=="1")` |
| 5 | `框架/backend/live_api.py` | 344–348 + 415–440 | 当前区加同一条 `approved<>0` 判据；`excluded_breakdown` 增 `unapproved_config` 计数 | **页面与路由同集合** | 删掉该 where 项与计数列 |
| 6 | `框架/backend/live_worker.py` | 356–364 | `available_at` 为空时不再用 `confirmed_at` 填补；`epoch_ms` 由 `paper_epoch` 改为 `floor_ms` | **修实现**（不得伪造可得时刻 / 共用下限） | 恢复 `available_at=int(d.get("available_at") or d["confirmed_at"])` 与 `epoch_ms=epoch` |

编译检查：4 个文件 `py_compile` 全部通过。

## 二、数据改动（生产信号库，289 行）

| 项 | 内容 |
|---|---|
| 目标 | `框架/backend/live/live_signals.sqlite3` → `live_signal` 表 |
| 命中 | 289 行（`source='antigravity_08_backtest'`） |
| 改动字段 | 只改 4 项：`origin` → `historical_research`；`executable_state` → `historical_not_executable`；`available_at` → `NULL`；`payload_json.role` → `research_only`（另追加 `payload_json.identity_repair` 审计留痕） |
| 未动 | `first_seen_at`、`event_time`、`confirmed_at`、`bar_open_time`、`bar_close_time`、`symbol`、`strategy_key/version`、`config_hash`、`executable_reason`、`display_state`、`admission`、`rank`、payload 其余 40 个键 |
| 行数 | 289 → 289（**未删除任何行**） |
| 账本 | 四本 `paper_3a–3d` **零改动**（逐表行数与快照一致） |
| 脚本 | `scripts/04_repair_signal_identity.py`（默认预演，`--apply` 才写） |
| 镜像 | `baseline/repair_before.jsonl`（289 行完整前置镜像） |
| 回滚 | `python scripts/04_repair_signal_identity.py --rollback --apply`（已在隔离副本验证：还原后回到 `recovered/enterable/available_at=confirmed_at`） |
| 幂等 | 第二次运行命中 0 条（隔离副本实测） |

## 三、本单**没有**做的事（避免误读）

- **没有重载生产进程**。8787（pid 68764）/ worker（68775）/ runtime（68802）仍是改动前加载的代码，
  所以上表 6 处代码修复**尚未在运行中生效**；已生效的只有数据修复（289 行）。
- 没有改币池、冻结阈值、风险参数、止损或执行时序。
- 没有新增策略、采集器、服务、数据库或第二套时钟。
- 没有启用真实交易，没有调用任何真实下单接口。
- 没有删历史数据、没有清账、没有强平。
- 没有动第十九次更新引入的共振评分本体（保留计算与展示，只切断它对下单量的影响）。

## 四、待用户/主代理裁决（3 项）

1. **是否重载 8787 让 6 处代码修复生效**（重载会短暂中断页面与路由；当前无挂单、无持仓，风险最低窗口）。
2. **OI 门槛要不要正式关闭**。本单已把它还原为「默认开」；若确要关，属于改策略规则，需要明确批准。
3. **共振评分与仓位倍数（0.5/0.8/1.0/1.3）要不要批准**。现状：照算、照展示、不改变下单量。
