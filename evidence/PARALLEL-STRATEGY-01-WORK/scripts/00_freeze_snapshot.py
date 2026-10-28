#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""00 · 冻结快照：源码 / 配置 / 运行身份 / 账本一致性索引。

本脚本**只读**：源码只算哈希，账本与行情库都用 `mode=ro` 打开。
唯一写入是 `--out` 指定的本单证据目录。

用法：
  python3 00_freeze_snapshot.py --tag before
  python3 00_freeze_snapshot.py --tag after_reload

输出 `out/00_freeze_<tag>.json`，含：
  · 源码哈希（本单拟改文件 + 只读依赖）
  · 配置哈希（生效参数视图 / 冻结规则 / 闸门文件）
  · 运行身份（pid / 启动时刻 / 轮次 / 水位 / 健康）
  · 账本一致性（四臂 sha256 + 表行数 + 关键行指纹）
  · 局限声明（-wal 非空时 sha 不能证明零写入）
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "框架" / "backend"
EVID = Path(__file__).resolve().parents[1]

# 本单拟改（写）的文件
WRITE_TARGETS = [
    "框架/backend/paper_engine.py",
    "框架/backend/live_store.py",
    "框架/backend/unified_runtime.py",
]
# 只读依赖（本单不改，但结论依赖其行为）
READ_DEPENDENCIES = [
    "框架/backend/live_api.py",
    "框架/backend/service_manager.py",
    "框架/backend/live_signal_engine.py",
    "框架/backend/realtime_collector.py",
    "框架/backend/live_worker.py",
]
CONFIGS = [
    "数据/研究输入/最新唐奇安威科夫接通与收尾_2026-10-11/5000U收尾与回测条件终验6/"
    "10_研究参数生效视图_v2_2_user5000_shared.json",
    "框架/backend/live/paper_new_entry_gate.json",
]
FROZEN_RULE_CANDIDATES = [
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
]


