"""60 · 代码锚点（行号现算，不抄旧报告）

原报告给的行号是 2026-10-25 那一刻的。行号会随任何一次改动漂移，
所以本脚本**每次现算**，并把文件 sha256 一并记下 —— 结论绑定「文件 + 行 + 内容」三者。

  out/60_code_anchors.json   逐条锚点：文件 / sha256 / 行号 / 原文 / 判定

只读：只读源码文本，不执行、不改动。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import ROOT, dump, now_utc

STATS_AT = now_utc()

ANCHORS = [
    # (锚点 id, 文件, 正则, 说明)
    ("A1_oi_empty_call", "框架/backend/unified_runtime.py",
     r"oi_timestamps\s*=\s*np\.array\(\[\]", "实时链路给状态机喂空 OI 数组"),
    ("A2_engine_default", "框架/backend/unified_runtime.py",
     r"enable_oi_filter:\s*bool\s*=\s*False", "信号引擎构造默认 False"),
    ("A3_argparse", "框架/backend/unified_runtime.py",
     r'parser\.add_argument\("--', "命令行参数清单（判断有没有 OI 开关）"),
    ("A4_runtime_ctor", "框架/backend/unified_runtime.py",
     r"runtime\s*=\s*UnifiedRuntime\(", "生产构造点（判断是否传 enable_oi_filter）"),
    ("A5_runner_hardcode", "策略/WyckoffMaster/event_trend_v0_1/runner.py",
     r"enable_oi_filter\s*=\s*False", "FT-LOW4H 回测分支写死 False"),
    ("A6_ab_same_source", "策略/WyckoffMaster/event_trend_v0_1/runner.py",
     r"sm_a\s*=\s*sm_b\s*=", "A/B 两组共用同一份候选"),
    ("A7_sm_default", "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
     r"enable_oi_filter:\s*bool\s*=\s*True", "状态机自身默认 True"),
    ("A8_flat_shards", "框架/backend/market_repository.py",
     r"def _flat_shards", "资金费/OI 冷档分片定位（目录式）"),
    ("A9_candle_shards", "框架/backend/market_repository.py",
     r"def _candle_shards", "K 线冷档分片定位（目录式）"),
    ("A10_metrics_no_write", "框架/backend/realtime_collector.py",
     r"不落库、不写历史", "大户/比率类实时只写快照、不落库"),
]


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


out = {"stats_at_utc": STATS_AT,
       "discipline": "行号现算；文件 sha256 一并记录 ⇒ 结论绑定「文件+行+内容」",
       "anchors": []}

for aid, rel, pat, desc in ANCHORS:
    p = ROOT / rel
    if not p.exists():
        out["anchors"].append({"id": aid, "file": rel, "exists": False, "desc": desc})
        continue
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    rx = re.compile(pat)
    hits = [{"line": i + 1, "text": l.strip()[:220]}
            for i, l in enumerate(lines) if rx.search(l)]
    out["anchors"].append({
        "id": aid, "file": rel, "exists": True, "desc": desc,
        "file_sha256": sha(p), "file_bytes": p.stat().st_size,
        "hit_count": len(hits), "hits": hits,
    })

# 生产构造点是否传了 enable_oi_filter（只看构造调用那几行）
ctor = next(a for a in out["anchors"] if a["id"] == "A4_runtime_ctor")
passes_oi = any("enable_oi_filter" in h["text"] for h in ctor.get("hits", []))
out["runtime_ctor_passes_oi_filter"] = passes_oi

# 命令行参数名清单
ap = next(a for a in out["anchors"] if a["id"] == "A3_argparse")
flags = sorted({m.group(0) for h in ap.get("hits", [])
                for m in [re.search(r'--[a-z_]+', h["text"])] if m})
out["cli_flags"] = flags
out["cli_has_oi_switch"] = any("oi" in f for f in flags)

dump("60_code_anchors.json", out)
print(f"锚点 {len(out['anchors'])} 条；命令行参数 {flags}")
print(f"构造点传 enable_oi_filter = {passes_oi}；命令行有 OI 开关 = {out['cli_has_oi_switch']}")
print(f"统计时点 {STATS_AT}")
