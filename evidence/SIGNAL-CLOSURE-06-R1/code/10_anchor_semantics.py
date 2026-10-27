# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜10 锚点的持久化语义 + 历史保留范围自检。

工单第 1 条末段明确要求：
  「如采用固定锚点或检查点，说明锚点如何持久保存、重启如何复用、
    历史超过保留范围怎么办。**不能每次启动重新计算一个"固定起点"**。」

本脚本回答这四件事，全部跑生产函数，不另写一套：

  ① **怎么保存**：`live_store.ensure_state_anchor_ms()` 把锚点写进 `live_run` 表
     （key=`state_anchor_ms`，value=毫秒时间戳），库是 `LIVE_DB`。
  ② **重启怎么复用**：同一个函数第二次调用**直接读回**既有值，不覆盖。
     本脚本在隔离库里真跑：落定一次 → 晚 7h 再调 → 换更小 depth 再调，
     三次必须返回同一个值。
  ③ **能不能被悄悄改**：唯一入口是 `reset_state_anchor_ms(new, reason)`，
     它会把 `from/to/reason/at` 写进 `state_anchor_reset_log`。本脚本验证留痕。
  ④ **历史超过保留范围怎么办**：算出「锚点是否落在数据实际可读范围内」。
     判定式（每币每周期）：`first_open_time <= anchor` ⇒ 该币锚点被覆盖。
     若某周期**全局最早一根**都晚于锚点 ⇒ 锚点已越界，此时 bootstrap 会用
     「实际能读到的最早数据」重建，等于**锚点静默失效**，重启一致性不再有保证。
     这种情况必须显式重锚，不能靠「每次启动重算」糊过去。

用法：python code/10_anchor_semantics.py [--anchor-hours 2880]
产出：out/10_锚点语义与覆盖.json、out/10_锚点覆盖逐币.csv
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT
H1, H4 = cm.H1, cm.H4


