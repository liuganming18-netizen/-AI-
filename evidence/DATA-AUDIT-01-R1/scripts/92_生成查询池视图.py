"""92 · 生成 queries/ 下可直接执行的只读查询文件

为什么要有这一步：`04_可复算查询与结果.md` 里的查询是给人看的摘要，验收方要的是
**能原样复制执行**的文件。池内 526 币的名单不能手抄（手抄必然与 scope 文件漂移），
所以这里从 `数据/contracts/旧标的池/scope_crypto.json` 直接抽出来生成一个 SQL 视图。

产物：
  queries/_pool_526.sql   池内 526 币的 SQL 视图（自动生成，勿手改）
  queries/_manifest.json  各查询文件与其对应结论、统计时点的索引
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import OUT, ROOT, dump, pool

QDIR = OUT.parent / "queries"
SCOPE = ROOT / "数据/contracts/旧标的池/scope_crypto.json"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    QDIR.mkdir(parents=True, exist_ok=True)
    raw = SCOPE.read_bytes()
    scope = json.loads(raw.decode("utf-8"))
    syms = [it["symbol"] for it in scope["binance_items"]]
    assert len(syms) == 526, len(syms)
    assert len(set(syms)) == 526, "币池有重复"

    lines = [
        "-- 自动生成，勿手改（生成脚本：scripts/92_生成查询池视图.py）",
        "-- 来源：数据/contracts/旧标的池/scope_crypto.json",
        f"-- scope_version={scope['scope_version']}  generated_utc={scope['generated_utc']}",
        f"-- 币数：{len(syms)}（仅加密币；已排除指数 BTCDOMUSDT / ALLUSDT）",
        f"-- 源文件 sha256：{hashlib.sha256(raw).hexdigest()}",
        "--",
        "-- 用法：在同一个 sqlite3 会话里先读本文件，再用 pool 视图过滤",
        "--   sqlite3 -readonly 数据/市场数据/db/market_v2.sqlite3 < queries/_pool_526.sql",
        "-- 只读：本文件只 CREATE TEMP VIEW，不写任何表。",
        "CREATE TEMP VIEW IF NOT EXISTS pool AS",
        "  SELECT column1 AS symbol FROM (VALUES",
    ]
    body = ",\n".join(f"    ('{s}')" for s in syms)
    lines.append(body)
    lines.append("  );")
    (QDIR / "_pool_526.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------------------------------------------------------- 查询索引
    files = {
        "Q1_oi_coverage.sql": {
            "conclusion": "持仓量 5m 覆盖：池内 526/526，全库 528 币",
            "facts": ["F10", "F13"],
            "expect": "池内 526 行；全库 528 行；池内合计 6,188,452 行",
        },
        "Q2_oi_freshness.sql": {
            "conclusion": "持仓量 5m 新鲜度：520/526 ≤1h，6 币陈旧（最久约 10.7 天）",
            "facts": ["F11", "F12"],
            "expect": "<=1h 520；1d~3d 2；>3d 4",
        },
        "Q3_catalog_diff.py": {
            "conclusion": "冷档索引双向差集：未登记 11,471 / 悬空 1,909",
            "facts": ["F50", "F51", "F52"],
            "expect": "disk_only 11471（live 9298）；catalog_only 1909",
        },
        "Q4_metrics_staleness.py": {
            "conclusion": "大户/多空比停更：526 币全部 >7 天，最新 2026-10-14T23:55Z",
            "facts": ["F40", "F41", "F42"],
            "expect": "526 币 / 127,973,455 行；停更中位 11.13 天",
        },
        "Q5_funding_cold_read.py": {
            "conclusion": "资金费冷读 526/526 币返回 0 行，而文件都在盘上",
            "facts": ["F22", "F23", "F24"],
            "expect": "cold 0 行 526/526；对照 oi/candles 冷读各 500 行",
        },
        "Q9_pit_fill.py": {
            "conclusion": "可得时间字段填充率（全量）",
            "facts": ["F25", "F43", "F6-01", "F6-02", "F6-05", "F6-06"],
            "expect": "冷档 available_at 0%；冷档 funding 无该列；热库 funding 100%",
        },
    }
    for name, meta in files.items():
        p = QDIR / name
        if not p.exists():
            raise SystemExit(f"缺文件：{p}")
        meta["sha256"] = sha256(p)
        meta["path"] = f"queries/{name}"
    manifest = {
        "stats_at_utc": json.loads((OUT / "FACTS-DATA-v1.json").read_text(encoding="utf-8"))["generated_at_utc"],
        "scope_file": "数据/contracts/旧标的池/scope_crypto.json",
        "scope_sha256": hashlib.sha256(raw).hexdigest(),
        "pool_symbols": len(syms),
        "queries": files,
        "note": "每个查询都只读；expect 是 2026-10-26 那次运行的完整结果，"
                "行情在动，行数会随采集增长，判定要看桶计数与 0 行的结构性事实，不看绝对行数。",
    }
    dump("92_query_manifest.json", manifest)
    print(f"queries/ 就绪：{len(files)} 个查询 + _pool_526.sql（{len(syms)} 币）")


if __name__ == "__main__":
    main()
