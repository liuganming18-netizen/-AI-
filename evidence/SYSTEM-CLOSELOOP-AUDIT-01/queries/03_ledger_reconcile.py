#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03_ledger_reconcile.py —— 冻结清单 L1–L8 账本／数据源对账（对**冻结快照**复算）

为什么对快照而不对活库
----------------------
实测：审核期间生产账本正被并发写入（`paper_3a.sqlite3` mtime 19:31:27，
3b/3c/3d 仍停在 10-18；闸门 19:30:03 被翻成 allow_new_entries=true；
生产 live 库出现 3 条 `sig_closeloop_btc_*` 合成信号）。
直接读活库是移动靶 ⇒ 一律对 `out/snapshots/S1/` 复算，并另取 S2 用**哈希变化本身**
证明并发写入。

用法：python 03_ledger_reconcile.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJ = ROOT.parent.parent
OUT = ROOT / "out"
SNAP = OUT / "snapshots" / "S1"

RESULTS: list[dict] = []
R6 = lambda x: None if x is None else round(float(x), 8)          # noqa: E731


def rec(cid, title, verdict, judged, measured, extra=None):
    RESULTS.append({"id": cid, "title": title, "verdict": verdict,
                    "判据": judged, "实测": measured, "补充": extra or {}})
    print(f"[{verdict}] {cid} {title}")
    if verdict != "通过":
        print(f"        判据：{judged}")
        print(f"        实测：{json.dumps(measured, ensure_ascii=False)[:600]}")


def conn(p: Path):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def iso(ms):
    if ms is None:
        return None
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000).isoformat(timespec="seconds")
    except (ValueError, OSError):
        return str(ms)


# ── L1 账户闭合 ───────────────────────────────────────────────────────────
def l1():
    per = []
    for arm in ("3a", "3b", "3c", "3d"):
        p = SNAP / f"paper_{arm}.sqlite3"
        if not p.exists():
            per.append({"arm": arm, "存在": False})
            continue
        c = conn(p)
        try:
            st = rows(c, "SELECT * FROM paper_state")
            eqs = rows(c, "SELECT * FROM paper_equity ORDER BY ts")
            re_ = rows(c, "SELECT * FROM paper_realized")
            acc = rows(c, "SELECT * FROM paper_account")
        finally:
            c.close()
        if not st:
            per.append({"arm": arm, "存在": True, "paper_state": "空"})
            continue
        s = st[0]
        cash = float(s["cash"])
        realized = float(s["realized_pnl"])
        fees = float(s["fees_paid"])
        funding = float(s["funding_paid"])
        sum_realized = sum(float(x["net_pnl"]) for x in re_)
        last_eq = float(eqs[-1]["equity"]) if eqs else None
        # 闭合：initial + realized == cash；末条 equity == cash（无未平仓时）
        d1 = R6(5000.0 + realized - cash)
        d2 = R6(realized - sum_realized)
        d3 = None if last_eq is None else R6(last_eq - cash)
        per.append({
            "arm": arm, "account_id": s["account_id"],
            "initial_equity": 5000.0, "cash": R6(cash),
            "realized_pnl": R6(realized), "fees_paid": R6(fees),
            "funding_paid": R6(funding),
            "realized行数": len(re_), "realized求和": R6(sum_realized),
            "equity行数": len(eqs),
            "末条equity": None if last_eq is None else R6(last_eq),
            "差额(5000+已实现-cash)": d1,
            "差额(已实现-明细求和)": d2,
            "差额(末条equity-cash)": d3,
            "paper_account行数": len(acc),
        })
    live = [x for x in per if x.get("paper_state") != "空" and x.get("cash") is not None]
    ok = bool(live) and all(
        x["差额(5000+已实现-cash)"] == 0 and x["差额(已实现-明细求和)"] == 0
        and x["差额(末条equity-cash)"] in (0, None) for x in live)
    rec("L1", "纸面账户闭合（逐臂可加总）", "通过" if ok else "不通过",
        "initial(5000) + 已实现 == cash；已实现 == paper_realized 求和；末条 equity == cash（无未平仓）",
        {"per_arm": per},
        {"说明": "paper_account 表 4 臂全为 0 行 ⇒ 初始本金只能取冻结常量 5000；"
                 "账本没有 per-arm 的初始权益字段，跨臂加总只能靠常量"})

    # 跨臂加总
    tot_cash = sum(x["cash"] for x in live)
    tot_real = sum(x["realized_pnl"] for x in live)
    rec("L1b", "四臂跨账户加总", "通过" if R6(4 * 5000.0 + tot_real - tot_cash) == 0 else "不通过",
        "4 × 5000 + Σ已实现 == Σcash",
        {"Σcash": R6(tot_cash), "Σ已实现": R6(tot_real),
         "差额": R6(4 * 5000.0 + tot_real - tot_cash),
         "仅 3a 有交易": [x["arm"] for x in live if x["realized行数"] > 0]})


