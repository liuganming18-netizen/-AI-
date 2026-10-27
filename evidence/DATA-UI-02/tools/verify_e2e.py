#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-02：端到端复验 —— **原始存储 → 同源 API → 多次采样证明时间推进**。

与 DATA-UI-01 的 `verify_consistency.py` 的区别：
  · 除了「源 vs 接口逐字段相等」，还**多次采样**，证明数据在自己推进
    （不是页面刷新时间冒充数据更新时间）；
  · 每个字段都比 `value / unit / period / event_time / fetched_at` 五项；
  · 明确区分「值没变」与「没在更新」：值不变但 `fetched_at` 推进 = 正常；
    `fetched_at` 也不动 = 真的没在采。

采样设计（一次跑完约 7 分钟）：
  S1 → 等 70s → S2 → 等 70s → S3   （跨 2 个以上实时 K 线批次，约 30s 一批）
  S3 → 等 320s → S4                （跨 1 个公开扩展快照轮次，300s 一轮）

用法：
  python verify_e2e.py                 # 默认四案例，完整采样
  python verify_e2e.py --quick         # 只做 S1/S2，不跑长等待
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(EVID, "..", ".."))
BACKEND = os.path.join(ROOT, "框架", "backend")
sys.path.insert(0, BACKEND)

API = os.environ.get("API_BASE") or "http://127.0.0.1:8787"
DB = os.environ.get("MARKET_DB") or os.path.join(
    ROOT, "数据", "市场数据", "db", "market_v2.sqlite3")
SNAP = os.path.join(BACKEND, "快照", "public_extras")

CASES = [
    ("binance", "BTCUSDT", "1h", "主流标的"),
    ("binance", "ETHUSDT", "1h", "主流标的"),
    ("binance", "1000CATUSDT", "1h", "历史有指标的小币（固定观测）"),
    ("okx", "OKB-USDT-SWAP", "1h", "欧易补充位"),
]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def num(v):
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


