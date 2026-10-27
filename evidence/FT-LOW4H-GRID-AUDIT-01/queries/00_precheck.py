# -*- coding: utf-8 -*-
"""00 开跑前核对：FT-LOW4H-GRID-01 的 144 组是否按冻结口径搭建。

只读上游源码与行情数据，**不读任何逐笔结果**（脚本内不出现 out/trades_*.csv）。
产出 out/00_开跑前核对.json。

上游尚未产出时，本脚本不报错，而是给出 状态=BLOCKED 与「待核清单」，
把「开跑前该核什么」先固定下来，避免事后按结果倒推标准。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

import _lib as L

RES: dict = {}
PROB: list[str] = []


def add(section: str, key: str, value, ok: bool | None = None, note: str = "") -> None:
    RES.setdefault(section, {})[key] = value
    if ok is False:
        PROB.append(f"{section}/{key}: {note or value}")


def sha(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def class_attrs(path: Path) -> dict[str, dict]:
    """取每个类的简单类属性（含 `x: float = 0.05` 这种带注解的写法）。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            d = {}
            for s in node.body:
                if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name):
                    try:
                        d[s.targets[0].id] = ast.literal_eval(s.value)
                    except Exception:
                        pass
                elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) and s.value is not None:
                    try:
                        d[s.target.id] = ast.literal_eval(s.value)
                    except Exception:
                        pass
            out[node.name] = d
    return out


def funcs(path: Path) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for f in node.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{f.name}"] = ast.get_source_segment(src, f) or ""
    return out


def identifiers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(n.name)
    return names


def code_only(src: str) -> str:
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except Exception:
        return src
    return " ".join(out)


# --------------------------------------------------------------- 0 上游状态
def check_upstream() -> bool:
    st = L.up_state()
    add("0_上游", "状态", st)
    add("0_上游", "可用（有逐笔结果）", st["可用"], None,
        "上游尚无逐笔结果，后 6 项审核停留在'待执行'（不是参数问题）")
    if not st["目录存在"]:
        add("0_上游", "目录缺失", str(L.UP), False, "目录不存在")
    return st["可用"]


# ----------------------------------------------------- D 行情前提（与上游无关）
def check_data() -> None:
    d = L.DATA
    kinds: dict[str, int] = {}
    for p in d.glob("*.feather"):
        m = re.search(r"-(\d+[mhd])-([a-z_]+)\.feather$", p.name)
        if m:
            k = f"{m.group(1)}-{m.group(2)}"
            kinds[k] = kinds.get(k, 0) + 1
    add("D_数据", "文件分类", dict(sorted(kinds.items())))
    add("D_数据", "4h K 线文件数", kinds.get("4h-futures", 0), kinds.get("4h-futures", 0) == 74)
    add("D_数据", "15m K 线文件数", kinds.get("15m-futures", 0), kinds.get("15m-futures", 0) == 74)

    # 4h 网格对齐与缺口：这是"最近一根已收盘 4h"语义能否成立的前提
    rows, bad = [], []
    for p in sorted(d.glob("*-4h-futures.feather")):
        df = pd.read_feather(p)
        ms = L._ms_of_series(df["date"]).astype("int64")
        d_ms = np.diff(ms)
        off = int(ms[0] % L.BAR_4H_MS)
        rows.append({
            "文件": p.name.replace("-4h-futures.feather", ""),
            "根数": int(len(ms)),
            "起点": str(pd.Timestamp(int(ms[0]), unit="ms", tz="UTC")),
            "终点": str(pd.Timestamp(int(ms[-1]), unit="ms", tz="UTC")),
            "起点对齐余数ms": off,
            "步长唯一值": sorted({int(x) for x in np.unique(d_ms)}),
            "缺口数(>4h)": int((d_ms > L.BAR_4H_MS).sum()),
            "重复时刻": int((d_ms == 0).sum()),
        })
    al = [r for r in rows if r["起点对齐余数ms"] != 0]
    step_bad = [r["文件"] for r in rows if r["步长唯一值"] != [L.BAR_4H_MS]]
    dup = [r["文件"] for r in rows if r["重复时刻"] > 0]
    gaps = {r["文件"]: r["缺口数(>4h)"] for r in rows if r["缺口数(>4h)"] > 0}
    add("D_数据", "4h 起点未对齐 4h 网格的文件", al, not al, str(al[:5]))
    add("D_数据", "步长非 4h 的文件", step_bad, not step_bad, str(step_bad[:5]))
    add("D_数据", "存在重复时刻的文件", dup, not dup, str(dup[:5]))
    add("D_数据", "存在缺口的文件数", len(gaps), None, "缺口本身不违规，但会影响'最近一根已收盘'的连续性")
    add("D_数据", "缺口明细（前 10）", dict(list(sorted(gaps.items()))[:10]))
    add("D_数据", "4h 总根数", int(sum(r["根数"] for r in rows)))
    pd.DataFrame(rows).to_csv(L.OUT / "00_4h数据体检.csv", index=False, encoding="utf-8-sig")

    # 冻结参数（与上一轮对照）
    cfg = L.PREV / "user_data/config.json"
    if cfg.exists():
        c = json.load(open(cfg, encoding="utf-8"))
        add("D_数据", "上一轮冻结参数（作为本轮对照基线）",
            {k: c.get(k) for k in ("max_open_trades", "dry_run_wallet", "timeframe", "fee",
                                   "trading_mode", "margin_mode", "stake_amount",
                                   "tradable_balance_ratio")})
        add("D_数据", "上一轮 timerange（运行脚本内）",
            re.findall(r'"--timerange",\s*"([^"]+)"',
                       (L.PREV / "code/01_run_all_grid_backtests.py").read_text(encoding="utf-8"))
            if (L.PREV / "code/01_run_all_grid_backtests.py").exists() else [])


