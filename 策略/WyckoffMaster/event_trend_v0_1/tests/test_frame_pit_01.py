# -*- coding: utf-8 -*-
"""FRAME-PIT-01 框架级防未来数据回归测试。

覆盖工单要求的全部用例，且**全部打在真实入口上**：
  · 策略入口 = `EventStateMachine.process_1h_bar`（真实决策函数）
  · 回放入口 = `EventTrendRunner.run_state_machine_simulation`（`run_variant_abc` 真正调用它）
  · 因子入口 = `features.extract_real_oi_growth(_ex)`
  · 产物装配 = `EventTrendRunner._build_run_files`

不测「一个没人调用的工具函数」——`trading_core/pit_guard.py` 只作为被这些入口调用的底层。

用例清单（对应工单「必须实际执行的测试」）：
  T01 未收盘 K 线不可读取（真实回放入口抛错）
  T02 未结束统计桶不可读取（决策时刻那一桶不得入选）
  T03 已结束但尚未发布的数据不可读取（显式声明发布延迟）
  T04 发布时间缺失：strict 拒用 / declared 照用但判不合格
  T05 数据缺失：显式返回不可用，不填默认值
  T06 边界时刻：available_at == decision_time 可用
  T07 决策时刻之后追加数据，之前的决策不变
  T08 截断到决策时刻的输入与完整输入结果一致
  T09 旧规则会选中未来桶（缺陷复现，防止「悄悄改回去」）
  T10 真实 227 条时点问题转为回归案例（真实行情 + 真实快照时刻）
  T11 真实数据端到端：回放入口受约束且如实报告不合格
  T12 run 产物记录 PIT 审计，正式资格由「规格 + PIT」双闸决定
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, os.path.join(base_dir, "策略"))

from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from WyckoffMaster.event_trend_v0_1.contracts import (BoxVersion, EventDossier,
                                                     EventState)
from WyckoffMaster.event_trend_v0_1.features import (OI_STATUS_ASSUMED,
                                                     extract_real_oi_growth,
                                                     extract_real_oi_growth_ex)
from WyckoffMaster.event_trend_v0_1.pit_bridge import PitBridge
from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
from WyckoffMaster.event_trend_v0_1.state_machine import (HOUR_MS,
                                                          EventStateMachine)
from WyckoffMaster.trading_core.pit_guard import (PitMode, PitViolation,
                                                 assert_frame_available)

MIN5_MS = 5 * 60 * 1000

# ── 合成场景常量（箱体 100 / 95 / A=2.0 ⇒ 宽度 2.5A，合法）──
BOX_H, BOX_L, BOX_A = 100.0, 95.0, 2.0
BREAKOUT_LEVEL = BOX_H + 0.1 * BOX_A          # 100.2
RETEST_LOW = 95.5
BASE_OPEN = 1_700_000_000_000 - (1_700_000_000_000 % HOUR_MS)
N_FLAT_BARS = 20                              # 凑够 V20 需要 20 根
BREAKOUT_IDX = N_FLAT_BARS                    # 第 21 根突破
BREAKOUT_OPEN = BASE_OPEN + BREAKOUT_IDX * HOUR_MS
DECISION_MS = BREAKOUT_OPEN + HOUR_MS         # 突破 K 线的决策时刻


def _make_sm(pit_mode=PitMode.DECLARED.value, declared_lag_ms=None,
             enable_oi_filter=True):
    """构造一个已经走到 RETEST 的事件，等待突破。"""
    sm = EventStateMachine(config=EventTrendConfig(),
                           enable_oi_filter=enable_oi_filter,
                           pit_mode=pit_mode,
                           oi_declared_lag_ms=declared_lag_ms)
    sm.symbol = "TESTUSDT"
    box = BoxVersion(
        version_id="box_1", symbol="TESTUSDT", venue="binance", timeframe="4h",
        formation_time_ms=BASE_OPEN - 4 * HOUR_MS,
        high_h=BOX_H, low_l=BOX_L, atr_a=BOX_A,
        efficiency_ratio_er=0.2, box_width_atr=(BOX_H - BOX_L) / BOX_A,
    )
    ev = EventDossier(
        event_id="ev_1", strategy_version="v0.1.0", venue="binance",
        symbol="TESTUSDT", created_at_ms=BASE_OPEN - 4 * HOUR_MS,
        current_state=EventState.RETEST, box=box,
        hypothesis_direction="LONG",
        sweep_time_ms=BASE_OPEN - 2 * HOUR_MS, sweep_low_s=94.8,
        sweep_volume_vs=100.0,
        retest_time_ms=BASE_OPEN - 1 * HOUR_MS, retest_low_lr=RETEST_LOW,
    )
    sm.active_event = ev
    return sm


def _bars():
    """24 根 1h：前 20 根横盘 99.0，第 21 根放量突破 101.0。"""
    out = []
    for i in range(BREAKOUT_IDX + 1):
        o = BASE_OPEN + i * HOUR_MS
        breakout = (i == BREAKOUT_IDX)
        close = 101.0 if breakout else 99.0
        out.append({
            "open_time": o, "close_time": o + HOUR_MS - 1,
            "open": 99.0, "high": max(close, 99.5),
            "low": 96.0, "close": close,
            "volume": 500.0 if breakout else 100.0,
        })
    return out


def _feed(sm, bars, oi_ts, oi_vals):
    """把 1h bar 逐根喂进真实决策函数。"""
    for b in bars:
        sm.process_1h_bar(
            open_time_ms=b["open_time"], open_p=b["open"], high_p=b["high"],
            low_p=b["low"], close_p=b["close"], volume_v=b["volume"],
            oi_timestamps=oi_ts, oi_values=oi_vals,
        )
    return sm


def _oi_series(end_ms=DECISION_MS, hours=7, growth_pct=0.05, spike_at=None):
    """构造 5m 桶 OI 序列：`ts` 是桶**起点**，从 end-6h 到 end 递增。"""
    start = end_ms - hours * HOUR_MS
    ts = np.arange(start, end_ms + 1, MIN5_MS, dtype=np.int64)
    base_val = 1_000_000.0
    n = len(ts)
    vals = base_val * (1.0 + growth_pct * np.arange(n) / max(n - 1, 1))
    if spike_at is not None:
        k = np.flatnonzero(ts == spike_at)
        if k.size:
            vals[k[0]] = base_val * 9.0
    return ts, vals.astype(np.float64)


# ══════════════════════ T01 未收盘 K 线 ══════════════════════

def test_t01_unclosed_1h_bar_is_rejected_by_real_runner(tmp_path):
    """真实回放入口：close_time 晚于决策时刻 ⇒ 直接抛 PitViolation。"""
    r = EventTrendRunner()
    r.runs_base = str(tmp_path)
    rows = _bars()
    df_1h = pd.DataFrame(rows)
    df_1h["timestamp"] = df_1h["open_time"]
    # 篡改成「这根 bar 还没收盘」：close_time = open + 2h
    df_1h.loc[df_1h.index[-1], "close_time"] = BREAKOUT_OPEN + 2 * HOUR_MS
    df_4h = pd.DataFrame({"close_time": [BASE_OPEN - 4 * HOUR_MS],
                          "high": [BOX_H], "low": [BOX_L],
                          "close": [99.0], "atr": [BOX_A]})
    oi_ts, oi_vals = _oi_series()
    with pytest.raises(PitViolation) as ei:
        r.run_state_machine_simulation("TESTUSDT", df_4h, df_1h, oi_ts, oi_vals)
    assert "未收盘" in str(ei.value)


def test_t01b_closed_bar_passes(tmp_path):
    """对照组：同一序列不改 close_time ⇒ 正常跑完且零越界。"""
    r = EventTrendRunner()
    r.runs_base = str(tmp_path)
    df_1h = pd.DataFrame(_bars())
    df_1h["timestamp"] = df_1h["open_time"]
    df_4h = pd.DataFrame({"close_time": [BASE_OPEN - 4 * HOUR_MS],
                          "high": [BOX_H], "low": [BOX_L],
                          "close": [99.0], "atr": [BOX_A]})
    oi_ts, oi_vals = _oi_series()
    sm = r.run_state_machine_simulation("TESTUSDT", df_4h, df_1h, oi_ts, oi_vals)
    assert sm.pit.candle_violations == []
    assert sm.pit.candles_checked >= len(df_1h)


# ══════════════════════ T02 未结束统计桶 ══════════════════════

def test_t02_unfinished_bucket_not_selected():
    """决策时刻那一刻开始的桶（覆盖未来 5 分钟）不得被选中。"""
    ts, vals = _oi_series(spike_at=DECISION_MS)   # 未来桶里塞一个巨大值
    res = extract_real_oi_growth_ex(ts, vals, DECISION_MS)
    assert res["status"] in ("ok", OI_STATUS_ASSUMED)
    assert res["curr_event_ms"] == DECISION_MS - MIN5_MS, "必须退到上一桶"
    assert res["curr_event_ms"] + MIN5_MS <= DECISION_MS
    # 未来桶的 9 倍尖峰不得进入取值
    assert res["oi_curr"] < 2_000_000.0


def test_t02b_old_rule_would_take_the_future_bucket():
    """T09 缺陷复现：旧的 `ts <= decision` 会选中桶起点 == 决策时刻的那一桶。"""
    ts, vals = _oi_series(spike_at=DECISION_MS)
    i_old = int(np.searchsorted(ts, DECISION_MS, side="right") - 1)
    old_ts = int(ts[i_old])
    assert old_ts == DECISION_MS
    assert old_ts + MIN5_MS > DECISION_MS, "旧选择落在未来区间"
    assert float(vals[i_old]) > 2_000_000.0


# ══════════════════════ T03 已结束但未发布 ══════════════════════

def test_t03_closed_but_unpublished_bucket_is_rejected():
    """桶在决策时刻已结束，但声明的发布延迟 60s ⇒ 决策时刻仍不可读。"""
    ts, vals = _oi_series(spike_at=DECISION_MS - MIN5_MS)
    # 声明延迟 60s：桶 [d-5m, d) 要到 d+60s 才可得
    res = extract_real_oi_growth_ex(ts, vals, DECISION_MS,
                                    oi_available_at=ts.astype(np.float64) + MIN5_MS + 60_000)
    assert res["curr_event_ms"] == DECISION_MS - 2 * MIN5_MS
    assert res["curr_available_ms"] <= DECISION_MS


def test_t03b_declared_lag_source_is_proven():
    """显式声明延迟 ⇒ 来源升级为「有证据」，不算假设。"""
    ts, vals = _oi_series()
    br = PitBridge(symbol="TESTUSDT", mode=PitMode.STRICT.value,
                   declared_lag_ms=60_000)
    br.register_oi(ts, vals)
    assert br.oi_source_proven is True
    res = br.read_oi(DECISION_MS, lookback_ms=6 * HOUR_MS,
                     max_distance_ms=15 * 60 * 1000)
    assert res["assumed"] is False
    assert res["growth"] is not None


# ══════════════════════ T04 发布时间缺失 ══════════════════════

def test_t04_missing_publication_time_strict_refuses():
    """strict：无发布时点证据 ⇒ 拒用，不填默认值。"""
    ts, vals = _oi_series()
    br = PitBridge(symbol="TESTUSDT", mode=PitMode.STRICT.value)
    br.register_oi(ts, vals)
    res = br.read_oi(DECISION_MS, lookback_ms=6 * HOUR_MS,
                     max_distance_ms=15 * 60 * 1000)
    assert res["growth"] is None
    assert res["oi_curr"] is None
    assert res["status"] in ("oi_curr_missing_before_start", OI_STATUS_ASSUMED)
    assert br.qualified is False


def test_t04b_missing_publication_time_declared_marks_unqualified():
    """declared：照用「桶结束」当可得时刻，但本次运行判不合格。"""
    ts, vals = _oi_series()
    br = PitBridge(symbol="TESTUSDT", mode=PitMode.DECLARED.value)
    br.register_oi(ts, vals)
    res = br.read_oi(DECISION_MS, lookback_ms=6 * HOUR_MS,
                     max_distance_ms=15 * 60 * 1000)
    assert res["growth"] is not None
    assert res["assumed"] is True
    assert res["status"] == OI_STATUS_ASSUMED
    assert br.qualified is False
    fails = br.qualification_failures()
    assert any("oi_available_at_unproven" in f for f in fails)


def test_t04c_strict_mode_blocks_candidate_at_strategy_entry():
    """策略入口：strict 下拿不到 OI ⇒ 事件被归档为 OI_UNAVAILABLE，不出候选。"""
    ts, vals = _oi_series()
    sm = _make_sm(pit_mode=PitMode.STRICT.value)
    _feed(sm, _bars(), ts, vals)
    assert sm.candidates == []
    # 事件被归档进 completed_events，状态必须是 OI_UNAVAILABLE（不是静默跳过）
    states = [e.current_state for e in sm.completed_events]
    assert EventState.OI_UNAVAILABLE in states
    assert sm.active_event is None


def test_t04d_declared_mode_produces_candidate_but_marks_unproven():
    ts, vals = _oi_series()
    sm = _make_sm(pit_mode=PitMode.DECLARED.value)
    _feed(sm, _bars(), ts, vals)
    assert len(sm.candidates) == 1
    cand = sm.candidates[0]
    assert cand.pit_proven is False
    assert "桶结束" in cand.pit_assumption_note
    assert sm.pit.qualified is False


# ══════════════════════ T05 数据缺失 ══════════════════════

def test_t05_empty_oi_returns_explicit_unavailable():
    res = extract_real_oi_growth_ex(np.array([], np.int64), np.array([], np.float64),
                                    DECISION_MS)
    assert res["status"] == "oi_empty"
    assert res["oi_curr"] is None and res["growth"] is None


def test_t05b_no_observation_before_decision():
    """所有观测都在决策时刻之后 ⇒ 显式不可用，不外推。"""
    ts = np.arange(DECISION_MS + MIN5_MS, DECISION_MS + HOUR_MS, MIN5_MS,
                   dtype=np.int64)
    vals = np.full(ts.size, 1_000_000.0)
    res = extract_real_oi_growth_ex(ts, vals, DECISION_MS)
    assert res["status"] == "oi_curr_missing_before_start"
    assert res["oi_curr"] is None


def test_t05c_stale_observation_rejected():
    """最近可得观测距决策超过 15 分钟 ⇒ 拒用，不拿旧值凑。"""
    ts = np.array([DECISION_MS - 3 * HOUR_MS], dtype=np.int64)
    vals = np.array([1_000_000.0])
    res = extract_real_oi_growth_ex(ts, vals, DECISION_MS,
                                    oi_available_at=ts.astype(np.float64) + MIN5_MS)
    assert res["status"].startswith("oi_curr_too_far")


# ══════════════════════ T06 边界时刻 ══════════════════════

def test_t06_available_equals_decision_is_usable():
    """available_at 恰好等于决策时刻 ⇒ 可用（闭区间），且只用它。"""
    ts, vals = _oi_series(end_ms=DECISION_MS - MIN5_MS)
    br = PitBridge(symbol="TESTUSDT", mode=PitMode.STRICT.value, declared_lag_ms=0)
    br.register_oi(ts, vals)
    avail = ts.astype(np.float64) + MIN5_MS
    assert avail.max() == DECISION_MS           # 最后一桶正好在决策时刻可得
    res = br.read_oi(DECISION_MS, lookback_ms=6 * HOUR_MS,
                     max_distance_ms=15 * 60 * 1000)
    assert res["curr_available_ms"] == DECISION_MS
    assert res["curr_event_ms"] == DECISION_MS - MIN5_MS


def test_t06b_decision_before_close_raises():
    """decision_time < close_time+1 ⇒ 构造时钟就失败。"""
    from WyckoffMaster.trading_core.pit_guard import DecisionClock
    with pytest.raises(PitViolation):
        DecisionClock(event_time_ms=0, close_time_ms=HOUR_MS - 1,
                      decision_time_ms=HOUR_MS - 1)


# ══════════════════════ T07 追加未来数据不影响先前决策 ══════════════════════

def _state_projection(sm):
    return {
        "candidates": [c.to_dict() for c in sm.candidates],
        "completed": [e.to_dict() for e in sm.completed_events],
        "active": sm.active_event.to_dict() if sm.active_event else None,
        "transitions": [t.to_dict() for t in sm.transitions],
        "traces": [t.to_dict() for t in sm.traces],
    }


def test_t07_appending_future_data_does_not_change_decisions():
    ts, vals = _oi_series()
    sm_a = _make_sm()
    _feed(sm_a, _bars(), ts, vals)
    proj_a = _state_projection(sm_a)

    # 决策时刻之后追加 2 小时 OI（含决策时刻那一刻的尖峰桶）
    extra_ts = np.arange(DECISION_MS, DECISION_MS + 2 * HOUR_MS + 1, MIN5_MS,
                         dtype=np.int64)
    extra_vals = np.full(extra_ts.size, 9_000_000.0)
    ts_b = np.concatenate([ts, extra_ts])
    vals_b = np.concatenate([vals, extra_vals])
    sm_b = _make_sm()
    _feed(sm_b, _bars(), ts_b, vals_b)
    proj_b = _state_projection(sm_b)

    assert proj_a == proj_b, "决策时刻之后的数据不得改变之前的任何决策"


def test_t07b_future_data_would_change_old_rule():
    """对照：旧规则下追加未来数据确实会改变取值 ⇒ 说明本测试不是空转。"""
    ts, vals = _oi_series()
    extra_ts = np.array([DECISION_MS], dtype=np.int64)
    extra_vals = np.array([9_000_000.0])
    ts_b = np.concatenate([ts, extra_ts])
    vals_b = np.concatenate([vals, extra_vals])
    i_old_a = int(np.searchsorted(ts, DECISION_MS, side="right") - 1)
    i_old_b = int(np.searchsorted(ts_b, DECISION_MS, side="right") - 1)
    assert float(vals_b[i_old_b]) != float(vals[i_old_a])
    # 新规则下两者一致
    a = extract_real_oi_growth_ex(ts, vals, DECISION_MS)
    b = extract_real_oi_growth_ex(ts_b, vals_b, DECISION_MS)
    assert a["oi_curr"] == b["oi_curr"]


# ══════════════════════ T08 截断等价 ══════════════════════

def test_t08_truncated_inputs_equal_full_inputs_at_decision():
    """把 K 线与 OI 截断到决策时刻，得到的候选必须逐字段相同。"""
    ts, vals = _oi_series(end_ms=DECISION_MS + 3 * HOUR_MS, hours=10)
    full = _make_sm()
    _feed(full, _bars(), ts, vals)
    assert len(full.candidates) == 1

    # 截断：OI 只保留 available_at <= DECISION 的行；K 线只保留已收盘的
    keep = ts.astype(np.float64) + MIN5_MS <= DECISION_MS
    trunc = _make_sm()
    _feed(trunc, _bars(), ts[keep], vals[keep])
    assert len(trunc.candidates) == 1
    assert full.candidates[0].to_dict() == trunc.candidates[0].to_dict()


def test_t08b_truncated_candles_equal_full_candles():
    ts, vals = _oi_series()
    full = _make_sm()
    _feed(full, _bars(), ts, vals)
    cut = _make_sm()
    _feed(cut, [b for b in _bars() if b["close_time"] < DECISION_MS], ts, vals)
    assert full.candidates[0].to_dict() == cut.candidates[0].to_dict()


# ══════════════════════ T10 真实 227 条回归 ══════════════════════

HANDOFF = os.path.join(base_dir, "evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet")
OI_BASE = os.path.join(base_dir, "数据/市场数据/parquet/oi/binance/um")


@pytest.mark.skipif(not os.path.exists(HANDOFF), reason="缺 ENTRY-AUDIT-04 交接包")
def test_t10_real_227_snapshot_points_regression():
    """真实快照时刻：旧规则 100% 取到「覆盖未来 5 分钟」的桶；新规则 0%。"""
    df = pd.read_parquet(HANDOFF)
    pairs = df[["symbol", "snapshot_time_ms"]].drop_duplicates()
    assert len(pairs) == 226, "227 行里唯一「标的+快照」是 226 个"

    cache, old_future, new_future, checked, changed = {}, 0, 0, 0, 0
    for sym, d in pairs.itertuples(index=False):
        if sym not in cache:
            fs = sorted(__import__("glob").glob(f"{OI_BASE}/{sym}/*.parquet"))
            if not fs:
                cache[sym] = None
            else:
                x = pd.concat([pd.read_parquet(f, columns=["ts", "open_interest"])
                               for f in fs], ignore_index=True)
                x = x.sort_values("ts").drop_duplicates("ts")
                cache[sym] = (x["ts"].to_numpy(np.int64),
                              x["open_interest"].to_numpy(np.float64))
        c = cache[sym]
        if c is None:
            continue
        ts, vals = c
        d = int(d)
        i_old = int(np.searchsorted(ts, d, side="right") - 1)
        if i_old < 0:
            continue
        if int(ts[i_old]) + MIN5_MS > d:
            old_future += 1
        res = extract_real_oi_growth_ex(ts, vals, d)
        if res["curr_available_ms"] is None:
            continue
        checked += 1
        if res["curr_available_ms"] > d:
            new_future += 1
        if res["curr_event_ms"] != int(ts[i_old]):
            changed += 1

    assert checked == 226, f"应全部可判定，实际 {checked}"
    assert old_future == 226, f"旧规则越界应 226，实际 {old_future}"
    assert new_future == 0, f"新规则仍越界 {new_future}"
    assert changed == 226, f"取值应全部改变，实际 {changed}"


# ══════════════════════ T11 真实数据端到端 ══════════════════════

@pytest.mark.skipif(not os.path.isdir(OI_BASE), reason="缺 OI parquet")
def test_t11_real_data_runner_entry_is_guarded(tmp_path):
    """真实行情 + 真实回放入口：守卫实际生效，且如实报告「不合格」。"""
    r = EventTrendRunner()
    r.runs_base = str(tmp_path)
    df_4h, df_1h, df_15m, oi_ts, oi_vals, _fund = r.load_symbol_data(
        "COTIUSDT", start_date="2022-01-01", end_date="2022-02-20")
    if len(df_1h) < 100:
        pytest.skip("COTIUSDT 该窗口数据不足")

    sm = r.run_state_machine_simulation("COTIUSDT", df_4h, df_1h, oi_ts, oi_vals)
    audit = sm.pit.audit()
    assert audit["candles_checked"] >= len(df_1h)
    assert audit["candle_violations"] == 0
    # 没有任何发布时点证据 ⇒ 必须判不合格，不得静默通过
    assert audit["qualified"] is False
    assert audit["oi_available_at_rows"] > 0
    assert audit["oi_available_at_assumed"] is True
    assert audit["sources"]["oi_5m"]["proven"] is False
    assert any("oi_available_at_unproven" in f
               for f in audit["qualification_failures"])
    # 每次 OI 读取都必须落在决策时刻之前
    assert audit["oi_assumed_reads"] == audit["oi_reads"] or audit["oi_reads"] == 0


# ══════════════════════ T12 run 产物记录 PIT ══════════════════════

def _stub_res(formal=True):
    return {
        "engine_result": {"trades": [], "equity_curve": [], "reconcile": {}},
        "summary": {"trade_count": 0, "formal_pass_allowed": formal},
        "instrument_spec": {"tick_size": "0.01"},
        "spec_status": "spec_applied",
        "identity": {}, "precision": {}, "orders": [], "ledger_events": [],
        "validation_results": [],
    }


def test_t12_run_artifacts_record_pit_audit_and_gate(tmp_path):
    ts, vals = _oi_series()
    sm = _make_sm()
    _feed(sm, _bars(), ts, vals)

    r = EventTrendRunner()
    r.runs_base = str(tmp_path)
    files = r._build_run_files(
        run_id="run_test", symbol="TESTUSDT", variant="A", sm_inst=sm,
        res_inst=_stub_res(formal=True), funnel={}, run_timestamp=1,
        data_summary={}, fingerprints={})

    inputs = json.loads(files["inputs.json"])
    summary = json.loads(files["summary.json"])

    assert inputs["pit_audit"]["pit_guard_version"].startswith("pit_guard_v")
    assert inputs["pit_audit"]["qualified"] is False
    assert inputs["pit_guard_version"] == inputs["pit_audit"]["pit_guard_version"]
    # 规格通过但 PIT 不合格 ⇒ 正式资格仍为 False
    assert summary["formal_pass_allowed"] is False
    assert summary["formal_pass_allowed_components"] == {
        "instrument_spec": True, "pit": False}
    assert any("oi_available_at_unproven" in x
               for x in summary["formal_pass_block_reasons"])
    assert summary["pit_audit_sha256"] == inputs["pit_audit"]["audit_sha256"]


def test_t12b_qualified_pit_allows_formal_pass(tmp_path):
    """OI 声明了发布延迟（有证据）⇒ PIT 合格，双闸同时通过才放行。"""
    ts, vals = _oi_series()
    sm = _make_sm(declared_lag_ms=60_000)
    _feed(sm, _bars(), ts, vals)
    assert sm.pit.qualified is True

    r = EventTrendRunner()
    r.runs_base = str(tmp_path)
    files = r._build_run_files(
        run_id="run_test2", symbol="TESTUSDT", variant="A", sm_inst=sm,
        res_inst=_stub_res(formal=True), funnel={}, run_timestamp=2,
        data_summary={}, fingerprints={})
    summary = json.loads(files["summary.json"])
    assert summary["formal_pass_allowed"] is True
    assert summary["formal_pass_block_reasons"] == []


def test_t12c_spec_failure_still_blocks(tmp_path):
    """规格不合格 ⇒ 即使 PIT 合格也不放行。"""
    ts, vals = _oi_series()
    sm = _make_sm(declared_lag_ms=60_000)
    _feed(sm, _bars(), ts, vals)
    r = EventTrendRunner()
    r.runs_base = str(tmp_path)
    files = r._build_run_files(
        run_id="run_test3", symbol="TESTUSDT", variant="A", sm_inst=sm,
        res_inst=_stub_res(formal=False), funnel={}, run_timestamp=3,
        data_summary={}, fingerprints={})
    summary = json.loads(files["summary.json"])
    assert summary["formal_pass_allowed"] is False
    assert "instrument_spec_unproven" in summary["formal_pass_block_reasons"]


# ══════════════════════ 守卫本身：帧级校验 ══════════════════════

def test_assert_frame_available_rejects_future_rows():
    frame = pd.DataFrame({"available_at": [1.0, 2.0, 3.0]})
    assert assert_frame_available(frame, 3) == 3
    with pytest.raises(PitViolation):
        assert_frame_available(frame, 2)
    with pytest.raises(PitViolation):
        assert_frame_available(pd.DataFrame({"x": [1]}), 10)


def test_assert_frame_available_rejects_nan_availability():
    frame = pd.DataFrame({"available_at": [1.0, np.nan]})
    with pytest.raises(PitViolation):
        assert_frame_available(frame, 10)
