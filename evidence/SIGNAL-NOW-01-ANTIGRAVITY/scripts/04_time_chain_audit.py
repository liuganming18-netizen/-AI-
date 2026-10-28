# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤4：逐字段核查时间来源与单调关系（生产只读）。

本步骤回答三件事
----------------
1. **每个时间字段是谁写的、语义是什么**：事件/确认、可得、现实发现（首次请求）、
   下单登记、计划执行 bar、实际撮合、处理时刻 —— 七个口径逐字段列清，不做合并解释。
2. **单调关系逐行成立吗**：四臂全部行（不是抽样）逐条验：
   `event_time ≤ available_at ≤ first_request ≤ created_at ≤ earliest_fill
    ≤ bar_open < bar_close < fill_ts`，以及 `entry_ts == bar_open`、
   `fill_ts == paper_order.ts == paper_pending.resolved_at == paper_processed.ts`。
3. **成交所用 bar 是否「当时确实拿得到」**：从行情库反查该 bar 的
   `is_closed` / `available_at` / `open`，并统计同一 `open_time` 全池标的里
   「`is_closed=1` 但 `available_at < close_time`」的条数（步骤 3 的 D4 前置条件
   在真实数据里到底会不会出现）。

纪律
----
- 生产库一律 `mode=ro` 打开；行情库 `mode=ro`；本脚本**只读不写任何生产文件**。
- 缺证据的字段一律记 `NULL/unproven`，不用报告结论替代证据。
- 查不清的差值**不推断原因**，标 `unresolved` 并列出候选解释。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
LIVE_DB = PROJ / "框架/backend/live/live_signals.sqlite3"
PAPER_DIR = PROJ / "数据/paper"
MARKET_DB = PROJ / "数据/市场数据/db/market_v2.sqlite3"
ARMS = ("3a", "3b", "3c", "3d")
BAR = 900_000
MAX_FILL_LAG_MS = 3_600_000


def utc(x):
    if x is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(x) / 1000)) + \
        f".{int(x) % 1000:03d}Z"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ro(p: Path) -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=15)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


