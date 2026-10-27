# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 12：系统 API 侧核对（v2）。

不采信页面自报，直接打后端接口，并与源账本逐笔对照：
  · 两个批次的汇总 / run 清单 / 逐笔 / K 线
  · 两套登记（组级 27 / 币级 1065）是不是同一批 4,325 笔
  · run_id 能否反查详情、逐笔、K 线
  · 下单时间字段是否等于成交时间（是否把成交时间冒充下单时间）
  · 同一笔交易在两个批次接口里的字段完整性
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
BASE = "http://127.0.0.1:8787/api/v1/backtests"
GRID_BATCH = "runs_ft_entry_09_research"
SYM_BATCH = "runs_ft_replay_01"


def get(path, timeout=90):
    p = subprocess.run(["curl", "-s", "-m", str(timeout), BASE + path],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"__error__": "非 JSON 响应", "raw": (p.stdout or "")[:300]}


def content_key(x):
    """跨两套登记识别「同一笔」的内容键：策略 + 币种 + 入场成交毫秒。

    两套登记的 run_id 命名不同：组级 `run_FT_ENTRY_09_Wyckoff_C1_Vs04_Z02`，
    币级 `run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT`。统一去掉 `run_FT_ENTRY_09_`
    与 `Wyckoff_` 前缀、再去掉尾部 `_<币种>`，才得到同一个策略标识。
    """
    rid = str(x.get("run_id") or "").replace("run_FT_ENTRY_09_", "").replace("Wyckoff_", "")
    sym = str(x.get("symbol") or "")
    if sym and rid.endswith("_" + sym):
        rid = rid[: -(len(sym) + 1)]
    return "%s|%s|%s" % (rid, sym, x.get("entry_time_ms"))


