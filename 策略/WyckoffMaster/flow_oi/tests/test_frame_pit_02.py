# -*- coding: utf-8 -*-
"""FRAME-PIT-02｜FLOW-OI 真实因子入口与四组实验入口的 PIT 约束测试。

纪律
────
1. 全部打**真实入口**：`evidence/FLOW-OI-01/generate_flow_oi_01.py::extract_features_for_candidate`
   与 `evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py::run_four_groups`，
   不测"另写一个没人用的工具函数"。
2. 反例来自 FRAME-VERIFY-02 的 VERIFY-A（未收盘 K 线 + 未结束 5m 桶），
   原样复跑，不得改造成更容易通过的样子。
3. 同时验证**合法历史输入照常处理** —— 一律拒绝不算修复。
4. 阈值/公式/撮合/费用一个都不改，只改"取哪些行"和"资格怎么标"。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import pytest

WORKSPACE = "/Users/boos/Desktop/威科夫因子共振趋势"
STRATEGY_ROOT = os.path.join(WORKSPACE, "策略")
FLOW_SCRIPT = os.path.join(WORKSPACE, "evidence/FLOW-OI-01/generate_flow_oi_01.py")
FOUR_GROUPS_V2 = os.path.join(WORKSPACE, "evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py")
LEGACY_PYC = os.path.join(
    WORKSPACE,
    "evidence/FRAME-PIT-02/legacy/generate_flow_oi_01.pre_pit.cpython-313.pyc")
FE09 = os.path.join(
    WORKSPACE,
    "evidence/FACTOR-ENTRY-09/reconstructed_candidate_features_08.parquet")

if STRATEGY_ROOT not in sys.path:
    sys.path.insert(0, STRATEGY_ROOT)

from WyckoffMaster.trading_core.pit_guard import PitUnavailable  # noqa: E402

HOUR = 3_600_000
MIN5 = 300_000
DAY = 24 * HOUR
TS = 1_600_000_000_000          # 2020-09-13 12:00:00 UTC，整点
SYM = "BTCUSDT"

#: 参与"决策后追加数据不影响已发生时刻"比对的字段
FEATURE_KEYS = [
    "bar_open", "bar_high", "bar_low", "bar_close", "bar_volume",
    "bar_quote_volume", "bar_taker_buy_quote", "bar_taker_sell_quote",
    "bar_taker_buy_net_quote", "bar_taker_buy_ratio", "bar_upper_shadow_ratio",
    "bar_body_ratio", "bar_return", "bar_volume_ratio_24h", "quote_cvd_24h",
    "base_cvd_24h", "norm_cvd_24h", "taker_buy_ratio_24h",
    "top_position_ls_ratio", "top_account_ls_ratio", "global_account_ls_ratio",
    "taker_ls_vol_ratio", "delta_top_pos_ratio_24h",
    "oi_base", "oi_quote", "delta_oi_base_24h", "delta_oi_quote_24h",
    "is_hyp_a_absorption", "is_hyp_b_healthy_drive",
    "is_hyp_c_stall_distribution", "is_hyp_d_short_squeeze_exhaustion",
    "pass_g1", "pass_g2", "g2_reject_reason",
    "pass_g3", "g3_reject_reason", "pass_g4", "g4_reject_reason",
    "coverage_status", "pit_status", "pit_qualified", "formal_eligible",
]


# ══════════════════════════ 装配 ══════════════════════════


def _fresh_flow():
    """每次拿一个干净的真实入口模块（模块级缓存互不污染）。"""
    spec = importlib.util.spec_from_file_location("flow_entry_under_test", FLOW_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(ts: int = TS, symbol: str = SYM) -> pd.Series:
    return pd.Series({
        "candidate_id": f"cand_test_{symbol}_{ts}",
        "symbol": symbol,
        "datetime_utc": "2020-09-13T12:00:00Z",
        "snapshot_time_ms": ts,
        "is_filled": True,
        "net_pnl": 50.0, "gross_pnl": 60.0, "fee_paid": 5.0, "funding_cost": 5.0,
        "entry_quality_category": "CONTINUED_UPTREND",
        "fwd_ret_15m": 0.01, "fwd_ret_1h": 0.02, "fwd_ret_4h": 0.03,
        "mfe_r": 2.0, "generation": "test",
    })


def _bar(open_time: int, *, is_closed: int = 1, vol: float = 1000.0,
         close: float = 101.0, taker_ratio: float = 0.5) -> dict:
    qvol = vol * 100.0
    return {
        "open_time": open_time,
        "close_time": open_time + HOUR - 1,
        "open": 100.0, "high": max(close, 102.0), "low": 98.0, "close": close,
        "volume": vol, "quote_volume": qvol,
        "taker_buy_volume": vol * taker_ratio,
        "taker_buy_quote_volume": qvol * taker_ratio,
        "is_closed": is_closed,
    }


def _candles(ts: int = TS, *, n_closed: int = 4, unclosed: bool = False) -> pd.DataFrame:
    rows = [_bar(ts - (n_closed - i) * HOUR) for i in range(n_closed)]
    if unclosed:
        # VERIFY-A 原样：决策时刻刚开始的那根，成交量/收盘价都是"未来"
        rows.append(_bar(ts, is_closed=0, vol=8_888_888.0, close=149.0))
    return pd.DataFrame(rows)


def _bucket(ts: int, oi: float, top: float = 1.0, **kw) -> dict:
    return {
        "ts": ts,
        "sum_open_interest": oi,
        "sum_open_interest_value": oi * 100.0,
        "sum_toptrader_long_short_ratio": top,
        "count_toptrader_long_short_ratio": kw.get("acc", 2.0),
        "count_long_short_ratio": kw.get("glob", 1.2),
        "sum_taker_long_short_vol_ratio": kw.get("taker", 1.1),
    }


def _metrics(ts: int = TS, *, unfinished: bool = True,
             with_prev: bool = True, with_mid: bool = True) -> pd.DataFrame:
    rows = []
    if with_prev:
        rows.append(_bucket(ts - DAY - MIN5, 1000.0, 1.00))
    if with_mid:
        rows.append(_bucket(ts - 2 * MIN5, 1050.0, 1.05))
    rows.append(_bucket(ts - MIN5, 1100.0, 1.10))
    if unfinished:
        rows.append(_bucket(ts, 99999.0, 9.90))
    return pd.DataFrame(rows)


def _inject(mod, candles=None, metrics=None, oi=None, symbol: str = SYM) -> None:
    mod._CACHE_CANDLES_1H[symbol] = candles if candles is not None else pd.DataFrame()
    mod._CACHE_METRICS[symbol] = metrics if metrics is not None else pd.DataFrame()
    mod._CACHE_OI[symbol] = oi if oi is not None else pd.DataFrame()


def _sig(res: dict) -> Dict[str, Any]:
    return {k: res.get(k) for k in FEATURE_KEYS}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
        return True
    return a == b


def _assert_same_features(a: dict, b: dict, keys=None) -> None:
    keys = keys or FEATURE_KEYS
    diff = {k: (a.get(k), b.get(k)) for k in keys if not _same(a.get(k), b.get(k))}
    assert not diff, f"特征发生变化：{diff}"


# ══════════════════════════ T01 未收盘 K 线 ══════════════════════════


def test_t01_unclosed_candle_is_not_read():
    """VERIFY-A 反例：决策时刻刚开始、is_closed=0 的那根 K 线不得进入特征。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(unclosed=True), metrics=_metrics())
    r = m.extract_features_for_candidate(_row())

    assert r["bar_volume"] != 8_888_888.0, "未收盘 K 线的成交量被读进了特征"
    assert r["bar_close"] != 149.0, "未收盘 K 线的收盘价被读进了特征"
    assert r["bar_volume"] == 1000.0, "应取上一根已收盘 K 线"
    assert r["bar_open_time_ms"] == TS - HOUR
    assert r["bar_available_at_ms"] <= TS, "可用时刻晚于决策时刻"


