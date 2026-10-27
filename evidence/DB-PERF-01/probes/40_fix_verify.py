"""DB-PERF-01 后续「修复问题」四项的机械复算探针（只读，不改任何业务状态）。

目的：报告里出现的每个数字都必须能从这里重新算出来，不靠人工抄写。
只发 GET，不写库、不发单、不改闸门。

用法：
    python evidence/DB-PERF-01/probes/40_fix_verify.py
输出：
    evidence/DB-PERF-01/out/fix_verify.json
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "DB-PERF-01" / "out"
OUT.mkdir(parents=True, exist_ok=True)

# 本机有 HTTP_PROXY 但没有 no_proxy ⇒ urllib 会把 127.0.0.1 也发给代理。
# 必须显式空 ProxyHandler，否则请求会被代理吞掉/超时。
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path: str, timeout: int = 180) -> tuple[int, int, float, bytes]:
    t0 = time.time()
    r = _op.open("http://127.0.0.1:8787" + path, timeout=timeout)
    body = r.read()
    return r.status, len(body), (time.time() - t0) * 1000.0, body


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


res: dict = {"captured_at_utc": datetime.now(timezone.utc).isoformat()}

# ── ① 冷库 catalog 53 条过期声明 ────────────────────────────────────
cat = ROOT / "数据" / "市场数据" / "parquet" / "_catalog.json"
bak = sorted(cat.parent.glob("_catalog.json.bak-*"))
st, n, ms, body = get("/api/v1/data/cold-archive-status")
cold = json.loads(body)
se = cold.get("shard_existence") or {}
res["cold_archive"] = {
    "endpoint_status": st,
    "response_bytes": n,
    "elapsed_ms": round(ms, 1),
    "status": cold.get("status"),
    "catalog_sha256_now": cold.get("catalog_sha256"),
    "catalog_sha256_recomputed": sha256_file(cat),
    "catalog_bytes": cat.stat().st_size,
    "catalog_backup": bak[-1].name if bak else None,
    "catalog_backup_bytes": bak[-1].stat().st_size if bak else None,
    "shard_count": cold.get("shard_count"),
    "candle_shard_count": cold.get("candle_shard_count"),
    "declared": se.get("declared"),
    "present": se.get("present"),
    "missing": se.get("missing"),
    "missing_symbols": se.get("missing_symbols"),
    "rows": se.get("rows"),
    "hash_sample": se.get("hash_sample"),
    "content_integrity_verified": se.get("content_integrity_verified"),
    "verified_scope": se.get("verified_scope"),
    "elapsed_breakdown_ms": se.get("elapsed_breakdown_ms"),
    "catalog_pruned": cold.get("catalog_pruned"),
    "limitations": cold.get("limitations"),
}

# ── ② /data/coverage 的 ?agg=1 ──────────────────────────────────────
st1, n1, ms1, b1 = get("/api/v1/data/coverage?agg=1")
st2, n2, ms2, _ = get("/api/v1/data/coverage")
agg = json.loads(b1)
agg = agg.get("data", agg)
res["coverage_agg"] = {
    "agg_status": st1,
    "agg_bytes": n1,
    "agg_elapsed_ms": round(ms1, 1),
    "full_status": st2,
    "full_bytes": n2,
    "full_elapsed_ms": round(ms2, 1),
    "shrink_ratio": round(n2 / max(n1, 1), 1),
    "agg_true": agg.get("agg"),
    "cells_count": agg.get("cells_count"),
    "cells_len": len(agg.get("cells") or []),
    "totals": agg.get("totals"),
    "by_timeframe": agg.get("by_timeframe"),
    "by_field": agg.get("by_field"),
    "by_exchange": agg.get("by_exchange"),
    "note": (agg.get("quality") or {}).get("note"),
}

# ── ③ 数据源红绿灯（EPICUSDT 资金费率）──────────────────────────────
st, n, ms, body = get("/api/v1/data/feeds")
feeds = json.loads(body).get("feeds") or []
res["feeds"] = {
    "endpoint_status": st,
    "response_bytes": n,
    "elapsed_ms": round(ms, 1),
    "count": len(feeds),
    "abnormal": [{"name": f.get("name"), "status": f.get("status"),
                  "status_label": f.get("status_label")} for f in feeds
                 if f.get("status") != "normal"],
    "funding": next(({"name": f.get("name"), "status": f.get("status"),
                      "status_label": f.get("status_label"),
                      "detail": f.get("detail")} for f in feeds
                     if "资金费率" in str(f.get("name"))), None),
}

# ── ④ 逐笔对账证据（含复算说明）────────────────────────────────────
st, n, ms, body = get("/api/v1/system-flow/paper-reconciliation")
rec = json.loads(body)
bad = {k: v for k, v in (rec.get("source_hashes") or {}).items()
       if v.get("status") != "match"}
res["paper_reconciliation"] = {
    "endpoint_status": st,
    "status": rec.get("status"),
    "passed": rec.get("passed"),
    "total": rec.get("total"),
    "scope_kind": rec.get("scope_kind"),
    "run_id": rec.get("run_id"),
    "generated_at_utc": rec.get("generated_at_utc"),
    "age_seconds": rec.get("age_seconds"),
    "stale_after_seconds": rec.get("stale_after_seconds"),
    "source_hash_count": len(rec.get("source_hashes") or {}),
    "source_hash_mismatch": bad,
    "reverify_note_present": bool(rec.get("reverify_note")),
    "reverify_note": rec.get("reverify_note"),
    "reason": rec.get("reason"),
}

# ── ⑤ 运行时交接期状态语义（stale_record）──────────────────────────
st, n, ms, body = get("/api/v1/paper/status")
_ps = json.loads(body)
_ps = _ps.get("data", _ps)          # 该接口无 data 包裹，item 在顶层；两种都兼容
it = _ps.get("item") or {}
ri = it.get("runtime_identity") or {}
sie = it.get("signal_engine") or {}
res["runtime_identity"] = {
    "status": ri.get("status"),
    "pid": ri.get("pid"),
    "record_pid_alive": ri.get("record_pid_alive"),
    "record_written_ms": ri.get("record_written_ms"),
    "record_age_ms": ri.get("record_age_ms"),
    "record_status_raw": ri.get("record_status_raw"),
    "cycle_count": ri.get("cycle_count"),
    "overall_health": ri.get("overall_health"),
    "last_cycle_ms": sie.get("last_cycle_ms"),
    "universe": sie.get("universe"),
    "universe_declared": sie.get("universe_declared"),
    "signals_generated_total": sie.get("signals_generated_total"),
}

# ── ⑥ 两套止损规则仍未裁决（本轮没动，写清楚免得被当成已解决）────
res["still_open"] = [
    "模拟盘止损用「成交价×0.95」，回测用「Lr−0.1A」⇒ 同一信号仓位差约 3.8 倍、出场全分歧，尚未裁决。",
    "自然行情 0 笔成交 ⇒ 「真实挂钟自动成交」仍未验证（不是故障，是还没发生）。",
    "正式实验能启动但 delivery_eligible=false ⇒ 结果不可交付。",
    "catalog 声明范围 ≠ 全市场覆盖；冷库哈希仍是 5% 抽样，不是逐字节全量。",
]

p = OUT / "fix_verify.json"
p.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({
    "out": str(p.relative_to(ROOT)),
    "cold_missing": res["cold_archive"]["missing"],
    "cold_integrity": res["cold_archive"]["content_integrity_verified"],
    "agg_bytes": res["coverage_agg"]["agg_bytes"],
    "full_bytes": res["coverage_agg"]["full_bytes"],
    "feeds_abnormal": len(res["feeds"]["abnormal"]),
    "recon_status": res["paper_reconciliation"]["status"],
    "recon_hash_mismatch": len(res["paper_reconciliation"]["source_hash_mismatch"]),
    "runtime_status": res["runtime_identity"]["status"],
}, ensure_ascii=False))
