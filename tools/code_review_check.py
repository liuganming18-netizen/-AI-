# -*- coding: utf-8 -*-
"""WX-CODE-REVIEW v1.0 · 自动门禁

把 `docs/代码审查标准与流程.md` 里能机械判定的条款落成检查，避免"标准只写在纸上"。

用法
────
    python tools/code_review_check.py                 # 全量（静态 + 运行留底审计）
    python tools/code_review_check.py --static-only   # 只做静态扫描（快）
    python tools/code_review_check.py --out evidence/CODE-REVIEW-01/out

退出码
──────
    0 = 无 P0；1 = 有 P0（不许合并）；2 = 脚本自身出错

纪律
────
· **只读**：不修改任何被扫描文件，只在 `--out` 下写报告。
· 每条发现都带 `文件:行` 与原文，便于直接定位；不做模糊归因。
· 无法机械判定的条款（口径同源、结论夸大、成本完整性）**不假装检查**，在报告里单列"需人工"。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── 扫描范围：只扫"会进生产/会出结论"的代码，不扫第三方与归档 ──────────────
SCAN_DIRS = [
    "策略",
    "框架/backend",
    "trading_core",
    "工具",
]
SCAN_ROOT_GLOBS = ["run_*.py"]
EXCLUDE_PARTS = {
    "__pycache__", "node_modules", "_legacy_archive", "_legacy_archive_v31",
    ".git", "dist", "build", ".venv", "venv", "_archive",
}

SEV_P0, SEV_P1, SEV_P2 = "P0", "P1", "P2"


class Finding(dict):
    def __init__(self, rule: str, sev: str, path: str, line: int, text: str, why: str):
        super().__init__(rule=rule, severity=sev, path=path, line=line,
                         text=text.strip()[:220], why=why)


# ══════════════════════════ 静态规则 ══════════════════════════

def _iter_py(root: Path):
    for d in SCAN_DIRS:
        base = root / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(part in EXCLUDE_PARTS for part in p.parts):
                continue
            yield p
    for g in SCAN_ROOT_GLOBS:
        for p in root.glob(g):
            yield p


def _docstring_mask(lines: list[str]) -> list[bool]:
    """标记哪些行处在三引号字符串（docstring）里 —— 文档里提到 `unlink()` 不算违规。"""
    mask = [False] * len(lines)
    in_doc = False
    for i, raw in enumerate(lines):
        cnt = raw.count('"""') + raw.count("'''")
        if in_doc:
            mask[i] = True
        if cnt % 2 == 1:
            in_doc = not in_doc
            mask[i] = True
    return mask


def _is_test_path(rel: str) -> bool:
    return "/tests/" in rel or Path(rel).name.startswith("test_")


def _del_severity(s: str, ctx: str) -> tuple[str, str]:
    """删除语句定级：只有"删数据"才是 P0；删临时文件/测试目录最多 P2。"""
    low = s.lower()
    data_hint = ("self.path" in s or "ledger" in low or "账本" in s
                 or "evidence" in low or "runs" in low or "回测日志" in s)
    temp_hint = any(k in low for k in ("tmp", "temp", "partial", "pid", "backup", ".tmp"))
    if data_hint and "exists()" in ctx + s:
        return SEV_P0, "删除可能是既有数据的路径（账本/产物）⇒ 必须白名单 + 显式确认"
    if temp_hint:
        return SEV_P2, "删除临时文件（原子写/清理）⇒ 确认路径确为临时即可"
    return SEV_P1, "删除文件/目录 ⇒ 需确认不碰账本、evidence、回测日志"


