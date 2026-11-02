# 工单 GM-BACKTEST-OI-BUCKET-CAUSAL-FIX-38 交付报告

## 一、工单基本信息
- **工单编号**：`GM-BACKTEST-OI-BUCKET-CAUSAL-FIX-38`
- **优先级**：P0
- **执行角色**：WorkBuddy B
- **执行环境**：隔离工作树 `/private/tmp/gm-backtest-oi-bucket-causal-fix-38/wt`
- **分支名称**：`candidate/gm-backtest-oi-bucket-causal-fix-38`
- **修改文件**：仅 `框架/backend/backtest_runner.py`（单文件最小改动）
- **核心目标**：
  1. 5m OI 必须先闭合再被决策使用，要求 `bucket_end <= decision_time`；
  2. 真实 `available_at` 缺失或晚于决策时间时必须保留 `PIT_UNPROVEN`，不得凭 `bucket_end` 升级为可信；
  3. 严密覆盖边界时刻、缺失时间、回填数据、无匹配桶和既有回归。
- **停手点**：隔离测试通过后停止；不自行集成、合并或部署。

---

## 二、执行闸核对
```
本轮授权：仅在隔离工作树修改 backtest_runner.py 的 OI 对齐逻辑，不修改策略参数、门禁、接线模块或生产数据。
不碰：策略参数、门禁、接线模块、生产数据，以及授权范围外的文件。不自行集成、合并或部署。
完成判据：在隔离工作树中完成 backtest_runner.py 的 OI 对齐逻辑修复（5m OI 必须先闭合再被决策使用，要求 bucket_end<=decision_time；真实 available_at 缺失或晚于决策时间时保留 PIT_UNPROVEN，不得凭 bucket_end 升级为可信），通过覆盖边界时刻、缺失时间、回填数据、无匹配桶和既有回归的隔离测试，交付最小补丁、前后哈希、测试、证据目录和 manifest。
停手点：隔离测试通过后停止；不自行集成、合并或部署。
```

---

## 三、文件前后哈希对比

| 文件路径 | 修改前 SHA256 | 修改后 SHA256 | 变更行数 |
| :--- | :--- | :--- | :--- |
| `框架/backend/backtest_runner.py` | `7e955abeecfcd1ca096ac53cc43704b2e9c790a87ec01da46c62c44123edb89b` | `1928d551fe84b764165d03cb60e1f5b0de1a006a2e97ffcfe2b1b61abe90af87` | +101 / -15 |

---

## 四、核心修复机制与因果契约

### 1. 5m 桶先闭合再使用（`bucket_end <= decision_time`）
- **原代码前视漏洞定性**：
  原实现采用 `pos = np.searchsorted(oi_ts, close_ms, side="right") - 1`。当信号 bar 收盘在整点（如 04:00:00 时），若刚好存在一条 `oi_ts == 04:00:00` 的 5m 桶，旧逻辑恰好选中该桶。但该桶聚合的是 04:00:00~04:05:00 的区间持仓，在 04:00:00 决策时刻尚未闭合，属于未来数据前视泄露。
- **新因果闭包实现**：
  引入标准 5m 统计周期 `BUCKET_5M_MS = 300_000`，明确推导桶结束时刻 `oi_bucket_ends = oi_ts + BUCKET_5M_MS`。查找逻辑改为针对闭合时刻对齐：
  `pos = np.searchsorted(oi_bucket_ends, close_ms, side="right") - 1`
  严格要求 `p >= 0 and oi_bucket_ends[p] <= dec_t`，确保被决策使用的 5m 桶已在时序轴上完整闭合走完。

### 2. PIT_UNPROVEN 状态严密保全（不得凭 bucket_end 升级为可信）
- **时效证据分级约束**：
  针对对齐选中的闭合桶，抽取原始真实物理字段 `available_at`、`fetched_at` 与 `source`：
  1. **缺失到达时间 (`available_at_missing`)**：若行中缺少真实到达时戳（冷档 Parquet 100% 缺失），**严禁凭借推断的 `bucket_end` 升级为可信**，必须保留标记为 `PIT_UNPROVEN`；
  2. **到达晚于决策 (`available_at_after_decision`)**：若网络传输或发布时延导致 `available_at > decision_time`，该数据在决策当刻尚不可得，标记为 `PIT_UNPROVEN`；
  3. **离线回填数据 (`backfilled_data_unproven`)**：若来源标为 `archive`、`batch` 或 `#gapfill`，无法证明历史实时到达时刻，标记为 `PIT_UNPROVEN`；
  4. **到达早于闭合 (`available_at_before_bucket_end`)**：到达时间早于桶闭合违背物理因果律，标记为 `PIT_UNPROVEN`；
  5. **只有真实可信到达**（`bucket_end <= available_at <= decision_time` 且非回填数据）才允许判定单 bar 为 `oi_pit_proven=True`；
  6. 只要全序列中存在未证明 bar，整体 `pit_status` 严格锁定为 `PIT_UNPROVEN`。

---

## 五、测试验证结论

在隔离工作树中运行 6 个模块共 **224 项单元测试，100.0% 全部通过（0 失败，0 错误）**：
1. **工单 38 专属专项测试集** (`test_backtest_oi_bucket_causal_38.py`，10/10 PASS)：
   - `test_01`: 边界时刻恰好闭合（`bucket_end == decision_time`）正确匹配闭合桶；
   - `test_02`: 边界时刻未闭合桶（`ts == decision_time`）严格排除未来桶，杜绝前视；
   - `test_03`: `available_at` 缺失时数值可用但严格保留 `PIT_UNPROVEN`；
   - `test_04`: `available_at` 晚于决策时刻严格保留 `PIT_UNPROVEN`；
   - `test_05`: 回填数据（archive/batch/gapfill）严格保留 `PIT_UNPROVEN`；
   - `test_06`: 无匹配闭合桶时严格返回 `NaN`（绝不填 0 或编造）；
   - `test_07`: 空数据集安全防御；
   - `test_08`: 完备真实到达样本正确评定为 `PIT_PROVEN`；
   - `test_09`: 多 bar 连续步进对齐严格满足因果律；
   - `test_10`: runner 源码整洁性守护，绝无门禁或 severity 侵入。
2. **数据门禁三态回归测试** (`test_backtest_data_gate_17.py`, `27.py`, `31.py`，134/134 PASS)。
3. **回测执行链回归测试** (`test_frame_fit_01.py`, `02.py`，80/80 PASS)。

---

## 六、交付物清单与目录结构

```
evidence/GM-BACKTEST-OI-BUCKET-CAUSAL-FIX-38/
├── README.md                           # 交付报告（本文件）
├── patch/
│   └── 01_oi_bucket_causal_fix.patch   # 最小补丁文件
├── scripts/
│   └── 01_test_oi_bucket_causal.py     # 自动化因果与 PIT 回归测试脚本
├── logs/
│   └── test_run.log                    # 224 项测试完整通过日志
├── out/
│   └── causal_fix_report.json          # 结构化交付报告
├── manifest.json                       # 交付物元数据清单
└── sha256sum.txt                       # SHA256 校验清单
```

---

## 七、停手点与边界纪律宣告
1. 隔离工作树验证通过后立即停手，未自行向主分支发起合并（`git merge`）；
2. 策略参数（止损、追踪、阈值等）0 修改；
3. 门禁契约与接线模块 0 修改；
4. 生产数据库与日志 0 修改；
5. 生产后台服务 0 重启；
6. 严格等待主代理验收裁决。
