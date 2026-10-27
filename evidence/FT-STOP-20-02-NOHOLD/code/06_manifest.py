# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD 产物清单（06）：本轮全部产物哈希 + 外部证据指纹。"""
import hashlib
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
AUD = ROOT / "evidence" / "FT-STOP-20-02-NOHOLD"
OUT = AUD / "out"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# 本轮产物（排除自身与日志）
products = []
for p in sorted(AUD.rglob("*")):
    if not p.is_file():
        continue
    if p.name == "06_产物清单.json":
        continue
    if "logs" in p.relative_to(AUD).parts:
        continue
    if "__pycache__" in p.parts:
        continue
    products.append({"路径": str(p.relative_to(ROOT)), "字节": p.stat().st_size, "sha256": sha(p)})

# 外部证据指纹（只读引用，不复制、不修改）
external = {}
for rel in [
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "evidence/FT-ENTRY-09/user_data/strategies/WyckoffParamExplorationBase.py",
    "evidence/FT-ENTRY-09/user_data/config.json",
    "evidence/FT-STOP-20-01/user_data/strategies/WyckoffStop20ParamExplorationBase.py",
    "evidence/FT-STOP-20-01/user_data/config.json",
    "evidence/FT-STOP-20-AUDIT-01/00_报告.md",
    "框架/backend/data/experiments/events.jsonl",
]:
    p = ROOT / rel
    external[rel] = {"存在": p.exists(), "sha256": sha(p) if p.exists() else None,
                     "字节": p.stat().st_size if p.exists() else None}

# 四套账本指纹（本轮的 A/B + 上游的 C0/C1，用于事后核验同一份数据）
def led(pat):
    d = {}
    for p in sorted(ROOT.glob(pat)):
        d[str(p.relative_to(ROOT))] = {"字节": p.stat().st_size, "sha256": sha(p)}
    return d


payload = {
    "工单": "FT-STOP-20-02-NOHOLD",
    "说明": "只读引用外部证据，不复制、不修改；哈希用于事后核验同一份数据。",
    "产物数": len(products),
    "产物": products,
    "外部证据指纹": external,
    "账本_A_20%止损_无超时": led("evidence/FT-STOP-20-02-NOHOLD/out/trades_Wyckoff_C1_Stop20NoHold_*.csv"),
    "账本_B_ATR止损_无超时": led("evidence/FT-STOP-20-02-NOHOLD/out/trades_Wyckoff_C1_NoHold_*.csv"),
    "账本_C1_20%止损_有超时": led("evidence/FT-STOP-20-01/out/trades_Wyckoff_C1_Stop20_*.csv"),
    "账本_C0_基线_有超时": led("evidence/FT-ENTRY-09/out/trades_Wyckoff_C1_Vs*.csv"),
}
(OUT / "06_产物清单.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

print(f"产物 {len(products)} 个（不含 logs/ 与清单自身）")
for x in products:
    print(f"  {x['字节']:>9}  {x['sha256'][:16]}…  {x['路径']}")
print("\n外部证据指纹:")
for k, v in external.items():
    print(f"  {'OK  ' if v['存在'] else 'MISS'} {str(v['sha256'])[:16]}…  {k}")
print(f"\n账本组数：A={len(payload['账本_A_20%止损_无超时'])} "
      f"B={len(payload['账本_B_ATR止损_无超时'])} "
      f"C1={len(payload['账本_C1_20%止损_有超时'])} "
      f"C0={len(payload['账本_C0_基线_有超时'])}")
