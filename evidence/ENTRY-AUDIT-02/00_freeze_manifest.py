# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-02 / 00 · 输入冻结指纹

只读扫描并记录 sha256，不修改任何被扫描文件。
输出：freeze_manifest.json / freeze_manifest.md / freeze_versions.json
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-02"
OUT.mkdir(parents=True, exist_ok=True)

PARQUET = ROOT / "数据" / "市场数据" / "parquet"


def hist_digest(p: Path) -> dict:
    """行情文件的「已闭合历史切片」指纹。

    live.parquet 会被采集器持续追加，整文件哈希必然变化；
    因此额外记录一份只依赖行内容的指纹（open_time/high/low/close），
    收尾时按「open_time <= 冻结时的最大 open_time」重新计算并比对，
    用来证明历史区间没有被改写、只是尾部追加。
    """
    try:
        d = pd.read_parquet(p, columns=["open_time", "high", "low", "close"])
    except Exception as exc:
        return {"rows": None, "min_open_time": None, "max_open_time": None,
                "hist_digest": None, "hist_error": str(exc)}
    if d.empty:
        return {"rows": 0, "min_open_time": None, "max_open_time": None, "hist_digest": None}
    d = d.sort_values("open_time")
    h = hashlib.sha256()
    h.update(d["open_time"].to_numpy("int64").tobytes())
    for c in ("high", "low", "close"):
        h.update(pd.to_numeric(d[c], errors="coerce").to_numpy("float64").tobytes())
    return {"rows": int(len(d)), "min_open_time": int(d["open_time"].min()),
            "max_open_time": int(d["open_time"].max()), "hist_digest": h.hexdigest()}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rec(p: Path, category: str) -> dict:
    st = p.stat()
    out = {
        "category": category,
        "path": str(p.relative_to(ROOT)),
        "size": st.st_size,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": sha256_file(p),
    }
    if category in ("market", "metrics", "funding"):
        out.update(hist_digest(p))
    return out


