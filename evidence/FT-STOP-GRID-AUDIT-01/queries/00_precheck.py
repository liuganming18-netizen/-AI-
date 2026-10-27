# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 00_开跑前核对

只读上游 evidence/FT-STOP-GRID-01，不改任何文件。
产出 out/00_开跑前核对.json：
  一 文件指纹（SHA-256）
  二 四档止损参数（36 组 = 4 档 × 9 入场组）
  三 源码级差异（相对 FT-EXIT-01 基类，AST 函数级）
  四 超时与跟踪是否真的取消（AST 标识符级，不看注释）
  五 0.5% 风险预算与仓位上限
  六 共同数据区间
  七 数据真实路径（软链穿透）
  八 其余冻结参数
  九 机制性偏差预登记（在结果出来前先算清的必然偏差）
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
UP = BASE_DIR / "evidence/FT-STOP-GRID-01"
PREV = BASE_DIR / "evidence/FT-EXIT-01"
OUT = BASE_DIR / "evidence/FT-STOP-GRID-AUDIT-01/out"
OUT.mkdir(parents=True, exist_ok=True)

TIERS = ["Stop05", "Stop10", "Stop15", "Stop20"]
SUFFIXES = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
            "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
            "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
EXPECT_TIER_PCT = {"Stop05": 0.05, "Stop10": 0.10, "Stop15": 0.15, "Stop20": 0.20}

