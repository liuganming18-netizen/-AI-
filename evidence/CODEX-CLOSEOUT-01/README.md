# CODEX-CLOSEOUT-01 · 项目收尾（接 Luna 中断点继续）

- **窗口**：WorkBuddy 新窗口（接手 Codex/Luna 未完成的收尾）
- **时间**：2026-10-24 09:34 – 10:05（Asia/Shanghai）/ 01:34 – 02:05 UTC
- **接手原因**：Codex 子代理 `luna` 在 2026-10-24T01:32:54Z 触发用量上限（`usageLimitExceeded`），中断在「逐项区分 12 个失败用例的类别并做最小兼容修复」这一步。
- **本目录定位**：收尾报告 + 复现脚本 + 原始产物。**不是**策略有效性报告。

---

## 〇、一句话结论

**收尾的工程项做完了：12 个失败用例里 10 个是真缺陷、已按最小改动修好，2 个是「测试断言的前提已不存在」、如实留红不粉饰；服务已重启并证明跑的是新代码；真实浏览器页面验收 15/15 通过。系统整体仍然不通过——模拟盘自然市场依旧 0 成交，正式实验的交付资格仍是 0。**

| 项 | 判定 | 依据 |
|---|---|---|
| 首个正式实验能否启动 | **通过**（本轮修复） | 身份漂移根因消除，`frame_fit_02` T01/T03/T04 由 409 变 202 并跑到执行 |
| 逐笔交易号/初始止损可回读 | **通过**（本轮修复） | 从账本 `POSITION_CLOSED` 与 `P0` 补出，来源逐条标注 |
| 服务重启后跑的是新代码 | **通过** | API pid 35163 启动 02:00:27Z，晚于全部受影响文件 mtime，`stale_files=[]` |
| 页面与服务端同源 | **通过** | 浏览器 15/15（闸门/费率/版本/交易号/持仓时长/首行定位） |
| 12 个失败用例全部转绿 | **不通过**（2 项留红） | 见第三节：2 项断言的是「执行链写死窗口」这个已删除的缺陷 |
| 模拟盘自动成交 | **未验证** | 全池 526 标的、自然市场 0 新信号；账本 0 笔已平 |
| 正式实验结果可交付 | **不通过** | `delivery_eligible=false`，`delivery_blockers=["尚无已登记运行"]` |

---

## 一、接手时的现场（不是我造的）

| 事实 | 值 |
|---|---|
| Codex 中断时刻 | 2026-10-24T01:32:54Z，错误 `usageLimitExceeded` |
| 中断前最后一句 | 「从失败日志逐项对照最近改动和测试 stub，区分签名兼容、夹具语义与真实回归；确认属于本轮引入的问题后，只做保持原断言的最小兼容修复，并跑指定测试集」 |
| 磁盘代码 | 已被 Luna 改到 01:31（`live_api.py`）、01:33（`metrics_api.py`） |
| 运行进程 | API 31932 / Worker 31936 / Runtime 31940，均启于 01:33–01:34Z（**早于** 01:44 的修复 ⇒ 需重启才算数） |
| 失败用例 | `guard_ui_tests.log`：12 failed / 83 passed |

---

## 二、本轮改动（5 个文件，全部为最小改动）

| 文件 | 改了什么 | 为什么必须改 |
|---|---|---|
| `框架/backend/experiment_registry.py` | 新增 `EXECUTION_CONTRACT_FIELDS` + `default_execution_contract()`；`current_design_parts()` 在未显式给契约时取**当前已批准契约** | 创建入口把执行契约并进设计身份，登记入口不并 ⇒ 同一份配置算出两个身份，首个正式实验恒被判「身份漂移」 |
| `框架/backend/backtest_api.py` | ①契约改调上面那个函数（删掉本地那份字段清单）；②初始止损回填带**来源标注**；③受理说明按实验真实模式写，不再把正式实验标成「研究模式」 | ①一处定义避免两侧再漂；②来源不能一律写 `stop_trace[0]`；③正式实验被标成研究模式是误报 |
| `框架/backend/backtest_batches.py` | 时间字段补认 `entry_time` / `exit_time`；初始止损补认 `P0` | 项目根批量脚本写的是 `entry_time`，少认一组 ⇒ `exit_ms` 恒空 ⇒ 账本对齐整条失效、交易号整列拿不到 |
| `框架/backend/ledger_links.py` | 初始止损兜底补 `P0` 与大写变体，并记录 `initial_stop_source` | FT 系列产物用大写 `P0`，原代码只认 `initial_stop_P0`/`p0` |
| `框架/backend/service_manager.py` | `get_process_cmdline()` / `get_process_cwd()` 在 `ps`/`lsof` 返回空时退回 `psutil` 直读 | 本机沙箱里 `ps` 被拒 ⇒ 命令行读到空串 ⇒ 被判「身份无法确认为本实例」⇒ `stop()` 直接抛错，**服务重启根本做不下去**（A-6 的实测复现） |