def main() -> int:
    argv = sys.argv[1:]
    anchor_h = 2880
    for i, a in enumerate(argv):
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])

    upto = cm.last_closed_1h_close_ms()
    # ── 隔离 live 库：不碰生产 ────────────────────────────────────────────
    iso = OUT / "10_iso_live"
    iso.mkdir(parents=True, exist_ok=True)
    live_db = iso / "live.sqlite3"
    if live_db.exists():
        live_db.unlink()
    os.environ["LIVE_DB"] = str(live_db)
    os.environ["no_proxy"] = "*"
    import live_store as L

    replay_ms = max(int(cm.warmup_ms()), anchor_h * H1)
    steps = []

    # ① 首次落定
    a1 = L.ensure_state_anchor_ms(replay_ms, upto)
    steps.append({"step": "1_首次落定", "call_now_utc": cm.utc(upto),
                  "depth_hours": anchor_h, "returned_utc": cm.utc(a1)})
    # ② 晚 7 小时再调（模拟重启）
    a2 = L.ensure_state_anchor_ms(replay_ms, upto + 7 * H1)
    steps.append({"step": "2_晚7h再调_模拟重启", "call_now_utc": cm.utc(upto + 7 * H1),
                  "depth_hours": anchor_h, "returned_utc": cm.utc(a2)})
    # ③ 换更小 depth（模拟改环境变量）—— 仍必须读回旧锚点
    a3 = L.ensure_state_anchor_ms(max(int(cm.warmup_ms()), 1160 * H1), upto + 100 * H1)
    steps.append({"step": "3_改depth=1160h再调", "call_now_utc": cm.utc(upto + 100 * H1),
                  "depth_hours": 1160, "returned_utc": cm.utc(a3)})
    # ④ 显式重锚（唯一允许改的入口），必须留痕
    new_anchor = upto - 60 * 24 * H1
    rl = L.reset_state_anchor_ms(new_anchor, reason="R1 自检：验证重锚留痕")
    a4 = L.get_state_anchor_ms()
    steps.append({"step": "4_显式重锚60天前", "call_now_utc": cm.utc(upto + 100 * H1),
                  "depth_hours": None, "returned_utc": cm.utc(a4)})
    # 复原成第 1 次的值，避免影响后续脚本
    L.reset_state_anchor_ms(a1, reason="R1 自检：复原")

    stable = (a1 == a2 == a3)
    reset_log = L.get_run("state_anchor_reset_log") if hasattr(L, "get_run") else None

    # ── ⑤ 覆盖自检：锚点 vs 真实可读数据 ─────────────────────────────────
    # **必须用冷+热合并口径**：热库 `candles` 视图只保留约 45 天（本脚本实测），
    # 锚点在 120 天前 ⇒ 只查热库会得出「0/526 覆盖」的假结论（本脚本第一版就踩了）。
    anchor = a1
    con = cm.market_conn()
    try:
        gmin = {}
        for tf in ("4h", "1h"):
            r = con.execute(
                "SELECT MIN(open_time) FROM candles WHERE exchange='binance' AND market='um' "
                "AND timeframe=? AND is_closed=1", (tf,)).fetchone()
            gmin[tf] = int(r[0]) if r and r[0] is not None else None
        hot_first = {}
        for tf in ("4h", "1h"):
            rows = con.execute(
                "SELECT symbol, MIN(open_time) FROM candles WHERE exchange='binance' "
                "AND market='um' AND timeframe=? AND is_closed=1 GROUP BY symbol", (tf,)).fetchall()
            hot_first[tf] = {s: int(m) for s, m in rows if m is not None}
    finally:
        con.close()

    # 冷库：从 `_catalog.json` 的分片名取「每个标的/周期最早出现的年份」
    cold_first_year = {"4h": {}, "1h": {}}
    cold_years = {"4h": set(), "1h": set()}
    cat_p = cm.BASE / "数据" / "市场数据" / "parquet" / "_catalog.json"
    if cat_p.exists():
        cat = json.loads(cat_p.read_text(encoding="utf-8"))
        for key in cat.get("shards", {}):
            parts = key.split("/")
            # 形如 candles/binance/um/<SYM>/<tf>/<YYYY>.parquet
            if len(parts) != 6 or parts[0] != "candles":
                continue
            tf = parts[4]
            if tf not in cold_first_year:
                continue
            try:
                yr = int(parts[5].split(".")[0])
            except ValueError:
                continue
            sym = parts[3]
            cold_years[tf].add(yr)
            prev = cold_first_year[tf].get(sym)
            if prev is None or yr < prev:
                cold_first_year[tf][sym] = yr

    anchor_year = int(cm.utc(anchor)[:4])
    cold_min_year = {tf: (min(v) if v else None) for tf, v in cold_years.items()}
    # 越界判定：冷库最早年份都晚于锚点所在年份 ⇒ 锚点已早于冷库覆盖
    out_of_range = {tf: (cold_min_year[tf] is not None and cold_min_year[tf] > anchor_year)
                    for tf in cold_min_year}

    syms, _meta = cm.universe()
    rows = []
    for s in syms:
        f4h = hot_first["4h"].get(s)
        c4y = cold_first_year["4h"].get(s)
        rows.append({
            "symbol": s,
            "hot_first_4h_open_utc": cm.utc(f4h),
            "cold_first_4h_year": c4y,
            "anchor_utc": cm.utc(anchor),
            # 「热库覆盖到锚点」几乎必然为假（热库只有 45 天），真正判据是冷库年份
            "hot_covers_anchor": (f4h is not None and f4h <= anchor),
            "cold_covers_anchor_year": (c4y is not None and c4y <= anchor_year),
            "only_2026_shards": (c4y == 2026),
        })

    n_hot = sum(1 for r in rows if r["hot_covers_anchor"])
    n_cold = sum(1 for r in rows if r["cold_covers_anchor_year"])
    only2026 = [r["symbol"] for r in rows if r["only_2026_shards"]]

    uncovered = [r["symbol"] for r in rows if not r["cold_covers_anchor_year"]]
    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "anchor_hours": anchor_h,
        "live_db": str(live_db),
        "storage": {
            "table": "live_run", "key": L.STATE_ANCHOR_KEY,
            "written_value_ms": anchor, "written_value_utc": cm.utc(anchor),
            "note": "锚点写在 LIVE_DB 的 live_run 表，key=state_anchor_ms；不写策略参数。",
        },
        "steps": steps,
        "checks": {
            "锚点三次调用恒等（不随后移/不随depth变化）": stable,
            "重锚留痕已写入": bool(reset_log),
            "锚点未被越界（4h）": not out_of_range["4h"],
            "锚点未被越界（1h）": not out_of_range["1h"],
        },
        "reset_log_sample": reset_log,
        "coverage": {
            "n_symbols": len(syms),
            "hot_covers_anchor_n": n_hot,
            "cold_covers_anchor_year_n": n_cold,
            "n_uncovered": len(uncovered),
            "uncovered_sample": uncovered[:30],
            "only_2026_shards_n": len(only2026),
            "only_2026_shards_sample": only2026[:20],
            "hot_global_first_4h_utc": cm.utc(gmin["4h"]),
            "hot_global_first_1h_utc": cm.utc(gmin["1h"]),
            "cold_shard_years_4h": sorted(cold_years["4h"])[:8],
            "cold_min_year": cold_min_year,
            "out_of_range": out_of_range,
            "note": ("热库 `candles` 只保留约 45 天，锚点在 120 天前 ⇒ **只查热库必然得出"
                     "「未覆盖」的假结论**；真实数据源是冷层 parquet + 热库合并。"
                     "最终覆盖判据以 11 号脚本的 `n_ready`（每场景成功重建的标的数）为准。"),
        },
        "retention_policy": {
            "判定式": "anchor 所在年份 >= 冷库该周期最早分片年份 ⇒ 锚点在保留范围内",
            "越界后果": ("bootstrap 会用实际能读到的最早数据重建，锚点**静默失效**，"
                        "重启一致性不再有保证（退化回「窗口随重启平移」）"),
            "当前状态": ("未越界" if not any(out_of_range.values()) else "**已越界，必须重锚**"),
            "处置": ("越界前应调用 reset_state_anchor_ms(新值, reason) 显式重锚并留痕；"
                    "不允许改成「每次启动重算」——那等于取消锚点。"),
            "余量": (f"锚点 {cm.utc(anchor)} 所在年份 {anchor_year}，"
                    f"冷库 4h 最早分片年份 {cold_min_year.get('4h')}、"
                    f"1h 最早分片年份 {cold_min_year.get('1h')}"),
        },
        "verdict": "PASS" if (stable and all(not v for v in out_of_range.values())) else "FAIL",
    }
    cm.dump("10_锚点语义与覆盖.json", res)
    cm.dump_csv("10_锚点覆盖逐币.csv", rows)
    print(f"[verdict] {res['verdict']}")
    for k, v in res["checks"].items():
        print(f"    {'OK ' if v else 'BAD'} {k}")
    print(f"    热库覆盖 {n_hot}/{len(syms)}（热库只留约 45 天，本来就不该覆盖 120 天前）")
    print(f"    冷库按年份覆盖 {n_cold}/{len(syms)}；仅 2026 分片的 {len(only2026)} 个")
    print(f"    保留余量：{res['retention_policy']['余量']}")
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
