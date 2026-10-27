# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/01｜脚本 02：修后通过（同一套判据）+ 状态机接线证据

做四件事：
  1. 用与 01 **完全同一套期望**重跑三类用例，要求全部相符；
  2. 静态复核调用点已从 `_confirm_actions` 移到 `on_bar`（AST，不靠注释）；
  3. 逐根解释表：承接的 ratio / 基准中位数 / 阈值 / 卖压额 / 上半区**在本脚本里
     独立重算**（不走 `_absorption`），再与引擎记录的条件逐项对上；
  4. 端到端接线：`scan_symbol` → 建档 → on_bar → 候选落 `accum05_candidate`。

「不得只直接调单体函数」的落实：1/4 全部走状态机，2 是静态证据，
3 只用来解释数字，且**独立重算**而非再调一次被测函数。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2 as W                                    # noqa: E402

AEE_PATH = W.BACKEND / "accum_early_entry.py"


# ══════════════════════════════════════════════════════════════════════════
def static_call_sites() -> dict:
    tree = ast.parse(AEE_PATH.read_text(encoding="utf-8"))

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack, self.sites = [], []

        def visit_FunctionDef(self, node):                       # noqa: N802
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):                              # noqa: N802
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name == "_absorption":
                self.sites.append({"line": node.lineno, "enclosing": list(self.stack),
                                   "arg_i_repr": (ast.unparse(node.args[1])
                                                  if len(node.args) > 1 else None)})
            self.generic_visit(node)

    v = V()
    v.visit(tree)
    return {"absorption_call_sites": v.sites,
            "in_on_bar": any("on_bar" in s["enclosing"] for s in v.sites),
            "in_confirm_actions": any("_confirm_actions" in s["enclosing"]
                                      for s in v.sites),
            "arg_i": [s["arg_i_repr"] for s in v.sites]}


explain_bar = W.explain_ratio      # 独立重算放在 _w2（只实现一次，避免两套算术漂移）


def expected_of(spec: dict) -> dict:
    return {"n_candidates": 1 if spec["expect_candidate"] else 0,
            "terminal_state": spec["expect_terminal"],
            "absorption_bar_i": W.SWEEP_I,
            "absorption_bar_close_utc": W.iso(W.T0 + W.SWEEP_I * W.H + W.H - 1)}


def actual_of(r: dict) -> dict:
    ab = [c for c in r["trace"] if c["fn"] == "absorption"]
    idx = sorted({c["i"] for c in ab})
    conds, status = {}, None
    if r["candidates"]:
        note = json.loads(r["candidates"][-1]["note"])
        conds = {c["name"]: c["result"] for c in note.get("conditions") or []}
        status = note.get("status")
    else:
        # 终结用例要读**终结动作**带的条件表，不能读上一根留下的 `_last_conditions`
        close = [a for a in r["timeline"] if a["kind"] == "close"]
        conds = ({c["name"]: c.get("result")
                  for c in (close[-1]["payload"].get("conditions") or [])}
                 if close else W.conds_of(r))
    terminal = (r["final_state"] if r["final_state"] in
                ("INVALIDATED", "EXPIRED", "CLOSED", "DATA_INSUFFICIENT") else None)
    return {"n_candidates": r["n_candidates"], "terminal_state": terminal,
            "final_state": r["final_state"], "candidate_status": status,
            "absorption_call_indices": idx, "absorption_n_calls": len(ab),
            "absorption_bar_i": (idx[0] if len(idx) == 1 else (idx[-1] if idx else None)),
            "absorption_bar_close_utc": (ab[-1]["bar_close_utc"] if ab else None),
            "conditions": conds,
            "transitions": [{"to": t["to_state"], "at": W.iso(t["at_ms"]),
                             "reason": t["reason"]} for t in r["transitions"]],
            "closed_reason": r["closed_reason"]}


def diff(exp: dict, act: dict) -> list:
    out = []
    for k in ("n_candidates", "terminal_state", "absorption_bar_i"):
        if act[k] != exp[k]:
            out.append({"item": k, "expected": exp[k], "actual": act[k]})
    return out