def test_t01b_verify_a_full_counterexample_no_longer_leaks():
    """FRAME-VERIFY-02 VERIFY-A 整体复跑：未来量、未来 OI 都不得出现。"""
    m = _fresh_flow()
    # 与 VERIFY-A 完全一致：metrics 只有一个 ts 桶，没有更早的可用桶
    _inject(m, candles=_candles(unclosed=True),
            metrics=pd.DataFrame([_bucket(TS, 99999.0, 9.9)]))
    r = m.extract_features_for_candidate(_row())

    assert r["bar_volume"] != 8_888_888.0
    assert r.get("oi_base") != 99999.0
    # 旧实现这里硬写 COMPLETE；现在必须如实说缺
    assert r["coverage_status"] != "COMPLETE"
    # 旧实现根本没有这些字段
    for k in ("pit_status", "pit_qualified", "formal_eligible",
              "formal_block_reasons", "pit_sources", "decision_clock",
              "feature_contract_version"):
        assert k in r, f"缺少 PIT 字段 {k}"
    assert r["formal_eligible"] is False


# ══════════════════════════ T02 未结束 5m 桶 ══════════════════════════


def test_t02_unfinished_bucket_is_not_read():
    """5m 桶 ts 是桶**起点**：ts 正好等于决策时刻的那一桶覆盖未来 5 分钟，不得使用。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(), metrics=_metrics(unfinished=True))
    r = m.extract_features_for_candidate(_row())

    assert r.get("oi_base") == 1100.0, "应取 ts-5m 那一桶，而不是 ts 桶"
    assert r.get("metrics_event_time_ms") == TS - MIN5
    assert r["pit_sources"]["metrics_5m"]["available_at_ms"] <= TS


def test_t02b_oi_parquet_fallback_also_respects_availability():
    """metrics 缺失时走 oi parquet，同样不得读未结束桶。"""
    m = _fresh_flow()
    oi = pd.DataFrame([
        {"ts": TS - DAY - MIN5, "open_interest": 1000.0, "open_interest_value": 100000.0},
        {"ts": TS - MIN5, "open_interest": 1100.0, "open_interest_value": 110000.0},
        {"ts": TS, "open_interest": 99999.0, "open_interest_value": 9999900.0},
    ])
    _inject(m, candles=_candles(), metrics=pd.DataFrame(), oi=oi)
    r = m.extract_features_for_candidate(_row())

    assert r["has_oi"] is True
    assert r.get("oi_base") == 1100.0
    assert r.get("oi_event_time_ms") == TS - MIN5
    assert r["oi_source_used"] == "oi_5m"


# ══════════════════════════ T03 已结束但未发布 ══════════════════════════


def test_t03_bucket_ended_but_not_published_is_not_read():
    """桶在决策时刻刚结束（ts-5m，结束于 ts），但声明发布延迟 60s ⇒ 决策时仍不可得。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(), metrics=_metrics(unfinished=False))
    r = m.extract_features_for_candidate(
        _row(), metrics_declared_lag_ms=60_000, oi_declared_lag_ms=60_000)

    # 桶结束 ≠ 已发布：ts-5m 桶要等到 ts+60s 才可得，只能用 ts-10m 那桶
    assert r.get("oi_base") == 1050.0, "已结束但未发布的桶被当成了可得"
    assert r.get("metrics_event_time_ms") == TS - 2 * MIN5
    assert r["pit_sources"]["metrics_5m"]["available_at_ms"] <= TS


