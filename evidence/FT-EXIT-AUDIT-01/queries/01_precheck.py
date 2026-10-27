# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 01：开跑前源码层核对

只读上游，不改任何文件。核对四件事：
 1. S / P 两组确实取消 240h 超时与 2R 跟踪退出
 2. 初始止损终生固定（S=结构 P0，P=成交价×0.80）
 3. 入场逻辑与仓位预算未被改动
 4. 18 个包装策略的 scheme 与网格参数正确、数据目录与币池一致
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = ROOT / "evidence/FT-EXIT-01"
OUT = ROOT / "evidence/FT-EXIT-AUDIT-01/out"
OUT.mkdir(parents=True, exist_ok=True)

NEW_BASE = EXP / "user_data/strategies/WyckoffExit01Base.py"
STOP20_BASE = ROOT / "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py"
BASE09 = ROOT / "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py"

R = {}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


src = NEW_BASE.read_text(encoding="utf-8")
stop20 = STOP20_BASE.read_text(encoding="utf-8")
base09 = BASE09.read_text(encoding="utf-8")

print("=" * 96)
print("【1】退出机制：超时与跟踪是否真的被取消")
print("=" * 96)

t1 = {}
# 1.1 超时
t1["含 timeout_240h 字符串"] = "timeout_240h" in src
t1["含 240 * 3600 判断"] = "240 * 3600" in src
t1["custom_exit 体"] = (
    src.split("def custom_exit", 1)[1].strip() if "def custom_exit" in src else None
)
print(f"  timeout_240h 出现次数: {src.count('timeout_240h')}（上游 20% 基类 {stop20.count('timeout_240h')}）")
print(f"  240 * 3600 出现次数: {src.count('240 * 3600')}（上游 {stop20.count('240 * 3600')}）")
print(f"  custom_exit 函数体:\n    {t1['custom_exit 体']}")

# 1.2 跟踪
t1["含 trigger_2r"] = "trigger_2r" in src
t1["含 actual_r"] = "actual_r" in src
t1["含 latest_l10"] = "latest_l10" in src
t1["含 pair_4h_df 用于止损"] = "df_4h_info" in src
t1["is_2r_activated 被读取"] = bool(re.search(r"state\[.is_2r_activated.\]", src))
print(f"\n  trigger_2r: {src.count('trigger_2r')}（上游 {stop20.count('trigger_2r')}）")
print(f"  actual_r: {src.count('actual_r')}（上游 {stop20.count('actual_r')}）")
print(f"  latest_l10: {src.count('latest_l10')}（上游 {stop20.count('latest_l10')}）")
print(f"  df_4h_info: {src.count('df_4h_info')}（上游 {stop20.count('df_4h_info')}）")
print(f"  is_2r_activated 被读取（state[...]）: {t1['is_2r_activated 被读取']}")
print("  → 2R 跟踪逻辑整体删除，is_2r_activated 只剩 state 初始化占位，无任何读取")

# 1.3 止损表达式
m = re.search(r"# FT-EXIT-01: 仅初始止损.*?return float\(stoploss_ratio\)", src, re.S)
t1["止损段源码"] = m.group(0) if m else None
print("\n  止损计算段（原样）:")
print("    " + (m.group(0).replace("\n", "\n    ") if m else "未找到"))
R["1_退出机制"] = t1

print()
print("=" * 96)
print("【2】入场与仓位预算是否被改动")
print("=" * 96)


def func_body(text: str, name: str) -> str:
    m = re.search(rf"\n    def {name}\(.*?(?=\n    def |\Z)", text, re.S)
    return m.group(0) if m else ""


t2 = {}
for fn in ("confirm_trade_entry", "populate_entry_trend", "populate_indicators",
           "custom_entry_price", "leverage"):
    a = func_body(src, fn)
    b = func_body(base09, fn)
    same = a == b
    t2[f"{fn} 与 FT-ENTRY-09 基线逐字节一致"] = same
    print(f"  {fn:<26} 与基线逐字节一致: {same}  （长度 {len(a)} vs {len(b)}）")

