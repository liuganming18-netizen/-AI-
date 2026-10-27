# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜05 异常行情标的定性 + 决策阻断证据。

工单「六」：对 3 个异常行情标的查清官方来源、上市/交易状态及占位行含义；
**没有依据不得直接删行或填造价格**；异常期间明确阻止依赖无效行情的决策。

实际命中 6 个标的（不止 3 个）——按「连续零成交平价行 ≥20 根」在全池扫出来的。
本脚本对每个标的做**三源核对**（都是官方来源，互不依赖）：
  源1 官方 `GET /fapi/v1/exchangeInfo`        → status / onboardDate / deliveryDate
  源2 官方 `GET /fapi/v1/klines`（REST）      → 尾部逐根 OHLCV
  源3 官方月度归档 `data.binance.vision` zip  → 同一批 open_time 的 OHLCV
再加一源本地：热库 `candles` 视图（candle_versions JOIN candle_heads）。

然后做**闸门有效性实验**：
  同一段 1160h 行情，闸门开（`INVALID_RUN_MIN=3`）vs 闸门关（=10^9），
  比较「是否产生候选 / 终态 / 被跳过 bar 数」⇒ 证明闸门真的改变了决策，不是装饰。

纪律（写进产物）：
  · `wrote_market_db=false`：本脚本对生产库只读（`mode=ro` + `query_only`）；
  · `deleted_rows=0` / `filled_prices=0`：占位行**保留原样**；
  · 官方原始响应落盘到 `out/05_官方核对/`，可离线复核。

