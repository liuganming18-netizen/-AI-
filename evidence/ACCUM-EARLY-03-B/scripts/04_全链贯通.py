# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜04 全链贯通（真实历史事件：观察 → 候选 → 委托 → 成交/拒绝 → 退出 → 账本）。

工单第 4 条：用真实历史事件在**独立运行**里贯通整条链，历史结果不回填生产账户。

本脚本做两件事：
1. 把「观察/候选」段从**生产记录**里取出来（`evidence/ACCUM-EARLY-01/out/07_事件回放.json`
   的 `1_当时可见数据` / `2_结构确认` / `4_候选`，来源是生产状态机 `discarded_detail`），
   与本单跑出来的「委托→成交→退出→账本」段按 `event_id` 对齐，拼成一条完整链。
2. 每条链都给出**数据来源**（成交 bar 来自冷档还是热档、available_at 有没有证据）、
   **拒绝原因**（如果被拒）、以及「这笔结果能不能进严格有效性成绩」的判定。

纪律：
· 隔离账本（`runs/04_*`），独立进程；`epoch_ms=0` 只用于隔离回放，生产 epoch 不动；
· 三种真实事件的定位不同（冷窗两条 / 热窗对照一条），逐条标注；
· 没有合法成交样本时如实写「无」，不构造交易。

输出：out/04_全链贯通.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
OUT = PROJECT / "evidence" / "ACCUM-EARLY-03-B" / "out"
E01 = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out" / "07_事件回放.json"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(PROJECT / "框架" / "backend"))
sys.path.insert(0, str(PROJECT))

import _harness as H                                          # noqa: E402


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def load_production_candidates() -> dict:
    """生产记录里的候选（按 event_id 索引）。"""
    if not E01.exists():
        return {}
    d = json.loads(E01.read_text(encoding="utf-8"))
    out = {}
    for ch in d.get("chains") or []:
        eid = ch.get("event_id")
        if not eid:
            continue
        out[eid] = {
            "candidate_id": ch.get("candidate_id"),
            "symbol": ch.get("symbol"),
            "来源": "evidence/ACCUM-EARLY-01/out/07_事件回放.json"
                    "（原始来源=生产状态机 bootstrap.discarded_detail）",
            "观察与候选时可见的数据": ch.get("1_当时可见数据"),
            "结构确认": ch.get("2_结构确认"),
            "候选记录": ch.get("4_候选"),
            "旧口径止损（候选快照）": (ch.get("6_止损") or {}).get("候选快照里的 initial_stop_price"),
        }
    return out


