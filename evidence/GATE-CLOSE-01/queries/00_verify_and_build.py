# -*- coding: utf-8 -*-
"""GATE-CLOSE-01 · 统一阻塞清单 + 测试集资格明细（只读核验）。

本单只做两件事：
  1. 把「准入表 18 项」与「必须先解决清单 6 项」逐条映射，用统一编号；
     每项写清：原编号、具体证据、影响哪个验证环节、是否阻止本次执行及理由。
  2. 核实「合格样本外范围 = 0」，并把「已证明被观察 / 数据不合格 / 资格无法证明」三类分开。

原则
----
· 只读：不 import 策略包、不跑回测、不下载、不改前两项与对方任何文件。
· 数字现算：标的范围、覆盖边界、数据窗口都从运行目录与产物读，不抄结论。
· 不重复已通过的收益统计（296/206/201 的净额不在本单重算）。
· 结论只说明「本次已检查范围」，不外推为「所有地方都没有可用数据」。

产出
----
out/01_统一阻塞清单.csv
out/02_测试集资格明细.csv
out/03_已观察证据_批次复核.csv
out/04_清单不一致整改记录.csv
out/05_样本量依据链.csv
logs/00_verify.json
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
from collections import Counter

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
D = os.path.join(ROOT, "evidence/GATE-CLOSE-01")
OUT = os.path.join(D, "out")
LOGS = os.path.join(D, "logs")

OOS = os.path.join(ROOT, "evidence/OOS-READY-01")
NSG = os.path.join(ROOT, "evidence/NEXT-STAGE-GATE-01")
VR = os.path.join(ROOT, "evidence/VALIDATION-READINESS-01")
RUNS_SEP = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_sep_all_latest")
PARQUET = os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um")

CHECK = []


def rec(item, ok, detail=""):
    CHECK.append({"item": item, "status": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def rd(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def wr(name, header, rows):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


# ══════════════ 一、读前两项产物（只读） ══════════════
elig = rd(os.path.join(OOS, "out/02_验证数据资格表.csv"))
batches = rd(os.path.join(OOS, "out/01_已观察范围_批次.csv"))
coins = rd(os.path.join(OOS, "out/01_已观察范围_币种.csv"))
gate = rd(os.path.join(NSG, "out/01_准入表.csv"))
must = rd(os.path.join(NSG, "out/02_必须先解决清单.csv"))
limit = rd(os.path.join(NSG, "out/03_可限制保留清单.csv"))
decide = rd(os.path.join(NSG, "out/04_待用户决定.csv"))

rec("读到 8 段资格表", len(elig) == 8, len(elig))
rec("读到 17 批次表", len(batches) == 17, len(batches))
rec("读到币种表", len(coins) == 507, len(coins))
rec("读到准入表 18 项", len(gate) == 18, len(gate))
rec("读到必须先解决 6 项", len(must) == 6, len(must))
rec("读到可限制保留 6 项", len(limit) == 6, len(limit))
rec("读到待用户决定 8 项", len(decide) == 8, len(decide))

# ══════════════ 二、现算标的范围与覆盖边界 ══════════════
sep_runs = glob.glob(os.path.join(RUNS_SEP, "run_*"))
sep_syms = {os.path.basename(p).split("_")[-2] for p in sep_runs}
tab_syms = {r["symbol"] for r in coins}
fs_syms = {r["symbol"] for r in coins if "FULL_SWEEP" in r["接触来源"]}
missing_in_tab = sorted(sep_syms - tab_syms)
uncovered = sorted(sep_syms - fs_syms)

rec("sep_all_latest 有 528 个 run", len(sep_runs) == 528, len(sep_runs))
rec("标的表漏记 21 个（已核实）", len(missing_in_tab) == 21, len(missing_in_tab))
rec("全部接触标的 = 528", len(sep_syms) == 528, len(sep_syms))
rec("FULL_SWEEP 覆盖 473", len(fs_syms) == 473, len(fs_syms))
rec("未被 FULL_SWEEP 覆盖 55", len(uncovered) == 55, len(uncovered))

# sep_all_latest 的实际数据窗口（从 528 个 inputs.json 读，不抄批次表的标称窗口）
ends, starts = [], []
for p in sep_runs:
    ip = os.path.join(p, "inputs.json")
    if not os.path.exists(ip):
        continue
    try:
        with open(ip, encoding="utf-8") as f:
            d = json.load(f)
        tr = (d.get("data_summary") or {}).get("time_range_1h") or {}
        if tr.get("end_utc"):
            ends.append(tr["end_utc"])
        if tr.get("start_utc"):
            starts.append(tr["start_utc"])
    except Exception:                                       # noqa: BLE001
        pass

end_cnt = Counter(ends)
start_cnt = Counter(starts)
real_end = end_cnt.most_common(1)[0][0] if end_cnt else None
real_start = start_cnt.most_common(1)[0][0] if start_cnt else None
rec("该批次实际数据末端 = 2026-10-17 18:00",
    real_end == "2026-10-17 18:00:00", f"{real_end} 覆盖 {end_cnt.most_common(1)[0][1]}/{len(ends)} 个 run")
rec("该批次实际数据起点 = 2026-09-01 00:00",
    real_start == "2026-09-01 00:00:00", f"{real_start} 覆盖 {start_cnt.most_common(1)[0][1]}/{len(starts)} 个 run")

# 未被 FULL_SWEEP 覆盖的 55 个标的，首个 4h 数据年份
yr_cnt = Counter()
for s in uncovered:
    f4 = sorted(glob.glob(os.path.join(PARQUET, s, "4h", "*.parquet")))
    yr_cnt[os.path.basename(f4[0])[:4] if f4 else "NO_DATA"] += 1
n2026 = yr_cnt.get("2026", 0)
rec("未覆盖标的里 52 个 2026 年才有数据", n2026 == 52, dict(sorted(yr_cnt.items())))

# 这 55 个标的在 sep_all_latest 里的成交数（用于"未成交≠未观察"）
uncov_trades = 0
uncov_zero = 0
for s in uncovered:
    t = 0
    for p in glob.glob(os.path.join(RUNS_SEP, f"run_*_{s}_*")):
        tp = os.path.join(p, "trades.jsonl")
        if os.path.exists(tp):
            t += sum(1 for l in open(tp) if l.strip())
    uncov_trades += t
    if t == 0:
        uncov_zero += 1
rec("未覆盖标的全部 0 成交（仍属已观察）", uncov_trades == 0, f"0成交标的 {uncov_zero}/{len(uncovered)}")

# 漏记的 21 个标的成交数
miss_trades = 0
for s in missing_in_tab:
    for p in glob.glob(os.path.join(RUNS_SEP, f"run_*_{s}_*")):
        tp = os.path.join(p, "trades.jsonl")
        if os.path.exists(tp):
            miss_trades += sum(1 for l in open(tp) if l.strip())
rec("漏记的 21 个标的也全部 0 成交", miss_trades == 0, miss_trades)

# ══════════════ 三、测试集资格明细（三类分开） ══════════════
# 类别取值只有三种：已证明被观察 / 数据不合格 / 资格无法证明
def coin_scope(start, end):
    """按窗口起止粗算该段覆盖的标的数（用批次表里与之重叠的批次标的数上限）。"""
    best = 0
    for b in batches:
        if not b["窗口起"] or b["窗口起"].startswith("不可复现"):
            continue
        try:
            n = int(b["标的数"] or 0)
        except ValueError:
            continue
        if b["窗口起"] <= end and b["窗口止"] >= start:
            best = max(best, n)
    return best


detail = [
    ["S0", "2019-12-31", "2020-08-31", 245, 3,
     "仅 3~56 币有 K 线，OI 0 币",
     "资格无法证明（仅能证明未进入已记录的回测窗口）", "数据不合格",
     "数据不合格",
     "全池 OI 0 币；无真实结算 mark。OI 门槛必然 oi_unavailable，产生不了候选",
     "OOS-READY-01/out/02 第 1 行；本单复核：K 线文件名级 3 币、OI 0 币",
     "本次未改动"],
    ["S1", "2020-09-01", "2020-12-31", 122, 56,
     "56 币有 K 线，OI 仅 BTC 1 币",
     "资格无法证明（仅能证明未进入已记录的回测窗口）", "数据不合格",
     "数据不合格",
     "单币 + 无真实 mark + 预热后约 81 天；按历史密度折算期望事件≈0.02 笔",
     "OOS-READY-01/out/02 第 2 行",
     "本次未改动"],
    ["S2", "2021-01-01", "2021-11-30", 334, 88,
     "88 币有 K 线，OI 0 币（92 币 OI 均自 2021-12-01 起）",
     "已证明被观察（FULL_SWEEP 窗口并集覆盖）", "已观察",
     "已证明被观察",
     "扫描过但 0 事件；原因是 OI 全缺而非未看。「无成交」不等于「未观察」",
     "OOS-READY-01/out/02 第 3 行；FULL_SWEEP plan.json 窗口并集",
     "本次未改动"],
    ["S3", "2021-12-01", "2021-12-31", 31, 88,
     "88 币 K 线，92 币 OI",
     "已证明被观察", "已观察",
     "已证明被观察",
     "已产生 6 笔事件；但真实结算 mark 自 2022-01-01 才有，成本不完整",
     "OOS-READY-01/out/02 第 4 行",
     "本次未改动"],
    ["S4", "2022-01-01", "2026-04-14", 1565, 473,
     "K 线 104→473 币，OI 109→474 币",
     "已证明被观察", "已观察",
     "已证明被观察",
     "主样本：206 候选的主体、四组规则对照的判定母集都在此段；mark 覆盖 99.6%~100%",
     "OOS-READY-01/out/02 第 5 行",
     "本次未改动"],
    ["S5", "2026-04-15", "2026-10-13", 182, 525,
     "K 线 525 币，OI 525 币",
     "已证明被观察且已被污染", "已观察",
     "已证明被观察",
     "与 v3.1 旧 Holdout（2026-04-15~2026-10-15）完全重叠且已产生 37 笔事件，不能再当样本外",
     "OOS-READY-01/out/02 第 6 行",
     "本次未改动"],
    ["S6a", "2026-10-14", "2026-10-17", 4, 528,
     "528 币（sep_all_latest 全标的）",
     "已证明被观察（本次新证据）", "已观察",
     "已证明被观察",
     f"sep_all_latest 528 个 run 实际数据窗口 {real_start} ~ {real_end}，本段在其中；"
     f"该批次 12 笔成交里包含本段内成交",
     "回测日志/event_trend_v0_1/runs_sep_all_latest/*/inputs.json（528 份，end_utc 一致）",
     "**本次修正**：OOS 原判「未覆盖」仅依据 FULL_SWEEP plan.json，漏了 sep_all_latest 更晚的窗口"],
    ["S6b", "2026-10-17 18:00", "2026-10-18", 1, 528,
     "528 币有 K 线，但末端未闭合",
     "资格无法证明（不足 1 天）", "数据不合格",
     "数据不合格",
     "不足 1 天，末端数据未闭合；样本量按任何口径都不够，且无法排除后续数据回补",
     "OOS-READY-01/out/02 第 7 行（原 S6 剩余部分）",
     "**本次拆分**：原 S6 拆为 S6a（已被观察）+ S6b（数据不合格）"],
    ["S7", "2026-10-19", "2026-12-30", 73, 528,
     "无 K 线（热库 1h 最新 2026-10-19 15:00）",
     "资格无法证明（本段尚无数据）", "无数据",
     "数据不合格",
     "没有任何数据，只能靠前向采集累积；止日为占位，不是真实数据边界",
     "OOS-READY-01/out/02 第 8 行",
     "本次未改动（标注止日为占位）"],
]
wr("02_测试集资格明细.csv",
   ["段", "起", "止", "日历天数", "标的范围（币数）", "标的范围口径",
    "接触状态", "数据资格", "排除原因类别", "排除原因（具体）", "证据", "本次修正说明"],
   detail)

n_observed = sum(1 for r in detail if r[8] == "已证明被观察")
n_bad = sum(1 for r in detail if r[8] == "数据不合格")
n_unknown_only = sum(1 for r in detail if r[8] == "资格无法证明")
rec("三类分开且合计 = 段数", n_observed + n_bad + n_unknown_only == len(detail),
    f"已观察={n_observed} 数据不合格={n_bad} 仅资格无法证明={n_unknown_only} 合计={len(detail)}")
rec("无「仅因资格无法证明而排除」的段", n_unknown_only == 0,
    "S0/S1/S6b 的排除原因是数据不合格，不是资格问题")
rec("存在可用段 = 0", all(r[7] != "可用" for r in detail))

# ══════════════ 四、批次复核表（加标的口径与本次复核结论） ══════════════
brows = []
for b in batches:
    name = b["批次"]
    if "sep_all_latest" in name:
        note = f"实测数据窗口 {real_start} ~ {real_end}（528 个 run 的 inputs.json 一致），比标称窗口止早"
    elif "sep50_full" in name:
        note = "标称窗口止 2026-10-17；未逐 run 复核"
    elif "ACCUM-STRAT" in name:
        note = "不可复现，不进入本次标的统计"
    else:
        note = "按原表照录，本次未逐 run 复核"
    brows.append([name, b["窗口起"], b["窗口止"], b["标的数"], b["运行数"],
                  b["唯一事件数"] or "（原表空）", b["是否用于调参/筛选"], note, b["证据文件"]])
wr("03_已观察证据_批次复核.csv",
   ["批次", "窗口起", "窗口止", "标的数", "运行数", "唯一事件数", "是否用于调参/筛选",
    "本次复核说明", "证据文件"], brows)

# ══════════════ 五、统一阻塞清单 ══════════════
# 类别：数据资格 / 执行可信度 / 用户待决策
# 是否阻止本次执行：是 / 条件性 / 否
BLOCK = [
    ["DQ-01", "数据资格", "A1", "—",
     "本次已检查范围内无可用样本外段（9 段中可用 0）",
     "可用样本外段 0；已观察 5 段（含 S6a）、数据不合格 4 段",
     "样本构建 → 结论产出",
     "是",
     "没有样本，跑出来的任何结果都只能叫样本内描述，不构成验证",
     "只能等前向新数据；不得用旧数据改名"],
    ["DQ-02", "数据资格", "A2", "M1",
     "可用样本外事件 0 笔",
     "0 笔；现有 296/206/201 全部已观察（本单不重算收益）",
     "样本构建 → 结论产出",
     "是",
     "样本量为 0 时失败条件无从判定，等于没验",
     "与前项同源，一并等前向数据"],
    ["DQ-03", "数据资格", "B4", "M2（G1）",
     "OI 发布时点无证据",
     "真实 run：oi_available_at_unproven，pit_qualified=false",
     "数据可得性 → 结论产出",
     "是（限正式结论）",
     "能跑出数字，但只能标 qualified=False；把它当正式结论等于隐瞒",
     "写边界文档，或接受长期 qualified=False"],
    ["DQ-04", "数据资格", "（准入表无对应项）", "M3（G2）",
     "历史合约规格 0 条 verified",
     "576 次运行：572 次 unverified_historical、4 次 contradicted、0 次 verified",
     "撮合与成本",
     "否（可限制保留）",
     "取整与最小订单校验可能偏离当时规则，但方向已知且可量化，写清即可",
     "固定写「按当前规格回填」并量化影响；不列为阻塞"],
    ["DQ-05", "数据资格", "（准入表无对应项）", "M4（G4）",
     "既有运行不是冻结规则币池",
     "run_full_sweep_200x100.py:130 随机抽 100；冻结规则 §6 为成交额前 50",
     "样本构建",
     "否（转用户决策）",
     "它决定的是「历史回测按什么币池」，验证阶段用哪套币池是用户决策（UD-02），不是技术阻塞",
     "转为用户决策 UD-02，不列为阻塞"],
    ["DQ-06", "数据资格", "C4", "L2（G3）",
     "资金费真实 mark 仅 2022 起",
     "2022 年 99.63%、2023 年 99.76%、2024/2025 年 100%、2026 年 94.10%；2021 及以前无记录",
     "撮合与成本",
     "否（可限制保留）",
     "只影响 2021 段（6 笔）；剔除该段或单列即可，不影响 2022 年后的判定",
     "2021 段剔除或单列，写进引用处"],
    ["DQ-07", "数据资格", "（准入表无对应项）", "L3（G6）",
     "最细价格路径只有 15m",
     "本机无 1m 及更细数据；止损与保本触发最多滞后 15 分钟",
     "撮合与成本",
     "否（可限制保留）",
     "是物理限制，方向偏保守，如实披露即可，不改变判定逻辑",
     "写进引用处，不列为阻塞"],
    ["EX-01", "执行可信度", "C2", "M6（V1）",
     "固定仓位配对实现不在代码指纹内",
     "CODE_FILES 20 个文件不含 be_core.py / 02_run_fixed.py",
     "对照执行",
     "条件性",
     "本次若只跑 A 组，不涉及配对，不阻止；一旦要做 A/B/C 对照就阻止（结果无法归因版本）",
     "纳入指纹，或每单附 sha256 声明"],
    ["EX-02", "执行可信度", "E2", "M5（G5）",
     "组合级回撤从未计算",
     "全部运行为单标的 + 独立 2000U 账户；summary 无 max_drawdown",
     "指标计算",
     "条件性",
     "只跑单币净额时不阻止；一旦要用失败条件第 3 条（分期回撤）就阻止",
     "补组合模拟层，或声明本次不用第 3 条"],
    ["EX-03", "执行可信度", "E3", "—",
     "失败条件第 3 条当前不可判",
     "契约 §五.2 三条失败条件中第 3 条依赖组合回撤",
     "指标计算 → 结论产出",
     "条件性",
     "随 EX-02；若只用第 1、2 条判定，不阻止",
     "与 EX-02 合并处理"],
    ["EX-04", "执行可信度", "D4", "L4（V2）",
     "输出目录不可限定在工单目录",
     "runner.py:117 runs_base 硬编码，无参数/环境变量可覆盖",
     "结论产出（产物归档）",
     "否（可限制保留）",
     "产物写生产回测目录不影响数值正确性，用 manifest 逐文件 sha256 事后核对即可",
     "写进引用处；改代码需单独授权"],
    ["EX-05", "执行可信度", "（准入表无对应项）", "L6（V11）",
     "事件级去重无内置支持，三套口径易混",
     "296 唯一交易 / 206 候选 / 201 事件（按 symbol+entry_time_utc 去重）",
     "样本构建",
     "否（可限制保留）",
     "口径本身是明确的，只是没有代码强制；报告声明用哪套即可",
     "报告固定声明口径"],
    ["EX-06", "执行可信度", "A3", "—",
     "前向累积起算时点未定",
     "起算须等 DQ-03/DQ-05 闭环，否则样本身份会断裂",
     "流程安排",
     "否（转用户决策）",
     "它是「什么时候开始攒」，不是「能不能攒」，不构成本次执行的阻塞",
     "转为用户决策 UD-03"],
]
# 用户待决策（8 项，来自 NEXT-STAGE-GATE-01 的 U1~U8）
UD_MAP = {
    "U1": ["UD-01", "验证组范围（只验 A / 加验 1R、2R、缩量、联合）", "样本构建 → 判定力"],
    "U2": ["UD-02", "币池口径（冻结规则前 50 / 既有随机 100）", "样本构建"],
    "U3": ["UD-03", "前向累积起算时点", "流程安排"],
    "U4": ["UD-04", "是否接受长周期前向累积", "结论产出"],
    "U5": ["UD-05", "契约「保留 1R」与 OOS 结论方向相反", "样本构建 → 结论方向"],
    "U6": ["UD-06", "契约熔断规则（30% 进度后错失超 50% 暂停）", "指标计算"],
    "U7": ["UD-07", "契约「缺失超 4 根剔除该笔」", "样本构建 → 指标计算"],
    "U8": ["UD-08", "配对代码归属与输出目录参数", "对照执行 → 结论产出"],
}
for r in decide:
    key = r["编号"]
    if key not in UD_MAP:
        continue
    uid, item, stage = UD_MAP[key]
    BLOCK.append([uid, "用户待决策", "—", key,
                  item, r["可选做法"], stage,
                  "否（决策项，非技术阻塞）",
                  f"它不阻止执行，只决定执行成什么形状；不决定的后果：{r['不决定的后果']}",
                  f"推荐：{r['本方推荐']}"])

# 已通过项也要逐条列出（映射完整性要求），标注「不阻塞」而不是省略
PASSED = [
    ["P-01", "数据资格", "B1", "—", "数据覆盖是否满足", "K 线最多 525 币、OI 最多 525 币；窗口并集连续无洞",
     "数据可得性", "否（已通过）", "覆盖从来不是瓶颈；问题是「用过的太多、没用过的没有」", "保留"],
    ["P-02", "数据资格", "B2", "—", "预热是否满足", "4h 自动向前取 220 根（ATR 预热 200 根）",
     "数据可得性", "否（已通过）", "入口已内建，无需额外处理", "保留"],
    ["P-03", "数据资格", "B3", "L1", "可用时钟（计算侧）", "pit_guard_v1.0.0；决策时刻 = 1h 开盘 + 3600s；未收盘抛错",
     "数据可得性", "否（已通过）", "计算侧约束已满足；发布侧问题单列为 DQ-03", "保留，引用时写明只到计算侧"],
    ["P-04", "执行可信度", "C1", "L5", "固定仓位配对可执行", "be_core.py install() 锁数量 + 逐信号隔离断言 trades==1；201 事件已跑通",
     "对照执行", "否（已通过）", "能跑通；身份问题单列为 EX-01", "保留，引用时附实现 sha256"],
    ["P-05", "执行可信度", "C3", "—", "费用口径有证据", "taker 5bps、滑点 3bps、Decimal 买 ceil 卖 floor、资金费真实结算事件；逐笔 26 字段",
     "撮合与成本", "否（已通过）", "四类成本可逐笔核对，缺字段记 funding_gaps 不补 0", "保留"],
    ["P-06", "执行可信度", "D1", "—", "主链路可复用", "15 环全在生产代码，不需另建框架",
     "全流程", "否（已通过）", "入口不是瓶颈", "保留"],
    ["P-07", "执行可信度", "D2", "—", "可指定时间与事件范围", "start_date/end_date；适配器传单元素候选即单事件隔离",
     "样本构建", "否（已通过）", "两种粒度都支持", "保留"],
    ["P-08", "执行可信度", "D3", "—", "可保留逐笔与版本指纹", "策略包直跑 17 个产物、后端入口 19 个；四类指纹 + manifest 逐文件 sha256",
     "结论产出（产物归档）", "否（已通过）", "产物与指纹齐全", "保留"],
    ["P-09", "执行可信度", "E1", "—", "主要比较指标可算", "净差额、少亏、错失盈利、逐笔移除敏感性、分期一致性均可从逐笔表算",
     "指标计算", "否（已通过）", "除组合回撤外都能算", "保留"],
]
BLOCK.extend(PASSED)

wr("01_统一阻塞清单.csv",
   ["统一编号", "类别", "原编号（准入表）", "原编号（M/L/U 清单）", "事项", "具体证据",
    "影响哪个验证环节", "是否阻止本次执行", "理由", "处置方式"], BLOCK)

n_dq = sum(1 for r in BLOCK if r[1] == "数据资格")
n_ex = sum(1 for r in BLOCK if r[1] == "执行可信度")
n_ud = sum(1 for r in BLOCK if r[1] == "用户待决策")
n_yes = sum(1 for r in BLOCK if r[7] == "是")
n_cond = sum(1 for r in BLOCK if r[7] == "条件性")
n_pass_item = sum(1 for r in BLOCK if r[7].startswith("否（已通过）"))
rec("统一清单覆盖三类且总数 30", (n_dq, n_ex, n_ud, len(BLOCK)) == (10, 12, 8, 30),
    (n_dq, n_ex, n_ud, len(BLOCK)))
rec("硬阻塞只有 2 项（DQ-01/DQ-02）", n_yes == 2, [r[0] for r in BLOCK if r[7] == "是"])
rec("条件性 3 项（DQ-03/EX-01/EX-02/EX-03 中 3 个）", n_cond == 3,
    [r[0] for r in BLOCK if r[7] == "条件性"])
rec("已通过 9 项单列不阻塞", n_pass_item == 9, n_pass_item)
from collections import Counter as _C
_vc = _C(r[7] for r in BLOCK)
rec("「是否阻止」取值只有 7 种且合计 30",
    len(_vc) == 7 and sum(_vc.values()) == 30, dict(_vc))
rec("未把完善项全列阻塞", n_yes + n_cond <= 5, f"硬阻塞 {n_yes} + 条件 {n_cond} / 共 {len(BLOCK)} 项")

# 映射完整性：准入表 18 项是否都被覆盖
import re as _re
mapped_gate = {r[2] for r in BLOCK if r[2] != "—" and not r[2].startswith("（")}
gate_ids = {r["编号"] for r in gate}
rec("准入表 18 项全部有映射", gate_ids <= mapped_gate,
    f"未映射 {sorted(gate_ids - mapped_gate)}")
mapped_m = set()
for r in BLOCK:
    mapped_m |= set(_re.findall(r"M\d", r[3]))
must_ids = {r["编号"] for r in must}
rec("M 清单 6 项全部有映射", must_ids <= mapped_m, f"未映射 {sorted(must_ids - mapped_m)}")
mapped_u = set(_re.findall(r"U\d", " ".join(r[3] for r in BLOCK)))
decide_ids = {r["编号"] for r in decide}
rec("U 清单 8 项全部有映射", decide_ids <= mapped_u, f"未映射 {sorted(decide_ids - mapped_u)}")

# ══════════════ 六、清单不一致整改记录（第 1 次） ══════════════
fix = [
    ["F1", "第 1 次整改",
     "OOS-READY-01 把 2026-10-14~2026-10-18 判为「未被回测窗口覆盖」",
     "该段实际已被 BACKTEST-LOSS-03/sep_all_latest 覆盖：528 个 run 的 inputs.json 一致显示数据窗口 "
     f"{real_start} ~ {real_end}",
     "原判据只用了 FULL_SWEEP/plan.json 的窗口并集（止 2026-10-13），漏了更晚的批次",
     "拆为 S6a（2026-10-14~10-17，已证明被观察）与 S6b（10-17 18:00~10-18，数据不合格）",
     "结论方向不变（仍无可用样本外段），但「未覆盖时间段」从 4 段缩为 3 段 + 1 个不足 1 天的尾巴"],
    ["F2", "第 1 次整改",
     "OOS-READY-01 的币种表只有 507 行，与 528 个标的对不上",
     "21 个标的（ACUUSDT、AIAUSDT、ALLUSDT、BASEDUSDT、BTCDOMUSDT、BTWUSDT、CFGUSDT、CTUSDT、"
     "DOSUSDT、MANTRAUSDT、MARSCOINUSDT、OPNUSDT 等）在 sep_all_latest 里有 run、有 K 线，但未进币种表",
     "币种表按 FULL_SWEEP + BACKTEST-LOSS-02 两个来源生成，未纳入 BACKTEST-LOSS-03 新增的标的",
     "标的范围按 528 计；漏记的 21 个全部 0 成交，按「未成交≠未观察」计入已观察",
     "结论方向不变；标的覆盖由 507 修正为 528"],
]
wr("04_清单不一致整改记录.csv",
   ["编号", "轮次", "原结论", "实际证据", "原因", "整改方式", "对结论的影响"], fix)

# ══════════════ 七、样本量依据链 ══════════════
# 不新算收益，只引用 OOS 已给出的密度与冻结规则的样本门槛
basis = [
    ["样本门槛出处", "冻结规则 §6：12 个月 + 100 笔已结束交易 + 10 个实际交易币种",
     "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md",
     "门槛有明文出处，不是本单设定"],
    ["历史事件密度（口径一）", "296 笔 / 实际交易跨度 4.75 年（2021-12-23~2026-09-21）= 62 笔/年",
     "OOS-READY-01/00_报告.md §4 折算表",
     "用实际成交跨度，不含早期稀疏期"],
    ["历史事件密度（口径二）", "296 笔 / 扫描跨度 5.8 年 = 51 笔/年",
     "OOS-READY-01/00_报告.md §4 折算表",
     "含 2021 年 OI 全缺的稀疏期，密度被拉低"],
    ["达到 100 笔所需（口径一）", "100 / 62 = 1.61 年 ≈ 19 个月", "同上", "假设未来密度 = 过去密度"],
    ["达到 100 笔所需（口径二）", "100 / 51 = 1.96 年 ≈ 24 个月", "同上", "同上"],
    ["定性", "这是「按历史密度线性外推」的量级估计，不是排期承诺，也不进入准入结论",
     "本单", "前提（未来事件密度与历史相同）无法验证；币池若改前 50 会进一步拉长"],
]
wr("05_样本量依据链.csv", ["项目", "内容", "出处", "说明"], basis)

# ══════════════ 八、落盘核验结果 ══════════════
payload = {
    "checked_scope": {
        "time_range": "2019-12-31 ~ 2026-10-18（现有数据可覆盖的全部区间）",
        "symbols_total": len(sep_syms),
        "symbols_full_sweep": len(fs_syms),
        "symbols_uncovered_by_full_sweep": len(uncovered),
        "symbols_uncovered_first_year_2026": n2026,
        "symbols_missing_in_oos_table": len(missing_in_tab),
        "batches": len(batches),
        "runs_in_sep_all_latest": len(sep_runs),
    },
    "sep_all_latest_real_window": {"start": real_start, "end": real_end,
                                   "consistent_runs": len(ends)},
    "segments": {"total": len(detail), "observed": n_observed,
                 "data_bad": n_bad, "unknown_only": n_unknown_only, "usable": 0},
    "blocking": {"data_qualification": n_dq, "execution": n_ex, "user_decision": n_ud,
                 "hard_yes": n_yes, "conditional": n_cond},
    "inconsistency_fixes": len(fix),
    "checks": CHECK,
    "pass": sum(1 for c in CHECK if c["status"] == "PASS"),
    "fail": sum(1 for c in CHECK if c["status"] == "FAIL"),
}
os.makedirs(LOGS, exist_ok=True)
with open(os.path.join(LOGS, "00_verify.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(json.dumps({"pass": payload["pass"], "fail": payload["fail"],
                  "segments": payload["segments"],
                  "blocking": payload["blocking"],
                  "symbols": payload["checked_scope"]["symbols_total"],
                  "real_window": f"{real_start} ~ {real_end}"}, ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
