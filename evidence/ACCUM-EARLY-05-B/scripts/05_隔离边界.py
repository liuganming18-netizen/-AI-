# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜05 隔离边界取证。

「写隔离」≠「零接触」：环境变量指着隔离目录，也可能有某处回落到生产库。
所以这里不看声明，只看**连接探针**（子进程里替换 `sqlite3.connect`，逐次记录
打开的是哪个库、是不是只读打开），再加生产指纹前后对照。

三条负向 + 四条正向，每条负向都配正向对照，避免「恒真判据」：

  X1 连接探针      生产四本账本 / 实时信号库上「可写打开」次数 == 0
  X2 闸门越界      往生产目录写放行闸门 ⇒ 必须被拒；往 runs/ 内写 ⇒ 必须成功
  X3 未知臂         `paper_conn("9z")` ⇒ ValueError；`paper_conn("4a")` 隔离下 ⇒ 成功
  X4 研究臂隔离     生产视图 ARMS 不含 4a/4b/4c；研究臂只能走 RESEARCH_ARMS
  X5 生产指纹      四本账本 + 实时信号库 sha 前后不变
  X6 账本落点      研究账本全部在 runs/ 下；生产账本目录里研究账本 == 0
  X7 正向对照      隔离目录里**确实**写了账本且有条数（否则「没写生产」可能只是「什么都没写」）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                            # noqa: E402

WORKER = Path(__file__).resolve().parent / "_worker05.py"
ISO_ROOT = C.RUNS / "05_isolation"
JOB = {"phase": "iso", "symbols": ["SOLUSDT"],
       "start": "2026-08-01T00:00:00Z", "end": "2026-08-20T00:00:00Z",
       "max_boxes": 2, "walk_days": 20, "legacy_4h_exit": False}