def build_chain(ev: dict, prod: dict, w: dict) -> dict:
    lg = w.get("ledger") or {}
    pos = (lg.get("positions") or [None])[0]
    real = (lg.get("realized") or [None])[0]
    steps = w.get("steps") or []
    opened = next((s for s in steps if s["step"] == "open_position"), {}).get("result", {})
    trail = [s for s in steps if s["step"] == "trailing"]
    exit15 = next((s for s in steps if s["step"] == "exit_15m"), None)
    trail_exit = next((s for s in trail if s.get("exit_triggered")), None)
    orders = lg.get("orders") or []
    fills = lg.get("fills") or []
    prov = w.get("market_provenance") or {}
    p15 = (prov.get("15m") or {})
    p4h = (prov.get("4h") or {})

    avail_proven = (opened.get("bar_provenance") or {}).get("available_at_proven")
    strict_ok = bool(opened.get("ok") and avail_proven)

    return {
        "事件身份": {"symbol": ev["symbol"], "event_id": ev["signal_uid"],
                     "snapshot_utc": ev["snapshot_utc"], "为什么选它": ev["why"]},
        "① 观察与候选（生产记录）": prod or {"note": "生产记录里没有这条事件的可对齐记录"},
        "② 委托（隔离账本，真实 open_position）": {
            "verdict": w.get("verdict"), "reason": opened.get("reason"),
            "reject_reason": w.get("reject_reason"),
            "earliest_fill_ms_utc": iso(opened.get("earliest_fill_ms")),
            "filled_at_utc": iso(opened.get("filled_at")),
            "fill_lag_ms": opened.get("fill_lag_ms"),
            "口径": "成交价 = 第一根 open_time >= max(事件时刻, 可得时刻, 首次请求) + 15 分钟"
                    " 的已闭合 15m bar 的 open",
        },
        "③ 成交": {
            "status": "filled" if opened.get("ok") else "rejected",
            "entry_bar_utc": iso(opened.get("bar_open_time")),
            "raw_open": opened.get("raw_price"),
            "entry_price_after_slippage_and_rounding": opened.get("entry_price"),
            "qty": opened.get("qty"),
            "notional": opened.get("notional"),
            "fee": opened.get("fee"),
            "initial_stop": opened.get("initial_stop_price"),
            "planned_risk": opened.get("planned_risk"),
            "数据来源": opened.get("market_source"),
            "bar_provenance": opened.get("bar_provenance"),
        },
        "④ 退出": {
            "status": (pos or {}).get("status"),
            "exit_bar_utc": (exit15 or {}).get("bar_open_utc")
                            or (trail_exit or {}).get("bar4h_open_utc"),
            "exit_path": ("15m 明细触损" if exit15 else
                          ("4h 收盘击穿生效止损" if trail_exit else "窗口内未退出")),
            "exit_price": (pos or {}).get("exit_price"),
            "exit_reason": (pos or {}).get("exit_reason"),
            "exit_bar_source": (exit15 or {}).get("bar_source")
                               or (trail_exit or {}).get("bar4h_source"),
            "止损轨迹（只上移）": [(s.get("old_stop"), s.get("new_stop"),
                                   iso(s.get("effective_at")), iso(s.get("available_at")))
                                  for s in (lg.get("stop_history") or [])],
            "止损轨迹可用时刻是否有证据": [
                {"effective_utc": iso(s.get("effective_at")),
                 "available_at_utc": iso(s.get("available_at")),
                 "note": "available_at 为空/等于 close_time 时只是占位，不是可得性证据"}
                for s in (lg.get("stop_history") or [])],
        },
        "⑤ 账本": {
            "state": (lg.get("state") or [None])[0],
            "realized": real,
            "orders": orders,
            "fills": fills,
            "counts": lg.get("counts"),
            "pending": lg.get("pending"),
            "blocked": lg.get("blocked"),
        },
        "⑥ 数据来源与可得性": {
            "15m": {"returned_by_source": p15.get("returned_by_source"),
                    "returned_available_at_missing": p15.get("returned_available_at_missing"),
                    "cache_loads": p15.get("cache_loads"),
                    "passthrough_sql_count": p15.get("passthrough_sql_count")},
            "4h": {"returned_by_source": p4h.get("returned_by_source"),
                   "returned_available_at_missing": p4h.get("returned_available_at_missing")},
            "结论": ("成交与退出 bar 来自冷档；冷档 available_at 填充率 0 ⇒ "
                     "「计算没用到未来 K 线」成立，但「当时确实拿得到这根 bar」**没有证明**"
                     if not avail_proven else
                     "成交 bar 来自热库且 available_at 有值 ⇒ 可得性有证据"),
        },
        "⑦ 能否进严格有效性成绩": {
            "eligible": strict_ok,
            "reason": ("可以（成交 bar 的可得时刻有证据）" if strict_ok else
                       "不可以：成交 bar 的 available_at 无证据 ⇒ 只能算「计算不超前」，"
                       "不能算「当时可得」"),
        },
        "错误": w.get("errors"),
    }


