# -*- coding: utf-8 -*-
"""研究回测 · 运行持久化（独立 `runs/<run_id>/`，原子独占创建，重名拒绝）。

设计纪律
────────
· **原子独占创建**：`os.mkdir`（存在即失败）⇒ 重名直接拒绝，绝不覆盖、绝不删旧结果。
· **不调用旧 `回测日志/WyckoffMaster/engine/ledger.py::Ledger`**：那个实现 `__init__` 里
  `if self.path.exists(): self.path.unlink()`，会**删掉已有账本**。本模块独立 schema、
  独立路径，绝不删除任何既有文件。
· 状态机：`queued → running → succeeded | failed`；进程重启后仍为 `queued|running` 的
  运行标 `interrupted`（**不冒充成功**，保留原因）。
· 只写 `RUNS_ROOT` 之内；越界路径拒绝。

目录结构（每个 run 一个独占目录）
──────────────────────────────────
`runs/<run_id>/`
  · `run.json`          运行状态 + 规格 + 哈希 + 窗口 + 数据质量 + 指标
  · `ledger.sqlite3`    可查询交易账本（signals / orders / trades / funding / equity / rejected / meta）
  · `inputs_manifest.json`  实际输入切片描述（不可变快照清单 + 逐序列 SHA256 + 行数 + 覆盖）
"""
from __future__ import annotations

