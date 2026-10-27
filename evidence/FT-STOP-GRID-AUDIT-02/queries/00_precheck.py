# -*- coding: utf-8 -*-
"""00 开跑前核对：FT-STOP-GRID-02 是否真的"恢复盈利跟踪 + 其余条件不变"。

只读上游与 freqtrade 源码。产出 out/00_开跑前核对.json。
必须在读取任何逐笔结果之前跑完（脚本本身不读 out/trades_*.csv）。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path

from _lib import BASE_DIR, HERE, OUT, TIER_PCT, TIERS, SUFFIXES

UP = BASE_DIR / "evidence/FT-STOP-GRID-02"
GRID01 = BASE_DIR / "evidence/FT-STOP-GRID-01"
ORIG_20 = BASE_DIR / "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py"
ORIG_E09 = BASE_DIR / "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py"
FT = BASE_DIR / "evidence/FT-BASELINE-01/.venv/lib/python3.11/site-packages/freqtrade"

BASE_PY = UP / "user_data/strategies/WyckoffStopGrid02Base.py"
GEN_PY = UP / "code/00_generate_strategies.py"
RUN_PY = UP / "code/01_run_all_grid_backtests.py"

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


def norm(src: str, cls_from: str = "", cls_to: str = "") -> str:
    s = src
    if cls_from and cls_to:
        s = s.replace(cls_from, cls_to)
    s = s.replace("evidence/FT-STOP-GRID-01", "evidence/<ROUND>")
    s = s.replace("evidence/FT-STOP-GRID-02", "evidence/<ROUND>")
    s = re.sub(r"FT-STOP-GRID-0\d", "FT-STOP-GRID-<N>", s)
    return s


def funcs(path: Path, cls_hint: str = "") -> dict[str, str]:
    """AST 取类方法源码，键 = Class.method。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for f in node.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{f.name}"] = ast.get_source_segment(path.read_text(encoding="utf-8"), f) or ""
    return out


def class_attrs(path: Path) -> dict[str, dict]:
    """取每个类的简单类属性（数字/字符串/布尔）。"""
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
            out[node.name] = d
    return out


def identifiers(path: Path) -> set[str]:
    """AST 里所有标识符（不含注释/字符串）。"""
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


# ---------------------------------------------------------------- A 文件与参数
def check_files() -> None:
    files = sorted((UP / "user_data/strategies").glob("Wyckoff_C1_Stop*.py"))
    add("A_文件", "策略文件数", len(files), len(files) == 36)
    add("A_文件", "基类存在", BASE_PY.exists(), BASE_PY.exists())
    add("A_文件", "生成脚本SHA256", sha(GEN_PY))
    add("A_文件", "基类SHA256", sha(BASE_PY))
    add("A_文件", "运行脚本SHA256", sha(RUN_PY))

    # 逐文件解析类属性，确认档位比例唯一、组参数无错位
    rows, bad = [], []
    for p in files:
        ca = class_attrs(p)
        cname = p.stem
        if cname not in ca:
            bad.append({"文件": p.name, "问题": "类名与文件名不符", "类": list(ca)})
            continue
        a = ca[cname]
        parts = cname.split("_")
        tier, suffix = parts[2], "_".join(parts[3:])
        exp_vs = int(suffix.split("_")[0].replace("Vs", "")) / 10.0
        exp_z = int(suffix.split("_")[1].replace("Z", "")) / 10.0
        row = {
            "文件": p.name, "档位": tier, "参数组": suffix,
            "stoploss_pct": a.get("stoploss_pct"),
            "timing_mode": a.get("timing_mode"),
            "c_vol_ratio_vs": a.get("c_vol_ratio_vs"),
            "c_retest_zone_atr": a.get("c_retest_zone_atr"),
        }
        rows.append(row)
        if a.get("stoploss_pct") != TIER_PCT.get(tier):
            bad.append({"文件": p.name, "问题": "档位比例与文件名不符", "值": a.get("stoploss_pct")})
        if a.get("timing_mode") != "C1":
            bad.append({"文件": p.name, "问题": "timing_mode 非 C1", "值": a.get("timing_mode")})
        if abs((a.get("c_vol_ratio_vs") or -1) - exp_vs) > 1e-9:
            bad.append({"文件": p.name, "问题": "c_vol_ratio_vs 错位", "值": a.get("c_vol_ratio_vs"), "应为": exp_vs})
        if abs((a.get("c_retest_zone_atr") or -1) - exp_z) > 1e-9:
            bad.append({"文件": p.name, "问题": "c_retest_zone_atr 错位", "值": a.get("c_retest_zone_atr"), "应为": exp_z})

    add("A_文件", "逐文件参数异常", bad, not bad, str(bad[:5]))
    add("A_文件", "四档×9组齐全", sorted({(r["档位"], r["参数组"]) for r in rows}) ==
        sorted({(t, s) for t in TIERS for s in SUFFIXES}),
        len({(r["档位"], r["参数组"]) for r in rows}) == 36)
    add("A_文件", "四档止损比例", {t: sorted({r["stoploss_pct"] for r in rows if r["档位"] == t}) for t in TIERS})


