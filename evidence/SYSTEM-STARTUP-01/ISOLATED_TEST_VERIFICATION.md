# SYSTEM-STARTUP-01 ｜ 独立端口全套验收测试证据报告

## 1. 验收测试环境与执行标准
- **测试执行时间**：2026-10-23 12:04:47 (CST)
- **测试套件**：`框架/backend/tests/test_system_startup_01.py`
- **执行解释器**：`/Library/Developer/CommandLineTools/usr/bin/python3` (3.9.6) 配合运行时解释器 `envs/default/bin/python` (3.13.12)
- **严格验收纪律**：
  - 必须使用动态独立高位端口，严禁抢占或重启日常 8787；
  - 必须使用独立临时 PID 目录与日志目录，避免污染日常环境；
  - 严禁另开终端人工补起 Worker，所有子进程全自动拉起并受控监控。

---

## 2. 6 大测试用例执行结果与日志证据

```text
test_01_diagnose_8787_read_only (__main__.TestSystemStartup01)
测试 1：只读检查 8787 端口状态与历史退出原因。 ... 
[Test 1] 8787 只读诊断结论: 系统重启前收到SIGTERM下线终止（OS重启: system boot  Oct 23 09:51，Worker记录: {"ts": "2026-10-23T01:46:57Z", "proc": "worker", "event": "signal", "signum": 15}），未安装开机自启launchd
ok

test_02_unified_start_default_safe_mode (__main__.TestSystemStartup01)
测试 2：隔离端口统一安全启动，默认模式绝不暗中开放交易链。 ... [*] 默认启动模式：不开放自动交易链（交易链保持禁用）
[*] 启动 API 服务 (端口 57874)...
[✔] API 服务已就绪: pid=12424, 端口: 57874
[*] 启动回测 Worker 进程...
[✔] 回测 Worker 已就绪: pid=12428

[Test 2] 安全启动就绪: API PID=12424, Worker PID=12428, Runtime=disabled_safe_mode
[*] 停止 Worker 进程 pid=12428...
[*] 停止 API 服务 pid=12424 (释放端口 57874)...
[*] 按照防误停策略，实时采集器保持不动（如需停止请传 --stop-collector）
ok

test_03_repeat_start_idempotency_and_port_conflict (__main__.TestSystemStartup01)
测试 3：重复启动幂等复用与端口冲突拦截。 ... [*] 默认启动模式：不开放自动交易链（交易链保持禁用）
[*] 启动 API 服务 (端口 57882)...
[✔] API 服务已就绪: pid=12467, 端口: 57882
[*] 启动回测 Worker 进程...
[✔] 回测 Worker 已就绪: pid=12470
[*] API 端口 57882 已就绪且健康（复用 pid=12467）
[*] 默认启动模式：不开放自动交易链（交易链保持禁用）

[Test 3] 重复启动幂等复用成功，PID 未变: API=12467, Worker=12470
[*] 停止 Worker 进程 pid=12470...
[*] 停止 API 服务 pid=12467 (释放端口 57882)...
[*] 按照防误停策略，实时采集器保持不动（如需停止请传 --stop-collector）
[Test 3] 端口冲突拦截生效: 端口冲突：端口 57882 已被非本项目进程占用 (pid=12409, cwd='/Users/boos/Desktop/威科夫因子共振趋势', cmd='/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python 框架/backend/tests/test_system_startup_01.py -v')
ok

test_04_paper_mode_missing_config_rejected_and_valid_config (__main__.TestSystemStartup01)
测试 4：隔离模拟交易模式启动，缺配置硬拦截与有效配置启动。 ... [*] 检测到明确启用隔离模拟交易链 (Arm: 3a)... 开始参数契约强验

[Test 4-A] 缺配置硬拦截成功: 启动拒绝：交易链配置无效或缺失 (config_file_not_found: 配置文件不存在于指定路径: /var/folders/nc/4wrvcr3j62v6gvf995mx198w0000gn/T/startup_test_ltxpyzrk/nonexistent_config.json)
[*] 检测到明确启用隔离模拟交易链 (Arm: 3a)... 开始参数契约强验
[✔] 交易链配置契约校验通过（版本: FT-LOW4H-EXP-V2.2, 规则: FT-LOW4H）
[*] 启动 API 服务 (端口 57896)...
[✔] API 服务已就绪: pid=12532, 端口: 57896
[*] 启动回测 Worker 进程...
[✔] 回测 Worker 已就绪: pid=12535
[*] 启动隔离交易链进程 unified_runtime (Arm: 3a, Symbol: BTCUSDT)...
[✔] 交易链 unified_runtime 已就绪: pid=12538
[Test 4-B] 隔离模拟交易链成功拉起: Runtime PID=12538
[*] 停止交易链进程 pid=12538...
[*] 停止 Worker 进程 pid=12535...
[*] 停止 API 服务 pid=12532 (释放端口 57896)...
[*] 按照防误停策略，实时采集器保持不动（如需停止请传 --stop-collector）
ok

test_05_process_crashed_detection_and_restart (__main__.TestSystemStartup01)
测试 5：进程崩溃异常识别（failed_process_crashed）与重启恢复。 ... [*] 默认启动模式：不开放自动交易链（交易链保持禁用）
[*] 启动 API 服务 (端口 57904)...
[✔] API 服务已就绪: pid=12590, 端口: 57904
[*] 启动回测 Worker 进程...
[✔] 回测 Worker 已就绪: pid=12593

[Test 5] 成功检测到工作进程异常崩溃: status=failed_process_crashed
[*] 停止 API 服务 pid=12590 (释放端口 57904)...
[*] 按照防误停策略，实时采集器保持不动（如需停止请传 --stop-collector）
[*] 默认启动模式：不开放自动交易链（交易链保持禁用）
[*] 启动 API 服务 (端口 57904)...
[✔] API 服务已就绪: pid=12621, 端口: 57904
[*] 启动回测 Worker 进程...
[✔] 回测 Worker 已就绪: pid=12625
[Test 5] 故障重启恢复成功，新 Worker PID=12625
[*] 停止 Worker 进程 pid=12625...
[*] 停止 API 服务 pid=12621 (释放端口 57904)...
[*] 按照防误停策略，实时采集器保持不动（如需停止请传 --stop-collector）
ok

test_06_stop_isolation_does_not_kill_collector (__main__.TestSystemStartup01)
测试 6：停止只作用于本启动器管理的进程，绝不误停采集器。 ... [*] 默认启动模式：不开放自动交易链（交易链保持禁用）
[*] 启动 API 服务 (端口 57924)...
[✔] API 服务已就绪: pid=12652, 端口: 57924
[*] 启动回测 Worker 进程...
[✔] 回测 Worker 已就绪: pid=12655
[*] 停止 Worker 进程 pid=12655...
[*] 停止 API 服务 pid=12652 (释放端口 57924)...
[*] 按照防误停策略，实时采集器保持不动（如需停止请传 --stop-collector）

[Test 6] 停止操作精准隔离验证通过。
ok

----------------------------------------------------------------------
Ran 6 tests in 17.464s

OK
```