import json
import errno
import itertools
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_TMP_SEQ = itertools.count()

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 运行根（可用环境变量覆盖；默认项目内最新 event_trend_v0_1 独立目录）
def get_runs_root() -> Path:
    """动态解析运行根目录，优先读取环境变量（支持单测临时隔离目录）。"""
    return Path(os.environ.get(
        "EVENT_TREND_RUNS_DIR",
        os.environ.get("OI_RESONANCE_RUNS_DIR", str(PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs"))))

RUNS_ROOT = get_runs_root()

RUN_ID_RE = re.compile(
    r"^(bt_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}"
    r"|run_WX_EVENT_TREND_V0_1_.*"
    # FT-REPLAY-01 / FT-STOP-20-01 / FT-EXIT-01 / FT-STOP-GRID-01 / FT-STOP-GRID-02：研究期结果
    # 只读复盘用，不进生产订单 / 模拟实盘账本。
    r"|run_FT_ENTRY_09_.*"
    r"|run_FT_STOP20_.*"
    r"|run_FT_EXIT01_.*"
    r"|run_FT_STOPGRID01_.*"
    r"|run_FT_STOPGRID02_.*"
    r"|run_FT_LOW4H_.*)$"
)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"
# 「无法判定」是一等公民：没有证据时**不许**用 succeeded 冒充（R10）。
# 注意：它**不是**终态（没有 finished_at 语义），不进 TERMINAL_STATUSES。
STATUS_UNKNOWN = "unknown"
STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED,
            STATUS_INTERRUPTED, STATUS_UNKNOWN)
TERMINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_INTERRUPTED)

_CREATE_LOCK = threading.Lock()


class RunExists(RuntimeError):
    """同名 run 已存在 ⇒ 拒绝（原子独占创建）。"""


class RunNotFound(KeyError):
    """run_id 不存在。"""


class IsolationError(RuntimeError):
    """路径越界（只允许 RUNS_ROOT 之内）。"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(spec_hash: str) -> str:
    """`bt_<UTC 秒级时间戳>_<规格哈希前 8 位>`。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bt_{stamp}_{str(spec_hash)[:8]}"


def assert_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise RunNotFound(f"非法 run_id：{run_id!r}")
    return run_id


def run_dir(run_id: str) -> Path:
    """run 目录（路径越界拒绝）。"""
    rid = assert_run_id(run_id)
    root = get_runs_root().resolve()
    d = (root / rid).resolve()
    if root != d.parent:
        raise IsolationError(f"路径越界（只允许 {root} 下的一级目录）：{d}")
    return d


# ═══════════════ 读取期 run 定位（默认根 + 同级批次目录；写入仍只认默认根）═══════════════

_LOCATE_TTL_S = 120.0
_LOCATE: Dict[str, Any] = {"at": 0.0, "map": {}}
_LOCATE_LOCK = threading.Lock()


def _sibling_batch_roots() -> List[Path]:
    """默认运行根的同级批次目录（`runs_*`，如 `runs_sep_all_latest`）。

    批量脚本把 run 落在这些目录里，它们与默认 `runs/` 是**平级**的独立根。
    """
    root = get_runs_root()
    parent = root.parent
    out: List[Path] = []
    try:
        with os.scandir(parent) as it:
            for e in it:
                if not e.is_dir() or not e.name.startswith("runs"):
                    continue
                if e.name == root.name:
                    continue
                out.append(Path(e.path))
    except OSError:
        return []
    return sorted(out, key=lambda p: p.name)


def _locate_index() -> Dict[str, Path]:
    """run_id → 批次目录（只扫目录名，不读文件；缓存 120 秒）。"""
    now = time.time()
    with _LOCATE_LOCK:
        cached = _LOCATE.get("map") or {}
        if cached and now - float(_LOCATE.get("at") or 0.0) < _LOCATE_TTL_S:
            return cached
    m: Dict[str, Path] = {}
    for r in _sibling_batch_roots():
        try:
            with os.scandir(r) as it:
                for e in it:
                    if e.is_dir() and RUN_ID_RE.match(e.name):
                        m.setdefault(e.name, Path(e.path))
        except OSError:
            continue
    with _LOCATE_LOCK:
        _LOCATE.update({"at": now, "map": m})
    return m


def read_run_dir(run_id: str) -> Path:
    """**只读**定位 run 目录：先默认根，再同级批次目录；都没有 ⇒ `RunNotFound`。

    为什么需要（BT-UI-01）：批次目录（`runs_sep_all_latest` 等）里的 run 不在默认
    `runs/` 下，而 `run_dir()` 只认默认根 ⇒ 页面从批次选中一个 run 后，
    `GET /backtests/{id}` 及其图表 / 逐笔 / 指标全部 404（页面上表现为「图表整块消失」）。
    写入路径**不**放宽：`create_run` / `write_run` / 账本写入仍只走 `run_dir()`。
    """
    rid = assert_run_id(run_id)
    d = run_dir(rid)
    if d.exists():
        return d
    hit = _locate_index().get(rid)
    if hit is not None:
        return hit
    raise RunNotFound(f"run 不存在：{rid}")


# ══════════════════════════ 创建 / 读取 / 状态 ══════════════════════════

def create_run(run_id: str, doc: Dict[str, Any]) -> Path:
    """**原子独占**创建 run 目录并写入 `run.json`。重名 ⇒ `RunExists`。"""
    rid = assert_run_id(run_id)
    root = get_runs_root()
    root.mkdir(parents=True, exist_ok=True)
    d = run_dir(rid)
    with _CREATE_LOCK:
        if d.exists():                       # 先判存在（不覆盖、不删除）
            raise RunExists(f"run 已存在，拒绝覆盖：{rid}")
        try:
            os.mkdir(d)                      # 存在即 OSError ⇒ 重名拒绝
        except FileExistsError as exc:
            raise RunExists(f"run 已存在，拒绝覆盖：{rid}") from exc
        except OSError as exc:
            # 某些运行环境（含本机工具沙箱 shim）把 mkdir 的 EEXIST 报成 PermissionError；
            # 按 errno/文本判定，保证「重名 ⇒ 拒绝」语义一致（不误报 500）。
            if getattr(exc, "errno", None) == errno.EEXIST or "EEXIST" in str(exc):
                raise RunExists(f"run 已存在，拒绝覆盖：{rid}") from exc
            raise
    doc = dict(doc)
    doc.setdefault("run_id", rid)
    doc.setdefault("created_at", utc_now_iso())
    doc["updated_at"] = utc_now_iso()
    _write_json(d / "run.json", doc)
    return d


def _write_json(p: Path, doc: Dict[str, Any]) -> None:
    # 临时名**唯一**（pid+线程+序号）：同一 run 的并发写（如后台任务线程与请求线程）
    # 不会互相覆盖对方的 .tmp，从而避免 `os.replace` 取到已被移走的临时文件（ENOENT）。
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.{next(_TMP_SEQ)}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, p)


def _epoch_sec_to_iso(v) -> Optional[str]:
    """epoch 秒 → ISO UTC。无法解析 ⇒ None（**不猜**）。"""
    try:
        sec = int(v)
    except (TypeError, ValueError):
        return None
    if sec <= 0:
        return None
    return datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _completion_evidence(d: Path, sm: Dict[str, Any]) -> Dict[str, Any]:
    """从**本 run 目录自己的产物**推断完成度（2026-10-21 CODE-REVIEW-02 修复）。

    纪律（R10 状态不得冒充 / R9 产物必登记）：
      · 旧实现把 `status` **写死** `succeeded` ⇒ 失败/未登记的运行也显示成功；
      · 现在只按落盘证据判：`manifest.json`（引擎原子提交标记）+
        `summary.json`（可解析的结果块）同时存在 ⇒ 有完成证据；
      · 判不了 ⇒ `unknown`，并附 `status_basis` 说明依据；
      · 无论哪种推断，只要没有 `run.json` 就标 `registered=False`（未登记），
        **不把历史产物洗成正式档案**（CODE-REVIEW-01 §4 第 5 条：不补造）。
    """
    basis: List[str] = []
    man = d / "manifest.json"
    has_manifest = man.exists()
    has_summary_block = bool(sm.get("summary_variant_a") or sm.get("summary"))
    if has_manifest and has_summary_block:
        status = STATUS_SUCCEEDED
        basis.append("manifest.json（引擎原子提交标记）+ summary.json 结果块均在 ⇒ 有完成证据")
    elif has_summary_block:
        # 有结果但缺提交标记：结果可能存在，但完成度证据不足
        status = STATUS_UNKNOWN
        basis.append("summary.json 有结果块但缺 manifest.json ⇒ 完成度证据不足")
    else:
        status = STATUS_UNKNOWN
        basis.append("summary.json 无结果块 ⇒ 无法判定完成度")
    return {"status": status, "basis": basis}


def _summary_to_run_doc(d: Path, run_id: str) -> Dict[str, Any]:
    """**无 run.json 的历史运行目录** → 合成只读视图（CODE-REVIEW-02 修复后）。

    硬纪律（对应 12 条硬规则）：
      · **不写死状态**：`status` 只按本目录产物证据推断（见 `_completion_evidence`），
        判不了给 `unknown`（R10）；
      · **不造时间**：`created_at` 只取落盘真实值（summary/inputs.json 的
        `timestamp_created`，epoch 秒→ISO 是确定性换算）；缺失 ⇒ `None`，
        界面显示「未记录」（R2，旧实现 `or utc_now_iso()` 让 1195 个 run 的
        创建时间全等于读取时刻）；
      · **不造参数**：`risk_budget_per_trade / fee_rate / slippage_bps` 本 run
        未落盘 ⇒ `None` + `config_note`（R2/R7，旧实现写死 0.005/0.0005/3.0；
        界面显示的必须等于实际跑的）；
      · **如实标未登记**：`registered=False` + `record_note`（R9）；
      · 窗口 / 指纹改从**真正落盘的 `inputs.json`** 读（旧实现读 `summary.json`
        的 `data_summary`，该文件里没有 ⇒ `win_start_ms/win_end_ms` 恒 null）。
    """
    sf = d / "summary.json"
    if not sf.exists():
        raise RunNotFound(run_id)
    sm = json.loads(sf.read_text(encoding="utf-8"))

    # inputs.json：真实创建时刻、真实窗口、真实代码指纹（引擎落盘，非读取时生成）
    inf = d / "inputs.json"
    inp: Dict[str, Any] = {}
    if inf.exists():
        try:
            inp = json.loads(inf.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            inp = {}

    symbol = sm.get("symbol") or inp.get("symbol") or "BTCUSDT"
    summary_data = sm.get("summary_variant_a") or sm.get("summary") or sm

    initial_eq = float(summary_data.get("initial_equity") or 2000.0)
    final_eq = float(summary_data.get("final_equity") or initial_eq)
    net_pnl = float(summary_data.get("net_pnl") or (final_eq - initial_eq))
    ret_pct = float(summary_data.get("net_return_pct") or ((net_pnl / initial_eq * 100.0) if initial_eq else 0.0))
    trade_cnt = int(summary_data.get("trade_count") or summary_data.get("trades_count") or 0)
    # ⚠ `summary_variant_a` 里**根本没有** `win_count`/`loss_count`/`win_rate` 字段。
    # 用 `or 0` 会把「没统计」伪装成「0 胜 0 负」——实测一笔净亏 −5.24 的交易被写成 0/0。
    # 项目纪律「缺结果一律 None，不用 0 冒充」⇒ 缺就 None（真实值由 /metrics 从账本现算）。
    win_cnt = summary_data.get("win_count")
    loss_cnt = summary_data.get("loss_count")
    win_rate = summary_data.get("win_rate")
    if win_rate is None and win_cnt is not None and trade_cnt:
        win_rate = float(win_cnt) / trade_cnt * 100.0
    # 回撤：**本 run 的 summary.json 里根本没有这个字段**（包只落 fee/funding/net_pnl/trade_count）。
    # 项目纪律「缺结果一律 None，不用 0 冒充」⇒ 缺就返回 None，让界面显示「—」。
    # 用 `or 0.0` 会把「没算」伪装成「回撤 0%」，是**假数字**（实测 200/200 条全被这样写成了 0.0）。
    _mdd_raw = summary_data.get("max_drawdown_pct")
    mdd = float(_mdd_raw) if _mdd_raw is not None else None

    # 窗口：data_summary 落在 **inputs.json**（旧实现读 summary.json ⇒ 恒 null）
    time_range = (inp.get("data_summary") or {}).get("time_range_1h") or {}
    t_start = time_range.get("start_ms")
    t_end = time_range.get("end_ms")

    # 完成度证据 + 未登记标记（见 _completion_evidence 纪律说明）
    comp = _completion_evidence(d, sm)
    created_at = (sm.get("created_at") or sm.get("executed_at")
                  or _epoch_sec_to_iso(inp.get("timestamp_created")))
    finished_at = sm.get("finished_at")          # 历史包不落盘完成时刻 ⇒ 一般为 None

    # 数据质量：**不再硬编码 "ok"**。有 `reconcile`/`identity` 块就按其真实自检结果给，
    # 没有就如实报 `unknown`（「无法判定」≠「通过」）。这些自检是回测程序自己写的，
    # 只能作为「程序内部一致性」的证据，不能单独作为「结果正确」的证据。
    rec = sm.get("reconcile") or {}
    ident = sm.get("identity") or {}
    dq_reasons: List[str] = []
    if rec.get("cash_identity_ok") is False:
        dq_reasons.append("现金恒等式不成立")
    if rec.get("equity_identity_ok") is False:
        dq_reasons.append("权益恒等式不成立")
    if rec.get("equity_curve_tail_matches_final") is False:
        dq_reasons.append("权益曲线末点与最终权益不一致")
    if ident.get("chain_ok") is False:
        dq_reasons.append("身份链条不闭合")
    if not (rec or ident):
        dq_status = "unknown"
        dq_reasons = ["summary.json 无 reconcile/identity 块 ⇒ 无法判定对账"]
    else:
        dq_status = "mismatch" if dq_reasons else "ok"

    hashes = dict(inp.get("fingerprints") or {})
    if not hashes:
        hashes = sm.get("fingerprints") or {}

    return {
        "run_id": run_id,
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "strategy_key": "wx_event_trend_v0_1",
        "status": comp["status"],
        "status_basis": "；".join(comp["basis"]),
        "registered": False,
        "record_note": ("该目录无 run.json（历史批量产物，未登记）⇒ 本视图由落盘产物只读合成，"
                        "不作为正式运行档案；界面显示「未登记」"),
        "created_at": created_at,
        "finished_at": finished_at,
        "spec": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "symbols": [symbol],
            "exchange": "binance",
            "market": "um",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
            "initial_equity": initial_eq,
        },
        "config": {
            "actual_config": {
                "initial_equity": initial_eq,
                # 本 run **未落盘**这三项 ⇒ 如实 null（旧实现写死 0.005/0.0005/3.0，
                # 界面会拿假参数充当实际配置，CODE-REVIEW-01 P0-2）
                "risk_budget_per_trade": None,
                "fee_rate": None,
                "slippage_bps": None,
            },
            "config_note": ("risk_budget_per_trade / fee_rate / slippage_bps 在本 run 落盘文件中"
                            "无记录 ⇒ null（未记录）；不许用默认值冒充实际配置"),
        },
        "window": {
            "win_start_ms": t_start,
            "win_end_ms": t_end,
            "symbol": symbol,
            "exchange": "binance",
            "market": "um",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
        },
        "metrics": {
            "initial_equity": initial_eq,
            "final_equity": final_eq,
            "net_pnl": net_pnl,
            "net_return_pct": ret_pct,
            "trades_count": trade_cnt,
            "wins": win_cnt,
            "losses": loss_cnt,
            "win_rate_pct": win_rate,
            "max_drawdown_pct": mdd,
            "profit_factor": summary_data.get("profit_factor"),
            "avg_win_loss_ratio": summary_data.get("avg_win_loss_ratio"),
            "funnel": sm.get("funnel"),
            "basis": ("summary.json 落盘原值（该文件未统计的字段一律 null，不填 0）；"
                      "胜负/回撤/PF/盈亏比的真实值由 GET /metrics 从本 run 账本现算；"
                      "本视图未登记（无 run.json），仅作只读历史参考"),
        },
        "data_quality": {
            "status": dq_status,
            "reasons": dq_reasons,
            "reconcile_ok": (None if not rec else not dq_reasons),
            "source": "summary.json:reconcile+identity（回测程序自检，非独立证据）",
        },
        "engine": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "signal_version": "v0.1.0",
            "core_version": "v0.1.0",
            "exec_core_version": "trading_core_shared_v1",
        },
        "hashes": hashes,
        "summary": sm,
    }


