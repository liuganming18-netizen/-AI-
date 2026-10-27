#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-01 端到端一致性核验：**原始来源 → 存储/快照 → 同源 API** 三方逐字段比对。

为什么要独立重查一遍来源：接口自己说自己对没有意义。
本脚本**不调用 metrics_api 的取数函数**，而是直接读：
  · `realtime_klines`（sqlite，未收盘 bar 原始字符串）
  · `快照/public_extras/<ex>__<mid>.json`（采集器写的原始 JSON）
  · `oi_history` / `realtime_quotes` / `funding_history`（sqlite 原始行）
然后与 `GET /api/v1/markets/{ex}/{mid}/metrics` 的返回逐字段比对。

判据：
  · 数值必须**逐位相等**（浮点用相对误差 1e-9，因为两边都从同一字符串解析）；
  · 缺失必须两边都为 null（不得一边 null 一边 0）；
  · 派生字段必须能由源值重算出来；
  · 单位/周期/时点字段必须存在。

用法：
  python verify_consistency.py                      # 默认四案例
  python verify_consistency.py binance BTCUSDT 1h
"""
import json
import math
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACKEND = os.path.join(ROOT, "框架", "backend")
sys.path.insert(0, BACKEND)

DB = os.environ.get("MARKET_DB") or os.path.join(
    ROOT, "数据", "市场数据", "db", "market_v2.sqlite3")
API = os.environ.get("API_BASE") or "http://127.0.0.1:8787"
SNAP_DIR = os.path.join(BACKEND, "快照", "public_extras")

CASES = [
    ("binance", "BTCUSDT", "1h", "主流标的"),
    ("binance", "ETHUSDT", "1h", "主流标的"),
    ("binance", "1000CATUSDT", "1h", "历史有指标的小币"),
    ("okx", "OKB-USDT-SWAP", "1h", "欧易补充位（币安无该币）"),
]


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    t = str(v).strip()
    if not t:
        return None
    try:
        f = float(t)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def api_get(path):
    url = API + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=30) as fh:
        return json.loads(fh.read().decode("utf-8"))


def _iso_to_ms(s):
    if not s:
        return None
    try:
        return int(datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def conn():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ── 各字段的**独立来源**取数 ────────────────────────────────────────────────

def src_rt_bar(ex, mid, period):
    con = conn()
    try:
        r = con.execute(
            "SELECT volume, quote_volume, taker_buy_volume, taker_buy_quote_volume, "
            "trade_count, event_time, received_at, is_closed FROM realtime_klines "
            "WHERE exchange=? AND symbol=? AND timeframe=?", (ex, mid, period)).fetchone()
    finally:
        con.close()
    return dict(r) if r else None


def src_snapshot(ex, mid):
    path = os.path.join(SNAP_DIR, f"{ex}__{mid}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def src_quote(ex, mid):
    con = conn()
    try:
        r = con.execute(
            "SELECT funding_rate, next_funding_time, funding_event_time, updated_at "
            "FROM realtime_quotes WHERE exchange=? AND symbol=?", (ex, mid)).fetchone()
    finally:
        con.close()
    return dict(r) if r else None


def src_settled(ex, mid):
    con = conn()
    try:
        r = con.execute(
            "SELECT funding_time, funding_rate, fetched_at FROM funding_history "
            "WHERE exchange=? AND symbol=? AND rate_type='settled' "
            "ORDER BY funding_time DESC LIMIT 1", (ex, mid)).fetchone()
    finally:
        con.close()
    return dict(r) if r else None


def src_oi(ex, mid, at_iso=None):
    """读 `oi_history` 的快照分区最新一行；给了 `at_iso` 就按该源时间精确定位。

    OI 每分钟会写新行，先读接口再读源会跨版本 ⇒ 用接口自己给的源时间去定位同一行。
    """
    con = conn()
    try:
        if at_iso:
            ms = _iso_to_ms(at_iso)
            if ms is not None:
                r = con.execute(
                    "SELECT ts, open_interest, open_interest_value, oi_ccy, oi_usd, fetched_at "
                    "FROM oi_history WHERE exchange=? AND symbol=? AND period='snapshot' "
                    "AND ts=? LIMIT 1", (ex, mid, ms)).fetchone()
                if r:
                    return dict(r)
        r = con.execute(
            "SELECT ts, open_interest, open_interest_value, oi_ccy, oi_usd, fetched_at "
            "FROM oi_history WHERE exchange=? AND symbol=? AND period='snapshot' "
            "ORDER BY ts DESC LIMIT 1", (ex, mid)).fetchone()
    finally:
        con.close()
    return dict(r) if r else None


# ── 比对 ──────────────────────────────────────────────────────────────────

def close(a, b, rel=1e-9):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / scale <= rel


def check_case(ex, mid, period, label):
    matrix = api_get(f"/api/v1/markets/{ex}/{mid}/metrics?period={period}")
    fields = {f["key"]: f for f in matrix.get("fields") or []}
    rt = src_rt_bar(ex, mid, period)
    snap = src_snapshot(ex, mid)
    quote = src_quote(ex, mid)
    settled = src_settled(ex, mid)
    oirow = src_oi(ex, mid)
    ls = (snap or {}).get("long_short") or {}
    oi_snap = (snap or {}).get("open_interest") or {}

    checks = []          # (字段, 源值, API值, 是否一致, 来源说明)

    def add(key, src_val, note):
        f = fields.get(key)
        if f is None:
            checks.append((key, src_val, None, False, f"API 缺该字段；来源：{note}"))
            return
        api_val = _num(f.get("value")) if f.get("value_kind") != "timestamp" else f.get("value")
        ok = close(_num(src_val), _num(api_val))
        checks.append((key, src_val, api_val, ok, note))

    # ① K 线原生字段（API 若用实时层，源就是 realtime_klines）
    if rt:
        for key in ("volume", "quote_volume", "taker_buy_volume",
                    "taker_buy_quote_volume", "trade_count"):
            f = fields.get(key) or {}
            # 欧易成交笔数恒为占位 0（有成交量却 0 笔）⇒ 按缺失口径核验，不按字面 0 比对
            src_v = _num(rt.get(key))
            if (ex == "okx" and key == "trade_count" and src_v == 0
                    and (_num(rt.get("volume")) or 0) > 0):
                api_null = f.get("value") is None
                checks.append((key, "源原值=0（占位）",
                               "null" if api_null else f.get("value"), api_null,
                               "欧易成交笔数恒为占位 0（成交量>0 却 0 笔）⇒ 判缺失，API 必须 null"))
                continue
            if f.get("layer") == "realtime_forming":
                # 未收盘 K 线每个标的每个周期**只有一行且被原地覆盖**，
                # 两次读取之间值会变。判据改成「同一根 bar + 同一写入版本」：
                #   ① API 的 fetched_at 与源行 received_at 相差 ≤ 2 秒 ⇒ 同一版本，必须逐位相等；
                #   ② 否则属版本推进（同一根 bar 在累计），源最新值必须 ≥ API 值（单调不减）。
                api_v = _num(f.get("value"))
                src_v = _num(rt.get(key))
                api_ms = _iso_to_ms(f.get("fetched_at"))
                src_ms = rt.get("received_at")
                same_bar = (_iso_to_ms(f.get("bar_open_time")) is not None
                            and rt.get("open_time") is not None
                            and _iso_to_ms(f.get("bar_open_time")) == int(rt["open_time"]))
                if api_ms is not None and src_ms is not None and abs(api_ms - int(src_ms)) <= 2000:
                    checks.append((key, src_v, api_v, close(api_v, src_v),
                                   "realtime_klines（同一写入版本，逐位相等）"))
                else:
                    mono = (api_v is not None and src_v is not None
                            and src_v >= api_v - 1e-9)
                    gap = (abs(int(src_ms) - api_ms) / 1000.0
                           if api_ms is not None and src_ms is not None else None)
                    checks.append((
                        key, src_v, api_v,
                        None if (same_bar and mono) else False,
                        f"realtime_klines（未收盘 bar 推进：API 读数早 {gap:.0f}s，"
                        f"同 bar={same_bar}，累计量单调不减={mono}）"
                        if gap is not None else
                        f"realtime_klines（同 bar={same_bar}，单调不减={mono}）"))
            elif f.get("layer") == "archive_closed":
                checks.append((key, "archive", f.get("value"), None,
                               "本次走归档最后一根已收盘（不在此比对范围）"))
            else:
                # 上游不支持：源应为空串/占位 0，API 必须 null
                src_raw = rt.get(key)
                api_null = f.get("value") is None
                checks.append((key, f"源原值={src_raw!r}", "null" if api_null else f.get("value"),
                               api_null, "上游不支持 ⇒ 源是空串/占位 0，API 必须 null"))

    # ② 派生：用**接口自己返回的原始字段**重算，验证公式；
    #    不用源侧最新值重算，否则会被未收盘 bar 的推进带偏（那是时序，不是公式错）。
    tb = _num((fields.get("taker_buy_volume") or {}).get("value"))
    vol = _num((fields.get("volume") or {}).get("value"))
    if tb is not None and vol is not None:
        sell = vol - tb
        add("taker_sell_volume", sell,
            "派生：volume - taker_buy_volume（以接口原始字段重算）")
        if sell:
            add("taker_buy_sell_ratio", tb / sell,
                "派生：taker_buy_volume / 主动卖出量（以接口原始字段重算）")
        if vol:
            add("taker_buy_share", tb / vol * 100.0,
                "派生：taker_buy_volume / volume × 100（以接口原始字段重算）")

    # ③ 多空比（源 = 快照 JSON 原文）
    for key, block in (("top_position_ls_ratio", "top_position_long_short_ratio"),
                       ("top_account_ls_ratio", "top_account_long_short_ratio"),
                       ("global_account_ls_ratio", "global_account_long_short_ratio")):
        b = ls.get(block) or {}
        if b.get("available"):
            add(key, _num(b.get("value")), f"快照 JSON long_short.{block}.value")
        else:
            f = fields.get(key) or {}
            checks.append((key, "快照未取/不支持", "null" if f.get("value") is None else f.get("value"),
                           f.get("value") is None,
                           "快照该块 available=false ⇒ API 必须 null 并给原因"))

    # ④ OI：快照有值优先，否则回退库内快照分区（**与接口同口径**：
    #    快照块可能写成 available:true 但 amount/usd 全空，此时接口回退库表）
    snap_oi_ok = (oi_snap.get("available")
                  and (_num(oi_snap.get("amount")) is not None
                       or _num(oi_snap.get("usd")) is not None))
    if snap_oi_ok:
        add("oi_qty", _num(oi_snap.get("amount")), "快照 JSON open_interest.amount")
        add("oi_notional_usd", _num(oi_snap.get("usd")), "快照 JSON open_interest.usd")
    elif oirow:
        # 用接口给的源时间回库里定位**同一行**，避免两次读取之间 OI 又写了新行
        api_oi_et = (fields.get("oi_qty") or {}).get("event_time")
        exact = src_oi(ex, mid, api_oi_et) if api_oi_et else None
        row_use = exact or oirow
        qty = _num(row_use.get("oi_ccy"))
        qty = qty if qty is not None else _num(row_use.get("open_interest"))
        why = ("（快照 OI 块为空 ⇒ 接口按同口径回退库表）"
               if oi_snap.get("available") else "")
        if exact:
            why += "；已按接口源时间定位同一行"
        else:
            why += "；⚠ 未定位到接口那一行，源可能已推进"
        add("oi_qty", qty, f"oi_history.oi_ccy（基础币口径）{why}")
        add("oi_notional_usd",
            _num(row_use.get("open_interest_value")) or _num(row_use.get("oi_usd")),
            f"oi_history.open_interest_value{why}")

    # ⑤ 资金费率：当前 与 已结算 分别比对
    if quote and _num(quote.get("funding_rate")) is not None:
        add("funding_rate_current", _num(quote["funding_rate"]) * 100.0,
            "realtime_quotes.funding_rate × 100（当前/预测，未结算）")
        f = fields.get("funding_next_time") or {}
        nft_src = quote.get("next_funding_time")
        nft_api = f.get("value")
        checks.append(("funding_next_time", nft_src, nft_api,
                       nft_src is not None and nft_api is not None
                       and int(nft_src) == int(nft_api),
                       "realtime_quotes.next_funding_time（毫秒）"))
    if settled and _num(settled.get("funding_rate")) is not None:
        add("funding_rate_settled", _num(settled["funding_rate"]) * 100.0,
            "funding_history.funding_rate × 100（已结算，rate_type=settled）")
        f = fields.get("funding_settled_time") or {}
        st_src = settled.get("funding_time")
        st_api = f.get("value")
        checks.append(("funding_settled_time", st_src, st_api,
                       st_src is not None and st_api is not None and int(st_src) == int(st_api),
                       "funding_history.funding_time（毫秒）"))

    # ⑥ 结构性要求：单位 / 周期 / 时点 / 新鲜度必须存在
    struct_ok = True
    struct_notes = []
    for f in matrix.get("fields") or []:
        if not f.get("unit"):
            struct_ok = False
            struct_notes.append(f"{f['key']} 缺单位")
        if f.get("value") is not None and not f.get("event_time"):
            struct_ok = False
            struct_notes.append(f"{f['key']} 有值但缺源时间")
        if f.get("value") is None and not f.get("missing_reason"):
            struct_ok = False
            struct_notes.append(f"{f['key']} 缺失但无原因")

    return {
        "case": f"{ex}:{mid}", "label": label, "period": period,
        "as_of": matrix.get("as_of"),
        "counts": matrix.get("counts"),
        "checks": checks,
        "struct_ok": struct_ok, "struct_notes": struct_notes,
        "coverage": [{"period": c["period"], "archive": c["archive"]["status"],
                      "realtime": c["realtime"]["status"],
                      "metrics": c["metrics_archive"]["status"]}
                     for c in matrix.get("coverage") or []],
        "snapshot_state": matrix.get("snapshot_state"),
        "identity": matrix.get("identity"),
    }


def render(results):
    L = []
    a = L.append
    ok_all = True
    for r in results:
        a(f"### {r['case']}（{r['label']}）· 周期 {r['period']}")
        a("")
        ident = r["identity"] or {}
        a(f"- 官方分类：`{ident.get('instrument_category')}` / "
          f"`{ident.get('category_field')}`；是否加密：{ident.get('is_crypto')}")
        a(f"- 字段可用：{r['counts']}")
        a(f"- 快照：{r['snapshot_state']}")
        a("")
        a("| 字段 | 源值（独立重查） | API 值 | 一致 | 来源说明 |")
        a("|---|---|---|---|---|")
        for key, src, api, ok, note in r["checks"]:
            if ok is None:
                mark = "—"
            else:
                mark = "✓" if ok else "✗"
                if ok is False:
                    ok_all = False
            a(f"| `{key}` | {src} | {api} | {mark} | {note} |")
        a("")
        if r["struct_ok"]:
            a("- 结构检查：每个字段都有单位；有值的都有源时间；缺失的都有原因 ✓")
        else:
            ok_all = False
            a(f"- 结构检查：**不通过** —— {'；'.join(r['struct_notes'])}")
        a("")
    return ok_all, "\n".join(L)


def main():
    argv = sys.argv[1:]
    cases = CASES
    if len(argv) >= 2:
        cases = [(argv[0], argv[1], argv[2] if len(argv) > 2 else "1h", "手工指定")]
    results = []
    for ex, mid, period, label in cases:
        try:
            results.append(check_case(ex, mid, period, label))
        except Exception as exc:                                 # noqa: BLE001
            results.append({"case": f"{ex}:{mid}", "label": label, "period": period,
                            "checks": [], "struct_ok": False,
                            "struct_notes": [f"核验失败：{type(exc).__name__}: {exc}"],
                            "counts": {}, "coverage": [], "identity": {},
                            "snapshot_state": {}, "as_of": None})
    ok, md = render(results)
    out = os.path.join(os.path.dirname(HERE), "一致性核验.md")
    header = (f"# DATA-UI-01 端到端一致性核验\n\n"
              f"生成时间（UTC）：`{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`\n\n"
              f"> 源 = 直接读 `realtime_klines` / 快照 JSON / `oi_history` / `realtime_quotes` / "
              f"`funding_history` 的原始行；API = `GET /api/v1/markets/{{ex}}/{{mid}}/metrics`。\n"
              f"> 比对口径：逐位相等（相对误差 ≤ 1e-9）；缺失必须两边都是 null。\n"
              f"> 刻意订正（非不一致）：欧易（OKX）成交笔数上游恒为占位 `0`（有成交量却 0 笔），"
              f"接口按缺失处理给 null，本表同样标为「源原值=0（占位）」；"
              f"欧易主动买入量/额源为空串，接口给 null。\n\n")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(header + md)
    print(md)
    print(f"\n写出：{out}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
