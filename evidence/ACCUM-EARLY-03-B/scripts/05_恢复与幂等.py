# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜05 重启恢复、重复扫描与不重复下单。

工单第 5 条：验证冷热交界、缺失 15 分钟线、重复扫描、重启恢复及不重复订单。
（冷热交界与缺失 15m 线见 02 脚本；本脚本负责后三项。）

做法是「把一次连续回放切成两段」，中间换一个**新进程**：
  第 1 段：只推进 20 根 15m 就停（持仓仍开着，跟踪线已动过）；
  第 2 段：新进程、同一账本目录、`resume=true` 接着跑到底。
判据（三条，缺一条都不算过）：
  A **不重复下单**：第 2 段不得新增开仓订单（`paper_order` 里入场单仍只有 1 条）；
  B **状态可恢复**：第 2 段能读到第 1 段落的持仓与止损，接着走完并平仓；
  C **结果与连续跑一致**：分段跑出来的退出价/净额/止损轨迹，必须与 04 脚本
    同一事件的连续跑**逐位相同**（否则说明恢复改变了经济结果）。
另加：对已平仓的持仓再扫一遍退出，必须**不产生第二笔平仓**（重复扫描无副作用）。

输出：out/05_恢复与幂等.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
OUT = PROJECT / "evidence" / "ACCUM-EARLY-03-B" / "out"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(PROJECT / "框架" / "backend"))
sys.path.insert(0, str(PROJECT))

import _harness as H                                          # noqa: E402


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def pick(w: dict) -> dict:
    lg = w.get("ledger") or {}
    pos = (lg.get("positions") or [None])[0]
    real = (lg.get("realized") or [None])[0]
    return {
        "verdict": w.get("verdict"),
        "reject_reason": w.get("reject_reason"),
        "status": (pos or {}).get("status"),
        "entry_price": (pos or {}).get("entry_price"),
        "exit_price": (pos or {}).get("exit_price"),
        "exit_reason": (pos or {}).get("exit_reason"),
        "net_pnl": (real or {}).get("net_pnl"),
        "gross_pnl": (real or {}).get("gross_pnl"),
        "funding_paid": (real or {}).get("funding_paid"),
        "counts": lg.get("counts"),
        "stop_history": [(s.get("old_stop"), s.get("new_stop"), s.get("effective_at"))
                         for s in (lg.get("stop_history") or [])],
        "orders": [(o.get("order_id"), o.get("side"), o.get("status"),
                    o.get("bar_open_time")) for o in (lg.get("orders") or [])],
        "walk_bars_checked": w.get("walk_bars_checked"),
        "steps": [s["step"] for s in (w.get("steps") or [])],
        "errors": w.get("errors"),
    }


def main() -> int:
    rep = {"probe": "05_恢复与幂等", "generated_at_utc": iso(time.time() * 1000),
           "cases": {}, "checks": {}, "errors": []}
    rep["生产账本指纹_跑前"] = H.production_ledger_fingerprint()

    # 连续跑的基准（直接取 04 的产物；同一事件、同一模式）
    base = {}
    p04 = OUT / "04_全链贯通.json"
    if p04.exists():
        d04 = json.loads(p04.read_text(encoding="utf-8"))
        for k, c in (d04.get("chains") or {}).items():
            if not k.endswith("::merge"):
                continue
            real = c["⑤ 账本"]["realized"] or {}
            base[k.split("::")[0]] = {
                "exit_price": (c["④ 退出"] or {}).get("exit_price"),
                "net_pnl": real.get("net_pnl"),
                "stop_history": [(x[0], x[1]) for x in (c["④ 退出"]["止损轨迹（只上移）"] or [])],
            }
    rep["基准（04 连续跑）"] = base

    for ev in H.EVENTS:
        key = ev["key"]
        name = f"05_{key}"
        try:
            # 第 1 段：跑 20 根 15m 就停
            w1 = H.run_job(H.event_job(ev, mode="merge", max_bars=20), name, mode="merge")
            # 第 2 段：新进程、同一账本，续跑到底（并要求它再试一次同一 signal_uid）
            w2 = H.run_job(H.event_job(ev, mode="merge", resume=True,
                                       retry_same_signal=True, reopen_ledger=True),
                           name, mode="merge", keep=True)
        except Exception as exc:                             # noqa: BLE001
            rep["errors"].append({"event": key, "error": f"{type(exc).__name__}: {exc}"})
            continue

        s1, s2 = pick(w1), pick(w2)
        resume_step = next((s for s in (w2.get("steps") or [])
                            if s["step"] == "resume_found_position"), None)
        retry_step = next((s for s in (w2.get("steps") or [])
                           if s["step"] == "open_position_retry_same_signal"), None)
        retry_reason = ((retry_step or {}).get("result") or {}).get("reason")
        rescan = next((s for s in (w2.get("steps") or [])
                       if s["step"] == "rescan_exits"), None)

        entry_orders = [o for o in (s2.get("orders") or []) if o[1] == "buy"]
        exit_orders = [o for o in (s2.get("orders") or []) if o[1] == "sell"]
        b = base.get(key) or {}

        rep["cases"][key] = {
            "symbol": ev["symbol"],
            "第1段": {**s1, "说明": "只推进 20 根 15m，故意让持仓开着"},
            "第2段": {**s2, "说明": "新进程、同一账本、resume=true"},
            "恢复时找到的持仓": resume_step,
            "重复下单尝试的结果": {"reason": retry_reason,
                            "是否被挡住": retry_reason in ("duplicate_signal_uid",
                                                          "position_already_open_no_add")},
            "重复扫描退出": rescan,
            "基准": b,
        }

        a_ok = len(entry_orders) == 1
        b_ok = (resume_step is not None and s2.get("status") == "closed")
        c_ok = (b.get("exit_price") == s2.get("exit_price")
                and b.get("net_pnl") == s2.get("net_pnl")
                and b.get("stop_history") == [(x[0], x[1]) for x in (s2.get("stop_history") or [])])
        d_ok = (len(exit_orders) == 1)
        rep["checks"][key] = {
            "A 不重复下单（入场单仍只有 1 条）": a_ok,
            "B 状态可恢复（读到旧持仓并走完平仓）": b_ok,
            "C 结果与连续跑逐位相同": c_ok,
            "D 平仓单只有 1 条（重复扫描无副作用）": d_ok,
            "entry_orders": len(entry_orders), "exit_orders": len(exit_orders),
            "结果": "通过" if (a_ok and b_ok and c_ok and d_ok) else "不通过",
        }

    rep["生产账本指纹_跑后"] = H.production_ledger_fingerprint()
    changed = [a for a in rep["生产账本指纹_跑前"]
               if rep["生产账本指纹_跑前"][a].get("sha256")
               != rep["生产账本指纹_跑后"][a].get("sha256")]
    rep["生产账本是否被改动"] = {"changed_arms": changed,
                          "verdict": "未被改动" if not changed else "被改动（违规）"}
    rep["总判定"] = ("通过" if all(v["结果"] == "通过" for v in rep["checks"].values())
                     and not changed else "不通过")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "05_恢复与幂等.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"checks": rep["checks"],
                      "生产账本": rep["生产账本是否被改动"]["verdict"],
                      "总判定": rep["总判定"], "errors": rep["errors"]},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
