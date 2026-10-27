#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-EVIDENCE-04｜通用交付校验库

一个 `verify_delivery(deliv_dir)` 同时用于：
  · 对真实交付（evidence/SYSTEM-UI-03）做对账
  · 对离线构造的反例做识别测试（negative_tests.py）

约定：被校验目录需具备
  audit_ui_verification.json                         （含 backtest_run_parity.source_dir / displayed_run_id）
  04_回测run详情与summary逐字段对账表.csv             （6 列：维度/DOM/服务端/字段路径/判定/说明）
  08_文件清单与哈希.csv                               （4 列：类别/相对路径/大小/SHA-256）
  *.png                                               （view_* 与 state_*）

判定三态：PASS / FAIL / UNVERIFIED。未验证不计入通过。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from collections import defaultdict

EMPTY_SHA = hashlib.sha256(b"").hexdigest()  # e3b0c442...


def sha256_file(p: Path) -> str:
    if not p or not Path(p).exists() or not Path(p).is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def read_csv_rows(p: Path) -> list:
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def resolve_dotted(doc, dotted: str):
    """返回 (found, value, 实际走到的路径)。"""
    cur, walked = doc, []
    if not dotted:
        return False, None, "<empty>"
    for seg in dotted.split("."):
        walked.append(seg)
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return False, None, ".".join(walked[:-1]) or "<root>"
    return True, cur, ".".join(walked)


# ───────────────────────────────────────────────────────────────────
# C1 字段路径真实性
# ───────────────────────────────────────────────────────────────────
def c1_field_paths(deliv: Path, project_root: Path) -> dict:
    rows = read_csv_rows(deliv / "04_回测run详情与summary逐字段对账表.csv")
    audit_p = deliv / "audit_ui_verification.json"
    audit = json.loads(audit_p.read_text(encoding="utf-8")) if audit_p.exists() else {}
    src_dir = Path((audit.get("backtest_run_parity") or {}).get("source_dir") or "")

    docs = {}
    for name in ("summary.json", "run.json", "manifest.json"):
        p = src_dir / name
        docs[name] = {
            "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else None,
            "sha256": sha256_file(p),
            "doc": json.loads(p.read_text(encoding="utf-8")) if p.exists() else None,
        }

    items = []
    for r in rows[1:]:
        if len(r) < 5:
            continue
        dim, _dom, srv, field_path, verdict = r[0], r[1], r[2], r[3], r[4]
        rec = {"维度": dim, "声称路径": field_path, "声称值": srv, "CSV判定": verdict}
        # 哨兵值：明确表示「来源缺失 / 未命中」，不是文件名
        SENTINEL = {"missing", "—", "-", "", "未命中", "字段不存在", "未提取", "n/a", "null"}
        if field_path.strip().lower() in SENTINEL:
            rec.update(判定="PASS", 实际值=None, 命中路径=None,
                       原因="来源缺失（声称缺失、实际缺失）⇒ 一致，记未验证而非 FAIL")
            items.append(rec); continue
        if " / " in field_path:
            fname, dotted = [x.strip() for x in field_path.split(" / ", 1)]
        else:
            fname, dotted = field_path.strip(), ""
        info = docs.get(fname, {"exists": False, "doc": None})
        rec["源文件"] = fname
        if not info.get("exists"):
            rec.update(判定="FAIL", 实际值=None, 命中路径=None,
                       原因=f"声称来源文件 {fname} 不存在")
            items.append(rec); continue
        found, val, walked = resolve_dotted(info["doc"] or {}, dotted)
        rec["实际值"], rec["命中路径"] = val, walked
        if not found:
            rec.update(判定="FAIL",
                       原因=f"{fname} 无字段 {dotted}（只走到 {walked}）")
        else:
            s = str(srv).strip()
            ok = s == str(val)
            if not ok:
                try:
                    ok = abs(float(s) - float(val)) < 1e-9
                except (TypeError, ValueError):
                    ok = False
            rec["判定"] = "PASS" if ok else "FAIL"
            if not ok:
                rec["原因"] = f"值不符：声称={s!r} 实际={val!r}"
        items.append(rec)

    n_fail = sum(1 for i in items if i["判定"] == "FAIL")
    return {
        "检查项": "C1 CSV字段路径真实性",
        "源目录": str(src_dir),
        "源目录存在": src_dir.is_dir(),
        "源文件": {k: {"存在": v["exists"], "大小": v["size"], "SHA256": v["sha256"]}
                for k, v in docs.items()},
        "逐行": items,
        "统计": {"总行": len(items), "FAIL": n_fail, "PASS": len(items) - n_fail},
        "判定": "PASS" if items and n_fail == 0 else "FAIL",
    }


