# -*- coding: utf-8 -*-
"""GM-BACKTEST-GATE-WIRING-21 · 17 号数据质量门禁的**最小接线**。

本模块只做三件事，不重写门禁判据：
  1. **取快照**：把 `backtest_runner` 两条运行路径已经加载好的数据，转成 17 号门禁认识的快照结构；
  2. **调门禁**：调 `backtest_data_gate.evaluate()` 拿三态判定；
  3. **落地**：合格/隔离 ⇒ 把判定写进 `run.json`；不合格且声明 trusted ⇒ 拒绝运行。

为什么需要单独一层
──────────────────
`backtest_data_gate.py` 是**纯函数 + 只读取数**，不知道「谁在跑、跑哪条路径、要不要拒」。
`backtest_runner.py` 有两条互不相同的取数路径：
  · `_run()`（通用路径）：`resolve_window` + `load_inputs`，产出 `sig_df / exec_df / oi_rows / funding_events`；
  · `_run_event_trend()`（FT-LOW4H 真实路径）：`EventTrendRunner.load_symbol_data`，
    产出 `df_4h / df_1h / df_15m / oi_ts / oi_vals / funding_events`，OI 的可得时刻在
    `runner.oi_rows`（`ts / oi / fetched_at / source`）里。
两条路径的字段名都不一样，所以在**接线层**做归一，门禁本身一行不改。

纪律
────
· **不伪造**：本模块不构造任何行情数值。`is_closed` 只做**原样透传**；源数据没有该列时
  留 `None`（门禁据此判 UNPROVEN），**不按 close_time 猜一个 1 出来**。
· **不降级为通过**：UNPROVEN 绝不当 PASS。`claim_level` 只由门禁给，本模块不覆写。
· **默认不阻断既有回测**：默认模式 `isolated_research`（放行 + 打隔离标记）；
  要「拒绝不合格数据」必须显式 `trusted`。理由见 `resolve_mode` 注释。
· **零写入到数据源**：只读已加载对象，不打开数据库、不写盘。

环境变量
────────
| 变量 | 默认 | 说明 |
|---|---|---|
| `BACKTEST_DATA_GATE_ENABLED` | `1` | 总开关。`0` ⇒ 不调用门禁（此时 run.json 记 `data_gate.enabled=false`，**不冒充 PASS**） |
| `BACKTEST_DATA_GATE_MODE` | `isolated_research` | `trusted` / `isolated_research` |
| `BACKTEST_DATA_GATE_POLICY` | 无 | 策略覆盖 JSON 文件路径（可选，只读） |

单次运行也可在 `spec` 里覆盖：`spec["data_gate_mode"]`。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    import backtest_data_gate as G
except ImportError:                                     # pragma: no cover
    G = None                                            # type: ignore[assignment]

try:
    import backtest_store as S
except ImportError:                                     # pragma: no cover
    S = None                                            # type: ignore[assignment]


ENV_ENABLED = "BACKTEST_DATA_GATE_ENABLED"
ENV_MODE = "BACKTEST_DATA_GATE_MODE"
ENV_POLICY = "BACKTEST_DATA_GATE_POLICY"

GATE_ID = "GM-BACKTEST-DATA-GATE-17"

# 默认模式为什么是 isolated_research 而不是 trusted：
#   本机真实历史窗口的 OI 全部来自 `binance.archive.metrics`，`fetched_at` 为空
#   ⇒ `OI_PIT` 恒 UNPROVEN ⇒ trusted 模式下**每一条历史回测都会被拒**。
#   把默认设成 trusted 等于一合并就让回测功能停摆，而那不是本单的授权范围。
#   所以默认「放行 + 明确降级为隔离研究」，要拒绝就显式切 trusted。
DEFAULT_MODE = "isolated_research"

PIT_PROVEN = "PIT_PROVEN"
PIT_UNPROVEN = "PIT_UNPROVEN"
PIT_NOT_APPLICABLE = "PIT_NOT_APPLICABLE"


class DataGateRejected(RuntimeError):
    """数据质量门禁拒绝运行（trusted 模式下数据不合格）。

    单独一个异常类型，是为了让上层能把「门禁拒绝」和「程序崩了」分开记录：
    前者是**正确行为**，后者是缺陷。
    """

    def __init__(self, message: str, decision: Optional[dict] = None):
        super().__init__(message)
        self.decision = decision or {}


# ══════════════════════════ 配置 ══════════════════════════

def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def is_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return _truthy(e.get(ENV_ENABLED, "1"))


def resolve_mode(spec: Optional[dict] = None,
                 env: Optional[Dict[str, str]] = None) -> str:
    """模式优先级：`spec["data_gate_mode"]` > 环境变量 > 默认 isolated_research。"""
    if G is None:                                       # pragma: no cover
        raise RuntimeError("backtest_data_gate 模块不可用，无法执行数据质量门禁")
    cand = None
    if isinstance(spec, dict):
        cand = spec.get("data_gate_mode")
    if cand is None:
        e = os.environ if env is None else env
        cand = e.get(ENV_MODE)
    cand = str(cand or DEFAULT_MODE).strip().lower()
    if cand not in G.MODES:
        raise ValueError(f"{ENV_MODE} 非法：{cand!r}（允许 {list(G.MODES)}）")
    return cand


def resolve_policy(spec: Optional[dict] = None,
                   env: Optional[Dict[str, str]] = None) -> Optional[dict]:
    """策略覆盖：`spec["data_gate_policy"]`（dict）> `BACKTEST_DATA_GATE_POLICY`（文件路径）。"""
    if isinstance(spec, dict) and isinstance(spec.get("data_gate_policy"), dict):
        return dict(spec["data_gate_policy"])
    e = os.environ if env is None else env
    p = e.get(ENV_POLICY)
    if not p:
        return None
    path = Path(p)
    if not path.is_file():
        raise FileNotFoundError(f"{ENV_POLICY} 指向的文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ══════════════════════════ 快照组装（两条路径各一个）══════════════════════════

def _df_records(df) -> List[dict]:
    """DataFrame → list[dict]，**原样**，不补列、不填缺。"""
    if df is None:
        return []
    try:
        return [dict(r) for r in df.to_dict("records")]
    except Exception:                                   # noqa: BLE001
        return []


def snapshot_from_frames(*, symbol: str, win_start_ms: int, win_end_ms: int,
                         df_signal, df_exec,
                         oi_rows: Optional[Sequence[dict]] = None,
                         funding_events: Optional[Sequence[dict]] = None,
                         warmup_bars: int = 0,
                         df_aux=None,
                         aux_timeframe: str = "4h",
                         aux_role: str = "atr_box_trailing_stop",
                         aux_warmup_bars: int = 220,
                         exchange: str = "binance", market: str = "um",
                         signal_timeframe: str = "1h", exec_timeframe: str = "15m",
                         extra_note: str = "") -> dict:
    """`_run_event_trend` 路径：`load_symbol_data` 的返回值 → 门禁快照。

    OI 行**必须**来自 `runner.oi_rows`（带 `fetched_at`），不能从 `oi_ts/oi_vals`
    两个数组重建——那两个数组在加载边界已经把可得时刻丢掉了，重建出来的 OI
    只能永远 UNPROVEN，等于把「不可证」误当成「已证缺失」。

    45 号：新增 `df_aux`（本项目里是 4H）。**这不是可选项** ——
    4H 的 high/low 决定 ATR、箱体宽度与跟踪止损，43 号证明它此前从不进快照。
    本函数**恒**置 `aux_required=True`，因此：
      · 传了 `df_aux` ⇒ 4H 完整性受判据；
      · 没传（老调用点/新调用点漏传）⇒ 门禁出 `AUX_ABSENT` UNPROVEN ⇒ **拿不到 TRUSTED**。
    也就是说「忘记传 4H」的后果是降级为隔离研究，而不是静默通过。
    """
    aux_records = _df_records(df_aux) if df_aux is not None else []
    snap = {
        "exchange": exchange, "market": market, "symbol": symbol,
        "signal_timeframe": signal_timeframe, "exec_timeframe": exec_timeframe,
        "win_start_ms": int(win_start_ms), "win_end_ms": int(win_end_ms),
        "warmup_bars": int(warmup_bars or 0),
        "signal_bars": _df_records(df_signal),
        "exec_bars": _df_records(df_exec),
        "oi_rows": [dict(r) for r in (oi_rows or [])],
        "funding_rows": [dict(r) for r in (funding_events or [])],
        # ── 辅助序列（45 号）──
        "aux_required": True,
        "aux_timeframe": (aux_timeframe if (aux_records or df_aux is not None) else None),
        "aux_role": aux_role,
        "aux_warmup_bars": int(aux_warmup_bars or 0),
        "aux_bars": aux_records,
        "provenance": ("snapshot_from_frames: EventTrendRunner.load_symbol_data 输出"
                       + (f"｜aux={aux_timeframe}×{len(aux_records)}" if aux_records else
                          "｜aux=未提供")
                       + (f"｜{extra_note}" if extra_note else "")),
    }
    return snap


def snapshot_from_inputs(win: dict, inputs: dict) -> dict:
    """`_run()` 通用路径：`load_inputs` 的返回值 → 门禁快照（复用 17 号自带转换）。"""
    if G is None:                                       # pragma: no cover
        raise RuntimeError("backtest_data_gate 模块不可用")
    return G.snapshot_from_loaded_inputs(inputs, win)


# ══════════════════════════ 判定 + 落地 ══════════════════════════

def run_gate(snapshot: dict, *, spec: Optional[dict] = None,
             env: Optional[Dict[str, str]] = None) -> dict:
    """调门禁出判定。**纯计算**，不写盘。"""
    if G is None:                                       # pragma: no cover
        raise RuntimeError("backtest_data_gate 模块不可用")
    mode = resolve_mode(spec, env)
    policy = resolve_policy(spec, env)
    return G.evaluate(snapshot, mode=mode, policy=policy)


def _has_valid_pit_evidence(check: dict, all_checks: Sequence[dict], decision: dict) -> bool:
    """核验 OI_PIT 检查项背后的有效 PIT 证据是否成立。

    有效 PIT 证据必须满足：
    1. check 状态明确为 PASS；
    2. evidence 存在且为有效字典；
    3. 行数充分（rows > 0 且 rows_with_available_at >= rows，全量覆盖）；
    4. 无回填时间污染（排除 is_backfill, backfilled, tainted, is_inferred, has_valid_arrival=False 等）；
    5. 无到达时刻缺失（排除 no_arrival_time=True 或 available_field 缺失）；
    6. 无其他基础 OI 判据异常（若 OI_EMPTY, OI_COVERAGE, OI_WINDOW_* 处于 UNPROVEN 或 FAIL 则证据不成立）；
    7. 门禁整体判定未处于 FAIL（若整体已 FAIL，则不得承认为有效通过）。
    """
    if str(check.get("status")).upper() != "PASS":
        return False

    ev = check.get("evidence")
    if not isinstance(ev, dict):
        return False

    # 1. 行数与覆盖度核验
    rows = ev.get("rows")
    if rows is not None:
        try:
            if int(rows) <= 0:
                return False
        except (ValueError, TypeError):
            return False

    rows_pit = ev.get("rows_with_available_at")
    if rows_pit is None:
        rows_pit = ev.get("rows_with_pit")

    if rows is not None and rows_pit is not None:
        try:
            if int(rows_pit) < int(rows) or int(rows_pit) <= 0:
                return False
        except (ValueError, TypeError):
            return False
    elif rows_pit is not None:
        try:
            if int(rows_pit) <= 0:
                return False
        except (ValueError, TypeError):
            return False
    elif rows is not None:
        # 有总行数但没有任何有效到达时间行数字段，视同无到达时间证据
        return False

    # 2. 缺失到达时间或到达时间不可用
    if ev.get("no_arrival_time") is True:
        return False
    if ev.get("has_valid_arrival") is False:
        return False

    # 3. 回填时间污染（Backfill / Tainted）阻断
    if ev.get("is_backfill") is True or ev.get("backfilled") is True:
        return False
    if ev.get("tainted") is True or ev.get("is_inferred") is True:
        return False
    avail_field = str(ev.get("available_field") or "").lower()
    if "backfill" in avail_field or "taint" in avail_field:
        return False

    # 4. 基础 OI 判据排查（空数据、覆盖率不足、窗口端点缺失）
    for c in all_checks:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id"))
        cst = str(c.get("status")).upper()
        if cid == "OI_EMPTY" and cst in ("UNPROVEN", "FAIL"):
            return False
        if cid in ("OI_COVERAGE", "OI_WINDOW_START", "OI_WINDOW_END") and cst in ("UNPROVEN", "FAIL"):
            return False

    # 5. 全局决策一致性（FAIL 则不成立）
    if str(decision.get("verdict")).upper() == "FAIL":
        return False

    return True


def pit_status(decision: dict) -> str:
    """把 OI 可得性判据压成一个状态位。

    契约（GM-BACKTEST-PIT-STATUS-FIX-37）：
    1. OI_OK=PASS 绝不能单独升级为 PIT_PROVEN（杜绝覆盖率通过偷换时效证明、oi_pit_required=False 漏洞）；
    2. 仅在 OI_PIT=PASS 且有效 PIT 证据成立时允许升级；
    3. 其余情况（包含 oi_pit_required=False、无历史到达时间、回填时间污染、OI_EMPTY、UNPROVEN 等）
       一律锁死为 PIT_UNPROVEN——「未证」绝不升级为「已证」。
    """
    if not isinstance(decision, dict):
        return PIT_UNPROVEN
    checks = decision.get("checks") or []
    if not isinstance(checks, list):
        return PIT_UNPROVEN

    pit_checks = [c for c in checks if isinstance(c, dict) and str(c.get("id")) == "OI_PIT"]
    if not pit_checks:
        # OI_PIT 判据不存在（包括 oi_pit_required=False 时仅出 OI_OK、或未生成 OI 判据）⇒ 绝不升级
        return PIT_UNPROVEN

    for c in pit_checks:
        if str(c.get("status")).upper() == "PASS" and _has_valid_pit_evidence(c, checks, decision):
            return PIT_PROVEN

    return PIT_UNPROVEN


def stamp(decision: dict, *, enabled: bool = True) -> Dict[str, Any]:
    """判定 → 写进 `run.json` 的字段（加性，不改既有字段）。"""
    if not enabled:
        return {
            "data_gate": {
                "gate": GATE_ID, "enabled": False,
                "note": (f"{ENV_ENABLED}=0 ⇒ 本轮未执行数据质量门禁；"
                         "「未执行」不等于「通过」，不得据此声称数据合格"),
            },
            "pit_status": PIT_UNPROVEN,
        }
    return {
        "data_gate": {
            "gate": decision.get("gate"),
            "gate_version": decision.get("gate_version"),
            "enabled": True,
            "mode": decision.get("mode"),
            "verdict": decision.get("verdict"),
            "data_qualifies_for_trusted": decision.get("data_qualifies_for_trusted"),
            "trusted_backtest_allowed": decision.get("trusted_backtest_allowed"),
            "run_allowed": decision.get("run_allowed"),
            "run_allowed_reason": decision.get("run_allowed_reason"),
            "severe_blocked": decision.get("severe_blocked"),
            "severe_check_ids": decision.get("severe_check_ids"),
            "severe_reasons": decision.get("severe_reasons"),
            "severity_policy": decision.get("severity_policy"),
            "isolation_required": decision.get("isolation_required"),
            "isolation_label": decision.get("isolation_label"),
            "claim_level": decision.get("claim_level"),
            # IFACE-01: 显式透传 pit_status 至 data_gate，解决 47 号 C-03 语义不一致
            "pit_status": pit_status(decision),
            # 45 号：4H 辅助序列（ATR / 箱体 / 跟踪止损的真实输入）覆盖声明。
            # 白名单之外的键会被静默丢弃（33 号 OBS-33-1、43 号 C 节已实证），
            # 不在这里显式透传，`run.json` 就看不到本轮到底有没有把 4H 送进门禁。
            "aux": decision.get("aux"),
            "counts": decision.get("counts"),
            "block_reasons": decision.get("block_reasons"),
            "unproven_reasons": decision.get("unproven_reasons"),
            "checks": decision.get("checks"),
            "verdict_sha256": decision.get("verdict_sha256"),
            "input_sha256": decision.get("input_sha256"),
            "generated_at_utc": decision.get("generated_at_utc"),
            "subject": decision.get("subject"),
            "no_fabrication": decision.get("no_fabrication"),
            "summary_zh": decision.get("summary_zh"),
        },
        "pit_status": pit_status(decision),
    }


def apply(run_id: str, decision: dict, *, store=None) -> Dict[str, Any]:
    """按判定落地：拒绝 ⇒ 先把拒绝原因写进 run.json 再抛；放行 ⇒ 返回待合并字段。

    「先写再抛」是故意的：如果只在异常里带原因，`run.json` 里就只剩
    `DataGateRejected: ...` 一行文本，事后无法复算是哪条判据拒的。
    """
    st = store if store is not None else S
    fields = stamp(decision, enabled=True)

    if decision.get("run_allowed"):
        if st is not None and hasattr(st, "read_run") and hasattr(st, "record_gate_receipt"):
            try:
                from backtest_run_provenance import issue_gate_receipt
                doc_curr = st.read_run(run_id)
                _ei = doc_curr.get("execution_instance") if isinstance(doc_curr, dict) else {}
                xi = _ei.get("exec_instance_id") if isinstance(_ei, dict) else None
                if xi:
                    receipt = issue_gate_receipt(run_id=run_id, exec_instance_id=xi, gate_block=fields["data_gate"])
                    st.record_gate_receipt(run_id, receipt)
                    fields.setdefault("provenance", {})["gate_receipt"] = receipt
            except Exception:
                pass
        return fields

    reasons = list(decision.get("block_reasons") or []) + \
        list(decision.get("unproven_reasons") or [])
    head = "数据质量门禁拒绝运行（GM-BACKTEST-DATA-GATE-17，trusted 模式）"
    msg = head + "：" + ("；".join(reasons[:6]) if reasons else "判定不合格")
    if len(reasons) > 6:
        msg += f"…（共 {len(reasons)} 条）"

    if st is not None:
        try:
            st.set_status(run_id, st.STATUS_FAILED,
                          gate="data_gate_17",
                          failure_reason=msg,
                          **fields)
        except Exception:                               # noqa: BLE001
            # 写不进 run.json 也要把拒绝抛出去：拒绝本身不能因为记账失败而失效
            pass
    raise DataGateRejected(msg, decision)


def enforce_snapshot(run_id: str, snapshot: dict, *, spec: Optional[dict] = None,
                     env: Optional[Dict[str, str]] = None,
                     store=None) -> Dict[str, Any]:
    """一步到位：判 → 落地。返回可直接并入 `_run` 结果字典的字段。"""
    if not is_enabled(env):
        return stamp({}, enabled=False)
    decision = run_gate(snapshot, spec=spec, env=env)
    return apply(run_id, decision, store=store)


__all__ = [
    "GATE_ID", "ENV_ENABLED", "ENV_MODE", "ENV_POLICY", "DEFAULT_MODE",
    "PIT_PROVEN", "PIT_UNPROVEN", "PIT_NOT_APPLICABLE",
    "DataGateRejected",
    "is_enabled", "resolve_mode", "resolve_policy",
    "snapshot_from_frames", "snapshot_from_inputs",
    "run_gate", "pit_status", "stamp", "apply", "enforce_snapshot",
]