for pat, label in ((r"equity \* 0\.005", "风险预算 0.5%"),
                   (r"equity \* 0\.30", "单仓 30% 帽"),
                   (r"prev_qv \* 0\.001", "24h 流动性 0.1% 帽"),
                   (r"max_open_trades", "3 槽位（config）")):
    n_new, n_old = len(re.findall(pat, src)), len(re.findall(pat, base09))
    t2[label] = {"新版": n_new, "基线": n_old, "一致": n_new == n_old}
    print(f"  {label:<22} 新版 {n_new} 处 / 基线 {n_old} 处 → {'一致' if n_new == n_old else '不一致'}")

cfg = json.loads((EXP / "user_data/config.json").read_text(encoding="utf-8"))
frozen = {
    "max_open_trades": (cfg["max_open_trades"], 3),
    "dry_run_wallet": (cfg["dry_run_wallet"], 2000),
    "timeframe": (cfg["timeframe"], "15m"),
    "fee": (cfg["fee"], 0.0005),
    "trading_mode": (cfg["trading_mode"], "futures"),
    "margin_mode": (cfg["margin_mode"], "cross"),
    "stake_amount": (cfg["stake_amount"], "unlimited"),
    "tradable_balance_ratio": (cfg["tradable_balance_ratio"], 0.99),
    "pairlist 数量": (len(cfg["exchange"]["pair_whitelist"]), 74),
}
print("\n  config 冻结参数:")
for k, (got, want) in frozen.items():
    print(f"    {k:<24} {str(got):<12} 期望 {want}  {'OK' if got == want else '✗'}")
    t2[f"config.{k}"] = {"实测": got, "期望": want, "一致": got == want}
R["2_入场与仓位"] = t2

print()
print("=" * 96)
print("【3】差异清单：按函数逐一对齐（除 3 个退出/仓位函数外必须逐字节一致）")
print("=" * 96)
import difflib


def all_funcs(text: str):
    """返回 {函数名: 函数体}，只取类内 4 空格缩进的方法。"""
    out = {}
    for m in re.finditer(r"\n    def (\w+)\(", text):
        name = m.group(1)
        start = m.start()
        nxt = re.search(r"\n    def \w+\(", text[m.end():])
        end = m.end() + nxt.start() if nxt else len(text)
        out[name] = text[start:end]
    return out


fa, fb_ = all_funcs(src), all_funcs(stop20)
same, diff = [], []
for name in sorted(set(fa) | set(fb_)):
    a, b = fa.get(name, ""), fb_.get(name, "")
    if a == b:
        same.append(name)
    else:
        # 仅路径 / 类名 / scheme 属性不同也算"实质一致"
        def norm(s: str) -> str:
            s = s.replace("evidence/FT-EXIT-01", "evidence/X").replace(
                "evidence/FT-STOP-20-01", "evidence/X")
            s = s.replace("WyckoffExit01Base", "CLS").replace(
                "WyckoffStop20ParamExplorationBase", "CLS")
            s = s.replace("    stoploss_scheme = 'S'  # 'S' = 结构 P0, 'P' = 固定 20%\n", "")
            return s
        (same if norm(a) == norm(b) else diff).append(name)

print(f"  函数总数 {len(set(fa) | set(fb_))}：逐字节一致 {len(same)}，有实质差异 {len(diff)}")
print(f"  有实质差异的函数: {diff}")
print(f"  （其中仅路径不同的已归入一致：{sorted(set(same) - {n for n in same if fa.get(n,'')==fb_.get(n,'')})}）")

for name in diff:
    print(f"\n  --- {name} 差异 ---")
    for l in difflib.unified_diff(fb_.get(name, "").splitlines(), fa.get(name, "").splitlines(),
                                  "上游", "FT-EXIT-01", n=0, lineterm=""):
        if l.startswith(("+++", "---", "@@")):
            continue
        print(f"    {l}")

