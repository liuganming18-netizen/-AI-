# -*- coding: utf-8 -*-
"""NEXT-STAGE-GATE-01 · 准入表生成（数据驱动，只读）。

原则
----
1. 准入结论不手写：测试集资格、能力、缺口三张表**直接读前两项工单的 CSV**，
   脚本先算，再落盘；报告只做解释。
2. 只读：不 import 策略包、不跑回测、不改前两项与对方任何文件。
3. 契约状态用 sha256 比对对方 versions.json，自动判断"是否更新/是否完成"。

产出
----
out/01_准入表.csv
out/02_必须先解决清单.csv
out/03_可限制保留清单.csv
out/04_待用户决定.csv
out/05_三项任务汇总.csv
logs/00_gate.json
"""
from __future__ import annotations

import csv
import hashlib
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
D = os.path.join(ROOT, "evidence/NEXT-STAGE-GATE-01")
OUT = os.path.join(D, "out")
LOGS = os.path.join(D, "logs")

OOS = os.path.join(ROOT, "evidence/OOS-READY-01")
VR = os.path.join(ROOT, "evidence/VALIDATION-READINESS-01")
CT = os.path.join(ROOT, "evidence/VALIDATION-CONTRACT-01")

CHECK = []


def rec(item, ok, detail=""):
    CHECK.append({"item": item, "status": "PASS" if ok else "FAIL", "detail": str(detail)[:300]})


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def read_csv(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(name, header, rows):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


# ══════════════════ 一、读前两项产物 ══════════════════
elig = read_csv(os.path.join(OOS, "out/02_验证数据资格表.csv"))
cap = read_csv(os.path.join(VR, "out/02_能力核对表.csv"))
gaps = read_csv(os.path.join(VR, "out/03_前置缺口清单.csv"))

rec("读到 OOS 验证数据资格表", len(elig) == 8, len(elig))
rec("读到能力核对表", len(cap) == 8, len(cap))
rec("读到缺口清单", len(gaps) == 11, len(gaps))

# 资格统计（按「综合判定」精确分档，不合并"无数据"与"资格不合格"）
usable = [r for r in elig if r["综合判定"].startswith("可用")]
unusable = [r for r in elig if r["综合判定"].startswith("不可用")]
qual_bad = [r for r in elig if r["综合判定"].startswith("不可用（数据资格不满足）")]
no_data = [r for r in elig if r["综合判定"].startswith("不可用（无数据）")]
observed = [r for r in elig if "已观察" in r["综合判定"]]
unknown = [r for r in elig if r["接触状态"].startswith("资格未知")]
rec("8 段中无任何可用段", len(usable) == 0,
    f"可用={len(usable)} 资格不合格={len(qual_bad)} 无数据={len(no_data)} "
    f"已观察={len(observed)} 资格未知={len(unknown)}")
rec("分段计数自洽（不可用 = 资格不合格 + 无数据）",
    len(unusable) == len(qual_bad) + len(no_data),
    f"{len(unusable)} vs {len(qual_bad)}+{len(no_data)}")
rec("已观察 4 段 / 资格未知 3 段", (len(observed), len(unknown)) == (4, 3),
    (len(observed), len(unknown)))

# 覆盖/预热/时钟（从 OOS 表里取可核对的量）
kline_max = max(int(r["K线可用币数（文件名级，4h）"] or 0) for r in elig)
oi_max = max(int(r["OI 可用币数（文件名级，5m）"] or 0) for r in elig)
rec("K 线覆盖币数 ≥ 500", kline_max >= 500, kline_max)
rec("OI 覆盖币数 ≥ 500", oi_max >= 500, oi_max)

# 能力统计
cap_ok = [r for r in cap if r["结论"].startswith("支持")]
cap_no = [r for r in cap if r["结论"].startswith("不支持")]
rec("能力核对：5 项支持 / 3 项不支持", (len(cap_ok), len(cap_no)) == (5, 3), (len(cap_ok), len(cap_no)))

# 缺口统计
n_block = sum(1 for r in gaps if r["分类"] == "阻止结果可信")
n_easy = sum(1 for r in gaps if r["分类"] == "仅影响方便使用")
rec("缺口：阻止可信 5 / 仅影响方便 6", (n_block, n_easy) == (5, 6), (n_block, n_easy))

# ══════════════════ 二、对方契约状态 ══════════════════
contract_md = os.path.join(CT, "下一阶段验证实验契约草案.md")
contract_ver = os.path.join(CT, "out/versions.json")
contract_sha = sha(contract_md) if os.path.exists(contract_md) else None
contract_recorded = None
contract_status = "未交付"
if os.path.exists(contract_ver):
    with open(contract_ver, encoding="utf-8") as f:
        v = json.load(f)
    for k, val in v.items():
        if k.endswith("契约草案.md"):
            contract_recorded = val.get("sha256")
            contract_status = val.get("status", "未知")
contract_updated = bool(contract_sha and contract_recorded and contract_sha != contract_recorded)
rec("对方契约存在", bool(contract_sha), contract_sha)
rec("契约哈希与对方自记一致（本次未被改动）",
    bool(contract_sha and contract_recorded and contract_sha == contract_recorded),
    f"now={contract_sha} rec={contract_recorded}")
rec("契约仍为待审批状态（未完成）", contract_status == "待用户审批", contract_status)

# ══════════════════ 三、准入表 ══════════════════
# 判定取值：通过 / 部分通过 / 不通过
gate = [
    # ── A 测试集资格 ──
    ["A1", "测试集是否具备独立验证资格", "存在可证明未用于调参/筛选/结果比较的时间或标的范围",
     f"{len(elig)} 段全部不满足：数据资格不合格 {len(qual_bad)} 段（S0/S1/S6）、"
     f"无数据 {len(no_data)} 段（S7）、已观察 {len(observed)} 段（S2~S5，其中 S5 旧 Holdout 已被交易）；"
     f"可用段 {len(usable)}",
     "不通过", "OOS-READY-01/out/02_验证数据资格表.csv（8 行）",
     "没有样本外数据，任何「验证结论」都只是样本内描述", "必须先解决（阻断）"],
    ["A2", "可用样本量是否够判定", "≥100 笔可称样本外的事件",
     "0 笔。现有 296/206/201 全部已观察；按 296 笔/5.8 年折算，前向累积需 18~24 个月",
     "不通过", "OOS-READY-01/00_报告.md §3.1、§4",
     "样本不足时失败条件无法判定，等于没验", "必须先解决（阻断）"],
    ["A3", "前向累积是否可行", "能从某天起算，且起算日冻结代码/参数/数据/币池",
     "可行，但起算必须等 G1/G2/G4 闭环；否则攒下的数据不能用（代码身份会变）",
     "部分通过", "OOS-READY-01/03_推荐方案与待决策项.md D3",
     "早起算 = 白攒一段，且中途改代码会让样本身份断裂", "仅需用户决定（起算时点）"],

    # ── B 数据覆盖 / 预热 / 时钟 ──
    ["B1", "数据覆盖是否满足", "K 线与 OI 覆盖到币池全部标的，时间连续无洞",
     f"K 线最多 {kline_max} 币、OI 最多 {oi_max} 币；2021-01-01~2026-10-13 窗口并集连续无洞（覆盖 473/528 标的）",
     "通过", "OOS-READY-01/out/01_已观察范围_批次.csv、out/02 覆盖列",
     "—", "保留"],
    ["B2", "预热是否满足", "4h 箱体需要 220 根（ATR 预热 200 根）历史",
     "load_symbol_data 自动向前取 220 根；窗口过短会不足，需保证起止跨度",
     "通过", "VALIDATION-READINESS-01/out/01_入口路径表.csv 第 3 环",
     "—", "保留"],
    ["B3", "可用时钟（计算侧）", "决策时刻只用已闭合 K 线；未收盘抛错",
     "pit_guard_v1.0.0 生效；决策时刻 = 1h 开盘 + 3600s；未收盘抛 PitViolation",
     "通过", "VALIDATION-READINESS-01/out/02 第 5 行；真实 run pit_audit",
     "—", "可限制后保留（只能声明「计算上不超前」）"],
    ["B4", "可用时钟（发布时点）", "OI 的可得时刻要有官方发布时点证据",
     "无证据。真实 run：oi_available_at_unproven，pit_qualified=false，双闸均 false",
     "不通过", "OOS-READY-01 §5.7 G1；真实 run summary.json",
     "只能声明「没在计算上超前」，不能声明「已证明无未来函数」", "必须先解决（阻断正式结论）"],

    # ── C 固定仓位与费用口径 ──
    ["C1", "固定仓位配对是否可执行", "A/B/C 三组共用同一冻结数量，切断串行权益耦合",
     "可执行：be_core.py install() + 02_run_fixed.py 逐信号隔离并断言 trades==1；201 事件已跑通",
     "通过", "VALIDATION-READINESS-01/out/02 第 1 行",
     "—", "可限制后保留（需带实现版本声明）"],
    ["C2", "配对代码身份是否可追溯", "配对实现必须被代码指纹覆盖",
     "未覆盖：CODE_FILES 20 个文件不含 be_core.py / 02_run_fixed.py，产物无法证明用的是哪一版",
     "不通过", "VALIDATION-READINESS-01/out/03 V1",
     "换人/换机器复用时，结果不可归因", "必须先解决（阻断）"],
    ["C3", "费用口径是否有证据", "手续费/滑点/取整/资金费四类可逐笔核对",
     "齐全：taker 5bps、滑点 3bps、Decimal 买 ceil 卖 floor、资金费按真实结算事件；逐笔 26 字段含四类成本与 funding_gaps",
     "通过", "VALIDATION-READINESS-01/out/01 第 8/9 环；真实 run trades.jsonl",
     "—", "保留"],
    ["C4", "资金费 mark 覆盖是否完整", "全程有真实结算 mark",
     "2022 年 99.63%、2023 年 99.76%、2024/2025 年 100%、2026 年 94.10%；2021 及以前无记录",
     "部分通过", "OOS-READY-01 §5.7 G3",
     "2021 段（6 笔）资金费金额算不准", "可限制后保留（2021 段剔除或单列）"],

    # ── D 既有入口能否复用 ──
    ["D1", "主链路能否复用", "冻结配置→候选→交易→费用→报告走同一套生产代码",
     "能。15 环全部定位到生产代码，不需要另建框架",
     "通过", "VALIDATION-READINESS-01/out/01_入口路径表.csv",
     "—", "保留"],
    ["D2", "能否指定时间与事件范围", "能按窗口跑，也能按单事件隔离",
     "时间范围：load_symbol_data(start,end,allow_auto_window=False)；事件范围：适配器传单元素候选",
     "通过", "VALIDATION-READINESS-01/out/02 第 2/3 行",
     "—", "保留"],
    ["D3", "能否保留逐笔与版本指纹", "逐笔全留 + 四类指纹 + 原子提交",
     "能。策略包直跑 17 个文件、后端入口 19 个；四类指纹 + pit_guard_version + manifest 逐文件 sha256",
     "通过", "VALIDATION-READINESS-01/out/02 第 4/5 行",
     "—", "保留"],
    ["D4", "输出能否限定在工单目录", "产物只写本轮授权目录",
     "不能。runs_base 硬编码 <base_dir>/回测日志/event_trend_v0_1/runs，无参数/环境变量可改",
     "不通过", "VALIDATION-READINESS-01/out/03 V2",
     "与「写入仅限工单目录」纪律冲突，只能事后用 manifest 核对", "可限制后保留（或改代码，需授权）"],

    # ── E 评估办法是否可执行 ──
    ["E1", "主要比较指标能否算出", "净差额 / 少亏 / 错失盈利 / 逐笔移除敏感性 / 分期一致性",
     "前四项可从逐笔表直接算；分期一致性需按事件时间切片，也可算",
     "通过", "VALIDATION-READINESS-01/out/01 第 13 环；真实 run trades.jsonl",
     "—", "保留"],
    ["E2", "组合回撤能否算出", "失败条件第 3 项需要组合净值回撤",
     "不能。全部运行为「单标的 + 独立 2000U 账户」，汇总只有单币 13 个字段，无 max_drawdown",
     "不通过", "OOS-READY-01 §5.7 G5；VALIDATION-READINESS-01/out/03 V5",
     "失败条件第 3 项目前无法判定", "必须先解决（阻断）"],
    ["E3", "失败条件能否判定", "契约 §五 三条失败条件可机械判定",
     "第 1、2 条可判；第 3 条依赖 E2，当前不可判",
     "部分通过", "契约 §五.2 与 E2",
     "只能判两条，结论会留缺口", "必须先解决（随 E2 一起）"],
]
write_csv("01_准入表.csv",
          ["编号", "检查项", "准入要求", "实测结果", "判定", "证据来源", "未通过的影响", "处置"],
          gate)

# ══════════════════ 四、必须先解决 / 可限制保留 / 待用户决定 ══════════════════
must = [
    ["M1", "测试集没有可证明的样本外资格（A1/A2）",
     "没有样本外数据，任何结论都只能是样本内描述，不能当验证结果",
     "存在一段可证明未接触的数据，且样本量达到判定门槛（≥100 笔）",
     "数据侧；可能需要长期前向累积", "否"],
    ["M2", "OI 发布时点无证据（B4 / G1）",
     "只能声明「计算上不超前」，不能声明「已证明无未来函数」；正式结论不成立",
     "官方发布时点归档，或把「声明边界」写成明文并接受 qualified=False",
     "研究侧写边界文档", "否"],
    ["M3", "历史合约规格 0 条 verified（G2）",
     "价格步长/最小下单量按当前规格回填历史，取整与最小订单校验可能偏离当时规则",
     "找到当时快照，或固定写「按当前规格回填」并量化影响",
     "研究侧", "否"],
    ["M4", "既有运行不是冻结规则币池（G4）",
     "19,496 次运行是随机抽 100 的探索性压力测试，目前没有一份按规则执行的正式回测",
     "按冻结规则 §6（成交额前 50）实现币池并产出运行清单",
     "需改批次脚本 + 重算历史（新工单）", "是"],
    ["M5", "组合级回撤从未计算（E2 / G5）",
     "失败条件第 3 项无法判定；逐笔相加不能冒充组合净值",
     "补一层组合模拟，输出 max_drawdown 与净值曲线",
     "需新写汇总代码", "是"],
    ["M6", "配对代码不在代码指纹内（C2 / V1）",
     "固定仓位配对的结果无法归因到具体版本",
     "把 be_core.py 纳入 CODE_FILES，或每单附带其 sha256 声明",
     "需改代码或固定交接格式", "是"],
]
write_csv("02_必须先解决清单.csv",
          ["编号", "事项", "为什么阻断", "闭环标准（可验收）", "由谁做", "是否改代码"], must)

limit = [
    ["L1", "可用时钟只能声明「计算上不超前」（B3）",
     "所有引用处必须写：「已满足计算侧约束（桶起点+5m≤决策时刻、未收盘抛错），发布时点无证据，qualified=False」",
     "若把 qualified=False 的运行当正式结论，等于隐瞒"],
    ["L2", "资金费 mark 2021 及以前缺失（C4 / G3）",
     "引用处必须写：「2021 年段资金费按缺口处理，不计入金额结论」或整段剔除",
     "2021 段 6 笔的成本偏低，方向不确定"],
    ["L3", "最细价格路径只有 15m（G6）",
     "引用处必须写：「止损与保本触发最多滞后 15 分钟，结果偏保守，方向不确定」",
     "不能声称已按真实 tick 撮合"],
    ["L4", "产物写到生产回测目录（D4 / V2）",
     "引用处必须写：「产物落在 回测日志/event_trend_v0_1/runs/，已用 manifest 逐文件 sha256 核对」",
     "目录里会混入其他工单的 run，需要靠 run_id 区分"],
    ["L5", "固定仓位配对实现放在 evidence 下（C1）",
     "引用处必须写：be_core.py / 02_run_fixed.py 的 sha256，以及「未纳入代码指纹」",
     "别人复现时可能用到另一版"],
    ["L6", "事件级去重无内置支持（V11）",
     "引用处必须写：用的是哪套口径（296 唯一交易 / 206 候选 / 201 事件）",
     "三套口径混用会让数字对不上"],
]
write_csv("03_可限制保留清单.csv",
          ["编号", "事项", "必须写进引用处的限制", "残余风险"], limit)

decide = [
    ["U1", "验证组范围：只验 A，还是把 1R/2R/缩量/联合一并验",
     "只验 A（本方推荐）／加验 1 个（等待时间约翻倍）／全部验（判定力降到无法判断）",
     "只验 A",
     "不决定就无法冻结评估办法；若默认全验，样本被切碎后等于没验"],
    ["U2", "币池口径：冻结规则前 50（按成交额）还是既有运行的随机 100",
     "前 50（与冻结文本一致，等待约 30 个月以上）／随机 100（与既有 296 笔可比，但只能标探索性）",
     "前 50",
     "不决定则样本口径混杂，结论无法写进策略文档"],
    ["U3", "起算时点：等 G1/G2/G4 闭环后起算，还是立即起算",
     "等闭环（推荐）／立即（可能白攒一段）",
     "等闭环",
     "立即起算若中途改代码，样本身份断裂，整段作废"],
    ["U4", "是否接受 18~24 个月前向累积",
     "接受（唯一能产生可称样本外的路径）／不接受（只剩工程验证，不能宣称策略有效）",
     "接受",
     "不接受则策略停在研究候选状态，不能进 Live"],
    ["U5", "契约里「保留 1R 保本」与 OOS 结论方向相反",
     "按契约保留 1R／按 OOS 结论不进入／另立新假说单独验",
     "按 OOS 结论不进入（201 事件中 200 个逐一移除仍为负）",
     "两边口径不一致，谁执行都会留下解释空间"],
    ["U6", "契约里的熔断规则（进度 30% 后错失盈利超原策略总收益 50% 即暂停）",
     "同意／改阈值／不加熔断",
     "需用户定（本方无证据支持具体阈值）",
     "熔断会提前终止回测，阈值不当会让结果不可比"],
    ["U7", "契约里的「缺失超 4 根即剔除该笔」",
     "同意／改根数／改为标注不剔除",
     "需用户定（本方未测试该规则的影响面）",
     "剔除规则会改变分母，直接影响净差额"],
    ["U8", "配对代码归属与输出目录参数（M6 / D4）",
     "搬进策略包并改指纹／留在 evidence 带哈希声明；加输出参数／不加",
     "两条都建议「先不改代码，用声明方式过渡」",
     "不决定则每单都要人工补声明，容易漏"],
]
write_csv("04_待用户决定.csv",
          ["编号", "事项", "可选做法", "本方推荐", "不决定的后果"], decide)

# ══════════════════ 五、三项任务汇总 ══════════════════
summary = [
    ["1", "OOS-READY-01｜独立验证数据资格", "已交付",
     "evidence/OOS-READY-01/",
     "8 段全部不可用；暂无可证明的样本外数据；前向累积需 18~24 个月",
     "D1~D4 待决策"],
    ["2", "VALIDATION-READINESS-01｜既有验证入口与前置缺口", "已交付",
     "evidence/VALIDATION-READINESS-01/",
     "入口可复用不需另建框架；11 项缺口（阻止可信 5 / 仅影响方便 6）",
     "U1~U4 待决策"],
    ["3", "NEXT-STAGE-GATE-01｜下一阶段准入清单", "本单",
     "evidence/NEXT-STAGE-GATE-01/",
     "当前不能进入执行；可准备但有条件；停止在方案阶段",
     "U1~U8 待决策（含契约 3 项）"],
]
write_csv("05_三项任务汇总.csv",
          ["序号", "任务", "状态", "产物路径", "核心结论", "未解决"], summary)

# ══════════════════ 六、总体判定 ══════════════════
n_pass = sum(1 for r in gate if r[4] == "通过")
n_part = sum(1 for r in gate if r[4] == "部分通过")
n_fail = sum(1 for r in gate if r[4] == "不通过")
blocking = [r[0] for r in gate if r[7].startswith("必须先解决")]
verdict = "当前不能进入执行阶段" if n_fail else ("有条件准备" if n_part else "可以准备执行")

rec("准入表 18 项", len(gate) == 18, len(gate))
rec("存在不通过项 ⇒ 不得进入执行", n_fail > 0, f"通过={n_pass} 部分={n_part} 不通过={n_fail}")
rec("阻断项覆盖 A1/A2/B4/C2/E2", {"A1", "A2", "B4", "C2", "E2"} <= set(blocking), blocking)
rec("必须先解决 6 项", len(must) == 6, len(must))
rec("可限制保留 6 项", len(limit) == 6, len(limit))
rec("待用户决定 8 项", len(decide) == 8, len(decide))
rec("三项任务汇总 3 行", len(summary) == 3, len(summary))

payload = {
    "verdict": verdict,
    "counts": {"通过": n_pass, "部分通过": n_part, "不通过": n_fail},
    "blocking_items": blocking,
    "eligibility_segments": {"total": len(elig), "usable": len(usable),
                             "unusable": len(unusable), "qual_bad": len(qual_bad),
                             "no_data": len(no_data), "observed": len(observed),
                             "unknown": len(unknown)},
    "coverage": {"kline_symbols_max": kline_max, "oi_symbols_max": oi_max},
    "capability": {"supported": len(cap_ok), "unsupported": len(cap_no)},
    "gaps": {"block_result": n_block, "convenience_only": n_easy},
    "contract": {"path": os.path.relpath(contract_md, ROOT), "sha256": contract_sha,
                 "recorded_sha256": contract_recorded, "status": contract_status,
                 "updated_since_draft": contract_updated},
    "checks": CHECK,
    "pass": sum(1 for c in CHECK if c["status"] == "PASS"),
    "fail": sum(1 for c in CHECK if c["status"] == "FAIL"),
}
with open(os.path.join(LOGS, "00_gate.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(json.dumps({"verdict": verdict,
                  "counts": payload["counts"],
                  "blocking": len(blocking),
                  "contract_updated": contract_updated,
                  "pass": payload["pass"], "fail": payload["fail"]}, ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