def test_t03b_declared_lag_never_grants_qualification():
    """声明延迟只推迟取数时刻，不提升资格等级（否则随手填个 lag 就能洗白）。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(), metrics=_metrics(unfinished=False))
    r = m.extract_features_for_candidate(
        _row(), metrics_declared_lag_ms=60_000, oi_declared_lag_ms=60_000)

    assert r["pit_status"] == "ASSUMED"
    assert r["pit_qualified"] is False
    assert r["formal_eligible"] is False
    assert any("declared_lag" in x for x in r["formal_block_reasons"])


def test_t03c_zero_lag_is_rejected():
    """lag=0 不是证据，等价于把桶结束当发布时间 —— 直接拒收。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(), metrics=_metrics())
    with pytest.raises(ValueError):
        m.extract_features_for_candidate(_row(), metrics_declared_lag_ms=0)


# ══════════════════════════ T04 可用时点未知 ══════════════════════════


def test_t04_unproven_source_strict_refuses_without_crashing():
    """可用时点未知：strict 模式显式拒用（值置 None + 写明原因），K 线仍照常处理。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(), metrics=_metrics())
    r = m.extract_features_for_candidate(_row(), mode="strict")

    assert r["pit_status"] == "REFUSED"
    assert r["pit_qualified"] is False
    assert r["has_metrics"] is False and r["has_oi"] is False
    assert r.get("oi_base") is None
    # 不得因为拒用就把能证的 K 线也一起丢掉（不是一律拒绝）
    assert r["has_c1h"] is True
    assert r["bar_volume"] == 1000.0
    assert r["coverage_status"] == "MISSING_METRICS"


def test_t04b_unproven_source_declared_uses_but_marks_unqualified():
    """可用时点未知：declared_assumption 模式照用，但必须判不合格并写明原因。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(), metrics=_metrics())
    r = m.extract_features_for_candidate(_row())

    assert r["pit_status"] == "ASSUMED"
    assert r["pit_qualified"] is False
    assert r["formal_eligible"] is False
    assert r["coverage_status"] == "COMPLETE"      # 数据是齐的
    assert r.get("oi_base") == 1100.0              # 值确实算出来了
    joined = " | ".join(r["formal_block_reasons"])
    assert "unproven" in joined or "assumed" in joined


