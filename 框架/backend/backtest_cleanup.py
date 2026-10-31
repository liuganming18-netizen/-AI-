# -*- coding: utf-8 -*-
"""回测运行结果自动清理（GM-BACKTEST-AUTO-CLEANUP-13）。

定位
────
本模块是 `backtest_store.py` 的**外挂清理器**，不是它的替代品。`backtest_store.py`
的设计纪律「原子独占创建 / 绝不覆盖 / 绝不删除任何既有文件」**保持不变**；所有删除
行为只发生在**本模块**，且必须由显式配置开启。

三重默认关闭（任一不满足就只做预览、不碰磁盘）
────────────────────────────────────────────
1. `BACKTEST_CLEANUP_ENABLED` 默认 `0` ⇒ 直接返回 `skipped`，不扫盘、不建目录、不写日志。
2. `BACKTEST_CLEANUP_MODE` 默认 `dry_run` ⇒ 只算候选，不改任何文件（含日志）。
3. 物理删除需要**两个闸同时开**：`MODE=delete` **且** `BACKTEST_CLEANUP_ALLOW_DELETE=1`。
   只开 `MODE=delete` 会被降级为 `dry_run`，并在报告里写 `warnings`。

绝对不删（硬编码，不可配置放开）
────────────────────────────────
· 非终态（`queued` / `running` / `unknown` / 缺 `run.json`）⇒ 永不进候选。
· `pinned=true` 的置顶运行 ⇒ 永不进候选。
· 目录名不匹配 `backtest_store.RUN_ID_RE` ⇒ 根本不扫（原始 K 线 / OI / 资金费历史、
  生产账本、实时信号库、审计证据都在别的路径，目录名也不匹配）。
· 符号链接（即使指向根内）⇒ 一律不认作 run 目录。
· `realpath` 父目录 ≠ `realpath(根)` ⇒ 拒绝（路径穿越）。
· 根目录本身是 `/`、家目录、项目根、`数据/`、`框架/`、`回测日志/` ⇒ 直接拒跑。

并发与幂等
──────────
· 进程级 `flock` 独占锁：第二个进程拿不到锁 ⇒ `skipped`，不重复删。
· 逐个候选先 `os.rename` 到隔离区（原子认领），认领失败（源已不在）⇒ `skipped_race`。
  于是「同一候选被两个进程同时选中」时只有一个能认领成功。

时间来源（不造时间）
────────────────────
`created_at` → `finished_at` → 目录 `st_mtime`，并在 `time_basis` 里如实标注用的是哪一个。
三者都取不到 ⇒ 不进候选（`protected["no_time"]`）。

引用保护（GM-BACKTEST-CLEANUP-PROTECTION-24 追加）
──────────────────────────────────────────────────
除 `pinned` 之外，另有一条**引用保护**：被显式白名单（`references.json`）或批次清单
（`runs.jsonl` 的 `run_id`）引用过的 run ⇒ 不进候选。判定全部委托
`backtest_cleanup_references`，本模块只消费结论。

· **fail-closed**：任一引用来源「声明了却读不出」（白名单 JSON 非法、批次清单有非法行、
  `batch_meta.json` 声明的 `runs.jsonl` 缺失、扫描超上限）⇒ `plan.reference.fail_closed=True`
  ⇒ **本轮 0 候选**，`execute_plan` 记 `skipped / reference_unknown_fail_closed`。
· 前端 URL/localStorage 与 `evidence/` 里的文本 run_id **不做识别**（服务端不可见 / 属猜测），
  需由人工登记进白名单。详见 `backtest_cleanup_references` 模块 docstring 与 `RULES.md`。
· 默认开启；`BACKTEST_CLEANUP_REFERENCE_PROTECTION=0` 可关（关掉会在报告里写 warning）。
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:                                                    # 包内导入（backend 作为包）
    from . import backtest_store as _store
except ImportError:                                     # 直接 sys.path 加了 backend 目录
    import backtest_store as _store            # type: ignore[no-redef]

try:                                                    # 引用保护提供者（PROTECTION-24）
    from . import backtest_cleanup_references as _refs
except ImportError:                                     # 直接 sys.path 加了 backend 目录
    import backtest_cleanup_references as _refs   # type: ignore[no-redef]

try:
    import fcntl
except ImportError:                                     # 非 POSIX：退化为无锁（仍靠 rename 认领）
    fcntl = None                                        # type: ignore[assignment]


# ══════════════════════════════ 常量 ══════════════════════════════

MODE_OFF = "off"
MODE_DRY_RUN = "dry_run"
MODE_QUARANTINE = "quarantine"
MODE_DELETE = "delete"
MODES: Tuple[str, ...] = (MODE_OFF, MODE_DRY_RUN, MODE_QUARANTINE, MODE_DELETE)

REASON_COUNT = "count_limit"          # 超出 BACKTEST_MAX_RUNS
REASON_AGE = "age_limit"              # 超出 BACKTEST_RETENTION_HOURS

RESULT_DELETED = "deleted"
RESULT_QUARANTINED = "quarantined"
RESULT_ERROR = "error"
RESULT_SKIPPED_RACE = "skipped_race"

ACTION_PREVIEW = "preview"
ACTION_APPLY = "apply"
ACTION_SKIPPED = "skipped"

LOG_DIRNAME = "_backtest_cleanup"
LOG_FILENAME = "cleanup_log.jsonl"
LOCK_FILENAME = "cleanup.lock"
QUARANTINE_DIRNAME = "_backtest_cleanup_quarantine"


class CleanupConfigError(ValueError):
    """配置值非法（fail-closed：宁可拒跑，也不按猜的值去删东西）。"""


class CleanupBusy(RuntimeError):
    """另一个清理进程正在运行。"""


class CleanupIsolationError(RuntimeError):
    """根目录不安全（可能删到运行根之外）。"""


# ══════════════════════════════ 配置 ══════════════════════════════

def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def _opt_int(raw: Optional[str], name: str, *, minimum: int = 1) -> Optional[int]:
    """空 / `0` ⇒ `None`（不限）。非法 ⇒ 抛错（不静默当成不限）。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise CleanupConfigError(f"{name} 不是整数：{raw!r}") from exc
    if n == 0:
        return None
    if n < minimum:
        raise CleanupConfigError(f"{name} 必须 ≥ {minimum}（或 0 表示不限）：{n}")
    return n