def scan_static(root: Path) -> list[Finding]:
    out: list[Finding] = []
    func_defs: dict[str, list[str]] = defaultdict(list)

    for p in _iter_py(root):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = str(p.relative_to(root))
        doc = _docstring_mask(lines)
        is_test = _is_test_path(rel)
        cur_fn = ""

        for i, raw in enumerate(lines, 1):
            s = raw.strip()
            if not s or s.startswith("#") or doc[i - 1]:
                continue
            mfn = re.match(r'def\s+([A-Za-z_]\w*)\s*\(', s)
            if mfn:
                cur_fn = mfn.group(1)

            # ── R1 PIT-01：用 timestamp 当"最后一根"判据 ──────────────────
            if re.search(r'\[\s*["\']timestamp["\']\s*\]\s*<\s*\w*(entry|decision|signal)', s) \
               or re.search(r'\btimestamp\b\s*<\s*\w*(entry|decision|signal)_ms', s):
                out.append(Finding("PIT-01", SEV_P0, rel, i, s,
                                   "用 timestamp/open_time 判『最后一根已闭合 bar』⇒ 会读到未收盘 bar"))

            # ── R4 DEL-01：删除既有产物 ───────────────────────────────────
            if ".unlink(" in s or "rmtree(" in s:
                ctx = "\n".join(lines[max(0, i - 4):i])
                sev, why = _del_severity(s, ctx)
                if is_test:
                    sev, why = SEV_P2, "测试代码清理自身临时目录 ⇒ 确认不碰生产数据即可"
                out.append(Finding("DEL-01", sev, rel, i, s, why))

            # ── R2 FAB-01：读时时间兜底 ───────────────────────────────────
            if re.search(r'\bor\s+(utc_now_iso|now_iso|datetime\.now|time\.time)\s*\(', s):
                out.append(Finding("FAB-01", SEV_P0, rel, i, s,
                                   "读接口用『当前时间』兜底 ⇒ 所有缺失时间的记录会变成同一个假时间"))

            # ── R2/R10 FAB-03：状态写死 ──────────────────────────────────
            if re.search(r'["\']status["\']\s*:\s*(S\.)?STATUS_SUCCEEDED', s):
                out.append(Finding("FAB-03", SEV_P0, rel, i, s,
                                   "状态写死 succeeded ⇒ 失败/中断的运行会被显示成成功"))

            # ── R7 ERR-01：吞异常 ────────────────────────────────────────
            if re.search(r'except[^:]*:\s*pass\s*$', s) or re.search(r'except\s*:\s*pass\s*$', s):
                sev = SEV_P2 if is_test else SEV_P1
                out.append(Finding("ERR-01", sev, rel, i, s,
                                   "静默吞异常 ⇒ 该行数据被丢掉且无人知晓"))

            # ── R7 NULL-01：对外展示载荷里缺失值被 `or 0.0` 顶成 0 ──────────
            # 只查"生成图表/接口载荷"的函数：内部缓存 TTL、费率兜底不算违规。
            if (p.name == "backtest_chart.py"
                    or (p.name == "backtest_api.py" and cur_fn.startswith("_generate_fallback"))):
                if re.search(r'float\([^)]*\.get\([^)]*\)\s*or\s*0(\.0)?\)', s):
                    out.append(Finding("NULL-01", SEV_P1, rel, i, s,
                                       "展示载荷缺失值用 0 顶替 ⇒ 界面把『没记录』显示成『等于 0』"))

            # ── 记录函数定义，供 DUP-01 人工判重 ─────────────────────────
            m = re.match(r'def\s+([A-Za-z_]\w*)\s*\(', s)
            if m and not m.group(1).startswith("_"):
                func_defs[m.group(1)].append(f"{rel}:{i}")

        # ── R3 BYPASS-01：根目录批量脚本手拼产物 ─────────────────────────
        if p.parent == root and p.name.startswith("run_"):
            txt = "\n".join(lines)
            if re.search(r'["\']chart\.json["\']', txt) or re.search(r'\bchart\s*=\s*\{', txt):
                ln = next((k for k, v in enumerate(lines, 1) if "chart.json" in v or "chart = {" in v), 0)
                out.append(Finding("BYPASS-01", SEV_P0, rel, ln, lines[ln - 1] if ln else "",
                                   "根目录脚本自己拼 chart.json ⇒ 绕过 backtest_store，signals/trade_id 会缺失"))

        # ── R2 FAB-02：合成函数里的硬编码参数 ───────────────────────────
        if p.name == "backtest_store.py":
            in_fn = False
            for i, raw in enumerate(lines, 1):
                if raw.lstrip().startswith("def _summary_to_run_doc"):
                    in_fn = True
                    continue
                if in_fn and raw.strip().startswith("def "):
                    in_fn = False
                if in_fn and re.search(r'["\'](risk_budget_per_trade|fee_rate|slippage_bps|taker_fee_rate)["\']\s*:\s*[0-9]', raw):
                    out.append(Finding("FAB-02", SEV_P0, rel, i, raw.strip(),
                                       "合成 run 文档里硬编码参数 ⇒ 界面显示的不是实际跑的配置"))

    # ── DUP-01：同名函数多文件定义（P2，供人工判是否违反 R5）──────────
    for fn, where in func_defs.items():
        files = {w.split(":")[0] for w in where}
        if len(files) > 1 and fn in {
            "extract_entry_features", "compute_efficiency_ratio", "read_oi",
            "compute_stop", "round_to_step", "compute_signals", "aggregate_metrics",
        }:
            out.append(Finding("DUP-01", SEV_P2, ",".join(sorted(files)), 0, fn,
                               "同名函数在多个文件定义 ⇒ 需人工确认是否违反『单一实现』"))

    return out


