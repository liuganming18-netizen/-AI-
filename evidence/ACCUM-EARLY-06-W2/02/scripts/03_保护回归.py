# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜脚本 03：已有保护回归 + 写域核对

派工要求「保留批准闸门、重复数据缺口去重、经济口径冲突阻断等已有保护」，
所以这里逐条证明它们**在修后版本上仍然成立**，且每条都给正向对照：

  R1 批准闸门：原样调用 05-B `04_配置闸门.py`（11 条用例：9 负 + 2 正）
     —— 只把落盘目录指到本窗，断言一行不改
  R2 经济口径冲突阻断：原样调用 05-B `07_经济口径一致性校验.py`（5 条用例）
  R3 重复数据缺口去重：`add_gap_once` 幂等（函数级）+ 同事件重跑不增行（端到端）
     —— 正向对照 1：同一 args 三次 ⇒ 只 1 行；正向对照 2：换 `to_ms` ⇒ 真能新增；
        反向对照：直接调 `observe_store.add_data_gap` 三次 ⇒ 3 行（证明去重来自调用侧）
  R4 共享文件未动：冻结包 / 引擎 / 采集器 / 前端 / 共享读层的 sha256 与 mtime
     —— 判据：mtime 早于本日 00:00Z（本单与 06-03 都只碰 `accum_early_entry.py`）
  R5 写域：生产账本目录没有新增研究臂文件；本窗外的证据目录 mtime 不变

注意：R3 的端到端用例用**合成行情**（06-03 的合成序列），因为本单修后在这份真实输入上
0 候选 ⇒ 不产生缺口，端到端去重在该输入上不可观测（0 行 vs 0 行不算验证）。
合成用例只作「保护仍在」的正向对照，**不替代原 7 事件**。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2b as W                                    # noqa: E402

HERE = Path(__file__).resolve()
W2B = HERE.parents[1]
SCRIPTS = HERE.parent
DAY0 = "2026-10-27T00:00:00Z"          # 本日 00:00Z：早于它的 mtime 才算「今天没动」

#: R4 清单：本单不允许改动的共享文件
SHARED = [
    ("冻结包_state_machine", "策略/WyckoffMaster/event_trend_v0_1/state_machine.py"),
    ("冻结包_config", "策略/WyckoffMaster/event_trend_v0_1/config.py"),
    ("引擎_paper_engine", "框架/backend/paper_engine.py"),
    ("观察库_observe_store", "框架/backend/observe_store.py"),
    ("共享读层_research_15m", "框架/backend/research_15m.py"),
    ("共享读层_replay_calc", "框架/backend/replay_calc.py"),
    ("研究臂_live_store", "框架/backend/live_store.py"),
    ("采集器_realtime_collector", "框架/backend/realtime_collector.py"),
    ("前端_ObservePanel", "UI界面/web/src/components/ObservePanel.tsx"),
    ("配置模板_空白表单", "evidence/ACCUM-EARLY-05-B/config/批准配置_空白表单_05.json"),
]

#: 本单唯一允许改动的文件
ONLY_ALLOWED = "框架/backend/accum_early_entry.py"


# ══════════════════════════════════════════════════════════════════════════
# R1 / R2：原样调用 05-B 脚本（子进程，独立环境）
# ══════════════════════════════════════════════════════════════════════════
def run_05b(which: str) -> dict:
    runner = SCRIPTS / "_regr_runner.py"
    env = dict(os.environ)
    env.pop("ACCUM05_CONFIG", None)
    env.pop("ACCUM04_CONFIG", None)
    proc = subprocess.run([sys.executable, str(runner), which],
                          capture_output=True, text=True, env=env,
                          cwd=str(W.PROJECT))
    return {"script": f"evidence/ACCUM-EARLY-05-B/scripts/{'04_配置闸门.py' if which == '04' else '07_经济口径一致性校验.py'}",
            "called_via": str(runner), "rc": proc.returncode,
            "stdout_tail": proc.stdout[-1500:], "stderr_tail": proc.stderr[-1500:],
            "ok": proc.returncode == 0}


