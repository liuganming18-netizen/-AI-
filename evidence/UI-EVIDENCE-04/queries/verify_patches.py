#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-EVIDENCE-04｜修复有效性机械验证（离线，不起后端、不起浏览器）

对 UI-03 原脚本与 UI-04 修复版做**逐项特征比对**：
  · 原脚本应含「缺陷特征」（存在即证明问题真实）
  · 修复版应**不含**缺陷特征
  · 修复版应含「修复特征」（证明真的改了，而不是删了了事）

任何一项不满足即 FAIL。这是「脚本修改」这一层的验收，不等于集成验收。
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import datetime
from pathlib import Path

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OLD = PROJECT_ROOT / "evidence" / "SYSTEM-UI-03" / "test_system_ui_03.py"
NEW = PROJECT_ROOT / "evidence" / "UI-EVIDENCE-04" / "patched" / "test_system_ui_04.py"
OUT = PROJECT_ROOT / "evidence" / "UI-EVIDENCE-04" / "out"

# (编号, 名称, 缺陷特征, 修复特征, 说明)
CASES = [
    ("P1", "manifest 缺失静默取 {} 并谎称哈希来源",
     ["manifest_data = json.load(open(manifest_path)) if manifest_path.exists() else {}"],
     ["manifest_status = \"present\" if manifest_path.exists() else \"missing\"",
      "official_summary_sha256", "official_sha_source"],
     "manifest 不存在时必须记 missing，哈希来源不得标成 manifest"),
    ("P2", "找不到 run 时硬编码回落固定目录",
     ["runs_ft_replay_01/run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT"],
     ["[P2]", "raise AssertionError"],
     "页面 run 与磁盘对不上必须 FAIL，禁止代填"),
    ("P3", "字段层靠 or 链兜底、来源不明",
     ["ts_data = summary_data.get(\"trading_summary\") or summary_data.get(\"summary_variant_a\") or {}"],
     ["ts_layer = \"trading_summary\"", "summary_field_layer", "field_paths_used"],
     "必须记录实际命中的字段层"),
    ("P4", "对账表字段路径手写且恒判一致",
     ['"summary.json / run_id"', '"manifest.json / files.summary.json.sha256"',
      '"summary.json / trading_summary.initial_equity"'],
     ["_fp.get(\"run_id\")", "_fp.get(\"initial_equity\")", "无法比对·未验证"],
     "字段路径取真实命中值，判定按实际比对"),
    ("P5", "文件清单含自身且自身被写成空文件",
     ["for p in sorted(EVID_DIR.glob(\"*\")):"],
     ["_p.resolve() == inv_path.resolve()", "08_清单自身哈希.json", "排除自身"],
     "清单排除自身，自身哈希单独保存"),
    ("P6", "截图数量写死 24 张",
     ["24 张高清测试截图", "8 张独立 State"],
     ["png_total", "png_unique", "png_dup_groups"],
     "数量从实际文件统计，重复画面单独标出"),
    ("P7", "无前后端源码/构建产物版本绑定",
     [],
     ["collect_version_binding", "frontend_src", "frontend_dist", "test_script"],
     "绑定前端源码/构建产物/后端源码/脚本哈希"),
    ("P8", "顶栏价格硬编码兜底",
     ["81,624", "2,404"],
     ["_btc_dom or \"未提取\"", "无法比对·未验证"],
     "DOM 取不到就写未提取，不填数字"),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not OLD.exists() or not NEW.exists():
        print(f"缺文件：OLD={OLD.exists()} NEW={NEW.exists()}")
        return 1

    old_s = OLD.read_text(encoding="utf-8")
    new_s = NEW.read_text(encoding="utf-8")

    # 语法必须能过
    try:
        ast.parse(new_s)
        syntax_ok = True
        syntax_err = None
    except SyntaxError as e:
        syntax_ok = False
        syntax_err = f"{e.msg} @ line {e.lineno}"

    results = []
    for pid, name, defect, fix, note in CASES:
        old_has = [t for t in defect if t in old_s]
        new_has_defect = [t for t in defect if t in new_s]
        new_has_fix = [t for t in fix if t in new_s]
        ok = (not new_has_defect) and (len(new_has_fix) > 0) and (len(old_has) > 0 or not defect)
        results.append({
            "编号": pid,
            "修复项": name,
            "原脚本含缺陷特征": old_has,
            "修复版仍含缺陷特征": new_has_defect,
            "修复版含修复特征": new_has_fix,
            "判定": "PASS" if ok else "FAIL",
            "说明": note,
        })

    n_pass = sum(1 for r in results if r["判定"] == "PASS")
    rec = {
        "工单": "UI-EVIDENCE-04 / 修复有效性",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "原脚本": {"路径": str(OLD.relative_to(PROJECT_ROOT)), "sha256": sha(OLD),
                "字节": OLD.stat().st_size, "字符数": len(old_s)},
        "修复版": {"路径": str(NEW.relative_to(PROJECT_ROOT)), "sha256": sha(NEW),
                "字节": NEW.stat().st_size, "字符数": len(new_s)},
        "语法检查": {"通过": syntax_ok, "错误": syntax_err},
        "逐项": results,
        "汇总": {"PASS": n_pass, "FAIL": len(results) - n_pass, "总数": len(results)},
        "总判定": "PASS" if (syntax_ok and n_pass == len(results)) else "FAIL",
        "边界": ("本验证只证明「脚本已按 7 项要求改对」，不证明修复版能跑通端到端。"
                 "端到端需起后端，按工单在 A 隔离核验通过前不做。"),
    }
    outp = OUT / "patch_verification.json"
    outp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"原脚本  {rec['原脚本']['sha256'][:16]}  {rec['原脚本']['字节']}B")
    print(f"修复版  {rec['修复版']['sha256'][:16]}  {rec['修复版']['字节']}B")
    print(f"语法    {'OK' if syntax_ok else syntax_err}\n")
    for r in results:
        print(f"[{r['判定']}] {r['编号']} {r['修复项']}")
        if r["修复版仍含缺陷特征"]:
            print(f"      仍含缺陷: {r['修复版仍含缺陷特征']}")
        if not r["修复版含修复特征"]:
            print("      缺修复特征")
    print(f"\n汇总 {rec['汇总']}  总判定 {rec['总判定']}")
    print(f"落盘: {outp}")
    return 0 if rec["总判定"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