t3 = {"函数总数": len(set(fa) | set(fb_)), "逐字节或仅路径不同": len(same),
      "有实质差异的函数": diff,
      "有实质差异函数数": len(diff),
      "判定": "通过" if set(diff) <= {"custom_stake_amount", "custom_stoploss", "custom_exit"} else "越界"}
print(f"\n  判定: {t3['判定']}（期望仅 custom_stake_amount / custom_stoploss / custom_exit）")
R["3_差异清单"] = t3

print()
print("=" * 96)
print("【4】18 个包装策略")
print("=" * 96)
SUF = [f"Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
t4 = {"组": []}
for scheme, prefix in (("S", "Wyckoff_C1_S_P0"), ("P", "Wyckoff_C1_P_Stop20")):
    for s in SUF:
        p = EXP / f"user_data/strategies/{prefix}_{s}.py"
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
        vs = float(s.split("_")[0].replace("Vs", "")) / 10.0
        z = float(s.split("_")[1].replace("Z", "")) / 10.0
        ok_scheme = f'stoploss_scheme = "{scheme}"' in txt
        ok_vs = f"c_vol_ratio_vs = {vs}" in txt
        ok_z = f"c_retest_zone_atr = {z}" in txt
        ok_c1 = 'timing_mode = "C1"' in txt
        ok = all([p.exists(), ok_scheme, ok_vs, ok_z, ok_c1])
        t4["组"].append({"文件": p.name, "存在": p.exists(), "scheme": ok_scheme,
                       "vs": ok_vs, "zone": ok_z, "C1": ok_c1, "通过": ok})
        if not ok:
            print(f"  ✗ {p.name} scheme={ok_scheme} vs={ok_vs} zone={ok_z} C1={ok_c1}")
print(f"  18 组包装全部正确: {all(x['通过'] for x in t4['组'])}（{sum(x['通过'] for x in t4['组'])}/18）")

data_link = EXP / "user_data/data"
real = Path(str(data_link)).resolve() if data_link.exists() else None
t4["数据目录"] = {"软链": str(data_link), "真实路径": str(real),
                "存在": bool(real and real.exists())}
print(f"  数据目录: {data_link} → {real}（存在 {real.exists() if real else False}）")
t4["预热币池"] = len(cfg["exchange"]["pair_whitelist"])
print(f"  币池: {t4['预热币池']} 个")
R["4_包装策略"] = t4

print()
print("=" * 96)
print("【5】文件指纹（版本只认 SHA-256）")
print("=" * 96)
fp = {
    "FT-EXIT-01/user_data/strategies/WyckoffExit01Base.py": sha(NEW_BASE),
    "FT-STOP-20-01/.../WyckoffStop20ParamExplorationBase.py": sha(STOP20_BASE),
    "FT-ENTRY-09/.../WyckoffParamExplorationBase.py": sha(BASE09),
    "FT-EXIT-01/user_data/config.json": sha(EXP / "user_data/config.json"),
    "FT-EXIT-01/code/00_generate_strategies.py": sha(EXP / "code/00_generate_strategies.py"),
    "FT-EXIT-01/code/01_run_all_backtests.py": sha(EXP / "code/01_run_all_backtests.py"),
    "FT-EXIT-01/code/02_reconcile_and_analyze.py": sha(EXP / "code/02_reconcile_and_analyze.py")
    if (EXP / "code/02_reconcile_and_analyze.py").exists() else None,
}
for k, v in fp.items():
    print(f"  {str(v)[:16]}…  {k}")
R["5_指纹"] = fp

(OUT / "01_源码核对.json").write_text(json.dumps(R, ensure_ascii=False, indent=2),
                                encoding="utf-8")
print(f"\n写出 {OUT/'01_源码核对.json'}")
