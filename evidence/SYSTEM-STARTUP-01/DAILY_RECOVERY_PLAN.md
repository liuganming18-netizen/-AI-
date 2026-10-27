# SYSTEM-STARTUP-01 ｜ 日常 8787 恢复操作方案与安全指南

## 1. 现状确认与恢复前纪律
- **当前现状**：任务 B 严格遵循“**不直接重启日常 8787，不影响其他并行任务**”的要求。当前 8787 端口处于干净的未监听状态，历史 PID 文件已记录其下线归因事实。
- **恢复纪律**：在其他并行任务（如数据导出、批量回测审计）需要访问后端或前端时，请按本指南受控执行恢复。

---

## 2. 恢复前环境自检 Checklist

在执行恢复命令前，请在终端执行以下快速健康核验（耗时约 2 秒）：
```bash
# 1. 确认 8787 端口空闲
lsof -iTCP:8787 -sTCP:LISTEN

# 2. 确认推荐 Python 解释器环境包含 duckdb 与 pyarrow
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python -c "import duckdb, pyarrow; print('Python 环境就绪')"

# 3. 确认行情库文件可读
ls -lh "数据/市场数据/db/market_v2.sqlite3"
```

---

## 3. 标准日常恢复执行方案

### 方案 A：恢复日常 UI 与 API 浏览（默认安全模式，推荐）
适用场景：日常查看行情面板、指标数据、进行历史回测研究。**不开启自动交易，零资金风险**。

```bash
# 方式 1：通过桌面入口启动（推荐日常使用）
./启动系统.command

# 方式 2：纯命令行后台拉起
sh 框架/backend/start.sh
```

**就绪标志**：
- 终端打印 `[✔] API 服务已就绪: pid=xxxx, 端口: 8787` 与 `[✔] 回测 Worker 已就绪: pid=xxxx`；
- 浏览器访问 `http://127.0.0.1:3000/` 能够正常加载 UI 页面。

---

### 方案 B：恢复日常 UI + 真实信号与隔离模拟交易链
适用场景：需要同时运行实时信号状态机、风控路由与纸面模拟撮合守护（Arm 3a）。

```bash
# 方式 1：通过桌面入口声明模拟模式
SYS_ENABLE_PAPER=1 ./启动系统.command

# 方式 2：纯命令行方式
ENABLE_PAPER_RUNTIME=1 sh 框架/backend/start.sh --arm 3a
```

**就绪标志**：
- 终端除 API 和 Worker 外，还会打印 `[✔] 交易链 unified_runtime 已就绪: pid=xxxx`；
- 状态文件 `框架/backend/locks/paper_runtime_3a_status.json` 中 `overall_health` 呈 `running` 或 `healthy`。

---

## 4. 恢复后的快速核验命令

服务启动后，执行以下命令验证核心通路：
```bash
# 1. 查看统一服务全景状态（真实 PID、身份、心跳）
sh 框架/backend/start.sh status

# 2. 验证基础 HTTP 探针
curl -s --noproxy '*' http://127.0.0.1:8787/api/health
# 预期返回: {"状态": "可用", "模式": "本机只读"}

# 3. 验证数据总池状态
curl -s --noproxy '*' http://127.0.0.1:8787/api/v1/pool/status
```

---

## 5. 长期稳定运行方案（开机自启 launchd 建议）

针对本次查明的“系统重启后因未安装 launchd 守护而停止”的根因，若后续希望系统在开机后自动拉起 8787，可选择安装 launchd 守护。

### 守护文件模板：`~/Library/LaunchAgents/com.crypto.backend.8787.plist`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.crypto.backend.8787</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>/Users/boos/Desktop/威科夫因子共振趋势/框架/backend/start.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>WorkingDirectory</key>
    <string>/Users/boos/Desktop/威科夫因子共振趋势/框架/backend</string>
    <key>StandardOutPath</key>
    <string>/Users/boos/Desktop/威科夫因子共振趋势/框架/backend/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/boos/Desktop/威科夫因子共振趋势/框架/backend/logs/launchd_stderr.log</string>
</dict>
</plist>
```
*注：因当前环境可能涉及多任务并行调试，建议待人工核准后再决定是否加载该 launchd 守护。*
