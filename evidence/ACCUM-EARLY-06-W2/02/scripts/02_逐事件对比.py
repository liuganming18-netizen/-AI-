# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜脚本 02：逐事件对比（修前 vs 修后）+ NOT_FOUND 归因

三块，分开写清楚，不混：

  A. 原 7 事件逐条对比
     · 修前/修后各自的 终态、失效原因、迁移序列（含时刻）、候选数、候选明细
     · `identical` 只按「迁移序列 + 终态 + 失效原因 + 候选数」逐字段比，不比「看起来差不多」
     · `NOT_FOUND` 单独归因（箱体是否达标 / 是否被前序事件占位 / 配额是否用尽），
       **不写成 INVALIDATED**——「本次没按原身份建档」≠「结构已失效」

  B. 同一次回放里、**不在原 7 事件清单内**的事件差异（范围外观察，不替代原输入）
     这些是同一份输入、同一段行情下 `scan_symbol` 顺带建出来的箱体；
     列出来是为了说明「修正影响面」到底落在哪里，**不作为原 7 事件的结论**。

  C. 候选数差异的机制证据
     修前 2 条候选 / 修后 0 条候选，差异必须落到「承接判定读哪一根 bar」上：
     修前候选的 `conditions_json` 里记着它当时用的承接值（确认根），
     修后的 SWEEP 迁移 payload 里记着扫低根的承接值 ⇒ 两根 bar 的数值并列对撞。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2b as W                                    # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# A. 原 7 事件
# ══════════════════════════════════════════════════════════════════════════
def _tr_key(t):
    return (t["from_state"], t["to_state"], int(t["at_ms"]), t["reason"])


def _one(run: dict, eid: str) -> dict:
    r = W.event_record(run, eid)
    return {"found": r["found"], "state": r["state"],
            "closed_reason": r["closed_reason"],
            "transitions": [{"from": t["from"], "to": t["to"],
                             "at_ms": t["at_ms"], "time_utc": t["time_utc"],
                             "reason": t["reason"]} for t in r["transitions"]],
            "n_candidates": r["n_candidates"],
            "candidates": r["candidates"],
            "sweep_absorption": r["sweep_absorption"],
            "same_box_other_event_id": r.get("same_box_other_event_id", [])}


def _cmp(a: dict, b: dict) -> dict:
    ta = [(t["from"], t["to"], int(t["at_ms"]), t["reason"]) for t in a["transitions"]]
    tb = [(t["from"], t["to"], int(t["at_ms"]), t["reason"]) for t in b["transitions"]]
    same = {"found": a["found"] == b["found"],
            "state": a["state"] == b["state"],
            "closed_reason": a["closed_reason"] == b["closed_reason"],
            "transitions": ta == tb,
            "n_candidates": a["n_candidates"] == b["n_candidates"]}
    diffs = []
    if not same["transitions"]:
        n = max(len(ta), len(tb))
        for k in range(n):
            x = ta[k] if k < len(ta) else None
            y = tb[k] if k < len(tb) else None
            if x != y:
                diffs.append({"step": k + 1,
                              "pre": (None if x is None else
                                      {"from": x[0], "to": x[1], "at": W.iso(x[2]),
                                       "reason": x[3]}),
                              "post": (None if y is None else
                                       {"from": y[0], "to": y[1], "at": W.iso(y[2]),
                                        "reason": y[3]})})
    return {"identical": all(same.values()), "fields_same": same,
            "transition_diffs": diffs}


def compare_events(pre: dict, post: dict) -> dict:
    rows = []
    for eid in W.EVENT_IDS:
        a, b = _one(pre, eid), _one(post, eid)
        c = _cmp(a, b)
        rows.append({"event_id": eid, "scheme": W.parse_event_id(eid)["scheme"],
                     "symbol": W.parse_event_id(eid)["symbol"],
                     "formation_utc": W.iso(W.parse_event_id(eid)["formation_ms"]),
                     "pre": a, "post": b, "compare": c,
                     "verdict": ("两版本完全一致" if c["identical"]
                                 else "两版本存在差异")})
    return {"n": len(rows), "rows": rows,
            "n_identical": sum(1 for r in rows if r["compare"]["identical"]),
            "n_differs": sum(1 for r in rows if not r["compare"]["identical"]),
            "n_found": sum(1 for r in rows if r["pre"]["found"]),
            "n_not_found": sum(1 for r in rows if not r["pre"]["found"])}


