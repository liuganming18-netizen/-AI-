# -*- coding: utf-8 -*-
"""GATE-CLOSE-02 · 纠正标的范围与历史规格准入判定（只读）。

本单只做两件事，不新增研究：
  1. 用**官方分类**（exchangeInfo 的 underlyingType / contractType）重定标的范围，
     区分「历史运行中观察过的全部标的」与「本工程允许纳入的加密币范围」。
  2. 撤回 GATE-CLOSE-01 对历史规格的「可限制保留」判定，改为分层的
     「带假设探索分析 / 正式历史验证」两档，并核实是否存在可单独放行的子集。

原则
----
· 只读：不 import 策略包、不跑回测、不下载、不改参数、不改生产、不动原报告。
· 不假定「528 减 2」：逐个标的按官方字段判定，并同时核查 underlyingType 全部取值
  与 contractType 全部取值，确认没有漏掉的排除项。
· 时间边界一律用**实际时间戳**表达；已观察末端与数据末端分开写，不写成完整自然日。
· 不重复收益统计（296/206/201 的净额、t 值、分期结果均不重算）。

产出
----
out/01_官方分类与范围清单.csv   528 个标的逐个分类 + 纳入/排除
out/02_纳入清单.csv             允许纳入的加密币范围
out/03_排除清单.csv             排除项 + 分类依据
out/04_资格明细.csv             按修正范围重算的 9 段资格（实际时间戳）
out/05_统一准入表.csv           逐项映射证据与影响
out/06_必须解决清单.csv         合并同源项后的修复任务
out/07_历史规格准入判定.csv     撤回与重判
logs/00_verify.json             实际只读查询输出
manifest.json                   输入与交付指纹（由 01 脚本写）
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import pyarrow.parquet as pq

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
D = os.path.join(ROOT, "evidence/GATE-CLOSE-02")
OUT = os.path.join(D, "out")
LOGS = os.path.join(D, "logs")

EXINFO = os.path.join(ROOT, "evidence/PRECISION-04/当前响应_exchangeInfo.json")
MANIFEST_V2 = os.path.join(ROOT, "evidence/BACKTEST-LOSS-02/all_runs_manifest_v2.csv")
PREC04_MD = os.path.join(ROOT, "evidence/PRECISION-04/PRECISION-04_历史合约规格证据盘点_2026-10-17.md")
OOS = os.path.join(ROOT, "evidence/OOS-READY-01")
GC1 = os.path.join(ROOT, "evidence/GATE-CLOSE-01")

CANDLE_BASE = os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um")
OI_BASE = os.path.join(ROOT, "数据/市场数据/parquet/oi/binance/um")
RUNS_SEP = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_sep_all_latest")

VERSION = "WX-GATE-v2.0"
CHECK = []


def rec(item, ok, detail=""):
    CHECK.append({"item": item, "status": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def wr(name, header, rows):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


def ts(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ms(s):
    """'2026-10-17 18:00:00' (UTC) → epoch ms"""
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


def parquet_range(path):
    """用 row group 统计信息取时间列 min/max（不加载数据）。

    K 线用 `open_time`，OI 用 `ts`（`oi/binance/um/<sym>/<year>.parquet`）。
    """
    try:
        md = pq.ParquetFile(path).metadata
        names = [md.schema.column(i).name for i in range(md.num_columns)]
        col = next((c for c in ("open_time", "ts", "timestamp") if c in names), None)
        if col is None:
            return None, None
        ci = names.index(col)
        lo = hi = None
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(ci).statistics
            if st is not None and st.has_min_max:
                lo = st.min if lo is None else min(lo, st.min)
                hi = st.max if hi is None else max(hi, st.max)
        return lo, hi
    except Exception:                                       # noqa: BLE001
        return None, None


# ══════════════ 一、官方分类 ══════════════
with open(EXINFO, encoding="utf-8") as f:
    exinfo = json.load(f)
meta = {x["symbol"]: x for x in exinfo.get("symbols") or []}
rec("exchangeInfo 快照可读且含 symbols", len(meta) > 0, len(meta))
rec("快照含 serverTime", "serverTime" in exinfo, ts(exinfo.get("serverTime")))

syms_all = sorted(d for d in os.listdir(CANDLE_BASE) if os.path.isdir(os.path.join(CANDLE_BASE, d)))
rec("数据目录标的数 = 528", len(syms_all) == 528, len(syms_all))

# 逐标的分类
ut = Counter()
ct = Counter()
for s in syms_all:
    x = meta.get(s)
    if x is None:
        ut["(不在快照)"] += 1
        ct["(不在快照)"] += 1
    else:
        ut[x.get("underlyingType")] += 1
        ct[x.get("contractType")] += 1
rec("underlyingType 取值只有 COIN / INDEX", set(ut) <= {"COIN", "INDEX"}, dict(ut))
rec("contractType 取值只有 PERPETUAL", set(ct) == {"PERPETUAL"}, dict(ct))

# 纳入规则：underlyingType == COIN 且 contractType == PERPETUAL
rows_all, included, excluded = [], [], []
for s in syms_all:
    x = meta.get(s) or {}
    u, c = x.get("underlyingType"), x.get("contractType")
    sub = x.get("underlyingSubType")
    sub_txt = "|".join(sub) if isinstance(sub, list) else str(sub)
    base = x.get("baseAsset")
    reason = ""
    if u is None:
        keep, reason = False, "分类无证据（不在官方快照）"
    elif u != "COIN":
        keep, reason = False, f"underlyingType={u}（非加密币，属指数类）"
    elif c != "PERPETUAL":
        keep, reason = False, f"contractType={c}（非永续）"
    else:
        keep, reason = True, "underlyingType=COIN 且 contractType=PERPETUAL"
    rows_all.append([s, u, c, sub_txt, base, x.get("status"), "纳入" if keep else "排除", reason])
    (included if keep else excluded).append([s, u, c, sub_txt, base, x.get("status"), reason])

wr("01_官方分类与范围清单.csv",
   ["symbol", "underlyingType", "contractType", "underlyingSubType", "baseAsset",
    "status", "纳入/排除", "判定依据"], rows_all)
wr("02_纳入清单.csv",
   ["symbol", "underlyingType", "contractType", "underlyingSubType", "baseAsset",
    "status", "判定依据"], included)
wr("03_排除清单.csv",
   ["symbol", "underlyingType", "contractType", "underlyingSubType", "baseAsset",
    "status", "判定依据"], excluded)

n_in, n_ex = len(included), len(excluded)
rec("纳入 + 排除 = 528", n_in + n_ex == 528, (n_in, n_ex))
rec("排除项全部为 INDEX 且 underlyingSubType 含 Index",
    all(r[1] == "INDEX" and "Index" in r[3] for r in excluded),
    [r[0] for r in excluded])
rec("排除 2 个（ALLUSDT / BTCDOMUSDT）",
    sorted(r[0] for r in excluded) == ["ALLUSDT", "BTCDOMUSDT"],
    sorted(r[0] for r in excluded))
rec("纳入 526", n_in == 526, n_in)

# 排除项的数据覆盖（用于判断它对哪些时间段有影响）
excl_range = {}
for r in excluded:
    lo, hi = parquet_range(os.path.join(CANDLE_BASE, r[0], "4h", "live.parquet"))
    excl_range[r[0]] = (lo, hi)
rec("两个排除标的只有 live 分片且区间相同",
    all(v[0] is not None for v in excl_range.values()), {k: (ts(v[0]), ts(v[1])) for k, v in excl_range.items()})

# ══════════════ 二、修正范围下的分段资格（实际时间戳） ══════════════
# 已观察末端：sep_all_latest 528 个 run 的 1h end_utc 一致值
ends = Counter()
for p in glob.glob(os.path.join(RUNS_SEP, "run_*", "inputs.json")):
    try:
        with open(p, encoding="utf-8") as f:
            tr = (json.load(f).get("data_summary") or {}).get("time_range_1h") or {}
        if tr.get("end_utc"):
            ends[tr["end_utc"]] += 1
    except Exception:                                       # noqa: BLE001
        pass
observed_end = ends.most_common(1)[0][0] if ends else None
rec("已观察末端 = 2026-10-17 18:00:00（528 run 一致）",
    observed_end == "2026-10-17 18:00:00", f"{observed_end} ({ends.most_common(1)[0][1]}/{sum(ends.values())})")

# 数据末端：全池 1h / 15m / 4h 的最大 open_time
def pool_max(tf):
    mx = None
    for s in syms_all:
        p = os.path.join(CANDLE_BASE, s, tf, "live.parquet")
        if not os.path.exists(p):
            fs = sorted(glob.glob(os.path.join(CANDLE_BASE, s, tf, "*.parquet")))
            p = fs[-1] if fs else None
        if not p:
            continue
        _, hi = parquet_range(p)
        if hi is not None:
            mx = hi if mx is None else max(mx, hi)
    return mx


data_end_1h = pool_max("1h")
data_end_15m = pool_max("15m")
data_end_4h = pool_max("4h")
rec("数据末端 1h = 2026-10-18 11:00:00", ts(data_end_1h) == "2026-10-18 11:00:00", ts(data_end_1h))
rec("数据末端 15m = 2026-10-18 12:00:00", ts(data_end_15m) == "2026-10-18 12:00:00", ts(data_end_15m))

# 段定义：全部用实际时间戳；S6a 止于已观察末端，S6b 止于数据末端
SEGS = [
    ("S0", "2019-12-31 00:00:00", "2020-08-31 23:59:59", "无回测窗口覆盖"),
    ("S1", "2020-09-01 00:00:00", "2020-12-31 23:59:59", "无回测窗口覆盖"),
    ("S2", "2021-01-01 00:00:00", "2021-11-30 23:59:59", "FULL_SWEEP 窗口并集覆盖"),
    ("S3", "2021-12-01 00:00:00", "2021-12-31 23:59:59", "FULL_SWEEP 窗口并集覆盖"),
    ("S4", "2022-01-01 00:00:00", "2026-04-14 23:59:59", "FULL_SWEEP 窗口并集覆盖"),
    ("S5", "2026-04-15 00:00:00", "2026-10-13 23:59:59", "FULL_SWEEP 窗口并集覆盖（含旧 Holdout）"),
    ("S6a", "2026-10-14 00:00:00", observed_end, "sep_all_latest 实测窗口覆盖"),
    ("S6b", observed_end, ts(data_end_15m), "有数据但无任何批次覆盖"),
    ("S7", ts(data_end_15m), None, "无数据"),
]

# 每个标的的 4h / OI 覆盖区间（一次性算好，逐段判断）
cov = {}
for s in [r[0] for r in included]:
    fs4 = sorted(glob.glob(os.path.join(CANDLE_BASE, s, "4h", "*.parquet")))
    lo4 = hi4 = None
    for p in fs4:
        a, b = parquet_range(p)
        if a is not None:
            lo4 = a if lo4 is None else min(lo4, a)
            hi4 = b if hi4 is None else max(hi4, b)
    fso = sorted(glob.glob(os.path.join(OI_BASE, s, "*.parquet")))
    loo = hio = None
    for p in fso:
        a, b = parquet_range(p)
        if a is not None:
            loo = a if loo is None else min(loo, a)
            hio = b if hio is None else max(hio, b)
    cov[s] = (lo4, hi4, loo, hio)

seg_rows = []
for name, a, b, why in SEGS:
    a_ms = ms(a)
    b_ms = ms(b) if b else None
    n4 = noi = 0
    for s, (lo4, hi4, loo, hio) in cov.items():
        if lo4 is not None and hi4 is not None:
            if lo4 <= (b_ms if b_ms is not None else 1 << 62) and hi4 >= a_ms:
                n4 += 1
        if loo is not None and hio is not None:
            if loo <= (b_ms if b_ms is not None else 1 << 62) and hio >= a_ms:
                noi += 1
    hours = ((b_ms - a_ms) / 3600000.0) if b_ms else None
    seg_rows.append([name, a, b or "（数据末端之后，无数据）",
                     f"{hours:.1f}" if hours else "—", n4, noi, why])

# 判定（三类：已证明被观察 / 数据不合格 / 资格无法证明）
JUDGE = {
    "S0": ("资格无法证明（仅能证明未进入已记录窗口）", "数据不合格",
           "全池 OI 0 币；无真实结算 mark；OI 门槛必然 oi_unavailable"),
    "S1": ("资格无法证明（仅能证明未进入已记录窗口）", "数据不合格",
           "OI 仅 1 币；无真实 mark；预热后约 81 天，期望事件≈0.02 笔"),
    "S2": ("已证明被观察", "已证明被观察",
           "扫描过但 0 事件；OI 仅 BTC 1 币有 2021 全年数据，其余 91 币自 2021-12-01 起，"
           "OI 门槛几乎全拒；**无成交≠未观察**"),
    "S3": ("已证明被观察", "已证明被观察", "已产生 6 笔；真实 mark 自 2022 起才有，成本不完整"),
    "S4": ("已证明被观察", "已证明被观察", "主样本；206 候选主体与四组对照母集都在此段"),
    "S5": ("已证明被观察且已被污染", "已证明被观察", "与旧 Holdout 重叠且已产生 37 笔，不能再当样本外"),
    "S6a": ("已证明被观察", "已证明被观察",
            f"sep_all_latest 528 个 run 实测 1h 窗口止于 {observed_end}，本段在其内"),
    "S6b": ("未观察（有数据，无任何批次覆盖）", "数据不合格",
            f"仅 {(ms(ts(data_end_15m)) - ms(observed_end)) / 3600000.0:.1f} 小时，"
            "时长不足以支撑任何判定；不是资格问题，是时长问题"),
    "S7": ("资格无法证明（无数据）", "数据不合格", "无任何 K 线，只能前向采集"),
}
final = []
for r in seg_rows:
    st, cat, why = JUDGE[r[0]]
    final.append(r[:6] + [st, cat, why])
wr("04_资格明细.csv",
   ["段", "起（UTC）", "止（UTC）", "时长（小时）", "4h 可用标的数（526 内）",
    "OI 可用标的数（526 内）", "接触状态", "排除原因类别", "排除原因"], final)

cats = Counter(r[7] for r in final)
rec("三类分开：已观察 5 / 数据不合格 4",
    (cats.get("已证明被观察"), cats.get("数据不合格")) == (5, 4), dict(cats))
rec("无「仅因资格无法证明而排除」的段", cats.get("资格无法证明", 0) == 0, dict(cats))
rec("S6b 时长 < 24 小时（不写成 1 天）",
    (ms(ts(data_end_15m)) - ms(observed_end)) / 3600000.0 < 24,
    f"{(ms(ts(data_end_15m)) - ms(observed_end)) / 3600000.0:.1f} 小时")
rec("S6a 止于 18:00 而非自然日末", observed_end.endswith("18:00:00"), observed_end)

# S2 的 OI 覆盖必须核实：BTC 有 2021 全年，其余没有
s2 = next(r for r in final if r[0] == "S2")
rec("S2 的 OI 覆盖 = 1 币（BTC 有 2021 全年）", s2[5] == 1, s2[5])
rec("S1 的 OI 覆盖 = 1 币（BTC 自 2020-09 起）", next(r for r in final if r[0] == "S1")[5] == 1)

# ══════════════ 三、历史规格准入判定（撤回与重判） ══════════════════
with open(MANIFEST_V2, encoding="utf-8-sig") as f:
    mrows = list(csv.DictReader(f))
qual = Counter(r["historical_qualification_status"] for r in mrows)
fpa = Counter(r["formal_pass_allowed"] for r in mrows)
rec("576 次运行：572 unverified / 4 contradicted / 0 verified",
    qual.get("unverified_historical") == 572 and qual.get("contradicted") == 4
    and qual.get("verified", 0) == 0, dict(qual))
rec("576 次运行 formal_pass_allowed 全为 False", set(fpa) == {"False"}, dict(fpa))

prec_txt = open(PREC04_MD, encoding="utf-8").read()
rec("PRECISION-04 明确无标的可升级 verified", "没有一个标的能升级为" in prec_txt)
rec("PRECISION-04 有「公告没变更≠没变更」的硬反例", "硬反例" in prec_txt)
rec("PRECISION-04 不产出 formal_pass_allowed=true", "不产出任何" in prec_txt)

spec_rows = [
    ["撤回项", "GATE-CLOSE-01 的 DQ-04 原判：「取整与最小订单校验可能偏离当时规则，"
               "但方向已知且可量化，写清即可」→「否（可限制保留）」",
     "无证据支持「可量化」。PRECISION-04 结论：六标的里 4 个的既有 run 成交价根本不可能成交"
     "（SPACE 以 0.01 买入，当时区间 0.009391–0.009721；AIGENSYN/TRUTH/COTI 同样越界），"
     "原因是把价格步长写死成 0.01 且未记录规格",
     "撤回。不能以「写清即可」放行", "本单"],
    ["重判｜正式历史验证", "历史规格未证实的运行，**不得**作为正式历史验证",
     "576 次运行：572 unverified_historical、4 contradicted、**0 verified**；"
     "576 次 formal_pass_allowed 全为 False",
     "未满足（不得放行）", "BACKTEST-LOSS-02/all_runs_manifest_v2.csv"],
    ["重判｜带假设探索分析", "可作探索分析，但必须标 `historical_spec_unverified`，"
                          "且结论不得进入准入判定、不得写进策略文档",
     "同上；PRECISION-04 证据分级 A 级「可确认」也只证明「段内某一刻是这个值」，"
     "不能证明整段恒定，不等于 verified",
     "允许（限探索）", "PRECISION-04 §2 证据分级"],
    ["可单独放行的子集", "若认为存在可单独放行的子集，必须提供具体证据，否则标未满足",
     "不存在。PRECISION-04：23 份本地快照 + 20 个可定位时点 + 6735 篇公告全量扫描，"
     "结论是「没有一个标的能升级为 verified，也没有一个能声称整窗未变更」；"
     "且公告缺失有硬反例：SPACE 最大数量在 2026-05-10~2026-10-01、2026-10-02~10-16 各变一次，"
     "两次都无公告",
     "**未满足**（无可放行子集）", "PRECISION-04 §0 结论 3、§7"],
    ["为什么「公告没变更」不能当证据", "不能把「未找到变更公告」当作「未变更」",
     "有硬反例（SPACE 两次无公告变更）；且 verified 要求「两端夹住 + 逐份比较 + 覆盖整窗的变更证据」，"
     "缺任一即 unverified_historical",
     "写进任何引用处", "PRECISION-04 §0、§2"],
]
wr("07_历史规格准入判定.csv", ["项目", "内容", "证据", "判定", "出处"], spec_rows)

# ══════════════ 四、统一准入表（逐项映射证据与影响） ══════════════
GATE = [
    # 编号, 类别, 检查项, 判定, 证据, 影响环节, 是否阻止本次执行, 修复任务, 本次修正
    ["DQ-01", "数据资格", "是否存在可证明未接触且有判定力的样本外范围",
     "未通过",
     f"修正范围 {n_in} 标的下，9 段中可用段 0；已观察 5 段（S2/S3/S4/S5/S6a）、"
     f"数据不合格 4 段（S0/S1/S6b/S7）",
     "样本构建 → 结论产出", "是", "FIX-01",
     f"范围由 528 修正为 {n_in}；S6 拆为 S6a（止 {observed_end}）/S6b（{ts(data_end_15m)} 前）"],
    ["DQ-02", "数据资格", "可用样本外事件数是否达到判定门槛",
     "未通过",
     "可用样本外事件 0 笔（本单不重算收益，只引用事件数）",
     "样本构建 → 结论产出", "是", "FIX-01",
     "与 DQ-01 同源，**合并为一个修复任务**，不重复列修复"],
    ["DQ-03", "数据资格", "OI 的发布时点是否有证据",
     "未通过（限正式结论）",
     "真实 run：oi_available_at_unproven，pit_qualified=false",
     "数据可得性 → 结论产出", "是（限正式结论）", "FIX-02", "本次未改动"],
    ["DQ-04", "数据资格", "历史合约规格是否足以支撑正式历史验证",
     "未满足（本次升级）",
     "576 次运行 0 verified（572 unverified + 4 contradicted）；"
     "PRECISION-04：0 标的可升级 verified，且有「无公告变更」硬反例",
     "撮合与成本 → 结论产出", "是（限正式结论）", "FIX-03",
     "**撤回**原「写清即可」判定；改为「可作带假设探索分析，不得作正式历史验证」"],
    ["DQ-05", "数据资格", "币池口径是否与冻结规则一致",
     "不阻塞（转用户决策）",
     "run_full_sweep_200x100.py:130 随机抽 100；冻结规则 §6 为成交额前 50",
     "样本构建", "否（转决策）", "UD-02", "本次未改动"],
    ["DQ-06", "数据资格", "资金费真实 mark 是否全程覆盖",
     "部分满足（可限制保留）",
     "2022 年 99.63%、2023 年 99.76%、2024/2025 年 100%、2026 年 94.10%；2021 及以前无记录",
     "撮合与成本", "否（可限制）", "—", "本次未改动"],
    ["DQ-07", "数据资格", "价格路径粒度是否足够",
     "不阻塞（可限制保留）",
     "最细 15m；止损与保本触发最多滞后 15 分钟",
     "撮合与成本", "否（可限制）", "—", "本次未改动"],
    ["EX-01", "执行可信度", "固定仓位配对的代码身份是否可追溯",
     "未通过（条件性）",
     "CODE_FILES 20 个文件不含 be_core.py / 02_run_fixed.py",
     "对照执行", "条件性", "FIX-04", "本次未改动"],
    ["EX-02", "执行可信度", "组合级回撤能否计算",
     "未通过（条件性）",
     "全部运行为单标的 + 独立 2000U 账户；summary 无 max_drawdown",
     "指标计算", "条件性", "FIX-05", "本次未改动"],
    ["EX-03", "执行可信度", "失败条件第 3 条能否判定",
     "未通过（条件性）",
     "契约 §五.2 第 3 条依赖组合回撤",
     "指标计算 → 结论产出", "条件性", "FIX-05", "与 EX-02 同源，合并为 FIX-05"],
    ["EX-04", "执行可信度", "产物能否只写授权目录",
     "不阻塞（可限制保留）",
     "runner.py:117 runs_base 硬编码",
     "产物归档", "否（可限制）", "—", "本次未改动"],
    ["EX-05", "执行可信度", "样本口径是否有唯一声明",
     "不阻塞（可限制保留）",
     "296 唯一交易 / 206 候选 / 201 事件三套口径",
     "样本构建", "否（可限制）", "—", "本次未改动"],
    ["EX-06", "执行可信度", "前向累积起算时点是否确定",
     "不阻塞（转用户决策）",
     "起算须等 DQ-03/DQ-05 闭环",
     "流程安排", "否（转决策）", "UD-03", "本次未改动"],
]
# 已通过项
for gid, item, ev in [
    ("P-01", "数据覆盖", f"K 线/OI 覆盖 {n_in} 标的，2021-01-01~2026-10-13 窗口并集连续无洞"),
    ("P-02", "4h 预热", "自动向前取 220 根（ATR 预热 200 根）"),
    ("P-03", "可用时钟（计算侧）", "pit_guard_v1.0.0；决策时刻 = 1h 开盘 + 3600s；未收盘抛错"),
    ("P-04", "固定仓位配对可执行", "be_core.py install() + 逐信号隔离断言 trades==1"),
    ("P-05", "费用口径", "taker 5bps、滑点 3bps、Decimal 同向取整、资金费真实结算事件"),
    ("P-06", "主链路复用", "15 环全在生产代码"),
    ("P-07", "指定时间/事件范围", "start_date/end_date；单元素候选即单事件隔离"),
    ("P-08", "逐笔与版本指纹", "17/19 个产物 + 四类指纹 + manifest 逐文件 sha256"),
    ("P-09", "主要比较指标", "净差额、少亏、错失盈利、逐项移除敏感性、分期一致性均可算"),
]:
    GATE.append([gid, "已通过", item, "通过", ev, "—", "否（已通过）", "—", "本次未改动"])

wr("05_统一准入表.csv",
   ["统一编号", "类别", "检查项", "判定", "证据", "影响的验证环节",
    "是否阻止本次执行", "修复任务", "本次修正说明"], GATE)

gv = Counter(r[3] for r in GATE)
rec("统一准入表 22 项（DQ7 + EX6 + P9）", len(GATE) == 22, len(GATE))
rec("存在「未通过」⇒ 准入未通过", gv.get("未通过", 0) + gv.get("未满足（本次升级）", 0) > 0,
    dict(gv))
rec("DQ-04 已从「可限制保留」升级为「未满足」",
    any(r[0] == "DQ-04" and r[3].startswith("未满足") for r in GATE))
rec("DQ-01/DQ-02 共用同一修复任务 FIX-01",
    len({r[7] for r in GATE if r[0] in ("DQ-01", "DQ-02")}) == 1,
    [r[7] for r in GATE if r[0] in ("DQ-01", "DQ-02")])
rec("EX-02/EX-03 共用同一修复任务 FIX-05",
    len({r[7] for r in GATE if r[0] in ("EX-02", "EX-03")}) == 1)

# ══════════════ 五、必须解决清单（合并同源项） ══════════════════
FIX = [
    ["FIX-01", "获取有判定力的样本外数据", "DQ-01 + DQ-02（同源，合并）",
     "修正范围下可用样本外段 0、可用事件 0 笔",
     "存在一段可证明未接触且 ≥ 判定门槛事件数的数据",
     "数据侧/时间", "否", "前向累积或换数据源；不得用旧数据改名"],
    ["FIX-02", "OI 发布时点证据或声明边界", "DQ-03",
     "oi_available_at_unproven，pit_qualified=false",
     "官方发布时点归档，或把「只能声明计算上不超前」写成明文并长期接受 qualified=False",
     "研究侧", "否", "写边界文档即可，不需改代码"],
    ["FIX-03", "历史合约规格证据，或明确降级", "DQ-04（本次升级）",
     "576 次运行 0 verified；PRECISION-04 无标的可升级；有「无公告变更」硬反例",
     "找到能夹住窗口且覆盖整窗变更证据的官方记录；否则固定标 `historical_spec_unverified`，"
     "只作探索分析、不进正式验证",
     "研究侧", "否", "找不到就长期标未满足，不得用「写清即可」放行"],
    ["FIX-04", "配对代码身份可追溯", "EX-01",
     "CODE_FILES 不含 be_core.py / 02_run_fixed.py",
     "纳入 CODE_FILES，或每单附两文件 sha256 声明",
     "需改代码或改交接格式", "是（或改流程）", "二选一"],
    ["FIX-05", "组合回撤层", "EX-02 + EX-03（同源，合并）",
     "无 max_drawdown；失败条件第 3 条不可判",
     "输出组合净值曲线与 max_drawdown",
     "需新写代码", "是", "不改则只能判失败条件第 1、2 条"],
]
wr("06_必须解决清单.csv",
   ["修复编号", "事项", "对应检查项", "依据", "闭环标准（可验收）", "由谁做",
    "是否改代码", "备注"], FIX)
rec("修复任务 5 个（原 6 项 M 合并为 5）", len(FIX) == 5, len(FIX))

# ══════════════ 六、查询输出 ══════════════════
payload = {
    "version": VERSION,
    "order": "GATE-CLOSE-02",
    "status": "待Codex审核",
    "inputs": {
        "exchangeInfo_snapshot": {"path": os.path.relpath(EXINFO, ROOT), "sha256": sha(EXINFO),
                                  "serverTime": ts(exinfo.get("serverTime")),
                                  "symbols": len(meta)},
        "runs_manifest_v2": {"path": os.path.relpath(MANIFEST_V2, ROOT), "sha256": sha(MANIFEST_V2),
                             "rows": len(mrows)},
        "precision04_report": {"path": os.path.relpath(PREC04_MD, ROOT), "sha256": sha(PREC04_MD)},
    },
    "scope": {
        "observed_all_symbols": len(syms_all),
        "included_crypto_perpetual": n_in,
        "excluded": [r[0] for r in excluded],
        "excluded_rule": "underlyingType != COIN（含 INDEX/crypto_index）或 contractType != PERPETUAL",
        "underlyingType_dist": dict(ut),
        "contractType_dist": dict(ct),
        "excluded_data_range": {k: [ts(v[0]), ts(v[1])] for k, v in excl_range.items()},
    },
    "time_axis": {
        "observed_end_1h": observed_end,
        "data_end_1h": ts(data_end_1h),
        "data_end_15m": ts(data_end_15m),
        "data_end_4h": ts(data_end_4h),
        "gap_hours_between_observed_and_data_end":
            round((ms(ts(data_end_15m)) - ms(observed_end)) / 3600000.0, 1),
    },
    "segments": {"total": len(final), "observed": cats.get("已证明被观察", 0),
                 "data_bad": cats.get("数据不合格", 0),
                 "unknown_only": cats.get("资格无法证明", 0), "usable": 0},
    "historical_spec": {"runs": len(mrows), "verified": qual.get("verified", 0),
                        "unverified_historical": qual.get("unverified_historical", 0),
                        "contradicted": qual.get("contradicted", 0),
                        "formal_pass_allowed_true": sum(1 for v in fpa if v == "True"),
                        "releasable_subset": "无",
                        "judgement": "未满足（正式历史验证）；可作带假设探索分析"},
    "fix_tasks": len(FIX),
    "checks": CHECK,
    "pass": sum(1 for c in CHECK if c["status"] == "PASS"),
    "fail": sum(1 for c in CHECK if c["status"] == "FAIL"),
}
os.makedirs(LOGS, exist_ok=True)
with open(os.path.join(LOGS, "00_verify.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(json.dumps({
    "version": VERSION,
    "scope": {"observed": len(syms_all), "included": n_in, "excluded": [r[0] for r in excluded]},
    "time_axis": payload["time_axis"],
    "segments": payload["segments"],
    "historical_spec": payload["historical_spec"],
    "pass": payload["pass"], "fail": payload["fail"],
}, ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
