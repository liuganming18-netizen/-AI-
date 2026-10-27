#!/usr/bin/env python3
"""04 · 产出稳定资金费事件文件 + 清单 + 接口说明（交付给窗口四、六）

「稳定」的定义（可验证，不是形容词）
----------------------------------
S1 事件文件字节可复现：同一输入重跑 ⇒ sha256 逐位相同。
   为此文件里**不含任何生成时刻**，键序固定（sort_keys），浮点用 JSON 原生表示。
S2 行序确定：按 (symbol, funding_time) 升序，不依赖字典/集合遍历顺序。
S3 清单同样字节可复现；生成时刻单独落在 `04_运行时刻.json`，不污染清单。
S4 每行自带两侧原值（rate_hot/rate_cold/mark_hot/mark_cold）与来源，消费方可自行复算，
   不需要回读本窗脚本。

产出
----
out/funding_events_母集48.jsonl      稳定事件文件（一行一个结算事件）
out/funding_events_母集48_manifest.json  清单（无时间戳，字节稳定）
out/接口说明.md                      给窗口四、六的接口说明（含判定表与禁区）
out/04_产出清单.json                 本脚本自检结果 + 文件身份
out/04_运行时刻.json                 生成时刻（单独存放，避免破坏稳定性）
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"))

import funding_events_adapter as A  # noqa: E402

DATASET = "funding_events_母集48"
JSONL = OUT / f"{DATASET}.jsonl"
MANIFEST = OUT / f"{DATASET}_manifest.json"


def utc(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    t0 = time.time()
    anchor = json.loads((OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    p02 = json.loads((OUT / "02_缺口段与分区行为.json").read_text(encoding="utf-8"))
    symbols = anchor["A_窗口与母集"]["symbols"]
    start_ms = anchor["A_窗口与母集"]["start_ms"]
    end_ms = anchor["A_窗口与母集"]["end_ms"]

    repo = A._get_repo(None, None)
    per = {s: A.load_funding_events("binance", "um", s, start_ms, end_ms, repo=repo)
           for s in symbols}

    # ---------- 写事件文件（S1/S2）----------
    lines = []
    for s in sorted(symbols):
        for e in per[s]["events"]:
            lines.append(json.dumps(e, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")))
    body = "\n".join(lines) + "\n"
    JSONL.write_text(body, encoding="utf-8")
    file_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    n_events = len(lines)

    # ---------- 聚合 ----------
    agg = {}
    for s in symbols:
        for k, v in per[s]["counts"].items():
            if isinstance(v, int):
                agg[k] = agg.get(k, 0) + v

    per_symbol = []
    for s in sorted(symbols):
        c = per[s]["counts"]
        cov = per[s]["coverage_observed"]
        per_symbol.append({
            "symbol": s,
            "events": c["events"],
            "observed_both": c["observed_both"],
            "observed_hot_only": c["observed_hot_only"],
            "observed_cold_only": c["observed_cold_only"],
            "status_resolved": c["resolved"],
            "status_resolved_single_side": c["resolved_single_side"],
            "status_pending_conflict": c["pending_conflict"],
            "status_pending_missing": c["pending_missing"],
            "with_rate": c["with_rate"],
            "with_mark": c["with_mark"],
            "cold_lo_utc": utc(cov["cold_lo"]),
            "cold_hi_utc": utc(cov["cold_hi"]),
            "hot_lo_utc": utc(cov["hot_lo"]),
            "hot_hi_utc": utc(cov["hot_hi"]),
            "hot_path_leaked_cold": c["hot_path_leaked_cold"],
            "notes": per[s]["notes"],
        })

    manifest = {
        "dataset": DATASET,
        "adapter_version": A.ADAPTER_VERSION,
        "generated_by": "evidence/ACCUM-EARLY-06-06/scripts/04_产出事件文件与清单.py",
        "generated_at": "见 04_运行时刻.json（本清单不含时间戳，保证字节可复现）",
        "scope": {
            "exchange": "binance", "market": "um",
            "start_ms": start_ms, "end_ms": end_ms,
            "start_utc": anchor["A_窗口与母集"]["start_utc"],
            "end_utc": anchor["A_窗口与母集"]["end_utc"],
            "limit": 200000, "prefer_hot": "hot", "prefer_cold": "cold", "strict": False,
            "dedup_key": list(A.KEY_FIELDS),
            "repo": "MarketDataRepository.funding（现成读取器，未改）",
        },
        "counts": {
            "symbols": len(symbols),
            "events": n_events,
            **{k: agg[k] for k in ("observed_both", "observed_hot_only", "observed_cold_only",
                                   "resolved", "resolved_single_side", "pending_conflict",
                                   "pending_missing", "with_rate", "with_mark")},
        },
        "field_semantics": {
            "funding_time": "原始结算时刻（毫秒，不截秒），来自交易所真实结算事件",
            "rate": "顶层取值：两侧一致或仅一侧有值时给出；冲突时为 null（fail-closed）",
            "mark_price": "同上",
            "rate_hot/rate_cold/mark_hot/mark_cold": "两侧原始值，永不覆盖，供并列保留与复算",
            "observed_in": "hot | cold | both —— 该事件在哪些源里出现过",
            "status": "resolved | resolved_single_side | pending_conflict | pending_missing",
            "conflicts": "逐字段冲突明细（field/kind/hot/cold/severity）",
            "available_at": "恒为 null：冷档无该列，热库 fetched_at 不是「当时可得」",
            "availability": "恒为 unproven —— 历史补齐 ≠ 当时可得",
        },
        "data_identity": {
            "热库": anchor["B_数据身份"]["热库"],
            "冷档": anchor["B_数据身份"]["冷档"],
            "注意": "热库在执行期间被外部进程持续写入（见 03），这里的哈希只代表 00 取哈希时刻",
        },
        "known_limits": [
            "全部事件的 availability 为 unproven：本文件**不能**用于证明「当时就能看到这条数据」，"
            "只能用于事后复算资金费成本。",
            "冷档覆盖末端之后（见 per_symbol.cold_hi_utc 的最大值）只能靠热库，"
            "该段不是「合并后完整」，而是「没有第二个源」。",
            "period 不可推算的标的（02 报 grid_unreliable）其「是否缺事件」无法判定，"
            "本文件只保证「有的都给了、没有的不编」。",
            "热库侧同键重复、冷档侧同键重复均为 0（见 counts / 03 I2），"
            "若上游将来出现重复，本文件的去重只保留首条并会在 per_symbol.notes 里记 hot_dup/cold_dup。",
        ],
        "gap_reference": {
            "缺口段_缺失端点口径": p02["缺口段_重算"]["全跨度_lo_utc"] + " → "
                                  + p02["缺口段_重算"]["全跨度_hi_utc"],
            "缺口段_在库端点口径": p02["与06-05对照"]["本单_两侧最近在库时刻_起"] + " → "
                                  + p02["与06-05对照"]["本单_两侧最近在库时刻_止"],
            "端点不可混用": "缺失端点表示「这些时刻没有数据」；在库端点表示「缺口两侧最后/最先有数据的时刻」",
        },
        "files": {
            "jsonl": {"name": JSONL.name, "bytes": len(body.encode("utf-8")),
                      "lines": n_events, "sha256": file_sha},
        },
        "per_symbol": per_symbol,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 自检（S1 用二次写入比对）----------
    lines2 = []
    for s in sorted(symbols):
        r = A.load_funding_events("binance", "um", s, start_ms, end_ms, repo=repo)
        for e in r["events"]:
            lines2.append(json.dumps(e, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")))
    body2 = "\n".join(lines2) + "\n"
    sha2 = hashlib.sha256(body2.encode("utf-8")).hexdigest()

    checks = []

    def add(name, ok, detail):
        checks.append({"检查": name, "通过": bool(ok), "证据": detail})

    add("S1 重跑同输入 ⇒ 事件文件 sha256 逐位相同", sha2 == file_sha,
        {"第一次": file_sha, "第二次": sha2, "lines": n_events})
    add("S2 行序确定：按 (symbol, funding_time) 升序",
        all(True for _ in [0]) and _sorted_ok(lines), {"首行": lines[0][:120]})
    keys = [(e["exchange"], e["market"], e["symbol"], e["funding_time"])
            for s in symbols for e in per[s]["events"]]
    add("S3 无重复键", len(keys) == len(set(keys)),
        {"events": len(keys), "distinct": len(set(keys))})
    add("S4 与 03 的 events_sha256 一致（跨脚本，同一输入同一摘要）",
        json.loads((OUT / "03_零写入与幂等.json").read_text(encoding="utf-8"))["events_sha256"]
        == A.events_sha256([e for s in symbols for e in per[s]["events"]]),
        {"03": json.loads((OUT / "03_零写入与幂等.json")
                          .read_text(encoding="utf-8"))["events_sha256"][:16],
         "04": A.events_sha256([e for s in symbols for e in per[s]["events"]])[:16]})
    add("S5 清单不含生成时刻（字节可复现的前提）",
        "generated_at_utc" not in json.dumps(manifest),
        {"清单键": list(manifest.keys())})
    add("S6 事件数 == 02 汇总事件数",
        n_events == p02["汇总"]["events"], {"04": n_events, "02": p02["汇总"]["events"]})

    n_pass = sum(1 for c in checks if c["通过"])
    (OUT / "04_产出清单.json").write_text(json.dumps({
        "task": "ACCUM-EARLY-06-06",
        "probe": "04_产出事件文件与清单",
        "files": manifest["files"],
        "checks": checks,
        "summary": {"total": len(checks), "pass": n_pass,
                    "verdict": "通过" if n_pass == len(checks) else "不通过"},
        "elapsed_sec": round(time.time() - t0, 1),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    (OUT / "04_运行时刻.json").write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jsonl_sha256": file_sha,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    _write_doc(manifest, per, symbols, p02)

    for c in checks:
        print(("PASS " if c["通过"] else "FAIL ") + c["检查"])
    print(f"\n事件文件 {JSONL.name}  {manifest['files']['jsonl']['bytes']:,} bytes  "
          f"{n_events:,} 行  sha {file_sha[:16]}")
    print(out_verdict := json.dumps({"total": len(checks), "pass": n_pass}, ensure_ascii=False))
    return 0 if n_pass == len(checks) else 1


def _sorted_ok(lines):
    prev = None
    for ln in lines:
        d = json.loads(ln)
        k = (d["symbol"], d["funding_time"])
        if prev is not None and k < prev:
            return False
        prev = k
    return True


def _write_doc(manifest, per, symbols, p02):
    c = manifest["counts"]
    gap = manifest["gap_reference"]
    # 冷档实测覆盖末端 = 逐标的最晚 cold_hi 的最大值（不是缺口段端点，两者差 26 天）
    cold_end = max((r["cold_hi_utc"] for r in manifest["per_symbol"] if r["cold_hi_utc"]),
                   default=None)
    rows = "\n".join(
        f"| `{r['symbol']}` | {r['events']} | {r['observed_both']} | {r['observed_cold_only']} "
        f"| {r['observed_hot_only']} | {r['status_pending_conflict']} "
        f"| {r['status_pending_missing']} | {r['cold_hi_utc'] or '—'} |"
        for r in manifest["per_symbol"][:8])

    doc = f"""# 资金费事件文件 · 接口说明（给窗口四、六）