# ══════════════════════════ T05 覆盖与资格分开 ══════════════════════════


def test_t05_coverage_and_pit_are_separate_fields():
    """覆盖状态（数据齐不齐）与 PIT 资格（时点有没有证据）必须分别表达。"""
    m = _fresh_flow()
    _inject(m, candles=_candles(), metrics=_metrics())
    full = m.extract_features_for_candidate(_row())

    m2 = _fresh_flow()
    _inject(m2, candles=_candles(), metrics=pd.DataFrame())
    no_metrics = m2.extract_features_for_candidate(_row())

    assert full["coverage_status"] == "COMPLETE"
    assert no_metrics["coverage_status"] == "MISSING_METRICS"
    # 覆盖变了，但 PIT 资格口径不受覆盖影响
    assert full["pit_qualified"] is False and no_metrics["pit_qualified"] is False
    assert no_metrics["formal_eligible"] is False
    assert "coverage_missing_metrics" in no_metrics["formal_block_reasons"]


# ══════════════════════════ T06 合法历史输入照常处理 ══════════════════════════


@pytest.fixture(scope="module")
def fe09() -> pd.DataFrame:
    return pd.read_parquet(FE09)


def test_t06_legitimate_history_is_processed_not_refused(fe09):
    """合法历史输入必须能正常算出特征 —— 一律拒绝不是修复。"""
    m = _fresh_flow()
    r = m.extract_features_for_candidate(fe09.iloc[0])

    assert r["coverage_status"] == "COMPLETE"
    assert r["pit_status"] == "ASSUMED"        # 未证明，但不是拒用
    assert r["has_c1h"] and r["has_metrics"] and r["has_oi"]
    assert r["bar_volume"] > 0
    assert 0.0 < r["bar_taker_buy_ratio"] < 1.0
    assert r.get("oi_base") and r.get("oi_base") > 0
    assert r.get("delta_oi_base_24h") is not None
    assert r["candle_24h_bars"] == 24          # 24 根已收盘，不多不少
    assert r["pass_g1"] is True
    # 研究可用 ≠ 正式合格
    assert r["formal_eligible"] is False


def test_t06b_decision_clock_is_consistent(fe09):
    """决策时钟：close = 所用 1h bar 起点 + 1h - 1ms；decision = 快照时刻。"""
    m = _fresh_flow()
    r = m.extract_features_for_candidate(fe09.iloc[0])
    c = r["decision_clock"]

    assert c["decision_time_ms"] == int(fe09.iloc[0]["snapshot_time_ms"])
    assert c["close_time_ms"] == c["event_time_ms"] + HOUR - 1
    assert c["decision_time_ms"] >= c["close_time_ms"] + 1
    assert r["bar_open_time_ms"] == c["event_time_ms"]


