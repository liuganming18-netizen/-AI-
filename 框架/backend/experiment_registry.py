#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-01 · 实验登记、配置冻结与数据分组使用规则。

设计前提（本模块**不声称**能保证「绝不过拟合」）：

  框架只能保证「同一份设计身份只被记录一次、改了就换身份、看过的数据不会再被
  当成没看过」。它无法阻止人反复试错；它只能让试错留下痕迹、让「未见留出」
  这个身份无法被重复使用。

三条硬机制：

  1. **append-only 事件流 + 哈希链**（`events.jsonl`）
     每条事件记录 `prev` 与自身 `hash`；`verify()` 重算全链。删改任何一条
     中间事件都会断链。末尾被截断由 `head.json` 的 `count/last_hash` 发现。

  2. **设计身份（design_identity）**由代码/参数/规则/样本范围/分组规则/评价指标
     六项派生。任一项变更 ⇒ 新身份 ⇒ 不能继承旧的「未见留出验证」资格。
     数据指纹在运行后才确定，单独派生 `run_identity`，不参与冻结。

  3. **真实入口两级闸门**（`guard_create` / `guard_execute`）
     不是只给登记工具做自测：`backtest_api.create_run` 与
     `backtest_runner.execute_run` 都必须先过这里。

未配置即阻断：分组日期、隔离长度、解封方案未确定时，
`formal_eligible` 恒为 `False`，且原因逐条列出，不填默认值冒充已配置。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ══════════════════════════ 常量 ══════════════════════════

GENESIS = "0" * 64
EVENTS_FILENAME = "events.jsonl"
HEAD_FILENAME = "head.json"
DEFAULT_ROOT = Path(__file__).resolve().parent / "data" / "experiments"

#: 设计身份的组成项（顺序固定，进哈希）。任一项变化 ⇒ 新设计身份。
DESIGN_FIELDS = (
    "strategy_id",       # 策略标识
    "strategy_version",  # 策略版本
    "code_sha256",       # 代码版本（复用 inputs.json fingerprints.code.combined_sha256）
    "config_sha256",     # 参数（复用 fingerprints.config.sha256）
    "rules_sha256",      # 冻结规则（复用 fingerprints.rules.sha256）
    "sample_scope",      # 样本范围（标的清单 + 窗口）
    "split_policy",      # 分组规则（含隔离长度）
    "metrics_spec",      # 评价指标定义
)

#: 运行身份 = 设计身份 + 数据版本。数据版本跑完才知道，故不参与冻结。
RUN_FIELDS = ("design_identity", "data_sha256")

#: 事件类型。**只增不改**。
EV_REGISTER = "register"      # 登记实验（含设计身份）
EV_FREEZE = "freeze"          # 冻结设计身份
EV_RUN = "run"                # 绑定一次实际运行（含数据指纹）
EV_TRIAL = "trial"            # 试验结果（含失败/放弃，不只留赢家）
EV_ABANDON = "abandon"        # 明确放弃
EV_OBSERVE = "observe"        # 登记「已被查看/比较/用于调参」的样本集
EV_UNSEAL = "unseal"          # 解封最终留出
EV_INVALIDATE = "invalidate"  # 冻结失效（配置漂移）

EVENT_TYPES = (EV_REGISTER, EV_FREEZE, EV_RUN, EV_TRIAL, EV_ABANDON,
               EV_OBSERVE, EV_UNSEAL, EV_INVALIDATE)

#: 分组角色
ROLE_TRAIN = "train"
ROLE_VALID = "valid"
ROLE_HOLDOUT = "holdout"
ROLES = (ROLE_TRAIN, ROLE_VALID, ROLE_HOLDOUT)

#: 实验运行模式
MODE_RESEARCH = "research"  # 允许跑，但永不具备正式资格
MODE_FORMAL = "formal"      # 必须具备正式资格

#: 资格判定的两个阶段。**必须分开**（FRAME-FIT-02）：
#:
#:   `STAGE_LAUNCH`   启动许可 —— 「现在能不能开跑」。只看静态条件：配置冻结、
#:                    身份一致、分组已配置、样本未被污染、实验未放弃。
#:                    **不要求已有 runs / trials** —— 否则首个正式实验永远启动不了。
#:   `STAGE_DELIVERY` 结果交付资格 —— 「跑完的结果能不能当正式结论交付」。
#:                    在启动许可之上，再要求真的有运行记录、每次运行都有结论、
#:                    结论里有量化结果。
#:
#: 把这两件事混在一起（FRAME-FIT-01 的原实现）会造成死锁：
#: 启动要求「已有运行记录」，而运行记录只能在启动之后产生。
STAGE_LAUNCH = "launch"
STAGE_DELIVERY = "delivery"
STAGES = (STAGE_LAUNCH, STAGE_DELIVERY)

_EXP_ID_RE = re.compile(r"^exp_[A-Za-z0-9][A-Za-z0-9_.:-]{0,80}$")

_LOCK = threading.RLock()


# ══════════════════════════ 异常 ══════════════════════════