def read_run(run_id: str) -> Dict[str, Any]:
    d = read_run_dir(run_id)
    f = d / "run.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return _summary_to_run_doc(d, run_id)


def write_run(run_id: str, doc: Dict[str, Any]) -> None:
    d = run_dir(run_id)
    doc = dict(doc)
    doc["updated_at"] = utc_now_iso()
    _write_json(d / "run.json", doc)


def register_external_run(run_id: str, doc: Dict[str, Any], *,
                          run_dir_path: Optional[Path] = None) -> Path:
    """为**已在目录里落好产物**的 run 补写登记档 `run.json`（CODE-REVIEW-02）。

    用途：根目录批量脚本（`run_batch_*.py`）用策略包引擎直接产出 run 目录
    （summary/trades/orders/manifest…），但从不写 `run.json` ⇒ 全工程 2127 个
    运行「结果在、档案不在」，读接口只能现场合成（假时间/假参数/假状态）。

    纪律（与 `create_run` 的差别与相同点）：
      · **同一写入口**：run.json 仍只由本模块写（R3），脚本只提交事实；
      · **不猜任何值**：`doc` 必须由调用方给出真实事实（开始/结束时刻、实际配置、
        窗口、结果），本函数只补 `run_id / created_at / updated_at` 并落盘；
      · **不覆盖**：目标目录已有 `run.json` ⇒ `RunExists`（R4，绝不洗掉旧档案）；
      · **原子写**：`.tmp` + `os.replace`；
      · `run_dir_path`：显式指定目录（默认根之外的同级批次目录用）；给了就必须
        存在、是目录、且目录名 == run_id。
    """
    rid = assert_run_id(run_id)
    d = Path(run_dir_path) if run_dir_path is not None else run_dir(rid)
    if not d.is_dir():
        raise RunNotFound(f"run 目录不存在：{d}")
    if d.name != rid:
        raise IsolationError(f"目录名与 run_id 不一致：{d.name} != {rid}")
    target = d / "run.json"
    if target.exists():
        raise RunExists(f"run.json 已存在，拒绝覆盖：{target}")
    doc = dict(doc)
    doc.setdefault("run_id", rid)
    doc.setdefault("created_at", utc_now_iso())
    doc["updated_at"] = utc_now_iso()
    doc.setdefault("registration", {
        "registered_by": "register_external_run",
        "note": "批量脚本产物补登记：事实由调用方提供，无默认值兜底",
    })
    _write_json(target, doc)
    return target


