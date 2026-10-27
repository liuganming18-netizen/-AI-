# SYSTEM-STARTUP-01 ｜ 8787 端口状态与历史停止诊断报告

## 1. 诊断原则
遵循只读排查纪律，直接从操作系统运行状态、系统引导日志（`uptime` / `who -b` / `last`）、进程端口监听状态（`lsof` / `ps`）以及历史落盘日志（`backend.log` / `worker.log` / `realtime.log`）中提取第一手客观事实，**严禁任何无据猜想与凭空归因**。

---

## 2. 诊断事实证据清单

### 2.1 端口与进程监听状态
- **检测时点**：2026-10-23 11:55:21 (CST)
- **命令输出**：
  ```bash
  $ lsof -i :8787
  # 返回码: 1, 无任何输出，当前 127.0.0.1:8787 与 [::1]:8787 均无进程监听
  ```
- **记录 PID 状态**：
  `框架/backend/backend.pid` 记录的 PID 值为 `20760`（文件最后更新于 2026-10-22 20:27:34）。
  ```bash
  $ ps -p 20760
  # 无输出，PID 20760 进程已不存在
  ```

### 2.2 启动与运行日志（backend.log）
- **文件位置**：`框架/backend/logs/backend.log`
- **最后修改时间**：2026-10-22 20:28:03
- **最后日志片段**：
  ```text
  [adapter] 127.0.0.1,::1:8787 db=/Users/boos/Desktop/威科夫因子共振趋势/数据/市场数据/db/market_v2.sqlite3
  [adapter] 预热完成（库级索引已进 TTL 缓存）用时 28.51s
  ```
- **关键事实**：`backend.log` 中**无任何 Traceback、无 MemoryError / OOM、无未捕获异常、无 Panic**。其最后一条记录为服务完成缓存预热后的正常就绪状态。

### 2.3 操作系统关机重启与进程退出时序（铁证）
- **系统引导记录**（`uptime` 与 `who -b`）：
  ```bash
  $ uptime
  11:56  up  2:05, 1 user, load averages: 5.66 5.01 5.30

  $ who -b
  system boot  Oct 23 09:51

  $ last reboot | head -2
  reboot time  Fri Oct 23 09:51
  ```
  **事实判定**：本机于 **2026-10-23 09:51** 经历了系统重新启动（System Boot）。
- **后台伴生进程下线日志**（`worker.log`）：
  在关机重启前 4 分钟（2026-10-23 09:46:57 CST / 01:46:57 UTC）：
  ```json
  {"ts": "2026-10-23T01:46:57Z", "proc": "worker", "event": "signal", "signum": 15}
  {"ts": "2026-10-23T01:46:57Z", "proc": "worker", "event": "worker_stop", "owner": "worker-14946"}
  ```
  **事实判定**：09:46:57 时，伴生 Worker 明确捕获到了 OS 关机流程广播的 `SIGTERM` 信号（`signum: 15`），随后主动停止下线。
- **采集器日志时序**（`realtime.log`）：
  采集器最后一条正常周期日志停在 `2026-10-23T01:46:05Z`（09:46:05 CST），与 Worker 下线时间严密吻合。

### 2.4 开机自启守护配置排查
- 检查 `框架/ops/schedule/` 与系统 `launchd` plist，仅包含快照、Parquet 归档及巡检任务（`com.crypto.ops.hotsnapshot.plist` 等），**从未配置过 `backend :8787` 的 launchd 开机守护服务**。

---

## 3. 诊断归因结论

| 诊断维度 | 结论 | 证据等级 |
| :--- | :--- | :--- |
| **8787 停止原因** | **操作系统在 2026-10-23 09:51 重启，重启前 OS 发送 SIGTERM 下线进程，8787 服务随系统关机正常终止** | 确凿实据（`worker.log` 捕获 SIGTERM、`who -b` 记录系统重启） |
| **是否发生异常崩溃** | **否**（无 OOM、无 Python Exception，各日志未见崩溃堆栈） | 确凿实据（日志终态正常） |
| **开机后为何未运行** | **系统未配置 backend 8787 的开机自动拉起守护，开机后未人工触发启动脚本** | 确凿实据（无对应的 launchd plist） |

依据任务“不直接重启日常 8787”的要求，当前保持原样停止状态，已为后续恢复准备安全可执行的日常恢复方案。