def _opt_float(raw: Optional[str], name: str) -> Optional[float]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise CleanupConfigError(f"{name} 不是数值：{raw!r}") from exc
    if v == 0:
        return None
    if v < 0:
        raise CleanupConfigError(f"{name} 不能为负（或 0 表示不限）：{v}")
    return v


@dataclass(frozen=True)
class CleanupConfig:
    enabled: bool = False
    mode: str = MODE_DRY_RUN
    allow_delete: bool = False
    max_runs: Optional[int] = None
    retention_hours: Optional[float] = None
    log_dir: Optional[Path] = None
    quarantine_root: Optional[Path] = None
    runs_root: Optional[Path] = None
    warnings: Tuple[str, ...] = ()
    reference_policy: Optional[Any] = None       # _refs.ReferencePolicy（PROTECTION-24）

    # ── 生效模式：三重闸收敛后的真实动作 ──
    def effective_mode(self) -> str:
        if not self.enabled:
            return MODE_OFF
        if self.mode == MODE_OFF:
            return MODE_OFF
        if self.mode == MODE_DELETE and not self.allow_delete:
            return MODE_DRY_RUN
        return self.mode

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "effective_mode": self.effective_mode(),
            "allow_delete": self.allow_delete,
            "max_runs": self.max_runs,
            "retention_hours": self.retention_hours,
            "log_dir": str(self.log_dir) if self.log_dir else None,
            "quarantine_root": str(self.quarantine_root) if self.quarantine_root else None,
            "runs_root": str(self.runs_root) if self.runs_root else None,
            "warnings": list(self.warnings),
            "reference_policy": (self.reference_policy.as_dict()
                                 if self.reference_policy is not None else None),
        }


