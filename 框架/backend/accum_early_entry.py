# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜甲乙丙「提前入场」统一研究入口（观察 → 候选 → 委托 → 成交 → 退出）。

本模块是 **05-C 唯一需要调用的研究入口**（`research_run()` / 命令行 `run`）。
它只做接线与编排：数据输入 → 观察状态 → 候选记录 → 失效原因 → 策略身份 → 独立模拟账户
→ 逐 15m 退出。它**不新造执行引擎**，也不改任何经济规则。

本单（05-B）相对 04-B 改了什么、为什么
--------------------------------------
1. **已确定的 8 条语义直接执行，不再当配置项**（《用户要求与决策》原文）：
   1h 确认、15m 执行、确认后**下一根** 15m 成交（12:00 确认 ⇒ 12:15，不回填）、
   初始止损 = 成交价 × 95%、跟踪止损 = 每根新收盘 4h 最低价 × 95% 且只上移、
   入场即跟踪（无 2R 门槛）、无持仓天数上限、手续费单边 5bps。
   这些值写在 `FIXED_RULES` 里，并由 `check_fixed_rules()` 与引擎生效视图**逐项对撞**；
   对不上 ⇒ 阻塞开仓（`fixed_rules_mismatch`），不静默按其中一方跑。
2. **退出不再走 `paper_engine.advance_position_trailing` 的「4h 最低价判退出」分支。**
   04-D 已证明该分支有两处口径问题（用 4h 低点判退出、退出时刻记在 4h 整点）。
   本模块改成：跟踪止损只做「上移」（`record_stop_update`），退出判定**只用 15m 最低价**，
   新止损生效时刻 = **4h 收盘 + 15 分钟**（`next_15m_after_4h_close`）。
   旧的驱动方式保留为**对照臂**（`legacy_4h_exit=True`），用于「原策略行为对照」。
3. **把「已写进规则文档但没接线」的判定补齐**，并把它们的取值全部留成显式待配置：
   扫低参照（含「必须高于箱体下沿」）、压制位合格性（离箱底/箱顶各留空间）、
   承接基准改成「前 n 根主动买占比中位数 × 倍数」、振幅上半区、卖压减弱、
   失效（连续 n 根收盘破箱体下沿 / 任一根低点破 L−kA）、乙组守位失败根数、
   确认根是否必须收阳、跳空追高上限、成交偏离上限、观察期限、冷却期。
   **一项默认值都不给**：缺一项 ⇒ `open_allowed=False`，只记录「卡在哪一项」。
4. **配置格式升级为 `accum-early-05/v1`**：字段名与 04 版不兼容（04 的绝对阈值字段
   `sell_pressure.buy_ratio_min` 与规则文档的「基准 × 倍数」不是同一语义）。
   旧格式文件会被明确拒绝（`schema_version_mismatch`），不会被猜着读。

三条不可越的线
--------------
1. **没有批准配置就不许开仓**：缺字段/取值未实现/已确定语义对不上 ⇒ 阻塞开仓；
   数据读取、观察推进、候选记录、隔离边界核对**照常执行**。
2. **不替人决定阈值语义**：可执行谓词是有限集合（`IMPLEMENTED`），批准值不在集合内 ⇒
   报 `unsupported_value` 并阻塞，**不猜、不近似**。
3. **研究账户与生产账户物理隔离**：账本目录必须在研究根下；`PAPER_LEDGER_DIR` 指向生产
   `数据/paper` ⇒ `try_open()` 直接拒绝。

不能说的话（写在这里，防止下游误用）
------------------------------------
· 「入口建成」≠「策略已实现」，更 ≠「策略有效」。三组阈值一项都没批准。
· 用 fixture 配置跑出来的成交只证明链路行为，不是甲乙丙的绩效结论。
· 15m/4h 的 `available_at` 在冷档填充率为 0 ⇒ 只能声称「计算不超前」，
  不能声称「已证明当时可得」；这类判定一律记 `not_proven`，不补造。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = PROJECT / "数据" / "research" / "ACCUM-EARLY-05"
DEFAULT_CONFIG_PATH = RESEARCH_ROOT / "approved_rules.json"
OBSERVE_DB = RESEARCH_ROOT / "observe.sqlite3"

