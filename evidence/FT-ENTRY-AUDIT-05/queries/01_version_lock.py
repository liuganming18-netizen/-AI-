# -*- coding: utf-8 -*-
"""01 版本锁定与差分核查（FT-ENTRY-AUDIT-05 第 1 条工单项）。

核验目标：
1. 锁定 FT-ENTRY-03（旧对照）与 FT-ENTRY-04（新结果）的全部关键文件 SHA-256；
2. 确认除「A 路线成交价严格低于冻结 H」这一条外，没有其他策略/配置/样本变化；
3. 逐组核对 10 组的回测参数（timerange / 币池 / 资金 / 槽位 / 费率 / timeframe）；
4. 核验基类 diff 的每一处改动是否都属于该约束的实现。

只读，不重跑回测。
"""
from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import zipfile
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, ENTRY03, ENTRY04, OUT, OUT03, OUT04,
    jdump, sha256_file, write_csv,
)

ALL_STRATS = [BASELINE] + [s for _, s, _, _ in CELLS_A]


def sh(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"__ERR__{e}"


def load_archive_meta(strategy: str, src: str) -> Optional[dict]:
    """从归档 zip 读 strategy_backtest 段的关键元数据。"""
    path = os.path.join(src, f"backtest-result-{strategy}.zip")
    if not os.path.isfile(path):
        return None
    try:
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".json") and "config" not in n)
            with z.open(name) as f:
                data = json.loads(f.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"__error__": str(e)}
    st = data.get("strategy", {})
    node = st.get(strategy, {}) if isinstance(st, dict) else {}
    sb = node.get("strategy", node)
    if not isinstance(sb, dict):
        sb = {}
    return {
        "timerange": sb.get("timerange"),
        "timeframe": sb.get("timeframe"),
        "timeframe_detail": sb.get("timeframe_detail"),
        "max_open_trades": sb.get("max_open_trades"),
        "stake_currency": sb.get("stake_currency"),
        "stake_amount": sb.get("stake_amount"),
        "dry_run_wallet": sb.get("dry_run_wallet"),
        "fee": sb.get("fee"),
        "total_trades": sb.get("total_trades"),
        "profit_total": sb.get("profit_total"),
        "profit_total_abs": sb.get("profit_total_abs"),
        "wins": sb.get("wins"),
        "losses": sb.get("losses"),
        "drawdown": sb.get("max_drawdown_account"),
        "backtest_start": sb.get("backtest_start"),
        "backtest_end": sb.get("backtest_end"),
        "pairs": len(sb.get("pairlist") or []) or None,
    }