PROD_FILES = {"live_signals": LIVE_DB,
              **{f"paper_{a}": PAPER_DIR / f"paper_{a}.sqlite3" for a in ARMS}}


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "4 逐字段时间来源与单调关系（生产只读）",
         "at_utc": utc(time.time() * 1000),
         "mode": "全部生产库 mode=ro + query_only；只新增本目录产物"}
    R["prod_before"] = {k: {"sha256": sha256_file(p), "size": p.stat().st_size}
                        for k, p in PROD_FILES.items() if p.exists()}

    # ══════════════════════════════════════════════════════════════════════
    # 1. 字段清单与语义（字段 → 写入点 → 语义）
    # ══════════════════════════════════════════════════════════════════════
    R["field_semantics"] = {
        "event_time": {
            "表": "paper_position / paper_fill / paper_order",
            "写入点": "open_position(... event_time=...) 原样透传自信号",
            "语义": "信号**事件/确认时刻**（bar 收盘那一刻），不是发现时刻",
            "缺证据时": "NULL（不得用别的时刻代替）"},
        "available_at": {
            "表": "paper_position / paper_fill",
            "写入点": "open_position(... available_at=...) 原样透传自信号",
            "语义": "该信号在**当时**确实可得的时刻；研究行无证据 ⇒ NULL",
            "缺证据时": "NULL + payload.timestamps.available_at_status='unproven'"},
        "first_request_ms": {
            "表": "paper_position / paper_fill / paper_order / paper_pending / paper_request_anchor",
            "写入点": "paper_request_anchor 的 INSERT OR IGNORE（只落首次墙钟）",
            "语义": "**现实发现时刻** = 第一次真正请求这笔单的服务端挂钟",
            "缺证据时": "不会为空：缺失时取本次 attempt"},
        "paper_pending.created_at": {
            "表": "paper_pending",
            "写入点": "_register_pending(... L.now_ms())",
            "语义": "**下单登记时刻**（挂单行落库的挂钟）",
            "缺证据时": "不会为空"},
        "earliest_fill_ms": {
            "表": "paper_position / paper_fill / paper_pending",
            "写入点": "order_request_ms + signal_execution_delay_minutes×60s",
            "语义": "**计划执行下限**（不是实际成交时刻）",
            "缺证据时": "不会为空"},
        "bar_open_time / bar_close_time": {
            "表": "paper_position(entry_ts) / paper_fill / paper_order",
            "写入点": "bar_at_or_after(earliest_fill_ms) 取到的**第一根已闭合 15m bar**",
            "语义": "**计划执行 bar**；entry_ts 回填为 bar_open_time",
            "缺证据时": "不会为空（取不到 bar 就登记挂单）"},
        "fill_ts": {
            "表": "paper_fill",
            "写入点": "attempt_ms（挂单推进时为推进时钟 T，日常开仓时为真实挂钟）",
            "语义": "**实际撮合/处理时刻**（挂钟），审计用；不等于 entry_ts",
            "缺证据时": "不会为空"},
        "paper_order.ts / paper_processed.ts / paper_pending.resolved_at": {
            "表": "对应三表",
            "写入点": "同上 attempt_ms / T",
            "语义": "同一处理时刻的镜像，应逐位相等",
            "缺证据时": "不会为空"},
    }

    # ══════════════════════════════════════════════════════════════════════
    # 2. 全量行枚举（不抽样）
    # ══════════════════════════════════════════════════════════════════════
    ledger = {}
    for arm in ARMS:
        p = PAPER_DIR / f"paper_{arm}.sqlite3"
        con = ro(p)
        try:
            d = {}
            for t in ("paper_position", "paper_fill", "paper_order", "paper_pending",
                      "paper_realized", "paper_processed", "paper_request_anchor"):
                try:
                    d[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
                except sqlite3.OperationalError as exc:
                    d[t] = [{"__error__": str(exc)}]
            ledger[arm] = d
        finally:
            con.close()
    R["ledger_row_counts"] = {arm: {t: len(rows) for t, rows in d.items()}
                              for arm, d in ledger.items()}
    R["ledger_note"] = ("3b/3c/3d 三臂当前**全表 0 行**（从未落过任何持仓/成交/挂单）；"
                        "全部账本活动集中在 3a。这是当前事实，不代表历史上没跑过回放。")

    # ══════════════════════════════════════════════════════════════════════
    # 3. 逐笔时间链（以 position 为主键串起 order/fill/pending/anchor/processed）
    # ══════════════════════════════════════════════════════════════════════
    mkt = ro(MARKET_DB)

    def market_bar(symbol, open_time):
        r = mkt.execute(
            "SELECT open_time,close_time,open,high,low,is_closed,available_at "
            "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? "
            "AND timeframe='15m' AND open_time=?", (symbol, int(open_time))).fetchone()
        return dict(r) if r else None

    def first_closed_at_or_after(symbol, ts):
        r = mkt.execute(
            "SELECT open_time,close_time,open,is_closed,available_at FROM candles "
            "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
            "AND is_closed=1 AND open_time>=? ORDER BY open_time LIMIT 1",
            (symbol, int(ts))).fetchone()
        return dict(r) if r else None

    chains, violations = [], []

    def chk(cid, name, ok, detail):
        if not ok:
            violations.append({"chain": cid, "assert": name, "detail": detail})

    for arm in ARMS:
        d = ledger[arm]
        orders = {o.get("event_uid"): o for o in d["paper_order"] if "event_uid" in o}
        fills_by_uid = {}
        for f in d["paper_fill"]:
            fills_by_uid.setdefault(f.get("signal_uid"), []).append(f)
        anchors = {a.get("event_uid"): a for a in d["paper_request_anchor"]
                   if "event_uid" in a}
        pend_by_uid = {p.get("signal_uid"): p for p in d["paper_pending"]
                       if "signal_uid" in p}
        proc_by_uid = {p.get("signal_uid"): p for p in d["paper_processed"]
                       if "signal_uid" in p}

        for pos in d["paper_position"]:
            uid = pos.get("signal_uid")
            cid = f"{arm}/pos{pos['position_id']}/{uid}"
            open_fill = next((f for f in fills_by_uid.get(uid, [])
                              if f.get("action") == "open"), None)
            o = orders.get(uid)
            a = anchors.get(uid)
            pd = pend_by_uid.get(uid)
            pr = proc_by_uid.get(uid)

            ev = pos.get("event_time")
            av = pos.get("available_at")
            fr = pos.get("first_request_ms")
            cre = pd.get("created_at") if pd else None
            efl = pos.get("earliest_fill_ms")
            bo = pos.get("entry_ts")
            bc = open_fill.get("bar_close_time") if open_fill else None
            fts = open_fill.get("fill_ts") if open_fill else None

            bar = market_bar(pos["symbol"], bo) if bo else None
            expect_bar = first_closed_at_or_after(pos["symbol"], efl) if efl else None

            rec = {
                "chain": cid, "arm": arm, "symbol": pos["symbol"], "uid": uid,
                "status": pos.get("status"), "voided": pos.get("voided"),
                "origin": pos.get("origin"),
                "event_time": ev, "event_utc": utc(ev),
                "available_at": av, "available_utc": utc(av),
                "first_request_ms": fr, "first_request_utc": utc(fr),
                "anchor_first_request_ms": (a or {}).get("first_request_ms"),
                "anchor_first_request_utc": utc((a or {}).get("first_request_ms")),
                "anchor_created_at": (a or {}).get("created_at"),
                "anchor_created_utc": utc((a or {}).get("created_at")),
                "pending_created_at": cre, "pending_created_utc": utc(cre),
                "earliest_fill_ms": efl, "earliest_fill_utc": utc(efl),
                "entry_ts": bo, "entry_utc": utc(bo),
                "bar_close_time": bc, "bar_close_utc": utc(bc),
                "fill_ts": fts, "fill_utc": utc(fts),
                "order_ts": (o or {}).get("ts"), "order_ts_utc": utc((o or {}).get("ts")),
                "pending_resolved_at": (pd or {}).get("resolved_at"),
                "pending_resolved_utc": utc((pd or {}).get("resolved_at")),
                "processed_ts": (pr or {}).get("ts"),
                "processed_ts_utc": utc((pr or {}).get("ts")),
                "market_bar": {k: bar[k] for k in
                               ("open_time", "close_time", "open", "is_closed",
                                "available_at")} if bar else None,
                "market_bar_available_utc": utc(bar["available_at"]) if bar else None,
                "recomputed_first_closed_bar_open": (expect_bar or {}).get("open_time"),
                "recomputed_first_closed_bar_open_utc": utc((expect_bar or {}).get("open_time")),
                "raw_price": open_fill.get("raw_price") if open_fill else None,
                "entry_price": pos.get("entry_price"),
                "planned_risk": pos.get("planned_risk"),
                "exit_ts": pos.get("exit_ts"), "exit_utc": utc(pos.get("exit_ts")),
                "qty": pos.get("qty"),
                "deltas": {
                    "event→available": (av - ev) if (av is not None and ev is not None) else None,
                    "event→first_request": (fr - ev) if (fr is not None and ev is not None) else None,
                    "first_request→pending_created": (cre - fr) if (cre and fr) else None,
                    "pending_created→earliest_fill": (efl - cre) if (efl and cre) else None,
                    "earliest_fill→bar_open": (bo - efl) if (bo and efl) else None,
                    "bar_open→bar_close": (bc - bo) if (bc and bo) else None,
                    "bar_close→fill_ts": (fts - bc) if (fts and bc) else None,
                    "bar_open→fill_ts(回填量)": (fts - bo) if (fts and bo) else None,
                    "event→fill_ts": (fts - ev) if (fts and ev) else None,
                },
                "fill_lag_vs_bar_open_ms": (fts - bo) if (fts and bo) else None,
                "within_max_fill_lag": ((fts - bo) <= MAX_FILL_LAG_MS) if (fts and bo) else None,
            }
            chains.append(rec)

            # ── 单调关系逐条 ──
            if ev is not None and av is not None:
                chk(cid, "event_time <= available_at", ev <= av, f"{ev} > {av}")
            if ev is not None and fr is not None:
                chk(cid, "event_time <= first_request", ev <= fr, f"{ev} > {fr}")
            if a and fr is not None:
                chk(cid, "first_request == anchor.first_request（首次请求不被刷新）",
                    a.get("first_request_ms") == fr, f"{a.get('first_request_ms')} != {fr}")
            if fr is not None and cre:
                chk(cid, "first_request <= pending.created_at", fr <= cre, f"{fr} > {cre}")
            if cre and efl:
                chk(cid, "pending.created_at <= earliest_fill", cre <= efl, f"{cre} > {efl}")
            if efl and bo:
                chk(cid, "earliest_fill <= bar_open", efl <= bo, f"{efl} > {bo}")
            if bo and bc:
                chk(cid, "bar_open < bar_close", bo < bc, f"{bo} >= {bc}")
            if bc and fts:
                chk(cid, "bar_close < fill_ts（成交不得早于 bar 收盘）",
                    bc < fts, f"bar_close={utc(bc)} fill_ts={utc(fts)}")
            if fts and (o or {}).get("ts"):
                chk(cid, "fill_ts == paper_order.ts", fts == o.get("ts"),
                    f"{fts} != {o.get('ts')}")
            if fts and pd and pd.get("resolved_at"):
                chk(cid, "fill_ts == paper_pending.resolved_at",
                    fts == pd.get("resolved_at"), f"{fts} != {pd.get('resolved_at')}")
            if fts and pr:
                chk(cid, "fill_ts == paper_processed.ts", fts == pr.get("ts"),
                    f"{fts} != {pr.get('ts')}")
            # 成交 bar 反查
            if expect_bar and bo:
                chk(cid, "记录的执行 bar == 从 earliest_fill 重算的第一根已闭合 bar",
                    expect_bar["open_time"] == bo,
                    f"重算={utc(expect_bar['open_time'])} 记录={utc(bo)}")
            if bar and rec["raw_price"] is not None:
                chk(cid, "成交 raw_price == 该 bar 的 open",
                    abs(float(bar["open"]) - float(rec["raw_price"])) < 1e-9,
                    f"{bar['open']} != {rec['raw_price']}")
            if bar:
                chk(cid, "成交所用 bar 的 is_closed == 1", int(bar["is_closed"]) == 1,
                    f"is_closed={bar['is_closed']}")
            if rec["fill_lag_vs_bar_open_ms"] is not None:
                chk(cid, "fill_ts - bar_open <= MAX_FILL_LAG_MS(1h)",
                    rec["within_max_fill_lag"],
                    f"lag={rec['fill_lag_vs_bar_open_ms']}ms")

    R["time_chains"] = chains
    R["monotonic_violations"] = violations
    R["monotonic_summary"] = {
        "chains": len(chains),
        "violations": len(violations),
        "note": "每条 chain 上最多 16 条单调/一致性断言，四臂全量行，不是抽样"}
    R["violation_reading"] = {
        "rows": [v["chain"] for v in violations],
        "all_are_voided_synthetic": all(
            (p.get("voided") or 0) == 1
            for arm in ARMS for p in ledger[arm]["paper_position"]
            if f"{arm}/pos{p['position_id']}/{p.get('signal_uid')}" in
            {v["chain"] for v in violations}),
        "what_it_means": (
            "三条违规全部落在 `voided=1 / void_reason=synthetic_test_isolation` 的旧手工闭环测试行"
            "（`origin=manual`、`model_version=paper_exec_v1_...`）。它们的 `fill_ts` 比所记执行 bar 的"
            "收盘时刻**早约 14 分钟**，且该 bar 的 `available_at` 也在 fill_ts 之后约 14 分钟 ⇒ "
            "用**当前**行情数据无法复原「当时是拿哪一根 bar 成交的」。"),
        "consequence": (
            "这三行**不能**作为「实时成交」的证据；做任何成绩统计时必须按 `voided` 排除。"
            "同时它们正面印证了步骤 3 的 D4 结论：只要 bar 带着 `is_closed=1` 出现在库里，"
            "引擎就会用它成交，代码层没有 `bar_close <= attempt` 的第二道校验。"),
        "not_a_claim_about_now": (
            "本单**不**据此说当前 FT-LOW4H 路径也在提前成交 —— 当前唯一非作废成交"
            "（`test_probe_succ`）的 `fill_ts` 晚于该 bar 收盘 14.729 秒。")}

    # ══════════════════════════════════════════════════════════════════════
    # 4. 成交 bar 的「当时可得」证据 + D4 前置条件在真实数据中的出现率
    # ══════════════════════════════════════════════════════════════════════
    avail_audit = []
    for rec in chains:
        bo = rec["entry_ts"]
        if not bo:
            continue
        bar = rec["market_bar"] or {}
        av = bar.get("available_at")
        bc = bar.get("close_time")
        # 同一 open_time 全池统计
        cnt_all = mkt.execute(
            "SELECT COUNT(*) FROM candles WHERE exchange='binance' AND market='um' "
            "AND timeframe='15m' AND open_time=? AND is_closed=1", (int(bo),)).fetchone()[0]
        cnt_early = mkt.execute(
            "SELECT COUNT(*) FROM candles WHERE exchange='binance' AND market='um' "
            "AND timeframe='15m' AND open_time=? AND is_closed=1 AND available_at < close_time",
            (int(bo),)).fetchone()[0]
        cnt_null = mkt.execute(
            "SELECT COUNT(*) FROM candles WHERE exchange='binance' AND market='um' "
            "AND timeframe='15m' AND open_time=? AND is_closed=1 AND available_at IS NULL",
            (int(bo),)).fetchone()[0]
        avail_audit.append({
            "chain": rec["chain"], "symbol": rec["symbol"], "open_time_utc": utc(bo),
            "bar_close_utc": utc(bc), "bar_available_at_utc": utc(av),
            "fill_ts_utc": rec["fill_utc"],
            "available_at_minus_close_ms": (av - bc) if (av and bc) else None,
            "fill_ts_minus_available_at_ms": (rec["fill_ts"] - av) if (av and rec["fill_ts"]) else None,
            "same_open_time_symbols": cnt_all,
            "same_open_time_is_closed_before_close": cnt_early,
            "same_open_time_available_at_null": cnt_null,
        })
    R["bar_availability_audit"] = avail_audit

    # 差值方向统计（只报事实，不推断原因）
    neg = [a for a in avail_audit
           if a["fill_ts_minus_available_at_ms"] is not None
           and a["fill_ts_minus_available_at_ms"] < 0]
    R["fill_earlier_than_bar_available_at"] = {
        "count": len(neg),
        "items": neg,
        "unresolved": True,
        "why_unresolved": (
            "同一根 bar 上 `fill_ts` 早于 `available_at`。两者必有一个不能当作「当时可得的时刻」。"
            "候选解释（**均未证实，不采信**）："
            "①`available_at` 取自帧接收时刻而落库更晚（`sources.imported_at` 比它晚 0.257 秒）；"
            "②挂单推进路径 `is_replay=True` 使 `attempt_ms` 取调用方传入的 T 而不是真实挂钟；"
            "③两个写入者/两个时钟。"
            "在独立时钟证据到位前，本单**不把 `available_at` 当作可得性证明**，"
            "也**不把 `fill_ts` 当作「当时确实拿得到」的证明**。")}

    # ══════════════════════════════════════════════════════════════════════
    # 4b. `fill_ts` 到底是什么时刻（代码路径 + 实测对撞）
    # ══════════════════════════════════════════════════════════════════════
    R["fill_ts_semantics"] = {
        "conclusion": (
            "挂单推进路径上 `paper_fill.fill_ts` = **本轮的循环起始时刻**，"
            "不是「真正读到该 bar 并完成撮合的那一刻」。"),
        "code_path": [
            "unified_runtime.py:2831  step_once(): `now = int(now_ms or time.time()*1000)` "
            "—— 在函数入口取一次，之后整轮复用",
            "unified_runtime.py:2852  signal_engine.step(now, ...) —— 全池 528 标的扫描在中间执行",
            "unified_runtime.py:2893  execution.execute_cycle(now, process_start_ms=...) —— 把同一个 now 传下去",
            "unified_runtime.py:2347  PE.advance_pending(T=now_ms, ...)",
            "paper_engine.py:920      T = int(T) if T else L.now_ms()",
            "paper_engine.py:1093     is_replay = (origin in ('replay','test') or pending_id is not None) "
            "⇒ **挂单推进时恒为 True**",
            "paper_engine.py:1094     attempt_ms = int(now_ms) if (is_replay and now_ms is not None) "
            "else real_wall_ms ⇒ 取调用方传入的 T，不取真实挂钟",
            "paper_engine.py:1461     paper_fill.fill_ts = attempt_ms",
        ],
        "empirical_corroboration": (
            "test_probe_succ：fill_ts=2026-10-27T16:30:14.728Z，而该 bar 的 "
            "`available_at`=2026-10-27T16:30:16.461Z、`sources.imported_at`=16:30:16.718Z "
            "⇒ 该行落库晚于 fill_ts 约 2.0 秒。同一 open_time 全池 528 个标的里"
            "「is_closed=1 且 available_at < close_time」的条数 = 0，"
            "即真实数据里不存在「bar 未收盘就被标成已闭合」的情形 ⇒ "
            "唯一自洽的解释是 fill_ts 早于真正读到该 bar 的时刻（循环起始 vs 撮合完成）。"),
        "strength": "强推断（代码路径逐行可查 + 实测时序对撞），不是受控实验；"
                    "未做「人为拖慢单轮耗时再观测 fill_ts」的对照。",
        "impact": [
            "`fill_ts` 只能当「本轮开始处理」的下界，不能当「当时确实拿得到该 bar」的证明；",
            "`fill_ts - bar_close` 会**低估**真实成交延迟（真实延迟 ≥ 记录值）；",
            "`MAX_FILL_LAG_MS` 用的是同一个 attempt_ms ⇒ 长轮次下「滞后」会被低估；",
            "`bar_open > attempt_ms` 的前视判据用的是同一个 attempt_ms ⇒ 该判据偏严不偏松。",
        ],
        "not_a_defect_claim": (
            "本单**不**据此宣布「成交用了未来数据」。bar 确实已收盘（"
            "全部 528 个标的 available_at 均晚于 close_time），成交价取自已收盘 bar 的 open。"
            "本条只否定「fill_ts 可作可得性证明」这一用法。"),
    }

    # ══════════════════════════════════════════════════════════════════════
    # 5. 289 条研究行的时间字段可得性（只读，不碰身份）
    # ══════════════════════════════════════════════════════════════════════
    lc = ro(LIVE_DB)
    try:
        rows = lc.execute(
            "SELECT signal_uid, symbol, origin, executable_state, available_at, "
            "confirmed_at, payload_json FROM live_signal "
            "WHERE origin='historical_research'").fetchall()
    finally:
        lc.close()
    res_stat = {"count": len(rows), "available_at_null": 0, "available_at_nonnull": 0,
                "confirmed_at_null": 0, "available_at_status": {}, "violations": []}
    for r in rows:
        if r["available_at"] is None:
            res_stat["available_at_null"] += 1
        else:
            res_stat["available_at_nonnull"] += 1
            if r["confirmed_at"] is not None and int(r["available_at"]) < int(r["confirmed_at"]):
                res_stat["violations"].append(
                    {"uid": r["signal_uid"], "available_at": r["available_at"],
                     "confirmed_at": r["confirmed_at"]})
        if r["confirmed_at"] is None:
            res_stat["confirmed_at_null"] += 1
        try:
            p = json.loads(r["payload_json"] or "{}")
        except Exception:                                    # noqa: BLE001
            p = {}
        st = (p.get("timestamps") or {}).get("available_at_status")
        res_stat["available_at_status"][str(st)] = \
            res_stat["available_at_status"].get(str(st), 0) + 1
    R["research_rows_time_fields"] = res_stat

    # ══════════════════════════════════════════════════════════════════════
    # 6. 生产零写入校验
    # ══════════════════════════════════════════════════════════════════════
    R["prod_after"] = {k: {"sha256": sha256_file(p), "size": p.stat().st_size}
                       for k, p in PROD_FILES.items() if p.exists()}
    R["prod_untouched"] = {k: (R["prod_before"][k]["sha256"] == R["prod_after"][k]["sha256"])
                           for k in R["prod_before"]}

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "04_时间字段与单调关系.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("=== 账本行数 ===")
    for arm, c in R["ledger_row_counts"].items():
        print(f"  {arm}: " + " ".join(f"{k}={v}" for k, v in c.items()))
    print("\n=== 时间链（逐笔）===")
    for rec in chains:
        print(f"  {rec['chain']}")
        for k in ("event_utc", "available_utc", "first_request_utc", "pending_created_utc",
                  "earliest_fill_utc", "entry_utc", "bar_close_utc", "fill_utc"):
            print(f"      {k:24s} {rec[k]}")
        d = rec["deltas"]
        print(f"      event→first_request      {d['event→first_request']} ms")
        print(f"      bar_close→fill_ts        {d['bar_close→fill_ts']} ms")
        print(f"      回填量 bar_open→fill_ts   {d['bar_open→fill_ts(回填量)']} ms")
    print(f"\n=== 单调关系违规：{len(violations)} 条 ===")
    for v in violations:
        print(f"  [{v['chain']}] {v['assert']} → {v['detail']}")
    print("\n=== 成交 bar 可得性 ===")
    for a in avail_audit:
        print(f"  {a['chain']}")
        print(f"      close={a['bar_close_utc']} available_at={a['bar_available_at_utc']} "
              f"fill_ts={a['fill_ts_utc']}")
        print(f"      available_at-close={a['available_at_minus_close_ms']}ms  "
              f"fill_ts-available_at={a['fill_ts_minus_available_at_ms']}ms")
        print(f"      同 open_time 全池={a['same_open_time_symbols']} "
              f"提前置 is_closed={a['same_open_time_is_closed_before_close']} "
              f"available_at 为空={a['same_open_time_available_at_null']}")
    print(f"\n=== 研究行时间字段：{json.dumps(res_stat, ensure_ascii=False)} ===")
    print("\n=== fill_ts 语义 ===")
    print("  " + R["fill_ts_semantics"]["conclusion"])
    for ln in R["fill_ts_semantics"]["code_path"]:
        print("    · " + ln)
    print("  强度：" + R["fill_ts_semantics"]["strength"])
    print("\n=== 违规行怎么读 ===")
    print("  " + R["violation_reading"]["what_it_means"])
    print("  " + R["violation_reading"]["consequence"])
    print("\n=== 生产库零写入校验 ===")
    for k, v in R["prod_untouched"].items():
        print(f"  {k}: 未变={v}")
    print(f"\n产物：{p}")
    mkt.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