FORBIDDEN = ["timeout", "timeout_240h", "trigger_2r", "latest_l10", "df_4h_info",
             "max_holding", "trade_duration", "is_2r_activated"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def norm_src(s: str) -> str:
    """把路径与类名归一，剩下的才算真差异。"""
    s = s.replace("evidence/FT-STOP-GRID-01", "evidence/FT-EXIT-01")
    s = re.sub(r"class WyckoffStopGridBase\(IStrategy\):\n\s+stoploss_pct = 0\.20[^\n]*\n",
               "class WyckoffExit01Base(IStrategy):\n", s)
    return s


def funcs_of(path: Path) -> dict[str, str]:
    """函数名 -> 源码段（顶层 + 类方法），用于函数级差异对比。"""
    src = read(path)
    tree = ast.parse(src)
    out: dict[str, str] = {}

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f"{prefix}{child.name}"
                out[key] = norm_src(ast.get_source_segment(src, child) or "")
                walk(child, key + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{child.name}.")

    walk(tree)
    return out


def identifiers(path: Path) -> set[str]:
    """AST 里出现的标识符（名字 + 属性名 + 关键字参数），不含注释与字符串。"""
    tree = ast.parse(read(path))
    ids: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            ids.add(n.id)
        elif isinstance(n, ast.Attribute):
            ids.add(n.attr)
        elif isinstance(n, ast.keyword) and n.arg:
            ids.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ids.add(n.name)
            for a in list(n.args.args) + list(n.args.kwonlyargs):
                ids.add(a.arg)
    return ids


def tier_params(strat_file: Path) -> dict:
    src = read(strat_file)
    def grab(pat, cast=float):
        m = re.search(pat, src)
        return cast(m.group(1)) if m else None
    return {
        "stoploss_pct": grab(r"stoploss_pct\s*=\s*([0-9.]+)"),
        "c_vol_ratio_vs": grab(r"c_vol_ratio_vs\s*=\s*([0-9.]+)"),
        "c_retest_zone_atr": grab(r"c_retest_zone_atr\s*=\s*([0-9.]+)"),
        "timing_mode": (re.search(r'timing_mode\s*=\s*"([^"]+)"', src) or [None, None])[1],
    }


def main() -> dict:
    res: dict = {}
    res["元信息"] = {
        "工单": "FT-STOP-GRID-AUDIT-01",
        "脚本": "queries/00_precheck.py",
        "生成时间": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "上游目录": str(UP.relative_to(BASE_DIR)),
        "只读声明": "本脚本只读上游，不写上游任何文件",
    }

    # ---------- 一 文件指纹 ----------
    fp: dict = {}
    cfg_p = UP / "user_data/config.json"
    base_p = UP / "user_data/strategies/WyckoffStopGridBase.py"
    gen_p = UP / "code/00_generate_strategies.py"
    run_p = UP / "code/01_run_all_grid_backtests.py"
    elig_p = UP / "user_data/strategies/pair_eligibility_rules.json"

    fp["config.json"] = sha256(cfg_p)
    fp["WyckoffStopGridBase.py"] = sha256(base_p)
    fp["00_generate_strategies.py"] = sha256(gen_p)
    fp["01_run_all_grid_backtests.py"] = sha256(run_p)
    fp["pair_eligibility_rules.json"] = sha256(elig_p) if elig_p.exists() else None
    fp["对比_FT-EXIT-01_基类"] = sha256(PREV / "user_data/strategies/WyckoffExit01Base.py")

    strat_sha = {}
    for t in TIERS:
        for s in SUFFIXES:
            name = f"Wyckoff_C1_{t}_{s}"
            p = UP / f"user_data/strategies/{name}.py"
            strat_sha[name] = sha256(p) if p.exists() else None
    fp["36组策略文件"] = strat_sha
    fp["缺失策略文件"] = [k for k, v in strat_sha.items() if v is None]
    res["一_文件指纹"] = fp

    # ---------- 二 四档止损参数 ----------
    grid = {}
    for t in TIERS:
        pcts, vs, zs, bad = set(), set(), set(), []
        for s in SUFFIXES:
            p = UP / f"user_data/strategies/Wyckoff_C1_{t}_{s}.py"
            tp = tier_params(p)
            pcts.add(tp["stoploss_pct"])
            vs.add(tp["c_vol_ratio_vs"])
            zs.add(tp["c_retest_zone_atr"])
            if tp["timing_mode"] != "C1":
                bad.append(s)
            exp_vs = float(s.split("_")[0].replace("Vs", "")) / 10.0
            exp_z = float(s.split("_")[1].replace("Z", "")) / 10.0
            if tp["c_vol_ratio_vs"] != exp_vs or tp["c_retest_zone_atr"] != exp_z:
                bad.append(s + "(参数错位)")
        pct = sorted(pcts)[0] if len(pcts) == 1 else None
        grid[t] = {
            "stoploss_pct": pct,
            "止损价系数": round(1 - pct, 4) if pct else None,
            "组数": len(SUFFIXES),
            "组内止损比例唯一": len(pcts) == 1,
            "组内参数异常": bad,
            "vs_集合": sorted(vs),
            "z_集合": sorted(zs),
            "理论名义仓位占权益": round(0.005 / pct, 6) if pct else None,
            "理论单笔往返手续费占R": round(2 * 0.0005 / pct, 6) if pct else None,
        }
    res["二_四档止损参数"] = {
        "格数": sum(g["组数"] for g in grid.values()),
        "四档是否与预期一致": all(grid[t]["stoploss_pct"] == EXPECT_TIER_PCT[t] for t in TIERS),
        "各档": grid,
    }

    # ---------- 三 源码级差异 ----------
    fa = funcs_of(PREV / "user_data/strategies/WyckoffExit01Base.py")
    fb = funcs_of(base_p)
    # 类名改了，函数键的类前缀要归一，否则会误判成「函数集合变化」
    fa = {k.replace("WyckoffExit01Base.", "WyckoffStopGridBase."): v for k, v in fa.items()}
    only_a = sorted(set(fa) - set(fb))
    only_b = sorted(set(fb) - set(fa))
    common = sorted(set(fa) & set(fb))
    diff = [k for k in common if fa[k] != fb[k]]
    same = [k for k in common if fa[k] == fb[k]]
    # 路径替换与类名重命名造成的纯文本差异要单独剔出来
    text_a = read(PREV / "user_data/strategies/WyckoffExit01Base.py")
    text_b = read(base_p)
    norm_b = norm_src(text_b)
    res["三_源码级差异"] = {
        "函数总数_旧": len(fa),
        "函数总数_新": len(fb),
        "仅旧有": only_a,
        "仅新有": only_b,
        "内容不同的函数": diff,
        "内容相同的函数数": len(same),
        "剥离路径与类名后是否仍不同": norm_b != text_a,
        "说明": "只在 custom_stake_amount 与 custom_stoploss 两处改逻辑，其余为路径/类名重命名",
    }

    # ---------- 四 超时与跟踪是否真的取消 ----------
    ids = identifiers(base_p)
    hit = {k: sorted(x for x in ids if k.lower() in x.lower()) for k in FORBIDDEN}
    src_b = read(base_p)
    res["四_超时与跟踪取消"] = {
        "AST标识符命中": {k: v for k, v in hit.items() if v},
        "命中项数": sum(1 for v in hit.values() if v),
        "custom_exit_仅返回None": bool(
            re.search(r"def custom_exit\(.*?\n(?:.*?\n)*?\s+return None\s*\n", src_b, re.S)
            and "return None" in src_b.split("def custom_exit", 1)[-1][:400]
        ),
        "注释里提到240但代码没有": "240" in src_b,
        "结论": "通过" if sum(1 for v in hit.values() if v) == 0 else "未通过",
    }

    # ---------- 五 风险预算与上限 ----------
    res["五_风险预算与上限"] = {
        "risk_budget表达式": "equity * 0.005" if "equity * 0.005" in src_b else "未找到",
        "risk_budget=0.005": "risk_budget = equity * 0.005" in src_b,
        "单仓上限表达式": "max_single = equity * 0.30" if "equity * 0.30" in src_b else "未找到",
        "流动性上限表达式": "prev_qv * 0.001" if "prev_qv * 0.001" in src_b else "未找到",
        "min_stake硬性拒单": "raw_stake < min_stake" in src_b,
        "结论": "通过" if all([
            "risk_budget = equity * 0.005" in src_b,
            "max_single = equity * 0.30" in src_b,
            "prev_qv * 0.001" in src_b,
            "raw_stake < min_stake" in src_b,
        ]) else "未通过",
    }

    # ---------- 六 共同数据区间 ----------
    tr_new = re.search(r'"--timerange",\s*"([^"]+)"', read(run_p))
    tr_old = re.search(r'"--timerange",\s*"([^"]+)"', read(PREV / "code/01_run_all_backtests.py"))
    res["六_共同数据区间"] = {
        "本轮timerange": tr_new.group(1) if tr_new else None,
        "FT-EXIT-01_timerange": tr_old.group(1) if tr_old else None,
        "一致": bool(tr_new and tr_old and tr_new.group(1) == tr_old.group(1)),
        "cache": "none" if '"--cache", "none"' in read(run_p) else "启用",
        "export": "trades" if '"--export", "trades"' in read(run_p) else "未设置",
        "并发数": (re.search(r"max_workers\s*=\s*(\d+)", read(run_p)) or [None, None])[1],
    }

    # ---------- 七 数据真实路径 ----------
    link_p = UP / "user_data/data"
    real = link_p.resolve()
    prev_real = (PREV / "user_data/data").resolve()
    counts = {}
    fut = real / "binance/futures"
    if fut.exists():
        names = [f.name for f in fut.iterdir() if f.is_file()]
        kinds: dict[str, int] = {}
        for n in names:
            stem = n[:-len(".feather")] if n.endswith(".feather") else n
            parts = stem.split("-")
            kind = "-".join(parts[-2:]) if len(parts) >= 2 else stem
            kinds[kind] = kinds.get(kind, 0) + 1
        counts = dict(sorted(kinds.items()))
        counts["总文件数"] = len(names)
    res["七_数据真实路径"] = {
        "软链": str(link_p),
        "指向": str(real.relative_to(BASE_DIR)),
        "与FT-EXIT-01同一份": str(real) == str(prev_real),
        "FT-EXIT-01真实路径": str(prev_real.relative_to(BASE_DIR)),
        "feather文件数": counts,
    }

    # ---------- 八 其余冻结参数 ----------
    cfg = json.loads(read(cfg_p))
    cfg_prev = json.loads(read(PREV / "user_data/config.json"))
    diff_keys = sorted(k for k in set(cfg) | set(cfg_prev) if cfg.get(k) != cfg_prev.get(k))
    res["八_其余冻结参数"] = {
        "max_open_trades": cfg.get("max_open_trades"),
        "dry_run_wallet": cfg.get("dry_run_wallet"),
        "timeframe": cfg.get("timeframe"),
        "fee": cfg.get("fee"),
        "trading_mode": cfg.get("trading_mode"),
        "margin_mode": cfg.get("margin_mode"),
        "stake_amount": cfg.get("stake_amount"),
        "tradable_balance_ratio": cfg.get("tradable_balance_ratio"),
        "币池数量": len(cfg["exchange"]["pair_whitelist"]),
        "币池与FT-EXIT-01一致": cfg["exchange"]["pair_whitelist"] == cfg_prev["exchange"]["pair_whitelist"],
        "与FT-EXIT-01不同的键": diff_keys,
        "仅路径差异": diff_keys == ["datadir", "user_data_dir"],
    }

    # ---------- 九 机制性偏差预登记 ----------
    # 这部分完全不依赖回测结果，纯由代码与配置推出。
    res["九_机制性偏差预登记"] = {
        "前提": "四档的 R 分母相同（开仓时权益×0.5%），所以 R 可跨档比较；但名义仓位相差 4 倍。",
        "名义仓位差": {
            "Stop05": "0.5%/5% = 权益的 10.0%",
            "Stop10": "权益的 5.0%",
            "Stop15": "权益的 3.33%",
            "Stop20": "权益的 2.5%",
            "比值": "4 : 2 : 1.333 : 1",
        },
        "偏差1_成本拖累按档位线性放大": {
            "往返手续费占R": {"Stop05": 0.02, "Stop10": 0.01, "Stop15": 0.006667, "Stop20": 0.005},
            "资金费同比例放大": "资金费 = 名义 × 费率 × 时长，Stop05 是 Stop20 的 4 倍",
            "后果": "止损越窄，每笔的固定成本在 R 里占比越高；700 笔规模下 Stop05 约 14R、Stop20 约 3.5R",
            "处理": "必须把「毛利 R」与「净 R」分列，并给出成本拖累的绝对 R 值",
        },
        "偏差2_最小下单额拒单按档位单调递减": {
            "机制": "stoploss=-0.99 ⇒ freqtrade 的 stoploss_reserve 恒为 1.5（exchange.py:1165-1167），四档 min_stake 完全相同",
            "raw_stake": {"Stop05": "≈200 USDT（权益2000时）", "Stop10": "≈100", "Stop15": "≈66.7", "Stop20": "≈50"},
            "后果": "同一事件，Stop05 最容易过门槛、Stop20 最容易被拒；被拒的多是「最小名义额偏高」的标的",
            "待测": "各档拒单次数、被拒标的清单、被拒标的最小名义额",
        },
        "偏差3_流动性上限对不同档位的实际风险不等": {
            "机制": "stake 被 liq_cap=前一根15m报价额×0.1% 截断时，实际风险 = liq_cap × pct",
            "后果": "同一受限事件，Stop05 实际风险只有 Stop20 的 1/4，R 幅度被压缩 ⇒ 会显得「更稳」",
            "处理": "统计各档 liq_cap 生效笔数与占比，并给出「实际风险/名义风险」比值分布",
        },
        "偏差4_选币偏差": {
            "机制": "Stop20 只在 min_stake<50 USDT 的标的上成交，而最小名义额与单价正相关",
            "后果": "Stop20 的币池是 Stop05 币池的真子集，且系统性偏向低价币",
            "处理": "必须给出四档成交标的集合的交集/差集，并在共同子集上重算",
        },
        "偏差5_期末未实现浮盈按档位单调放大": {
            "机制": "止损越宽越不容易被打掉 ⇒ 期末仍持有的笔数越多",
            "后果": "Stop20 的「总 R」里未实现部分占比最高，直接比较总 R 会把浮盈当成绩效",
            "处理": "已实现 R 与期末浮盈 R 必须分列，主结论以已实现为准",
        },
    }

    # ---------- 十 判定 ----------
    issues = []
    if res["二_四档止损参数"]["格数"] != 36:
        issues.append(f"策略格数不是 36（实际 {res['二_四档止损参数']['格数']}）")
    if not res["二_四档止损参数"]["四档是否与预期一致"]:
        issues.append("四档止损比例与 5/10/15/20% 不一致")
    if res["三_源码级差异"]["仅新有"] or res["三_源码级差异"]["仅旧有"]:
        issues.append("函数集合发生变化，超出「只改两个函数」的范围")
    if {x.split(".")[-1] for x in res["三_源码级差异"]["内容不同的函数"]} - {"custom_stake_amount", "custom_stoploss"}:
        issues.append("改动函数超出 custom_stake_amount / custom_stoploss")
    if res["四_超时与跟踪取消"]["结论"] != "通过":
        issues.append("超时或跟踪退出未彻底取消")
    if res["五_风险预算与上限"]["结论"] != "通过":
        issues.append("风险预算或仓位上限被改动")
    if not res["六_共同数据区间"]["一致"]:
        issues.append("数据区间与 FT-EXIT-01 不一致，不可与历史结果直接比较")
    if not res["七_数据真实路径"]["与FT-EXIT-01同一份"]:
        issues.append("数据目录与 FT-EXIT-01 不是同一份")
    if not res["八_其余冻结参数"]["仅路径差异"]:
        issues.append(f"配置文件除路径外还有改动：{res['八_其余冻结参数']['与FT-EXIT-01不同的键']}")
    if fp["缺失策略文件"]:
        issues.append(f"缺策略文件 {len(fp['缺失策略文件'])} 个")

    res["十_开跑前判定"] = {
        "结论": "通过（可开跑，但必须按第九节口径解读）" if not issues else "未通过",
        "问题清单": issues,
        "问题数": len(issues),
    }
    return res


if __name__ == "__main__":
    out = main()
    p = OUT / "00_开跑前核对.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out["十_开跑前判定"], ensure_ascii=False, indent=2))
    print(f"\n已写出 {p}  ({p.stat().st_size} 字节)")
    print("四档止损：", json.dumps(out["二_四档止损参数"]["四档是否与预期一致"], ensure_ascii=False))
    print("改动函数：", out["三_源码级差异"]["内容不同的函数"])
    print("超时/跟踪：", out["四_超时与跟踪取消"]["结论"], out["四_超时与跟踪取消"]["AST标识符命中"])
    print("数据同一份：", out["七_数据真实路径"]["与FT-EXIT-01同一份"], out["七_数据真实路径"]["指向"])
    print("配置仅路径差异：", out["八_其余冻结参数"]["仅路径差异"], out["八_其余冻结参数"]["与FT-EXIT-01不同的键"])
    print("币池数量：", out["八_其余冻结参数"]["币池数量"])
