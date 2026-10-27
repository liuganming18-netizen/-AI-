# SYSTEM-STARTUP-01 ｜ 统一启动命令与运行链编排规范

## 1. 架构目标与接线原则
在本次重构中，系统统一编排入口完成对“启动系统 → 真实运行链”的接线，严格践行以下原则：
1. **统一管理入口**：通过核心管理组件 `框架/backend/service_manager.py`，对外封装至 `框架/backend/start.sh` 与桌面双击脚本 `启动系统.command`。
2. **默认防线（安全启动）**：普通 UI/API 启动**严禁暗中开放自动交易**。默认启动只运行 API (`adapter.py`)、回测 Worker (`backtest_worker.py`) 并感知复用采集器，交易链组件处于 `disabled_safe_mode`。
3. **隔离模拟模式显式声明**：只有明确传入 `--enable-paper` 或环境变量 `SYS_ENABLE_PAPER=1` / `ENABLE_PAPER_RUNTIME=1`，且**生效参数配置核验必须完全通过**，才真正启动 `unified_runtime.py`。
4. **缺配置/冲突/崩溃强闸门**：遇到配置缺失、端口被未知进程抢占、子进程启动异常退出时，均显式抛出非零退出码并记录失败状态（`failed_missing_config` / `failed_port_conflict` / `failed_process_crashed`）。

---

## 2. 启动与管理命令一览

### 2.1 桌面交互启动（`启动系统.command`）
- **默认安全模式（查看行情/UI/回测，不交易）**：
  ```bash
  ./启动系统.command
  # 或在 macOS Finder 中直接双击运行
  ```
- **隔离模拟交易模式（接通真实信号生成与模拟执行链）**：
  ```bash
  SYS_ENABLE_PAPER=1 ./启动系统.command
  # 或
  ./启动系统.command --enable-paper
  ```
- **只查服务状态不启动**：
  ```bash
  ./启动系统.command --status
  ```

### 2.2 后端脚本管理（`框架/backend/start.sh`）
- **默认安全启动**：
  ```bash
  sh 框架/backend/start.sh
  ```
- **指定独立端口与 Python 解释器（隔离测试推荐）**：
  ```bash
  PORT=9876 PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python sh 框架/backend/start.sh
  ```
- **启动隔离模拟交易链（指定策略臂与配置）**：
  ```bash
  PORT=8787 sh 框架/backend/start.sh --enable-paper --arm 3a --symbol BTCUSDT
  ```
- **全景状态查询（输出结构化 JSON）**：
  ```bash
  sh 框架/backend/start.sh status --json
  ```
- **安全停止当前端口管理的服务**：
  ```bash
  PORT=8787 sh 框架/backend/start.sh stop
  ```

### 2.3 底层服务管理器核心（`框架/backend/service_manager.py`）
支持最灵活的命令行选项：
```bash
python3 框架/backend/service_manager.py <action> [options]

Actions:
  start          启动服务编排链（API、Worker、可选 Runtime）
  stop           精准停止本实例管理的进程，释放端口
  restart        安全重启本实例
  status         输出所有进程的真实 PID、配置身份、启动时间、心跳与退出状态
  diagnose-8787  只读诊断日常 8787 历史状态与退出依据

Options:
  --port PORT           API 端口 (默认: 8787)
  --enable-paper        启用真实信号与纸面模拟执行链
  --arm {3a,3b,3c,3d}   纸面模拟账户策略臂 (默认: 3a)
  --symbol SYMBOL       交易对标的 (默认: BTCUSDT)
  --config-path PATH    参数生效视图文件路径 (若不传使用默认批准视图)
  --pid-dir PATH        独立 PID 文件目录 (隔离测试防污染)
  --log-dir PATH        独立日志输出目录 (隔离测试防污染)
  --stop-collector      显式要求停止实时采集器 (默认不停止，防误停)
  --json                以结构化 JSON 输出结果
```

---

## 3. 环境变量对照表

| 环境变量 | 默认值 | 作用说明 |
| :--- | :--- | :--- |
| `PORT` / `SYS_BE_PORT` | `8787` | 后端 API 监听端口 |
| `SYS_FE_PORT` | `3000` | 前端 UI 访问端口 |
| `ENABLE_PAPER_RUNTIME` | `0` | 是否拉起交易链 (`0`: 禁用; `1`: 启用) |
| `SYS_PAPER_ARM` | `3a` | 纸面执行策略臂身份标识 |
| `EFFECTIVE_VIEW` | 默认批准视图 | 参数生效视图 JSON 路径，缺损即拦截 |
| `SYS_PID_DIR` | `框架/backend` | PID 文件落盘目录 |
| `SYS_LOG_DIR` | `框架/backend/logs` | 日志文件落盘目录 |