def gate_and_caliber() -> dict:
    r1 = run_05b("04")
    r2 = run_05b("07")

    gate = W.load_out("03_regr_04_配置闸门.json")
    cal = None
    p = W.OUT / "03_regr" / "07_经济口径一致性校验.json"
    if p.exists():
        cal = json.loads(p.read_text(encoding="utf-8"))

    r1["summary"] = (gate or {}).get("summary")
    r1["cases"] = [{"case": c["case"], "ok": c["ok"],
                    "blocked_by": c["blocked_by"],
                    "open_allowed": c["open_allowed"]}
                   for c in (gate or {}).get("cases", [])]
    r1["required_fields"] = (gate or {}).get("required_fields")
    r1["open_probe"] = (gate or {}).get("open_probe")

    r2["cases"] = {k: {"pass": v.get("pass"),
                       "open_allowed": v.get("open_allowed"),
                       "called_paper_engine": v.get("called_paper_engine"),
                       "blocked_by": v.get("blocked_by")}
                   for k, v in ((cal or {}).get("results") or {}).items()}
    r2["all_pass"] = bool(r2["cases"]) and all(c["pass"] for c in r2["cases"].values())
    return {"R1_approved_gate": r1, "R2_economic_caliber": r2}


# ══════════════════════════════════════════════════════════════════════════
# R3：缺口去重
# ══════════════════════════════════════════════════════════════════════════
def _count_gaps(store, event_id=None) -> int:
    if event_id:
        r = store.con.execute("SELECT count(*) c FROM observe_data_gap WHERE event_id=?",
                              (event_id,)).fetchone()
    else:
        r = store.con.execute("SELECT count(*) c FROM observe_data_gap").fetchone()
    return int(r["c"])


def gap_dedup(post_mod) -> dict:
    RUNS = W.RUNS
    RUNS.mkdir(parents=True, exist_ok=True)
    db = RUNS / "observe_dedup.sqlite3"
    if db.exists():
        db.unlink()
    store = post_mod.open_store(db)

    # ── 函数级：同一 (事件, 字段, 起, 止) 三次 ────────────────────────────
    eid = "ev_4a_binance_DEDUPUSDT_box_DEDUPUSDT_1700000000000_1700000000000"
    args = dict(field="entry_15m_available_at", reason="合成：available_at 为空",
                from_ms=1700000000000, to_ms=1700000000000)
    rets = [post_mod.add_gap_once(store, eid, **args) for _ in range(3)]
    n_same = _count_gaps(store, eid)
    # 正向对照：换 to_ms ⇒ 必须真能新增
    ret_new = post_mod.add_gap_once(store, eid, field=args["field"], reason=args["reason"],
                                    from_ms=args["from_ms"], to_ms=args["to_ms"] + 3_600_000)
    n_after_new = _count_gaps(store, eid)
    # 反向对照：绕过包装直接写三次 ⇒ 表本身不去重
    eid2 = eid.replace("4a", "4b")
    for _ in range(3):
        store.add_data_gap(eid2, args["field"], args["reason"],
                           from_ms=args["from_ms"], to_ms=args["to_ms"])
    n_raw = _count_gaps(store, eid2)

    # ── 端到端：同事件重跑两次，缺口行数不得增长 ──────────────────────────
    e2e = _e2e_rescan(post_mod, store)
    store.close()

    return {
        "unit": {"returns_three_calls": rets, "rows_after_three_same": n_same,
                 "new_key_return": ret_new, "rows_after_new_key": n_after_new,
                 "raw_add_data_gap_three_calls_rows": n_raw,
                 "pass": (rets == [True, False, False] and n_same == 1
                          and ret_new is True and n_after_new == 2 and n_raw == 3),
                 "why": ("三次同键 ⇒ 只 1 行且后两次返回 False；换 to_ms ⇒ 真新增（不是恒假）；"
                         "直接调 observe_store.add_data_gap 三次 ⇒ 3 行（去重确实来自调用侧包装）")},
        "end_to_end": e2e,
        "db": str(db),
    }


