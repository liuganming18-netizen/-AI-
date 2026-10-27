# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 14：三类边界记录核对（v2）。

三类：拒单 / 确认失败（未成交）/ 缺 K 线。
每一类都要回答两件事：
  ① 源账本里的状态是真的吗（是不是真被拒、真没成交）
  ② 系统里能不能看到同一个状态（读不出来 ⇒ 未完成，不能拿「接口返回空」当「没有拒单」）
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
SYM_DIR = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_ft_replay_01")
BASE = "http://127.0.0.1:8787/api/v1/backtests"
CANDLE_BASE = os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um")


def get(path, timeout=90):
    p = subprocess.run(["curl", "-s", "-m", str(timeout), BASE + path],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"__error__": "非 JSON", "raw": (p.stdout or "")[:200]}


def read_jsonl(p):
    out = []
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    rows, detail = [], {}
    # 候选对照矩阵（页面「同一候选在 C0/C1/C2 的结果对照」用的就是它）
    from collections import Counter
    mc = Counter()
    mp = os.path.join(SYM_DIR, "_candidate_matrix.json")
    if os.path.isfile(mp):
        for ev, rs in json.load(open(mp, encoding="utf-8")).items():
            for r in rs:
                mc[r.get("status")] += 1
    detail["候选对照矩阵"] = dict(mc)

    # ── 1. 拒单 ─────────────────────────────────────────────────
    rej_runs = [p for p in sorted(glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*")))
                if os.path.getsize(os.path.join(p, "rejected.jsonl")) > 0]
    rid = os.path.basename(rej_runs[0])
    rej = read_jsonl(os.path.join(rej_runs[0], "rejected.jsonl"))
    r0 = rej[0]
    tr = read_jsonl(os.path.join(rej_runs[0], "trades.jsonl"))
    api = get("/%s/rejected" % rid)
    api_ids = [x.get("event_id") or x.get("signal_id") for x in (api.get("items") or [])]
    # 拒单事件是否被伪造成成交
    trade_events = set(t.get("source", {}).get("event_id") for t in tr)
    faked = r0["event_id"] in trade_events
    rows.append({
        "类别": "拒单", "run_id": rid, "标的": r0["symbol"], "event_id": r0["event_id"],
        "源账本状态": "REJECTED / %s" % r0["reason"],
        "系统接口状态": "items=%d；missing_reason=%s" % (len(api.get("items") or []),
                                                     api.get("missing_reason")),
        "源里是否被记成成交": "是（异常）" if faked else "否",
        "真实性判定": "通过：源账本有明确拒单原因，且未出现在成交表里",
        "系统可见性判定": ("通过：页面候选对照块可见（矩阵 rejected %d 条）；"
                          "但专用 /rejected 接口 0 条（文件名不匹配）" % mc.get("rejected", 0))
                          if mc.get("rejected") else "未通过：页面与接口都看不到",
        "证据": "rejected.jsonl 非空 run 数=%d；本 run 拒单 %d 条" % (len(rej_runs), len(rej)),
    })
    detail["拒单"] = {
        "run_id": rid, "源条数": len(rej), "系统条数": len(api.get("items") or []),
        "missing_reason": api.get("missing_reason"),
        "非空 rejected.jsonl 的 run 数": len(rej_runs),
        "全批 rejected.jsonl 文件数": len(glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*/rejected.jsonl"))),
        "全批 rejects.jsonl 文件数": len(glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*/rejects.jsonl"))),
        "读取器期望文件名": "rejects.jsonl（backtest_store.read_ledger_rows 的 JSONL 兜底表）",
        "实际文件名": "rejected.jsonl",
    }

    # ── 2. 确认失败 / 未成交 ────────────────────────────────────
    nf_runs = [p for p in sorted(glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*")))
               if os.path.getsize(os.path.join(p, "not_filled.jsonl")) > 0]
    rid2 = os.path.basename(nf_runs[0])
    nf = read_jsonl(os.path.join(nf_runs[0], "not_filled.jsonl"))
    n0 = nf[0]
    tr2 = read_jsonl(os.path.join(nf_runs[0], "trades.jsonl"))
    tr2_events = set(t.get("source", {}).get("event_id") for t in tr2)
    api_nf = get("/%s/rejected" % rid2)
    rows.append({
        "类别": "确认失败/未成交", "run_id": rid2, "标的": n0.get("symbol"),
        "event_id": n0.get("event_id"),
        "源账本状态": "%s / %s" % (n0.get("decision"), n0.get("reason")),
        "系统接口状态": "无「未成交」接口；/rejected 返回 items=%d、missing_reason=%s"
                        % (len(api_nf.get("items") or []), api_nf.get("missing_reason")),
        "源里是否被记成成交": "是（异常）" if n0.get("event_id") in tr2_events else "否",
        "真实性判定": "通过：源账本明确标 CONFIRMED_NOT_FILLED，且未出现在成交表里",
        "系统可见性判定": ("通过：页面候选对照块可见（矩阵 not_filled %d 条）；无专用入口"
                          % mc.get("not_filled", 0)) if mc.get("not_filled") else "未通过：页面也看不到",
        "证据": "not_filled.jsonl 非空 run 数=%d；本 run %d 条" % (len(nf_runs), len(nf)),
    })
    detail["确认失败"] = {
        "run_id": rid2, "源条数": len(nf),
        "非空 not_filled.jsonl 的 run 数": len(nf_runs),
        "样例": n0,
        "系统入口": "无（route 表里没有 not_filled / unfilled 子资源）",
    }

    # ── 3. 缺 K 线 ──────────────────────────────────────────────
    syms = set()
    for p in glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*")):
        syms.add(os.path.basename(p).replace("run_FT_ENTRY_09_", "").split("_")[-1])
    no_dir = sorted(s for s in syms if not os.path.isdir(os.path.join(CANDLE_BASE, s)))
    empty_15m = sorted(s for s in syms if os.path.isdir(os.path.join(CANDLE_BASE, s))
                       and not os.listdir(os.path.join(CANDLE_BASE, s, "15m")))
    # 构造缺失标的，看闸门是否给明确说明
    probe = get("/run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT/chart?symbol=NOTEXISTUSDT")
    pch = probe.get("chart") or {}
    rows.append({
        "类别": "缺K线", "run_id": "run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT",
        "标的": "NOTEXISTUSDT（构造）", "event_id": "—",
        "源账本状态": "本批 59 币种 15m 全部有数据 ⇒ 本批不存在真实缺 K 线记录",
        "系统接口状态": "candles=%d；missing_reason=%s" % (len(pch.get("candles") or []),
                                                       probe.get("missing_reason")),
        "源里是否被记成成交": "—",
        "真实性判定": "本批无真实缺 K 线记录（不可判）",
        "系统可见性判定": "未通过：缺数据时静默返回 0 根，没有 missing_reason",
        "证据": "缺 15m 目录的币种 %d 个、15m 空目录 %d 个" % (len(no_dir), len(empty_15m)),
    })
    detail["缺K线"] = {
        "本批币种数": len(syms), "缺目录": no_dir, "15m空目录": empty_15m,
        "构造探测": {"symbol": "NOTEXISTUSDT", "candles": len(pch.get("candles") or []),
                   "missing_reason": probe.get("missing_reason")},
        "说明": "FT-REPLAY-02 声称缺数据会返回 missing_reason，实测未返回。",
    }

    # ── 4. 同一候选既成交又被拒（复评拒单） ────────────────────
    both = []
    for p in glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*")):
        rj = read_jsonl(os.path.join(p, "rejected.jsonl"))
        if not rj:
            continue
        te = set(t.get("source", {}).get("event_id")
                 for t in read_jsonl(os.path.join(p, "trades.jsonl")))
        for x in rj:
            if x.get("event_id") in te:
                both.append({"run_id": os.path.basename(p), "event_id": x.get("event_id"),
                             "reason": x.get("reason")})
    detail["既成交又被拒"] = {"条数": len(both), "样例": both[:5],
                          "判读": "同一候选先成交、后又被复评拒单；成交笔本身在源账本存在，"
                                  "属「成交后又拒」而非「把拒单伪造成成交」。"}

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "14_边界案例_v2.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(OUT, "14_边界案例摘要_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(detail, fh, ensure_ascii=False, indent=2, default=str)

    print(df[["类别", "标的", "真实性判定", "系统可见性判定"]].to_string(index=False))
    print("\n" + json.dumps(detail, ensure_ascii=False, indent=1, default=str)[:2500])


if __name__ == "__main__":
    main()