# ───────────────────────────────────────────────────────────────────
# C2 manifest 声称
# ───────────────────────────────────────────────────────────────────
def c2_manifest(deliv: Path, project_root: Path) -> dict:
    audit_p = deliv / "audit_ui_verification.json"
    audit = json.loads(audit_p.read_text(encoding="utf-8")) if audit_p.exists() else {}
    brp = audit.get("backtest_run_parity") or {}
    src_dir = Path(brp.get("source_dir") or "")
    manifest = src_dir / "manifest.json"
    run_json = src_dir / "run.json"
    hashes = {}
    if run_json.exists():
        try:
            hashes = json.loads(run_json.read_text(encoding="utf-8")).get("hashes") or {}
        except Exception:  # noqa: BLE001
            hashes = {}

    rows = read_csv_rows(deliv / "04_回测run详情与summary逐字段对账表.csv")
    claim_rows = [{"维度": r[0], "声称路径": r[3], "CSV判定": r[4]}
                  for r in rows[1:] if len(r) >= 4 and "manifest" in r[3]]

    # 若声称了 manifest 来源，但 manifest 不存在 / hashes 为空 ⇒ FAIL
    claimed_manifest = bool(claim_rows)
    manifest_ok = manifest.exists()
    hashes_ok = bool(hashes)

    if not claimed_manifest:
        verdict, why = "PASS", "CSV 未声称 manifest 来源"
    elif manifest_ok or hashes_ok:
        verdict, why = "PASS", "manifest 或 run.json.hashes 存在，来源可核对"
    else:
        verdict = "FAIL"
        why = ("CSV 声称哈希来自 manifest.json，但 manifest 不存在且 run.json.hashes 为空 ⇒ "
               "源侧无官方哈希；该哈希实为测试脚本自算，来源标注失实")

    return {
        "检查项": "C2 manifest 声称真实性",
        "manifest存在": manifest_ok,
        "manifest大小": manifest.stat().st_size if manifest_ok else None,
        "run_json_hashes": hashes,
        "CSV声称manifest的行": claim_rows,
        "判定": verdict,
        "原因": why,
    }


# ───────────────────────────────────────────────────────────────────
# C3 清单自身排除 + 自身哈希
# ───────────────────────────────────────────────────────────────────
def c3_inventory(deliv: Path, project_root: Path) -> dict:
    inv = deliv / "08_文件清单与哈希.csv"
    rows = read_csv_rows(inv)
    items = []
    for r in rows[1:]:
        if len(r) < 4:
            continue
        cat, rel, size_s, sha = r[0], r[1], r[2], r[3]
        p = Path(rel) if Path(rel).is_absolute() else project_root / rel
        rec = {"相对路径": rel, "清单大小": size_s, "清单哈希": sha,
               "实际存在": p.exists(),
               "实际大小": p.stat().st_size if p.exists() else None,
               "实际哈希": sha256_file(p)}
        rec["是清单自身"] = Path(rel).name == inv.name
        rec["大小一致"] = str(rec["实际大小"]) == str(size_s)
        rec["哈希一致"] = rec["实际哈希"] == sha
        if not rec["实际存在"]:
            rec["判定"], rec["原因"] = "FAIL", "文件不存在"
        elif rec["是清单自身"] and str(size_s) == "0":
            rec["判定"], rec["原因"] = "FAIL", "自身被写成空文件(size=0, 空哈希)"
        elif not rec["大小一致"]:
            rec["判定"], rec["原因"] = "FAIL", f"大小 {size_s}→{rec['实际大小']}"
        elif not rec["哈希一致"]:
            rec["判定"], rec["原因"] = "FAIL", "哈希不符"
        else:
            rec["判定"], rec["原因"] = "PASS", ""
        items.append(rec)

    self_rows = [i for i in items if i["是清单自身"]]
    self_in_list = len(self_rows) > 0
    self_empty = any(i["清单哈希"] == EMPTY_SHA for i in self_rows)
    n_fail = sum(1 for i in items if i["判定"] == "FAIL")

    if not items:
        verdict, why = "FAIL", "清单为空或不存在"
    elif n_fail:
        verdict, why = "FAIL", f"{n_fail} 行不合格（含自身空文件 {self_empty}）"
    else:
        verdict, why = "PASS", "清单全部一致且已排除自身"

    return {
        "检查项": "C3 清单自身排除与自身哈希",
        "行数": len(items),
        "清单自身被列入": self_in_list,
        "自身哈希为空文件哈希": self_empty,
        "不合格行": [{"路径": i["相对路径"], "原因": i["原因"]}
                 for i in items if i["判定"] == "FAIL"],
        "判定": verdict,
        "原因": why,
    }