def load_config(env: Optional[Mapping[str, str]] = None, *,
                runs_root: Optional[Path] = None) -> CleanupConfig:
    """从环境变量读取配置。**默认全关**。

    | 变量 | 默认 | 含义 |
    |---|---|---|
    | `BACKTEST_CLEANUP_ENABLED` | `0` | 总开关；`0` ⇒ 什么都不做 |
    | `BACKTEST_CLEANUP_MODE` | `dry_run` | `off`/`dry_run`/`quarantine`/`delete` |
    | `BACKTEST_CLEANUP_ALLOW_DELETE` | `0` | 物理删除第二道闸 |
    | `BACKTEST_MAX_RUNS` | 不限 | 最多保留多少个终态运行 |
    | `BACKTEST_RETENTION_HOURS` | 不限 | 终态运行最多保留多少小时 |
    | `BACKTEST_CLEANUP_LOG_DIR` | `<runs 根>/../_backtest_cleanup` | 日志目录 |
    | `BACKTEST_CLEANUP_QUARANTINE_DIR` | `<runs 根>/../_backtest_cleanup_quarantine` | 隔离区 |
    | `BACKTEST_CLEANUP_REFERENCE_PROTECTION` | `1` | 引用保护开关（PROTECTION-24） |
    | `BACKTEST_CLEANUP_REFERENCES_FILE` | `<runs 根>/../_backtest_cleanup/references.json` | 引用白名单 |
    | `BACKTEST_CLEANUP_REQUIRE_REFERENCES` | `0` | `1` ⇒ 白名单缺失即拒跑 |
    | `BACKTEST_CLEANUP_BATCH_ROOT` | `<runs 根>/..` | 批次清单根 |
    """
    e: Mapping[str, str] = os.environ if env is None else env
    root = Path(runs_root) if runs_root is not None else _store.get_runs_root()
    parent = root.parent

    warnings: List[str] = []
    mode = str(e.get("BACKTEST_CLEANUP_MODE", MODE_DRY_RUN) or MODE_DRY_RUN).strip().lower()
    if mode not in MODES:
        raise CleanupConfigError(
            f"BACKTEST_CLEANUP_MODE 非法：{mode!r}（允许 {', '.join(MODES)}）")
    enabled = _truthy(e.get("BACKTEST_CLEANUP_ENABLED", "0"))
    allow_delete = _truthy(e.get("BACKTEST_CLEANUP_ALLOW_DELETE", "0"))
    if enabled and mode == MODE_DELETE and not allow_delete:
        warnings.append(
            "MODE=delete 但 BACKTEST_CLEANUP_ALLOW_DELETE≠1 ⇒ 已降级为 dry_run（物理删除需双闸）")
    if enabled and mode in (MODE_QUARANTINE, MODE_DELETE) and \
            _opt_int(e.get("BACKTEST_MAX_RUNS"), "BACKTEST_MAX_RUNS") is None and \
            _opt_float(e.get("BACKTEST_RETENTION_HOURS"), "BACKTEST_RETENTION_HOURS") is None:
        warnings.append("两项保留限制都未设置 ⇒ 无候选，不会删除任何运行")

    log_dir = Path(e["BACKTEST_CLEANUP_LOG_DIR"]) if e.get("BACKTEST_CLEANUP_LOG_DIR") \
        else parent / LOG_DIRNAME
    qroot = Path(e["BACKTEST_CLEANUP_QUARANTINE_DIR"]) \
        if e.get("BACKTEST_CLEANUP_QUARANTINE_DIR") else parent / QUARANTINE_DIRNAME

    # ── 引用保护策略（PROTECTION-24）──
    try:
        ref_policy = _refs.policy_from_env(e, runs_root=root)
    except Exception as exc:                            # noqa: BLE001
        # 策略构造失败**不能**退化成「无保护」：直接拒跑（fail-closed）
        raise CleanupConfigError(f"引用保护策略构造失败：{type(exc).__name__}: {exc}") from exc
    if not ref_policy.enabled:
        warnings.append("BACKTEST_CLEANUP_REFERENCE_PROTECTION=0 ⇒ 本轮不做引用保护"
                        "（pinned 保护仍在）")
    elif ref_policy.require_registry:
        warnings.append("引用保护严格模式：白名单缺失即拒跑")

    return CleanupConfig(
        enabled=enabled,
        mode=mode,
        allow_delete=allow_delete,
        max_runs=_opt_int(e.get("BACKTEST_MAX_RUNS"), "BACKTEST_MAX_RUNS"),
        retention_hours=_opt_float(e.get("BACKTEST_RETENTION_HOURS"), "BACKTEST_RETENTION_HOURS"),
        log_dir=log_dir,
        quarantine_root=qroot,
        runs_root=root,
        warnings=tuple(warnings),
        reference_policy=ref_policy,
    )