> 由 `ACCUM-EARLY-06-06/scripts/04_产出事件文件与清单.py` 生成。
> 数据集 `{manifest['dataset']}`，适配器版本 `{manifest['adapter_version']}`。
> **本文件不是「当时可得的证据」**，只用于事后复算资金费成本。

## 1 这是什么

从**现成读取器** `MarketDataRepository.funding` 分两侧读取后，按
`(exchange, market, symbol, funding_time)` 去重合并得到的资金费**结算事件**清单。

- 交易所 `binance`，合约 `um`，窗口 `{manifest['scope']['start_utc']}` → `{manifest['scope']['end_utc']}`
- 标的 {c['symbols']} 个，事件 **{c['events']:,}** 条
- 其中两侧都有 {c['observed_both']:,}，仅冷档 {c['observed_cold_only']:,}，仅热库 {c['observed_hot_only']:,}
- 取值状态：resolved {c['resolved']:,} / resolved_single_side {c['resolved_single_side']:,}
  / pending_conflict {c['pending_conflict']:,} / pending_missing {c['pending_missing']:,}

## 2 怎么用

### 2.1 直接用文件

```python
import json
evs = [json.loads(l) for l in open("evidence/ACCUM-EARLY-06-06/out/funding_events_母集48.jsonl")]
```

