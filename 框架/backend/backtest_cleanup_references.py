# -*- coding: utf-8 -*-
"""回测运行的**引用保护**判定（GM-BACKTEST-CLEANUP-PROTECTION-24）。

定位
────
本模块只回答一个问题：**这个 run_id 是否仍被别处引用？** 它不做清理、不改磁盘、
不调用 `backtest_cleanup`（避免循环依赖）。`backtest_cleanup.plan_cleanup()` 调它，
把判定结果作为「受保护」理由之一。

为什么需要它（2026-10-30 实核事实）
──────────────────────────────────
· `backtest_cleanup` 原有关键保护只有 `run.json.pinned == true` 一条。实测真实运行根
  `回测日志/event_trend_v0_1/runs` 的 16 个有 `run.json` 的目录里，**`pinned=true` 为 0 个**
  ⇒ 该开关**全仓无人写入**，是事实上的死开关。
· 前端引用 run 的唯一方式（`BacktestWorkbench.tsx:1319-1367`）是 URL 查询参数 `?run=<run_id>`
  与 `localStorage['wyckoff_backtest_last_selection'].run`。**两者都只在浏览器里，服务端不可见**
  ⇒ 服务端**不可能**可靠识别「用户上次点开的是哪个 run」。
· 审计报告引用 run 的方式是 `evidence/**/*.md|json` 里**散落的文本 run_id**，没有注册表
  ⇒ 靠全项目扫描去「猜」引用，正是工单明令禁止的做法（判据 5）。

因此本模块**只认显式、结构化、可解析的引用来源**，其余一律不猜：

| 来源 | 载体 | 可靠性 |
|---|---|---|
| `registry` | 人工维护的 `references.json`（唯一权威白名单） | 高（显式声明） |
| `batch_manifest` | 批次目录的 `runs.jsonl`（每行 `run_id`）或其 `batch_meta.json` **声明的** `source.runs_jsonl` | 高（结构化） |
| `run_json_pinned` | `run.json` 的 `pinned` | 中（机制存在，实测无人写入；仍保留） |

**不认**（明确写在这里，防止后人误以为已覆盖）：前端 URL/localStorage、`evidence/` 里的
文本 run_id、`chart.json`/`summary.json` 里出现的其它 run 名、软链目标推断。

fail-closed 的分界线（本模块唯一的判断难点）
────────────────────────────────────────────
· **「声明了却读不出」⇒ 拒跑**（`fail_closed=True`，调用方必须给出 0 候选）：
  注册表存在但 JSON 非法 / 版本不支持 / 某条 `run_id` 非法；批次本地 `runs.jsonl` 存在但
  有非法行；`batch_meta.json` **声明了** `source.runs_jsonl` 而该路径缺失或不可读；
  扫描批次数或 run_id 数超过上限（无法保证完整）。
· **「从未声明」⇒ 不算未知**（只记 warning，不拒跑）：批次目录既无 `runs.jsonl`、
  `batch_meta.json` 也未声明成员清单。此时没有任何机器可读的东西声称「我引用了谁」，
  把它当未知会永久堵死清理，属于过度保护而不是 fail-closed。

这条分界线是本模块的设计取舍，写进 `RULES.md` 供裁决；改它必须改这里和测试。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:                                                    # 包内导入（backend 作为包）
    from . import backtest_store as _store
except ImportError:                                     # 直接 sys.path 加了 backend 目录
    import backtest_store as _store            # type: ignore[no-redef]


# ══════════════════════════════ 常量 ══════════════════════════════

REGISTRY_VERSION = 1

SOURCE_REGISTRY = "registry"
SOURCE_BATCH = "batch_manifest"
SOURCE_PINNED = "run_json_pinned"

STATUS_OK = "ok"                       # 引用来源已完整读取
STATUS_DISABLED = "disabled"           # 保护被显式关闭（不读任何来源）
STATUS_FAIL_CLOSED = "fail_closed"     # 有来源声明了引用却读不出 ⇒ 必须拒跑

BATCH_MANIFEST_NAME = "runs.jsonl"
BATCH_META_NAME = "batch_meta.json"

#: 清理器自己产生的目录（日志/隔离区）。它们不是批次，扫描时必须跳过，
#: 否则每次运行都会给自己造一个「无清单批次」的假 warning。
OWN_ARTIFACT_PREFIXES: Tuple[str, ...] = ("_backtest_cleanup",)

DEFAULT_MAX_BATCHES = 500
DEFAULT_MAX_RUN_IDS = 200_000


# ══════════════════════════════ 策略 ══════════════════════════════

@dataclass(frozen=True)
class ReferencePolicy:
    """引用保护策略。**默认开启**：开启只会增加保护，不会放宽任何既有闸。"""

    enabled: bool = True
    registry_path: Optional[Path] = None
    require_registry: bool = False
    scan_batch_manifests: bool = True
    batch_root: Optional[Path] = None
    max_batches: int = DEFAULT_MAX_BATCHES
    max_run_ids: int = DEFAULT_MAX_RUN_IDS
    # 记录 batch_root 是「显式配置」还是「按 runs 根父目录推导」：
    # 推导出来的路径不存在只是 warning；显式配置了却不存在属于配置错误 ⇒ fail-closed。
    batch_root_explicit: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "registry_path": str(self.registry_path) if self.registry_path else None,
            "require_registry": self.require_registry,
            "scan_batch_manifests": self.scan_batch_manifests,
            "batch_root": str(self.batch_root) if self.batch_root else None,
            "batch_root_explicit": self.batch_root_explicit,
            "max_batches": self.max_batches,
            "max_run_ids": self.max_run_ids,
        }


def policy_from_env(env: Mapping[str, str], *, runs_root: Path) -> ReferencePolicy:
    """从环境变量构造策略。

    | 变量 | 默认 | 含义 |
    |---|---|---|
    | `BACKTEST_CLEANUP_REFERENCE_PROTECTION` | `1` | 引用保护总开关（`0` ⇒ 不读任何来源） |
    | `BACKTEST_CLEANUP_REFERENCES_FILE` | `<runs 根>/../_backtest_cleanup/references.json` | 白名单文件 |
    | `BACKTEST_CLEANUP_REQUIRE_REFERENCES` | `0` | `1` ⇒ 白名单文件缺失即拒跑（严格模式） |
    | `BACKTEST_CLEANUP_BATCH_ROOT` | `<runs 根>/..` | 批次清单根（只读其直接子目录） |
    | `BACKTEST_CLEANUP_MAX_BATCHES` | `500` | 批次扫描上限，超过 ⇒ 拒跑 |
    | `BACKTEST_CLEANUP_MAX_RUN_IDS` | `200000` | 引用 run_id 上限，超过 ⇒ 拒跑 |
    """
    enabled = _truthy(env.get("BACKTEST_CLEANUP_REFERENCE_PROTECTION", "1"))
    require = _truthy(env.get("BACKTEST_CLEANUP_REQUIRE_REFERENCES", "0"))

    raw_reg = (env.get("BACKTEST_CLEANUP_REFERENCES_FILE") or "").strip()
    if raw_reg:
        registry = Path(raw_reg)
    else:
        registry = Path(runs_root).parent / "_backtest_cleanup" / "references.json"

    raw_batch = (env.get("BACKTEST_CLEANUP_BATCH_ROOT") or "").strip()
    if raw_batch:
        batch_root: Optional[Path] = Path(raw_batch)
        explicit = True
    else:
        batch_root = Path(runs_root).parent
        explicit = False

    max_batches = _pos_int(env.get("BACKTEST_CLEANUP_MAX_BATCHES"), DEFAULT_MAX_BATCHES)
    max_run_ids = _pos_int(env.get("BACKTEST_CLEANUP_MAX_RUN_IDS"), DEFAULT_MAX_RUN_IDS)

    return ReferencePolicy(
        enabled=enabled,
        registry_path=registry,
        require_registry=require,
        scan_batch_manifests=True,
        batch_root=batch_root,
        max_batches=max_batches,
        max_run_ids=max_run_ids,
        batch_root_explicit=explicit,
    )


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def _pos_int(raw: Any, default: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return n if n >= 1 else default


# ══════════════════════════════ 结果 ══════════════════════════════

@dataclass
class ReferenceSet:
    """一次引用收集的结果。`fail_closed=True` ⇒ 调用方**必须**给出 0 候选。"""

    status: str
    fail_closed: bool
    protect_all: bool = False
    run_ids: Dict[str, List[str]] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    batches: List[Dict[str, Any]] = field(default_factory=list)
    policy: Dict[str, Any] = field(default_factory=dict)

    def is_protected(self, run_id: str) -> Optional[str]:
        """返回引用来源标签；未被引用返回 `None`。"""
        if self.protect_all:
            return "registry:protect_all"
        srcs = self.run_ids.get(run_id)
        if not srcs:
            return None
        return "+".join(sorted(set(srcs)))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "fail_closed": self.fail_closed,
            "protect_all": self.protect_all,
            "referenced_run_count": len(self.run_ids),
            "counts_by_source": dict(self.counts),
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "batches": list(self.batches),
            "policy": dict(self.policy),
            "note": ("fail_closed=true ⇒ 本轮不产生任何候选" if self.fail_closed else ""),
        }


def _empty(status: str, *, policy: Optional[ReferencePolicy] = None) -> ReferenceSet:
    return ReferenceSet(
        status=status, fail_closed=False,
        policy=(policy.as_dict() if policy else {}),
    )


# ══════════════════════════════ 白名单 ══════════════════════════════

def load_registry(path: Path) -> Tuple[Dict[str, List[str]], bool, List[str], List[str]]:
    """读白名单。返回 `(run_ids, protect_all, problems, warnings)`。

    `problems` 非空 ⇒ 调用方 fail-closed。**任何结构不合规都不猜**：宁可拒跑。
    """
    run_ids: Dict[str, List[str]] = {}
    problems: List[str] = []
    warnings: List[str] = []

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return run_ids, False, ["registry_missing"], warnings
    except OSError as exc:
        return run_ids, False, [f"registry_unreadable:{type(exc).__name__}"], warnings

    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return run_ids, False, [f"registry_invalid_json:{exc}"], warnings

    if not isinstance(doc, dict):
        return run_ids, False, ["registry_not_object"], warnings

    ver = doc.get("version")
    if not isinstance(ver, int) or isinstance(ver, bool) or ver != REGISTRY_VERSION:
        problems.append(f"registry_version_unsupported:{ver!r}")

    protect_all = doc.get("protect_all", False)
    if not isinstance(protect_all, bool):
        problems.append("registry_protect_all_not_bool")
        protect_all = False

    if "protect" not in doc and "protect_all" not in doc:
        problems.append("registry_no_protect_key")

    items = doc.get("protect", [])
    if not isinstance(items, list):
        problems.append("registry_protect_not_list")
        items = []

    for i, it in enumerate(items):
        if not isinstance(it, dict):
            problems.append(f"registry_item_{i}_not_object")
            continue
        rid = it.get("run_id")
        if not isinstance(rid, str) or not _store.RUN_ID_RE.match(rid):
            problems.append(f"registry_item_{i}_bad_run_id:{rid!r}")
            continue
        src = it.get("source")
        label = f"{SOURCE_REGISTRY}:{src}" if isinstance(src, str) and src.strip() else SOURCE_REGISTRY
        run_ids.setdefault(rid, []).append(label)

    if not run_ids and not protect_all and not problems:
        warnings.append("registry_empty（白名单为空 ⇒ 未提供任何额外保护）")

    return run_ids, protect_all, problems, warnings


# ══════════════════════════════ 批次清单 ══════════════════════════════

def _read_jsonl_run_ids(path: Path) -> Tuple[List[str], int, List[str]]:
    """读一个 `runs.jsonl`，只取 `run_id`。返回 `(ids, lines_without_id, problems)`。

    非法 JSON 行 ⇒ problem（该批次「声明了成员却读不出」⇒ fail-closed）。
    合法行但没有 `run_id` 字段 ⇒ 只是「这行不引用 run」，计入 `lines_without_id`，
    **不算 problem**（实测 `evidence/FULL_SWEEP_200x100/runs.jsonl` 就是这种 sweep 表）。
    """
    ids: List[str] = []
    no_id = 0
    problems: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except ValueError:
                    problems.append(f"bad_json_line:{ln}")
                    continue
                if not isinstance(obj, dict):
                    problems.append(f"not_object_line:{ln}")
                    continue
                rid = obj.get("run_id")
                if rid is None:
                    no_id += 1
                    continue
                if not isinstance(rid, str) or not _store.RUN_ID_RE.match(rid):
                    problems.append(f"bad_run_id_line:{ln}")
                    continue
                ids.append(rid)
    except OSError as exc:
        problems.append(f"unreadable:{type(exc).__name__}")
    return ids, no_id, problems


def _declared_manifest(meta_path: Path) -> Optional[str]:
    """从 `batch_meta.json` 取 `source.runs_jsonl`（**声明路径**，不是猜的）。"""
    try:
        doc = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    src = doc.get("source")
    if not isinstance(src, dict):
        return None
    p = src.get("runs_jsonl")
    return p if isinstance(p, str) and p.strip() else None


def scan_batch_manifests(
    batch_root: Path,
    *,
    exclude_roots: Sequence[Path] = (),
    max_batches: int = DEFAULT_MAX_BATCHES,
    max_run_ids: int = DEFAULT_MAX_RUN_IDS,
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]], List[str], List[str]]:
    """只读 `batch_root` 的**直接子目录**里的批次清单。

    返回 `(run_ids, batches, problems, warnings)`。不递归、不 glob 全项目、不读其它目录。
    """
    run_ids: Dict[str, List[str]] = {}
    batches: List[Dict[str, Any]] = []
    problems: List[str] = []
    warnings: List[str] = []

    excluded = set()
    for r in exclude_roots:
        try:
            excluded.add(Path(r).resolve(strict=False))
        except OSError:                                  # pragma: no cover
            continue

    try:
        with os.scandir(batch_root) as it:
            entries = [e for e in it if e.is_dir(follow_symlinks=False)]
    except OSError as exc:
        problems.append(f"batch_root_unreadable:{type(exc).__name__}")
        return run_ids, batches, problems, warnings

    entries.sort(key=lambda e: e.name)
    if len(entries) > max_batches:
        problems.append(f"too_many_batches:{len(entries)}>{max_batches}")
        entries = entries[:max_batches]

    total = 0
    for e in entries:
        if e.name.startswith(OWN_ARTIFACT_PREFIXES):
            continue                                     # 清理器自己的日志/隔离区
        d = Path(e.path)
        try:
            real = d.resolve(strict=True)
        except (OSError, RuntimeError):                  # pragma: no cover
            continue
        if real in excluded:
            # 被清理的根自身不是「引用它的批次」；把它算进来会永久锁死清理。
            continue

        rec: Dict[str, Any] = {"batch": e.name, "manifest": None, "ids": 0, "status": "no_manifest"}
        local = d / BATCH_MANIFEST_NAME
        manifest: Optional[Path] = None
        declared_only = False

        if local.is_file():
            manifest = local
        else:
            decl = _declared_manifest(d / BATCH_META_NAME)
            if decl:
                declared_only = True
                p = Path(decl)
                if not p.is_absolute():
                    p = (d / p)
                if p.is_file():
                    manifest = p
                else:
                    # 声明了成员清单却读不到 ⇒ 无法排除它引用了谁 ⇒ fail-closed
                    problems.append(f"declared_manifest_missing:{e.name}")
                    rec["status"] = "declared_missing"
                    rec["declared"] = str(p)
                    batches.append(rec)
                    continue

        if manifest is None:
            rec["status"] = "no_manifest"
            warnings.append(f"batch_no_manifest:{e.name}（未声明成员清单 ⇒ 不提供引用）")
            batches.append(rec)
            continue

        ids, no_id, probs = _read_jsonl_run_ids(manifest)
        rec["manifest"] = str(manifest)
        rec["declared_only"] = declared_only
        rec["lines_without_run_id"] = no_id
        if probs:
            rec["status"] = "unreadable"
            rec["problems"] = probs[:10]
            for p_ in probs[:10]:
                problems.append(f"batch_manifest_{p_}:{e.name}")
            batches.append(rec)
            continue

        label = f"{SOURCE_BATCH}:{e.name}"
        for rid in ids:
            run_ids.setdefault(rid, []).append(label)
        rec["ids"] = len(ids)
        rec["status"] = "ok"
        total += len(ids)
        batches.append(rec)
        if total > max_run_ids:
            problems.append(f"too_many_run_ids:{total}>{max_run_ids}")
            break

    return run_ids, batches, problems, warnings


# ══════════════════════════════ 汇总 ══════════════════════════════

def collect_references(
    policy: Optional[ReferencePolicy],
    *,
    exclude_roots: Sequence[Path] = (),
) -> ReferenceSet:
    """按策略收集引用。**只读**；不创建目录、不写任何文件。"""
    if policy is None or not policy.enabled:
        rs = _empty(STATUS_DISABLED, policy=policy)
        rs.warnings.append("reference_protection_disabled（本轮未做引用保护）")
        return rs

    run_ids: Dict[str, List[str]] = {}
    counts: Dict[str, int] = {SOURCE_REGISTRY: 0, SOURCE_BATCH: 0, SOURCE_PINNED: 0}
    problems: List[str] = []
    warnings: List[str] = []
    batches: List[Dict[str, Any]] = []
    protect_all = False

    # ── 来源 1：白名单 ──
    if policy.registry_path is not None:
        reg_ids, protect_all, reg_probs, reg_warn = load_registry(policy.registry_path)
        warnings.extend(reg_warn)
        if reg_probs == ["registry_missing"]:
            if policy.require_registry:
                problems.append("registry_missing_but_required")
            else:
                warnings.append("registry_missing（严格模式未开 ⇒ 仅无额外保护）")
        else:
            problems.extend(reg_probs)
        for rid, srcs in reg_ids.items():
            run_ids.setdefault(rid, []).extend(srcs)
            counts[SOURCE_REGISTRY] += 1

    # ── 来源 2：批次清单 ──
    if policy.scan_batch_manifests and policy.batch_root is not None:
        br = Path(policy.batch_root)
        if not br.is_dir():
            if policy.batch_root_explicit:
                problems.append("batch_root_missing_but_configured")
            else:
                warnings.append("batch_root_missing（推导路径不存在 ⇒ 未做批次引用）")
        else:
            b_ids, b_recs, b_probs, b_warn = scan_batch_manifests(
                br, exclude_roots=exclude_roots,
                max_batches=policy.max_batches, max_run_ids=policy.max_run_ids)
            batches.extend(b_recs)
            warnings.extend(b_warn)
            problems.extend(b_probs)
            for rid, srcs in b_ids.items():
                run_ids.setdefault(rid, []).extend(srcs)
                counts[SOURCE_BATCH] += 1

    fail_closed = bool(problems)
    status = STATUS_FAIL_CLOSED if fail_closed else STATUS_OK

    return ReferenceSet(
        status=status,
        fail_closed=fail_closed,
        protect_all=protect_all,
        run_ids=run_ids,
        counts=counts,
        problems=problems,
        warnings=warnings,
        batches=batches,
        policy=policy.as_dict(),
    )


__all__ = [
    "REGISTRY_VERSION",
    "SOURCE_REGISTRY", "SOURCE_BATCH", "SOURCE_PINNED",
    "STATUS_OK", "STATUS_DISABLED", "STATUS_FAIL_CLOSED",
    "DEFAULT_MAX_BATCHES", "DEFAULT_MAX_RUN_IDS", "OWN_ARTIFACT_PREFIXES",
    "ReferencePolicy", "ReferenceSet",
    "policy_from_env", "load_registry", "scan_batch_manifests", "collect_references",
]
