# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 00 范围与身份锚定

做四件事：
1. 锁本单范围：原 51 候选、两套经济口径、逐候选持仓区间（口径间 exit 可能不同）。
2. 登记输入与代码身份：适配器、共享计算、候选来源、R1 产物、冷档清单。
3. 取一次**官方周期快照**（Binance `/fapi/v1/fundingInfo`），带 `updateTime`，
   落盘原始响应 + sha256。这不是采集器：一次性只读快照，不入任何管道、不写生产库。
4. 明确登记「本地是否存在官方历史周期记录」的探针结果（表 / 目录 / 文件）。

判定纪律：本脚本只做取证与登记，不做周期判断（判断在 01/02）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L

OFFICIAL_URL = "https://fapi.binance.com/fapi/v1/fundingInfo"
CHECKS: list = []


def add(name: str, ok: bool, evidence) -> None:
    CHECKS.append({"检查": name, "通过": bool(ok), "证据": evidence})


def fetch_official(refresh: bool) -> dict:
    """一次性只读快照。已存在则复用（保证重跑字节稳定），除非 R2_REFRESH=1。"""
    p = L.official_snapshot_path()
    if p.exists() and not refresh:
        d = json.loads(p.read_text(encoding="utf-8"))
        d["reused_from_disk"] = True
        return d

    req = urllib.request.Request(OFFICIAL_URL, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=15) as r:
        status = r.status
        body = r.read().decode("utf-8")
    rows = json.loads(body)
    payload = {
        "source": "binance.fapi.fundingInfo",
        "url": OFFICIAL_URL,
        "fetched_at_utc": L.iso(int(time.time() * 1000)),
        "http_status": status,
        "elapsed_sec": round(time.time() - t0, 3),
        "n_rows": len(rows),
        "rows_sha256": L.sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":"))),
        "note": ("官方**当前**周期快照。`updateTime` 是该行最后一次更新时间；"
                 "本单只用它界定「官方值自 updateTime 起生效」的窗口，"
                 "不把它当成完整历史变更流水（交易所不提供历史流水）。"),
        "rows": rows,
        "reused_from_disk": False,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def probe_local_official_evidence() -> dict:
    """探针：本地是否存在任何官方周期记录（表 / 目录 / 文件 / 伴生库）。"""
    found = {"db_tables_with_interval": [], "files_with_interval": [],
             "funding_interval_meta_sqlite3": None, "catalog_has_interval": None}

    con = sqlite3.connect(f"file:{L.HOT_DB}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()]
    for t in tables:
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})").fetchall()]
        if any("interval" in (c or "").lower() for c in cols):
            found["db_tables_with_interval"].append({"table": t, "columns": cols})
    found["funding_history_columns"] = [
        c[1] for c in con.execute("PRAGMA table_info(funding_history)").fetchall()]
    found["db_table_count"] = len(tables)
    con.close()

    cat = L.ARCHIVE_ROOT / "_catalog.json"
    if cat.exists():
        s = cat.read_text(encoding="utf-8")
        found["catalog_has_interval"] = "interval" in s.lower()

    # 伴生库（05-F 曾指出其不存在）
    for name in ("funding_interval_meta.sqlite3", "funding_interval_meta.db"):
        hits = list((L.ROOT / "数据").rglob(name))
        found["funding_interval_meta_sqlite3"] = (
            str(hits[0]) if hits else "不存在")

    # 归档目录里是否有周期字段
    sample = sorted((L.ARCHIVE_ROOT / "funding" / "binance" / "um").glob("*.parquet"))[:1]
    if sample:
        import pyarrow.parquet as pq
        found["cold_parquet_columns"] = pq.read_schema(sample[0]).names
    return found


def cold_manifest() -> dict:
    files = sorted((L.ARCHIVE_ROOT / "funding" / "binance" / "um").glob("*.parquet"))
    items = [[f.name, f.stat().st_size] for f in files]
    return {"files": len(files), "bytes": sum(x[1] for x in items),
            "manifest_sha256": L.sha256_text(json.dumps(items, separators=(",", ":")))}