# ══════════════════════════════ 根目录安全 ══════════════════════════════

def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _forbidden_roots() -> List[Path]:
    pr = _project_root()
    home = Path.home()
    cands = [
        Path("/"),
        home,
        pr,
        pr / "数据",
        pr / "框架",
        pr / "回测日志",
        pr / "evidence",
        pr / "docs",
    ]
    return cands


def assert_safe_root(root: Path) -> Path:
    """根目录本身必须是一个**明确的运行根**，不能是系统/项目/数据目录。"""
    try:
        rp = Path(root).resolve(strict=False)
    except OSError as exc:                                # pragma: no cover
        raise CleanupIsolationError(f"根目录无法解析：{root}") from exc
    for f in _forbidden_roots():
        try:
            fr = f.resolve(strict=False)
        except OSError:                                   # pragma: no cover
            continue
        if rp == fr:
            raise CleanupIsolationError(f"拒绝在受保护目录上执行清理：{rp}")
    if not rp.is_dir():
        raise CleanupIsolationError(f"根目录不存在或不是目录：{rp}")
    return rp


# ══════════════════════════════ 扫描 ══════════════════════════════

@dataclass
class RunSnapshot:
    run_id: str
    path: Path
    status: str
    pinned: bool
    created_ts: Optional[float]
    time_basis: str
    size_bytes: int
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "path": str(self.path),
            "status": self.status,
            "pinned": self.pinned,
            "created_at": _iso(self.created_ts),
            "time_basis": self.time_basis,
            "size_bytes": self.size_bytes,
            "reasons": list(self.reasons),
        }


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(v: Any) -> Optional[float]:
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _dir_size(p: Path, *, limit: int = 20000) -> int:
    """目录字节数（不跟随符号链接；超限就停在 limit 个条目）。"""
    total = 0
    n = 0
    for cur, dirnames, filenames in os.walk(p, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(cur, d))]
        for fn in filenames:
            n += 1
            if n > limit:
                return total
            try:
                total += os.lstat(os.path.join(cur, fn)).st_size
            except OSError:
                continue
    return total


def _iter_run_dirs(root: Path):
    """只产出**根的直接子目录**、名字匹配 `RUN_ID_RE`、且**本身不是符号链接**的目录。"""
    try:
        with os.scandir(root) as it:
            entries = list(it)
    except OSError:
        return
    for e in entries:
        if not _store.RUN_ID_RE.match(e.name):
            continue
        # follow_symlinks=False ⇒ 符号链接（即使指向根内）返回 False
        if not e.is_dir(follow_symlinks=False):
            continue
        yield Path(e.path)