# ------------------------------------------------- P1 规模与参数映射
def check_scale(combos: list[str], rows: list[dict]) -> None:
    add("P1_规模", "策略文件数", len(rows), len(rows) == L.EXPECT_GROUPS,
        f"应为 {L.EXPECT_GROUPS}")
    add("P1_规模", "组合数", len(combos), len(combos) == L.EXPECT_COMBOS, f"应为 {L.EXPECT_COMBOS}")
    add("P1_规模", "组合名单", combos)
    cell = {(r["组合"], r["参数组"]) for r in rows}
    add("P1_规模", "组合×参数组 去重格数", len(cell), len(cell) == L.EXPECT_GROUPS)
    miss = [(c, s) for c in combos for s in L.SUFFIXES if (c, s) not in cell]
    add("P1_规模", "缺失格", miss[:20], not miss, f"缺 {len(miss)} 格")
    extra = [r for r in rows if r["参数组"] not in L.SUFFIXES]
    add("P1_规模", "参数组名不在冻结名单内", extra, not extra, str(extra[:5]))

    # 逐文件：类名=文件名、类属性与名字一致（本轮 16 组合 = 初始止损 S × 跟踪距离 D 的 4×4）
    bad = []
    strat = L.UP / "user_data/strategies"
    for r in rows:
        p = strat / r["文件"]
        ca = class_attrs(p)
        stem = p.stem
        if stem not in ca:
            bad.append({"文件": p.name, "问题": "类名与文件名不符", "类": list(ca)})
            continue
        a = ca[stem]
        vs = int(r["参数组"].split("_")[0].replace("Vs", "")) / 10.0
        z = int(r["参数组"].split("_")[1].replace("Z", "")) / 10.0
        parts = r["组合"].split("_")
        if len(parts) != 2 or not parts[0].startswith("S") or not parts[1].startswith("D"):
            bad.append({"文件": p.name, "问题": "组合名不符合 S##_D## 结构", "组合": r["组合"]})
            continue
        exp_s = int(parts[0][1:]) / 100.0
        exp_d = int(parts[1][1:]) / 100.0
        if a.get("timing_mode") != "C1":
            bad.append({"文件": p.name, "问题": "timing_mode 非 C1", "值": a.get("timing_mode")})
        if abs(float(a.get("stoploss_s", -1)) - exp_s) > 1e-9:
            bad.append({"文件": p.name, "问题": "stoploss_s 与组合名不符", "值": a.get("stoploss_s"), "应": exp_s})
        if abs(float(a.get("trail_d", -1)) - exp_d) > 1e-9:
            bad.append({"文件": p.name, "问题": "trail_d 与组合名不符", "值": a.get("trail_d"), "应": exp_d})
        if abs(float(a.get("c_vol_ratio_vs", -1)) - vs) > 1e-9:
            bad.append({"文件": p.name, "问题": "c_vol_ratio_vs 错位", "值": a.get("c_vol_ratio_vs"), "应": vs})
        if abs(float(a.get("c_retest_zone_atr", -1)) - z) > 1e-9:
            bad.append({"文件": p.name, "问题": "c_retest_zone_atr 错位", "值": a.get("c_retest_zone_atr"), "应": z})
    add("P1_规模", "逐文件参数错位", bad[:20], not bad, f"{len(bad)} 个文件错位")

    # 16 个组合的参数差异必须只体现在"止损比例/缓冲"这一维
    dims: dict[str, set] = {}
    for r in rows:
        ca = class_attrs(strat / r["文件"])
        a = ca.get(Path(r["文件"]).stem, {})
        for k, v in a.items():
            if isinstance(v, (int, float, str)) and k not in ("timing_mode",):
                dims.setdefault(k, set()).add(v)
    add("P1_规模", "各类属性取值集合（组合间差异维度）",
        {k: sorted(v, key=str) for k, v in dims.items()})