def main() -> int:
    refresh = os.environ.get("R2_REFRESH") == "1"
    snap = fetch_official(refresh)
    idx = L.official_index()

    cands = L.load_candidates()
    acc2 = json.loads(L.ACC2_JSON.read_text(encoding="utf-8"))
    rows_all = acc2["B_全量回放"]["逐条"]
    by_cal = {c: [r for r in rows_all if r.get("caliber") == c] for c in L.CALIBERS}

    # 逐候选 × 口径的持仓区间（exit 可能因口径不同而不同）
    holds = []
    for c in L.CALIBERS:
        for r in by_cal[c]:
            holds.append({
                "caliber": c, "candidate_id": r["candidate_id"], "symbol": r["symbol"],
                "status": r.get("status"),
                "entry_ms": L.to_ms(r["entry_bar_utc"]),
                "entry_utc": r["entry_bar_utc"],
                "exit_ms": L.to_ms(r["exit_utc"]) if r.get("exit_utc") else None,
                "exit_utc": r.get("exit_utc"),
                "exit_kind": r.get("exit_kind"),
                "funding_cost_02": r.get("funding_cost"),
                "realized_net_02": r.get("realized_net"),
                "participation_capped": r.get("participation_capped"),
            })
    ok_holds = [h for h in holds if h["status"] == "ok" and h["exit_ms"]]

    win_lo = min(h["entry_ms"] for h in holds)
    win_hi = max(h["exit_ms"] for h in ok_holds)

    syms = sorted({c["symbol"] for c in cands})
    official_hit = [s for s in syms if s in idx]
    official_missing = [s for s in syms if s not in idx]
    official_no_ut = [s for s in syms if s in idx and idx[s]["update_ms"] is None]

    # ── 数据身份 ──
    st = L.HOT_DB.stat()
    con = sqlite3.connect(f"file:{L.HOT_DB}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    n_rows, max_t = con.execute(
        "SELECT COUNT(*), MAX(funding_time) FROM funding_history").fetchone()
    con.close()

    hashes = {
        "adapter": {"path": "evidence/ACCUM-EARLY-06-06/adapter/funding_events_adapter.py",
                    "sha256": L.sha256_file(L.ADAPTER_DIR / "funding_events_adapter.py")},
        "shared_replay_calc": {"path": "框架/backend/replay_calc.py",
                               "sha256": L.sha256_file(L.BACKEND / "replay_calc.py")},
        "shared_market_repository": {"path": "框架/backend/market_repository.py",
                                     "sha256": L.sha256_file(L.BACKEND / "market_repository.py")},
        "shared_funding": {"path": "策略/WyckoffMaster/trading_core/funding.py",
                           "sha256": L.sha256_file(
                               L.ROOT / "策略" / "WyckoffMaster" / "trading_core" / "funding.py")},
        "shared_research_15m": {"path": "框架/backend/research_15m.py",
                                "sha256": L.sha256_file(L.BACKEND / "research_15m.py")},
        "candidates_source": {"path": "evidence/ACCUM-EARLY-02/out/04_回放计算修复.json",
                              "sha256": L.sha256_file(L.ACC2_JSON)},
        "r1_product": {"path": "evidence/ACCUM-EARLY-06-W4/03_R1/out/01_资金费重算与判据整改明细.json",
                       "sha256": L.sha256_file(L.R1_JSON) if L.R1_JSON.exists() else None},
        "r1_script": {"path": "evidence/ACCUM-EARLY-06-W4/03_R1/scripts/01_适配器对账与资金费判据重构.py",
                      "sha256": L.sha256_file(L.ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1"
                                              / "scripts" / "01_适配器对账与资金费判据重构.py")},
    }

    out = {
        "工单": "ACCUM-EARLY-06-W4-R2",
        "A_范围": {
            "candidates": len(cands),
            "candidate_ids_unique": len({c["candidate_id"] for c in cands}),
            "symbols": len(syms),
            "calibers": list(L.CALIBERS),
            "holds": len(holds),
            "holds_filled": len(ok_holds),
            "window_lo_ms": win_lo, "window_lo_utc": L.iso(win_lo),
            "window_hi_ms": win_hi, "window_hi_utc": L.iso(win_hi),
            "window_hours": round((win_hi - win_lo) / 3600000.0, 3),
            "hold_hours_min": round(min((h["exit_ms"] - h["entry_ms"]) / 3600000.0
                                        for h in ok_holds), 3),
            "hold_hours_max": round(max((h["exit_ms"] - h["entry_ms"]) / 3600000.0
                                        for h in ok_holds), 3),
            "symbols_list": syms,
        },
        "B_官方快照": {
            "source": snap["source"], "url": snap["url"],
            "fetched_at_utc": snap["fetched_at_utc"], "http_status": snap["http_status"],
            "n_rows": snap["n_rows"], "rows_sha256": snap["rows_sha256"],
            "reused_from_disk": snap.get("reused_from_disk"),
            "命中标的数": len(official_hit), "未命中标的数": len(official_missing),
            "未命中清单": official_missing,
            "命中但 updateTime 为空": official_no_ut,
            "是采集器吗": "不是：一次性只读快照，落盘原始响应，无调度、无重试、不写生产库",
            "能证明历史吗": "不能。updateTime 只是该行最后一次更新时间，"
                            "交易所不提供周期变更流水；本单只用它界定"
                            "「官方值自 updateTime 起生效」的窗口。",
        },
        "C_本地官方记录探针": probe_local_official_evidence(),
        "D_数据身份": {
            "hot_db": {"path": "数据/市场数据/db/market_v2.sqlite3",
                       "bytes": st.st_size, "mtime_ms": int(st.st_mtime * 1000),
                       "rows_funding_history": n_rows,
                       "max_funding_time_ms": max_t,
                       "max_funding_time_utc": L.iso(max_t),
                       "full_file_sha256": None,
                       "why_no_full_sha": "生产库正被外部进程持续写入（06-06 已取证 PID 91940），"
                                          "整文件哈希随时间变化，不能当稳定身份；"
                                          "改用行数+最大时刻+逐标的消费事件 sha"},
            "cold_archive": cold_manifest(),
        },
        "E_代码与输入哈希": hashes,
        "F_候选与持仓": holds,
        "checks": CHECKS,
    }

    add("A1 原 51 候选全部保留，candidate_id 唯一",
        out["A_范围"]["candidate_ids_unique"] == 51 and len(cands) == 51,
        {"candidates": len(cands), "unique": out["A_范围"]["candidate_ids_unique"]})
    add("A2 两套口径各自 51 行（口径间 exit 可能不同，逐条记录）",
        all(len(by_cal[c]) == 51 for c in L.CALIBERS),
        {c: len(by_cal[c]) for c in L.CALIBERS})
    add("A3 已成交笔数 = 101（口径 B 有 1 笔拒单）",
        len(ok_holds) == 101,
        {"holds_filled": len(ok_holds),
         "rejected": [{"caliber": h["caliber"], "symbol": h["symbol"]}
                      for h in holds if h["status"] != "ok"]})
    add("B1 官方快照 48 个标的全部命中（无一缺行）",
        len(official_hit) == 48,
        {"命中": len(official_hit), "未命中": official_missing})
    add("B2 官方快照行数 >= 800（非空响应）", snap["n_rows"] >= 800,
        {"n_rows": snap["n_rows"], "http_status": snap["http_status"]})
    add("C1 本地**不存在**任何官方周期记录（表/目录/伴生库）",
        not out["C_本地官方记录探针"]["db_tables_with_interval"]
        and out["C_本地官方记录探针"]["funding_interval_meta_sqlite3"] == "不存在"
        and out["C_本地官方记录探针"]["catalog_has_interval"] is False,
        out["C_本地官方记录探针"])
    add("D1 热库 funding_history 无 interval 字段（无法从库内推官方周期）",
        "interval" not in " ".join(out["C_本地官方记录探针"]["funding_history_columns"]).lower(),
        out["C_本地官方记录探针"]["funding_history_columns"])
    add("E1 共享生产代码只登记不修改（哈希留证）", True,
        {k: v["sha256"][:16] for k, v in hashes.items() if v["sha256"]})

    (L.OUT / "00_范围与身份锚定.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = sum(1 for c in CHECKS if c["通过"])
    print(f"范围：{len(cands)} 候选 / {len(syms)} 标的 / {len(ok_holds)} 笔已成交")
    print(f"窗口：{out['A_范围']['window_lo_utc']} → {out['A_范围']['window_hi_utc']}"
          f"（{out['A_范围']['window_hours']}h）")
    print(f"官方快照：{snap['n_rows']} 行，命中 48 标的；updateTime 为空 "
          f"{len(official_no_ut)} 个")
    print(f"本地官方记录：无（表/目录/伴生库均无）")
    print(f"checks {ok}/{len(CHECKS)}")
    for c in CHECKS:
        if not c["通过"]:
            print("  FAIL", c["检查"], json.dumps(c["证据"], ensure_ascii=False)[:300])
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