class RegistryTampered(RuntimeError):
    """事件流哈希链不成立 / 被截断 ⇒ 登记库不可信。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ExperimentBlocked(RuntimeError):
    """闸门拒绝。`reasons` 逐条给出，调用方必须原样透出，不得只报一句。"""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = [str(r) for r in reasons if str(r).strip()]
        super().__init__("；".join(self.reasons) or "实验被阻断")


class ExperimentNotFound(KeyError):
    pass


# ══════════════════════════ 基础工具 ══════════════════════════

def canonical_bytes(obj: Any) -> bytes:
    """确定性序列化：键排序、无空格、UTF-8。所有哈希都走这里。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def sha256_hex(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical_bytes(obj))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date_ms(s: Optional[str]) -> Optional[int]:
    """`YYYY-MM-DD` 或 ISO 时间 → UTC 毫秒。空值返回 `None`（**不填 0**）。"""
    if s is None:
        return None
    txt = str(s).strip()
    if not txt:
        return None
    try:
        if len(txt) == 10:
            dt = datetime.strptime(txt, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


# ══════════════════════════ 身份派生 ══════════════════════════

def normalize_sample_scope(scope: Optional[dict]) -> Optional[dict]:
    """样本范围规范化：标的去重升序、窗口统一。

    返回 `None` 表示**未给出**（不是空样本）。空样本与未给出必须分开。

    ⚠ 只保留 `symbols/window_start/window_end` 三项 —— 这三项才是「样本范围」。
    `source`（谁提供的）、`pinned_by_code`（是否被代码写死）属于**元数据**，
    不能进设计身份哈希，否则同一批样本只因来源字段不同就派生出两个身份。
    """
    if not scope:
        return None
    syms = scope.get("symbols")
    if syms is None:
        return None
    if isinstance(syms, str):
        syms = [syms]
    return {
        "symbols": sorted({str(s).strip().upper() for s in syms if str(s).strip()}),
        "window_start": scope.get("window_start"),
        "window_end": scope.get("window_end"),
    }


#: 执行成本契约的字段清单（进设计身份）。**唯一一份**——登记侧与创建侧必须用同一套，
#: 否则同一份配置会算出两个身份（见 `default_execution_contract` 注释）。
EXECUTION_CONTRACT_FIELDS = (
    "stoploss_s", "trail_d", "risk_per_trade_equity_fraction", "fee_each_side",
    "slippage_each_side", "entry_participation_previous_15m_quote_cap", "view_sha256",
)


def default_execution_contract() -> Optional[dict]:
    """从**已批准参数视图**取当前执行契约（费用/滑点/止损/跟踪/参与率）。

    为什么必须唯一：创建入口把契约并进设计身份，而登记入口（`current_design_parts`）
    原先不并 ⇒ 同一份配置算出两个身份，首个正式实验恒被判「身份漂移」。
    实测（FRAME-FIT-02 T01）：登记 `3b15359a…` / 创建时 `b5dc37c6…`，8 个用例全被
    409 挡在启动之前。修复方式不是放宽闸门，而是让两侧**取同一份契约**。

    取不到（配置不可读）⇒ `None`，闸门照旧按缺项拒绝，不猜默认值。
    """
    try:
        import paper_engine as _PE
        frozen = _PE.approved_params() or {}
        if not frozen.get("_config_valid"):
            return None
        contract = {k: frozen.get(k) for k in EXECUTION_CONTRACT_FIELDS}
        contract["rule"] = "FT-LOW4H"
        contract["initial_equity"] = frozen.get("initial_equity", 5000.0)
        return contract
    except Exception:                                     # noqa: BLE001
        return None


def compute_design_identity(parts: Dict[str, Any]) -> str:
    """由六项（+策略标识/版本）派生设计身份。缺项**不补默认值** ⇒ 记 `None` 进哈希。

    缺项会让身份带 `None`，与「给了值」区分开；同时 `missing_design_fields()`
    会把缺项列出来供闸门拒绝。
    """
    payload = {k: parts.get(k) for k in DESIGN_FIELDS}
    return sha256_hex(canonical_bytes(payload))


def missing_design_fields(parts: Dict[str, Any]) -> List[str]:
    out = []
    for k in DESIGN_FIELDS:
        v = parts.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(k)
    return out


def compute_run_identity(design_identity: str, data_sha256: Optional[str]) -> str:
    return sha256_hex(canonical_bytes({"design_identity": design_identity,
                                       "data_sha256": data_sha256}))


# ══════════════════════════ 标签跨度与隔离长度 ══════════════════════════

def label_span_hours(entry_ms: Optional[int], exit_ms: Optional[int]) -> Optional[float]:
    """单条样本的标签跨度（小时）。缺任一端 ⇒ `None`（**不当 0**）。"""
    if entry_ms is None or exit_ms is None:
        return None
    return (int(exit_ms) - int(entry_ms)) / 3_600_000.0


def required_isolation_hours(spans: Iterable[Optional[float]],
                             *, rounding: str = "ceil") -> Optional[int]:
    """由**实测标签跨度**推出最小隔离长度（小时）。

    规则：取实测最大跨度并向上取整到整小时。
    - 无有效跨度 ⇒ `None`（UNKNOWN，**不猜一个默认天数**）。
    - 向上取整而不是四舍五入：隔离宁可多不能少，少 1 小时就是泄漏。
    """
    vals = [float(s) for s in spans if s is not None and float(s) >= 0.0]
    if not vals:
        return None
    mx = max(vals)
    if rounding == "ceil":
        return int(-(-mx // 1))
    return int(round(mx))


# ══════════════════════════ 分组规则 ══════════════════════════

@dataclass
class SplitPolicy:
    """训练 / 验证 / 最终留出的时间顺序划分。

    `train/valid/holdout` 各为 `(start_date, end_date)`，闭区间（含首尾日）。
    未配置一律 `None` —— 闸门据此拒绝正式资格，**不填默认日期**。
    """

    train: Optional[Tuple[str, str]] = None
    valid: Optional[Tuple[str, str]] = None
    holdout: Optional[Tuple[str, str]] = None
    isolation_hours: Optional[int] = None
    label_span_source: Optional[str] = None   # 隔离长度的依据说明
    split_method: str = "chronological"
    note: str = ""

    # ── 校验 ──────────────────────────────────────────────
    def segments(self) -> List[Tuple[str, Tuple[str, str]]]:
        out = []
        for role, seg in ((ROLE_TRAIN, self.train), (ROLE_VALID, self.valid),
                          (ROLE_HOLDOUT, self.holdout)):
            if seg:
                out.append((role, seg))
        return out

    def validate(self) -> List[str]:
        """返回问题清单。空列表 = 配置完整且自洽。"""
        errs: List[str] = []
        for role, seg in self.segments():
            if not (isinstance(seg, (tuple, list)) and len(seg) == 2):
                errs.append(f"{role} 区间格式不合法（需 (起, 止)）：{seg!r}")
                continue
            s, e = parse_date_ms(seg[0]), parse_date_ms(seg[1])
            if s is None or e is None:
                errs.append(f"{role} 区间含无法解析的日期：{seg!r}")
            elif s > e:
                errs.append(f"{role} 区间起晚于止：{seg!r}")

        missing = [r for r, seg in ((ROLE_TRAIN, self.train), (ROLE_VALID, self.valid),
                                    (ROLE_HOLDOUT, self.holdout)) if not seg]
        if missing:
            errs.append("未配置分组：" + "、".join(missing))

        if self.split_method != "chronological":
            errs.append(f"分组方式 {self.split_method!r} 非时间顺序（本框架只接受 chronological）")

        if self.isolation_hours is None:
            errs.append("未配置隔离长度（isolation_hours）")
        elif self.isolation_hours < 0:
            errs.append(f"隔离长度为负：{self.isolation_hours}")

        # 段间必须按时间严格递增，且相邻段之间留出 ≥ 隔离长度
        ordered = sorted(self.segments(),
                         key=lambda kv: parse_date_ms(kv[1][0]) or 0)
        for i in range(1, len(ordered)):
            prev_role, prev_seg = ordered[i - 1]
            role, seg = ordered[i]
            prev_end = parse_date_ms(prev_seg[1])
            cur_start = parse_date_ms(seg[0])
            if prev_end is None or cur_start is None:
                continue
            if cur_start <= prev_end:
                errs.append(f"{prev_role} 与 {role} 时间重叠或相接："
                            f"{prev_seg[1]} → {seg[0]}")
                continue
            if self.isolation_hours is not None:
                gap_h = (cur_start - prev_end) / 3_600_000.0
                if gap_h < self.isolation_hours:
                    errs.append(
                        f"{prev_role}→{role} 间隔 {gap_h:.1f}h 小于标签跨度 "
                        f"{self.isolation_hours}h（标签会跨界泄漏）")
        return errs

    @property
    def configured(self) -> bool:
        return not self.validate()

    def to_dict(self) -> dict:
        return {
            "train": list(self.train) if self.train else None,
            "valid": list(self.valid) if self.valid else None,
            "holdout": list(self.holdout) if self.holdout else None,
            "isolation_hours": self.isolation_hours,
            "label_span_source": self.label_span_source,
            "split_method": self.split_method,
            "note": self.note,
            "configured": self.configured,
            "problems": self.validate(),
        }

    def sha(self) -> str:
        """进设计身份的哈希：只含**规则本身**，不含 `configured/problems` 派生量。"""
        return hash_obj({
            "train": list(self.train) if self.train else None,
            "valid": list(self.valid) if self.valid else None,
            "holdout": list(self.holdout) if self.holdout else None,
            "isolation_hours": self.isolation_hours,
            "split_method": self.split_method,
        })

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SplitPolicy":
        if not d:
            return cls()
        def _seg(v):
            if not v:
                return None
            if isinstance(v, (list, tuple)) and len(v) == 2:
                return (v[0], v[1])
            return None
        return cls(train=_seg(d.get("train")), valid=_seg(d.get("valid")),
                   holdout=_seg(d.get("holdout")),
                   isolation_hours=d.get("isolation_hours"),
                   label_span_source=d.get("label_span_source"),
                   split_method=d.get("split_method") or "chronological",
                   note=d.get("note") or "")


def assign_role(entry_ms: int, policy: SplitPolicy) -> Optional[str]:
    """按入场时刻把一条样本归入某段。落在隔离带 / 段外 ⇒ `None`（丢弃，不进任何组）。"""
    for role, seg in policy.segments():
        s, e = parse_date_ms(seg[0]), parse_date_ms(seg[1])
        if s is None or e is None:
            continue
        if s <= entry_ms <= e:
            return role
    return None


def propose_chronological_split(coverage_start: str, coverage_end: str, *,
                                ratios: Tuple[float, float, float] = (0.6, 0.2, 0.2),
                                isolation_hours: int) -> SplitPolicy:
    """按「时间顺序 + 固定比例 + 隔离带」**算出一份候选划分**。

    这个函数只做算术，不落地、不登记、不改任何状态 —— 目的是让「候选方案」
    可复现、可审计，而不是在文档里手写三个日期。

    ⚠ 返回的 `SplitPolicy` 是**候选**。是否采用由人拍板；未采用前
      `SplitPolicy.configured` 仍要按 `validate()` 判，且必须在登记时显式标注
      `note="候选，未经批准"`。框架不替人决定日期。

    切法：把 `[coverage_start, coverage_end]` 按比例切成三段，
    相邻段之间各让出 `isolation_hours` 的空档（空档从**后一段**头部扣掉，
    这样训练段不会被削短）。
    """
    if abs(sum(ratios) - 1.0) > 1e-9:
        raise ValueError(f"比例之和必须为 1：{ratios}")
    if isolation_hours is None or isolation_hours < 0:
        raise ValueError("必须给出非负隔离长度")
    s0 = parse_date_ms(coverage_start)
    e0 = parse_date_ms(coverage_end)
    if s0 is None or e0 is None or s0 >= e0:
        raise ValueError(f"覆盖范围不合法：{coverage_start!r} ~ {coverage_end!r}")

    total = e0 - s0
    iso = int(isolation_hours) * 3_600_000
    day = 86_400_000

    def _d(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    train_end = s0 + int(total * ratios[0])
    valid_start = train_end + iso
    valid_end = valid_start + int(total * ratios[1])
    hold_start = valid_end + iso
    hold_end = e0

    if hold_start >= hold_end:
        raise ValueError(
            f"隔离带 {isolation_hours}h × 2 已吃掉全部可用区间（总跨度 "
            f"{total / day:.0f} 天）⇒ 无法切分，须缩短隔离或扩大覆盖范围")
    return SplitPolicy(
        train=(_d(s0), _d(train_end)),
        valid=(_d(valid_start), _d(valid_end)),
        holdout=(_d(hold_start), _d(hold_end)),
        isolation_hours=int(isolation_hours),
        label_span_source=(f"由实测标签跨度推出：{isolation_hours}h"
                           f"（见 required_isolation_hours）"),
        split_method="chronological",
        note="候选划分（按比例 + 隔离带自动算出，未经人工批准前不得用于正式实验）",
    )


# ══════════════════════════ 泄漏检查 ══════════════════════════

def check_leakage(rows: Sequence[dict], policy: SplitPolicy,
                  observed_keys: Optional[set] = None) -> List[str]:
    """检查分组泄漏。`rows` 每项需含：

        `sample_key`（可选，缺省用 `symbol|entry_ms`）
        `symbol`、`entry_ms`、`exit_ms`（可为 `None`）

    返回问题清单（空 = 无泄漏）。三类检查：

      1. **标签跨界**：某样本标签区间（entry→exit）跨入后一段，且跨度 > 隔离长度。
      2. **重复样本**：同 `(symbol, entry_ms)` 出现多次。
      3. **已观察样本被当留出**：holdout 段内的样本键命中 `observed_keys`。
    """
    errs: List[str] = []
    if not policy.configured:
        # 未配置时不假装检查过
        errs.append("分组未配置，无法进行泄漏检查：" + "；".join(policy.validate()))
        return errs

    keyed: Dict[str, List[dict]] = {}
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        ent = r.get("entry_ms")
        if ent is None:
            continue
        k = r.get("sample_key") or f"{sym}|{int(ent)}"
        keyed.setdefault(k, []).append(r)

    # ① 重复样本
    dups = {k: v for k, v in keyed.items() if len(v) > 1}
    for k, v in list(dups.items())[:10]:
        errs.append(f"重复样本 {k} 出现 {len(v)} 次（同标的同入场时刻，只能算一条）")
    if len(dups) > 10:
        errs.append(f"…另有 {len(dups) - 10} 组重复样本未逐条列出")

    # ② 标签跨界：样本出场越过**后一段的起点**（落进隔离带内不算跨界 ——
    #    隔离带的作用就是吸收这段尾巴；隔离带够不够由 `SplitPolicy.validate` 管）
    seg_bounds: List[Tuple[str, int, int]] = []
    for role, seg in policy.segments():
        s, e = parse_date_ms(seg[0]), parse_date_ms(seg[1])
        if s is not None and e is not None:
            seg_bounds.append((role, s, e))
    seg_bounds.sort(key=lambda x: x[1])
    next_start: Dict[str, Optional[int]] = {}
    for i, (role, s, e) in enumerate(seg_bounds):
        next_start[role] = seg_bounds[i + 1][1] if i + 1 < len(seg_bounds) else None

    crossing = 0
    for r in rows:
        ent, ext = r.get("entry_ms"), r.get("exit_ms")
        if ent is None or ext is None:
            continue
        role = assign_role(int(ent), policy)
        if role is None:
            continue
        ns = next_start.get(role)
        if ns is not None and int(ext) > ns:
            crossing += 1
            if crossing <= 30:
                span_h = label_span_hours(int(ent), int(ext)) or 0.0
                errs.append(
                    f"标签跨界：{r.get('sample_key') or r.get('symbol')} 入场在 {role}、"
                    f"出场越过下一段起点（跨度 {span_h:.1f}h，"
                    f"超出 {(int(ext) - ns) / 3_600_000.0:.1f}h）")
    if crossing > 30:
        errs.append(f"…另有 {crossing - 30} 条跨界记录未逐条列出")

    # ③ 已观察样本被当留出
    if observed_keys:
        hold = None
        for role, seg in policy.segments():
            if role == ROLE_HOLDOUT:
                hold = seg
        if hold:
            hs, he = parse_date_ms(hold[0]), parse_date_ms(hold[1])
            hit = []
            for r in rows:
                ent = r.get("entry_ms")
                if ent is None or hs is None or he is None:
                    continue
                if not (hs <= int(ent) <= he):
                    continue
                k = r.get("sample_key") or f"{str(r.get('symbol') or '').upper()}|{int(ent)}"
                if k in observed_keys:
                    hit.append(k)
            if hit:
                errs.append(
                    f"留出段内有 {len(hit)} 条**已被观察过**的样本（如 {hit[0]}）"
                    f"⇒ 该段不能声称未见留出")
    return errs


# ══════════════════════════ 事件流 ══════════════════════════

class ExperimentRegistry:
    """append-only 实验登记库。多进程安全靠 `flock`（无 flock 时退化为进程内锁）。"""

    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.events_path = self.root / EVENTS_FILENAME
        self.head_path = self.root / HEAD_FILENAME
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 读写底层 ──────────────────────────────────────────
    def _read_rows(self) -> List[dict]:
        if not self.events_path.exists():
            return []
        rows: List[dict] = []
        with open(self.events_path, "r", encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise RegistryTampered(
                        f"{EVENTS_FILENAME} 第 {ln} 行不是合法 JSON：{exc}") from exc
        return rows

    def _read_head(self) -> dict:
        if not self.head_path.exists():
            return {"count": 0, "last_hash": GENESIS}
        try:
            return json.loads(self.head_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"count": -1, "last_hash": None}

    def _write_head(self, count: int, last_hash: str) -> None:
        tmp = self.head_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"count": count, "last_hash": last_hash,
                                   "updated_at": now_iso()}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.head_path)

    def verify(self, *, check_head: bool = True) -> dict:
        """重算全链。返回 `{ok, count, problems, last_hash}`。"""
        rows = self._read_rows()
        problems: List[str] = []
        prev = GENESIS
        for i, row in enumerate(rows):
            if row.get("seq") != i:
                problems.append(f"第 {i + 1} 条 seq 不连续：期望 {i}，实际 {row.get('seq')}")
            if row.get("prev") != prev:
                problems.append(f"第 {i + 1} 条 prev 断链：期望 {prev[:12]}…，"
                                f"实际 {str(row.get('prev'))[:12]}…")
            body = {k: row[k] for k in ("seq", "prev", "event") if k in row}
            calc = sha256_hex(canonical_bytes(body))
            if row.get("hash") != calc:
                problems.append(f"第 {i + 1} 条 hash 不符（内容被改写）")
            prev = row.get("hash") or ""
        last = prev if rows else GENESIS
        if check_head:
            head = self._read_head()
            if head.get("count") != len(rows):
                problems.append(
                    f"事件数与 head 不一致：文件 {len(rows)} 条 / head 记录 "
                    f"{head.get('count')} 条（末尾可能被截断）")
            if head.get("last_hash") != last:
                problems.append("最后一条 hash 与 head 不符（末尾可能被替换）")
        return {"ok": not problems, "count": len(rows), "problems": problems,
                "last_hash": last}

    def _require_intact(self) -> List[dict]:
        v = self.verify()
        if not v["ok"]:
            raise RegistryTampered("；".join(v["problems"][:6]))
        return self._read_rows()

    def _append(self, event: dict) -> dict:
        """追加一条事件。调用方负责语义校验，这里只管链与落盘。"""
        with _LOCK:
            rows = self._require_intact()
            prev = rows[-1]["hash"] if rows else GENESIS
            body = {"seq": len(rows), "prev": prev, "event": event}
            row = dict(body)
            row["hash"] = sha256_hex(canonical_bytes(body))
            with open(self.events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._write_head(len(rows) + 1, row["hash"])
            return row

    # ── 状态重建 ──────────────────────────────────────────
    def events(self) -> List[dict]:
        return [r["event"] for r in self._require_intact()]

    def state(self) -> dict:
        """把事件流折叠成当前状态。"""
        exps: Dict[str, dict] = {}
        observed: Dict[str, dict] = {}
        for ev in self.events():
            t = ev.get("type")
            eid = ev.get("experiment_id")
            if t == EV_REGISTER:
                exps[eid] = {
                    "experiment_id": eid,
                    "parent_experiment_id": ev.get("parent_experiment_id"),
                    "design": ev.get("design") or {},
                    "design_identity": ev.get("design_identity"),
                    "sample_scope": ev.get("sample_scope"),
                    "split_policy": ev.get("split_policy"),
                    "metrics_spec": ev.get("metrics_spec"),
                    "group": ev.get("group"),
                    "mode": ev.get("mode") or MODE_RESEARCH,
                    "created_at": ev.get("at"),
                    "created_by": ev.get("by"),
                    "note": ev.get("note"),
                    "frozen_at": None,
                    "frozen_identity": None,
                    "invalidations": [],
                    "runs": [],
                    "trials": [],
                    "abandoned": None,
                    "unsealed_at": None,
                    "unseal_identity": None,
                }
            elif t == EV_OBSERVE:
                # `observe` 事件**没有** experiment_id（观察的是样本集，不是实验）
                # ⇒ 必须放在「eid 不存在就跳过」之前，否则永远折不进状态。
                sid = ev.get("sample_set_id")
                if sid:
                    st = observed.setdefault(sid, {
                        "sample_set_id": sid, "keys": set(), "rows": 0,
                        "observed_at": ev.get("at"), "by": ev.get("by"),
                        "purpose": ev.get("purpose"), "note": ev.get("note"),
                        "events": 0,
                    })
                    st["keys"].update(ev.get("sample_keys") or [])
                    st["rows"] = max(int(st["rows"]), int(ev.get("rows") or 0))
                    st["events"] += 1
            elif eid not in exps:
                continue
            elif t == EV_FREEZE:
                exps[eid]["frozen_at"] = ev.get("at")
                exps[eid]["frozen_identity"] = ev.get("design_identity")
            elif t == EV_INVALIDATE:
                exps[eid]["invalidations"].append(
                    {"at": ev.get("at"), "reason": ev.get("reason"),
                     "expected": ev.get("design_identity"),
                     "actual": ev.get("actual_identity")})
                # 失效即解冻：必须重新冻结
                exps[eid]["frozen_at"] = None
                exps[eid]["frozen_identity"] = None
            elif t == EV_RUN:
                exps[eid]["runs"].append({
                    "run_id": ev.get("run_id"), "at": ev.get("at"),
                    "data_sha256": ev.get("data_sha256"),
                    "run_identity": ev.get("run_identity"),
                    "actual_scope": ev.get("actual_scope"),
                })
            elif t == EV_TRIAL:
                exps[eid]["trials"].append({
                    "run_id": ev.get("run_id"), "at": ev.get("at"),
                    "outcome": ev.get("outcome"), "metrics": ev.get("metrics"),
                    "role": ev.get("role"), "note": ev.get("note"),
                })
            elif t == EV_ABANDON:
                exps[eid]["abandoned"] = {"at": ev.get("at"), "reason": ev.get("reason")}
            elif t == EV_UNSEAL:
                exps[eid]["unsealed_at"] = ev.get("at")
                exps[eid]["unseal_identity"] = ev.get("design_identity")
        return {"experiments": exps, "observed": observed}

    # ── 登记 ──────────────────────────────────────────────
    def register(self, *, design: dict, sample_scope: Optional[dict],
                 split_policy: Optional[SplitPolicy], metrics_spec: Optional[dict],
                 experiment_id: Optional[str] = None,
                 parent_experiment_id: Optional[str] = None,
                 group: Optional[str] = None, mode: str = MODE_RESEARCH,
                 by: str = "system", note: str = "") -> dict:
        """登记一个实验。**同一设计身份重复登记会被拒绝**（不静默覆盖旧结果）。"""
        st = self.state()
        exps = st["experiments"]
        if experiment_id is None:
            experiment_id = "exp_" + hash_obj(
                {"design": design, "scope": sample_scope,
                 "split": split_policy.sha() if split_policy else None,
                 "metrics": metrics_spec})[:16]
        if not _EXP_ID_RE.match(experiment_id):
            raise ExperimentBlocked([f"实验 ID 不合法：{experiment_id!r}"])
        if experiment_id in exps:
            raise ExperimentBlocked([f"实验 ID 已存在，拒绝覆盖：{experiment_id}"])
        if parent_experiment_id and parent_experiment_id not in exps:
            raise ExperimentBlocked(
                [f"父实验不存在：{parent_experiment_id}（父子关系必须指向已登记实验）"])

        scope_n = normalize_sample_scope(sample_scope)
        policy = split_policy or SplitPolicy()
        design_parts = dict(design)
        design_parts["sample_scope"] = scope_n
        design_parts["split_policy"] = policy.sha()
        design_parts["metrics_spec"] = metrics_spec
        ident = compute_design_identity(design_parts)

        # 同身份不得重复登记（含「换个 ID 再登记一次」）
        for other_id, other in exps.items():
            if other.get("design_identity") == ident:
                raise ExperimentBlocked([
                    f"同一设计身份已登记为 {other_id}（设计身份 {ident[:12]}…）；"
                    f"要改配置请登记新实验并显式写 parent_experiment_id，"
                    f"不能换个 ID 重登记以继承旧结果"])

        ev = {
            "type": EV_REGISTER, "at": now_iso(), "by": by,
            "experiment_id": experiment_id,
            "parent_experiment_id": parent_experiment_id,
            "design": design, "design_identity": ident,
            "sample_scope": scope_n, "split_policy": policy.to_dict(),
            "metrics_spec": metrics_spec, "group": group,
            "mode": mode, "note": note,
            "missing_design_fields": missing_design_fields(design_parts),
        }
        self._append(ev)
        return self.state()["experiments"][experiment_id]

    def freeze(self, experiment_id: str, *, by: str = "system", note: str = "") -> dict:
        """冻结设计身份。冻结后配置漂移会被 `guard_execute` 发现并失效。"""
        st = self.state()["experiments"]
        exp = st.get(experiment_id)
        if not exp:
            raise ExperimentNotFound(experiment_id)
        if exp["abandoned"]:
            raise ExperimentBlocked([f"实验已放弃：{experiment_id}"])
        if exp["frozen_at"]:
            raise ExperimentBlocked(
                [f"实验已冻结于 {exp['frozen_at']}，重复冻结会掩盖冻结后的改动；"
                 f"要换配置请登记新实验"])
        miss = missing_design_fields({
            **exp["design"], "sample_scope": exp["sample_scope"],
            "split_policy": (exp["split_policy"] or {}).get("configured"),
            "metrics_spec": exp["metrics_spec"]})
        # 分组未配置**不阻止冻结**（冻结是「我要锁定当前设计」），但会阻止解封
        blocking = [m for m in miss if m != "split_policy"]
        if blocking:
            raise ExperimentBlocked([f"设计缺少必填项，不能冻结：{'、'.join(blocking)}"])
        self._append({"type": EV_FREEZE, "at": now_iso(), "by": by,
                      "experiment_id": experiment_id,
                      "design_identity": exp["design_identity"], "note": note})
        return self.state()["experiments"][experiment_id]

    def invalidate(self, experiment_id: str, *, actual_identity: str,
                   reason: str, by: str = "system") -> dict:
        """冻结失效：实测身份与冻结身份不一致（代码/参数/规则/样本/分组/指标被改）。"""
        st = self.state()["experiments"]
        exp = st.get(experiment_id)
        if not exp:
            raise ExperimentNotFound(experiment_id)
        self._append({"type": EV_INVALIDATE, "at": now_iso(), "by": by,
                      "experiment_id": experiment_id, "reason": reason,
                      "design_identity": exp["design_identity"],
                      "actual_identity": actual_identity})
        return self.state()["experiments"][experiment_id]

    def attach_run(self, experiment_id: str, run_id: str, *,
                   data_sha256: Optional[str],
                   actual_scope: Optional[dict] = None,
                   by: str = "system") -> dict:
        """绑定一次实际运行。`data_sha256` 未知时记 `None`，**不填占位符**。"""
        exp = self.state()["experiments"].get(experiment_id)
        if not exp:
            raise ExperimentNotFound(experiment_id)
        if not exp["frozen_at"]:
            raise ExperimentBlocked(
                [f"实验 {experiment_id} 未冻结 ⇒ 不允许产生运行记录"
                 f"（先冻结设计身份，跑完再回填数据指纹）"])
        run_ident = compute_run_identity(exp["design_identity"], data_sha256)
        for r in exp["runs"]:
            if r["run_id"] == run_id:
                raise ExperimentBlocked([f"run 已绑定到本实验：{run_id}"])
        self._append({"type": EV_RUN, "at": now_iso(), "by": by,
                      "experiment_id": experiment_id, "run_id": run_id,
                      "data_sha256": data_sha256, "run_identity": run_ident,
                      "actual_scope": normalize_sample_scope(actual_scope)})
        return self.state()["experiments"][experiment_id]

    def record_trial(self, experiment_id: str, *, outcome: str,
                     run_id: Optional[str] = None, metrics: Optional[dict] = None,
                     role: Optional[str] = None, note: str = "",
                     by: str = "system") -> dict:
        """记录一次试验结果。**失败/放弃也必须调用**，否则只剩赢家。"""
        exp = self.state()["experiments"].get(experiment_id)
        if not exp:
            raise ExperimentNotFound(experiment_id)
        if outcome not in ("success", "failure", "abandoned", "invalid"):
            raise ExperimentBlocked([f"未知试验结论：{outcome!r}"])
        if role is not None and role not in ROLES:
            raise ExperimentBlocked([f"未知分组角色：{role!r}"])
        self._append({"type": EV_TRIAL, "at": now_iso(), "by": by,
                      "experiment_id": experiment_id, "run_id": run_id,
                      "outcome": outcome, "metrics": metrics, "role": role,
                      "note": note})
        return self.state()["experiments"][experiment_id]

    def abandon(self, experiment_id: str, *, reason: str, by: str = "system") -> dict:
        exp = self.state()["experiments"].get(experiment_id)
        if not exp:
            raise ExperimentNotFound(experiment_id)
        if not reason.strip():
            raise ExperimentBlocked(["放弃实验必须写明原因"])
        self._append({"type": EV_ABANDON, "at": now_iso(), "by": by,
                      "experiment_id": experiment_id, "reason": reason})
        return self.state()["experiments"][experiment_id]

    def observe(self, sample_set_id: str, sample_keys: Sequence[str], *,
                rows: Optional[int] = None, by: str = "system",
                purpose: str = "研究/比较/调参", note: str = "") -> dict:
        """登记「这批样本已经被看过」。此后任何实验都不得把它当未见留出。"""
        if not sample_set_id or not str(sample_set_id).strip():
            raise ExperimentBlocked(["sample_set_id 不能为空"])
        keys = [str(k) for k in sample_keys]
        if not keys:
            raise ExperimentBlocked([
                "已观察样本必须给出样本键（不能只说『看过 227 条』而无从核对）"])
        self._append({"type": EV_OBSERVE, "at": now_iso(), "by": by,
                      "sample_set_id": sample_set_id, "sample_keys": keys,
                      "rows": int(rows) if rows is not None else len(keys),
                      "purpose": purpose, "note": note})
        return self.state()["observed"][sample_set_id]

    # ── 解封 ──────────────────────────────────────────────
    def unseal_holdout(self, experiment_id: str, *, by: str,
                       note: str = "") -> dict:
        """解封最终留出。要求：已冻结、身份一致、分组已配置、留出未被污染。"""
        st = self.state()
        exp = st["experiments"].get(experiment_id)
        if not exp:
            raise ExperimentNotFound(experiment_id)
        reasons = self._unseal_blockers(exp, st["observed"])
        if reasons:
            raise ExperimentBlocked(reasons)
        self._append({"type": EV_UNSEAL, "at": now_iso(), "by": by,
                      "experiment_id": experiment_id,
                      "design_identity": exp["design_identity"], "note": note})
        return self.state()["experiments"][experiment_id]

    def _contamination_blockers(self, policy: SplitPolicy,
                                observed: dict) -> List[str]:
        """留出段内是否含有**已观察过**的样本。

        解封（`unseal_holdout`）与启动许可（`launch_blockers`）共用这一份判据 ——
        不允许两处各写一套，否则迟早一边放宽一边收紧。
        """
        hold = policy.holdout
        if not (hold and observed):
            return []
        hs, he = parse_date_ms(hold[0]), parse_date_ms(hold[1])
        if hs is None or he is None:
            return []
        hit_sets = []
        for sid, s in observed.items():
            for k in s["keys"]:
                parts = k.rsplit("|", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    t = int(parts[1])
                    if hs <= t <= he:
                        hit_sets.append(sid)
                        break
        if not hit_sets:
            return []
        return ["留出段内含有已观察样本（登记集："
                + "、".join(sorted(set(hit_sets)))
                + "）⇒ 该段已被污染，不能声称未见留出"]

    def _unseal_blockers(self, exp: dict, observed: dict) -> List[str]:
        out: List[str] = []
        if exp["abandoned"]:
            out.append(f"实验已放弃（{exp['abandoned'].get('reason')}）")
        if not exp["frozen_at"]:
            out.append("配置未冻结：必须先 freeze 设计身份才能解封最终留出")
        elif exp["frozen_identity"] != exp["design_identity"]:
            out.append("冻结身份与当前登记身份不一致（配置已被改动）⇒ 需重新登记新实验")
        policy = SplitPolicy.from_dict(exp.get("split_policy"))
        if not policy.configured:
            out.append("分组未配置或不自洽：" + "；".join(policy.validate()))
        if exp.get("unsealed_at"):
            out.append(f"留出已于 {exp['unsealed_at']} 解封过（一次性，不可重复解封）")
        out.extend(self._contamination_blockers(policy, observed))
        return out

    # ── 启动许可（STAGE_LAUNCH）────────────────────────────
    def _launch_blockers(self, exp: dict, observed: dict) -> List[str]:
        """启动阶段的静态条件。**刻意不看 runs / trials。**

        首个正式实验在这五项都满足时就必须能启动；把「已有运行记录」放进这里
        等于要求「先跑过才能开跑」，会让任何首次正式实验物理上无法创建。
        """
        out: List[str] = []
        if exp["mode"] != MODE_FORMAL:
            out.append(f"登记模式为 {exp['mode']!r}（研究运行不具正式资格）")
        miss = missing_design_fields({
            **exp["design"], "sample_scope": exp["sample_scope"],
            "split_policy": (exp["split_policy"] or {}).get("configured"),
            "metrics_spec": exp["metrics_spec"]})
        if miss:
            out.append("设计缺项：" + "、".join(miss))
        policy = SplitPolicy.from_dict(exp.get("split_policy"))
        if not policy.configured:
            out.append("分组未配置/不自洽：" + "；".join(policy.validate()))
        if not exp["frozen_at"]:
            out.append("配置未冻结")
        elif exp["frozen_identity"] != exp["design_identity"]:
            out.append("冻结身份与当前登记身份不一致（配置已被改动）⇒ 需重新登记新实验")
        if exp["abandoned"]:
            out.append("实验已放弃")
        # 样本污染：不在启动阶段放行已被污染的留出（与解封同一判据）
        out.extend(self._contamination_blockers(policy, observed))
        return out

    def launch_blockers(self, experiment_id: str) -> List[str]:
        """启动许可的阻断原因清单（空 = 可以开跑）。"""
        st = self.state()
        exp = st["experiments"].get(experiment_id)
        if not exp:
            return [f"实验未登记：{experiment_id}"]
        return self._launch_blockers(exp, st["observed"])

    def formal_launchable(self, experiment_id: str) -> Tuple[bool, List[str]]:
        """**启动许可**：能不能开始跑。不要求已有运行/试验记录。"""
        why = self.launch_blockers(experiment_id)
        return (not why), why

    # ── 结果交付资格（STAGE_DELIVERY）──────────────────────
    def formal_eligible(self, experiment_id: str) -> Tuple[bool, List[str]]:
        """**结果交付资格**（≠ 启动许可）。

        在启动许可之上，还要求：真的跑过、每次运行都有结论、结论里有量化结果。
        首个正式实验**不该**被这一条卡住 —— 启动看 `formal_launchable()`。
        """
        st = self.state()
        exp = st["experiments"].get(experiment_id)
        if not exp:
            return False, [f"实验未登记：{experiment_id}"]
        reasons: List[str] = self._launch_blockers(exp, st["observed"])
        if not exp["runs"]:
            reasons.append("尚无已登记运行（试验次数为 0，不能宣称有结果）")
        elif not exp["trials"]:
            # 失败的试验也必须留痕，且不能把「只跑成功的那次」当全部
            reasons.append(
                f"有 {len(exp['runs'])} 次运行但无任何试验结论记录"
                f"（失败/放弃也须入册，只留赢家不算合格）")
        else:
            trial_runs = {t.get("run_id") for t in exp["trials"] if t.get("run_id")}
            missing = [r["run_id"] for r in exp["runs"]
                       if r.get("run_id") and r["run_id"] not in trial_runs]
            if missing:
                reasons.append(
                    f"{len(missing)} 次运行没有对应的试验结论（如 {missing[0]}）"
                    f"⇒ 结果记录不完整，不能宣称正式交付")
            if not any(t.get("metrics") for t in exp["trials"]):
                reasons.append(
                    "所有试验结论都没有量化结果（metrics 全空）"
                    "⇒ 没有可交付的结果，不能宣称正式交付通过")
        return (not reasons), reasons

    def trials_count(self, experiment_id: str) -> int:
        exp = self.state()["experiments"].get(experiment_id) or {}
        return len(exp.get("trials") or [])

    def list_experiments(self) -> List[dict]:
        st = self.state()["experiments"]
        out = []
        for eid, e in st.items():
            ok, why = self.formal_eligible(eid)
            launch_ok, launch_why = self.formal_launchable(eid)
            out.append({
                "experiment_id": eid,
                "parent_experiment_id": e["parent_experiment_id"],
                "design_identity": e["design_identity"],
                "frozen": bool(e["frozen_at"]),
                "frozen_at": e["frozen_at"],
                "runs": len(e["runs"]),
                "trials": len(e["trials"]),
                "abandoned": bool(e["abandoned"]),
                "unsealed_at": e["unsealed_at"],
                # 两个阶段分开报：启动许可 ≠ 结果交付资格
                "launchable": launch_ok,
                "launch_blockers": launch_why,
                "formal_eligible": ok,
                "blockers": why,
                "mode": e["mode"],
                "group": e["group"],
                "created_at": e["created_at"],
            })
        return out

    def observed_sample_sets(self) -> List[dict]:
        st = self.state()["observed"]
        return [{k: (sorted(v) if k == "keys" else v) for k, v in s.items()}
                for s in st.values()]


# ══════════════════════════ 当前设计身份（真实链路） ══════════════════════════

def current_design_parts(strategy_id: str = "WX_EVENT_TREND_V0_1",
                         sample_scope: Optional[dict] = None,
                         split_policy: Optional[SplitPolicy] = None,
                         metrics_spec: Optional[dict] = None, execution_contract: Optional[dict] = None) -> dict:
    """从**现有指纹机制**取代码/参数/规则三项，不另算一套。

    复用 `EventTrendRunner.code_fingerprint / config_fingerprint / rules_fingerprint`：
    与 `inputs.json.fingerprints` 同源，避免「登记用一套、回测用另一套」。
    取不到 ⇒ 对应项为 `None`（闸门会因此拒绝，不猜）。
    """
    parts: Dict[str, Any] = {"strategy_id": strategy_id, "strategy_version": None,
                             "code_sha256": None, "config_sha256": None,
                             "rules_sha256": None}
    # 契约未显式给出时**取当前已批准契约**，而不是留空。
    # 留空会让「登记」与「创建」两侧身份不一致（见 default_execution_contract 注释）。
    if execution_contract is None:
        execution_contract = default_execution_contract()
    try:
        # 复用桥接模块已做好的路径注入与可用性判定，不另起一套 import 逻辑
        import event_trend_bridge as _BR
        if not getattr(_BR, "AVAILABLE", False):
            raise RuntimeError(getattr(_BR, "IMPORT_ERROR", "event_trend 不可用"))
        from WyckoffMaster.event_trend_v0_1 import EventTrendConfig, EventTrendRunner
        runner = EventTrendRunner()
        # ⚠ 桥接的 `code_hash()` 取 `fp["sha256"]`，而实际键是 `combined_sha256`
        #   ⇒ 恒返回 64 个 0。这里直取真键，不复用那个取值。
        parts["code_sha256"] = (runner.code_fingerprint() or {}).get("combined_sha256")
        parts["rules_sha256"] = (runner.rules_fingerprint() or {}).get("sha256")
        cfg = EventTrendConfig()
        if execution_contract:
            parts["execution_contract"] = execution_contract
        parts["config_sha256"] = (runner.config_fingerprint(cfg) or {}).get("sha256")
        parts["strategy_version"] = getattr(cfg, "strategy_version", None)
    except Exception as exc:                              # noqa: BLE001
        parts["_error"] = f"{type(exc).__name__}: {exc}"
    if execution_contract:
        import hashlib
        parts["config_sha256"] = hashlib.sha256(json.dumps(execution_contract, sort_keys=True).encode()).hexdigest()
    parts["sample_scope"] = normalize_sample_scope(sample_scope)
    parts["split_policy"] = (split_policy or SplitPolicy()).sha()
    parts["metrics_spec"] = metrics_spec
    return parts


# ══════════════════════════ 真实入口闸门 ══════════════════════════

#: 标准评价指标定义（进设计身份）。改指标 = 换身份。
DEFAULT_METRICS_SPEC = {
    "primary": "net_pnl",
    "secondary": ["max_drawdown_pct", "sharpe_sqrt365", "profit_factor",
                  "trades_count", "win_rate"],
    "cost_basis": "扣费后净盈亏（fees + funding 已减）",
    "notes": "胜负按 net_pnl；回撤用真实权益；夏普 = UTC 逐日净值 sqrt(365)",
}

REGISTRY_ENV = "FRAME_FIT_REGISTRY"

#: ⚠ **历史缺陷记录（已修复，常量仅作追溯用，不要再拿它做判断）**
#:
#: 早期 `backtest_runner._run_event_trend` 把数据窗口**写死**成
#: `2024-01-01 ~ 2024-06-30`，请求里的 `window_start/window_end` 在真实链路上不生效。
#: 后果是「登记的样本范围」与「实际跑的样本」可能不是一回事 —— 这是必须阻断的。
#:
#: 该写死在 FRAME-FIT 系列修复中已删除：现在 `backtest_runner.py` 里搜不到
#: 这两个日期，执行链严格使用请求显式窗口。因此下面这个常量**不再参与任何判断**，
#: 保留它只是为了让后来的人知道「当年为什么要有 scope_mismatch 这个字段」。
PINNED_EXEC_WINDOW = ("2024-01-01", "2024-06-30")   # 历史值，勿用
PINNED_EXEC_SOURCE = ("框架/backend/backtest_runner.py::_run_event_trend "
                      "写死窗口（已于 FRAME-FIT 修复中删除）")


def execution_scope_mismatch(spec: dict) -> Optional[str]:
    """请求窗口与执行链实际生效窗口是否不一致。

    **当前恒返回 `None`**，原因是检查对象已经不存在了：
    执行链不再写死窗口，请求什么窗口就按什么窗口跑。

    那「请求窗口与登记窗口不一致」靠什么拦？靠**设计身份比对**
    （见 `guard_create` 里 `exp["design_identity"] != out["design_identity"]`）：
    窗口是 `sample_scope` 的一部分，会参与身份计算，与登记不一致就得到不同身份，
    于是被拒。实测比旧机制更严 —— 连「请求窗口是登记窗口的子集」也会被拒。

    保留本函数与 `scope_mismatch` 字段，是为了让 UI 与证据文件仍能读到
    「这项检查的当前结论」，而不是让它悄无声息地消失、后人以为没做过检查。
    """
    return None


def get_registry(root: Optional[Path | str] = None) -> ExperimentRegistry:
    """取登记库。`FRAME_FIT_REGISTRY` 环境变量可改根目录（测试用）。"""
    if root is not None:
        return ExperimentRegistry(root)
    env = os.environ.get(REGISTRY_ENV)
    return ExperimentRegistry(env if env else DEFAULT_ROOT)


def _requested_mode(spec: dict) -> str:
    blk = (spec or {}).get("experiment") or {}
    m = str(blk.get("mode") or MODE_RESEARCH).lower()
    return MODE_FORMAL if m == MODE_FORMAL else MODE_RESEARCH


def guard_create(spec: dict, *, registry: Optional[ExperimentRegistry] = None,
                 run_id: Optional[str] = None) -> dict:
    """**创建入口**闸门（`backtest_api.create_run` 调用）。

    返回 `{ok, mode, experiment_id, design_identity, blockers, registered}`。
    `ok=False` 时调用方必须拒绝创建并原样透出 `blockers`。

    研究模式：自动登记（若调用方未指定实验），`formal_eligible` 恒 False，
              **允许运行**——因为不允许跑就等于让所有研究停摆。
    正式模式：**只校验启动许可**（已登记、已冻结、身份一致、分组已配置、
              留出未被污染、实验未放弃）。**不要求已有 runs / trials** ——
              那是结果交付资格（`formal_eligible`）的事，放到启动阶段就是死锁。
    """
    reg = registry or get_registry()
    mode = _requested_mode(spec)
    blk = (spec or {}).get("experiment") or {}
    eid = blk.get("experiment_id")
    out = {"ok": True, "mode": mode, "stage": STAGE_LAUNCH,
           "experiment_id": eid, "design_identity": None,
           "blockers": [], "registered": False}

    scope = {
        "symbols": (spec or {}).get("symbols") or [],
        "window_start": (blk.get("window_start") or (spec or {}).get("window_start")),
        "window_end": (blk.get("window_end") or (spec or {}).get("window_end")),
        "source": "request",
    }
    mismatch = execution_scope_mismatch({
        "window_start": scope["window_start"], "window_end": scope["window_end"], "execution_contract": spec.get("execution_contract")})
    if mismatch:
        scope["pinned_by_code"] = True
    out["scope_mismatch"] = mismatch

    # ⚠ 正式模式下，**分组规则与指标以登记为准**，不从请求里取。
    #   理由：请求里没带 `split_policy` 时若按空规则算身份，必然与登记身份不符 ⇒
    #   会把「配置一致」误判成「身份漂移」。请求只用来校验**样本范围**是否一致。
    exp = None
    if eid:
        try:
            exp = reg.state()["experiments"].get(eid)
        except Exception:                                  # noqa: BLE001
            exp = None
    if mode == MODE_FORMAL and exp:
        policy = SplitPolicy.from_dict(exp.get("split_policy"))
        metrics = exp.get("metrics_spec") or DEFAULT_METRICS_SPEC
    else:
        policy = SplitPolicy.from_dict(blk.get("split_policy"))
        metrics = blk.get("metrics_spec") or DEFAULT_METRICS_SPEC
    parts = current_design_parts(sample_scope=scope, split_policy=policy,
                                 metrics_spec=metrics, execution_contract=spec.get("execution_contract"))
    out["design_identity"] = compute_design_identity(parts)

    if mode == MODE_RESEARCH:
        # 研究运行：未指定实验则自动登记一条（留下痕迹，但明确不具正式资格）
        if not eid:
            try:
                st = reg.register(
                    design=parts, sample_scope=scope, split_policy=policy,
                    metrics_spec=metrics,
                    parent_experiment_id=blk.get("parent_experiment_id"),
                    group=blk.get("group"), mode=MODE_RESEARCH,
                    by=str(blk.get("by") or "api:create_run"),
                    note=str(blk.get("note") or "研究运行自动登记"))
                out["experiment_id"] = st["experiment_id"]
                out["registered"] = True
            except ExperimentBlocked as exc:
                # 同身份已登记 ⇒ 复用已有实验，不阻断研究运行
                existing = [e for e in reg.list_experiments()
                            if e["design_identity"] == out["design_identity"]]
                if existing:
                    out["experiment_id"] = existing[0]["experiment_id"]
                else:
                    out["ok"] = False
                    out["blockers"] = exc.reasons
        out["blockers"] = out["blockers"] or []
        out["formal_eligible"] = False
        out["formal_blockers"] = (
            ["研究模式运行，不具正式实验资格"]
            + ([mismatch] if mismatch else [])
            + (["分组未配置"] if not policy.configured else []))
        return out

    # ── 正式模式：**只校验启动许可**（STAGE_LAUNCH）──
    # 首个正式实验在这里必须能过：它的 runs / trials 必然为空，
    # 拿「结果交付资格」来卡启动就是死锁。交付资格留给 `formal_eligible()`。
    if not eid:
        out["ok"] = False
        out["stage"] = STAGE_LAUNCH
        out["blockers"] = ["正式实验必须显式给出 experiment.experiment_id（不得临时起意）"]
        return out
    ok, why = reg.formal_launchable(eid)
    exp = reg.state()["experiments"].get(eid)
    if exp and exp["design_identity"] != out["design_identity"]:
        why = why + [f"当前代码/参数/规则/样本/分组/指标与登记身份不一致"
                     f"（登记 {exp['design_identity'][:12]}… / 当前 "
                     f"{out['design_identity'][:12]}…）⇒ 请登记新实验版本，不得沿用旧身份"]
        ok = False
    if mismatch:
        why = why + [mismatch]
        ok = False
    out["stage"] = STAGE_LAUNCH
    out["ok"] = ok
    out["blockers"] = why
    # 启动通过 ≠ 结果可交付：把交付资格一并透出，供调用方如实标注
    delivery_ok, delivery_why = reg.formal_eligible(eid) if exp else (False, ["实验未登记"])
    out["delivery_eligible"] = bool(delivery_ok)
    out["delivery_blockers"] = delivery_why
    return out


def guard_execute(run_id: str, doc: dict, *,
                  registry: Optional[ExperimentRegistry] = None) -> Optional[str]:
    """**执行入口**闸门（`backtest_runner.execute_run` 调用）。

    返回拒绝原因字符串或 `None`。检查两件事：

      1. 该 run 是否绑定到已登记且**已冻结**的实验（未冻结不得产出结果）。
      2. 登记身份与**当前**代码/参数是否一致（防「登记后改代码再跑」）。
         不一致 ⇒ 写 `invalidate` 事件并拒绝。
    """
    reg = registry or get_registry()
    spec = (doc or {}).get("spec") or {}
    blk = spec.get("experiment") or {}
    eid = blk.get("experiment_id")
    if not eid:
        return ("运行未绑定实验登记（spec.experiment.experiment_id 缺失）"
                "⇒ 该 run 只能作为临时研究，不得计入任何实验结论")
    st = reg.state()["experiments"]
    exp = st.get(eid)
    if not exp:
        return f"实验未登记：{eid}"
    if exp["abandoned"]:
        return f"实验已放弃：{eid}（{exp['abandoned'].get('reason')}）"
    if not exp["frozen_at"]:
        return f"实验未冻结：{eid} ⇒ 冻结前不得产出可用于比较的结果"

    policy = SplitPolicy.from_dict(exp.get("split_policy"))
    scope = {
        "symbols": spec.get("symbols") or [],
        "window_start": blk.get("window_start") or spec.get("window_start"),
        "window_end": blk.get("window_end") or spec.get("window_end"),
        "source": "request",
    }
    parts = current_design_parts(sample_scope=scope, split_policy=policy,
                                 metrics_spec=exp.get("metrics_spec"), execution_contract=spec.get("execution_contract"))
    actual = compute_design_identity(parts)
    if actual != exp["design_identity"]:
        try:
            reg.invalidate(eid, actual_identity=actual,
                           reason="执行时实测设计身份与冻结身份不一致",
                           by="api:execute_run")
        except Exception:                                  # noqa: BLE001
            pass
        return (f"设计身份漂移：冻结时 {exp['design_identity'][:12]}… / "
                f"执行时 {actual[:12]}… ⇒ 冻结已失效，须登记新实验版本")
    return None


def record_execution(run_id: str, doc: dict, *, outcome: str,
                     metrics: Optional[dict] = None,
                     note: str = "",
                     registry: Optional[ExperimentRegistry] = None) -> Optional[str]:
    """把**一次真实执行**写进登记：先绑 run，再记试验结论。

    返回错误说明，或 `None`（已写入 / 按规则跳过）。

    设计约束（FRAME-FIT-02）：

      - 只从 `doc`（执行器自己读出来的运行文档）取 `experiment_id`，
        **不接受调用方另传实验身份** —— 没有「指定一个实验就把结果记进去」这条路；
      - 实验必须已登记、已冻结、未放弃，否则**不写任何记录**（返回原因）；
      - run 已绑过就不重复绑，trial 已记过就不重复记（幂等）；
      - `outcome` 只允许 `success / failure / abandoned / invalid`；
      - `metrics` 为空就照实记空 —— 交付资格那边会因此判「没有可交付的结果」。

    ⚠ 信任边界：本函数不校验 `run_id` 是否真的存在于运行库。它与
      `attach_run()` 同级 —— 能直接调用登记 API 的代码，本来就能直接写事件。
      防伪造靠的是**闸门不写记录**（`guard_create` / `guard_execute` 全程只读），
      以及运行记录由执行器在真实执行后产生。
    """
    reg = registry or get_registry()
    spec = (doc or {}).get("spec") or {}
    eid = (spec.get("experiment") or {}).get("experiment_id")
    if not eid:
        return ("运行未绑定实验登记（spec.experiment.experiment_id 缺失）"
                "⇒ 不写入任何运行/试验记录")
    exp = reg.state()["experiments"].get(eid)
    if not exp:
        return f"实验未登记：{eid} ⇒ 不写入任何运行/试验记录"
    if exp["abandoned"]:
        return f"实验已放弃：{eid} ⇒ 不写入任何运行/试验记录"
    if not exp["frozen_at"]:
        return (f"实验未冻结：{eid} ⇒ 不写入运行/试验记录"
                f"（研究运行不具正式资格，不冒充已登记结果）")
    if outcome not in ("success", "failure", "abandoned", "invalid"):
        return f"未知试验结论：{outcome!r} ⇒ 不写入"

    try:
        if not any(r.get("run_id") == run_id for r in exp["runs"]):
            reg.attach_run(eid, run_id,
                           data_sha256=(doc or {}).get("data_sha256"),
                           by="api:execute_run")
        exp = reg.state()["experiments"].get(eid) or exp
        if not any(t.get("run_id") == run_id for t in exp["trials"]):
            reg.record_trial(eid, outcome=outcome, run_id=run_id,
                             metrics=metrics, note=note, by="api:execute_run")
    except ExperimentBlocked as exc:                        # 并发/重复绑定等
        return "；".join(exc.reasons)
    return None


__all__ = [
    "GENESIS", "DEFAULT_ROOT", "REGISTRY_ENV", "DESIGN_FIELDS", "RUN_FIELDS",
    "EV_REGISTER", "EV_FREEZE", "EV_RUN", "EV_TRIAL", "EV_ABANDON",
    "EV_OBSERVE", "EV_UNSEAL", "EV_INVALIDATE", "EVENT_TYPES",
    "ROLE_TRAIN", "ROLE_VALID", "ROLE_HOLDOUT", "ROLES",
    "MODE_RESEARCH", "MODE_FORMAL", "STAGE_LAUNCH", "STAGE_DELIVERY", "STAGES",
    "DEFAULT_METRICS_SPEC",
    "RegistryTampered", "ExperimentBlocked", "ExperimentNotFound",
    "canonical_bytes", "sha256_hex", "hash_obj", "now_iso", "parse_date_ms",
    "normalize_sample_scope", "compute_design_identity", "compute_run_identity",
    "missing_design_fields", "label_span_hours", "required_isolation_hours",
    "SplitPolicy", "assign_role", "check_leakage",
    "ExperimentRegistry", "current_design_parts", "get_registry",
    "guard_create", "guard_execute", "record_execution",
]