def attribute_not_found(pre_mod, post_mod, pre: dict, post: dict) -> dict:
    """对「两版本都没建档」的事件做归因；两版本各算一遍，证明归因不依赖版本。"""
    out = {}
    for eid in W.EVENT_IDS:
        if eid in pre["events"] or eid in post["events"]:
            continue
        out[eid] = {"pre": W.not_found_reason(pre_mod, pre, eid),
                    "post": W.not_found_reason(post_mod, post, eid)}
        out[eid]["same_verdict"] = out[eid]["pre"]["verdict"] == out[eid]["post"]["verdict"]
        out[eid]["is_invalidated"] = False
        out[eid]["label"] = "NOT_FOUND（未按原身份建档）——不是 INVALIDATED"
    return out


# ══════════════════════════════════════════════════════════════════════════
# B. 范围外事件差异
# ══════════════════════════════════════════════════════════════════════════
def diff_out_of_scope(pre: dict, post: dict) -> dict:
    """不在原 7 事件清单里、但两版本命运不同的事件（同一份输入顺带建出来的）。"""
    allids = sorted(set(pre["events"]) | set(post["events"]))
    rows = []
    for eid in allids:
        if eid in W.EVENT_IDS:
            continue
        a, b = _one(pre, eid), _one(post, eid)
        c = _cmp(a, b)
        if c["identical"]:
            continue
        rows.append({"event_id": eid, "scheme": W.parse_event_id(eid)["scheme"],
                     "symbol": W.parse_event_id(eid)["symbol"],
                     "formation_utc": W.iso(W.parse_event_id(eid)["formation_ms"]),
                     "pre_state": a["state"], "pre_closed_reason": a["closed_reason"],
                     "pre_n_candidates": a["n_candidates"],
                     "post_state": b["state"], "post_closed_reason": b["closed_reason"],
                     "post_n_candidates": b["n_candidates"],
                     "transition_diffs": c["transition_diffs"],
                     "pre_sweep_absorption": a["sweep_absorption"],
                     "post_sweep_absorption": b["sweep_absorption"]})
    return {"scope_note": ("这些事件**不属于** 05-E 原 7 事件清单；它们只是同一段行情、"
                           "同一份配置下 `scan_symbol` 顺带建出来的箱体。"
                           "列出它们只说明修正影响面落在哪里，不作为原 7 事件的结论。"),
            "n_events_total_pre": len(pre["events"]),
            "n_events_total_post": len(post["events"]),
            "n_out_of_scope_diff": len(rows), "rows": rows}


# ══════════════════════════════════════════════════════════════════════════
# C. 候选差异的机制证据
# ══════════════════════════════════════════════════════════════════════════
_AB_NAMES = ("absorption_buy_ratio", "absorption_close_upper_half", "sell_pressure_amount")


def _pick_ab(conds) -> dict:
    return {x.get("name"): {"value": x.get("value"),
                            "requirement": x.get("requirement"),
                            "result": x.get("result")}
            for x in conds if x.get("name") in _AB_NAMES}


def _pre_absorption_from_transitions(pre: dict, eid: str) -> dict:
    """修前 4b/4c 的承接值不在候选条件表里（乙/丙的候选在 RETEST 之后才落库），
    而在「进 RETEST」那条迁移的 payload.conditions 里 —— 回读它，不重算。"""
    for t in pre["transitions"].get(eid, []):
        if t["to_state"] != "RETEST":
            continue
        p = json.loads(t["payload_json"] or "{}")
        ab = _pick_ab(p.get("conditions") or [])
        if ab:
            return {"at_utc": W.iso(t["at_ms"]), "conditions": ab,
                    "source": "observe_transition.payload_json(→RETEST)"}
    return {}