def main() -> None:
    rows: List[dict] = []
    for strat in ALL_STRATS:
        for tag, src in (("FT-ENTRY-03", OUT03), ("FT-ENTRY-04", OUT04)):
            zip_p = os.path.join(src, f"backtest-result-{strat}.zip")
            csv_p = os.path.join(src, f"trades_{strat}.csv")
            rows.append({
                "batch": tag,
                "strategy": strat,
                "archive_exists": os.path.isfile(zip_p),
                "archive_sha256": sha256_file(zip_p),
                "archive_bytes": os.path.getsize(zip_p) if os.path.isfile(zip_p) else None,
                "trades_csv_exists": os.path.isfile(csv_p),
                "trades_csv_sha256": sha256_file(csv_p),
                "trades_csv_rows": (sum(1 for _ in open(csv_p, encoding="utf-8-sig")) - 1)
                if os.path.isfile(csv_p) else None,
            })
    manifest = pd.DataFrame(rows)
    write_csv(manifest, "01_产物清单与哈希_v1.csv")
    print("== 产物清单 ==")
    print(manifest.to_string(index=False))

    # ── 归档元数据对比（10 组 × 2 批）──
    meta_rows: List[dict] = []
    for strat in ALL_STRATS:
        m3 = load_archive_meta(strat, OUT03)
        m4 = load_archive_meta(strat, OUT04)
        for tag, m in (("FT-ENTRY-03", m3), ("FT-ENTRY-04", m4)):
            if m is None:
                meta_rows.append({"batch": tag, "strategy": strat, "__missing_archive__": True})
                continue
            meta_rows.append({"batch": tag, "strategy": strat, **m})
    meta = pd.DataFrame(meta_rows)
    write_csv(meta, "01_归档元数据_v1.csv")

    # 只对两批都存在的组做参数等同性比对
    param_cols = ["timerange", "timeframe", "timeframe_detail", "max_open_trades",
                  "stake_currency", "stake_amount", "dry_run_wallet", "fee",
                  "backtest_start", "backtest_end", "pairs"]
    diff_rows: List[dict] = []
    for strat in ALL_STRATS:
        a = meta[(meta["batch"] == "FT-ENTRY-03") & (meta["strategy"] == strat)]
        b = meta[(meta["batch"] == "FT-ENTRY-04") & (meta["strategy"] == strat)]
        if a.empty or b.empty:
            diff_rows.append({"strategy": strat, "comparable": False,
                              "note": "缺少任一批归档，无法比对参数"})
            continue
        ra, rb = a.iloc[0], b.iloc[0]
        changed = {c: (ra.get(c), rb.get(c)) for c in param_cols
                   if str(ra.get(c)) != str(rb.get(c))}
        diff_rows.append({
            "strategy": strat,
            "comparable": True,
            "params_identical": len(changed) == 0,
            "changed_params": json.dumps(changed, ensure_ascii=False) if changed else None,
            "trades_03": ra.get("total_trades"),
            "trades_04": rb.get("total_trades"),
            "profit_abs_03": ra.get("profit_total_abs"),
            "profit_abs_04": rb.get("profit_total_abs"),
        })
    pdiff = pd.DataFrame(diff_rows)
    write_csv(pdiff, "01_参数等同性比对_v1.csv")
    print("\n== 参数等同性 ==")
    print(pdiff.to_string(index=False))

    # ── 代码差分：基类 ──
    p3 = os.path.join(ENTRY03, "user_data/strategies/WyckoffParamExplorationBase.py")
    p4 = os.path.join(ENTRY04, "user_data/strategies/WyckoffParamExplorationBase.py")
    diff_txt = ""
    if os.path.isfile(p3) and os.path.isfile(p4):
        a = open(p3, encoding="utf-8").read().splitlines()
        b = open(p4, encoding="utf-8").read().splitlines()
        diff_txt = "\n".join(difflib.unified_diff(a, b, "FT-ENTRY-03", "FT-ENTRY-04", lineterm=""))
        outp = os.path.join(OUT, "01_基类差分全文_v1.patch")
        with open(outp, "w", encoding="utf-8") as f:
            f.write(diff_txt)

    # ── 代码差分：config 与 10 个子策略 ──
    code_rows: List[dict] = []
    cfg3 = os.path.join(ENTRY03, "user_data/config.json")
    cfg4 = os.path.join(ENTRY04, "user_data/config.json")
    code_rows.append({
        "file": "user_data/config.json",
        "sha256_03": sha256_file(cfg3), "sha256_04": sha256_file(cfg4),
        "identical": sha256_file(cfg3) == sha256_file(cfg4),
    })
    for strat in ALL_STRATS:
        f3 = os.path.join(ENTRY03, "user_data/strategies", f"{strat}.py")
        f4 = os.path.join(ENTRY04, "user_data/strategies", f"{strat}.py")
        code_rows.append({
            "file": f"user_data/strategies/{strat}.py",
            "sha256_03": sha256_file(f3), "sha256_04": sha256_file(f4),
            "identical": sha256_file(f3) == sha256_file(f4),
        })
    code_df = pd.DataFrame(code_rows)
    write_csv(code_df, "01_代码文件哈希对比_v1.csv")
    print("\n== 代码文件哈希对比 ==")
    print(code_df.to_string(index=False))

    # config 的键级差异（确认只有路径/bot_name）
    cfg_key_diff = {}
    if os.path.isfile(cfg3) and os.path.isfile(cfg4):
        j3 = json.load(open(cfg3, encoding="utf-8"))
        j4 = json.load(open(cfg4, encoding="utf-8"))
        keys = sorted(set(j3) | set(j4))
        for k in keys:
            if str(j3.get(k)) != str(j4.get(k)):
                cfg_key_diff[k] = {"03": j3.get(k), "04": j4.get(k)}
    jdump(os.path.join(OUT, "01_config键级差异_v1.json"), cfg_key_diff)
    print("\n== config 键级差异 ==")
    print(json.dumps(cfg_key_diff, ensure_ascii=False, indent=2))

    # ── 汇总 ──
    summary = {
        "n_strategies_compared": len(ALL_STRATS),
        "all_params_identical": bool(pdiff.get("params_identical", pd.Series(dtype=bool)).fillna(False).all())
        if "params_identical" in pdiff.columns else None,
        "n_params_identical": int(pdiff["params_identical"].sum()) if "params_identical" in pdiff.columns else None,
        "config_changed_keys": list(cfg_key_diff.keys()),
        "config_changed_keys_are_path_or_botname_only": all(
            k in ("user_data_dir", "datadir", "bot_name") for k in cfg_key_diff),
        "strategy_subfiles_all_identical": bool(code_df.iloc[1:]["identical"].all()),
        "base_class_identical": bool(code_df.iloc[0]["identical"]),
        "base_class_diff_lines": len(diff_txt.splitlines()) if diff_txt else 0,
        "base_class_diff_hunks": diff_txt.count("\n@@ ") + (1 if diff_txt.startswith("@@") else 0),
        "note": "FT-ENTRY-04 的基类差分应仅包含：A 路线成交价严格 < 冻结 H 的实现、"
                "订单审计记录设施、防重试集合；其余必须完全一致",
    }
    jdump(os.path.join(OUT, "01_差分核查摘要_v1.json"), summary)
    print("\n== 差分核查摘要 ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