def sha256(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def file_block(rel_paths):
    out = []
    for rel in rel_paths:
        p = ROOT / rel
        if not p.exists():
            out.append({"path": rel, "exists": False})
            continue
        st = p.stat()
        out.append({"path": rel, "exists": True,
                    "bytes": st.st_size,
                    "mtime_utc": iso(st.st_mtime * 1000),
                    "sha256": sha256(p)})
    return out


def runtime_identity():
    """运行身份：只认**当前活着的 pid** + 它自己写的状态文件。"""
    out = {"status_file": None, "identity": None, "pid_files": {}}
    for name in ("backend.pid", "runtime-3a-8787.pid", "worker.pid"):
        p = BACKEND / name
        if p.exists():
            try:
                pid = int(p.read_text().strip())
            except Exception:                                  # noqa: BLE001
                pid = None
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    alive = False
            out["pid_files"][name] = {"pid": pid, "alive": alive}
    st = BACKEND / "locks" / "paper_runtime_3a_status.json"
    if st.exists():
        out["status_file"] = {"path": str(st.relative_to(ROOT)),
                              "bytes": st.stat().st_size,
                              "mtime_utc": iso(st.stat().st_mtime * 1000),
                              "sha256": sha256(st)}
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            ri = d.get("runtime_identity", {}) or {}
            se = d.get("signal_engine", {}) or {}
            od = d.get("order_processing", {}) or {}
            ed = d.get("execution_daemon", {}) or {}
            out["identity"] = {
                "arm": ri.get("arm"), "pid": ri.get("pid"),
                "status": ri.get("status"), "overall_health": ri.get("overall_health"),
                "started_at_ms": ri.get("started_at"),
                "started_at_utc": iso(ri.get("started_at")),
                "cycle_count": ri.get("cycle_count"),
                "universe": se.get("universe"),
                "watermark_1h": se.get("watermark_1h"),
                "watermark_1h_utc": iso(se.get("watermark_1h")),
                "watermark_4h": se.get("watermark_4h"),
                "watermark_4h_utc": iso(se.get("watermark_4h")),
                "pending_in_db": od.get("current_pending_in_db"),
                "new_entry_gate": od.get("new_entry_gate"),
                "open_positions": ed.get("open_positions"),
                "pending_matched_total": ed.get("pending_matched_total"),
                "trailing_updates_total": ed.get("trailing_updates_total"),
                "exits_triggered_total": ed.get("exits_triggered_total"),
                "last_error": ed.get("last_error"),
            }
        except Exception as exc:                               # noqa: BLE001
            out["identity"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def ledger_block():
    """四臂账本一致性：sha + 行数 + 关键行指纹。

    ⚠ 局限：`-wal` 非 0 字节时，主文件 sha 不变**什么都不能证明**（写入还在 WAL 里）。
    因此同时记录 wal 大小，并在 `limitations` 里显式声明。
    """
    d = ROOT / "数据" / "paper"
    out = {"dir": str(d.relative_to(ROOT)), "arms": {}, "limitations": []}
    for arm in ("3a", "3b", "3c", "3d"):
        p = d / f"paper_{arm}.sqlite3"
        wal = d / f"paper_{arm}.sqlite3-wal"
        rec = {"exists": p.exists()}
        if p.exists():
            rec["sha256"] = sha256(p)
            rec["bytes"] = p.stat().st_size
            rec["mtime_utc"] = iso(p.stat().st_mtime * 1000)
            rec["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
            try:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
                tabs = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                counts = {}
                for t in tabs:
                    if t == "sqlite_sequence":
                        continue
                    try:
                        counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    except Exception:                          # noqa: BLE001
                        counts[t] = None
                rec["table_counts"] = counts
                # 关键行指纹：持仓与成交（不改写历史，只读摘要）
                try:
                    rows = list(con.execute(
                        "SELECT position_id,symbol,status,entry_price,entry_ts,exit_ts,"
                        "signal_uid,voided,market_source,origin FROM paper_position "
                        "ORDER BY position_id"))
                    rec["positions"] = [
                        {"position_id": r[0], "symbol": r[1], "status": r[2],
                         "entry_price": r[3], "entry_ts": r[4],
                         "entry_ts_utc": iso(r[4]), "exit_ts": r[5],
                         "signal_uid": r[6], "voided": r[7],
                         "market_source": r[8], "origin": r[9]} for r in rows]
                except Exception as exc:                       # noqa: BLE001
                    rec["positions"] = f"err:{type(exc).__name__}:{exc}"
                con.close()
            except Exception as exc:                           # noqa: BLE001
                rec["db_error"] = f"{type(exc).__name__}: {exc}"
        out["arms"][arm] = rec
        if rec.get("wal_bytes", 0) > 0:
            out["limitations"].append(
                f"paper_{arm}.sqlite3-wal = {rec['wal_bytes']} 字节（非 0）"
                f"⇒ 该臂主文件 sha256 不能作为零写入证明")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="before")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else EVID / "out" / f"00_freeze_{args.tag}.json"

    now_ms = int(dt.datetime.now(UTC).timestamp() * 1000)
    res = {
        "tag": args.tag,
        "generated_at_utc": iso(now_ms),
        "generated_at_ms": now_ms,
        "project_root": str(ROOT),
        "write_targets": file_block(WRITE_TARGETS),
        "read_dependencies": file_block(READ_DEPENDENCIES),
        "configs": file_block(CONFIGS),
        "frozen_rule_candidates": file_block(FROZEN_RULE_CANDIDATES),
        "runtime_identity": runtime_identity(),
        "ledger": ledger_block(),
        "env_subset": {k: os.environ.get(k) for k in (
            "PAPER_LEDGER_DIR", "MARKET_DB", "WX_MARKET_MERGE", "WX_ARCHIVE_DIR",
            "PAPER_GATE_STRICT", "WX_HISTORY_SOURCE", "WX_REALITY_FILL_MODE")},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out_path}")
    for b in ("write_targets", "configs"):
        for f in res[b]:
            print(f"  {b:16s} {f['path']}  sha={str(f.get('sha256'))[:16]}  "
                  f"mtime={f.get('mtime_utc')}")
    ri = res["runtime_identity"].get("identity") or {}
    print(f"  runtime pid={ri.get('pid')} started={ri.get('started_at_utc')} "
          f"cycle={ri.get('cycle_count')} health={ri.get('overall_health')}")
    for a, v in res["ledger"]["arms"].items():
        print(f"  ledger {a}: sha={str(v.get('sha256'))[:16]} wal={v.get('wal_bytes')} "
              f"pos={v.get('table_counts', {}).get('paper_position')}")
    if res["ledger"]["limitations"]:
        for x in res["ledger"]["limitations"]:
            print("  ⚠", x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
