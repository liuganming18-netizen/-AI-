# 代码审查自动门禁报告

- 生成时间：2026-10-21T04:01:17.848824+00:00
- 标准：WX-CODE-REVIEW v1.0
- 结论：**P0 1 项 / P1 5 项 / P2 18 项**

## 运行留底统计

| 项 | 值 |
|---|---:|
| runs_scanned | 2135 |
| archived_dirs_excluded | 218 |
| with_run_json | 8 |
| without_run_json | 2127 |
| trades_total | 700 |
| zero_trade_runs | 1517 |
| duplicate_created_at_values | 1 |

## P0 · 阻断（必须修完才能合并）

| 规则 | 位置 | 原文 | 原因 |
|---|---|---|---|
| REG-01 | `2127 个目录` | `示例：['回测日志/event_trend_v0_1/runs_sep_all_latest/run_WX_EVENT_TREND_V0_1_IDUSDT_1792262193', '回测日志/event_trend_v0_1/runs_s` | 有回测产物但缺 run.json ⇒ 该运行不算已记录；读接口会用默认值合成文档 |

## P1 · 必修

| 规则 | 位置 | 原文 | 原因 |
|---|---|---|---|
| DEL-01 | `框架/backend/realtime_collector.py:1645` | `FORCE_RECONNECT_FILE.unlink()` | 删除文件/目录 ⇒ 需确认不碰账本、evidence、回测日志 |
| DEL-01 | `框架/backend/tools/api_snapshot.py:229` | `shutil.rmtree(t0_dir, ignore_errors=True)` | 删除文件/目录 ⇒ 需确认不碰账本、evidence、回测日志 |
| DEL-01 | `框架/backend/tools/api_snapshot.py:230` | `shutil.rmtree(t1_dir, ignore_errors=True)` | 删除文件/目录 ⇒ 需确认不碰账本、evidence、回测日志 |
| REG-04 | `6 个 run` | `示例：['回测日志/event_trend_v0_1/runs/bt_20261017T195450Z_cccccccc', '回测日志/event_trend_v0_1/runs/bt_20261017T195450Z_dddddddd'` | 缺 manifest.json ⇒ 产物无法逐个校验 sha256 |
| REG-05 | `1 个时间值` | `2026-10-17T19:54:50Z → 5 个 run` | 多个 run 的 created_at 完全相同 ⇒ 疑似读时兜底，不是真实运行时间 |

## P2 · 建议

| 规则 | 位置 | 原文 | 原因 |
|---|---|---|---|
| DEL-01 | `策略/WyckoffMaster/event_trend_v0_1/runner.py:172` | `shutil.rmtree(partial)` | 删除临时文件（原子写/清理）⇒ 确认路径确为临时即可 |
| DEL-01 | `框架/backend/backtest_chart.py:90` | `os.unlink(tmp)` | 删除临时文件（原子写/清理）⇒ 确认路径确为临时即可 |
| DEL-01 | `框架/backend/spawn_detached.py:35` | `os.unlink(pid_path)` | 删除临时文件（原子写/清理）⇒ 确认路径确为临时即可 |
| DEL-01 | `框架/backend/public_snapshot.py:78` | `tmp.unlink()` | 删除临时文件（原子写/清理）⇒ 确认路径确为临时即可 |
| DEL-01 | `框架/backend/smart_money.py:698` | `tmp.unlink()` | 删除临时文件（原子写/清理）⇒ 确认路径确为临时即可 |
| DEL-01 | `框架/backend/tests/test_frame_fit_02.py:84` | `shutil.rmtree(self.tmp, ignore_errors=True)` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_rest_import_contract.py:89` | `self.db.unlink()` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_metrics_contract.py:206` | `shutil.rmtree(self.tmp, ignore_errors=True)` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_okx_volume_mapping.py:101` | `self.db.unlink()` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_okx_volume_fix_isolation.py:96` | `self.db.unlink()` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_okx_volume_fix_rectification.py:115` | `self.db.unlink()` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_okx_volume_fix_rectification.py:286` | `other.unlink()` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_okx_volume_fix_rectification.py:429` | `Path(e2["body_path"]).unlink()                       # 计划后正文丢失` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_research_ledger.py:58` | `p.unlink()` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_research_ledger.py:122` | `p.unlink()` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_collect_schedule.py:75` | `shutil.rmtree(self.tmp, ignore_errors=True)` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_collect_schedule.py:159` | `shutil.rmtree(self.tmp, ignore_errors=True)` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |
| DEL-01 | `框架/backend/tests/test_frame_fit_01.py:67` | `shutil.rmtree(self.tmp, ignore_errors=True)` | 测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可 |

## 需人工判定（门禁不假装检查）

- R6 口径是否同源（分子分母是否同一批样本）
- R8 结论是否超出 n 与置信区间能支撑的范围
- R11 成本是否含手续费/滑点/资金费/价格取整四项
- R12 是否把『程序自检通过』当成『结果正确』
- R5 同一概念是否有多套实现（门禁只报同名函数，语义重复需人工）