def _snapshot(d: Path, root_real: Path) -> Optional[RunSnapshot]:
    """读一个 run 目录的事实。越界 / 符号链接 ⇒ `None`（不认作 run）。"""
    try:
        if d.is_symlink():
            return None
        real = d.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if real.parent != root_real:                          # 路径穿越防线
        return None

    doc: Dict[str, Any] = {}
    rf = d / "run.json"
    if rf.exists():
        try:
            doc = json.loads(rf.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                doc = {}
        except Exception:                                 # noqa: BLE001
            doc = {}

    status = str(doc.get("status") or "").strip().lower()
    if status not in _store.STATUSES:
        status = _store.STATUS_UNKNOWN

    pinned = doc.get("pinned") is True or doc.get("pin") is True

    created_ts = _parse_iso(doc.get("created_at"))
    basis = "run.json:created_at"
    if created_ts is None:
        created_ts = _parse_iso(doc.get("finished_at"))
        basis = "run.json:finished_at"
    if created_ts is None:
        try:
            created_ts = float(d.stat().st_mtime)
            basis = "dir_mtime"
        except OSError:
            created_ts = None
            basis = "unavailable"

    return RunSnapshot(
        run_id=d.name,
        path=d,
        status=status,
        pinned=pinned,
        created_ts=created_ts,
        time_basis=basis,
        size_bytes=_dir_size(d),
    )


# ══════════════════════════════ 计划（预览）══════════════════════════════

@dataclass
class CleanupPlan:
    mode: str
    roots: List[str]
    scanned: int
    protected: Dict[str, int]
    candidates: List[RunSnapshot]
    config: Dict[str, Any]
    warnings: List[str]
    generated_at: str
    reference: Dict[str, Any] = field(default_factory=dict)   # PROTECTION-24

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "roots": list(self.roots),
            "scanned": self.scanned,
            "protected": dict(self.protected),
            "candidate_count": len(self.candidates),
            "candidates": [c.as_dict() for c in self.candidates],
            "config": dict(self.config),
            "warnings": list(self.warnings),
            "generated_at": self.generated_at,
            "reference": dict(self.reference),
        }


def plan_cleanup(cfg: CleanupConfig, roots: Optional[Sequence[Path]] = None, *,
                 now_ts: Optional[float] = None) -> CleanupPlan:
    """算出候选（**纯计算，不碰磁盘**，只读 `run.json` 与 `stat`）。"""
    now = float(now_ts if now_ts is not None else time.time())
    root_list = [Path(r) for r in (roots if roots is not None else
                                   ([cfg.runs_root] if cfg.runs_root else [_store.get_runs_root()]))]
    protected: Dict[str, int] = {}
    terminal: List[RunSnapshot] = []
    scanned = 0

    # 先把根全部校验/解析出来：引用收集需要「被清理的根」列表来排除它们自身
    real_roots: List[Path] = [assert_safe_root(r) for r in root_list]

    # ── 引用保护（PROTECTION-24）：只读收集，结论决定候选 ──
    refset = _refs.collect_references(cfg.reference_policy, exclude_roots=real_roots)

    for root, rp in zip(root_list, real_roots):
        for d in _iter_run_dirs(Path(root)):
            scanned += 1
            snap = _snapshot(d, rp)
            if snap is None:
                protected["path_escape_or_symlink"] = protected.get("path_escape_or_symlink", 0) + 1
                continue
            if snap.pinned:
                protected["pinned"] = protected.get("pinned", 0) + 1
                continue
            if snap.status not in _store.TERMINAL_STATUSES:
                key = f"non_terminal:{snap.status}"
                protected[key] = protected.get(key, 0) + 1
                continue
            # 引用来源「声明了却读不出」⇒ 全部视为受保护（fail-closed，宁可一个都不删）
            if refset.fail_closed:
                protected["reference_unknown_fail_closed"] = \
                    protected.get("reference_unknown_fail_closed", 0) + 1
                continue
            ref_src = refset.is_protected(snap.run_id)
            if ref_src:
                key = f"referenced:{ref_src}"
                protected[key] = protected.get(key, 0) + 1
                continue
            if snap.created_ts is None:
                protected["no_time"] = protected.get("no_time", 0) + 1
                continue
            terminal.append(snap)

    # 数量限制：终态运行按创建时间倒序，第 max_runs 之后全部进候选
    terminal.sort(key=lambda s: (-(s.created_ts or 0.0), s.run_id))
    if cfg.max_runs is not None:
        for i, s in enumerate(terminal):
            if i >= cfg.max_runs:
                s.reasons.append(REASON_COUNT)
    # 小时限制：年龄 > retention_hours 全部进候选
    if cfg.retention_hours is not None:
        cutoff = now - float(cfg.retention_hours) * 3600.0
        for s in terminal:
            if (s.created_ts or 0.0) < cutoff:
                s.reasons.append(REASON_AGE)

    candidates = [s for s in terminal if s.reasons]
    candidates.sort(key=lambda s: (s.created_ts or 0.0, s.run_id))

    plan_warnings = list(cfg.warnings) + list(refset.warnings)
    if refset.fail_closed:
        plan_warnings.append(
            "引用来源声明了却读不出 ⇒ fail-closed：本轮 0 候选（" + "; ".join(refset.problems) + "）")

    return CleanupPlan(
        mode=cfg.effective_mode(),
        roots=[str(r) for r in real_roots],
        scanned=scanned,
        protected=protected,
        candidates=candidates,
        config=cfg.as_dict(),
        warnings=plan_warnings,
        generated_at=_iso(now) or "",
        reference=refset.as_dict(),
    )