PROD_LEDGER_DIR = (C.PROJECT / "数据" / "paper").resolve()
PROD_LIVE = (C.BACKEND / "live" / "live_signals.sqlite3").resolve()
PROD_MARKET = (C.PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3").resolve()


def _classify(db: str, isolated_dir: Path) -> str:
    """把一次连接归到「生产账本 / 生产实时库 / 生产行情库 / 隔离目录 / 其它」。

    注意 `file:` 前缀：读取器用 `file:/path?mode=ro` 的 URI 形式打开，
    `Path("file:/x")` 会变成相对路径 ⇒ 必须先剥前缀，否则生产库会被误判成「其它」，
    「生产上 0 次可写」就成了「压根没匹配上」。
    """
    s = str(db)
    if s.startswith("file:"):
        s = s[len("file:"):]
    s = s.split("?")[0]
    try:
        p = Path(s).resolve()
    except Exception:                                          # noqa: BLE001
        return "unparsed"
    iso = isolated_dir.resolve()
    if p == PROD_LIVE:
        return "prod_live"
    if p == PROD_MARKET:
        return "prod_market"
    if p == PROD_LEDGER_DIR or PROD_LEDGER_DIR in p.parents:
        return "prod_ledger"
    if p == iso or iso in p.parents:
        return "isolated"
    return "other"


def _classifier_selfcheck(isolated_dir: Path) -> dict:
    """分类器自检（**不连库**，只喂字符串）。

    没有这一步，「生产上可写连接 = 0」可能只是分类器把生产路径都归成了「其它」。
    """
    iso = isolated_dir
    cases = [
        (str(PROD_LEDGER_DIR / "paper_3a.sqlite3"), "prod_ledger"),
        (f"file:{PROD_LEDGER_DIR / 'paper_3c.sqlite3'}?mode=ro", "prod_ledger"),
        (str(PROD_LIVE), "prod_live"),
        (f"file:{PROD_LIVE}?mode=ro", "prod_live"),
        (f"file:{PROD_MARKET}?mode=ro", "prod_market"),
        (str(PROD_MARKET), "prod_market"),
        (str(iso / "paper" / "paper_4a.sqlite3"), "isolated"),
        (f"file:{iso / 'paper' / 'paper_4b.sqlite3'}?mode=ro", "isolated"),
        ("/tmp/__不存在的库__.sqlite3", "other"),
    ]
    rows = [{"db": d, "want": w, "got": _classify(d, iso), "ok": _classify(d, iso) == w}
            for d, w in cases]
    return {"rows": rows, "ok": all(r["ok"] for r in rows)}


def main() -> int:
    run_id = C.iso(int(__import__("time").time() * 1000)).replace(":", "").replace("-", "")
    gate = ISO_ROOT / run_id
    gate.mkdir(parents=True, exist_ok=True)
    (ISO_ROOT / "LATEST.json").write_text(
        json.dumps({"run_id": run_id, "root": str(gate)}, ensure_ascii=False),
        encoding="utf-8")
    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "run_id": run_id, "run_dir": str(gate),
           "why_note": ("每次运行落在 runs/05_isolation/<运行号>/ 下，不删除任何既有目录："
                        "环境的安全删除守卫按单轮累计文件数拦截，删目录会让复跑莫名失败；"
                        "不删反而能留历史对照。"),
           "why": ("证明隔离运行没有以可写方式连生产库：用连接探针逐次记录打开方式，"
                   "再配生产指纹前后对照与三条负向用例（每条都带正向对照）。"),
           "prod_before": {"ledgers": {a: C.sha256_file(PROD_LEDGER_DIR / f"paper_{a}.sqlite3")
                                       for a in ("3a", "3b", "3c", "3d")},
                           "live": C.sha256_file(PROD_LIVE)},
           "prod_market_note": ("行情库 sha 必然变化（采集器实时写），不作为泄漏判据；"
                                "只统计本进程对它的打开方式")}

    # ── X1 连接探针：跑一次短作业，收集全部连接 ──────────────────────────
    C.write_gate(gate)
    res = C.run_worker(WORKER, JOB, gate, config=C.FIXTURE, fixture_ok=True,
                       extra_env={"ACCUM05_SPY": "1"})
    out["worker_json"] = str(gate / "worker_iso.json")
    (gate / "worker_iso.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    log = (res.get("conn") or {}).get("log") or []
    iso_dir = gate
    rows = []
    for e in log:
        rows.append({"db": e["db"], "readonly": e["readonly"],
                     "kind": _classify(e["db"], iso_dir)})
    by_kind = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], {"n": 0, "readonly": 0, "writable": 0})
        k["n"] += 1
        k["readonly" if r["readonly"] else "writable"] += 1

    # 违规定义（判严）：生产账本 / 实时信号库上出现**可写打开**即算泄漏
    viol = [r for r in rows if r["kind"] in ("prod_ledger", "prod_live") and not r["readonly"]]
    market_writable = [r for r in rows if r["kind"] == "prod_market" and not r["readonly"]]
    selfcheck = _classifier_selfcheck(iso_dir)
    out["x1_conn_probe"] = {
        "total": len(rows), "by_kind": by_kind,
        "prod_ledger_or_live_writable": len(viol),
        "prod_market_writable": len(market_writable),
        "isolated_writable": by_kind.get("isolated", {}).get("writable", 0),
        "violations": viol[:20],
        "classifier_selfcheck": selfcheck,
        "ok": (len(viol) == 0
               and by_kind.get("isolated", {}).get("writable", 0) > 0      # 正向对照
               and selfcheck["ok"]),                                       # 分类器真能认出生产路径
        "note": ("两个正向对照：① `isolated_writable > 0` 证明探针确实看得见可写打开；"
                 "② `classifier_selfcheck` 证明生产路径不会被归成「其它」而漏判"),
    }

    # ── X2 闸门越界 ───────────────────────────────────────────────────────
    import _common as CC
    # 负向：往生产账本目录写闸门
    prod_gate = PROD_LEDGER_DIR / "gate.json"
    had = prod_gate.exists()
    try:
        CC.write_gate(PROD_LEDGER_DIR)
        neg = {"raised": False, "type": None, "msg": None}
    except SystemExit as exc:
        neg = {"raised": True, "type": "SystemExit", "msg": str(exc)}
    neg["gate_written_into_prod"] = prod_gate.exists() and not had
    # 正向：往 runs/ 内写
    pos_dir = gate / "gate_pos"
    try:
        pos = CC.write_gate(pos_dir)
        pos_ok = (pos_dir / "gate.json").exists() and pos["inside_run_dir"]
    except SystemExit as exc:
        pos_ok, pos = False, {"error": str(exc)}
    out["x2_gate_boundary"] = {
        "negative": neg, "positive": pos, "positive_ok": bool(pos_ok),
        "ok": (neg["raised"] and not neg["gate_written_into_prod"] and bool(pos_ok)),
        "note": "只断言「被拒」不够：还要断言生产目录里**没有**多出 gate.json",
    }

    # ── X3 未知臂 ─────────────────────────────────────────────────────────
    os.environ.update({
        "PAPER_LEDGER_DIR": str(gate / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DB": str(gate / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(gate / "gate.json"),
        "MARKET_DB": str(PROD_MARKET),
        "WX_ARCHIVE_DIR": str(C.PROJECT / "数据" / "市场数据" / "parquet"),
        "WX_MARKET_MERGE": "1",
        "ACCUM05_FIXTURE_OK": "1",
        "ACCUM05_CONFIG": str(C.FIXTURE),
    })
    for k in ("ACCUM04_CONFIG", "ACCUM04_FIXTURE_OK"):
        os.environ.pop(k, None)

    import live_store as L
    try:
        L.paper_conn("9z")
        bad = {"raised": False}
    except Exception as exc:                                   # noqa: BLE001
        bad = {"raised": True, "type": type(exc).__name__, "msg": str(exc)}
    good = {"ok": False}
    try:
        con = L.paper_conn("4a", ro=True)
        n = con.execute("SELECT COUNT(*) FROM paper_position").fetchone()[0]
        con.close()
        good = {"ok": True, "positions": n,
                "path": str(L.ledger_path("4a"))}
    except Exception as exc:                                   # noqa: BLE001
        good = {"ok": False, "type": type(exc).__name__, "msg": str(exc)}
    out["x3_unknown_arm"] = {
        "negative": bad, "positive": good,
        "arms": list(L.ARMS), "research_arms": list(getattr(L, "RESEARCH_ARMS", ())),
        "ok": (bad.get("raised") is True and good.get("ok") is True),
        "note": "负向（未知臂被拒）必须配正向（已知研究臂在隔离目录里真能打开），否则拒得过头也看不出来",
    }

    # ── X4 研究臂不进生产视图 ─────────────────────────────────────────────
    out["x4_research_arms_not_in_production"] = {
        "ARMS": list(L.ARMS),
        "RESEARCH_ARMS": list(getattr(L, "RESEARCH_ARMS", ())),
        "overlap": sorted(set(L.ARMS) & set(getattr(L, "RESEARCH_ARMS", ()))),
        "ok": (not (set(L.ARMS) & set(getattr(L, "RESEARCH_ARMS", ())))
               and set(getattr(L, "RESEARCH_ARMS", ())) == {"4a", "4b", "4c"}),
    }

    # ── X5 / X6 生产指纹与账本落点 ────────────────────────────────────────
    prod_after = {"ledgers": {a: C.sha256_file(PROD_LEDGER_DIR / f"paper_{a}.sqlite3")
                              for a in ("3a", "3b", "3c", "3d")},
                  "live": C.sha256_file(PROD_LIVE)}
    leak = C.production_research_ledger_leak()
    run_ledgers = sorted(str(p) for p in C.RUNS.rglob("paper_4*.sqlite3"))
    out["x5_prod_fingerprint"] = {
        "ledgers_unchanged": {
            a: (out["prod_before"]["ledgers"][a].get("sha256")
                == prod_after["ledgers"][a].get("sha256")) for a in ("3a", "3b", "3c", "3d")},
        "live_unchanged": (out["prod_before"]["live"].get("sha256")
                           == prod_after["live"].get("sha256")),
    }
    out["x5_prod_fingerprint"]["ok"] = (
        all(out["x5_prod_fingerprint"]["ledgers_unchanged"].values())
        and out["x5_prod_fingerprint"]["live_unchanged"])
    out["x6_ledger_location"] = {
        "production_dir_research_ledgers": leak,
        "run_ledgers": run_ledgers,
        "run_ledgers_inside_runs": all(str(C.RUNS.resolve()) in p for p in run_ledgers),
        "prior_research_unchanged": {
            k: (v.get("sha256") == C.prior_research_fingerprint().get(k, {}).get("sha256"))
            for k, v in C.prior_research_fingerprint().items()},
    }
    out["x6_ledger_location"]["ok"] = (
        leak["count"] == 0 and out["x6_ledger_location"]["run_ledgers_inside_runs"]
        and all(out["x6_ledger_location"]["prior_research_unchanged"].values()))

    # ── X7 正向对照：隔离目录里确实写了账本 ───────────────────────────────
    files = {}
    for p in sorted((gate / "paper").glob("paper_*.sqlite3")):
        import sqlite3
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            n_pos = con.execute("SELECT COUNT(*) FROM paper_position").fetchone()[0]
            n_ord = con.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0]
        finally:
            con.close()
        files[p.name] = {"positions": n_pos, "orders": n_ord, "bytes": p.stat().st_size}
    total_pos = sum(v["positions"] for v in files.values())
    out["x7_isolated_ledgers_written"] = {
        "files": files, "total_positions": total_pos,
        "ok": (len(files) == 3 and total_pos > 0),
        "note": "三个研究臂各一本账本，且至少有一笔持仓 —— 否则「没写生产」只是「什么都没写」",
    }

    out["summary"] = {
        "x1_conn_probe": out["x1_conn_probe"]["ok"],
        "x2_gate_boundary": out["x2_gate_boundary"]["ok"],
        "x3_unknown_arm": out["x3_unknown_arm"]["ok"],
        "x4_research_arms": out["x4_research_arms_not_in_production"]["ok"],
        "x5_prod_fingerprint": out["x5_prod_fingerprint"]["ok"],
        "x6_ledger_location": out["x6_ledger_location"]["ok"],
        "x7_isolated_written": out["x7_isolated_ledgers_written"]["ok"],
    }
    out["summary"]["all_ok"] = all(out["summary"].values())

    C.dump("05_隔离边界.json", out)
    print(json.dumps({"summary": out["summary"],
                      "conn_by_kind": by_kind,
                      "market_writable_conns": len(market_writable)},
                     ensure_ascii=False, indent=1))
    return 0 if out["summary"]["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
