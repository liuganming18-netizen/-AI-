#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 历史产物分层统计（只读，全量 5,091 个 run 目录）

为什么重算：上一单（PROJECT-REVIEW-07）的 3,989 只覆盖「同时有 chart/inputs 且有
candidates/run.json」的目录，漏掉 1,102 个（主要是 `runs_ft_replay_01` 的重建产物）。
本脚本按目录名规则全量枚举，并把每一层分成四种状态：

  A 应能重开却报错        —— 支持范围内、chart.candles 是非空数组（读侧 400）
  B 尚不支持但有明确提示  —— 支持范围内、盘上无 K 线（读侧需重建/给 missing_reason）
  C 已归档且不在索引范围  —— 目录不在读侧扫描范围（读侧表现为「未知 run_id」）
  D 缺失来源、无法复算    —— 缺 inputs/chart，或没有规则参数，或没有订单/成交/权益来源
  E 可重开且身份齐全      —— 对象K线 + 规则参数 + 九件套齐

数据来源：上一单 out/03（逐 run 详情）+ 本轮目录枚举（文件存在性）。
只读；不写任何 run 目录。
"""
import json
import os
import re
from collections import Counter, defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
SRC = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07/out/03_回测可追溯性.json")
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/01_产物分层.json")

RUN_ID_RE = re.compile(
    r"^(bt_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}"
    r"|run_WX_EVENT_TREND_V0_1_.*"
    r"|run_FT_ENTRY_09_.*"
    r"|run_FT_STOP20_.*"
    r"|run_FT_EXIT01_.*"
    r"|run_FT_STOPGRID01_.*"
    r"|run_FT_STOPGRID02_.*"
    r"|run_FT_LOW4H_.*)$"
)
STD = ["candidates.jsonl", "chart.json", "equity_curve.jsonl", "inputs.json",
       "manifest.json", "orders.jsonl", "run.json", "summary.json", "trades.jsonl"]


def tier_of(rel_batch: str) -> str:
    if rel_batch == "event_trend_v0_1/runs":
        return "支持-默认根"
    if rel_batch.startswith("event_trend_v0_1/runs"):
        return "支持-同级批次"
    return "归档-不在扫描范围"


def rule_of(inp: dict) -> dict:
    fp = inp.get("fingerprints") or {}
    p = ((fp.get("config") or {}).get("params") or {})
    return {
        "tr": p.get("trailing_trigger_r"),
        "lb": p.get("trailing_lookback_4h_bars"),
        "mh": p.get("max_holding_days"),
        "s": p.get("stoploss_s"),
        "d": p.get("trail_d"),
        "equity": p.get("initial_equity"),
        "maxpos": p.get("max_active_positions"),
        "single": p.get("max_notional_per_position"),
        "total": p.get("max_total_notional"),
        "part": p.get("max_participation_of_prev_15m_quote"),
        "slip": p.get("slippage_bps"),
        "rules_sha": ((fp.get("rules") or {}).get("sha256") or "")[:16],
        "has_params": bool(p),
    }


def rule_label(r: dict) -> str:
    if not r["has_params"]:
        return "无参数记录（不可复算）"
    if r["tr"] == 0.0 and r["lb"] == 1 and r["mh"] is None:
        return "FT-LOW4H"
    if r["tr"] == 2.0 and r["lb"] == 10 and r["mh"] == 10:
        return "旧 V0_1（2R/10根/10天）"
    return f"其它(tr={r['tr']},lb={r['lb']},mh={r['mh']})"


def main() -> None:
    src = json.load(open(SRC, encoding="utf-8"))
    rows = []
    covered = set()
    for r in src["记录"]:
        rel = r["run"]
        covered.add(rel)
        batch = "/".join(rel.split("/")[1:-1])
        d = os.path.join(ROOT, rel)
        inp, runj = {}, {}
        try:
            inp = json.load(open(os.path.join(d, "inputs.json"), encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            pass
        try:
            runj = json.load(open(os.path.join(d, "run.json"), encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            pass
        ch = r.get("chart") or {}
        files = r.get("files") or {}
        rows.append({
            "run": rel, "batch": batch, "tier": tier_of(batch),
            "has_chart": bool(files.get("chart.json")),
            "has_inputs": bool(files.get("inputs.json")),
            "std_complete": not (r.get("missing") or []),
            "candle_n": ch.get("candle_n"),
            "candle_is_object": ch.get("candle_is_object"),
            "orders_n": r.get("orders_n"), "trades_n": r.get("trades_n"),
            "engine": ((runj.get("engine") or {}).get("exec_core_version")
                       or ("未知（无 run.json）" if not runj else "项目回测引擎")),
            "rule": rule_label(rule_of(inp)), "params": rule_of(inp),
        })

    # 目录枚举补齐（上一单漏掉的 1,102 个）
    extra = []
    for dp, dn, fn in os.walk(os.path.join(ROOT, "回测日志")):
        keep = []
        for d in dn:
            if RUN_ID_RE.match(d):
                rel = os.path.relpath(os.path.join(dp, d), ROOT)
                if rel not in covered:
                    have = {f: os.path.exists(os.path.join(dp, d, f)) for f in STD}
                    extra.append({"run": rel, "have": have})
            else:
                keep.append(d)
        dn[:] = keep
    for x in extra:
        rel = x["run"]
        batch = "/".join(rel.split("/")[1:-1])
        have = x["have"]
        rows.append({
            "run": rel, "batch": batch, "tier": tier_of(batch),
            "has_chart": have["chart.json"], "has_inputs": have["inputs.json"],
            "std_complete": all(have.values()),
            "candle_n": None, "candle_is_object": None,
            "orders_n": None, "trades_n": None,
            "engine": "未知（无 run.json）" if not have["run.json"] else "项目回测引擎",
            "rule": "无参数记录（不可复算）",
            "params": {"has_params": False},
            "_lite": True,
        })

    def state(x):
        if x.get("_lite") and not x["std_complete"]:
            return "D 缺来源（无 candidates/orders/equity/manifest）"
        if not x["has_inputs"]:
            return "D 缺 inputs（不可复算）"
        if not x["has_chart"]:
            return "D 缺 chart（无盘上K线）"
        if x["candle_is_object"] is False and (x["candle_n"] or 0) > 0:
            return "A 数组K线且非空（读侧 400）"
        if x["candle_is_object"] is False and (x["candle_n"] or 0) == 0:
            return "B 数组K线但空（读时重建）"
        if not x["params"].get("has_params"):
            return "D 有K线但无规则参数（不可复算）"
        return "E 可重开且身份齐全"

    for x in rows:
        x["state"] = state(x)

    by_tier, by_batch = defaultdict(Counter), defaultdict(Counter)
    by_rule = Counter()
    for x in rows:
        by_tier[x["tier"]][x["state"]] += 1
        by_batch[x["batch"]][x["state"]] += 1
        by_rule[x["rule"]] += 1

    ft = Counter()
    for x in rows:
        if x["rule"] == "FT-LOW4H":
            p = x["params"]
            ft[(p["equity"], p["maxpos"], p["single"], p["total"],
                p["part"], p["slip"], p["rules_sha"])] += 1

    out = {
        "run_目录数": len(rows),
        "上一单覆盖数": len(src["记录"]),
        "本轮补齐数": len(extra),
        "分层": {k: dict(v) for k, v in by_tier.items()},
        "分层合计": {k: sum(v.values()) for k, v in by_tier.items()},
        "按批次状态": {k: dict(v) for k, v in sorted(by_batch.items())},
        "按规则身份": dict(by_rule),
        "FT_LOW4H参数组合": [
            {"initial_equity": k[0], "max_active_positions": k[1],
             "single_notional": k[2], "total_notional": k[3],
             "participation": k[4], "slippage_bps": k[5],
             "rules_sha": k[6], "n": v} for k, v in ft.most_common()],
        "状态计数": dict(Counter(x["state"] for x in rows)),
        "说明": "tier 依据 backtest_store._sibling_batch_roots()：默认根 runs/ 与同级 runs* 目录可被读侧定位；"
                "其余目录不在扫描范围。上一单 3,989 的判据是「有 chart/inputs 且 有 candidates/run.json」，"
                "因此漏掉 1,102 个（1,072 在 event_trend_v0_1 下，多为 runs_ft_replay_01 的重建产物）。",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("run 目录数", len(rows), "（上一单", len(src["记录"]), "+ 补齐", len(extra), "）")
    for k, v in out["分层"].items():
        print(f"{k:16s}", v)
    print("状态计数", out["状态计数"])
    print("规则身份", dict(by_rule))
    print("→", OUT)


if __name__ == "__main__":
    main()