def main() -> None:
    # ── 1. 上游研究产物（被审计对象） ──
    upstream = [
        "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.csv",
        "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.jsonl",
        "evidence/FACTOR-ENTRY-01/candidate_features.parquet",
        "evidence/FACTOR-ENTRY-01/candidate_features.csv",
        "evidence/FACTOR-ENTRY-01/stage2_candidate_feature_pipeline.py",
        "evidence/FACTOR-ENTRY-01/enrich_unified_candidate_dataset.py",
        "evidence/FACTOR-ENTRY-01/stage3_ablation_evaluation.py",
        "evidence/FACTOR-ENTRY-01/结论.md",
        "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet",
        "evidence/FACTOR-ENTRY-03/candidate_features_enriched.csv",
        "evidence/FACTOR-ENTRY-03/attribution_analysis.py",
        "evidence/FACTOR-ENTRY-03/evaluation_definitions_and_plan.md",
        "evidence/FACTOR-ENTRY-03/结论.md",
        "evidence/FACTOR-ENTRY-03/code_fingerprints.json",
        "evidence/FACTOR-ENTRY-04/unfilled_reasons.json",
        "evidence/FACTOR-ENTRY-04/reconciliation_227_to_205.csv",
        "evidence/FACTOR-ENTRY-04/reconciliation_227_to_205.json",
        "evidence/FACTOR-ENTRY-04/reconciliation_summary.json",
        "evidence/FACTOR-ENTRY-04/reconciliation_and_evidence.md",
        "evidence/FACTOR-ENTRY-04/candidate_evaluations_audit.csv",
        "evidence/FACTOR-ENTRY-04/audit_and_reconcile_04.py",
        "evidence/FACTOR-ENTRY-04/code_fingerprints.json",
        "evidence/FACTOR-ENTRY-04/结论.md",
        "evidence/FULL_SWEEP_200x100/trades_rich.jsonl",
        "evidence/FULL_SWEEP_200x100/trades.jsonl",
        "evidence/FULL_SWEEP_200x100/runs.jsonl",
        "evidence/FULL_SWEEP_200x100/plan.json",
        "evidence/FULL_SWEEP_200x100/statistics.json",
        "evidence/FULL_SWEEP_200x100/结论.md",
        "run_full_sweep_200x100.py",
    ]
    # ── 2. 代码指纹（决定「为什么 22 条被标成超时」的关键文件） ──
    code = [
        "策略/WyckoffMaster/event_trend_v0_1/runner.py",
        "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
        "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
        "策略/WyckoffMaster/event_trend_v0_1/contracts.py",
        "策略/WyckoffMaster/event_trend_v0_1/config.py",
        "策略/WyckoffMaster/event_trend_v0_1/features.py",
        "策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py",
        "策略/WyckoffMaster/trading_core/engine.py",
        "策略/WyckoffMaster/trading_core/policy.py",
        "策略/WyckoffMaster/trading_core/fill.py",
        "策略/WyckoffMaster/trading_core/risk.py",
        "策略/WyckoffMaster/trading_core/rejects.py",
        "策略/WyckoffMaster/trading_core/ledger.py",
    ]

    items = []
    for rel in upstream:
        p = ROOT / rel
        if p.exists():
            items.append(rec(p, "upstream"))
        else:
            print(f"[缺失] {rel}")

    for rel in code:
        p = ROOT / rel
        if p.exists():
            items.append(rec(p, "code"))
        else:
            print(f"[缺失] {rel}")

    # ── 3. 行情数据：审计涉及的 152 个标的，15m / 1h / 4h 全量 ──
    df = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
    symbols = sorted(df["symbol"].unique().tolist())
    market_n = 0
    market_bytes = 0
    for sym in symbols:
        for tf in ("15m", "1h", "4h"):
            d = PARQUET / "candles/binance/um" / sym / tf
            if not d.exists():
                print(f"[缺失行情] {sym}/{tf}")
                continue
            for f in sorted(d.glob("*.parquet")):
                r = rec(f, "market")
                items.append(r)
                market_n += 1
                market_bytes += r["size"]

    # ── 4. 21 个未成交标的的 metrics / funding ──
    unfilled_syms = sorted(df.loc[df["exec_status"] != "FILLED", "symbol"].unique().tolist())
    for sym in unfilled_syms:
        d = PARQUET / "metrics/binance/um" / sym
        if d.exists():
            for f in sorted(d.glob("*.parquet")):
                items.append(rec(f, "metrics"))
        f = PARQUET / "funding/binance/um" / f"{sym}.parquet"
        if f.exists():
            items.append(rec(f, "funding"))

    manifest = {
        "audit": "ENTRY-AUDIT-02",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(ROOT),
        "counts": {
            "total": len(items),
            "upstream": sum(1 for i in items if i["category"] == "upstream"),
            "code": sum(1 for i in items if i["category"] == "code"),
            "market": market_n,
            "metrics": sum(1 for i in items if i["category"] == "metrics"),
            "funding": sum(1 for i in items if i["category"] == "funding"),
            "market_bytes": market_bytes,
        },
        "symbols": symbols,
        "unfilled_symbols": unfilled_syms,
        "files": items,
    }
    with open(OUT / "00_freeze_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # 关键文件版本（供报告引用）
    key = {}
    for i in items:
        if i["category"] in ("upstream", "code"):
            key[i["path"]] = {"sha256": i["sha256"], "size": i["size"], "mtime_utc": i["mtime_utc"]}
    with open(OUT / "00_freeze_versions.json", "w", encoding="utf-8") as f:
        json.dump(key, f, ensure_ascii=False, indent=2)

    # Markdown 摘要
    lines = [
        "# ENTRY-AUDIT-02 · 输入冻结清单",
        "",
        f"- 冻结时刻（UTC）：{manifest['generated_utc']}",
        f"- 文件总数：{len(items)}（上游 {manifest['counts']['upstream']} · 代码 {manifest['counts']['code']} · "
        f"行情 {market_n} · metrics {manifest['counts']['metrics']} · funding {manifest['counts']['funding']}）",
        f"- 行情体量：{market_bytes/1024/1024:.1f} MB，覆盖 {len(symbols)} 个标的的 15m/1h/4h",
        "",
        "## 关键文件",
        "",
        "| 类别 | 文件 | 字节 | 修改时间(UTC) | sha256 |",
        "| :--- | :--- | ---: | :--- | :--- |",
    ]
    for i in items:
        if i["category"] in ("upstream", "code"):
            lines.append(f"| {i['category']} | `{i['path']}` | {i['size']} | {i['mtime_utc']} | `{i['sha256'][:32]}…` |")
    lines += ["", "> 完整 64 位哈希见 `00_freeze_manifest.json`。", ""]
    (OUT / "00_freeze_manifest.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=1))
    print("冻结完成 →", OUT / "00_freeze_manifest.json")


if __name__ == "__main__":
    main()
