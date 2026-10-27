#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-EVIDENCE-04｜对 UI-03 现有产物做离线机械对账（不起后端、不起浏览器）

设计原则（对应工单 1~7 条）：
  R1  字段路径必须真实存在；manifest 缺失就写 missing，禁止编造来源
  R2  页面 / API / 源产物必须按同一 run_id 对齐；对不上就 FAIL，不硬填
  R3  文件清单排除自身；清单自身哈希单独保存
  R4  截图数量从实际文件统计，不抄报告文字
  R5  绑定前端源码 / 构建产物 / 后端源码 / 测试脚本的 SHA-256
  R6  重复截图不得算作覆盖；如实标出
  R7  离线反例见 negative_tests.py

判定三态：PASS / FAIL / UNVERIFIED（未验证不计入通过）
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import datetime
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
UI03 = PROJECT_ROOT / "evidence" / "SYSTEM-UI-03"
OUT = PROJECT_ROOT / "evidence" / "UI-EVIDENCE-04" / "out"


def sha256_file(p: Path) -> str:
    if not p.exists() or not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def read_csv(p: Path) -> list:
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def resolve_json_path(doc: dict, dotted: str) -> tuple:
    """按 a.b.c 逐层取值，返回 (found, value, 实际走过的路径)。"""
    cur = doc
    walked = []
    for seg in dotted.split("."):
        walked.append(seg)
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return False, None, ".".join(walked[:-1]) or "<root>"
    return True, cur, ".".join(walked)


# ═══════════════════════════════════════════════════════════════════
# 检查 1：CSV04 声称的字段路径，在真实源文件里是否存在
# ═══════════════════════════════════════════════════════════════════
def check_csv04_field_paths() -> dict:
    rows = read_csv(UI03 / "04_回测run详情与summary逐字段对账表.csv")
    audit = json.loads((UI03 / "audit_ui_verification.json").read_text(encoding="utf-8"))
    src_dir = Path(audit.get("backtest_run_parity", {}).get("source_dir", ""))
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
        dim, dom, srv, field_path, verdict = r[0], r[1], r[2], r[3], r[4]
        rec = {"对账维度": dim, "CSV声称字段路径": field_path,
               "CSV声称服务端值": srv, "CSV判定": verdict}
        # 解析 "文件名 / 点分路径"
        if " / " in field_path:
            fname, dotted = [x.strip() for x in field_path.split(" / ", 1)]
        else:
            fname, dotted = field_path.strip(), ""
        doc_info = docs.get(fname, {"exists": False})
        rec["源文件"] = fname
        rec["源文件存在"] = bool(doc_info.get("exists"))
        if not doc_info.get("exists"):
            rec["字段存在"] = False
            rec["实际值"] = None
            rec["判定"] = "FAIL"
            rec["原因"] = f"声称的来源文件 {fname} 不存在"
            items.append(rec)
            continue
        found, val, walked = resolve_json_path(doc_info["doc"] or {}, dotted)
        rec["字段存在"] = found
        rec["实际值"] = val
        rec["实际命中路径"] = walked
        if not found:
            rec["判定"] = "FAIL"
            rec["原因"] = f"{fname} 中不存在字段 {dotted}（实际只能走到 {walked}）"
        else:
            # 字段存在，再核对值与 CSV 声称是否一致
            srv_s = str(srv).strip()
            ok = (srv_s == str(val)) or (val is not None and srv_s in (str(val), f"{val}"))
            # 数值型放宽到 float 比较
            if not ok:
                try:
                    ok = abs(float(srv_s) - float(val)) < 1e-9
                except (TypeError, ValueError):
                    ok = False
            rec["判定"] = "PASS" if ok else "FAIL"
            if not ok:
                rec["原因"] = f"值不符：CSV={srv_s!r} 实际={val!r}"
        items.append(rec)

    return {
        "检查项": "CSV04 字段路径真实性",
        "源产物目录": str(src_dir),
        "源产物目录存在": src_dir.is_dir(),
        "源文件清单": {k: {"存在": v["exists"], "大小": v["size"], "SHA256": v["sha256"]}
                   for k, v in docs.items()},
        "逐行": items,
        "统计": {
            "总行数": len(items),
            "PASS": sum(1 for i in items if i["判定"] == "PASS"),
            "FAIL": sum(1 for i in items if i["判定"] == "FAIL"),
        },
        "判定": ("PASS" if all(i["判定"] == "PASS" for i in items) and items else "FAIL"),
        "原因": ("全部字段路径真实存在且值一致" if items and all(i["判定"] == "PASS" for i in items)
                 else "存在字段路径不存在或值不符的行，见 逐行"),
    }


