#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-UI-LINK-07-R1 探针⑤：生产历史明细核对 + 三方对账（要求 4）

核对清单（用户原话逐条对应）
----------------------------
  1. 「数量与明细一致」    —— `history_candidates_discarded` == `len(discarded_detail)`
                             == `discarded_detail_n`，且 `discarded_detail_consistent`
  2. 「能查 ID/时间/不可执行原因」
                          —— 每条都有非空 `candidate_id`、`event_id`、`snapshot_utc`、
                             `execution_earliest_utc`、`discard_reasons`（且 reason 属于
                             已知枚举，不是随手写的字符串）
  3. 「不进当前可执行列表」—— `scope=current` 的 ID 集合与这 51 条 ID 集合交集为空；
                             生产 `live_signal` 表 0 行
  4. 「不补下过期订单」    —— 这 51 个 ID 在**四本账本**里都不存在任何订单/成交
  5. 三方对账              —— 库（0 行）/ 接口（0 条）/ 状态接口（51 条明细）自洽

只读：接口 GET + 三个 sqlite 以 `mode=ro` 打开。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
OUT = HERE.parents[1] / "out"
API = "http://127.0.0.1:8787"
PROD_LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
LEDGERS = {a: ROOT / f"数据/paper/paper_3{a}.sqlite3" for a in ("a", "b", "c", "d")}