import sys as _sys
for _p in (str(PROJECT), str(Path(__file__).resolve().parent)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

#: 版本标识（三个都要能单独引用，不要合成一个字符串）
VERSION = "accum_early_05b_v1"                 # 本模块代码版本
CONFIG_SCHEMA = "accum-early-05/v1"            # 配置格式版本
RULE_VERSION = "accum-early-05::v1"            # 观察/候选规则版本
EXIT_RULES = "accum-exit-05/v1"                # 退出规则版本

SCHEMES = ("4a", "4b", "4c")

H = 3_600_000
M15 = 900_000
H4 = 14_400_000
DAY = 86_400_000

#: 允许开仓的两个开关（环境变量优先于配置文件，防止「配置文件一改就自动跑」）
FIXTURE_ENV = "ACCUM05_FIXTURE_OK"
CONFIG_ENV = "ACCUM05_CONFIG"

# ══════════════════════════════════════════════════════════════════════════
# 一、已确定语义（不是配置项，是断言项）
# ══════════════════════════════════════════════════════════════════════════
#: 依据：《用户要求与决策》与 `evidence/ACCUM-EARLY-04-A/02_规则接口.md` §6.一。
#: 这些值**不允许**由配置文件改写；`check_fixed_rules()` 会拿引擎生效视图与冻结策略包逐项真实跨源对撞。
FIXED_RULES: Dict[str, Any] = {
    "confirm_timeframe": "1h",
    "execution_timeframe": "15m",
    "entry_delay_minutes": 15,          # 确认后下一根 15m（12:00 确认 ⇒ 12:15 成交）
    "initial_stop_pct": 0.05,           # 初始止损 = 实际成交价 × 95%
    "trailing_low_pct": 0.05,           # 候选止损 = 新收盘 4h 最低价 × 95%，只上移
    "trailing_trigger_r": 0.0,          # 入场即跟踪，无 2R 门槛
    "max_holding_days": None,           # 不设持仓天数上限
    "taker_fee_bps": 5.0,               # 单边手续费 5bps
    "trail_effective_mode": "next_15m_after_4h_close",
}

#: 跟踪止损的生效时刻口径（已确定；`legacy_*` 只用于对照，不是可选口径）
TRAIL_EFFECTIVE_MODE = "next_15m_after_4h_close"
TRAIL_EFFECTIVE_MODE_LEGACY = "same_15m_as_4h_close"
ACTUAL_TRAIL_EFFECTIVE_MODE = TRAIL_EFFECTIVE_MODE


def get_actual_trail_effective_mode() -> str:
    """获取当前退出驱动实际采用的止损生效模式。"""
    return ACTUAL_TRAIL_EFFECTIVE_MODE


def set_actual_trail_effective_mode(mode: str) -> None:
    """设置当前退出驱动实际采用的止损生效模式（供测试或受控切换）。"""
    global ACTUAL_TRAIL_EFFECTIVE_MODE
    ACTUAL_TRAIL_EFFECTIVE_MODE = mode


def check_fixed_rules(engine_params: Optional[dict] = None,
                      frozen_cfg: Optional[Any] = None,
                      trail_effective_mode: Optional[str] = None) -> dict:
    """已确定语义 ↔ 引擎生效视图与冻结策略包逐项真实跨源对撞。任一项不符 ⇒ 开仓必须被阻塞。"""
    import paper_engine as PE
    p = engine_params if engine_params is not None else PE.approved_params()
    fc = frozen_cfg if frozen_cfg is not None else frozen_config()
    act_trail_mode = trail_effective_mode if trail_effective_mode is not None else get_actual_trail_effective_mode()
    rows = []

    def _row(name, expected, actual, source, note=""):
        rows.append({"name": name, "expected": expected, "actual": actual,
                     "source": source, "ok": (actual == expected), "note": note})

    _row("confirm_timeframe", FIXED_RULES["confirm_timeframe"],
         getattr(fc, "timeframe_event", None),
         "EventTrendConfig.timeframe_event",
         "冻结策略包推动周期必须为 1h")
    _row("execution_timeframe", FIXED_RULES["execution_timeframe"],
         p.get("detail_timeframe"),
         "paper_engine.approved_params()['detail_timeframe']",
         "引擎 detail_timeframe 必须为 15m")
    _row("entry_delay_minutes", FIXED_RULES["entry_delay_minutes"],
         p.get("signal_execution_delay_minutes"),
         "paper_engine.approved_params()",
         "引擎的 signal_execution_delay_minutes 必须等于 15，否则成交 bar 会回填确认根")
    _row("initial_stop_pct", FIXED_RULES["initial_stop_pct"],
         p.get("stoploss_s"), "paper_engine.approved_params()")
    _row("trailing_low_pct", FIXED_RULES["trailing_low_pct"],
         p.get("trail_d"), "paper_engine.approved_params()")
    _row("taker_fee_bps", FIXED_RULES["taker_fee_bps"],
         None if p.get("fee_each_side") is None else round(float(p["fee_each_side"]) * 10000, 6),
         "paper_engine.approved_params()")
    _row("trailing_trigger_r", FIXED_RULES["trailing_trigger_r"],
         getattr(fc, "trailing_trigger_r", None),
         "EventTrendConfig.trailing_trigger_r",
         "冻结策略包必须取消 2R 门槛，入场立即跟踪")
    _row("max_holding_days", FIXED_RULES["max_holding_days"],
         getattr(fc, "max_holding_days", None),
         "EventTrendConfig.max_holding_days",
         "冻结策略包必须取消持仓天数上限（不设强平）")
    _row("trail_effective_mode", FIXED_RULES["trail_effective_mode"],
         act_trail_mode,
         "accum_early_entry.get_actual_trail_effective_mode()",
         "跟踪止损生效口径必须为 4h 收盘后下一根 15m 生效")
    return {"rows": rows, "ok": all(r["ok"] for r in rows),
            "mismatches": [r for r in rows if not r["ok"]],
            "engine": {"_config_valid": p.get("_config_valid"),
                       "_source": p.get("_source"),
                       "view_sha256": p.get("view_sha256"),
                       "strategy_version": p.get("strategy_version")}}


# ══════════════════════════════════════════════════════════════════════════
# 二、配置：显式必填，没有默认值
# ══════════════════════════════════════════════════════════════════════════
#: 待决参数 ↔ 配置字段（与 `accum_research.PENDING_PARAMS` 一一对应，由 `mapping_check()` 硬校验）
PENDING_TO_CONFIG: Dict[str, Tuple[str, ...]] = {
    "local_resistance_definition": ("shared.local_resistance.mode",
                                    "shared.local_resistance.lookback_bars",
                                    "shared.local_resistance.swing_k"),
    "local_resistance_confirm": ("shared.local_resistance_confirm.mode",
                                 "shared.local_resistance_confirm.buffer_atr"),
    "sweep_bars": ("shared.sweep_recovery_scope.mode",
                   "shared.sweep_recovery_scope.max_bars"),
    "sell_pressure_baseline": ("shared.sell_pressure.baseline",
                               "shared.sell_pressure.n"),
    "sell_pressure_threshold": ("shared.sell_pressure.buy_ratio_mult",
                                "shared.sell_pressure.require"),
    "observe_deadline_hours": ("shared.observe_deadline_hours",),
    "pullback_deadline_hours": ("schemes.4c.pullback.deadline_hours",),
    "intrabar_break_cancel": ("shared.intrabar_break_cancel.mode",
                              "shared.intrabar_break_cancel.buffer_atr"),
    "entry_distance_limit": ("shared.entry_gate.entry_price_deviation_cap",),
    "oi_necessity": ("shared.oi_role",),
    "repeat_candidate_limit": ("shared.repeat_candidate_limit.per_event",
                               "shared.repeat_candidate_limit.cooldown_hours"),
    "risk_budget_new_scheme": ("conflict_resolution.risk_config",),
    "slippage_source": ("conflict_resolution.slippage_source",),
}

#: 三项「口径冲突」的登记（见 `accum_pending.CONFLICTS`）。本模块不改经济规则 ⇒
#: 只要求批准方**指名**用哪一套，再由账本如实记录引擎实际用了哪套；对不上就阻塞。
CONFLICT_FIELDS = ("conflict_resolution.slippage_source",
                   "conflict_resolution.risk_config",
                   "conflict_resolution.participation_cap")

REQUIRED_PATHS: Tuple[str, ...] = tuple(sorted(
    {p for paths in PENDING_TO_CONFIG.values() for p in paths}
    | set(CONFLICT_FIELDS)
    | {
        "schema_version", "kind", "approved", "approval_ref",
        # 扫低参照（04-B 漏了这两项 ⇒ 代码读不到会直接 KeyError，本单补上）
        "shared.sweep_reference.mode", "shared.sweep_reference.n",
        "shared.sweep_reference.must_be_above_box_low",
        "shared.sweep_max_overshoot_atr", "shared.sweep_min_recovery_atr",
        "shared.local_resistance.timeframe",
        "shared.resistance_qualify.min_gap_atr",
        "shared.sell_pressure.require_upper_half",
        "shared.sell_pressure.sell_pressure_mult",
        "shared.invalidate.close_below_l_bars", "shared.invalidate.l_overshoot_atr",
        "shared.armed_fail.bars", "shared.armed_fail.buffer_atr",
        "shared.confirm.require_bull_close",
        "shared.entry_gate.max_gap_up_atr",
        "exit.gap_model",
        "schemes.4a.enabled", "schemes.4a.confirm",
        "schemes.4b.enabled", "schemes.4b.confirm",
        "schemes.4c.enabled", "schemes.4c.confirm",
        "schemes.4c.pullback.zone_atr",
    }))

#: 已实现的谓词取值集合。批准值不在这里 ⇒ 报 unsupported，不猜。
IMPLEMENTED: Dict[str, set] = {
    "kind": {"approved", "fixture"},
    "shared.sweep_reference.mode": {"box_low", "prior_n_low"},
    "shared.sweep_recovery_scope.mode": {"single_bar", "multi_bar"},
    "shared.local_resistance.mode": {"swing_high_k3_lookback48", "prior_n_high", "box_high"},
    "shared.local_resistance.timeframe": {"1h", "4h"},
    "shared.local_resistance_confirm.mode": {"1h_close", "intrabar"},
    "shared.sell_pressure.baseline": {"v20_median", "prior_n_mean"},
    "shared.intrabar_break_cancel.mode": {"close_only", "intrabar"},
    "shared.oi_role": {"evidence_only", "required", "ignored"},
    "schemes.4a.confirm": {"close_above_resistance_first"},
    "schemes.4b.confirm": {"next_close_holds_above_resistance"},
    "schemes.4c.confirm": {"pullback_to_resistance_then_close_up"},
    "exit.gap_model": {"stop_price", "bar_open", "midpoint"},
    "conflict_resolution.slippage_source": {"strategy_package", "effective_view"},
    "conflict_resolution.risk_config": {"strategy_package", "effective_view"},
    "conflict_resolution.participation_cap": {"strategy_package", "effective_view"},
}

_MISSING = object()


def _dig(doc, dotted: str):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def config_path(path=None) -> Path:
    return Path(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH)


def load_config(path=None) -> dict:
    """读批准配置并做**硬校验**。缺文件/缺字段/取值未实现/已确定语义不符 ⇒ 不许开仓。"""
    p = config_path(path)
    out: Dict[str, Any] = {
        "schema_version": CONFIG_SCHEMA,
        "path": str(p),
        "present": p.exists(),
        "parse_ok": False,
        "config_sha256": None,
        "kind": None,
        "approved": False,
        "approval_ref": None,
        "missing": [],
        "unsupported": [],
        "fixture": False,
        "ready": False,
        "open_allowed": False,
        "blocked_by": [],
        "doc": None,
        "fixed_rules": None,
        "economic_caliber": None,
    }
    if not p.exists():
        out["missing"] = list(REQUIRED_PATHS)
        out["blocked_by"] = ["config_file_missing"]
        # 配置不在也要把「引擎实际是什么口径」「已确定语义对不对」摆出来，
        # 否则 `diagnose` 只剩一句「缺配置」，看不出到底能不能跑。
        out["economic_caliber"] = _economic_caliber({})
        out["fixed_rules"] = check_fixed_rules()
        out["reason"] = f"批准配置不存在：{p}（本模块不提供任何默认值）"
        return out
    raw = p.read_bytes()
    out["config_sha256"] = _sha256_bytes(raw)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        out["blocked_by"] = ["config_parse_error"]
        out["reason"] = f"配置无法解析：{exc}"
        return out
    if not isinstance(doc, dict):
        out["blocked_by"] = ["config_not_object"]
        out["reason"] = "配置顶层必须是对象"
        return out
    out["parse_ok"] = True
    out["doc"] = doc

    # 格式版本不符必须**关上闸门**，不能只记一条原因：
    # 04 版与本版字段语义不同，若只记 blocked_by 而不清 ready，一份「49 个路径恰好都在」
    # 的旧格式配置就会通过 ready 判定，把 open_allowed 打开（负向用例 N4 实测过）。
    schema_bad = doc.get("schema_version") != CONFIG_SCHEMA
    if schema_bad:
        out["blocked_by"].append("schema_version_mismatch")
        out["reason"] = (f"配置格式版本必须是 {CONFIG_SCHEMA}（读到 "
                         f"{doc.get('schema_version')!r}）。04 版字段语义与本版不同，"
                         f"不猜着迁移。")

    missing = []
    for path_key in REQUIRED_PATHS:
        v = _dig(doc, path_key)
        if v is _MISSING or v is None or (isinstance(v, str) and not v.strip()):
            missing.append(path_key)
    out["missing"] = missing

    unsupported = []
    for key, allowed in IMPLEMENTED.items():
        v = _dig(doc, key)
        if v is _MISSING or v is None:
            continue
        if v not in allowed:
            unsupported.append({"field": key, "value": v, "implemented": sorted(allowed)})
    out["unsupported"] = unsupported

    kind = doc.get("kind")
    out["kind"] = kind
    out["approved"] = bool(doc.get("approved") is True)
    out["approval_ref"] = doc.get("approval_ref")
    out["fixture"] = (kind == "fixture")
    out["ready"] = (not missing) and (not unsupported) and (not schema_bad)
    out["doc_sha256_declared"] = doc.get("config_sha256")

    # ── 经济口径：只**核对**声明与引擎实际，不替人择一 ──────────────────────
    cal = _economic_caliber(doc)
    out["economic_caliber"] = cal
    if cal.get("declared_mismatch"):
        out["ready"] = False
        out["blocked_by"].append("economic_caliber_declared_mismatch")

    # ── 已确定语义：配置齐了才去撞引擎（缺配置时撞了也没意义）───────────────
    if out["ready"]:
        fr = check_fixed_rules()
        out["fixed_rules"] = fr
        if not fr["ok"]:
            out["ready"] = False
            out["blocked_by"].append("fixed_rules_mismatch")

    # 开仓许可：三档
    if out["ready"] and out["approved"] and kind == "approved":
        out["open_allowed"] = True
        out["open_scope"] = "approved_rules"
    elif out["ready"] and kind == "fixture" and os.environ.get(FIXTURE_ENV) == "1":
        out["open_allowed"] = True
        out["open_scope"] = "fixture_chain_selfcheck"
    else:
        out["open_allowed"] = False
        out["open_scope"] = "blocked"
        if missing:
            out["blocked_by"].append("config_fields_missing")
        if unsupported:
            out["blocked_by"].append("config_value_unsupported")
        if out["ready"] and not out["approved"]:
            out["blocked_by"].append("not_approved")
        if out["ready"] and kind == "fixture" and os.environ.get(FIXTURE_ENV) != "1":
            out["blocked_by"].append("fixture_without_explicit_env")
    out["reason"] = ("允许开仓（" + out["open_scope"] + "）" if out["open_allowed"]
                     else (f"经济口径声明与引擎实际生效值不一致 ⇒ 阻塞开仓：{cal['declared_mismatch']}"
                           if cal.get("declared_mismatch")
                           else "未获批准配置 ⇒ 阻塞开仓；数据读取/观察/记录/隔离验证不受影响"))
    return out


def _economic_caliber(doc) -> dict:
    """滑点/风控/参与率：严格校验「声明值」与「引擎实际生效值」的一致性。

    本模块**不改生产经济规则**，因此配置校验必须硬拦截：
      · 声明为 `strategy_package`（3bps / 2000U / 3仓 / 30%单名 / 0.1%参与率）而引擎生效视图实际是
        5bps / 5000U / 10仓 / 10%单名 / 1%参与率 ⇒ 账本口径与声明不符；
      · 声明为 `effective_view` 时，同样严格校验引擎参数是否与生效视图一致；
      · 任何一项声明与引擎实际不一致，必须明确阻塞并列明具体差异，绝不允许静默按另一套值执行。
    """
    import paper_engine as PE
    p = PE.approved_params()
    eng_slip_bps = (None if p.get("slippage_each_side") is None
                    else round(float(p["slippage_each_side"]) * 10000, 6))
    eng_equity = float(p.get("initial_equity_USDT") or 0.0)
    eng_max_pos = int(p.get("max_positions") or 0)
    eng_single_cap = float(p.get("single_name_notional_equity_cap") or 0.0)
    eng_part_cap = float(p.get("entry_participation_previous_15m_quote_cap") or 0.0)

    decl = {
        "slippage_source": _dig(doc, "conflict_resolution.slippage_source"),
        "risk_config": _dig(doc, "conflict_resolution.risk_config"),
        "participation_cap": _dig(doc, "conflict_resolution.participation_cap"),
    }
    decl = {k: (None if v is _MISSING else v) for k, v in decl.items()}
    engine = {
        "slippage_each_side_bps": eng_slip_bps,
        "initial_equity_USDT": eng_equity,
        "max_positions": eng_max_pos,
        "single_name_notional_equity_cap": eng_single_cap,
        "entry_participation_previous_15m_quote_cap": eng_part_cap,
        "view_sha256": p.get("view_sha256"),
        "_source": p.get("_source"),
    }

    mismatches = []
    mismatch_details = {}

    # 1. 滑点一致性校验
    slip_decl = decl["slippage_source"]
    if slip_decl == "strategy_package":
        if eng_slip_bps != 3.0:
            diff_msg = f"滑点声明为策略包 3.0bps，但引擎实际生效为 {eng_slip_bps}bps"
            mismatches.append(diff_msg)
            mismatch_details["slippage_source"] = {
                "declared": "strategy_package (3.0bps)",
                "engine_actual": f"{eng_slip_bps}bps",
                "diff": diff_msg
            }
    elif slip_decl == "effective_view":
        if eng_slip_bps != 5.0:
            diff_msg = f"滑点声明为生效视图 5.0bps，但引擎实际生效为 {eng_slip_bps}bps"
            mismatches.append(diff_msg)
            mismatch_details["slippage_source"] = {
                "declared": "effective_view (5.0bps)",
                "engine_actual": f"{eng_slip_bps}bps",
                "diff": diff_msg
            }

    # 2. 风控与仓位一致性校验
    risk_decl = decl["risk_config"]
    if risk_decl == "strategy_package":
        if eng_equity != 2000.0 or eng_max_pos != 3 or abs(eng_single_cap - 0.3) > 1e-6:
            diff_msg = (f"风控声明为策略包（本金2000U/最多3仓/单名30%），"
                        f"但引擎实际生效为（本金{eng_equity:.0f}U/最多{eng_max_pos}仓/单名{eng_single_cap*100:.0f}%）")
            mismatches.append(diff_msg)
            mismatch_details["risk_config"] = {
                "declared": "strategy_package (2000U, 3 positions, 30% cap)",
                "engine_actual": f"{eng_equity:.0f}U, {eng_max_pos} positions, {eng_single_cap*100:.0f}% cap",
                "diff": diff_msg
            }
    elif risk_decl == "effective_view":
        if eng_equity != 5000.0 or eng_max_pos != 10 or abs(eng_single_cap - 0.1) > 1e-6:
            diff_msg = (f"风控声明为生效视图（本金5000U/最多10仓/单名10%），"
                        f"但引擎实际生效为（本金{eng_equity:.0f}U/最多{eng_max_pos}仓/单名{eng_single_cap*100:.0f}%）")
            mismatches.append(diff_msg)
            mismatch_details["risk_config"] = {
                "declared": "effective_view (5000U, 10 positions, 10% cap)",
                "engine_actual": f"{eng_equity:.0f}U, {eng_max_pos} positions, {eng_single_cap*100:.0f}% cap",
                "diff": diff_msg
            }

    # 3. 参与率一致性校验
    part_decl = decl["participation_cap"]
    if part_decl == "strategy_package":
        if abs(eng_part_cap - 0.001) > 1e-6:
            diff_msg = f"参与率声明为策略包（0.1%），但引擎实际生效为（{eng_part_cap*100:.1f}%）"
            mismatches.append(diff_msg)
            mismatch_details["participation_cap"] = {
                "declared": "strategy_package (0.1%)",
                "engine_actual": f"{eng_part_cap*100:.1f}%",
                "diff": diff_msg
            }
    elif part_decl == "effective_view":
        if abs(eng_part_cap - 0.01) > 1e-6:
            diff_msg = f"参与率声明为生效视图（1.0%），但引擎实际生效为（{eng_part_cap*100:.1f}%）"
            mismatches.append(diff_msg)
            mismatch_details["participation_cap"] = {
                "declared": "effective_view (1.0%)",
                "engine_actual": f"{eng_part_cap*100:.1f}%",
                "diff": diff_msg
            }

    mismatch_str = "；".join(mismatches) if mismatches else None

    return {
        "declared": decl,
        "engine": engine,
        "mismatches": mismatches,
        "mismatch_details": mismatch_details,
        "declared_mismatch": mismatch_str,
        "note": ("经济参数严格一致性校验：声明口径与引擎生效视图必须逐项吻合，"
                 "不一致时硬阻断开仓并列明差异，绝不静默执行。")
    }


def mapping_check() -> dict:
    """待决参数 ↔ 配置字段 一一对应校验（漏项/多出都算错）。"""
    import accum_research as AR
    pending = set(AR.pending_params())
    mapped = set(PENDING_TO_CONFIG)
    errs = []
    for k in sorted(pending - mapped):
        errs.append(f"pending_not_mapped:{k}")
    for k in sorted(mapped - pending):
        errs.append(f"mapped_but_not_pending:{k}")
    for k, paths in PENDING_TO_CONFIG.items():
        for p in paths:
            if p not in REQUIRED_PATHS:
                errs.append(f"mapped_path_not_required:{k}->{p}")
    return {"ok": not errs, "errors": errs,
            "pending_count": len(pending), "mapped_count": len(mapped),
            "required_fields": len(REQUIRED_PATHS)}


# ══════════════════════════════════════════════════════════════════════════
# 三、数据输入：1h/4h/15m 全部走冷+热合并（复用 03-B 的读取器）
# ══════════════════════════════════════════════════════════════════════════
class DataFeed:
    """按 (周期, 标的) 一次性装载窗口，之后只在本进程内二分取用。

    纪律（与 `research_15m` 一致，本层不新增任何合并规则）：
      · 冷+热合并只由 `MergedReader` 完成；
      · 缺口不补造：装载结果里的缺口段原样带出；
      · `available_at` 为空 ⇒ `available_at_missing=True`，**不当成可得时刻**。

    注意（本单踩过）：`load()` 命中缓存时返回的是**已装载的整段**，不是请求窗口。
    调用方若只想用某一段，必须自己按 `open_time` 过滤——`window()` 就是干这个的。
    """

    def __init__(self, hot_db=None, archive_root=None, exchange="binance", market="um"):
        import research_15m as R
        d_hot, d_cold = R.default_paths()
        self.hot_db = Path(os.environ.get("MARKET_DB") or hot_db or d_hot)
        self.archive_root = Path(os.environ.get("WX_ARCHIVE_DIR") or archive_root or d_cold)
        self.exchange, self.market = exchange, market
        self._R = R
        self._readers: Dict[str, Any] = {}
        self._cache: Dict[Tuple[str, str], List[dict]] = {}
        self._range: Dict[Tuple[str, str], Tuple[int, int]] = {}
        self._stats: Dict[Tuple[str, str], dict] = {}

    def reader(self, timeframe: str):
        if timeframe not in self._readers:
            self._readers[timeframe] = self._R.MergedReader(
                self.hot_db, self.archive_root, timeframe,
                self.exchange, self.market)
        return self._readers[timeframe]

    def load(self, timeframe: str, symbol: str, start_ms: int, end_ms: int) -> List[dict]:
        """装载窗口（返回**缓存内整段**，可能比请求宽；要精确窗口用 `window()`）。"""
        key = (timeframe, symbol)
        rng = self._range.get(key)
        if rng and rng[0] <= int(start_ms) and int(end_ms) <= rng[1]:
            return self._cache[key]
        lo = int(start_ms) if not rng else min(int(start_ms), rng[0])
        hi = int(end_ms) if not rng else max(int(end_ms), rng[1])
        rd = self.reader(timeframe)
        bars = [dict(b) for b in rd.bars(symbol, lo, hi)]
        self._cache[key] = bars
        self._range[key] = (lo, hi)
        self._stats[key] = rd.stats(symbol) or {}
        return bars

    def window(self, timeframe: str, symbol: str, start_ms: int, end_ms: int) -> List[dict]:
        """精确窗口：`start_ms <= open_time < end_ms`（先 load 再过滤，避开缓存整段）。"""
        bars = self.load(timeframe, symbol, start_ms, end_ms)
        return [b for b in bars
                if int(start_ms) <= int(b["open_time"]) < int(end_ms)]

    def stats(self, timeframe: str, symbol: str) -> dict:
        return self._stats.get((timeframe, symbol), {})

    def gaps(self, timeframe: str, symbol: str) -> dict:
        st = self.stats(timeframe, symbol)
        return (st.get("continuity") or {})

    def at_or_after(self, timeframe: str, symbol: str, ts_ms: int,
                    span_ms: int = 7 * DAY) -> Optional[dict]:
        """第一根 `open_time >= ts` 的已闭合 bar（按需装载，缺口不补造）。"""
        key = (timeframe, symbol)
        rng = self._range.get(key)
        if not rng or not (rng[0] <= int(ts_ms) and int(ts_ms) <= rng[1]):
            self.load(timeframe, symbol, int(ts_ms) - DAY, int(ts_ms) + span_ms)
        bars = self._cache.get(key) or []
        for b in bars:
            if int(b["open_time"]) >= int(ts_ms):
                return b
        return None

    def available_at_report(self, timeframe: str, symbol: str) -> dict:
        st = self.stats(timeframe, symbol)
        return st.get("available_at") or {}

    def provenance(self) -> dict:
        out = {}
        for tf, rd in self._readers.items():
            try:
                out[tf] = rd.provenance()
            except Exception as exc:                          # noqa: BLE001
                out[tf] = {"error": str(exc)}
        return out

    def close(self):
        for rd in self._readers.values():
            try:
                rd.close()
            except Exception:                                 # noqa: BLE001
                pass


# ══════════════════════════════════════════════════════════════════════════
# 四、箱体与指标：参数只从冻结配置取
# ══════════════════════════════════════════════════════════════════════════
def frozen_config():
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    return EventTrendConfig()


def _is_zero_flat(bar: dict) -> bool:
    """交易所零成交平价占位行（与 `unified_runtime._is_zero_flat` 同一判据）。"""
    try:
        return (float(bar["volume"]) == 0.0
                and float(bar["high"]) == float(bar["low"])
                and float(bar["open"]) == float(bar["close"]))
    except Exception:                                         # noqa: BLE001
        return False


def build_boxes(bars_4h: Sequence[dict], cfg=None) -> List[dict]:
    """按冻结门槛逐根 4h 判断能否建档。返回**所有**达标时刻（含未通过的原因）。

    与冻结实现对齐的三点（不自己发明）：
      · 切片 = 含当根的最近 `box_bars_4h` 根（`bars_4h[i+1-n:i+1]`）；
      · ATR = **连续** Wilder 递推（`features.compute_wilder_atr_series`），不是每根重算；
      · 预热门槛 = 已见 4h 根数 ≥ `atr_warmup_bars`，不足则不建档。
    """
    import numpy as np
    from 策略.WyckoffMaster.event_trend_v0_1.features import (
        compute_efficiency_ratio, compute_wilder_atr_series)

    cfg = cfg or frozen_config()
    n = int(cfg.box_bars_4h)
    highs = np.array([float(b["high"]) for b in bars_4h], dtype=np.float64)
    lows = np.array([float(b["low"]) for b in bars_4h], dtype=np.float64)
    closes = np.array([float(b["close"]) for b in bars_4h], dtype=np.float64)
    atr = compute_wilder_atr_series(highs, lows, closes, int(cfg.atr_period_4h))

    out: List[dict] = []
    for i in range(len(bars_4h)):
        row = {
            "index": i,
            "bar_open_ms": int(bars_4h[i]["open_time"]),
            "formation_ms": int(bars_4h[i]["close_time"]),
            "atr_bars_seen": i + 1,
            "zero_flat": _is_zero_flat(bars_4h[i]),
            "formed": False,
            "skip": None,
        }
        if i + 1 < n:
            row["skip"] = "window_short"
            out.append(row)
            continue
        if (i + 1) < int(cfg.atr_warmup_bars):
            row["skip"] = "atr_warmup"
            out.append(row)
            continue
        a = atr[i]
        row["atr"] = None if a is None else float(a)
        if a is None or not np.isfinite(a) or a <= 0:
            row["skip"] = "atr_invalid"
            out.append(row)
            continue
        if row["zero_flat"]:
            row["skip"] = "zero_flat_bar"
            out.append(row)
            continue
        seg_h = highs[i + 1 - n:i + 1]
        seg_l = lows[i + 1 - n:i + 1]
        seg_c = closes[i + 1 - n:i + 1]
        high_h = float(np.max(seg_h))
        low_l = float(np.min(seg_l))
        width_atr = (high_h - low_l) / float(a)
        er = compute_efficiency_ratio(seg_c)
        row.update({"high_h": high_h, "low_l": low_l,
                    "width_atr": width_atr, "er": er})
        if er is None:
            row["skip"] = "er_none"
        elif not (float(cfg.box_min_width_atr) <= width_atr <= float(cfg.box_max_width_atr)):
            row["skip"] = "width_out_of_range"
        elif er > float(cfg.er_threshold):
            row["skip"] = "er_above_threshold"
        else:
            row["formed"] = True
        out.append(row)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 五、观察状态机（甲乙丙共用起点，确认方式不同）
# ══════════════════════════════════════════════════════════════════════════
# 状态名沿用 `observe_store.STATES`，不另造词汇：
#   BOX / SWEEP / RETEST / CANDIDATE / INVALIDATED / EXPIRED / DATA_INSUFFICIENT
# 04-A 的 `NO_RESISTANCE`（压制位不合格＝没有空间，不是失败）在共享词表里没有对应项，
# 本模块**不扩共享词表**，而是写成 `INVALIDATED` + 固定前缀 `NO_RESISTANCE:`，
# 统计时按前缀单独计数（`terminal_reason` 字段）。

NO_RESISTANCE_PREFIX = "NO_RESISTANCE:"


def _iso(ms) -> Optional[str]:
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def _cond(name, value, requirement, result, note=None) -> dict:
    return {"name": name, "value": value, "requirement": requirement,
            "result": result, **({"note": note} if note else {})}


def _median(xs: Sequence[float]) -> Optional[float]:
    s = sorted(float(x) for x in xs)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _resistance(bars_1h: Sequence[dict], i: int, box: dict, cfg: dict,
                i_sweep: Optional[int] = None) -> Tuple[Optional[float], dict]:
    """局部压制位。三档实现（取值由配置给，不写死）：

    · `swing_high_k3_lookback48`：扫低根**之前** `lookback_bars` 根 1h 内，
      **最后一个**「左右各 `swing_k` 根都不高于它」的最高价；找不到回退为窗口最高。
    · `prior_n_high`：之前 `lookback_bars` 根的最高价（04-B 的口径，保留作对照）。
    · `box_high`：箱体上沿（等于原策略的突破位，提前量消失）。
    """
    doc = cfg["doc"]["shared"]["local_resistance"]
    mode = doc["mode"]
    if mode == "box_high":
        return float(box["high_h"]), {"mode": mode}
    L = int(doc.get("lookback_bars") or 0)
    if L <= 0 or i < L:
        return None, {"mode": mode, "lookback_bars": L, "reason": "not_enough_history"}
    seg = bars_1h[i - L:i]                        # **只用扫低根之前**的 bar
    highs = [float(b["high"]) for b in seg]
    if mode == "prior_n_high":
        return max(highs), {"mode": mode, "lookback_bars": L, "window_bars": len(highs)}
    if mode == "swing_high_k3_lookback48":
        k = int(doc.get("swing_k") or 0)
        if k <= 0:
            return None, {"mode": mode, "swing_k": k, "reason": "swing_k_invalid"}
        found = None
        for j in range(len(highs) - 1 - k, k - 1, -1):        # 从近到远找「最后一个」
            h = highs[j]
            left = highs[j - k:j]
            right = highs[j + 1:j + 1 + k]
            if len(left) == k and len(right) == k and h >= max(left) and h >= max(right):
                found = j
                break
        if found is not None:
            return highs[found], {"mode": mode, "swing_k": k, "lookback_bars": L,
                                  "index_in_window": found,
                                  "pivot": True, "window_bars": len(highs)}
        return max(highs), {"mode": mode, "swing_k": k, "lookback_bars": L,
                            "pivot": False, "fallback": "window_high",
                            "window_bars": len(highs)}
    return None, {"mode": mode, "reason": "unsupported"}


def _resistance_qualified(res: Optional[float], box: dict, atr: float, cfg: dict):
    """压制位是否合格：`L + gap·A <= R <= H - gap·A`。

    不合格不是「失败」，是「没有提前入场的空间」⇒ 记 `NO_RESISTANCE`。
    下界保证 5% 止损容得下，上界保证它比箱体上沿低（否则与原策略突破同义）。
    """
    gap = float(cfg["doc"]["shared"]["resistance_qualify"]["min_gap_atr"])
    if res is None:
        return False, "resistance_missing", {}
    lo = float(box["low_l"]) + gap * atr
    hi = float(box["high_h"]) - gap * atr
    detail = {"low_bound": lo, "high_bound": hi, "min_gap_atr": gap,
              "resistance": float(res), "atr": atr}
    if float(res) < lo:
        return False, "too_close_to_L", detail
    if float(res) > hi:
        return False, "too_close_to_H", detail
    return True, "ok", detail


def _sweep_check(bars_1h: Sequence[dict], i: int, box: dict, atr: float, cfg: dict):
    """扫低收回判定（事件起点）。返回 (ok, detail, conditions)。"""
    doc = cfg["doc"]["shared"]
    ref_mode = doc["sweep_reference"]["mode"]
    scope = doc["sweep_recovery_scope"]
    overshoot = float(doc["sweep_max_overshoot_atr"])
    recover = float(doc["sweep_min_recovery_atr"])
    must_above = bool(doc["sweep_reference"].get("must_be_above_box_low"))

    if ref_mode == "box_low":
        ref = float(box["low_l"])
        ref_detail = {"reference_mode": ref_mode}
    else:
        n = int(doc["sweep_reference"].get("n") or 0)
        if n <= 0 or i < n:
            return None, {"reference_mode": ref_mode, "reason": "not_enough_history"}, []
        ref = min(float(b["low"]) for b in bars_1h[i - n:i])
        ref_detail = {"reference_mode": ref_mode, "n": n}
        if must_above and ref <= float(box["low_l"]):
            # 「扫破局部支撑」必须高于箱体下沿，否则这条就是原策略的破 L 扫低
            return False, {**ref_detail, "reference": ref,
                           "box_low": float(box["low_l"]),
                           "reason": "reference_not_above_box_low"}, [
                _cond("sweep_reference_above_box_low", ref,
                      f"> {float(box['low_l']):.10g}", "fail")]

    max_bars = 1 if scope["mode"] == "single_bar" else int(scope.get("max_bars") or 1)
    lo = max(0, i - max_bars + 1)
    seg = bars_1h[lo:i + 1]
    lowest = min(float(b["low"]) for b in seg)
    cur = bars_1h[i]
    ok = (lowest < ref
          and lowest >= ref - overshoot * atr
          and float(cur["close"]) >= ref + recover * atr)
    detail = {**ref_detail, "reference": ref,
              "scope": scope["mode"], "max_bars": max_bars,
              "lowest_low": lowest, "close": float(cur["close"]),
              "overshoot_limit": ref - overshoot * atr,
              "recover_level": ref + recover * atr}
    conds = [
        _cond("sweep_lowest_below_reference", lowest, f"< {ref:.10g}",
              "pass" if lowest < ref else "fail"),
        _cond("sweep_overshoot_within", lowest, f">= {ref - overshoot * atr:.10g}",
              "pass" if lowest >= ref - overshoot * atr else "fail"),
        _cond("sweep_recover_close", float(cur["close"]), f">= {ref + recover * atr:.10g}",
              "pass" if float(cur["close"]) >= ref + recover * atr else "fail"),
    ]
    return ok, detail, conds


def _absorption(bars_1h: Sequence[dict], i: int, cfg: dict):
    """承接 / 卖压判定（**改过的语义，见工单第 1 条**）。

    04-B 的实现有两个错（本单修正，不是加参数）：
      ① 基准算的是「前 n 根**成交量**中位数」，而规则要的是「前 n 根**主动买占比**中位数」；
      ② 基准算出来只写进证据，**没有进入判定**（判定用的是绝对阈值 `buy_ratio_min`）。
    规则原文（04-A §6.三）：`扫低根主动买占比 ≥ 前 n 根中位数 × mult`，
    且收盘位于当根振幅上半区；卖压 = `quote_volume − taker_buy_quote_volume`，
    要求 `≤ 前 n 根中位数 × sell_pressure_mult`。

    缺字段 ⇒ 返回 None（证据不足），不充 0、不当成立。
    """
    doc = cfg["doc"]["shared"]["sell_pressure"]
    if not doc.get("require"):
        return True, {"skipped": "not_required"}, [
            _cond("absorption", None, "require=false", "not_required")]
    n = int(doc.get("n") or 0)
    mult = float(doc["buy_ratio_mult"])
    base_mode = doc["baseline"]
    require_upper = bool(doc.get("require_upper_half"))
    sp_mult = doc["sell_pressure_mult"]
    cur = bars_1h[i]

    def _ratio(b):
        qv = b.get("quote_volume")
        tbq = b.get("taker_buy_quote_volume")
        if qv in (None, 0) or tbq is None:
            return None, None
        return float(tbq) / float(qv), float(qv) - float(tbq)

    ratio, sell_amt = _ratio(cur)
    if ratio is None:
        return None, {"reason": "quote_volume_or_taker_buy_missing"}, [
            _cond("absorption_buy_ratio", None, "quote_volume 与 taker_buy_quote_volume 存在",
                  "not_evaluable", "该根缺字段或 quote_volume=0")]
    if n <= 0 or i < n:
        return None, {"reason": "baseline_window_short", "n": n}, [
            _cond("absorption_baseline", None, f"前 {n} 根", "not_evaluable",
                  "历史不足，无法算基准")]
    seg = bars_1h[i - n:i]
    pairs = [_ratio(b) for b in seg]
    hist_ratio = [r for r, _ in pairs if r is not None]
    hist_sell = [s for _, s in pairs if s is not None]
    if len(hist_ratio) < n or len(hist_sell) < n:
        return None, {"reason": "baseline_missing_fields", "n": n,
                      "have_ratio": len(hist_ratio), "have_sell": len(hist_sell)}, [
            _cond("absorption_baseline", len(hist_ratio), f"前 {n} 根都有字段",
                  "not_evaluable", "基准窗口内存在缺字段的 bar")]
    if base_mode == "v20_median":
        base_ratio = _median(hist_ratio)
        base_sell = _median(hist_sell)
    else:                                        # prior_n_mean
        base_ratio = sum(hist_ratio) / len(hist_ratio)
        base_sell = sum(hist_sell) / len(hist_sell)
    thr = float(base_ratio) * mult

    hi, lo, cl = float(cur["high"]), float(cur["low"]), float(cur["close"])
    upper_half = (cl >= lo + 0.5 * (hi - lo))
    conds = [
        _cond("absorption_buy_ratio", round(ratio, 10),
              f">= 基准 {base_ratio:.10g} × {mult} = {thr:.10g}",
              "pass" if ratio >= thr else "fail",
              f"baseline={base_mode},n={n}"),
    ]
    if require_upper:
        conds.append(_cond("absorption_close_upper_half", round(cl, 10),
                           f">= low + 0.5*(high-low) = {lo + 0.5 * (hi - lo):.10g}",
                           "pass" if upper_half else "fail"))
    sp_ok = True
    if sp_mult is not None:
        sp_thr = float(base_sell) * float(sp_mult)
        sp_ok = sell_amt <= sp_thr
        conds.append(_cond("sell_pressure_amount", round(sell_amt, 6),
                           f"<= 基准 {base_sell:.10g} × {sp_mult} = {sp_thr:.10g}",
                           "pass" if sp_ok else "fail",
                           "主动卖出额 = quote_volume − taker_buy_quote_volume（推导量）"))
    ok = (ratio >= thr) and sp_ok and (upper_half if require_upper else True)
    detail = {"buy_ratio": ratio, "baseline_buy_ratio": base_ratio,
              "mult": mult, "threshold": thr, "baseline": base_mode, "n": n,
              "sell_amount": sell_amt, "baseline_sell_amount": base_sell,
              "sell_mult": sp_mult, "require_upper_half": require_upper,
              # 06-03：把「判的是哪一根」写进证据（调用方不再靠推断）
              "bar_open_ms": int(cur["open_time"]),
              "bar_close_ms": int(cur["close_time"])}
    return ok, detail, conds


class SchemeObserver:
    """单个 (方案, 标的) 的观察推进器。一个箱体 → 一个事件。"""

    def __init__(self, scheme: str, symbol: str, box: dict, cfg: dict,
                 store, feed: DataFeed, *, strategy_id: str, rule_version: str):
        self.scheme = scheme
        self.symbol = symbol
        self.box = box
        self.cfg = cfg
        self.store = store
        self.feed = feed
        self.strategy_id = strategy_id
        self.rule_version = rule_version
        self.event_id: Optional[str] = None
        self.state = "BOX"
        self.sweep_low: Optional[float] = None
        self.sweep_ms: Optional[int] = None
        # 06-03：承接判定在**扫低发生根**求值一次，之后只读结果（不再按确认根重算）
        self.sweep_i: Optional[int] = None
        self.sweep_bar_ms: Optional[int] = None
        self.sweep_baseline_window: Optional[dict] = None
        self.sweep_absorption = None
        self.resistance: Optional[float] = None
        self.confirm_ms: Optional[int] = None
        self.substage: Optional[str] = None
        self.awaiting_hold_since_ms: Optional[int] = None
        self.pullback_deadline_ms: Optional[int] = None
        self.below_l_count = 0                      # 连续收盘破箱体下沿的根数
        self.armed_fail_count = 0                   # 乙组：连续收盘跌回压制位下方的根数
        self.candidates: List[dict] = []
        self.sweep_conditions: List[dict] = []
        self.pending_actions: List[dict] = []
        self.blocked_recorded = False
        self.done = False

    # ── 建档 ────────────────────────────────────────────────────────────
    def open_event(self, *, evidence=None, counter_evidence=None,
                   missing_evidence=None, data_quality="ok") -> str:
        box_id = f"box_{self.symbol}_{self.box['formation_ms']}"
        r = self.store.create_event(
            scheme=self.scheme, symbol=self.symbol, venue="binance",
            strategy_version=self.strategy_id, param_version=self.rule_version,
            box_version_id=box_id,
            structure_seen_ms=int(self.box["formation_ms"]),
            confirmed_ms=int(self.box["formation_ms"]),
            expires_at_ms=self._deadline_ms(),
            old_low=float(self.box["low_l"]), sweep_low=None,
            state="BOX", evidence=evidence, counter_evidence=counter_evidence,
            missing_evidence=missing_evidence, data_quality=data_quality,
            now_ms=int(self.box["formation_ms"]))
        self.event_id = r["event_id"]
        return self.event_id

    def _deadline_ms(self) -> int:
        hours = _dig(self.cfg.get("doc") or {}, "shared.observe_deadline_hours")
        if hours is _MISSING or hours is None:
            # 观察期限未批准 ⇒ 不编造期限：用箱体自身形成时刻，并让事件停在 BOX
            return int(self.box["formation_ms"])
        return int(self.box["formation_ms"]) + int(hours) * H

    # ── 逐根 1h ─────────────────────────────────────────────────────────
    def on_bar(self, bars_1h: Sequence[dict], i: int, atr: float) -> List[dict]:
        if self.done or not self.event_id:
            return []
        bar = bars_1h[i]
        close_ms = int(bar["close_time"])
        if close_ms <= int(self.box["formation_ms"]):
            return []                                    # 建档前/同刻的 bar 不推动事件
        acts: List[dict] = []

        dl = self._deadline_ms()
        if dl > int(self.box["formation_ms"]) and close_ms > dl:
            acts.append(self._close_action("EXPIRED", bar,
                                           "超过观察期限（配置 observe_deadline_hours）",
                                           {"deadline_ms": dl}))
            return acts

        # 结构失效对所有状态生效（连续 n 根收盘破 L / 任一根低点破 L−kA）
        inv = self._structural_invalid(bar, atr)
        if inv:
            acts.append(self._close_action("INVALIDATED", bar, inv[0], inv[1]))
            return acts

        if self.state == "BOX":
            if not self.cfg.get("ready"):
                if not self.blocked_recorded:
                    self.blocked_recorded = True
                    acts.append(self._blocked_config_action(bar))
                return acts
            ok, detail, conds = _sweep_check(bars_1h, i, self.box, atr, self.cfg)
            if ok is None:
                acts.append(self._close_action("DATA_INSUFFICIENT", bar,
                                               "扫低参照所需历史不足", {"detail": detail}))
                return acts
            if ok:
                # ── 06-03 时序修正 ──────────────────────────────────────────
                # 承接（主动买占比 / 卖压）必须判在**扫低发生根**：
                #   依据 evidence/ACCUM-EARLY-04-A/README.md:53（承接/卖压的可得时点
                #   = 扫低根 1h 收盘）与 02_规则接口.md:134（基准 = 扫低根**之前**
                #   20 根 1h 的中位数）。
                # 修前 `_absorption(bars_1h, i, ...)` 写在 `_confirm_actions` 里，
                # `i` 是**确认突破根** ⇒ 读错 bar、基准窗口整体右移（06-W2/01 修前轨迹
                # 实测：三类用例的承接判定下标全部 = 确认根）。
                # 扫低发生根 = scope 窗口内最低价那一根；`single_bar` 下就是本根。
                # multi_bar 扫低根定义（工单 W1 写死）：扫低窗口内按最低价最低的一根为扫低根，并列取最早一根。
                scope_bars = int(detail.get("max_bars") or 1)
                lo_i = max(0, i - scope_bars + 1)
                i_sweep = min(range(lo_i, i + 1),
                              key=lambda j: (float(bars_1h[j]["low"]), j))
                ab = _absorption(bars_1h, i_sweep, self.cfg)
                self.sweep_i = i_sweep
                self.sweep_bar_ms = int(bars_1h[i_sweep]["close_time"])
                self.sweep_absorption = ab
                n_base = int(self.cfg["doc"]["shared"]["sell_pressure"].get("n") or 0)
                self.sweep_baseline_window = (
                    {"from_ms": int(bars_1h[i_sweep - n_base]["open_time"]),
                     "to_ms": int(bars_1h[i_sweep - 1]["close_time"]),
                     "n": n_base} if (n_base > 0 and i_sweep - n_base >= 0) else None)
                self.sweep_low = detail["lowest_low"]
                self.sweep_ms = close_ms
                self.sweep_conditions = conds
                self.state = "SWEEP"
                self.substage = "await_confirm"
                acts.append(self._transition("SWEEP", bar, "扫低收回成立", {
                    "conditions": conds, "detail": detail,
                    "sweep_bar_i": i_sweep,
                    "sweep_scope_mode": detail.get("scope"),
                    "multi_bar_scope_unverified": False,
                    "absorption_bar_close_ms": self.sweep_bar_ms,
                    "absorption_baseline_window": self.sweep_baseline_window,
                    "absorption": {"ok": ab[0], "detail": ab[1],
                                   "conditions": ab[2]}}))
            else:
                self._last_conditions = conds
            return acts

        if self.state == "SWEEP":
            return self._confirm_actions(bars_1h, i, atr, bar)

        if self.state == "RETEST":
            if self.scheme == "4b":
                return self._hold_actions(bar, atr)
            if self.scheme == "4c":
                return self._pullback_actions(bars_1h, i, atr, bar)
        return acts

    # ── 失效判定 ────────────────────────────────────────────────────────
    def _structural_invalid(self, bar, atr):
        """箱体层面的失效（对 BOX/SWEEP/RETEST 全部生效）。

        只实现规则文档里写着的两条（`04-A §3`）：
        · 连续 `close_below_l_bars` 根 1h 收盘 < 箱体下沿 L；
        · 破箱体下沿超过 `l_overshoot_atr` × ATR ——
          盘中破位是否算取消由 `intrabar_break_cancel.mode` 决定
          （`close_only` ⇒ 看收盘；`intrabar` ⇒ 看最低价）。
        """
        if not self.cfg.get("ready") or not self.cfg.get("doc"):
            return None
        doc = _dig(self.cfg["doc"], "shared.invalidate")
        mode = _dig(self.cfg["doc"], "shared.intrabar_break_cancel.mode")
        if doc is _MISSING or not isinstance(doc, dict) or doc.get("close_below_l_bars") is None or doc.get("l_overshoot_atr") is None:
            return None
        l = float(self.box["low_l"])
        n_need = int(doc["close_below_l_bars"])
        k = float(doc["l_overshoot_atr"])
        if float(bar["close"]) < l:
            self.below_l_count += 1
        else:
            self.below_l_count = 0
        if n_need > 0 and self.below_l_count >= n_need:
            return ("连续 %d 根 1h 收盘跌破箱体下沿 %s ⇒ 失效" % (n_need, l),
                    {"kind": "close_below_L", "count": self.below_l_count,
                     "low_l": l, "close": float(bar["close"])})
        limit = l - k * atr
        used = float(bar["low"]) if mode == "intrabar" else float(bar["close"])
        if used < limit:
            return ("%s 跌破箱体下沿-%sA（%s）⇒ 失效（%s）"
                    % ("1h 最低价" if mode == "intrabar" else "1h 收盘", k, limit, mode),
                    {"kind": "break_below_L_overshoot", "limit": limit,
                     "used": used, "mode": mode, "low_l": l, "k": k})
        return None

    # ── 确认 ────────────────────────────────────────────────────────────
    def _confirm_actions(self, bars_1h, i, atr, bar):
        """SWEEP 阶段：甲/乙/丙 的首次确认。"""
        close_ms = int(bar["close_time"])
        # 工单 W1 修正：局部压制位按方案规定取「扫低根之前 48 根」，严禁读取确认根及未来 K 线
        if self.sweep_i is None:
            return [self._close_action(
                "DATA_INSUFFICIENT", bar,
                "扫低发生根未记录（内部状态缺失）⇒ 无法定位局部压制位窗口",
                {"confirm_bar_i": i})]
        res, res_detail = _resistance(bars_1h, self.sweep_i, self.box, self.cfg)
        if res_detail is not None:
            res_detail = {**res_detail,
                          "judged_at": "sweep_bar",
                          "sweep_bar_i": self.sweep_i,
                          "sweep_bar_close_ms": self.sweep_bar_ms}
        if res is None:
            return [self._close_action("DATA_INSUFFICIENT", bar,
                                       "局部压制位所需历史不足",
                                       {"resistance_detail": res_detail})]
        ok_q, why_q, q_detail = _resistance_qualified(res, self.box, atr, self.cfg)
        doc = self.cfg["doc"]["shared"]["local_resistance_confirm"]
        buf = float(doc["buffer_atr"]) * atr
        level = res + buf
        if doc["mode"] == "intrabar":
            hit = float(bar["high"]) > level
            used = float(bar["high"])
        else:
            hit = float(bar["close"]) > level
            used = float(bar["close"])
        conds = [
            _cond("local_resistance", res, res_detail, "pass"),
            _cond("resistance_qualified", res,
                  f"L+{q_detail.get('min_gap_atr')}A <= R <= H-{q_detail.get('min_gap_atr')}A",
                  "pass" if ok_q else "fail", why_q),
            _cond("confirm_above_resistance", used,
                  f"{doc['mode']} > {level:.10g}", "pass" if hit else "fail"),
        ]
        # 承接：只读**扫低根**求值的那一份结果（06-03）。
        # 绝不在这里用确认根重算——那正是修前的缺陷（突破根偷换了承接判断）。
        if self.sweep_absorption is None:
            return [self._close_action(
                "DATA_INSUFFICIENT", bar,
                "承接未在扫低根求值（内部状态缺失）⇒ 拒绝用确认根替代",
                {"conditions": conds, "sweep_bar_i": self.sweep_i})]
        sp_ok, sp_detail, sp_conds = self.sweep_absorption
        sp_detail = {**sp_detail,
                     "judged_at": "sweep_bar",
                     "judged_bar_i": self.sweep_i,
                     "judged_bar_close_ms": self.sweep_bar_ms,
                     "baseline_window": self.sweep_baseline_window}
        conds.extend(sp_conds)
        if not ok_q:
            # 没有空间不是失败 ⇒ NO_RESISTANCE（共享词表里落到 INVALIDATED + 前缀）
            return [self._close_action(
                "INVALIDATED", bar, NO_RESISTANCE_PREFIX + why_q,
                {"conditions": conds, "resistance_detail": res_detail,
                 "qualify_detail": q_detail, "terminal_reason": "NO_RESISTANCE"})]
        if not hit:
            self._last_conditions = conds
            return []
        bull_ok = True
        if bool(self.cfg["doc"]["shared"]["confirm"]["require_bull_close"]):
            bull_ok = float(bar["close"]) >= float(bar["open"])
            conds.append(_cond("confirm_bar_is_bull", round(float(bar["close"]), 12),
                               f">= open({float(bar['open']):.10g})",
                               "pass" if bull_ok else "fail"))
        if not bull_ok:
            self._last_conditions = conds
            return []
        self.resistance = res
        if sp_ok is None:
            # 06-03：原因写到「扫低根 + 缺什么」，不再只写一句笼统的缺字段
            return [self._close_action(
                "DATA_INSUFFICIENT", bar,
                ("承接在扫低根（%s）无法判定：%s"
                 % (_iso(self.sweep_bar_ms), sp_detail.get("reason"))),
                {"conditions": conds, "detail": sp_detail,
                 "sweep_bar_i": self.sweep_i,
                 "sweep_bar_close_ms": self.sweep_bar_ms})]
        if sp_ok is False:
            self._last_conditions = conds
            return []                                    # 收上但承接不达标 ⇒ 不确认，继续观察
        self.confirm_ms = close_ms
        if self.scheme == "4a":
            return [self._candidate_action(bar, conds, "甲：首次 1h 收盘收上局部压制位")]
        if self.scheme == "4b":
            self.state = "RETEST"
            self.substage = "await_hold"
            self.awaiting_hold_since_ms = close_ms
            return [self._transition("RETEST", bar,
                                     "乙：已收上压制位，等下一根 1h 收盘守位",
                                     {"conditions": conds, "substage": "await_hold"})]
        pull = self.cfg["doc"]["schemes"]["4c"]["pullback"]
        self.state = "RETEST"
        self.substage = "await_pullback"
        self.pullback_deadline_ms = close_ms + int(pull["deadline_hours"]) * H
        return [self._transition("RETEST", bar,
                                 "丙：已收上压制位，等回抽到压制位附近后转强",
                                 {"conditions": conds, "substage": "await_pullback",
                                  "pullback_zone_atr": pull["zone_atr"],
                                  "pullback_deadline_ms": self.pullback_deadline_ms})]

    def _hold_actions(self, bar, atr):
        """乙：下一根 1h 收盘仍守住同一压制位；连续 `armed_fail.bars` 根跌回则失效。"""
        close_ms = int(bar["close_time"])
        level = float(self.resistance)
        doc = self.cfg["doc"]["shared"]["armed_fail"]
        buf = float(doc["buffer_atr"]) * atr
        fail_level = level - buf
        holds = float(bar["close"]) > level
        if holds:
            conds = [_cond("next_close_holds_above_resistance", float(bar["close"]),
                           f"> {level:.10g}", "pass")]
            return [self._candidate_action(bar, conds, "乙：下一根收盘守住压制位")]
        if float(bar["close"]) < fail_level:
            self.armed_fail_count += 1
        else:
            self.armed_fail_count = 0
        conds = [_cond("next_close_holds_above_resistance", float(bar["close"]),
                       f"> {level:.10g}", "fail"),
                 _cond("armed_fail_count", self.armed_fail_count,
                       f"< {int(doc['bars'])} 根连续跌回 {fail_level:.10g} 下方",
                       "pass" if self.armed_fail_count < int(doc["bars"]) else "fail")]
        if self.armed_fail_count >= int(doc["bars"]):
            return [self._close_action("INVALIDATED", bar,
                                       "乙：连续 %d 根收盘跌回压制位下方" % self.armed_fail_count,
                                       {"conditions": conds, "level": level,
                                        "fail_level": fail_level,
                                        "close": float(bar["close"])})]
        self._last_conditions = conds
        return []

    def _pullback_actions(self, bars_1h, i, atr, bar):
        """丙：回抽到压制位附近（≤ zone_atr × ATR）且当根收盘转强（≥ open）。"""
        close_ms = int(bar["close_time"])
        pull = self.cfg["doc"]["schemes"]["4c"]["pullback"]
        zone = float(self.resistance) + float(pull["zone_atr"]) * atr
        low = float(bar["low"])
        close = float(bar["close"])
        openp = float(bar["open"])
        in_zone = low <= zone
        turns_up = close >= openp and close > float(self.resistance)
        conds = [
            _cond("pullback_into_zone", low, f"<= {zone:.10g}", "pass" if in_zone else "fail"),
            _cond("pullback_close_turns_up", close,
                  f">= open({openp:.10g}) 且 > resistance({float(self.resistance):.10g})",
                  "pass" if turns_up else "fail"),
        ]
        if in_zone and turns_up:
            return [self._candidate_action(bar, conds, "丙：回抽到压制位附近后 1h 收盘转强")]
        if self.pullback_deadline_ms and close_ms > self.pullback_deadline_ms:
            return [self._close_action("EXPIRED", bar, "丙：回抽等待超期",
                                       {"conditions": conds,
                                        "deadline_ms": self.pullback_deadline_ms})]
        self._last_conditions = conds
        return []

    # ── 候选 ────────────────────────────────────────────────────────────
    def _candidate_action(self, bar, conds, reason) -> dict:
        """生成候选，并把**完整条件真值表**写进记录（不是只给一个分数）。"""
        confirm_ms = int(bar["close_time"])
        doc = self.cfg["doc"]["shared"]
        delay_ms = int(FIXED_RULES["entry_delay_minutes"]) * 60_000
        earliest = confirm_ms + delay_ms
        bar15 = self.feed.at_or_after("15m", self.symbol, earliest)
        gap = None
        entry_ref = None
        entry_bar = None
        avail_at = None
        dev = None
        table: List[dict] = list(self.sweep_conditions or [])
        table.extend(conds)

        hours = doc.get("observe_deadline_hours")
        elapsed_h = (confirm_ms - int(self.box["formation_ms"])) / H
        table.append(_cond("observe_deadline_hours", round(elapsed_h, 3),
                           f"<= {hours}" if hours is not None else "批准值",
                           ("pass" if hours is not None and elapsed_h <= float(hours)
                            else "fail") if hours is not None else "not_evaluable"))

        if bar15 is None:
            gap = {"field": "entry_15m_bar", "reason": "earliest_fill 之后没有已闭合 15m bar",
                   "from_ms": earliest, "to_ms": None}
            status, status_reason = "NO_ENTRY_BAR", "成交 bar 不存在（数据缺口，不补造）"
            table.append(_cond("entry_15m_bar_exists", None,
                               f"存在 open_time>={_iso(earliest)} 的已闭合 15m bar", "fail"))
        else:
            entry_bar = int(bar15["open_time"])
            entry_ref = float(bar15["open"])
            avail_at = bar15.get("available_at")
            dev = (entry_ref - float(bar["close"])) / float(bar["close"])
            cap = float(doc["entry_gate"]["entry_price_deviation_cap"])
            gap_atr = float(doc["entry_gate"]["max_gap_up_atr"])
            gap_limit = float(bar["close"]) + gap_atr * float(self.box["atr"])
            table.append(_cond("entry_15m_bar_exists", _iso(entry_bar),
                               f"存在 open_time>={_iso(earliest)} 的已闭合 15m bar", "pass"))
            table.append(_cond("entry_delay_respected", _iso(entry_bar),
                               f"bar_open >= confirm+{delay_ms // 60000}min",
                               "pass" if entry_bar >= earliest else "fail"))
            table.append(_cond("entry_price_deviation", round(dev, 6),
                               f"|x| <= {cap}", "pass" if abs(dev) <= cap else "fail"))
            table.append(_cond("entry_gap_up_within", entry_ref,
                               f"<= confirm_close + {gap_atr}A = {gap_limit:.10g}",
                               "pass" if entry_ref <= gap_limit else "fail"))
            table.append(_cond("entry_bar_available_at_proven", _iso(avail_at),
                               "有值（否则只能声称计算不超前）",
                               "pass" if avail_at is not None else "not_proven",
                               None if avail_at is not None else
                               f"成交 bar 来自 {bar15.get('source') or '未知层'}，"
                               f"该层没有 available_at 证据 ⇒ 只能声称「计算不超前」，"
                               f"不能证明「当时可得」（缺口已落 data_gap，不补造）"))
            if entry_ref > gap_limit:
                status, status_reason = "CANCELLED", (
                    f"成交根开盘跳空 {entry_ref - float(bar['close']):.10g} 超过 "
                    f"{gap_atr}A（{gap_limit:.10g}）")
                gap = {"field": "entry_gap_up", "reason": status_reason,
                       "from_ms": entry_bar, "to_ms": entry_bar}
            elif abs(dev) > cap:
                status, status_reason = "CANCELLED", (
                    f"入场偏离 {dev:.6f} 超过上限 {cap}")
                gap = {"field": "entry_price_deviation", "reason": status_reason,
                       "from_ms": entry_bar, "to_ms": entry_bar}
            else:
                status, status_reason = "WAIT_ENTRY", "条件成立，待委托"

        oi_role = doc.get("oi_role")
        table.append(_cond("oi_role", oi_role,
                           "required ⇒ 必须通过；evidence_only ⇒ 只记录",
                           "not_required" if oi_role in ("evidence_only", "ignored")
                           else "not_evaluable",
                           "本单未把 OI 接进入场门槛，角色由配置决定"))

        if bar15 is not None and avail_at is None:
            add_gap_once(self.store, self.event_id, "entry_15m_available_at",
                                    f"{bar15.get('source') or '未知层'} 层 15m bar 的 "
                                    f"available_at 为空 ⇒ 只能声称计算不超前，"
                                    f"不能证明当时可得",
                                    from_ms=entry_bar, to_ms=entry_bar)
        if gap:
            add_gap_once(self.store, self.event_id, gap["field"], gap["reason"],
                         from_ms=gap["from_ms"], to_ms=gap["to_ms"])

        # 幂等（本单新增）：同一事件、同一确认时刻的候选只落一次。
        # 不加这一条，重复扫描会给同一事件再插一条候选（`observe_candidate` 的幂等只在
        # 事件级），于是「重复扫描无副作用」只能对订单成立、对候选不成立。
        dup = self.store.con.execute(
            "SELECT candidate_id, signal_uid FROM accum05_candidate "
            "WHERE event_id=? AND confirm_close_ms=?",
            (self.event_id, confirm_ms)).fetchone()
        if dup:
            cand_id = dup["candidate_id"]
            signal_uid = dup["signal_uid"] or f"accum05_{self.scheme}_{self.symbol}_{confirm_ms}"
            self.state = "CANDIDATE"
            self.done = True
            return {"kind": "candidate", "candidate_id": cand_id, "status": status,
                    "status_reason": status_reason, "reason": reason,
                    "confirm_close_ms": confirm_ms, "earliest_entry_ms": earliest,
                    "entry_bar_open_ms": entry_bar, "entry_ref_price": entry_ref,
                    "conditions": table, "signal_uid": signal_uid, "dedup": True}

        r = self.store.add_candidate(
            self.event_id, scheme=self.scheme, created_ms=confirm_ms,
            local_support_price=self.resistance, entry_price_ref=entry_ref,
            state="WAIT_ENTRY" if status == "WAIT_ENTRY" else "CANCELLED",
            note=json.dumps({"status": status, "status_reason": status_reason,
                             "conditions": table}, ensure_ascii=False))
        cand_id = r["candidate_id"]
        self.state = "CANDIDATE"
        self.done = True
        signal_uid = f"accum05_{self.scheme}_{self.symbol}_{confirm_ms}"
        self.candidates.append({
            "candidate_id": cand_id, "event_id": self.event_id, "scheme": self.scheme,
            "signal_uid": signal_uid,
            "symbol": self.symbol, "confirm_close_ms": confirm_ms,
            "earliest_entry_ms": earliest, "entry_bar_open_ms": entry_bar,
            "entry_ref_price": entry_ref, "entry_bar_available_at": avail_at,
            "resistance": self.resistance, "deviation": dev,
            "status": status, "status_reason": status_reason,
            "conditions": table, "gap": gap,
        })
        return {"kind": "candidate", "candidate_id": cand_id, "status": status,
                "status_reason": status_reason, "reason": reason,
                "confirm_close_ms": confirm_ms, "earliest_entry_ms": earliest,
                "entry_bar_open_ms": entry_bar, "entry_ref_price": entry_ref,
                "conditions": table, "signal_uid": signal_uid}

    def _blocked_config_action(self, bar) -> dict:
        """配置未批准：如实记录「卡在哪一项」，状态仍停在 BOX（不是数据不足）。"""
        cfg = self.cfg
        missing = cfg.get("missing") or []
        unsupported = cfg.get("unsupported") or []
        conds = []
        for p in missing:
            conds.append(_cond(p, None, "批准值", "not_evaluable", "配置缺该项"))
        for u in unsupported:
            conds.append(_cond(u["field"], u["value"],
                               "已实现取值：" + ",".join(u["implemented"]),
                               "not_evaluable", "批准值未实现 ⇒ 不猜"))
        self.blocked_recorded = True
        return {"kind": "blocked_config", "event_id": self.event_id,
                "at_ms": int(bar["close_time"]),
                "reason": "配置未批准（不是数据不足）",
                "missing": missing, "unsupported": unsupported,
                "conditions": conds}

    # ── 迁移工具 ────────────────────────────────────────────────────────
    def _transition(self, to_state, bar, reason, payload) -> dict:
        at = int(bar["close_time"])
        self.store.transition(self.event_id, self.state, to_state, reason, at,
                              payload=payload)
        prev = self.state
        self.state = to_state
        return {"kind": "transition", "from": prev, "to": to_state,
                "at_ms": at, "reason": reason, "payload": payload}

    def _close_action(self, state, bar, reason, payload) -> dict:
        at = int(bar["close_time"])
        self.store.close_event(self.event_id, state, reason, at)
        self.state = state
        self.done = True
        return {"kind": "close", "state": state, "at_ms": at,
                "reason": reason, "payload": payload}


# ══════════════════════════════════════════════════════════════════════════
# 六、候选级 / 退出级记录表（与 observe_store 同一个库，只做加性建表）
# ══════════════════════════════════════════════════════════════════════════
EXTRA_DDL = """
CREATE TABLE IF NOT EXISTS accum05_candidate (
    candidate_id        TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL,
    scheme              TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    confirm_close_ms    INTEGER NOT NULL,
    earliest_entry_ms   INTEGER,
    entry_bar_open_ms   INTEGER,
    entry_bar_source    TEXT,
    entry_ref_price     REAL,
    resistance          REAL,
    deviation           REAL,
    status              TEXT NOT NULL,
    status_reason       TEXT,
    conditions_json     TEXT,
    gaps_json           TEXT,
    config_sha256       TEXT,
    config_kind         TEXT,
    rule_version        TEXT,
    signal_uid          TEXT,
    created_ms          INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS accum05_open_attempt (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id        TEXT NOT NULL,
    scheme              TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    at_ms               INTEGER NOT NULL,
    config_sha256       TEXT,
    config_kind         TEXT,
    allowed             INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    missing_json        TEXT,
    paper_result_json   TEXT
);
CREATE TABLE IF NOT EXISTS accum05_exit (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme              TEXT NOT NULL,
    position_id         INTEGER NOT NULL,
    symbol              TEXT NOT NULL,
    entry_ts            INTEGER,
    exit_ts             INTEGER,
    exit_class          TEXT,
    gap                 INTEGER NOT NULL DEFAULT 0,
    gap_size            REAL,
    gap_model           TEXT,
    stop_used           REAL,
    initial_stop        REAL,
    raw_price           REAL,
    fill_price          REAL,
    net_pnl             REAL,
    trail_updates       INTEGER,
    exit_rules          TEXT,
    trail_effective_mode TEXT,
    bar_source          TEXT,
    available_at_proven INTEGER,
    detail_json         TEXT,
    created_ms          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_accum05_cand_event ON accum05_candidate(event_id);
CREATE INDEX IF NOT EXISTS ix_accum05_attempt_cand ON accum05_open_attempt(candidate_id);
CREATE INDEX IF NOT EXISTS ix_accum05_exit_pos ON accum05_exit(position_id);
"""


def ensure_tables(store) -> None:
    store.con.executescript(EXTRA_DDL)
    cols = {r[1] for r in store.con.execute("PRAGMA table_info(accum05_candidate)")}
    if "signal_uid" not in cols:                       # 加性迁移，不改既有列语义
        store.con.execute("ALTER TABLE accum05_candidate ADD COLUMN signal_uid TEXT")
    if "rule_version" not in cols:
        store.con.execute("ALTER TABLE accum05_candidate ADD COLUMN rule_version TEXT")
    store.con.commit()


def open_store(path=None):
    """打开研究观察库（**必须是研究路径**；`ObserveStore.assert_isolated()` 会核对）。"""
    import observe_store as OS
    st = OS.ObserveStore(path or OBSERVE_DB)
    st.assert_isolated()
    ensure_tables(st)
    return st


def record_candidate(store, cand: dict, cfg: dict) -> None:
    store.con.execute(
        """INSERT OR REPLACE INTO accum05_candidate(
               candidate_id, event_id, scheme, symbol, confirm_close_ms,
               earliest_entry_ms, entry_bar_open_ms, entry_bar_source,
               entry_ref_price, resistance, deviation, status, status_reason,
               conditions_json, gaps_json, config_sha256, config_kind,
               rule_version, signal_uid, created_ms)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cand["candidate_id"], cand["event_id"], cand["scheme"], cand["symbol"],
         int(cand["confirm_close_ms"]), cand.get("earliest_entry_ms"),
         cand.get("entry_bar_open_ms"), cand.get("entry_bar_source"),
         cand.get("entry_ref_price"), cand.get("resistance"), cand.get("deviation"),
         cand["status"], cand.get("status_reason"),
         json.dumps(cand.get("conditions") or [], ensure_ascii=False),
         json.dumps(cand.get("gap") or {}, ensure_ascii=False),
         cfg.get("config_sha256"), cfg.get("kind"), RULE_VERSION,
         cand.get("signal_uid"), int(cand["confirm_close_ms"])))
    store.con.commit()


def add_gap_once(store, event_id: str, field: str, reason: str, *,
                 from_ms: int, to_ms: int) -> bool:
    """缺口记录幂等：同一 (事件, 字段, 起, 止) 只写一行。

    `observe_store.add_data_gap` 是无条件 INSERT，重复扫描/重启会把同一条缺口
    再插一遍 —— 实测冷窗口 2 条缺口被写了 3 遍（表里 6 行），于是「缺口数」这个
    指标会随重跑次数增长，读的人会误以为缺口变多了。本单不改共享模块，在调用侧
    先查后写。

    返回 True 表示这次真的写入了。
    """
    try:
        hit = store.con.execute(
            "SELECT 1 FROM observe_data_gap WHERE event_id=? AND field=? "
            "AND from_ms=? AND to_ms=? LIMIT 1",
            (event_id, field, int(from_ms), int(to_ms))).fetchone()
    except Exception:                                          # noqa: BLE001
        hit = None                       # 查不了就照写：宁可重复，也不要丢记录
    if hit:
        return False
    store.add_data_gap(event_id, field, reason, from_ms=from_ms, to_ms=to_ms)
    return True


def record_attempt(store, cand: dict, cfg: dict, *, allowed: bool, reason: str,
                   missing=None, paper_result=None, at_ms=None) -> None:
    store.con.execute(
        """INSERT INTO accum05_open_attempt(candidate_id, scheme, symbol, at_ms,
               config_sha256, config_kind, allowed, reason, missing_json,
               paper_result_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (cand["candidate_id"], cand["scheme"], cand["symbol"],
         int(at_ms if at_ms is not None else time.time() * 1000),
         cfg.get("config_sha256"), cfg.get("kind"), 1 if allowed else 0, reason,
         json.dumps(missing or [], ensure_ascii=False),
         json.dumps(paper_result or {}, ensure_ascii=False, default=str)))
    store.con.commit()


def record_exit(store, row: dict) -> None:
    store.con.execute(
        """INSERT INTO accum05_exit(
               scheme, position_id, symbol, entry_ts, exit_ts, exit_class, gap,
               gap_size, gap_model, stop_used, initial_stop, raw_price, fill_price,
               net_pnl, trail_updates, exit_rules, trail_effective_mode, bar_source,
               available_at_proven, detail_json, created_ms)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (row["scheme"], int(row["position_id"]), row["symbol"], row.get("entry_ts"),
         row.get("exit_ts"), row.get("exit_class"), 1 if row.get("gap") else 0,
         row.get("gap_size"), row.get("gap_model"), row.get("stop_used"),
         row.get("initial_stop"), row.get("raw_price"), row.get("fill_price"),
         row.get("net_pnl"), row.get("trail_updates"), EXIT_RULES,
         row.get("trail_effective_mode"), row.get("bar_source"),
         1 if row.get("available_at_proven") else 0,
         json.dumps(row.get("detail") or {}, ensure_ascii=False, default=str),
         int(time.time() * 1000)))
    store.con.commit()


# ══════════════════════════════════════════════════════════════════════════
# 七、委托：只有批准配置才走到 paper_engine
# ══════════════════════════════════════════════════════════════════════════
def production_ledger_dir() -> Path:
    return (PROJECT / "数据" / "paper").resolve()


def ledger_isolation() -> dict:
    """账本目录是否与研究隔离。**默认目录 = 生产 ⇒ 一律视为未隔离。**"""
    import live_store as L
    ld = L.get_ledger_dir().resolve()
    prod = production_ledger_dir()
    explicit = bool(os.environ.get("PAPER_LEDGER_DIR"))
    inside_prod = (ld == prod) or (prod in ld.parents)
    return {
        "ledger_dir": str(ld),
        "production_dir": str(prod),
        "explicit_env": explicit,
        "inside_production": inside_prod,
        "isolated": explicit and not inside_prod,
        "account_ids": {s: f"paper43_{s}" for s in SCHEMES},
    }


def try_open(scheme: str, symbol: str, cand: dict, cfg: dict, store, *,
             origin: str = "replay", now_ms: Optional[int] = None) -> dict:
    """候选 → 委托。未批准配置 ⇒ 只写阻塞记录，**绝不**调 paper_engine。"""
    if not cfg.get("open_allowed"):
        if "economic_caliber_declared_mismatch" in (cfg.get("blocked_by") or []):
            reason = "blocked_by_economic_caliber_mismatch"
        else:
            reason = "blocked_by_unapproved_config"
        record_attempt(store, cand, cfg, allowed=False, reason=reason,
                       missing=cfg.get("missing") or [],
                       at_ms=cand.get("earliest_entry_ms"))
        return {"ok": False, "reason": reason,
                "blocked_by": cfg.get("blocked_by") or [],
                "missing": cfg.get("missing") or [],
                "mismatch": (cfg.get("economic_caliber") or {}).get("declared_mismatch"),
                "note": "配置未批准或经济口径不一致 ⇒ 阻塞开仓；观察/记录/隔离验证不受影响",
                "called_paper_engine": False}

    iso = ledger_isolation()
    if not iso["isolated"]:
        reason = "isolation_violation"
        record_attempt(store, cand, cfg, allowed=False, reason=reason,
                       at_ms=cand.get("earliest_entry_ms"))
        return {"ok": False, "reason": reason, "isolation": iso,
                "note": "账本目录指向生产 ⇒ 拒绝；研究账户必须物理隔离",
                "called_paper_engine": False}

    import paper_engine as PE
    # 本次尝试时刻 = 成交 bar 的开盘时刻（回放里就是「那根 bar 刚开出来」）；
    # 不能用「最早可成交时刻」——它比 bar 开盘早 1 毫秒（收盘时刻是 ...:59.999），
    # 会被引擎的 `bar_after_attempt_clock_no_lookahead` 当成前视拒掉。
    attempt_ms = (cand.get("entry_bar_open_ms")
                  or now_ms or cand.get("earliest_entry_ms"))
    res = PE.open_position(
        scheme, symbol, signal_uid=cand["signal_uid"], direction="long",
        event_time=int(cand["confirm_close_ms"]),
        available_at=int(cand["confirm_close_ms"]),
        requested_at=int(cand["confirm_close_ms"]),
        now_ms=int(attempt_ms),
        origin=origin, epoch_ms=0)
    ok = bool(res.get("ok"))
    record_attempt(store, cand, cfg, allowed=True,
                   reason="opened" if ok else f"rejected:{res.get('reason')}",
                   paper_result=res, at_ms=cand.get("earliest_entry_ms"))
    return {"ok": ok, "paper_result": res, "called_paper_engine": True,
            "isolation": iso}


def read_position(scheme: str, position_id: int) -> Optional[dict]:
    import live_store as L
    con = L.paper_conn(scheme, ro=True)
    try:
        r = con.execute("SELECT * FROM paper_position WHERE position_id=?",
                        (int(position_id),)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def open_positions(scheme: str) -> List[dict]:
    import live_store as L
    con = L.paper_conn(scheme, ro=True)
    try:
        rows = con.execute(
            "SELECT * FROM paper_position WHERE account_id=? AND status='open' "
            "AND COALESCE(voided,0)=0 ORDER BY position_id",
            (f"paper43_{scheme}",)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def ledger_snapshot(scheme: str) -> dict:
    """账本摘要（委托/成交/止损轨迹/账目），供核验脚本对撞。"""
    import live_store as L
    acc = f"paper43_{scheme}"
    con = L.paper_conn(scheme, ro=True)
    try:
        def q(sql, args=()):
            return [dict(r) for r in con.execute(sql, args)]
        return {
            "account_id": acc,
            "positions": q("SELECT position_id,symbol,status,qty,entry_price,entry_ts,"
                           "exit_ts,exit_reason,exit_price,stop_price,initial_stop_price,"
                           "entry_fee,exit_fee,funding_paid,market_source,stoploss_s,trail_d "
                           "FROM paper_position WHERE account_id=? ORDER BY position_id", (acc,)),
            "orders": q("SELECT order_id,event_uid,symbol,side,status,reject_reason,"
                        "fill_price,bar_open_time,market_source FROM paper_order "
                        "WHERE account_id=? ORDER BY order_id", (acc,)),
            "fills": q("SELECT fill_id,order_id,position_id,action,fill_price,raw_price,qty,"
                       "notional,fee,bar_open_time,market_source FROM paper_fill "
                       "WHERE account_id=? ORDER BY fill_id", (acc,)),
            "stop_history": q("SELECT position_id,old_stop,new_stop,reason,decision_at,"
                              "effective_at,available_at FROM paper_stop_history "
                              "WHERE account_id=? ORDER BY id", (acc,)),
            "realized": q("SELECT position_id,net_pnl,exit_reason,entry_ts,exit_ts "
                          "FROM paper_realized WHERE account_id=? ORDER BY position_id", (acc,)),
            "state": q("SELECT cash,realized_pnl,fees_paid,funding_paid FROM paper_state "
                       "WHERE account_id=?", (acc,)),
            "pending": q("SELECT pending_id,status,reason FROM paper_pending "
                         "WHERE account_id=? ORDER BY pending_id", (acc,)),
            "blocked": q("SELECT event_uid,reason FROM paper_blocked WHERE account_id=?", (acc,)),
        }
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════
# 八、退出驱动（**本单的核心修正**）
# ══════════════════════════════════════════════════════════════════════════

# ── [AUDIT-003-PATCH] 运行状态与失效原因码（BLK-01）────────────────────────
# AUDIT-003 实测：退出推进抛异常时，原实现只往 `out` 里塞一条 `drive_error`，
# 回放继续跑，`research_run` 照常 `ledger_snapshot`，上层跑分器照常算胜率/收益率
# ⇒ **带病产出有效绩效报告**。现在把「运行是否可信」变成一等公民：
#   · 任何一次退出推进异常 / 平仓被拒 ⇒ `run_state["status"] = FAILED`；
#   · `research_run` 把状态写进 `out["run_status"]`，绩效汇总入口据此**阻断指标产出**。
RUN_STATUS_OK = "OK"
RUN_STATUS_FAILED = "FAILED"

INVALID_EXIT_DRIVE_ERROR = "EXIT_DRIVE_ERROR"          # 退出推进抛异常
INVALID_EXIT_CLOSE_REJECTED = "EXIT_CLOSE_REJECTED"    # 平仓被引擎拒（越界/缺高低价/缺规格）
INVALID_TIMELINE_REPLAY_ERROR = "TIMELINE_REPLAY_ERROR"
INVALID_DRIVE_EXITS_ERROR = "DRIVE_EXITS_ERROR"

#: 这些退出原因属于「执行有效性错误」⇒ 一旦出现，整次运行不可用于出成绩
_EXIT_INVALID_REASONS = frozenset({
    "missing_contract_spec",
    "MISSING_CONTRACT_SPEC",
    "EXEC_PRICE_OUT_OF_RANGE",
    "MISSING_BAR_RANGE",
    "INVALID_QTY_PRECISION",
})


def new_run_state() -> dict:
    """一次回测运行的可信度状态。`status` 只有 OK / FAILED 两态。"""
    return {"status": RUN_STATUS_OK, "invalid_reasons": [], "problems": []}


def mark_run_invalid(run_state: Optional[dict], code: str, detail: Optional[dict] = None) -> None:
    """把运行标记为 FAILED 并登记原因码（幂等：同一原因码只记一次）。"""
    if run_state is None:
        return
    run_state["status"] = RUN_STATUS_FAILED
    reasons = run_state.setdefault("invalid_reasons", [])
    if code not in reasons:
        reasons.append(code)
    if detail is not None:
        run_state.setdefault("problems", []).append(detail)


def _bar_label(bar: dict, base: str = "binance_um") -> str:
    """写进账本的来源标签：层 + 「可得时刻有没有证据」。"""
    layer = bar.get("source")
    if layer is None:
        return base
    tag = f"{base}|merged.{layer}"
    if bar.get("available_at") is None:
        tag += "|available_at_unproven"
    return tag


def _round_protect(px: float, step: float) -> float:
    """**惰性**转发冻结的保护价取整（多头止损向下＝更保守）。唯一实现。"""
    from strategies.wx_execution import round_protect_price
    return float(round_protect_price(px, step))


def drive_position_exit(scheme: str, position_id: int, feed: DataFeed, *,
                        cfg: dict, end_ms: int, legacy_4h_exit: bool = False,
                        store=None) -> dict:
    """逐 15m 推进一笔持仓的跟踪止损与退出。

    与 `paper_engine.advance_position_trailing` 的关系
    ------------------------------------------------
    本函数**不重写**止损口径，只重排调用顺序，全部复用引擎原语：
      · 候选止损 = 4h 最低价 × 95%，用 `strategies.wx_execution.round_protect_price` 取整；
      · 上移用 `paper_engine.record_stop_update()`（同一张 `paper_stop_history`）；
      · 平仓用 `paper_engine.close_at_price()`（同一套滑点、取整、手续费、资金费）。
    差别只有两条（都是 04-D 指出的缺陷，本单按已确定语义改正）：
      ① 新止损的生效时刻 = **4h 收盘 + 15 分钟**（`next_15m_after_4h_close`），
         不再用 4h 收盘那一刻，也不会回填到已收盘的那根 15m；
      ② 退出判定**只用 15m 最低价**，不再用「4h 最低价 ≤ 止损」直接平仓
         （那条路径会把盘中触及时刻记成 4h 整点，还会把入场前那半个 4h 的低点
          当成入场后的价格事件）。
    `legacy_4h_exit=True` 保留旧行为，仅用于「原策略行为对照」，不用于出结论。
    """
    import paper_engine as PE

    pos = read_position(scheme, position_id)
    if not pos:
        return {"ok": False, "reason": "position_not_found", "position_id": position_id}
    if pos.get("status") != "open":
        return {"ok": False, "reason": "position_not_open", "position_id": position_id,
                "status": pos.get("status")}
    symbol = pos["symbol"]
    entry_ts = int(pos["entry_ts"])
    initial_stop = float(pos["initial_stop_price"])
    # [WYCKOFF-DEC01-EXECUTION-FIX-10R] 修复历史止损时间因果：无论数据库中 stop_price 曾被记录为何值，
    # 从 entry_ts 重新扫描时，止损基准严格初始化为 initial_stop，随 4h 生效逐根推进，杜绝未来止损倒灌。
    cur_stop = initial_stop
    trail_d = float(pos["trail_d"] if pos.get("trail_d") is not None
                    else FIXED_RULES["trailing_low_pct"])
    limits = PE.contract_limits(symbol) or {}
    step = float(limits.get("price_step") or 0.0)
    if step <= 0:
        return {"ok": False, "reason": "missing_contract_spec", "position_id": position_id}
    _gm = _dig(cfg.get("doc") or {}, "exit.gap_model")
    gap_model = "stop_price" if (_gm is _MISSING or _gm is None) else str(_gm)

    bars15 = feed.window("15m", symbol, entry_ts - M15, int(end_ms))
    bars4 = feed.window("4h", symbol, entry_ts - H4, int(end_ms) + H4)

    i4 = 0
    updates: List[dict] = []
    checked = 0
    mode = TRAIL_EFFECTIVE_MODE_LEGACY if legacy_4h_exit else TRAIL_EFFECTIVE_MODE

    def _exit(raw: float, bar: dict, *, gap: bool, gap_size: float,
              stop_used: float) -> dict:
        cls = ("initial" if abs(stop_used - initial_stop) < 1e-12 else "trailing") \
            + ("_stop_gap" if gap else "_stop_loss")
        exit_ts = int(bar["open_time"])
        # [AUDIT-003-PATCH] 必须把出场 K 线的真实高低价交给引擎：`close_at_price`
        # 现在缺高低价直接拒单（MISSING_BAR_RANGE），越界则 EXEC_PRICE_OUT_OF_RANGE。
        # 引擎拒单时**不得**当成已平仓（原来只看 `exit_ts` 是否存在，会把拒单
        # 误记成一次退出，仓位却仍 open ⇒ 既漏记又带病出成绩）。
        res = PE.close_at_price(
            scheme, int(position_id), raw_price=float(raw), now_ms=exit_ts,
            reason=cls, bar_open_time=int(bar["open_time"]),
            bar_close_time=int(bar["close_time"]), exit_ts=exit_ts,
            bar_low=float(bar["low"]), bar_high=float(bar["high"]),
            bar_source=_bar_label(bar),
            # [PR2-FUNDING-HISTORICAL-SEMANTICS-06] 历史经济结算必须显式声明
            # **模拟观察时刻**，否则 `_settle_funding` 只能用真实挂钟（今天）
            # 去比历史决策时刻 ⇒ 必然 fail-closed ⇒ 历史资金费静默记 0。
            # 平仓这一刻能观察到的最晚时刻 = 退出 bar 的开盘时刻（与退出决策同刻），
            # 故 `settlement_observed_at = exit_ts`；策略决策的 PIT 仍由
            # `historical_decision_ts` 单独约束，两者不互相替代。
            funding_context={"mode": PE.FUNDING_HISTORICAL,
                             "historical_decision_ts": exit_ts,
                             "settlement_observed_at": exit_ts})
        same_bar = (exit_ts == entry_ts)
        row = {"ok": bool(res.get("ok")), "scheme": scheme,
               "position_id": int(position_id), "symbol": symbol,
               "entry_ts": entry_ts, "exit_ts": exit_ts, "exit_class": cls,
               "same_bar_execution": same_bar,
               "gap": gap, "gap_size": gap_size, "gap_model": gap_model,
               "stop_used": stop_used, "initial_stop": initial_stop,
               "raw_price": raw, "fill_price": res.get("exit_price"),
               "net_pnl": res.get("net_pnl"), "trail_updates": len(updates),
               "trail_effective_mode": mode, "bar_source": bar.get("source"),
               "available_at_proven": bar.get("available_at") is not None,
               "detail": {"close_result": res, "updates": updates,
                          "same_bar_execution": same_bar,
                          "bar_open_time": int(bar["open_time"]),
                          "bar_low": float(bar["low"]),
                          "bar_high": float(bar["high"]),
                          "bar_open": float(bar["open"])}}
        if not res.get("ok"):
            # 平仓被拒 ⇒ 这不是一次退出。带出原因码，交给上层把整次运行判无效。
            row["reason"] = res.get("reason") or "exit_close_rejected"
            row["close_rejected"] = True
            row["exit_ts"] = None
            row["net_pnl"] = None
        if store is not None:
            record_exit(store, row)
        return row

    for b in bars15:
        if int(b["close_time"]) < entry_ts:
            continue
        checked += 1
        # ① 推进已到生效时刻的 4h 跟踪止损
        while i4 < len(bars4):
            b4 = bars4[i4]
            c4 = int(b4["close_time"])
            # 生效时刻：4h 收盘（...:59.999）整点确认后 + 15m ⇒ 12:00 确认 ⇒ 最早 12:15 生效，严禁回填 12:00
            confirm_ms = (c4 // M15 + 1) * M15 if c4 % M15 != 0 else c4
            eff = c4 if legacy_4h_exit else (confirm_ms + M15)
            gate = int(b["close_time"]) if legacy_4h_exit else int(b["open_time"])
            if eff > gate:
                break
            i4 += 1
            if confirm_ms <= entry_ts:
                continue                            # 入场前已收盘的 4h 不参与（因果守卫）
            cand_stop = _round_protect(float(b4["low"]) * (1.0 - trail_d), step)
            if cand_stop > cur_stop:
                avail = b4.get("available_at")
                ok = PE.record_stop_update(
                    arm=scheme, position_id=int(position_id),
                    old_stop=cur_stop, new_stop=cand_stop,
                    reason="ft_low4h_trail", ref_bar_open_ms=int(b4["open_time"]),
                    decision_at=c4, effective_at=eff,
                    available_at=(int(avail) if avail is not None else c4))
                if ok:
                    updates.append({"ref_bar_open_ms": int(b4["open_time"]),
                                    "bar4h_close_ms": c4, "old_stop": cur_stop,
                                    "new_stop": cand_stop, "effective_at": eff,
                                    "bar4h_source": b4.get("source"),
                                    "available_at_proven": avail is not None})
                    cur_stop = cand_stop
            if legacy_4h_exit and float(b4["low"]) <= cur_stop:
                return _exit(cur_stop, b, gap=False, gap_size=0.0,
                             stop_used=cur_stop) | {"legacy_path": "4h_low",
                                                    "bars_checked": checked}
        # ② 只用 15m 判退出（跳空与盘中分开）
        o, lo = float(b["open"]), float(b["low"])
        gap = o <= cur_stop
        if gap or lo <= cur_stop:
            # [WYCKOFF-DEC01-EXECUTION-FIX-10R] 跳空低开市价单采用明确的 bar_open 加不利滑点模型
            # 盘中触损采用触发价 cur_stop；跳空低开采用出场 bar 的开盘价 o
            raw = o if gap else cur_stop
            used_gap_model = "bar_open" if gap else gap_model
            out = _exit(raw, b, gap=gap,
                        gap_size=(cur_stop - o) if gap else 0.0, stop_used=cur_stop)
            out.update({"legacy_path": None, "bars_checked": checked, "gap_model": used_gap_model})
            return out

    return {"ok": True, "exited": False, "reason": "open_at_window_end",
            "position_id": int(position_id), "symbol": symbol,
            "entry_ts": entry_ts, "stop_used": cur_stop,
            "trail_updates": len(updates), "trail_effective_mode": mode,
            "bars_checked": checked, "updates": updates}


def drive_all_exits(feed: DataFeed, *, cfg: dict, end_ms: int, schemes=SCHEMES,
                    legacy_4h_exit: bool = False, store=None,
                    run_state: Optional[dict] = None) -> List[dict]:
    """收尾：把所有仍开着的持仓推进到窗口末。

    `run_state`（AUDIT-003-PATCH）：单笔失败不能只记一条就放过——平仓被拒或引擎
    明确失败都说明账本与行情不自洽 ⇒ 整次运行标 FAILED，绩效汇总入口据此阻断。
    """
    out = []
    for scheme in schemes:
        for pos in open_positions(scheme):
            pid = int(pos["position_id"])
            # [WYCKOFF-DEC01-EXECUTION-FIX-10R] 消除同一持仓被时间轴与收尾阶段重复记录拒单
            if store is not None:
                existing = store.con.execute(
                    "SELECT count(*) FROM accum05_exit WHERE scheme=? AND position_id=?",
                    (scheme, pid)).fetchone()[0]
                if existing > 0:
                    continue
            try:
                r = drive_position_exit(scheme, pid, feed,
                                        cfg=cfg, end_ms=end_ms,
                                        legacy_4h_exit=legacy_4h_exit, store=store)
            except Exception as exc:                          # noqa: BLE001
                rec = {"ok": False, "scheme": scheme, "position_id": pid,
                       "reason": "drive_error", "error": repr(exc)}
                out.append(rec)
                mark_run_invalid(run_state, INVALID_DRIVE_EXITS_ERROR, rec)
                continue
            out.append(r)
            if r.get("close_rejected") or (r.get("ok") is False
                                           and str(r.get("reason") or "") in _EXIT_INVALID_REASONS):
                mark_run_invalid(run_state, INVALID_EXIT_CLOSE_REJECTED, r)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 九、扫描驱动
# ══════════════════════════════════════════════════════════════════════════
def _ms_of(s: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def scan_symbol(symbol: str, cfg: dict, feed: DataFeed, store, *,
                start_ms: int, end_ms: int, max_boxes: int = 3,
                warmup_days: int = 60, open_candidates: bool = False,
                now_ms: Optional[int] = None, schemes=SCHEMES) -> dict:
    """扫一个标的：**单次时间推进**（4h 建档 → 1h 推进），不重复消费同一段行情。

    为什么必须是单次推进：原策略有「单活跃事件 + 终结后冷却」的路径依赖；
    若按箱体各自跑一遍，同一段 1h 会被多个箱体重复消费，同一个确认时刻会冒出
    多条候选——那是扫描方式造成的，不是策略行为。这里改为：**每个方案同时只允许
    一个活跃事件**，箱体到期未终结就不再接新箱体；冷却期从配置取
    （未批准 ⇒ 0，并在报告里标明「冷却未生效」）。
    """
    scheme_meta = _scheme_identity()
    w_start = int(start_ms) - warmup_days * DAY
    bars4 = feed.load("4h", symbol, w_start, end_ms)
    bars1 = feed.load("1h", symbol, w_start, end_ms)
    boxes = build_boxes(bars4)
    box_by_formation = {int(b["formation_ms"]): b for b in boxes}

    out = {"symbol": symbol,
           "bars": {"4h": len(bars4), "1h": len(bars1),
                    "4h_gaps": feed.gaps("4h", symbol),
                    "1h_gaps": feed.gaps("1h", symbol),
                    "available_at_4h": feed.available_at_report("4h", symbol),
                    "available_at_1h": feed.available_at_report("1h", symbol)},
           "boxes_formed": sum(1 for b in boxes
                               if b["formed"] and int(b["formation_ms"]) >= int(start_ms)),
           "boxes_used": 0, "events": [], "candidates": [], "actions": 0,
           "cooldown_ms": 0, "skipped": []}

    cooldown_ms = 0
    if cfg.get("ready"):
        try:
            cooldown_ms = int(cfg["doc"]["shared"]["repeat_candidate_limit"]["cooldown_hours"]) * H
        except Exception:                                     # noqa: BLE001
            cooldown_ms = 0
    out["cooldown_ms"] = cooldown_ms
    if not cfg.get("ready"):
        out["skipped"].append({"why": "cooldown_not_applied",
                               "note": "repeat_candidate_limit 未批准 ⇒ 冷却不生效（不编造）"})

    active: Dict[str, Optional[SchemeObserver]] = {s: None for s in schemes}
    next_allowed: Dict[str, int] = {s: 0 for s in schemes}

    def _spawn(box):
        made = []
        for scheme in schemes:
            if active[scheme] is not None:
                continue
            if int(box["formation_ms"]) < next_allowed[scheme]:
                continue
            obs = SchemeObserver(scheme, symbol, box, cfg, store, feed,
                                 strategy_id=scheme_meta[scheme]["strategy_id"],
                                 rule_version=scheme_meta[scheme]["rule_version"])
            obs.open_event(
                evidence={"box": {k: box[k] for k in
                                  ("formation_ms", "high_h", "low_l", "atr",
                                   "width_atr", "er")},
                          "config_kind": cfg.get("kind"),
                          "config_sha256": cfg.get("config_sha256"),
                          "config_ready": bool(cfg.get("ready")),
                          "rule_version": RULE_VERSION,
                          "exit_rules": EXIT_RULES},
                missing_evidence=({"missing_config": cfg.get("missing")}
                                  if not cfg.get("ready") else {}),
                data_quality="ok" if bars1 else "missing")
            active[scheme] = obs
            made.append(obs)
        return made

    def _reap(obs, at_ms):
        for a in obs.pending_actions:
            if a["kind"] == "candidate":
                if a.get("dedup"):
                    # 重复扫描命中既有候选：不重复落库、不重复下单
                    out["events"].append({"event_id": obs.event_id, "scheme": obs.scheme,
                                          "state": "CANDIDATE",
                                          "reason": "重复扫描命中既有候选（未重复落库/下单）",
                                          "at_ms": a["confirm_close_ms"],
                                          "candidate_id": a["candidate_id"],
                                          "dedup": True})
                    continue
                cand = obs.candidates[-1]
                cand["entry_bar_source"] = _bar_source(feed, "15m", symbol,
                                                       cand["entry_bar_open_ms"])
                if open_candidates:
                    cand["open_result"] = try_open(obs.scheme, symbol, cand, cfg, store,
                                                   now_ms=now_ms)
                record_candidate(store, cand, cfg)
                out["candidates"].append(dict(cand))
                out["events"].append({"event_id": obs.event_id, "scheme": obs.scheme,
                                      "state": "CANDIDATE", "reason": a["reason"],
                                      "at_ms": a["confirm_close_ms"],
                                      "candidate_id": cand["candidate_id"]})
            elif a["kind"] == "blocked_config":
                store.transition(obs.event_id, obs.state, obs.state, a["reason"], a["at_ms"],
                                 payload={"conditions": a["conditions"],
                                          "missing": a["missing"],
                                          "unsupported": a["unsupported"],
                                          "note": "观察停在此处；不是数据不足"})
                out["events"].append({"event_id": obs.event_id, "scheme": obs.scheme,
                                      "state": "BOX", "reason": a["reason"],
                                      "blocked_config": True, "at_ms": a["at_ms"]})
                obs.done = True
            elif a["kind"] == "close":
                out["events"].append({"event_id": obs.event_id, "scheme": obs.scheme,
                                      "state": a["state"], "reason": a["reason"],
                                      "at_ms": a["at_ms"],
                                      "terminal_reason": (a.get("payload") or {}).get("terminal_reason")})
            elif a["kind"] == "transition":
                out["events"].append({"event_id": obs.event_id, "scheme": obs.scheme,
                                      "state": a["to"], "reason": a["reason"],
                                      "at_ms": a["at_ms"]})
        obs.pending_actions = []
        if obs.done:
            next_allowed[obs.scheme] = max(next_allowed[obs.scheme],
                                           int(at_ms) + cooldown_ms)
            active[obs.scheme] = None

    i4 = 0
    for k, bar in enumerate(bars1):
        cl = int(bar["close_time"])
        if cl < int(start_ms):
            continue
        if cl > int(end_ms):
            break
        # ① 先把「本根 1h 收盘时已收盘」的 4h 建档处理掉（与生产同一顺序）
        while i4 < len(bars4) and int(bars4[i4]["close_time"]) <= cl:
            b4 = bars4[i4]
            rec = box_by_formation.get(int(b4["close_time"]))
            if (rec and rec["formed"] and int(rec["formation_ms"]) >= int(start_ms)
                    and out["boxes_used"] < max_boxes):
                if _spawn(rec):
                    out["boxes_used"] += 1
            i4 += 1
        # ② 推进活跃观察者
        for scheme in schemes:
            obs = active[scheme]
            if obs is None:
                continue
            acts = obs.on_bar(bars1, k, float(obs.box["atr"]))
            out["actions"] += len(acts)
            obs.pending_actions.extend(acts)
            _reap(obs, cl)

    for scheme in schemes:
        obs = active[scheme]
        if obs is not None:
            out["events"].append({"event_id": obs.event_id, "scheme": scheme,
                                  "state": obs.state, "reason": "窗口结束时仍在观察",
                                  "at_ms": int(end_ms)})
    return out


def _bar_source(feed: DataFeed, tf: str, symbol: str, open_ms) -> Optional[str]:
    if open_ms is None:
        return None
    for b in feed._cache.get((tf, symbol)) or []:
        if int(b["open_time"]) == int(open_ms):
            return b.get("source")
    return None


def _scheme_identity() -> Dict[str, dict]:
    import accum_research as AR
    out = {}
    for s in SCHEMES:
        meta = AR.SCHEMES[s]
        out[s] = {"strategy_id": f"{meta['strategy_id']}::{s}",
                  "label": meta["label"],
                  "rule_version": f"{RULE_VERSION}::{s}",
                  "approved": False}
    return out


# ══════════════════════════════════════════════════════════════════════════
# 十、统一研究入口（05-C 调这个）
# ══════════════════════════════════════════════════════════════════════════
def run_identity(cfg: dict, feed: Optional[DataFeed] = None) -> dict:
    """一次运行的身份块：代码/配置/规则/引擎/数据来源，全部可复核。"""
    import paper_engine as PE
    p = PE.approved_params()
    ident = {
        "entry_version": VERSION,
        "config_schema": CONFIG_SCHEMA,
        "rule_version": RULE_VERSION,
        "exit_rules": EXIT_RULES,
        "generated_utc": _iso(int(time.time() * 1000)),
        "config": {"path": cfg.get("path"), "kind": cfg.get("kind"),
                   "approved": cfg.get("approved"),
                   "approval_ref": cfg.get("approval_ref"),
                   "sha256": cfg.get("config_sha256"),
                   "open_allowed": cfg.get("open_allowed"),
                   "open_scope": cfg.get("open_scope"),
                   "blocked_by": cfg.get("blocked_by")},
        "engine": {"view_sha256": p.get("view_sha256"),
                   "_source": p.get("_source"),
                   "strategy_version": p.get("strategy_version"),
                   "stoploss_s": p.get("stoploss_s"),
                   "trail_d": p.get("trail_d"),
                   "fee_each_side": p.get("fee_each_side"),
                   "slippage_each_side": p.get("slippage_each_side"),
                   "signal_execution_delay_minutes": p.get("signal_execution_delay_minutes")},
        "fixed_rules": FIXED_RULES,
        "fixed_rules_check": cfg.get("fixed_rules"),
        "economic_caliber": cfg.get("economic_caliber"),
        "schemes": _scheme_identity(),
        "isolation": ledger_isolation(),
    }
    if feed is not None:
        ident["data"] = {"hot_db": str(feed.hot_db),
                         "archive_root": str(feed.archive_root),
                         "merge": os.environ.get("WX_MARKET_MERGE"),
                         "provenance": feed.provenance()}
    return ident


def _timeline_events(per_symbol: Sequence[dict]) -> List[dict]:
    """把各标的的候选拍平成统一时间轴事件；顺序**完全确定**，与输入币种顺序无关。

    排序键 `(时间戳, symbol, scheme, signal_uid)`：
      · 时间戳取成交 bar 开盘时刻（`entry_bar_open_ms`）——这是「同一时刻」的唯一判据；
      · `symbol` 字母升序，满足「同刻多个候选竞争槽位」时的确定性要求；
      · `scheme`/`signal_uid` 只作兜底，正常不会同时命中。

    为什么用 `entry_bar_open_ms` 而不是 `confirm_close_ms`：委托的尝试时刻就是成交 bar
    开盘（见 `try_open` 里的 `attempt_ms` 注释），用别的时刻排序会与引擎的
    `bar_after_attempt_clock_no_lookahead` 闸门口径不一致。
    """
    ev: List[dict] = []
    for rec in per_symbol:
        sym = rec.get("symbol")
        for c in (rec.get("candidates") or []):
            t = c.get("entry_bar_open_ms") or c.get("earliest_entry_ms")
            if t is None:
                continue
            ev.append({"t": int(t), "symbol": str(sym),
                       "scheme": str(c.get("scheme")),
                       "signal_uid": str(c.get("signal_uid")),
                       "cand": c})
    ev.sort(key=lambda e: (e["t"], e["symbol"], e["scheme"], e["signal_uid"]))
    return ev


def _advance_open_positions(feed: DataFeed, *, cfg: dict, schemes, until_ms: int,
                            legacy_4h_exit: bool = False, store=None,
                            cache: Dict[str, List[int]],
                            run_state: Optional[dict] = None) -> List[dict]:
    """把各方案存量持仓推进到 `until_ms`（含），返回本次新增的退出记录。

    `drive_position_exit` 每根 15m 先上移 4h 跟踪止损、再判退出，所以一次调用同时完成
    工单要求的第一步与第二步：
      ① 触发的持仓立即平仓落账 ⇒ **返回后槽位即可复用**（`pre_open_check` 查库判槽位）；
      ② 未触发的持仓把跟踪止损推进到该时刻。

    仍开着的持仓留在 `cache` 里供下一轮继续推进；已平仓、已找不到、缺合约规格的一律移出，
    避免同一笔被反复重试。**不重写任何止损/退出口径**，只重排调用时机。

    `run_state`（AUDIT-003-PATCH / BLK-01）：退出推进异常或平仓被拒时，**必须**把整次
    运行标成 FAILED。原实现只记一条 `drive_error` 就继续跑，上层照样算胜率 ⇒ 带病出报告。
    """
    out: List[dict] = []
    for scheme in schemes:
        keep: List[int] = []
        for pid in list(cache.get(scheme) or []):
            try:
                r = drive_position_exit(scheme, int(pid), feed, cfg=cfg,
                                        end_ms=int(until_ms),
                                        legacy_4h_exit=legacy_4h_exit, store=store)
            except Exception as exc:                          # noqa: BLE001
                keep.append(int(pid))
                rec = {"ok": False, "scheme": scheme, "position_id": int(pid),
                       "reason": "drive_error", "error": repr(exc),
                       "until_ms": int(until_ms)}
                out.append(rec)
                # ① 异常 ⇒ 运行无效（不再「记一笔就当没事」）
                mark_run_invalid(run_state, INVALID_EXIT_DRIVE_ERROR, rec)
                continue
            # ② 平仓被引擎拒（价格越界 / 缺高低价 / 缺规格）⇒ 不是退出，仓位仍 open
            if r.get("close_rejected"):
                out.append(r)
                mark_run_invalid(run_state, INVALID_EXIT_CLOSE_REJECTED, r)
                continue
            # ③ 引擎明确失败且原因属执行有效性 ⇒ 运行无效
            if r.get("ok") is False and str(r.get("reason") or "") in _EXIT_INVALID_REASONS:
                out.append(r)
                mark_run_invalid(run_state, INVALID_EXIT_CLOSE_REJECTED, r)
                continue
            if r.get("exit_ts") is not None:
                out.append(r)                                 # 已平仓
            elif r.get("reason") == "open_at_window_end":
                keep.append(int(pid))                         # 仍开着，继续推进
            else:
                out.append(r)                                 # 找不到/已平：不再重试
        cache[scheme] = keep
    return out


def _replay_timeline(feed: DataFeed, *, cfg: dict, per_symbol: Sequence[dict],
                     schemes=SCHEMES, store, legacy_4h_exit: bool = False,
                     run_state: Optional[dict] = None,
                     ) -> Tuple[List[dict], dict]:
    """统一时间轴回放：同一时刻先退出释放槽位，再分配槽位开新仓。

    每个时间戳严格按工单要求的三步执行：
      ① 处理存量持仓的平仓/退出事件，并**立即释放仓位槽位**；
      ② 更新剩余存量持仓状态（跟踪止损上移，与 ① 在同一次 `drive_position_exit` 内完成）；
      ③ 评估新入场信号并分配可用槽位（同刻按 `symbol` 升序，与输入顺序无关）。

    只对「该时刻确实有候选」的方案推进持仓——没有候选的方案在这一刻不需要最新权益，
    省掉大量无意义推进，不影响结果（下一次它有候选时会补推）。
    """
    events = _timeline_events(per_symbol)
    # 预先把「同一时刻涉及哪些方案」算好（一次遍历），避免在每个时间戳上重扫全表
    schemes_at: Dict[int, List[str]] = {}
    for e in events:
        bucket = schemes_at.setdefault(e["t"], [])
        if e["scheme"] not in bucket:
            bucket.append(e["scheme"])
    for ts in schemes_at:
        schemes_at[ts] = [s for s in schemes if s in schemes_at[ts]]   # 按方案固定顺序

    cache: Dict[str, List[int]] = {}
    for s in schemes:
        try:
            cache[s] = [int(p["position_id"]) for p in open_positions(s)]
        except Exception:                                     # noqa: BLE001
            cache[s] = []

    exits: List[dict] = []
    opened = 0
    last_t: Optional[int] = None
    for e in events:
        if e["t"] != last_t:
            # 只推进「这一刻有候选」的方案：先退出释放槽位，再轮到入场
            exits.extend(_advance_open_positions(
                feed, cfg=cfg, schemes=schemes_at[e["t"]],
                until_ms=e["t"], legacy_4h_exit=legacy_4h_exit,
                store=store, cache=cache, run_state=run_state))
            last_t = e["t"]
        res = try_open(e["scheme"], e["symbol"], e["cand"], cfg, store)
        e["cand"]["open_result"] = res
        if res.get("ok"):
            pid = (res.get("paper_result") or {}).get("position_id")
            if pid is not None:
                cache.setdefault(e["scheme"], []).append(int(pid))
                opened += 1

    stats = {"events": len(events), "opened": opened,
             "distinct_timestamps": len({e["t"] for e in events}),
             "order_key": "entry_bar_open_ms,symbol,scheme,signal_uid",
             "run_status": (run_state or {}).get("status", RUN_STATUS_OK),
             "invalid_reasons": list((run_state or {}).get("invalid_reasons") or [])}
    return exits, stats


def research_run(*, config=None, symbols: Sequence[str], start_ms: int, end_ms: int,
                 schemes=SCHEMES, max_boxes: int = 3, open_candidates: bool = True,
                 drive_exits: bool = True, legacy_4h_exit: bool = False,
                 walk_days: int = 30, warmup_days: int = 60,
                 observe_db=None, now_ms: Optional[int] = None,
                 feed: Optional[DataFeed] = None, store=None,
                 timeline_entries: bool = True) -> dict:
    """统一研究入口：观察 → 候选 → 委托 → 成交 → 退出 → 账本，一次跑完。

    参数
    ----
    config        批准配置路径（缺省 `ACCUM05_CONFIG` 或研究根下的 approved_rules.json）
    symbols       标的列表（**调用方给，本函数不去猜池子**）
    start_ms/end_ms  观察窗口（毫秒，UTC）
    open_candidates  是否走到委托（配置不允许时只记阻塞，不会开仓）
    drive_exits   是否对未平仓逐 15m 推进退出
    legacy_4h_exit  退出对照臂：True = 旧行为（4h 低点判退出、4h 收盘即生效）
    walk_days     退出推进最多看多少天（从入场起）
    timeline_entries  **FIX-001**：True（默认）= 统一时间轴推进，扫描与开仓分两段，
                  同一时刻先退出释放槽位再入场；False = 旧行为（按标的顺序边扫边开、
                  退出全部后置）。旧行为只保留用于复现历史证据，**不要用于出成绩**。

    返回 dict（键固定，05-C 直接读）：
      identity / config / per_symbol / exits / ledgers / errors
      （timeline 模式下多一个 `timeline` 键，记录事件数/成交数/排序键）
    """
    cfg = load_config(config)
    own_store = store is None
    st = store or open_store(observe_db)
    own_feed = feed is None
    fd = feed or DataFeed()
    # 两段式只在「真的会开仓」且调用方没显式要求旧行为时启用
    use_timeline = bool(open_candidates and timeline_entries)
    # [AUDIT-003-PATCH] 运行可信度：任何退出推进异常/平仓被拒都会把它打成 FAILED，
    # 上层绩效汇总入口必须据此**拒绝产出收益与胜率**（BLK-01）。
    run_state = new_run_state()
    out = {"identity": run_identity(cfg, fd),
           "config": {k: v for k, v in cfg.items() if k != "doc"},
           "window": {"start_utc": _iso(start_ms), "end_utc": _iso(end_ms),
                      "start_ms": int(start_ms), "end_ms": int(end_ms)},
           "symbols": list(symbols), "per_symbol": [], "exits": [],
           "ledgers": {}, "errors": [],
           "run_status": RUN_STATUS_OK, "invalid_reasons": [],
           "flags": {"open_candidates": bool(open_candidates),
                     "drive_exits": bool(drive_exits),
                     "legacy_4h_exit": bool(legacy_4h_exit),
                     "timeline_entries": bool(timeline_entries),
                     "entry_order": ("timeline" if use_timeline else "symbol_major"),
                     "walk_days": int(walk_days)}}
    try:
        # ── 第一段：扫描。timeline 模式下**不开仓**，只收集候选 ────────────
        # `scan_symbol` 内部唯一与仓位相关的调用是 `try_open`（受 open_candidates 控制）；
        # 箱体、状态机、候选判定都不读仓位 ⇒ 关掉开仓后候选产出逐条相同。
        scan_open = bool(open_candidates) and not use_timeline
        for sym in symbols:
            try:
                r = scan_symbol(sym, cfg, fd, st, start_ms=start_ms, end_ms=end_ms,
                                max_boxes=max_boxes, warmup_days=warmup_days,
                                open_candidates=scan_open, now_ms=now_ms,
                                schemes=schemes)
                out["per_symbol"].append(r)
            except Exception as exc:                          # noqa: BLE001
                out["errors"].append({"symbol": sym, "stage": "scan", "error": repr(exc)})

        # ── 第二段：统一时间轴回放（同刻先退出释放槽位，再入场）────────────
        if use_timeline:
            try:
                tl_exits, tl_stats = _replay_timeline(
                    fd, cfg=cfg, per_symbol=out["per_symbol"], schemes=schemes,
                    store=st, legacy_4h_exit=legacy_4h_exit, run_state=run_state)
                out["exits"] = tl_exits
                out["timeline"] = tl_stats
            except Exception as exc:                          # noqa: BLE001
                out["errors"].append({"stage": "timeline_replay", "error": repr(exc)})
                # [AUDIT-003-PATCH] 回放整体崩掉 ⇒ 运行无效（原来只记 errors，照样出成绩）
                mark_run_invalid(run_state, INVALID_TIMELINE_REPLAY_ERROR,
                                 {"stage": "timeline_replay", "error": repr(exc)})

        # ── 收尾：把仍开着的持仓推进到窗口末（两段式与旧行为共用）──────────
        if drive_exits:
            try:
                tail = drive_all_exits(
                    fd, cfg=cfg, end_ms=int(end_ms) + int(walk_days) * DAY,
                    schemes=schemes, legacy_4h_exit=legacy_4h_exit, store=st,
                    run_state=run_state)
                if use_timeline:
                    out["exits"].extend(tail)
                else:
                    out["exits"] = tail
            except Exception as exc:                          # noqa: BLE001
                out["errors"].append({"stage": "drive_exits", "error": repr(exc)})
                mark_run_invalid(run_state, INVALID_DRIVE_EXITS_ERROR,
                                 {"stage": "drive_exits", "error": repr(exc)})
        for scheme in schemes:
            try:
                out["ledgers"][scheme] = ledger_snapshot(scheme)
            except Exception as exc:                          # noqa: BLE001
                out["errors"].append({"scheme": scheme, "stage": "ledger",
                                      "error": repr(exc)})
    finally:
        if own_feed:
            fd.close()
        if own_store:
            st.close()
    # ── [AUDIT-003-PATCH] 把运行可信度摊到返回值顶层，绩效入口直接读 ──────────
    out["run_status"] = run_state["status"]
    out["invalid_reasons"] = list(run_state["invalid_reasons"])
    out["run_problems"] = list(run_state["problems"])[:50]
    if run_state["status"] != RUN_STATUS_OK:
        out["run_invalid_note"] = (
            "本次运行被标记为 FAILED：退出推进出现异常或平仓被拒 ⇒ 账本与行情不自洽，"
            "禁止用本次结果计算收益率/胜率/回撤；必须先修数据或代码再重跑。")
    return out


# ══════════════════════════════════════════════════════════════════════════
# 十一、自检（接口检查，不发 HTTP、不碰前端）
# ══════════════════════════════════════════════════════════════════════════
def diagnose(path=None, probe_symbol: Optional[str] = None) -> dict:
    import accum_research as AR
    import accum_pending as AP
    cfg = load_config(path)
    out = {
        "version": VERSION,
        "config_schema": CONFIG_SCHEMA,
        "rule_version": RULE_VERSION,
        "exit_rules": EXIT_RULES,
        "config": {k: v for k, v in cfg.items() if k != "doc"},
        "mapping": mapping_check(),
        "fixed_rules": check_fixed_rules(),
        "pending_snapshot": {"count": len(AP.PENDING), "valid": AP.validate()["通过"]},
        "schemes": {},
        "isolation": ledger_isolation(),
        "http_api": {
            "research_arms_exposed": False,
            "note": ("HTTP 层只认 live_store.ARMS=('3a','3b','3c','3d')；"
                     "4a/4b/4c 没有端点、前端也不展示 ⇒ 属跨界协调项，本单不改"),
        },
    }
    for s in SCHEMES:
        v = AR.validate(s)
        out["schemes"][s] = {
            "label": v["label"], "direction": v["direction"],
            "strategy_id": _scheme_identity()[s]["strategy_id"],
            "approved": v["approved"], "ready": v["ready"],
            "pending_count": v["pending_count"],
            "can_open": AR.can_open_position(s),
        }
    if probe_symbol:
        feed = DataFeed()
        try:
            now = int(time.time() * 1000)
            start = now - 40 * DAY
            out["data_probe"] = {
                "symbol": probe_symbol,
                "4h": len(feed.window("4h", probe_symbol, start - 60 * DAY, now)),
                "1h": len(feed.window("1h", probe_symbol, start, now)),
                "15m": len(feed.window("15m", probe_symbol, start, now)),
                "paths": {"hot": str(feed.hot_db), "cold": str(feed.archive_root)},
            }
        finally:
            feed.close()
    return out


def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ACCUM-EARLY-05-B 统一研究入口")
    ap.add_argument("cmd", choices=["diagnose", "scan", "run"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--start", default=None, help="UTC ISO，如 2026-07-01T00:00:00Z")
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-boxes", type=int, default=3)
    ap.add_argument("--open", action="store_true", help="允许走到委托（需配置放行）")
    ap.add_argument("--no-exits", action="store_true", help="不推进退出")
    ap.add_argument("--legacy-4h-exit", action="store_true", help="退出对照臂（旧行为）")
    ap.add_argument("--walk-days", type=int, default=30)
    ap.add_argument("--observe-db", default=None)
    ap.add_argument("--probe", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    if a.cmd == "diagnose":
        res = diagnose(a.config, a.probe)
    else:
        symbols = [s.strip() for s in a.symbols.split(",") if s.strip()]
        start = _ms_of(a.start or "2026-07-01T00:00:00Z")
        end = _ms_of(a.end or "2026-09-30T00:00:00Z")
        if a.cmd == "run":
            res = research_run(config=a.config, symbols=symbols, start_ms=start,
                               end_ms=end, max_boxes=a.max_boxes,
                               open_candidates=a.open, drive_exits=not a.no_exits,
                               legacy_4h_exit=a.legacy_4h_exit,
                               walk_days=a.walk_days, observe_db=a.observe_db)
        else:
            cfg = load_config(a.config)
            store = open_store(a.observe_db)
            feed = DataFeed()
            res = {"config": {k: v for k, v in cfg.items() if k != "doc"},
                   "symbols": symbols, "window": {"start": a.start, "end": a.end},
                   "per_symbol": []}
            try:
                for sym in symbols:
                    r = scan_symbol(sym, cfg, feed, store, start_ms=start, end_ms=end,
                                    max_boxes=a.max_boxes, open_candidates=a.open)
                    res["per_symbol"].append(r)
                res["provenance"] = feed.provenance()
            finally:
                feed.close()
                store.close()
    text = json.dumps(res, ensure_ascii=False, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(json.dumps({"written": a.out}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