# ------------------------------------------------- B 与 GRID-01 的函数级差异
def check_diff_vs_grid01() -> None:
    a = funcs(GRID01 / "user_data/strategies/WyckoffStopGridBase.py")
    b = funcs(BASE_PY)
    na = {k.replace("WyckoffStopGridBase.", "WyckoffStopGrid02Base."): norm(v, "WyckoffStopGridBase", "WyckoffStopGrid02Base")
          for k, v in a.items()}
    nb = {k: norm(v, "WyckoffStopGridBase", "WyckoffStopGrid02Base") for k, v in b.items()}
    only_a = sorted(set(na) - set(nb))
    only_b = sorted(set(nb) - set(na))
    diff = sorted(k for k in set(na) & set(nb) if na[k] != nb[k])
    add("B_相对上一轮", "函数数(01/02)", [len(na), len(nb)])
    add("B_相对上一轮", "仅01有", only_a, not only_a)
    add("B_相对上一轮", "仅02有", only_b, not only_b)
    add("B_相对上一轮", "内容不同的函数", diff)
    add("B_相对上一轮", "只改了预期函数",
        set(diff) <= {"WyckoffStopGrid02Base.custom_stoploss"},
        f"实际差异 {diff}")

    # 关键函数必须逐字节一致
    for fn in ("custom_stake_amount", "confirm_trade_entry", "populate_indicators",
               "populate_entry_trend", "custom_exit"):
        k = f"WyckoffStopGrid02Base.{fn}"
        same = k in na and k in nb and na[k] == nb[k]
        add("B_相对上一轮", f"{fn} 与上一轮一致", same, same)


# ------------------------------------- C 跟踪算法是否真的恢复成原版
def code_only(src: str) -> str:
    """去掉注释与字符串字面量，只留代码（AST 层面比较时注释本无影响，此处用于字面量扫描）。"""
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


def trailing_block(src: str) -> str:
    """抽出'棘轮+2R判定'那段。"""
    i = src.find("t_id = trade.id or 0")
    j = src.find('effective_stop = state["current_stop"]')
    if i < 0 or j < 0:
        return ""
    return src[i:j]


def block_fp(src: str, extra_norm: dict[str, str] | None = None) -> str:
    """把一段代码归一化后取 AST 指纹（忽略注释、空白、变量别名）。"""
    import textwrap
    b = trailing_block(src)
    if not b:
        return ""
    b = textwrap.dedent(b)
    for a, c in (extra_norm or {}).items():
        b = b.replace(a, c)
    try:
        return ast.dump(ast.parse(b), annotate_fields=True, include_attributes=False)
    except SyntaxError:
        return "PARSE_FAIL:" + b[:200]