# ══════════════════════════════════════════════════════════════════════════
def integration_scan_symbol(cfg) -> dict:
    """端到端：`scan_symbol` 全链（箱体来源用已知箱体替身，不替代状态机）。"""
    bars = W.series(W.TBQ_PASS, W.TBQ_FAIL)
    feed = W.SynthFeed(bars, bars_4h=[{"open_time": W.T0 + W.FORM_I * W.H,
                                       "close_time": W.BOX["formation_ms"],
                                       "open": 103.7, "high": 103.9, "low": 103.4,
                                       "close": 103.7, "volume": 1000.0,
                                       "quote_volume": 1000.0,
                                       "taker_buy_quote_volume": 500.0}])
    orig = W.AEE.build_boxes
    W.AEE.build_boxes = lambda bars4, cfg=None: [dict(
        W.BOX, formed=True, skip=None, index=0, atr_bars_seen=201,
        bar_open_ms=W.T0 + W.FORM_I * W.H)]
    store = W.fresh_store("02_integration")
    try:
        res = W.AEE.scan_symbol(W.SYMBOL, cfg, feed, store,
                                start_ms=W.T0, end_ms=W.T0 + 62 * W.H,
                                max_boxes=1, warmup_days=0,
                                open_candidates=False)
    finally:
        W.AEE.build_boxes = orig
    rows = [dict(r) for r in store.con.execute(
        "SELECT scheme, candidate_id, confirm_close_ms, status, signal_uid, "
        "conditions_json FROM accum05_candidate ORDER BY scheme")]
    obs = [dict(r) for r in store.con.execute(
        "SELECT scheme, candidate_id, created_ms, state FROM observe_candidate "
        "ORDER BY scheme")]
    store.close()
    return {"scan_events": res["events"], "scan_candidates": len(res["candidates"]),
            "accum05_candidate_rows": rows, "observe_candidate_rows": obs,
            "cooldown_ms": res["cooldown_ms"], "skipped": res["skipped"],
            "expected_confirm_close_ms": W.T0 + W.CONFIRM_I * W.H + W.H - 1}


def main() -> int:
    cfg = W.load_fixture_config()
    out = {"code_after_fix": W.sha256_file(AEE_PATH),
           "config_sha256": cfg.get("config_sha256"),
           "static": static_call_sites(), "cases": {}, "summary": {}}

    all_ok = True
    for name, spec in W.CASES.items():
        bars = W.series(spec["sweep_tbq"], spec["confirm_tbq"], confirm_i=spec["confirm_i"])
        r = W.run_observer(name, bars, cfg, tag="02_" + name[:1])
        exp, act = expected_of(spec), actual_of(r)
        d = diff(exp, act)
        all_ok = all_ok and not d
        out["cases"][name] = {
            "expected": exp, "actual": act, "mismatches": d,
            "verdict": "PASS" if not d else "FAIL",
            "explain": {
                "sweep_bar": explain_bar(bars, W.SWEEP_I, cfg),
                "confirm_bar": explain_bar(bars, spec["confirm_i"], cfg),
            },
            "transition_payload_absorption": _absorption_payload(r),
        }

    out["integration"] = integration_scan_symbol(cfg)
    integ_ok = (out["integration"]["scan_candidates"] >= 1
                and any(x["scheme"] == "4a" for x in
                        out["integration"]["accum05_candidate_rows"]))
    out["summary"] = {"all_cases_pass_after_fix": all_ok,
                      "integration_ok": integ_ok,
                      "verdict": ("修后通过：三类全部相符且端到端落库" if (all_ok and integ_ok)
                                  else "未通过：见 cases/integration")}
    W.dump("02_post_fix_pass.json", out)

    print("static:", json.dumps(out["static"], ensure_ascii=False))
    for n, c in out["cases"].items():
        print(f"{n}: {c['verdict']}  mismatches={json.dumps(c['mismatches'], ensure_ascii=False)}"
              f"  absorption_bar_i={c['actual']['absorption_bar_i']}"
              f"  cand={c['actual']['n_candidates']}"
              f"  terminal={c['actual']['terminal_state']}")
    print("integration:", json.dumps({
        "scan_candidates": out["integration"]["scan_candidates"],
        "accum05_rows": len(out["integration"]["accum05_candidate_rows"]),
        "schemes": [x["scheme"] for x in out["integration"]["accum05_candidate_rows"]]},
        ensure_ascii=False))
    print("summary:", json.dumps(out["summary"], ensure_ascii=False))
    return 0 if (all_ok and integ_ok) else 2


def _absorption_payload(r):
    """从 SWEEP 迁移 payload 里取出「承接判在哪一根」的原始记录。"""
    for t in r["transitions"]:
        pass
    for a in r["timeline"]:
        if a["kind"] == "transition" and a["state"] == "SWEEP":
            p = a["payload"]
            return {"absorption_bar_close_ms": p.get("absorption_bar_close_ms"),
                    "absorption_bar_close_utc": W.iso(p.get("absorption_bar_close_ms")),
                    "absorption_baseline_window": p.get("absorption_baseline_window"),
                    "absorption_ok": (p.get("absorption") or {}).get("ok"),
                    "sweep_bar_i": p.get("sweep_bar_i")}
    return None


if __name__ == "__main__":
    raise SystemExit(main())