# ══════════════════════════════ 执行 ══════════════════════════════

class _DirLock:
    """进程级独占锁；拿不到 ⇒ `CleanupBusy`（不排队、不阻塞调用方）。"""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self._fh.close()
                self._fh = None
                raise CleanupBusy(f"另一个清理进程持有锁：{self.path}") from exc
        return self

    def __exit__(self, *a):
        if self._fh is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        return False


def _append_log(log_path: Path, record: Dict[str, Any]) -> Optional[str]:
    """追加一行 JSONL。写失败**不抛出**（清理结果不能因为日志失败而回滚）。"""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def _claim(src: Path, qroot: Path) -> Path:
    """原子认领：`os.rename` 到隔离区。源已被移走 ⇒ `FileNotFoundError`。"""
    qroot.mkdir(parents=True, exist_ok=True)
    dst = qroot / f"{src.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.quarantine"
    os.rename(src, dst)
    return dst


def execute_plan(plan: CleanupPlan, cfg: CleanupConfig, *,
                 now_ts: Optional[float] = None,
                 force_preview: bool = False) -> Dict[str, Any]:
    """按计划执行。`dry_run` / `off` / `force_preview` ⇒ **不建目录、不写日志、不动任何文件**。"""
    now = float(now_ts if now_ts is not None else time.time())
    mode = MODE_DRY_RUN if force_preview else cfg.effective_mode()
    started = _iso(now) or ""

    base: Dict[str, Any] = {
        "mode": mode,
        "config_mode": cfg.mode,
        "enabled": cfg.enabled,
        "forced_preview": bool(force_preview),
        "started_at": started,
        "finished_at": None,
        "roots": list(plan.roots),
        "scanned": plan.scanned,
        "protected": dict(plan.protected),
        "candidate_count": len(plan.candidates),
        "candidates": [c.as_dict() for c in plan.candidates],
        "results": [],
        "deleted_count": 0,
        "quarantined_count": 0,
        "error_count": 0,
        "skipped_count": 0,
        "log_path": None,
        "warnings": list(plan.warnings),
        "config": cfg.as_dict(),
        "reference": dict(plan.reference),
    }

    # ── 闸 1：总开关关闭（手动预览可越过，但仍不写盘）──
    if not cfg.enabled and not force_preview:
        base.update({"action": ACTION_SKIPPED, "skipped_reason": "disabled",
                     "finished_at": _iso(time.time())})
        return base

    # ── 闸 1.5：引用来源读不出 ⇒ fail-closed，任何模式都不产生动作 ──
    if plan.reference.get("fail_closed"):
        base.update({"action": ACTION_SKIPPED,
                     "skipped_reason": "reference_unknown_fail_closed",
                     "skipped_detail": "; ".join(plan.reference.get("problems") or []),
                     "finished_at": _iso(time.time())})
        return base

    # ── 闸 2：dry_run / off / 手动预览 ⇒ 零磁盘改动 ──
    if force_preview or mode in (MODE_DRY_RUN, MODE_OFF):
        base.update({"action": ACTION_PREVIEW,
                     "finished_at": _iso(time.time())})
        return base

    # ── 真实清理：加锁 → 逐候选认领 ──
    log_dir = cfg.log_dir or (Path(plan.roots[0]).parent / LOG_DIRNAME)
    log_path = log_dir / LOG_FILENAME
    base["log_path"] = str(log_path)
    qroot = cfg.quarantine_root or (Path(plan.roots[0]).parent / QUARANTINE_DIRNAME)
    base["quarantine_root"] = str(qroot)

    try:
        with _DirLock(log_dir / LOCK_FILENAME):
            base["lock"] = "acquired"
            base.update(_run_candidates(plan, cfg, qroot, log_path, mode))
    except CleanupBusy as exc:
        base.update({"action": ACTION_SKIPPED, "skipped_reason": "busy",
                     "skipped_detail": str(exc), "lock": "busy",
                     "finished_at": _iso(time.time())})
        return base

    base["action"] = ACTION_APPLY
    base["finished_at"] = _iso(time.time())
    return base