# --------------------------------------------- P2 止损取价（最近一根已收盘4h低点）
def check_stop_basis() -> None:
    base = L.UP / "user_data/strategies"
    cands = [p for p in base.glob("*.py") if "Base" in p.name]
    if not cands:
        add("P2_止损取价", "基类文件", None, False, "未找到 *Base*.py")
        return
    bp = sorted(cands, key=lambda p: p.stat().st_size, reverse=True)[0]
    add("P2_止损取价", "基类文件", bp.name)
    add("P2_止损取价", "基类SHA256", sha(bp))
    f = funcs(bp)
    cs = [v for k, v in f.items() if k.endswith(".custom_stoploss")]
    if not cs:
        add("P2_止损取价", "custom_stoploss", None, False, "未找到 custom_stoploss")
        return
    src = cs[0]
    chk = {
        "过滤条件含 close_ms <= 决策时刻": bool(re.search(r'close_ms"\]\s*<=\s*current_ms', src)),
        "取的是 low 列（不是 close/l10）": bool(re.search(r'\["low"\]', src)),
        "取的是最后一根（iloc[-1] 或 tail）": ("iloc[-1]" in src or ".tail(1)" in src),
        "止损 = 基准价 × (1 − 比例)": bool(re.search(r"\(1\.0?\s*-\s*", src)),
        "不得出现 l10（本轮不是 L10 跟踪）": "l10" not in src,
    }
    add("P2_止损取价", "源码断言", chk, all(chk.values()),
        str([k for k, v in chk.items() if not v]))
    add("P2_止损取价", "custom_stoploss 源码", src)


# ------------------------------------------------- P3 入场即跟踪（无 2R）
def check_no_2r() -> None:
    base = [p for p in (L.UP / "user_data/strategies").glob("*.py") if "Base" in p.name]
    if not base:
        add("P3_入场即跟踪", "基类文件", None, False, "未找到")
        return
    bp = sorted(base, key=lambda p: p.stat().st_size, reverse=True)[0]
    ids = identifiers(bp)
    forbid = ["trigger_2r", "is_2r_activated", "actual_r", "activate_ms", "activation"]
    hit = sorted(i for i in forbid if i in ids)
    add("P3_入场即跟踪", "2R 门槛标识符命中", hit, not hit, str(hit))
    code = code_only(bp.read_text(encoding="utf-8"))
    lit = re.findall(r"2\.0\s*\*\s*actual_r|2R|2\s*R\b", code)
    add("P3_入场即跟踪", "代码中 2R 字面量", lit, not lit, str(lit))
    f = [v for k, v in funcs(bp).items() if k.endswith(".custom_stoploss")]
    if f:
        add("P3_入场即跟踪", "首次调用即允许上移（无激活前置）",
            "is_2r_activated" not in f[0], "is_2r_activated" not in f[0])


