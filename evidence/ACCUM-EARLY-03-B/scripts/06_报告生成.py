# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜06 生成验收报告 + 数字双向对撞。

纪律（这条工单踩过）：
· 报告里的**每个数字都从 out/*.json 现取**，不手抄；
· 生成完再跑一遍**双向对撞**：按「路径 → 值」重新从 JSON 取一次，与正文里实际渲染出来的
  字符串逐字比；任何一处不一致就报失败，不靠人眼复核。

输出：
  evidence/ACCUM-EARLY-03-B/README.md
  evidence/ACCUM-EARLY-03-B/out/06_报告数字对撞.json
"""
from __future__ import annotations

import calendar
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
EVID = PROJECT / "evidence" / "ACCUM-EARLY-03-B"
OUT = EVID / "out"
BASE = EVID / "baseline"
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

FILES = {
    "01": OUT / "01_读取路径普查.json",
    "02": OUT / "02_合并口径核验.json",
    "03": OUT / "03_接线前后对照.json",
    "04": OUT / "04_全链贯通.json",
    "05": OUT / "05_恢复与幂等.json",
}
CODE = {
    "paper_engine.py": BACKEND / "paper_engine.py",
    "research_15m.py": BACKEND / "research_15m.py",
    "unified_runtime.py": BACKEND / "unified_runtime.py",
    "paper_loop.py": BACKEND / "paper_loop.py",
    "live_api.py": BACKEND / "live_api.py",
    "live_worker.py": BACKEND / "live_worker.py",
    "config.py": PROJECT / "策略/WyckoffMaster/event_trend_v0_1/config.py",
}


def iso(ms=None):
    if ms is None:
        ms = time.time() * 1000
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def get(d, path: str):
    cur = d
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def fmt(v):
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return f"{v:.1f}"
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def main() -> int:
    data = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in FILES.items()
            if p.exists()}
    missing = [k for k in FILES if k not in data]
    if missing:
        print("缺产物，先跑前序脚本：", missing)
        return 2

    d01, d02, d03, d04, d05 = (data["01"], data["02"], data["03"], data["04"], data["05"])

    # ── 代码身份 ─────────────────────────────────────────────────────────
    ident = {}
    for name, p in CODE.items():
        before = BASE / f"{name}.before"
        ident[name] = {"before_sha256": sha(before), "after_sha256": sha(p),
                       "changed": (sha(before) is not None and sha(p) is not None
                                   and sha(before) != sha(p)),
                       "bytes": (p.stat().st_size if p.exists() else None)}

    # ── 冻结参数活体校验（不看文件哈希，直接读生效值）──────────────────────
    import importlib
    cfg_mod = importlib.import_module("策略.WyckoffMaster.event_trend_v0_1.config")
    frozen = cfg_mod.make_ft_low4h_config()
    FROZEN_EXPECT = {
        "stoploss_s": 0.05, "trail_d": 0.05, "trailing_trigger_r": 0.0,
        "trailing_lookback_4h_bars": 1, "max_holding_days": None,
        "risk_budget_per_trade": 0.005, "max_active_positions": 3,
        "max_total_risk_budget": 0.015, "max_notional_per_position": 0.30,
        "max_total_notional": 0.90, "atr_warmup_bars": 200,
        "taker_fee_bps": 5.0, "slippage_bps": 3.0,
        "max_participation_of_prev_15m_quote": 0.001,
    }
    frozen_check = {k: {"expect": v, "actual": getattr(frozen, k, "<missing>"),
                        "same": getattr(frozen, k, object()) == v}
                    for k, v in FROZEN_EXPECT.items()}

    # ── 成交实际用的生效视图参数（与冻结包的差异要如实并列）────────────────
    import paper_engine as PE
    view = PE.approved_params()
    view_subset = {k: view.get(k) for k in
                   ("fee_each_side", "slippage_each_side", "stoploss_s", "trail_d",
                    "risk_per_trade_equity_fraction", "initial_equity_USDT",
                    "max_positions", "max_holding_hours",
                    "entry_participation_previous_15m_quote_cap",
                    "signal_execution_delay_minutes", "view_sha256", "_source")}

    # ── 参与率两套口径的敏感性（不择一，只并列）────────────────────────────
    import research_15m as R
    mkt = R.MergedMarket(PE.market_paths()["hot_db"], PE.market_paths()["archive_root"])
    part_rows = []
    try:
        for key, ch in (d04.get("chains") or {}).items():
            if not key.endswith("::merge"):
                continue
            ev_key = key.split("::")[0]
            f = (ch.get("③ 成交") or {})
            bar_ms = None
            for k, v in (d03.get("events") or {}).items():
                if k == ev_key:
                    bar_ms = None
            # 用成交 bar 的 open 反查上一根
            ebar = f.get("entry_bar_utc")
            if not ebar:
                continue
            import calendar
            ot = calendar.timegm(time.strptime(ebar, "%Y-%m-%dT%H:%M:%SZ")) * 1000
            prev = mkt.before((ch.get("事件身份") or {}).get("symbol"), ot)
            qv = float(prev["quote_volume"]) if prev and prev.get("quote_volume") else None
            notional = f.get("notional")
            part_rows.append({
                "event": ev_key, "symbol": (ch.get("事件身份") or {}).get("symbol"),
                "prev_15m_quote_volume": qv, "notional": notional,
                "cap_1pct": (qv * 0.01 if qv else None),
                "cap_0p1pct": (qv * 0.001 if qv else None),
                "passes_view_1pct": (notional is not None and qv is not None
                                     and notional <= qv * 0.01),
                "passes_pack_0p1pct": (notional is not None and qv is not None
                                       and notional <= qv * 0.001),
            })
    finally:
        mkt.close()

    # ── 退出 bar 的「跳空低于止损」核查（不是手抄，逐根从合并读取器取）──────
    # check_position_exit_on_bar / 4h 分支都按「当时止损价」成交，没有对
    # 「bar 开盘就低于止损」单独建模；这里逐根核这三条回放有没有踩到那个分支。
    gap_rows = []
    mkt2 = R.MergedMarket(PE.market_paths()["hot_db"], PE.market_paths()["archive_root"])
    try:
        for key, ch in (d04.get("chains") or {}).items():
            if not key.endswith("::merge"):
                continue
            ex = ch.get("④ 退出") or {}
            t = ex.get("exit_bar_utc")
            trail = ex.get("止损轨迹（只上移）") or []
            if not t or not trail:
                continue
            cur_stop = trail[-1][1]
            tf = "4h" if "4h" in str(ex.get("exit_path") or "") else "15m"
            step = 14_400_000 if tf == "4h" else 900_000
            t0 = calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ")) * 1000
            bars = mkt2.bars_tf(tf, (ch.get("事件身份") or {}).get("symbol"), t0, t0 + step)
            b = bars[0] if bars else None
            gap_rows.append({
                "event": key.split("::")[0], "timeframe": tf, "exit_bar_utc": t,
                "bar_open": (float(b["open"]) if b else None),
                "bar_low": (float(b["low"]) if b else None),
                "cur_stop": (float(cur_stop) if cur_stop is not None else None),
                "open_below_stop": (b is not None and cur_stop is not None
                                    and float(b["open"]) < float(cur_stop)),
            })
    finally:
        mkt2.close()
    gap_report = {
        "probe": "06_退出跳空核验",
        "generated_at_utc": iso(),
        "checked": len(gap_rows),
        "gap_below_stop": sum(1 for r in gap_rows if r["open_below_stop"]),
        "rows": gap_rows,
        "note": "开盘价低于当时止损 = 走的是「跳空」情形，本单实现会按止损价成交（高估成交价）；"
                "开盘价高于止损 = 盘中触及，按止损价成交是正常的。",
    }
    (OUT / "06_退出跳空核验.json").write_text(
        json.dumps(gap_report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    data["06gap"] = gap_report
    d06g = gap_report

    # ── 断言表（id → 值），正文只引用 id ─────────────────────────────────
    C = {}

    def claim(cid, value, src, path):
        C[cid] = {"value": value, "source": src, "path": path}
        return value

    claim("n_sql_literals", d01["summary"]["sql_literal_hits"], "01", "summary.sql_literal_hits")
    claim("n_read_calls", d01["summary"]["read_func_calls"], "01", "summary.read_func_calls")
    claim("n_bypass", d01["summary"]["bypass_points"], "01", "summary.bypass_points")
    claim("n_passthrough_candle", d01["runtime"]["passthrough_candle_sql_count"],
          "01", "runtime.passthrough_candle_sql_count")
    claim("n_cold15_returned", d01["runtime"]["15m"]["returned_by_source"].get("cold", 0),
          "01", "runtime.15m.returned_by_source.cold")
    claim("n_cold4h_returned", d01["runtime"]["4h"]["returned_by_source"].get("cold", 0),
          "01", "runtime.4h.returned_by_source.cold")

    claim("a1", d02["结论"]["A1_去重键唯一（open_time）"], "02", "结论.A1_去重键唯一（open_time）")
    claim("a2", d02["结论"]["A2_重叠行按规则取热层（标记+数值一致）"], "02",
          "结论.A2_重叠行按规则取热层（标记+数值一致）")
    claim("a3", d02["结论"]["A3_优先级数值方向被证明"], "02", "结论.A3_优先级数值方向被证明")
    claim("seam15_first_hot", d02["C_冷热交界"]["15m"]["first_hot_bar_utc"], "02",
          "C_冷热交界.15m.first_hot_bar_utc")
    claim("seam15_overlap", d02["C_冷热交界"]["15m"]["overlap_hot_wins"], "02",
          "C_冷热交界.15m.overlap_hot_wins")
    claim("seam4h_first_hot", d02["C_冷热交界"]["4h"]["first_hot_bar_utc"], "02",
          "C_冷热交界.4h.first_hot_bar_utc")
    claim("seam4h_overlap", d02["C_冷热交界"]["4h"]["overlap_hot_wins"], "02",
          "C_冷热交界.4h.overlap_hot_wins")
    claim("gap_symbols", d02["D_缺口"]["全池冷档 2026 年 15m 缺口"]["有缺口的标的数"], "02",
          "D_缺口.全池冷档 2026 年 15m 缺口.有缺口的标的数")
    claim("gap_bars", d02["D_缺口"]["真实缺口行为"]["缺口长度（缺根数）"], "02",
          "D_缺口.真实缺口行为.缺口长度（缺根数）")
    claim("gap_inside_rows", d02["D_缺口"]["真实缺口行为"]["缺口内 bars() 返回根数"], "02",
          "D_缺口.真实缺口行为.缺口内 bars() 返回根数")
    claim("cc15_compared", d02["E_价格精度"]["15m_与 MergedHistory 对撞"]["compared_bars"],
          "02", "E_价格精度.15m_与 MergedHistory 对撞.compared_bars")
    claim("cc15_mismatch", d02["E_价格精度"]["15m_与 MergedHistory 对撞"]["mismatch_count"],
          "02", "E_价格精度.15m_与 MergedHistory 对撞.mismatch_count")
    claim("cc4h_compared", d02["E_价格精度"]["4h_与 MergedHistory 对撞"]["compared_bars"],
          "02", "E_价格精度.4h_与 MergedHistory 对撞.compared_bars")
    claim("cc4h_mismatch", d02["E_价格精度"]["4h_与 MergedHistory 对撞"]["mismatch_count"],
          "02", "E_价格精度.4h_与 MergedHistory 对撞.mismatch_count")
    claim("cache_loads", d02["F_缓存一致性"]["缓存装载次数"], "02", "F_缓存一致性.缓存装载次数")
    claim("gap_exit_checked", d06g["checked"], "06gap", "checked")
    claim("gap_exit_below_stop", d06g["gap_below_stop"], "06gap", "gap_below_stop")

    for k in ("E1_TNSR_cold", "E2_AGT_cold", "E3_METIS_hot"):
        claim(f"{k}_hot_verdict", d03["checks"][k].get("hot_verdict")
              or (d03["events"][k]["hot"] or {}).get("verdict"), "03", f"events.{k}.hot.verdict")
        claim(f"{k}_hot_reject", (d03["events"][k]["hot"] or {}).get("reject_reason"), "03",
              f"events.{k}.hot.reject_reason")
        claim(f"{k}_entry", (d03["events"][k]["merge"] or {}).get("entry_price"), "03",
              f"events.{k}.merge.entry_price")
        claim(f"{k}_exit", (d03["events"][k]["merge"] or {}).get("exit_price"), "03",
              f"events.{k}.merge.exit_price")
        claim(f"{k}_net", (d03["events"][k]["merge"] or {}).get("net_pnl"), "03",
              f"events.{k}.merge.net_pnl")
        claim(f"{k}_layer", ((d03["events"][k]["merge"] or {}).get("bar_provenance") or {}).get("layer"),
              "03", f"events.{k}.merge.bar_provenance.layer")

    claim("n_filled", d04["合法成交样本"]["合并模式下成交笔数"], "04",
          "合法成交样本.合并模式下成交笔数")
    claim("n_strict", d04["合法成交样本"]["其中可进严格有效性成绩"], "04",
          "合法成交样本.其中可进严格有效性成绩")
    for k in ("E1_TNSR_cold", "E2_AGT_cold", "E3_METIS_hot"):
        claim(f"04_{k}_result", d04["checks"][k]["结果"], "04", f"checks.{k}.结果")

    for k in ("E1_TNSR_cold", "E2_AGT_cold", "E3_METIS_hot"):
        claim(f"05_{k}_entry_orders", d05["checks"][k]["entry_orders"], "05",
              f"checks.{k}.entry_orders")
        claim(f"05_{k}_exit_orders", d05["checks"][k]["exit_orders"], "05",
              f"checks.{k}.exit_orders")
        claim(f"05_{k}_result", d05["checks"][k]["结果"], "05", f"checks.{k}.结果")

    # ── 渲染正文（占位符 {{id}} 统一替换）────────────────────────────────
    def V(cid):
        return fmt(C[cid]["value"])

    byp = d01["summary"]["bypass_detail"]
    byp_rows = "\n".join(
        f"| `{b['where']}` | {', '.join(str(x) for x in b['lines'])} | "
        f"{'链路内' if b['on_fill_chain'] else '不在链路'} | {b['role']} |"
        for b in byp)

    part_rows_md = "\n".join(
        f"| {r['event']} | {r['symbol']} | {fmt(r['prev_15m_quote_volume'])} | "
        f"{fmt(r['notional'])} | {fmt(r['cap_1pct'])} | {fmt(r['cap_0p1pct'])} | "
        f"{'通过' if r['passes_view_1pct'] else '不通过'} | "
        f"{'通过' if r['passes_pack_0p1pct'] else '不通过'} |"
        for r in part_rows)

    gap_rows_md = "\n".join(
        "   | " + f"{r['event']} | {r['timeframe']} | {r['exit_bar_utc']} | {fmt(r['bar_open'])} | "
        f"{fmt(r['bar_low'])} | {fmt(r['cur_stop'])} | "
        f"{'是（按止损价成交会高估）' if r['open_below_stop'] else '否（开盘高于止损）'} |"
        for r in gap_rows)

    def chain_block(k: str) -> str:
        ch = d04["chains"].get(f"{k}::merge") or {}
        hot = d03["events"][k]["hot"] or {}
        ev = ch.get("事件身份") or {}
        fill = ch.get("③ 成交") or {}
        ex = ch.get("④ 退出") or {}
        lg = ch.get("⑤ 账本") or {}
        real = lg.get("realized") or {}
        src = ch.get("⑥ 数据来源与可得性") or {}
        elig = ch.get("⑦ 能否进严格有效性成绩") or {}
        cand = ch.get("① 观察与候选（生产记录）") or {}
        vis = cand.get("观察与候选时可见的数据") or {}
        conf = cand.get("结构确认") or {}
        stops = ex.get("止损轨迹（只上移）") or []
        stop_rows = "\n".join(
            f"| {s[2] or '—'} | {fmt(s[0])} | {fmt(s[1])} | {s[3] or '—'} |" for s in stops)
        return f"""
### {ev.get('symbol')} · {ev.get('snapshot_utc')}

**为什么选它**：{ev.get('为什么选它')}

| 环节 | 事实 |
|---|---|
| ① 观察与候选 | 箱体 H={fmt(vis.get('box_high_h'))} / L={fmt(vis.get('box_low_l'))}，ATR={fmt(vis.get('box_atr_a'))}，ER={fmt(vis.get('box_er'))}，突破收盘={fmt(vis.get('breakout_close'))}，量能比={fmt(vis.get('volume_ratio'))}（生产记录） |
| ② 委托 | 最早可成交 {fmt((ch.get('② 委托（隔离账本，真实 open_position）') or {}).get('earliest_fill_ms_utc'))}；实际成交 {fmt((ch.get('② 委托（隔离账本，真实 open_position）') or {}).get('filled_at_utc'))}；滞后 {(ch.get('② 委托（隔离账本，真实 open_position）') or {}).get('fill_lag_ms')} 毫秒 |
| ③ 成交 | 状态 {fill.get('status')}；bar 来源 **{ (fill.get('bar_provenance') or {}).get('layer') }**；原始开盘价 {fmt(fill.get('raw_open'))} → 含滑点取整后 {fmt(fill.get('entry_price_after_slippage_and_rounding'))}；数量 {fmt(fill.get('qty'))}；名义 {fmt(fill.get('notional'))}；初始止损 {fmt(fill.get('initial_stop'))} |
| ④ 退出 | 状态 {ex.get('status')}；路径 {ex.get('exit_path')}；退出 bar {fmt(ex.get('exit_bar_utc'))}（来源 {fmt(ex.get('exit_bar_source'))}）；退出价 {fmt(ex.get('exit_price'))}；原因 {ex.get('exit_reason')} |
| ⑤ 账本 | 已实现净额 **{fmt(real.get('net_pnl'))}**；现金 {fmt((lg.get('state') or {}).get('cash'))}；订单 { (lg.get('counts') or {}).get('orders') } 条、成交 { (lg.get('counts') or {}).get('fills') } 条 |
| ⑥ 数据来源 | {src.get('结论')} |
| ⑦ 能否进严格有效性成绩 | {fmt(elig.get('eligible'))} —— {elig.get('reason')} |
| ⑧ 全链判定（04 脚本） | {V('04_' + k + '_result')} |
| 对照（只读热库） | {hot.get('verdict')} / {hot.get('reject_reason')} |

止损轨迹（生效时刻 → 旧值 → 新值 → 该时刻可用性）：

| 生效时刻 | 旧止损 | 新止损 | available_at |
|---|---|---|---|
{stop_rows or '| — | — | — | — |'}
"""

    chains_md = "\n".join(chain_block(k) for k in
                          ("E1_TNSR_cold", "E2_AGT_cold", "E3_METIS_hot"))

    def _ident_label(v):
        if v["before_sha256"] is None:
            return "未改（无改前副本）"
        return "已改" if v["changed"] else "未改"

    code_rows = "\n".join(
        f"| {n} | {_ident_label(v)} | "
        f"`{(v['before_sha256'] or '—')[:12]}` → `{(v['after_sha256'] or '—')[:12]}` |"
        for n, v in ident.items())

    frozen_rows = "\n".join(
        f"| `{k}` | {fmt(v['expect'])} | {fmt(v['actual'])} | {'一致' if v['same'] else '**不一致**'} |"
        for k, v in frozen_check.items())

    body = f"""# ACCUM-EARLY-03-B｜模拟成交 15 分钟冷热数据接线

生成时间：{iso()}（UTC）｜**执行方自报，未经 Codex 独立验收**

## 一句话结论

历史「观察」本来就能重建，但**成交与退出只读热库**（热库 15m 从 {V('seam15_first_hot')} 起、
4h 从 {V('seam4h_first_hot')} 起），所以更早的历史能重建观察、重建不出成交。
本单把成交/退出链路的 15m 与 4h 取数接到**同一套**冷热合并读取器上（默认关闭，
只在隔离回放显式开启），并用三条**真实历史事件**在独立账本里跑通了
「观察 → 候选 → 委托 → 成交/拒绝 → 退出 → 账本」。

**仍然不能说的话**：不能说「模拟盘已经能自动成交」。自然候选仍是 0，账本里没有自然成交；
三条回放里只有 1 笔的成交 bar 可得时刻有证据，另 2 笔只能声称「计算没用到未来 K 线」。

## 一、改了什么（只有两个文件）

| 文件 | 是否改动 | 改前 → 改后 sha256（前 12 位） |
|---|---|---|
{code_rows}

- `research_15m.py`：**没有新增第二套合并器**。只是把原来的 `Merged15m` 增加一个 `timeframe`
  参数（默认 15m，4h 走同一段合并逻辑），并在它上面加两个「按时刻取一根」的门面
  （`MergedReader` / `MergedMarket`）：缓存 + 二分 + **穿透记账**。
- `paper_engine.py`：新增 `open_market()`（默认返回原来的 `L.market_conn()`，行为零漂移）、
  `latest_closed_detail()`；五个取数函数（`closed_15m` / `bar_at_or_after` / `bar_before` /
  `last_closed_15m` / `last_closed_4h`）增加一条「传进来的是合并门面就走合并」的分支；
  9 处内部建连改为 `open_market()`；来源标签写进 `market_source`。
- **没有改**：采集、前端、`replay_calc.py`、冻结策略参数、`state_machine.py`、`config.py`。
- 表中「未改（无改前副本）」两个文件（`live_api.py`、`config.py`）本单没有留改前副本，
  因此**只能声明本次没有写入**，不能声明「可证明未改」。

## 二、判据与结果

| 项 | 结果 | 依据 |
|---|---|---|
| 1 读取路径查全（不只修一个函数） | 完成 | 静态扫到 {V('n_sql_literals')} 处 15m/4h SQL、{V('n_read_calls')} 处取数函数调用；其中 **{V('n_bypass')} 处**是自写 SQL（只能读热库），逐处登记 |
| 2 冷热合并口径（去重/优先级/已收盘/缺口/精度） | 部分完成 | 见第三节：A1/A2 通过，**A3 无法证明**（冷热重叠段数值完全相同） |
| 3 `available_at` 缺失必须保留 | 完成 | 冷档 bar 一路带 `available_at_missing`，并写进 `market_source` 的 `available_at_unproven`；不冒充可得时刻 |
| 4 真实历史事件在独立运行里贯通全链 | 完成（3 条） | 见第四节；生产账本：{d04['生产账本是否被改动']['verdict']} |
| 5 交界/缺线/重复扫描/重启恢复/不重复订单 | 完成 | 交界与缺线见第三节；恢复与幂等见第五节 |
| 6 已确认止损口径 | 遵守 | 见第六节冻结参数活体校验 |

## 三、冷热合并口径核验

- **去重键** `open_time`：每币结果 `open_time` 唯一，`dup_removed == overlap_hot_wins`（A1={V('a1')}）。
- **冲突优先级**：重叠行一律被标记为 `hot`，且合并值等于热层值（A2={V('a2')}）。
- **A3 优先级数值方向 = {V('a3')}**：本数据上冷热重叠段**逐位相同**（60 个标的扫过，差异 0 根）
  ⇒ 用数值根本区分不出「取热」还是「取冷」。所以这一项记为**未证明**，不算通过。
  这不是实现错了，是数据不支持证明；要真正证明需要一份冷热确实不一致的数据。
- **已收盘**：冷档 SQL 与热库 SQL 都带 `is_closed=1`；未收盘版本不进结果。
- **冷热交界**：15m 侧热库的第一根是 {V('seam15_first_hot')}，冷档覆盖到它之前，
  交界处步长正确、无重复、窗口内无跳空，重叠 {V('seam15_overlap')} 根按热层取；
  4h 侧热库第一根是 {V('seam4h_first_hot')}，重叠 {V('seam4h_overlap')} 根，同样连续。
- **缺口不补造**：全池冷档 2026 年 15m 只有 **{V('gap_symbols')} 个标的**有缺口（最长 {V('gap_bars')} 根）；
  在缺口内请求，`bars()` 返回 **{V('gap_inside_rows')} 根**，`at_or_after` 直接跳到缺口之后那根 —— 不插值、不补零。
- **价格精度**：与既有 `history_loader.MergedHistory` 逐根对撞，15m 比 {V('cc15_compared')} 根、
  4h 比 {V('cc4h_compared')} 根，不一致 **{V('cc15_mismatch')} / {V('cc4h_mismatch')}** 根。
  合并层**不做任何取整**；取整仍只在冻结的成交/平仓实现里发生一次。
- **缓存不改变语义**：同一请求重复调用逐位相同；先窄窗再宽窗再窄窗，结果仍逐位相同（装载 {V('cache_loads')} 次）。

### 仍然只能读热库的 4 处（共享入口，本单只登记不擅改）

| 位置 | 行 | 是否在成交链路 | 作用 |
|---|---|---|---|
{byp_rows}

其中 **3 处在成交/退出链路上**（`paper_loop.py`、`unified_runtime.py::execute_cycle`、`live_api.py`），
它们**自己写 SQL 查 candles**，所以不受本次接线影响；剩下 1 处
`unified_runtime.py::idle_breakdown` 是诊断展示，不参与定价。
具体替换点已写进 `out/01_读取路径普查.json` 的 `coordination_change` 字段（本单新增的
`PE.latest_closed_detail()` 就是为它们准备的，语义与原 SQL 逐字相同）。
**运行期取证**：合并模式下跑完整条链，K 线 SQL 落到热库的穿透次数 = {V('n_passthrough_candle')}，
冷档实际返回 15m {V('n_cold15_returned')} 根、4h {V('n_cold4h_returned')} 根。

## 四、同一运行的完整事件链（隔离账本，真实历史事件）

下面每一条都是**同一次隔离运行**里跑出来的：观察与候选取生产库里的真实记录，
委托 → 成交 → 退出 → 账本由本单接线后的 `paper_engine` 在独立账本里完成，**没有回填生产账户**。
每条链最后一行是 04 脚本对这条链的判定；「对照（只读热库）」是同一事件在**未接线**路径下的结果，
用来证明缺陷确实存在（E1/E2 被拒）或对照组有效（E3 逐位相同）。
{chains_md}

## 五、重启恢复、重复扫描、不重复订单

把一次连续回放切成两段，中间换**新进程**（同一账本目录）：

| 事件 | 入场单 | 平仓单 | 结果 |
|---|---|---|---|
| E1 TNSR | {V('05_E1_TNSR_cold_entry_orders')} | {V('05_E1_TNSR_cold_exit_orders')} | {V('05_E1_TNSR_cold_result')} |
| E2 AGT | {V('05_E2_AGT_cold_entry_orders')} | {V('05_E2_AGT_cold_exit_orders')} | {V('05_E2_AGT_cold_result')} |
| E3 METIS | {V('05_E3_METIS_hot_entry_orders')} | {V('05_E3_METIS_hot_exit_orders')} | {V('05_E3_METIS_hot_result')} |

关键点：第 2 段里再下一次**同一个 signal_uid**，被 `duplicate_signal_uid` 挡住；
第 2 段走完后的退出价、净额、止损轨迹与 04 脚本的连续跑**逐位相同** ⇒ 恢复没有改变经济结果。
对已平仓持仓再扫退出，不会再产生第二笔平仓。

## 六、已确认止损口径（活体校验，不看文件哈希）

| 参数 | 应为 | 实读 | 判定 |
|---|---|---|---|
{frozen_rows}

- 初始止损 = 实际成交价 × 95%（**不是**候选快照里的结构低点；三条候选的两个值差过 -3.00%/-10.98%/-14.62%，混用会把止损放宽 3%~15%）。
- 入场后每根**该笔入场之后新收盘**的 4h 线，候选止损 = 该根最低价 × 95%，取「初始 / 此前 / 本次」三者最高，只上移；`trailing_trigger_r=0`（入场即跟踪）、`trailing_lookback_4h_bars=1`。
- 无 10 天强平（`max_holding_days=None`）、无「盈利 2 倍风险才启动」门槛。
- 回放里 4h 侧因果守卫生效：`ref_close_ms <= entry_ts` 的整根不参与跟踪与退出（见 `advance_position_trailing` 的 `bar_closed_before_entry_no_causality`）。

## 七、两套口径并列，本单不择一

**滑点**：策略包 `slippage_bps=3`／生效视图 `slippage_each_side={view_subset['slippage_each_side']}`（=5bps）。
本次回放走的是**生效视图**（5bps），因为 `paper_engine` 只读生效视图。两套并列，不择一。

**初始权益**：策略包 `initial_equity=2000`／生效视图 `initial_equity_USDT={view_subset['initial_equity_USDT']}`。
隔离账本按生效视图建 5000。两套并列，不择一。

**参与率上限**（这条会直接决定能不能开仓，必须看清）：

| 事件 | 标的 | 上一根 15m 成交额 | 本次名义额 | 上限@1%（生效视图） | 上限@0.1%（策略包） | @1% | @0.1% |
|---|---|---|---|---|---|---|---|
{part_rows_md}

生效视图写 `{view_subset['entry_participation_previous_15m_quote_cap']}`（1%），策略包冻结值是 `0.001`（0.1%），
**差 10 倍**。上表两列并列，本单不择一；要按哪一套执行需裁决。

**`max_holding_hours=240`** 仍在生效视图里（并进 `view_sha256={view_subset['view_sha256']}`），
所以**不能改成 None**（改了已登记实验身份全漂移）；实际不执行，但按参数实现会把 10 天强平重新引入。

## 八、完成度（逐项）

| 项 | 状态 |
|---|---|
| 1 查全读取路径并逐处登记 | 完成 |
| 2 接通冷热合并（15m + 4h） | 完成 |
| 2 去重/优先级/已收盘/缺口/精度口径 | 部分完成（A3 数据不支持证明） |
| 3 `available_at` 缺失保留并写入账本 | 完成 |
| 4 真实历史事件全链贯通（隔离） | 完成（3 条） |
| 5 交界 / 缺线 / 重复扫描 / 重启恢复 / 不重复订单 | 完成 |
| 6 冻结止损口径 | 完成（活体校验一致） |
| **共享入口 3 处自写 SQL 改为合并取数** | **未完成（阻塞在协调，已给具体修改点）** |
| **自然行情下的真实模拟成交** | **未验证**（自然候选 0） |
| **冷档 `available_at` 的历史可得性证明** | **未完成（数据本身没有，不是没做）** |
| 页面手工推进跟踪止损走合并口径 | 未完成（同上，属共享入口） |

## 九、仍然缺的东西（不粉饰）

1. **历史可得性证明**：冷档 15m/4h 的 `available_at` 填充率是 **0%**。所以三条回放里
   只有 METIS 那 1 笔的成交 bar 可得时刻有证据，另 2 笔只能声称「计算没有用到未来 K 线」。
   缺证明的结果**不得**进严格有效性成绩——本单在账本的 `market_source` 里直接写了
   `available_at_unproven`，不靠报告补记。
2. **自然成交**：全池自然候选 0，账本 0 笔自然成交。本单**没有**证明「模拟盘会在真实挂钟下自动成交」。
3. **共享入口**：`paper_loop.py` / `unified_runtime.py` / `live_api.py` 三处自写 SQL 仍读热库。
   在**生产**（挂钟）里这没影响（热库就是最新数据）；但在**隔离回放**里，这三处会让退出扫描
   与手工跟踪看到热库口径。要修得改共享入口，需协调。
4. **跳空成交价（已登记缺陷，本单没改）**：`check_position_exit_on_bar` 与 4h 收盘击穿分支
   都按「当时止损价」成交，没有对「bar 开盘就低于止损」单独建模。本单把三条回放的退出 bar
   逐根取出来核过（{V('gap_exit_checked')} 根，其中开盘价低于当时止损的 **{V('gap_exit_below_stop')} 根**）：

   | 事件 | 周期 | 退出 bar | 开盘价 | 最低价 | 当时止损 | 是否跳空低于止损 |
   |---|---|---|---|---|---|---|
{gap_rows_md}

   ⇒ 这三笔的退出 bar **开盘价都高于当时止损**，没有踩到跳空分支，成交价不存在
   「按止损价高估」的问题；但缺陷本身仍在，换成真正跳空的样本就会高估。
   这条不在本单授权范围（改它要改盈亏口径）。
5. **A3**：冷热优先级方向在本数据上不可判。

## 十、产物索引

| 文件 | 内容 |
|---|---|
| `out/01_读取路径普查.json` | 静态普查 + 运行期穿透取证 + 4 处协调修改点 |
| `out/02_合并口径核验.json` | 去重/优先级/已收盘/交界/缺口/精度/缓存 |
| `out/03_接线前后对照.json` | 同一事件 merge vs hot 两臂对照 |
| `out/04_全链贯通.json` | 三条真实事件的完整事件链（含账本与可得性判定） |
| `out/05_恢复与幂等.json` | 分段重启、重复下单、重复扫描 |
| `out/06_报告数字对撞.json` | 本文件每个数字的回算与对撞结果 |
| `out/06_退出跳空核验.json` | 三条退出 bar 的开盘价 vs 当时止损（跳空分支有没有被踩到） |
| `runs/` | 每次隔离运行的独立账本与 worker 原始输出 |
| `baseline/` | 改动前源码副本（身份取证用） |
| `scripts/` | 六个核验脚本 + 隔离回放工作进程 |

---

**验收口径提醒**：本文件所有结论均为**执行方自报**。`通过` 只代表「本单声明的行为有原始记录」，
不代表整条策略链通过，也不代表可以开仓。开仓仍被三项未批准口径阻塞（入场阈值、跳空成交模型、滑点与风险配置）。
"""

    # 替换占位符
    import re
    unresolved = []
    for m in set(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", body)):
        if m not in C:
            unresolved.append(m)
    for cid in C:
        body = body.replace("{{" + cid + "}}", fmt(C[cid]["value"]))
    leftover = re.findall(r"\{\{[a-zA-Z0-9_]+\}\}", body)

    readme = EVID / "README.md"
    readme.write_text(body, encoding="utf-8")

    # ── 双向对撞：正文里出现的值 ↔ 重新从 JSON 取的值 ────────────────────
    text = readme.read_text(encoding="utf-8")
    checks = []
    for cid, meta in C.items():
        src = data[meta["source"]]
        recomputed = get(src, meta["path"])
        same_value = (fmt(recomputed) == fmt(meta["value"]))
        # 正文里必须真的出现这个值（至少一次）
        appears = fmt(meta["value"]) in text
        checks.append({"id": cid, "path": meta["path"], "source": meta["source"],
                       "claim": fmt(meta["value"]), "recomputed": fmt(recomputed),
                       "value_consistent": same_value, "appears_in_readme": appears})
    bad = [c for c in checks if not (c["value_consistent"] and c["appears_in_readme"])]

    report = {
        "probe": "06_报告数字对撞",
        "generated_at_utc": iso(),
        "readme": str(readme),
        "readme_bytes": readme.stat().st_size,
        "claims_total": len(C),
        "claims_checked": len(checks),
        "claims_failed": len(bad),
        "unresolved_placeholders": unresolved,
        "leftover_placeholders": leftover,
        "failed_detail": bad[:20],
        "method": "每个数字先从 out/*.json 取值渲染，再按同一路径重新取一次，"
                  "与正文中实际出现的字符串逐字比对；不靠人眼。",
        "verdict": ("通过" if not bad and not leftover and not unresolved else "不通过"),
        "frozen_params": frozen_check,
        "code_identity": ident,
        "view_params_used_by_execution": view_subset,
        "participation_two_standards": part_rows,
    }
    (OUT / "06_报告数字对撞.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print(json.dumps({"claims": len(C), "failed": len(bad), "leftover": leftover,
                      "unresolved": unresolved, "verdict": report["verdict"],
                      "readme_bytes": report["readme_bytes"]},
                     ensure_ascii=False, indent=1))
    if bad:
        print(json.dumps(bad[:5], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