def iso_ms(s):
    if not s:
        return None
    try:
        return int(datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def get(path):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as fh:
        return json.loads(fh.read().decode("utf-8"))


def conn():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ── 原始存储侧（独立重查，不调用接口的取数函数）──────────────────────────────

def src_rt_bar(ex, mid, period):
    con = conn()
    try:
        r = con.execute(
            "SELECT volume, quote_volume, taker_buy_volume, taker_buy_quote_volume, "
            "trade_count, open_time, event_time, received_at FROM realtime_klines "
            "WHERE exchange=? AND symbol=? AND timeframe=?", (ex, mid, period)).fetchone()
    finally:
        con.close()
    return dict(r) if r else None


def src_snapshot(ex, mid):
    p = os.path.join(SNAP, f"{ex}__{mid}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                             # noqa: BLE001
        return None


def src_oi(ex, mid, at_iso=None):
    con = conn()
    try:
        if at_iso:
            ms = iso_ms(at_iso)
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


def close(a, b, rel=1e-9):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a == b:
        return True
    d = abs(a - b)
    return d <= rel * max(abs(a), abs(b)) or d <= 1e-9


# ── 单次采样 ────────────────────────────────────────────────────────────────

# 这几种字段在 7 分钟窗口内**本来就不该变**，不能拿「没变」当故障：
#   · 已结算资金费：8 小时结算一次，两次结算之间恒为同一值；
#   · 轮转补采的两类账户多空比：62 标的 / 每轮 12 / 单轮实测约 456s（取数 156s + 间隔 300s）
#     ⇒ 同一标的大约 38~40 分钟才轮到一次，阈值也据此订正为 2880s。
SETTLE_KEYS = {"funding_rate_settled", "funding_settled_time"}
ROTATION_KEYS = {"top_account_ls_ratio", "global_account_ls_ratio"}
# OI 是**全池轮询**：528 个币安标的逐币取 + 欧易 1 次批量，一轮约 545s，
# 跑完再等 interval 600s ⇒ 同一标的约 **10~20 分钟**才刷新一次。
# 实测日志：04:49:58 / 04:59:58 / 05:09:58 / 05:31:56 / 05:51:19 / 06:00:58。
# 观察窗口只有 8 分钟，OI 在窗口内不变是**正常**，不能算「异常静止」。
OI_KEYS = {"oi_qty", "oi_notional_usd"}


def _verdict(key, seq, moving):
    """给每个字段一个结论，避免把「窗口内不变」一律算成故障。"""
    if moving:
        return "在推进"
    if all(x["value"] is None for x in seq):
        return "全空（缺失/上游不支持，不适用推进判据）"
    if key in SETTLE_KEYS:
        return "窗口内不变（结算类，8 小时一次）"
    if key in ROTATION_KEYS:
        return "窗口内不变（轮转类，约 40 分钟一轮）"
    if key in OI_KEYS:
        return "窗口内不变（OI 全池轮询，10~20 分钟一次）"
    return "异常静止"


def sample(tag):
    out = {"tag": tag, "at": now_iso(), "cases": []}
    for ex, mid, period, label in CASES:
        d = get(f"/api/v1/markets/{ex}/{mid}/metrics?period={period}")
        # 接口在 build_metrics 抛异常时会返回 200 + **空字段集**（`degraded: true`）。
        # 只看状态码会以为成功，所以这里显式判一次并重试一次：
        # 瞬时读空（例如刚好撞上快照重写）重试就能恢复；真故障会连报两次并留下原因。
        retried = 0
        while not (d.get("fields") or []) and retried < 1:
            retried += 1
            time.sleep(2)
            d = get(f"/api/v1/markets/{ex}/{mid}/metrics?period={period}")
        fields = {f["key"]: f for f in d.get("fields") or []}
        out["cases"].append({"ex": ex, "mid": mid, "period": period, "label": label,
                             "as_of": d.get("as_of"), "counts": d.get("counts"),
                             "fields": fields,
                             "degraded": bool(d.get("degraded")),
                             "degraded_reason": d.get("missing_reason"),
                             "retried": retried,
                             "identity": d.get("identity"),
                             "snapshot_state": d.get("snapshot_state")})
    return out


def compare_with_source(case):
    """逐字段与原始存储比对：value / unit / period / event_time / fetched_at。"""
    ex, mid, period = case["ex"], case["mid"], case["period"]
    F = case["fields"]
    rt = src_rt_bar(ex, mid, period)
    snap = src_snapshot(ex, mid)
    ls = (snap or {}).get("long_short") or {}
    oi_snap = (snap or {}).get("open_interest") or {}
    q = src_quote(ex, mid)
    st = src_settled(ex, mid)

    checks = []

    def add(key, src_val, src_note, src_event=None):
        f = F.get(key)
        if f is None:
            why = "接口缺该字段"
            if case.get("degraded_reason"):
                why = f"接口**降级**（返回空字段集）：{case['degraded_reason'][:90]}"
            checks.append((key, src_val, None, False, f"{why}（源：{src_note}）"))
            return
        api_val = f.get("value")
        ok = close(num(src_val), num(api_val))
        detail = []
        if not f.get("unit"):
            ok = False
            detail.append("缺单位")
        if src_event is not None and f.get("event_time") != src_event:
            ok = False
            detail.append(f"源时间不符（源 {src_event} / 接口 {f.get('event_time')}）")
        checks.append((key, src_val, api_val, ok,
                       f"{src_note}" + ("；" + "；".join(detail) if detail else "")))

    # K 线原生 + 派生
    if rt:
        for key in ("volume", "quote_volume", "taker_buy_volume",
                    "taker_buy_quote_volume", "trade_count"):
            f = F.get(key) or {}
            if f.get("layer") != "realtime_forming":
                continue
            if ex == "okx" and key in ("taker_buy_volume", "taker_buy_quote_volume",
                                       "trade_count"):
                continue          # 欧易上游不支持，单独在下方判
            api_ms = iso_ms(f.get("fetched_at"))
            src_ms = rt.get("received_at")
            if api_ms is not None and src_ms is not None and abs(api_ms - int(src_ms)) <= 2000:
                add(key, rt.get(key), "realtime_klines（同一写入版本）")
            else:
                checks.append((key, rt.get(key), f.get("value"), None,
                               "未收盘 bar 已推进（同一根 bar 的累计值在增长，不做逐位比较）"))
        # 派生三项：用接口自己的原始字段重算，验证公式
        tb = num((F.get("taker_buy_volume") or {}).get("value"))
        vol = num((F.get("volume") or {}).get("value"))
        if tb is not None and vol is not None:
            add("taker_sell_volume", vol - tb, "派生重算：volume - taker_buy_volume")
            if vol - tb:
                add("taker_buy_sell_ratio", tb / (vol - tb), "派生重算：买入 ÷ 卖出")
            if vol:
                add("taker_buy_share", tb / vol * 100.0, "派生重算：买入 ÷ 总量 × 100")

    # 多空比（快照原文）
    for key, block in (("top_position_ls_ratio", "top_position_long_short_ratio"),
                       ("top_account_ls_ratio", "top_account_long_short_ratio"),
                       ("global_account_ls_ratio", "global_account_long_short_ratio")):
        b = ls.get(block) or {}
        if b.get("available") and b.get("value"):
            add(key, b.get("value"), f"快照 JSON long_short.{block}",
                src_event=b.get("event_time"))
        else:
            f = F.get(key) or {}
            checks.append((key, "快照未取/不支持", "null" if f.get("value") is None else f.get("value"),
                           f.get("value") is None,
                           f"快照块不可用 ⇒ 接口必须 null 并给原因（{b.get('reason') or '无记录'}）"))

    # OI
    snap_ok = (oi_snap.get("available")
               and (num(oi_snap.get("amount")) is not None or num(oi_snap.get("usd")) is not None))
    if snap_ok:
        add("oi_qty", oi_snap.get("amount"), "快照 JSON open_interest.amount")
        add("oi_notional_usd", oi_snap.get("usd"), "快照 JSON open_interest.usd")
    else:
        api_et = (F.get("oi_qty") or {}).get("event_time")
        row = src_oi(ex, mid, api_et) or src_oi(ex, mid)
        if row:
            qty = num(row.get("oi_ccy"))
            qty = qty if qty is not None else num(row.get("open_interest"))
            add("oi_qty", qty, "oi_history.oi_ccy（基础币口径）")
            add("oi_notional_usd",
                num(row.get("open_interest_value")) or num(row.get("oi_usd")),
                "oi_history.open_interest_value")

    # 资金费率：当前 与 已结算 分开比
    if q and num(q.get("funding_rate")) is not None:
        add("funding_rate_current", num(q["funding_rate"]) * 100.0,
            "realtime_quotes.funding_rate × 100（当前/预测）")
        f = F.get("funding_next_time") or {}
        checks.append(("funding_next_time", q.get("next_funding_time"), f.get("value"),
                       (q.get("next_funding_time") is not None and f.get("value") is not None
                        and int(q["next_funding_time"]) == int(f["value"])),
                       "realtime_quotes.next_funding_time"))
    if st and num(st.get("funding_rate")) is not None:
        add("funding_rate_settled", num(st["funding_rate"]) * 100.0,
            "funding_history.funding_rate × 100（已结算）")

    # 结构性要求
    struct_bad = []
    for key, f in F.items():
        if not f.get("unit"):
            struct_bad.append(f"{key} 缺单位")
        if f.get("value") is not None and not f.get("event_time"):
            struct_bad.append(f"{key} 有值缺源时间")
        if f.get("value") is None and not f.get("missing_reason"):
            struct_bad.append(f"{key} 缺值无原因")
    return checks, struct_bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只做 S1/S2，不跑长等待")
    a = ap.parse_args()

    samples = []
    print(f"[{now_iso()}] S1 …", flush=True)
    samples.append(sample("S1"))
    time.sleep(70)
    print(f"[{now_iso()}] S2 …", flush=True)
    samples.append(sample("S2"))
    if not a.quick:
        time.sleep(70)
        print(f"[{now_iso()}] S3 …", flush=True)
        samples.append(sample("S3"))
        time.sleep(320)
        print(f"[{now_iso()}] S4 …", flush=True)
        samples.append(sample("S4"))

    # 与原始存储比对：**每个案例取最后一次「未降级」的采样**。
    # 若某次采样撞上接口降级（200 + 空字段集），用它做逐字段比对会把「接口故障」
    # 误报成「数据不一致」；这里改用最近一次正常采样，并把降级事件单列出来。
    per_case = []
    degraded_events = []
    for ci in range(len(CASES)):
        for s in samples:
            c0 = s["cases"][ci]
            if c0.get("degraded"):
                degraded_events.append({
                    "sample": s["tag"], "at": s["at"],
                    "case": f"{c0['ex']}:{c0['mid']}",
                    "reason": c0.get("degraded_reason"),
                    "retried": c0.get("retried")})
        pick_tag, pick = None, None
        for s in reversed(samples):
            c0 = s["cases"][ci]
            if not c0.get("degraded") and c0.get("fields"):
                pick_tag, pick = s["tag"], c0
                break
        if pick is None:
            pick_tag, pick = samples[-1]["tag"], samples[-1]["cases"][ci]
        checks, struct_bad = compare_with_source(pick)
        per_case.append({"case": f"{pick['ex']}:{pick['mid']}", "label": pick["label"],
                         "period": pick["period"], "counts": pick["counts"],
                         "as_of": pick["as_of"], "sample": pick_tag,
                         "checks": checks, "struct_bad": struct_bad,
                         "identity": pick["identity"],
                         "snapshot_state": pick["snapshot_state"]})

    # 时间推进：逐字段看 fetched_at / event_time / value 有没有变
    advance = []
    for ci, case0 in enumerate(samples[0]["cases"]):
        key = f"{case0['ex']}:{case0['mid']}"
        per_field = []
        for fk in case0["fields"]:
            seq = []
            for s in samples:
                c = s["cases"][ci]
                f = c["fields"].get(fk) or {}
                seq.append({"tag": s["tag"], "value": f.get("value"),
                            "event_time": f.get("event_time"),
                            "fetched_at": f.get("fetched_at")})
            fetched_set = {x["fetched_at"] for x in seq}
            event_set = {x["event_time"] for x in seq}
            value_set = {x["value"] for x in seq}
            per_field.append({
                "key": fk,
                "n_samples": len(seq),
                "fetched_at_changes": len(fetched_set),
                "event_time_changes": len(event_set),
                "value_changes": len(value_set),
                "fetched_ats": [x["fetched_at"] for x in seq],
                "event_times": [x["event_time"] for x in seq],
                "values": [x["value"] for x in seq],
                "moving": len(fetched_set) > 1 or len(event_set) > 1,
                "verdict": _verdict(fk, seq, len(fetched_set) > 1 or len(event_set) > 1),
            })
        advance.append({"case": key, "fields": per_field})

    out = {
        "generated_at": now_iso(),
        "api": API,
        "samples": [{"tag": s["tag"], "at": s["at"],
                     "as_of": {f"{c['ex']}:{c['mid']}": c["as_of"] for c in s["cases"]},
                     "degraded": [f"{c['ex']}:{c['mid']}" for c in s["cases"]
                                  if c.get("degraded")]}
                    for s in samples],
        "degraded_events": degraded_events,
        "source_compare": per_case,
        "time_advance": advance,
    }
    jp = os.path.join(EVID, "端到端复验.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    # ── Markdown ──
    L = []
    A = L.append
    A("# DATA-UI-02 端到端复验（原始存储 → 同源 API → 多次采样）\n")
    A(f"生成时间（UTC）：`{out['generated_at']}`　接口：`{API}`\n")
    A("> 判据：每个字段比 **值 / 单位 / 统计周期 / 源时间** 四项；")
    A("> 时间推进看 `fetched_at`（本机取数时刻）与 `event_time`（业务时刻）是否变化，")
    A("> **不看页面刷新时间**。值不变但 `fetched_at` 推进 = 正常；两者都不动 = 真的没在采。\n")

    A("## 一、采样时刻\n")
    A("| 采样 | 观察时刻 | " + " | ".join(f"{c[1]}" for c in CASES) + " |")
    A("|---|---|" + "---|" * len(CASES))
    for s in samples:
        row = [s["tag"], s["at"]]
        for c in s["cases"]:
            row.append((c["as_of"] or "—")
                       + ("　**降级**" if c.get("degraded") else ""))
        A("| " + " | ".join(row) + " |")
    A("")

    # 采样健康：接口降级（200 + 空字段集）必须单列，不能混进「字段不一致」
    A("## 一之二、采样健康（接口降级事件）\n")
    if degraded_events:
        A("接口在 `build_metrics` 抛异常时会返回 **HTTP 200 + 空字段集**，"
          "错误信息只在 `missing_reason` 里。这类事件与「数据不一致」是两件事，单列如下：\n")
        A("| 采样 | 时刻 | 案例 | 重试次数 | 接口给出的原因 |")
        A("|---|---|---|---|---|")
        for d in degraded_events:
            A(f"| {d['sample']} | {d['at']} | `{d['case']}` | {d['retried']} | "
              f"{(d['reason'] or '—')[:90]} |")
        A("")
        A("> 逐字段比对已改用**该案例最近一次未降级的采样**，不拿降级响应当数据去比。\n")
    else:
        A("本次采样全部正常，无降级事件。\n")

    A("## 二、源 vs 接口逐字段\n")
    for pc in per_case:
        A(f"### {pc['case']}（{pc['label']}）· {pc['period']}\n")
        A(f"- 字段可用：{pc['counts']}")
        A(f"- 官方分类：`{(pc['identity'] or {}).get('instrument_category')}` / "
          f"`{(pc['identity'] or {}).get('category_field')}`")
        A("")
        A("| 字段 | 源值 | 接口值 | 一致 | 说明 |")
        A("|---|---|---|---|---|")
        for key, sv, av, ok, note in pc["checks"]:
            mark = "—" if ok is None else ("✓" if ok else "✗")
            A(f"| `{key}` | {sv} | {av} | {mark} | {note} |")
        A("")
        if pc["struct_bad"]:
            A(f"- 结构检查：**不通过** —— {'；'.join(pc['struct_bad'])}\n")
        else:
            A("- 结构检查：每字段有单位、有值有源时间、缺值有原因 ✓\n")

    A("## 三、时间推进（跨采集周期）\n")
    A("> 「窗口内不变」不等于故障：已结算资金费 8 小时结算一次、轮转补采的账户多空比约 40 分钟"
      "才轮到一次、OI 全池轮询 10~20 分钟一次，8 分钟窗口内本来就不会变。"
      "只有**没有任何合理原因的静止**才算异常。\n")
    A("| 案例 | 字段 | 采样数 | 值变化 | fetched_at 变化 | event_time 变化 | 结论 |")
    A("|---|---|---|---|---|---|---|")
    for ca in advance:
        for f in ca["fields"]:
            A(f"| {ca['case']} | `{f['key']}` | {f['n_samples']} | "
              f"{f['value_changes']} | {f['fetched_at_changes']} | "
              f"{f['event_time_changes']} | {f['verdict']} |")
    A("")

    mp = os.path.join(EVID, "端到端复验.md")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    bad = sum(1 for pc in per_case for c in pc["checks"] if c[3] is False)
    bad += sum(1 for pc in per_case if pc["struct_bad"])
    stuck = sum(1 for ca in advance for f in ca["fields"]
                if f["verdict"] == "异常静止")
    print("\n".join(L[-40:]))
    print(f"\n逐字段不一致：{bad}　异常静止字段：{stuck}　接口降级事件：{len(degraded_events)}")
    print(f"写出：{jp}\n写出：{mp}", file=sys.stderr)
    return 0 if (bad == 0 and stuck == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