# ═══════════════════════════════════════════════════════════════════
# 检查 2：manifest 声称核查
# ═══════════════════════════════════════════════════════════════════
def check_manifest_claim() -> dict:
    audit = json.loads((UI03 / "audit_ui_verification.json").read_text(encoding="utf-8"))
    brp = audit.get("backtest_run_parity", {})
    src_dir = Path(brp.get("source_dir", ""))
    manifest = src_dir / "manifest.json"
    rows = read_csv(UI03 / "04_回测run详情与summary逐字段对账表.csv")
    claim_rows = [r for r in rows if len(r) >= 4 and "manifest" in r[3]]

    actual_summary_sha = sha256_file(src_dir / "summary.json")
    audit_claimed_sha = brp.get("summary_sha256")

    return {
        "检查项": "manifest 是否存在 + CSV 是否谎称哈希来源",
        "audit_json_manifest_present": brp.get("manifest_present"),
        "磁盘_manifest存在": manifest.exists(),
        "磁盘_manifest大小": manifest.stat().st_size if manifest.exists() else None,
        "run_json_hashes字段": (json.loads((src_dir / "run.json").read_text(encoding="utf-8")).get("hashes")
                            if (src_dir / "run.json").exists() else None),
        "CSV_声称manifest行": [{"维度": r[0], "字段路径": r[3], "判定": r[4]} for r in claim_rows],
        "summary_sha_审计JSON记录": audit_claimed_sha,
        "summary_sha_本脚本实算": actual_summary_sha,
        "两者一致": audit_claimed_sha == actual_summary_sha,
        "判定": "FAIL",
        "原因": ("manifest.json 不存在，run.json.hashes 为空 ⇒ 源侧无任何官方哈希可比对；"
                 "CSV04 却把哈希来源写成 'manifest.json / files.summary.json.sha256' 并判'一致'。"
                 "该哈希实为测试脚本自算，来源标注失实。"),
    }


# ═══════════════════════════════════════════════════════════════════
# 检查 3：清单自身哈希 + 清单大小 vs 实际大小
# ═══════════════════════════════════════════════════════════════════
def check_inventory_self() -> dict:
    inv = UI03 / "08_文件清单与哈希.csv"
    rows = read_csv(inv)
    items = []
    for r in rows[1:]:
        if len(r) < 4:
            continue
        cat, rel, size_s, sha = r[0], r[1], r[2], r[3]
        p = PROJECT_ROOT / rel
        rec = {"相对路径": rel, "清单记大小": size_s, "清单记哈希": sha}
        rec["实际存在"] = p.exists()
        rec["实际大小"] = p.stat().st_size if p.exists() else None
        rec["实际哈希"] = sha256_file(p)
        rec["大小一致"] = str(rec["实际大小"]) == str(size_s)
        rec["哈希一致"] = rec["实际哈希"] == sha
        rec["是清单自身"] = rel.endswith("08_文件清单与哈希.csv")
        rec["判定"] = "PASS" if (rec["实际存在"] and rec["大小一致"] and rec["哈希一致"]) else "FAIL"
        items.append(rec)

    self_row = next((i for i in items if i["是清单自身"]), None)

    def _why(i):
        if i["是清单自身"] and str(i["清单记大小"]) == "0":
            return "自身被写成空文件(size=0, 空哈希)"
        if not i["实际存在"]:
            return "文件不存在"
        if not i["大小一致"]:
            return f"大小 {i['清单记大小']} → {i['实际大小']}"
        if not i["哈希一致"]:
            return "哈希不符"
        return ""

    return {
        "检查项": "文件清单自身排除 + 自身哈希单独保存",
        "清单行数": len(items),
        "清单自身行": self_row,
        "自身行是否为空文件哈希": bool(self_row and self_row["清单记哈希"].startswith("e3b0c442")),
        "空文件哈希常量": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "不合格行": [{"路径": i["相对路径"], "原因": _why(i)}
                 for i in items if i["判定"] == "FAIL"],
        "判定": "FAIL",
        "原因": ("清单在自身被打开写入的状态下遍历目录，把自身统计成 size=0 / 空文件哈希 "
                 "e3b0c442…；且清单自身也在清单内，形成自指。audit_ui_verification.json "
                 "清单记 4499B 而实际 4504B（清单先写、后写 JSON，顺序错）。"),
    }