# ══════════════════════════ T07 未来数据不影响已发生时刻 ══════════════════════════


def test_t07_future_data_does_not_change_past_features(fe09):
    """决策之后追加/篡改数据，该时刻的特征必须一模一样。"""
    row = fe09.iloc[0]
    sym, ts = row["symbol"], int(row["snapshot_time_ms"])
    m = _fresh_flow()
    base = m.extract_features_for_candidate(row)

    c = m.load_symbol_data(sym, "candles", "1h").copy()
    mt = m.load_symbol_data(sym, "metrics").copy()
    oi = m.load_symbol_data(sym, "oi").copy()

    # 1) 篡改决策之后已有的行（成交量 ×3、收盘价改成 999）
    #    真实 parquet 的数值列是字符串（ArrowStringArray），先转数值再改
    for col in ("volume", "quote_volume", "close", "open", "high", "low"):
        c[col] = pd.to_numeric(c[col], errors="coerce")
    fut_mask = c["open_time"] > ts
    c.loc[fut_mask, "volume"] = c.loc[fut_mask, "volume"] * 3.0
    c.loc[fut_mask, "quote_volume"] = c.loc[fut_mask, "quote_volume"] * 3.0
    c.loc[fut_mask, "close"] = 999.0
    for col in ("sum_open_interest", "sum_toptrader_long_short_ratio"):
        mt[col] = pd.to_numeric(mt[col], errors="coerce")
    m_mask = mt["ts"] > ts
    mt.loc[m_mask, "sum_open_interest"] = mt.loc[m_mask, "sum_open_interest"] * 7.0
    mt.loc[m_mask, "sum_toptrader_long_short_ratio"] = 9.9
    oi["open_interest"] = pd.to_numeric(oi["open_interest"], errors="coerce")
    o_mask = oi["ts"] > ts
    oi.loc[o_mask, "open_interest"] = oi.loc[o_mask, "open_interest"] * 7.0

    # 2) 再往后追加几根极端值
    fut_c = pd.concat([c, pd.DataFrame([
        _bar(ts + 10 * HOUR, vol=5_555_555.0, close=999.0),
        _bar(ts + 11 * HOUR, vol=6_666_666.0, close=888.0),
    ])], ignore_index=True)
    fut_m = pd.concat([mt, pd.DataFrame([
        _bucket(ts + 10 * HOUR, 777777.0, 7.7),
        _bucket(ts + 10 * HOUR + MIN5, 888888.0, 8.8),
    ])], ignore_index=True)
    fut_o = pd.concat([oi, pd.DataFrame([
        {"ts": ts + 10 * HOUR, "open_interest": 666666.0, "open_interest_value": 66666600.0},
    ])], ignore_index=True)

    _inject(m, candles=fut_c, metrics=fut_m, oi=fut_o)
    after = m.extract_features_for_candidate(row)

    _assert_same_features(base, after)


def test_t07b_truncation_equivalence(fe09):
    """只喂决策时刻前的数据，与喂全量数据，在该时刻结果一致。"""
    row = fe09.iloc[0]
    sym, ts = row["symbol"], int(row["snapshot_time_ms"])
    m = _fresh_flow()
    full = m.extract_features_for_candidate(row)

    c = m.load_symbol_data(sym, "candles", "1h")
    mt = m.load_symbol_data(sym, "metrics")
    oi = m.load_symbol_data(sym, "oi")
    _inject(m,
            candles=c[c["open_time"] < ts],
            metrics=mt[mt["ts"] < ts],
            oi=oi[oi["ts"] < ts])
    trunc = m.extract_features_for_candidate(row)

    _assert_same_features(full, trunc)


# ══════════════════════════ T08 阈值未改 ══════════════════════════


