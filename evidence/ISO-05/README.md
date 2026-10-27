# ISO-05 冷层归档隔离修复验收交付报告

## 1. 任务背景与施工范围

- **任务代号**：ISO-05
- **核心目标**：彻底关闭冷层归档（`market_archive`）隔离遗漏与全路径绕行风险，确保隔离模式下必须显式提供独立的冷层归档目录，严禁任何回退或别名绕入日常生产 `数据/市场数据/parquet` 目录，并向所有子进程传递隔离归档环境变量，实现冷层读取真实隔离。
- **施工范围约束**（严格限定）：
  1. `框架/backend/service_manager.py`（服务管理与路径审计）
  2. `框架/backend/tests/test_system_isolation_05.py`（新增专项隔离测试）

---

## 2. 缺陷定位与修复详情

| 序号 | 缺陷位置（原代码） | 缺陷原因分析 | 修复方案 |
| :--- | :--- | :--- | :--- |
| **1** | `service_manager.py` 约 98 行 | 隔离模式必填项检查遗漏 `market_archive` | 在 `missing` 检查列表中显式增加 `self.market_archive`，缺失时抛出 `RuntimeError` 拒绝启动并报错，严禁回退日常目录 |
| **2** | `service_manager.py` 约 136 行 | 仅在提供归档路径时才检查，存在条件穿透漏洞 | 将 `market_archive` 无条件列入 `paths_to_audit` 必填审计列表，进行符号链接、相对路径（`..`）及绝对路径防日常生产目录绕入硬拦截；并解析规范绝对路径 |
| **3** | `service_manager.py` 约 175 行 | 隔离模式若漏掉必填项会穿透回退到日常 `数据/市场数据/parquet` | 强制隔离模式提前校验并 return，只有非隔离模式（日常 8787 生产端口）才允许回退日常 parquet，保持生产完全兼容 |
| **4** | `service_manager.py` 约 665 行 | 环境变量传递逻辑中对隔离模式的 `MARKET_ARCHIVE` 缺乏显式保证与 `ISOLATED_MODE` 传递 | 在 `shared_env` 中确保 `MARKET_ARCHIVE` 注入到所有子进程（`adapter.py`、`backtest_worker.py`、`unified_runtime.py`），且设置 `ISOLATED_MODE=1` |
| **5** | `service_manager.py` CLI 解析处 | 命令行参数缺失 `--market-archive` 支持 | 增加 `--market-archive` 命令行参数并传入 `ServiceConfig`，确保 CLI 与环境变量双通道支持 |

---

## 3. 同次全量验证记录（Run ID: ISO-05-RUN-20261023-081730）

本次验收在同一次执行运行周期内完成了全量用例对账与证据固化：

- **同次运行编号 (Run ID)**: `ISO-05-RUN-20261023-081730`
- **运行时间戳 (UTC)**: `2026-10-23T08:17:30.395966+00:00`
- **隔离测试分配端口**: `57553`
- **API 进程 PID**: `37744`
- **回测 Worker 进程 PID**: `37747`

### 3.1 验收项执行结论

| 验收项 | 期望结果 | 实测结论 | 证据文件 |
| :--- | :--- | :--- | :--- |
| **1. 缺路径拒绝** | 缺少 `market_archive` 立即报错拒绝启动 | **PASSED**：精确抛出 `隔离模式缺少必要路径配置：['market_archive']，拒绝启动，禁止退回日常目录` | `isolation_evidence.json` |
| **2. 日常路径拒绝** | `market_archive` 指向日常生产目录拒绝启动 | **PASSED**：拦截并提示 `隔离模式严禁使用日常数据/状态目录: market_archive -> ... 指向日常数据根目录 ...` | `isolation_evidence.json` |
| **3. 符号链接/别名绕入拒绝** | 符号链接或 `..` 相对路径指向日常目录拒绝启动 | **PASSED**：符号链接被 `resolve()` 还原后硬拦截，阻断任何别名绕行 | `isolation_evidence.json` |
| **4. 隔离冷层读取成功** | 实际触发冷层 Parquet 读取，命中隔离沙盒特有数据 | **PASSED**：调用 `/api/v1/markets/binance/BTCUSDT/funding?limit=10` 成功返回冷层特有特征数据 `funding_rate=0.00777777`, `source=isolated_evidence_ISO-05-RUN-20261023-081730` | `isolation_evidence.json`, `api.log` |
| **5. 子进程打开文件核验** | 子进程 0 访问日常受保护生产数据 | **PASSED**：`lsof` 详细检查 PID 37744 与 37747，日常生产目录（`数据/市场数据/parquet`、`数据/任务队列`、`数据/paper`、`live`、`locks`、`logs`）违规访问数为 0 | `lsof_api.txt`, `lsof_worker.txt` |
| **6. 日常非隔离模式兼容** | 非隔离模式未指定归档时回退日常 parquet | **PASSED**：正常填充日常 `数据/市场数据/parquet`，日常服务启动不受影响 | `test_execution.log` |
| **7. 既有隔离与风险逻辑保留** | 保留 `jobs_db` 隔离与缺 live 库 unavailable 约定 | **PASSED**：缺失 `jobs_db` 拦截、账户风险接口 unavailable 约定全部验证通过 | `test_execution.log` |

---

## 4. 证据文件清单

- `isolation_evidence.json`: 同次运行完整结构化对账证据（包含 PID、返回数据、验证状态及受保护规则）
- `test_execution.log`: `unittest 框架/backend/tests/test_system_isolation_05.py` 全量执行日志（6 项测试全部通过）
- `api.log`: 同次运行 API 服务（端口 57553）启动、冷层请求处理与优雅退出日志
- `worker.log`: 同次运行 Worker 进程启动与优雅退出日志
- `lsof_api.txt`: API 进程（PID: 37744）在运行状态下的完整系统打开文件句柄（`lsof`）核验清单
- `lsof_worker.txt`: Worker 进程（PID: 37747）在运行状态下的完整系统打开文件句柄（`lsof`）核验清单
- `source_hashes.txt`: 施工文件 SHA-256 哈希清单
- `source_hashes.json`: 施工文件 SHA-256 哈希结构化数据

---

## 5. 施工源码哈希

```text
7ba1edf95b65b728c79c6890fd438e541a5e6c0f46c21c85a8ad84e5b0b0fa2a  框架/backend/service_manager.py
0b18e980f99787aaa4ecc60e57f5144ba2c9161da943cd36e208600edc8e53f9  框架/backend/tests/test_system_isolation_05.py
```