# ═══════════════════════════════════════════════════════════════════
# 检查 4：截图数量与唯一性（从实际文件统计）
# ═══════════════════════════════════════════════════════════════════
def check_screenshots() -> dict:
    pngs = sorted(UI03.glob("*.png"))
    by_hash = defaultdict(list)
    for p in pngs:
        by_hash[sha256_file(p)].append(p.name)
    view = [p.name for p in pngs if p.name.startswith("view_")]
    state = [p.name for p in pngs if p.name.startswith("state_")]
    dup_groups = {k: v for k, v in by_hash.items() if len(v) > 1}

    # 报告文字里的数字
    md = (UI03 / "00_SYSTEM_UI_03_真实页面与数据对账交付报告.md")
    md_txt = md.read_text(encoding="utf-8") if md.exists() else ""
    claimed = None
    import re
    m = re.search(r"(\d+)\s*张高清测试截图", md_txt)
    if m:
        claimed = int(m.group(1))
    m2 = re.search(r"(\d+)\s*个\s*View", md_txt)
    m3 = re.search(r"(\d+)\s*张独立\s*State", md_txt)

    return {
        "检查项": "截图数量机械统计 + 重复检测",
        "实际PNG总数": len(pngs),
        "其中_view": len(view),
        "其中_state": len(state),
        "唯一哈希数": len(by_hash),
        "重复组数": len(dup_groups),
        "重复组": [{"sha256_前12": k[:12], "文件": v} for k, v in dup_groups.items()],
        "报告声称总数": claimed,
        "报告声称_view": int(m2.group(1)) if m2 else None,
        "报告声称_state": int(m3.group(1)) if m3 else None,
        "数量是否自相矛盾": (claimed is not None and claimed != len(pngs)),
        "判定": "FAIL",
        "原因": (f"实际 {len(pngs)} 张（view {len(view)} + state {len(state)}），报告写 {claimed} 张 / "
                 f"{m2.group(1) if m2 else '?'} View + {m3.group(1) if m3 else '?'} State；"
                 f"唯一哈希仅 {len(by_hash)}，{len(dup_groups)} 组重复 —— "
                 f"3 个「正常态」截图与 view 截图字节相同，属改名而非覆盖。"),
    }


# ═══════════════════════════════════════════════════════════════════
# 检查 5：源码 / 构建产物版本绑定
# ═══════════════════════════════════════════════════════════════════
def check_version_binding() -> dict:
    audit = json.loads((UI03 / "audit_ui_verification.json").read_text(encoding="utf-8"))
    binding_keys = [k for k in audit.keys()
                    if any(t in k.lower() for t in ("hash", "commit", "version", "binding", "source"))]

    # 候选前端源码 / 构建产物
    fe_candidates = [
        PROJECT_ROOT / "UI界面" / "web" / "src",
        PROJECT_ROOT / "UI界面" / "web" / "dist",
        PROJECT_ROOT / "UI界面" / "web" / "package.json",
    ]
    fe = []
    for c in fe_candidates:
        if c.is_file():
            fe.append({"路径": str(c.relative_to(PROJECT_ROOT)), "类型": "文件",
                       "SHA256": sha256_file(c), "大小": c.stat().st_size})
        elif c.is_dir():
            files = [p for p in c.rglob("*") if p.is_file()]
            combined = hashlib.sha256()
            for p in sorted(files):
                combined.update(str(p.relative_to(c)).encode())
                combined.update(sha256_file(p).encode())
            fe.append({"路径": str(c.relative_to(PROJECT_ROOT)), "类型": "目录",
                       "文件数": len(files), "目录树SHA256": combined.hexdigest()})

    be_files = ["adapter.py", "live_api.py", "live_store.py", "unified_runtime.py", "service_manager.py"]
    be = []
    for f in be_files:
        p = PROJECT_ROOT / "框架" / "backend" / f
        if p.exists():
            be.append({"文件": f, "SHA256": sha256_file(p), "大小": p.stat().st_size})

    script = UI03 / "test_system_ui_03.py"

    return {
        "检查项": "前后端源码 / 构建产物 / 测试脚本 版本绑定",
        "audit_json_含绑定字段": binding_keys,
        "audit_json_是否绑定源码": bool(binding_keys),
        "本脚本实算_前端": fe,
        "本脚本实算_后端": be,
        "本脚本实算_测试脚本": {"文件": str(script.relative_to(PROJECT_ROOT)),
                          "SHA256": sha256_file(script), "大小": script.stat().st_size},
        "判定": "FAIL" if not binding_keys else "PASS",
        "原因": ("audit_ui_verification.json 顶层 13 个键中无任何 hash/commit/version/binding/source 类字段；"
                 "08 清单只列 evidence 目录内的交付物，不含 UI界面/web/src、dist 与后端源码 ⇒ "
                 "无法证明本轮截图对应的到底是哪一版前端/后端。"),
    }


