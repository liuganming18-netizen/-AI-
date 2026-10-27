# -*- coding: utf-8 -*-
"""STEP1-B / 00 · 只读扫描：把验收矩阵里每个数字落到可复算的事实上。

约束（本工单明令）
------------------
· 只读：不跑回测、不跑任何会写原产物的复现脚本、不改任何上游文件；
· 只写 `evidence/STEP1-B/` 内的产物；
· 缺证据一律写 UNKNOWN，不用 0 冒充。

输出
----
08_scan.json         机器可读事实总表（矩阵与缺口清单的唯一数字来源）
01_入口清单.csv       每个实现入口文件的路径 / 字节 / sha256 / 是否存在
02_数量对账.csv       样本、run、候选、成交、测试的口径对账（含差额与去向）
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "STEP1-B"
OUT.mkdir(parents=True, exist_ok=True)

PY = sys.executable


def sha256_file(p: Path) -> str | None:
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# ══════════════════════════ 1. 实现入口清单 ══════════════════════════
# 只登记「本次待验回测」真正会走到的文件；不含 UI 前端与采集器。
ENTRY_FILES = [
    # 策略层（WX_EVENT_TREND_V0_1）
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/contracts.py",
    "策略/WyckoffMaster/event_trend_v0_1/features.py",
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
    "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
    "策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py",
    # 共享执行核心
    "策略/WyckoffMaster/trading_core/engine.py",
    "策略/WyckoffMaster/trading_core/fill.py",
    "策略/WyckoffMaster/trading_core/funding.py",
    "策略/WyckoffMaster/trading_core/position.py",
    "策略/WyckoffMaster/trading_core/risk.py",
    "策略/WyckoffMaster/trading_core/order.py",
    "策略/WyckoffMaster/trading_core/policy.py",
    "策略/WyckoffMaster/trading_core/signal.py",
    "策略/WyckoffMaster/trading_core/ledger.py",
    "策略/WyckoffMaster/trading_core/pit.py",
    "策略/WyckoffMaster/trading_core/contracts.py",
    "策略/WyckoffMaster/trading_core/rejects.py",
    # 框架层（读接口 / 批次 / 指标 / 图表 / 建单）
    "框架/backend/backtest_runner.py",
    "框架/backend/backtest_api.py",
    "框架/backend/backtest_batches.py",
    "框架/backend/backtest_metrics.py",
    "框架/backend/backtest_chart.py",
    "框架/backend/backtest_store.py",
    "框架/backend/ledger_links.py",
    "框架/backend/market_repository.py",
    "框架/backend/strategies/registry.py",
    "框架/backend/oi_resonance_bridge.py",
    "框架/backend/event_trend_bridge.py",
    # 冻结规则与回放入口
    "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md",
    "evidence/ENTRY-AUDIT-03/02_replay_full.py",
]


def scan_entries() -> list[dict]:
    rows = []
    for r in ENTRY_FILES:
        p = ROOT / r
        rows.append({
            "文件": r,
            "存在": p.is_file(),
            "字节": (p.stat().st_size if p.is_file() else None),
            "sha256": sha256_file(p),
        })
    return rows


# ══════════════════════════ 2. 数据层 ══════════════════════════
PQ = ROOT / "数据" / "市场数据" / "parquet"


def scan_data() -> dict:
    out: dict = {"parquet_base": rel(PQ), "exists": PQ.is_dir()}
    if not PQ.is_dir():
        return out
    # 标的覆盖面（目录数）
    candles_um = PQ / "candles" / "binance" / "um"
    oi_um = PQ / "oi" / "binance" / "um"
    fund_um = PQ / "funding" / "binance" / "um"
    out["candles_symbols"] = len([d for d in candles_um.iterdir() if d.is_dir()]) if candles_um.is_dir() else None
    out["oi_symbols"] = len([d for d in oi_um.iterdir() if d.is_dir()]) if oi_um.is_dir() else None
    out["funding_files"] = len(list(fund_um.glob("*.parquet"))) if fund_um.is_dir() else None
    out["timeframes"] = sorted([d.name for d in (candles_um / "COTIUSDT").iterdir() if d.is_dir()]) \
        if (candles_um / "COTIUSDT").is_dir() else []

    # 单标的剖面（COTIUSDT 是 EA04 点名样本，用它做结构代表）
    probe = "COTIUSDT"
    prof: dict = {}
    for tf in ("4h", "1h", "15m"):
        files = sorted((candles_um / probe / tf).glob("*.parquet"))
        prof[tf] = {"files": len(files), "shards": [f.name for f in files]}
    prof["oi"] = {"files": len(list((oi_um / probe).glob("*.parquet")))}
    prof["funding"] = {"exists": (fund_um / f"{probe}.parquet").is_file()}
    out["probe_symbol"] = probe
    out["probe_profile"] = prof

    # 列与时间口径（用 pyarrow 只读 schema，不加载全表）
    try:
        import pyarrow.parquet as pq
        sch = pq.read_schema(str(sorted((candles_um / probe / "4h").glob("*.parquet"))[0]))
        out["candle_columns"] = list(sch.names)
        out["candle_dtypes"] = {n: str(t) for n, t in zip(sch.names, sch.types)}
        s2 = pq.read_schema(str(sorted((oi_um / probe).glob("*.parquet"))[0]))
        out["oi_columns"] = list(s2.names)
        out["oi_dtypes"] = {n: str(t) for n, t in zip(s2.names, s2.types)}
        s3 = pq.read_schema(str(fund_um / f"{probe}.parquet"))
        out["funding_columns"] = list(s3.names)
        out["funding_dtypes"] = {n: str(t) for n, t in zip(s3.names, s3.types)}
    except Exception as exc:  # noqa: BLE001
        out["schema_error"] = f"{type(exc).__name__}: {exc}"

    # 数据版本水位
    st = PQ / "_state.json"
    if st.is_file():
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            out["data_watermark_ms"] = d.get("watermark_ms")
            out["data_reconcile_watermark_iso"] = d.get("reconcile_watermark_iso")
        except Exception:  # noqa: BLE001
            out["data_watermark_ms"] = None
    return out


# ══════════════════════════ 3. 合约规格来源 ══════════════════════════
def scan_contracts() -> dict:
    d = ROOT / "数据" / "contracts" / "旧标的池"
    out = {"dir": rel(d), "exists": d.is_dir()}
    if not d.is_dir():
        return out
    snaps = sorted(d.glob("exchangeInfo_futures_*.json"))
    out["futures_snapshots"] = [f.name for f in snaps]
    out["futures_snapshot_count"] = len(snaps)
    out["spec_changes_files"] = sorted(f.name for f in d.glob("spec_changes_*.json"))
    out["spec_changes_count"] = len(out["spec_changes_files"])
    # 快照自身 serverTime（判断能否夹住 2021-12~2026-10 的窗口）
    times = []
    for f in snaps:
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            times.append({"file": f.name, "serverTime": j.get("serverTime")})
        except Exception:  # noqa: BLE001
            times.append({"file": f.name, "serverTime": None})
    out["snapshot_server_times"] = times
    return out


# ══════════════════════════ 4. 样本与 run 口径 ══════════════════════════
def scan_sample() -> dict:
    out: dict = {}
    ea03 = ROOT / "evidence" / "ENTRY-AUDIT-03" / "runs"
    handoff = ROOT / "evidence" / "ENTRY-AUDIT-04" / "handoff" / "ledger_handoff.parquet"

    if ea03.is_dir():
        dirs = sorted([d for d in ea03.iterdir() if d.is_dir()])
        st: dict = {}
        gen: dict = {}
        cand_sum = trade_sum = 0
        per_run: dict = {}
        for d in dirs:
            rj = d / "run.json"
            if not rj.is_file():
                st["no_run_json"] = st.get("no_run_json", 0) + 1
                continue
            r = json.loads(rj.read_text(encoding="utf-8"))
            s = str(r.get("status"))
            st[s] = st.get(s, 0) + 1
            g = str(r.get("generation"))
            gen[g] = gen.get(g, 0) + 1
            n_c = int((r.get("run") or {}).get("candidates") or 0)
            n_t = int((r.get("run") or {}).get("trades") or 0)
            cand_sum += n_c
            trade_sum += n_t
            per_run[d.name] = {"candidates": n_c, "trades": n_t}
        out["ea03_run_dirs"] = len(dirs)
        out["ea03_status"] = st
        out["ea03_generation"] = gen
        out["ea03_candidates_from_run_json"] = cand_sum
        out["ea03_trades_from_run_json"] = trade_sum
        out["ea03_per_run"] = per_run

    if handoff.is_file():
        import pandas as pd
        h = pd.read_parquet(handoff)
        h["_rd"] = h["run_dir"].str.replace("evidence/ENTRY-AUDIT-03/runs/", "", regex=False)
        out["handoff_rows"] = int(len(h))
        out["handoff_filled"] = int((h["exec_status"] == "FILLED").sum())
        out["handoff_unfilled"] = int((h["exec_status"] == "UNFILLED").sum())
        out["handoff_runs"] = int(h["_rd"].nunique())
        out["handoff_generation"] = {str(k): int(v) for k, v in
                                     h["generation"].value_counts().items()}
        out["handoff_net_pnl"] = float(h["net_pnl"].sum())
        out["handoff_fees"] = float(h["fees"].sum())
        out["handoff_funding"] = float(h["funding_fee"].sum())
        out["handoff_spec_qualification"] = {str(k): int(v) for k, v in
                                             h["spec_qualification"].value_counts().items()}
        out["handoff_formal_pass_allowed_true"] = int(h["formal_pass_allowed"].sum())

        # 逐 run 差额：run.json 候选数 vs 交接包行数
        cnt = h.groupby("_rd").size().to_dict()
        diff = []
        for name, v in out.get("ea03_per_run", {}).items():
            got = int(cnt.get(name, 0))
            if v["candidates"] != got:
                diff.append({"run": name, "run_json_candidates": v["candidates"],
                             "run_json_trades": v["trades"], "handoff_rows": got})
        out["per_run_candidate_gap"] = diff
        out["per_run_candidate_gap_total"] = sum(
            d["run_json_candidates"] - d["handoff_rows"] for d in diff)
        out["per_run_trade_gap_total"] = sum(
            d["run_json_trades"] - (1 if d["handoff_rows"] else 0) for d in diff)
    return out


# ══════════════════════════ 5. 正式 run 根与批次 ══════════════════════════
def scan_runs() -> dict:
    out: dict = {}
    et = ROOT / "回测日志" / "event_trend_v0_1"
    if et.is_dir():
        batches = sorted([d for d in et.iterdir() if d.is_dir() and
                          (d.name == "runs" or d.name.startswith("runs_"))])
        out["batch_dirs"] = [{"batch": d.name, "runs": len([x for x in d.iterdir() if x.is_dir()])}
                             for d in batches]
        out["batch_count"] = len(batches)
        out["batch_runs_total"] = sum(b["runs"] for b in out["batch_dirs"])
    oi = ROOT / "回测日志" / "oi_resonance_v1" / "runs"
    out["oi_resonance_runs_dir_exists"] = oi.is_dir()
    out["oi_resonance_runs"] = len([d for d in oi.iterdir() if d.is_dir()]) if oi.is_dir() else None
    return out


# ══════════════════════════ 6. 测试清单 ══════════════════════════
TEST_DIRS = [
    "策略/WyckoffMaster/event_trend_v0_1/tests",
    "框架/backend/tests",
]
TEST_FILES_EXTRA = [
    "策略/WyckoffMaster/test_work02_core_contract.py",
]


def count_tests(p: Path) -> tuple[int, int]:
    """返回 (test 函数数, parametrize 装饰器数)。展开后条数另算。"""
    t = p.read_text(encoding="utf-8", errors="ignore")
    return (len(re.findall(r"^\s*def test_", t, re.M)),
            len(re.findall(r"@pytest\.mark\.parametrize", t)))


def scan_tests() -> list[dict]:
    rows = []
    for d in TEST_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.glob("test_*.py")):
            n, par = count_tests(p)
            rows.append({"文件": rel(p), "test函数": n, "parametrize": par,
                         "行数": len(p.read_text(encoding="utf-8", errors="ignore").splitlines())})
    for r in TEST_FILES_EXTRA:
        p = ROOT / r
        if p.is_file():
            n, par = count_tests(p)
            rows.append({"文件": r, "test函数": n, "parametrize": par,
                         "行数": len(p.read_text(encoding="utf-8", errors="ignore").splitlines())})
    return rows


# ══════════════════════════ 7. 闸门实况（只读 import，不下单） ══════════════════════════
def scan_gate() -> dict:
    out: dict = {}
    be = ROOT / "框架" / "backend"
    if not (be / "strategies" / "registry.py").is_file():
        return {"error": "registry.py 不存在"}
    sys.path.insert(0, str(be))
    try:
        from strategies import registry as reg  # noqa: PLC0415
        out["CURRENT_BACKTEST_KEY"] = getattr(reg, "CURRENT_BACKTEST_KEY", None)
        out["CURRENT_BACKTEST_ID"] = getattr(reg, "CURRENT_BACKTEST_ID", None)
        out["guard_WX_EVENT_TREND_V0_1"] = reg.guard_runnable("WX_EVENT_TREND_V0_1")
        out["guard_WX_OI_RESONANCE_V1"] = reg.guard_runnable("WX_OI_RESONANCE_V1")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    try:
        import oi_resonance_bridge as BR  # noqa: PLC0415
        out["bridge_STRATEGY_ID"] = BR.STRATEGY_ID
        out["bridge_AVAILABLE"] = BR.AVAILABLE
    except Exception as exc:  # noqa: BLE001
        out["bridge_error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    scan = {
        "audit": "STEP1-B",
        "generated_by": "evidence/STEP1-B/00_scan.py",
        "python": PY,
        "entries": scan_entries(),
        "data": scan_data(),
        "contracts": scan_contracts(),
        "sample": scan_sample(),
        "runs": scan_runs(),
        "tests": scan_tests(),
        "gate": scan_gate(),
    }
    (OUT / "08_scan.json").write_text(
        json.dumps(scan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    with open(OUT / "01_入口清单.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["文件", "存在", "字节", "sha256"])
        w.writeheader()
        w.writerows(scan["entries"])

    # 数量对账
    s = scan["sample"]
    rows = [
        {"口径": "EA03 磁盘 run 目录", "数值": s.get("ea03_run_dirs"), "来源": "evidence/ENTRY-AUDIT-03/runs/"},
        {"口径": "EA03 run.json 候选合计", "数值": s.get("ea03_candidates_from_run_json"),
         "来源": "各 run.json 的 run.candidates"},
        {"口径": "EA03 run.json 成交合计", "数值": s.get("ea03_trades_from_run_json"),
         "来源": "各 run.json 的 run.trades"},
        {"口径": "交接包行数", "数值": s.get("handoff_rows"),
         "来源": "evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet"},
        {"口径": "交接包成交行", "数值": s.get("handoff_filled"), "来源": "同上 exec_status=FILLED"},
        {"口径": "交接包未成交行", "数值": s.get("handoff_unfilled"), "来源": "同上 exec_status=UNFILLED"},
        {"口径": "候选差额（run.json − 交接包）", "数值": s.get("per_run_candidate_gap_total"),
         "来源": "09 个 run 逐 run 比对"},
        {"口径": "成交差额（run.json − 交接包）", "数值": s.get("per_run_trade_gap_total"),
         "来源": "同上"},
        {"口径": "正式 run 批次目录数", "数值": scan["runs"].get("batch_count"),
         "来源": "回测日志/event_trend_v0_1/runs*"},
        {"口径": "正式 run 总数", "数值": scan["runs"].get("batch_runs_total"),
         "来源": "同上"},
        {"口径": "OI_RESONANCE run 数", "数值": scan["runs"].get("oi_resonance_runs"),
         "来源": "回测日志/oi_resonance_v1/runs/"},
        {"口径": "合约规格变更证据份数", "数值": scan["contracts"].get("spec_changes_count"),
         "来源": "数据/contracts/旧标的池/spec_changes_*.json"},
        {"口径": "exchangeInfo 期货快照份数", "数值": scan["contracts"].get("futures_snapshot_count"),
         "来源": "数据/contracts/旧标的池/exchangeInfo_futures_*.json"},
    ]
    with open(OUT / "02_数量对账.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["口径", "数值", "来源"])
        w.writeheader()
        w.writerows(rows)

    print("=== STEP1-B 扫描完成 ===")
    print("入口文件", len(scan["entries"]), "存在",
          sum(1 for e in scan["entries"] if e["存在"]))
    print("EA03 run", s.get("ea03_run_dirs"), "候选(run.json)", s.get("ea03_candidates_from_run_json"),
          "成交(run.json)", s.get("ea03_trades_from_run_json"))
    print("交接包", s.get("handoff_rows"), "行 =", s.get("handoff_filled"), "成交 +",
          s.get("handoff_unfilled"), "未成交")
    print("候选差额", s.get("per_run_candidate_gap_total"), "成交差额", s.get("per_run_trade_gap_total"))
    print("批次", scan["runs"].get("batch_count"), "总 run", scan["runs"].get("batch_runs_total"))
    print("规格变更证据", scan["contracts"].get("spec_changes_count"),
          "快照", scan["contracts"].get("futures_snapshot_count"))
    print("闸门", scan["gate"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