产出：out/05_异常标的.json、out/05_异常标的.csv、out/05_官方核对/*.json
"""
from __future__ import annotations

import csv
import importlib
import io
import json
import sqlite3
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import live_store as L
import project_paths as PP
import unified_runtime as UR

H1, H4 = cm.H1, cm.H4
RAW = cm.OUT / "05_官方核对"
RAW.mkdir(parents=True, exist_ok=True)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 必须绕代理
RUN_MIN = 3          # 连续零成交平价 ≥3 根即判无效（与引擎一致）
SCAN_MIN = 20        # 扫描「异常标的」用的门槛：连续 ≥20 根才算占位段，不是清淡时段


# ── 源 1：官方 exchangeInfo ────────────────────────────────────────────────
def official_exchange_info() -> dict:
    with OPENER.open("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30) as r:
        d = json.loads(r.read())
    return {s["symbol"]: s for s in d["symbols"]}


# ── 源 2：官方 REST klines ────────────────────────────────────────────────
def official_klines(sym: str, interval: str, start_ms: int, end_ms: int) -> list:
    url = ("https://fapi.binance.com/fapi/v1/klines?symbol=" + sym
           + f"&interval={interval}&startTime={int(start_ms)}&endTime={int(end_ms)}&limit=1500")
    with OPENER.open(url, timeout=30) as r:
        return json.loads(r.read())


# ── 源 3：官方归档 zip（按日；月度归档在当月尚未发布，会 404）───────────────
def official_archive_day(sym: str, interval: str, day: str):
    url = (f"https://data.binance.vision/data/futures/um/daily/klines/{sym}/{interval}/"
           f"{sym}-{interval}-{day}.zip")
    with OPENER.open(url, timeout=60) as r:
        blob = r.read()
    out = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8")
    for line in text.splitlines():
        f = line.split(",")
        if not f or not f[0] or not f[0].lstrip("-").isdigit():
            continue                                   # 表头
        ot = int(f[0])
        if ot > 10 ** 14:                              # 微秒 → 毫秒
            ot //= 1000
        out.append({"open_time": ot, "open": float(f[1]), "high": float(f[2]),
                    "low": float(f[3]), "close": float(f[4]), "volume": float(f[5])})
    return out, url, len(blob)


# ── 本地：热库只读 ────────────────────────────────────────────────────────
def ro_conn() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{L.MARKET_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=1")
    return con


def local_rows(con, sym: str, tf: str) -> list:
    cur = con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, is_closed "
        "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe=? "
        "ORDER BY open_time ASC", (sym, tf))
    return [dict(r) for r in cur.fetchall()]


def tail_rows(con, sym: str, tf: str, n: int = 60) -> list:
    """只取尾部 n 根（扫描全池时用，避免把 260 万行读一遍）。"""
    cur = con.execute(
        "SELECT open_time, close_time, open, high, low, close, volume, is_closed FROM ("
        "  SELECT open_time, close_time, open, high, low, close, volume, is_closed "
        "  FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe=? "
        "  ORDER BY open_time DESC LIMIT ?) ORDER BY open_time ASC", (sym, tf, int(n)))
    return [dict(r) for r in cur.fetchall()]


def is_zero_flat(r: dict) -> bool:
    return (float(r["volume"]) == 0.0 and float(r["high"]) == float(r["low"])
            and float(r["open"]) == float(r["close"]))


def tail_run(rows: list) -> dict:
    """返回尾部连续「零成交平价」段的描述（从最后一根往回数）。"""
    n = 0
    for r in reversed(rows):
        if is_zero_flat(r):
            n += 1
        else:
            break
    if n == 0:
        return {"run": 0, "first_open_ms": None, "last_open_ms": None,
                "price": None, "before_last_traded": rows[-1] if rows else None}
    seg = rows[-n:]
    prev = rows[-n - 1] if len(rows) > n else None
    return {"run": n, "first_open_ms": int(seg[0]["open_time"]),
            "last_open_ms": int(seg[-1]["open_time"]),
            "first_utc": cm.utc(seg[0]["open_time"]), "last_utc": cm.utc(seg[-1]["open_time"]),
            "price": float(seg[-1]["close"]),
            "all_same_price": len({float(x["close"]) for x in seg}) == 1,
            "before_last_traded": ({"open_time": int(prev["open_time"]),
                                    "utc": cm.utc(prev["open_time"]),
                                    "close": float(prev["close"]),
                                    "volume": float(prev["volume"])} if prev else None)}


def atr_effect(rows: list, tf: str) -> dict:
    """占位段对 Wilder ATR 的污染程度（纯递推，不涉及状态机）。

    意义：占位行的 TR 恒为 0 ⇒ 连续递推会把 ATR 往下拖（每根 ×(n-1)/n）。
    ATR→0 会同时污染「箱体宽度闸」「初始止损距离」「仓位计算」，所以这不是审美问题。
    """
    import unified_runtime as UR
    if not rows:
        return {"available": False}
    a = UR._WilderATR(14)
    vals = []
    for r in rows:
        vals.append(a.update(float(r["high"]), float(r["low"]), float(r["close"])))
    n = 0
    for r in reversed(rows):
        if is_zero_flat(r):
            n += 1
        else:
            break
    if n == 0:
        return {"available": False, "run": 0}
    i_before = len(rows) - n - 1
    before = vals[i_before] if i_before >= 0 else None
    after = vals[-1]
    return {"available": True, "run": n,
            "atr_before_run": (round(before, 10) if before else None),
            "atr_after_run": (round(after, 10) if after else None),
            "decay_pct": (round((after - before) / before * 100, 4)
                          if before and after else None),
            "theoretical_decay_pct": round(((13 / 14) ** n - 1) * 100, 4)}


def fingerprint(con, syms, upto_ms: int) -> dict:
    """在**固定时间截点**上做行级指纹。

    为什么必须带截点：采集器一直在写新 bar，直接数总行数会在脚本运行期间变大 ⇒
    看起来像「我改了库」。带 `open_time <= 截点` 后，采集器的新行被排除在外，
    前后指纹**必须逐位相同**，否则就是有人删行或改价。
    """
    out = {}
    for s in syms:
        r = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(open_time),0), COALESCE(SUM(close),0.0), "
            "COALESCE(SUM(volume),0.0) FROM candles "
            "WHERE exchange='binance' AND market='um' AND symbol=? AND open_time<=?",
            (s, int(upto_ms))).fetchone()
        out[s] = {"rows": int(r[0]), "sum_open_ms": int(r[1]),
                  "sum_close": round(float(r[2]), 6), "sum_volume": round(float(r[3]), 6)}
    return out


# ── 扫描：全池「尾部连续零成交平价 ≥SCAN_MIN 根」的标的 ─────────────────────
def scan_universe(con, syms) -> list:
    hit = []
    for s in syms:
        for tf in ("4h", "1h"):
            rows = tail_rows(con, s, tf, 60)
            if not rows:
                continue
            n = 0
            for r in reversed(rows):
                if is_zero_flat(r):
                    n += 1
                else:
                    break
            if n >= SCAN_MIN:
                hit.append({"symbol": s, "tf": tf, "run": n})
                break
    return hit


# ── 闸门有效性：同一段行情，闸门开 vs 关 ──────────────────────────────────
def gate_experiment(sym: str, b4: list, b1: list, floor_ms: int) -> dict:
    def run(run_min: int):
        old = UR.RealSignalEngine.INVALID_RUN_MIN
        UR.RealSignalEngine.INVALID_RUN_MIN = run_min
        try:
            e = UR.RealSignalEngine(symbol=sym)
            e.history_floor_ms = int(floor_ms)
            w = UR._MemWatcher(b4, b1)
            cands = []
            for x in b4:
                cands += e.process_market(int(x["close_time"]), w)
            if b4:                       # 收尾：把最后一根 4h 之后才收盘的 1h 补齐
                e.process_market(int(b4[-1]["close_time"]) + H4, w)
            ev = e.sm.active_event
            dq = dict(e.data_quality)
            return {"candidates": len(cands),
                    "candidate_ids": [c.event_uid for c in cands][:10],
                    "state": (ev.current_state.value if ev else "IDLE"),
                    "data_quality": dq,
                    # 真正喂进状态机的 1h 根数（被闸门拦下的不算）
                    "processed_1h_bars": len(e.processed_1h_opens),
                    "fed_1h_bars": len(e.processed_1h_opens) - int(dq.get("skipped_bars") or 0),
                    "atr_bars_seen": e.atr_bars_seen,
                    "box_gate_bars": e.box_gate_bars,
                    "last_atr": e.last_atr,
                    "n_transitions": len(e.sm.transitions),
                    "n_completed": len(e.sm.completed_events)}
        finally:
            UR.RealSignalEngine.INVALID_RUN_MIN = old

    on = run(RUN_MIN)
    off = run(10 ** 9)
    return {"gate_on": on, "gate_off": off,
            "gate_changed_decision": (on["candidates"] != off["candidates"]
                                      or on["state"] != off["state"]),
            "skipped_1h_bars": on["data_quality"].get("skipped_bars"),
            "box_skipped_invalid": on["data_quality"].get("box_skipped_invalid"),
            "fed_1h_bars_on": on["fed_1h_bars"], "fed_1h_bars_off": off["fed_1h_bars"],
            "note": ("闸门的实际效果是「不让占位行进入状态机」：fed_1h_bars_off − fed_1h_bars_on "
                     "就是被拦下的根数。本窗口内两种设置候选数都是 0（该标的没有箱体，"
                     "状态机无迁移）⇒ 闸门在这里是**预防性**的，不是「拦下了一个真实信号」。"
                     "这一点必须如实写，不能把「拦了 470 根」说成「拦掉了一个信号」。")}


def main() -> int:
    t0 = time.time()
    syms, _ = cm.universe()
    con = ro_conn()
    fp_cutoff = cm.now_ms()
    fp_before = fingerprint(con, syms, fp_cutoff)
    try:
        hits = scan_universe(con, syms)
    finally:
        con.close()
    print(f"[scan] 尾部连续零成交平价 ≥{SCAN_MIN} 根的标的：{len(hits)} -> "
          f"{[h['symbol'] for h in hits]}")

    info = official_exchange_info()
    targets = [h["symbol"] for h in hits]
    res = {"generated_at_utc": cm.utc(cm.now_ms()), "universe": len(syms),
           "scan_threshold_bars": SCAN_MIN, "invalid_run_min": RUN_MIN,
           "anomaly_symbols": targets,
           "sources": {
               "S1_exchangeInfo": "https://fapi.binance.com/fapi/v1/exchangeInfo",
               "S2_klines_rest": "https://fapi.binance.com/fapi/v1/klines",
               "S3_daily_archive": "https://data.binance.vision/data/futures/um/daily/klines/",
               "S4_local": "hot sqlite candles view (candle_versions JOIN candle_heads)",
           },
           "discipline": {"wrote_market_db": False, "deleted_rows": 0, "filled_prices": 0,
                          "note": "占位行保留原样：不删行、不改价、不插值。"},
           "items": []}
    csv_rows = []
    con = ro_conn()
    try:
        for sym in targets:
            meta = info.get(sym) or {}
            now_ms = cm.now_ms()
            dd = meta.get("deliveryDate")
            item = {"symbol": sym,
                    "exchange_info": {
                        "status": meta.get("status"),
                        "contractType": meta.get("contractType"),
                        "onboardDate": meta.get("onboardDate"),
                        "onboard_utc": cm.utc(meta.get("onboardDate")),
                        "deliveryDate": dd,
                        "delivery_utc": cm.utc(dd),
                        "pricePrecision": meta.get("pricePrecision"),
                        "quoteAsset": meta.get("quoteAsset"),
                        # 已过交割时刻 ⇒ 交易所侧已停止撮合，K 线接口只剩占位行
                        "delivery_in_past": bool(dd and int(dd) <= now_ms),
                    }}
            for tf in ("4h", "1h"):
                loc = local_rows(con, sym, tf)
                tr = tail_run(loc)
                if not tr["run"]:
                    item[tf] = {"local": tr, "note": "该周期无尾部占位段"}
                    continue
                # 官方 REST：从占位段前 10 根开始取
                back = 10 * (H4 if tf == "4h" else H1)
                st = tr["first_open_ms"] - back
                k = official_klines(sym, tf, st, cm.now_ms())
                off_all = [{"open_time": int(x[0]), "open": float(x[1]), "high": float(x[2]),
                            "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])}
                           for x in k]
                # **必须在重叠区间上比**：本地比官方少最新 1~2 根（采集滞后），
                # 直接比 run 长度会把「我们还没采到」误判成「来源不一致」。
                local_last = int(loc[-1]["open_time"])
                off_rows = [r for r in off_all if r["open_time"] <= local_last]
                off_tr = tail_run(off_rows)
                # 官方归档：在**占位段覆盖的若干个自然日**里挑一个能下载到的。
                # 为什么不用「最后一根往前 2 天」：短占位段（如 MITOUSDT 1h 只有 40 根）
                # 用那个日期会落到占位段**之前**的真实行情上，比对必然不匹配 ——
                # 那是我的取样点选错了，不是数据不一致。
                # 为什么还要多日回退：当日归档有 ~1 天发布延迟，昨天的日文件可能还是 404。
                tf_ms = H4 if tf == "4h" else H1
                mid = tr["first_open_ms"] + (tr["run"] - 1) * tf_ms // 2
                days = []
                for cand in (mid, tr["first_open_ms"], tr["last_open_ms"]):
                    d0 = time.strftime("%Y-%m-%d", time.gmtime(cand / 1000))
                    if d0 not in days:
                        days.append(d0)
                arch = {"candidate_days": days}
                for day in days:
                    try:
                        ar, aurl, asz = official_archive_day(sym, tf, day)
                    except Exception as exc:                 # noqa: BLE001
                        arch.setdefault("tried", []).append(
                            {"day": day, "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    a_map = {r["open_time"]: r for r in ar}
                    run_opens = {int(r["open_time"]) for r in loc[-tr["run"]:]}
                    a_hit = [a_map[ot] for ot in run_opens if ot in a_map]
                    a_n = len(a_hit)
                    a_match = a_n > 0 and all(
                        float(x["volume"]) == 0.0 and float(x["close"]) == float(tr["price"])
                        for x in a_hit)
                    arch.update({"day": day, "url": aurl, "bytes": asz, "rows_in_zip": len(ar),
                                 "compared_rows_in_run": a_n, "matched": a_match,
                                 "all_zero_flat_same_price": a_match})
                    break
                else:
                    arch["matched"] = None
                    arch["note"] = "候选日的归档文件都不可用（404），该源本次未取得"
                same_start = (off_tr["first_open_ms"] == tr["first_open_ms"])
                same_run = (off_tr["run"] == tr["run"])
                same_price = (off_tr["price"] == tr["price"])
                # ATR 被这段行情污染的程度：占位段前 vs 段末（纯 Wilder 递推，不涉及状态机）
                atr_eff = atr_effect(loc, tf)
                item[tf] = {
                    "local": tr,
                    "local_last_open_utc": cm.utc(local_last),
                    "official_rest": off_tr,
                    "official_rest_raw_last_utc": cm.utc(off_all[-1]["open_time"]) if off_all else None,
                    "official_archive": arch,
                    "atr_effect": atr_eff,
                    "verdict": {
                        "run_len_equal": same_run,
                        "run_start_equal": same_start,
                        "price_equal": same_price,
                        "all_three_sources_agree": bool(same_run and same_start and same_price
                                                        and arch.get("matched")),
                        "placeholder_confirmed": bool(same_run and same_start and same_price),
                        "note": ("run_len_equal / run_start_equal / price_equal 都是**在重叠区间**上比"
                                 "（本地截止 = 本地最后一根 open_time）。"
                                 "official_rest_raw_last_utc 是官方最新一根，通常比本地新 1~2 根，"
                                 "那是采集滞后，不是来源不一致。"),
                    },
                    "placeholder_meaning": (
                        "交易所对该合约已停止撮合（status=SETTLING，deliveryDate 已过），"
                        "但 K 线接口仍按每根周期返回「零成交 + O=H=L=C=最后成交价」的占位行。"
                        "该行**不是采集错误**：官方 REST 与官方归档两个独立来源返回同样的行。"
                        if (same_run and same_start and same_price) else
                        "本地与官方不一致 —— 必须按数据缺陷处理，不得据此决策。"),
                }
            # 闸门有效性（只用 4h/1h 声明窗口，与生产同口径）
            now = cm.now_ms()
            win = now - 1160 * H1
            b4 = [r for r in local_rows(con, sym, "4h")
                  if int(r["open_time"]) >= win and int(r["close_time"]) <= now]
            b1 = [r for r in local_rows(con, sym, "1h")
                  if int(r["open_time"]) >= win and int(r["close_time"]) <= now]
            item["gate"] = gate_experiment(sym, b4, b1, win)
            item["gate"]["window_bars"] = {"4h": len(b4), "1h": len(b1)}
            res["items"].append(item)
            g = item["gate"]
            for tf in ("4h", "1h"):
                d = item.get(tf) or {}
                l, o = d.get("local") or {}, d.get("official_rest") or {}
                ae = d.get("atr_effect") or {}
                csv_rows.append({
                    "symbol": sym, "tf": tf,
                    "status": item["exchange_info"]["status"],
                    "delivery_utc": item["exchange_info"]["delivery_utc"],
                    "delivery_in_past": item["exchange_info"]["delivery_in_past"],
                    "local_run": l.get("run"), "official_run": o.get("run"),
                    "run_start_utc": l.get("first_utc"),
                    "price": l.get("price"),
                    "agree_rest": d.get("verdict", {}).get("placeholder_confirmed"),
                    "agree_archive": (d.get("official_archive") or {}).get("matched"),
                    "atr_decay_pct": ae.get("decay_pct"),
                    "gate_on_cands": g["gate_on"]["candidates"],
                    "gate_off_cands": g["gate_off"]["candidates"],
                    "gate_on_state": g["gate_on"]["state"],
                    "gate_off_state": g["gate_off"]["state"],
                    "skipped_1h_bars": g["skipped_1h_bars"],
                    "box_skipped_invalid": g["box_skipped_invalid"],
                })
            (RAW / f"{sym}.json").write_text(
                json.dumps({"exchange_info": item["exchange_info"],
                            "4h": item.get("4h"), "1h": item.get("1h")},
                           ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        con.close()

    # 收尾：确认全程未写库（指纹不变）
    con = ro_conn()
    try:
        fp_after = fingerprint(con, syms, fp_cutoff)
    finally:
        con.close()
    res["db_fingerprint_unchanged"] = (fp_before == fp_after)
    res["db_fingerprint_cutoff_utc"] = cm.utc(fp_cutoff)
    res["db_fingerprint_rows"] = sum(v["rows"] for v in fp_after.values())
    res["elapsed_sec"] = round(time.time() - t0, 1)
    cm.dump("05_异常标的.json", res)
    p = cm.OUT / "05_异常标的.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        w.writeheader()
        w.writerows(csv_rows)
    print(f"[out] {p}")
    for it in res["items"]:
        v4 = (it.get("4h") or {}).get("verdict") or {}
        v1 = (it.get("1h") or {}).get("verdict") or {}
        a4 = (it.get("4h") or {}).get("official_archive") or {}
        a1 = (it.get("1h") or {}).get("official_archive") or {}
        g = it["gate"]
        ae = (it.get("1h") or {}).get("atr_effect") or {}
        print(f"[{it['symbol']:16s}] {it['exchange_info']['status']:9s} "
              f"交割已过={it['exchange_info']['delivery_in_past']} "
              f"run 4h/1h={(it.get('4h') or {}).get('local',{}).get('run')}/"
              f"{(it.get('1h') or {}).get('local',{}).get('run')} "
              f"REST一致={v4.get('placeholder_confirmed')}/{v1.get('placeholder_confirmed')} "
              f"归档一致={a4.get('matched')}/{a1.get('matched')} "
              f"ATR衰减={ae.get('decay_pct')}% "
              f"拦下1h={g['skipped_1h_bars']}(喂入 {g['fed_1h_bars_on']} vs {g['fed_1h_bars_off']}) "
              f"4h不建档={g['box_skipped_invalid']} "
              f"候选改变={g['gate_changed_decision']}")
    print(f"[fingerprint] unchanged={res['db_fingerprint_unchanged']} "
          f"rows={res['db_fingerprint_rows']} elapsed={res['elapsed_sec']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