### 2.2 直接调适配器（推荐，能拿到 notes 与读取范围）

```python
import sys
sys.path.insert(0, "evidence/ACCUM-EARLY-06-06/adapter")
import funding_events_adapter as A

loaded = A.load_funding_events("binance", "um", "TNSRUSDT", 1782547200000, 1793005200000)
events = A.to_replay_events(loaded)      # 直接喂 replay_one(funding_events=events)
print(loaded["counts"], loaded["notes"], loaded["read_scope"])
```

`to_replay_events()` 输出的每条含 `funding_time`（毫秒）/ `rate` / `mark_price`，
外加 `status` / `observed_in` 供统计。**pending 事件不会被丢掉**：`rate=None` 会落成
`funding_gap`，缺口在账面上可见；丢掉则少一次结算而账面看不出异常。

### 2.3 接进 `replay_one`

`replay_calc.replay_one(..., funding_events=...)` 是**唯一需要的接入点**，
本单没有改 `replay_calc.py`、也没有改 `trading_core/funding.py`。

## 3 字段与判定表

| 字段 | 含义 |
|---|---|
| `funding_time` | 原始结算时刻，毫秒，不截秒 |
| `rate` / `mark_price` | 顶层取值。**冲突时为 `null`**（fail-closed） |
| `rate_hot`/`rate_cold`/`mark_hot`/`mark_cold` | 两侧原始值，永不覆盖 |
| `observed_in` | `hot` / `cold` / `both` |
| `status` | 见下表 |
| `conflicts` | 逐字段冲突明细 |
| `available_at` / `availability` | 恒为 `null` / `unproven` |

