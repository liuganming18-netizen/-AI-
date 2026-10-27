# -*- coding: utf-8 -*-
"""40｜真实 HTTP + 真实页面：研究信号「能查得到、进不去交易区」。

A. HTTP（对生产 8787 发真实 GET，只读）
   · scope=all&origin=historical_research → 289 条，逐条核对可执行态/原因/四时刻
   · scope=current                       → 0 条，且排除明细里能看到 289 被排除
   · scope=all&origin=live               → 0 条（负向对照：origin 过滤真的生效）
   · limit=1/1000                        → 记录 `total` 是「本页条数」不是库内总数
B. 页面（Playwright 只读打开生产前端 3000）
   · 信号面板 0 行、空态正常
   · 页面自己请求的 `/signals?scope=current` 返回 total=0、excluded_n=289（同一本库）
   · 面板内不出现任何研究信号 symbol；body 里的命中项逐个归因到观察区
   · 展开「诊断与历史候选明细」后仍未列出研究信号

输出：out/40_HTTP与页面.json + out/40_页面_当前交易区.png
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

NODE = "/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node"
PAGE = Path(__file__).resolve().parent / "page40.mjs"
PNG = L.OUT / "40_页面_当前交易区.png"


def get(path: str, timeout: int = 60) -> dict:
    url = L.API + path
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read()
            return {"url": url, "status": r.status, "bytes": len(raw),
                    "elapsed_s": round(time.time() - t0, 3),
                    "json": json.loads(raw.decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        return {"url": url, "status": exc.code, "error": f"HTTPError {exc.code}",
                "elapsed_s": round(time.time() - t0, 3)}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "status": None, "error": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.time() - t0, 3)}


def sample_symbols(n: int = 5) -> list:
    con = L.ro_conn(L.LIVE_DB)
    try:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM live_signal WHERE origin='historical_research' "
            "ORDER BY symbol LIMIT ?", (n,))]
    finally:
        con.close()


def research_uids() -> set:
    con = L.ro_conn(L.LIVE_DB)
    try:
        return {r[0] for r in con.execute(
            "SELECT signal_uid FROM live_signal WHERE origin='historical_research'")}
    finally:
        con.close()


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}
    UIDS = research_uids()

    # ── A. HTTP ────────────────────────────────────────────────────────────
    r_all = get("/api/v1/signals?scope=all&origin=historical_research&limit=1000")
    r_cur = get("/api/v1/signals?scope=current&limit=500")
    r_live = get("/api/v1/signals?scope=all&origin=live&limit=1000")
    r_any = get("/api/v1/signals?scope=all&limit=1000")
    r_l1 = get("/api/v1/signals?scope=all&origin=historical_research&limit=1")

    items = (r_all.get("json") or {}).get("items") or []
    cur_items = (r_cur.get("json") or {}).get("items") or []
    cur_q = (r_cur.get("json") or {}).get("quality") or {}

    rep["HTTP"] = {
        "研究范围(scope=all&origin=historical_research&limit=1000)": {
            "status": r_all.get("status"), "bytes": r_all.get("bytes"),
            "elapsed_s": r_all.get("elapsed_s"),
            "total": (r_all.get("json") or {}).get("total"),
            "items_n": len(items),
            "has_more": (r_all.get("json") or {}).get("has_more"),
            "quality_note": ((r_all.get("json") or {}).get("quality") or {}).get("note"),
            "顶层身份": {k: (r_all.get("json") or {}).get(k) for k in
                     ("strategy_version", "rule_version", "param_hash", "data_version")},
            "首条": items[0] if items else None,
        },
        "当前交易区(scope=current&limit=500)": {
            "status": r_cur.get("status"), "items_n": len(cur_items),
            "total": (r_cur.get("json") or {}).get("total"),
            "quality_status": cur_q.get("status"),
            "excluded_n": cur_q.get("excluded_n"),
            "excluded_breakdown": cur_q.get("excluded_breakdown"),
            "live_start_ms": cur_q.get("live_start_ms"),
            "effective_floor_ms": cur_q.get("effective_floor_ms"),
            "as_of": (r_cur.get("json") or {}).get("as_of"),
        },
        "负向对照(scope=all&origin=live)": {
            "status": r_live.get("status"),
            "items_n": len((r_live.get("json") or {}).get("items") or []),
            "quality_note": ((r_live.get("json") or {}).get("quality") or {}).get("note"),
        },
        "审计全量(scope=all)": {
            "status": r_any.get("status"),
            "items_n": len((r_any.get("json") or {}).get("items") or []),
        },
        "分页语义(limit=1)": {
            "status": r_l1.get("status"),
            "total": (r_l1.get("json") or {}).get("total"),
            "items_n": len((r_l1.get("json") or {}).get("items") or []),
            "has_more": (r_l1.get("json") or {}).get("has_more"),
        },
    }

    # 逐条核对（不靠样本）
    per = {
        "n": len(items),
        "origin_ok": sum(1 for i in items if i.get("origin") == "historical_research"),
        "exec_ok": sum(1 for i in items if i.get("executable_state") == "historical_not_executable"),
        "actionable_false": sum(1 for i in items if i.get("actionable") is False),
        "reason_present": sum(1 for i in items if (i.get("actionable_reason") or "").strip()),
        "avail_null": sum(1 for i in items if i.get("available_at") is None),
        "role_research": sum(1 for i in items if i.get("role") == "research_only"),
        "bar_order_ok": sum(1 for i in items
                            if (i.get("bar_open_time") or 0) < (i.get("bar_close_time") or 0)),
        "uid_hit_db": sum(1 for i in items if i.get("signal_id") in UIDS),
        "schemes": sorted({i.get("strategy_key") for i in items}),
        "config_hashes": sorted({i.get("config_hash") for i in items if i.get("config_hash")}),
    }
    rep["逐条核对"] = per
    rep["当前区是否混入研究信号"] = any(
        i.get("origin") == "historical_research" for i in cur_items)

    # ── B. 页面 ────────────────────────────────────────────────────────────
    syms = sample_symbols(6)
    uid_sample = sorted(UIDS)[:3]
    rep["页面取证_样本symbol"] = syms
    rep["页面取证_样本uid"] = uid_sample
    pr = L.run([NODE, str(PAGE), str(PNG), ",".join(syms), ",".join(uid_sample)], timeout=300)
    page = None
    for line in (pr["stdout"] or "").splitlines():
        if line.startswith("@@PAGE@@"):
            page = json.loads(line[len("@@PAGE@@"):])
    rep["页面"] = {"rc": pr["returncode"], "elapsed_s": pr["elapsed_s"],
                 "stderr_tail": (pr["stderr"] or "")[-1500:]}
    if page is None:
        rep["页面"]["parse_error"] = True
        rep["页面"]["stdout_tail"] = (pr["stdout"] or "")[-2000:]
    else:
        rep["页面"]["结果"] = page
        rep["页面"]["截图"] = {"path": str(PNG),
                            "exists": PNG.exists(),
                            "bytes": PNG.stat().st_size if PNG.exists() else None}

    pg = rep["页面"].get("结果") or {}
    sig_resp = pg.get("signals_responses") or []
    cur_resp = next((x for x in sig_resp if "scope=current" in x.get("url", "")), {})
    disc_rows = (pg.get("discarded_rows") or {}).get("sample") or []
    disc_uid_hit = [t for t in disc_rows if any(u in t for u in UIDS)]

    # ── C. 判定 ────────────────────────────────────────────────────────────
    checks = {
        "HTTP 研究范围 200": r_all.get("status") == 200,
        "研究范围一次读全 289 条（has_more=false）":
            len(items) == 289 and (r_all.get("json") or {}).get("has_more") is False,
        "289 条全为 historical_research": per["origin_ok"] == per["n"] == 289,
        "289 条全为 historical_not_executable": per["exec_ok"] == per["n"],
        "289 条 actionable 全 false": per["actionable_false"] == per["n"],
        "289 条都带不可执行原因": per["reason_present"] == per["n"],
        "289 条 available_at 全为空": per["avail_null"] == per["n"],
        "289 条 role=research_only": per["role_research"] == per["n"],
        "289 条 bar_open<bar_close": per["bar_order_ok"] == per["n"],
        "289 条 signal_id 都能在库里对上": per["uid_hit_db"] == per["n"],
        "HTTP 当前交易区 0 条": len(cur_items) == 0,
        "当前区质量状态 ok（不是异常空表）": cur_q.get("status") == "ok",
        "当前区排除明细里 289 条被排除": (cur_q.get("excluded_n") == 289),
        "当前区未混入任何研究信号": rep["当前区是否混入研究信号"] is False,
        "负向对照 origin=live 返回 0（过滤真的生效）":
            len((r_live.get("json") or {}).get("items") or []) == 0,
        "页面信号面板已渲染": pg.get("panel_found") is True,
        "页面当前区 0 行（无 signal-row）": (pg.get("rows") or {}).get("count") == 0,
        "页面空态是「暂无交易信号」而非报错":
            (pg.get("empty") or {}).get("count") == 1
            and (pg.get("error_state") or {}).get("count") == 0
            and (pg.get("not_connected") or {}).get("count") == 0,
        "页面自己请求的 scope=current 返回 total=0": cur_resp.get("total") == 0,
        "页面请求同时报告 289 条被排除（同一本库）": cur_resp.get("excluded_n") == 289,
        "页面面板内不出现任何研究信号 uid": (pg.get("panel_uid_hits") or []) == [],
        "页面面板内 symbol 命中都能归因到观察区（非研究信号）":
            all(h.get("in_observe_list") for h in (pg.get("body_hits_explained") or [])),
        "页面 body 命中项都能归因到观察区（非研究信号）":
            all(h.get("in_observe_list") for h in (pg.get("body_hits_explained") or [])),
        "诊断明细已加载并展开（不是一直转圈）": pg.get("diag_state") in ("loaded", "nodiag"),
        "诊断明细展开后未列出研究信号":
            pg.get("diag_state") in ("loaded", "nodiag") and not disc_uid_hit,
        "页面无 console 错误": not (pg.get("console_errors") or []),
        "截图已生成": PNG.exists() and PNG.stat().st_size > 10000,
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = (
        "研究信号可经真实 HTTP 按研究范围查得（289 条、全不可执行、原因可见）；"
        "当前交易区 HTTP 与页面均为 0 行、未混入；页面读的同一本库报告 289 条被排除。"
        if not fail else f"存在未通过项：{fail}")
    rep["待裁决"] = {
        "顶层身份 vs 条目身份": {
            "现象": "响应顶层 strategy_version/rule_version/param_hash 是运行时身份"
                    f"（{(r_all.get('json') or {}).get('strategy_version')} / "
                    f"{(r_all.get('json') or {}).get('rule_version')} / "
                    f"{(r_all.get('json') or {}).get('param_hash')}），"
                    "而研究信号条目自带 ACCUM-RESEARCH-v0::4x 身份",
            "风险": "只读顶层的消费方会把研究信号误标成 FT-LOW4H",
            "本单处置": "不改接口结构（属前端/接口契约改动，需裁决）；条目级身份已逐条正确",
        },
        "研究信号在页面无可视入口": {
            "现象": "前端只请求 scope=current，没有研究/审计视图",
            "本单处置": "未改前端架构；研究展示走 scope=all&origin=historical_research 的 HTTP 查询",
        },
    }

    p = L.dump("40_HTTP与页面.json", rep)
    print("== 40 真实 HTTP 与页面 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
