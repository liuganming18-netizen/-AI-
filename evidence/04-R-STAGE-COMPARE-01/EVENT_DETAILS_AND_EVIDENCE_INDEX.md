# 04-R 可追溯事件明细及成果索引 (报告级返工补正版)

- **工单编号**：`04-R`
- **治理版本**：`6cf7edf4d5956f4e8056351faecbd0cdd8eb8c16`
- **代码基线 HEAD**：`e986857fa23ebf5d2334863c3d551699986b6274`
- **配置指纹**：`82dec362c18230e15ccd96bf98b4f0a34d3ce4a6565eb9da86230ae53f4eed53`
- **编制日期**：2026-11-10

---

## 一、关联版本与环境凭证

| 维度 | 版本与标识 | 验证凭据与哈希 |
| :--- | :--- | :--- |
| **治理主线规范** | Git Commit `6cf7edf4...` | PR #10 候选；README.md、AGENTS.md、`docs/研究规范/策略研究与冻结验收规范.md` |
| **代码执行基线** | Git Commit `e986857fa23ebf5d2334863c3d551699986b6274` | 06-B 回测准备与 04-R 补正分支 |
| **正式策略配置** | `10_研究参数生效视图_v2_2_user5000_shared.json` | SHA256: `82dec362c18230e15ccd96bf98b4f0a34d3ce4a6565eb9da86230ae53f4eed53` |
| **数据读取基线** | 热库 `market_v2.sqlite3` + 冷档 Parquet | 已验收冻结（支持 15m/1h/4h 冷热无缝合并与真实资金费/OI 读取） |

---

## 二、承接之既有研究成果索引清单

本次研究严格继承以下既有成果，同版本同输入不再重复运行：

### 1. 全池真实数据资格成果：`evidence/Q05-REAL-DATA-QUAL-01/`
- **交付日期**：2026-11-10
- **核心结论**：完成 547 个候选区间、11,057,283 个小时起点的真实流式读取。G1~G4 输入可用，G3 压缩描述依赖完整且可评价（1,105 万起点）。因历史可得时间字段缺失，严格价格 PIT 状态客观标记为 `UNKNOWN`。
- **关键文件索引**：
  - [01_source_version_manifest_r2.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/Q05-REAL-DATA-QUAL-01/01_source_version_manifest_r2.json)
  - [README.md](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/Q05-REAL-DATA-QUAL-01/attempts/attempt_009/out/README.md)
  - [SHA256SUMS.txt](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/Q05-REAL-DATA-QUAL-01/SHA256SUMS.txt)

### 2. 早中期隔离核验成果：`evidence/ISOLATED-VERIFY-EARLY-MID-03/`
- **交付日期**：2026-11-09
- **核心结论**：19 项用例 **100% ALL_PASS**。通道严格基于索引切片消除数值误判；多轮逐根比较未来不变性；PitBridge 真实生命周期调用；三种漏报率与双基准核验通过。
- **关键文件索引**：
  - [VERIFICATION_REPORT_R3.md](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ISOLATED-VERIFY-EARLY-MID-03/out/VERIFICATION_REPORT_R3.md)
  - [verification_summary_r3.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ISOLATED-VERIFY-EARLY-MID-03/out/verification_summary_r3.json)
  - [research_adapters_r3.py](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ISOLATED-VERIFY-EARLY-MID-03/adapters/research_adapters_r3.py)

