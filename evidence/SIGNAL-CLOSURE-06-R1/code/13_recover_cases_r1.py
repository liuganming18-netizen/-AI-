# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜13 候选恢复验证（修正 06 的失效案例，四条路径分开）。

## 06 的 `06_recovered_candidate_execution.py` 错在哪

  · S2 声称验证「停机期间候选正常恢复并挂单」，实际候选
    `exec_earliest = 2026-10-02T12:15Z` **早于** `PAPER_EPOCH_MS`（10-14T01:00Z）
    ⇒ 订单被 `before_paper_epoch_no_backfill` 拒掉，**根本没进挂单**；
  · S2b 为了让成交链路跑通，把 `epoch_ms` 前移到 `Tc2 - 24h`
    ⇒ **平移了生产启用时点**，违反工单「生产启用时点不得挪动」。

所以 06 的 `verdict=PASS` 不可信。本脚本重做，**一条路径一个结论**。

## 四条路径（工单原文）

  ① 过期历史候选：拒绝且不下单
  ② 启用时点之后、停机期间产生且恢复时仍有效：正常恢复，按规则等待成交或执行
  ③ 同一候选重复恢复：不重复登记、不重复下单
  ④ 恢复后新增候选：正常进入执行链

## 前提（19 号实测）

`out/19_真实历史候选清单.json`：真实历史候选 **51 个**，
`exec_earliest >= PAPER_EPOCH_MS` 的 **0 个**，`Tr` 时刻仍满足恢复闸门的 **0 个**；
最新一个候选是 `2026-10-11T20:00Z`，比启用时点还早 **3.2 天**。

⇒ ② 和 ④ **没有真实样本**，只能记 `UNVERIFIED`。
   **不允许**像 06 的 S2b 那样平移时间轴去凑一个「成功」。

## 严格不做的事

  · `PAPER_EPOCH_MS` 一字节不动（从 `live_store` 读，不写字面量）；
  · 不放宽任何门禁；不新增门槛；
  · 不碰生产库/生产账本（全部在 `out/13_iso/` 下）。

用法：python code/13_recover_cases_r1.py --upto 2026-10-25T04:59:59Z
产出：out/13_候选恢复四路径.json、out/13_逐案记录.csv
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
ISO = OUT / "13_iso"
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT

# ── 生产行情路径必须在覆盖 env 之前取（覆盖后 `project_paths` 会指向隔离库）──
import project_paths as PP            # noqa: E402
PROD_MARKET = str(PP.MARKET_DB)
PROD_ARCHIVE = str(PP.MARKET_ARCHIVE)
print(f"[prod] MARKET_DB={PROD_MARKET}")
print(f"[prod] MARKET_ARCHIVE={PROD_ARCHIVE}")

H1 = cm.H1


