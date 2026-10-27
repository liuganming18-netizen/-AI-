# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜01 现有入口普查：哪些只是占位、哪些能实际运行。

方法：**静态读源码 + 运行时最小探针**，两样都留原始输出。
不靠"看文件名像什么"，只看「有没有读行情 / 有没有推进状态 / 有没有下单调用」。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

BE = C.BACKEND


def _src(name: str) -> str:
    return (BE / name).read_text(encoding="utf-8")


def _count(text: str, *needles) -> dict:
    return {n: text.count(n) for n in needles}


def static_audit() -> dict:
    out = {}
    # ① accum_research：配置/校验/账本 DDL
    t = _src("accum_research.py")
    out["accum_research.py"] = {
        "lines": t.count("\n") + 1,
        "has_market_read": ("candles" in t or "read_parquet" in t),
        "has_state_progress": ("transition" in t or "on_bar" in t),
        "has_order_call": ("open_position" in t or "paper_engine" in t),
        "purpose": "三组入口的配置校验 + 研究账本 DDL（open_ledger/log_blocked）",
        "verdict": "占位：入口与账本在，行情读取/状态推进/下单调用三者全无",
    }
    # ② accum_pending：纯数据表
    t = _src("accum_pending.py")
    out["accum_pending.py"] = {
        "lines": t.count("\n") + 1,
        "has_market_read": "candles" in t,
        "has_state_progress": False,
        "has_order_call": False,
        "purpose": "待决表 / 口径冲突表 / 三组提案文本",
        "verdict": "占位：纯数据表，不推进任何状态",
    }
    # ③ observe_store：观察持久化
    t = _src("observe_store.py")
    out["observe_store.py"] = {
        "lines": t.count("\n") + 1,
        "has_market_read": "candles" in t,
        "has_state_progress": True,
        "has_order_call": False,
        "purpose": "观察事件/候选/迁移/数据缺口的持久化（含 scheme 列与隔离断言）",
        "verdict": "可复用：负责「记录」，不负责「取数」与「下单」",
    }
    # ④ research_15m：冷+热合并读取
    t = _src("research_15m.py")
    out["research_15m.py"] = {
        "lines": t.count("\n") + 1,
        "has_market_read": ("read_parquet" in t and "candles" in t),
        "has_state_progress": False,
        "has_order_call": False,
        "purpose": "15m/1h/4h 冷+热合并读取（Merged15m / MergedReader / MergedMarket）",
        "verdict": "可运行：只读，03-B 已把成交/退出接上",
    }
    # ⑤ paper_engine：执行引擎
    t = _src("paper_engine.py")
    out["paper_engine.py"] = {
        "lines": t.count("\n") + 1,
        "has_market_read": "FROM candles" in t,
        "has_state_progress": ("advance_position_trailing" in t),
        "has_order_call": ("def open_position" in t and "def close_at_price" in t),
        "purpose": "模拟执行：委托/成交/止损/退出/资金费/账本",
        "verdict": "可运行：03-B 已在隔离账本里跑通三条真实事件",
    }
    # ⑥ 冻结状态机：能不能表达甲乙丙
    t = (C.PROJECT / "策略" / "WyckoffMaster" / "event_trend_v0_1"
         / "state_machine.py").read_text(encoding="utf-8")
    out["state_machine.py（冻结包）"] = {
        "lines": t.count("\n") + 1,
        "transition_sites": t.count("self._make_transition("),
        "states_referenced": _count(t, "EventState.BOX", "EventState.SWEEP",
                                    "EventState.RETEST", "EventState.CANDIDATE",
                                    "EventState.INVALIDATED", "EventState.EXPIRED"),
        "purpose": "BOX→SWEEP(必须跌破下沿L)→RETEST→突破上沿",
        "verdict": ("不可直接表达甲乙丙：迁移条件写死在冻结包里，"
                    "而甲乙丙的起点与确认方式不同；改它 = 改冻结规则 ⇒ 本单不改"),
    }
    # ⑦ 生产循环：只跑四臂
    for name in ("paper_loop.py", "unified_runtime.py", "live_api.py"):
        t = _src(name)
        out[name] = {
            "lines": t.count("\n") + 1,
            "mentions_L_ARMS": t.count("L.ARMS"),
            "purpose": "生产挂钟循环 / 接口",
            "verdict": "只跑 3a–3d：研究臂 4a–4c 不在其循环与接口内（本单不改）",
        }
    return out