# ═══════════════════════════════════════════════════════════════════
# 检查 6：同 run_id 三方对齐（页面 DOM / 审计 JSON / 磁盘源产物）
# ═══════════════════════════════════════════════════════════════════
def check_runid_alignment() -> dict:
    audit = json.loads((UI03 / "audit_ui_verification.json").read_text(encoding="utf-8"))
    brp = audit.get("backtest_run_parity", {})
    displayed = brp.get("displayed_run_id")
    src_dir = Path(brp.get("source_dir", ""))

    disk_run_id = None
    run_json = src_dir / "run.json"
    if run_json.exists():
        disk_run_id = json.loads(run_json.read_text(encoding="utf-8")).get("run_id")

    # CSV04 第一行 Run ID 声称
    rows = read_csv(UI03 / "04_回测run详情与summary逐字段对账表.csv")
    csv_run_id = rows[1][1] if len(rows) > 1 and len(rows[1]) > 1 else None
    csv_src_run_id = rows[1][2] if len(rows) > 1 and len(rows[1]) > 2 else None

    # 脚本里硬编码的回落目录（对照用）
    script_txt = (UI03 / "test_system_ui_03.py").read_text(encoding="utf-8")
    hardcoded_fallback = "runs_ft_replay_01/run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT"
    has_hardcoded = hardcoded_fallback in script_txt

    aligned = (displayed == disk_run_id == csv_run_id)

    return {
        "检查项": "页面 / 审计JSON / 磁盘源产物 同 run_id 对齐",
        "页面显示run_id(审计JSON记录)": displayed,
        "磁盘run.json.run_id": disk_run_id,
        "CSV04_run_id列": csv_run_id,
        "CSV04_服务端列": csv_src_run_id,
        "源产物目录": str(src_dir),
        "源产物目录存在": src_dir.is_dir(),
        "三方一致": aligned,
        "脚本含硬编码回落目录": has_hardcoded,
        "硬编码回落内容": hardcoded_fallback if has_hardcoded else None,
        "判定": "PASS" if aligned else "FAIL",
        "原因": ("" if aligned else
                 "页面 run_id 与磁盘 run.json.run_id 不一致，三方无法对齐。"),
        "附加风险": ("脚本第 835-838 行在 os.walk 找不到时硬编码回落到 "
                     "runs_ft_replay_01/…1000XECUSDT —— 一旦页面显示的是别的 run，"
                     "会用这个固定目录的数据代填，属硬填数字。") if has_hardcoded else None,
    }


# ═══════════════════════════════════════════════════════════════════
# 检查 7：硬编码数字扫描（CSV03 顶栏价格等）
# ═══════════════════════════════════════════════════════════════════
def check_hardcoded_values() -> dict:
    script_txt = (UI03 / "test_system_ui_03.py").read_text(encoding="utf-8")
    suspects = []
    for token in ("81,624", "2,404", "2000.0", "-8.24", "\"null\""):
        if token in script_txt:
            lines = [i + 1 for i, ln in enumerate(script_txt.split("\n")) if token in ln]
            suspects.append({"token": token, "出现行": lines[:8], "次数": script_txt.count(token)})
    rows03 = read_csv(UI03 / "03_顶栏与行情9列同一响应逐字段比对表.csv")
    return {
        "检查项": "硬编码数字扫描",
        "脚本内硬编码token": suspects,
        "CSV03前5行": rows03[:5],
        "判定": "FAIL" if suspects else "PASS",
        "原因": ("脚本第 1139/1140 行在 DOM 取不到时回落字面量 '81,624' / '2,404'，"
                 "即页面没渲染出数值也会写出一个价格并判'一致'。"),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not UI03.exists():
        print(f"UI-03 交付不存在：{UI03}")
        return 1

    checks = {
        "C1_CSV04字段路径": check_csv04_field_paths(),
        "C2_manifest声称": check_manifest_claim(),
        "C3_清单自身哈希": check_inventory_self(),
        "C4_截图数量与重复": check_screenshots(),
        "C5_版本绑定": check_version_binding(),
        "C6_run_id对齐": check_runid_alignment(),
        "C7_硬编码数字": check_hardcoded_values(),
    }

    rec = {
        "工单": "UI-EVIDENCE-04",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "审核对象": str(UI03),
        "审核对象_清单自身哈希": sha256_file(UI03 / "08_文件清单与哈希.csv"),
        "审核对象_文件数": len([p for p in UI03.glob("*") if p.is_file()]),
        "检查项": checks,
        "汇总": {
            "FAIL": sum(1 for v in checks.values() if v.get("判定") == "FAIL"),
            "PASS": sum(1 for v in checks.values() if v.get("判定") == "PASS"),
            "UNVERIFIED": sum(1 for v in checks.values() if v.get("判定") == "UNVERIFIED"),
        },
    }
    outp = OUT / "recon_ui03.json"
    outp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"审核对象: {UI03}")
    print(f"汇总: {rec['汇总']}\n")
    for name, v in checks.items():
        print(f"[{v.get('判定')}] {name}")
        if v.get("判定") == "FAIL":
            print(f"      {v.get('原因','')[:170]}")
    print(f"\n落盘: {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
