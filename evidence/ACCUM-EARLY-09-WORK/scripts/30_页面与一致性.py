# -*- coding: utf-8 -*-
"""30｜页面与 API 一致性（隔离前端 3101 → 隔离后端 8899 → 隔离库）。

验什么
------
1. 页面（隔离实例）真的从隔离后端取数：抓页面发出的 `/signals` 响应，条数 289、`has_more=false`；
2. **研究区存在于既有信号面板内**，且 `data-testid` 前缀与当前区完全分离（`research-` vs `signal-`）；
3. 研究行**没有**混进当前交易区（`signal-row[data-origin=historical_research]` 必须为 0）；
4. **逐条对齐**：页面 289 行的 `data-signal-uid` 集合 == API 289 条 `signal_id` 集合；
   抽样行的 `data-origin/role/exec/approved/run_id/event_time/confirmed_at/available_at/
   available-status/config-hash` 与 API 对应条目**逐字段相等**（证明页面不是自己编的）；
5. 研究区里**没有任何执行/下单按钮**；
6. 分页完整性用 `has_more` 判定（不看 `total`）。

纪律
----
· 只读打开页面，不点任何下单/执行控件（研究区本就没有）；
· 生产 8787 / 3000 一律不碰；隔离前端 3101 与隔离后端 8899 由本脚本起停。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isoapi  # noqa: E402
import lib09 as U  # noqa: E402

Q = "/api/v1/signals?scope=all&origin=historical_research&limit=1000"
SAMPLE_ROWS = 12
TABLES = ("paper_account", "paper_position", "paper_order", "paper_prompt", "paper_funding")


def ledger_counts() -> dict:
    """隔离四臂账本的行数（用于证明服务启停没改账本数据）。"""
    out = {}
    for a in ("3a", "3b", "3c", "3d"):
        p = U.ISO / "paper" / f"paper_{a}.sqlite3"
        con = U.ro_conn(p)
        try:
            out[a] = {}
            for t in TABLES:
                try:
                    out[a][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except sqlite3.OperationalError:
                    out[a][t] = None
        finally:
            con.close()
    return out


def sig_counts() -> dict:
    con = U.ro_conn(U.ISO / "live" / "live_signals.sqlite3")
    try:
        return {"rows": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
                "research": con.execute(
                    "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research'"
                ).fetchone()[0]}
    finally:
        con.close()


def run_page(base: str, png: Path, open_research: bool = True, limit_rows: int = SAMPLE_ROWS) -> dict:
    cmd = [U.NODE, str(Path(__file__).resolve().parent / "page09.mjs"), base, str(png)]
    if open_research:
        cmd.append("--open-research")
    cmd.append(f"--limit-rows={limit_rows}")
    r = U.run(cmd, cwd=U.PROJECT, timeout=300)
    out = {"cmd": " ".join(cmd), "returncode": r["returncode"], "elapsed_s": r["elapsed_s"],
           "stderr": r["stderr"][-2000:]}
    for line in (r["stdout"] or "").splitlines():
        if line.startswith("@@PAGE@@"):
            out["page"] = json.loads(line[len("@@PAGE@@"):])
            break
    return out


def main() -> int:
    checks: dict = {}
    api = isoapi.IsoApi()
    web = isoapi.IsoWeb()
    ledger_before = ledger_counts()
    sig_before = sig_counts()
    started = api.start()
    checks["隔离后端已就绪（8899）"] = bool(started.get("ok"))
    if not started.get("ok"):
        U.dump("30_页面与一致性.json", {"后端启动": started,
                                     "判定": U.summarise(checks, "", "隔离后端未起来")})
        print(json.dumps(started, ensure_ascii=False, indent=2))
        return 1
    wstarted = web.start()
    checks["隔离前端已就绪（3101）"] = bool(wstarted.get("ok"))
    checks["隔离前端代理指向 8899"] = wstarted.get("api_proxy_target") == "http://127.0.0.1:8899"
    checks["隔离前端缓存目录在 isolated/ 下"] = str(wstarted.get("vite_cache_dir", "")).startswith(
        str(U.ISO.resolve()))
    checks["3101 端口占用者 = 本脚本起的 pid"] = str(wstarted.get("pid")) in (
        web.listeners().get("stdout") or "")

    png = U.OUT / "30_页面_研究区.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    if not wstarted.get("ok"):
        api.stop()
        U.dump("30_页面与一致性.json", {"后端启动": started, "前端启动": wstarted,
                                     "判定": U.summarise(checks, "", "隔离前端未起来")})
        print(json.dumps(wstarted, ensure_ascii=False, indent=2))
        return 1

    pg = run_page(wstarted["url"], png, open_research=True)
    P = pg.get("page") or {}
    checks["Playwright 取到页面结果"] = bool(P)
    checks["页面未报错（console_errors 为空）"] = not (P.get("console_errors") or [])
    checks["信号面板存在"] = P.get("panel_found") is True
    checks["研究区存在（research-signals）"] = (P.get("research_section_present") or 0) == 1
    checks["研究区展开后行数 = 289"] = (P.get("research_rows_after_open") or 0) == 289
    checks["研究区展开前不渲染行（折叠态 0 行）"] = (P.get("research_rows_before_open") or 0) == 0
    checks["研究行未混入当前交易区"] = (P.get("current_area_has_research") or 0) == 0
    checks["当前交易区行数 0（库内无 live/recovered 行）"] = (P.get("current_rows") or 0) == 0
    checks["研究区无执行/下单按钮"] = (P.get("research_action_buttons") or 0) == 0
    checks["页面请求命中隔离后端（响应条数 289）"] = any(
        r.get("items_n") == 289 for r in (P.get("signals_responses") or []))
    checks["页面请求的 has_more=false"] = all(
        r.get("has_more") is False for r in (P.get("signals_responses") or [])
        if r.get("items_n") == 289)

    # ── 与 API 逐条对齐 ──────────────────────────────────────────────────
    iso = api.get(Q)
    body = iso.get("json") or {}
    items = body.get("items") or []
    api_by = {it["signal_id"]: it for it in items}
    checks["API 研究条目 289"] = len(items) == 289
    checks["API has_more=false"] = body.get("has_more") is False

    page_uids = P.get("research_all_uids") or []
    checks["页面研究行 289 条"] = len(page_uids) == 289
    checks["页面 uid 集合 == API uid 集合（289/289）"] = (
        len(page_uids) == len(set(page_uids)) and set(page_uids) == set(api_by))

    field_map = [
        ("origin", lambda it: it.get("origin")),
        ("role", lambda it: it.get("role")),
        ("exec", lambda it: it.get("executable_state")),
        ("approved", lambda it: "false" if (it.get("research") or {}).get("approved") is False else "true"),
        ("run_id", lambda it: (it.get("research") or {}).get("run_id")),
        ("event_time", lambda it: it.get("event_time")),
        ("confirmed_at", lambda it: it.get("confirmed_at")),
        ("available_at", lambda it: it.get("available_at")),
        ("avail_status", lambda it: (it.get("research") or {}).get("available_at_status")),
        ("config_hash", lambda it: it.get("config_hash")),
    ]
    mismatches, checked_n = [], 0
    for row in (P.get("research_rows_sample") or []):
        uid = row.get("uid")
        it = api_by.get(uid)
        if it is None:
            mismatches.append({"uid": uid, "问题": "页面 uid 不在 API 结果里"})
            continue
        for attr, getter in field_map:
            checked_n += 1
            want = getter(it)
            got = row.get(attr)
            if attr == "available_at" and got in ("", None):
                got = None
            if attr == "approved":
                want = str(want).lower()
            if str(want) != str(got):
                mismatches.append({"uid": uid, "字段": attr, "页面": got, "API": want})
    checks["抽样行逐字段对齐（10 字段 × 抽样条数）"] = not mismatches
    checks["抽样行数 = 12"] = len(P.get("research_rows_sample") or []) == SAMPLE_ROWS

    # ── 文本层：研究区必须自带「不可交易」标识 ──────────────────────────
    meta = (P.get("research_meta_text") or "") + " " + (P.get("research_summary_text") or "")
    checks["研究区标题含『历史研究（不可交易）』"] = "历史研究（不可交易）" in meta
    checks["研究区显示条数与 has_more"] = ("289" in meta) and ("has_more" in meta)
    row_texts = " ".join(r.get("text") or "" for r in (P.get("research_rows_sample") or []))
    checks["研究行文本含 historical_not_executable"] = "historical_not_executable" in row_texts
    checks["研究行文本含 historical_research"] = "historical_research" in row_texts
    checks["研究行文本含『历史研究』标签"] = "历史研究" in row_texts
    checks["研究行文本含批准=false（未批准）"] = "false（未批准）" in row_texts
    checks["研究行文本给出不可执行原因"] = "不可执行原因" in row_texts

    # ── 收尾：停前端、停后端、抓探针 ────────────────────────────────────
    wstop = web.stop()
    astop = api.stop()
    ledger_after = ledger_counts()
    sig_after = sig_counts()
    probe = (astop.get("probe") or {}).get("probe") or {}
    writes = probe.get("写语句按库") or {}
    samples = probe.get("写语句样本") or []
    cls = probe.get("写语句分类（前4词）") or {}
    iso_tag = str(U.ISO.resolve())
    ddl_only = bool(probe.get("写语句是否全为幂等建表"))
    checks["隔离后端退出码 0"] = astop.get("returncode") == 0
    checks["隔离前端已停"] = bool(wstop.get("stopped"))
    checks["对生产信号库/账本/行情库写语句 = 0"] = all(
        sum(n for k, n in writes.items() if k.startswith(p)) == 0
        for p in (str(U.LIVE_DB.resolve()), str(U.PAPER_DIR.resolve()),
                  str(U.MARKET_DB.resolve())))
    # 隔离账本上确实出现了 64 条写语句 —— 全部是 `CREATE TABLE IF NOT EXISTS`（建表 DDL），
    # 来源是 `live_store` 连账本时的幂等建表脚本。**这不是业务写**，但也不能说成「零写」，
    # 所以单列：写语句必须只落在 isolated/ 下，且逐条都是幂等建表。
    checks["写语句只落在 isolated/ 下"] = all(k.startswith(iso_tag) for k in writes)
    checks["写语句全部是幂等建表（CREATE … IF NOT EXISTS）"] = ddl_only
    checks["写语句分类只有 CREATE 一类"] = bool(cls) and all(k.startswith("CREATE") for k in cls)
    checks["非幂等建表写语句数 = 0"] = probe.get("非幂等建表写语句数") == 0
    checks["写语句无 INSERT/UPDATE/DELETE/REPLACE/DROP"] = not any(
        k.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "DROP", "ALTER", "VACUUM"))
        for k in cls)
    checks["隔离账本行数在服务启停前后不变（DDL 没改数据）"] = ledger_after == ledger_before
    checks["隔离信号库行数在服务启停前后不变"] = sig_after == sig_before

    rep = {
        "生成时间": U.now_utc(),
        "隔离后端": started,
        "隔离前端": wstarted,
        "页面取证": {k: v for k, v in P.items() if k != "research_all_uids"},
        "页面 uid 总数": len(page_uids),
        "API": {"path": Q, "status": iso.get("status"), "items": len(items),
                "has_more": body.get("has_more"), "total": body.get("total")},
        "对齐": {"比对字段数": checked_n, "不一致": mismatches[:20],
                 "不一致条数": len(mismatches)},
        "写探针": {"按库": writes, "分类": cls, "样本": samples[:6],
                   "口径": "写语句 = INSERT/UPDATE/DELETE/REPLACE/CREATE/DROP/ALTER/VACUUM；"
                           "隔离账本上的 CREATE … IF NOT EXISTS 是 live_store 连库时的"
                           "幂等建表，不是业务写；生产三库写语句必须为 0"},
        "启停前后": {"账本行数_前": ledger_before, "账本行数_后": ledger_after,
                     "信号库_前": sig_before, "信号库_后": sig_after},
        "收尾": {"前端": wstop, "后端": astop},
        "截图": str(png),
        "判定": U.summarise(checks, "页面与 API 一致性通过：研究信号可逐条识别且不进当前交易区",
                          "页面与 API 一致性未通过"),
    }
    p = U.dump("30_页面与一致性.json", rep)
    U.print_checks("30 页面与一致性", rep, p)
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
