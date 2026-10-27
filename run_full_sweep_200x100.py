# -*- coding: utf-8 -*-
"""全币种全时段错开压力测试：200 组 × 每组 100 标的

为什么要这么测
--------------
此前所有批次都是「一个固定窗口 + 一批标的」，只能回答「这段时间这批币表现如何」，
回答不了「策略换个时间段、换批币还成不成立」。本脚本把时间轴切开错位，
让 200 个窗口在 2021~2026 全时段上均匀铺开，每组再随机抽 100 个标的，
用两万个独立样本回答「这套规则到底稳不稳」。

设计要点
--------
· 全时段 = 2021-01-01 ~ 2026-10-18（持仓量数据的实际覆盖范围，再早没有 OI 就跑不出信号）
· 每组窗口 = 365 天。200 个起点在可用起点区间内**分层错开**（每段内随机取点），
  保证窗口在时间轴上均匀铺开，不会全挤在同一个行情阶段。
· 每组从 528 个标的中随机抽 100 个，随机种子 = 固定基数 + 组号，可完全复现。
· 只跑变体 A（完整含 OI 过滤），跳过 B/C 消融 —— 消融是另一件事，这里要的是样本量。
· **不写 run 目录**。20000 个 run 目录约 36GB，本机只剩 29GB，写下去必爆盘。
  只把每次回测的汇总与精简逐笔落到 jsonl，跑完出统计报告。

产出
----
evidence/FULL_SWEEP_200x100/
    plan.json            200 组的窗口与标的清单（可复现）
    runs.jsonl           20000 行，每次回测一行
    trades.jsonl         全部成交明细（精简）
    report.md            统计结论

用法
----
    python run_full_sweep_200x100.py                 # 全量 200 组
    GROUPS=2 PER_GROUP=5 python run_full_sweep_200x100.py   # 小样验证
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import os
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
for _p in (PROJECT_ROOT, f"{PROJECT_ROOT}/策略", f"{PROJECT_ROOT}/框架/backend"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PARQUET = Path(PROJECT_ROOT) / "数据/市场数据/parquet"
OUT_DIR = Path(PROJECT_ROOT) / "evidence" / "FULL_SWEEP_200x100"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FULL_START = dt.date(2021, 1, 1)
FULL_END = dt.date(2026, 10, 18)
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "365"))
GROUPS = int(os.environ.get("GROUPS", "200"))
PER_GROUP = int(os.environ.get("PER_GROUP", "100"))
SEED = int(os.environ.get("SEED", "20261018"))
WORKERS = int(os.environ.get("WORKERS", "8"))
INITIAL_EQUITY = 2000.0

PLAN_JSON = OUT_DIR / "plan.json"
RUNS_JSONL = OUT_DIR / "runs.jsonl"
TRADES_JSONL = OUT_DIR / "trades.jsonl"
REPORT_MD = OUT_DIR / "report.md"

POOL_SIZE: list[str] = []


def list_symbols() -> list[str]:
    """只取同时具备 K 线与持仓量目录的标的，缺一个都跑不出信号。"""
    k = PARQUET / "candles/binance/um"
    o = PARQUET / "oi/binance/um"
    if not k.exists():
        raise SystemExit("K 线目录不存在：%s" % k)
    ks = {p.name for p in k.iterdir() if p.is_dir()}
    os_ = {p.name for p in o.iterdir() if p.is_dir()} if o.exists() else ks
    return sorted(ks & os_)


def symbol_year_map(symbols: list[str]) -> dict[str, set[int]]:
    """标的 → 有 15m 数据的年份集合。

    不做这一步的话，2021 年的窗口会抽到 2023 年才上线的币，
    抽 100 个有 80 个是空跑。按年份预筛，把算力全部用在真能跑出结果的样本上。
    """
    base = PARQUET / "candles/binance/um"
    out = {}
    for sym in symbols:
        d = base / sym / "15m"
        if not d.is_dir():
            continue
        ys = {int(f.stem) for f in d.glob("*.parquet") if f.stem.isdigit()}
        if ys:
            out[sym] = ys
    return out


def build_plan(symbols: list[str], year_map: dict[str, set[int]]) -> list[dict]:
    """200 组：起点分层错开 + 组内随机抽标的。同一份 SEED 必然生成同一份计划。

    抽样池按「窗口两端年份都有数据」预筛。早年标的少（2021 年只有 88 个），
    不足 100 个时全取，并在计划里如实记下实际数量，不拿空跑凑数。
    """
    latest_start = FULL_END - dt.timedelta(days=WINDOW_DAYS)
    span_days = (latest_start - FULL_START).days
    if span_days <= 0:
        raise SystemExit("窗口太长，起点范围为空")
    seg = span_days / float(GROUPS)

    plan = []
    for i in range(GROUPS):
        # 分层错开：第 i 组落在第 i 段内，段内用组号派生的随机数取点
        rng_off = random.Random(SEED * 31 + i)
        offset = i * seg + rng_off.uniform(0.0, seg)
        s = FULL_START + dt.timedelta(days=int(offset))
        e = s + dt.timedelta(days=WINDOW_DAYS)

        need = {s.year, e.year}
        pool = [x for x in symbols if need <= year_map.get(x, set())]
        if len(pool) < PER_GROUP:
            # 窗口两端不全有数据时放宽到「起始年有数据」，仍然比盲抽好得多
            pool = [x for x in symbols if s.year in year_map.get(x, set())]
        if not pool:
            pool = symbols[:]

        rng_sym = random.Random(SEED + 1000 + i)
        n = min(PER_GROUP, len(pool))
        picked = sorted(rng_sym.sample(pool, n))
        plan.append({"group": i, "start": s.isoformat(), "end": e.isoformat(),
                     "target": PER_GROUP, "actual": n,
                     "pool": len(pool), "symbols": picked})
    return plan


# ────────────────────────────────────────── worker 侧

_runner = None
_cfg = None
_run_bt = None


def _init_worker():
    global _runner, _cfg, _run_bt
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

    _runner = EventTrendRunner(base_dir=PROJECT_ROOT)
    _cfg = EventTrendConfig(initial_equity=INITIAL_EQUITY)
    _run_bt = run_event_trend_backtest


def run_task(task):
    """一次回测 = 一个标的 × 一个窗口。只跑变体 A。"""
    g, sym, s, e = task
    rec = {"group": g, "symbol": sym, "start": s, "end": e,
           "status": "error", "bars15": 0, "bars1h": 0, "candidates": 0,
           "trades": 0, "wins": 0, "net": 0.0, "gross": 0.0,
           "fees": 0.0, "funding": 0.0, "slip": 0.0,
           "spec_status": "", "formal": False, "err": "", "elapsed_s": 0.0}
    t0 = time.time()
    try:
        dfs = _runner.load_symbol_data(sym, start_date=s, end_date=e, allow_auto_window=False)
        df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = dfs
        rec["bars15"] = int(len(df_15m))
        rec["bars1h"] = int(len(df_1h))
        if len(df_15m) < 2000 or len(df_1h) < 500:
            rec["status"] = "no_data"
            rec["elapsed_s"] = round(time.time() - t0, 2)
            return rec

        spec = _runner._resolve_spec(sym, df_15m, _cfg)
        sm = _runner.run_state_machine_simulation(
            sym, df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True)
        rec["candidates"] = int(len(sm.candidates))

        res = _run_bt(
            symbol=sym, df_1h=df_1h, df_15m=df_15m, candidates=sm.candidates,
            funding_events=funding, df_4h=df_4h, variant="A",
            initial_equity=INITIAL_EQUITY, config=_cfg, instrument_spec=spec)

        smry = res.get("summary") or {}
        trades = (res.get("engine_result") or {}).get("trades") or []
        rec["status"] = "ok"
        rec["trades"] = int(len(trades))
        rec["wins"] = int(sum(1 for t in trades if float(t.get("net_pnl", 0.0)) > 0))
        rec["net"] = round(float(smry.get("net_pnl", 0.0)), 6)
        rec["gross"] = round(sum(float(t.get("gross_pnl", 0.0)) for t in trades), 6)
        rec["fees"] = round(float(smry.get("fee_total", 0.0)), 6)
        rec["funding"] = round(float(smry.get("funding_total", 0.0)), 6)
        rec["slip"] = round(float(smry.get("slippage_total", 0.0)), 6)
        rec["spec_status"] = str(smry.get("spec_status", ""))
        rec["formal"] = bool(smry.get("formal_pass_allowed", False))

        rec["trade_list"] = [{
            "group": g, "symbol": sym,
            "t_in": int(t.get("entry_time", 0)), "t_out": int(t.get("exit_time", 0)),
            "p_in": float(t.get("entry_price", 0.0)), "p_out": float(t.get("exit_price", 0.0)),
            "qty": float(t.get("qty", 0.0)), "net": round(float(t.get("net_pnl", 0.0)), 6),
            "why": str(t.get("exit_reason", "")),
        } for t in trades]
    except Exception as exc:
        rec["status"] = "error"
        rec["err"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    rec["elapsed_s"] = round(time.time() - t0, 2)
    return rec


# ────────────────────────────────────────── 汇总

def build_report(records: list[dict], plan: list[dict], wall_s: float) -> str:
    import statistics as st

    ok = [r for r in records if r["status"] == "ok"]
    err = [r for r in records if r["status"] == "error"]
    nod = [r for r in records if r["status"] == "no_data"]
    traded = [r for r in ok if r["trades"] > 0]

    all_trades = []
    for r in records:
        all_trades.extend(r.get("trade_list") or [])

    # 窗口高度重叠 ⇒ 同一笔交易会在几十个窗口里重复出现。
    # 统计必须去重，否则样本量被虚增，会把「不显著」误判成「显著」。
    def _key(t):
        return (t["symbol"], int(t["t_in"]), int(t["t_out"]),
                round(float(t["p_in"]), 12), round(float(t["net"]), 6))

    uniq_map = {}
    for t in all_trades:
        uniq_map.setdefault(_key(t), t)
    uniq_trades = list(uniq_map.values())
    mult = len(all_trades) / max(1, len(uniq_trades))

    wins = [t for t in all_trades if t["net"] > 0]
    losses = [t for t in all_trades if t["net"] <= 0]
    u_wins = [t for t in uniq_trades if t["net"] > 0]
    u_net = [t["net"] for t in uniq_trades]
    u_mean = (sum(u_net) / len(u_net)) if u_net else 0.0
    u_sd = st.stdev(u_net) if len(u_net) > 1 else 0.0
    u_se = (u_sd / (len(u_net) ** 0.5)) if u_net else 0.0
    u_t = (u_mean / u_se) if u_se > 0 else 0.0

    def s(xs):
        return round(sum(xs), 2)

    # 按组
    by_group = {}
    for r in ok:
        g = by_group.setdefault(r["group"], {"net": 0.0, "trades": 0, "wins": 0, "n": 0})
        g["net"] += r["net"]
        g["trades"] += r["trades"]
        g["wins"] += r["wins"]
        g["n"] += 1
    g_net = [v["net"] for v in by_group.values()]
    pos_groups = sum(1 for x in g_net if x > 0)

    # 按年（用窗口起点年份）
    by_year = {}
    for r in ok:
        y = r["start"][:4]
        v = by_year.setdefault(y, {"net": 0.0, "trades": 0, "wins": 0, "runs": 0})
        v["net"] += r["net"]
        v["trades"] += r["trades"]
        v["wins"] += r["wins"]
        v["runs"] += 1

    # 按标的
    by_sym = {}
    for r in ok:
        v = by_sym.setdefault(r["symbol"], {"net": 0.0, "trades": 0, "wins": 0, "runs": 0})
        v["net"] += r["net"]
        v["trades"] += r["trades"]
        v["wins"] += r["wins"]
        v["runs"] += 1
    sym_rank = sorted(by_sym.items(), key=lambda kv: kv[1]["net"])

    formal_ok = sum(1 for r in ok if r["formal"])

    L = []
    L.append("# 全币种全时段错开压力测试 · %d 组 × 每组 %d 标的" % (len(plan), PER_GROUP))
    L.append("")
    L.append("- 生成时间：%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("- 全时段：%s ~ %s" % (FULL_START.isoformat(), FULL_END.isoformat()))
    L.append("- 每组窗口：%d 天；组数 %d；每组标的 %d" % (WINDOW_DAYS, len(plan), PER_GROUP))
    L.append("- 随机种子：%d（同一份种子必然复现同一份计划）" % SEED)
    L.append("- 并发进程：%d；总耗时 %.1f 分钟" % (WORKERS, wall_s / 60.0))
    L.append("- 变体：只跑 A（完整含持仓量过滤）")
    L.append("")
    L.append("## 一、任务执行情况")
    L.append("")
    L.append("| 项 | 数值 |")
    L.append("|---|---:|")
    L.append("| 计划任务 | %d |" % sum(g["actual"] for g in plan))
    L.append("| 有效回测（ok） | %d |" % len(ok))
    L.append("| 数据不足跳过 | %d |" % len(nod))
    L.append("| 报错 | %d |" % len(err))
    L.append("| 合约规格证明通过（formal） | %d |" % formal_ok)
    if formal_ok == 0 and ok:
        L.append("")
        L.append("> `formal` 全为 0 是预期内的：合约规格库无法证明「这条规格在该标的历史窗口内一直适用」，"
                 "所以每次 run 的 `formal_pass_allowed` 都是 False。这只影响「能否对外声明真实历史盈亏已修正」，"
                 "不影响本报告对策略行为的统计。")
    if err:
        L.append("")
        L.append("报错类型前 5：")
        from collections import Counter
        for k, v in Counter(r["err"].split(":")[0] for r in err).most_common(5):
            L.append("- `%s`：%d 次" % (k, v))
    L.append("")
    L.append("## 二、总体结果")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---:|")
    L.append("| 有成交的回测 | %d / %d（%.1f%%）|" % (
        len(traded), len(ok), 100.0 * len(traded) / max(1, len(ok))))
    L.append("| 成交记录行数（含重复） | %d |" % len(all_trades))
    L.append("| **去重后唯一交易** | **%d** |" % len(uniq_trades))
    L.append("| 平均重复倍数 | %.2f 倍 |" % mult)
    L.append("")
    L.append("> **口径警告**：本批次 200 组窗口都是 365 天，铺在 %s ~ %s 共 %d 天里，"
             "相邻两组起点只差约 %.1f 天，窗口重叠约 %.0f%%。同一笔交易会在几十个窗口里"
             "重复出现，平均重复 %.2f 倍。**下面第二节用去重后的唯一交易，"
             "第三节的组间稳定性用的是重复计数（因为那反映的是「一组跑下来赚不赚」）。**"
             % (FULL_START.isoformat(), FULL_END.isoformat(),
                (FULL_END - FULL_START).days,
                (FULL_END - FULL_START).days / max(1, len(plan)),
                max(0.0, 100.0 * (1 - (FULL_END - FULL_START).days / max(1, len(plan)) / WINDOW_DAYS)),
                mult))
    L.append("")
    L.append("### 2.1 去重后的唯一交易（真实样本）")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---:|")
    L.append("| 唯一交易笔数 | %d |" % len(uniq_trades))
    L.append("| 盈利 / 亏损 | %d / %d |" % (len(u_wins), len(uniq_trades) - len(u_wins)))
    L.append("| 胜率 | %s |" % (
        "%.2f%%" % (100.0 * len(u_wins) / len(uniq_trades)) if uniq_trades else "—"))
    L.append("| 净盈亏合计 | %+.2f U |" % sum(u_net))
    L.append("| 每笔期望 | %+.4f U |" % u_mean)
    L.append("| 每笔标准差 | %.4f U |" % u_sd)
    L.append("| t 值 | %.2f |" % u_t)
    L.append("| 95%% 置信区间 | [%+.3f, %+.3f] U |" % (
        u_mean - 1.96 * u_se, u_mean + 1.96 * u_se))
    L.append("| 判定 | %s |" % (
        "显著为负" if u_t < -1.96 else ("显著为正" if u_t > 1.96 else "与 0 无法区分")))
    L.append("")
    L.append("### 2.2 重复计数口径（会把样本量虚增 %.1f 倍，仅供对照）" % mult)
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---:|")
    L.append("| 成交记录行数 | %d |" % len(all_trades))
    L.append("| 盈利笔数 / 亏损笔数 | %d / %d |" % (len(wins), len(losses)))
    L.append("| 胜率 | %s |" % ("%.2f%%" % (100.0 * len(wins) / len(all_trades)) if all_trades else "—"))
    L.append("| 净盈亏合计 | %+.2f U |" % s(t["net"] for t in all_trades))
    L.append("| 每笔期望 | %s |" % (
        "%+.4f U" % (s(t["net"] for t in all_trades) / len(all_trades)) if all_trades else "—"))
    if wins and losses:
        avg_w = sum(t["net"] for t in wins) / len(wins)
        avg_l = sum(t["net"] for t in losses) / len(losses)
        L.append("| 平均盈利 / 平均亏损 | %+.4f / %+.4f U |" % (avg_w, avg_l))
        L.append("| 盈亏比 | %.2f |" % (avg_w / abs(avg_l) if avg_l else float("inf")))
    L.append("")
    L.append("## 三、200 组之间的稳定性（重复计数口径，回答「一组跑下来赚不赚」）")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---:|")
    L.append("| 净盈亏为正的组 | %d / %d（%.1f%%）|" % (
        pos_groups, len(g_net), 100.0 * pos_groups / max(1, len(g_net))))
    L.append("| 单组净盈亏 中位数 | %+.2f U |" % st.median(g_net) if g_net else "| — |")
    L.append("| 单组净盈亏 均值 | %+.2f U |" % (sum(g_net) / len(g_net)) if g_net else "| — |")
    L.append("| 单组净盈亏 最好 / 最差 | %+.2f / %+.2f U |" % (max(g_net), min(g_net)) if g_net else "| — |")
    L.append("| 单组净盈亏 标准差 | %.2f U |" % st.pstdev(g_net) if len(g_net) > 1 else "| — |")
    L.append("")
    L.append("## 四、按窗口起点年份拆分")
    L.append("")
    L.append("| 起点年份 | 回测数 | 成交笔数 | 胜率 | 净盈亏 |")
    L.append("|---|---:|---:|---:|---:|")
    for y in sorted(by_year):
        v = by_year[y]
        wr = ("%.1f%%" % (100.0 * v["wins"] / v["trades"])) if v["trades"] else "—"
        L.append("| %s | %d | %d | %s | %+.2f |" % (y, v["runs"], v["trades"], wr, v["net"]))
    L.append("")
    L.append("## 五、标的盈亏两端各 10 个（跨全部窗口累计）")
    L.append("")
    L.append("| 标的 | 回测次数 | 成交 | 净盈亏 |")
    L.append("|---|---:|---:|---:|")
    for sym, v in sym_rank[:10]:
        L.append("| %s | %d | %d | %+.2f |" % (sym, v["runs"], v["trades"], v["net"]))
    L.append("| … | | | |")
    for sym, v in sym_rank[-10:]:
        L.append("| %s | %d | %d | %+.2f |" % (sym, v["runs"], v["trades"], v["net"]))
    L.append("")
    L.append("## 六、落地文件")
    L.append("")
    L.append("- `plan.json`：200 组的窗口与标的清单")
    L.append("- `runs.jsonl`：每次回测一行（%d 行）" % len(records))
    L.append("- `trades.jsonl`：全部成交明细（%d 行）" % len(all_trades))
    L.append("")
    L.append("## 七、这份结果不能说明什么")
    L.append("")
    L.append("1. 只跑变体 A，没有做「关掉持仓量过滤」的消融对比，所以不能说「OI 过滤贡献了多少」。")
    L.append("2. **200 组不是 200 个独立样本**。窗口 %d 天铺在 %d 天里，起点间隔约 %.1f 天，"
             "重叠约 %.0f%%，去重后只有 %d 笔唯一交易。第三节的「组间稳定性」反映的是"
             "「任意取一个 365 天窗口跑一遍赚不赚」，不能当成 200 次独立试验来做统计推断。"
             % (WINDOW_DAYS, (FULL_END - FULL_START).days,
                (FULL_END - FULL_START).days / max(1, len(plan)),
                max(0.0, 100.0 * (1 - (FULL_END - FULL_START).days / max(1, len(plan)) / WINDOW_DAYS)),
                len(uniq_trades)))
    L.append("3. 窗口固定 %d 天，没有测更短（周级）或更长（多年）的持有周期适应性。" % WINDOW_DAYS)
    L.append("4. 标的池只有 %d 个，同一标的会被不同窗口反复抽到，这是去重后唯一交易仍然偏少的直接原因。"
             % len(POOL_SIZE))
    L.append("")
    return "\n".join(L)


def main() -> int:
    global POOL_SIZE
    symbols = list_symbols()
    POOL_SIZE = symbols

    # REPORT_ONLY=1：不跑回测，只用已有 runs.jsonl 重新生成报告（改口径时用）
    if os.environ.get("REPORT_ONLY", "0") == "1":
        if not (RUNS_JSONL.exists() and PLAN_JSON.exists()):
            raise SystemExit("缺 runs.jsonl 或 plan.json，无法只出报告")
        plan = json.loads(PLAN_JSON.read_text(encoding="utf-8"))
        records = [json.loads(l) for l in RUNS_JSONL.open(encoding="utf-8") if l.strip()]
        report = build_report(records, plan, 0.0)
        REPORT_MD.write_text(report, encoding="utf-8")
        print("报告已按新口径重算 → %s（%d 条记录）" % (REPORT_MD.name, len(records)))
        return 0

    print("=" * 78)
    print("全币种全时段错开压力测试")
    print("标的池 %d 个 ｜ 全时段 %s ~ %s ｜ 每组窗口 %d 天" % (
        len(symbols), FULL_START, FULL_END, WINDOW_DAYS))
    print("计划 %d 组 × %d 标的 = %d 次回测 ｜ 并发 %d 进程" % (
        GROUPS, PER_GROUP, GROUPS * PER_GROUP, WORKERS))
    print("=" * 78)

    plan = build_plan(symbols, symbol_year_map(symbols))
    PLAN_JSON.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    n_actual = sum(g["actual"] for g in plan)
    short = [g for g in plan if g["actual"] < PER_GROUP]
    print("计划已写入 %s" % PLAN_JSON.name)
    print("首组 %s ~ %s ｜ 末组 %s ~ %s" % (
        plan[0]["start"], plan[0]["end"], plan[-1]["start"], plan[-1]["end"]))
    print("实际任务 %d（%d 组标的不足 %d 个，按可得数据全取）" % (
        n_actual, len(short), PER_GROUP))

    tasks = [(g["group"], sym, g["start"], g["end"])
             for g in plan for sym in g["symbols"]]

    # 断点续跑：RESUME=1 时读已有 runs.jsonl，跳过已完成的任务，不重复不遗漏
    resume = os.environ.get("RESUME", "0") == "1"
    records: list[dict] = []
    if resume and RUNS_JSONL.exists():
        seen = set()
        for line in RUNS_JSONL.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            seen.add((r.get("group"), r.get("symbol")))
            records.append(r)
        before = len(tasks)
        tasks = [t for t in tasks if (t[0], t[1]) not in seen]
        print("断点续跑：已有 %d 条记录，跳过 %d 个任务，剩余 %d" % (
            len(records), before - len(tasks), len(tasks)))

    print("任务总数 %d" % len(tasks))
    if not tasks:
        print("没有待跑任务，直接出报告")
        report = build_report(records, plan, 0.0)
        REPORT_MD.write_text(report, encoding="utf-8")
        print("报告 → %s" % REPORT_MD)
        return 0

    t_all = time.time()
    t_start = time.time()

    with RUNS_JSONL.open("a" if resume else "w", encoding="utf-8") as fr:
        with cf.ProcessPoolExecutor(max_workers=WORKERS, initializer=_init_worker) as ex:
            futs = [ex.submit(run_task, t) for t in tasks]
            done = 0
            for fut in cf.as_completed(futs):
                try:
                    r = fut.result()
                except Exception as exc:
                    r = {"status": "error", "err": "worker_crash: %s" % str(exc)[:160],
                         "group": -1, "symbol": "?", "start": "", "end": "",
                         "bars15": 0, "bars1h": 0, "candidates": 0, "trades": 0,
                         "wins": 0, "net": 0.0, "gross": 0.0, "fees": 0.0,
                         "funding": 0.0, "slip": 0.0, "spec_status": "", "formal": False,
                         "elapsed_s": 0.0, "trade_list": []}
                fr.write(json.dumps(r, ensure_ascii=False) + "\n")
                records.append(r)
                done += 1
                if done % 500 == 0 or done == len(tasks):
                    el = time.time() - t_start
                    eta = el / done * (len(tasks) - done)
                    print("  %6d/%d ｜ 已用 %5.1f 分 ｜ 预计剩余 %5.1f 分 ｜ 成交 %d 笔" % (
                        done, len(tasks), el / 60.0, eta / 60.0,
                        sum(x.get("trades", 0) for x in records)), flush=True)

    wall = time.time() - t_all
    print("-" * 78)
    ok = [r for r in records if r["status"] == "ok"]
    print("完成 %d ｜ 有效 %d ｜ 成交合计 %d 笔 ｜ 净盈亏 %+.2f ｜ 用时 %.1f 分" % (
        len(records), len(ok), sum(r["trades"] for r in records),
        sum(r["net"] for r in records), wall / 60.0))

    # 逐笔明细单独展平一份，便于外部直接读
    n_t = 0
    with TRADES_JSONL.open("w", encoding="utf-8") as ft:
        for r in records:
            for t in (r.get("trade_list") or []):
                ft.write(json.dumps(t, ensure_ascii=False) + "\n")
                n_t += 1
    print("逐笔明细 %d 行 → %s" % (n_t, TRADES_JSONL.name))

    report = build_report(records, plan, wall)
    REPORT_MD.write_text(report, encoding="utf-8")
    print("报告 → %s" % REPORT_MD)

    # ── 登记成系统里可查的批次（BACKFILL-01）────────────────────────────────
    # 本脚本刻意不落 run 目录（两万个目录约 36GB，磁盘放不下），但「跑过什么」
    # 必须能在回测页看到：把 runs.jsonl 现算成 batch_meta.json（清单型批次），
    # 批次列表/汇总即显示真实条数与净盈亏，明细指回 runs.jsonl / trades.jsonl。
    # 登记失败不影响回测结果本身（结果文件已落盘）。
    try:
        from 工具_登记压测批次 import main as _register_batch
        _register_batch(["--apply"])
    except Exception as exc:                                  # noqa: BLE001
        print("批次登记失败（不影响回测结果）：%s: %s" % (type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
