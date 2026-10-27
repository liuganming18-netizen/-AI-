# 真实候选隔离入口（供反重力直接复用）

> 本文件是 `SIGNAL-LIVE-CHECK-09` 要求 5 的交付物。
> **复用 `SIGNAL-UI-LINK-07-R1` 已经造好的 live / recovered 两份样例，本单没有另造候选。**

---

## 一、一句话用法

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-LIVE-CHECK-09/probes

# 起（端口 8799）
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 40_iso_serve.py --mode live --start

# 查（进程 + 接口 + 候选四项）
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 40_iso_serve.py --mode live --status

# 停
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 40_iso_serve.py --mode live --stop
```

把 `--mode live` 换成 `--mode recovered` 就是重启恢复那条分支（端口 8798）。

---

## 二、六个必填项

### 1. 启动方法

| 项 | 值 |
|---|---|
| 启动命令 | `python 40_iso_serve.py --mode live --start` |
| 启动内核 | `evidence/SIGNAL-UI-LINK-07-R1/probes/15_iso_api_launcher.py`（**只改进程内存**里的启用时点） |
| 解释器 | `/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python` |
| 工作目录 | `框架/backend`（脚本内部已 `cwd` 过去） |
| 就绪判据 | `GET /api/v1/health` 返回 200，脚本会自己等到就绪（上限 180 秒） |
| 典型就绪耗时 | 3.1 秒（live）/ 3.4 秒（recovered），实测 |
| PID 文件 | `evidence/SIGNAL-LIVE-CHECK-09/out/40_iso_<mode>.pid` |
| 日志 | `evidence/SIGNAL-LIVE-CHECK-09/out/40_iso_<mode>.log` |

**前置条件**：两份隔离库必须存在。若被删了，用下面命令重新生成（约 100 秒/份）：

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-LINK-07-R1/probes
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 40_two_paths.py --mode live
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 40_two_paths.py --mode recovered
```

### 2. 端口

| mode | 端口 | 用途 |
|---|---|---|
| `live` | **8799** | 新产生候选（`origin=live`） |
| `recovered` | **8798** | 重启恢复候选（`origin=recovered`） |

**避开**：生产 API `8787`、前端 `3000`、重复入口 `3100`（别用）。

### 3. 候选 ID（两条路径同一个）

```
cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000
```

| 字段 | 值 |
|---|---|
| 币种 | `VELVETUSDT` |
| 方向 | `long` |
| 确认时间 | `2026-10-02T15:00:00Z`（毫秒 `1790953200000`） |
| 可执行状态 | `enterable` |
| 来源 | live 库 `origin=live`；recovered 库 `origin=recovered` |

### 4. 数据库位置

| mode | LIVE_DB | 账本 | 锁 |
|---|---|---|---|
| `live` | `evidence/SIGNAL-UI-LINK-07-R1/iso_live/live_signals.sqlite3` | `…/iso_live/paper/` | `…/iso_live/locks/` |
| `recovered` | `evidence/SIGNAL-UI-LINK-07-R1/iso_recovered/live_signals.sqlite3` | `…/iso_recovered/paper/` | `…/iso_recovered/locks/` |

两份库各 **1 行** `live_signal`，`paper_epoch_ms` / `live_start_ms` 均为 `1790870400000`（2026-10-01T16:00:00Z）。

**行情库**：`数据/市场数据/db/market_v2.sqlite3`（只读；`sync_dataset_bars_upto` 在探针进程内为 no-op）。

### 5. 版本指纹

**隔离入口本身**（本单新增，改动它要同步本文件）：

| 文件 | 字节 | sha256 |
|---|---|---|
| `probes/40_iso_serve.py` | 见 `ls` | 见下「怎么自查」 |
| `evidence/SIGNAL-UI-LINK-07-R1/probes/15_iso_api_launcher.py` | 1,714 | 见 `out/20_version_check.json` |

**后端三条路径 + 交付文件**（本单实测，与 07-R1 交付一致）：

| 角色 | 文件 | 字节 | sha256（前 16 位） |
|---|---|---|---|
| 信号生成 | `框架/backend/live_signal_engine.py` | 38,373 | `d3b10ccd230d510c…` |
| 入库（持久化） | `框架/backend/unified_runtime.py` | 135,918 | `a8cb626a0646c581…` |
| 接口（实时层） | `框架/backend/live_api.py` | 113,929 | `312ecf9cfdd4329b…` |
| 接口（合并层） | `框架/backend/adapter.py` | 215,744 | `6218416fae5f6dc3…` |
| 前端（状态分类器） | `UI界面/web/src/api/signalsState.ts` | 7,008 | `f2d408931c502053…` |
| 前端（拉取信号） | `UI界面/web/src/api/useMarketData.ts` | 56,031 | `b1289ce6a2aa8d38…` |
| 前端（信号面板） | `UI界面/web/src/components/SignalListPanel.tsx` | 46,388 | `bf8eb3ce8df12718…` |
| 前端（入口） | `UI界面/web/src/App.tsx` | 21,206 | `07aefc6b8f82a8a7…` |

**守卫文件**（不得变）：

| 文件 | sha256（前 16 位） |
|---|---|
| `evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md` | `1f1167de35412cb7…` |
| `框架/backend/live/paper_new_entry_gate.json` | `f4691db2c7f66c81…` |
| `框架/backend/live_store.py` | `1e1218ea1bdf18d5…` |

**运行身份**（本单核对时刻 2026-10-25T11:50Z）：API pid `38655` 启动 `11:46:35Z`，Runtime pid `38664` 启动 `11:46:38Z`，采集器 pid `42278`（未动）。