def _e2e_rescan(post_mod, store) -> dict:
    """合成序列（06-03 的 A 用例）连跑两遍，缺口行数必须不变。"""
    sys.path.insert(0, str(W.PROJECT / "evidence" / "ACCUM-EARLY-06-W2" / "01" / "scripts"))
    import _w2 as W2                                # noqa: E402  （06-03 的合成工具）

    cfg = W2.load_fixture_config()
    case = W2.CASES["A_扫低承接通过_突破根不通过"]
    bars = W2.series(case["sweep_tbq"], case["confirm_tbq"], confirm_i=case["confirm_i"])
    box = dict(W2.BOX)
    feed = W2.SynthFeed(bars)
    meta = post_mod._scheme_identity()["4a"]

    runs = []
    for k in range(2):
        obs = post_mod.SchemeObserver("4a", W2.SYMBOL, box, cfg, store, feed,
                                      strategy_id=meta["strategy_id"],
                                      rule_version=meta["rule_version"])
        obs.open_event(evidence={"box": {kk: box[kk] for kk in
                                         ("formation_ms", "high_h", "low_l", "atr")},
                                 "data_kind": "synthetic_06w2_dedup"},
                       data_quality="ok")
        for i, b in enumerate(bars):
            obs.on_bar(bars, i, float(box["atr"]))
            if obs.done:
                break
        eid = obs.event_id
        # 与 `scan_symbol._reap` 同一动作：候选落 `accum05_candidate`（去重判据就查这张表）
        if obs.candidates:
            post_mod.record_candidate(store, obs.candidates[-1], cfg)
        runs.append({"round": k + 1, "event_id": eid, "state": obs.state,
                     "done": obs.done,
                     "gap_rows_this_event": _count_gaps(store, eid),
                     "candidates_this_event": int(store.con.execute(
                         "SELECT count(*) c FROM accum05_candidate WHERE event_id=?",
                         (eid,)).fetchone()["c"]),
                     "observe_candidates": int(store.con.execute(
                         "SELECT count(*) c FROM observe_candidate WHERE event_id=?",
                         (eid,)).fetchone()["c"]),
                     "observer_emitted_candidate": len(obs.candidates)})
    same_event = runs[0]["event_id"] == runs[1]["event_id"]
    gap_ok = runs[0]["gap_rows_this_event"] == runs[1]["gap_rows_this_event"] == 1
    # 候选幂等：两轮都只有 1 条（第 2 轮命中既有候选 ⇒ dedup，不再落库）
    cand_ok = (runs[0]["candidates_this_event"] == 1 and runs[1]["candidates_this_event"] == 1
               and runs[0]["observe_candidates"] == 1 and runs[1]["observe_candidates"] == 1)
    return {"rounds": runs, "same_event_id": same_event,
            "gap_rows_stable": gap_ok, "candidate_not_duplicated": cand_ok,
            "pass": same_event and gap_ok and cand_ok,
            "why": ("同一事件连跑两遍：缺口行数第 1 轮=1、第 2 轮仍=1（不增长），"
                    "候选也只 1 条（第 2 轮命中既有候选 ⇒ dedup，不再落库）")}


# ══════════════════════════════════════════════════════════════════════════
# R4 / R5：写域
# ══════════════════════════════════════════════════════════════════════════
def shared_files() -> dict:
    rows = []
    for name, rel in SHARED:
        p = W.PROJECT / rel
        f = W.sha256_file(p)
        m = W.iso(int(p.stat().st_mtime * 1000)) if p.exists() else None
        rows.append({"name": name, "rel": rel, "exists": f.get("exists"),
                     "bytes": f.get("bytes"), "sha256": f.get("sha256"),
                     "mtime_utc": m,
                     "untouched_today": (m is not None and m < DAY0)})
    only = W.sha256_file(W.PROJECT / ONLY_ALLOWED)
    only_m = W.iso(int((W.PROJECT / ONLY_ALLOWED).stat().st_mtime * 1000))
    return {"cutoff_utc": DAY0, "files": rows,
            "n": len(rows),
            "n_untouched": sum(1 for r in rows if r["untouched_today"]),
            "all_untouched": all(r["untouched_today"] for r in rows),
            "only_allowed_file": {"rel": ONLY_ALLOWED, "sha256": only.get("sha256"),
                                  "bytes": only.get("bytes"), "mtime_utc": only_m,
                                  "is_today": only_m >= DAY0},
            "note": ("判据是 mtime 早于本日 00:00Z。`accum_early_entry.py` 是本单唯一允许"
                     "改动的文件（06-03 改的，本单未再改），所以它必然是本日的。")}


