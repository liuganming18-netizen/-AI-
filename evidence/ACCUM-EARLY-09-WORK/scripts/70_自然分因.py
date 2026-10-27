# -*- coding: utf-8 -*-
"""70｜自然零候选只读分因（沿当前自然扫描路径，不注入、不改阈值）。

读什么
------
1. 运行时状态快照 `框架/backend/locks/paper_runtime_3a_status.json`（守护进程周期重写）：
   水位、扫描范围、漏斗、逐标的状态、空转分因、数据质量、ATR 预热；
2. 生产信号库（只读）：live 行 / 研究行；
3. 真实 HTTP：当前交易区、观察区；
4. **独立复算**：直接查行情库，对 526 个标的逐个算 1h 水位落后根数，
   与运行时自报的 `lag_1h_histogram` 对撞（不复用它的结论）。

口径纪律（工单要求）
--------------------
· 漏斗里的 `BOX/SWEEP/RETEST/CANDIDATE` 是**状态迁移次数**（按根/按次累加），
  **不是**独立事件数；独立单位是「标的」——由 `per_symbol_state` 给出（526 个标的各一处）。
  两者本单分开列，不相加、不互替。
· 只记录「零候选发生在哪一层」，**不解释成策略无效**，也不改阈值/规则制造信号。
· 本脚本对生产库只读；前后各取一次 sha256 证明没被写过。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib09 as U  # noqa: E402

STATUS = U.BACKEND / "locks" / "paper_runtime_3a_status.json"
GATE = U.BACKEND / "live" / "paper_new_entry_gate.json"
# 冻结规则 md（13,315B / sha256 1f1167de…9a8ab）；**不是** config.py 的 29a42d98…
RULE_MD = U.PROJECT / "evidence" / "WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"
RULE_SHA = "1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab"
CONFIG_PY = U.BACKEND / "config.py"


def iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000)) if ms else "—"


def main() -> int:
    checks: dict = {}
    live_fp_before = U.fingerprint(U.LIVE_DB)
    rep: dict = {"生成时刻": U.now_utc()}

    st = json.loads(STATUS.read_text(encoding="utf-8"))
    age = time.time() - STATUS.stat().st_mtime
    se = st.get("signal_engine") or {}
    op = st.get("order_processing") or {}
    ex = st.get("execution_daemon") or {}
    wc = se.get("watermark_check") or {}
    fn = se.get("funnel") or {}
    idle = se.get("idle_breakdown") or {}
    dq = se.get("data_quality") or {}
    atr = se.get("atr_caliber") or {}
    pss = se.get("per_symbol_state") or {}
    since = fn.get("since_start") or {}
    hist = fn.get("history") or {}

    # ── 1. 运行身份与新鲜度 ──────────────────────────────────────────────
    rep["运行身份"] = {"快照路径": str(STATUS), "快照年龄秒": round(age, 1),
                   "runtime_identity": st.get("runtime_identity"),
                   "market_data": st.get("market_data")}
    checks["状态快照新鲜（<180s）"] = age < 180
    checks["信号引擎 status=running"] = (st.get("runtime_identity") or {}).get("status") == "running"
    checks["执行守护 status=running"] = ex.get("status") == "running"

    # ── 2. 扫描范围与水位 ───────────────────────────────────────────────
    rep["扫描范围"] = {
        "universe": se.get("universe"), "universe_declared": se.get("universe_declared"),
        "引擎数": atr.get("n_engines"), "已预热": atr.get("n_warmed"),
        "未预热": atr.get("n_not_warmed"), "箱体门开": atr.get("n_box_gate_open"),
        "ATR 口径": atr.get("caliber"), "预热根数要求": atr.get("warmup_bars"),
        "回放窗口小时": fn.get("bootstrap_window_hours"),
    }
    rep["水位"] = {
        "口径": wc.get("convention"),
        "global_1h": iso(wc.get("global_1h")), "processed_1h_max": iso(wc.get("processed_1h_max")),
        "db_1h_max": iso(wc.get("db_1h_max")),
        "aligned_1h": wc.get("aligned_1h"), "lag_1h_bars": wc.get("lag_1h_bars"),
        "global_4h": iso(wc.get("global_4h")),
        "processed_4h_max_open": iso(wc.get("processed_4h_max_open")),
        "processed_4h_max_close": iso(wc.get("processed_4h_max_close")),
        "aligned_4h": wc.get("aligned_4h"), "lag_4h_bars": wc.get("lag_4h_bars"),
        "per_symbol_n": (wc.get("per_symbol") or {}).get("n"),
        "lag_1h_histogram": (wc.get("per_symbol") or {}).get("lag_1h_histogram"),
        "n_lag_1h_gt1": (wc.get("per_symbol") or {}).get("n_lag_1h_gt1"),
    }
    checks["1h 水位与库内最大一致（lag=0）"] = (
        wc.get("aligned_1h") is True and wc.get("lag_1h_bars") == 0)
    checks["4h 水位与库内最大一致（lag=0）"] = (
        wc.get("aligned_4h") is True and wc.get("lag_4h_bars") == 0)
    checks["全池无落后标的（自报 lag>1 为 0）"] = (wc.get("per_symbol") or {}).get(
        "n_lag_1h_gt1") == 0
    checks["扫描范围 526 标的（universe == declared）"] = (
        se.get("universe") == se.get("universe_declared") == 526)

    # ── 3. 独立复算 1h 水位（自己查库，不复用运行时结论）────────────────
    t0 = time.time()
    con = U.ro_conn(U.MARKET_DB)
    try:
        lags, per_symbol_mine, missing = {}, {}, []
        for sym in pss:
            r = con.execute(
                "SELECT MAX(open_time) FROM candle_heads WHERE exchange='binance' "
                "AND market='um' AND symbol=? AND timeframe='1h'", (sym,)).fetchone()
            last = r[0] if r else None
            per_symbol_mine[sym] = last
            if last is None:
                missing.append(sym)
        gmax = max(v for v in per_symbol_mine.values() if v is not None)
        for sym, last in per_symbol_mine.items():
            if last is None:
                continue
            lag = int((gmax - last) // 3600000)
            lags[lag] = lags.get(lag, 0) + 1
        rep["独立复算_1h水位"] = {
            "耗时秒": round(time.time() - t0, 2),
            "标的数": len(per_symbol_mine),
            "行情库最大 1h open": iso(gmax),
            "lag 分布（本脚本算）": {str(k): v for k, v in sorted(lags.items())},
            "运行时自报 lag 分布": (wc.get("per_symbol") or {}).get("lag_1h_histogram"),
            "无 1h 数据的标的": missing,
        }
        checks["独立复算：行情库最大 1h open == 运行时 db_1h_max"] = gmax == wc.get("db_1h_max")
        checks["独立复算：lag 分布与运行时自报一致"] = (
            {str(k): v for k, v in sorted(lags.items())}
            == (wc.get("per_symbol") or {}).get("lag_1h_histogram"))
        checks["独立复算：无 1h 数据的标的 = 0"] = not missing
    finally:
        con.close()

    # ── 4. 漏斗（迁移次数）与独立单位（标的数）分开列 ───────────────────
    rep["漏斗_迁移次数"] = {
        "since_start": since, "history_48天回放": hist,
        "读法": fn.get("note"),
        "强调": "这些是**状态迁移次数**（同一标的可反复进入 BOX/RETEST），不是独立事件数；"
                "不得当作「事件总数」或「候选数」使用。",
    }
    rep["独立单位_标的"] = {
        "per_symbol_state": dict(Counter(pss.values())),
        "state_counter": se.get("state_counter"),
        "标的数": len(pss),
        "说明": "独立单位是「标的」（526 个），每标的一处当前状态；"
                "与上面的迁移次数不是同一个量，禁止相加或互相换算。",
    }
    checks["per_symbol_state 合计 == 526"] = len(pss) == 526
    checks["state_counter 合计 == 526"] = sum((se.get("state_counter") or {}).values()) == 526
    checks["per_symbol_state 分布 == state_counter"] = dict(Counter(pss.values())) == dict(
        se.get("state_counter") or {})
    checks["本进程候选数 = 0"] = fn.get("candidates_since_start") == 0
    checks["自然信号生成总数 = 0"] = se.get("signals_generated_total") == 0

    # ── 5. 零候选发生在哪一层（只定位，不解释成策略无效）───────────────
    rep["零候选定位"] = {
        "本进程": {"BOX": since.get("BOX"), "SWEEP": since.get("SWEEP"),
                   "RETEST": since.get("RETEST"), "CANDIDATE": since.get("CANDIDATE"),
                   "后续关卡拒绝": {"VOLUME_REJECTED": since.get("VOLUME_REJECTED"),
                                "OI_UNAVAILABLE": since.get("OI_UNAVAILABLE"),
                                "OI_REJECTED": since.get("OI_REJECTED")},
                   "失效": {"INVALIDATED": since.get("INVALIDATED"), "EXPIRED": since.get("EXPIRED"),
                          "MISSED_WITHOUT_RETEST": since.get("MISSED_WITHOUT_RETEST")}},
        "48天回放": {"BOX": hist.get("BOX"), "SWEEP": hist.get("SWEEP"),
                     "RETEST": hist.get("RETEST"), "CANDIDATE": hist.get("CANDIDATE"),
                     "后续关卡拒绝": {"VOLUME_REJECTED": hist.get("VOLUME_REJECTED"),
                                  "OI_UNAVAILABLE": hist.get("OI_UNAVAILABLE"),
                                  "OI_REJECTED": hist.get("OI_REJECTED")},
                     "失效": {"INVALIDATED": hist.get("INVALIDATED"),
                            "EXPIRED": hist.get("EXPIRED"),
                            "MISSED_WITHOUT_RETEST": hist.get("MISSED_WITHOUT_RETEST")}},
        "结论口径": "两条时间尺度的漏斗都显示：扫低(SWEEP)与回踩(RETEST)都发生过，"
                    "而 CANDIDATE 在两个尺度上都是 0 ⇒ 零候选发生在 **RETEST→CANDIDATE** 这一层。"
                    "量价量能拒绝(VOLUME_REJECTED)/OI 不可用(OI_UNAVAILABLE)数量远小于 RETEST，"
                    "不足以解释 0 候选。",
        "本单不下结论的部分": "RETEST→CANDIDATE 的判定链为什么一条都没通过，本单只读、不改规则、"
                          "不注入候选，因此**无法**给出根因；需要单独立项核查该判定链。",
    }
    checks["本进程 RETEST>0 而 CANDIDATE=0（定位在 RETEST→CANDIDATE）"] = (
        (since.get("RETEST") or 0) > 0 and since.get("CANDIDATE") == 0)
    checks["48天回放 RETEST>0 而 CANDIDATE=0"] = (
        (hist.get("RETEST") or 0) > 0 and hist.get("CANDIDATE") == 0)
    checks["量价/OI 拒绝数不足以解释 0 候选（远小于 RETEST）"] = (
        (hist.get("VOLUME_REJECTED") or 0) + (hist.get("OI_UNAVAILABLE") or 0)
        + (hist.get("OI_REJECTED") or 0)) < (hist.get("RETEST") or 0)

    # ── 6. 异常 / 缺数 / 阻断原因 ───────────────────────────────────────
    rep["异常与缺数"] = {
        "scan_errors_total": se.get("scan_errors_total"),
        "scan_errors": se.get("scan_errors"),
        "skipped_reasons": se.get("skipped_reasons"),
        "data_quality_规则": dq.get("rule"),
        "data_quality_被拦标的数": dq.get("blocked_n"),
        "data_quality_明细": dq.get("blocked"),
        "ATR_未预热标的": atr.get("n_not_warmed"),
        "ATR_预热根数区间": [atr.get("atr_bars_seen_min"), atr.get("atr_bars_seen_max")],
    }
    rep["阻断原因_空转分因"] = {
        "counts": idle.get("counts"), "idle_total": idle.get("idle_total"),
        "与独立标的数一致": idle.get("idle_total") == (se.get("state_counter") or {}).get("IDLE"),
        "样例": dict(list((idle.get("sample") or {}).items())[:5]),
        "说明": "IDLE_WAIT_BOX=箱体宽度/ER 不满足；IDLE_COOLING=冷却未到；"
                "IDLE_READY_BOX=箱体就绪但未扫低。三类都是「还没到可推进的状态」，"
                "不是扫描失败。",
    }
    checks["扫描错误 = 0"] = se.get("scan_errors_total") == 0 and not se.get("scan_errors")
    checks["跳过原因为空"] = not se.get("skipped_reasons")
    checks["空转分因合计 == IDLE 标的数"] = idle.get("idle_total") == (
        se.get("state_counter") or {}).get("IDLE")
    checks["空转分因三类齐全"] = set((idle.get("counts") or {})) == {
        "IDLE_WAIT_BOX", "IDLE_COOLING", "IDLE_READY_BOX"}
    checks["数据质量拦截标的已如实列出（6 个）"] = dq.get("blocked_n") == 6 and bool(dq.get("blocked"))

    # ── 7. 生产侧现状（与「候选为 0」互相印证）──────────────────────────
    con = U.ro_conn(U.LIVE_DB)
    try:
        rep["生产信号库"] = {
            "总行数": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
            "live/recovered 行数": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin IN ('live','recovered')"
            ).fetchone()[0],
            "研究行数": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research'"
            ).fetchone()[0],
        }
    finally:
        con.close()
    rep["订单与执行"] = {
        "orders_routed_total": op.get("orders_routed_total"),
        "orders_rejected_total": op.get("orders_rejected_total"),
        "orders_waiting_gate": op.get("orders_waiting_gate"),
        "current_pending_in_db": op.get("current_pending_in_db"),
        "open_positions": ex.get("open_positions"),
        "last_error": ex.get("last_error"),
        "new_entry_gate_allow": (op.get("new_entry_gate") or {}).get("allow_new_entries"),
    }
    cur = U.get_json(U.API + "/api/v1/signals?scope=current&limit=500")
    obs = U.get_json(U.API + "/api/v1/signals/observe?arm=3a")
    rep["HTTP"] = {
        "当前交易区": {"status": cur.get("status"),
                   "items_n": len(((cur.get("json") or {}).get("items") or []))},
        "观察区": {"status": obs.get("status"),
                "items_n": len(((obs.get("json") or {}).get("items") or []))},
    }
    checks["生产库 live 行 = 0（只有研究行）"] = rep["生产信号库"]["live/recovered 行数"] == 0
    checks["生产库研究行 = 289"] = rep["生产信号库"]["研究行数"] == 289
    checks["当前交易区 HTTP 0 条"] = rep["HTTP"]["当前交易区"]["items_n"] == 0
    checks["观察区 HTTP 有数据（扫描链路在跑）"] = rep["HTTP"]["观察区"]["items_n"] > 0
    checks["路由挂单 0、持仓 0"] = (op.get("orders_routed_total") == 0
                              and ex.get("open_positions") == 0)
    checks["执行守护无错误"] = ex.get("last_error") is None

    # ── 8. 本单没注入、没改阈值 ─────────────────────────────────────────
    rep["本单未干预的证据"] = {
        "生产信号库 sha256 前": live_fp_before.get("sha256"),
        "生产信号库 sha256 后": U.fingerprint(U.LIVE_DB).get("sha256"),
        "冻结规则文件": U.fingerprint(RULE_MD),
        "冻结规则 sha256 期望值": RULE_SHA,
        "config.py": U.fingerprint(CONFIG_PY),
        "闸门文件": {"path": str(GATE),
                  "sha256": U.sha256_file(GATE) if GATE.exists() else None,
                  "content": json.loads(GATE.read_text(encoding="utf-8")) if GATE.exists() else None},
        "说明": "本单全程只读：没有向 live_signal 写入任何候选、没有改阈值/规则/闸门。",
    }
    checks["生产信号库在本脚本运行期间未被写入"] = U.same_file(
        live_fp_before, U.fingerprint(U.LIVE_DB))["same"]
    checks["冻结规则文件存在"] = RULE_MD.exists()
    checks["冻结规则 sha256 未变（1f1167de…9a8ab）"] = (
        U.sha256_file(RULE_MD) == RULE_SHA if RULE_MD.exists() else False)

    rep["判定"] = U.summarise(
        checks,
        "自然零候选已定位：扫描链路在跑（水位对齐、观察区有数据），零候选发生在 RETEST→CANDIDATE 层",
        "自然零候选分因未通过")
    p = U.dump("70_自然分因.json", rep)
    U.print_checks("70 自然零候选分因", rep, p)
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
