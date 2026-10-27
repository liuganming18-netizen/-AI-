# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 产物清单（08）：本轮全部产物哈希 + 外部证据指纹。"""
import hashlib
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
AUDIT = ROOT / "evidence" / "FT-STOP-20-AUDIT-01"
OUT = AUDIT / "out"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# 本轮产物（排除自身与日志）
products = []
for p in sorted(AUDIT.rglob("*")):
    if not p.is_file():
        continue
    if p.name == "08_产物清单.json":
        continue
    products.append(
        {"路径": str(p.relative_to(ROOT)), "字节": p.stat().st_size, "sha256": sha(p)}
    )

# 外部证据指纹（只读引用，不复制）
external = {}
for rel in [
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "evidence/FT-STOP-20-01/code/00_generate_stop20_strategies.py",
    "evidence/FT-STOP-20-01/code/01_run_all_stop20_backtests.py",
    "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py",
    "evidence/FT-STOP-20-01/user_data/config.json",
    "evidence/FT-STOP-20-01/00_固定20止损对照综合研究报告.md",
    "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py",
    "evidence/FT-ENTRY-09/user_data/config.json",
    "回测日志/event_trend_v0_1/runs_ft_stop_20_01_research/batch_meta.json",
    "框架/backend/data/experiments/events.jsonl",
]:
    p = ROOT / rel
    external[rel] = {"存在": p.exists(), "sha256": sha(p) if p.exists() else None,
                     "字节": p.stat().st_size if p.exists() else None}

# 20% 方案 9 组账本指纹
ledgers = {}
for p in sorted((ROOT / "evidence/FT-STOP-20-01/out").glob("trades_Wyckoff_C1_Stop20_*.csv")):
    ledgers[str(p.relative_to(ROOT))] = {"字节": p.stat().st_size, "sha256": sha(p)}
base_ledgers = {}
for p in sorted((ROOT / "evidence/FT-ENTRY-09/out").glob("trades_Wyckoff_C1_Vs*.csv")):
    base_ledgers[str(p.relative_to(ROOT))] = {"字节": p.stat().st_size, "sha256": sha(p)}

payload = {
    "工单": "FT-STOP-20-AUDIT-01",
    "产物数": len(products),
    "产物": products,
    "外部证据指纹": external,
    "20方案账本": ledgers,
    "基线账本": base_ledgers,
    "说明": "只读引用外部证据，不复制、不修改；哈希用于事后核验同一份数据。",
}
(OUT / "08_产物清单.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
)

print(f"产物 {len(products)} 个")
for x in products:
    print(f"  {x['字节']:>9}  {x['sha256'][:16]}…  {x['路径']}")
print("\n外部证据指纹:")
for k, v in external.items():
    print(f"  {'OK ' if v['存在'] else 'MISS'} {str(v['sha256'])[:16]}…  {k}")