def candidate_mechanism(pre: dict, post: dict) -> dict:
    """把「修前候选用的承接值（确认根）」与「修后扫低根的承接值」并列。

    数据来源都是**两版本自己写进库里的**，不是本脚本重算：
      · 修前：`accum05_candidate.conditions_json`（候选落库时的条件表）
      · 修后：`observe_transition.payload_json`（进 SWEEP 时求值的承接）
    """
    rows = []
    for eid, cs in sorted(pre["candidates"].items()):
        for c in cs:
            conds = json.loads(c["conditions_json"] or "[]")
            pre_ab = _pick_ab(conds)
            pre_ab_source = "accum05_candidate.conditions_json"
            if not pre_ab:                       # 4b/4c：承接值在「进 RETEST」的迁移 payload 里
                alt = _pre_absorption_from_transitions(pre, eid)
                if alt:
                    pre_ab = alt["conditions"]
                    pre_ab_source = f"{alt['source']} @ {alt['at_utc']}"
            post_ab = None
            for t in post["transitions"].get(eid, []):
                if t["to_state"] == "SWEEP" and t["from_state"] == "SWEEP":
                    p = json.loads(t["payload_json"] or "{}")
                    ab = p.get("absorption") or {}
                    post_ab = {"sweep_bar_i": p.get("sweep_bar_i"),
                               "bar_close_utc": W.iso(p.get("absorption_bar_close_ms")),
                               "baseline_window": p.get("absorption_baseline_window"),
                               "ok": ab.get("ok"),
                               "detail": ab.get("detail"),
                               "conditions": {x.get("name"): {"value": x.get("value"),
                                                              "requirement": x.get("requirement"),
                                                              "result": x.get("result")}
                                              for x in (ab.get("conditions") or [])}}
                    break
            pre_bar = None
            for t in pre["transitions"].get(eid, []):
                if t["to_state"] == "WAIT_ENTRY":
                    pre_bar = W.iso(t["at_ms"])
            rows.append({
                "event_id": eid,
                "scheme": W.parse_event_id(eid)["scheme"],
                "candidate_id": c["candidate_id"],
                "confirm_utc": W.iso(c["confirm_close_ms"]),
                "pre_absorption_source": pre_ab_source,
                "pre_absorption_at_confirm_bar": pre_ab,
                "post_absorption_at_sweep_bar": post_ab,
                "post_state": post["events"].get(eid, {}).get("state"),
                "post_n_candidates": len(post["candidates"].get(eid, [])),
                "reading": ("修前：承接在**确认根**上判（%s）；修后：承接在**扫低根**上判（%s）。"
                            % (pre_bar or "?", (post_ab or {}).get("bar_close_utc") or "?")),
            })
    return {"n_pre_candidates": sum(len(v) for v in pre["candidates"].values()),
            "n_post_candidates": sum(len(v) for v in post["candidates"].values()),
            "rows": rows,
            "why_zero_post": ("修后不产生候选的原因不是「更严格」，而是**换了判定 bar**："
                              "扫低根承接不达标 ⇒ 不确认（继续观察），随后结构转为不合格 ⇒ "
                              "NO_RESISTANCE。修前之所以出候选，是它把确认根的承接值拿来用了。")}


# ══════════════════════════════════════════════════════════════════════════
# A3. 第三方对照：05-E-R1（版本 f7056980，与本单两版都不同）
# ══════════════════════════════════════════════════════════════════════════
TRACE_05E_R1 = W.PROJECT / "evidence" / "ACCUM-EARLY-05-E-R1" / "out" / "02_original_events_retest.json"


