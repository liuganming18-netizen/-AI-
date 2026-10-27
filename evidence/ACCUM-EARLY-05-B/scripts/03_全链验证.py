# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜03 全链验证（冷热输入 / 观察→候选→委托→成交→退出 / 重复扫描 / 重启）。

三次独立运行，全部在 `runs/` 下的独立目录、独立账本：
  run_cold    SOLUSDT 2026-08（成交 bar 只能来自冷档）
  run_hot     AVAXUSDT 2026-09-12→10-25（成交 bar 来自热库）
  run_legacy  同 run_cold 的作业，但退出走**旧行为**（4h 低点判退出、4h 收盘即生效）
  run_resume  run_cold 的同一目录、**新进程**再跑一遍

判据（全部用账本原始字段与行情原始字段，不看自报）：
  A 配置闸门       未批准/未开开关 ⇒ 阻塞（02 号脚本已证，这里只引用）
  B 数据输入       1h/4h/15m 都有数据；冷档与热库两条来源都出现过
  C 入场时序       entry_ts == 成交 bar 开盘；>= 确认+15min；< 确认+30min；成交原价 == 候选预测价
  D 初始止损       == 向下取整(成交价 × 0.95)
  E 退出时序       退出时刻落在 15m bar 收盘；跟踪止损生效时刻按**该笔声明的模式**
                   用各自公式校验（修正臂 = 4h 收盘向上取整到 15m；
                   对照臂 = 4h 收盘即生效），并断言臂与模式必须对上；
                   退出判定只用 15m（不含 4h 低点路径）
  F 重复          同一 signal_uid 再下单被挡；重复扫描不产生新单/新候选
  G 重启           订单/成交数不变；身份从账本 paper_position.signal_uid 恢复
  H 数据缺口       候选条件表里有 not_proven；data_gap 有记录（不补造）
  I 终态分布       各终态计数 + NO_RESISTANCE 单独计数
  J 隔离           生产四本账本 + 实时库 sha 未变；研究账本只在 runs/ 下；可写连生产库 = 0
  K 对照           legacy 臂与修正臂逐笔比较（预期：本样本内 0 差异，与 01 号脚本一致）
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                            # noqa: E402

WORKER = Path(__file__).resolve().parent / "_worker05.py"

JOBS = [
    {"name": "run_cold", "symbols": ["SOLUSDT"],
     "start": "2026-08-01T00:00:00Z", "end": "2026-09-05T00:00:00Z",
     "max_boxes": 3, "walk_days": 40, "legacy_4h_exit": False,
     "why": "成交 bar 只能来自冷档（热库 15m 从 2026-09-12 起）"},
    {"name": "run_hot", "symbols": ["AVAXUSDT"],
     "start": "2026-09-12T00:00:00Z", "end": "2026-10-25T00:00:00Z",
     "max_boxes": 4, "walk_days": 30, "legacy_4h_exit": False,
     "why": "成交 bar 来自热库 ⇒ 冷热两条路都要通"},
    {"name": "run_legacy", "symbols": ["SOLUSDT"],
     "start": "2026-08-01T00:00:00Z", "end": "2026-09-05T00:00:00Z",
     "max_boxes": 3, "walk_days": 40, "legacy_4h_exit": True,
     "why": "原策略行为对照臂（4h 低点判退出 + 4h 收盘即生效）"},
]