def check_trailing_restored() -> None:
    cur = funcs(BASE_PY).get("WyckoffStopGrid02Base.custom_stoploss", "")
    o20 = funcs(ORIG_20).get("WyckoffStop20ParamExplorationBase.custom_stoploss", "")
    oe9 = funcs(ORIG_E09).get("WyckoffParamExplorationBase.custom_stoploss", "")

    add("C_跟踪算法", "当前实现棘轮段长度", len(trailing_block(cur)), len(trailing_block(cur)) > 0)
    # 原版 20% 方案：仅"初始止损"变量名不同
    fp_cur = block_fp(cur, {"stop20_initial": "initial_stop"})
    fp_20 = block_fp(o20, {"stop20_initial": "initial_stop"})
    add("C_跟踪算法", "与 FT-STOP-20-01 棘轮段 AST 一致（仅初始止损变量名不同）", fp_cur == fp_20, fp_cur == fp_20)
    # 原版结构止损方案：p0 来自候选结构价，变量名归一后应一致
    fp_9 = block_fp(oe9, {'"p0": p0': '"p0": initial_stop', '"current_stop": p0': '"current_stop": initial_stop'})
    add("C_跟踪算法", "与 FT-ENTRY-09 棘轮段 AST 一致（仅初始止损来源不同）", fp_cur == fp_9, fp_cur == fp_9)

    def tail(src: str) -> str:
        i = src.find('effective_stop = state["current_stop"]')
        return src[i:] if i >= 0 else ""

    add("C_跟踪算法", "与 FT-STOP-20-01 触发/退出段逐字节一致", tail(cur) == tail(o20), tail(cur) == tail(o20))

    add("C_跟踪算法", "当前 1R 定义行",
        [l.strip() for l in cur.splitlines() if "actual_r =" in l or "trigger_2r =" in l or "initial_stop =" in l])
    add("C_跟踪算法", "原版(20%) 1R 定义行",
        [l.strip() for l in o20.splitlines() if "actual_r =" in l or "trigger_2r =" in l or "stop20_initial =" in l])
    add("C_跟踪算法", "原版(结构) 1R 定义行",
        [l.strip() for l in oe9.splitlines() if "actual_r =" in l or "trigger_2r =" in l or "p0 =" in l])


# ------------------------------------------------------------- D 关键公式
def check_formulas() -> None:
    src = BASE_PY.read_text(encoding="utf-8")
    f = funcs(BASE_PY)["WyckoffStopGrid02Base.custom_stoploss"]
    checks = {
        "初始止损 = 成交价×(1−档位比例)": "initial_stop = actual_entry * (1.0 - pct)" in f,
        "1R = 成交价×档位比例（入场时冻结）": "actual_r = actual_entry * pct" in f,
        "2R 触发价 = 成交价+2×1R": "trigger_2r = actual_entry + 2.0 * actual_r" in f,
        "2R 判定用已收盘 4h 的 close": 'max_4h_close = float(sub_4h["close"].max())' in f,
        "2R 判定为 >= 触发价": "if max_4h_close >= trigger_2r:" in f,
        "跟踪值取已收盘 4h 的 l10 末值": 'latest_l10 = float(sub_4h["l10"].iloc[-1])' in f,
        "棘轮条件为严格上移": 'if math.isfinite(latest_l10) and latest_l10 > state["current_stop"]:' in f,
        "价格已破止损时不再上报新止损": "if current_rate <= effective_stop:" in f and "return 0.0" in f,
    }
    add("D_公式", "源码断言", checks, all(checks.values()), str([k for k, v in checks.items() if not v]))

    # l10 / close_ms 构造
    pop = funcs(BASE_PY)["WyckoffStopGrid02Base.populate_indicators"]
    c2 = {
        "l10 = low.rolling(10).min()（右侧窗口，无居中）": 'df_4h["l10"] = df_4h["low"].rolling(10).min()' in pop,
        "close_ms = open_ms + 4h": 'df_4h["close_ms"] = df_4h["open_ms"] + 4 * 3600 * 1000' in pop,
        "只保留 close_ms/close/low/l10 四列": 'df_4h[["close_ms", "close", "low", "l10"]]' in pop,
    }
    add("D_公式", "4h 指标构造", c2, all(c2.values()), str([k for k, v in c2.items() if not v]))

    # 静态验证 l10 无居中窗口
    add("D_公式", "rolling 是否出现 center=True", "center=True" in src, "center=True" not in src)


