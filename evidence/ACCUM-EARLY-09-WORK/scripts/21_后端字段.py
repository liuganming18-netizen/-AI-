# -*- coding: utf-8 -*-
"""21｜后端字段验收（隔离服务 8899，生产代码同一份）。

验什么
------
1. 隔离服务真的指向隔离库（`@@READY@@` 自报的 `LIVE_DB` / `PAPER_LEDGER_DIR` 在 `09/isolated/` 下）；
2. `GET /api/v1/signals?scope=all&origin=historical_research&limit=1000` 能取到 289 条，
   且**每一条**都有 `research` 子对象、`research` 里每个身份字段都非空（除如实为 null 的项）；
3. **live 行不受影响**：`research` 键只在 `origin=='historical_research'` 出现 ——
   用「把 `research` 去掉后与生产（未重载旧代码）响应逐字节比对」来证明；
4. 分页完整性只看 `has_more`（不拿 `total` 当库内总数）；
5. 服务退出时进程内 SQL 写探针必须显示：对**生产信号库 / 生产账本 / 生产行情库**零写语句。

为什么能拿生产响应当「改动前」基准
--------------------------------
生产 8787 的进程（pid 91940）是**改代码之前**启动的，模块已在内存里，所以它吐出的条目
结构就是改动前结构（条目键数 34、无 `research`）。隔离 8899 用的是改后代码（键数 35，
研究行多一个 `research`）。把隔离条目的 `research` 摘掉再与生产同 uid 条目比，
**逐字节相同**才说明「既有字段一个没动」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isoapi  # noqa: E402
import lib09 as U  # noqa: E402

Q = "/api/v1/signals?scope=all&origin=historical_research&limit=1000"
RESEARCH_KEYS = ("run_id", "scheme", "approved", "config_kind", "config_sha256",
                 "engine_view_sha256", "code_hashes_n", "code_drift", "approval_blockers",
                 "gaps", "available_at_status", "available_at_reason", "entry_bar_open_ms",
                 "data_version_note", "source_file", "source_file_sha256")


def main() -> int:
    checks: dict = {}
    api = isoapi.IsoApi()
    started = api.start()
    checks["隔离服务已就绪"] = bool(started.get("ok"))
    if not started.get("ok"):
        U.dump("21_后端字段.json", {"启动": started, "判定": U.summarise(
            checks, "", "隔离服务未起来，后续检查不成立")})
        print(json.dumps(started, ensure_ascii=False, indent=2))
        return 1

    ready = started.get("ready") or {}
    iso_root = str(U.ISO.resolve())
    checks["隔离服务 LIVE_DB 指向 isolated/"] = str(ready.get("LIVE_DB", "")).startswith(iso_root)
    checks["隔离服务账本指向 isolated/"] = str(ready.get("PAPER_LEDGER_DIR", "")).startswith(iso_root)
    checks["隔离服务用的是生产同一份 adapter.py"] = str(ready.get("adapter_file", "")).endswith(
        "框架/backend/adapter.py")
    checks["隔离服务端口 = 8899"] = ready.get("port") == U.ISO_API_PORT
    checks["端口占用者 = 本脚本起的 pid"] = str(started.get("pid")) in (api.listeners().get("stdout") or "")

    # ── 隔离侧取数 ────────────────────────────────────────────────────────
    iso = api.get(Q)
    checks["隔离服务 HTTP 200"] = iso.get("status") == 200
    body = iso.get("json") or {}
    items = body.get("items") or []
    checks["隔离侧研究条目数 = 289"] = len(items) == 289
    checks["隔离侧 has_more = false（分页已取尽）"] = body.get("has_more") is False
    uids = [it.get("signal_id") for it in items]
    checks["signal_id 无重复"] = len(set(uids)) == len(uids)
    checks["全部 origin=historical_research"] = all(
        it.get("origin") == "historical_research" for it in items)
    checks["全部 executable_state=historical_not_executable"] = all(
        it.get("executable_state") == "historical_not_executable" for it in items)
    checks["全部 approved=false"] = all((it.get("research") or {}).get("approved") is False
                                        for it in items)
    checks["全部 actionable=false（不可执行）"] = all(it.get("actionable") is False for it in items)
    checks["全部 available_at 为 null（可得时刻未证）"] = all(
        it.get("available_at") is None for it in items)
    checks["全部带 research 子对象"] = all(isinstance(it.get("research"), dict) for it in items)
    checks["research 字段齐全（16 键）"] = all(
        set((it.get("research") or {}).keys()) == set(RESEARCH_KEYS) for it in items)
    checks["research.run_id 全部非空"] = all((it.get("research") or {}).get("run_id") for it in items)
    checks["research.scheme 全部非空"] = all((it.get("research") or {}).get("scheme") for it in items)
    checks["research.config_sha256 全部非空"] = all(
        (it.get("research") or {}).get("config_sha256") for it in items)
    checks["research.engine_view_sha256 全部非空"] = all(
        (it.get("research") or {}).get("engine_view_sha256") for it in items)
    checks["research.available_at_status 全部有值"] = all(
        (it.get("research") or {}).get("available_at_status") for it in items)
    checks["research.available_at_reason 全部有值"] = all(
        (it.get("research") or {}).get("available_at_reason") for it in items)
    checks["research.entry_bar_open_ms 全部非空"] = all(
        (it.get("research") or {}).get("entry_bar_open_ms") is not None for it in items)
    checks["research.code_drift 有值（非 None）"] = all(
        (it.get("research") or {}).get("code_drift") is not None for it in items)
    checks["条目 confirmed_at 全部非空"] = all(it.get("confirmed_at") for it in items)
    checks["条目 event_time 全部非空"] = all(it.get("event_time") for it in items)

    # ── 与库内逐条对齐（API 不是自己编的）────────────────────────────────
    db_rows = {r["signal_uid"]: r for r in U.research_rows(U.ISO / "live" / "live_signals.sqlite3")}
    checks["API uid 集合 == 库内 uid 集合"] = set(uids) == set(db_rows)
    bad = []
    for it in items:
        row = db_rows.get(it["signal_id"]) or {}
        try:
            pl = json.loads(row.get("payload_json") or "{}")
        except ValueError:
            pl = {}
        ts = pl.get("timestamps") or {}
        r = it.get("research") or {}
        if not (r.get("run_id") == pl.get("run_id")
                and r.get("scheme") == pl.get("scheme")
                and r.get("config_sha256") == pl.get("config_sha256")
                and r.get("engine_view_sha256") == pl.get("engine_view_sha256")
                and r.get("available_at_status") == ts.get("available_at_status")
                and r.get("entry_bar_open_ms") == ts.get("entry_bar_open_ms")
                and it.get("executable_state") == row.get("executable_state")
                and it.get("confirmed_at") is not None):
            bad.append(it["signal_id"])
    checks["API research 身份字段 == 库内 payload（逐条 289）"] = not bad
    if bad:
        checks["逐条不一致清单"] = False

    # ── live 行等价性：摘掉 research 后与生产（旧代码）逐字节比 ──────────
    prod = U.get_json(U.API + Q)
    prod_items = (prod.get("json") or {}).get("items") or []
    prod_by = {it.get("signal_id"): it for it in prod_items}
    common = [u for u in uids if u in prod_by]
    checks["生产 8787 也能取到同批研究条目"] = len(common) == 289
    checks["生产条目键数 34（改动前结构）"] = bool(prod_items) and all(
        len(it) == 34 for it in prod_items)
    checks["隔离条目键数 35（= 34 + research）"] = all(len(it) == 35 for it in items)
    diff_n, sample = 0, None
    for it in items:
        prod_it = prod_by.get(it["signal_id"])
        if prod_it is None:
            diff_n += 1
            continue
        stripped = {k: v for k, v in it.items() if k != "research"}
        if json.dumps(stripped, ensure_ascii=False, sort_keys=True) != json.dumps(
                prod_it, ensure_ascii=False, sort_keys=True):
            diff_n += 1
            if sample is None:
                ka = set(stripped) | set(prod_it)
                sample = {"uid": it["signal_id"],
                          "差异键": sorted(k for k in ka
                                          if json.dumps(stripped.get(k), sort_keys=True)
                                          != json.dumps(prod_it.get(k), sort_keys=True))}
    checks["摘掉 research 后与生产逐字节等价（289/289）"] = diff_n == 0

    # ── 研究行不得出现在当前区 ──────────────────────────────────────────
    cur = api.get("/api/v1/signals?scope=current&limit=1000")
    cur_body = cur.get("json") or {}
    cur_items = cur_body.get("items") or []
    checks["当前区（scope=current）不含研究行"] = all(
        it.get("origin") != "historical_research" for it in cur_items)
    checks["当前区 has_more=false"] = cur_body.get("has_more") is False
    checks["当前区条目数 0（生产库内无 live/recovered 行）"] = len(cur_items) == 0
    checks["当前区给出口径说明（missing_reason/quality 非空）"] = bool(
        cur_body.get("missing_reason") or (cur_body.get("quality") or {}).get("status"))

    # ── 分页语义：has_more 才是完整性判据 ───────────────────────────────
    small = api.get("/api/v1/signals?scope=all&origin=historical_research&limit=50")
    sb = small.get("json") or {}
    checks["limit=50 时 has_more=true"] = sb.get("has_more") is True
    checks["limit=50 时返回 50 条"] = len(sb.get("items") or []) == 50
    checks["limit=50 时 total 不等于库内总数（total 只按本页/合并口径）"] = (
        sb.get("total") != 289)
    page = api.get("/api/v1/signals?scope=all&origin=historical_research&limit=1000")
    pb = page.get("json") or {}
    checks["limit=1000 时 has_more=false 且条数=289"] = (
        pb.get("has_more") is False and len(pb.get("items") or []) == 289)
    checks["两次取数 uid 集合一致"] = (
        {it["signal_id"] for it in (sb.get("items") or [])}
        <= {it["signal_id"] for it in (pb.get("items") or [])})

    # ── 停服务 + 抓进程内写探针 ──────────────────────────────────────────
    stopped = api.stop()
    probe = (stopped.get("probe") or {}).get("probe") or {}
    writes = probe.get("写语句按库") or {}
    prod_live = str(U.LIVE_DB.resolve())
    prod_paper = str(U.PAPER_DIR.resolve())
    prod_market = str(U.MARKET_DB.resolve())

    def w_of(path: str) -> int:
        return sum(n for k, n in writes.items() if k.startswith(path))

    checks["隔离服务退出码 0"] = stopped.get("returncode") == 0
    checks["探针报告已取到"] = bool(probe)
    checks["对生产信号库写语句 = 0"] = w_of(prod_live) == 0
    checks["对生产账本目录写语句 = 0"] = w_of(prod_paper) == 0
    checks["对生产行情库写语句 = 0"] = w_of(prod_market) == 0
    # 连接键是 `sqlite3.connect` 的原始参数：隔离信号库走 URI 形式（`file:...?mode=ro`），
    # 所以按**路径子串**匹配，而不是按前缀匹配（前缀匹配会把「已读过」误判成「没读」）。
    iso_db_tag = "isolated/live/live_signals.sqlite3"
    checks["服务确实读过隔离信号库（连接数 > 0）"] = any(
        iso_db_tag in k for k in (probe.get("连接过的库") or {}))
    checks["隔离信号库连接为只读 URI（mode=ro）"] = all(
        "mode=ro" in k for k in (probe.get("连接过的库") or {}) if iso_db_tag in k)
    checks["进程内写语句总数 = 0（对任何库都没写）"] = probe.get("写语句总数") == 0
    checks["行情库以只读方式打开（无写语句）"] = w_of(str(U.MARKET_DB.parent)) == 0
    checks["账本 ATTACH 4 次（四臂，非写操作）"] = probe.get("ATTACH/DETACH 次数") == 4

    rep = {
        "生成时间": U.now_utc(),
        "服务": started,
        "查询": {"path": Q, "隔离": {"status": iso.get("status"), "bytes": iso.get("bytes"),
                                    "elapsed_s": iso.get("elapsed_s"),
                                    "has_more": body.get("has_more"),
                                    "total": body.get("total"),
                                    "items": len(items)},
                 "生产8787": {"status": prod.get("status"),
                              "has_more": (prod.get("json") or {}).get("has_more"),
                              "total": (prod.get("json") or {}).get("total"),
                              "items": len(prod_items)}},
        "样例条目": items[0] if items else None,
        "库内样例": {k: db_rows[k] for k in list(db_rows)[:1]},
        "等价性比对": {"比对条数": len(common), "差异条数": diff_n, "差异样例": sample,
                       "口径": "隔离条目去掉 research 键后与生产 8787 同 uid 条目 sort_keys 序列化逐字节比"},
        "当前区": {"items": len(cur_items), "has_more": cur_body.get("has_more"),
                   "missing_reason": cur_body.get("missing_reason"),
                   "quality": cur_body.get("quality")},
        "停服务": stopped,
        "判定": U.summarise(checks, "后端字段验收通过：研究身份可逐条追溯、live 行响应零改动",
                          "后端字段验收未通过"),
    }
    p = U.dump("21_后端字段.json", rep)
    U.print_checks("21 后端字段", rep, p)
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