def runtime_probes() -> dict:
    import accum_pending as AP
    import accum_research as AR
    import live_store as L
    import observe_store as OS
    import research_15m as R

    probes = {}

    # 1) 三组入口的配置校验
    probes["accum_research.validate(4a)"] = {
        "ready": AR.validate("4a")["ready"],
        "pending_count": AR.validate("4a")["pending_count"],
        "auto_entry_allowed": AR.validate("4a")["auto_entry_allowed"],
        "can_open": AR.can_open_position("4a"),
    }
    # 2) 待决表自校验
    v = AP.validate()
    probes["accum_pending.validate"] = {"通过": v["通过"], "错误": v["错误"],
                                        "计数": v["计数"]}
    # 3) observe_store 能不能真的建档/迁移/终结（临时库，不动研究库）
    with tempfile.TemporaryDirectory() as td:
        st = OS.ObserveStore(Path(td) / "probe.sqlite3")
        iso = st.assert_isolated()
        ev = st.create_event(scheme="4a", symbol="PROBEUSDT", venue="binance",
                             strategy_version="probe", param_version="probe",
                             box_version_id="box_probe", structure_seen_ms=1,
                             confirmed_ms=1, expires_at_ms=2, state="BOX",
                             evidence={"k": 1}, now_ms=1)
        dup = st.create_event(scheme="4a", symbol="PROBEUSDT", venue="binance",
                              strategy_version="probe", param_version="probe",
                              box_version_id="box_probe", structure_seen_ms=1,
                              confirmed_ms=1, expires_at_ms=999, state="BOX",
                              now_ms=1)
        st.transition(ev["event_id"], "BOX", "SWEEP", "probe", 3, payload={"x": 1})
        st.add_candidate(ev["event_id"], scheme="4a", created_ms=3)
        st.close_event(ev["event_id"], "EXPIRED", "probe", 4)
        dump = st.dump_event(ev["event_id"])
        st.close()
        probes["observe_store（临时库）"] = {
            "assert_isolated": iso["isolated"],
            "create_event": ev["created"],
            "rescan_keeps_deadline": (dup["expires_at_ms"] == 2),
            "transitions": len(dump["transitions"]),
            "candidates": len(dump["candidates"]),
            "final_state": dump["event"]["state"],
        }
    # 4) 行情读取：三个周期都要能取到（冷+热）
    hot, cold = R.default_paths()
    rd = {}
    for tf in ("1h", "4h", "15m"):
        m = R.MergedReader(hot, cold, tf)
        bars = m.bars("BTCUSDT", C.ms_of("2026-08-01T00:00:00Z"),
                      C.ms_of("2026-08-08T00:00:00Z"))
        rd[tf] = {"bars": len(bars),
                  "first": C.iso(bars[0]["open_time"]) if bars else None,
                  "source_first": bars[0].get("source") if bars else None}
        m.close()
    probes["research_15m 三周期"] = rd
    # 5) 账本臂解析：研究臂 / 未知臂
    def _try(fn):
        try:
            return {"ok": True, "value": str(fn())}
        except Exception as exc:                        # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    probes["live_store.ledger_path(4a) 未设 PAPER_LEDGER_DIR"] = _try(
        lambda: L.ledger_path("4a"))
    probes["live_store.ledger_path(9z) 未知臂"] = _try(lambda: L.ledger_path("9z"))
    probes["live_store.ledger_path(3a) 生产臂"] = _try(lambda: L.ledger_path("3a"))
    probes["live_store.RESEARCH_ARMS"] = list(getattr(L, "RESEARCH_ARMS", []))
    probes["live_store.ARMS"] = list(L.ARMS)
    # 6) 接口层：研究臂能不能下单
    try:
        import live_api as LA
        r = LA.paper_order_post({"arm": "4a", "symbol": "BTCUSDT"})
        payload = r[0] if isinstance(r, tuple) else r
        probes["live_api.paper_order_post(arm=4a)"] = {
            "http": (r[1] if isinstance(r, tuple) else None),
            "missing_reason": (payload or {}).get("missing_reason"),
            "ok": (payload or {}).get("ok"),
        }
    except Exception as exc:                            # noqa: BLE001
        probes["live_api.paper_order_post(arm=4a)"] = {
            "error": type(exc).__name__, "message": str(exc)}
    return probes