# -------------------------------------------------------- P4 无 240 小时退出
def check_no_timeout() -> None:
    base = [p for p in (L.UP / "user_data/strategies").glob("*.py") if "Base" in p.name]
    if not base:
        add("P4_取消超时", "基类文件", None, False, "未找到")
        return
    bp = sorted(base, key=lambda p: p.stat().st_size, reverse=True)[0]
    src = bp.read_text(encoding="utf-8")
    tree = ast.parse(src)
    ce = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            for x in n.body:
                if isinstance(x, ast.FunctionDef) and x.name == "custom_exit":
                    ce = x
    ok = (ce is not None and len(ce.body) == 1
          and isinstance(ce.body[0], ast.Return)
          and isinstance(ce.body[0].value, ast.Constant)
          and ce.body[0].value.value is None)
    add("P4_取消超时", "custom_exit 唯一语句是 return None", ok,
        str([type(x).__name__ for x in (ce.body if ce else [])]))
    code = code_only(src)
    hit = re.findall(r"240\s*\*\s*3600|max_holding|trade_duration|\b240h\b", code)
    add("P4_取消超时", "代码中 240h/持仓时长字面量", hit, not hit, str(hit))


# ------------------------------------------------------------ P5 止损只上移
def check_monotonic() -> None:
    base = [p for p in (L.UP / "user_data/strategies").glob("*.py") if "Base" in p.name]
    if not base:
        add("P5_只上移", "基类文件", None, False, "未找到")
        return
    bp = sorted(base, key=lambda p: p.stat().st_size, reverse=True)[0]
    f = [v for k, v in funcs(bp).items() if k.endswith(".custom_stoploss")]
    if not f:
        add("P5_只上移", "custom_stoploss", None, False, "未找到")
        return
    src = f[0]
    lines = [l.strip() for l in src.splitlines()]
    writes = [l for l in lines if re.match(r'state\["current_stop"\]\s*=', l)]
    ok_w = False
    for i, l in enumerate(lines):
        if l in writes and i > 0 and "> state[\"current_stop\"]" in lines[i - 1]:
            ok_w = True
    add("P5_只上移", "对 current_stop 的赋值语句", writes)
    add("P5_只上移", "赋值仅一处且紧跟严格上移判断", len(writes) == 1 and ok_w, str(writes))

    tm = (L.FT / "persistence/trade_model.py").read_text(encoding="utf-8")
    add("P5_只上移", "引擎 adjust_stop_loss 只上移",
        "stop losses only walk up, never down!" in tm)
    itf = (L.FT / "strategy/interface.py").read_text(encoding="utf-8")
    add("P5_只上移", "返回 0.0 语义 = 不改写（不是止损=现价）",
        "CustomStoploss function did not return valid stoploss" in itf)


# ------------------------------------------------------------ P6 仓位口径
def check_sizing() -> None:
    base = [p for p in (L.UP / "user_data/strategies").glob("*.py") if "Base" in p.name]
    if not base:
        add("P6_仓位", "基类文件", None, False, "未找到")
        return
    bp = sorted(base, key=lambda p: p.stat().st_size, reverse=True)[0]
    f = [v for k, v in funcs(bp).items() if k.endswith(".custom_stake_amount")]
    if not f:
        add("P6_仓位", "custom_stake_amount", None, False, "未找到")
        return
    src = f[0]
    chk = {
        "risk_budget = equity × 0.005": "0.005" in src and "risk_budget" in src,
        "按 成交价−初始止损 的风险距离算数量": ("r_dist" in src or "risk_dist" in src or "/" in src),
        "超预算/最小下单额硬性拒单（return 0.0）": "return 0.0" in src,
        "单笔名义上限": "0.30" in src or "max_single" in src,
        "流动性上限": "0.001" in src or "liq_cap" in src,
    }
    add("P6_仓位", "源码断言", chk, all(chk.values()), str([k for k, v in chk.items() if not v]))
    add("P6_仓位", "custom_stake_amount 源码", src)