def update_run(run_id: str, **fields) -> Dict[str, Any]:
    doc = read_run(run_id)
    doc.update(fields)
    write_run(run_id, doc)
    return doc


def set_status(run_id: str, status: str, **extra) -> Dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"非法状态：{status}")
    doc = read_run(run_id)
    doc["status"] = status
    now = utc_now_iso()
    if status == STATUS_RUNNING and not doc.get("started_at"):
        doc["started_at"] = now
    if status in TERMINAL_STATUSES:
        doc["finished_at"] = now
    doc.update(extra)
    write_run(run_id, doc)
    return doc


def write_inputs_manifest(run_id: str, doc: Dict[str, Any]) -> Path:
    d = run_dir(run_id)
    p = d / "inputs_manifest.json"
    _write_json(p, doc)
    return p


# ══════════════════════════ 实际输入切片快照（不可变）══════════════════════════

SNAPSHOT_DIRNAME = "inputs"
_SNAPSHOT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def snapshot_dir(run_id: str) -> Path:
    return run_dir(run_id) / SNAPSHOT_DIRNAME


def _assert_snapshot_name(name: str) -> str:
    if not isinstance(name, str) or not _SNAPSHOT_NAME_RE.match(name) or ".." in name:
        raise IsolationError(f"非法快照文件名：{name!r}")
    return name