# ══════════════════════════ 运行留底审计 ══════════════════════════

def _count_lines(p: Path) -> int:
    try:
        with p.open("rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return -1


def audit_runs(root: Path, max_runs: int = 5000) -> tuple[list[Finding], dict]:
    runs_root = root / "回测日志"
    if not runs_root.exists():
        return [], {"runs_scanned": 0, "note": "未找到 回测日志/"}
    # 归档/隔离目录不算"当前运行"，单独计数，避免把历史包袱当成今天的 P0
    ARCHIVE_PARTS = {"legacy_archive", "batch_tests_archive"}
    dirs: list[Path] = []
    archived = 0
    for p in runs_root.rglob("*"):
        if not p.is_dir() or any(part in EXCLUDE_PARTS for part in p.parts):
            continue
        if any(part in ARCHIVE_PARTS or part.startswith("_隔离_") for part in p.parts):
            if (p / "summary.json").exists() or (p / "trades.jsonl").exists():
                archived += 1
            continue
        if (p / "summary.json").exists() or (p / "trades.jsonl").exists() or (p / "run.json").exists():
            dirs.append(p)
        if len(dirs) >= max_runs:
            break

    out: list[Finding] = []
    no_runjson, reg02, reg03, reg04 = [], [], [], []
    created: dict[str, list[str]] = defaultdict(list)
    n_trades_total = 0
    zero_trade = 0

    for d in dirs:
        rel = str(d.relative_to(root))
        rj = d / "run.json"
        trades = d / "trades.jsonl"
        n_tr = _count_lines(trades) if trades.exists() else -1

        # 逐笔计数与 run.json 无关：没有 run.json 的运行同样有 trades.jsonl
        if n_tr >= 0:
            n_trades_total += n_tr
            if n_tr == 0:
                zero_trade += 1

        if not rj.exists():
            no_runjson.append(rel)
            continue  # 无 run.json 的目录不再做指标交叉（指标来源本身不可信）

        try:
            doc = json.loads(rj.read_text(encoding="utf-8"))
        except Exception:
            out.append(Finding("REG-01", SEV_P0, rel, 0, "run.json 无法解析", "登记文件损坏"))
            continue

        m = doc.get("metrics") or {}
        cnt = m.get("trades_count")
        net = m.get("net_pnl")
        if isinstance(cnt, int) and cnt > 0 and n_tr == 0:
            reg02.append(rel)
        if n_tr == 0 and isinstance(net, (int, float)) and abs(float(net)) > 1e-9:
            reg03.append(f"{rel} (net={net})")
        if not (d / "manifest.json").exists():
            reg04.append(rel)
        ca = str(doc.get("created_at") or "")
        if ca:
            created[ca].append(rel)

    if no_runjson:
        out.append(Finding("REG-01", SEV_P0, f"{len(no_runjson)} 个目录", 0,
                           f"示例：{no_runjson[:3]}",
                           "有回测产物但缺 run.json ⇒ 该运行不算已记录；读接口会用默认值合成文档"))
    if reg02:
        out.append(Finding("REG-02", SEV_P0, f"{len(reg02)} 个 run", 0, f"示例：{reg02[:3]}",
                           "metrics.trades_count>0 但 trades.jsonl 为空 ⇒ 逐笔丢了"))
    if reg03:
        out.append(Finding("REG-03", SEV_P0, f"{len(reg03)} 个 run", 0, f"示例：{reg03[:3]}",
                           "trades.jsonl 为空但 net_pnl≠0 ⇒ 指标与逐笔不一致"))
    if reg04:
        out.append(Finding("REG-04", SEV_P1, f"{len(reg04)} 个 run", 0, f"示例：{reg04[:3]}",
                           "缺 manifest.json ⇒ 产物无法逐个校验 sha256"))
    dup = {k: v for k, v in created.items() if len(v) > 1}
    if dup:
        k = max(dup, key=lambda x: len(dup[x]))
        out.append(Finding("REG-05", SEV_P1, f"{len(dup)} 个时间值", 0,
                           f"{k} → {len(dup[k])} 个 run",
                           "多个 run 的 created_at 完全相同 ⇒ 疑似读时兜底，不是真实运行时间"))

    stats = {
        "runs_scanned": len(dirs),
        "archived_dirs_excluded": archived,
        "with_run_json": len(dirs) - len(no_runjson),
        "without_run_json": len(no_runjson),
        "trades_total": n_trades_total,
        "zero_trade_runs": zero_trade,
        "duplicate_created_at_values": len(dup),
    }
    return out, stats


# ══════════════════════════ 报告 ══════════════════════════

MANUAL = [
    "R6 口径是否同源（分子分母是否同一批样本）",
    "R8 结论是否超出 n 与置信区间能支撑的范围",
    "R11 成本是否含手续费/滑点/资金费/价格取整四项",
    "R12 是否把『程序自检通过』当成『结果正确』",
    "R5 同一概念是否有多套实现（门禁只报同名函数，语义重复需人工）",
]


def write_report(out_dir: Path, findings: list[Finding], stats: dict, root: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_sev = defaultdict(list)
    for f in findings:
        by_sev[f["severity"]].append(f)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "standard": "WX-CODE-REVIEW v1.0",
        "counts": {s: len(by_sev.get(s, [])) for s in (SEV_P0, SEV_P1, SEV_P2)},
        "run_stats": stats,
        "findings": findings,
        "manual_required": MANUAL,
    }
    (out_dir / "check_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 代码审查自动门禁报告", "",
          f"- 生成时间：{payload['generated_at']}",
          f"- 标准：WX-CODE-REVIEW v1.0",
          f"- 结论：**P0 {payload['counts']['P0']} 项 / P1 {payload['counts']['P1']} 项 / P2 {payload['counts']['P2']} 项**",
          "", "## 运行留底统计", ""]
    if stats:
        md += ["| 项 | 值 |", "|---|---:|"]
        md += [f"| {k} | {v} |" for k, v in stats.items()]
        md.append("")

    for sev, title in ((SEV_P0, "P0 · 阻断（必须修完才能合并）"),
                       (SEV_P1, "P1 · 必修"), (SEV_P2, "P2 · 建议")):
        items = by_sev.get(sev, [])
        md += [f"## {title}", ""]
        if not items:
            md += ["（无）", ""]
            continue
        md += ["| 规则 | 位置 | 原文 | 原因 |", "|---|---|---|---|"]
        for f in items:
            loc = f"{f['path']}:{f['line']}" if f["line"] else f["path"]
            txt = f["text"].replace("|", "\\|")[:120]
            md.append(f"| {f['rule']} | `{loc}` | `{txt}` | {f['why']} |")
        md.append("")

    md += ["## 需人工判定（门禁不假装检查）", ""]
    md += [f"- {x}" for x in MANUAL]
    md.append("")

    p = out_dir / "check_report.md"
    p.write_text("\n".join(md), encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="evidence/CODE-REVIEW-01/out")
    ap.add_argument("--static-only", action="store_true")
    ap.add_argument("--max-runs", type=int, default=5000)
    a = ap.parse_args()

    root = Path(a.root).resolve()
    out_dir = (root / a.out) if not os.path.isabs(a.out) else Path(a.out)

    try:
        findings = scan_static(root)
        stats: dict = {}
        if not a.static_only:
            run_findings, stats = audit_runs(root, a.max_runs)
            findings += run_findings
    except Exception as exc:                              # noqa: BLE001
        print(f"[code_review_check] 自身出错：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    p = write_report(out_dir, findings, stats, root)
    p0 = sum(1 for f in findings if f["severity"] == SEV_P0)
    p1 = sum(1 for f in findings if f["severity"] == SEV_P1)
    print(f"[code_review_check] P0={p0} P1={p1} 报告：{p}")
    return 1 if p0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