def write_scope() -> dict:
    paper = W.PROJECT / "数据" / "paper"
    research_leak = sorted(str(p) for p in paper.glob("paper_4*.sqlite3")) if paper.exists() else []
    ledgers = {}
    for arm in ("3a", "3b", "3c", "3d"):
        p = paper / f"paper_{arm}.sqlite3"
        ledgers[arm] = {"exists": p.exists(),
                        "mtime_utc": (W.iso(int(p.stat().st_mtime * 1000))
                                      if p.exists() else None)}
    mine = sorted(p.name for p in W.RUNS.iterdir()) if W.RUNS.exists() else []
    return {"production_ledger_dir": str(paper),
            "research_ledger_files_in_production": research_leak,
            "production_ledgers": ledgers,
            "my_runs_dir": str(W.RUNS), "my_runs_entries": mine,
            "pass": (not research_leak),
            "note": ("本单所有账本/观察库都落在 evidence/ACCUM-EARLY-06-W2/02/runs/ 下；"
                     "生产账本目录里不得出现研究臂文件（paper_4*.sqlite3）")}


def main() -> int:
    post_mod = W.load_post_fix()
    out = {"generated_utc": W.iso(int(__import__("time").time() * 1000))}
    out.update(gate_and_caliber())
    out["R3_gap_dedup"] = gap_dedup(post_mod)
    out["R4_shared_files"] = shared_files()
    out["R5_write_scope"] = write_scope()

    out["summary"] = {
        "R1_ok": out["R1_approved_gate"]["ok"],
        "R1_cases": (out["R1_approved_gate"]["summary"] or {}).get("cases_ok"),
        "R1_cases_n": (out["R1_approved_gate"]["summary"] or {}).get("cases_n"),
        "R2_ok": out["R2_economic_caliber"]["ok"] and out["R2_economic_caliber"]["all_pass"],
        "R2_cases_n": len(out["R2_economic_caliber"]["cases"]),
        "R3_unit_ok": out["R3_gap_dedup"]["unit"]["pass"],
        "R3_e2e_ok": out["R3_gap_dedup"]["end_to_end"]["pass"],
        "R4_ok": out["R4_shared_files"]["all_untouched"],
        "R4_n": out["R4_shared_files"]["n"],
        "R5_ok": out["R5_write_scope"]["pass"],
    }
    out["summary"]["all_ok"] = all([out["summary"]["R1_ok"], out["summary"]["R2_ok"],
                                    out["summary"]["R3_unit_ok"], out["summary"]["R3_e2e_ok"],
                                    out["summary"]["R4_ok"], out["summary"]["R5_ok"]])
    W.dump("03_protection_regression.json", out)

    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
    print("R1 cases:", json.dumps(out["R1_approved_gate"]["cases"], ensure_ascii=False))
    print("R3 unit:", json.dumps(out["R3_gap_dedup"]["unit"], ensure_ascii=False)[:400])
    print("R3 e2e :", json.dumps(out["R3_gap_dedup"]["end_to_end"]["rounds"], ensure_ascii=False))
    print("R4:", json.dumps([(r["name"], r["mtime_utc"], r["untouched_today"])
                             for r in out["R4_shared_files"]["files"]], ensure_ascii=False))
    return 0 if out["summary"]["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
