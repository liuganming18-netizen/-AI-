"""20 · 冷档索引一致性（全量枚举 + 逐条差异）

工单整改要求 3：「冷档索引异常」必须给可复算查询、完整结果、统计时点，且全量/抽样分开。
本脚本**不用抽样**：把磁盘上全部 parquet 文件与 `_catalog.json` 的 `shards` 键做双向全量比对。

  out/20_catalog_diff.json         汇总（按类型分组的四类差异计数）
  out/21_catalog_diff_detail.csv   逐文件差异明细（全量，一行一个文件）
  out/22_catalog_meta.json         索引自身元信息（updated_at / pruned / 键格式样例）
  out/23_funding_path_probe.json   资金费路径口径探针（索引键格式 vs 磁盘真实路径）

只读：只读 json 与目录枚举，不写冷档。
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import ARCHIVE, dump, now_utc, utc, write_csv

T0 = time.time()
STATS_AT = now_utc()
CATALOG = ARCHIVE / "_catalog.json"


def log(m: str) -> None:
    print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)


cat = json.load(open(CATALOG, encoding="utf-8"))
shards: dict = cat.get("shards") or {}
log(f"索引 shards {len(shards)} 条；updated_at={cat.get('updated_at')}")

# ---------------------------------------------------------------- 磁盘全量枚举
disk: dict[str, int] = {}
for dp, _dns, fns in os.walk(ARCHIVE):
    for fn in fns:
        if not fn.endswith(".parquet"):
            continue
        full = os.path.join(dp, fn)
        rel = os.path.relpath(full, ARCHIVE).replace(os.sep, "/")
        try:
            disk[rel] = os.path.getsize(full)
        except OSError:
            disk[rel] = -1
log(f"磁盘 parquet 文件 {len(disk)} 个")


def kind(rel: str) -> str:
    return rel.split("/", 1)[0]


disk_kinds = Counter(kind(r) for r in disk)
idx_kinds = Counter(kind(r) for r in shards)

disk_only = sorted(set(disk) - set(shards))
idx_only = sorted(set(shards) - set(disk))

do_by = Counter(kind(r) for r in disk_only)
io_by = Counter(kind(r) for r in idx_only)

# 全量明细
rows = []
for r in disk_only:
    rows.append(["盘上有·索引无", kind(r), r, disk[r], "", ""])
for r in idx_only:
    rows.append(["索引有·盘上无", kind(r), r, "", shards[r].get("rows", ""),
                 shards[r].get("bytes", "")])
rows.sort(key=lambda x: (x[0], x[1], x[2]))
write_csv("21_catalog_diff_detail.csv",
          ["差异类型", "数据类型", "相对路径", "磁盘字节", "索引rows", "索引bytes"], rows)

summary = {
    "stats_at_utc": STATS_AT,
    "population": "全量（磁盘全部 parquet 与索引全部 shards 键双向比对，无抽样）",
    "query": "os.walk(数据/市场数据/parquet) 全量枚举 vs json.load(_catalog.json)['shards'].keys()",
    "catalog_path": "_catalog.json",
    "catalog_updated_at": cat.get("updated_at"),
    "catalog_updated_at_utc": utc(int(cat["updated_at"]) * 1000) if cat.get("updated_at") else None,
    "catalog_shards": len(shards),
    "disk_parquet_files": len(disk),
    "disk_bytes": sum(v for v in disk.values() if v > 0),
    "disk_by_kind": dict(disk_kinds),
    "index_by_kind": dict(idx_kinds),
    "disk_only_total": len(disk_only),
    "disk_only_by_kind": dict(do_by),
    # 未登记文件的构成：live.parquet（实时滚动分片）与年度分片要分开说，
    # 否则「11,471 个文件没登记」会被误读成「11,471 段历史没登记」。
    "disk_only_by_basename": dict(Counter(os.path.basename(r) for r in disk_only).most_common(8)),
    "disk_only_live_parquet": sum(1 for r in disk_only if os.path.basename(r) == "live.parquet"),
    "disk_only_year_parquet": sum(1 for r in disk_only
                                  if os.path.basename(r) != "live.parquet"
                                  and os.path.basename(r)[:4].isdigit()),
    # 按「类型 × 形态」拆开：live.parquet 是滚动分片（本就不该进年度索引），
    # 年度分片/平铺文件才是真正「历史没登记」。
    "disk_only_by_kind_and_form": {
        f"{k}|{'live' if form == 'live' else 'year_or_flat'}": v
        for (k, form), v in sorted(
            Counter(
                (kind(r), "live" if os.path.basename(r) == "live.parquet" else "other")
                for r in disk_only
            ).items()
        )
    },
    "index_only_total": len(idx_only),
    "index_only_by_kind": dict(io_by),
    "samples": {
        "disk_only": disk_only[:5],
        "index_only": idx_only[:5],
    },
}
dump("20_catalog_diff.json", summary)
log(f"盘上有·索引无 {len(disk_only)}；索引有·盘上无 {len(idx_only)}")

# ---------------------------------------------------------------- 索引元信息
pruned = cat.get("pruned")
meta = {
    "stats_at_utc": STATS_AT,
    "catalog_bytes": os.path.getsize(CATALOG),
    "updated_at": cat.get("updated_at"),
    "pruned_type": type(pruned).__name__,
    "pruned": pruned if isinstance(pruned, (int, str, list, dict)) else str(pruned),
    "key_samples": list(shards)[:3],
    "backups": sorted(p.name for p in ARCHIVE.glob("_catalog.json.bak*")),
}
dump("22_catalog_meta.json", meta)

# ---------------------------------------------------------------- 资金费路径探针
fund_disk = sorted(r for r in disk if r.startswith("funding/"))
fund_idx = sorted(r for r in shards if r.startswith("funding/"))
probe = {
    "stats_at_utc": STATS_AT,
    "population": "全量（funding 类型全部文件与全部索引键）",
    "disk_files": len(fund_disk),
    "index_keys": len(fund_idx),
    "disk_samples": fund_disk[:5],
    "index_samples": fund_idx[:5],
    "disk_depth_hist": dict(Counter(r.count("/") for r in fund_disk)),
    "index_depth_hist": dict(Counter(r.count("/") for r in fund_idx)),
    "disk_flat_layout": all(r.count("/") == 3 for r in fund_disk),
    "index_flat_layout": all(r.count("/") == 3 for r in fund_idx),
    "note": "磁盘为 funding/binance/um/<SYMBOL>.parquet 平铺；索引键为 "
            "funding/binance/um/<SYMBOL>/<年>.parquet 目录式 ⇒ 按索引找必然落空",
}
dump("23_funding_path_probe.json", probe)
log(f"funding 磁盘 {len(fund_disk)} 个 / 索引 {len(fund_idx)} 条")

print(f"\n完成，用时 {time.time()-T0:.1f}s；统计时点 {STATS_AT}")