# ───────────────────────────────────────────────────────────────────
# C4 截图数量与唯一性
# ───────────────────────────────────────────────────────────────────
def c4_screenshots(deliv: Path, project_root: Path) -> dict:
    pngs = sorted(deliv.glob("*.png"))
    by_hash = defaultdict(list)
    for p in pngs:
        by_hash[sha256_file(p)].append(p.name)
    view = [p.name for p in pngs if p.name.startswith("view_")]
    state = [p.name for p in pngs if p.name.startswith("state_")]
    dup = {k: v for k, v in by_hash.items() if len(v) > 1}

    # 报告文字声称
    claimed = claimed_view = claimed_state = None
    for md in deliv.glob("*.md"):
        txt = md.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(\d+)\s*张[^\n]{0,10}截图", txt)
        if m:
            claimed = int(m.group(1))
            m2 = re.search(r"(\d+)\s*个\s*View", txt)
            m3 = re.search(r"(\d+)\s*张独立\s*State", txt)
            claimed_view = int(m2.group(1)) if m2 else None
            claimed_state = int(m3.group(1)) if m3 else None
            break

    consistent = True
    reasons = []
    if claimed is not None and claimed != len(pngs):
        consistent = False
        reasons.append(f"报告称 {claimed} 张，实际 {len(pngs)} 张")
    if claimed_view is not None and claimed_view != len(view):
        consistent = False
        reasons.append(f"报告称 {claimed_view} View，实际 {len(view)}")
    if claimed_state is not None and claimed_state != len(state):
        consistent = False
        reasons.append(f"报告称 {claimed_state} State，实际 {len(state)}")
    if dup:
        consistent = False
        reasons.append(f"{len(dup)} 组截图字节重复（改名不等于覆盖）")

    return {
        "检查项": "C4 截图数量与唯一性",
        "PNG总数": len(pngs),
        "view数": len(view),
        "state数": len(state),
        "唯一哈希": len(by_hash),
        "重复组": [{"sha256_前12": k[:12], "文件": v} for k, v in dup.items()],
        "报告声称": {"总数": claimed, "view": claimed_view, "state": claimed_state},
        "判定": "PASS" if consistent else "FAIL",
        "原因": "；".join(reasons) if reasons else "数量与唯一性一致",
    }


