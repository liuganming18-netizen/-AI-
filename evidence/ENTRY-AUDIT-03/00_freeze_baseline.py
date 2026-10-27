# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-03 / 00 · 冻结基线：策略代码、配置、合约规格、数据、时间窗口

只读扫描，不改任何被扫描文件。输出全部写入 evidence/ENTRY-AUDIT-03/。

产出
----
00_freeze_manifest.json  全部被扫描文件的 sha256 / 大小 / 修改时间（行情另含历史切片指纹）
00_freeze_manifest.md    人读摘要
00_config_fingerprint.json  配置参数全量序列化 + 指纹 + 回放规则事实声明
00_window_plan.json      两个代际各自的时间窗口定义（163 连续 / 42 窗口），禁止拼接

设计要点
--------
· 行情 `live.parquet` 会被采集器持续追加 ⇒ 整文件哈希必然变化。
  因此额外登记「已闭合历史切片指纹」（只依赖 open_time/high/low/close 行内容），
  收尾自检时按「open_time <= 冻结时最大值」重算比对，证明历史区间未被改写。
· 合约规格证据目录只有两份 exchangeInfo 快照、零份 spec_changes_*.json，
  这一事实本身要落盘（它决定「合约规格资格」全部不是 verified）。
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-03"
OUT.mkdir(parents=True, exist_ok=True)
PARQUET = ROOT / "数据" / "市场数据" / "parquet"
CONTRACTS = ROOT / "数据" / "contracts" / "旧标的池"

FROZEN = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
SYMBOLS = sorted(FROZEN["symbol"].unique().tolist())


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hist_digest(p: str) -> dict:
    """行情文件的「已闭合历史切片」指纹（open_time/high/low/close 行内容）。"""
    try:
        d = pd.read_parquet(p, columns=["open_time", "high", "low", "close"])
    except Exception as exc:
        return {"rows": None, "min_open_time": None, "max_open_time": None,
                "hist_digest": None, "hist_error": str(exc)[:120]}
    if d.empty:
        return {"rows": 0, "min_open_time": None, "max_open_time": None, "hist_digest": None}
    d = d.sort_values("open_time")
    h = hashlib.sha256()
    h.update(d["open_time"].to_numpy("int64").tobytes())
    for c in ("high", "low", "close"):
        h.update(pd.to_numeric(d[c], errors="coerce").to_numpy("float64").tobytes())
    return {"rows": int(len(d)), "min_open_time": int(d["open_time"].min()),
            "max_open_time": int(d["open_time"].max()), "hist_digest": h.hexdigest()}


def rec_one(job) -> dict:
    path, category, with_hist = job
    p = Path(path)
    st = p.stat()
    out = {
        "category": category,
        "path": str(p.relative_to(ROOT)),
        "size": st.st_size,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": sha256_file(path),
    }
    if with_hist:
        out.update(hist_digest(path))
    return out