def write_snapshot(run_id: str, name: str, data: bytes) -> Path:
    """把**实际输入切片**原样落盘（原子写）。`data` 即被哈希的字节 ⇒ 文件哈希 == 记录哈希。"""
    nm = _assert_snapshot_name(name)
    d = snapshot_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / nm
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)
    return p


def read_snapshot(run_id: str, name: str) -> bytes:
    nm = _assert_snapshot_name(name)
    p = read_run_dir(run_id) / SNAPSHOT_DIRNAME / nm
    if not p.exists():
        raise RunNotFound(f"快照不存在：{run_id}/{nm}")
    return p.read_bytes()


def write_chart(run_id: str, chart: Dict[str, Any]) -> Path:
    """图表载荷单独落盘（`chart.json`），**不塞进 run.json**（列表接口要逐个读 run.json）。"""
    p = run_dir(run_id) / "chart.json"
    _write_json(p, chart)
    return p


def read_chart(run_id: str) -> Dict[str, Any]:
    p = read_run_dir(run_id) / "chart.json"
    if not p.exists():
        raise RunNotFound(f"图表不存在：{run_id}")
    return json.loads(p.read_text(encoding="utf-8"))


_LIST_CACHE: Dict[str, Any] = {"at": 0.0, "root": None, "val": None, "ttl": 0.0}
_LIST_LOCK = threading.Lock()
_LIST_BUILD_LOCK = threading.Lock()
# 列表缓存时长：列表内容（已完成的 run）几乎不变，120s 足够；
# 有 queued/running 时缩到 3s —— 状态变化要能被 2s 轮询看到。
# ⚠ 原来是 10s：页面停留超过 10 秒再刷新就要重扫 1198 个目录（约 7 秒），
# 这段时间里它会和批次请求抢磁盘与 GIL ⇒ 首屏图表要等到 19 秒（BT-UI-01 实测）。
_LIST_TTL_S = 120.0
_LIST_TTL_PENDING_S = 3.0


def list_runs(limit: int = 200) -> List[Dict[str, Any]]:
    """列出全部运行（按创建时间倒序）。

    2026-10-18（BT-UI-01）：原实现逐个目录串行读盘，`runs/` 有 1198 个目录时
    一次请求要 18–21 秒（limit 只是**最后**才截断，等于没生效）⇒ 回测页首屏
    35 秒空白。现在：
      · 目录并行读（8 线程，纯 I/O 与 JSON 解析）；
      · 全量结果缓存 3 秒（同一屏多次请求不重复扫盘）；
      · **存在非终态 run 时不写缓存** —— 状态变化要立刻可见，不拿旧值糊弄。
    返回语义不变：仍按 `created_at` 倒序取前 `limit` 条。
    """
    root = get_runs_root()
    if not root.exists():
        return []

    now = time.time()
    with _LIST_LOCK:
        cached = _LIST_CACHE.get("val")
        if (cached is not None and _LIST_CACHE.get("root") == str(root)
                and now - float(_LIST_CACHE.get("at") or 0.0) < float(_LIST_CACHE.get("ttl") or 0.0)):
            return cached[:max(1, int(limit))]
        build_lock = _LIST_BUILD_LOCK

    # 同 key 只构建一次：预热线程与首个请求同时到达时，不要让两份扫描串着做
    with build_lock:
        now = time.time()
        with _LIST_LOCK:
            cached = _LIST_CACHE.get("val")
            if (cached is not None and _LIST_CACHE.get("root") == str(root)
                    and now - float(_LIST_CACHE.get("at") or 0.0) < float(_LIST_CACHE.get("ttl") or 0.0)):
                return cached[:max(1, int(limit))]

        dirs = [d for d in root.iterdir() if d.is_dir()]
        # 将同级批次目录（包含 runs_work_dynamic_bt_01 完整批次）纳入正常回测列表
        seen_names = {d.name for d in dirs}
        for br in _sibling_batch_roots():
            try:
                for d in br.iterdir():
                    if d.is_dir() and RUN_ID_RE.match(d.name) and d.name not in seen_names:
                        dirs.append(d)
                        seen_names.add(d.name)
            except OSError:
                continue

        def _one(d: Path) -> Dict[str, Any]:
            try:
                return read_run(d.name)
            except Exception as exc:  # noqa: BLE001
                return {"run_id": d.name, "status": "unknown",
                        "note": f"读取失败：{type(exc).__name__}: {exc}"}

        workers = min(8, max(1, len(dirs)))
        if len(dirs) > 32:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                out: List[Dict[str, Any]] = list(ex.map(_one, dirs))
        else:
            out = [_one(d) for d in dirs]

        out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

        has_pending = any(str(x.get("status") or "") in (STATUS_QUEUED, STATUS_RUNNING) for x in out)
        ttl = _LIST_TTL_PENDING_S if has_pending else _LIST_TTL_S
        with _LIST_LOCK:
            _LIST_CACHE.update({"at": now, "root": str(root), "val": out, "ttl": ttl})

    return out[:max(1, int(limit))]


