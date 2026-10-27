"""40 · 持仓量关闭时间：三类证据分级

工单整改要求 1：修正关闭时间的推断。**「文件当时存在」不能证明当时已关闭持仓量。**
按证据分别列出：① 首次明确关闭记录 ② 首次代码快照 ③ 首次运行证据。
确切时间、实施者、批准依据无法证明的写未知。

本脚本把三类证据各自**独立取证**，并逐条标注「这条能证明什么 / 不能证明什么」：

  out/40_close_evidence.json          三类证据汇总 + 未知项 + 版本库核对
  out/41_close_evidence_timeline.csv  逐条证据（时刻/类别/载体/内容/证明力）
  out/42_code_snapshots.csv           盘上全部 unified_runtime 副本及其开关默认值

只读：读文件、算 sha256、跑 `git ls-files`；不改任何代码。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, dump, now_utc, utc, write_csv

T0 = time.time()
STATS_AT = now_utc()


def log(m: str) -> None:
    print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)


EVID = ROOT / "evidence"
MEM = ROOT / ".workbuddy-ai/memory"
PROD = ROOT / "框架/backend/unified_runtime.py"

# 只看人写的/机器产出的文本载体，跳过二进制与大文件
EXT = {".md", ".txt", ".json", ".csv", ".log", ".yaml", ".yml"}
MAXB = 3_000_000
# **必须排除本目录**：本目录的脚本/产物里就写着「关闭 OI」，若不排除，
# 扫描会把自己扫成证据，条数虚增、最早时点也可能被污染。
SELF = str((EVID / "DATA-AUDIT-01-R1").resolve())

# 「明确关闭」判定：同一行里既出现关闭语义，又出现持仓量语义
CLOSE_RE = re.compile(
    r"((关闭|关掉|停用|禁用|不启用|未启用|未使用|不参与|未参与)[^。\n]{0,20}(OI|持仓量|开仓量|open.?interest)"
    r"|(OI|持仓量)[^。\n]{0,20}(关闭|关掉|停用|禁用|不启用|未启用|未使用|不参与|未参与)"
    r"|B\s*变体[^。\n]{0,12}(关闭|关)|B\s*组[^。\n]{0,12}(关闭|关))"
)
# 「运行证据」判定：运行产物里出现 OI 未参与的结构化字段
RUN_RE = re.compile(r'"oi_used"\s*:\s*false|oi_empty|OI_UNAVAILABLE\s*[:=]\s*0')

# 机器初筛靠上下文关键词，容易把「B 组」记录误判成回测口径 ⇒ 下面这几条人工复核后钉住口径，
# 理由写在 reason 里，可逐条反驳（不许无声改判）。
OVERRIDES = {
    ("evidence/SYSTEM-RUNTIME-01-FIX/README-POOL-526.md", 16):
        ("全池扫描/B组引擎", "原文「突破瞬间的 OI 闸门在『关闭 OI 过滤』（B 组）时仍然拦单」，"
                            "指向全池扫描用的引擎，不是 daemon，也不是回测 run"),
    (".workbuddy-ai/memory/2026-10-23.md", 318):
        ("生产运行时", "原文「运行时是 B 组（enable_oi_filter=False）且 OI 传空数组」，明确指 daemon"),
    ("evidence/CODEX-CLOSEOUT-01/runs/bt_20261023T161934Z_c91ebcfe/run.json", 73):
        ("回测 run note", "run.json 顶层 note，自带 created_at，是回测 run 的自我描述"),
    ("evidence/CODEX-CLOSEOUT-01/out/api_run.json", 73):
        ("回测 run note", "api_run.json 里 spec.note 是「标准回测」，这句在 run.json 顶层"),
}

# ---------------------------------------------------------------- ① 文本记录
records = []
scanned = 0
for base in (EVID, MEM):
    if not base.exists():
        continue
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in ("node_modules", ".git", "__pycache__")]
        if os.path.abspath(dp).startswith(SELF):
            dns[:] = []
            continue
        for fn in fns:
            if os.path.splitext(fn)[1].lower() not in EXT:
                continue
            f = Path(dp) / fn
            try:
                if f.stat().st_size > MAXB:
                    continue
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:                                  # noqa: BLE001
                continue
            scanned += 1
            if not CLOSE_RE.search(txt):
                continue
            for ln, line in enumerate(txt.splitlines(), 1):
                m = CLOSE_RE.search(line)
                if not m:
                    continue
                # 判断这条记录说的是「生产运行时」还是「回测变体」
                ctx = line + " " + " ".join(txt.splitlines()[max(0, ln - 3):ln + 3])
                target = "生产运行时" if ("unified_runtime" in ctx or "实时链路" in ctx
                                          or "模拟盘" in ctx or "运行时" in ctx) else "回测/研究变体"
                ov = OVERRIDES.get((str(f.relative_to(ROOT)), ln))
                if ov:
                    target, why = ov
                else:
                    why = "机器初筛（上下文关键词）"
                records.append({
                    "kind": "文本记录",
                    "file": str(f.relative_to(ROOT)),
                    "file_mtime_utc": utc(int(f.stat().st_mtime * 1000)),
                    "line_no": ln,
                    "text": line.strip()[:400],
                    "target": target,
                    "classified_by": why,
                })
            if len(records) > 4000:
                break
log(f"扫描文本载体 {scanned} 个，命中「明确关闭」记录 {len(records)} 条")

# 按口径分桶：不同载体说的是不同的事，混在一起排时间就会得出错误的「最早」
buckets = {}
for r in records:
    buckets.setdefault(r["target"], []).append(r)
for k in buckets:
    buckets[k].sort(key=lambda r: r["file_mtime_utc"] or "")
prod_recs = buckets.get("生产运行时", [])
scan_recs = buckets.get("全池扫描/B组引擎", [])
run_note_recs = buckets.get("回测 run note", [])
earliest_bucket = min(
    (b[0] for b in (prod_recs, scan_recs, run_note_recs) if b),
    key=lambda r: r["file_mtime_utc"] or "", default=None)

# ---------------------------------------------------------------- ② 代码快照
snaps = []
for f in list(EVID.rglob("unified_runtime*.py")) + [PROD]:
    if not f.exists():
        continue
    try:
        raw = f.read_bytes()
    except OSError:
        continue
    txt = raw.decode("utf-8", errors="ignore")
    defaults = []
    for i, line in enumerate(txt.splitlines(), 1):
        if "enable_oi_filter" in line and ("=" in line):
            defaults.append({"line": i, "text": line.strip()[:160]})
    snaps.append({
        "file": str(f.relative_to(ROOT)),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mtime_utc": utc(int(f.stat().st_mtime * 1000)),
        "enable_oi_filter_lines": defaults,
        "has_false_default": any("= False" in d["text"] or "=False" in d["text"]
                                 for d in defaults),
        "is_production_path": f == PROD,
    })
snaps.sort(key=lambda s: s["mtime_utc"] or "")
write_csv("42_code_snapshots.csv",
          ["file", "bytes", "sha256", "mtime_utc", "has_false_default",
           "is_production_path", "enable_oi_filter_lines"],
          [[s["file"], s["bytes"], s["sha256"], s["mtime_utc"],
            1 if s["has_false_default"] else 0, 1 if s["is_production_path"] else 0,
            " | ".join(f"L{d['line']}:{d['text']}" for d in s["enable_oi_filter_lines"])]
           for s in snaps])
log(f"代码副本 {len(snaps)} 个（含生产文件）")

# ---------------------------------------------------------------- ③ 运行证据
runs = []
for dp, dns, fns in os.walk(EVID):
    dns[:] = [d for d in dns if d not in ("node_modules", ".git", "__pycache__")]
    if os.path.abspath(dp).startswith(SELF):
        dns[:] = []
        continue
    for fn in fns:
        if os.path.splitext(fn)[1].lower() not in {".json", ".csv", ".log", ".txt"}:
            continue
        f = Path(dp) / fn
        try:
            if f.stat().st_size > MAXB:
                continue
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:                                      # noqa: BLE001
            continue
        m = RUN_RE.search(txt)
        if not m:
            continue
        hit = next((l.strip() for l in txt.splitlines() if RUN_RE.search(l)), "")
        runs.append({
            "kind": "运行产物",
            "file": str(f.relative_to(ROOT)),
            "file_mtime_utc": utc(int(f.stat().st_mtime * 1000)),
            "match": m.group(0),
            "text": hit[:400],
        })
runs.sort(key=lambda r: r["file_mtime_utc"] or "")
log(f"运行证据 {len(runs)} 条")

# ---------------------------------------------------------------- 版本库核对
def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception as exc:                                   # noqa: BLE001
        return f"<git 不可用：{type(exc).__name__}>"


git_files = git("ls-files", "框架/backend/unified_runtime.py")
git_head = git("log", "-1", "--format=%H|%cI|%s")
git_err = git("ls-files", "--error-unmatch", "框架/backend/unified_runtime.py")

vcs = {
    "tracked": bool(git_files.strip()),
    "ls_files_output": git_files,
    "ls_files_error_unmatch": git_err[:300],
    "last_commit": git_head,
    "implication": ("文件未纳入版本库 ⇒ 没有提交历史、没有 diff、没有作者、没有首次写入时间"
                    " ⇒ 「何时第一次写成 False」「谁改的」都无版本库证据"),
}

# ---------------------------------------------------------------- 汇总
timeline = []
for r in records:
    timeline.append(["文本记录", r["file_mtime_utc"], r["file"], f"L{r['line_no']}",
                     r["target"], r["text"]])
for s in snaps:
    timeline.append(["代码快照", s["mtime_utc"], s["file"], s["sha256"][:12],
                     "生产文件" if s["is_production_path"] else "证据副本",
                     f"enable_oi_filter 默认 False：{s['has_false_default']}"])
for r in runs:
    timeline.append(["运行产物", r["file_mtime_utc"], r["file"], r["match"],
                     "运行时/扫描", r["text"]])
timeline.sort(key=lambda x: x[1] or "")
write_csv("41_close_evidence_timeline.csv",
          ["证据类别", "时刻(UTC，文件mtime)", "载体", "位置/摘要", "指向", "内容"], timeline)

out = {
    "stats_at_utc": STATS_AT,
    "question": "持仓量门槛是何时、被谁、依据什么关闭的",
    "discipline": ("文件存在 ≠ 当时状态。mtime 只能证明「该载体此刻在盘上」，"
                   "不能证明「当时已关闭」；代码副本的 mtime 只能证明「复制那一刻内容如此」。"),
    "class_1_explicit_record": {
        "definition": "文本记录里**明确写出**持仓量/OI 被关闭或不参与决策的那一条",
        "population": f"全量扫描 evidence/ + .workbuddy-ai/memory/ 下 {scanned} 个文本载体",
        "hits_total": len(records),
        "buckets": {k: {"n": len(v), "earliest": v[0]} for k, v in sorted(buckets.items())},
        "earliest_production_runtime": prod_recs[0] if prod_recs else None,
        "earliest_pool_scan": scan_recs[0] if scan_recs else None,
        "earliest_run_note": run_note_recs[0] if run_note_recs else None,
        "earliest_any_closure_statement": earliest_bucket,
        "proves": "在某一时刻，有人/某产物明确写下了「关闭」这件事",
        "does_not_prove": "写下这句话的时刻 ≠ 关闭发生的时刻；也不证明实施者与批准",
        "mtime_caveat": "记忆日志是当日追加写，mtime = 当日最后一次追加 ⇒ 该条记录本身时间 ≤ mtime",
    },
    "class_2_code_snapshot": {
        "definition": "盘上现存最早的、内容为 `enable_oi_filter` 默认 False 的 unified_runtime 副本",
        "population": f"全量枚举 evidence/ 下所有 unified_runtime*.py + 生产文件，共 {len(snaps)} 个",
        "snapshots": snaps,
        "earliest_with_false": next((s for s in snaps if s["has_false_default"]), None),
        "proves": "复制那一刻，代码内容确实是默认 False",
        "does_not_prove": "该副本第一次被写成 False 的时刻（副本是后来拷的）",
    },
    "class_3_runtime_evidence": {
        "definition": "运行/扫描产物里能证明「OI 没参与决策」的结构化字段（oi_used=false / oi_empty）",
        "population": f"全量扫描 evidence/ 下 json/csv/log/txt，命中 {len(runs)} 条",
        "earliest": runs[0] if runs else None,
        "proves": "在那个运行时点，OI 确实没有参与决策（产物本身是运行时输出）",
        "does_not_prove": "关闭是谁在何时做的；也不证明在此之前从未参与",
    },
    "unknown": {
        "exact_close_time": "未知（无版本库、无变更单、无带时刻的变更记录）",
        "implementer": "未知（文件未入库，无提交作者；代码注释无署名）",
        "approval": "未找到批准依据（检索 evidence/ 与记忆日志，只有研究口径定义，无生产授权）",
        "why_unknown": "证据载体不存在，而不是没查",
    },
    "vcs": vcs,
    "retracted_inference": {
        "old_claim": "「2026-10-23 02:48 工作日志记录 unified_runtime.py 已存在」⇒ 最晚在 2026-10-23 02:48 UTC 生产运行时已是关闭状态",
        "why_invalid": ("该推理的前提是「文件存在 ⇒ 文件内容为 False」。文件在某个时刻被创建，"
                        "但内容可以是后来改的；没有版本库就没有内容时间线。"
                        "正确表述只能是：现存最早代码副本（见 class_2）证明「不晚于该副本时刻内容已是 False」。"),
        "status": "作废，不得再引用",
    },
}
dump("40_close_evidence.json", out)

log("三类证据已分开取证")
if prod_recs:
    log(f"① 最早生产运行时关闭记录：{prod_recs[0]['file_mtime_utc']} {prod_recs[0]['file']}")
e2 = out["class_2_code_snapshot"]["earliest_with_false"]
if e2:
    log(f"② 最早代码副本：{e2['mtime_utc']} {e2['file']}")
if runs:
    log(f"③ 最早运行证据：{runs[0]['file_mtime_utc']} {runs[0]['file']}")
print(f"\n完成，用时 {time.time()-T0:.1f}s；统计时点 {STATS_AT}")
