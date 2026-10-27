# -*- coding: utf-8 -*-
"""60b｜下单防护「五层闸门」单因素矩阵 —— 工作进程（隔离沙箱内跑真实代码）。

为什么单开一个进程
------------------
要在**进程内**用 SQL 写探针证明「没写生产库」，就必须在 import 业务模块**之前**装探针；
而且矩阵要调用真实 `SignalPersistenceAndRouter.route_pending_orders()` 与
`paper_engine.open_position()`（**生产同一份代码**），只有在隔离 env 下跑才安全。

矩阵口径（严格按工单）
----------------------
`_scan_actionable` 的实际谓词有 6 条（交易所/市场/周期算一条「身份三元组」）：
  C1 `executable_state='enterable'`
  C2 `json_extract(payload_json,'$.role')=ARM_ENTRY_ROLE`
  C3 `json_extract(payload_json,'$.arm')=arm`
  C4 `confirmed_at IS NOT NULL AND available_at IS NOT NULL`
  C5 `origin=?`（**仅当调用方传 origin 才生效**）
  C6 身份三元组 `exchange/market/timeframe`

每格只改一件事：
  · 「单因素移除」= 从真实研究行出发，只让**一层**变通过，其余仍全部不通过
    ⇒ 期望仍不被扫描（证明该层单独就能挡住）；
  · 「仅保留」= 从「全层失效」出发，只让**一层**恢复不通过
    ⇒ 期望仍不被扫描（证明该层单独就是必要的）；
  · 「全层失效」= 全部改通过 ⇒ 期望被扫描（证明实验装置真的能开仓，不是空跑）。

三态记录（每格都记，不合并）：
  `被扫描到`（`_scan_actionable` 结果里有没有这条 uid）
  `登记为挂单`（`paper_pending` 里有没有这条 uid）
  `生成模拟开仓`（`paper_position` 里有没有这条 uid）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

PROJECT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT / "框架" / "backend"
for p in (str(BACKEND), str(PROJECT / "框架"), str(PROJECT),
          str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import probe_sql  # noqa: E402

probe_sql.install()          # 必须早于业务模块 import

import live_store as L  # noqa: E402
import unified_runtime as UR  # noqa: E402
import paper_engine as PE  # noqa: E402

ARM = "3a"
ACCOUNT = f"paper43_{ARM}"
SYNTH_PREFIX = "m09_"
ROW_COLS: set = set()          # `live_signal` 的真实列名（运行时从样本行推断）


def build_cells(base: dict) -> list:
    """14 个格子。`over` 只改一个维度；`caller` 是调用方传给 `_scan_actionable` 的 origin。"""
    C1_ok = {"executable_state": "enterable"}
    C2_ok = {"role": UR.ARM_ENTRY_ROLE}
    C3_ok = {"arm": ARM}
    C4_ok = {"available_at": base["confirmed_at"]}
    C5_ok = {"origin": "live"}
    C6_ok = {"exchange": UR.EXCHANGE, "market": UR.MARKET, "timeframe": UR.TIMEFRAME}

    all_ok = {}
    for d in (C1_ok, C2_ok, C3_ok, C4_ok, C5_ok, C6_ok):
        all_ok.update(d)

    def cell(key, label, over, caller=None, note=""):
        return {"key": key, "label": label, "over": over, "caller": caller, "note": note}

    cells = [
        cell("A", "全层有效（原始研究行，未改任何字段）", {}, None,
             "基准：研究行原样进扫描"),
        cell("B", "单因素移除 C1 可执行态", C1_ok, None,
             "只把 executable_state 改成 enterable，其余仍不通过"),
        cell("C", "单因素移除 C2 事件角色", C2_ok, None,
             "只把 payload.role 改成 enterable_arm_entry"),
        cell("D", "单因素移除 C3 所属臂", C3_ok, None,
             "只把 payload.arm 改成 3a"),
        cell("E", "单因素移除 C4 时间齐备", C4_ok, None,
             "只把 available_at 补成 confirmed_at"),
        cell("F", "单因素移除 C6 身份三元组", C6_ok, None,
             "只把 exchange/market/timeframe 对齐（研究行本已对齐 ⇒ 该层对它不构成阻挡）"),
        cell("G", "单因素移除 C5 来源（调用方不传 origin）", C5_ok, None,
             "origin 改 live；但调用方不传 origin ⇒ C5 根本不参与 ⇒ 仍不应被扫描"),
        cell("H", "仅保留 C1（其余全通过）", {}, None,
             "先全通过再只让 C1 不通过"),
        cell("I", "仅保留 C2（其余全通过）", {}, None, ""),
        cell("J", "仅保留 C3（其余全通过）", {}, None, ""),
        cell("K", "仅保留 C4（其余全通过）", {}, None, ""),
        cell("L", "全层失效（危险对照）", dict(all_ok), None,
             "六层全部通过 ⇒ 应被扫描；这是「装置真的能开仓」的对照"),
        cell("M", "仅保留 C5（调用方传 origin=live）", dict(all_ok, origin="historical_research"),
             "live", "其余全通过，只让 C5 不通过 ⇒ 应被 C5 挡住"),
        cell("N", "全层失效 + 调用方传 origin=live", dict(all_ok), "live",
             "C5 也参与且通过 ⇒ 应被扫描"),
    ]
    # 把「仅保留」格子补成：全通过 + 单独把某层改回不通过
    breaks = {
        "H": {"executable_state": base["executable_state"]},
        "I": {"role": base["role"]},
        "J": {"arm": base["arm"]},
        "K": {"available_at": None},
    }
    for c in cells:
        if c["key"] in breaks:
            over = dict(all_ok)
            over.update(breaks[c["key"]])
            c["over"] = over
            c["note"] = c["note"] or f"全通过后只把 {c['key']} 对应那层改回不通过"
    return cells


def make_row(base: dict, cell: dict) -> dict:
    row = {k: v for k, v in base.items() if k in ROW_COLS}
    row["signal_uid"] = SYNTH_PREFIX + cell["key"]
    payload = json.loads(base["payload_json"])
    for k, v in cell["over"].items():
        if k == "role":
            payload["role"] = v
        elif k == "arm":
            payload["arm"] = v
        elif k == "origin":
            payload["origin"] = v
            row["origin"] = v
        elif k == "available_at":
            row["available_at"] = v
            payload.setdefault("timestamps", {})["available_at_ms"] = v
        else:
            row[k] = v
    row["payload_json"] = json.dumps(payload, ensure_ascii=False)
    return row


def wipe(con_live, con_paper, uid: str) -> None:
    con_live.execute("DELETE FROM live_signal WHERE signal_uid LIKE ?", (SYNTH_PREFIX + "%",))
    for t in ("paper_pending", "paper_order", "paper_processed", "paper_blocked",
              "paper_position", "paper_prompt"):
        try:
            con_paper.execute(f"DELETE FROM {t} WHERE signal_uid=?", (uid,))
        except sqlite3.OperationalError:
            pass
    con_live.commit()
    con_paper.commit()


def insert(con_live, row: dict) -> None:
    cols = list(row.keys())
    con_live.execute(
        f"INSERT OR REPLACE INTO live_signal({','.join(cols)}) "
        f"VALUES({','.join('?' * len(cols))})", tuple(row[c] for c in cols))
    con_live.commit()


def ledger_state(con_paper, uid: str) -> dict:
    """三态取数。`paper_order` / `paper_blocked` 的键是 `event_uid`（不是 `signal_uid`），
    取错列会抛 `no such column` 并被误读成「没有记录」—— 这里两列都试，如实记录用哪列。"""
    out = {}
    for t in ("paper_pending", "paper_processed", "paper_position"):
        try:
            out[t] = con_paper.execute(
                f"SELECT COUNT(*) FROM {t} WHERE signal_uid=?", (uid,)).fetchone()[0]
        except sqlite3.OperationalError as exc:
            out[t] = None
            out[f"{t}_error"] = str(exc)
    for t in ("paper_order", "paper_blocked"):
        try:
            out[t] = con_paper.execute(
                f"SELECT COUNT(*) FROM {t} WHERE event_uid=?", (uid,)).fetchone()[0]
        except sqlite3.OperationalError as exc:
            out[t] = None
            out[f"{t}_error"] = str(exc)
    try:
        out["paper_order_reject_reasons"] = [r[0] for r in con_paper.execute(
            "SELECT DISTINCT reject_reason FROM paper_order WHERE event_uid=?", (uid,))]
    except sqlite3.OperationalError:
        out["paper_order_reject_reasons"] = None
    try:
        rows = list(con_paper.execute(
            "SELECT action, detail FROM paper_processed WHERE signal_uid=?", (uid,)))
        out["paper_processed_detail"] = [
            {"action": a, "detail": (json.loads(d) if d else None)} for a, d in rows]
    except (sqlite3.OperationalError, ValueError):
        out["paper_processed_detail"] = None
    return out


def main() -> int:
    out = {"LIVE_DB": str(L.LIVE_DB), "PAPER_DIR": str(L.PAPER_DIR),
           "GATE": PE.new_entry_gate(), "cells": [], "errors": []}
    con_live = sqlite3.connect(str(L.LIVE_DB), timeout=15)
    con_live.row_factory = sqlite3.Row
    try:
        # 取**确认时刻最晚**的研究行作基准：更早的行会被 `before_paper_epoch_no_backfill`
        # 终态拒单（那是另一道闸），会把「登记为挂单」这一态掩盖掉。
        base = dict(con_live.execute(
            "SELECT * FROM live_signal WHERE origin='historical_research' "
            "ORDER BY confirmed_at DESC, signal_uid LIMIT 1").fetchone())
    finally:
        pass
    base_payload = json.loads(base["payload_json"])
    base = {"base_uid": base["signal_uid"],
            "role": base_payload.get("role"), "arm": base_payload.get("arm")} | base
    global ROW_COLS
    ROW_COLS = set(base.keys()) - {"base_uid", "role", "arm"}

    router = UR.SignalPersistenceAndRouter(arm=ARM)
    con_paper = L.paper_conn(ARM)
    try:
        for cell in build_cells(base):
            row = make_row(base, cell)
            uid = row["signal_uid"]
            wipe(con_live, con_paper, uid)
            insert(con_live, row)
            rec = {"key": cell["key"], "label": cell["label"], "note": cell["note"],
                   "uid": uid, "caller_origin": cell["caller"],
                   "改动": cell["over"]}
            try:
                scanned = router._scan_actionable(con_live, cell["caller"])
                rec["被扫描到"] = any(s["signal_uid"] == uid for s in scanned)
                rec["扫描命中条数"] = len(scanned)
                now = int(time.time() * 1000)
                st = router.route_pending_orders(now, origin=cell["caller"])
                rec["route_stats"] = st
                rec["ledger"] = ledger_state(con_paper, uid)
                rec["登记为挂单"] = bool(rec["ledger"].get("paper_pending"))
                rec["生成模拟开仓"] = bool(rec["ledger"].get("paper_position"))
            except Exception as exc:                      # noqa: BLE001
                rec["异常"] = f"{type(exc).__name__}: {exc}"
                out["errors"].append({"cell": cell["key"], "error": rec["异常"]})
            out["cells"].append(rec)
        wipe(con_live, con_paper, SYNTH_PREFIX + "__cleanup__")
    finally:
        con_paper.close()
        con_live.close()

    out["probe"] = probe_sql.report()
    print("@@MATRIX@@" + json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
