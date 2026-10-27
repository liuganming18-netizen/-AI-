# -*- coding: utf-8 -*-
"""BT-LEDGER-PILOT-04｜单组原始订单补证试点 —— 引擎侧运行器。

唯一改动：继承 `Backtesting` 并**只在 `backtest()` 返回后**把进程内的真实对象
（`LocalTrade.bt_trades` → `Order` 实例）序列化落盘。
不改策略文件、不改 config、不改 freqtrade 包、不改任何计算路径。

必须用引擎 venv 跑，且 cwd = 项目根（config 里 datadir 是相对路径）：
  cd /Users/boos/Desktop/威科夫因子共振趋势
  evidence/FT-BASELINE-01/.venv/bin/python evidence/BT-LEDGER-PILOT-04/code/01_pilot_run.py

固定的那一组（运行前锁定，见 out/00_engine_raw/run_meta.json）
  strategy = Wyckoff_C1_S05_D05_Vs04_Z02
  old run_id = run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02
  旧结果 zip = evidence/WORK-DYNAMIC-BT-01/out/raw_results/backtest-result-<strategy>.zip
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BT01 = BASE / "evidence/WORK-DYNAMIC-BT-01"
ME = BASE / "evidence/BT-LEDGER-PILOT-04"
RAW = ME / "out/00_engine_raw"
EXPORT = RAW / "freqtrade_export"

STRATEGY = "Wyckoff_C1_S05_D05_Vs04_Z02"
OLD_RUN_ID = "run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02"
OLD_ZIP = BT01 / "out/raw_results" / f"backtest-result-{STRATEGY}.zip"
CONFIG = BT01 / "user_data/config.json"
STRAT_DIR = BT01 / "user_data/strategies"
STRAT_FILE = STRAT_DIR / f"{STRATEGY}.py"
BASE_FILE = STRAT_DIR / "WyckoffLow4hGridBase.py"
TIMERANGE = "20211201-20241231"

# 旧 run.json 登记的身份（运行前比对，不一致就中止）
EXPECTED_STRATEGY_SHA = "4eb070b7892ac9008bc446b627f5b0fd5a41c141848a30a39158f37dfd9c8d2e"
EXPECTED_CONFIG_SHA = "88c233f1dfa4676cee34c1453b86bff21d1521058ccdfe4053d0874eda2bc3b3"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    # ── 0. 运行前锁定身份：策略/基类/配置哈希必须与旧 run.json 一致 ──
    pre = {
        "started_at": utc_now(),
        "strategy": STRATEGY,
        "old_run_id": OLD_RUN_ID,
        "strategy_file": str(STRAT_FILE),
        "strategy_sha256": sha256(STRAT_FILE),
        "base_file": str(BASE_FILE),
        "base_sha256": sha256(BASE_FILE),
        "config_file": str(CONFIG),
        "config_sha256": sha256(CONFIG),
        "timerange": TIMERANGE,
        "old_zip": str(OLD_ZIP),
        "old_zip_sha256": sha256(OLD_ZIP),
        "expected_strategy_sha256": EXPECTED_STRATEGY_SHA,
        "expected_config_sha256": EXPECTED_CONFIG_SHA,
    }
    assert pre["strategy_sha256"] == EXPECTED_STRATEGY_SHA, "策略文件已变，拒绝运行"
    assert pre["config_sha256"] == EXPECTED_CONFIG_SHA, "config 已变，拒绝运行"
    RAW.mkdir(parents=True, exist_ok=True)
    if EXPORT.exists():
        shutil.rmtree(EXPORT)
    EXPORT.mkdir(parents=True, exist_ok=True)

    # ── 1. 用官方 CLI 解析器 + 官方配置装配，保证与旧命令逐项一致 ──
    from freqtrade.commands import Arguments
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from sqlalchemy import inspect as sa_inspect
    from freqtrade.persistence.trade_model import Order

    argv = [
        "backtesting",
        "--config", str(CONFIG),
        "--strategy", STRATEGY,
        "--strategy-path", str(STRAT_DIR),
        "--export-directory", str(EXPORT),
        "--timerange", TIMERANGE,
        "--cache", "none",
        "--export", "trades",
    ]
    args = Arguments(argv).get_parsed_arg()
    config = setup_optimize_configuration(args, RunMode.BACKTEST)
    config["exportdirectory"] = EXPORT

    # Order 模型的**全部**映射列（不是挑几个）—— 用于证明"引擎里到底有什么"
    ORDER_COLUMNS = [c.key for c in sa_inspect(Order).mapper.column_attrs]

    captured: dict[str, Any] = {}

    class PilotBacktesting(Backtesting):
        """只在 backtest() 返回后加一次序列化，其余全部走父类。"""

        def backtest(self, processed, start_date, end_date):  # type: ignore[override]
            content = super().backtest(processed, start_date, end_date)
            trades = list(LocalTrade.bt_trades)
            captured["n_trades"] = len(trades)
            captured["n_orders"] = sum(len(t.orders) for t in trades)
            captured["order_columns"] = ORDER_COLUMNS
            captured["strategy_sha256_at_run"] = sha256(STRAT_FILE)
            captured["base_sha256_at_run"] = sha256(BASE_FILE)
            captured["config_sha256_at_run"] = sha256(CONFIG)

            o_path = RAW / "orders_full.jsonl"
            t_path = RAW / "trades_full.jsonl"
            with open(o_path, "w", encoding="utf-8") as fo, \
                    open(t_path, "w", encoding="utf-8") as ft:
                for t in trades:
                    entry_side = "sell" if t.is_short else "buy"
                    ft.write(json.dumps({
                        "engine_trade_id": t.id,
                        "to_json_full": t.to_json(minified=False),
                        "nr_of_successful_entries": t.nr_of_successful_entries,
                        "nr_of_successful_exits": t.nr_of_successful_exits,
                        "has_open_orders": t.has_open_orders,
                        "n_orders_in_memory": len(t.orders),
                    }, ensure_ascii=False, default=str) + "\n")
                    for o in t.orders:
                        # ① to_json(minified=False)：引擎官方全字段视图
                        row = {"pair": t.pair,
                               "engine_trade_id": t.id,
                               "trade_open_timestamp": int(t.open_date.timestamp() * 1000),
                               "trade_close_timestamp": (int(t.close_date.timestamp() * 1000)
                                                         if t.close_date else None),
                               "entry_side": entry_side,
                               "to_json_full": o.to_json(entry_side, minified=False)}
                        # ② 直接读模型列（含 to_json 永不出、也含导出被裁的）
                        model = {}
                        for k in ORDER_COLUMNS:
                            v = getattr(o, k, None)
                            model[k] = (v.isoformat() if hasattr(v, "isoformat") else v)
                        row["model_columns"] = model
                        # ③ 安全属性（派生）
                        row["safe_props"] = {
                            "safe_amount": o.safe_amount,
                            "safe_price": o.safe_price,
                            "safe_filled": o.safe_filled,
                            "safe_remaining": o.safe_remaining,
                            "safe_fee_base": o.safe_fee_base,
                            "safe_amount_after_fee": o.safe_amount_after_fee,
                            "stake_amount_filled": o.stake_amount_filled,
                        }
                        fo.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

            # 引擎计数器（与旧导出同源）
            captured["engine_counters"] = {
                "rejected_signals": content.get("rejected_signals"),
                "timedout_entry_orders": content.get("timedout_entry_orders"),
                "timedout_exit_orders": content.get("timedout_exit_orders"),
                "canceled_trade_entries": content.get("canceled_trade_entries"),
                "canceled_entry_orders": content.get("canceled_entry_orders"),
                "replaced_entry_orders": content.get("replaced_entry_orders"),
                "final_balance": content.get("final_balance"),
            }
            return content

    bt = PilotBacktesting(config)
    bt.start()

    # ── 2. 落运行元数据（含新 zip 路径与哈希）──
    zips = sorted(EXPORT.glob("backtest-result-*.zip"))
    post = {
        "finished_at": utc_now(),
        "export_dir": str(EXPORT),
        "new_zip": str(zips[-1]) if zips else None,
        "new_zip_sha256": sha256(zips[-1]) if zips else None,
        "strategy_sha256_after": sha256(STRAT_FILE),
        "base_sha256_after": sha256(BASE_FILE),
        "config_sha256_after": sha256(CONFIG),
        "strategy_file_unchanged": sha256(STRAT_FILE) == EXPECTED_STRATEGY_SHA,
        "base_file_unchanged": sha256(BASE_FILE) == pre["base_sha256"],
        "config_file_unchanged": sha256(CONFIG) == EXPECTED_CONFIG_SHA,
    }
    meta = {
        "ticket": "BT-LEDGER-PILOT-04",
        "scope": "单组（1/45），不外推",
        "pre": pre,
        "post": post,
        "captured": captured,
        "capture_point": "Backtesting.backtest() 返回后、generate_backtest_stats() 之前；"
                         "此刻 LocalTrade.bt_trades 仍持有全部已平仓交易对象",
        "code_changes": "仅新增子类 override backtest() 做序列化；策略/config/引擎包 0 改动",
    }
    (RAW / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print("== PILOT DONE ==")
    print(json.dumps({k: captured.get(k) for k in
                      ("n_trades", "n_orders", "engine_counters", "strategy_sha256_at_run")},
                     ensure_ascii=False, indent=1))
    print("new_zip:", post["new_zip"])


if __name__ == "__main__":
    main()
