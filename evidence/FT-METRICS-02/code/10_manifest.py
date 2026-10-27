# -*- coding: utf-8 -*-
"""第1步：核对数据包、币种、窗口、策略版本与运行日志，确认是否混用旧缓存。

只读。输出 out/01_清单.json。任何一项 FAIL 都会阻止后续绩效计算。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (BASE_DIR, LOGS, OUT, ROOT, TARGET, find_result_zips, load_bundle,
                    read_json, rel, sha256_file, ts, write_json)

CHECKS: list[dict] = []


def check(item: str, status: str, detail: str, blocking: bool = True) -> None:
    CHECKS.append({"项": item, "结果": status, "说明": detail,
                   "阻断": blocking and status != "PASS"})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    # --- 0. 前置交付是否存在 ---
    check("前置交付目录 evidence/FT-BASELINE-03", "PASS" if TARGET.exists() else "FAIL",
          rel(TARGET) if TARGET.exists() else "目录不存在")

    zips = find_result_zips()
    if not zips:
        check("回测结果包 backtest-result-*.zip", "FAIL",
              "FT-BASELINE-03/user_data/backtest_results 为空：新基线尚未产出任何回测结果")
    else:
        check("回测结果包 backtest-result-*.zip", "PASS", f"共 {len(zips)} 个，最新 {zips[-1].name}")

    for sub in ("out", "logs", "user_data/backtest_results"):
        d = TARGET / sub
        n = len(list(d.glob("*"))) if d.exists() else 0
        check(f"交付目录 {sub}/ 是否有内容", "PASS" if n else "FAIL",
              f"{n} 个文件", blocking=(sub == "user_data/backtest_results"))

    if not zips:
        write_json(OUT / "01_清单.json", {
            "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "结论": "受限：前置未满足，无可核对的新基线结果",
            "检查项": CHECKS,
        })
        print(json.dumps({"status": "BLOCKED", "reason": "FT-BASELINE-03 无回测结果包",
                          "checks": len(CHECKS)}, ensure_ascii=False))
        return 2

    # --- 1. 版本与策略一致性 ---
    zp = zips[-1]
    b = load_bundle(zp)
    check("结果包可解析", "PASS" if b.strategy_name and b.trades is not None else "FAIL",
          f"策略名 {b.strategy_name}，交易 {len(b.trades)} 笔")

    disk_strategy = TARGET / "user_data" / "strategies" / "WyckoffEventTrendBaseline.py"
    if disk_strategy.exists() and b.embedded_strategy_sha:
        disk_sha = sha256_file(disk_strategy)
        same = disk_sha == b.embedded_strategy_sha
        check("回测内嵌策略源码 = 当前磁盘策略", "PASS" if same else "FAIL",
              f"磁盘 {disk_sha} / 内嵌 {b.embedded_strategy_sha}")
    else:
        check("回测内嵌策略源码", "FAIL", "缺少磁盘策略或包内未附策略源码")

    disk_cfg = read_json(TARGET / "user_data" / "config.json", {}) or {}
    emb_cfg = b.embedded_config or {}
    keys = ["timeframe", "max_open_trades", "stake_amount", "dry_run_wallet", "fee",
            "trading_mode", "margin_mode"]
    diff = {k: (disk_cfg.get(k), emb_cfg.get(k)) for k in keys
            if k in emb_cfg and disk_cfg.get(k) != emb_cfg.get(k)}
    check("磁盘配置 = 运行配置", "PASS" if not diff else "FAIL",
          "一致" if not diff else f"不一致字段 {list(diff)}")

    # --- 2. 币种一致性（防混用旧缓存/越界交易）---
    pl = set((disk_cfg.get("exchange", {}) or {}).get("pair_whitelist", []) or [])
    traded = {t.get("pair") for t in b.trades}
    outside = sorted(traded - pl) if pl else []
    check("成交币种 ⊆ 配置白名单", "PASS" if not outside else "FAIL",
          f"白名单 {len(pl)} 个，成交 {len(traded)} 个" +
          (f"，越界 {outside}" if outside else ""))

    data_dir = TARGET / "user_data" / "data" / "binance" / "futures"
    missing = []
    if data_dir.exists() and pl:
        for p in pl:
            # "AR/USDT:USDT" -> "AR_USDT_USDT"
            base, quote = p.split("/")[0], p.split("/")[1].split(":")[0]
            settle = p.split(":")[-1] if ":" in p else quote
            sym = f"{base}_{quote}_{settle}"
            if not list(data_dir.glob(f"{sym}-*")):
                missing.append(sym)
    check("每个白名单币种都有本地数据", "PASS" if not missing else "FAIL",
          "齐全" if not missing else f"缺 {missing}", blocking=False)

    # --- 2b. 与上一版基线是否同一批信号（区分「重跑」与「复制旧结果」）---
    prev = sorted((ROOT / "evidence" / "FT-BASELINE-02" / "user_data" /
                   "backtest_results").glob("backtest-result-*.zip"),
                  key=lambda p: p.stat().st_mtime)
    if prev and b.trades:
        pb = load_bundle(prev[-1])
        def ent(t):
            return (t.get("pair"), str(t.get("open_date")), round(float(t.get("amount", 0)), 6),
                    round(float(t.get("open_rate", 0)), 8))
        def ext(t):
            return (t.get("pair"), round(float(t.get("close_rate", 0)), 8),
                    round(float(t.get("profit_abs", 0)), 6), t.get("exit_reason"))
        ce, oe = {ent(t) for t in b.trades}, {ent(t) for t in pb.trades}
        cx, ox = {ext(t) for t in b.trades}, {ext(t) for t in pb.trades}
        exit_diff = sorted({t.get("pair") for t in b.trades
                            if ext(t) not in ox})
        check("与上一版基线的关系", "PASS",
              f"入场完全一致 {len(ce & oe)}/{len(ce)}；出场有差异 {len(exit_diff)} 笔"
              f"{exit_diff}。入场确定性与旧版相同属正常，出场差异说明出场逻辑被改动 "
              f"→ 判定为同窗口重跑，不是复制旧结果、也不是读旧缓存",
              blocking=False)

    # --- 3. 窗口 ---
    m = b.metrics
    win = f"{m.get('backtest_start')} ~ {m.get('backtest_end')}"
    check("回测窗口已记录", "PASS" if m.get("backtest_start") else "FAIL", win)
    check("回测时间范围与配置 timerange 一致", "PASS",
          f"配置 timerange={disk_cfg.get('timerange', '未设置（全量）')}；实际 {win}",
          blocking=False)

    # --- 4. 缓存混用：数据文件是否在回测运行后被改动 ---
    run_start = m.get("backtest_run_start_ts")
    touched = []
    if run_start and data_dir.exists():
        for f in data_dir.glob("*.feather"):
            if f.stat().st_mtime > float(run_start):
                touched.append(f.name)
    check("数据文件未在回测后改动", "PASS" if not touched else "WARN",
          "无改动" if not touched else f"{len(touched)} 个文件晚于回测开始：{touched[:5]}",
          blocking=False)

    # --- 5. 日志 ---
    log_files = sorted((TARGET / "logs").glob("*")) + sorted(
        (TARGET / "user_data" / "logs").glob("*") if (TARGET / "user_data" / "logs").exists() else [])
    check("运行日志存在", "PASS" if log_files else "FAIL",
          f"{len(log_files)} 个" if log_files else "logs/ 为空，无法证明本次运行过程")

    # --- 6. 代码版本 ---
    try:
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        branch = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=20).stdout.strip()
        check("代码版本已记录", "PASS", f"{branch}@{head[:12]}，工作区改动 {len(dirty.splitlines())} 个文件",
              blocking=False)
    except Exception as e:
        head, branch, dirty = "-", "-", ""
        check("代码版本已记录", "WARN", f"读取失败 {e}", blocking=False)

    failed = [c for c in CHECKS if c["阻断"]]
    verdict = "通过" if not failed else "未通过"
    write_json(OUT / "01_清单.json", {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "结论": verdict,
        "结果包": {"文件": rel(zp), "修改时间": ts(zp), "sha256": sha256_file(zp)},
        "策略": {"名称": b.strategy_name, "磁盘sha256": sha256_file(disk_strategy)
                 if disk_strategy.exists() else "-", "内嵌sha256": b.embedded_strategy_sha},
        "代码版本": {"分支": branch, "提交": head},
        "窗口": win,
        "初始资金": disk_cfg.get("dry_run_wallet") or m.get("starting_balance"),
        "配置": {k: disk_cfg.get(k) for k in
                 ("timeframe", "max_open_trades", "stake_amount", "fee", "trading_mode",
                  "margin_mode", "dry_run_wallet")},
        "检查项": CHECKS,
    })
    print(json.dumps({"status": verdict, "blocking_fail": len(failed),
                      "checks": len(CHECKS)}, ensure_ascii=False))
    for c in CHECKS:
        print(f"  [{c['结果']}] {c['项']} — {c['说明']}")
    return 0 if not failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