KNOWN_REASONS = {"before_paper_epoch_no_backfill", "missed_fill_moment_no_backfill"}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with _opener.open(API + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def ro(db: Path):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def main():
    out = {
        "probe": "SIGNAL-UI-LINK-07-R1/60_history_reconcile",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "checks": [],
        "ok": False,
    }

    def chk(name, ok, detail):
        out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"{'[OK]' if ok else '[FAIL]'} {name} — {detail}")

    # ── 生产接口三态 ──────────────────────────────────────────────────────────
    cur = get("/api/v1/signals?scope=current&limit=500")
    allq = get("/api/v1/signals?scope=all&limit=500")
    hist = get("/api/v1/signals?scope=history&limit=500")
    st = get("/api/v1/paper/status")

    se = (st.get("item") or {}).get("signal_engine") or {}
    bs = se.get("bootstrap") or {}
    detail = bs.get("discarded_detail") or []
    n_cnt = bs.get("history_candidates_discarded")
    n_det = bs.get("discarded_detail_n")

    out["production"] = {
        "signals_current": {"total": cur.get("total"),
                            "ids": [i.get("signal_id") for i in (cur.get("items") or [])],
                            "quality_status": (cur.get("quality") or {}).get("status"),
                            "missing_reason": cur.get("missing_reason"),
                            "note_len": len((cur.get("quality") or {}).get("note") or "")},
        "signals_all": {"total": allq.get("total")},
        "signals_history": {"total": hist.get("total")},
        "bootstrap": {
            "history_candidates_discarded": n_cnt,
            "discarded_detail_n": n_det,
            "discarded_detail_len": len(detail),
            "discarded_detail_consistent": bs.get("discarded_detail_consistent"),
            "recovered_candidates": bs.get("recovered_candidates"),
            "recovery_gate": bs.get("recovery_gate"),
        },
        "funnel": se.get("funnel"),
        "runtime_identity": (st.get("item") or {}).get("runtime_identity"),
    }

    # ── 1. 数量与明细一致 ─────────────────────────────────────────────────────
    chk("① 数量与明细一致",
        n_cnt == len(detail) == n_det and bs.get("discarded_detail_consistent") is True,
        f"计数={n_cnt} 明细={len(detail)} 明细计数={n_det} "
        f"自洽标记={bs.get('discarded_detail_consistent')}")

    ids = [d.get("candidate_id") for d in detail]
    chk("①b 明细 ID 无重复",
        len(ids) == len(set(ids)),
        f"{len(ids)} 条 / 去重 {len(set(ids))} 个")

    # ── 2. 每条能查 ID / 时间 / 原因 ──────────────────────────────────────────
    bad_id = [d for d in detail if not (d.get("candidate_id") and d.get("event_id"))]
    bad_time = [d for d in detail
                if not (d.get("snapshot_utc") and d.get("execution_earliest_utc"))]
    bad_reason = [d for d in detail
                  if not d.get("discard_reasons")
                  or not set(d["discard_reasons"]).issubset(KNOWN_REASONS)]
    chk("② 每条都有候选 ID + 事件 ID", not bad_id, f"缺失 {len(bad_id)} 条")
    chk("②b 每条都有确认时间 + 最早可执行时间", not bad_time, f"缺失 {len(bad_time)} 条")
    chk("②c 每条都有不可执行原因，且属于已知枚举", not bad_reason,
        f"异常 {len(bad_reason)} 条；枚举={sorted(KNOWN_REASONS)}")

    reason_hist = {}
    for d in detail:
        for r in (d.get("discard_reasons") or []):
            reason_hist[r] = reason_hist.get(r, 0) + 1
    out["reason_histogram"] = reason_hist

    # 每条原因的语义自洽：before_paper_epoch ⇒ available_at < epoch
    ep = int((cur.get("quality") or {}).get("paper_epoch_ms")
             or (st.get("quality") or {}).get("paper_epoch_ms") or 0)
    out["paper_epoch_ms"] = ep
    mis_epoch = []
    if ep:
        for d in detail:
            r = d.get("discard_reasons") or []
            av = d.get("available_at_utc")
            if "before_paper_epoch_no_backfill" in r and av:
                t = int(dt.datetime.strptime(av, "%Y-%m-%dT%H:%M:%SZ")
                        .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
                if t >= ep:
                    mis_epoch.append(d.get("candidate_id"))
    chk("②d 原因与实际时间自洽（before_paper_epoch ⇒ 确认时间早于启用时点）",
        not mis_epoch,
        f"不一致 {len(mis_epoch)} 条" + (f"：{mis_epoch[:2]}" if mis_epoch else ""))

    # ── 3. 不进当前可执行列表 ─────────────────────────────────────────────────
    con = ro(PROD_LIVE_DB)
    try:
        live_rows = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        live_ids = [r[0] for r in con.execute("SELECT signal_uid FROM live_signal")]
    finally:
        con.close()
    cur_ids = set(i.get("signal_id") for i in (cur.get("items") or []))
    out["production"]["live_db_rows"] = live_rows
    chk("③ 生产实时库 0 行", live_rows == 0, f"{live_rows} 行")
    chk("③b 当前列表 0 条", cur.get("total") == 0 and not cur_ids,
        f"total={cur.get('total')}")
    chk("③c 51 条历史明细与当前列表交集为空",
        not (set(ids) & cur_ids) and not (set(ids) & set(live_ids)),
        f"交集 {len(set(ids) & cur_ids)} 个")

    # ── 4. 不补下过期订单 ─────────────────────────────────────────────────────
    ledger_report = {}
    hit_ids = []
    for arm, p in LEDGERS.items():
        item = {"path": str(p.relative_to(ROOT)), "exists": p.exists()}
        if p.exists():
            c = ro(p)
            try:
                tables = [r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")]
                item["tables"] = tables
                n_ord = 0
                for t in ("orders", "trades", "positions", "fills", "paper_order",
                          "paper_trade", "paper_position"):
                    if t in tables:
                        cnt = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        item[f"{t}_rows"] = cnt
                        n_ord += cnt
                        # 有没有任何一行引用了这 51 个 ID
                        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
                        for col in cols:
                            if col in ("signal_id", "signal_uid", "candidate_id"):
                                q = (f"SELECT COUNT(*) FROM {t} WHERE {col} IN "
                                     f"({','.join('?' * len(ids))})")
                                n = c.execute(q, ids).fetchone()[0]
                                if n:
                                    hit_ids.append({"arm": arm, "table": t,
                                                    "column": col, "n": n})
                item["total_order_like_rows"] = n_ord
            finally:
                c.close()
        ledger_report[arm] = item
    out["ledgers"] = ledger_report
    chk("④ 四本账本里没有任何一行引用这 51 个候选 ID",
        not hit_ids, f"命中 {len(hit_ids)} 处" + (f"：{hit_ids[:3]}" if hit_ids else ""))

    # ── 5. 三方对账 ───────────────────────────────────────────────────────────
    chk("⑤ 库(0 行) / 当前接口(0 条) / 状态接口(51 条明细) 自洽",
        live_rows == 0 and cur.get("total") == 0 and len(detail) == n_cnt,
        f"库={live_rows} 当前={cur.get('total')} 明细={len(detail)}")
    chk("⑤b 正常空列表不写故障字段（status=ok 且 missing_reason 为空）",
        (cur.get("quality") or {}).get("status") == "ok"
        and not cur.get("missing_reason"),
        f"status={(cur.get('quality') or {}).get('status')} "
        f"missing_reason={cur.get('missing_reason')} "
        f"note长度={len((cur.get('quality') or {}).get('note') or '')}")

    out["sample_detail"] = detail[:3]
    out["all_ids"] = ids
    out["ok"] = all(c["ok"] for c in out["checks"])

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "60_history_reconcile.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n== 结果：{'全部通过' if out['ok'] else '存在失败项'} ==")
    print(f"写出：{p}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