# --------------------------------------------------------- E 止损只上移
def check_monotonic() -> None:
    f = funcs(BASE_PY)["WyckoffStopGrid02Base.custom_stoploss"]
    lines = [l.strip() for l in f.splitlines()]
    writes = [l for l in lines if l.startswith('state["current_stop"] =')]
    add("E_止损只上移", "对 current_stop 的赋值语句", writes)
    # 赋值必须在 if latest_l10 > current_stop 的下一行（严格上移）
    ok_w = False
    for i, l in enumerate(lines):
        if l in writes and i > 0 and 'latest_l10 > state["current_stop"]' in lines[i - 1]:
            ok_w = True
    add("E_止损只上移", "赋值仅一处且紧跟严格上移判断",
        len(writes) == 1 and ok_w, str(writes))
    add("E_止损只上移", "初始化值为初始止损",
        '"current_stop": initial_stop' in f, '"current_stop": initial_stop' in f)

    # 引擎侧：adjust_stop_loss 只上移
    tm = (FT / "persistence/trade_model.py").read_text(encoding="utf-8").splitlines()
    seg = "\n".join(tm[880:900])
    add("E_止损只上移", "引擎 adjust_stop_loss 只上移",
        "stop losses only walk up, never down!" in seg and "higher_stop and not self.is_short" in seg,
        "trade_model.py:881-899")

    # 返回 0.0 的语义：freqtrade 视为无效值 ⇒ 保持原止损（不是"止损=现价"）
    itf = (FT / "strategy/interface.py").read_text(encoding="utf-8").splitlines()
    seg2 = "\n".join(itf[1566:1590])
    add("E_止损只上移", "返回 0.0 的引擎语义",
        "if stop_loss_value_custom and not (" in seg2 and "CustomStoploss function did not return valid stoploss" in seg2,
        "0.0 为假值 ⇒ 走 else 分支，不改写 trade.stop_loss")


# ------------------------------------------------------------ F 240h 取消
def check_no_timeout() -> None:
    ids = identifiers(BASE_PY)
    forbidden = ["timeout", "timeout_240h", "max_holding", "max_holding_days", "trade_duration",
                 "is_2r_activated_old", "custom_exit_timeout"]
    hit = sorted(i for i in forbidden if i in ids)
    add("F_取消超时", "禁用标识符命中", hit, not hit, str(hit))

    src = BASE_PY.read_text(encoding="utf-8")
    code = code_only(src)
    hit240 = re.findall(r"240\s*\*\s*3600|\b240h\b|240\s*小时|max_holding|trade_duration", code)
    add("F_取消超时", "代码中出现 240 小时/持仓时长相关字面量", hit240, not hit240, str(hit240))
    add("F_取消超时", "注释里仍保留的说明（不算问题）",
        [l.strip() for l in src.splitlines() if "240" in l and l.strip().startswith("#")])
    # custom_exit 的语句体必须只有一条 return None（AST 级）
    tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
    ce_node = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            for x in n.body:
                if isinstance(x, ast.FunctionDef) and x.name == "custom_exit":
                    ce_node = x
    stmts = [type(x).__name__ for x in (ce_node.body if ce_node else [])]
    ret_ok = (ce_node is not None and len(ce_node.body) == 1
              and isinstance(ce_node.body[0], ast.Return)
              and isinstance(ce_node.body[0].value, ast.Constant)
              and ce_node.body[0].value.value is None)
    add("F_取消超时", "custom_exit 语句类型", stmts)
    add("F_取消超时", "custom_exit 唯一语句是 return None", ret_ok, str(stmts))

    # 结果包自报的 trailing_stop 之后核对（此处只核策略属性）
    ca = class_attrs(BASE_PY).get("WyckoffStopGrid02Base", {})
    add("F_取消超时", "类属性 use_custom_stoploss / trailing_stop",
        {"use_custom_stoploss": ca.get("use_custom_stoploss"), "trailing_stop": ca.get("trailing_stop")})