def main() -> int:
    rep = {"probe": "04_全链贯通", "generated_at_utc": iso(time.time() * 1000),
           "隔离方式": {
               "账本": "PAPER_LEDGER_DIR = evidence/ACCUM-EARLY-03-B/runs/04_*/paper",
               "行情库": "MARKET_DB 只读指向生产热库；WX_ARCHIVE_DIR 指向生产冷档",
               "闸门": "PAPER_GATE_FILE 在隔离目录内自建（生产闸门文件不动）",
               "回放时钟": "origin=replay + epoch_ms=0（仅隔离账本；生产 epoch 不可下调）",
           },
           "chains": {}, "checks": {}, "errors": []}
    rep["生产账本指纹_跑前"] = H.production_ledger_fingerprint()
    prod = load_production_candidates()

    for ev in H.EVENTS:
        for mode in ("merge", "hot"):
            name = f"04_{ev['key']}_{mode}"
            try:
                w = H.run_job(H.event_job(ev, mode=mode), name, mode=mode)
            except Exception as exc:                         # noqa: BLE001
                rep["errors"].append({"event": ev["key"], "mode": mode,
                                      "error": f"{type(exc).__name__}: {exc}"})
                continue
            chain = build_chain(ev, prod.get(ev["signal_uid"]), w)
            chain["模式"] = mode
            chain["账本身份"] = H.ledger_identity(name)
            rep["chains"][f"{ev['key']}::{mode}"] = chain

    # ── 判据 ─────────────────────────────────────────────────────────────
    for ev in H.EVENTS:
        k_m, k_h = f"{ev['key']}::merge", f"{ev['key']}::hot"
        cm, ch = rep["chains"].get(k_m), rep["chains"].get(k_h)
        if not cm or not ch:
            rep["checks"][ev["key"]] = {"结果": "未跑成", "说明": "见 errors"}
            continue
        filled = cm["③ 成交"]["status"] == "filled"
        closed = cm["④ 退出"]["status"] == "closed"
        ledger_ok = bool((cm["⑤ 账本"]["counts"] or {}).get("realized"))
        if ev["expect_layer"] == "cold":
            # 冷窗事件：只读热库必须**跑不通**（这是缺陷存在过的证据）
            contrast_ok = ch["③ 成交"]["status"] == "rejected"
            contrast_label = "对照：只读热库时被拒（缺陷复现）"
        else:
            # 热窗对照事件：只读热库本来就该跑通（证明对照臂有效，不是恒拒）
            contrast_ok = ch["④ 退出"]["status"] == "closed"
            contrast_label = "对照：只读热库也能跑通（对照组有效）"
        rep["checks"][ev["key"]] = {
            "① 观察/候选有生产记录": bool(prod.get(ev["signal_uid"])),
            "② 委托在隔离账本内": True,
            "③ 成交（合并模式）": filled,
            "④ 退出并平仓（合并模式）": closed,
            "⑤ 账本落了已实现记录": ledger_ok,
            contrast_label: contrast_ok,
            "⑦ 可进严格有效性成绩": cm["⑦ 能否进严格有效性成绩"]["eligible"],
            "结果": ("通过（链路贯通）" if (filled and closed and ledger_ok and contrast_ok)
                     else "不通过"),
        }

    rep["生产账本指纹_跑后"] = H.production_ledger_fingerprint()
    changed = [a for a in rep["生产账本指纹_跑前"]
               if rep["生产账本指纹_跑前"][a].get("sha256")
               != rep["生产账本指纹_跑后"][a].get("sha256")]
    rep["生产账本是否被改动"] = {"changed_arms": changed,
                          "verdict": "未被改动" if not changed else "被改动（违规）"}

    # ── 汇总：到底有没有「合法成交样本」──────────────────────────────────
    n_filled = sum(1 for k, c in rep["chains"].items()
                   if k.endswith("::merge") and c["③ 成交"]["status"] == "filled")
    n_strict = sum(1 for k, c in rep["chains"].items()
                   if k.endswith("::merge") and c["⑦ 能否进严格有效性成绩"]["eligible"])
    rep["合法成交样本"] = {
        "合并模式下成交笔数": n_filled,
        "其中可进严格有效性成绩": n_strict,
        "逐笔": [{"事件": k, "成交": c["③ 成交"]["status"],
                  "成交bar来源": (c["③ 成交"]["bar_provenance"] or {}).get("layer"),
                  "可得时刻有证据": (c["③ 成交"]["bar_provenance"] or {}).get("available_at_proven"),
                  "净额": (c["⑤ 账本"]["realized"] or {}).get("net_pnl"),
                  "可进严格成绩": c["⑦ 能否进严格有效性成绩"]["eligible"]}
                 for k, c in rep["chains"].items() if k.endswith("::merge")],
        "说明": (f"合并模式下共成交 {n_filled} 笔，其中 {n_strict} 笔的成交 bar 可得时刻有证据。"
                 "其余成交 bar 来自冷档，冷档 available_at 填充率为 0 ⇒ 只能声称「计算不超前」，"
                 "不能声称「当时可得」，因此不进严格有效性成绩。"),
        "不夸大的说法": ("本单证明了「真实历史事件能在隔离环境里走完观察→候选→委托→成交→退出→账本」；"
                         "**没有**证明「自然行情下会自动成交」——后者需要真实挂钟与自然候选，"
                         "当前自然候选为 0，账本里也没有自然成交。"),
    }
    rep["总判定"] = ("通过（链路贯通；严格有效性仍不可声明）"
                     if all(v.get("结果", "").startswith("通过") for v in rep["checks"].values())
                     and not changed else "不通过")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "04_全链贯通.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"checks": rep["checks"], "合法成交样本": rep["合法成交样本"],
                      "生产账本": rep["生产账本是否被改动"]["verdict"],
                      "总判定": rep["总判定"], "errors": rep["errors"]},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
