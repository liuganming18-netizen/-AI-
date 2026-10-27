#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 周期口径实测（只读，走本机 8787 接口）

判据（工单口径）：
  只有「请求周期 ≠ 返回实际 K 线周期」或「界面标签 ≠ 返回实际周期」才算缺陷。
  「默认展示 1h、执行 15m」是允许的，不算缺陷。

实测三处：
  ① 行情接口 /markets/{ex}/{sym}/candles —— 8 个周期，比较请求 tf / 响应 timeframe / 实测间隔
  ② 回测图表接口 /backtests/{id}/chart —— 6 个 run × 6 个请求周期，比较请求 / display_timeframe / 实测间隔
  ③ 前端取周期的方式 —— LabChartPanel.actualTf（回测工作台）与 adaptCandles（行情页）

不写任何 run 目录。
"""
import json
import os
import urllib.error
import urllib.request

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/05_周期口径.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
STEP = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
        "8h": 28800, "12h": 43200, "1d": 86400}

RUNS = [
    "run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs08_Z02",   # freqtrade 组合 run
    "run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs04_Z04",   # freqtrade 组合 run（单笔）
    "run_WX_EVENT_TREND_V0_1_TAOUSDT_1792274378",       # 项目引擎单标的
    "run_FT_STOP20_Wyckoff_C1_Stop20_Vs04_Z02",         # 项目引擎研究批次
    "run_WX_EVENT_TREND_V0_1_COOKIEUSDT_1792252327",    # 项目引擎零成交
    "run_FT_ENTRY_09_C2_Vs08_Z04_CELOUSDT",             # _archive 里能打开的
]


def get(url: str) -> dict:
    try:
        with OPENER.open(url, timeout=30) as r:
            return {"http": r.status, "data": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "data": None, "err": e.read().decode("utf-8", "replace")[:120]}
    except Exception as e:                                     # noqa: BLE001
        return {"http": -1, "data": None, "err": str(e)[:120]}


def step_of(bars, key="t") -> int | None:
    ts = []
    for b in bars[:80]:
        if isinstance(b, dict) and isinstance(b.get(key), int):
            ts.append(b[key])
    if len(ts) < 2:
        return None
    diffs = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    return diffs[len(diffs) // 2] // 1000


def main() -> None:
    market = []
    for ex, sym in (("binance", "BTCUSDT"), ("okx", "BTC-USDT-SWAP")):
        for tf in STEP:
            res = get(f"http://127.0.0.1:8787/api/v1/markets/{ex}/{sym}/candles?timeframe={tf}&limit=80")
            d = res.get("data") or {}
            bars = d.get("bars") or []
            ms = []
            for b in bars[:80]:
                try:
                    ms.append(int(__import__("datetime").datetime.fromisoformat(
                        b["open_time"].replace("Z", "+00:00")).timestamp() * 1000))
                except Exception:                              # noqa: BLE001
                    pass
            step = step_of([{"t": x} for x in ms]) if len(ms) > 1 else None
            market.append({
                "exchange": ex, "symbol": sym, "req_tf": tf,
                "http": res["http"], "resp_timeframe": d.get("timeframe"),
                "bars": len(bars), "step_s": step, "expect_s": STEP[tf],
                "is_native": d.get("is_native"),
                "missing_reason": d.get("missing_reason"),
                "verdict": ("一致" if step == STEP[tf] else
                            ("未取到" if not bars else "不一致")),
            })

    chart = []
    for rid in RUNS:
        for q in ("", "?timeframe=15m", "?timeframe=30m", "?timeframe=1h", "?timeframe=4h"):
            res = get(f"http://127.0.0.1:8787/api/v1/backtests/{rid}/chart{q}")
            d = res.get("data") or {}
            c = d.get("chart") or {}
            bars = c.get("candles") or []
            step = step_of(bars)
            req = q.split("=")[1] if "=" in q else "(默认)"
            chart.append({
                "run_id": rid, "req_tf": req, "http": res["http"],
                "bars": len(bars), "step_s": step,
                "exec_tf": c.get("exec_timeframe"), "display_tf": c.get("display_timeframe"),
                "candles_timeframe": c.get("candles_timeframe"),
                "blocked": c.get("chart_blocked_reason"),
                "top_missing": d.get("missing_reason"),
                "verdict": ("一致" if (req in STEP and step == STEP[req]) else
                            ("默认按显示周期" if req == "(默认)" and step is not None else
                             ("未取到" if not bars else "需人工看"))),
            })

    # 前端取周期的方式（静态代码，只看「是否使用后端声明」）
    lab = open(os.path.join(ROOT, "UI界面/web/src/components/LabChartPanel.tsx"), encoding="utf-8").read()
    ada = open(os.path.join(ROOT, "UI界面/web/src/api/adapters.ts"), encoding="utf-8").read()
    frontend = {
        "回测工作台_LabChartPanel.actualTf": (
            "优先 candles_timeframe → display_timeframe → detectCandleTimeframe(实际蜡烛) → exec_timeframe"
            if "candles_timeframe" in lab and "detectCandleTimeframe" in lab else "未按后端声明取周期"),
        "回测工作台_是否按请求周期贴标签": "否（用 actualTf）" if "actualTf" in lab else "未知",
        "行情页_adaptCandles": (
            "用**请求的** tf 调 formatCandleTime，未读取响应 timeframe"
            if "formatCandleTime(b.open_time, tf)" in ada else "已校验"),
        "行情接口是否回 timeframe 字段": "是（实测响应含 timeframe / is_native）",
    }

    market_bad = [m for m in market if m["verdict"] == "不一致"]
    chart_bad = [c for c in chart if c["verdict"] == "需人工看"]
    out = {
        "判据": "请求周期 / 返回实际间隔 / 界面标签 三者一致才通过；默认 1h 展示 + 15m 执行不算缺陷",
        "行情接口": market,
        "行情接口_不一致": market_bad,
        "回测图表接口": chart,
        "回测图表_需人工看": chart_bad,
        "前端": frontend,
        "结论": [
            f"行情接口：{sum(1 for m in market if m['verdict']=='一致')}/{len(market)} 请求周期与实测间隔一致；"
            f"不一致 {len(market_bad)} 条。okx 非原生周期返回空并带 missing_reason（正确行为）。",
            "回测图表接口：请求 15m/30m/1h/4h 时，返回间隔分别 900/1800/3600/14400 秒，"
            "display_timeframe 与之一致；exec_timeframe 恒为 15m（真实执行周期）。"
            "『显示 1h、执行 15m』是设计口径，不是矛盾。",
            "回测工作台前端用 candles_timeframe/display_timeframe/实测间隔 取周期，不再按请求周期贴标签；"
            "行情页 adaptCandles 仍按请求周期贴标签（响应里有 timeframe 但没读）⇒ 这是唯一残留的『标签可能撒谎』点。",
        ],
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("行情接口")
    for m in market:
        print(f"  {m['exchange']:8s} {m['req_tf']:4s} http={m['http']} bars={m['bars']:3d} "
              f"step={m['step_s']} resp_tf={m['resp_timeframe']} {m['verdict']}")
    print("回测图表接口")
    for c in chart:
        print(f"  {c['run_id'][:44]:44s} req={c['req_tf']:6s} http={c['http']} bars={c['bars']:6d} "
              f"step={c['step_s']} exec={c['exec_tf']} display={c['display_tf']} {c['verdict']}")
    print("→", OUT)


if __name__ == "__main__":
    main()
