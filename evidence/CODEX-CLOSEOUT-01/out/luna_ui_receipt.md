# Luna UI 收尾核验回执

核验日期：2026-10-24（Asia/Shanghai）

## 本轮改动

- `UI界面/web/src/components/BacktestWorkbench.tsx`：提交成功后立即切为单 run 并选中提交的 run；本页提交状态改为独立读取该 run 详情并每 2 秒轮询，不受旧批次列表/全局 runs 列表影响。
- `框架/backend/ledger_links.py`：逐笔时间解析支持账本原生 `entry_time` / `exit_time` 毫秒字段，并据此唯一匹配真实 `POSITION_CLOSED` 标识。
- `UI界面/web/src/api/liveTrading.ts`：优先把 `paper/status.signal_engine.last_cycle_ms` 映射成推进时间，旧时间字段作为回退。
- `UI界面/web/src/components/StrategyManagementView.tsx`：改用既有 `/paper/accounts` 与 `/paper/execution_daemon/status?arm=3a` 轮询接口，显示服务端真实开仓闸门、FT-LOW4H 版本/规则、止损/跟踪/风险参数、单边手续费与滑点，以及行情/信号引擎状态。接口尚未返回或失败时显示读取中/未知/失败，不再显示“总闸关闭”“滑点 1 bps”或“17 项核心流正常”。
- `框架/backend/live_api.py`：将 `paper/accounts` 质量说明中的固定“新开仓已阻断”改为中性指引，以 `new_entry_gate.allow_new_entries` 为准，不修改闸门逻辑。

## 核验结果

- 对 `回测日志/event_trend_v0_1/runs/bt_20261023T162149Z_59f3c8fa` 只读加载 ledger，得到 `trade_id=trd_edfeb086690a19e3b79b7982`，其来源是 `positions.jsonl` 中 `POSITION_CLOSED.payload.trade_id`，不是合成值或按行号生成。时间为 entry `1790954100000`、exit `1791004500000`，持仓 `14.0` 小时；关联到真实开仓/平仓订单 `ord_af1ce46b242122be4dff48a9` / `ord_eec8efe1cbd2e0b57493fbae`，止损轨迹 2 点。
- 运行时状态快照 `框架/backend/locks/paper_runtime_3a_status.json:44` 的 `signal_engine.last_cycle_ms=1792805306541`（2026-10-24 01:28:26.541 UTC）。前端 API 现将它转成 ISO UTC，供 PositionPanel“最近推进”显示。
- 策略管理页的状态与参数来自两个现有只读接口，每 15 秒刷新；不再通过静态“17 项正常”声称数据流健康。
- UI TypeScript 检查 `tsc --noEmit` 通过；`vite build` 通过，产物含本轮 UI 改动。仅有既存 `__dirname` 配置和 chunk 体积警告。
- 未启动/重启服务，未运行写库测试；浏览器真实页面核验由 root 执行，API 重启后需确认 `/trades` 返回时间、trade_id、持仓时长字段。
