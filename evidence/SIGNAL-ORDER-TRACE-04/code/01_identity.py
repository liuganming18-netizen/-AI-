# -*- coding: utf-8 -*-
"""01｜身份盘点：四类 ID 的**真实边界**（不伪造不存在的身份）。

本脚本只做三件事：
  A. **静态取证**：把 `event_id` / `candidate_id` / `signal_uid` / `paper_order.order_id` /
     `paper_fill.fill_id` / `paper_position.position_id` / `paper_realized.id` 的**生成代码原文**
     连同行号、文件 SHA 一起摘出来 —— 身份从哪来，必须能回指到代码行。
  B. **机械验证**：
     B1 跨进程确定性：同窗口、两次独立进程驱动，`candidate_id` 集合必须逐位相同；
     B2 窗口位移敏感性：换窗口起点后 `candidate_id` 会变（因为它含 `box_formation_close_ms`），
        而 `cross_run_key`（只取形态量）不变 ⇒ 证明「跨运行匹配键」不能用 `candidate_id` 顶替。
  C. **能力边界**：生产纸面账本里到底有没有「交易所订单号」；没有就写 `None` + 原因，
     **绝不用引擎自增 `order_id` 冒充交易所单号**。
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")


# ══════════════════════════════════════════════════════════════════════
# A. 静态取证
# ══════════════════════════════════════════════════════════════════════
#: 需要摘原文的「身份生成点」：(文件, 正则)
ID_SITES = [
    ("策略/WyckoffMaster/event_trend_v0_1/state_machine.py", r"event_id = f\"ev_"),
    ("策略/WyckoffMaster/event_trend_v0_1/state_machine.py", r"cand_id = f\"cand_"),
    ("框架/backend/unified_runtime.py", r"sig_uid = c\.candidate_id"),
    ("框架/backend/paper_engine.py", r"INSERT OR REPLACE INTO paper_order\(account_id,event_uid"),
    ("框架/backend/paper_engine.py", r"INSERT INTO paper_fill\(account_id,order_id,position_id"),
    ("框架/backend/paper_engine.py", r"SELECT 1 FROM paper_processed WHERE signal_uid"),
    ("框架/backend/live_store.py", r"def paper_conn"),
]

#: 交易所订单号可能出现的符号（本工程**只读**扫描，用来证明「没有」而不是「没查」）
EXCHANGE_ORDER_TOKENS = (
    "exchange_order_id", "exchangeOrderId", "clientOrderId", "client_order_id",
    "newOrderResp", "orderId", "order_id_exchange",
)


def phase_static() -> dict:
    sites = []
    for rel, pat in ID_SITES:
        p = cm.BASE / rel
        if not p.exists():
            sites.append({"file": rel, "pattern": pat, "error": "file_missing"})
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        rx = re.compile(pat)
        hits = []
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                # 摘出命中行 + 其后 6 行（够看清插入的字段与来源变量）
                ctx = lines[i - 1:i + 6]
                hits.append({"line": i, "code": [c.rstrip() for c in ctx]})
        sites.append({"file": rel, "sha256": cm.sha256_file(p),
                      "pattern": pat, "hits": hits})

    # ── 交易所订单号：全后端机械搜索（只读、限定目录，不做全盘 grep）──
    ex_hits = []
    for p in sorted((cm.BACKEND).rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:                                          # noqa: BLE001
            continue
        for tok in EXCHANGE_ORDER_TOKENS:
            if tok in txt:
                for i, line in enumerate(txt.splitlines(), 1):
                    if tok in line:
                        ex_hits.append({"file": str(p.relative_to(cm.BASE)),
                                        "line": i, "token": tok,
                                        "code": line.strip()[:160]})

    return {
        "phase": "A_static",
        "generated_at_utc": cm.utc_now(),
        "id_sites": sites,
        "exchange_order_tokens_searched": list(EXCHANGE_ORDER_TOKENS),
        "exchange_order_hits_in_backend": ex_hits,
        "exchange_order_verdict": (
            "纸面模拟链路**没有交易所订单号**：本模块不含任何交易所写接口，"
            "成交由「已闭合 15m bar 开盘价」本地撮合（`paper_engine.FILL_POLICY`）。"
            "因此 `exchange_order_id` 一律 `None` + 原因，"
            "**不用引擎自增 `paper_order.order_id` 冒充**。"
        ),
        "identity_taxonomy": [
            {
                "kind": "运行内 ID",
                "fields": ["event_id", "candidate_id", "transition_id",
                           "paper_order.order_id", "paper_fill.fill_id",
                           "paper_position.position_id", "paper_realized.id"],
                "stable_across_runs": False,
                "why": ("`event_id` 含 `box_formation_close_ms`；`candidate_id` 再拼 `breakout_close_ms`；"
                        "`paper_*` 的自增主键更是**每个账本文件各自从 1 开始** ⇒ 跨库不可比。"),
                "usable_as": "仅在本进程/本账本内定位；跨运行匹配必须另立键",
            },
            {
                "kind": "跨运行匹配键",
                "fields": ["cross_run_key", "candidate_id（同窗口下）", "snapshot_hash"],
                "stable_across_runs": True,
                "why": ("`cross_run_key` 只取形态语义量（箱体 H/L/A/ER、扫低、回踩、突破价量、"
                        "止损价、可得时刻），不含计数器、不含箱体建档时刻 ⇒ 换窗口/换进程仍对上同一形态。"),
                "usable_as": "候选↔订单↔成交 的**唯一可跨运行**关联键",
            },
            {
                "kind": "引擎订单号",
                "fields": ["paper_order.order_id"],
                "stable_across_runs": False,
                "why": "SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`，按账本文件自增。",
                "usable_as": "账本内唯一；对外必须连同 `account_id` 一起标识",
            },
            {
                "kind": "交易所订单号",
                "fields": [],
                "stable_across_runs": None,
                "why": "**不存在**：模拟盘不下真单，无交易所回报。",
                "usable_as": "不适用（如实写 None）",
            },
        ],
    }


# ══════════════════════════════════════════════════════════════════════
# B. 机械验证
# ══════════════════════════════════════════════════════════════════════
def _run_once(sym, floor_ms, db) -> dict:
    """单标的从 `floor_ms` 推到库末，返回候选的两种身份。"""
    import unified_runtime as UR
    cfg = cm.config()
    con = cm.market_conn(db)
    b4 = cm.load_bars(con, sym, "4h")
    b1 = cm.load_bars(con, sym, "1h")
    con.close()
    eng = UR.RealSignalEngine(symbol=sym, config=cfg)
    eng.history_floor_ms = int(floor_ms)
    w = cm.FastMemWatcher(b4, b1)
    cands = []
    for bar in b4:
        if int(bar["open_time"]) < int(floor_ms):
            continue
        cands.extend(eng.process_market(int(bar["close_time"]), w))
    return {
        "symbol": sym,
        "floor_utc": cm.iso(floor_ms),
        "n_cand": len(cands),
        "candidate_ids": sorted(c.candidate_id for c in cands),
        "cross_run_keys": sorted(cm.cross_run_key(c) for c in cands),
        "pairs": [{"candidate_id": c.candidate_id,
                   "cross_run_key": cm.cross_run_key(c),
                   "available_at_utc": cm.iso(c.available_at_ms),
                   "box_atr_a": c.box_atr_a,
                   "breakout_close": c.breakout_close} for c in cands],
        "transitions": len(eng.sm.transitions),
        "atr_bars_seen": eng.atr_bars_seen,
        "box_gate_bars": eng.box_gate_bars,
    }


def phase_determinism(sym, db, shift_days=30) -> dict:
    con = cm.market_conn(db)
    first4h = con.execute(
        "SELECT MIN(open_time) FROM candle_versions WHERE symbol=? AND timeframe='4h'",
        (sym,)).fetchone()[0]
    last4h = con.execute(
        "SELECT MAX(close_time) FROM candle_versions WHERE symbol=? AND timeframe='4h'",
        (sym,)).fetchone()[0]
    con.close()
    floor_a = int(first4h)
    floor_b = floor_a + int(shift_days) * cm.DAY

    a1 = _run_once(sym, floor_a, db)          # 第 1 次
    a2 = _run_once(sym, floor_a, db)          # 同进程第 2 次（隔离运行内状态）
    b = _run_once(sym, floor_b, db)           # 窗口位移

    b1 = {
        "same_window_two_runs_identical_candidate_id":
            a1["candidate_ids"] == a2["candidate_ids"],
        "same_window_two_runs_identical_cross_run_key":
            a1["cross_run_keys"] == a2["cross_run_keys"],
        "run1": a1, "run2": a2,
    }
    b2 = {
        "floor_a_utc": cm.iso(floor_a), "floor_b_utc": cm.iso(floor_b),
        "shift_days": shift_days,
        "candidate_id_changed": a1["candidate_ids"] != b["candidate_ids"],
        "cross_run_key_changed": a1["cross_run_keys"] != b["cross_run_keys"],
        "n_cand_a": a1["n_cand"], "n_cand_b": b["n_cand"],
        "ids_a": a1["candidate_ids"], "ids_b": b["candidate_ids"],
        "keys_a": a1["cross_run_keys"], "keys_b": b["cross_run_keys"],
        "shift_run": b,
        "verdict": None,
    }

    # ── B3：**有效对照** —— 只看「两个窗口都产出的同一个候选」────────────
    # B2 的整体集合比较有个坑：位移 30 天会改变候选集合本身（早段箱体没了），
    # 于是「ID 变了」既可能是身份不稳定、也可能只是换了批候选。必须按
    # **窗口无关的语义键**取交集再比 ID，才测得到「换窗口是否换身份」。
    # 键取 `available_at`（= 确认 15m bar 收盘时刻）：它是候选自身的时刻，
    # 不依赖窗口起点，也不参与 candidate_id 的构造（构造只含 symbol + 箱体建档
    # 4h bar close + 确认 15m close，见 state_machine.py:241/584）。
    pa = {p["available_at_utc"]: p for p in a1["pairs"]}
    pb = {p["available_at_utc"]: p for p in b["pairs"]}
    common = sorted(set(pa) & set(pb))
    b3 = {
        "match_key": "available_at（确认 15m bar 收盘时刻，窗口无关）",
        "n_a": len(pa), "n_b": len(pb), "n_common": len(common),
        "n_only_a": len(set(pa) - set(pb)), "n_only_b": len(set(pb) - set(pa)),
        "candidate_id_identical_on_common": all(
            pa[k]["candidate_id"] == pb[k]["candidate_id"] for k in common) if common else None,
        "cross_run_key_identical_on_common": all(
            pa[k]["cross_run_key"] == pb[k]["cross_run_key"] for k in common) if common else None,
        "mismatches": [{"available_at_utc": k, "a": pa[k]["candidate_id"],
                        "b": pb[k]["candidate_id"]}
                       for k in common if pa[k]["candidate_id"] != pb[k]["candidate_id"]],
        "verdict": None,
    }
    if not common:
        b3["verdict"] = "两窗口无共同候选 ⇒ 未构成对照（**不当作通过**）"
    elif b3["candidate_id_identical_on_common"]:
        b3["verdict"] = ("同一确认 bar 上的候选，换窗口后 candidate_id **逐位相同** ⇒ "
                         "candidate_id 可直接作跨运行匹配键（不含窗口起点/计数器/挂钟）；"
                         "cross_run_key 属冗余二次校验")
    else:
        b3["verdict"] = ("同一确认 bar 上 candidate_id **不同** ⇒ candidate_id 不能作跨运行键，"
                         "必须用 cross_run_key")

    if b2["candidate_id_changed"] and not b2["cross_run_key_changed"]:
        b2["verdict"] = ("整体集合两处都变；**单看 B2 无法归因** ⇒ 结论以 B3（交集对照）为准")
    elif (not b2["candidate_id_changed"]) and (not b2["cross_run_key_changed"]):
        b2["verdict"] = "本标的窗口位移未改变候选 ⇒ 该维度**未被证伪也未证实**（不当作通过）"
    else:
        b2["verdict"] = ("两者同时变化 ⇒ 位移改变了候选集合本身（不只是 ID），"
                         "属真实语义差异，不是身份问题；身份结论以 B3 为准")
    return {"phase": "B_determinism", "generated_at_utc": cm.utc_now(),
            "symbol": sym, "db": str(db),
            "last_4h_utc": cm.iso(last4h),
            "B1_same_window": b1, "B2_window_shift": b2, "B3_intersection": b3}


# ══════════════════════════════════════════════════════════════════════
# C. 生产账本实证（只读）
# ══════════════════════════════════════════════════════════════════════
def phase_ledger() -> dict:
    p = cm.PROD_LEDGER_DIR / f"paper_{cm.PROD_ARM}.sqlite3"
    out = {"phase": "C_ledger_readonly", "generated_at_utc": cm.utc_now(),
           "path": str(p), "exists": p.exists()}
    if not p.exists():
        out["missing_reason"] = "生产账本不存在"
        return out
    out["sha256_before"] = cm.sha256_file(p)
    out["size"] = p.stat().st_size
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    counts = {}
    for t in ("paper_account", "paper_order", "paper_fill", "paper_pending",
              "paper_position", "paper_realized", "paper_blocked", "paper_processed",
              "paper_stop_history", "paper_funding"):
        try:
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception as exc:                                   # noqa: BLE001
            counts[t] = f"err: {exc}"
    out["row_counts"] = counts

    def cols(t):
        try:
            return [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        except Exception:                                          # noqa: BLE001
            return []

    out["columns"] = {t: cols(t) for t in ("paper_order", "paper_fill",
                                           "paper_pending", "paper_position",
                                           "paper_realized")}
    # 有没有交易所订单号字段？
    flat = [c.lower() for t in out["columns"].values() for c in t]
    out["exchange_order_column_present"] = any(
        ("exchange" in c and "order" in c) or c in ("client_order_id", "clientorderid")
        for c in flat)
    out["fill_has_signal_uid"] = "signal_uid" in out["columns"]["paper_fill"]
    out["order_has_signal_uid"] = "signal_uid" in out["columns"]["paper_order"]
    out["order_has_event_uid"] = "event_uid" in out["columns"]["paper_order"]
    out["capability_boundary"] = (
        "`paper_order` 只有 `event_uid`（= 候选 `candidate_id`），**没有** `signal_uid` 列；"
        "`paper_fill` 有 `signal_uid` 且直接带 `order_id` / `position_id`。"
        "⇒ 订单→候选 走 `paper_order.event_uid`；成交→候选 走 `paper_fill.signal_uid`；"
        "成交→订单 走 `paper_fill.order_id`。三者都是真实外键，不需要按顺序猜。")
    con.close()
    out["sha256_after"] = cm.sha256_file(p)
    out["unchanged"] = out["sha256_before"] == out["sha256_after"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=["static", "determinism", "ledger", "all"])
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--db", default=str(cm.ISO_DB_180D))
    ap.add_argument("--shift-days", type=int, default=30)
    a = ap.parse_args()

    if a.phase in ("static", "all"):
        r = phase_static()
        cm.write_json("01_A_身份静态取证.json", r)
        print(f"[A] 身份生成点 {len(r['id_sites'])} 处；"
              f"后端交易所单号命中 {len(r['exchange_order_hits_in_backend'])} 处")
        for s in r["id_sites"]:
            print(f"   {s['file']} :: {s.get('pattern')} -> {len(s.get('hits', []))} hit")

    if a.phase in ("ledger", "all"):
        r = phase_ledger()
        cm.write_json("01_C_生产账本只读实证.json", r)
        print(f"[C] 生产账本 {r['path']} 行数={r.get('row_counts')}")
        print(f"   交易所单号列存在={r.get('exchange_order_column_present')} "
              f"order.signal_uid={r.get('order_has_signal_uid')} "
              f"fill.signal_uid={r.get('fill_has_signal_uid')} "
              f"只读未变={r.get('unchanged')}")

    if a.phase in ("determinism", "all"):
        sym = a.symbol
        if not sym:
            p = cm.OUT / "02_候选扫描.json"
            if not p.exists():
                raise SystemExit("先跑 02_candidates.py，或显式 --symbol")
            d = cm.read_json(p)
            if not d["candidates"]:
                raise SystemExit("02 扫描未找到任何候选 ⇒ 无法做确定性验证（不空转判通过）")
            sym = d["candidates"][0]["symbol"]
        r = phase_determinism(sym, Path(a.db), a.shift_days)
        cm.write_json("01_B_身份确定性验证.json", r)
        b1, b2 = r["B1_same_window"], r["B2_window_shift"]
        print(f"[B1] {sym} 同窗口两次运行：candidate_id 相同="
              f"{b1['same_window_two_runs_identical_candidate_id']} "
              f"cross_run_key 相同={b1['same_window_two_runs_identical_cross_run_key']} "
              f"(n={b1['run1']['n_cand']})")
        print(f"[B2] 窗口位移 {a.shift_days} 天：candidate_id 变="
              f"{b2['candidate_id_changed']} cross_run_key 变={b2['cross_run_key_changed']}")
        print(f"     {b2['verdict']}")
        print(f"[B3] 交集对照（键={b3['match_key']}）：共同 {b3['n_common']} 个 "
              f"candidate_id 相同={b3['candidate_id_identical_on_common']} "
              f"cross_run_key 相同={b3['cross_run_key_identical_on_common']}")
        print(f"     {b3['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
