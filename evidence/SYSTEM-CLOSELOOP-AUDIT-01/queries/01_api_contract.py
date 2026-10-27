#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01_api_contract.py —— 冻结清单 A1–A21 中的**只读**契约复验（A1–A10 读侧、A12–A18、A21 GET）

纪律
----
· 只读：本轮**不发**任何写请求（写侧 A11/A19/A20 与 C1–C6 一律在隔离实例做，见 04）。
· 每条都落「实测值 + 判据 + 三态结论」，不写「看起来正常」。
· 原始响应体全量落盘到 `out/api/`，报告里的每个数字都能回到这里复算。
· 已知本机 `grep` 读本项目含全角字符的 md 会静默返回空 ⇒ 本脚本一律用 pathlib 读。

本机环境坑（第一版踩过，已修）
------------------------------
· `HTTP_PROXY=http://127.0.0.1:65505` 且**无 `no_proxy`** ⇒ `urllib` 默认把
  `127.0.0.1:8787` 也发给代理，代理偶发返回 `502 upstream connect failed`
  （纯文本、非 JSON）⇒ 多接口被误判。现在显式 `ProxyHandler({})` 强制直连 + 重试。
· K 线参数名是 **`timeframe`**（不是 `tf`），返回键是 **`bars`**（不是 `items`）；
  用错参数名不会报错，只会静默返回默认 1d —— 第一版因此拿到 0 条并误判。
· `open_interest` / `funding_rate` 是**字符串**，直接比 0 恒不命中。

用法：python 01_api_contract.py
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # evidence/SYSTEM-CLOSELOOP-AUDIT-01
PROJ = ROOT.parent.parent               # 项目根
OUT = ROOT / "out"
API = OUT / "api"
BASE = "http://127.0.0.1:8787"

PAPER_DIR = PROJ / "数据" / "paper"
LIVE_DB = PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESULTS: list[dict] = []