**怎么自查**：
```bash
cd /Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-LIVE-CHECK-09/probes
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 20_version_check.py
# 会逐项比对 07-R1 交付指纹、后端文件 mtime vs 进程启动时刻、守卫文件 sha
```

### 6. 清理方法

```bash
# 停进程（按端口找 pid，SIGTERM → 等 20s → SIGKILL）
python 40_iso_serve.py --mode live --stop
python 40_iso_serve.py --mode recovered --stop

# 删本入口产生的 pid / log（**不动隔离库**，那是证据）
python 40_iso_serve.py --mode live --clean
```

**隔离库与账本默认不删**。确需彻底删除时（会丢掉证据，自己确认）：

```bash
rm -rf '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-LINK-07-R1/iso_live'
rm -rf '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-LINK-07-R1/iso_recovered'
```

删除后要重新造，用第二节的 `40_two_paths.py`。

---

## 三、验收断言（起完就跑这几条）

```bash
# 1. 接口层四项字段
curl -s --noproxy '*' 'http://127.0.0.1:8799/api/v1/signals?scope=current&limit=500'
#   期望：total=1，items[0].signal_id == 上面的候选 ID
#        quality.status == "ok"，missing_reason == null
#        quality.effective_floor_ms == 1790870400000

# 2. 库层
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python - <<'PY'
import sqlite3
p='evidence/SIGNAL-UI-LINK-07-R1/iso_live/live_signals.sqlite3'
con=sqlite3.connect(f'file:{p}?mode=ro',uri=True)
print(list(con.execute('SELECT signal_uid,symbol,direction,origin,executable_state,confirmed_at FROM live_signal')))
PY

# 3. 生产没被影响
curl -s --noproxy '*' 'http://127.0.0.1:8787/api/v1/signals?scope=current&limit=5'
#   期望：quality.paper_epoch_ms == 1791939600000（仍是 2026-10-14T01:00Z）
```

**必须三条一起看**。只看接口 200、只看页面能开、只看截图，都不构成通过。

---

## 四、版本变化记录（要求 6）

> 反重力正在验证前端文件。任何改动都会在这里先登记，避免测试与代码版本错配。

| 时刻（UTC） | 文件 | 变化 | 影响反重力验证吗 |
|---|---|---|---|
| 2026-10-25T11:46Z（本单） | **无前端改动** | 本单到目前为止**没有修改任何前端文件**；只重启了后端进程（让 `adapter.py` 的既有改动进进程） | 否 |
| 2026-10-25T12:00Z–12:20Z（本单） | **仍无前端改动** | 本单新增的 `probes/30_iso_replay.py`、`50_analyze_samples.py`、`60_changed_symbols.py` 全部在 `evidence/` 下，**不参与前端构建**；四个前端文件 sha 仍为 `f2d40893…`/`b1289ce6…`/`bf8eb3ce…`/`07aefc6b…` | 否 |
| **2026-10-25T13:21:41Z（外部改动，非本单）** | `UI界面/web/src/components/SignalListPanel.tsx` | sha `bf8eb3ce8df12718…` → **`546fdab0d66e98c0…`**；字节 46,388 → 47,253（+865）。新增「模拟盘启停切换」按钮（`onToggleGate` / `isGateRunning` / `Play` / `Pause`）与「清理信号库」按钮（`onClearSignals` / `Trash2`），并引入 `ExecutionDaemonView` 类型 | **是**。若反重力的测试跑在 13:21Z 之前，结论对应旧版本，需重跑；vite HMR 会让新代码立即生效，页面不用重启 |

> **这一行是本单按「要求 6」做的动作**：本单自己**没有**改任何前端文件（12:15Z 核对的四个 sha 全部等于 07-R1 交付值），
> 但 13:29Z 的复核发现上面这个文件被**另一个并行任务**改了。按要求 6 的口径，先把版本变化登记在这里，
> 再谈影响——不登记就没人知道反重力测的是哪一版。**本单没有回滚它**：那是在途工作，回滚会破坏别人。
>
> 证据：`out/23_frontend_drift_1321Z.json`（含检测时刻、前后 sha、改动性质判断、影响面）。

**登记纪律**：反重力正在验证前端文件。任何前端改动必须在**动手之前**加到上表；
反过来，上表说「无改动」也要能被核——用 `probes/20_version_check.py` 跑一次，
它会拿四个前端文件的 sha 去比 07-R1 交付登记值，不一致就报错。

**说明**：本单开工时发现 `adapter.py` 的 mtime（`11:05:00Z`）晚于 API 进程启动时刻（`11:03:51Z`），
即「文件改过但进程还跑着旧代码」。虽然文件内容 sha 与 07-R1 交付一致（`6218416f…`），
但为了让「运行版本 = 交付版本」这条结论站得住，本单在 `11:46Z` 做了一次受控重启：
API `32935 → 38655`、Worker `32940 → 38660`、Runtime `32944 → 38664`，**采集器 42278 未动**。
重启后 `state_counter` 与重启前逐项相同（`IDLE 192 / BOX 300 / RETEST 16 / SWEEP 18`）。

---

## 五、这套入口证不了什么（别误读）

- 它证明的是：**隔离环境里，一个真实产生的候选能从引擎一路走到接口**，且四项字段可核对。
- 它**不能**证明：生产「有自然候选时页面能显示」。生产当前 0 条是规则决定的，
  要等自然候选出现才能说这句话通过。
- 它**不能**证明：真实挂钟自动成交。那需要自然候选 + 15m 成交窗口，不在本入口范围。
- 隔离库的 `paper_epoch_ms` 是**模拟值** `1790870400000`（2026-10-01T16:00:00Z），
  只在隔离进程内存生效。生产真实值是 `1791939600000`（2026-10-14T01:00:00Z），**没有改**。