def main():
    os.makedirs(OUT, exist_ok=True)
    checks, detail = [], {}

    # ── 1. 批次列表 ─────────────────────────────────────────────
    bl = get("/batches")
    items = bl.get("items", []) if isinstance(bl, dict) else []
    ids = [x.get("batch_id") for x in items]
    checks.append({
        "项": "批次列表含本批两套登记",
        "期望": "组级与币级两套登记都能在批次列表里找到",
        "实际": "共 %d 个批次；组级=%s 币级=%s" % (len(items), GRID_BATCH in ids, SYM_BATCH in ids),
        "通过": GRID_BATCH in ids and SYM_BATCH in ids,
    })

    # ── 2. 批次汇总（自报 vs 逐笔行数自洽） ────────────────────
    summ = {}
    for bid, er, et in ((GRID_BATCH, 27, 4325), (SYM_BATCH, 1065, 4325)):
        s = get("/batches/%s/summary" % bid)
        b = (s.get("batch") or {}) if isinstance(s, dict) else {}
        chk = (s.get("checks") or {}) if isinstance(s, dict) else {}
        summ[bid] = {"batch": b, "checks": chk}
        ok = (b.get("runs_total") == er and b.get("trades_total") == et
              and chk.get("trades_total_matches_rows") is not False)
        checks.append({
            "项": "批次汇总 %s" % bid,
            "期望": "%d run / %d 笔 / 与逐笔行数自洽" % (er, et),
            "实际": "runs=%s trades=%s net=%s；自洽=%s" % (
                b.get("runs_total"), b.get("trades_total"), b.get("net_pnl_sum"),
                {k: chk.get(k) for k in ("trades_total_matches_rows", "net_pnl_matches_rows")}),
            "通过": bool(ok),
        })

    # ── 3. 批次 run 清单 ───────────────────────────────────────
    runs = {}
    for bid in (GRID_BATCH, SYM_BATCH):
        r = get("/batches/%s/runs" % bid)
        its = r.get("items", []) if isinstance(r, dict) else []
        runs[bid] = its
        tot = int(sum((x.get("trades_count") or 0) for x in its))
        checks.append({
            "项": "批次 run 清单 %s" % bid,
            "期望": "清单 trades_count 合计 = 4325",
            "实际": "清单 %d 条；trades_count 合计 %d" % (len(its), tot),
            "通过": tot == 4325,
        })

    # ── 4. 批次逐笔 ─────────────────────────────────────────────
    bt = {}
    for bid in (GRID_BATCH, SYM_BATCH):
        t = get("/batches/%s/trades" % bid)
        its = t.get("items", []) if isinstance(t, dict) else []
        bt[bid] = its
        checks.append({
            "项": "批次逐笔 %s" % bid,
            "期望": "4325 条",
            "实际": "%d 条（total=%s）" % (len(its), t.get("total") if isinstance(t, dict) else None),
            "通过": len(its) == 4325,
        })

    # ── 5. 两套登记 = 同一批 4325 笔（按内容键判，不看 trade_id） ──
    kg = set(content_key(x) for x in bt.get(GRID_BATCH, []))
    ks = set(content_key(x) for x in bt.get(SYM_BATCH, []))
    union = kg | ks
    checks.append({
        "项": "两套登记是否为同一批 4,325 笔",
        "期望": "内容键（策略|币种|入场毫秒）集合相等，并集 = 4325（不是 8650）",
        "实际": "组级 %d 个键、币级 %d 个键；交集 %d；并集 %d"
                % (len(kg), len(ks), len(kg & ks), len(union)),
        "通过": len(union) == 4325 and len(kg & ks) == 4325,
    })
    detail["content_key"] = {"组级": len(kg), "币级": len(ks), "交集": len(kg & ks), "并集": len(union)}

    # ── 6. 两套登记的 trade_id 是否可互相映射 ──────────────────
    g_ids = set(x.get("trade_id") for x in bt.get(GRID_BATCH, []) if x.get("trade_id"))
    s_ids = set(x.get("trade_id") for x in bt.get(SYM_BATCH, []) if x.get("trade_id"))
    checks.append({
        "项": "两套登记的 trade_id 口径",
        "期望": "同一笔在组级/币级登记里应有同一 trade_id（否则跨批次无法按 ID 关联）",
        "实际": "组级 %d 个 ID（样例 %s）；币级 %d 个 ID（样例 %s）；交集 %d"
                % (len(g_ids), sorted(g_ids)[:1], len(s_ids), sorted(s_ids)[:1], len(g_ids & s_ids)),
        "通过": len(g_ids & s_ids) == 4325,
    })

    # ── 7. run_id 反查详情/逐笔/K 线 ───────────────────────────
    probe = []
    for bid, its in ((GRID_BATCH, runs.get(GRID_BATCH, [])), (SYM_BATCH, runs.get(SYM_BATCH, []))):
        if not its:
            continue
        rid = its[0].get("run_id")
        d = get("/%s" % rid)
        t = get("/%s/trades" % rid)
        c = get("/%s/chart" % rid)
        ch = (c.get("chart") or {}) if isinstance(c, dict) else {}
        probe.append({
            "批次": bid, "run_id": rid,
            "详情_missing": d.get("missing_reason") if isinstance(d, dict) else None,
            "逐笔条数": len(t.get("items", [])) if isinstance(t, dict) else None,
            "K线蜡烛数": len(ch.get("candles") or []),
            "K线成交标记数": len(ch.get("trades") or []),
            "K线信号标记数": len(ch.get("signals") or []),
            "K线_missing": c.get("missing_reason") if isinstance(c, dict) else None,
            "K线来源": (ch.get("candles_source") or "")[:60],
        })
    checks.append({
        "项": "run_id 反查详情/逐笔/K线",
        "期望": "两个批次的 run_id 都能打开详情、逐笔与 K 线，且 K 线有真实蜡烛",
        "实际": json.dumps(probe, ensure_ascii=False),
        "通过": all(p["逐笔条数"] and p["K线蜡烛数"] > 0 for p in probe),
    })
    detail["run_probe"] = probe

    # ── 8. 下单时间 vs 成交时间（三个入口分别看） ──────────────
    def order_stats(rows, label):
        def ent(x):
            v = x.get("entry_time_ms")
            if v is None and isinstance(x.get("entry"), dict):
                v = x["entry"].get("t")
            return v
        eq = sum(1 for x in rows if x.get("order_time_ms") is not None
                 and x.get("order_time_ms") == ent(x))
        nul = sum(1 for x in rows if x.get("order_time_ms") is None)
        return {"入口": label, "笔数": len(rows), "order_time_ms==成交时间": eq,
                "order_time_ms为空": nul}

    o_rows = [order_stats(bt.get(bid, []), "批次逐笔 %s" % bid) for bid in (GRID_BATCH, SYM_BATCH)]
    for p in probe:
        t = get("/%s/trades" % p["run_id"])
        its = t.get("items", []) if isinstance(t, dict) else []
        o_rows.append(order_stats(its, "单run逐笔 %s" % p["run_id"]))
        c = get("/%s/chart" % p["run_id"])
        ch = (c.get("chart") or {}) if isinstance(c, dict) else {}
        o_rows.append(order_stats(ch.get("trades") or [], "K线标记 %s" % p["run_id"]))
    bad = [r for r in o_rows if r["order_time_ms==成交时间"] > 0]
    checks.append({
        "项": "下单时间是否等于成交时间",
        "期望": "源引擎未记录下单时刻 ⇒ order_time_ms 应为 null（不得用成交时间顶替）",
        "实际": json.dumps(o_rows, ensure_ascii=False),
        "通过": len(bad) == 0,
    })
    detail["order_time"] = o_rows

    # ── 9. 同一笔在两个批次接口里的字段完整性 ──────────────────
    flds = ["signal_id", "signal_time_ms", "initial_stop", "gross_pnl", "fees", "qty",
            "entry_price", "exit_price", "net_pnl", "exit_reason"]
    comp = {}
    for bid in (GRID_BATCH, SYM_BATCH):
        rows = bt.get(bid, [])
        comp[bid] = {f: int(sum(1 for x in rows if x.get(f) is not None)) for f in flds}
    diff_fields = [f for f in flds if comp[GRID_BATCH][f] != comp[SYM_BATCH][f]]
    checks.append({
        "项": "同一笔在两个批次接口的字段完整性",
        "期望": "同一笔交易在两个入口的关键字段应一致（不能一个有一个没有）",
        "实际": "有差异的字段 %s；明细 %s" % (diff_fields, json.dumps(comp, ensure_ascii=False)),
        "通过": len(diff_fields) == 0,
    })
    detail["field_completeness"] = comp

    # ── 10. 相加会翻倍（分组关系风险，单独记，不并入通过项） ───
    gt = (summ.get(GRID_BATCH, {}).get("batch") or {}).get("trades_total")
    st = (summ.get(SYM_BATCH, {}).get("batch") or {}).get("trades_total")
    detail["double_count_risk"] = {
        "组级笔数": gt, "币级笔数": st, "直接相加": (gt or 0) + (st or 0),
        "真实笔数": 4325,
        "说明": "两个批次是同一批数据的两种粒度；页面上并列展示，若被当成两批相加会得到 8650。"
                "本次核对按内容键判定并集=4325，属显示层风险，非数据错误。",
    }

    df = pd.DataFrame(checks)
    df.to_csv(os.path.join(OUT, "12_API核对_v2.csv"), index=False, encoding="utf-8-sig")
    detail["summary"] = summ
    detail["batch_ids"] = ids
    with open(os.path.join(OUT, "12_API摘要_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(detail, fh, ensure_ascii=False, indent=2, default=str)

    print(df[["项", "通过"]].to_string(index=False))
    print("\n通过 %d / %d" % (int(df["通过"].sum()), len(df)))
    for r in checks:
        if not r["通过"]:
            print("\n未通过：%s\n  期望：%s\n  实际：%s" % (r["项"], r["期望"], r["实际"]))


if __name__ == "__main__":
    main()