# -------------------------------------------------------- G 无未来数据（静态）
def check_no_lookahead() -> None:
    f = funcs(BASE_PY)["WyckoffStopGrid02Base.custom_stoploss"]
    cond = 'sub_4h = df_4h_info[(df_4h_info["close_ms"] <= current_ms) & (df_4h_info["close_ms"] >= entry_ms)]'
    add("G_无未来数据", "4h 过滤条件", cond in f, cond in f)
    add("G_无未来数据", "条件为 close_ms <= 决策时刻", "close_ms\"] <= current_ms" in f)

    # freqtrade 侧：custom_stoploss 的 current_time / current_rate 来源
    bt = (FT / "optimize/backtesting.py").read_text(encoding="utf-8").splitlines()
    call_exit = "\n".join(bt[991:1001])
    add("G_无未来数据", "回测传入 current_time = 本根 15m 开盘时间",
        "row[DATE_IDX].to_pydatetime()" in call_exit, call_exit.strip()[:200])
    add("G_无未来数据", "回测传入 low/high = 本根 15m 最低/最高",
        "low=row[LOW_IDX]" in call_exit and "high=row[HIGH_IDX]" in call_exit, call_exit.strip()[:200])

    itf = (FT / "strategy/interface.py").read_text(encoding="utf-8").splitlines()
    seg = "\n".join(itf[1551:1585])
    add("G_无未来数据", "custom_stoploss 的 current_rate = 本根最高价（多头）",
        "bound = low if trade.is_short else high" in seg and "current_rate=(bound or current_rate)" in seg,
        "interface.py:1559,1573")
    add("G_无未来数据", "上一根止损已破时不再调用 custom_stoploss",
        "dir_correct = (" in seg and "if self.use_custom_stoploss and dir_correct:" in seg,
        "interface.py:1552,1566 ⇒ 退出那根 K 线用的是上一根算出的止损")

    # 4h 收盘时刻与 15m 开盘时刻的对齐：close_ms 必为 4h 的整数倍
    add("G_无未来数据", "4h close_ms 对齐说明",
        "close_ms = open_ms + 4h；15m 决策时刻为 15m 整数倍；close_ms<=current_ms 等价于'该 4h 已收完'")