# --------------------------------------------------- P8 派生类属性是否真正贯通
def check_attr_wiring(rows: list[dict]) -> None:
    """派生类声明的属性，基类到底读了没有、读的是不是同一个。

    这是本轮最容易出错的地方：16 种组合靠 stoploss_s（初始止损）与 trail_d（跟踪距离）
    两个属性区分，只要基类读错名字，144 组就会塌缩成同一个策略。
    """
    base = [p for p in (L.UP / "user_data/strategies").glob("*.py") if "Base" in p.name]
    if not base:
        add("P8_属性贯通", "基类文件", None, False, "未找到")
        return
    bp = sorted(base, key=lambda p: p.stat().st_size, reverse=True)[0]
    fa = funcs(bp)
    cs = next((v for k, v in fa.items() if k.endswith(".custom_stoploss")), "")
    csa = next((v for k, v in fa.items() if k.endswith(".custom_stake_amount")), "")
    getattr_re = re.compile(r'getattr\(\s*self\s*,\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']')
    stop_reads = sorted(set(getattr_re.findall(cs)))
    size_reads = sorted(set(getattr_re.findall(csa)))
    add("P8_属性贯通", "custom_stoploss 读取的属性", stop_reads)
    add("P8_属性贯通", "custom_stake_amount 读取的属性", size_reads)

    base_attrs = class_attrs(bp).get(bp.stem, {})
    add("P8_属性贯通", "基类类属性", base_attrs)

    # 派生类声明了哪些属性
    declared = set()
    for r in rows:
        a = class_attrs(L.UP / "user_data/strategies" / r["文件"]).get(Path(r["文件"]).stem, {})
        declared |= set(a)
    add("P8_属性贯通", "派生类声明的属性", sorted(declared))

    # 声明了但基类全文都没引用 = 死属性（用 AST 标识符判断，避免只看 getattr 造成误报）
    base_ids = identifiers(bp)
    dead = sorted(d for d in declared
                  if d not in base_ids and d not in stop_reads and d not in size_reads
                  and d != "timing_mode")
    add("P8_属性贯通", "派生类声明但基类全文未引用的属性", dead, not dead,
        f"这些属性对行为无影响：{dead}")

    # 基类读了但派生类没声明 = 恒取默认值
    all_reads = set(stop_reads) | set(size_reads)
    fallback = sorted(a for a in all_reads if a not in declared)
    add("P8_属性贯通", "基类读取但派生类未声明（恒取默认值）", fallback, not fallback,
        f"这些属性在所有 144 组里取同一个默认值：{fallback}")

    # 逐组解析：止损实际用的比例 vs 下单用的比例
    res = []
    for r in rows:
        a = class_attrs(L.UP / "user_data/strategies" / r["文件"]).get(Path(r["文件"]).stem, {})
        eff = {**base_attrs, **a}
        stop_pct = float(eff.get(stop_reads[0], 0.20)) if stop_reads else None
        size_pct = float(eff.get(size_reads[0], 0.05)) if size_reads else None
        trail_used = "trail_d" in stop_reads
        res.append({
            "策略": Path(r["文件"]).stem, "组合": r["组合"], "参数组": r["参数组"],
            "止损实际比例": stop_pct, "下单实际比例": size_pct,
            "跟踪距离是否被读取": trail_used,
            "实际风险占权益": (0.005 * stop_pct / size_pct) if (stop_pct and size_pct) else None,
        })
    pd.DataFrame(res).to_csv(L.OUT / "00_属性贯通逐组.csv", index=False, encoding="utf-8-sig")
    add("P8_属性贯通", "止损实际比例的取值集合",
        sorted({x["止损实际比例"] for x in res if x["止损实际比例"] is not None}))
    n_combo = len({x["组合"] for x in res})
    n_pct = len({x["止损实际比例"] for x in res if x["止损实际比例"] is not None})
    add("P8_属性贯通", "16 种组合是否真的产生 16 种不同止损比例",
        n_pct == n_combo, f"{n_combo} 种组合只产生 {n_pct} 种止损比例")
    add("P8_属性贯通", "跟踪距离被基类读取的组合数",
        sum(1 for x in res if x["跟踪距离是否被读取"]), all(x["跟踪距离是否被读取"] for x in res),
        "trail_d 未被读取 ⇒ 4 种跟踪距离全部无效")
    risk = sorted({round(x["实际风险占权益"], 6) for x in res if x["实际风险占权益"]})
    add("P8_属性贯通", "各组实际风险占权益（应恒为 0.005）", risk,
        all(abs(v - 0.005) < 1e-12 for v in risk), f"实际取值 {risk}")


