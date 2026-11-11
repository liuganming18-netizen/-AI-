# -*- coding: utf-8 -*-
"""框架级 PIT（Point-in-Time）守卫 —— FRAME-PIT-01。

要解决的问题
────────────
框架此前**没有任何强制的可得性检查**：
  · 状态机按 `open_time + 3600s` 自己算收盘，直接读传入的整段数组；
  · OI 取值用 `ts <= decision_time`（`ts` 是 5 分钟桶的**起点**）
    ⇒ 恰好落在决策时刻的那个桶（覆盖决策之后 5 分钟）会被当成"已知"；
  · metrics / 因子脚本用 `open_time <= ts` ⇒ 直接读到还没收盘的 K 线。
这些都不是"策略作者不小心"，而是**数据访问层没提供受控入口**，谁都能直接 `read_parquet`。

本模块的立场
────────────
1. 四个时刻必须分开，不得互相冒充：

   | 名称 | 含义 | 谁定义 |
   |---|---|---|
   | `event_time`   | 数据点所属区间的**起点** | 交易所（K 线 open_time、统计桶 ts） |
   | `close_time`   | 区间的**最后一毫秒** | 交易所（K 线 close_time = open + 周期 − 1ms） |
   | `available_at` | 这个值**最早能被读到**的时刻 | 本模块按来源规则推导，必须有证据 |
   | `decision_time`| 策略做决定的时刻 | 调用方；必须 ≥ 所用数据的 `available_at` |

2. **禁止**用 `open_time <= decision_time` 判可用（那只说明区间开始了，不说明区间结束了）。
3. **禁止**把"桶结束"或"机械平移 5 分钟"当成**已证明**的发布时间：
   桶结束只能作为"假设"，一旦采用，本次运行就必须标记为**不具备正式资格**。
4. 无法证明可用时点的数据源，只有两条路：
   `strict` 模式**显式拒用**；`declared_assumption` 模式**照用但判不合格**。
   两条路都必须把原因写进审计记录，不允许静默填默认值。

防护边界（必须如实说明）
────────────────────────
本模块只能约束**经过它**的读取。同进程代码仍可直接 `pd.read_parquet(...)` 绕过。
因此它提供的是"受控入口 + 违规可检测"，**不是**进程级隔离，不得宣称已完全隔离。

纯逻辑：不读库、不联网、不下单。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PIT_GUARD_VERSION = "pit_guard_v1.0.0"

# 判定为「可用时点有证据」的状态集合
PROVEN_STATUSES = ("exchange_close", "declared_lag", "settlement_event",
                   "observed_fetch")


class PitMode(str, Enum):
    """未证明数据源的处理方式。"""

    STRICT = "strict"                     # 显式拒用
    DECLARED = "declared_assumption"      # 照用，但本次运行不具备正式资格


class AvailabilityStatus(str, Enum):
    """一个数据源的可得性证据等级。

    `OBSERVED_FETCH`（ACCUM-EARLY-02 新增）
    ---------------------------------------
    「逐行实测抓取时刻」：每一行都有一条**真实发生过的抓取记录**（网关收到响应体的
    时刻），该时刻就是这行数据最早能被读到的时刻。它比 `DECLARED_LAG`（人工声明的
    统一延迟）强：不是按规则平移，而是逐行有据。
    本状态**不自己算**可得时刻（`SourceRule.available_at` 返回全 NaN），
    由调用方通过 `PitBridge.register_oi(..., available_at=...)` 逐行传入；
    传不出来的行必须给 `NaN`（下游自动排除），**不允许回退成桶结束**。
    ⚠ 未改 `PIT_GUARD_VERSION`：已有审计指纹绑定该版本号，且新增成员不改变
      任何既有分支的行为（纯追加）。
    """

    EXCHANGE_CLOSE = "exchange_close"     # K 线：交易所定义的闭合时刻即可得
    DECLARED_LAG = "declared_lag"         # 统计桶：桶结束 + 显式声明的发布延迟
    SETTLEMENT = "settlement_event"       # 结算事件：事件时刻即可得（仅用于事后记账）
    OBSERVED_FETCH = "observed_fetch"     # 逐行实测抓取时刻（调用方提供）
    UNPROVEN = "unproven"                 # 无任何可用时点证据


class PitUnavailable(RuntimeError):
    """数据在 decision_time 不可用 —— 调用方必须显式拒用，不得回退默认值。"""

    def __init__(self, source: str, decision_time_ms: int, reason: str):
        self.source = source
        self.decision_time_ms = int(decision_time_ms)
        self.reason = reason
        super().__init__(f"PIT_UNAVAILABLE source={source} decision_time={decision_time_ms} reason={reason}")


class PitViolation(RuntimeError):
    """试图把未来数据送入决策，或绕过守卫。"""


# ══════════════════════════ 决策时钟 ══════════════════════════


@dataclass(frozen=True)
class DecisionClock:
    """一次决策的四个时刻。构造后不可变，避免"跑着跑着把 decision_time 改小"。"""

    event_time_ms: int
    close_time_ms: int
    decision_time_ms: int
    source: str = ""

    def __post_init__(self) -> None:
        if self.close_time_ms < self.event_time_ms:
            raise PitViolation(
                f"close_time({self.close_time_ms}) < event_time({self.event_time_ms})")
        if self.decision_time_ms < self.close_time_ms + 1:
            raise PitViolation(
                f"decision_time({self.decision_time_ms}) 必须 >= close_time+1ms"
                f"({self.close_time_ms + 1})：区间未结束不能决策")

    @classmethod
    def for_bar(cls, open_time_ms: int, bar_ms: int, source: str = "") -> "DecisionClock":
        """按「区间起点 + 周期」构造：close = open + 周期 − 1ms，decision = open + 周期。"""
        o = int(open_time_ms)
        b = int(bar_ms)
        return cls(event_time_ms=o, close_time_ms=o + b - 1,
                   decision_time_ms=o + b, source=source)

    def identity(self) -> dict:
        return {"event_time_ms": self.event_time_ms,
                "close_time_ms": self.close_time_ms,
                "decision_time_ms": self.decision_time_ms,
                "source": self.source}


# ══════════════════════════ 来源规则 ══════════════════════════


@dataclass(frozen=True)
class SourceRule:
    """一个数据源的可用性规则。`evidence` 必须写清"凭什么认为它那时可得"。"""

    name: str
    event_col: str
    status: AvailabilityStatus
    close_col: Optional[str] = None
    bucket_ms: Optional[int] = None
    declared_lag_ms: Optional[int] = None
    evidence: str = ""
    note: str = ""

    def available_at(self, frame: pd.DataFrame) -> np.ndarray:
        """按规则算每行的 `available_at`（毫秒）。无证据时整列 NaN，不猜。

        `OBSERVED_FETCH` 一律返回 NaN：可得时刻由调用方逐行提供，
        本方法不替它编一个（编出来就等于把假设冒充成证据）。
        """
        n = len(frame)
        out = np.full(n, np.nan, dtype=np.float64)
        if n == 0 or self.status in (AvailabilityStatus.UNPROVEN,
                                     AvailabilityStatus.OBSERVED_FETCH):
            return out

        if self.status is AvailabilityStatus.EXCHANGE_CLOSE:
            if self.close_col and self.close_col in frame.columns:
                v = pd.to_numeric(frame[self.close_col], errors="coerce").to_numpy(np.float64)
                # close_time 是区间最后一毫秒 ⇒ 值在 close_time+1ms 才最终确定
                return v + 1.0
            if self.event_col in frame.columns and self.bucket_ms:
                v = pd.to_numeric(frame[self.event_col], errors="coerce").to_numpy(np.float64)
                return v + float(self.bucket_ms)
            return out

        if self.status is AvailabilityStatus.DECLARED_LAG:
            if self.event_col not in frame.columns or not self.bucket_ms:
                return out
            lag = float(self.declared_lag_ms or 0)
            v = pd.to_numeric(frame[self.event_col], errors="coerce").to_numpy(np.float64)
            return v + float(self.bucket_ms) + lag

        if self.status is AvailabilityStatus.SETTLEMENT:
            if self.event_col not in frame.columns:
                return out
            return pd.to_numeric(frame[self.event_col], errors="coerce").to_numpy(np.float64)

        return out

    def identity(self) -> dict:
        return {"name": self.name,
                "event_col": self.event_col,
                "close_col": self.close_col,
                "bucket_ms": self.bucket_ms,
                "status": self.status.value,
                "declared_lag_ms": self.declared_lag_ms,
                "evidence": self.evidence,
                "note": self.note}


@dataclass
class PitSource:
    """一个受控数据源：帧 + 规则 + 逐行可得时刻。"""

    rule: SourceRule
    frame: pd.DataFrame
    event_time: np.ndarray = field(init=False)
    available_at: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        f = self.frame
        self.event_time = (pd.to_numeric(f[self.rule.event_col], errors="coerce")
                           .to_numpy(np.float64)
                           if self.rule.event_col in f.columns
                           else np.full(len(f), np.nan))
        self.available_at = self.rule.available_at(f)

    @property
    def name(self) -> str:
        return self.rule.name

    @property
    def proven(self) -> bool:
        return self.rule.status.value in PROVEN_STATUSES

    def summary(self) -> dict:
        avail = self.available_at[np.isfinite(self.available_at)]
        return {
            **self.rule.identity(),
            "rows": int(len(self.frame)),
            "proven": bool(self.proven),
            "event_time_min": (int(np.nanmin(self.event_time))
                               if np.isfinite(self.event_time).any() else None),
            "event_time_max": (int(np.nanmax(self.event_time))
                               if np.isfinite(self.event_time).any() else None),
            "available_at_rows": int(avail.size),
            "available_at_min": int(avail.min()) if avail.size else None,
            "available_at_max": int(avail.max()) if avail.size else None,
        }


# ══════════════════════════ 帧级校验 ══════════════════════════


def assert_frame_available(frame: pd.DataFrame, decision_time_ms: int,
                           available_col: str = "available_at",
                           label: str = "") -> int:
    """帧里只要有一行 `available_at > decision_time` 就抛 `PitViolation`。

    返回可用行数。缺 `available_col` 时直接抛 —— 没有可得时刻的帧不允许进决策。
    """
    if available_col not in frame.columns:
        raise PitViolation(
            f"帧缺少 `{available_col}` 列，无法证明可用性（{label or '未命名'}）")
    avail = pd.to_numeric(frame[available_col], errors="coerce").to_numpy(np.float64)
    if np.isnan(avail).any():
        raise PitViolation(
            f"帧存在 {int(np.isnan(avail).sum())} 行没有可用时刻（{label or '未命名'}）")
    future = avail > float(decision_time_ms)
    if future.any():
        raise PitViolation(
            f"帧含 {int(future.sum())} 行未来数据，最早越界行 available_at="
            f"{int(avail[future].min())} > decision_time={int(decision_time_ms)}"
            f"（{label or '未命名'}）")
    return int(len(frame))


# ══════════════════════════ 守卫本体 ══════════════════════════


class PitStore:
    """受控数据仓库：所有决策输入都必须从这里取，取不到就显式拒用。"""

    def __init__(self, mode: Any = PitMode.DECLARED, symbol: str = ""):
        self.mode = PitMode(mode) if not isinstance(mode, PitMode) else mode
        self.symbol = symbol
        self.sources: Dict[str, PitSource] = {}
        self.refusals: List[dict] = []
        self.assumptions: List[dict] = []

    # ── 装配 ──
    def add(self, name: str, frame: pd.DataFrame, rule: SourceRule) -> PitSource:
        if name != rule.name:
            raise PitViolation(f"源名不一致：add({name}) vs rule({rule.name})")
        src = PitSource(rule=rule, frame=frame)
        self.sources[name] = src
        if not src.proven:
            self.assumptions.append({
                "source": name,
                "status": rule.status.value,
                "mode": self.mode.value,
                "note": rule.note,
            })
        return src

    def source(self, name: str) -> PitSource:
        if name not in self.sources:
            raise PitViolation(f"未注册的数据源：{name}")
        return self.sources[name]

    def is_proven(self, name: str) -> bool:
        return self.source(name).proven

    # ── 读取 ──
    def as_of(self, name: str, decision_time_ms: int, *,
              raise_on_unproven: bool = False) -> pd.DataFrame:
        """返回 `available_at <= decision_time` 的行。未证明源在 strict 下直接拒用。"""
        src = self.source(name)
        d = int(decision_time_ms)
        if not src.proven:
            if self.mode is PitMode.STRICT or raise_on_unproven:
                self.note_refusal(name, d, f"unproven_source:{src.rule.status.value}")
                raise PitUnavailable(name, d, f"unproven_source:{src.rule.status.value}")
            self._note_assumption(name, d)
            avail = self._assumed_availability(src)
        else:
            avail = src.available_at
        keep = np.isfinite(avail) & (avail <= float(d))
        out = src.frame.loc[keep].copy()
        out["available_at"] = avail[keep]
        return out.reset_index(drop=True)

    def value_as_of(self, name: str, decision_time_ms: int, value_col: str, *,
                    max_staleness_ms: Optional[int] = None,
                    raise_on_unproven: bool = False) -> dict:
        """取 `decision_time` 时点已可得的最新值（as-of，不回填、不外推）。

        返回 dict：`value` / `event_time_ms` / `available_at_ms` / `age_ms` /
        `status` / `assumed` / `reason`。取不到时 `value=None` 且 `reason` 写明原因。
        """
        src = self.source(name)
        d = int(decision_time_ms)
        if value_col not in src.frame.columns:
            self.note_refusal(name, d, f"value_col_missing:{value_col}")
            return self._miss(name, d, f"value_col_missing:{value_col}")

        if not src.proven:
            if self.mode is PitMode.STRICT or raise_on_unproven:
                self.note_refusal(name, d, f"unproven_source:{src.rule.status.value}")
                return self._miss(name, d, f"unproven_source:{src.rule.status.value}")
            self._note_assumption(name, d)
            avail = self._assumed_availability(src)
            assumed = True
        else:
            avail = src.available_at
            assumed = False

        ok = np.isfinite(avail) & (avail <= float(d))
        if not ok.any():
            self.note_refusal(name, d, "no_available_observation_before_decision")
            return self._miss(name, d, "no_available_observation_before_decision",
                              assumed=assumed)

        idx = int(np.flatnonzero(ok)[-1])   # 已按 event_time 升序 ⇒ 最后一个即最新
        a = float(avail[idx])
        age = d - a
        if max_staleness_ms is not None and age > float(max_staleness_ms):
            self.note_refusal(name, d, f"stale_{int(age)}ms_gt_{int(max_staleness_ms)}ms")
            return self._miss(name, d, f"stale_{int(age)}ms_gt_{int(max_staleness_ms)}ms",
                              assumed=assumed, age=age)
        val = src.frame[value_col].iloc[idx]
        try:
            val = float(val)
        except (TypeError, ValueError):
            self.note_refusal(name, d, "value_not_numeric")
            return self._miss(name, d, "value_not_numeric", assumed=assumed)
        if not np.isfinite(val):
            self.note_refusal(name, d, "value_not_finite")
            return self._miss(name, d, "value_not_finite", assumed=assumed)
        return {"value": val,
                "event_time_ms": int(src.event_time[idx]),
                "available_at_ms": int(a),
                "age_ms": int(age),
                "status": src.rule.status.value,
                "assumed": assumed,
                "reason": "ok_assumed" if assumed else "ok"}

    # ── 记录 ──
    def note_refusal(self, source: str, decision_time_ms: int, reason: str) -> None:
        self.refusals.append({"source": source,
                              "decision_time_ms": int(decision_time_ms),
                              "reason": reason})

    def _note_assumption(self, name: str, d: int) -> None:
        self.refusals.append({"source": name,
                              "decision_time_ms": int(d),
                              "reason": "assumed_bucket_end_as_available"})

    def _assumed_availability(self, src: PitSource) -> np.ndarray:
        """`declared_assumption` 模式下，用「区间结束 + 声明延迟（可为 0）」当作假设时刻。"""
        rule = src.rule
        base = src.event_time + float(rule.bucket_ms or 0) + float(rule.declared_lag_ms or 0)
        return base

    @staticmethod
    def _miss(name: str, d: int, reason: str, *, assumed: bool = False,
              age: Optional[float] = None) -> dict:
        return {"value": None, "event_time_ms": None, "available_at_ms": None,
                "age_ms": (int(age) if age is not None else None),
                "status": "unavailable", "assumed": assumed, "reason": reason}

    # ── 资格与审计 ──
    @property
    def unproven_sources(self) -> List[str]:
        return [n for n, s in self.sources.items() if not s.proven]

    @property
    def qualified(self) -> bool:
        """正式资格：所有源都有可用时点证据，且没有走过任何假设路径。"""
        return (not self.unproven_sources) and (not self.assumptions)

    def audit(self) -> dict:
        return {
            "pit_guard_version": PIT_GUARD_VERSION,
            "symbol": self.symbol,
            "mode": self.mode.value,
            "qualified": bool(self.qualified),
            "unproven_sources": self.unproven_sources,
            "assumptions": list(self.assumptions),
            "refusal_count": len(self.refusals),
            "refusal_reason_counts": _count(self.refusals, "reason"),
            "sources": {n: s.summary() for n, s in self.sources.items()},
        }

    def audit_sha256(self) -> str:
        payload = json.dumps(self.audit(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _count(rows: Sequence[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        k = str(r.get(key, ""))
        out[k] = out.get(k, 0) + 1
    return out


# ══════════════════════════ 预置来源规则 ══════════════════════════
# 每条规则都要写清「凭什么」。写不出证据的，一律 UNPROVEN。


def rule_candles(timeframe: str, bar_ms: int) -> SourceRule:
    """K 线：交易所定义的 close_time 即区间最后一毫秒。

    证据：落盘字段 `is_closed=1` 且 `close_time` 存在；`available_at` 列 100% 为空
    ⇒ 只能证明「K 线在 close_time+1ms 已闭合」，**不能**证明我们那一刻就收到了它。
    因此状态记为 `exchange_close`，并在 note 里如实写「摄取延迟未证」。
    """
    return SourceRule(
        name=f"candles_{timeframe}",
        event_col="open_time",
        close_col="close_time",
        bucket_ms=bar_ms,
        status=AvailabilityStatus.EXCHANGE_CLOSE,
        evidence="parquet: candles/binance/um/<symbol>/%s/*.parquet 的 close_time 与 is_closed=1" % timeframe,
        note="交易所闭合时刻可用；本地摄取延迟未证（available_at 列全空）",
    )


def rule_bucket_metrics(name: str = "metrics_5m", bucket_ms: int = 300_000,
                        declared_lag_ms: Optional[int] = None,
                        evidence: str = "") -> SourceRule:
    """官方 metrics / OI 统计桶：`ts` 是桶**起点**，桶结束≠发布时刻。

    未声明发布延迟 ⇒ `unproven`。声明了才升级为 `declared_lag`。
    """
    if declared_lag_ms is None:
        return SourceRule(name=name, event_col="ts", bucket_ms=bucket_ms,
                          status=AvailabilityStatus.UNPROVEN,
                          evidence=evidence or "无发布时点证据（原始档案只有 create_time=桶起点）",
                          note="桶结束时间不得当作已证明的发布时间")
    return SourceRule(name=name, event_col="ts", bucket_ms=bucket_ms,
                      status=AvailabilityStatus.DECLARED_LAG,
                      declared_lag_ms=int(declared_lag_ms),
                      evidence=evidence or f"显式声明发布延迟 {int(declared_lag_ms)}ms",
                      note="延迟为人工声明，非交易所证明")


def rule_settlement(name: str = "funding") -> SourceRule:
    """资金费：结算事件时刻即可得（只用于结算**之后**的记账，不参与入场决策）。"""
    return SourceRule(name=name, event_col="funding_time",
                      status=AvailabilityStatus.SETTLEMENT,
                      evidence="parquet: funding/binance/um/<symbol>.parquet 的 funding_time + rate_type=settled",
                      note="仅用于结算后记账；不得用于入场决策")


__all__ = [
    "PIT_GUARD_VERSION", "PitMode", "AvailabilityStatus", "PROVEN_STATUSES",
    "PitUnavailable", "PitViolation", "DecisionClock", "SourceRule", "PitSource",
    "PitStore", "assert_frame_available",
    "rule_candles", "rule_bucket_metrics", "rule_settlement",
]
