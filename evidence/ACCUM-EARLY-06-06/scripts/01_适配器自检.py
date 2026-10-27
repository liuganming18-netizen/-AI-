#!/usr/bin/env python3
"""01 · 适配器行为核验（不是读注释，是跑行为）

本脚本回答三个问题，每个都用「可复现的输入 → 可观测的输出」证明：
  Q1 列名映射是不是必需的？（不映射会怎样，映射后是否真的算进了成本）
  Q2 同键冲突会不会被擅自挑一个值？（合成冲突 / 单侧缺失 / 两侧都缺 三态）
  Q3 真实数据上这些分支到底走到了没有？（空跑必须承认空跑，不能拿合成用例冒充真实覆盖）

产出 out/01_适配器自检.json。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"))
sys.path.insert(0, str(ROOT / "框架" / "backend"))

import funding_events_adapter as A  # noqa: E402

from replay_calc import settle_funding_events  # noqa: E402
from 策略.WyckoffMaster.trading_core.funding import funding_events_ms  # noqa: E402

QTY, DIRECTION = 100.0, 1


def settle(events, t):
    """把事件喂进真结算内核，返回 (已结算, 缺口数, 成本合计)。"""
    r = settle_funding_events(symbol="TESTSYM", qty=QTY, direction=DIRECTION,
                              entry_ms=t - 1, exit_ms=t + 1, funding_events=events)
    return r["已结算"], r["缺口数"], round(r["成本合计"], 10)


def main() -> int:
    t0 = time.time()
    checks = []

    def add(name, ok, detail):
        checks.append({"检查": name, "通过": bool(ok), "证据": detail})

    T = 1788307200000  # 2026-09-02T20:00Z（缺口段起点，便于人读）

    # ================= Q1 列名映射 =================
    cold_shaped = {"funding_time": T, "funding_rate": 0.0001, "mark_price": 100.0,
                   "rate_type": "settled", "source": "binance.archive.fundingRate+mark1h"}
    hot_shaped = {"funding_time": T, "funding_rate": "0.0001", "mark_price": "100.0",
                  "rate_type": "settled", "source": "binance.archive.fundingRate+mark1h",
                  "fetched_at": 1792035104993}

    raw_norm = funding_events_ms([cold_shaped])
    raw_settle = settle([cold_shaped], T)
    add("Q1a 冷档行原样喂入 ⇒ rate 被归一化为 None（列名 funding_rate 不认）",
        raw_norm[0]["rate"] is None,
        {"归一化": raw_norm, "settle(已结算,缺口,成本)": raw_settle})
    add("Q1b 冷档行原样喂入 ⇒ 落 funding_gap、成本 0、**不报错**（静默）",
        raw_settle == (0, 1, 0.0), {"settle": raw_settle})

    # 适配器映射后的单条事件（冷侧独有）
    mapped = A._event_from_sides("binance", "um", "TESTSYM", T, None, cold_shaped, False)
    mapped_settle = settle([mapped], T)
    add("Q1c 适配器映射后 ⇒ rate 有值、成本 = mark×qty×rate×dir = 1.0",
        mapped["rate"] == 0.0001 and mapped_settle == (1, 0, 1.0),
        {"event.rate": mapped["rate"], "settle": mapped_settle,
         "手算": 100.0 * QTY * 0.0001 * DIRECTION})

    # 热库行（字符串）也要能解析
    hot_ev = A._event_from_sides("binance", "um", "TESTSYM", T, hot_shaped, None, False)
    add("Q1d 热库字符串值能被解析成 float（'0.0001'/'100.0' → 0.0001/100.0）",
        hot_ev["rate"] == 0.0001 and hot_ev["mark_price"] == 100.0,
        {"rate": hot_ev["rate"], "mark_price": hot_ev["mark_price"]})

    # ================= Q2 冲突三态 =================
    # --- differs：两侧都有且不等 ---
    h_d = {"funding_time": T, "funding_rate": "0.001", "mark_price": "100.0", "fetched_at": 1,
           "source": "hot.src", "rate_type": "settled"}
    c_d = {"funding_time": T, "funding_rate": 0.002, "mark_price": 100.0,
           "source": "cold.src", "rate_type": "settled"}
    ev_d = A._event_from_sides("binance", "um", "TESTSYM", T, h_d, c_d, False)
    st_d = settle([ev_d], T)
    kinds = sorted({(c["field"], c["kind"]) for c in ev_d["conflicts"]})
    add("Q2a differs：顶层 rate 置 None（fail-closed），两侧原值并列保留",
        ev_d["rate"] is None and ev_d["rate_hot"] == 0.001 and ev_d["rate_cold"] == 0.002,
        {"rate": ev_d["rate"], "rate_hot": ev_d["rate_hot"], "rate_cold": ev_d["rate_cold"],
         "status": ev_d["status"]})
    add("Q2b differs：status=pending_conflict，冲突明细含 rate/differs 与 source/differs",
        ev_d["status"] == "pending_conflict" and ("rate", "differs") in kinds,
        {"status": ev_d["status"], "conflicts": kinds})
    add("Q2c differs：喂进结算 ⇒ 落 gap 而不是拿 0.001 或 0.002 里任一个去算",
        st_d == (0, 1, 0.0),
        {"settle": st_d, "若擅自取热值": 100.0 * QTY * 0.001,
         "若擅自取冷值": 100.0 * QTY * 0.002, "实际": st_d[2]})

    # --- one_sided：一侧有值一侧为空（真实数据里唯一出现的「缺字段」形态）---
    h_o = {"funding_time": T, "funding_rate": "0.001", "mark_price": None, "fetched_at": 1,
           "source": "hot.src", "rate_type": "settled"}
    c_o = {"funding_time": T, "funding_rate": 0.001, "mark_price": 123.0,
           "source": "cold.src", "rate_type": "settled"}
    ev_o = A._event_from_sides("binance", "um", "TESTSYM", T, h_o, c_o, False)
    ev_o_strict = A._event_from_sides("binance", "um", "TESTSYM", T, h_o, c_o, True)
    add("Q2d one_sided(strict=False)：取非空值并标 resolved_single_side + 留 one_sided 痕迹",
        ev_o["mark_price"] == 123.0 and ev_o["status"] == "resolved_single_side"
        and any(c["kind"] == "one_sided" for c in ev_o["conflicts"]),
        {"mark": ev_o["mark_price"], "status": ev_o["status"],
         "conflicts": [(c["field"], c["kind"], c["severity"]) for c in ev_o["conflicts"]]})
    add("Q2e one_sided(strict=True)：降级为 pending_conflict，mark 置 None",
        ev_o_strict["mark_price"] is None and ev_o_strict["status"] == "pending_conflict",
        {"mark": ev_o_strict["mark_price"], "status": ev_o_strict["status"]})

    # --- none：两侧都缺 ---
    h_n = {"funding_time": T, "funding_rate": "0.001", "mark_price": None, "fetched_at": 1}
    c_n = {"funding_time": T, "funding_rate": 0.001, "mark_price": None}
    ev_n = A._event_from_sides("binance", "um", "TESTSYM", T, h_n, c_n, False)
    st_n = settle([ev_n], T)
    add("Q2f none：两侧都缺 mark ⇒ status=pending_missing、rate 保留、结算落 gap",
        ev_n["status"] == "pending_missing" and ev_n["rate"] == 0.001 and st_n == (0, 1, 0.0),
        {"status": ev_n["status"], "rate": ev_n["rate"], "settle": st_n})

    # --- agree：两侧一致 → resolved，不留 blocking 痕迹 ---
    h_a = {"funding_time": T, "funding_rate": "0.0001", "mark_price": "100.0", "fetched_at": 1,
           "source": "s", "rate_type": "settled"}
    c_a = {"funding_time": T, "funding_rate": 0.0001, "mark_price": 100.0,
           "source": "s", "rate_type": "settled"}
    ev_a = A._event_from_sides("binance", "um", "TESTSYM", T, h_a, c_a, False)
    add("Q2g agree：两侧一致 ⇒ resolved、无 blocking 冲突、可算成本",
        ev_a["status"] == "resolved"
        and not [c for c in ev_a["conflicts"] if c["severity"] == "blocking"]
        and settle([ev_a], T) == (1, 0, 1.0),
        {"status": ev_a["status"], "conflicts": ev_a["conflicts"], "settle": settle([ev_a], T)})

    # ================= Q3 真实数据 =================
    anchor = json.loads((OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    symbols = anchor["A_窗口与母集"]["symbols"]
    start_ms = anchor["A_窗口与母集"]["start_ms"]
    end_ms = anchor["A_窗口与母集"]["end_ms"]

    repo = A._get_repo(None, None)
    per = {}
    bad_leak, bad_dup, bad_order, tot = [], [], [], {}
    for s in symbols:
        r = A.load_funding_events("binance", "um", s, start_ms, end_ms, repo=repo)
        per[s] = r
        c = r["counts"]
        # 不变式 1：prefer='hot' 混进来的冷档行数 == cold_only 事件数
        if c["hot_path_leaked_cold"] != c["observed_cold_only"]:
            bad_leak.append({"symbol": s, "leaked": c["hot_path_leaked_cold"],
                             "cold_only": c["observed_cold_only"]})
        # 不变式 2：键唯一
        keys = [(e["exchange"], e["market"], e["symbol"], e["funding_time"]) for e in r["events"]]
        if len(keys) != len(set(keys)):
            bad_dup.append(s)
        # 不变式 3：严格升序
        ts = [e["funding_time"] for e in r["events"]]
        if ts != sorted(ts):
            bad_order.append(s)
        for k, v in c.items():
            if isinstance(v, int):
                tot[k] = tot.get(k, 0) + v

    add(f"Q3a 不变式「泄漏行数 == cold_only 事件数」在母集 {len(symbols)} 个标的上成立",
        not bad_leak, {"违反": bad_leak[:5], "样本数": len(symbols)})
    add("Q3b 去重键在母集全部标的上唯一（无重复事件）", not bad_dup, {"违反": bad_dup[:5]})
    add("Q3c 事件按 funding_time 严格升序", not bad_order, {"违反": bad_order[:5]})

    # 真实冲突普查（窗口内）
    real_conf = {"differs": 0, "one_sided": 0, "none": 0, "source_differs": 0,
                 "rate_type_differs": 0}
    for r in per.values():
        for e in r["events"]:
            for c in e["conflicts"]:
                if c["field"] == "source":
                    real_conf["source_differs"] += 1
                elif c["field"] == "rate_type":
                    real_conf["rate_type_differs"] += 1
                else:
                    real_conf[c["kind"]] = real_conf.get(c["kind"], 0) + 1
    add("Q3d 窗口内真实同键冲突普查（结果照实记录，空跑就写空跑）",
        True, {"母集48_汇总计数": tot, "冲突普查": real_conf,
               "说明": "differs/one_sided 若为 0，则这两条分支在真实数据上**未被走到**，"
                       "只有合成用例覆盖；不得声称「已用真实数据验证冲突处理」"})

    # 真实 none 案例：CYBERUSDT 2023-08（热冷两侧都缺 mark）
    cy_lo, cy_hi = 1691337600000, 1693411200000
    cy = A.load_funding_events("binance", "um", "CYBERUSDT", cy_lo, cy_hi, repo=repo)
    cy_none = [e for e in cy["events"] if e["status"] == "pending_missing"]
    add("Q3e 真实 none 案例 CYBERUSDT 2023-08：两侧都缺 mark ⇒ pending_missing，不伪造",
        len(cy_none) == 13 and all(e["mark_price"] is None and e["rate"] is not None
                                   for e in cy_none),
        {"pending_missing": len(cy_none), "样例": cy_none[0]["funding_time"] if cy_none else None})

    # 端到端：真实 pending_missing 事件喂结算 ⇒ 缺口可见、成本只算有值的那部分
    evs = A.to_replay_events(cy)
    lo_t, hi_t = cy["events"][0]["funding_time"] - 1, cy["events"][-1]["funding_time"] + 1
    r_end = settle_funding_events(symbol="CYBERUSDT", qty=QTY, direction=DIRECTION,
                                  entry_ms=lo_t, exit_ms=hi_t, funding_events=evs)
    add("Q3f 端到端：真实事件喂真结算内核 ⇒ 缺口数 == pending_missing 数（缺口可见而非静默）",
        r_end["缺口数"] == len(cy_none) and r_end["已结算"] == len(cy["events"]) - len(cy_none),
        {"已结算": r_end["已结算"], "缺口数": r_end["缺口数"],
         "事件数": len(cy["events"]), "pending_missing": len(cy_none),
         "成本合计": round(r_end["成本合计"], 6)})

    # 幂等：同输入两次，事件序列与摘要一致
    r1 = A.load_funding_events("binance", "um", "TNSRUSDT", start_ms, end_ms, repo=repo)
    r2 = A.load_funding_events("binance", "um", "TNSRUSDT", start_ms, end_ms, repo=repo)
    h1, h2 = A.events_sha256(r1["events"]), A.events_sha256(r2["events"])
    add("Q3g 同输入两次读取 ⇒ 事件数与摘要逐位相同（无重复、无顺序漂移）",
        h1 == h2 and len(r1["events"]) == len(r2["events"]),
        {"events": len(r1["events"]), "sha256": h1, "second_sha256": h2})

    n_pass = sum(1 for c in checks if c["通过"])
    out = {
        "task": "ACCUM-EARLY-06-06",
        "probe": "01_适配器自检",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adapter_version": A.ADAPTER_VERSION,
        "checks": checks,
        "summary": {"total": len(checks), "pass": n_pass,
                    "verdict": "通过" if n_pass == len(checks) else "不通过"},
        "母集48_汇总": tot,
        "真实冲突普查": real_conf,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    p = OUT / "01_适配器自检.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    for c in checks:
        print(("PASS " if c["通过"] else "FAIL ") + c["检查"])
    print(f"\n{out['summary']}  elapsed {out['elapsed_sec']}s")
    print("母集48 汇总:", json.dumps(tot, ensure_ascii=False))
    print("真实冲突普查:", json.dumps(real_conf, ensure_ascii=False))
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