def change_list() -> list:
    """最小修改清单（本单实际做了什么 / 没做什么）。"""
    return [
        {"file": "框架/backend/accum_early_entry.py", "kind": "新增",
         "what": ("配置闸门（36 个必填字段、无默认值）+ 1h/4h/15m 取数 + 甲乙丙观察状态机"
                  "+ 候选条件表 + 委托桥 + 隔离断言"),
         "why": "现有入口没有「甲乙丙的观察推进」与「未批准即阻塞」的实现",
         "applied": True},
        {"file": "框架/backend/live_store.py", "kind": "修改（3 处）",
         "what": ("① 新增 RESEARCH_ARMS=('4a','4b','4c')；"
                  "② ledger_path() 允许研究臂，但**强制**要求显式 PAPER_LEDGER_DIR，"
                  "否则拒绝（默认目录=生产）；③ 缺库提示里 ARMS.index(arm) 加保护，"
                  "避免研究臂抛 ValueError 而不是 LedgerUnavailable"),
         "why": ("实测阻塞：paper_engine.open_position(arm='4a') 在 live_store.ledger_path "
                 "抛 ValueError: 未知臂 '4a' ⇒ 不开这条口子就必须另写一套账本，"
                 "违反「不另建引擎」"),
         "applied": True},
        {"file": "框架/backend/observe_store.py", "kind": "未改",
         "what": "直接复用（scheme 列 + 隔离断言 + 事件/候选/迁移/缺口表）",
         "why": "已有能力足够，改它没有收益", "applied": False},
        {"file": "框架/backend/research_15m.py", "kind": "未改",
         "what": "用 MergedReader('1h') 直接取 1h；MergedMarket 只暴露 15m/4h 也不影响",
         "why": "不需要为 1h 改共享门面", "applied": False},
        {"file": "框架/backend/paper_engine.py", "kind": "未改",
         "what": "只调用 open_position / check_position_exit_on_bar / advance_position_trailing",
         "why": "经济规则与成交模型不在本单授权范围", "applied": False},
        {"file": "策略/WyckoffMaster/event_trend_v0_1/state_machine.py", "kind": "未改",
         "what": "只读复用箱体门槛参数与 ATR/ER 特征函数",
         "why": "改它 = 改冻结规则；甲乙丙在**研究侧**另建观察推进，不碰冻结包",
         "applied": False},
        {"file": "框架/backend/paper_loop.py / unified_runtime.py / live_api.py / api/*",
         "kind": "未改（协调项）",
         "what": "生产循环与 HTTP 接口只认 ARMS=3a–3d ⇒ 研究臂没有端点、前端不展示",
         "why": "本单不接前端、不改生产循环；已列入剩余阻塞",
         "applied": False},
    ]


def main() -> int:
    res = {
        "generated_utc": C.iso(int(__import__("time").time() * 1000)),
        "static": static_audit(),
        "runtime": runtime_probes(),
        "minimal_change_list": change_list(),
    }
    C.dump("01_入口普查.json", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