**没有改**：策略规则、撮合逻辑、止损参数、闸门语义、账本结构、任何阈值、任何验收断言。

---

## 三、12 个失败用例：分类与结果

| # | 用例 | 类别 | 根因 | 处理 |
|---|---|---|---|---|
| 1–7 | `test_frame_fit_02` T01(3) / T03(2) / T04(2) | **真实回归**（本轮引入） | 设计身份：登记 `3b15359a…` vs 创建 `b5dc37c6…`，差在 `execution_contract` 只有请求侧有 | **已修**，全部转 202/通过 |
| 8–9 | `test_bt_ui_02` 交易号补全 / `test_bt_ui_01` 逐笔事实 | **真实回归**（本轮引入） | `_trade_row` 不认 `entry_time`/`exit_time` ⇒ `exit_ms=None` ⇒ 账本索引查不到 | **已修**（MOGUSDT 实得 `trd_483d75df…`） |
| 10 | `test_bt_ui_02` 初始止损回填 | **真实回归**（本轮引入） | 只认 `stop_trace`/`initial_stop`，FT 产物只有大写 `P0` | **已修**（1000XEC 实得 `P0=0.045861…`，来源标 `trades.jsonl.P0`） |
| 11 | `test_frame_fit_02` T06 写死窗口被拒 | **前提已不存在** | 断言「窗口与执行链写死的 2024H1 不符 ⇒ 409 且含『写死』」。新基线（FT-LOW4H 显式窗口）**已删除写死窗口**，同一请求现在合法 ⇒ 202 | **不改代码、不改断言，留红**（改代码去伪造「写死」= 撒谎；改断言 = 动验收契约，须你批） |
| 12 | `test_frame_fit_01` T07 同上 | **前提已不存在** | 同上 | **同上** |

**修复前后（同一命令、同一批文件）**

| 时点 | 结果 |
|---|---|
| 接手时（Luna 日志） | 12 failed / 83 passed |
| 修完后（四个文件合跑） | **2 failed / 93 passed** |
| 全量 `框架/backend/tests` | 上一轮基线（2026-10-23 AUTO-LOOP-01 期间实测）**18 failed / 361 passed / 79 skipped** → 收尾后 **13 failed / 366 passed / 79 skipped** |

剩下 13 个失败 = 上面 2 个「前提失效」+ 11 个**环境冲突**（`system_isolation_03` 4 个、`system_isolation_05` 2 个、`system_startup_01` 5 个）。这 11 个都是因为**生产服务正在跑**：`worker_already_running`、端口占用、隔离实例与在线实例抢同一份 `jobs.sqlite3`。它们不是本轮改出来的，也不是代码回归——把在线服务停掉才能跑，属于测试夹具与在线环境互斥，已如实记录，不当作通过。

---

## 四、服务重启与版本绑定（"跑的是新代码"必须自证）

| 项 | 值 |
|---|---|
| 重启命令 | `python 框架/backend/service_manager.py restart --enable-paper --arm 3a --symbol all` |
| 第一次失败 | `拒绝停止未知 runtime 进程：pid=31940, cmd=''` ⇒ 暴露 A-6（`ps` 无降级） |
| 修复后重启 | API **35163**（02:00:27Z）/ Worker 35166 / Runtime 35173 |
| 版本绑定 | `loaded_newer_than_all_files=true`，`stale_files=[]`（受影响 6 个文件 mtime 全早于进程启动时刻） |
| 重启后状态 | Runtime `running`，cycle 13，universe 526，`scan_errors_total=0`，`execution_daemon=running`，闸门 `allow_new_entries=true` |
| 采集器 | 未动（复用 pid 42278，符合防误停策略） |

---

## 五、真实浏览器页面验收（15/15）

脚本：`probes/20_ui_acceptance.mjs`（headless Chromium，读 DOM 文本/属性 + 同源 `/api/v1` 取数逐项比对）。
产物：`out/ui_acceptance.json`、`out/ui_shots/*.png`。

| 编号 | 检查 | 实测 |
|---|---|---|
| U0 | 页面非白屏 | body 文本 1037 字 |
| U1.1 | 闸门取服务端 | DOM「新开仓总闸：开启（user_ui_resumed）」/ API `allow_new_entries=true` |
| U1.2 | 费率不硬编码 | DOM「滑点单边 5.00 bps · 手续费单边 5.00 bps」= 契约 |
| U1.3 | 版本同源 | DOM 与 API 同为 `FT-LOW4H-EXP-V2.2` |
| U1.4 | 不再宣称「17 项核心流正常」 | 全文不含 |
| U2.1/U2.2 | 数据中心默认页 | 给出实时结论；默认页无「100% 补齐」静态断言 |
| U3.1–U3.5 | 回测复盘 | 列表可载；逐笔 `trd_edfeb086…`、entry 1790954100000 / exit 1791004500000 / 持仓 14h；「按单 run」里该 run 在**首行**（不跳回旧批次） |
| U4.1/U4.2 | 工作台「最近推进」 | 字段存在，`last_cycle_ms` 与接口同源 |
| U5.1 | 权益字段 | `items[0].equity=5000 / initial=5000 / cash=5000` |