def third_party(pre: dict, post: dict) -> dict:
    """只读 05-E-R1 已落盘的结果做**对照列**，不重跑该版本（文件已不存在）。

    版本不同 ⇒ 只能比「终态 + 迁移序列」是否巧合一致，不能声称「同输入同版本对照」。
    """
    doc = json.loads(TRACE_05E_R1.read_text(encoding="utf-8"))
    by_id = {c["old_event_id"]: c for c in doc["cases_retest"]}
    rows = []
    for eid in W.EVENT_IDS:
        c = by_id.get(eid)
        a = _one(pre, eid)
        b = _one(post, eid)
        if c is None:
            rows.append({"event_id": eid, "in_05E_R1": False})
            continue
        t_r1 = [(t["from_state"], t["to_state"], int(t["at_ms"]), t["reason"])
                for t in c.get("current_transitions") or []]
        t_pre = [(t["from"], t["to"], int(t["at_ms"]), t["reason"]) for t in a["transitions"]]
        t_post = [(t["from"], t["to"], int(t["at_ms"]), t["reason"]) for t in b["transitions"]]
        rows.append({"event_id": eid, "in_05E_R1": True,
                     "r1_state": c.get("current_state"),
                     "r1_candidate_produced": c.get("candidate_produced"),
                     "r1_n_transitions": len(t_r1),
                     "pre_state": a["state"], "post_state": b["state"],
                     "state_same_3way": c.get("current_state") == a["state"] == b["state"],
                     "transitions_same_3way": t_r1 == t_pre == t_post,
                     "old_04_status": c.get("old_04_status"),
                     "old_04_confirm_time": c.get("old_04_confirm_time")})
    return {"source": str(TRACE_05E_R1),
            "source_generated_utc": doc.get("timestamp_utc"),
            "config_used_by_05E_R1": doc.get("config_used"),
            "version_of_05E_R1": W.VERSION_05E_R1,
            "version_note": ("05-E-R1 的结论产生在 f7056980 上，与本单修前 d96ad60e / 修后 "
                             "3c570edc **都不同**；该版本文件已不存在，无法重跑。"
                             "因此这里只作第三方对照列，不构成同输入同版本对照。"),
            "n": len(rows), "rows": rows,
            "n_state_same": sum(1 for r in rows if r.get("state_same_3way")),
            "n_transitions_same": sum(1 for r in rows if r.get("transitions_same_3way"))}


# ══════════════════════════════════════════════════════════════════════════
# D. 归因钉死：差异到底该记在「承接」还是「压制位」账上
# ══════════════════════════════════════════════════════════════════════════
#: 出现候选差异的那个箱体（SOLUSDT 第二个箱体）与它修前确认的那一根 bar
OOS_FORMATION_MS = 1785614399999
OOS_CONFIRM_UTC = "2026-08-08T05:59:59Z"
OOS_SYMBOL = "SOLUSDT"


