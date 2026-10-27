# SYSTEM-STARTUP-01 ｜ 进程拓扑与生命周期流转规范

## 1. 架构与进程拓扑图

系统分为用户层、接入与路由层、计算与执行层、数据存储层。各进程各司其职，生命周期独立隔离：

```mermaid
graph TD
    User["用户浏览器 / 桌面启动脚本"] --> Launch["启动系统.command / start.sh"]
    Launch --> SM["service_manager.py 统一生命周期管理中心"]

    subgraph "管理与调度域 (由 service_manager 负责)"
        SM -->|"1. 脱离会话拉起"| API["API 适配服务 (adapter.py)<br/>监听: 127.0.0.1:8787<br/>只读行情 / 状态暴露 / Web接入"]
        SM -->|"2. 脱离会话拉起"| Worker["异步回测工作进程 (backtest_worker.py)<br/>独立 CPU 密集任务 / 任务表租约"]
        SM -.->|"3. 仅显式 --enable-paper<br/>且配置通过时拉起"| Runtime["真实信号与统一执行链 (unified_runtime.py)<br/>4h箱体 -> 1h扫低 -> 路由风控 -> 撮合守护"]
    end

    subgraph "全局行情总线域 (独立采集，生命周期受保护)"
        SM -.->|"感知/健康核验 (不随意 kill)"| Collector["实时行情采集器 (realtime_collector.py)<br/>WS/REST 行情补缺 / 聪明钱快照"]
        Collector -->|"写入闭合与实时 K 线"| DB[("market_v2.sqlite3 行情总库")]
    end

    API -->|"只读查询"| DB
    API -->|"任务入队"| JobsDB[("jobs.sqlite3 任务队列")]
    Worker -->|"领任务并计算"| JobsDB
    Runtime -->|"读取闭合K线"| DB
    Runtime -->|"信号落盘与撮合"| PaperLedger[("paper_ledger 纸面模拟账本")]

    classDef managed fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    classDef protected fill:#fff3e0,stroke:#f57c00,stroke-width:2px;
    classDef storage fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px;

    class API,Worker,Runtime managed;
    class Collector protected;
    class DB,JobsDB,PaperLedger storage;
```

---

## 2. 各进程身份判据与生命周期矩阵

| 组件名称 | 启动脚本 | 真实监听/特征 | PID 文件 | 身份核验判据 | 停止行为 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **API 服务** | `adapter.py` | 端口 (如 `:8787`) | `backend-<port>.pid` | 监听目标端口 + CWD 在工程内 + 命令行含 `adapter.py` | 发送 SIGTERM，至多等 10s，释放端口 |
| **回测 Worker** | `workers/backtest_worker.py` | 领任务表计算 | `worker-<port>.pid` | 进程存活 + CWD 在工程内 + 命令行含 `backtest_worker.py` | 发送 SIGTERM，等待当前任务完成或超时优雅退出 |
| **真实运行链** | `unified_runtime.py` | 事件状态机推进 | `runtime-<arm>-<port>.pid` | 进程存活 + CWD 在工程内 + 命令行含 `unified_runtime.py` + 持有文件锁 | 发送 SIGTERM，释放文件锁，退出原因记为 `normal_shutdown` |
| **实时采集器** | `realtime_collector.py` | 行情入库心跳 | `logs/realtime.pid` | 进程存活 + CWD 在工程内 + 命令行含 `realtime_collector.py` | **防误停保护**：常规 stop 绝不杀采集器；必须传 `--stop-collector` 且由本环境发起时才操作 |

---

## 3. 运行状态流转图

```mermaid
stateDiagram-v2
    [*] --> Initializing: 启动命令下达

    Initializing --> ConflictCheck: 端口与配置前置核验
    ConflictCheck --> PortConflict: 目标端口已被外部未知进程占用
    PortConflict --> [*]: 明确报错 failed_port_conflict (不篡改端口、不杀未知进程)

    ConflictCheck --> PaperGateCheck: 是否声明 --enable-paper
    PaperGateCheck --> SafeMode: 否 (默认普通 UI/API 模式)
    PaperGateCheck --> ConfigVerify: 是 (隔离模拟模式)

    ConfigVerify --> ConfigMissing: 缺少有效配置文件或契约失效
    ConfigMissing --> [*]: 明确报错 failed_missing_config (坚决不静默启动)

    ConfigVerify --> ReadyToSpawn: 契约核验通过 (版本与规则有效)
    SafeMode --> ReadyToSpawn: 交易链置为 disabled_safe_mode

    ReadyToSpawn --> Spawning: setsid 脱离会话双重 fork 拉起各子进程
    Spawning --> HealthProbing: 等待 HTTP 200 与进程心跳
    HealthProbing --> ProcessCrashed: 子进程提前退出或日志带异常
    ProcessCrashed --> [*]: 记录 failed_process_crashed 并暴露堆栈

    HealthProbing --> Running: 全部服务健康心跳建立
    Running --> Stopped: 接收到 service_manager.py stop 指令
    Stopped --> [*]: 释放端口与锁，标记 normal_shutdown
```

---

## 4. 采集器防误停隔离机制
1. **启动阶段**：
   - 如果系统已存在一个健康采集器（`realtime_collector.py --health` 正常），启动器只**感知并报告复用**，绝不重复拉起第二实例。
2. **停止阶段**：
   - 采集器负责全盘 500+ 合约的历史与实时行情持续同步，关系到行情库一致性。
   - `service_manager.stop()` 与 `start.sh stop` 默认将 `stop_collector=False`。
   - 停止扫描列表中只包含当前端口绑定的 API、Worker 和 Runtime，**任何常规服务停止操作均不会误碰采集器**。