# ── L2 手续费/资金费各计一次 ─────────────────────────────────────────────
def l2():
    p = SNAP / "paper_3a.sqlite3"
    c = conn(p)
    try:
        re_ = rows(c, "SELECT * FROM paper_realized ORDER BY id")
        fills = rows(c, "SELECT * FROM paper_fill ORDER BY fill_id")
        st = rows(c, "SELECT * FROM paper_state")[0]
        fund = rows(c, "SELECT * FROM paper_funding")
    finally:
        c.close()
    per = []
    for x in re_:
        calc = (float(x["gross_pnl"]) - float(x["entry_fee"]) - float(x["exit_fee"])
                + float(x["funding_paid"]))
        gross_calc = (float(x["exit_price"]) - float(x["entry_price"])) * float(x["qty"])
        per.append({
            "id": x["id"], "position_id": x["position_id"],
            "gross_pnl账本": R6(x["gross_pnl"]), "gross_pnl重算": R6(gross_calc),
            "gross差": R6(float(x["gross_pnl"]) - gross_calc),
            "net_pnl账本": R6(x["net_pnl"]), "net_pnl重算": R6(calc),
            "net差": R6(float(x["net_pnl"]) - calc),
            "entry_fee": R6(x["entry_fee"]), "exit_fee": R6(x["exit_fee"]),
            "funding_paid": R6(x["funding_paid"]),
        })
    fee_fills = sum(float(f["fee"]) for f in fills)
    ok = all(x["gross差"] == 0 and x["net差"] == 0 for x in per) \
        and R6(fee_fills) == R6(st["fees_paid"]) and len(fund) == 0
    rec("L2", "手续费/资金费各计一次", "通过" if ok else "不通过",
        "gross == (平-开)×数量；net == gross − 开费 − 平费 + 资金费；"
        "fees_paid == Σ每笔成交 fee（不重复计）",
        {"per_realized": per,
         "Σ成交fee": R6(fee_fills), "账本fees_paid": R6(st["fees_paid"]),
         "差额": R6(fee_fills - float(st["fees_paid"])),
         "paper_funding行数": len(fund),
         "说明": "资金费表为空 ⇒ funding_paid 全 0，本轮无资金费可验；"
                 "「各计一次」只对已发生的手续费成立"})