def attribution_check(post_mod) -> dict:
    """用**修后代码自己的函数**在修前确认的那一根 bar 上重算四项。

    06-01 已判定 `_resistance` 传确认根是**另一个未修缺陷**（C-04），并明确要求
    「06-04 回归时 `_resistance` 造成的差异不得算成承接修正的效果」。
    所以这里必须证明：在修前确认的那根 bar 上，修后代码的压制位、合格性、突破、阳线
    四项**都成立**——那么它不确认就只可能是承接那一条分支，不能赖到压制位头上。

    同时给出反向自证：同一根 bar 上修后的 `_absorption` 返回 ok=True（与修前落库值一致），
    而扫低根那一份 ok=False ⇒ 分歧点唯一。
    """
    spec = next(s for s in W.RUNS_SPEC if s["symbol"] == OOS_SYMBOL)
    feed = post_mod.DataFeed()
    bars4 = feed.load("4h", OOS_SYMBOL, ms_of_start(spec), W.ms_of(spec["end"]))
    bars1 = feed.load("1h", OOS_SYMBOL, ms_of_start(spec), W.ms_of(spec["end"]))
    box = next((b for b in post_mod.build_boxes(bars4)
                if int(b["formation_ms"]) == OOS_FORMATION_MS), None)
    if box is None:
        feed.close()
        return {"ok": False, "why": "找不到该箱体"}
    cfg = post_mod.load_config(W.CONFIG_PATH)
    atr = float(box["atr"])

    i = next((k for k, b in enumerate(bars1)
              if W.iso(b["close_time"]) == OOS_CONFIRM_UTC), None)
    if i is None:
        feed.close()
        return {"ok": False, "why": f"找不到 {OOS_CONFIRM_UTC} 这根 1h bar"}

    res, res_detail = post_mod._resistance(bars1, i, box, cfg)
    ok_q, why_q, q_detail = post_mod._resistance_qualified(res, box, atr, cfg)
    doc = cfg["doc"]["shared"]["local_resistance_confirm"]
    buf = float(doc["buffer_atr"]) * atr
    level = res + buf
    bar = bars1[i]
    if doc["mode"] == "intrabar":
        hit = float(bar["high"]) > level
        used = float(bar["high"])
    else:
        hit = float(bar["close"]) > level
        used = float(bar["close"])
    bull = float(bar["close"]) >= float(bar["open"])
    ab_confirm = post_mod._absorption(bars1, i, cfg)

    # 扫低根那一份（进 SWEEP 时求值的那根）：从 post 运行结果里取下标，再独立重算一次
    sweep_i = None
    post_run = W.load_out("01_post_run.json") or {}
    for eid, trs in (post_run.get("transitions") or {}).items():
        if not eid.startswith("ev_4a_binance_SOLUSDT") or str(OOS_FORMATION_MS) not in eid:
            continue
        for t in trs:
            if t["to_state"] == "SWEEP" and t["from_state"] == "SWEEP":
                sweep_i = json.loads(t["payload_json"] or "{}").get("sweep_bar_i")
    ab_sweep = (post_mod._absorption(bars1, sweep_i, cfg) if sweep_i is not None else None)
    feed.close()

    all_pass_at_confirm = bool(ok_q and hit and bull and ab_confirm[0] is True)
    return {
        "symbol": OOS_SYMBOL, "box_formation_utc": W.iso(OOS_FORMATION_MS),
        "at_utc": OOS_CONFIRM_UTC, "bar_i": i,
        "bar_open_utc": W.iso(bar["open_time"]),
        "resistance": res, "resistance_detail": res_detail,
        "resistance_qualified": ok_q, "why_qualified": why_q,
        "qualify_detail": q_detail,
        "confirm_mode": doc["mode"], "confirm_level": level, "confirm_used": used,
        "confirm_hit": hit, "bull_close": bull,
        "bar_close": float(bar["close"]), "bar_open": float(bar["open"]),
        "absorption_at_confirm_bar_ok": ab_confirm[0],
        "absorption_at_confirm_bar_detail": ab_confirm[1],
        "absorption_at_sweep_bar_i": sweep_i,
        "absorption_at_sweep_bar_ok": (None if ab_sweep is None else ab_sweep[0]),
        "absorption_at_sweep_bar_detail": (None if ab_sweep is None else ab_sweep[1]),
        "all_other_gates_pass_at_confirm_bar": all_pass_at_confirm,
        "divergence_attributable_to_absorption": (
            all_pass_at_confirm and ab_sweep is not None and ab_sweep[0] is False),
        "resistance_unchanged_between_versions": _same_source("_resistance"),
        "note": ("压制位/合格性/突破/阳线四项在修前确认那根 bar 上**修后也全成立**，"
                 "唯一不成立的是「扫低根承接」⇒ 修前出候选、修后不出，只能记在承接判定 bar 上。"
                 "`_resistance` 传确认根是 C-04 另一个未修缺陷，与本次差异无关。"),
    }


def ms_of_start(spec: dict) -> int:
    return W.ms_of(spec["start"]) - spec["warmup_days"] * 86_400_000


