# -*- coding: utf-8 -*-
"""02 A 关（配置关）独立验收。

工单 5 / A 关三条：
  A1 必需配置缺失时拒绝新单，无静默兜底；
  A2 参数身份贯穿挂单、订单、持仓和重启；
  A3 客户端不能覆盖冻结参数。

做法：只读业务代码，用引擎自己的入口 `open_position` 在**五种配置场景**下各登记一次，
看它是「拒绝」还是「静默按兜底开仓」；再静态核对四层账本的列，确认身份到底贯穿到哪一层。
不调用任何推进方法、不写账本（登记本身由引擎完成）。

产物：out/02_A关配置.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sqlite3
import sys

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-FINAL-AUDIT-01"
OUT = HERE / "out"
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_final01")
PAPER = ROOT / "paper"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"),
    "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(MARKET),
    "PAPER_GATE_FILE": str(GATE),
    "PAPER_LOCK_DIR": str(ROOT / "locks"),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
}
os.environ.update(ENV)
# 运行期改 os.environ 的 PYTHONPATH 不会生效，必须显式插 sys.path
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EXP1 = BASE / "evidence/SYSTEM-CLOSELOOP-05/config/exp1_config.json"
EXP2 = BASE / "evidence/SYSTEM-CLOSELOOP-05/config/exp2_config.json"
SYM = "BTCUSDT"


def view_missing() -> pathlib.Path:
    return ROOT / "view_missing.json"


def view_no_stops() -> pathlib.Path:
    p = ROOT / "view_no_stops.json"
    p.write_text(json.dumps({
        "schema_version": "audit",
        "execution": {"signal_execution_delay_minutes": 15, "signal_exit_delay_minutes": 15,
                      "detail_timeframe": "15m"},
        "risk": {"initial_equity_USDT": 5000.0, "risk_per_trade_equity_fraction": 0.005,
                 "max_positions": 10, "short_enabled": False,
                 "portfolio_initial_stop_risk_cap": 0.05, "max_holding_hours": 240},
        # 故意不给 stops 段
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def view_zero_s() -> pathlib.Path:
    p = ROOT / "view_zero_s.json"
    p.write_text(json.dumps({
        "schema_version": "audit",
        "execution": {"signal_execution_delay_minutes": 15, "signal_exit_delay_minutes": 15,
                      "detail_timeframe": "15m"},
        "stops": {"rule": "FT-LOW4H", "stoploss_s": 0, "trail_d": 0.05,
                  "max_holding_hours": 240, "trailing_enabled": True},
        "risk": {"initial_equity_USDT": 5000.0, "risk_per_trade_equity_fraction": 0.005,
                 "max_positions": 10, "short_enabled": False,
                 "portfolio_initial_stop_risk_cap": 0.05, "max_holding_hours": 240},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def pconn(arm: str = "3a") -> sqlite3.Connection:
    p = PAPER / f"paper_{arm}.sqlite3"
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


def rows(con, sql, args=()):
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except sqlite3.Error as exc:                                  # 出错返回空并留痕，绝不占位
        return [{"_错误": str(exc)}]


def main() -> None:
    import live_store as L
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()
    import paper_engine as PE

    res: dict = {"生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "环境": ENV, "场景": [], "四层身份列": {}, "判定": {}}

    def run_case(name: str, view: pathlib.Path | None, uid: str, expect: str) -> dict:
        if view is None:
            os.environ.pop("EFFECTIVE_VIEW", None)
        else:
            os.environ["EFFECTIVE_VIEW"] = str(view)
        try:
            p = PE.approved_params()
        except Exception as exc:                                  # noqa: BLE001
            return {"场景": name, "approved_params": f"异常: {exc}", "结论": "拒绝(参数加载异常)"}
        rec = {"场景": name, "EFFECTIVE_VIEW": (str(view) if view else "(未设，用工程内默认路径)"),
               "视图存在": bool(view and view.exists()),
               "approved_params": {"source": p.get("source") or p.get("_source"),
                                   "stoploss_s": p.get("stoploss_s"), "trail_d": p.get("trail_d"),
                                   "max_holding_hours": p.get("max_holding_hours"),
                                   "portfolio_initial_stop_risk_cap": p.get("portfolio_initial_stop_risk_cap"),
                                   "model_version": p.get("model_version"),
                                   "view_sha256": (p.get("view_sha256") or "")[:12] or None}}
        try:
            r = PE.open_position("3a", SYM, signal_uid=uid, direction="long", atr=1.0,
                                 now_ms=L.now_ms(), event_time=L.now_ms(), available_at=L.now_ms(),
                                 requested_at=L.now_ms(), origin="replay")
            rec["open_position"] = r
            rec["结论"] = "静默接受(登记挂单)" if r.get("pending_id") else f"未登记: {r.get('reason')}"
        except Exception as exc:                                  # noqa: BLE001
            rec["open_position"] = f"抛异常: {type(exc).__name__}: {exc}"
            rec["结论"] = "拒绝(显式报错)"
        rec["期望"] = expect
        con = pconn()
        rec["挂单落库"] = rows(con, "SELECT pending_id,symbol,signal_uid,status,stoploss_s,trail_d,"
                                    "model_version,config_fingerprint FROM paper_pending "
                                    "WHERE signal_uid=?", (uid,))
        con.close()
        return rec

    # 场景 1：视图文件不存在（= 必需配置缺失）
    res["场景"].append(run_case("S1 视图文件不存在（必需配置缺失）", view_missing(),
                                "fin_a_s1", "应拒绝新单"))
    # 场景 2：视图存在但缺 stops 段
    res["场景"].append(run_case("S2 视图存在但缺 stops 段", view_no_stops(), "fin_a_s2", "应拒绝新单"))
    # 场景 3：视图 stops.stoploss_s = 0（显式非法）
    res["场景"].append(run_case("S3 stops.stoploss_s=0（显式非法）", view_zero_s(), "fin_a_s3", "应拒绝新单"))
    # 场景 4：完整配置 exp1
    res["场景"].append(run_case("S4 完整配置 exp1(0.05/0.05)", EXP1, "fin_a_s4", "应接受"))
    # 场景 5：完整配置 exp2
    res["场景"].append(run_case("S5 完整配置 exp2(0.03/0.08)", EXP2, "fin_a_s5", "应接受"))

    # A2：四层身份列（挂单/订单/成交/持仓）
    con = pconn()
    for t in ("paper_pending", "paper_order", "paper_fill", "paper_position", "paper_stop_history"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        res["四层身份列"][t] = {
            "列": cols,
            "有stoploss_s": "stoploss_s" in cols,
            "有trail_d": "trail_d" in cols,
            "有config_fingerprint": "config_fingerprint" in cols,
        }
    con.close()

    # 判定
    s1 = res["场景"][0]
    s2 = res["场景"][1]
    s3 = res["场景"][2]
    s4 = res["场景"][3]
    s5 = res["场景"][4]
    res["判定"]["A1_缺配置拒绝新单"] = {
        "S1 视图不存在": "通过" if s1["结论"].startswith("拒绝") else "不通过",
        "S2 缺 stops 段": "通过" if s2["结论"].startswith("拒绝") else "不通过",
        "S3 s=0 显式非法": "通过" if s3["结论"].startswith("拒绝") else "不通过",
        "结论": ("通过" if all(x["结论"].startswith("拒绝") for x in (s1, s2, s3)) else "不通过"),
    }
    res["判定"]["A1_完整配置应接受"] = {
        "exp1": s4["结论"], "exp2": s5["结论"],
        "结论": "通过" if s4.get("pending_id") or "静默接受" in s4["结论"] else "不通过",
    }
    res["判定"]["A2_身份贯穿"] = {
        "挂单": res["四层身份列"]["paper_pending"]["有stoploss_s"],
        "订单": res["四层身份列"]["paper_order"]["有stoploss_s"],
        "持仓": res["四层身份列"]["paper_position"]["有stoploss_s"],
        "结论": "通过" if all(res["四层身份列"][t]["有stoploss_s"] for t in
                          ("paper_pending", "paper_order", "paper_position")) else "不通过(订单层缺身份)",
    }

    (OUT / "02_A关配置.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[A关] {res['生成时刻']}")
    for c in res["场景"]:
        print(f"  {c['场景']}")
        print(f"     source={c['approved_params'].get('source')} s={c['approved_params'].get('stoploss_s')} "
              f"d={c['approved_params'].get('trail_d')} hold={c['approved_params'].get('max_holding_hours')} "
              f"cap={c['approved_params'].get('portfolio_initial_stop_risk_cap')}")
        print(f"     结论={c['结论']}  期望={c['期望']}")
        print(f"     挂单={json.dumps(c['挂单落库'], ensure_ascii=False)[:200]}")
    print("  四层身份列：")
    for t, v in res["四层身份列"].items():
        print(f"     {t:20} s={v['有stoploss_s']} d={v['有trail_d']} fp={v['有config_fingerprint']}")
    print("  判定：")
    for k, v in res["判定"].items():
        print(f"     {k}: {json.dumps(v, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