def main() -> None:
    # ── 1. 被审计的上游研究产物（读入即冻结，不改） ──
    upstream = [
        "evidence/FACTOR-ENTRY-01/candidate_features.parquet",
        "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.csv",
        "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.jsonl",
        "evidence/FACTOR-ENTRY-01/stage2_candidate_feature_pipeline.py",
        "evidence/FACTOR-ENTRY-01/enrich_unified_candidate_dataset.py",
        "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet",
        "evidence/FACTOR-ENTRY-04/unfilled_reasons.json",
        "evidence/FACTOR-ENTRY-04/candidate_evaluations_audit.parquet",
        "evidence/FACTOR-ENTRY-05/reconstructed_candidate_features.parquet",
        "evidence/FULL_SWEEP_200x100/trades_rich.jsonl",
        "evidence/FULL_SWEEP_200x100/trades.jsonl",
        "evidence/FULL_SWEEP_200x100/runs.jsonl",
        "evidence/FULL_SWEEP_200x100/plan.json",
        "run_full_sweep_200x100.py",
        # 上一轮审计结论（本轮不推翻、只在其上扩展）
        "evidence/ENTRY-AUDIT-02/报告.md",
        "evidence/ENTRY-AUDIT-02/01_counts.json",
        "evidence/ENTRY-AUDIT-02/01_mapping_227.csv",
    ]
    # ── 2. 策略代码：决定信号、撮合、费用、拒单的每一个文件 ──
    code = [
        "策略/WyckoffMaster/event_trend_v0_1/config.py",
        "策略/WyckoffMaster/event_trend_v0_1/contracts.py",
        "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
        "策略/WyckoffMaster/event_trend_v0_1/features.py",
        "策略/WyckoffMaster/event_trend_v0_1/runner.py",
        "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
        "策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py",
        "策略/WyckoffMaster/trading_core/__init__.py",
        "策略/WyckoffMaster/trading_core/engine.py",
        "策略/WyckoffMaster/trading_core/policy.py",
        "策略/WyckoffMaster/trading_core/fill.py",
        "策略/WyckoffMaster/trading_core/risk.py",
        "策略/WyckoffMaster/trading_core/rejects.py",
        "策略/WyckoffMaster/trading_core/ledger.py",
        "策略/WyckoffMaster/trading_core/position.py",
        "策略/WyckoffMaster/trading_core/funding.py",
        "策略/WyckoffMaster/trading_core/signal.py",
        "策略/WyckoffMaster/trading_core/pit.py",
    ]

    jobs = []
    for rel in upstream:
        p = ROOT / rel
        (jobs.append((str(p), "upstream", False)) if p.exists() else print(f"[缺失-上游] {rel}"))
    for rel in code:
        p = ROOT / rel
        (jobs.append((str(p), "code", False)) if p.exists() else print(f"[缺失-代码] {rel}"))

    # ── 3. 合约规格证据（决定「规格资格」） ──
    for p in sorted(CONTRACTS.glob("*.json")):
        jobs.append((str(p), "contracts", False))
    for p in sorted(CONTRACTS.glob("*.md")):
        jobs.append((str(p), "contracts", False))

    # ── 4. 行情：152 标的的 15m/1h/4h（重放读）+ oi（信号读）+ funding（资金费读）+ metrics（特征读） ──
    n_by_cat = {"15m": 0, "1h": 0, "4h": 0, "oi": 0, "funding": 0, "metrics": 0}
    for sym in SYMBOLS:
        for tf in ("15m", "1h", "4h"):
            d = PARQUET / "candles/binance/um" / sym / tf
            if not d.exists():
                print(f"[缺失行情] {sym}/{tf}")
                continue
            for f in sorted(d.glob("*.parquet")):
                jobs.append((str(f), "candle", True))
                n_by_cat[tf] += 1
        d = PARQUET / "oi/binance/um" / sym
        if d.exists():
            for f in sorted(d.glob("*.parquet")):
                jobs.append((str(f), "oi", False))
                n_by_cat["oi"] += 1
        f = PARQUET / "funding/binance/um" / f"{sym}.parquet"
        if f.exists():
            jobs.append((str(f), "funding", False))
            n_by_cat["funding"] += 1
        d = PARQUET / "metrics/binance/um" / sym
        if d.exists():
            for f in sorted(d.glob("*.parquet")):
                jobs.append((str(f), "metrics", False))
                n_by_cat["metrics"] += 1

    print(f"待冻结文件 {len(jobs)} 个，开始并行哈希 ...", flush=True)
    workers = min(8, os.cpu_count() or 4)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        items = list(ex.map(rec_one, jobs, chunksize=16))
    print(f"哈希完成 {len(items)} 个", flush=True)

    counts = {
        "total": len(items),
        "upstream": sum(1 for i in items if i["category"] == "upstream"),
        "code": sum(1 for i in items if i["category"] == "code"),
        "contracts": sum(1 for i in items if i["category"] == "contracts"),
        "candle_15m": n_by_cat["15m"],
        "candle_1h": n_by_cat["1h"],
        "candle_4h": n_by_cat["4h"],
        "oi": n_by_cat["oi"],
        "funding": n_by_cat["funding"],
        "metrics": n_by_cat["metrics"],
        "bytes_total": sum(i["size"] for i in items),
    }

    spec_changes = sorted(p.name for p in CONTRACTS.glob("spec_changes_*.json"))
    exchange_info = sorted(p.name for p in CONTRACTS.glob("exchangeInfo_futures_*.json"))

    manifest = {
        "audit": "ENTRY-AUDIT-03",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(ROOT),
        "counts": counts,
        "symbols": SYMBOLS,
        "contracts_evidence": {
            "dir": str(CONTRACTS.relative_to(ROOT)),
            "exchangeInfo_futures": exchange_info,
            "spec_changes": spec_changes,
            "note": ("零份 spec_changes_*.json ⇒ 无任何窗口能证明「规格在该窗口内未变更」"
                     "⇒ 按 instrument_spec 的四条要求，全池不可能出现 verified"),
        },
        "files": items,
    }
    with open(OUT / "00_freeze_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    key = {i["path"]: {"sha256": i["sha256"], "size": i["size"], "mtime_utc": i["mtime_utc"]}
           for i in items if i["category"] in ("upstream", "code", "contracts")}
    with open(OUT / "00_freeze_versions.json", "w", encoding="utf-8") as f:
        json.dump(key, f, ensure_ascii=False, indent=2)

    # ── 5. 配置指纹 + 回放规则事实声明 ──
    import sys
    for p in (str(ROOT), str(ROOT / "策略")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    from WyckoffMaster.trading_core.engine import ENGINE_CONTRACT_VERSION
    from WyckoffMaster.event_trend_v0_1.engine_adapter import EventTrendExecParams

    cfg = EventTrendConfig(initial_equity=2000.0)
    cfg_dict = cfg.to_dict()
    cfg_blob = json.dumps(cfg_dict, sort_keys=True, ensure_ascii=False).encode()
    cfg_sha = hashlib.sha256(cfg_blob).hexdigest()

    ep = EventTrendExecParams()  # 仅取默认值字段，不参与本次回放
    exec_defaults = {k: v for k, v in ep.__dict__.items()}

    config_fp = {
        "audit": "ENTRY-AUDIT-03",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "engine_contract_version": ENGINE_CONTRACT_VERSION,
        "config_class": "WyckoffMaster.event_trend_v0_1.config.EventTrendConfig",
        "config_instance": cfg_dict,
        "config_sha256": cfg_sha,
        "exec_params_defaults": exec_defaults,
        "replay_rules_facts": {
            "initial_equity": cfg.initial_equity,
            "initial_equity_source": "EventTrendConfig.initial_equity（唯一事实源；runner/adapter 不另立默认）",
            "positions_initial": "空仓（引擎每个 symbol 独立回测，起始无持仓）",
            "cash_initial": cfg.initial_equity,
            "warmup_4h_bars": 220,
            "warmup_4h_rule": ("load_symbol_data: idx_start_4h = searchsorted(4h.close_time, start_ms)，"
                               "warmup_start_idx = max(0, idx_start_4h - 220)，再裁 close_time <= end_ms"),
            "atr_source": ("df_4h_raw 全历史 compute_wilder_atr_series(14) 先算再裁剪 ⇒ "
                           "ATR 数值不受预热截断影响"),
            "warmup_1h_15m": "无预热：df_1h / df_15m 严格按 open_time >= start_ms 且 close_time <= end_ms 截取",
            "state_machine_start": "df_1h 第一根（路径依赖：箱体只能被突破一次，冷却 24h）",
            "max_holding": f"{cfg.max_holding_days} 个自然日（{cfg.max_holding_days*24} 小时）",
            "close_at_end": "close_open_positions_at_end=True（期末按最后一根可执行 15m 开盘强平）",
            "fee_model": f"taker {cfg.taker_fee_bps} bps（入场与出场均 taker），maker 2 bps 未使用",
            "slippage_bps": cfg.slippage_bps,
            "funding_model": "real_events（真实结算事件；缺 rate/mark ⇒ 记 funding_gap，不补 0）",
            "funding_sign_convention": ("cost = mark_price × qty × rate × direction；"
                                        "cum_funding_fee += cost；cash -= cost ⇒ 正号=支出、负号=收入"),
            "fee_sign_convention": "fees = entry_fee + exit_fee，正号=支出",
            "net_identity": "net_pnl = gross_pnl − fees − funding_fee",
            "risk_budget_per_trade": cfg.risk_budget_per_trade,
            "max_active_positions": cfg.max_active_positions,
            "max_total_risk_budget": cfg.max_total_risk_budget,
            "max_participation_of_prev_15m_quote": cfg.max_participation_of_prev_15m_quote,
            "entry_delay": f"{cfg.entry_delay_15m_bars} 根 15m（严格更晚的下一根开盘）",
        },
    }
    with open(OUT / "00_config_fingerprint.json", "w", encoding="utf-8") as f:
        json.dump(config_fp, f, ensure_ascii=False, indent=2)

    # ── 6. 时间窗口计划：两个代际分开登记 ──
    plan = json.load(open(ROOT / "evidence/FULL_SWEEP_200x100/plan.json", encoding="utf-8"))
    pg = {p["group"]: {"start": p["start"], "end": p["end"]} for p in plan}

    old = [json.loads(l) for l in open(ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl", encoding="utf-8")]
    from collections import defaultdict
    by_entry = defaultdict(list)
    for t in old:
        by_entry[(t["symbol"], int(t["t_in"]))].append(t)

    oc = FROZEN[FROZEN["candidate_id"].str.startswith("old_cand")]
    win_rows = []
    for _, r in oc.iterrows():
        rows = by_entry.get((r["symbol"], int(r["entry_time"]))) or []
        groups = sorted({int(x["group"]) for x in rows})
        win_rows.append({
            "candidate_id": r["candidate_id"],
            "symbol": r["symbol"],
            "entry_time_ms": int(r["entry_time"]),
            "groups": groups,
            "windows": [{"group": g, **pg[g]} for g in groups],
        })

    ce = FROZEN[FROZEN["candidate_id"].str.startswith("cand_ev")]
    window_plan = {
        "audit": "ENTRY-AUDIT-03",
        "generation_A_continuous": {
            "label": "连续回放（cand_ev_*）",
            "start": "2021-12-01",
            "end": "2026-10-18",
            "allow_auto_window": False,
            "rows": int(len(ce)),
            "filled_rows": int((ce["exec_status"] == "FILLED").sum()),
            "unfilled_rows": int((ce["exec_status"] != "FILLED").sum()),
            "symbols": int(ce["symbol"].nunique()),
            "symbol_list": sorted(ce["symbol"].unique().tolist()),
            "source": "evidence/FACTOR-ENTRY-01/stage2_candidate_feature_pipeline.py（START_DATE/END_DATE）",
        },
        "generation_B_windows": {
            "label": "365 天窗口回放（old_cand_*）",
            "rows": int(len(oc)),
            "symbols": int(oc["symbol"].nunique()),
            "distinct_groups": sorted({g for w in win_rows for g in w["groups"]}),
            "distinct_group_symbol_pairs": len({(g, w["symbol"]) for w in win_rows for g in w["groups"]}),
            "window_days": 365,
            "allow_auto_window": False,
            "source": "evidence/FULL_SWEEP_200x100/plan.json + trades_rich.jsonl(group)",
            "per_record": win_rows,
        },
        "merge_policy": ("两代际禁止直接拼接统计：连续回放与窗口回放的起点不同 ⇒ 状态机初始状态不同 "
                         "⇒ 建箱节奏不同。任何比率必须声明分母属于哪一代际。"),
    }
    with open(OUT / "00_window_plan.json", "w", encoding="utf-8") as f:
        json.dump(window_plan, f, ensure_ascii=False, indent=2)

    # ── 7. Markdown 摘要 ──
    lines = [
        "# ENTRY-AUDIT-03 · 冻结基线清单",
        "",
        f"- 冻结时刻（UTC）：{manifest['generated_utc']}",
        f"- 文件总数：{counts['total']}",
        f"- 体量：{counts['bytes_total']/1024/1024:.1f} MB",
        "",
        "## 分类计数",
        "",
        "| 类别 | 数量 |",
        "| :--- | ---: |",
        f"| 上游研究产物 | {counts['upstream']} |",
        f"| 策略代码 | {counts['code']} |",
        f"| 合约规格证据 | {counts['contracts']} |",
        f"| 15m K 线 | {counts['candle_15m']} |",
        f"| 1h K 线 | {counts['candle_1h']} |",
        f"| 4h K 线 | {counts['candle_4h']} |",
        f"| 持仓量 OI | {counts['oi']} |",
        f"| 资金费 | {counts['funding']} |",
        f"| 指标 metrics | {counts['metrics']} |",
        "",
        f"覆盖 {len(SYMBOLS)} 个标的（= 冻结研究范围内出现的全部标的）。",
        "",
        "## 合约规格证据（决定「规格资格」）",
        "",
        f"- exchangeInfo 快照：{len(exchange_info)} 份 → {', '.join(exchange_info)}",
        f"- 规格变更证据 `spec_changes_*.json`：**{len(spec_changes)} 份**",
        "",
        "> 零份变更证据 ⇒ 没有任何窗口能证明「规格在该窗口内未变更」⇒ 全池不可能出现 `verified`。",
        "",
        "## 关键代码版本",
        "",
        "| 文件 | sha256 |",
        "| :--- | :--- |",
    ]
    for i in items:
        if i["category"] == "code":
            lines.append(f"| `{i['path']}` | `{i['sha256'][:32]}…` |")
    lines += ["", "## 配置指纹", "",
              f"- `EventTrendConfig` 序列化 sha256：`{cfg_sha}`",
              f"- 引擎契约版本：`{ENGINE_CONTRACT_VERSION}`",
              "", "完整参数见 `00_config_fingerprint.json`（含预热/持仓/资金初始化/符号约定的逐条事实声明）。",
              "", "## 两个代际（禁止拼接）", "",
              f"- 连续回放 `cand_ev_*`：{window_plan['generation_A_continuous']['rows']} 行"
              f"（成交 {window_plan['generation_A_continuous']['filled_rows']} + 未成交 "
              f"{window_plan['generation_A_continuous']['unfilled_rows']}），"
              f"{window_plan['generation_A_continuous']['symbols']} 个标的，窗口 2021-12-01 ~ 2026-10-18",
              f"- 窗口回放 `old_cand_*`：{window_plan['generation_B_windows']['rows']} 行，"
              f"{window_plan['generation_B_windows']['symbols']} 个标的，"
              f"{len(window_plan['generation_B_windows']['distinct_groups'])} 个不同组、"
              f"{window_plan['generation_B_windows']['distinct_group_symbol_pairs']} 个 (组,标的) 组合，每组 365 天",
              "", "> 完整 64 位哈希见 `00_freeze_manifest.json`。", ""]
    (OUT / "00_freeze_manifest.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(counts, ensure_ascii=False, indent=1))
    print("配置指纹:", cfg_sha)
    print("冻结完成 →", OUT)


if __name__ == "__main__":
    main()