def _run_candidates(plan: CleanupPlan, cfg: CleanupConfig, qroot: Path,
                    log_path: Path, mode: str) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    deleted = quarantined = errors = skipped = 0

    for snap in plan.candidates:
        rec: Dict[str, Any] = {
            "run_id": snap.run_id,
            "path": str(snap.path),
            "reasons": list(snap.reasons),
            "mode": mode,
            "at": _iso(time.time()),
            "result": None,
            "error": None,
            "size_bytes": snap.size_bytes,
        }
        try:
            claimed = _claim(snap.path, qroot)
        except FileNotFoundError:
            # 已被别的进程/别的清理认领 ⇒ 幂等跳过
            rec["result"] = RESULT_SKIPPED_RACE
            rec["error"] = "源目录已不存在（已被其他清理认领）"
            skipped += 1
        except OSError as exc:
            rec["result"] = RESULT_ERROR
            rec["error"] = f"认领失败 {type(exc).__name__}: {exc}"
            errors += 1
        else:
            # 认领后再校验一次：必须在隔离区之内
            try:
                cq = claimed.resolve(strict=True)
                qq = qroot.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                rec["result"] = RESULT_ERROR
                rec["error"] = f"隔离区校验失败 {type(exc).__name__}: {exc}"
                errors += 1
            else:
                if cq.parent != qq:
                    rec["result"] = RESULT_ERROR
                    rec["error"] = f"认领路径越出隔离区：{cq}"
                    errors += 1
                elif mode == MODE_DELETE:
                    try:
                        shutil.rmtree(claimed)
                        rec["result"] = RESULT_DELETED
                        deleted += 1
                    except OSError as exc:
                        # 删除失败：**只影响本 run**，其余继续；如实记错误
                        rec["result"] = RESULT_ERROR
                        rec["error"] = f"删除失败 {type(exc).__name__}: {exc}"
                        rec["partial_path"] = str(claimed)
                        errors += 1
                else:                                  # MODE_QUARANTINE
                    rec["result"] = RESULT_QUARANTINED
                    rec["quarantined_to"] = str(claimed)
                    quarantined += 1

        # 日志写失败只记在记录里，不影响清理结论
        log_err = _append_log(log_path, rec)
        if log_err:
            rec["log_error"] = log_err
        results.append(rec)

    return {
        "results": results,
        "deleted_count": deleted,
        "quarantined_count": quarantined,
        "error_count": errors,
        "skipped_count": skipped,
    }


# ══════════════════════════════ 入口 ══════════════════════════════