# ------------------------------------------------------------ P7 其余不变
def check_others() -> None:
    if not L.UP.exists():
        add("P7_其余不变", "上游不存在，跳过", None, None)
        return
    a = sha(L.PREV / "user_data/strategies/pair_eligibility_rules.json")
    b = sha(L.UP / "user_data/strategies/pair_eligibility_rules.json")
    add("P7_其余不变", "pair_eligibility_rules SHA 相同", a == b, f"{a} vs {b}")
    for tag in ("FT-STOP-GRID-02", "FT-LOW4H-GRID-01"):
        p = L.BASE_DIR / f"evidence/{tag}/user_data/data"
        add("P7_其余不变", f"{tag} data 路径", {
            "存在": p.exists(),
            "realpath": str(Path(p).resolve()) if p.exists() else None,
        })

    # 两轮用的到底是不是同一份数据：逐文件 SHA-256 全量比对
    def digest(d: Path) -> dict:
        out = {}
        for p in sorted(d.glob("*.feather")):
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for c in iter(lambda: f.read(1 << 20), b""):
                    h.update(c)
            out[p.name] = h.hexdigest()
        return out

    da = digest(L.BASE_DIR / "evidence/FT-ENTRY-02/user_data/data/binance/futures")
    db = digest(L.BASE_DIR / "evidence/FT-FACTOR-03/user_data/data/binance/futures")
    diff = sorted(k for k in da if da.get(k) != db.get(k))
    add("P7_其余不变", "两轮数据文件数（ENTRY-02 / FACTOR-03）", [len(da), len(db)])
    add("P7_其余不变", "两轮数据逐文件 SHA-256 是否全同",
        set(da) == set(db) and not diff, f"不同 {len(diff)} 个：{diff[:5]}")
    add("P7_其余不变", "说明",
        "FT-LOW4H-GRID-01 的 data 软链指向 FT-FACTOR-03，与 FT-STOP-GRID-02 指向的 "
        "FT-ENTRY-02 是两个目录但内容逐文件相同 ⇒ 数据可比")


def main() -> dict:
    usable = check_upstream()
    check_data()
    combos, names, rows = L.discover_groups()
    if rows:
        check_scale(combos, rows)
        check_stop_basis()
        check_no_2r()
        check_no_timeout()
        check_monotonic()
        check_sizing()
        check_attr_wiring(rows)
        check_others()
        status = "READY" if not PROB else "READY_有问题"
    else:
        for sec in ("P1_规模", "P2_止损取价", "P3_入场即跟踪", "P4_取消超时",
                    "P5_只上移", "P6_仓位", "P8_属性贯通", "P7_其余不变"):
            add(sec, "未执行", "上游策略文件不存在", None, "等上游产出后再跑本脚本")
        status = "BLOCKED_上游未产出"
        PROB.clear()   # 上游缺失不记为"参数问题"，单独用状态表达

    return {
        "工单": "FT-LOW4H-GRID-AUDIT-01 开跑前核对",
        "状态": status,
        "上游已产出": usable,
        "问题数": len(PROB),
        "问题": PROB,
        "明细": RES,
    }


if __name__ == "__main__":
    r = main()
    L.OUT.mkdir(parents=True, exist_ok=True)
    p = L.OUT / "00_开跑前核对.json"
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"状态": r["状态"], "问题数": r["问题数"], "问题": r["问题"]},
                     ensure_ascii=False, indent=1))
    print(f"\n写出 {p}")
