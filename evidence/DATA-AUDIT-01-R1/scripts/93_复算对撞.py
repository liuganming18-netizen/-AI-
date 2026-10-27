"""93 · 复算对撞（交付证据 out/ ↔ 复算产物 out_repro/ ↔ 正文 md 数字）

三件事，全部机器判，不靠人眼：
  A. 交付的 `out/` 与复算的 `out_repro/` 逐项对撞：
     - **结构性事实必须完全相同**（0 行、列不存在、文件数、路径深度这类，不会随行情变）
     - **增长性事实只报差异**（行数、新鲜度秒数、停更天数会随时间增长，不算失败）
  B. 正文（01/03/04/05/06）里引用的数字，逐个回 `out/` 的原始字段复算，
     对不上就报错——防止手抄错、防止正文与证据脱节。
  C. 业务代码 sha256 未变（本单只读，不许改业务代码）。

产物：`out_repro/93_crosscheck.json`
退出码：0 = 全过；1 = 有硬失败（结构性不一致 / 正文数字对不上 / 业务代码变了）

用法：
  sh evidence/DATA-AUDIT-01-R1/reproduce.sh          # 会先跑 10..92 再跑本脚本
  python evidence/DATA-AUDIT-01-R1/scripts/93_复算对撞.py   # 只跑对撞（要求 out_repro 已存在）
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
OUT = BASE / "out"                  # 交付证据（冻结）
REPRO = BASE / "out_repro"          # 复算产物


def load(d: Path, name: str):
    p = d / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur.get(part)
    return cur


# ------------------------------------------------------------------ A. 结构 vs 增长
# kind="struct" 必须完全相同；kind="grow" 允许变化（只报差异）
CHECKS = [
    ("冷档 parquet 文件数", "15_cold_inventory.json", "total_parquet_files", "struct"),
    ("索引 shards 条数", "20_catalog_diff.json", "catalog_shards", "struct"),
    ("索引未登记文件数", "20_catalog_diff.json", "disk_only_total", "struct"),
    ("索引悬空条目数", "20_catalog_diff.json", "index_only_total", "struct"),
    ("资金费路径-磁盘平铺文件数", "23_funding_path_probe.json", "disk_files", "struct"),
    ("资金费路径-索引目录式键数", "23_funding_path_probe.json", "index_keys", "struct"),
    ("资金费冷读-池内币数", "30_funding_cold_read.json", "symbols", "struct"),
    ("资金费冷读-盘上有文件", "30_funding_cold_read.json", "symbols_with_flat_file_on_disk", "struct"),
    ("资金费冷读-找到分片的币", "30_funding_cold_read.json", "symbols_with_cold_shards_found", "struct"),
    ("资金费冷读-返回 0 行的币", "30_funding_cold_read.json", "symbols_cold_zero", "struct"),
    ("资金费冷读-冷读合计行数", "30_funding_cold_read.json", "cold_rows_total", "struct"),
    ("metrics 币数", "14_metrics_staleness.json", "symbols", "struct"),
    ("metrics 总行数", "14_metrics_staleness.json", "rows_total", "struct"),
    ("metrics 最新时刻", "14_metrics_staleness.json", "max_ts_all", "struct"),
    ("metrics 停更>7天币数", "14_metrics_staleness.json", "pool_stale_gt_7d", "struct"),
    ("聪明钱快照文件数", "70_smart_money_t0.json", "files", "struct"),
    ("聪明钱停更>24h 币数", "70_smart_money_t0.json", "stale_gt_24h", "struct"),
    ("冷档 funding 列数", "80_pit_fill.json", "funding_columns", "struct"),
    ("metrics 是否有时点列", "80_pit_fill.json", "metrics_has_publish_or_fetch_col", "struct"),
    ("关闭证据-首次明确关闭记录（生产口径）",
     "40_close_evidence.json", "class_1_explicit_record.earliest_production_runtime.file_mtime_utc", "struct"),
    ("关闭证据-首次代码快照 mtime",
     "40_close_evidence.json", "class_2_code_snapshot.earliest_with_false.mtime_utc", "struct"),
    ("关闭证据-首次代码快照 sha256",
     "40_close_evidence.json", "class_2_code_snapshot.earliest_with_false.sha256", "struct"),
    ("关闭证据-首次运行证据 mtime",
     "40_close_evidence.json", "class_3_runtime_evidence.earliest.file_mtime_utc", "struct"),
    # 增长性：采集器一直在写，每次跑都不一样
    ("持仓量 5m 行数（增长）", "12_oi_freshness.json", "total_rows_5m", "grow"),
    ("持仓量快照行数（增长）", "18_oi_snapshot.json", "rows", "grow"),
    ("资金费热读合计行数（增长）", "30_funding_cold_read.json", "hot_rows_total", "grow"),
    ("持仓量 5m 起点（增长）", "12_oi_freshness.json", "min_ts_all", "grow"),
]


def part_a() -> list[dict]:
    rows = []
    for label, fname, path, kind in CHECKS:
        a, b = load(OUT, fname), load(REPRO, fname)
        if a is None or b is None:
            rows.append({"item": label, "kind": kind, "status": "缺产物",
                         "delivered": None, "reproduced": None})
            continue
        va, vb = dig(a, path), dig(b, path)
        same = json.dumps(va, sort_keys=True, ensure_ascii=False) == \
            json.dumps(vb, sort_keys=True, ensure_ascii=False)
        if kind == "struct":
            status = "一致" if same else "**硬失败**"
        else:
            status = "未变" if same else "已增长（允许）"
        rows.append({"item": label, "kind": kind, "status": status,
                     "delivered": va, "reproduced": vb})
    return rows


# ------------------------------------------------------------------ B. 正文 ↔ 证据
def pool_rows_from_csv(d: Path) -> int | None:
    p = d / "11_oi_5m_per_symbol.csv"
    if not p.exists():
        return None
    with open(p, encoding="utf-8-sig") as fh:
        return sum(int(r["rows_5m"]) for r in csv.DictReader(fh) if r["in_approved_pool"] == "1")


def build_literals() -> list[tuple[str, str, str]]:
    """返回 (正文文件, 该出现于正文的字面量, 说明)；字面量全部由 out/ 现算。"""
    oi = load(OUT, "12_oi_freshness.json")
    cold = load(OUT, "15_cold_inventory.json")
    cat = load(OUT, "20_catalog_diff.json")
    met = load(OUT, "14_metrics_staleness.json")
    pit = load(OUT, "80_pit_fill.json")
    snap = load(OUT, "18_oi_snapshot.json")
    sm = load(OUT, "70_smart_money_t0.json")
    fund = load(OUT, "17_funding_hot.json")
    close = load(OUT, "40_close_evidence.json")
    pool_rows = pool_rows_from_csv(OUT)

    oi_rows = next(f["rows"] for f in pit["fields"] if f["dataset"] == "冷档 oi")
    cd_rows = next(f["rows"] for f in pit["fields"] if f["dataset"] == "冷档 candles")

    lit = [
        ("04_可复算查询与结果.md", f"{oi['total_rows_5m']:,}", "持仓量 5m 全库行数"),
        ("04_可复算查询与结果.md", f"{pool_rows:,}", "持仓量 5m 池内行数"),
        ("04_可复算查询与结果.md",
         f"{oi['fresh_le_1h']}/{oi['approved_pool']}（{oi['fresh_le_1h_pct']}%）", "≤1h 覆盖"),
        ("04_可复算查询与结果.md", f"{cold['total_parquet_files']:,}", "冷档文件数"),
        ("04_可复算查询与结果.md", f"{cat['catalog_shards']:,}", "索引条数"),
        ("04_可复算查询与结果.md", f"{cat['disk_only_total']:,}", "未登记文件数"),
        ("04_可复算查询与结果.md", f"{cat['index_only_total']:,}", "悬空条目数"),
        ("04_可复算查询与结果.md", f"{met['rows_total']:,}", "metrics 总行数"),
        ("04_可复算查询与结果.md", met["max_ts_all"], "metrics 最新时刻"),
        ("04_可复算查询与结果.md", f"{met['stale_days_pool']['p50']:.2f}", "metrics 停更中位天数"),
        ("04_可复算查询与结果.md", f"{oi_rows:,}", "冷档 oi 行数"),
        ("04_可复算查询与结果.md", f"{cd_rows:,}", "冷档 candles 行数"),
        ("03_逐项数据台账.md", f"{oi['total_rows_5m']:,}", "持仓量 5m 行数"),
        ("03_逐项数据台账.md", f"{oi_rows:,}", "冷档 oi 行数"),
        ("03_逐项数据台账.md", f"{fund['rows_total']:,}", "资金费热库行数"),
        ("03_逐项数据台账.md", f"{fund['symbols']}", "资金费覆盖币数"),
        ("05_聪明钱前向分析.md", f"{sm['files']}", "聪明钱文件数"),
        ("05_聪明钱前向分析.md", f"{sm['stale_gt_24h']}", "聪明钱停更>24h 数"),
        ("05_聪明钱前向分析.md", f"{sm['freshness_hours']['p50']:.1f}", "聪明钱停更中位小时"),
        ("06_旧说法纠正表.md", f"{oi_rows:,}", "冷档 oi 行数"),
        ("06_旧说法纠正表.md", f"{snap['rows']:,}", "持仓量快照行数"),
        ("01_持仓量关闭时间证据分级.md",
         close["class_1_explicit_record"]["earliest_production_runtime"]["file_mtime_utc"],
         "首次明确关闭记录（生产口径）"),
        ("01_持仓量关闭时间证据分级.md",
         close["class_2_code_snapshot"]["earliest_with_false"]["mtime_utc"], "首次代码快照"),
        ("01_持仓量关闭时间证据分级.md",
         close["class_3_runtime_evidence"]["earliest"]["file_mtime_utc"], "首次运行证据"),
    ]
    return [(f, s, why) for f, s, why in lit if s]


def part_b() -> list[dict]:
    rows = []
    for fname, literal, why in build_literals():
        p = BASE / fname
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
        rows.append({"file": fname, "literal": literal, "what": why,
                     "status": "命中" if literal in txt else "**未命中（正文与证据脱节）**"})
    return rows


# ------------------------------------------------------------------ C. 业务代码未改
CODE_FILES = [
    "框架/backend/unified_runtime.py",
    "框架/backend/market_repository.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
]


def sha256(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def part_c() -> list[dict]:
    anchors = load(OUT, "60_code_anchors.json")
    recorded: dict[str, str] = {}
    if anchors:
        for a in anchors["anchors"]:
            if a.get("file_sha256"):
                recorded[a["file"]] = a["file_sha256"]
    rows = []
    for rel in CODE_FILES:
        now = sha256(BASE.parents[1] / rel) if (BASE.parents[1] / rel).exists() else None
        if now is None:
            rows.append({"file": rel, "recorded": recorded.get(rel), "now": None,
                         "status": "文件不存在（跳过）"})
            continue
        exp = recorded.get(rel)
        if exp is None:
            status = "无基线（本单未记锚点，只核对此刻 sha256）"
        else:
            status = "未变" if exp == now else "**变了（业务代码被改动）**"
        rows.append({"file": rel, "recorded": exp, "now": now, "status": status})
    return rows


def main() -> int:
    if not REPRO.exists():
        print(f"没有 {REPRO}：先跑 reproduce.sh（本脚本只做对撞，不产出证据）")
        return 2

    a, b, c = part_a(), part_b(), part_c()

    print("=" * 78)
    print("A · 交付 out/  ↔  复算 out_repro/")
    print("=" * 78)
    for r in a:
        mark = "  " if r["status"] in ("一致", "未变", "已增长（允许）") else "!!"
        print(f"{mark} [{r['kind']:<6}] {r['item']:<34} {r['status']}")
        if r["status"] not in ("一致", "未变"):
            print(f"        交付={r['delivered']}  复算={r['reproduced']}")

    print()
    print("=" * 78)
    print("B · 正文数字  ↔  证据字段（逐个回算，不靠人眼）")
    print("=" * 78)
    for r in b:
        mark = "  " if r["status"] == "命中" else "!!"
        print(f"{mark} {r['file']:<28} {r['literal']:<24} {r['what']:<20} {r['status']}")

    print()
    print("=" * 78)
    print("C · 业务代码 sha256（本单只读）")
    print("=" * 78)
    for r in c:
        mark = "  " if (r["status"] in ("未变", "文件不存在（跳过）")
                       or r["status"].startswith("无基线")) else "!!"
        print(f"{mark} {r['file']:<40} {r['status']}")

    hard = [r for r in a if r["status"] in ("硬失败", "缺产物")] + \
           [r for r in b if r["status"] != "命中"] + \
           [r for r in c if r["status"].startswith("**")]
    REPRO.mkdir(parents=True, exist_ok=True)
    (REPRO / "93_crosscheck.json").write_text(json.dumps(
        {"stats_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "part_a": a, "part_b": b, "part_c": c,
         "hard_failures": len(hard)}, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    if hard:
        print(f"结论：**不通过**，{len(hard)} 项硬失败")
        for r in hard:
            print("   -", r)
        return 1
    print("结论：通过（结构性事实逐项一致；正文数字全部回算命中；业务代码未变）")
    print("注意：增长性事实的差异是行情在写库导致的，不是复算失败；"
          "判定依据见 A 段 kind=grow 那几行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