# ── L3 标识贯通 ───────────────────────────────────────────────────────────
def l3():
    p = SNAP / "paper_3a.sqlite3"
    c = conn(p)
    try:
        pos = rows(c, "SELECT * FROM paper_position ORDER BY position_id")
        orders = rows(c, "SELECT * FROM paper_order ORDER BY order_id")
        fills = rows(c, "SELECT * FROM paper_fill ORDER BY fill_id")
        re_ = rows(c, "SELECT * FROM paper_realized ORDER BY id")
        proc = rows(c, "SELECT * FROM paper_processed")
        anchor = rows(c, "SELECT * FROM paper_request_anchor")
        pend = rows(c, "SELECT * FROM paper_pending")
    finally:
        c.close()
    o_by_uid = {}
    for o in orders:
        o_by_uid.setdefault(o["event_uid"], []).append(o["order_id"])
    f_open_by_pos = {}
    f_close_by_pos = {}
    for f in fills:
        if f["action"] == "open":
            f_open_by_pos.setdefault(f["position_id"], []).append(f["fill_id"])
        else:
            f_close_by_pos.setdefault(f["position_id"], []).append(f["fill_id"])
    re_by_pos = {}
    for r in re_:
        re_by_pos.setdefault(r["position_id"], []).append(r["id"])
    proc_by_uid = {x["signal_uid"]: x for x in proc}
    anchor_by_uid = {x["event_uid"]: x for x in anchor}

    chain = []
    for ps in pos:
        uid = ps["signal_uid"]
        chain.append({
            "position_id": ps["position_id"], "signal_uid": uid,
            "status": ps["status"],
            "order_ids(按event_uid)": o_by_uid.get(uid, []),
            "fill_open": f_open_by_pos.get(ps["position_id"], []),
            "fill_close": f_close_by_pos.get(ps["position_id"], []),
            "realized_ids": re_by_pos.get(ps["position_id"], []),
            "processed": bool(proc_by_uid.get(uid)),
            "anchor": bool(anchor_by_uid.get(uid)),
        })
    broken = []
    for ch in chain:
        if not ch["order_ids(按event_uid)"]:
            broken.append({**ch, "断点": "order 无对应 event_uid"})
        if ch["status"] == "closed" and not ch["realized_ids"]:
            broken.append({**ch, "断点": "已平仓但无 realized 行"})
        if not ch["fill_open"]:
            broken.append({**ch, "断点": "无开仓成交"})
    # 反向：order/fill 的 uid 必须能在 position 找到
    pos_uids = {x["signal_uid"] for x in pos}
    orphan_orders = [o["order_id"] for o in orders if o["event_uid"] not in pos_uids]
    orphan_fills = [f["fill_id"] for f in fills
                    if f["signal_uid"] not in pos_uids]
    ok = not broken and not orphan_orders and not orphan_fills
    rec("L3", "标识贯通 signal_uid → order → fill → position → realized", "通过" if ok else "不通过",
        "每笔持仓的 signal_uid 能在 order.event_uid / fill.signal_uid / processed / anchor 查到；"
        "反向无孤儿订单或成交",
        {"链路": chain, "断点数": len(broken), "断点": broken,
         "孤儿订单": orphan_orders, "孤儿成交": orphan_fills,
         "pending行数": len(pend),
         "pending状态": [{"pending_id": x["pending_id"], "status": x["status"],
                          "signal_uid": x["signal_uid"]} for x in pend]})