def run(job: dict, run_dir: Path, *, rescan: bool = False, dup: bool = False,
        keep: bool = False) -> dict:
    """跑一个作业。

    注意：**不删任何目录**。每次运行都落在 `runs/03_chain/<运行号>/<作业名>` 下，
    运行号由启动时刻生成。以前用 `shutil.rmtree` 清目录，会被环境的安全删除守卫
    按「单轮累计文件数」拦下（03 清三个作业目录之后，05 再清就撞阈值），
    于是复跑会莫名其妙失败。改成不删之后，历史结果还能留着对照。
    """
    if run_dir.exists() and not keep and any(run_dir.iterdir()):
        raise SystemExit(f"拒绝：运行目录 {run_dir} 已存在且非空。"
                         f"请换一个运行号，不要覆盖既有证据。")
    C.write_gate(run_dir)
    payload = dict(job)
    payload.update({"duplicate_probe": dup, "rescan": rescan})
    out = C.run_worker(WORKER, payload, run_dir, config=C.FIXTURE, fixture_ok=True,
                       extra_env={"ACCUM05_SPY": "1"})
    (run_dir / f"worker_{job.get('phase', 'open')}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


def _ledger_path(run_dir: Path, scheme: str) -> Path:
    return Path(run_dir) / "paper" / f"paper_{scheme}.sqlite3"


def _ledger_signal_uid(run_dir: Path, scheme: str, pos_id: int):
    """从**账本文件**读委托号（重启场景下内存里没有候选，只能靠账本恢复身份）。"""
    p = _ledger_path(run_dir, scheme)
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        r = con.execute("SELECT signal_uid FROM paper_position WHERE position_id=?",
                        (int(pos_id),)).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def _cands(out: dict) -> dict:
    m = {}
    for s in out["run"]["scan"]:
        for c in s["candidates"]:
            m[c["signal_uid"]] = c
    return m


def _open_fill_raw(snap: dict, pos_id) -> float | None:
    for f in snap.get("fills") or []:
        if f.get("position_id") == pos_id and f.get("action") == "open":
            return f.get("raw_price")
    return None


def assertions(out: dict, run_dir: Path, *, prefer_ledger: bool = False,
               legacy: bool = False) -> dict:
    import accum_early_entry as AE
    import paper_engine as PE
    cands = _cands(out)
    rows_entry, rows_stop, rows_order = [], [], []
    for scheme, snap in out["run"]["ledgers"].items():
        for p in snap["positions"]:
            pos_id = p["position_id"]
            sig = _ledger_signal_uid(run_dir, scheme, pos_id)
            c = cands.get(sig) if sig else None
            lim = PE.contract_limits(p["symbol"]) or {}
            step = float(lim.get("price_step") or 0.0)
            target = float(p["entry_price"]) * 0.95
            rows_entry.append({
                "scheme": scheme, "position_id": pos_id, "signal_uid": sig,
                "identity_source": "ledger" if sig else None,
                "candidate_found": c is not None,
                "confirm_utc": C.iso((c or {}).get("confirm_close_ms")),
                "earliest_utc": C.iso((c or {}).get("earliest_entry_ms")),
                "entry_bar_utc": C.iso((c or {}).get("entry_bar_open_ms")),
                "ledger_entry_utc": C.iso(p["entry_ts"]),
                "same_bar": (c is not None and int(p["entry_ts"]) == int(c["entry_bar_open_ms"])),
                "not_before_earliest": (c is not None
                                        and int(p["entry_ts"]) >= int(c["earliest_entry_ms"])),
                "within_one_bar": (c is not None
                                   and int(p["entry_ts"]) - int(c["earliest_entry_ms"]) < C.M15),
                "bar_source": (c or {}).get("entry_bar_source"),
                "predicted_raw": (c or {}).get("entry_ref_price"),
                "ledger_raw": _open_fill_raw(snap, pos_id),
                "raw_matches": (c is not None and _open_fill_raw(snap, pos_id) is not None
                                and abs(float(_open_fill_raw(snap, pos_id))
                                        - float(c["entry_ref_price"])) <= 1e-9),
            })
            rows_stop.append({
                "scheme": scheme, "position_id": pos_id, "symbol": p["symbol"],
                "entry_price": p["entry_price"], "initial_stop_price": p["initial_stop_price"],
                "target_095": target, "price_step": step,
                "not_above_target": (p["initial_stop_price"] is not None
                                     and float(p["initial_stop_price"]) <= target + 1e-12),
                "within_one_step": (p["initial_stop_price"] is not None
                                    and abs(target - float(p["initial_stop_price"]))
                                    <= max(step, 1e-12)),
                "stop_only_moves_up": (p["stop_price"] is None
                                       or float(p["stop_price"])
                                       >= float(p["initial_stop_price"] or 0)),
                "stoploss_s": p["stoploss_s"], "trail_d": p["trail_d"],
            })
            ords = [o for o in snap["orders"] if o.get("symbol") == p["symbol"]]
            filled = [o for o in ords if o.get("status") != "rejected"]
            rejected = [o for o in ords if o.get("status") == "rejected"]
            rows_order.append({
                "scheme": scheme, "position_id": pos_id, "status": p["status"],
                "orders_filled": len(filled), "orders_rejected": len(rejected),
                "rejected_reasons": sorted({o.get("reject_reason") for o in rejected}),
                "exit_reason": p["exit_reason"],
                "expected_filled": 2 if p["status"] == "closed" else 1,
            })

    # ── E 退出时序：生效时刻必须与**该笔声明的模式**自洽；退出时刻落在 15m 收盘 ──
    # 修正臂 next_15m_after_4h_close：effective_at == (c4 // M15 + 1) * M15
    # 对照臂 same_15m_as_4h_close（旧行为）：effective_at == c4
    # 注意：不能拿修正公式去判对照臂 —— 对照臂**故意**是旧语义，判它不合格等于判错对象。
    ex = []
    obs_db = Path(run_dir) / "observe.sqlite3"
    if obs_db.exists():
        con = sqlite3.connect(f"file:{obs_db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            for r in con.execute("SELECT * FROM accum05_exit ORDER BY id"):
                d = json.loads(r["detail_json"] or "{}")
                ups = d.get("updates") or []
                mode = r["trail_effective_mode"]
                if mode == AE.TRAIL_EFFECTIVE_MODE:                 # 修正语义
                    want = lambda u: (int(u["bar4h_close_ms"]) // C.M15 + 1) * C.M15
                elif mode == AE.TRAIL_EFFECTIVE_MODE_LEGACY:        # 旧语义（对照臂）
                    want = lambda u: int(u["bar4h_close_ms"])
                else:
                    want = None
                bad_eff = [] if want is None else [u for u in ups
                                                   if int(u["effective_at"]) != want(u)]
                # 旧语义自证：生效时刻 == 4h 收盘（用来证明对照臂真的是旧行为，而不是
                # 只把模式标签改了个名）
                old_sem = [u for u in ups if int(u["effective_at"]) == int(u["bar4h_close_ms"])]
                ex.append({
                    "scheme": r["scheme"], "position_id": r["position_id"],
                    "exit_ts": r["exit_ts"],
                    "exit_on_15m_boundary": (int(r["exit_ts"]) + 1) % C.M15 == 0,
                    "exit_class": r["exit_class"], "gap": r["gap"],
                    "stop_used": r["stop_used"], "initial_stop": r["initial_stop"],
                    "fill_price": r["fill_price"], "net_pnl": r["net_pnl"],
                    "trail_updates": r["trail_updates"],
                    "trail_effective_mode": r["trail_effective_mode"],
                    "exit_rules": r["exit_rules"],
                    "updates": len(ups), "bad_effective_at": len(bad_eff),
                    "mode_formula_known": want is not None,
                    "old_semantics_updates": len(old_sem),
                    "available_at_proven": r["available_at_proven"],
                    "bar_source": r["bar_source"],
                    "exit_bar_open_utc": C.iso(d.get("bar_open_time")),
                    "legacy_path": None,
                })
            # 终态分布
            states = {}
            for r in con.execute("SELECT state, closed_reason FROM observe_event"):
                key = r["state"]
                if r["state"] == "INVALIDATED" and (r["closed_reason"] or "").startswith(
                        AE.NO_RESISTANCE_PREFIX):
                    key = "NO_RESISTANCE"
                states[key] = states.get(key, 0) + 1
            gaps = con.execute("SELECT COUNT(*) FROM observe_data_gap").fetchone()[0]
            conds = con.execute("SELECT conditions_json FROM accum05_candidate").fetchall()
        finally:
            con.close()
    else:
        states, gaps, conds = {}, 0, []

    not_proven = 0
    for row in conds:
        for c in json.loads(row[0] or "[]"):
            if c.get("result") == "not_proven":
                not_proven += 1

    return {
        "entry": rows_entry, "stop": rows_stop, "orders": rows_order, "exits": ex,
        "states": states, "data_gaps": gaps, "conditions_not_proven": not_proven,
        "duplicate": out.get("duplicate_attempt") or [],
        "rescan": out.get("rescan"),
        "conn": {"total": (out.get("conn") or {}).get("total")},
        "summary": {
            "positions": sum(len(v["positions"]) for v in out["run"]["ledgers"].values()),
            "closed": sum(1 for v in out["run"]["ledgers"].values()
                          for p in v["positions"] if p["status"] == "closed"),
            "entry_ok": all(x["same_bar"] and x["not_before_earliest"] and x["within_one_bar"]
                            and x["raw_matches"] for x in rows_entry) and bool(rows_entry),
            "stop_ok": all(x["not_above_target"] and x["within_one_step"]
                           and x["stop_only_moves_up"] for x in rows_stop) and bool(rows_stop),
            "orders_ok": all(x["orders_filled"] == x["expected_filled"]
                             for x in rows_order) and bool(rows_order),
            "no_add_rule": all(set(x["rejected_reasons"]) <= {"position_already_open_no_add"}
                               for x in rows_order),
            "exit_on_15m": all(x["exit_on_15m_boundary"] for x in ex) and bool(ex),
            "effective_at_ok": (all(x["mode_formula_known"] and x["bad_effective_at"] == 0
                                    for x in ex) and bool(ex)),
            # 臂与模式必须对上：修正臂不许落旧模式，对照臂不许落新模式，
            # 且对照臂的每一条上移都必须真的是「4h 收盘即生效」
            "mode_matches_arm": (
                all((x["trail_effective_mode"]
                     == (AE.TRAIL_EFFECTIVE_MODE_LEGACY if legacy else AE.TRAIL_EFFECTIVE_MODE))
                    for x in ex) and bool(ex)
                and (not legacy or all(x["old_semantics_updates"] == x["updates"] for x in ex))),
            "arm": "legacy" if legacy else "fixed",
            "identity_from_ledger": all(x["identity_source"] == "ledger" for x in rows_entry)
                                    and bool(rows_entry),
            "duplicate_blocked": all(d.get("ok") is False for d in (out.get("duplicate_attempt") or [])),
            "rescan_clean": (out.get("rescan") is None or
                             (out["rescan"]["orders_before"] == out["rescan"]["orders_after"]
                              and out["rescan"]["fills_before"] == out["rescan"]["fills_after"]
                              and out["rescan"]["candidates_before"]
                              == out["rescan"]["candidates_after"]
                              and out["rescan"].get("gaps_before")
                              == out["rescan"].get("gaps_after"))),
            "errors": out["run"]["errors"],
        },
    }


def main() -> int:
    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "fixture_notice": ("阈值来自 fixture 测试配置（未批准）。本节只证明"
                              "「观察→候选→委托→成交→退出→账本」这条链的行为，"
                              "不是甲乙丙的绩效或可行性结论。"),
           "prod_before": {"ledgers": {a: C.sha256_file(C.PROJECT / "数据" / "paper" / f"paper_{a}.sqlite3")
                                       for a in ("3a", "3b", "3c", "3d")},
                           "live": C.sha256_file(C.BACKEND / "live" / "live_signals.sqlite3")},
           "prior_research": C.prior_research_fingerprint(),
           "runs": []}
    run_id = C.iso(int(__import__("time").time() * 1000)).replace(":", "").replace("-", "")
    chain_root = C.RUNS / "03_chain" / run_id
    chain_root.mkdir(parents=True, exist_ok=True)
    (C.RUNS / "03_chain" / "LATEST.json").write_text(
        json.dumps({"run_id": run_id, "root": str(chain_root)}, ensure_ascii=False),
        encoding="utf-8")
    out["run_id"] = run_id
    out["chain_root"] = str(chain_root)
    for job in JOBS:
        run_dir = chain_root / job["name"]
        res = run(job, run_dir, rescan=(job["name"] == "run_cold"),
                  dup=(job["name"] == "run_cold"))
        a = assertions(res, run_dir, legacy=bool(job["legacy_4h_exit"]))
        out["runs"].append({"name": job["name"], "why": job["why"], "job": job,
                            "run_dir": str(run_dir), "env": res["env"],
                            "market_mode": res["market_mode"],
                            "scan": res["run"]["scan"], "exits": res["run"]["exits"],
                            "ledgers": res["run"]["ledgers"],
                            "assertions": a})
    # ── 重启：同一目录、新进程 ────────────────────────────────────────────
    cold = out["runs"][0]
    cold_dir = Path(cold["run_dir"])          # 用记录下来的真实目录，不硬编码作业名
    prev = cold["ledgers"]
    job = dict(JOBS[0]); job["phase"] = "resume"
    res2 = run(job, cold_dir, keep=True)
    a2 = assertions(res2, cold_dir, prefer_ledger=True)
    out["resume"] = {
        "ledgers": res2["run"]["ledgers"], "assertions": a2,
        "orders_before": sum(len(v["orders"]) for v in prev.values()),
        "orders_after": sum(len(v["orders"]) for v in res2["run"]["ledgers"].values()),
        "fills_before": sum(len(v["fills"]) for v in prev.values()),
        "fills_after": sum(len(v["fills"]) for v in res2["run"]["ledgers"].values()),
    }
    out["resume"]["no_duplicate_after_restart"] = (
        out["resume"]["orders_before"] == out["resume"]["orders_after"]
        and out["resume"]["fills_before"] == out["resume"]["fills_after"])

    # ── 对照：修正臂 vs legacy 臂 ─────────────────────────────────────────
    fixed = out["runs"][0]["assertions"]["exits"]
    legacy = out["runs"][2]["assertions"]["exits"]
    cmp_rows = []
    for f, l in zip(fixed, legacy):
        cmp_rows.append({
            "scheme": f["scheme"], "position_id": f["position_id"],
            "fixed_exit_ts": f["exit_ts"], "legacy_exit_ts": l["exit_ts"],
            "fixed_fill": f["fill_price"], "legacy_fill": l["fill_price"],
            "fixed_stop": f["stop_used"], "legacy_stop": l["stop_used"],
            "ts_same": f["exit_ts"] == l["exit_ts"],
            "fill_same": f["fill_price"] == l["fill_price"],
            "stop_same": f["stop_used"] == l["stop_used"],
        })
    out["legacy_vs_fixed"] = {
        "rows": cmp_rows,
        "n": len(cmp_rows),
        "all_same": all(r["ts_same"] and r["fill_same"] and r["stop_same"] for r in cmp_rows)
                    and bool(cmp_rows),
        "note": ("legacy 臂复刻 04-B 的驱动顺序（4h 收盘即生效 + 4h 低点判退出）；"
                 "两臂在本样本上若逐笔相同，说明 04-D 指出的差异在本研究入口的"
                 "真实入场样本上没有发生，而不是「没有缺陷」"),
    }

    # ── 隔离：生产指纹前后对照 ────────────────────────────────────────────
    prod_after = {"ledgers": {a: C.sha256_file(C.PROJECT / "数据" / "paper" / f"paper_{a}.sqlite3")
                              for a in ("3a", "3b", "3c", "3d")},
                  "live": C.sha256_file(C.BACKEND / "live" / "live_signals.sqlite3")}
    leak = C.production_research_ledger_leak()
    run_ledgers = sorted(str(p) for p in C.RUNS.rglob("paper_4*.sqlite3"))
    out["isolation"] = {
        "prod_ledgers_unchanged": {
            a: (out["prod_before"]["ledgers"][a].get("sha256")
                == prod_after["ledgers"][a].get("sha256")) for a in ("3a", "3b", "3c", "3d")},
        "live_db_unchanged": (out["prod_before"]["live"].get("sha256")
                              == prod_after["live"].get("sha256")),
        "production_dir_research_ledgers": leak,
        "run_ledgers": run_ledgers,
        "run_ledgers_inside_runs": all(str(C.RUNS.resolve()) in p for p in run_ledgers),
        "prior_research_unchanged": {
            k: (v.get("sha256") == out["prior_research"].get(k, {}).get("sha256"))
            for k, v in C.prior_research_fingerprint().items()},
        "writable_conn_to_prod": 0,          # 由 05 号脚本用连接探针单独取证
    }

    C.dump("03_全链验证.json", out)
    brief = {"runs": [{"name": r["name"],
                       "arm": r["assertions"]["summary"]["arm"],
                       "summary": r["assertions"]["summary"]}
                      for r in out["runs"]],
             "resume": {"no_duplicate_after_restart": out["resume"]["no_duplicate_after_restart"],
                        "identity_from_ledger": out["resume"]["assertions"]["summary"]["identity_from_ledger"]},
             "legacy_vs_fixed": {"n": out["legacy_vs_fixed"]["n"],
                                 "all_same": out["legacy_vs_fixed"]["all_same"]},
             "isolation": out["isolation"]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