# ------------------------------------------------------- H 数据与配置
def check_data_config() -> None:
    rows = {}
    for tag in ("FT-STOP-GRID-01", "FT-STOP-GRID-02", "FT-EXIT-01", "FT-ENTRY-02", "FT-ENTRY-07"):
        p = BASE_DIR / f"evidence/{tag}/user_data/data"
        rows[tag] = {"存在": p.exists(), "realpath": os.path.realpath(p) if p.exists() else None,
                     "链接指向": os.readlink(p) if p.is_symlink() else "(实体目录)"}
    add("H_数据与配置", "data 路径", rows)
    rp = {v["realpath"] for v in rows.values() if v["存在"]}
    add("H_数据与配置", "全部指向同一份数据", len(rp) == 1, str(rp))

    a = json.load(open(GRID01 / "user_data/config.json", encoding="utf-8"))
    b = json.load(open(UP / "user_data/config.json", encoding="utf-8"))
    diffs = {k: [a[k], b[k]] for k in set(a) & set(b) if a[k] != b[k]}
    add("H_数据与配置", "与上一轮配置差异", diffs, set(diffs) <= {"user_data_dir", "datadir"}, str(diffs))
    add("H_数据与配置", "冻结参数", {k: b.get(k) for k in
                                 ("max_open_trades", "dry_run_wallet", "timeframe", "fee",
                                  "trading_mode", "margin_mode", "stake_amount", "tradable_balance_ratio")})

    # timerange 与命令行
    run_src = RUN_PY.read_text(encoding="utf-8")
    add("H_数据与配置", "timerange", re.findall(r'"--timerange",\s*"([^"]+)"', run_src))
    add("H_数据与配置", "cache/export", re.findall(r'"--cache",\s*"([^"]+)"', run_src) +
        re.findall(r'"--export",\s*"([^"]+)"', run_src))
    add("H_数据与配置", "并发数", re.findall(r"max_workers\s*=\s*(\d+)", run_src))

    # 数据文件清点
    ddir = BASE_DIR / "evidence/FT-ENTRY-02/user_data/data/binance/futures"
    kinds: dict[str, int] = {}
    for p in ddir.glob("*.feather"):
        parts = p.stem.split("-")
        kinds["-".join(parts[-2:])] = kinds.get("-".join(parts[-2:]), 0) + 1
    add("H_数据与配置", "数据文件分类", dict(sorted(kinds.items())))
    add("H_数据与配置", "数据文件总数", sum(kinds.values()))


# ------------------------------------------- I 其余条件与上一轮一致（补充）
def check_others() -> None:
    # 配对资格规则必须与上一轮一致
    a = sha(GRID01 / "user_data/strategies/pair_eligibility_rules.json")
    b = sha(UP / "user_data/strategies/pair_eligibility_rules.json")
    add("I_其余条件", "pair_eligibility_rules SHA 相同", a == b, f"{a} vs {b}")
    add("I_其余条件", "pair_eligibility_rules SHA", {"01": a, "02": b})

    # 风控四件套仍在
    f = funcs(BASE_PY)["WyckoffStopGrid02Base.custom_stake_amount"]
    c = {
        "risk_budget = equity × 0.005": "risk_budget = equity * 0.005" in f,
        "raw_stake = risk_budget / pct": "raw_stake = qty * current_rate" in f and "qty = risk_budget / r_dist" in f,
        "最小下单额硬性拒单": "raw_stake < min_stake" in f and "return 0.0" in f,
        "单笔名义上限 30%": "max_single = equity * 0.30" in f,
        "流动性上限 prev_qv×0.001": "liq_cap = prev_qv * 0.001" in f,
    }
    add("I_其余条件", "风控四件套", c, all(c.values()), str([k for k, v in c.items() if not v]))

    # 1R 在"下单额"与"止损"两处口径可能不一致（预登记观察点）
    add("I_其余条件", "下单额用的风险距离", "r_dist = current_rate * pct（下单时价格）")
    add("I_其余条件", "止损用的 1R", "actual_r = actual_entry * pct（实际成交价）")
    add("I_其余条件", "两处口径差异的影响", "成交价 ≠ 下单时价格时，实际风险 ≠ 恰好 0.5%，需在交付后逐笔核")


def main() -> dict:
    check_files()
    check_diff_vs_grid01()
    check_trailing_restored()
    check_formulas()
    check_monotonic()
    check_no_timeout()
    check_no_lookahead()
    check_data_config()
    check_others()

    return {
        "工单": "FT-STOP-GRID-AUDIT-02 开跑前核对",
        "核对对象": str(UP),
        "问题数": len(PROB),
        "问题": PROB,
        "明细": RES,
    }


if __name__ == "__main__":
    r = main()
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "00_开跑前核对.json"
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"问题数": r["问题数"], "问题": r["问题"]}, ensure_ascii=False, indent=1))
    print(f"\n写出 {p}")