def fetch(path: str, timeout: int = 180) -> tuple[int, dict | list | str, float]:
    """直连（绕过环境代理）+ 最多 3 次重试。"""
    t0 = time.time()
    last = None
    for _ in range(3):
        try:
            with _OPENER.open(BASE + path, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8")), round(time.time() - t0, 3)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(body), round(time.time() - t0, 3)
            except ValueError:
                return e.code, body, round(time.time() - t0, 3)
        except Exception as e:                                # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(0.8)
    return -1, last or "unknown", round(time.time() - t0, 3)


def save(name: str, obj) -> None:
    API.mkdir(parents=True, exist_ok=True)
    (API / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def fnum(x):
    """字符串/None → float；不可转则 None（**不当作 0**）。"""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def iso_ms(s):
    """'2026-10-21T10:30:00Z' → epoch ms。"""
    if s is None:
        return None
    try:
        return int(datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def rec(cid: str, title: str, verdict: str, judged: str, measured, extra=None) -> None:
    RESULTS.append({"id": cid, "title": title, "verdict": verdict,
                    "判据": judged, "实测": measured, "补充": extra or {}})
    print(f"[{verdict}] {cid} {title}")
    if verdict != "通过":
        print(f"        判据：{judged}")
        print(f"        实测：{json.dumps(measured, ensure_ascii=False)[:500]}")


# ── A1 健康 ────────────────────────────────────────────────────────────────
def a1():
    st, b, dt = fetch("/api/v1/health")
    save("health.json", b)
    if not isinstance(b, dict):
        return rec("A1", "GET /api/v1/health", "不通过", "HTTP 200 且 items 非空", {"status": st, "body": str(b)[:200]})
    items = b.get("items") or []
    bad = [i for i in items if (i.get("status") or "").lower() not in ("ok", "healthy")]
    ok = st == 200 and len(items) > 0
    rec("A1", "GET /api/v1/health", "通过" if ok else "不通过",
        "200 且 items 非空；失败项如实标非 ok（不得吞掉）",
        {"status": st, "n_items": len(items), "n_not_ok": len(bad),
         "not_ok": [{"name": i.get("name"), "status": i.get("status"),
                     "age_seconds": i.get("age_seconds")} for i in bad],
         "elapsed_s": dt})


# ── A2 总览 vs 市场列表 ────────────────────────────────────────────────────
def a2():
    st1, ov, _ = fetch("/api/v1/overview")
    st2, mk, _ = fetch("/api/v1/markets")
    save("overview.json", ov)
    save("markets.json", mk)
    mt = ((ov or {}).get("counts") or {}).get("markets_total")
    n = len((mk or {}).get("items") or [])
    total = (mk or {}).get("total")
    ok = mt == n == total
    rec("A2", "GET /api/v1/overview 与 /markets 口径", "通过" if ok else "不通过",
        "counts.markets_total == markets.items 条数 == markets.total",
        {"overview.markets_total": mt, "markets.n_items": n, "markets.total": total,
         "markets.has_more": (mk or {}).get("has_more"),
         "markets.quality": (mk or {}).get("quality")})


# ── A3 市场列表字段质量 ────────────────────────────────────────────────────
def a3():
    st, mk, dt = fetch("/api/v1/markets")
    items = (mk or {}).get("items") or []
    keys = ["exchange", "market_id", "symbol", "base", "quote", "contract_type"]
    nulls = {k: sum(1 for i in items if i.get(k) in (None, "")) for k in keys}
    extra = ["market_cap_usd", "price", "change_24h_pct", "quote_volume_24h"]
    ext_nulls = {k: sum(1 for i in items if i.get(k) is None) for k in extra}
    elig = sum(1 for i in items if i.get("eligibility") is not None)
    ok = len(items) > 0 and all(v == 0 for v in nulls.values())
    rec("A3", "GET /api/v1/markets 字段非空率", "通过" if ok else "不通过",
        "条数>0 且 6 个身份字段无空值",
        {"n_items": len(items), "身份字段空值数": nulls, "扩展字段空值数": ext_nulls,
         "eligibility非空条数": elig, "elapsed_s": dt},
        {"说明": "eligibility 恒 null 是设计（数据总池 ≠ 交易资格），不计失败"})


# ── A4 K 线参数生效 ────────────────────────────────────────────────────────
def a4():
    base = "/api/v1/markets/binance/BTCUSDT/candles"
    expect = {"15m": 900000, "1h": 3600000, "4h": 14400000}
    cases = []
    for tf, limit in [("15m", 100), ("1h", 50), ("4h", 200)]:
        q = urllib.parse.urlencode({"timeframe": tf, "limit": limit})
        st, b, dt = fetch(f"{base}?{q}")
        bars = (b or {}).get("bars") or []
        ts = [iso_ms(x.get("open_time")) for x in bars]
        ts = [t for t in ts if t is not None]
        mono = all(ts[i] < ts[i + 1] for i in range(len(ts) - 1)) if len(ts) > 1 else None
        dup = len(ts) != len(set(ts))
        step = ((ts[-1] - ts[0]) // (len(ts) - 1)) if len(ts) > 1 else None
        cases.append({"tf": tf, "limit": limit, "status": st, "n": len(bars),
                      "returned_timeframe": (b or {}).get("timeframe"),
                      "le_limit": len(bars) <= limit, "monotonic": mono, "dup": dup,
                      "step_ms": step, "step_ok": step == expect[tf],
                      "first": bars[0].get("open_time") if bars else None,
                      "last": bars[-1].get("open_time") if bars else None,
                      "all_closed": all(x.get("is_closed") is True for x in bars),
                      "elapsed_s": dt})
        save(f"candles_{tf}.json", b)
    ok = all(c["status"] == 200 and c["n"] > 0 and c["le_limit"] and c["monotonic"]
             and not c["dup"] and c["step_ok"] and c["returned_timeframe"] == c["tf"]
             for c in cases)
    rec("A4", "GET /markets/{ex}/{sym}/candles 参数生效", "通过" if ok else "不通过",
        "timeframe/limit 生效；返回 timeframe 回显一致；根数≤limit；时间严格递增无重复；间隔=周期",
        {"cases": cases},
        {"说明": "参数名是 timeframe；传 tf 会被**静默忽略**并回落 1d（第一版实测），"
                 "属宽松入参，不计失败但记入观察项"})


# ── A5 OI 缺失不补 0 ──────────────────────────────────────────────────────
def a5():
    st, b, _ = fetch("/api/v1/markets/binance/BTCUSDT/oi?limit=50")
    save("oi_btc.json", b)
    items = (b or {}).get("items") or []
    vals = [fnum(i.get("open_interest")) for i in items]
    zeros = [v for v in vals if v == 0.0]
    nulls = [v for v in vals if v is None]
    ok = st == 200 and len(items) > 0 and len(zeros) == 0
    rec("A5", "GET /markets/{ex}/{sym}/oi 缺失不补 0", "通过" if ok else "不通过",
        "200；有数据；**不出现 open_interest==0 的伪造行**；缺失走 null",
        {"status": st, "n": len(items), "n_zero": len(zeros), "n_null": len(nulls),
         "quality": (b or {}).get("quality"),
         "missing_reason": (b or {}).get("missing_reason"),
         "sample": items[:2]},
        {"说明": "open_interest 是字符串，需转 float 再判 0（第一版直接比 0 恒不命中）"})


# ── A6 资金费符号保留 ─────────────────────────────────────────────────────
def a6():
    st, b, _ = fetch("/api/v1/markets/binance/BTCUSDT/funding?limit=50")
    save("funding_btc.json", b)
    items = (b or {}).get("items") or []
    rates = [fnum(i.get("funding_rate")) for i in items]
    rates = [r for r in rates if r is not None]
    neg = [r for r in rates if r < 0]
    ok = st == 200 and len(rates) > 0 and len(neg) > 0
    rec("A6", "GET /markets/{ex}/{sym}/funding 符号保留", "通过" if ok else "未验证",
        "200；有数据；**存在负值且未被取绝对值**",
        {"status": st, "n": len(items), "n_rate": len(rates), "n_negative": len(neg),
         "min": min(rates) if rates else None, "max": max(rates) if rates else None,
         "quality": (b or {}).get("quality"),
         "newest": items[0].get("event_time") if items else None},
        {"说明": "若窗口内恰好无负费率，本条只能判未验证，不得判通过"})


# ── A7 信号条数与库内一致 ─────────────────────────────────────────────────
def a7():
    st, b, _ = fetch("/api/v1/signals?scope=current")
    save("signals_current.json", b)
    items = (b or {}).get("items") or []
    q = (b or {}).get("quality") or {}
    uids = [i.get("signal_uid") or i.get("signal_id") or i.get("uid") for i in items]
    db_total = db_enter = None
    found = {}
    err = None
    try:
        con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            db_total = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
            db_enter = con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE executable_state='enterable'").fetchone()[0]
            for u in uids:
                if u is None:
                    continue
                row = con.execute(
                    "SELECT signal_uid, executable_state, origin FROM live_signal "
                    "WHERE signal_uid=?", (str(u),)).fetchone()
                found[str(u)] = dict(row) if row else None
        finally:
            con.close()
    except Exception as e:                                    # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    all_found = bool(uids) and all(found.get(str(u)) for u in uids if u is not None)
    # scope=current 的口径是「合并后卡片数」：多臂行会合并成一张卡 ⇒
    # 正确判据是 items == n_cards，且 n_arm_rows ≥ n_cards（原始臂行不少于卡片），
    # 且每条卡片 uid 都能在库里查到。不能强求 items == n_arm_rows。
    ok = (st == 200 and len(items) == q.get("n_cards")
          and isinstance(q.get("n_arm_rows"), int) and q["n_arm_rows"] >= len(items)
          and all_found)
    rec("A7", "GET /api/v1/signals?scope=current 与库内一致", "通过" if ok else "不通过",
        "items 条数 == quality.n_cards；n_arm_rows ≥ n_cards（多臂合并）；"
        "每条 signal_uid 在库内可查到",
        {"status": st, "api_n": len(items), "n_cards": q.get("n_cards"),
         "n_arm_rows": q.get("n_arm_rows"), "excluded_n": q.get("excluded_n"),
         "db_live_signal_total": db_total, "db_enterable": db_enter,
         "uids": uids, "每条库内命中": all_found, "db_error": err,
         "quality.note": q.get("note")},
        {"说明": "第一版判据写成 items == n_arm_rows，把「多臂合并成一张卡」误判为不一致；"
                 "已按接口自身口径修正。scope=current 的过滤条件见 quality.note"})


# ── A8 纸面账户闭合 ───────────────────────────────────────────────────────
def a8():
    st, b, _ = fetch("/api/v1/paper/accounts")
    save("paper_accounts.json", b)
    items = (b or {}).get("items") or []
    ls = (b or {}).get("ledger_source") or {}
    rows = []
    for it in items:
        cash = fnum(it.get("cash"))
        eq = fnum(it.get("equity"))
        unr = fnum(it.get("unrealized_pnl"))
        rows.append({
            "arm": it.get("arm"), "initial_equity": it.get("initial_equity"),
            "cash": cash, "equity": eq, "unrealized_pnl": unr,
            "used_margin": it.get("used_margin"),
            "gross_notional": it.get("gross_notional"),
            "drawdown": it.get("drawdown"), "peak_equity": it.get("peak_equity"),
            "闭合差(cash+未实现-权益)": None if None in (cash, unr, eq) else round(cash + unr - eq, 8),
        })
    diffs = [r["闭合差(cash+未实现-权益)"] for r in rows]
    ok = st == 200 and len(items) == 4 and all(d == 0 for d in diffs if d is not None)
    rec("A8", "GET /api/v1/paper/accounts 账户闭合", "通过" if ok else "不通过",
        "账户数=账本臂数(4)；每臂 cash + unrealized_pnl == equity",
        {"status": st, "n_accounts": len(items), "arms": [r["arm"] for r in rows],
         "ledger_source": ls, "per_arm": rows})


# ── A9 持仓与账本一致 ─────────────────────────────────────────────────────
def a9():
    st, b, _ = fetch("/api/v1/paper/positions")
    save("paper_positions.json", b)
    items = (b or {}).get("items") or []
    db_counts = {}
    for arm in ("3a", "3b", "3c", "3d"):
        p = PAPER_DIR / f"paper_{arm}.sqlite3"
        if not p.exists():
            db_counts[arm] = None
            continue
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            try:
                db_counts[arm] = con.execute(
                    "SELECT COUNT(*) FROM paper_position WHERE status='open' "
                    "AND COALESCE(voided,0)=0").fetchone()[0]
            finally:
                con.close()
        except Exception as e:                                # noqa: BLE001
            db_counts[arm] = f"ERR {type(e).__name__}"
    api_counts: dict[str, int] = {}
    for it in items:
        a = it.get("arm")
        api_counts[a] = api_counts.get(a, 0) + 1
    total_db = sum(v for v in db_counts.values() if isinstance(v, int))
    ok = st == 200 and len(items) == total_db
    rec("A9", "GET /api/v1/paper/positions 与账本逐笔一致", "通过" if ok else "不通过",
        "接口持仓条数 == 各臂账本 status='open' 且未作废的仓位数",
        {"status": st, "api_n": len(items), "api_per_arm": api_counts,
         "db_per_arm_open": db_counts, "db_total": total_db,
         "sample": items[:1]})


# ── A10 只读说不通 / 写口仍可写 ───────────────────────────────────────────
def a10():
    st, b, _ = fetch("/api/v1/paper/orders")
    save("paper_orders.json", b)
    q = (b or {}).get("quality") or {}
    status = q.get("status")
    note = (b or {}).get("missing_reason")
    src = (PROJ / "框架" / "backend" / "adapter.py").read_text(encoding="utf-8")
    has_post_orders = 'parsed.path == "/api/v1/paper/orders"' in src
    la = (PROJ / "框架" / "backend" / "live_api.py").read_text(encoding="utf-8")
    wired_to_legacy = "return _paper_order_post_legacy(payload)" in la
    docstring_claim = "已接通新共享核心" in la
    arms = None
    ls = (PROJ / "框架" / "backend" / "live_store.py").read_text(encoding="utf-8")
    for line in ls.split("\n"):
        if line.startswith("ARMS = "):
            arms = line.strip()
            break
    consistent = (status == "not_connected") and (not has_post_orders)
    rec("A10", "GET /api/v1/paper/orders 只读声明与写入口一致", "通过" if consistent else "不通过",
        "只读返回 not_connected ⇒ 写入口必须同样关闭（不得只读说未接通、写口仍可写）",
        {"read_status": status, "read_note": note,
         "read_items": len((b or {}).get("items") or []),
         "写路由存在": has_post_orders,
         "写实现指向": "_paper_order_post_legacy（其 docstring 自述「已退役、不再挂任何路由」）"
                       if wired_to_legacy else "其他",
         "写入口 docstring 自称": "已接通新共享核心" if docstring_claim else "—",
         "臂表": arms},
        {"说明": "三处自相矛盾：① 只读侧声明 NOT_CONNECTED；② 写路由仍挂在 adapter 上；"
                 "③ 写函数 docstring 写「已接通新共享核心」，函数体却直通"
                 "「已退役、不再挂任何路由」的旧实现。实际拒绝理由是账本不可用（503），"
                 "而不是未接通 ⇒ 声明与行为不一致。"})


# ── A12 worker 健康 ──────────────────────────────────────────────────────
def a12():
    st, b, _ = fetch("/api/v1/live/health")
    save("live_health.json", b)
    q = (b or {}).get("quality") or {}
    ok = st == 200 and q.get("status") == "down"
    rec("A12", "GET /api/v1/live/health worker 未运行必须 down", "通过" if ok else "不通过",
        "worker 无心跳 ⇒ quality.status == 'down'（不得因接口 200 报 ok）",
        {"status": st, "quality.status": q.get("status"),
         "worker_status": q.get("worker_status"), "heartbeats": q.get("heartbeats"),
         "worker_ttl_seconds": q.get("worker_ttl_seconds"),
         "missing_reason": (b or {}).get("missing_reason"),
         "items": (b or {}).get("items")})


# ── A13 实盘账户空态 ─────────────────────────────────────────────────────
def a13():
    st, b, _ = fetch("/api/v1/accounts")
    save("accounts_live.json", b)
    q = (b or {}).get("quality") or {}
    mr = (b or {}).get("missing_reason")
    ok = st == 200 and q.get("status") == "not_connected" and bool(mr)
    rec("A13", "GET /api/v1/accounts 空态可解释", "通过" if ok else "不通过",
        "无快照 ⇒ not_connected 且 missing_reason 明确（不得伪造 0 余额）",
        {"status": st, "quality.status": q.get("status"), "missing_reason": mr,
         "n_items": len((b or {}).get("items") or [])})


# ── A14 策略注册表 ───────────────────────────────────────────────────────
def a14():
    st, b, _ = fetch("/api/v1/strategies")
    save("strategies.json", b)
    items = (b or {}).get("items") or []
    cur = (b or {}).get("current") or []
    legacy = (b or {}).get("legacy") or []
    ok = st == 200 and len(items) == (b or {}).get("total") == len(cur) + len(legacy)
    rec("A14", "GET /api/v1/strategies 条数与注册表一致", "通过" if ok else "不通过",
        "items 条数 == total == current + legacy",
        {"status": st, "n_items": len(items), "total": (b or {}).get("total"),
         "n_current": len(cur), "n_legacy": len(legacy),
         "current_keys": [x.get("strategy_key") for x in cur],
         "legacy_keys": [x.get("strategy_key") for x in legacy]})


# ── A15 数据源 17 项 ────────────────────────────────────────────────────
def a15():
    st, b, _ = fetch("/api/v1/data/feeds")
    save("data_feeds.json", b)
    feeds = (b or {}).get("feeds") or []
    by = {}
    for f in feeds:
        by.setdefault(f.get("status"), []).append(f.get("code"))
    ok = st == 200 and len(feeds) == 17 and (b or {}).get("total") == 17
    rec("A15", "GET /api/v1/data/feeds 17 项且状态如实", "通过" if ok else "不通过",
        "17 项；status 分布如实（停更项不得为正常）",
        {"status": st, "n_feeds": len(feeds), "total": (b or {}).get("total"),
         "状态分布": {k: len(v) for k, v in by.items()},
         "非正常项": [{"code": f.get("code"), "status": f.get("status"),
                       "light": f.get("status_light")} for f in feeds
                      if (f.get("status") or "") not in ("正常", "ok", "healthy")]})


# ── A16 数据中心四个明细表信封 ──────────────────────────────────────────
def a16():
    out = {}
    for name in ("oi", "funding", "smart-money", "all-in-one"):
        st, b, dt = fetch(f"/api/v1/data/{name}?limit=30")
        save(f"dc_{name.replace('-', '_')}.json", b)
        n30 = len((b or {}).get("items") or [])
        st2, b2, _ = fetch(f"/api/v1/data/{name}?limit=10")
        n10 = len((b2 or {}).get("items") or [])
        out[name] = {"status": st, "limit30_n": n30, "limit10_n": n10,
                     "total": (b or {}).get("total"),
                     "has_items_key": "items" in (b or {}),
                     "has_total_key": "total" in (b or {}),
                     "limit_effective": n30 <= 30 and n10 <= 10,
                     "elapsed_s": dt}
    ok = all(v["status"] == 200 and v["has_items_key"] and v["has_total_key"]
             and v["limit_effective"] for v in out.values())
    rec("A16", "GET /data/{oi|funding|smart-money|all-in-one} 信封与 limit", "通过" if ok else "不通过",
        "四个明细表统一 items+total 信封；limit 生效", out)


# ── A17 覆盖矩阵规模 ────────────────────────────────────────────────────
def a17():
    st, b, dt = fetch("/api/v1/data/coverage")
    save("data_coverage.json", b)
    cells = (b or {}).get("cells") or []
    total_cells = (b or {}).get("total_cells")
    ratio = (b or {}).get("coverage_ratio")
    combos = {(c.get("exchange"), c.get("market_id")) for c in cells}
    by_field = {}
    for c in cells:
        by_field[c.get("field")] = by_field.get(c.get("field"), 0) + 1
    ok = st == 200 and len(cells) > 0
    rec("A17", "GET /api/v1/data/coverage 规模与分母", "通过" if ok else "不通过",
        "200 且有 cells；**分母字段（total_cells / coverage_ratio）不得凭空为 0**，"
        "缺失就留 null 并说明",
        {"status": st, "n_cells": len(cells), "n_combos": len(combos),
         "total_cells": total_cells, "coverage_ratio": ratio,
         "covered_cells": (b or {}).get("covered_cells"),
         "by_field": by_field, "elapsed_s": dt},
        {"说明": "cells 的粒度是「标的×字段×周期」，不等于 /health 报的 (标的,周期) 组合数；"
                 "分母两个字段均为 null ⇒ 前端无法算覆盖率，只能显示已覆盖数"})


# ── A18 回测列表过滤在截断之前 ──────────────────────────────────────────
def a18():
    st, b, dt = fetch("/api/v1/backtests?limit=5")
    save("backtests_limit5.json", b)
    items = (b or {}).get("items") or []
    total = (b or {}).get("total")
    returned = (b or {}).get("returned")
    truncated = (b or {}).get("truncated")
    st_all, b_all, _ = fetch("/api/v1/backtests?limit=5000")
    save("backtests_all.json", b_all)
    all_items = (b_all or {}).get("items") or []
    all_ids = [x.get("run_id") for x in all_items]
    deep_id = all_ids[-1] if len(all_ids) > 5 else None
    st_q, b_q, _ = fetch("/api/v1/backtests?limit=5&q=" + urllib.parse.quote(deep_id or ""))
    q_ids = [x.get("run_id") for x in ((b_q or {}).get("items") or [])]
    deep_found = bool(deep_id) and deep_id in q_ids
    st_s, b_s, _ = fetch("/api/v1/backtests?limit=5000&status=done")
    n_s = len((b_s or {}).get("items") or [])
    ok = (st == 200 and len(items) <= 5 and returned == len(items) and truncated is True
          and isinstance(total, int) and total >= len(all_items) and deep_found)
    rec("A18", "GET /api/v1/backtests 过滤在截断之前", "通过" if ok else "不通过",
        "limit 生效；truncated 如实；**第 6 条之后的 run 用 q 仍能搜到**（证明过滤先于截断）",
        {"status": st, "limit5_n": len(items), "returned": returned, "total": total,
         "truncated": truncated, "runs_total_all": (b or {}).get("runs_total_all"),
         "excluded_legacy_total": (b or {}).get("excluded_legacy_total"),
         "all_n": len(all_items), "deep_id": deep_id, "deep_found_by_q": deep_found,
         "status_done_n": n_s, "elapsed_s": dt})


# ── A21 未知路径 404 ───────────────────────────────────────────────────
def a21():
    probes = ["/api/v1/__nope__", "/api/v1/paper/__nope__",
              "/api/v1/markets/binance/BTCUSDT/__nope__"]
    got = []
    for p in probes:
        st, b, _ = fetch(p)
        got.append({"path": p, "status": st, "body": b})
    save("unknown_paths.json", got)
    ok = all(g["status"] == 404 for g in got)
    rec("A21", "未知路径 404（读侧）", "通过" if ok else "不通过",
        "三条未知路径均 404；写侧 404 与「不改状态」在隔离实例复验（见 04）", got)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for fn in (a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a12, a13, a14, a15, a16, a17, a18, a21):
        try:
            fn()
        except Exception as e:                                # noqa: BLE001
            rec(fn.__name__.upper(), fn.__name__, "未验证", "脚本异常", f"{type(e).__name__}: {e}")
    cnt = {}
    for r in RESULTS:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    doc = {"生成时刻": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "后端": BASE,
           "纪律": "本轮只读，不发写请求；写侧见 04_lifecycle_isolated.py",
           "网络": "显式绕过环境代理（HTTP_PROXY 未设 no_proxy）",
           "耗时_s": round(time.time() - t0, 2), "计数": cnt, "明细": RESULTS}
    (OUT / "01_接口契约.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 汇总 ===")
    print(json.dumps(cnt, ensure_ascii=False, indent=2))
    for r in RESULTS:
        print(f"  {r['id']:4s} {r['verdict']:4s} {r['title']}")


if __name__ == "__main__":
    main()