---

## 3. 验收标准逐项核验结果对照表

| 验收项 | 测试用例 | 验证事实与判定 | 结论 |
| :--- | :--- | :--- | :--- |
| **1. 8787 只读诊断** | `test_01` | 查明系统 09:51 重启与 09:46 收到 SIGTERM，绝无猜测 | **PASS** |
| **2. 统一启动** | `test_02` | API (:57874) 与 Worker 进程均就绪，HTTP 探针返回 200 | **PASS** |
| **3. 安全默认防线** | `test_02` | 交易链 `runtime` 保持 `disabled_safe_mode`，无 PID | **PASS** |
| **4. 重复启动拒绝** | `test_03` | 再次 start 幂等复用，API PID 保持 12467，未重复拉起 | **PASS** |
| **5. 端口冲突明确失败** | `test_03` | 模拟原生 socket 占口，捕获 `RuntimeError: 端口冲突` | **PASS** |
| **6. 缺配置强硬拦截** | `test_04` | 传入无效配置，立即中断并置 `failed_missing_config` | **PASS** |
| **7. 隔离模拟交易接通** | `test_04` | 显式 `--enable-paper` 且配置合格，拉起 `unified_runtime` | **PASS** |
| **8. 异常崩溃识别与重启** | `test_05` | 杀掉 worker，状态识别 `failed_process_crashed`，重启成功 | **PASS** |
| **9. 停止隔离与防误停** | `test_06` | stop 只清理本端口 PID，释放端口，不触碰采集器 | **PASS** |
