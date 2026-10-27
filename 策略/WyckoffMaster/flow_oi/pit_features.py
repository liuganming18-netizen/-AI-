# -*- coding: utf-8 -*-
"""FLOW-OI 受控因子提取（FRAME-PIT-02）。

为什么要这一层
──────────────
`evidence/FLOW-OI-01/generate_flow_oi_01.py::extract_features_for_candidate`
是 FLOW-OI 四组实验**真实**的因子入口。它原来的写法是：

    c_pit = df_c[df_c["open_time"] <= ts]      # ts 是决策时刻
    m_pit = df_m[df_m["ts"] <= ts]

`open_time` / `ts` 都是**区间起点**。决策时刻恰好是整点，于是这两句都会选中
"从决策时刻才刚开始"的那一根 / 那一桶 —— 整段内容都在决策之后。
结果：突破当根的 OHLCV、当刻 OI 全是未来数据，`coverage_status` 还硬写 `COMPLETE`。

本层做的唯一一件事：**把取数改成按 `available_at` 判定**，并把
"数据是否齐全"（coverage）与"可用时点是否有证据"（PIT 资格）分成两个字段。
它不重新实现 PIT —— 全部复用 `trading_core/pit_guard.py`（FRAME-PIT-01）。

四条口径（与 FRAME-PIT-01 完全一致，不得各自发明）
────────────────────────────────────────────────
| 数据源 | 规则 | 结论 |
|---|---|---|
| 1h K 线 | `rule_candles`：`close_time + 1ms` | `exchange_close`（有闭合证据） |
| metrics 5m | `rule_bucket_metrics`：桶起点 `ts` | 无发布时点证据 ⇒ `unproven` |
| OI 5m | 同上 | 无发布时点证据 ⇒ `unproven` |

`unproven` 只有两条路：`strict` 拒用；`declared_assumption` 按"桶结束"取用，
但本次结果**不具备正式资格**（`pit_qualified=False`）。

**不得**把桶结束、或"桶时间加 5 分钟"当成已证明的发布时间 —— 那只是假设。

防护边界（必须如实说明）
────────────────────────
本层只约束"经过它"的读取。同进程代码仍可直接 `pd.read_parquet`。
它提供受控入口 + 违规可检测，**不是**进程级隔离，不得宣称已完全隔离。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..trading_core.pit_guard import (PIT_GUARD_VERSION, AvailabilityStatus,
                                      DecisionClock, PitMode, PitStore,
                                      PitUnavailable, PitViolation,
                                      rule_bucket_metrics, rule_candles)

FLOW_PIT_VERSION = "flow_oi_pit_v2.0.0"
FEATURE_CONTRACT_VERSION = "flow_oi_features_v2"

SOURCE_CANDLES = "candles_1h"
SOURCE_METRICS = "metrics_5m"
SOURCE_OI = "oi_5m"

HOUR_MS = 3_600_000
MIN5_MS = 300_000

#: 与原脚本完全一致的容差（不得改动）
STALE_CANDLE_MS = 2 * HOUR_MS
STALE_METRICS_MS = 2 * HOUR_MS
LOOKBACK_24H_MS = 24 * HOUR_MS

#: coverage（数据齐不齐）与 pit（时点有没有证据）是两件事
COVERAGE_COMPLETE = "COMPLETE"
COVERAGE_PARTIAL = "PARTIAL"
COVERAGE_MISSING_CANDLES = "MISSING_CANDLES"
COVERAGE_MISSING_METRICS = "MISSING_METRICS"
COVERAGE_MISSING_OI = "MISSING_OI"

PIT_PROVEN = "PROVEN"
PIT_ASSUMED = "ASSUMED"
PIT_REFUSED = "REFUSED"
PIT_INCOMPLETE = "INCOMPLETE"     # 数据有洞 ⇒ 无法判定完整性，不能算可证


@dataclass
class SourceRead:
    """一个数据源在一次决策里的读取结果。"""

    name: str
    state: str                     # used / missing / refused
    proven: bool = False           # 本层资格口径：只有 exchange_close 才算"可证"
    guard_proven: bool = False     # pit_guard 自己的判级（declared_lag 也算 proven）
    rule_status: str = ""
    assumed: bool = False          # 是否走了"桶结束/声明延迟"这类假设时刻
    rows_total: int = 0
    rows_used: int = 0
    event_time_ms: Optional[int] = None
    available_at_ms: Optional[int] = None
    age_ms: Optional[int] = None
    reason: str = ""

    def identity(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "proven": bool(self.proven),
            "guard_proven": bool(self.guard_proven),
            "rule_status": self.rule_status,
            "assumed": bool(self.assumed),
            "rows_total": int(self.rows_total),
            "rows_used": int(self.rows_used),
            "event_time_ms": self.event_time_ms,
            "available_at_ms": self.available_at_ms,
            "age_ms": self.age_ms,
            "reason": self.reason,
        }


class FlowPitFeatureExtractor:
    """FLOW-OI 因子提取的受控入口。

    一次 `extract()` = 一次决策 = 一个独立的 `PitStore`（不做跨候选共享，
    避免上一个候选的假设/拒用记录污染下一个候选的资格判定）。
    """

    def __init__(self,
                 *,
                 mode: str = PitMode.DECLARED.value,
                 candle_bar_ms: int = HOUR_MS,
                 metrics_bucket_ms: int = MIN5_MS,
                 oi_bucket_ms: int = MIN5_MS,
                 metrics_declared_lag_ms: Optional[int] = None,
                 oi_declared_lag_ms: Optional[int] = None):
        self.mode = PitMode(mode)
        self.candle_bar_ms = int(candle_bar_ms)
        self.metrics_bucket_ms = int(metrics_bucket_ms)
        self.oi_bucket_ms = int(oi_bucket_ms)
        # 声明延迟必须是正数：0 不是证据，等价于"把桶结束当发布时间"
        self.metrics_declared_lag_ms = _validated_lag(metrics_declared_lag_ms,
                                                      SOURCE_METRICS)
        self.oi_declared_lag_ms = _validated_lag(oi_declared_lag_ms, SOURCE_OI)

    # ── 规则装配 ──
    def _rules(self) -> Dict[str, Any]:
        return {
            SOURCE_CANDLES: rule_candles("1h", self.candle_bar_ms),
            SOURCE_METRICS: rule_bucket_metrics(
                SOURCE_METRICS, self.metrics_bucket_ms,
                declared_lag_ms=self.metrics_declared_lag_ms,
                evidence=("parquet metrics/binance/um/<symbol>/*.parquet："
                          "只有 ts（5m 桶起点），无 create_time / available_at"
                          "⇒ 无发布时点证据")
                if self.metrics_declared_lag_ms is None else
                f"人工声明发布延迟 {self.metrics_declared_lag_ms}ms（非交易所证明）"),
            SOURCE_OI: rule_bucket_metrics(
                SOURCE_OI, self.oi_bucket_ms,
                declared_lag_ms=self.oi_declared_lag_ms,
                evidence=("parquet oi/binance/um/<symbol>/*.parquet："
                          "只有 ts（5m 桶起点），fetched_at 全为空"
                          "⇒ 无发布时点证据")
                if self.oi_declared_lag_ms is None else
                f"人工声明发布延迟 {self.oi_declared_lag_ms}ms（非交易所证明）"),
        }

    # ── 主入口 ──
    def extract(self, *, symbol: str, decision_time_ms: int,
                candles: Optional[pd.DataFrame],
                metrics: Optional[pd.DataFrame],
                oi: Optional[pd.DataFrame],
                meta: Optional[Dict[str, Any]] = None) -> dict:
        """在 `decision_time_ms` 提取 FLOW-OI 特征。

        `meta` 里的候选基础字段（candidate_id / 盈亏 / 分类等）原样带出，
        本层只负责"取数 + 算指标 + 标 PIT 状态"，不改任何阈值。
        """
        d = int(decision_time_ms)
        store = PitStore(mode=self.mode, symbol=symbol)
        rules = self._rules()

        frames = {
            SOURCE_CANDLES: _prepare_candles(candles),
            SOURCE_METRICS: _prepare_buckets(metrics),
            SOURCE_OI: _prepare_buckets(oi),
        }
        for name, frame in frames.items():
            if frame is not None and len(frame) > 0:
                store.add(name, frame, rules[name])

        reads: Dict[str, SourceRead] = {}
        avail: Dict[str, Optional[pd.DataFrame]] = {}
        for name in (SOURCE_CANDLES, SOURCE_METRICS, SOURCE_OI):
            frame, read = self._read(store, name, frames[name], d, rules)
            avail[name] = frame
            reads[name] = read

        res: Dict[str, Any] = dict(meta or {})
        res.setdefault("symbol", symbol)
        res.setdefault("snapshot_time_ms", d)
        res.update({
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "flow_pit_version": FLOW_PIT_VERSION,
            "pit_guard_version": PIT_GUARD_VERSION,
            "pit_mode": self.mode.value,
            "decision_time_ms": d,
            "has_c1h": reads[SOURCE_CANDLES].state == "used",
            "has_metrics": reads[SOURCE_METRICS].state == "used",
            "has_oi": False,          # 由下面两条路径之一置真
        })

        # ── 1. 突破当根 1h K 线（已收盘、且 available_at <= decision）──
        c = avail[SOURCE_CANDLES]
        if c is None:
            res["coverage_status"] = COVERAGE_MISSING_CANDLES
            res["pit_status"], res["pit_qualified"], res["pit_reasons"] = \
                self._qualify(reads, coverage=res["coverage_status"])
            return self._finish(res, store, reads, symbol, d)

        bar = c.iloc[-1]
        if (d - int(bar["open_time"])) > STALE_CANDLE_MS:
            # 原阈值：最近一根距今超过 2 小时 ⇒ 视为缺数据（阈值未改）
            reads[SOURCE_CANDLES].state = "missing"
            reads[SOURCE_CANDLES].reason = "candle_stale_gt_2h"
            res["has_c1h"] = False
            res["coverage_status"] = COVERAGE_MISSING_CANDLES
            res["pit_status"], res["pit_qualified"], res["pit_reasons"] = \
                self._qualify(reads, coverage=res["coverage_status"])
            return self._finish(res, store, reads, symbol, d)

        b_open = float(bar["open"])
        b_high = float(bar["high"])
        b_low = float(bar["low"])
        b_close = float(bar["close"])
        b_vol = float(bar["volume"])
        b_qvol = float(bar["quote_volume"])
        b_taker_buy_q = float(bar["taker_buy_quote_volume"])
        b_taker_buy_b = float(bar["taker_buy_volume"])

        b_range = b_high - b_low
        b_body = abs(b_close - b_open)
        b_upper_shadow = b_high - max(b_open, b_close)

        res.update({
            "bar_open": b_open, "bar_high": b_high, "bar_low": b_low,
            "bar_close": b_close, "bar_volume": b_vol,
            "bar_quote_volume": b_qvol,
            "bar_taker_buy_quote": b_taker_buy_q,
            "bar_taker_sell_quote": b_qvol - b_taker_buy_q,
            "bar_taker_buy_net_quote": 2.0 * b_taker_buy_q - b_qvol,
            "bar_taker_buy_ratio": (b_taker_buy_q / b_qvol) if b_qvol > 0 else None,
            "bar_upper_shadow_ratio": (b_upper_shadow / (b_range + 1e-8)) if b_range > 0 else 0.0,
            "bar_body_ratio": (b_body / (b_range + 1e-8)) if b_range > 0 else 0.0,
            "bar_return": (b_close - b_open) / (b_open + 1e-8),
            "bar_open_time_ms": int(bar["open_time"]),
            "bar_available_at_ms": int(bar["available_at"]),
        })

        # 突破前 24h：只取已收盘的 24 根（原写法多算了 1 根未收盘的当根）
        win_24h = c[c["open_time"] >= (d - LOOKBACK_24H_MS)]
        if len(win_24h) > 0:
            mean_qvol = float(win_24h["quote_volume"].astype(float).mean())
            tot_qvol_24h = float(win_24h["quote_volume"].astype(float).sum())
            tot_tb_qvol_24h = float(win_24h["taker_buy_quote_volume"].astype(float).sum())
            tot_bvol_24h = float(win_24h["volume"].astype(float).sum())
            tot_tb_bvol_24h = float(win_24h["taker_buy_volume"].astype(float).sum())

            res["bar_volume_ratio_24h"] = (b_qvol / (mean_qvol + 1e-8)) if mean_qvol > 0 else 1.0
            res["quote_cvd_24h"] = 2.0 * tot_tb_qvol_24h - tot_qvol_24h
            res["base_cvd_24h"] = 2.0 * tot_tb_bvol_24h - tot_bvol_24h
            res["norm_cvd_24h"] = (res["quote_cvd_24h"] / (tot_qvol_24h + 1e-8)) if tot_qvol_24h > 0 else 0.0
            res["taker_buy_ratio_24h"] = (tot_tb_qvol_24h / (tot_qvol_24h + 1e-8)) if tot_qvol_24h > 0 else None
            res["candle_24h_bars"] = int(len(win_24h))
        else:
            res["bar_volume_ratio_24h"] = 1.0
            res["quote_cvd_24h"] = 0.0
            res["base_cvd_24h"] = 0.0
            res["norm_cvd_24h"] = 0.0
            res["taker_buy_ratio_24h"] = None
            res["candle_24h_bars"] = 0

        # ── 2. metrics（大户/账户多空比）──
        m_prev = None
        m = avail[SOURCE_METRICS]
        if m is not None:
            m_curr = m.iloc[-1]
            if (d - int(m_curr["ts"])) > STALE_METRICS_MS:
                reads[SOURCE_METRICS].state = "missing"
                reads[SOURCE_METRICS].reason = "metrics_stale_gt_2h"
                res["has_metrics"] = False
            else:
                res["has_metrics"] = True
                res["top_position_ls_ratio"] = _num(m_curr.get("sum_toptrader_long_short_ratio"))
                res["top_account_ls_ratio"] = _num(m_curr.get("count_toptrader_long_short_ratio"))
                res["global_account_ls_ratio"] = _num(m_curr.get("count_long_short_ratio"))
                res["taker_ls_vol_ratio"] = _num(m_curr.get("sum_taker_long_short_vol_ratio"))
                res["metrics_event_time_ms"] = int(m_curr["ts"])
                res["metrics_available_at_ms"] = int(m_curr["available_at"])

                # 24h 前 metrics：同样按 available_at 取
                m_prev = avail_prev(store, SOURCE_METRICS, frames[SOURCE_METRICS],
                                    d - LOOKBACK_24H_MS)
                if m_prev is not None and len(m_prev) > 0:
                    m_p = m_prev.iloc[-1]
                    if (d - LOOKBACK_24H_MS - int(m_p["ts"])) <= STALE_METRICS_MS:
                        res["delta_top_pos_ratio_24h"] = (
                            res["top_position_ls_ratio"] - _num(m_p.get("sum_toptrader_long_short_ratio"))
                            if res["top_position_ls_ratio"] else None)
                    else:
                        res["delta_top_pos_ratio_24h"] = None
                else:
                    res["delta_top_pos_ratio_24h"] = None
        else:
            res["has_metrics"] = False

        # ── 3. OI：优先 metrics 官方字段，其次 oi parquet ──
        if res["has_metrics"]:
            res["has_oi"] = True
            res["oi_base"] = _num(m_curr.get("sum_open_interest"))
            res["oi_quote"] = _num(m_curr.get("sum_open_interest_value"))
            if m_prev is not None and len(m_prev) > 0:
                oi_p = m_prev.iloc[-1]
                p_base = _num(oi_p.get("sum_open_interest"))
                p_quote = _num(oi_p.get("sum_open_interest_value"))
                res["delta_oi_base_24h"] = (
                    (res["oi_base"] - p_base) / (p_base + 1e-8)
                    if (res["oi_base"] is not None and p_base) else None)
                res["delta_oi_quote_24h"] = (
                    (res["oi_quote"] - p_quote) / (p_quote + 1e-8)
                    if (res["oi_quote"] is not None and p_quote) else None)
            else:
                res["delta_oi_base_24h"] = None
                res["delta_oi_quote_24h"] = None
            res["oi_source_used"] = SOURCE_METRICS
        else:
            o = avail[SOURCE_OI]
            if o is not None:
                oi_curr = o.iloc[-1]
                if (d - int(oi_curr["ts"])) <= STALE_METRICS_MS:
                    res["has_oi"] = True
                    res["oi_base"] = _num(oi_curr.get("open_interest"))
                    res["oi_quote"] = _num(oi_curr.get("open_interest_value"))
                    res["oi_event_time_ms"] = int(oi_curr["ts"])
                    res["oi_available_at_ms"] = int(oi_curr["available_at"])
                    o_prev = avail_prev(store, SOURCE_OI, frames[SOURCE_OI],
                                        d - LOOKBACK_24H_MS)
                    if o_prev is not None and len(o_prev) > 0:
                        op = o_prev.iloc[-1]
                        p_b = _num(op.get("open_interest"))
                        p_q = _num(op.get("open_interest_value"))
                        res["delta_oi_base_24h"] = (
                            (res["oi_base"] - p_b) / (p_b + 1e-8)
                            if (res["oi_base"] is not None and p_b) else None)
                        res["delta_oi_quote_24h"] = (
                            (res["oi_quote"] - p_q) / (p_q + 1e-8)
                            if (res["oi_quote"] is not None and p_q) else None)
                    else:
                        res["delta_oi_base_24h"] = None
                        res["delta_oi_quote_24h"] = None
                    res["oi_source_used"] = SOURCE_OI
                else:
                    reads[SOURCE_OI].state = "missing"
                    reads[SOURCE_OI].reason = "oi_stale_gt_2h"
                    res["has_oi"] = False
            else:
                res["has_oi"] = False

        # ── 4. coverage（数据齐不齐，与时点证据无关）──
        res["coverage_status"] = self._coverage(res)

        # ── 5. PIT 资格（时点有没有证据，与数据齐不齐无关）──
        res["pit_status"], res["pit_qualified"], res["pit_reasons"] = \
            self._qualify(reads, coverage=res["coverage_status"])

        # ── 6. 四大过程假设 + 四组准入（阈值原样，一字未改）──
        self._classify(res)

        return self._finish(res, store, reads, symbol, d)

    # ── 内部：取数 ──
    def _read(self, store: PitStore, name: str, frame: Optional[pd.DataFrame],
              d: int, rules: Dict[str, Any]):
        total = 0 if frame is None else int(len(frame))
        rule = rules[name]
        if total == 0:
            return None, SourceRead(name=name, state="missing", rows_total=0,
                                    rule_status=rule.status.value,
                                    reason=f"{name}:no_rows")
        try:
            out = store.as_of(name, d)
        except PitUnavailable as exc:
            return None, SourceRead(name=name, state="refused", rows_total=total,
                                    rule_status=rule.status.value,
                                    reason=f"{name}:{exc.reason}")
        if out.empty:
            return None, SourceRead(name=name, state="missing", rows_total=total,
                                    rule_status=rule.status.value,
                                    reason=f"{name}:no_available_observation_before_decision")
        guard_proven = bool(store.is_proven(name))
        # 本层资格口径：只有"交易所闭合"算可证。
        # declared_lag 是人填的延迟，不是交易所给的发布时刻 —— 它只推迟取数时刻，
        # 不提升资格等级，否则随手填个 lag 就能把未证明的源洗白。
        proven = rule.status is AvailabilityStatus.EXCHANGE_CLOSE
        assumed = not proven
        last = out.iloc[-1]
        avail_at = int(last["available_at"])
        event_at = int(last["ts"] if "ts" in out.columns else last["open_time"])
        if avail_at > d:      # 兜底：as_of 已保证，这里再挡一次
            raise PitViolation(
                f"{name} 取到 available_at={avail_at} > decision_time={d}")
        if proven:
            reason = "ok"
        elif rule.status is AvailabilityStatus.DECLARED_LAG:
            reason = f"{name}:declared_lag_not_qualifying:{self._lag(name)}ms"
        else:
            reason = f"{name}:assumed_bucket_end_as_available"
        return out, SourceRead(
            name=name, state="used", proven=proven, guard_proven=guard_proven,
            rule_status=rule.status.value, assumed=assumed,
            rows_total=total, rows_used=int(len(out)),
            event_time_ms=event_at, available_at_ms=avail_at,
            age_ms=int(d - avail_at), reason=reason)

    def _lag(self, name: str) -> int:
        return int(self.metrics_declared_lag_ms if name == SOURCE_METRICS
                   else (self.oi_declared_lag_ms or 0))

    # ── 内部：覆盖 ──
    @staticmethod
    def _coverage(res: dict) -> str:
        c1h, m, o = bool(res.get("has_c1h")), bool(res.get("has_metrics")), bool(res.get("has_oi"))
        if c1h and m and o:
            return COVERAGE_COMPLETE
        if not c1h:
            return COVERAGE_MISSING_CANDLES
        if not m:
            return COVERAGE_MISSING_METRICS
        if not o:
            return COVERAGE_MISSING_OI
        return COVERAGE_PARTIAL

    # ── 内部：资格 ──
    @staticmethod
    def _qualify(reads: Dict[str, SourceRead], *, coverage: str):
        """PIT 资格只看时点证据，不看数据齐不齐；两者分开报。

        口径：只要**某个数据源存在、但它本身没有可用时点证据**，本次就不能称
        `PROVEN` —— 哪怕这一刻恰好没从它取到行（取不到是 coverage 的事，
        能不能证是 PIT 的事，两件事不能互相抵消）。
        """
        reasons: List[str] = []
        refused = [r for r in reads.values() if r.state == "refused"]
        assumed_used = [r for r in reads.values() if r.state == "used" and r.assumed]
        unproven_present = [r for r in reads.values()
                            if r.rows_total > 0 and not r.proven]

        if refused:
            for r in refused:
                reasons.append(f"{r.name}:refused:{_suffix(r.reason)}")
            return PIT_REFUSED, False, reasons

        if assumed_used:
            for r in assumed_used:
                reasons.append(
                    f"{r.name}:{_suffix(r.reason)}:按假设时刻取用"
                    f"（无发布时点证据，不得视为已证明）")
            return PIT_ASSUMED, False, reasons

        if unproven_present:
            for r in unproven_present:
                reasons.append(
                    f"{r.name}:unproven_available_at:该源无发布时点证据"
                    f"（本次决策时刻前未取到可用行，但源头不可证）")
            return PIT_ASSUMED, False, reasons

        if coverage != COVERAGE_COMPLETE:
            reasons.append(f"coverage_{coverage.lower()}:数据有洞，无法判定完整性")
            return PIT_INCOMPLETE, False, reasons

        return PIT_PROVEN, True, reasons

    # ── 内部：分类（原样搬运）──
    @staticmethod
    def _classify(res: dict) -> None:
        tbr_24h = res.get("taker_buy_ratio_24h")
        doi_b = res.get("delta_oi_base_24h")
        tbr_bar = res.get("bar_taker_buy_ratio")
        vol_r = res.get("bar_volume_ratio_24h", 1.0)
        up_sh = res.get("bar_upper_shadow_ratio", 0.0)
        body_r = res.get("bar_body_ratio", 0.0)

        is_hyp_a = bool(tbr_24h is not None and doi_b is not None
                        and tbr_24h < 0.48 and doi_b > 0.0)
        res["is_hyp_a_absorption"] = is_hyp_a

        is_hyp_b = bool(tbr_bar is not None and doi_b is not None
                        and tbr_bar > 0.52 and doi_b > 0.02 and body_r > 0.4)
        res["is_hyp_b_healthy_drive"] = is_hyp_b

        is_hyp_c = bool(tbr_bar is not None and doi_b is not None
                        and tbr_bar > 0.50 and vol_r >= 2.0
                        and up_sh >= 0.45 and body_r < 0.35)
        res["is_hyp_c_stall_distribution"] = is_hyp_c

        is_hyp_d = bool(doi_b is not None and doi_b <= -0.05)
        res["is_hyp_d_short_squeeze_exhaustion"] = is_hyp_d

        res["pass_g1"] = True

        pass_g2 = True
        g2_reasons: List[str] = []
        if tbr_bar is not None and tbr_bar < 0.45:
            pass_g2 = False
            g2_reasons.append("LOW_TAKER_BUY_RATIO")
        if vol_r >= 2.0 and up_sh >= 0.50 and body_r < 0.30:
            pass_g2 = False
            g2_reasons.append("STALL_HIGH_VOLUME_SPIKE")
        res["pass_g2"] = pass_g2
        res["g2_reject_reason"] = "|".join(g2_reasons) if g2_reasons else "PASS"

        pass_g3 = pass_g2
        g3_reasons = list(g2_reasons)
        if pass_g2 and doi_b is not None and doi_b <= -0.05:
            pass_g3 = False
            g3_reasons.append("OI_EXHAUSTION_DROP")
        res["pass_g3"] = pass_g3
        res["g3_reject_reason"] = "|".join(g3_reasons) if g3_reasons else "PASS"

        pass_g4 = pass_g3
        g4_reasons = list(g3_reasons)
        if pass_g3:
            g_acc = res.get("global_account_ls_ratio")
            t_pos = res.get("top_position_ls_ratio")
            if g_acc is not None and t_pos is not None:
                if g_acc > 1.8 and t_pos < 0.9:
                    pass_g4 = False
                    g4_reasons.append("RETAIL_EUPHORIA_WHALE_SHORT")
        res["pass_g4"] = pass_g4
        res["g4_reject_reason"] = "|".join(g4_reasons) if g4_reasons else "PASS"

    # ── 内部：收尾（审计 + 时钟 + 资格字段）──
    def _finish(self, res: dict, store: PitStore,
                reads: Dict[str, SourceRead], symbol: str, d: int) -> dict:
        # 决策时钟锚在 1h bar 上（不是 5m 桶）：event=bar 起点，close=起点+1h-1ms
        event_ms = reads[SOURCE_CANDLES].event_time_ms

        clock = None
        if event_ms is not None:
            try:
                clock = DecisionClock(
                    event_time_ms=int(event_ms),
                    close_time_ms=int(event_ms) + self.candle_bar_ms - 1,
                    decision_time_ms=int(d),
                    source="candidate.snapshot_time_ms",
                )
            except PitViolation:
                clock = None

        res["pit_sources"] = {n: r.identity() for n, r in reads.items()}
        res["decision_clock"] = (clock.identity() if clock else {
            "event_time_ms": None,
            "close_time_ms": None,
            "decision_time_ms": int(d),
            "source": "candidate.snapshot_time_ms",
        })
        res["decision_clock_rule"] = (
            "decision_time = 候选快照时刻；close_time = 所用 1h bar 起点 + 1h - 1ms；"
            "available_at <= decision_time 才可读")
        audit = store.audit()
        # 两层资格分开写清楚，避免"两个 qualified 打架"：
        #   guard_level_qualified = 只看来源规则的证据等级（pit_guard 原判）
        #   qualified             = 本次实际生效的资格（再叠加消费端收紧）
        audit["guard_level_qualified"] = bool(audit.pop("qualified", False))
        audit["qualified"] = bool(res.get("pit_qualified", False))
        audit["consumer_rules"] = {
            "qualification_requires": "exchange_close",
            "declared_lag_qualifies": False,
            "note": ("声明延迟只推迟取数时刻，不提升资格等级；"
                     "metrics/OI 没有发布时点证据 ⇒ FLOW-OI 特征向量不可能取得正式资格"),
        }
        res["pit_audit"] = audit
        res["pit_audit_sha256"] = _sha256(audit)

        # 正式资格 = PIT 可证 AND 数据齐全。两者缺一即 False，且理由分开写。
        coverage = res.get("coverage_status", COVERAGE_PARTIAL)
        res["pit_qualified"] = bool(res.get("pit_qualified", False))
        res["formal_eligible"] = bool(res["pit_qualified"] and coverage == COVERAGE_COMPLETE)
        res["formal_block_reasons"] = self._block_reasons(res, coverage)
        res["symbol"] = symbol
        return res

    @staticmethod
    def _block_reasons(res: dict, coverage: str) -> List[str]:
        out: List[str] = []
        if not res.get("pit_qualified", False):
            out.extend(res.get("pit_reasons") or ["pit_qualified_false"])
        if coverage != COVERAGE_COMPLETE:
            out.append(f"coverage_{coverage.lower()}")
        return out


# ══════════════════════════ 辅助 ══════════════════════════


def _validated_lag(lag_ms: Optional[int], source: str) -> Optional[int]:
    if lag_ms is None:
        return None
    v = int(lag_ms)
    if v <= 0:
        raise ValueError(
            f"{source} 的 declared_lag_ms 必须是正整数（收到 {v}）。"
            f"0 或负数不是发布时点证据，等价于把桶结束当发布时间。")
    return v


def _suffix(reason: str) -> str:
    """把 `源名:原因` 里的原因部分取出来（没有冒号就原样返回）。"""
    return reason.split(":", 1)[-1] if reason else "unknown"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _prepare_candles(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None
    out = df.copy()
    if "is_closed" in out.columns:
        # 未收盘的 bar 直接剔除（不得靠时间戳"碰巧"排除）
        closed = pd.to_numeric(out["is_closed"], errors="coerce")
        out = out[closed == 1]
    if "open_time" not in out.columns:
        return None
    out = out.sort_values("open_time").drop_duplicates(subset=["open_time"])
    return out.reset_index(drop=True)


def _prepare_buckets(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None
    if "ts" not in df.columns:
        return None
    out = df.copy().sort_values("ts").drop_duplicates(subset=["ts"])
    return out.reset_index(drop=True)


def avail_prev(store: PitStore, name: str, frame: Optional[pd.DataFrame],
               at_ms: int) -> Optional[pd.DataFrame]:
    """取 `at_ms` 时点可用的帧（24h 前对照点）。取不到就 None，不外推。"""
    if frame is None or len(frame) == 0:
        return None
    try:
        out = store.as_of(name, int(at_ms))
    except PitUnavailable:
        return None
    return None if out.empty else out


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        .encode("utf-8")).hexdigest()


def summarize_pit(rows: List[dict]) -> dict:
    """把逐候选 PIT 字段汇总成实验入口需要的一份资格报告。"""
    n = len(rows)
    if n == 0:
        return {"candidates": 0}
    coverage: Dict[str, int] = {}
    pit: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    for r in rows:
        cov = str(r.get("coverage_status"))
        coverage[cov] = coverage.get(cov, 0) + 1
        st = str(r.get("pit_status"))
        pit[st] = pit.get(st, 0) + 1
        for x in (r.get("formal_block_reasons") or []):
            key = x.split(":", 1)[0]
            reasons[key] = reasons.get(key, 0) + 1
    return {
        "candidates": n,
        "coverage_status_counts": coverage,
        "pit_status_counts": pit,
        "pit_qualified_count": sum(1 for r in rows if r.get("pit_qualified")),
        "formal_eligible_count": sum(1 for r in rows if r.get("formal_eligible")),
        "formal_block_reason_counts": reasons,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "flow_pit_version": FLOW_PIT_VERSION,
        "pit_guard_version": PIT_GUARD_VERSION,
        "formal_eligible": all(bool(r.get("formal_eligible")) for r in rows),
    }


__all__ = [
    "FlowPitFeatureExtractor", "summarize_pit",
    "FLOW_PIT_VERSION", "FEATURE_CONTRACT_VERSION",
    "SOURCE_CANDLES", "SOURCE_METRICS", "SOURCE_OI",
    "COVERAGE_COMPLETE", "COVERAGE_PARTIAL", "COVERAGE_MISSING_CANDLES",
    "COVERAGE_MISSING_METRICS", "COVERAGE_MISSING_OI",
    "PIT_PROVEN", "PIT_ASSUMED", "PIT_REFUSED", "PIT_INCOMPLETE",
]
