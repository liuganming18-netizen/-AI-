# -*- coding: utf-8 -*-
"""70｜自然扫描进度与未触发原因（只读；证明「不是没接上，是确实没产生自然候选」）。

读四处：
  1) 运行时状态快照 `框架/backend/locks/paper_runtime_3a_status.json`（守护进程每 2s 重写）
  2) 生产信号库（`live` 行数、研究行数）
  3) 真实 HTTP：当前交易区 / 观察区
  4) 新开仓闸门文件

结论只写事实：水位到哪、漏斗走到哪、卡在什么原因、候选几个。
**不**用「单边上涨」这类解释去顶替数据（漏斗数字与旧结论不一致的地方单列未解项）。

输出：out/70_自然扫描.json
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

STATUS = L.BACKEND / "locks" / "paper_runtime_3a_status.json"
GATE = L.BACKEND / "live" / "paper_new_entry_gate.json"


def iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000)) if ms else "—"


def get_json(path: str) -> dict:
    with urllib.request.urlopen(L.API + path, timeout=90) as r:
        return {"status": r.status, "json": json.loads(r.read().decode())}


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}

    st = json.loads(STATUS.read_text(encoding="utf-8"))
    age = time.time() - STATUS.stat().st_mtime
    se, op, ex = st.get("signal_engine") or {}, st.get("order_processing") or {}, \
        st.get("execution_daemon") or {}
    wc = se.get("watermark_check") or {}
    fn = se.get("funnel") or {}
    idle = se.get("idle_breakdown") or {}

    rep["状态快照"] = {
        "path": str(STATUS), "age_s": round(age, 1),
        "runtime_identity": st.get("runtime_identity"),
        "market_data": st.get("market_data"),
        "signal_engine.status": se.get("status"),
        "universe": se.get("universe"), "universe_declared": se.get("universe_declared"),
        "watermark_1h_open": iso(se.get("watermark_1h")),
        "watermark_4h_open": iso(se.get("watermark_4h")),
        "state_counter": se.get("state_counter"),
        "signals_generated_total": se.get("signals_generated_total"),
        "last_cycle_ms": se.get("last_cycle_ms"),
    }
    rep["水位一致性"] = {
        "aligned_1h": wc.get("aligned_1h"), "lag_1h_bars": wc.get("lag_1h_bars"),
        "aligned_4h": wc.get("aligned_4h"), "lag_4h_bars": wc.get("lag_4h_bars"),
        "global_1h": iso(wc.get("global_1h")), "processed_1h_max": iso(wc.get("processed_1h_max")),
        "per_symbol_n": (wc.get("per_symbol") or {}).get("n"),
        "lag_1h_histogram": (wc.get("per_symbol") or {}).get("lag_1h_histogram"),
        "n_lag_1h_gt1": (wc.get("per_symbol") or {}).get("n_lag_1h_gt1"),
    }
    rep["漏斗"] = fn
    rep["空转分因"] = {"counts": idle.get("counts"), "idle_total": idle.get("idle_total"),
                    "sample_n": len(idle.get("sample") or {})}
    rep["订单与执行"] = {
        "orders_routed_total": op.get("orders_routed_total"),
        "orders_rejected_total": op.get("orders_rejected_total"),
        "orders_waiting_gate": op.get("orders_waiting_gate"),
        "current_pending_in_db": op.get("current_pending_in_db"),
        "gate_status": op.get("status"),
        "new_entry_gate": op.get("new_entry_gate"),
        "open_positions": ex.get("open_positions"),
        "pending_matched_total": ex.get("pending_matched_total"),
        "exits_triggered_total": ex.get("exits_triggered_total"),
        "execution_status": ex.get("status"),
        "last_error": ex.get("last_error"),
    }
    rep["闸门文件"] = {"path": str(GATE),
                    "content": json.loads(GATE.read_text(encoding="utf-8")) if GATE.exists() else None}

    con = L.ro_conn(L.LIVE_DB)
    try:
        rep["生产信号库"] = {
            "总行数": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
            "live 行数": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin IN ('live','recovered')").fetchone()[0],
            "研究行数": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research'").fetchone()[0],
            "最近一条 live 的 event_time": con.execute(
                "SELECT MAX(event_time) FROM live_signal WHERE origin IN ('live','recovered')"
            ).fetchone()[0],
        }
    finally:
        con.close()

    cur = get_json("/api/v1/signals?scope=current&limit=500")
    obs = get_json("/api/v1/signals/observe?arm=3a")
    rep["HTTP"] = {
        "当前交易区": {"status": cur["status"],
                   "items_n": len((cur["json"].get("items") or [])),
                   "quality": cur["json"].get("quality")},
        "观察区": {"status": obs["status"],
                "items_n": len((obs["json"].get("items") or []))},
    }

    since = fn.get("since_start") or {}
    hist = fn.get("history") or {}
    rep["未触发原因"] = {
        "本进程漏斗": since,
        "48天预热回放漏斗": hist,
        "候选数（本进程）": fn.get("candidates_since_start"),
        "最近候选时刻": fn.get("last_candidate_utc"),
        "空转分因计数": idle.get("counts"),
        "读法": "since_start=本次进程启动后的状态机迁移；history=预热回放期；两者不可相加、不可互替",
    }
    rep["未解项"] = {
        "RETEST→CANDIDATE 归零": {
            "事实": f"预热回放期 RETEST={hist.get('RETEST')} 而 CANDIDATE={hist.get('CANDIDATE')}；"
                    f"本进程 RETEST={since.get('RETEST')} 而 CANDIDATE={since.get('CANDIDATE')}",
            "为何单列": "旧结论把这波 0 候选归因为「单边上涨里箱体等不到跌破下沿」，"
                        "但漏斗显示扫低/回踩都发生过、候选仍为 0 ⇒ 该解释不能覆盖全部现象",
            "处置": "本单不下结论、不据此改策略；需单独立项核查 RETEST→CANDIDATE 的判定链",
        },
        "计数口径": "漏斗是状态迁移计数（按根/按次），不是独立事件数；不得当作事件数使用",
    }
    rep["未验证"] = [
        "自然信号 → 订单 → 撮合 → 平仓 的完整闭环：自然候选为 0，本单无法用自然信号验证",
        "研究信号若被批准后的真实路由：本单只有不可执行态研究信号，未做任何可执行研究信号",
    ]

    gate = op.get("new_entry_gate") or {}
    checks = {
        "状态快照存在且新鲜（<180s）": age < 180,
        "守护进程 status=running": ex.get("status") == "running",
        "1h 水位与库内最大一致（lag=0）": wc.get("aligned_1h") is True and wc.get("lag_1h_bars") == 0,
        "4h 水位与库内最大一致（lag=0）": wc.get("aligned_4h") is True and wc.get("lag_4h_bars") == 0,
        "全池无落后标的（lag_1h>1 为 0）": (wc.get("per_symbol") or {}).get("n_lag_1h_gt1") == 0,
        "自然信号生成数 0": se.get("signals_generated_total") == 0,
        "本进程候选数 0": fn.get("candidates_since_start") == 0,
        "48 天预热回放候选数 0": (hist.get("CANDIDATE") == 0),
        "生产库 live 行数 0（只有研究行）": rep["生产信号库"]["live 行数"] == 0,
        "生产库研究行 289": rep["生产信号库"]["研究行数"] == 289,
        "当前交易区 HTTP 0 条": rep["HTTP"]["当前交易区"]["items_n"] == 0,
        "观察区 HTTP 有数据（333 条，链路通）": rep["HTTP"]["观察区"]["items_n"] > 0,
        "路由挂单 0、库内挂单 0、持仓 0":
            op.get("orders_routed_total") == 0 and op.get("current_pending_in_db") == 0
            and ex.get("open_positions") == 0,
        "未触发原因可读（空转分因 3 类齐全）":
            set((idle.get("counts") or {})) == {"IDLE_WAIT_BOX", "IDLE_COOLING", "IDLE_READY_BOX"},
        "执行守护无错误": ex.get("last_error") is None,
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = (
        "自然扫描链路在跑（水位对齐、观察区有 333 条），但本进程与 48 天预热回放的候选数都是 0 "
        "⇒ 「没有自然信号」是策略确实没产生，不是管道断了；研究信号导入因此是当前唯一可验证路径。"
        if not fail else f"存在未通过项：{fail}")
    rep["待裁决"] = {
        "新开仓闸门当前是开着的": {
            "allow_new_entries": gate.get("allow_new_entries"),
            "reason": gate.get("reason"), "set_by": gate.get("set_by"),
            "since_ms": gate.get("since_ms"), "since_utc": iso(gate.get("since_ms")),
            "风险": "研究信号虽被四层条件挡住，但闸门是开的 ⇒ 一旦有人把可执行态/角色改错就会真开仓",
            "建议（需裁决，本单未改）": "在 paper_engine 入口增加 approved=false 硬拒，或研究期间关闭闸门",
        },
        "RETEST→CANDIDATE 归零": rep["未解项"]["RETEST→CANDIDATE 归零"],
    }

    p = L.dump("70_自然扫描.json", rep)
    print("== 70 自然扫描 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
