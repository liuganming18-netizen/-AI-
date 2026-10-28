# SIGNAL-EFFECTIVE-02 独立运行验收与终审复核总览报告

- **日期**：2026-10-28（北京时间）
- **工单**：`docs/派工_SIGNAL-EFFECTIVE-02_ANTIGRAVITY_运行验收_2026-10-27.md`
- **执行角色**：反重力（独立只读复核方）
- **被复核对象**：Work 最终交付物 `evidence/SIGNAL-EFFECTIVE-02-WORK/`（代码受控重载、加载证明、17 项隔离断言、账本接管与自然运行监测）
- **专属证据目录**：`evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/`
- **复核边界**：**100% 只读与独立沙箱复算**；零生产代码写入、零生产数据库写入、未重启采集器、未改变全局风控闸门。
- **终审裁决**：**全项独立复核通过，建议主代理予以正式终审闭环**。

---

## 一、 核心验收维度分项结论

| 验收维度 | 验收判定 | 核心独立证据与实测依据 |
| :--- | :---: | :--- |
| **1. 原始授权与生效边界** | **通过** | 详见专报《[生效边界与时序语义裁决报告.md](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/生效边界与时序语义裁决报告.md)》；厘清 10-23 持久化水位属于相同策略重启的断点保护机制，而 10-27 属于用户法定上线指令时刻；证实不论按哪一口径，289 条历史信号均被双重硬拦截，绝不追补挂单。 |
| **2. 源码改动与配置哈希** | **通过** | 4 个关键后端文件（`live_api.py`、`live_worker.py`、`paper_engine.py`、`unified_runtime.py`）源码哈希已锁定；确认切断未授权仓位倍数、补齐 `approved` 判据、修复 9 天 8 小时生效真空；策略身份严格绑定 `FT-LOW4H-EXP-V2.2`。 |
| **3. 进程加载证明 (三重闭环)** | **通过** | **实测确认新代码已正式在生产进程加载生效**：<br>① 新 API (pid 14857) 与新 Runtime (pid 14879) 启动时间（16:12Z）晚于源码修改时间（15:06Z）65 分钟；<br>② 真实 HTTP API `/signals?scope=current` 成功吐出改动⑤新增的代码键 `unapproved_config=289`；<br>③ 水位自述与重载前逐位一致；采集器 (pid 42278) 与 Worker (pid 68775) **未被重启**（最小重载范围达成）。 |
| **4. 隔离行为复算 (17/17)** | **通过** | 独立复跑 Work 交付的完整隔离断言脚本 [`scripts/02_iso_boundary_assert.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-WORK/scripts/02_iso_boundary_assert.py)，**全项 17/17 独立断言 100% 通过**（含毫秒级闭开区间、结构早确认晚放行、`approved=false` 拦截、未获批仓位锁死 1.0x 等）。 |
| **5. 账本保护与挂单接管** | **通过** | 查验 `paper_3a.sqlite3` 账本：<br>• 基线时刻已存在的挂单 `test_probe_succ` 被新 Runtime 正确接管并在到期时自然成交为 `BTCUSDT` 多单持仓（position_id=4, entry=80469.5, stop=76446.0，严格执行 95% 初始止损且 `planned_risk=24.14` 未被倍数放大）；<br>• 其余既有历史持仓与订单记录零丢失、零改动。 |
| **6. 自然扫描与水位推进** | **客观证实** | 独立读取当前进程锁文件：UnifiedRuntime (pid 14879) 周期推进正常（`cycles` 已推进至 480+），全池 526 标的无滞后；`watermark_1h` 成功跨边界从 15:00Z 推进至 16:00Z；客观证实自然候选为 **0 笔**系反转策略高门槛所致（漏斗卡在回踩层），非代码断链。 |

---

## 二、 关键时间语义与授权核查定性

反重力穿透底层运行库与交接记录，得出确凿定性：
1. **持久化历史水位（10-23）**：`1792748844240`（2026-10-23 09:47:24 UTC）是此前测试运行自动落定的断点保护线，代码在相同模型版本重启时幂等沿用此值；
2. **用户法定上线时刻（10-27）**：`1793109556649`（2026-10-27 13:59:16 UTC）是用户正式下达“上线系统”指令并拉起实盘进程的时刻；
3. **因果防线双重安全**：
   - 在完成 01 数据修复后，全部 289 条记录已恢复为 `origin='historical_research'` 且 `approved=false`，配合代码级双端 `approved` 判据，**无论按 10-23 还是 10-27 计算，所有历史信号均被 100% 绝对拦截**，零挂单、零坏账。

---

## 三、 交付产物与哈希清单

全部成果均位于反重力专属证据目录 `evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/`：

| 文件相对路径 | 类型 | SHA256 校验哈希 | 说明 |
| :--- | :---: | :--- | :--- |
| [`README.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/README.md) | 主报告 | *本文件落盘时计算* | 全项独立运行验收与终审总览报告 |
| [`Work交付终审独立复核核验表.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/Work交付终审独立复核核验表.md) | 专报 | `d9e798725890b07b1b11b51e9b2ce7481baef80276a6cf4ecfa58d6973ba88e7` | 5 大验收维度与技术细节逐项核对表 |
| [`生效边界与时序语义裁决报告.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/生效边界与时序语义裁决报告.md) | 专报 | `0db7876dc9080709b1ea6cb5d496052beec1c0734a26e8557b777a285d0d9701` | 10-23 vs 10-27 授权关系与时间语义穿透 |
| [`out/epoch_semantics_audit.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/out/epoch_semantics_audit.json) | 数据 | `8ddefdf60c3e88747fcb4cfe0ab06296280cc100d70859b3c7d038ee5fca4b66` | 持久化水位与运行身份详细对比数据 |
| [`out/时间语义对照表.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/out/时间语义对照表.csv) | 数据 | `51e6ec6f2560e2c4854b5d5d6833b03d900b1dbc97e6873c509138bf715c3ab0` | 五重时间语义层定义与依据对照表 |
| [`out/source_code_and_process_fingerprint.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/out/source_code_and_process_fingerprint.json) | 数据 | `4319ede11cda9cbe4f5b6b8c76c6e2d24d0c825ff4dea06c93a7b0ba92a2e452` | 源码哈希、运行进程、锁状态快照 |
| [`out/isolated_verification_result.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/out/isolated_verification_result.json) | 数据 | `df2e0241078205eaf4f14791821c11c81edf7416dc46ef6f96e3134adefc60bf` | 8 类独立隔离复算原始断言结果 (8/8 PASS) |
| [`out/natural_watermark_status.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/out/natural_watermark_status.json) | 数据 | `556429362cc91e7dd674cedda64934b1c281c8e97ca246d77cd909995496efb6` | 自然运行水位推进与 0 候选客观原因 |
| [`scripts/01_audit_authorization_and_epoch_semantics.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/scripts/01_audit_authorization_and_epoch_semantics.py) | 脚本 | `a17b0d778d9b1c7da2ef0ee20b1297e6b8a8b1be408aaee832d2df26cb75079a` | 授权溯源与时间语义分析脚本 |
| [`scripts/02_audit_source_code_and_runtime_fingerprint.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/scripts/02_audit_source_code_and_runtime_fingerprint.py) | 脚本 | `0149ea67bcfa032338d82d4ff9841f3d4ef2f7b8c7512d7b88981180e0c0df4c` | 源码哈希与进程扫描脚本 |
| [`scripts/03_isolated_boundary_verification.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/scripts/03_isolated_boundary_verification.py) | 脚本 | `3130d29463c2cbb54d6d630d87be7b223cba0034604a11f97330740a8523c0f4` | 8 类隔离复算独立套件脚本 |
| [`scripts/04_audit_natural_watermark_and_candidates.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-EFFECTIVE-02-ANTIGRAVITY/scripts/04_audit_natural_watermark_and_candidates.py) | 脚本 | `a61b7f94bbfe486c43336ae954f91048e7f12e2df5ecbbdbf165e317075c3db6` | 自然运行水位与候选监测脚本 |

---

## 四、 最终建议

反重力独立复核认定：
1. Work 在 `SIGNAL-EFFECTIVE-02` 中对代码重载的范围把控极其严谨（只重启 Runtime + API，未重启采集器与 Worker），新代码加载证明确凿闭环；
2. 17 项隔离时序与权限断言 100% 成立；
3. 既有挂单管理与资产保护具有确定性，未产生非预期坏账；
4. 自然运行水位跨 1h 边界正常推进，0 候选现象客观真实。

**建议主代理正式予以终审闭环，工单通过验收**。