def run_cleanup(cfg: Optional[CleanupConfig] = None, *,
                roots: Optional[Sequence[Path]] = None,
                dry_run: bool = False,
                now_ts: Optional[float] = None) -> Dict[str, Any]:
    """一次完整清理（或预览）。永不因单个 run 失败而抛出。"""
    cfg = cfg or load_config()
    if dry_run:
        # 强制预览：**不改写配置值**（报告里 enabled 仍如实反映配置），只加 force_preview。
        # 不建目录、不写日志、不加锁。
        plan = plan_cleanup(cfg, roots, now_ts=now_ts)
        return execute_plan(plan, cfg, now_ts=now_ts, force_preview=True)
    if not cfg.enabled:
        # 未启用：**不扫盘**直接 skipped（生产 runs 根有 2000+ 目录，白扫没必要）
        now = float(now_ts if now_ts is not None else time.time())
        return {
            "action": ACTION_SKIPPED, "skipped_reason": "disabled",
            "mode": MODE_OFF, "config_mode": cfg.mode, "enabled": False,
            "started_at": _iso(now), "finished_at": _iso(now),
            "roots": [], "scanned": 0, "protected": {}, "candidate_count": 0,
            "candidates": [], "results": [], "deleted_count": 0,
            "quarantined_count": 0, "error_count": 0, "skipped_count": 0,
            "log_path": None, "warnings": list(cfg.warnings), "config": cfg.as_dict(),
            "reference": {"status": "not_scanned", "fail_closed": False,
                          "note": "未启用 ⇒ 未做引用收集（不扫盘）"},
        }
    plan = plan_cleanup(cfg, roots, now_ts=now_ts)
    return execute_plan(plan, cfg, now_ts=now_ts)


def preview(**kw) -> Dict[str, Any]:
    """手动预览（dry-run）：只算候选，不改任何文件。"""
    kw["dry_run"] = True
    return run_cleanup(**kw)


def auto_cleanup_if_enabled(**kw) -> Dict[str, Any]:
    """给计划任务/运维钩子调用的入口：未启用 ⇒ 立刻 `skipped`，不扫盘。"""
    cfg = load_config()
    if not cfg.enabled:
        return {"action": ACTION_SKIPPED, "skipped_reason": "disabled",
                "mode": MODE_OFF, "config": cfg.as_dict()}
    return run_cleanup(cfg, **kw)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="回测运行结果清理（默认预览；默认不删任何东西）")
    ap.add_argument("--preview", action="store_true", help="只预览候选（默认行为）")
    ap.add_argument("--apply", action="store_true", help="按配置执行（仍需 ENABLED=1）")
    ap.add_argument("--runs-root", default=None, help="覆盖运行根目录（隔离测试用）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON 报告")
    a = ap.parse_args(list(argv) if argv is not None else None)

    try:
        cfg = load_config(runs_root=Path(a.runs_root) if a.runs_root else None)
    except CleanupConfigError as exc:
        print(f"配置错误：{exc}")
        return 2

    rep = preview(cfg=cfg) if (a.preview or not a.apply) else run_cleanup(cfg)

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"动作：{rep.get('action')}  模式：{rep.get('mode')}")
        print(f"扫描：{rep.get('scanned')}  候选：{rep.get('candidate_count')}")
        for c in rep.get("candidates") or []:
            print(f"  - {c['run_id']}  原因={','.join(c['reasons'])}  "
                  f"状态={c['status']}  创建={c['created_at']}（{c['time_basis']}）")
        if rep.get("results"):
            print(f"删除={rep.get('deleted_count')} 隔离={rep.get('quarantined_count')} "
                  f"失败={rep.get('error_count')} 跳过={rep.get('skipped_count')}")
        for w in rep.get("warnings") or []:
            print(f"提示：{w}")
    return 0


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(_main())


__all__ = [
    "MODE_OFF", "MODE_DRY_RUN", "MODE_QUARANTINE", "MODE_DELETE", "MODES",
    "REASON_COUNT", "REASON_AGE",
    "RESULT_DELETED", "RESULT_QUARANTINED", "RESULT_ERROR", "RESULT_SKIPPED_RACE",
    "ACTION_PREVIEW", "ACTION_APPLY", "ACTION_SKIPPED",
    "CleanupConfigError", "CleanupBusy", "CleanupIsolationError",
    "CleanupConfig", "CleanupPlan", "RunSnapshot",
    "load_config", "assert_safe_root", "plan_cleanup", "execute_plan",
    "run_cleanup", "preview", "auto_cleanup_if_enabled",
    "references",          # PROTECTION-24：引用保护提供者模块（别名，便于外部取用）
]

#: 引用保护提供者（`backtest_cleanup_references`）的模块别名。
references = _refs
