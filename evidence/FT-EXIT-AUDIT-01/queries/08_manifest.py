# -*- coding: utf-8 -*-
"""FT-EXIT-AUDIT-01 / 08：产物清单与指纹

 - 本目录所有产物：相对路径 + 字节数 + SHA-256（清单自身不入清单）
 - 外部依赖指纹：上游策略/配置/脚本、数据目录、登记库链头
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BASE = ROOT / "evidence/FT-EXIT-AUDIT-01"
EXP = ROOT / "evidence/FT-EXIT-01"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


files = []
for p in sorted(BASE.rglob("*")):
    if not p.is_file():
        continue
    rel = p.relative_to(BASE).as_posix()
    if rel == "out/08_清单.json":
        continue
    files.append({"文件": rel, "字节": p.stat().st_size, "SHA256": sha(p)})

external = {
    "FT-EXIT-01/user_data/strategies/WyckoffExit01Base.py":
        EXP / "user_data/strategies/WyckoffExit01Base.py",
    "FT-EXIT-01/user_data/config.json": EXP / "user_data/config.json",
    "FT-EXIT-01/code/00_generate_strategies.py": EXP / "code/00_generate_strategies.py",
    "FT-EXIT-01/code/01_run_all_backtests.py": EXP / "code/01_run_all_backtests.py",
    "FT-EXIT-01/code/02_reconcile_and_analyze.py": EXP / "code/02_reconcile_and_analyze.py",
    "FT-EXIT-01/00_仅初始止损持仓对照综合研究报告.md":
        EXP / "00_仅初始止损持仓对照综合研究报告.md",
    "FT-EXIT-01/user_data/strategies/pair_eligibility_rules.json":
        EXP / "user_data/strategies/pair_eligibility_rules.json",
}
ext = {}
for name, p in external.items():
    ext[name] = {"存在": p.exists(),
                 "字节": p.stat().st_size if p.exists() else None,
                 "SHA256": sha(p) if p.exists() else None}

# 数据目录真实路径（软链要还原）
data_link = EXP / "user_data/data"
data_real = str(data_link.resolve()) if data_link.exists() else None

# 登记库链头
head = None
try:
    sys.path.insert(0, str(ROOT / "框架/backend"))
    from experiment_registry import ExperimentRegistry  # noqa: E402
    head = ExperimentRegistry()._read_head()
except Exception as exc:  # pragma: no cover
    head = {"error": str(exc)}

doc = {
    "本目录产物数": len(files),
    "本目录总字节": sum(f["字节"] for f in files),
    "产物": files,
    "外部指纹": ext,
    "数据目录真实路径": data_real,
    "登记库链头": head,
}
(BASE / "out/08_清单.json").write_text(
    json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"产物 {len(files)} 个，合计 {doc['本目录总字节']:,} 字节")
for f in files:
    print(f"  {f['字节']:>10,}  {f['SHA256'][:16]}  {f['文件']}")
print("\n外部指纹：")
for k, v in ext.items():
    print(f"  {str(v['SHA256'])[:16]}  {k}")
print(f"\n数据目录真实路径: {data_real}")
print(f"登记库链头: {head}")
print(f"\n写出 {BASE/'out/08_清单.json'}")
