# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜真实 Freqtrade 引擎批量回测（45 组：27 主实验 + 18 一致性对照）。

纪律
────
· 用**真实引擎**（`evidence/FT-BASELINE-01/.venv/bin/freqtrade backtesting`），
  不自写向量化盈亏器替代；
· 每组独立 `--export-directory`，结果 zip + trades CSV + 完整日志全部保留；
· 失败 / 零成交**照样落盘**，不吞掉；
· 记录每组命令、开始/结束时刻、返回码、策略文件 SHA、配置 SHA、引擎版本；
· 只写 `evidence/WORK-DYNAMIC-BT-01/` 之内。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE_DIR / "evidence/WORK-DYNAMIC-BT-01"
UD = EXP / "user_data"
CFG = UD / "config.json"
STRAT = UD / "strategies"
LOGS = EXP / "logs"
OUT = EXP / "out"
RAW = OUT / "raw_results"
FT_BIN = BASE_DIR / "evidence/FT-BASELINE-01/.venv/bin/freqtrade"
TIMERANGE = "20211201-20241231"     # 与 FT-LOW4H-GRID-01 完全一致

LOGS.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
RAW.mkdir(parents=True, exist_ok=True)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_one(item: dict, force: bool = False) -> dict:
    name = item["class_name"]
    run_id = f"run_FT_LOW4H_DYN01_{name}"
    log_path = LOGS / f"{name}.log"
    csv_path = OUT / f"trades_{name}.csv"
    zip_path = RAW / f"backtest-result-{name}.zip"
    meta_path = RAW / f"meta_{name}.json"
    tmp_dir = RAW / f"tmp_{name}"

    if not force and meta_path.exists() and csv_path.exists():
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            if old.get("returncode") == 0 and old.get("timerange") == TIMERANGE:
                old["status"] = "cached"
                return old
        except Exception:                                       # noqa: BLE001
            pass

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(FT_BIN), "backtesting",
        "-c", str(CFG),
        "--strategy", name,
        "--strategy-path", str(STRAT),
        "--export-directory", str(tmp_dir),
        "--timerange", TIMERANGE,
        "--cache", "none",
        "--export", "trades",
    ]

    t0 = time.time()
    started = utc_iso()
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=lf,
                              stderr=subprocess.STDOUT, text=True)
    rc = proc.returncode
    elapsed = time.time() - t0
    finished = utc_iso()

    n_trades = -1
    net_pnl = None
    extract_note = None

    zips = sorted(tmp_dir.glob("backtest-result*.zip"), key=os.path.getmtime, reverse=True)
    if rc == 0 and zips:
        shutil.copyfile(zips[0], zip_path)
        try:
            with zipfile.ZipFile(zips[0], "r") as z:
                names = [n for n in z.namelist() if n.endswith(".json") and not n.endswith(".meta.json")]
                data = json.loads(z.read(names[0]).decode("utf-8")) if names else {}
            trades = ((data.get("strategy") or {}).get(name) or {}).get("trades") or []
            if trades:
                df = pd.DataFrame(trades)
                df["strategy"] = name
                df.to_csv(csv_path, index=False, encoding="utf-8-sig")
                n_trades = len(df)
                net_pnl = float(df["profit_abs"].sum()) if "profit_abs" in df.columns else None
            else:
                pd.DataFrame().to_csv(csv_path, index=False)
                n_trades = 0
                net_pnl = 0.0
                extract_note = "引擎返回 0 笔成交（零成交，保留）"
        except Exception as exc:                                # noqa: BLE001
            extract_note = f"解析 zip 失败: {type(exc).__name__}: {exc}"
    elif rc != 0:
        extract_note = f"引擎非零退出 rc={rc}，详见日志"
    else:
        extract_note = "引擎返回 0 但未找到结果 zip"
        pd.DataFrame().to_csv(csv_path, index=False)

    meta = {
        "run_id": run_id,
        "strategy": name,
        "group": item["group"],
        "group_label": item["tag"],
        "param_group": item["p_code"],
        "c_vol_ratio_vs": item["vs"],
        "c_retest_zone_atr": item["z"],
        "stoploss_s": 0.05,
        "d_spec": ({k: item[k] for k in ("d",) if k in item}
                   or {k: item[k] for k in ("d1", "d2", "d3", "t1", "t2")}),
        "timerange": TIMERANGE,
        "command": cmd,
        "cwd": str(BASE_DIR),
        "started_at": started,
        "finished_at": finished,
        "elapsed_sec": round(elapsed, 1),
        "returncode": rc,
        "trades": n_trades,
        "net_pnl_realized_plus_open_mark": (round(net_pnl, 4) if net_pnl is not None else None),
        "log": str(log_path),
        "trades_csv": str(csv_path),
        "result_zip": (str(zip_path) if zip_path.exists() else None),
        "strategy_sha256": sha256_file(STRAT / f"{name}.py"),
        "config_sha256": sha256_file(CFG),
        "note": extract_note,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only-groups", default="", help="逗号分隔，如 A,C；空=全部")
    ap.add_argument("--only-params", default="", help="逗号分隔 param_group；空=全部")
    args = ap.parse_args()

    manifest = json.loads((OUT / "01_策略清单.json").read_text(encoding="utf-8"))
    if args.only_groups:
        gs = {g.strip() for g in args.only_groups.split(",") if g.strip()}
        manifest = [m for m in manifest if m["group"] in gs]
    if args.only_params:
        ps = {p.strip() for p in args.only_params.split(",") if p.strip()}
        manifest = [m for m in manifest if m["p_code"] in ps]

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(LOGS / "batch_run.log", encoding="utf-8")])
    log = logging.getLogger("WD-BT-01")
    log.info(f"启动批量回测：{len(manifest)} 组，workers={args.workers}，timerange={TIMERANGE}")

    results = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, m, args.force): m for m in manifest}
        for i, f in enumerate(as_completed(futs), 1):
            m = futs[f]
            try:
                r = f.result()
            except Exception as exc:                            # noqa: BLE001
                log.error(f"{m['class_name']} 未捕获异常: {exc}")
                r = {"strategy": m["class_name"], "group": m["group"],
                     "run_id": f"run_FT_LOW4H_DYN01_{m['class_name']}",
                     "returncode": -1, "trades": -1, "note": f"异常: {exc}"}
            results.append(r)
            log.info(f"[{i}/{len(manifest)}] {m['class_name']} rc={r.get('returncode')} "
                     f"trades={r.get('trades')} pnl={r.get('net_pnl_realized_plus_open_mark')} "
                     f"({r.get('elapsed_sec','?')}s)")

    df = pd.DataFrame(results)
    order = {"A": 0, "B": 1, "C": 2, "P1": 3, "P2": 4}
    df["_o"] = df["group"].map(order).fillna(9)
    df = df.sort_values(["_o", "param_group"]).drop(columns=["_o"])
    df.to_csv(OUT / "02_运行索引.csv", index=False, encoding="utf-8-sig")
    log.info(f"批量结束，总耗时 {time.time()-t0:.0f}s；索引: {OUT/'02_运行索引.csv'}")
    print(df[["strategy", "group", "returncode", "trades",
              "net_pnl_realized_plus_open_mark"]].to_string())


if __name__ == "__main__":
    main()