| 情形 | 顶层取值 | status | 说明 |
|---|---|---|---|
| 两侧都有且相等 | 取值 | `resolved` | 正常 |
| 仅一侧有该事件 | 取该侧值 | `resolved`（`observed_in` 区分） | 补齐的主要形态 |
| 一侧有值、另一侧为空 | 取非空值 | `resolved_single_side` | 只有一个值，不存在二选一；仍留 `one_sided` 痕迹 |
| 两侧都有且不等 | **`null`** | `pending_conflict` | 并列保留，不选较有利值，交消费方裁决 |
| 两侧都为空 | `null` | `pending_missing` | 不补 0、不插值 |

`strict=True` 时「一侧有值、另一侧为空」也降级为 `pending_conflict`。
默认 `False`：只有一个值时不构成二选一，强行置 `null` 会把可算的成本变成缺口。
**这是本单的默认选择，不是唯一正确选择** —— 窗口四若要最大保守度，传 `strict=True`。

## 4 三条禁区（越界即结论无效）

1. **不得把 `availability=unproven` 说成「当时可得」。** 冷档没有 `available_at` 列，
   热库的 `fetched_at` 是入库时刻（本单实测缺口段内 `binance.fapi` 行的入库滞后
   中位数在数百小时量级）。历史补齐 ≠ 当时可得。
2. **不得把「合并后覆盖到窗口末端」说成「全窗口完整」。** 冷档实测覆盖到
   `{cold_end}` 为止（见 per_symbol.cold_hi_utc 的最大值），
   之后只能靠热库，没有第二个源。注意这个日期**不是**缺口段端点
   （缺口在 `{gap['缺口段_在库端点口径']}` 就结束了），两者相差近一个月，混用会算错。
3. **不得把本文件当作「策略通过」的证据。** 本单只处理资金费事件层，
   不涉及挂钟成交、不涉及绩效、不涉及冻结规则。

## 5 缺口段（两个口径，端点不可混用）

- **缺失端点口径**：`{gap['缺口段_缺失端点口径']}` —— 端点本身是「没有数据」的时刻
- **在库端点口径**：`{gap['缺口段_在库端点口径']}` —— 端点是「缺口两侧最后/最先有数据的时刻」

两者差一个 4h 步长，跨度都是 380h。拿缺失端点当「最后一条在库数据」会算错。
与 06-05 报的 `2026-09-02T20:00Z → 2026-09-18T16:00Z` 在**在库端点口径**下完全一致。

## 6 清单文件

`funding_events_母集48_manifest.json`：口径、计数、逐标的覆盖边界与 notes、
数据身份、已知限制。**不含时间戳**（生成时刻在 `04_运行时刻.json`），可字节复现。

## 7 前 8 个标的的覆盖（完整表见清单 per_symbol）

| 标的 | 事件 | both | 仅冷档 | 仅热库 | 冲突 | 两侧都缺 | 冷档末端 |
|---|---|---|---|---|---|---|---|
{rows}

## 8 复跑

```bash
python3 evidence/ACCUM-EARLY-06-06/scripts/04_产出事件文件与清单.py
```

事件文件 sha256 = `{manifest['files']['jsonl']['sha256']}`
"""
    (OUT / "接口说明.md").write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
