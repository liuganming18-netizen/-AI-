# FRAME-VERIFY-02｜02. FLOW-OI 实验入口绕过 PIT 独立实测报告

## 一、测试背景与要求

工单明确要求：
> **FLOW-OI实验入口是否仍绕过PIT**  
> 当前 `generate_flow_oi_01.py` 中仍可见：  
> `c_pit = df_c[df_c["open_time"] <= ts]`  
> `m_pit = df_m[df_m["ts"] <= ts]`  
> 必须通过实际因子提取入口注入未收盘K线、未结束桶及延迟发布数据，检查是否拒绝使用、标记不具正式资格，以及是否仍能生成看似合格的特征。  
> 不能用 `features.extract_real_oi_growth` 的通过结果代替 FLOW-OI四组实验入口验证。  
> 保留旧研究产物；如实现方提供新版受控入口，核对实际实验是否已指向新版，旧入口是否明确失效，不能仅凭旧脚本仍存在判失败。

---

## 二、真实入口注入验证过程

### 1. 被测真实入口与调用命令
- **实际被测入口**：`evidence/FLOW-OI-01/generate_flow_oi_01.py::extract_features_for_candidate`
- **实现方有无提供新版受控入口**：经核对 `evidence/FRAME-PIT-01/` 全量文件（包括 `03_实际调用链.md` 与 `06_未覆盖路径与防护边界.md`），实现方**未对 FLOW-OI 因子提取提供任何新版受控脚本**，亦未废弃或重定向该入口。实现方在 `06_未覆盖路径与防护边界.md` 明确声明：“同进程仍可直接读文件……本单范围是 WX_EVENT_TREND_V0_1 与数据链，没有改动其他脚本”。
- **测试命令**：
  ```bash
  /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python -c "
  import importlib.util, pandas as pd
  spec = importlib.util.spec_from_file_location('flow_mod', 'evidence/FLOW-OI-01/generate_flow_oi_01.py')
  flow_mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(flow_mod)
  ... # 注入未收盘蜡烛与未结束桶
  res = flow_mod.extract_features_for_candidate(row)
  "
  ```

### 2. 注入反例数据设计 (Counter-example Injection)
- **决策时刻**：整点 `ts = 1600000000000` (2020-09-13 12:00:00 UTC)；
- **未收盘 K 线注入**：
  注入一根 `open_time = 1600000000000`、`close_time = 1600003599999`（覆盖 12:00:00 ~ 12:59:59）的蜡烛，`is_closed = 0`，内含极端未来成交量 `volume = 8888888.0`、极端未来收盘价 `close = 148.0`（比开盘价 102.0 暴涨 45.1%）；
- **未结束 5m Metrics 桶注入**：
  注入一个 `ts = 1600000000000` 的 5m Metrics 桶（实际覆盖 12:00:00 ~ 12:05:00），包含 `sum_open_interest = 99999.0`。

---

## 三、实际执行结果与终端原始输出

```text
Successfully imported generate_flow_oi_01
=== 实际提取结果 ===
coverage_status: COMPLETE
bar_open: 102.0
bar_close: 148.0
bar_volume: 8888888.0
bar_return: 0.450980392112649
oi_base: 99999.0
has_c1h: True
has_metrics: True
has_oi: True
pit_proven in res: False
qualified in res: False
```

---

## 四、技术分析与定性结论

| 检验项 | 受控框架预期防御行为 (Expected) | 实际入口行为 (Actual) | 结论判定 |
| :--- | :--- | :--- | :---: |
| **未收盘 1h K 线** | 抛出 `PitViolation` 或忽略未收盘蜡烛，只读决策时刻前已收盘蜡烛 | **100% 泄露**：读入未来蜡烛，成交量 `8888888.0` 和暴涨收盘价 `148.0` 全被吃入 | **FAIL** |
| **未结束 5m 桶** | 拒绝匹配决策时刻正开始的桶，前移一桶读取 `[ts-5m, ts)` | **100% 泄露**：直接读入覆盖未来 5 分钟的未结束桶（OI=99999.0） | **FAIL** |
| **数据覆盖状态标记** | 标注 `UNPROVEN`、`LEAKAGE` 或拒绝赋予正式资格 | **虚假完备**：依然硬编码标记为 `coverage_status = "COMPLETE"` | **FAIL** |
| **资格标识字段** | 产物输出包含显式资格字段（`pit_proven: False`, `qualified: False`） | **完全缺失**：特征字典内根本不存在 `pit_proven` 或 `qualified` 字段 | **FAIL** |

### 归属判定与最小整改要求：
- **责任归属**：**`FRAME-PIT-01`**
- **失败原因**：`FRAME-PIT-01` 仅在 `event_trend_v0_1` 策略内部（`pit_bridge.py`）接入了 K 线闭合闸，而将**真实生成 FLOW-OI 四组实验特征的入口 `evidence/FLOW-OI-01/generate_flow_oi_01.py` 完全遗漏**。
- **最小整改要求**：
  1. 将 `generate_flow_oi_01.py` 中的 K 线切片修改为严格已收盘：`c_pit = df_c[df_c["open_time"] + 3600000 <= ts]`（或 `close_time < ts`）；
  2. 将 5m Metrics 切片修改为已结束桶：`m_pit = df_m[df_m["ts"] + 300000 <= ts]`；
  3. 引入 `pit_guard` 的 `assert_candles_closed` 与 `available_at` 校验，输出字典中强制增加 `pit_proven` 与 `formal_eligible` 状态，严禁无条件标记 `coverage_status: COMPLETE`。
