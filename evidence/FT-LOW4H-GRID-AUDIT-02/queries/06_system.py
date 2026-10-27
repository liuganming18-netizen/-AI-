# -*- coding: utf-8 -*-
"""06 系统展示核对：API 能证明什么、不能证明什么。

原则：API 成功 ≠ 页面通过。本脚本只把"能用接口复核的部分"钉死，
页面渲染一律标"页面未验收"，除非真的看到渲染结果。
"""
from __future__ import annotations

import json
import urllib.request

import pandas as pd

import _lib as L

BASE = "http://127.0.0.1:8787"
BATCH = "runs_ft_low4h_grid_01_research"
RES: dict = {"段": {}}


def add(sec, key, val, ok=None, note=""):
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES.setdefault("判据", []).append({"段": sec, "项": key, "通过": bool(ok), "说明": note})


def get(path: str, timeout: int = 10):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"_error": str(e)}


def main() -> None:
    tr, _, _ = L.load_all()
    df = L.enrich(tr)
    my_trades = len(df)
    my_pnl = float(df["profit_abs"].sum())

    s = get(f"/api/v1/backtests/batches/{BATCH}/summary?refresh=1")
    if "_error" in s:
        add("接口", "批次汇总接口", s, False, "接口不可达")
        json.dump(RES, open(L.OUT / "06_系统核对.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2, default=str)
        return

    b = s["batch"]
    add("接口", "批次 runs_total", b.get("runs_total"), b.get("runs_total") == 144)
    add("接口", "批次 trades.count", s["trades"]["count"], s["trades"]["count"] == my_trades,
        f"我方逐笔 {my_trades}")
    add("接口", "批次 net_pnl_sum（由逐笔行现算）", s["trades"]["net_pnl_sum"],
        abs(s["trades"]["net_pnl_sum"] - my_pnl) < 0.01, f"我方 {my_pnl:.4f}")
    add("接口", "批次胜率", s["trades"]["win_rate_pct"])

    # 缺陷 1：逐 run 的 summary 字段没被读进批次
    add("缺陷", "runs_with_trades", b.get("runs_with_trades"), b.get("runs_with_trades") == 0,
        "批次汇总把每 run 的成交笔数读成 0")
    add("缺陷", "trades_total（批次汇总字段）", b.get("trades_total"), b.get("trades_total") == 0,
        "与同接口 trades.count=9,849 自相矛盾")
    add("缺陷", "net_pnl_sum（批次汇总字段）", b.get("net_pnl_sum"), b.get("net_pnl_sum") is None,
        "每 run 的 net_pnl 未被读取，批次层面为 null")
    add("缺陷", "checks.trades_total_matches_rows", s.get("checks", {}).get("trades_total_matches_rows"),
        s.get("checks", {}).get("trades_total_matches_rows") is False,
        "系统自己的校验字段就是 false，说明它知道对不上")

    # 缺陷 2：逐 run summary 的手续费把费率当金额
    run_dir = L.BASE_DIR / f"回测日志/event_trend_v0_1/{BATCH}"
    f0 = run_dir / "run_FT_LOW4H_Wyckoff_C1_S05_D05_Vs04_Z02/summary.json"
    if f0.exists():
        js = json.load(open(f0, encoding="utf-8"))
        g = df[df["策略"].eq("Wyckoff_C1_S05_D05_Vs04_Z02")]
        add("缺陷", "缺陷确认：单 run summary 的 total_fees 把费率当金额", js.get("total_fees"),
            abs(float(js.get("total_fees", 0)) - g["手续费USDT"].sum()) > 1e-6,
            f"该组真实手续费 {g['手续费USDT'].sum():.4f} USDT；"
                   f"summary 写 {js.get('total_fees')} = 21 笔 × 0.001（费率被当金额）")
        add("缺陷", "缺陷确认：单 run summary 的 total_funding 恒为 0", js.get("total_funding"),
            abs(float(js.get("total_funding", 0)) - float(g["资金费USDT"].sum())) > 1e-6,
            f"该组真实资金费 {g['资金费USDT'].sum():.4f}")
        add("缺陷", "单 run summary 的 trades_total", js.get("trades_total"),
            js.get("trades_total") == len(g), f"该组真实 {len(g)} 笔（summary 自身是对的，是接口没读）")

    # 抽核：系统逐笔 vs 源账本
    t = get(f"/api/v1/backtests/batches/{BATCH}/trades?limit=500")
    items = t.get("items", [])
    add("逐笔抽核", "接口返回条数", len(items))
    bad = []
    if items:
        dd = df.copy()
        dd["symbol"] = dd["pair"].str.split("/").str[0] + dd["pair"].str.split("/").str[1].str.split(":").str[0]
        m = dd.set_index(["策略", "open_ms", "symbol"])
        for it in items[:500]:
            run = it["run_id"].replace("run_FT_LOW4H_", "")
            try:
                row = m.loc[(run, int(it["entry_time_ms"]), it["symbol"])]
            except KeyError:
                bad.append({"run": run, "entry_ms": it["entry_time_ms"], "问题": "源账本无此入场"})
                continue
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            if abs(float(it["entry_price"]) - float(row["open_rate"])) > 1e-9:
                bad.append({"run": run, "entry_ms": it["entry_time_ms"], "问题": "入场价不符",
                            "系统": it["entry_price"], "账本": float(row["open_rate"])})
            if int(it["exit_time_ms"]) != int(row["close_ms"]):
                bad.append({"run": run, "entry_ms": it["entry_time_ms"], "问题": "退出时间不符"})
            if abs(float(it["exit_price"]) - float(row["close_rate"])) > 1e-9:
                bad.append({"run": run, "entry_ms": it["entry_time_ms"], "问题": "退出价不符",
                            "系统": it["exit_price"], "账本": float(row["close_rate"])})
            if abs(float(it["net_pnl"]) - float(row["profit_abs"])) > 1e-3:
                bad.append({"run": run, "entry_ms": it["entry_time_ms"], "问题": "净盈亏不符",
                            "系统": it["net_pnl"], "账本": float(row["profit_abs"])})
    add("逐笔抽核", "与源账本不一致条数", len(bad), len(bad) == 0, f"抽核 {len(items)} 条")
    if bad:
        add("逐笔抽核", "不一致样本", bad[:10])

    # 页面能否展示止损轨迹
    if items:
        it = items[0]
        add("页面可展示性", "缺陷确认：initial_stop 为空", it.get("initial_stop"),
            it.get("initial_stop") is None, "系统逐笔记录里初始止损为空")
        add("页面可展示性", "缺陷确认：stop_trace_points 为 0", it.get("stop_trace_points"),
            (it.get("stop_trace_points") or 0) == 0, "止损上移轨迹点数为 0 ⇒ 页面无法展示止损上移过程")
        add("页面可展示性", "qty / fees / funding_fee",
            [it.get("qty"), it.get("fees"), it.get("funding_fee")], None,
            "三项均为空，页面上看不到数量与费用拆分")
        add("页面可展示性", "mae / mfe", [it.get("mae"), it.get("mfe")], None, "均为空")

    add("页面验收", "结论", "页面未验收",
        None, "本机未安装浏览器自动化，无法渲染 3000 端口页面；仅完成接口层核对，"
              "按工单要求不得把'接口成功'当作'页面通过'")

    crit = RES.get("判据", [])
    RES["判据统计"] = {"总": len(crit), "通过": sum(1 for c in crit if c["通过"]),
                       "不通过": sum(1 for c in crit if not c["通过"]),
                       "无判据": sum(1 for c in crit if c["通过"] is None)}
    json.dump(RES, open(L.OUT / "06_系统核对.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, default=str)
    print("判据:", RES["判据统计"])
    for c in crit:
        print(("✓" if c["通过"] else "✗"), c["段"], c["项"], "|", c["说明"][:70])


if __name__ == "__main__":
    main()
