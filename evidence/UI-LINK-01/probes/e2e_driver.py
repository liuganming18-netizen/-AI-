#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-LINK-01 · 隔离环境端到端驱动（只动 /tmp/ui-link-01）

分工与边界（必须如实记录，否则就是造假）：
  · 界面操作（选信号 / 点开多 / 看挂单 / 看持仓 / 切总闸）由 `e2e_ui.js` 用真实浏览器完成。
  · 挂单**成交**在真实系统里由常驻 worker 的进程时钟推进（`advance_pending()`）。
    隔离环境没有常驻 worker，本脚本用**同一个引擎函数** `PE.advance_pending(T=earliest_fill_ms)`
    推进一次 —— 这是把「等 15 分钟真实挂钟」压缩成一次显式时钟注入，
    函数本身、成交价规则（最早可成交 15m 闭合 bar 开盘价）、拒单不补旧价全部不变。
    因此本脚本产出的成交**不等于**「真实挂钟自动成交」，结论里必须写明这一点。
  · 止损推进 / 平仓走真实 HTTP 接口（`/paper/advance_trailing`、`/paper/orders` action=close）。

用法：
  python3 e2e_driver.py snapshot
  python3 e2e_driver.py advance-pending
  python3 e2e_driver.py advance-trailing [arm] [position_id]
  python3 e2e_driver.py close [arm] [position_id]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from iso_backend import iso_env, ISO, ROOT, BACKEND  # noqa: E402

BASE = "http://127.0.0.1:8799"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    try:
        r = _OP.open(BASE + path, timeout=30)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None
    except Exception as e:
        return "ERR", str(e)


def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        r = _OP.open(req, timeout=60)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None
    except Exception as e:
        return "ERR", str(e)


def snapshot():
    _, ac = get("/api/v1/paper/accounts")
    _, pos = get("/api/v1/paper/positions")
    _, st = get("/api/v1/paper/status")
    out = {
        "accounts": [],
        "positions": [],
        "gate": ((ac or {}).get("quality") or {}).get("new_entry_gate") if isinstance(ac, dict) else None,
        "n_pending_orders": ((ac or {}).get("quality") or {}).get("n_pending_orders") if isinstance(ac, dict) else None,
        "process": ((st or {}).get("item") or {}) if isinstance(st, dict) else None,
    }
    for it in ((ac or {}).get("items") or []):
        out["accounts"].append({
            "arm": it.get("arm"), "equity": it.get("equity"),
            "n_closed": it.get("n_closed"), "n_orders": it.get("n_orders"),
            "n_pending": it.get("n_pending"),
            "realized_pnl_total": it.get("realized_pnl_total"),
            "pending": it.get("pending"),
            "positions": it.get("positions"),
        })
    for it in ((pos or {}).get("items") or []):
        out["positions"].append(it)
    return out


def pending_rows():
    """直接读隔离账本里的挂单表（只读），拿到 earliest_fill_ms。"""
    import sqlite3
    rows = []
    for arm in ("3a", "3b", "3c", "3d"):
        db = ISO / "paper" / f"paper_{arm}.sqlite3"
        if not db.exists():
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            for r in con.execute(
                "SELECT pending_id, arm, symbol, direction, origin, status, reason, "
                "earliest_fill_ms, requested_at, signal_uid FROM paper_pending "
                "WHERE status='pending' ORDER BY pending_id"
            ).fetchall():
                rows.append(dict(r))
        finally:
            con.close()
    return rows


def advance_pending():
    import os
    os.environ.update(iso_env())
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(ROOT / "框架"))
    import paper_engine as PE

    rows = pending_rows()
    if not rows:
        return {"error": "没有待成交挂单"}
    earliest = min(int(r["earliest_fill_ms"] or 0) for r in rows)

    # 成交 bar 必须「已闭合」且「发现时刻 - bar.open_time <= MAX_FILL_LAG_MS(1h)」。
    # 起点取**真实当前时刻所在的那一格**（而不是 earliest_fill 那一格）：
    # 这样注入的 T 不超过真实挂钟，后续止损推进/平仓用真实 now 时不会出现
    # 「决策时刻早于成交时刻」的倒挂。
    import time as _time
    Q = 15 * 60 * 1000
    now_ms = int(_time.time() * 1000)
    base = max(((earliest + Q - 1) // Q) * Q, (now_ms // Q) * Q)
    attempts = []
    res = None
    used_T = None
    for k in range(0, 5):
        T = base + k * Q
        r = PE.advance_pending(T=T, log=lambda *a, **k2: None)
        attempts.append({"T_ms": T,
                         "T_utc": _utc(T),
                         "filled": len(r.get("filled") or []),
                         "rejected": len(r.get("rejected") or []),
                         "still_pending": r.get("still_pending")})
        if (r.get("filled") or []):
            res, used_T = r, T
            break
        res = r
    before = None
    after = snapshot()
    return {
        "clock_injection": {
            "T_ms": used_T,
            "T_utc": _utc(used_T) if used_T else None,
            "why": ("隔离环境无常驻 worker，用同一引擎函数按「15m 逐格推进到该 bar 闭合」推一次；"
                    "成交价规则（最早可成交 15m 闭合 bar 开盘价）与拒单不补旧价逻辑完全不变。"
                    "结论中不得写成「真实挂钟自动成交」。"),
            "attempts": attempts,
        },
        "pending_before": rows,
        "advance_result": res,
        "positions_after": after["positions"],
        "accounts_after": after["accounts"],
    }


def _utc(ms):
    import datetime
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "snapshot"

    if action == "snapshot":
        print(json.dumps({"snapshot": snapshot(), "pending_rows": pending_rows()},
                         ensure_ascii=False, indent=1, default=str))
        return 0

    if action == "advance-pending":
        print(json.dumps(advance_pending(), ensure_ascii=False, indent=1, default=str))
        return 0

    if action == "advance-trailing":
        arm = sys.argv[2] if len(sys.argv) > 2 else "3a"
        pid = sys.argv[3] if len(sys.argv) > 3 else None
        payload = {"arm": arm}
        if pid:
            payload["position_id"] = int(pid)
        code, body = post("/api/v1/paper/advance_trailing", payload)
        print(json.dumps({"http": code, "request": payload,
                          "quality": (body or {}).get("quality"),
                          "missing_reason": (body or {}).get("missing_reason"),
                          "items": (body or {}).get("items"),
                          "snapshot_after": snapshot()}, ensure_ascii=False, indent=1, default=str))
        return 0

    if action == "close":
        arm = sys.argv[2] if len(sys.argv) > 2 else "3a"
        pid = sys.argv[3] if len(sys.argv) > 3 else None
        if not pid:
            pos = snapshot()["positions"]
            if not pos:
                print(json.dumps({"error": "没有持仓可平"}, ensure_ascii=False))
                return 1
            pid = pos[0].get("position_id") or pos[0].get("pos_id") or pos[0].get("id")
        code, body = post("/api/v1/paper/orders",
                          {"action": "close", "arm": arm, "position_id": int(pid)})
        print(json.dumps({"http": code, "request": {"arm": arm, "position_id": int(pid)},
                          "quality": (body or {}).get("quality"),
                          "missing_reason": (body or {}).get("missing_reason"),
                          "items": (body or {}).get("items"),
                          "snapshot_after": snapshot()}, ensure_ascii=False, indent=1, default=str))
        return 0

    print(json.dumps({"error": "未知动作，可用：snapshot/advance-pending/advance-trailing/close"},
                     ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main())