---

## 六、端到端提交（真实入口，不是脚本代推）

脚本：`probes/21_create_run_e2e.py`；产物：`out/e2e_create_run.json`。

```
POST /api/v1/backtests  {"symbols":["BTCUSDT"],"window_start":"2026-10-10","window_end":"2026-10-12",...}
→ 202 queued  run_id=bt_20261024T020045Z_25b81923
→ poll: queued → succeeded
→ 产物目录 19 个文件齐全（含 ledger/fills/positions/trades/chart/manifest）
→ 指标 net_return_pct=0.0，trades_count=0（该 2 天窗口自然无信号，不补造）
```

同日 01:57 另有一条同规格 run `bt_20261024T015742Z_25b81923`（修复过程中验证用），两条都是研究模式、都 0 成交，**已如实留在列表里，没有删除**。

---

## 七、仍然不通过 / 未验证（不许被上面的绿掩盖）

1. **模拟盘自动成交：未验证。** 全池 526 标的、`signals_generated_total=0`；账本 0 笔已平。自然市场没信号就是没信号，不补开历史信号。
2. **正式实验可交付：不通过。** 启动闸门已能过，但 `delivery_eligible=false`（`尚无已登记运行`）。启动 ≠ 可交付，这条线没变。
3. **11 个环境冲突用例未转绿**（见第三节），在线服务不停就跑不了，属夹具互斥。
4. **2 个断言前提失效的用例留红**：`test_frame_fit_01::T07`、`test_frame_fit_02::T06::pinned_window`。要不要改这两条断言，需要你拍板（改断言=改验收契约）。
5. **`config_sha256` 被执行契约覆盖**：给了契约时代码把 `config_sha256` 直接换成契约哈希（原有写法，本轮未动）。后果是「改了 `EventTrendConfig` 参数但契约没变」时设计身份不变。本轮只记录，未改——改它等于换全部身份口径。
6. **数据中心「待补数据与核销清单」子页**仍是静态结论（「全库应补未补 0 根 / 100% 补齐」），默认页已不显示该文案。子页要不要改成实时计算，待定。
7. **回测列表的运行编号是中间省略显示**（`bt_2026102...59f3c8fa`），完整值只在 `title` 属性里；肉眼复制不到完整编号。

---

## 八、复现

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node

# 1) 失败用例（应得 2 failed / 93 passed，2 条为「前提失效」）
$PY -m pytest 框架/backend/tests/test_frame_fit_01.py 框架/backend/tests/test_frame_fit_02.py \
     框架/backend/tests/test_bt_ui_01_read_path.py 框架/backend/tests/test_bt_ui_02_chart_window.py -q

# 2) 全量（在线服务不停时 13 failed / 366 passed / 79 skipped）
$PY -m pytest 框架/backend/tests -q

# 3) 服务重启 + 版本绑定 + 端到端提交
$PY 框架/backend/service_manager.py restart --enable-paper --arm 3a --symbol all
$PY evidence/CODEX-CLOSEOUT-01/probes/21_create_run_e2e.py

# 4) 浏览器验收（应得 15/15）
$NODE evidence/CODEX-CLOSEOUT-01/probes/20_ui_acceptance.mjs
```

---

## 九、文件清单

```
probes/00_prepare_iso.py          隔离行情/账本准备（Luna 留下，未改）
probes/02_runtime_launcher.py     回放/常驻启动器（Luna 留下，未改）
probes/verify_ui_run.py           单 run 详情核验（Luna 留下，未改）
probes/20_ui_acceptance.mjs       本轮新增：浏览器 DOM↔API 验收
probes/21_create_run_e2e.py       本轮新增：真实入口提交→执行→结果
out/code_hashes_after_fix.json    修复后 10 个后端文件 SHA-256 + mtime
out/final_checks_closeout.log     全量测试最终输出
out/e2e_create_run.json           端到端提交与版本绑定
out/ui_acceptance.json            15 项页面验收明细
out/ui_shots/*.png                四页截图
out/service_restart_closeout.log  重启（含第一次失败）
out/service_restart_final2.log    最终重启
```

> 判据口径：只有**通过 / 不通过 / 未验证**三态。「页面能点开」不算通过；绿色测试单独不构成通过；本轮所有结论绑定到 API pid 35163 与 `out/code_hashes_after_fix.json` 记录的那一版磁盘代码。
