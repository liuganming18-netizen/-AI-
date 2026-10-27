# -*- coding: utf-8 -*-
"""10｜现状快照（只读）：289 条研究记录 + 现有 API/页面呈现 + 开工前文件指纹。

不写任何库、不改任何文件。输出：
  out/10_研究记录_289.json   逐条字段（uid/来源/身份/状态/时刻/原因）
  out/10_现状快照.json       汇总 + API/页面基线 + 开工前文件指纹
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib09 as L  # noqa: E402

PAGE = Path(__file__).resolve().parent / "page09.mjs"


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}
    L.OUT.mkdir(parents=True, exist_ok=True)

    # ── 开工前文件指纹（本单允许触及的三个文件 + 生产数据）──────────────
    rep["开工前_允许触及文件"] = {k: L.fingerprint(p) for k, p in L.TOUCH_FILES.items()}
    rep["开工前_生产数据"] = {
        "信号库": L.fingerprint(L.LIVE_DB),
        "行情库": L.fingerprint(L.MARKET_DB),
        **{f"账本_{a}": L.fingerprint(L.PAPER_DIR / f"paper_{a}.sqlite3")
           for a in ("3a", "3b", "3c", "3d")},
    }
    rep["08_R1_依赖"] = {
        "契约": L.fingerprint(L.WORK08 / "接口契约_R1.md"),
        "报告": L.fingerprint(L.WORK08 / "README.md"),
        "导入器": L.fingerprint(L.BACKEND / "research_signal_import.py"),
    }

    # ── 逐条记录 ────────────────────────────────────────────────────────
    rows = L.research_rows(L.LIVE_DB)
    detail = []
    for r in rows:
        pl = json.loads(r.get("payload_json") or "{}")
        ts = pl.get("timestamps") or {}
        detail.append({
            "signal_uid": r["signal_uid"],
            "source": r["source"],
            "origin": r["origin"],
            "symbol": r["symbol"],
            "timeframe": r["timeframe"],
            "strategy_key": r["strategy_key"],
            "strategy_version": r["strategy_version"],
            "config_hash": r["config_hash"],
            "data_version": r["data_version"],
            "structural_kind": r["structural_kind"],
            "direction": r["direction"],
            "display_state": r["display_state"],
            "executable_state": r["executable_state"],
            "executable_reason": r["executable_reason"],
            "event_time": r["event_time"],
            "confirmed_at": r["confirmed_at"],
            "available_at": r["available_at"],
            "bar_open_time": r["bar_open_time"],
            "bar_close_time": r["bar_close_time"],
            "first_seen_at": r["first_seen_at"],
            "updated_at": r["updated_at"],
            "payload": {
                "run_id": pl.get("run_id"),
                "scheme": pl.get("scheme"),
                "role": pl.get("role"),
                "approved": pl.get("approved"),
                "approval_blockers": pl.get("approval_blockers"),
                "config_kind": pl.get("config_kind"),
                "config_sha256": pl.get("config_sha256"),
                "config_approval_ref": pl.get("config_approval_ref"),
                "engine_view_sha256": pl.get("engine_view_sha256"),
                "code_hashes_n": len(pl.get("code_hashes") or {}),
                "code_drift": pl.get("code_drift"),
                "gaps_n": len(pl.get("gaps") or []),
                "data_version_note": pl.get("data_version_note"),
                "available_at_status": ts.get("available_at_status"),
                "available_at_reason": ts.get("available_at_reason"),
                "entry_bar_open_ms": ts.get("entry_bar_open_ms"),
                "source_file": pl.get("source_file"),
                "source_file_sha256": pl.get("source_file_sha256"),
            },
        })
    L.dump("10_研究记录_289.json", {"生成时刻": L.now_utc(), "n": len(detail), "rows": detail})

    # 汇总（按 uid 唯一性、方案分布、状态分布）
    uids = [d["signal_uid"] for d in detail]
    rep["逐条汇总"] = {
        "n": len(detail),
        "uid_唯一": len(set(uids)) == len(uids),
        "uid_重复数": len(uids) - len(set(uids)),
        "按方案": {k: sum(1 for d in detail if d["strategy_key"] == k)
                for k in sorted({d["strategy_key"] for d in detail})},
        "按可执行态": {k: sum(1 for d in detail if d["executable_state"] == k)
                  for k in sorted({d["executable_state"] for d in detail})},
        "按来源": {k: sum(1 for d in detail if d["origin"] == k)
                for k in sorted({d["origin"] for d in detail})},
        "approved=false 条数": sum(1 for d in detail if d["payload"]["approved"] is False),
        "role=research_only 条数": sum(1 for d in detail if d["payload"]["role"] == "research_only"),
        "available_at 为空条数": sum(1 for d in detail if d["available_at"] is None),
        "availability=unproven 条数":
            sum(1 for d in detail if d["payload"]["available_at_status"] == "unproven"),
        "run_id 取值": sorted({d["payload"]["run_id"] for d in detail}),
        "config_hash 取值": sorted({d["config_hash"] for d in detail if d["config_hash"]}),
        "引擎视图 sha": sorted({d["payload"]["engine_view_sha256"] for d in detail
                            if d["payload"]["engine_view_sha256"]}),
        "带缺口说明条数": sum(1 for d in detail if (d["payload"]["gaps_n"] or 0) > 0),
    }

    # ── 现有 API 呈现 ───────────────────────────────────────────────────
    r_all = L.get_json(f"{L.API}/api/v1/signals?scope=all&origin=historical_research&limit=1000")
    r_cur = L.get_json(f"{L.API}/api/v1/signals?scope=current&limit=500")
    items = (r_all.get("json") or {}).get("items") or []
    cur_items = (r_cur.get("json") or {}).get("items") or []
    rep["现有API"] = {
        "研究范围": {"status": r_all.get("status"), "items_n": len(items),
                 "has_more": (r_all.get("json") or {}).get("has_more"),
                 "total字段": (r_all.get("json") or {}).get("total"),
                 "顶层身份": {k: (r_all.get("json") or {}).get(k) for k in
                          ("strategy_version", "rule_version", "param_hash")},
                 "条目字段名": sorted(items[0].keys()) if items else [],
                 "条目是否已有research子对象":
                     bool(items) and "research" in items[0]},
        "当前区": {"status": r_cur.get("status"), "items_n": len(cur_items),
                "excluded_n": ((r_cur.get("json") or {}).get("quality") or {}).get("excluded_n")},
        "条目级身份（首条）": {k: (items[0] or {}).get(k) for k in
                        ("signal_id", "origin", "role", "executable_state",
                         "strategy_key", "strategy_version", "config_hash",
                         "available_at", "actionable_reason")} if items else None,
    }

    # ── 现有页面呈现（生产前端，只读）────────────────────────────────────
    png = L.OUT / "10_页面_改动前.png"
    pr = L.run([L.NODE, str(PAGE), L.WEB_PROD, str(png)], timeout=300)
    page = None
    for line in (pr["stdout"] or "").splitlines():
        if line.startswith("@@PAGE@@"):
            page = json.loads(line[len("@@PAGE@@"):])
    rep["现有页面"] = {"rc": pr["returncode"], "stderr_tail": (pr["stderr"] or "")[-800:]}
    if page:
        rep["现有页面"].update({
            "面板存在": page.get("panel_found"),
            "当前区行数": page.get("current_rows"),
            "研究区是否存在": page.get("research_section_present"),
            "研究行是否混入当前区": page.get("current_area_has_research"),
            "页面请求": [x for x in (page.get("signals_responses") or []) if "/signals?" in x.get("url", "")],
            "截图": {"path": str(png), "exists": png.exists(),
                   "bytes": png.stat().st_size if png.exists() else None},
        })
    else:
        rep["现有页面"]["parse_error"] = True

    # ── 服务状态（不改）────────────────────────────────────────────────
    rep["服务状态"] = {
        "生产API": L.wait_http(f"{L.API}/api/v1/health", 10),
        "生产前端": L.wait_http(L.WEB_PROD, 10),
        "后端pid文件": (L.BACKEND / "backend.pid").read_text().strip()
        if (L.BACKEND / "backend.pid").exists() else None,
    }

    # ── 判定 ────────────────────────────────────────────────────────────
    pg = rep["现有页面"]
    checks = {
        "生产库恰有 289 条研究记录": len(detail) == 289,
        "289 条 uid 无重复": len(set(uids)) == len(uids) == 289,
        "全部 origin=historical_research": rep["逐条汇总"]["按来源"] == {"historical_research": 289},
        "全部 executable_state=historical_not_executable":
            rep["逐条汇总"]["按可执行态"] == {"historical_not_executable": 289},
        "全部 approved=false": rep["逐条汇总"]["approved=false 条数"] == 289,
        "全部 role=research_only": rep["逐条汇总"]["role=research_only 条数"] == 289,
        "全部 available_at 为空且标 unproven":
            rep["逐条汇总"]["available_at 为空条数"] == 289
            and rep["逐条汇总"]["availability=unproven 条数"] == 289,
        "run_id / config_hash / 引擎视图 sha 各只有一个取值":
            len(rep["逐条汇总"]["run_id 取值"]) == 1
            and len(rep["逐条汇总"]["config_hash 取值"]) == 1
            and len(rep["逐条汇总"]["引擎视图 sha"]) == 1,
        "API 研究范围读到 289 且 has_more=false":
            len(items) == 289 and (r_all.get("json") or {}).get("has_more") is False,
        "API 当前区 0 条": len(cur_items) == 0,
        "改动前 API 条目**没有** research 子对象（待本单补）":
            rep["现有API"]["研究范围"]["条目是否已有research子对象"] is False,
        "改动前页面**没有**研究区": pg.get("研究区是否存在") == 0,
        "改动前研究行未混入当前区": pg.get("研究行是否混入当前区") == 0,
        "生产 API 健康": rep["服务状态"]["生产API"].get("ok") is True,
        "生产前端健康": rep["服务状态"]["生产前端"].get("ok") is True,
    }
    rep["判定"] = L.summarise(checks, "现状已如实记录：289 条研究记录字段完整、API 可读、页面尚无研究区。",
                              "存在未通过项")
    p = L.dump("10_现状快照.json", rep)
    L.print_checks("10 现状快照", rep, p)
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