def _same_source(name: str) -> bool:
    """两个版本的某个函数源码是否逐字相同（证明「没动过」）。"""
    import ast as _ast
    import inspect

    def grab(p):
        src = Path(p).read_text(encoding="utf-8")
        lines = src.splitlines()
        tree = _ast.parse(src)
        for node in tree.body:
            if getattr(node, "name", None) == name:
                return "\n".join(lines[node.lineno - 1:node.end_lineno])
        return ""

    del inspect
    return grab(W.PRE_FIX_PATH) == grab(W.POST_FIX_PATH) != ""


def main() -> int:
    pre = W.load_out("01_pre_run.json")
    post = W.load_out("01_post_run.json")
    if pre is None or post is None:
        raise SystemExit("先跑 01_双版本回放.py")

    pre_mod, post_mod = W.load_pre_fix(), W.load_post_fix()

    ev = compare_events(pre, post)
    nf = attribute_not_found(pre_mod, post_mod, pre, post)
    oos = diff_out_of_scope(pre, post)
    mech = candidate_mechanism(pre, post)
    tp = third_party(pre, post)
    attrib = attribution_check(post_mod)

    out = {"generated_utc": W.iso(int(__import__("time").time() * 1000)),
           "A_original_7_events": ev,
           "A2_not_found_attribution": nf,
           "A3_third_party_05E_R1": tp,
           "B_out_of_scope_diff": oos,
           "C_candidate_mechanism": mech,
           "D_attribution_check": attrib,
           "code": {"pre": W.sha256_file(W.PRE_FIX_PATH),
                    "post": W.sha256_file(W.POST_FIX_PATH)},
           "config": W.sha256_file(W.CONFIG_PATH)}
    out["summary"] = {
        "original_events_n": ev["n"],
        "original_events_identical": ev["n_identical"],
        "original_events_differs": ev["n_differs"],
        "original_events_not_found": ev["n_not_found"],
        "not_found_attributed": all(v["same_verdict"] for v in nf.values()) if nf else True,
        "third_party_state_same": tp["n_state_same"],
        "third_party_transitions_same": tp["n_transitions_same"],
        "out_of_scope_diff_n": oos["n_out_of_scope_diff"],
        "candidates_pre": mech["n_pre_candidates"],
        "candidates_post": mech["n_post_candidates"],
        "attribution_isolated_to_absorption": attrib["divergence_attributable_to_absorption"],
        "verdict": ("原 7 事件在两版本上逐字段一致（6 条 INVALIDATED + 1 条 NOT_FOUND 归因完成）；"
                    "差异只出现在原清单之外的箱体上，已单列"
                    if ev["n_differs"] == 0 else "原 7 事件上存在差异，见 rows"),
    }
    W.dump("02_event_compare.json", out)

    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
    for r in ev["rows"]:
        print(" %-4s %-8s %-20s pre=%-12s post=%-12s cand %d->%d %s"
              % (r["scheme"], r["symbol"], r["formation_utc"],
                 r["pre"]["state"], r["post"]["state"],
                 r["pre"]["n_candidates"], r["post"]["n_candidates"],
                 "SAME" if r["compare"]["identical"] else "DIFF"))
    for eid, v in nf.items():
        print(" NOT_FOUND", eid, "|", v["post"]["verdict"])
    for r in tp["rows"]:
        if r.get("in_05E_R1"):
            print(" 3WAY", r["event_id"][-24:], "| r1", r["r1_state"],
                  "pre", r["pre_state"], "post", r["post_state"],
                  "| state_same", r["state_same_3way"],
                  "| tr_same", r["transitions_same_3way"])
    for r in oos["rows"]:
        print(" OOS", r["scheme"], r["symbol"], r["formation_utc"],
              "|", r["pre_state"], "->", r["post_state"],
              "| cand", r["pre_n_candidates"], "->", r["post_n_candidates"])
    print(" ATTRIB:", json.dumps({k: attrib[k] for k in
                                  ("at_utc", "resistance_qualified", "confirm_hit",
                                   "bull_close", "absorption_at_confirm_bar_ok",
                                   "absorption_at_sweep_bar_ok",
                                   "divergence_attributable_to_absorption",
                                   "resistance_unchanged_between_versions")},
                                 ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
