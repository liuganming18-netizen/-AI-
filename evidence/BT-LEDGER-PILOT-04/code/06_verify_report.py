# -*- coding: utf-8 -*-
"""BT-LEDGER-PILOT-04｜报告数字机械复算。

项目纪律：报告里每个关键数字都必须能回到 `out/*.json` / `out/*.csv` 的原始字段。
本脚本把 `00_报告_BT-LEDGER-PILOT-04.md` 里引用的数字**逐条重算并断言**，
任何一条对不上就 FAIL —— 防止人工抄写出错。

产出：out/15_报告数字复算.json
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ME = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/BT-LEDGER-PILOT-04")
OUT = ME / "out"
RAW = OUT / "00_engine_raw"

checks: list[dict] = []


def ck(name: str, got, expect, tol: float | None = None) -> None:
    if tol is not None:
        ok = abs(float(got) - float(expect)) <= tol
    else:
        ok = got == expect
    checks.append({"item": name, "got": got, "expect": expect,
                   "tol": tol, "ok": bool(ok)})


def main() -> None:
    meta = json.loads((RAW / "run_meta.json").read_text(encoding="utf-8"))
    t03 = json.loads((OUT / "03_引擎对象真值核查.json").read_text(encoding="utf-8"))
    t04 = json.loads((OUT / "04_引擎计数器对照.json").read_text(encoding="utf-8"))
    t07 = json.loads((OUT / "07_对账结论.json").read_text(encoding="utf-8"))
    t11 = json.loads((OUT / "11_关联核对.json").read_text(encoding="utf-8"))
    t12 = json.loads((RAW / "12_上游追加写入_取证与还原.json").read_text(encoding="utf-8"))
    t14 = json.loads((OUT / "14_上游不可变快照_内容判据.json").read_text(encoding="utf-8"))

    o8 = pd.read_csv(OUT / "08_订单_规范导出.csv")
    o9 = pd.read_csv(OUT / "09_成交_规范导出.csv")
    o10 = pd.read_csv(OUT / "10_交易汇总.csv")
    o06 = pd.read_csv(OUT / "06_K线止损切档对应.csv")
    o01 = pd.read_csv(OUT / "01_新旧行为对照.csv")
    o02 = pd.read_csv(OUT / "02_订单七字段对照.csv")
    o05 = pd.read_csv(OUT / "05_统计块对照.csv")

    # ── §1 身份与耗时 ──
    ck("strategy_sha256", meta["pre"]["strategy_sha256"],
       "4eb070b7892ac9008bc446b627f5b0fd5a41c141848a30a39158f37dfd9c8d2e")
    ck("base_sha256", meta["pre"]["base_sha256"],
       "d5467a2e413ddb29a34774fecdfcce08fc04b07d58de4504ce468e7b50b03608")
    ck("config_sha256", meta["pre"]["config_sha256"],
       "88c233f1dfa4676cee34c1453b86bff21d1521058ccdfe4053d0874eda2bc3b3")
    ck("old_zip_sha256", meta["pre"]["old_zip_sha256"],
       "407005948a0f394896a47bb569cb4266ef31ef05a34dcea63c7884b8af51327c")
    a = datetime.fromisoformat(meta["pre"]["started_at"].replace("Z", "+00:00"))
    b = datetime.fromisoformat(meta["post"]["finished_at"].replace("Z", "+00:00"))
    ck("elapsed_seconds", (b - a).total_seconds(), 161.0)
    ck("identity_unchanged_after", [meta["post"][k] for k in
       ("strategy_file_unchanged", "base_file_unchanged", "config_file_unchanged")],
       [True, True, True])

    # ── §2 规模 ──
    ck("trades", len(o10), 21)
    ck("orders", len(o8), 42)
    ck("pairs", o10["pair"].nunique(), 12)
    ck("order_columns_engine", len(t03["order_identity"]["order_columns_in_model"]), 26)
    ck("export08_shape", list(o8.shape), [42, 43])
    ck("export09_shape", list(o9.shape), [42, 17])
    ck("export10_shape", list(o10.shape), [21, 33])
    ck("internal_order_key_unique", int(o8["internal_order_key"].nunique()), 42)
    ck("order_id_kind_all_engine_counter",
       sorted(o8["order_id_kind"].unique().tolist()), ["engine_internal_counter_string"])
    ck("exchange_order_id_all_null", int(o8["exchange_order_id"].notna().sum()), 0)
    ck("fill_id_all_null", int(o8["fill_id"].notna().sum()), 0)
    ck("fill_id_status_all_nonexistent",
       sorted(o8["fill_id_status"].unique().tolist()), ["nonexistent_in_backtest"])

    # ── §2.4 引擎真值 13 组断言 ──
    sf = t03["status_filled"]
    ck("status_closed", sf["status_dist"], {"closed": 42})
    ck("is_open_false", sf["is_open_dist"], {"False": 42})
    ck("order_type_limit", sf["order_type_dist"], {"limit": 42})
    ck("filled_eq_amount", sf["filled_eq_amount"], 42)
    ck("remaining_zero", sf["remaining_zero"], 42)
    ck("time_all_equal", t03["time_semantics"]["order_date_eq_filled_date_eq_update_date"], 42)
    ck("time_eq_filled_ts", t03["time_semantics"]["order_date_eq_order_filled_timestamp"], 42)
    ck("price_all_equal", t03["price_semantics"]["price_eq_average_eq_safe_price_eq_ft_price"], 42)
    ck("ft_fee_base_none", t03["fees"]["ft_fee_base_dist"], {"None": 42})
    ck("ft_cancel_reason_none", t03["fees"]["ft_cancel_reason_dist"], {"None": 42})
    ck("stop_price_none", t03["fees"]["stop_price_dist"], {"None": 42})
    ck("ft_trade_id_eq_engine", t03["trade_identity"]["ft_trade_id_equals_engine_trade_id"], 42)
    ck("engine_order_id_gapless", t03["order_identity"]["order_id_is_gapless_1_to_N"], True)
    ck("engine_trade_id_gapless", t03["trade_identity"]["engine_trade_id_is_gapless_1_to_N"], True)

    # ── §3 能力边界 ──
    cb = t03["capability_boundary"]
    ck("fill_id_like_columns", cb["fill_id_like_columns"], [])
    ck("has_fill_id", cb["has_fill_id"], False)
    ck("orders_per_trade_dist", cb["orders_per_trade_dist"], {"2": 21})
    ck("nr_entries_dist", cb["nr_of_successful_entries_dist"], {"1": 21})
    ck("nr_exits_dist", cb["nr_of_successful_exits_dist"], {"1": 21})
    ck("record_kind_all_order_level",
       sorted(o9["record_kind"].unique().tolist()), ["order_level_full_fill"])
    ck("is_partial_all_false", int((o9["is_partial"] == False).sum()), 42)  # noqa: E712
    ck("fill_seq_all_1", int((o9["fill_seq_in_order"] == 1).sum()), 42)

    # ── §3.2 资金费归属 ──
    fu = t03["funding"]
    ck("funding_by_leg", fu["by_leg"],
       {"entry_null": 21, "entry_zero": 0, "entry_nonzero": 0,
        "exit_null": 0, "exit_zero": 1, "exit_nonzero": 20})
    ck("funding_sum_eq_trade", fu["sum_per_trade_eq_trade_funding"], 21)
    ck("funding_total", round(float(o10["funding_fees"].sum()), 10), -1.0905316279, tol=1e-9)

    # ── §3.3 费用字段 ──
    ck("fee_open_cost_present", t03["fees"]["fee_open_cost_present"], 21)
    ck("fee_close_cost_present", t03["fees"]["fee_close_cost_present"], 0)
    ck("fee_route_a_maxdev", t11["fee_cross_check"]["route_a_order_cost_minus_notional_maxdev"],
       2.491062911502695e-14, tol=1e-20)
    ck("fee_two_routes_agree", t11["fee_cross_check"]["entry_fee_two_routes_agree"], 21)

    # ── §4 新旧行为对照 ──
    for f in ("open_rate", "close_rate", "amount", "profit_abs", "funding",
              "exit_reason", "enter_tag"):
        ck(f"trade_field_{f}_equal", int(o01[f"{f}_equal"].sum()), 21)
    for f in ("amount", "price", "cost", "side", "ts", "is_entry", "tag"):
        ck(f"order_field_{f}_equal", int(o02[f"{f}_equal"].sum()), 42)
    ck("stats_fields_compared", t07["stats_fields_compared"], 104)
    ck("stats_fields_unequal_raw", t07["stats_fields_unequal_raw"], 2)
    ck("stats_unequal_are_clock", sorted(t07["stats_fields_unequal_are_runtime_clock"]),
       ["backtest_run_end_ts", "backtest_run_start_ts"])
    ck("stats_behavior_fields_unequal", t07["stats_behavior_fields_unequal"], [])
    ck("stats_block_rows", len(o05), 104)
    ck("stats_expected_to_differ", int(o05["expected_to_differ"].sum()), 2)
    ck("stats_behavior_equal_all", int((o05["behavior_equal"] == False).sum()), 0)  # noqa: E712
    clock = o05[o05["expected_to_differ"] == True].set_index("field")  # noqa: E712
    ck("clock_diff_start", int(float(clock.loc["backtest_run_start_ts", "new"])
                              - float(clock.loc["backtest_run_start_ts", "old"])), 19132)
    ck("clock_diff_end", int(float(clock.loc["backtest_run_end_ts", "new"])
                            - float(clock.loc["backtest_run_end_ts", "old"])), 19028)
    ck("final_balance_abs_diff", t07["final_balance"]["abs_diff"], 0.0)
    ck("final_balance_value", t07["final_balance"]["new"], 1899.11520196)
    ck("engine_counters_all_equal", t04["all_equal"] if "all_equal" in t04 else
       all(v["equal"] for k, v in t04.items() if isinstance(v, dict) and "equal" in v), True)

    # ── §5 关联核对 ──
    ck("order_trade_link_ok", t11["order_trade_link"]["ok"], 21)
    ck("cost_formula_maxdev", t11["cost_formula_max_abs_dev"], 0.0)
    ck("pnl_rebuild_maxdev", t11["pnl_rebuild_max_abs_dev"], 4.965388100686141e-09, tol=1e-24)
    ck("pnl_ok", t11["pnl_ok"], 21)
    ck("causality_ok", t11["causality_ok"], 21)
    ck("kline_replay_found", t11["kline_link"]["replay_found"], 21)
    ck("kline_entry_ts_match", t11["kline_link"]["entry_ts_match"], 21)
    ck("kline_exit_ts_match", t11["kline_link"]["exit_ts_match"], 21)
    ck("kline_entry_price_match", t11["kline_link"]["entry_price_match"], 21)
    ck("kline_exit_price_match", t11["kline_link"]["exit_price_match"], 21)
    ck("kline_trade_id_match_engine", t11["kline_link"]["trade_id_match_engine"], 21)
    ck("stop_trace_rows_total", t11["kline_link"]["trace_rows_total"], 452)
    ck("stop_trace_rows_min", int(o06["stop_trace_rows"].min()), 2)
    ck("stop_trace_rows_max", int(o06["stop_trace_rows"].max()), 89)
    ck("stop_trace_has_entry_event_all", int(o06["stop_trace_has_entry_event"].sum()), 21)

    # ── §5.4 切档：必须全 0（这是「未验证」的证据，不是通过项）──
    ck("stage_events_all_zero", sorted(o06["stage_events_in_replay"].unique().tolist()), [0])
    ck("stage_events_total", int(o06["stage_events_in_replay"].sum()), 0)

    # ── §5.1 交易汇总分布 ──
    ck("exit_reason_dist", o10["exit_reason"].value_counts().to_dict(),
       {"trailing_stop_loss": 13, "stop_loss": 8})
    ck("profit_abs_sum", round(float(o10["profit_abs"].sum()), 8), -100.88479804, tol=1e-8)
    ck("is_open_all_false", int((o10["is_open"] == False).sum()), 21)  # noqa: E712

    # ── §6 上游事故 ──
    ck("incident_data_loss", t12["data_loss"], False)
    ck("incident_n_files", len(t12["records"]), 2)
    ck("incident_appended_confirmation",
       t12["records"][0]["appended_bytes"], 39675)
    ck("incident_appended_confirmation_lines", t12["records"][0]["appended_lines"], 173)
    ck("incident_appended_orders", t12["records"][1]["appended_bytes"], 6855)
    ck("incident_appended_orders_lines", t12["records"][1]["appended_lines"], 21)
    ck("incident_all_restored",
       [r["restored_equals_snapshot"] for r in t12["records"]], [True, True])
    ck("incident_all_prefix_ok",
       [r["prefix_equals_snapshot"] for r in t12["records"]], [True, True])

    # ── §6.4 上游内容判据 ──
    ck("upstream_C1", t14["criteria"]["C1_content_zero_drift"]["pass"], True)
    ck("upstream_C2", t14["criteria"]["C2_mtime_change_attributable"]["pass"], True)
    ck("upstream_C3", t14["criteria"]["C3_identity_locked"]["pass"], True)
    ck("upstream_total_files", sum(t["n_files_current"] for t in t14["trees"].values()), 1076)
    ck("upstream_mtime_after", sum(len(t["mtime_after_ticket_start"])
                                   for t in t14["trees"].values()), 2)
    ck("upstream_unattributed", sum(len(t["mtime_unattributed"])
                                    for t in t14["trees"].values()), 0)
    ck("upstream_verdict", t14["verdict"], "PASS")

    # ── 报告正文里是否真的写了「未验证」这类限定词（防止文字层面夸大）──
    md = (ME / "00_报告_BT-LEDGER-PILOT-04.md").read_text(encoding="utf-8")
    ck("report_says_stage_unverified", "切档链路本次未被验证" in md, True)
    ck("report_says_not_45", "单组通过 ≠ 45 组通过" in md, True)
    ck("report_says_fill_nonexistent", "nonexistent_backtest" in md, True)

    # 正向伪造断言：按行判定，含否定词的「说明行」不算（否则本报告自己写的禁止说明会误伤）
    NEG = ("不得", "禁止", "不应", "不算", "未验证", "不存在", "不可得", "不能")
    forged = []
    for ln in md.splitlines():
        if any(w in ln for w in NEG):
            continue
        if re.search(r"逐次成交.{0,6}(已补齐|已完成|齐全|已完整)", ln):
            forged.append(ln.strip()[:120])
    ck("report_no_forged_claim", forged, [])

    bad = [c for c in checks if not c["ok"]]
    res = {
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": str(ME / "00_报告_BT-LEDGER-PILOT-04.md"),
        "n_checks": len(checks),
        "n_pass": len(checks) - len(bad),
        "n_fail": len(bad),
        "failed": bad,
        "checks": checks,
        "verdict": "PASS" if not bad else "FAIL",
    }
    (OUT / "15_报告数字复算.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"checks={len(checks)} pass={res['n_pass']} fail={res['n_fail']} "
          f"verdict={res['verdict']}")
    for c in bad:
        print("  FAIL:", c["item"], "got=", c["got"], "expect=", c["expect"])


if __name__ == "__main__":
    main()
