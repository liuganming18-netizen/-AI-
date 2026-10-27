# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜12 跨新增小时线的逐币阶段效果。

工单第 5 条要求「跨至少一根新增小时线核对实际处理、阶段变化和候选结果」。
本脚本把三件事分开证明，不混在一起：

  A. **实际处理**：生产水位是否真的前进（`06_新增K线观察.json` 的 watermark 前后），
     且滞后是否为 0 —— 这是「处理了」，不是「时间过去了」。
  B. **阶段变化**：同一份行情、两个相邻截止时刻（前 = 生产重启后水位，
     后 = 新增 1h 收盘）各回放一次，逐币比 `state` 与 `stage_entered_utc`。
  C. **候选结果**：生产自报 `funnel.since_start` 是否出现 CANDIDATE；
     以及离线回放在新截止下候选集合相对前一截止的增量（必须是 0 或可解释）。

用法（回放侧产物由 02 带 `--upto` 生成，文件名自动带后缀，不覆盖旧证据）：
    python code/12_new_bar_effect.py \
        --before out/07_当前卡点.csv \
        --after  out/02_逐币归因_upto<ms>.csv

产出：out/12_新增K线效果.json
"""
from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = Path(__file__).parent.parent / "out"


def resolve(p: str) -> Path:
    """允许传绝对路径 / 相对 out 的文件名 / 带 out/ 前缀的名字，统一落到真实文件。"""
    q = Path(p)
    if q.is_absolute() and q.exists():
        return q
    for cand in (q, OUT / q.name, Path(__file__).parent.parent / q):
        if cand.exists():
            return cand
    return OUT / q.name


def load_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return {r["symbol"]: r for r in csv.DictReader(f)}


def main() -> int:
    argv = sys.argv[1:]
    kw = {}
    for i, a in enumerate(argv):
        if a.startswith("--") and i + 1 < len(argv):
            kw[a[2:]] = argv[i + 1]
    before_p = resolve(kw.get("before", "07_当前卡点.csv"))
    after_p = resolve(kw["after"]) if kw.get("after") else OUT / ""
    if not kw.get("after"):
        print("[ERR] 必须给 --after（02 带 --upto 生成的逐币 CSV）")
        return 2

    before = load_csv(before_p)
    after = load_csv(after_p)
    watch_p = OUT / "06_新增K线观察.json"
    watch = json.loads(watch_p.read_text(encoding="utf-8")) if watch_p.exists() else {}
    samples = watch.get("samples") or []
    w_first = samples[0] if samples else {}
    w_last = samples[-1] if samples else {}

    # ── A. 实际处理 ──────────────────────────────────────────────────────
    a_block = {
        "watermark_from_utc": w_first.get("watermark_1h_utc"),
        "watermark_to_utc": w_last.get("watermark_1h_utc"),
        "watermark_advanced": bool(w_first.get("watermark_1h") and w_last.get("watermark_1h")
                                  and w_last["watermark_1h"] > w_first["watermark_1h"]),
        "db_1h_max_utc": w_last.get("db_1h_max_utc"),
        "aligned_1h": w_last.get("aligned_1h"),
        "lag_1h_bars": w_last.get("lag_1h_bars"),
        "lag_1h_max": w_last.get("lag_1h_max"),
        "n_lag_1h_gt1": w_last.get("n_lag_1h_gt1"),
        "cycle_count_from": w_first.get("cycle_count"),
        "cycle_count_to": w_last.get("cycle_count"),
        "n_samples": watch.get("n_samples"),
    }

    # ── B. 阶段变化（逐币）────────────────────────────────────────────────
    syms = sorted(set(before) & set(after))
    changes = []
    # 空转自证：两个文件必须是「不同截止时刻」，否则 0 变化毫无意义
    n_wm_diff = sum(1 for s in syms
                    if (before[s].get("last_processed_1h_utc") or "")
                    != (after[s].get("last_processed_1h_utc") or ""))
    for s in syms:
        b, a = before[s], after[s]
        bs, as_ = b.get("state"), a.get("state")
        bse, ase = b.get("stage_entered_utc") or "", a.get("stage_entered_utc") or ""
        if bs != as_ or bse != ase:
            changes.append({
                "symbol": s,
                "state_before": bs, "state_after": as_,
                "stage_entered_before": bse or None,
                "stage_entered_after": ase or None,
                "blocked_by_before": b.get("blocked_by") or None,
                "blocked_by_after": a.get("blocked_by") or None,
                "last_processed_1h_before": b.get("last_processed_1h_utc"),
                "last_processed_1h_after": a.get("last_processed_1h_utc"),
            })

    def dist(rows):
        c = {}
        for r in rows.values():
            c[r.get("state")] = c.get(r.get("state"), 0) + 1
        return dict(sorted(c.items()))

    # ── C. 候选结果 ──────────────────────────────────────────────────────
    funnel = w_last.get("funnel_since_start") or {}
    c_block = {
        "prod_funnel_since_start": funnel,
        "prod_new_candidates": funnel.get("CANDIDATE"),
        "prod_orders_routed_total": w_last.get("orders_routed_total"),
        "prod_open_positions": w_last.get("open_positions"),
        "offline_state_before": dist(before),
        "offline_state_after": dist(after),
        "n_symbols_compared": len(syms),
        "n_state_changed": len(changes),
        "changes": changes,
    }

    # ── D. 账本与真实交易（跨新增小时线期间是否被动过）────────────────────
    d_block = {"checked_at_utc": cm.utc(cm.now_ms())}
    try:
        import hashlib
        led = cm.BASE / "数据" / "paper" / "paper_3a.sqlite3"
        cur = hashlib.sha256(led.read_bytes()).hexdigest() if led.exists() else None
        post_p = OUT / "06_生产核验_post.json"
        ref = None
        if post_p.exists():
            _l = json.loads(post_p.read_text(encoding="utf-8")).get("ledger") or {}
            _l = _l.get("ledger", _l)
            ref = ((_l.get("files") or {}).get("paper_3a.sqlite3") or {}).get("sha256")
        d_block.update({"ledger_sha256_now": cur, "ledger_sha256_after_restart": ref,
                        "ledger_unchanged": bool(cur and ref and cur == ref)})
    except Exception as exc:                                 # noqa: BLE001
        d_block["error"] = f"{type(exc).__name__}: {exc}"

    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "before_file": before_p.name,
        "after_file": after_p.name,
        "A_actually_processed": a_block,
        "B_stage_changes": {
            "n_state_changed": len(changes),
            "n_stage_entered_changed": sum(
                1 for c in changes
                if c["stage_entered_before"] != c["stage_entered_after"]),
            "n_last_processed_1h_differs": n_wm_diff,
            "vacuity_guard": ("n_last_processed_1h_differs 必须 > 0，否则两个文件是同一截止，"
                              "「0 变化」等于什么都没比"),
            "changes": changes,
        },
        "C_candidate_result": c_block,
        "D_ledger_and_real_trading": d_block,
        "verdict": {
            "processed": bool(a_block["watermark_advanced"]
                              and a_block["lag_1h_bars"] in (0, 1)
                              and (a_block["n_lag_1h_gt1"] or 0) == 0),
            "no_new_candidate_in_prod": (funnel.get("CANDIDATE") or 0) == 0,
            "real_trading_still_off": (w_last.get("orders_routed_total") or 0) == 0
                                       and (w_last.get("open_positions") or 0) == 0,
        },
        "note": ("B 段是离线复算（同行情、两个相邻截止），只用来解释「这根小时线改变了什么」；"
                 "C 段生产自报的 since_start 才是「实际有没有出新候选」的判据。"
                 "两者口径不同，不能互相替代。"),
    }
    cm.dump("12_新增K线效果.json", res)
    print(f"  A 水位 {a_block['watermark_from_utc']} → {a_block['watermark_to_utc']} "
          f"lag={a_block['lag_1h_bars']} advanced={a_block['watermark_advanced']}")
    print(f"  B 阶段变化 {len(changes)}/{len(syms)} 币；"
          f"进入时刻变化 {res['B_stage_changes']['n_stage_entered_changed']} 币")
    print(f"  C 生产 since_start 候选 = {c_block['prod_new_candidates']}；"
          f"订单 {c_block['prod_orders_routed_total']} / 持仓 {c_block['prod_open_positions']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
