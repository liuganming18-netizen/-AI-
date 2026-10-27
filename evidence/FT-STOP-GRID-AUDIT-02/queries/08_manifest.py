# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-02 / 08_清单

产物清单 + 每个文件的 SHA-256；上游关键文件指纹；登记库链头。
必须在所有其他脚本跑完之后执行（报告本身也算产物）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE_DIR / "evidence/FT-STOP-GRID-AUDIT-02"
UP = BASE_DIR / "evidence/FT-STOP-GRID-02"
UP01 = BASE_DIR / "evidence/FT-STOP-GRID-01"
EVENTS = BASE_DIR / "框架/backend/data/experiments/events.jsonl"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> dict:
    files = sorted(p for p in HERE.rglob("*") if p.is_file() and p.name != "08_清单.json")
    rows = []
    for p in files:
        rows.append({
            "相对路径": str(p.relative_to(HERE)),
            "字节": p.stat().st_size,
            "SHA256": sha(p),
            "修改时间": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).astimezone().isoformat(timespec="seconds"),
        })

    up_files = {}
    for rel in ("user_data/config.json",
                "user_data/strategies/WyckoffStopGrid02Base.py",
                "user_data/strategies/pair_eligibility_rules.json",
                "code/00_generate_strategies.py",
                "code/01_run_all_grid_backtests.py"):
        p = UP / rel
        up_files[rel] = sha(p) if p.exists() else None
    for p in sorted(UP.glob("out/trades_*.csv")):
        up_files[f"out/{p.name}"] = sha(p)
    for rel in ("user_data/strategies/WyckoffStopGridBase.py",
                "user_data/strategies/pair_eligibility_rules.json"):
        p = UP01 / rel
        up_files[f"FT-STOP-GRID-01/{rel}"] = sha(p) if p.exists() else None

    chain = {"存在": EVENTS.exists()}
    if EVENTS.exists():
        lines = [l for l in EVENTS.read_text(encoding="utf-8").splitlines() if l.strip()]
        chain["事件数"] = len(lines)
        if lines:
            last = json.loads(lines[-1])
            # events.jsonl 的行结构是 {"event": {...}, "hash": ..., "prev": ..., "seq": ...}
            # 业务字段在 event 里，hash/prev/seq 在外层
            ev = last.get("event", last)
            chain["链头seq"] = last.get("seq")
            chain["链头hash"] = last.get("hash")
            chain["链头prev"] = last.get("prev")
            chain["链头type"] = ev.get("type")
            chain["链头sample_set_id"] = ev.get("sample_set_id")
            chain["链头at"] = ev.get("at") or ev.get("observed_at")
            chain["链头rows"] = ev.get("rows")
            chain["链头样本键数"] = len(ev.get("sample_keys") or ev.get("keys") or [])

    return {
        "生成时间": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "产物数": len(rows),
        "产物总字节": sum(r["字节"] for r in rows),
        "产物": rows,
        "上游关键文件": up_files,
        "登记库": chain,
    }


if __name__ == "__main__":
    r = main()
    p = HERE / "out/08_清单.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"产物 {r['产物数']} 个，共 {r['产物总字节']} 字节")
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    print("登记库：", json.dumps(r["登记库"], ensure_ascii=False))