# ── L4 初始止损与跟踪上移 ────────────────────────────────────────────────
def l4():
    p = SNAP / "paper_3a.sqlite3"
    c = conn(p)
    try:
        pos = rows(c, "SELECT * FROM paper_position ORDER BY position_id")
        tabs = [r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        c.close()
    stop_tabs = [t for t in tabs if "stop" in t.lower() or "trail" in t.lower()]
    detail = []
    for x in pos:
        entry = float(x["entry_price"])
        atr = x["atr_at_entry"]
        stop = x["stop_price"]
        detail.append({
            "position_id": x["position_id"],
            "entry_price": R6(entry), "atr": atr, "stop_price": R6(stop),
            "止损距离": None if stop is None else R6(entry - float(stop)),
            "止损距离/ATR": None if (stop is None or not atr) else round((entry - float(stop)) / float(atr), 4),
            "exit_price": R6(x["exit_price"]), "exit_reason": x["exit_reason"],
            "exit_ts": iso(x["exit_ts"]),
        })
    rec("L4", "初始止损与跟踪上移可重建", "未验证",
        "止损价可从 15m/4h K 线因果重建，且跟踪上移有轨迹可查；生效时间不早于参考 K 线收盘",
        {"止损/轨迹相关表": stop_tabs or "无",
         "账本可用止损字段": ["paper_position.stop_price（单值）"],
         "逐笔": detail},
        {"说明": "账本**没有任何止损变更轨迹表**（无 stop_history / 无 events 表），"
                 "paper_position.stop_price 只有一个终值 ⇒ 「跟踪上移」在本账本上"
                 "**无法证明也无法否证**，只能判未验证。这是账本结构缺口，不是本轮测试不足。"})


# ── L5 撮合因果（成交时钟可被请求体改写）──────────────────────────────
def l5():
    p = SNAP / "paper_3a.sqlite3"
    c = conn(p)
    try:
        pos = rows(c, "SELECT * FROM paper_position ORDER BY position_id")
        fills = rows(c, "SELECT * FROM paper_fill ORDER BY fill_id")
        anchor = rows(c, "SELECT * FROM paper_request_anchor")
    finally:
        c.close()
    anc = {x["event_uid"]: x for x in anchor}
    f_open = {f["position_id"]: f for f in fills if f["action"] == "open"}
    detail = []
    for x in pos:
        uid = x["signal_uid"]
        a = anc.get(uid) or {}
        f = f_open.get(x["position_id"]) or {}
        declared_first = a.get("first_request_ms")
        real_write = a.get("created_at")
        delta = (None if (declared_first is None or real_write is None)
                 else round((int(real_write) - int(declared_first)) / 60000.0, 2))
        detail.append({
            "position_id": x["position_id"], "signal_uid": uid,
            "声明首次请求 first_request_ms": declared_first,
            "声明时刻(本地)": iso(declared_first),
            "**真实写入挂钟** anchor.created_at": real_write,
            "真实写入(本地)": iso(real_write),
            "差值_分钟(真实写入−声明)": delta,
            "成交bar_open_time": f.get("bar_open_time"),
            "成交bar(本地)": iso(f.get("bar_open_time")),
            "成交fill_ts": f.get("fill_ts"),
            "成交fill_ts(本地)": iso(f.get("fill_ts")),
            "bar是否晚于真实写入": (None if (f.get("bar_open_time") is None or real_write is None)
                                   else int(f["bar_open_time"]) > int(real_write)),
        })
    bad = [d for d in detail if d["差值_分钟(真实写入−声明)"] is not None
           and d["差值_分钟(真实写入−声明)"] > 1]
    ok = not bad
    rec("L5", "撮合因果：成交不得早于真实请求时刻", "通过" if ok else "不通过",
        "成交 bar 的开盘时刻必须 ≥ 真实请求挂钟（不是调用方声明的 requested_at）",
        {"逐笔": detail,
         "回填笔数(真实写入−声明 > 1 分钟)": len(bad),
         "最大回填_分钟": max([d["差值_分钟(真实写入−声明)"] for d in detail
                            if d["差值_分钟(真实写入−声明)"] is not None] or [None])},
        {"说明": "账本自身就带着矛盾：`paper_request_anchor.created_at` 是引擎写入的真实挂钟，"
                 "`first_request_ms` 是调用方声明的经济时刻。三笔全部「声明的请求时刻」"
                 "比「真实写入时刻」早 14~29 分钟，成交 bar 也落在真实写入之前 ⇒ "
                 "**成交价来自过去的 K 线**。根因是写入口允许请求体注入 requested_at/now_ms（见 L5b）"})

    # L5b：根因——写入口是否接受调用方注入时钟
    la = (PROJ / "框架" / "backend" / "live_api.py").read_text(encoding="utf-8")
    inject_req = 'int(payload.get("requested_at") or L.now_ms())' in la
    inject_now = 'int(payload.get("now_ms") or L.now_ms())' in la
    pe = (PROJ / "框架" / "backend" / "paper_engine.py").read_text(encoding="utf-8")
    lag = None
    for line in pe.split("\n"):
        if line.startswith("MAX_FILL_LAG_MS"):
            lag = line.strip()
    rec("L5b", "根因：写入口是否允许调用方注入成交时钟", "不通过" if (inject_req or inject_now) else "通过",
        "成交时钟必须由服务端真实挂钟决定，不得由请求体指定",
        {"接受 payload.requested_at": inject_req,
         "接受 payload.now_ms": inject_now,
         "迟滞容忍常量": lag,
         "后果": "注入 now_ms 后 lag = now_clk − bar_open 恒为小值 ⇒ 迟滞保护被绕过；"
                 "注入 requested_at 后 earliest_fill_ms 也由调用方决定"})


# ── L6 期末持仓 ───────────────────────────────────────────────────────────
def l6():
    p = SNAP / "paper_3a.sqlite3"
    c = conn(p)
    try:
        pos = rows(c, "SELECT * FROM paper_position")
        pend = rows(c, "SELECT * FROM paper_pending")
    finally:
        c.close()
    open_pos = [x for x in pos if x["status"] == "open" and not x["voided"]]
    closed = [x for x in pos if x["status"] == "closed"]
    forced = [x for x in closed if "force" in str(x["exit_reason"]).lower()]
    api = json.loads((OUT / "api" / "paper_positions.json").read_text(encoding="utf-8"))
    api_n = len(api.get("items") or [])
    ok = len(open_pos) == api_n == 0 and len(forced) == 0
    rec("L6", "期末持仓与账本一致，未靠强制结算伪装已实现", "通过" if ok else "不通过",
        "账本 open 仓位 == 接口持仓条数；无 force_exit 伪装；挂单状态如实",
        {"账本open": len(open_pos), "账本closed": len(closed),
         "强制结算笔数": len(forced),
         "接口持仓条数": api_n,
         "挂单": [{"pending_id": x["pending_id"], "status": x["status"],
                   "earliest_fill(本地)": iso(x["earliest_fill_ms"]),
                   "signal_uid": x["signal_uid"]} for x in pend],
         "exit_reason分布": {r: sum(1 for x in pos if x["exit_reason"] == r)
                          for r in {x["exit_reason"] for x in pos}}},
        {"说明": "3 笔挂单仍停在 pending（earliest_fill 在采集时刻之后），"
                 "说明挂单推进要靠真实进程时钟；本轮未观察到推进，故「挂单→成交」未验证"})


# ── L7 回测账本对账（抽一个 run）────────────────────────────────────────
def l7():
    """runs 根目录以接口返回的 `runs_root` 为准（不是猜路径）。"""
    runs_root = None
    try:
        b = json.loads((OUT / "api" / "backtests_all.json").read_text(encoding="utf-8"))
        runs_root = Path(b["runs_root"])
    except Exception:                                         # noqa: BLE001
        runs_root = PROJ / "回测日志" / "event_trend_v0_1" / "runs"
    picked = []
    if runs_root.exists():
        for d in sorted(runs_root.glob("run_*")):
            if d.is_dir() and (d / "summary.json").exists() and (d / "trades.jsonl").exists():
                picked.append(d)
            if len(picked) >= 40:
                break
    if not picked:
        return rec("L7", "回测账本 run 目录可对账", "未验证",
                   "找到至少一个含 summary.json + trades.jsonl 的 run",
                   {"runs_root": str(runs_root), "exists": runs_root.exists()})

    # 按接口列表里的 research_period 标记，各取 2 个（summary 结构不同，必须都覆盖）
    res, eng = [], []
    try:
        b = json.loads((OUT / "api" / "backtests_all.json").read_text(encoding="utf-8"))
        for it in (b.get("items") or []):
            d = runs_root / str(it.get("run_id"))
            if not (d.is_dir() and (d / "summary.json").exists() and (d / "trades.jsonl").exists()):
                continue
            if it.get("research_period") and len(res) < 2:
                res.append(d)
            elif (not it.get("research_period")) and len(eng) < 2:
                eng.append(d)
            if len(res) >= 2 and len(eng) >= 2:
                break
    except Exception:                                         # noqa: BLE001
        pass
    chosen = res + eng
    if not chosen:
        return rec("L7", "回测账本 run 目录可对账", "未验证",
                   "接口列表里的 run 能在 runs_root 找到 summary+trades",
                   {"runs_root": str(runs_root), "候选数": len(picked)})
    cases = []
    all_ok = True
    for cand in chosen:
        try:
            summ = json.loads((cand / "summary.json").read_text(encoding="utf-8"))
            trades = [json.loads(x) for x in
                      (cand / "trades.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception as e:                                # noqa: BLE001
            cases.append({"run": cand.name, "读取失败": f"{type(e).__name__}: {e}"})
            all_ok = False
            continue
        pnl = [float(t.get("profit_abs") or t.get("net_pnl") or 0) for t in trades]
        s_sum = R6(sum(pnl))
        sv = summ.get("summary_variant_a") if isinstance(summ.get("summary_variant_a"), dict) else None
        if sv:                                                # 研究期形状
            tp = sv.get("net_pnl")
            nt = sv.get("trade_count")
            shape = "研究期(summary_variant_a)"
        else:                                                 # 引擎期形状
            tp = summ.get("total_profit", summ.get("profit_total_abs"))
            nt = summ.get("total_trades", summ.get("trades_count"))
            shape = "引擎期"
        d = None if tp is None else R6(float(tp) - s_sum)
        # 0 笔 run 本身合法（net_pnl 0 == Σ0），不能算失败；只要求能对上账
        ok = (tp is not None and d is not None and abs(d) <= max(0.02, abs(s_sum) * 1e-6)
              and (nt is None or int(nt) == len(trades)))
        all_ok = all_ok and ok
        cases.append({"run": cand.name, "形状": shape,
                      "逐笔笔数": len(trades), "summary笔数": nt,
                      "Σ逐笔profit_abs": s_sum, "summary金额": tp,
                      "差额": d, "本run通过": ok,
                      "空run": len(trades) == 0,
                      "summary顶层键": sorted(summ.keys())[:12]})
    n_nonempty = sum(1 for c in cases if not c.get("空run"))
    all_ok = all_ok and n_nonempty >= 2
    rec("L7", "回测账本 run 目录/summary/逐笔可对账", "通过" if all_ok else "不通过",
        "summary 的金额 == Σ逐笔 profit_abs（容差 0.02 或 1e-6 相对）；笔数 == 逐笔行数。"
        "研究期与引擎期两种 summary 形状都要覆盖，且至少 2 个 run 非空",
        {"runs_root": str(runs_root), "抽检": cases,
         "非空run数": n_nonempty, "空run数": len(cases) - n_nonempty},
        {"说明": "只抽 4 个 run（2 研究期 + 2 引擎期）做机械对账，不代表 2,263 个 run 全部对过；"
                 "引擎期 run 抽到的都是 0 笔（页面列表里它们也是 0 笔），"
                 "「0 笔」不是对账失败，但也不能当成「链路已验证」"})


# ── L8 研究/生产数据未被污染 ────────────────────────────────────────────
def l8():
    s1 = json.loads((SNAP / "_snapshot.json").read_text(encoding="utf-8"))
    s2p = OUT / "snapshots" / "S2" / "_snapshot.json"
    s2 = json.loads(s2p.read_text(encoding="utf-8")) if s2p.exists() else None
    diff = []
    if s2:
        a = {x["源文件"]: x for x in s1["文件"] if x.get("存在")}
        b = {x["源文件"]: x for x in s2["文件"] if x.get("存在")}
        for k in sorted(set(a) | set(b)):
            ha = (a.get(k) or {}).get("源SHA256")
            hb = (b.get(k) or {}).get("源SHA256")
            if ha != hb:
                diff.append({"文件": k, "S1": (ha or "缺失")[:16], "S2": (hb or "缺失")[:16],
                             "S1行数": (a.get(k) or {}).get("行数"),
                             "S2行数": (b.get(k) or {}).get("行数")})
    live = PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3"
    c = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        syn = [dict(zip(["signal_uid", "symbol", "strategy_version"], r))
               for r in c.execute(
                   "SELECT signal_uid, symbol, strategy_version FROM live_signal")]
    finally:
        c.close()
    synth = [x for x in syn if str(x["signal_uid"]).startswith(("sig_closeloop", "sig_ui_test",
                                                               "sig_test_closeloop"))]
    gate = json.loads((SNAP / "paper_new_entry_gate.json").read_text(encoding="utf-8"))
    ok = (not diff) and (not synth) and (gate.get("allow_new_entries") is False)
    rec("L8", "测试数据隔离 / 生产账本未被污染", "通过" if ok else "不通过",
        "审核前后账本哈希不变；生产 live 库无合成测试信号；总闸不得在验收未通过时留在开启态",
        {"快照S1": s1["快照时刻"], "快照S2": (s2 or {}).get("快照时刻"),
         "哈希变化文件": diff,
         "生产live库总行数": len(syn),
         "合成测试信号": synth,
         "闸门": gate},
        {"说明": "`sig_closeloop_btc_*` / `sig_ui_test_*` / `sig_test_closeloop_01` 是"
                 "闭环验证脚本写进**生产**实时库的合成信号；闸门被留在 "
                 "allow_new_entries=true（set_by=user_ui_control, 19:30:03）。"
                 "两者都不属于「测试数据隔离」"})


def main():
    for fn in (l1, l2, l3, l4, l5, l6, l7, l8):
        try:
            fn()
        except Exception as e:                                # noqa: BLE001
            rec(fn.__name__.upper(), fn.__name__, "未验证", "脚本异常",
                f"{type(e).__name__}: {e}")
    cnt = {}
    for r in RESULTS:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    doc = {"生成时刻": dt.datetime.now().isoformat(timespec="seconds"),
           "对账对象": "out/snapshots/S1（冻结快照）",
           "计数": cnt, "明细": RESULTS}
    (OUT / "03_账本对账.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print("\n=== 汇总 ===")
    print(json.dumps(cnt, ensure_ascii=False, indent=2))
    for r in RESULTS:
        print(f"  {r['id']:4s} {r['verdict']:4s} {r['title']}")


if __name__ == "__main__":
    main()