def mark_orphans_interrupted(keep: Optional[Set[str]] = None) -> List[str]:
    """启动时把遗留 `queued|running` 标 `interrupted`（保留原因，不冒充成功）。

    `keep`：仍在任务队列里排队/执行的 run_id 集合。传了就**跳过**这些 run，
    避免把「已投递、等待 worker 执行」的回测误判为中断（第 2 期任务队列）。
    """
    root = get_runs_root()
    if not root.exists():
        return []
    touched: List[str] = []
    for d in root.iterdir():
        if not d.is_dir() or not RUN_ID_RE.match(d.name):
            continue
        if keep and d.name in keep:
            continue
        f = d / "run.json"
        if not f.exists():
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                 # noqa: BLE001
            continue
        if doc.get("status") in (STATUS_QUEUED, STATUS_RUNNING):
            doc["status"] = STATUS_INTERRUPTED
            doc["interrupted_at"] = utc_now_iso()
            doc["failure_reason"] = (
                "服务重启时该运行仍为 queued/running ⇒ 标 interrupted"
                "（未完成，结果不得当成功使用）")
            doc["updated_at"] = utc_now_iso()
            _write_json(f, doc)
            touched.append(d.name)
    return touched


# ══════════════════════════ 交易账本（独立 schema）══════════════════════════

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  run_id TEXT, key TEXT, value TEXT, PRIMARY KEY(run_id, key));
CREATE TABLE IF NOT EXISTS signals (
  run_id TEXT, signal_id TEXT, symbol TEXT, signal_bar_open_ms INTEGER, confirm_ms INTEGER,
  kind TEXT, direction TEXT, stop_price REAL, atr REAL, oi_score REAL,
  oi_available INTEGER, oi_quality TEXT, rank_key REAL, in_window INTEGER);
CREATE TABLE IF NOT EXISTS orders (
  run_id TEXT, order_id TEXT, trade_id TEXT, signal_id TEXT, symbol TEXT, side TEXT, phase TEXT,
  ts_ms INTEGER, action TEXT, reason TEXT, price REAL, qty REAL, fee REAL,
  stop_price REAL, signal_type INTEGER);
CREATE TABLE IF NOT EXISTS trades (
  run_id TEXT, trade_id TEXT, signal_id TEXT, symbol TEXT, direction TEXT, signal_type INTEGER,
  entry_time_ms INTEGER, exit_time_ms INTEGER, entry_price REAL, exit_price REAL, qty REAL,
  gross_pnl REAL, entry_fee REAL, exit_fee REAL, fees REAL, funding_fee REAL, net_pnl REAL,
  exit_reason TEXT, mfe REAL, mae REAL, holding_hours REAL, funding_events INTEGER,
  funding_gaps_json TEXT, stop_trace_json TEXT);
CREATE TABLE IF NOT EXISTS funding (
  run_id TEXT, trade_id TEXT, funding_time_ms INTEGER, rate REAL, mark REAL, qty REAL,
  amount REAL, source TEXT);
CREATE TABLE IF NOT EXISTS equity (
  run_id TEXT, ts_ms INTEGER, cash_equity REAL, unrealized_pnl REAL, total_equity REAL,
  open_positions_count INTEGER, open_risk_pct REAL);