def test_t08_group_thresholds_unchanged():
    """四组准入阈值原样：taker_buy_ratio < 0.45 仍拦在 G2。"""
    m = _fresh_flow()
    low = _candles()
    low.loc[low.index[-1], "taker_buy_quote_volume"] = (
        low.loc[low.index[-1], "quote_volume"] * 0.40)
    _inject(m, candles=low, metrics=_metrics())
    r = m.extract_features_for_candidate(_row())

    assert r["bar_taker_buy_ratio"] < 0.45
    assert r["pass_g2"] is False
    assert "LOW_TAKER_BUY_RATIO" in r["g2_reject_reason"]
    assert r["pass_g3"] is False and r["pass_g4"] is False


# ══════════════════════════ T09 正式用途被阻断 ══════════════════════════


def test_t09_require_qualified_blocks_formal_use(fe09):
    """正式用途必须走 require_qualified=True；未证明就抛，不得静默返回。"""
    m = _fresh_flow()
    with pytest.raises(PitUnavailable):
        m.extract_features_for_candidate(fe09.iloc[0], require_qualified=True)


# ══════════════════════════ T10 四组实验入口 ══════════════════════════


def _load_four_groups():
    spec = importlib.util.spec_from_file_location("flow_four_groups_v2", FOUR_GROUPS_V2)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_t10_four_groups_consume_controlled_features(fe09):
    """四组只吃受控特征；资格字段与逐候选一致。"""
    fg = _load_four_groups()
    df_feat = fg.extract_all(fe09, limit=3)
    summary = fg.run_four_groups(df_feat)

    assert summary["groups_consumed_from"] == "controlled_entry_only"
    assert summary["formal_eligible"] == bool(df_feat["formal_eligible"].all())
    assert summary["pit"]["candidates"] == 3
    assert summary["pit"]["formal_eligible_count"] == int(df_feat["formal_eligible"].sum())
    # 正式资格没拿到，就不许把结论写成"正式"
    assert summary["formal_eligible"] is False
    assert "研究结论" in summary["formal_statement"]
    # 四组都算了，且都来自同一张受控表
    assert len(summary["overall"]) == 4
    assert all(g["trades"] >= 0 for g in summary["overall"])


def test_t10b_legacy_feature_table_is_rejected():
    """旧路径产物（没有 PIT 字段）不得进入四组实验。"""
    fg = _load_four_groups()
    legacy = pd.DataFrame([{"candidate_id": "x", "is_filled": True,
                            "net_pnl": 1.0, "gross_pnl": 1.0, "fee_paid": 0.0,
                            "funding_cost": 0.0, "entry_quality_category": "X",
                            "pass_g1": True, "pass_g2": True, "pass_g3": True,
                            "pass_g4": True}])
    with pytest.raises(fg.UncontrolledFeatureError):
        fg.run_four_groups(legacy)


def test_t10c_legacy_four_group_function_also_rejects_uncontrolled_table():
    """旧脚本里的四组函数也加了闸：非受控特征表直接拒收。"""
    m = _fresh_flow()
    legacy = pd.DataFrame([{"candidate_id": "x", "is_filled": True, "net_pnl": 1.0,
                            "gross_pnl": 1.0, "fee_paid": 0.0, "funding_cost": 0.0,
                            "entry_quality_category": "X", "pass_g1": True,
                            "pass_g2": True, "pass_g3": True, "pass_g4": True}])
    with pytest.raises(RuntimeError, match="非受控特征表"):
        m.step4_run_four_groups(legacy)


# ══════════════════════════ T11 与旧实现对照 ══════════════════════════


def _load_legacy():
    import importlib.machinery
    loader = importlib.machinery.SourcelessFileLoader("legacy_flow_pre_pit", LEGACY_PYC)
    spec = importlib.util.spec_from_loader("legacy_flow_pre_pit", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_t11_legacy_implementation_really_leaked():
    """把旧实现（冻结字节码）跑同一反例，确认它确实泄露 —— 证明修复针对的是真问题。"""
    old = _load_legacy()
    _inject(old, candles=_candles(unclosed=True), metrics=_metrics(
        unfinished=True, with_prev=False, with_mid=False))
    r = old.extract_features_for_candidate(_row())

    assert r["bar_volume"] == 8_888_888.0
    assert r.get("oi_base") == 99999.0
    assert r["coverage_status"] == "COMPLETE"
    assert "pit_status" not in r