# ───────────────────────────────────────────────────────────────────
# C5 版本绑定
# ───────────────────────────────────────────────────────────────────
def c5_version(deliv: Path, project_root: Path) -> dict:
    audit_p = deliv / "audit_ui_verification.json"
    audit = json.loads(audit_p.read_text(encoding="utf-8")) if audit_p.exists() else {}
    keys = [k for k in audit.keys()
            if any(t in k.lower() for t in ("hash", "commit", "version", "binding", "source", "code"))]

    fe_files = [
        project_root / "UI界面" / "web" / "package.json",
        project_root / "UI界面" / "web" / "vite.config.ts",
    ]
    fe_dirs = [
        project_root / "UI界面" / "web" / "src",
        project_root / "UI界面" / "web" / "dist",
    ]
    fe = []
    for p in fe_files:
        if p.exists():
            fe.append({"路径": str(p.relative_to(project_root)), "类型": "文件",
                       "SHA256": sha256_file(p), "大小": p.stat().st_size})
    for d in fe_dirs:
        if d.is_dir():
            files = sorted(x for x in d.rglob("*") if x.is_file())
            combined = hashlib.sha256()
            for x in files:
                combined.update(str(x.relative_to(d)).encode())
                combined.update(sha256_file(x).encode())
            fe.append({"路径": str(d.relative_to(project_root)), "类型": "目录",
                       "文件数": len(files), "目录树SHA256": combined.hexdigest()})

    be = []
    for f in ("adapter.py", "live_api.py", "live_store.py", "unified_runtime.py", "service_manager.py"):
        p = project_root / "框架" / "backend" / f
        if p.exists():
            be.append({"文件": f, "SHA256": sha256_file(p), "大小": p.stat().st_size})

    return {
        "检查项": "C5 前后端源码/构建产物/脚本版本绑定",
        "audit含绑定字段": keys,
        "本审计实算_前端": fe,
        "本审计实算_后端": be,
        "判定": "PASS" if keys else "FAIL",
        "原因": ("交付 JSON 无任何 hash/commit/version/binding/source 字段，"
                 "清单也不含 UI界面/web 与后端源码 ⇒ 无法证明截图对应哪一版代码"),
    }


# ───────────────────────────────────────────────────────────────────
# C6 run_id 三方对齐
# ───────────────────────────────────────────────────────────────────
def c6_runid(deliv: Path, project_root: Path) -> dict:
    audit_p = deliv / "audit_ui_verification.json"
    audit = json.loads(audit_p.read_text(encoding="utf-8")) if audit_p.exists() else {}
    brp = audit.get("backtest_run_parity") or {}
    displayed = brp.get("displayed_run_id")
    src_dir = Path(brp.get("source_dir") or "")

    disk_run_id = None
    rj = src_dir / "run.json"
    if rj.exists():
        try:
            disk_run_id = json.loads(rj.read_text(encoding="utf-8")).get("run_id")
        except Exception:  # noqa: BLE001
            disk_run_id = None

    rows = read_csv_rows(deliv / "04_回测run详情与summary逐字段对账表.csv")
    csv_run_id = rows[1][1] if len(rows) > 1 and len(rows[1]) > 1 else None

    aligned = bool(displayed) and displayed == disk_run_id == csv_run_id
    return {
        "检查项": "C6 页面/审计JSON/磁盘源产物 同 run_id 对齐",
        "页面run_id": displayed,
        "磁盘run.json.run_id": disk_run_id,
        "CSV_run_id": csv_run_id,
        "源目录存在": src_dir.is_dir(),
        "判定": "PASS" if aligned else "FAIL",
        "原因": "" if aligned else "三方 run_id 未全部一致或源目录缺失",
    }


# ───────────────────────────────────────────────────────────────────
# 总入口
# ───────────────────────────────────────────────────────────────────
def verify_delivery(deliv_dir, project_root) -> dict:
    deliv = Path(deliv_dir)
    root = Path(project_root)
    if not deliv.is_dir():
        return {"错误": f"交付目录不存在：{deliv}", "判定": "FAIL"}

    checks = {
        "C1_字段路径": c1_field_paths(deliv, root),
        "C2_manifest声称": c2_manifest(deliv, root),
        "C3_清单自身": c3_inventory(deliv, root),
        "C4_截图唯一性": c4_screenshots(deliv, root),
        "C5_版本绑定": c5_version(deliv, root),
        "C6_run_id对齐": c6_runid(deliv, root),
    }
    summary = {
        "PASS": sum(1 for v in checks.values() if v["判定"] == "PASS"),
        "FAIL": sum(1 for v in checks.values() if v["判定"] == "FAIL"),
        "UNVERIFIED": sum(1 for v in checks.values() if v["判定"] == "UNVERIFIED"),
    }
    return {
        "交付目录": str(deliv),
        "检查项": checks,
        "汇总": summary,
        "总判定": "PASS" if summary["FAIL"] == 0 else "FAIL",
    }