def ledger_counts(ledger_dir: Path):
    """直接读隔离账本，不经过后端（避免被后端缓存骗）。"""
    import sqlite3
    out = {"paper_order": 0, "paper_position": 0, "paper_processed": 0,
           "rejects": {}, "signal_uids": []}
    p = ledger_dir / "paper_ledger.sqlite3"
    if not p.exists():
        cand = sorted(ledger_dir.glob("*.sqlite3"))
        if not cand:
            return out
        p = cand[0]
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        def cnt(t):
            try:
                return con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                return 0
        out["paper_order"] = cnt("paper_order")
        out["paper_position"] = cnt("paper_position")
        out["paper_processed"] = cnt("paper_processed")
        try:
            for r in con.execute("SELECT COALESCE(reject_reason,'(null)'), COUNT(*) "
                                 "FROM paper_order GROUP BY 1"):
                out["rejects"][r[0]] = r[1]
        except Exception:
            pass
        try:
            out["signal_uids"] = [r[0] for r in con.execute(
                "SELECT DISTINCT signal_uid FROM paper_order "
                "WHERE signal_uid IS NOT NULL")]
        except Exception:
            pass
    finally:
        con.close()
    return out


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
    T1 = cm.parse_utc_ms(upto_s)

    # ── 真实候选清单（19 号产物，不重新构造）──────────────────────────────
    # 必须在覆盖 env **之前**读行情：`load_hist` 走生产 `MARKET_DB`（只读）。
    cand_file = OUT / "19_真实历史候选清单.json"
    if not cand_file.exists():
        print("[FATAL] 缺 19_真实历史候选清单.json，先跑 19 号")
        return 2
    cj = json.loads(cand_file.read_text(encoding="utf-8"))
    cands = cj["candidates"]
    n_after = cj["n_after_paper_epoch"]
    n_rec = cj["n_recoverable_at_Tr"]
    print(f"[cands] 真实候选 {len(cands)} 个；epoch 之后 {n_after} 个；"
          f"Tr 时刻可恢复 {n_rec} 个；最新 {cands[-1]['snapshot_utc']}")

    case = cands[-1]          # 最新那个真实候选（离启用时点最近）
    SYMS = sorted({c["symbol"] for c in cands[-3:]})
    print(f"[case] 主案例 {case['symbol']} candidate_id={case['candidate_id']}")
    print(f"       snapshot={case['snapshot_utc']} exec_earliest={case['exec_earliest_utc']} "
          f"（epoch 之后={case['after_paper_epoch']}）")
    Tc = int(case["snapshot_ms"])
    up_to_case = Tc + 20 * 60_000      # 重启在候选后 20 分钟 ⇒ 仍在成交时效内

    # ── 隔离环境：**先**建目录并设 LIVE_DB / 账本，再读行情 ─────────────────
    # 踩过：`load_hist` 会 `import live_store`，若在它之后才设 `LIVE_DB`，
    # `live_store.LIVE_DB` 已被钉成生产路径 ⇒ 隔离形同虚设。
    # 而行情（`MARKET_DB`）必须保持生产只读，直到 `build_iso_market` 把隔离库建好。
    if ISO.exists():
        shutil.rmtree(ISO)
    for d in ("ledger", "locks", "batches", "runs", "market"):
        (ISO / d).mkdir(parents=True)
    os.environ["PAPER_LEDGER_DIR"] = str(ISO / "ledger")
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["LIVE_DB"] = str(ISO / "live.sqlite3")
    os.environ["PAPER_GATE_FILE"] = str(ISO / "paper_gate.json")
    os.environ["EVENT_TREND_BATCH_ROOT"] = str(ISO / "batches")
    os.environ["RUNS_DIR"] = str(ISO / "runs")
    os.environ["WX_HISTORY_SOURCE"] = "hot"
    os.environ["no_proxy"] = "*"

    hist, _ = cm.load_hist(SYMS, T1, read_days=200, keep_from_ms=T1 - 2880 * H1)
    print(f"[hist] 真实行情已装入内存（币数 {len(SYMS)}），来源=生产库 {PROD_MARKET}")

    # 行情也切到隔离库（只读引用生产库的拷贝，避免后端写生产行情）
    os.environ["MARKET_DB"] = str(ISO / "market" / "market.sqlite3")
    os.environ["MARKET_ARCHIVE"] = PROD_ARCHIVE

    # ── 隔离行情库：从生产库拷 schema，再灌真实历史（不改生产库）───────────
    # `build_iso_market` 只用标准库 sqlite3，不 import 后端 ⇒ 可放在后端 import 之前。
    m06 = importlib.import_module("06_recovered_candidate_execution")
    m06.PROD_MARKET = PROD_MARKET       # 让它的 build_iso_market 从生产库拷
    m06.ISO = ISO
    m06.build_iso_market(SYMS, hist, T1, tf15_days=70)
    print(f"[iso] 隔离行情库就绪：{ISO / 'market' / 'market.sqlite3'}")

    # ── 后端 import 必须在隔离行情库建好之后 ──────────────────────────────
    import live_store as L                      # noqa: E402
    import paper_engine as PE                   # noqa: E402
    import unified_runtime as UR                # noqa: E402

    PAPER_EPOCH_MS = int(L.PAPER_EPOCH_MS)      # 生产值，一字节不动
    MAX_FILL_LAG_MS = int(PE.MAX_FILL_LAG_MS)
    print(f"[cfg] PAPER_EPOCH_MS={cm.utc(PAPER_EPOCH_MS)}  "
          f"MAX_FILL_LAG_MS={MAX_FILL_LAG_MS}")
    print(f"[cfg] 隔离目录={ISO}")
    print(f"[cfg] LIVE_DB(实际)={L.LIVE_DB}")

    # ── 观测探针：只**记录** `open_position` 的返回值，不改任何行为 ──────────
    # `route_pending_orders` 只把拒绝计数成 `rejected`，不暴露原因；
    # 工单要求「保留原因」，所以在这里把真实 reason 抓下来。
    _CAPTURED = []
    _orig_open = PE.open_position

    def _probe_open(*a, **kw):
        r = _orig_open(*a, **kw)
        try:
            _CAPTURED.append({"signal_uid": kw.get("signal_uid"),
                              "ok": r.get("ok"), "reason": r.get("reason"),
                              "status": r.get("status")})
        except Exception:                                  # noqa: BLE001
            pass
        return r

    PE.open_position = _probe_open

    results, rows = {}, []

    def scenario(name, upto_ms, note):
        rt = UR.UnifiedRuntime(arm="3a", symbols=SYMS, poll_interval_sec=1.0,
                               lock_dir=ISO / "locks")
        se = rt.signal_engine
        se.history_provider = cm.MemProvider(hist)
        se.watcher = cm.MemPoolWatcher(hist, SYMS)
        se.replay_ms = 2880 * H1
        se.state_anchor_ms = None
        se.history_floor_ms = se.resolve_replay_floor(int(upto_ms))
        se.history_provider.set_upto(int(upto_ms))
        se.watcher.set_now(int(upto_ms))
        se.prepare_history(int(upto_ms), log=lambda *a: None)
        se.bootstrap(int(upto_ms), log=lambda *a: None)
        # **补 step(upto)**：bootstrap 只按 4h 收盘驱动，会漏最后一根 4h 之后的 1h
        se.watcher.set_now(int(upto_ms))
        se.step(int(upto_ms), log=None)
        bs = dict(se.bootstrap_stats)
        _CAPTURED.clear()
        rec = rt._route_recovered_candidates(now_ms=int(upto_ms))
        calls = list(_CAPTURED)
        lc = ledger_counts(ISO / "ledger")
        snap = {"scenario": name, "note": note,
                "upto_utc": cm.utc(upto_ms),
                "bootstrap_recovered": bs.get("recovered_candidates"),
                "bootstrap_discarded": bs.get("history_candidates_discarded"),
                "recovered_detail": bs.get("recovered_detail"),
                "route": rec,
                "open_position_calls": calls,
                "reject_reasons": [c.get("reason") for c in calls],
                "ledger": lc}
        results[name] = snap
        rows.append({"scenario": name, "upto_utc": snap["upto_utc"],
                     "bootstrap_recovered": snap["bootstrap_recovered"],
                     "bootstrap_discarded": snap["bootstrap_discarded"],
                     "route_registered": (rec or {}).get("registered"),
                     "route_rejected": (rec or {}).get("rejected"),
                     "paper_order": lc["paper_order"],
                     "paper_position": lc["paper_position"],
                     "rejects": json.dumps(lc["rejects"], ensure_ascii=False),
                     "note": note})
        print(f"  [{name}] recovered={snap['bootstrap_recovered']} "
              f"discarded={snap['bootstrap_discarded']} "
              f"registered={(rec or {}).get('registered')} "
              f"rejected={(rec or {}).get('rejected')} "
              f"order={lc['paper_order']} pos={lc['paper_position']} "
              f"rejects={lc['rejects']}")
        return snap

    # ── ① 过期历史候选：拒绝且不下单 ───────────────────────────────────────
    a1 = scenario("A_过期历史候选_拒绝且不下单", up_to_case,
                  "真实候选（早于启用时点）+ 重启时刻仍在成交时效内 ⇒ "
                  "应进恢复池，但被既有 epoch 闸门拒绝，且不产生持仓")
    # 路由层有时会因 `_scan_actionable` 的 origin/状态过滤而**根本不进下单入口**
    # （隔离库刚建、live_signal 只有这一条）。为了拿到**真实拒绝原因**，
    # 这里直接调生产唯一的下单入口 `PE.open_position`（`route_pending_orders`
    # 内部也是调它，参数完全一致）—— 门禁一个字没改，只是把候选喂到闸门前。
    ep = PE.open_position(
        "3a", case["symbol"], signal_uid=case["candidate_id"], direction="long",
        now_ms=up_to_case, event_time=int(case["snapshot_ms"]),
        available_at=int(case["available_ms"]), requested_at=up_to_case,
        origin="recovered")
    epoch_probe = {"ok": ep.get("ok"), "reason": ep.get("reason"),
                   "status": ep.get("status"),
                   "now_utc": cm.utc(up_to_case),
                   "signal_uid": case["candidate_id"]}
    a1["direct_open_position"] = epoch_probe
    a1["ledger_after_direct"] = ledger_counts(ISO / "ledger")
    print(f"  [A/直接下单入口] ok={ep.get('ok')} reason={ep.get('reason')} "
          f"status={ep.get('status')}")
    # ── ③ 同一候选重复恢复：不重复登记、不重复下单 ─────────────────────────
    before = ledger_counts(ISO / "ledger")
    scenario("C_同一候选重复恢复", up_to_case,
             "与 A 完全相同的重启时刻再跑一次 ⇒ 订单/持仓行数不得增加")
    after = ledger_counts(ISO / "ledger")
    idem = {"order_before": before["paper_order"], "order_after": after["paper_order"],
            "pos_before": before["paper_position"], "pos_after": after["paper_position"],
            "n_signal_uid_before": len(before["signal_uids"]),
            "n_signal_uid_after": len(after["signal_uids"]),
            "no_duplicate_order": before["paper_order"] == after["paper_order"],
            "no_duplicate_position": before["paper_position"] == after["paper_position"]}

    # ── ② ④ 无真实样本 ────────────────────────────────────────────────────
    b_note = (f"epoch 之后真实候选 {n_after} 个、Tr 时刻可恢复 {n_rec} 个 ⇒ 无真实样本。"
              f"最新真实候选 {cands[-1]['snapshot_utc']} 早于启用时点 "
              f"{cm.utc(PAPER_EPOCH_MS)}。按纪律记 UNVERIFIED，不平移时间轴。")
    results["B_停机期间仍有效候选"] = {"verdict": "UNVERIFIED", "note": b_note}
    results["D_恢复后新增候选"] = {"verdict": "UNVERIFIED", "note": b_note}
    for nm in ("B_停机期间仍有效候选", "D_恢复后新增候选"):
        rows.append({"scenario": nm, "upto_utc": None, "bootstrap_recovered": None,
                     "bootstrap_discarded": None, "route_registered": None,
                     "route_rejected": None, "paper_order": None,
                     "paper_position": None, "rejects": None, "note": b_note})

    # ── 生产门禁未被放宽（源码级自证）──────────────────────────────────────
    import inspect
    src_route = inspect.getsource(UR.UnifiedRuntime._route_recovered_candidates)
    gate_check = {
        "route_passes_epoch_ms": ("epoch_ms" in src_route),
        "paper_epoch_unchanged": PAPER_EPOCH_MS == 1_791_939_600_000,
        "route_source_lines": len(src_route.splitlines()),
        "note": ("`_route_recovered_candidates` 源码里若出现 `epoch_ms` 就说明调用方在"
                 "回填时钟；生产路径不允许。"),
    }

    # ── 判定 ──────────────────────────────────────────────────────────────
    rec_a = a1["bootstrap_recovered"] or 0
    lc_a = a1.get("ledger_after_direct") or a1["ledger"]
    reasons_a = [a1.get("direct_open_position", {}).get("reason")] + \
                (a1.get("reject_reasons") or [])
    # 「过期历史候选被既有 epoch 闸门拒绝」= 真拿到这个原因，而不是靠推断
    epoch_rejected = any(r == "before_paper_epoch_no_backfill" for r in reasons_a)
    verdicts = {
        "A_过期历史候选_拒绝且不下单": (
            "PASS" if (rec_a >= 1 and epoch_rejected
                       and lc_a["paper_order"] == 0 and lc_a["paper_position"] == 0)
            else "FAIL"),
        "C_同一候选重复恢复": (
            "PASS" if (idem["no_duplicate_order"] and idem["no_duplicate_position"]
                       and idem["n_signal_uid_before"] == idem["n_signal_uid_after"])
            else "FAIL"),
        "B_停机期间仍有效候选": "UNVERIFIED",
        "D_恢复后新增候选": "UNVERIFIED",
        "生产门禁未放宽": ("PASS" if (not gate_check["route_passes_epoch_ms"]
                                      and gate_check["paper_epoch_unchanged"]) else "FAIL"),
    }
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(T1),
        "paper_epoch_utc": cm.utc(PAPER_EPOCH_MS),
        "max_fill_lag_ms": MAX_FILL_LAG_MS,
        "iso_dir": str(ISO),
        "prod_market_db_untouched": PROD_MARKET,
        "symbols": SYMS, "main_case": case,
        "candidates_source": str(cand_file),
        "n_real_candidates": len(cands),
        "n_after_paper_epoch": n_after,
        "n_recoverable_at_Tr": n_rec,
        "scenarios": results,
        "idempotency": idem,
        "gate_check": gate_check,
        "verdicts": verdicts,
        "verdict_note": ("A/C 用真实候选与真实行情验证；B/D 因「epoch 之后 0 个真实候选」"
                         "记 UNVERIFIED。**没有**通过平移启用时点或放宽门禁制造成功案例。"),
    }
    cm.dump("13_候选恢复四路径.json", out)
    cm.dump_csv("13_逐案记录.csv", rows)
    print("\n[verdicts]")
    for k, v in verdicts.items():
        print(f"  {k:26s} {v}")
    print(f"  {out['verdict_note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
