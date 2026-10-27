# -*- coding: utf-8 -*-
"""01 三套参数口径机械对照（SYSTEM-PARALLEL-AUDIT-01 并行工作 5）。

为什么要这个脚本
----------------
FINAL-01 的 `05_最终交付.md` §五 把两件事列成了「用户待决」：

    U-1  权益基准到底用 2000 还是 5000
    U-2  风险参数用冻结表那套还是反重力那套

按工单第 5 条「纠正旧报告中已失效的判断，不再把已授权的冻结规则重新列为
用户待选项」，这两条**不该再问用户**——冻结规则（FT-VALIDATION-02 认定）
已经授权。真正该记的是一个**事实缺陷**：

    工程内被当作「冻结视图」使用的 exp1/exp2 配置文件，
    与冻结规则表在多个字段上不一致。

本脚本把这个事实机械钉死，不靠记忆、不靠人工抄写。三套口径分别是：

    R  回测冻结规则   `策略/WyckoffMaster/event_trend_v0_1/config.py`
                      （规则指纹 1f1167de…9a8ab，2125 个 run 一致）
    V  模拟盘视图文件 `evidence/SYSTEM-CLOSELOOP-05/config/exp{1,2}_config.json`
    E  模拟盘引擎常量 `框架/backend/live_store.py::INIT_EQUITY` 与
                      `框架/backend/paper_engine.py::FALLBACK`

产物：out/01_口径对照.json
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-PARALLEL-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = BASE / "框架/backend"

CFG_PY = "策略/WyckoffMaster/event_trend_v0_1/config.py"
VIEW1 = "evidence/SYSTEM-CLOSELOOP-05/config/exp1_config.json"
VIEW2 = "evidence/SYSTEM-CLOSELOOP-05/config/exp2_config.json"
# config.py 第 4-5 行注释声明的 SHA256 指向这份冻结规则文档
RULE_DOC = "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"

# 冻结规则文档声明的 SHA（写在 config.py 注释里）
DECLARED_RULE_FP = "1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab"


def sha_of(rel: str) -> dict:
    p = BASE / rel
    b = p.read_bytes()
    return {"path": rel, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))}


def read_cfg_constants() -> dict:
    """用 ast 读 config.py 里 EventTrendConfig 的 dataclass 默认值。

    注意：这些值在 `@dataclass(frozen=True)` 类体内以 AnnAssign 形式出现，
    不是模块级常量，所以必须下钻到 ClassDef.body。
    """
    src = (BASE / CFG_PY).read_text(encoding="utf-8")
    tree = ast.parse(src)
    vals = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for sub in node.body:
            if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                try:
                    vals[sub.target.id] = ast.literal_eval(sub.value)
                except Exception:
                    continue
    return vals


def load_view(rel: str) -> dict:
    p = BASE / rel
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    # ---------- 1. 采三套原始值 ----------
    cfg = read_cfg_constants()
    v1 = load_view(VIEW1)
    v2 = load_view(VIEW2)
    sha_cfg = sha_of(CFG_PY)
    sha_v1 = sha_of(VIEW1)
    sha_v2 = sha_of(VIEW2)
    sha_rule = sha_of(RULE_DOC)
    rule_fp_match = (sha_rule["sha256"] == DECLARED_RULE_FP)

    # 引擎侧常量（只读 import，不写任何东西）
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("PAPER_LEDGER_DIR", "/tmp/wx_parallel_probe/paper")
    os.environ.setdefault("PAPER_LOCK_DIR", "/tmp/wx_parallel_probe/locks")
    os.environ.setdefault("LIVE_DIR", "/tmp/wx_parallel_probe/live")
    pathlib.Path("/tmp/wx_parallel_probe/paper").mkdir(parents=True, exist_ok=True)
    pathlib.Path("/tmp/wx_parallel_probe/locks").mkdir(parents=True, exist_ok=True)
    pathlib.Path("/tmp/wx_parallel_probe/live").mkdir(parents=True, exist_ok=True)
    import live_store as L
    import paper_engine as PE
    FB = dict(PE.FALLBACK)

    # ---------- 2. 逐项对照表 ----------
    # 每项：冻结规则键 → (回测值, 视图键路径, 引擎侧取值方式)
    rows = []

    def add(item, frozen_key, frozen_val, view_key, engine_val, engine_src, note="",
            norm_r=None, norm_v=None, comparable=True):
        """norm_r / norm_v：把两侧换算到同一单位后再比。

        comparable=False 表示两侧口径根本不同（如「R 倍数」vs「回撤百分比」），
        只登记不判一致性 —— 硬比会得出假结论。
        """
        vv1 = _dig(v1, view_key)
        vv2 = _dig(v2, view_key)
        r_cmp = norm_r(frozen_val) if (norm_r and frozen_val is not None) else frozen_val
        v1_cmp = norm_v(vv1) if (norm_v and vv1 is not None) else vv1
        v2_cmp = norm_v(vv2) if (norm_v and vv2 is not None) else vv2
        e_cmp = norm_v(engine_val) if (norm_v and engine_val is not None) else engine_val
        if comparable:
            ok1, ok2, okE = _eq(v1_cmp, r_cmp), _eq(v2_cmp, r_cmp), _eq(e_cmp, r_cmp)
        else:
            ok1 = ok2 = okE = None
        rows.append({
            "项目": item,
            "冻结规则键": frozen_key,
            "R_回测冻结值": frozen_val,
            "V_exp1值": vv1,
            "V_exp2值": vv2,
            "E_引擎实际值": engine_val,
            "E_来源": engine_src,
            "归一后_R": r_cmp,
            "归一后_exp1": v1_cmp,
            "归一后_exp2": v2_cmp,
            "归一后_引擎": e_cmp,
            "可比": comparable,
            "exp1与冻结一致": ok1,
            "exp2与冻结一致": ok2,
            "引擎与冻结一致": okE,
            "备注": note,
        })

    bps = lambda x: None if x is None else round(float(x) * 10000.0, 10)   # 比例 → bps
    days2h = lambda x: None if x is None else float(x) * 24.0              # 天 → 小时
    same = lambda x: x                                                     # 同单位

    add("初始权益 USDT", "initial_equity", cfg.get("initial_equity"),
        "risk.initial_equity_USDT", float(L.INIT_EQUITY), "live_store.INIT_EQUITY",
        "回测=2000；模拟盘视图写 5000 且该字段不生效，引擎恒用常量 5000")
    add("单笔风险预算（权益比例）", "risk_budget_per_trade", cfg.get("risk_budget_per_trade"),
        "risk.risk_per_trade_equity_fraction", FB.get("risk_per_trade_equity_fraction"),
        "paper_engine.FALLBACK", "比例本身一致；因权益基数不同，绝对预算 10 vs 25 USDT")
    add("最大同时持仓", "max_active_positions", cfg.get("max_active_positions"),
        "risk.max_positions", FB.get("max_positions"), "paper_engine.FALLBACK", "")
    add("单仓名义上限（权益比例）", "max_notional_per_position",
        cfg.get("max_notional_per_position"), "risk.single_name_notional_equity_cap",
        FB.get("single_name_notional_equity_cap"), "paper_engine.FALLBACK", "")
    add("总名义上限（权益比例）", "max_total_notional", cfg.get("max_total_notional"),
        "risk.gross_long_notional_equity_cap", FB.get("gross_long_notional_equity_cap"),
        "paper_engine.FALLBACK", "")
    add("组合初始止损风险上限", "max_total_risk_budget", cfg.get("max_total_risk_budget"),
        "risk.portfolio_initial_stop_risk_cap", FB.get("portfolio_initial_stop_risk_cap"),
        "paper_engine.FALLBACK", "语义近似但键名不同，数值 0.015 vs 0.05")
    add("流动性参与率上限", "max_participation_of_prev_15m_quote",
        cfg.get("max_participation_of_prev_15m_quote"),
        "risk.entry_participation_previous_15m_quote_cap",
        FB.get("entry_participation_previous_15m_quote_cap"), "paper_engine.FALLBACK",
        "冻结 0.001（0.1%）；视图 0.01（1%），差 10 倍")
    add("手续费（bps，每边）", "taker_fee_bps", cfg.get("taker_fee_bps"),
        "execution.fee_each_side", FB.get("fee_each_side"), "paper_engine.FALLBACK",
        "冻结 5bps；视图 0.0005=5bps ⇒ 归一后一致",
        norm_r=same, norm_v=bps)
    add("滑点（bps，每边）", "slippage_bps", cfg.get("slippage_bps"),
        "execution.slippage_each_side", FB.get("slippage_each_side"), "paper_engine.FALLBACK",
        "冻结 3bps；视图 0.0005=5bps ⇒ 归一后差 1.67 倍",
        norm_r=same, norm_v=bps)
    add("跟踪触发 R 倍数", "trailing_trigger_r", cfg.get("trailing_trigger_r"),
        "stops.trail_d", None, "（模拟盘以 trail_d 百分比表达，无 R 倍数概念）",
        "口径不可直接比：回测按 R，模拟盘按 4h 低点回撤百分比", comparable=False)
    add("最长持仓（小时）", "max_holding_days", cfg.get("max_holding_days"),
        "stops.max_holding_hours", None, "（模拟盘读 max_holding_hours）",
        "回测 10 天 = 240h；视图 240h ⇒ 归一后一致",
        norm_r=days2h, norm_v=same)

    # ---------- 3. 汇总 ----------
    n_cmp = sum(1 for r in rows if r["可比"])
    n_bad1 = sum(1 for r in rows if r["exp1与冻结一致"] is False)
    n_bad2 = sum(1 for r in rows if r["exp2与冻结一致"] is False)
    n_badE = sum(1 for r in rows if r["引擎与冻结一致"] is False)
    n_ok1 = sum(1 for r in rows if r["exp1与冻结一致"] is True)

    # 预算绝对值对比（这是 A-1 的真实后果）
    budget = {
        "回测冻结_2000x0.005": cfg.get("initial_equity") * cfg.get("risk_budget_per_trade"),
        "模拟盘视图声明_5000x0.005": 5000 * v1["risk"]["risk_per_trade_equity_fraction"],
        "模拟盘引擎实际_5000x0.005": float(L.INIT_EQUITY) * FB.get("risk_per_trade_equity_fraction"),
    }
    budget["引擎_除以_冻结"] = budget["模拟盘引擎实际_5000x0.005"] / budget["回测冻结_2000x0.005"]

    # ---------- 4. 旧报告失效判断清单 ----------
    corrections = [
        {
            "编号": "C-1",
            "旧表述出处": "SYSTEM-FINAL-AUDIT-01/05_最终交付.md §五 U-1",
            "旧表述": "权益基准到底用 2000 还是 5000 —— 列为用户待决",
            "判定": "作废（不应列为待决）",
            "依据": "冻结规则已由 FT-VALIDATION-02 认定并授权：回测侧 initial_equity=2000。"
                    "问题不是「选哪个」，而是模拟盘视图文件写 5000 且字段不生效。",
            "纠正后表述": "记为一个事实缺陷（见 C-3），归入 A 任务验收项「本金与风险参数真实生效」，"
                          "不再询问用户。",
        },
        {
            "编号": "C-2",
            "旧表述出处": "SYSTEM-FINAL-AUDIT-01/05_最终交付.md §五 U-2",
            "旧表述": "风险参数用冻结表那套还是反重力那套 —— 列为用户待决",
            "判定": "作废（不应列为待决）",
            "依据": "同上。冻结表已授权；反重力那套是研究期实验配置（production_config=false、"
                    "live_trading_authorized=false），本身不构成「候选生产参数」。",
            "纠正后表述": "记为一个事实缺陷（见 C-3）。生产参数选择不在本轮范围，"
                          "但「工程把实验配置当冻结视图用」必须记录。",
        },
        {
            "编号": "C-3",
            "旧表述出处": "SYSTEM-FINAL-AUDIT-01/10_A关配置_复验.md 缺陷 A-1",
            "旧表述": "「冻结写 2000 ⇒ 单笔预算实际 25 而非 10 USDT」",
            "判定": "表述需修正（结论对，归因错）",
            "依据": f"exp1/exp2_config.json 的 risk.initial_equity_USDT 实际写的是 "
                    f"{v1['risk']['initial_equity_USDT']}，不是 2000。"
                    f"「冻结写 2000」指的是回测 config.py，不是这两个视图文件。",
            "纠正后表述": "模拟盘视图文件写 5000（与回测冻结 2000 不同），且该字段不生效，"
                          "引擎恒用 live_store.INIT_EQUITY=5000 ⇒ 单笔预算 25 USDT，"
                          "而回测冻结口径应为 10 USDT，差 2.5 倍。",
        },
        {
            "编号": "C-4",
            "旧表述出处": "项目记忆「冻结参数（FT-VALIDATION-02 认定）」",
            "旧表述": "冻结规则 SHA 1f1167de…9a8ab 不得改",
            "判定": "表述正确，但需明确指向哪个文件（易被误读成 config.py 的 SHA）",
            "依据": f"1f1167de…9a8ab 实为 `{RULE_DOC}` 的文件 SHA-256"
                    f"（实算 {'与声明一致' if rule_fp_match else '与声明不一致'}，"
                    f"{sha_rule['bytes']} B）；config.py 文件 SHA 实为 {sha_cfg['sha256']}。"
                    f"STEP1-A 把它称作「规则指纹」属表述混用。",
            "纠正后表述": f"冻结规则文档 `{RULE_DOC}` 的 SHA-256 = 1f1167de…9a8ab 不得变；"
                          f"config.py 文件 SHA 用于版本绑定，两者分开记。",
        },
        {
            "编号": "C-5",
            "旧表述出处": f"{CFG_PY} 第 66-68 行注释",
            "旧表述": "「并未固定绝对本金；10000 USDT 是本轮研究口径」",
            "判定": "注释与代码自相矛盾（新发现）",
            "依据": f"同一处注释下方代码为 `initial_equity: float = "
                    f"{cfg.get('initial_equity')}`。注释说 10000，代码是 2000。",
            "纠正后表述": "以代码 2000.0 为准；该注释需修正（本轮不改业务代码，只登记）。"
                          "这条矛盾会直接影响「本金到底是多少」的判断，属 A 任务验收项。",
        },
    ]

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "用途": "把「回测冻结规则 / 模拟盘视图文件 / 模拟盘引擎常量」三套口径的差异机械钉死；"
                "纠正 FINAL-01 中已失效的判断（工单第 5 条）",
        "边界": "只读三处来源并计算，不修改任何业务代码、配置或旧证据",
        "来源文件SHA": {
            "R_回测config.py": sha_cfg,
            "R_冻结规则文档": sha_rule,
            "V_exp1": sha_v1,
            "V_exp2": sha_v2,
        },
        "冻结规则文档SHA": {
            "路径": RULE_DOC,
            "实算sha256": sha_rule["sha256"],
            "声明sha256": DECLARED_RULE_FP,
            "一致": rule_fp_match,
            "说明": "config.py 第 4-5 行注释里的 SHA256 指向这份文档，"
                    "不是 config.py 自身，也不是「参数值算出的指纹」",
        },
        "对照表": rows,
        "差异计数": {
            "对照项总数": len(rows),
            "可比项数": n_cmp,
            "不可比项数": len(rows) - n_cmp,
            "exp1_与冻结不一致项数": n_bad1,
            "exp2_与冻结不一致项数": n_bad2,
            "引擎与冻结不一致项数": n_badE,
            "exp1_一致项数": n_ok1,
        },
        "单笔预算绝对值": budget,
        "旧报告失效判断": corrections,
        "结论": {
            "A任务验收核心": "本金与风险参数真实生效 = equity() 随视图 initial_equity_USDT 变化，"
                             "且等于 2000 时单笔预算为 10 USDT",
            "当前状态": "不通过（视图字段不生效，引擎恒 5000 ⇒ 预算 25 USDT）",
            "不得再问用户的事": ["权益基准用 2000 还是 5000", "风险参数用哪一套"],
        },
    }

    (OUT / "01_口径对照.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    # ---------- 5. 打印 ----------
    print(f"R 回测 config.py  sha256={sha_cfg['sha256'][:16]}…  bytes={sha_cfg['bytes']}")
    print(f"R 冻结规则文档    sha256={sha_rule['sha256'][:16]}…  bytes={sha_rule['bytes']}  "
          f"与声明一致={rule_fp_match}")
    print(f"V exp1_config.json sha256={sha_v1['sha256'][:16]}…")
    print(f"V exp2_config.json sha256={sha_v2['sha256'][:16]}…")
    print(f"\n规则文档 SHA 声明值 = {DECLARED_RULE_FP[:16]}…（≠ config.py 文件 SHA，见 C-4）")
    print(f"\n{'项目':<22}{'冻结R':>10}{'exp1':>10}{'exp2':>10}{'引擎E':>10}  一致(1/2/E)")
    print("-" * 88)
    for r in rows:
        def f(x):
            if x is None:
                return "—"
            if isinstance(x, float):
                return f"{x:g}"
            return str(x)
        if r["可比"]:
            flag = f"{_s(r['exp1与冻结一致'])}/{_s(r['exp2与冻结一致'])}/{_s(r['引擎与冻结一致'])}"
        else:
            flag = "不可比"
        print(f"{r['项目']:<22}{f(r['归一后_R']):>10}{f(r['归一后_exp1']):>10}"
              f"{f(r['归一后_exp2']):>10}{f(r['归一后_引擎']):>10}  {flag}")
    print("-" * 88)
    print(f"可比项 {n_cmp}/{len(rows)}；不一致：exp1={n_bad1}  exp2={n_bad2}  引擎={n_badE}")
    print(f"一致项：exp1={n_ok1}")
    print(f"\n单笔预算：回测冻结 {budget['回测冻结_2000x0.005']:.4f} USDT  "
          f"引擎实际 {budget['模拟盘引擎实际_5000x0.005']:.4f} USDT  "
          f"倍数 {budget['引擎_除以_冻结']:.2f}x")
    print("\n旧报告失效判断：")
    for c in corrections:
        print(f"  {c['编号']} [{c['判定']}] {c['旧表述'][:46]}")
    print("\n→ out/01_口径对照.json")


def _dig(d, path):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _eq(a, b) -> bool | None:
    if a is None or b is None:
        return None
    try:
        return abs(float(a) - float(b)) < 1e-12
    except Exception:
        return a == b


def _s(x) -> str:
    return "T" if x is True else ("F" if x is False else "—")


if __name__ == "__main__":
    main()