### 3. 早期提前入场平账与熔断成果：`evidence/ACCUM-EARLY-09-ANTIGRAVITY/`
- **决议日期**：2026-10-27
- **核心结论**：主代理独立终审通过并闭环。96 笔交易毛损益 -431.25 U，手续费/滑点 -57.82 U，资金费 -39.62 U，净损益 -528.69 U，勾稽残差 0.00 U；查明 48 天回放中 1,220 次自然回踩全部被阻断（自然零候选）；4b/4c 触及 20% 回撤熔断。
- **关键文件索引**：
  - [验收决议_ACCUM-EARLY-09_主代理终审闭环_2026-10-27.md](file:///Users/boos/Desktop/威科夫因子共振趋势/docs/验收决议_ACCUM-EARLY-09_主代理终审闭环_2026-10-27.md)

### 4. 缩量与保本规则统一研究成果：`evidence/RESEARCH-CLOSE-01/` 与 `evidence/BREAKEVEN-02/`
- **交付日期**：2026-10-20
- **核心结论**：主口径 201 个去重事件中，基准 A 净额 +105.44 U；入场缩量过滤 C 净差 -50.53 U（错失盈利 -77.98 U）；1R 保本 B 净差 -53.01 U。A 组真实胜率为 41.29%（83 赢 118 输），交易亏损比例 58.71%。
- **关键文件索引**：
  - [统一研究总结.md](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/RESEARCH-CLOSE-01/统一研究总结.md)
  - [paired_fixed.csv](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/BREAKEVEN-02/out/paired_fixed.csv)
  - [dedup_sensitivity.json](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/BREAKEVEN-02/out/dedup_sensitivity.json)

### 5. 末期基准分期稳定性检验成果：`evidence/BREAKEVEN-ROBUST-03/`
- **交付日期**：2026-10-19
- **核心结论**：201 笔去重事件按自然年份分期稳定性检验：2021—2023 年 -78.058873 U，2024 年 +96.492916 U，2025—2026 年 +87.005821 U，全期合计 +105.439864 U。此为自然年份稳定性检验，严禁挪用为形态三阶段收益。
- **关键文件索引**：
  - [分期统计_固定仓位.csv](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/BREAKEVEN-ROBUST-03/out/分期统计_固定仓位.csv)

### 6. 历史旧吸筹汇总复核：`evidence/ACCUM-STRAT-01/`
- **交付日期**：2026-10-19
- **核心结论**：477 笔旧吸筹代码与逐笔明细彻底缺失，复现链断裂；全池扫描同口径仅产出 2 笔 USDC 稳定币；定性为【证据不可采信或复现链缺失】，裁决淘汰作废。
- **关键文件索引**：
  - [报告.md](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-STRAT-01/报告.md)

---

## 三、成果直接引用与确实缺失清单

| 成果项 | 状态 | 直接引用依据 | 是否确实缺失 / 局限说明 |
| :--- | :---: | :--- | :--- |
| **全池 547 标的数据可用性** | **直接引用** | `Q05-REAL-DATA-QUAL-01`（1,105 万起点） | 无缺失（已证实数据可读且完整） |
| **严格历史可得时刻 (PIT)** | **标置缺失** | `Q05` 实际检查热库与冷档 | **确实缺失**：563 万条 `available_at` 为 NULL，客观标记为 UNKNOWN |
| **早期 96 笔成交账本** | **直接引用** | `ACCUM-EARLY-09` 终审报告 | 无缺失（勾稽平账残差 0.00 U） |
| **末期 201 笔去重事件账本** | **直接引用** | `RESEARCH-CLOSE-01` / `BREAKEVEN-02` | 无缺失（基准净额 +105.44 U，真实胜率 41.29%） |
| **末期年份分组稳定性** | **直接引用** | `BREAKEVEN-ROBUST-03` | 无缺失（2021-23: -78.06 U, 2024: +96.49 U, 2025-26: +87.01 U） |
| **早中期隔离算法核验** | **直接引用** | `ISOLATED-VERIFY-EARLY-MID-03` | 无缺失（19 项单测 ALL_PASS） |
| **中期全池 547 标的实际收益** | **标置未评估** | 尚未经第 06 阶段回测程序撮合 | **未评估**：交第 06 阶段严格样本外回测处理 |
| **逐事件提前量/漏报/等待/MAE** | **标置未核实** | 既有事件库中缺乏对应逐事件原始列 | **未核实**：诚实标为未核实，不新增实验凑数 |
| **477 笔旧吸筹逐笔明细** | **证据不可采信** | `ACCUM-STRAT-01` 全盘搜索核对 | **复现链断裂**：历史环境未留存代码与逐笔表，已淘汰作废 |