CREATE TABLE IF NOT EXISTS rejected (
  run_id TEXT, ts_ms INTEGER, symbol TEXT, phase TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS ix_bt_sig ON signals(run_id, symbol, signal_bar_open_ms);
CREATE INDEX IF NOT EXISTS ix_bt_ord ON orders(run_id, trade_id);
CREATE INDEX IF NOT EXISTS ix_bt_trd ON trades(run_id, trade_id);
CREATE INDEX IF NOT EXISTS ix_bt_fund ON funding(run_id, trade_id);
CREATE INDEX IF NOT EXISTS ix_bt_eq ON equity(run_id, ts_ms);
"""


class Ledger:
    """独立交易账本。**不删除**任何既有文件；`ledger.sqlite3` 已存在即拒绝。"""

    def __init__(self, run_id: str, *, commit_every: int = 1):
        d = run_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "ledger.sqlite3"
        self.run_id = run_id
        if self.path.exists():
            raise RunExists(f"账本已存在，拒绝覆盖：{self.path}")
        self.con = sqlite3.connect(str(self.path))
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(LEDGER_SCHEMA)
        self.commit_every = max(1, int(commit_every))
        self._pending = 0
        self.counts: Dict[str, int] = {}

    def _maybe_commit(self):
        self._pending += 1
        if self._pending >= self.commit_every:
            self.con.commit()
            self._pending = 0

    def _ins(self, table: str, cols: List[str], row: List[Any]):
        self.con.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            row)
        self.counts[table] = self.counts.get(table, 0) + 1
        self._maybe_commit()

    def meta(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?)",
                         (self.run_id, str(key), str(value)))
        self._maybe_commit()

    def signal(self, **kw):
        cols = ["run_id", "signal_id", "symbol", "signal_bar_open_ms", "confirm_ms", "kind",
                "direction", "stop_price", "atr", "oi_score", "oi_available", "oi_quality",
                "rank_key", "in_window"]
        self._ins("signals", cols, [self.run_id] + [kw.get(c) for c in cols[1:]])

    def order(self, **kw):
        cols = ["run_id", "order_id", "trade_id", "signal_id", "symbol", "side", "phase",
                "ts_ms", "action", "reason", "price", "qty", "fee", "stop_price",
                "signal_type"]
        self._ins("orders", cols, [self.run_id] + [kw.get(c) for c in cols[1:]])

    def trade(self, **kw):
        cols = ["run_id", "trade_id", "signal_id", "symbol", "direction", "signal_type",
                "entry_time_ms", "exit_time_ms", "entry_price", "exit_price", "qty",
                "gross_pnl", "entry_fee", "exit_fee", "fees", "funding_fee", "net_pnl",
                "exit_reason", "mfe", "mae", "holding_hours", "funding_events",
                "funding_gaps_json", "stop_trace_json"]
        self._ins("trades", cols, [self.run_id] + [kw.get(c) for c in cols[1:]])

    def funding(self, **kw):
        cols = ["run_id", "trade_id", "funding_time_ms", "rate", "mark", "qty", "amount",
                "source"]
        self._ins("funding", cols, [self.run_id] + [kw.get(c) for c in cols[1:]])

    def equity(self, **kw):
        cols = ["run_id", "ts_ms", "cash_equity", "unrealized_pnl", "total_equity",
                "open_positions_count", "open_risk_pct"]
        self._ins("equity", cols, [self.run_id] + [kw.get(c) for c in cols[1:]])

    def rejected(self, **kw):
        cols = ["run_id", "ts_ms", "symbol", "phase", "reason"]
        self._ins("rejected", cols, [self.run_id] + [kw.get(c) for c in cols[1:]])

    def flush(self):
        self.con.commit()
        self._pending = 0

    def close(self):
        self.con.commit()
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def ledger_path(run_id: str) -> Path:
    return read_run_dir(run_id) / "ledger.sqlite3"


def read_ledger_rows(run_id: str, table: str, order_by: Optional[str] = None) -> List[dict]:
    """只读读取账本某表（优先 SQLite，无 SQLite 时自动降级读取 JSONL）。"""
    p = ledger_path(run_id)
    if p.exists():
        if table not in ("signals", "orders", "trades", "funding", "equity", "rejected", "meta"):
            raise ValueError(f"未知账本表：{table}")
        sql = f"SELECT * FROM {table} WHERE run_id=?"
        if order_by:
            sql += f" ORDER BY {order_by}"
        with closing(sqlite3.connect(f"file:{p}?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, (run_id,))]

    # 兼容 JSONL 文件格式
    d = read_run_dir(run_id)
    jsonl_map = {
        "trades": ["trades.jsonl"],
        "orders": ["orders.jsonl"],
        "rejected": ["rejects.jsonl", "rejected.jsonl"],
        "equity": ["equity_curve.jsonl"],
        "signals": ["candidates.jsonl", "signals.jsonl"],
        "funding": ["funding.jsonl"],
    }
    candidate_files = jsonl_map.get(table, [])
    jf_path = None
    for cf in candidate_files:
        if (d / cf).exists():
            jf_path = d / cf
            break

    if jf_path:
        rows = []
        for line in jf_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                r.setdefault("run_id", run_id)
                if table == "equity":
                    r.setdefault("ts_ms", r.get("timestamp") or r.get("t"))
                    r.setdefault("cash_equity", r.get("cash", 0.0))
                    r.setdefault("total_equity", r.get("equity", 0.0))
                    r.setdefault("open_positions_count", r.get("positions", 0))
                elif table == "trades":
                    # ⚠ **不得**用 `f"trd_{len(rows)+1}"` 兜底：那是**按行号猜配**，
                    # 会让 `trd_1` 与账本真实标识（`positions.jsonl` 的 `trd_<16hex>`）对不上，
                    # 且「图上选中的那一笔」失去可核对性。真实标识请走
                    # `ledger_links.LedgerFacts.trade_rows`（源：POSITION_CLOSED.payload.trade_id）。
                    r.setdefault("trade_id", r.get("trade_id"))
                    r.setdefault("entry_time_ms", r.get("entry_time") or r.get("entry_time_ms"))
                    r.setdefault("exit_time_ms", r.get("exit_time") or r.get("exit_time_ms"))
                    r.setdefault("gross_pnl", r.get("gross_pnl", 0.0))
                    r.setdefault("fees", (r.get("entry_fee", 0.0) + r.get("exit_fee", 0.0)))
                    r.setdefault("funding_fee", r.get("funding_fee", 0.0))
                    r.setdefault("net_pnl", r.get("net_pnl", 0.0))
                elif table == "orders":
                    # 同上：不用 `f"ord_{len(rows)+1}"` 猜配（`orders.jsonl` 本身有真 `order_id`）。
                    r.setdefault("order_id", r.get("order_id"))
                    # 真实时刻在 `created_at`（JSONL 里没有 `ts_ms`/`timestamp`）。
                    r.setdefault("ts_ms", (r.get("created_at") or r.get("submitted_at")
                                           or r.get("filled_at") or r.get("timestamp")
                                           or r.get("ts_ms")))
                    r.setdefault("price", r.get("fill_price"))
                    r.setdefault("qty", r.get("quantity"))
                elif table == "rejected":
                    r.setdefault("ts_ms", r.get("timestamp") or r.get("ts_ms"))
                rows.append(r)
            except Exception:
                continue
        return rows

    # 若独立 equity_curve.jsonl 不存在，但存在 trades.jsonl，则基于已实现收益与初始权益合成已实现净值曲线
    if table == "equity":
        trades_file = d / "trades.jsonl"
        summary_file = d / "summary.json"
        init_eq = 2000.0
        if summary_file.exists():
            try:
                s_data = json.loads(summary_file.read_text(encoding="utf-8"))
                init_eq = float((s_data.get("summary_variant_a") or {}).get("initial_equity") or s_data.get("initial_equity") or 2000.0)
            except Exception:
                pass
        eq_rows = []
        if trades_file.exists():
            t_rows = []
            for line in trades_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    t_rows.append(json.loads(line))
                except Exception:
                    continue
            t_rows.sort(key=lambda x: int(x.get("exit_time_ms") or x.get("exit_time") or 0))
            if t_rows:
                first_entry = int(t_rows[0].get("entry_time_ms") or t_rows[0].get("entry_time") or 0)
                if first_entry > 0:
                    eq_rows.append({
                        "run_id": run_id,
                        "ts_ms": first_entry - 60000,
                        "cash_equity": init_eq,
                        "total_equity": init_eq,
                        "open_positions_count": 0,
                    })
                cum_eq = init_eq
                for tr in t_rows:
                    exit_ms = int(tr.get("exit_time_ms") or tr.get("exit_time") or 0)
                    net_pnl = float(tr.get("net_pnl") or 0.0)
                    cum_eq += net_pnl
                    eq_rows.append({
                        "run_id": run_id,
                        "ts_ms": exit_ms,
                        "cash_equity": round(cum_eq, 4),
                        "total_equity": round(cum_eq, 4),
                        "open_positions_count": 0,
                    })
        if not eq_rows:
            eq_rows.append({
                "run_id": run_id,
                "ts_ms": int(time.time() * 1000),
                "cash_equity": init_eq,
                "total_equity": init_eq,
                "open_positions_count": 0,
            })
        return eq_rows

    if table in ("funding", "rejected", "orders", "signals"):
        return []

    raise RunNotFound(f"账本与数据文件均不存在：{run_id}/{table}")


def ledger_counts(run_id: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for t in ("signals", "orders", "trades", "funding", "equity", "rejected"):
        try:
            out[t] = len(read_ledger_rows(run_id, t))
        except Exception:                                 # noqa: BLE001
            out[t] = -1
    return out


def new_order_id(run_id: str, seq: int, ts_ms: int, symbol: str, action: str) -> str:
    h = f"{run_id}|{seq}|{ts_ms}|{symbol}|{action}".encode("utf-8")
    import hashlib as _h
    return "ord_" + _h.sha256(h).hexdigest()[:16]


def new_trade_id(run_id: str, symbol: str, entry_ms: int, seq: int) -> str:
    import hashlib as _h
    return "trd_" + _h.sha256(f"{run_id}|{symbol}|{entry_ms}|{seq}".encode()).hexdigest()[:16]


def _ts() -> float:
    return time.time()


__all__ = [
    "RUNS_ROOT", "STATUSES", "STATUS_QUEUED", "STATUS_RUNNING", "STATUS_SUCCEEDED",
    "STATUS_FAILED", "STATUS_INTERRUPTED", "STATUS_UNKNOWN", "TERMINAL_STATUSES",
    "RunExists", "RunNotFound", "IsolationError",
    "utc_now_iso", "new_run_id", "assert_run_id", "run_dir", "read_run_dir",
    "create_run", "read_run", "write_run", "update_run", "set_status",
    "write_inputs_manifest", "list_runs", "mark_orphans_interrupted",
    "register_external_run",
    "snapshot_dir", "write_snapshot", "read_snapshot", "write_chart", "read_chart",
    "Ledger", "ledger_path", "read_ledger_rows", "ledger_counts",
    "new_order_id", "new_trade_id",
]
