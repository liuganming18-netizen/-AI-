#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-EVIDENCE-04｜生成本工单自身的交付清单（按 P5 新规范示范）

规则（与修复版 test_system_ui_04.py 一致）：
  1. 全部产物写完后**最后**生成清单
  2. 清单**排除自身**，避免自指与空文件哈希
  3. 清单自身哈希单独存 `08_清单自身哈希.json`
  4. 版本绑定单独存 `09_源码与构建产物版本绑定.json`

另附：用 lib_verifier 对**本工单自身清单**做一次自检（不含截图类检查）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import datetime
from pathlib import Path

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = PROJECT_ROOT / "evidence" / "UI-EVIDENCE-04"
OUT = HERE / "out"

sys.path.insert(0, str(HERE / "queries"))


def sha(p: Path) -> str:
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def tree_sha(d: Path, root: Path) -> dict:
    files = sorted(x for x in d.rglob("*") if x.is_file())
    t = hashlib.sha256()
    for x in files:
        rel = str(x.relative_to(d))
        t.update(rel.encode())
        t.update(sha(x).encode())
    return {"path": str(d.relative_to(root)), "file_count": len(files),
            "tree_sha256": t.hexdigest()}


def main() -> int:
    inv = HERE / "08_文件清单与哈希.csv"
    self_hash_p = HERE / "08_清单自身哈希.json"
    binding_p = HERE / "09_源码与构建产物版本绑定.json"

    # ── 1. 收集本工单顶层交付物（排除清单自身与自身哈希、绑定文件本身）────────
    # self_manifest_check.json 在清单生成之后才写入，若列入则记录的必然是旧哈希
    EXCLUDE = {inv.name, self_hash_p.name, binding_p.name, "self_manifest_check.json"}
    targets = []
    for p in sorted(HERE.rglob("*")):
        if not p.is_file() or p.name in EXCLUDE:
            continue
        rel = p.relative_to(HERE)
        if str(rel).startswith("out/fixed_sample/"):
            continue          # 样例复用了 UI-03 的 PNG，不逐文件重复登记
        if rel.parts and rel.parts[0] == "out" and rel.name in (
                "fixed_sample_check.json",):
            targets.append(("样例自检读数", p)); continue
        if rel.parts and rel.parts[0] == "queries":
            targets.append(("复算脚本", p))
        elif rel.parts and rel.parts[0] == "out":
            targets.append(("原始读数", p))
        else:
            targets.append(("交付文档", p))

    # ── 2. 版本绑定 ───────────────────────────────────────────────
    web = PROJECT_ROOT / "UI界面" / "web"
    binding = {"生成时刻": datetime.datetime.now().isoformat(), "绑定": {}, "缺失项": []}
    for key, d in (("frontend_src", web / "src"), ("frontend_dist", web / "dist")):
        if d.is_dir():
            binding["绑定"][key] = tree_sha(d, PROJECT_ROOT)
        else:
            binding["缺失项"].append(key)
    be = {}
    for name in ("adapter.py", "live_api.py", "live_store.py", "unified_runtime.py",
                 "service_manager.py", "paper_engine.py", "strategy_contracts.py"):
        fp = PROJECT_ROOT / "框架" / "backend" / name
        if fp.exists():
            be[name] = {"sha256": sha(fp), "size": fp.stat().st_size,
                        "mtime": datetime.datetime.fromtimestamp(fp.stat().st_mtime).isoformat()}
    binding["绑定"]["backend"] = be
    binding["绑定"]["test_script"] = {
        "path": "evidence/UI-EVIDENCE-04/patched/test_system_ui_04.py",
        "sha256": sha(HERE / "patched" / "test_system_ui_04.py"),
        "size": (HERE / "patched" / "test_system_ui_04.py").stat().st_size}
    binding["绑定"]["对照_UI03原脚本"] = {
        "path": "evidence/SYSTEM-UI-03/test_system_ui_03.py",
        "sha256": sha(PROJECT_ROOT / "evidence" / "SYSTEM-UI-03" / "test_system_ui_03.py"),
        "说明": "原脚本只作对照，未修改"}
    # 样例目录摘要（不逐文件）
    sample = OUT / "fixed_sample" / "deliv"
    if sample.is_dir():
        binding["样例产物目录摘要"] = {
            **tree_sha(sample, OUT),
            "说明": "复用了 UI-03 的 25 张真实 PNG，不逐文件登记；逐项自检见 out/fixed_sample_check.json"}

    # ── 3. 生成清单（最后，排除自身）───────────────────────────────
    rows = [["文件类别", "相对路径", "文件大小(Bytes)", "SHA-256完整哈希"]]
    for cat, p in targets:
        rows.append([cat, str(p.relative_to(PROJECT_ROOT)), p.stat().st_size, sha(p)])
    with open(inv, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)

    # ── 4. 自身哈希单独保存 ────────────────────────────────────────
    self_hash = {
        "文件": str(inv.relative_to(PROJECT_ROOT)),
        "大小(Bytes)": inv.stat().st_size,
        "SHA-256": sha(inv),
        "清单行数": len(rows) - 1,
        "排除自身": True,
        "说明": "清单排除自身；清单自身哈希单独存于本文件，避免自指循环",
        "生成时刻": datetime.datetime.now().isoformat(),
    }

    # ── 5. 落盘 ────────────────────────────────────────────────────
    self_hash_p.write_text(json.dumps(self_hash, ensure_ascii=False, indent=2), encoding="utf-8")
    binding_p.write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 6. 自检：用 lib_verifier 校验本清单自身（C3）────────────────
    import lib_verifier as V  # noqa: E402
    chk = V.c3_inventory(HERE, PROJECT_ROOT)

    rec = {
        "工单": "UI-EVIDENCE-04 / 自身清单生成",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "清单": self_hash,
        "清单行数": len(rows) - 1,
        "版本绑定键": list(binding["绑定"].keys()),
        "版本绑定缺失项": binding["缺失项"],
        "自检_C3": chk,
        "自检判定": chk["判定"],
        "清单未覆盖文件": {
            "08_文件清单与哈希.csv": self_hash,
            "08_清单自身哈希.json": {
                "sha256": sha(self_hash_p),
                "size": self_hash_p.stat().st_size,
                "说明": "登记清单自身哈希，故自身不入清单"},
            "09_源码与构建产物版本绑定.json": {
                "sha256": sha(binding_p),
                "size": binding_p.stat().st_size,
                "说明": "版本绑定文件，在清单生成后才落盘"},
            "out/self_manifest_check.json": {
                "说明": "本文件在清单生成之后才写入，故不在清单内；其内容含上三项的哈希"},
        },
        "未覆盖原因": ("08 清单/08 自身哈希/09 版本绑定 三个文件与清单存在生成顺序依赖，"
                    "放入清单会形成自指；统一登记于本文件。"),
        "样例目录": ("out/fixed_sample/deliv/ 复用 UI-03 的 25 张真实 PNG，不逐文件重复登记，"
                 "只以目录树摘要形式记入 09_源码与构建产物版本绑定.json"),
    }
    outp = OUT / "self_manifest_check.json"
    outp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"清单: {inv}")
    print(f"  行数 {len(rows)-1}，自身已排除: {not any(r[1].endswith(inv.name) for r in rows[1:])}")
    print(f"  清单自身 SHA-256 = {self_hash['SHA-256']}")
    print(f"版本绑定键: {list(binding['绑定'].keys())}  缺失: {binding['缺失项']}")
    print(f"C3 自检: {chk['判定']} — {chk['原因']}")
    print(f"落盘: {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
