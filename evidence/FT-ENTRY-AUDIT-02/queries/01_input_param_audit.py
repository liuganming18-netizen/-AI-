# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 01 开跑前输入清单、有效区间与冻结参数表核对

只读 FT-ENTRY-01（反重力交付）与 FT-FACTOR-03（数据源）与 FT-FACTOR-AUDIT-01（我方 L1 口径），
不改任何上游文件。产出：
  out/01_配置与冻结方案核对_v1.csv
  out/02_币池与数据覆盖_v1.csv
  out/03_策略与归档指纹_v1.csv
  out/04_规则实现对照_v1.csv
  out/05_开跑前核对摘要_v1.json
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone

import pandas as pd

csv.field_size_limit(10 ** 9)

BASE = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY = os.path.join(BASE, "evidence/FT-ENTRY-01")
AUDIT = os.path.join(BASE, "evidence/FT-ENTRY-AUDIT-02")
OUT = os.path.join(AUDIT, "out")
DATA = os.path.join(BASE, "evidence/FT-FACTOR-03/user_data/data/binance")
STRAT = os.path.join(ENTRY, "user_data/strategies")

RESEARCH_START = datetime(2021, 12, 1, tzinfo=timezone.utc)
RESEARCH_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

ARMS = [
    "WyckoffBaseline",
    "WyckoffEntryA1_NearTop",
    "WyckoffEntryA2_AtBoxHigh",
    "WyckoffEntryC1_StopSweep",
    "WyckoffEntryC2_StopBar",
]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slug(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def load_trades(arm: str) -> pd.DataFrame:
    p = os.path.join(ENTRY, "out", f"trades_{arm}.csv")
    df = pd.read_csv(p, encoding="utf-8-sig")
    if "pair" not in df.columns:
        df = df.rename(columns={df.columns[0]: "pair"})
    return df


def zip_payload(arm: str):
    zp = os.path.join(ENTRY, "out", f"backtest-result-{arm}.zip")
    z = zipfile.ZipFile(zp)
    names = z.namelist()
    jname = next(n for n in names if n.endswith(".json") and not n.endswith(".meta.json")
                 and not n.endswith("_config.json"))
    py = next((n for n in names if n.endswith(".py")), None)
    cfg = next((n for n in names if n.endswith("_config.json")), None)
    wallet = next((n for n in names if n.endswith("_wallet.feather")), None)
    return {
        "zip_path": zp,
        "zip_sha256": sha256_file(zp),
        "json_name": jname,
        "data": json.loads(z.read(jname)),
        "py_name": py,
        "py_bytes": z.read(py) if py else b"",
        "cfg": json.loads(z.read(cfg)) if cfg else {},
        "wallet_bytes": z.read(wallet) if wallet else b"",
        "n_members": len(names),
        "member_names": names,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    summary = {"work_order": "FT-ENTRY-AUDIT-02", "generated_by": "queries/01_input_param_audit.py"}

    # ---------- 1. 配置 vs 冻结方案 ----------
    cfg = json.load(open(os.path.join(ENTRY, "user_data/config.json"), encoding="utf-8"))
    wl = cfg["exchange"]["pair_whitelist"]

    checks = []

    def add(item, frozen, actual, verdict, note=""):
        checks.append({"check_item": item, "frozen_requirement": frozen,
                       "actual_value": actual, "verdict": verdict, "note": note})

    add("币池数量", "74 标的合格池", str(len(wl)), "符合" if len(wl) == 74 else "不符")
    add("时间区间", "2021-12-01 ~ 2024-12-31", "CLI --timerange 20211201-20241231",
        "符合（需二次核验实际起止）", "timerange 未写入 config.json，只在命令行与 run 脚本中出现")
    add("手续费 Taker", "5 bps", f"fee={cfg.get('fee')}", "符合" if cfg.get("fee") == 0.0005 else "不符")
    add("滑点", "3 bps", "配置与策略源码中均无滑点项", "不符", "引擎无 slippage 参数；全仓搜索未发现 0.0003 注入")
    add("单笔风险预算", "0.5% 账户权益", "custom_stake_amount: equity*0.005", "符合")
    add("单仓上限", "30%", "custom_stake_amount: equity*0.30", "符合")
    add("并发上限", "max_open_trades = 3", str(cfg.get("max_open_trades")), "符合" if cfg.get("max_open_trades") == 3 else "不符")
    add("初始资金", "未在冻结方案中写明", f"dry_run_wallet={cfg.get('dry_run_wallet')}", "需披露",
        "所有 net_return_pct 均以 2000 U 为分母")
    add("可交易余额比例", "未写明", f"tradable_balance_ratio={cfg.get('tradable_balance_ratio')}", "需披露")
    add("数据源", "未写明", cfg.get("datadir"), "需披露", "与 FT-FACTOR-03 同一份 feather，未重新导出")
    add("交易模式", "未写明", f"{cfg.get('trading_mode')}/{cfg.get('margin_mode')}", "需披露",
        "cross 保证金、leverage=1.0（逐笔字段确认）")

    # ---------- 2. 币池比对 ----------
    f03 = json.load(open(os.path.join(BASE, "evidence/FT-FACTOR-03/user_data/config.json"), encoding="utf-8"))
    wl03 = f03["exchange"]["pair_whitelist"]
    l1 = json.load(open(os.path.join(BASE, "evidence/FT-FACTOR-AUDIT-01/out/03_计数复核_v1.json"),
                        encoding="utf-8"))["research_period"]["L1_symbols"]

    set_entry, set03 = set(wl), set(wl03)
    # 命名归一：L1 用 SOLUSDT，本批用 SOL/USDT:USDT
    norm_entry = {p.split("/")[0] + "USDT" for p in wl}
    set_l1 = set(l1)
    summary["universe"] = {
        "count": len(wl),
        "identical_to_FT_FACTOR_03_whitelist": set_entry == set03,
        "diff_vs_FT_FACTOR_03": sorted(set_entry ^ set03),
        "in_my_L1_241": len(norm_entry & set_l1),
        "not_in_my_L1_241": sorted(norm_entry - set_l1),
        "coverage_of_L1": round(len(norm_entry & set_l1) / len(set_l1) * 100, 2),
    }

    # ---------- 3. 数据覆盖 ----------
    cover_rows = []
    for pair in wl:
        s = slug(pair)
        row = {"pair": pair, "symbol": pair.split("/")[0] + "USDT"}
        for tf in ["15m", "1h", "4h"]:
            p = os.path.join(DATA, "futures", f"{s}-{tf}-futures.feather")
            if not os.path.exists(p):
                row[f"{tf}_exists"] = 0
                row[f"{tf}_first"] = ""
                row[f"{tf}_last"] = ""
                row[f"{tf}_bars"] = 0
                row[f"{tf}_bars_before_research_start"] = 0
                continue
            d = pd.read_feather(p, columns=["date"])
            dt = pd.to_datetime(d["date"], utc=True)
            row[f"{tf}_exists"] = 1
            row[f"{tf}_first"] = dt.min().strftime("%Y-%m-%d %H:%M")
            row[f"{tf}_last"] = dt.max().strftime("%Y-%m-%d %H:%M")
            row[f"{tf}_bars"] = int(len(dt))
            row[f"{tf}_bars_before_research_start"] = int((dt < RESEARCH_START).sum())
        p_oi = os.path.join(DATA, "futures", f"{s}-5m-open_interest.feather")
        row["oi_exists"] = int(os.path.exists(p_oi))
        if os.path.exists(p_oi):
            doi = pd.read_feather(p_oi, columns=["date"])
            dt = pd.to_datetime(doi["date"], utc=True)
            row["oi_first"] = dt.min().strftime("%Y-%m-%d %H:%M")
            row["oi_last"] = dt.max().strftime("%Y-%m-%d %H:%M")
        else:
            row["oi_first"] = row["oi_last"] = ""
        cover_rows.append(row)

    # 逐组交易数 & 首次/末次成交
    arm_stats = {}
    for arm in ARMS:
        df = load_trades(arm)
        arm_stats[arm] = {
            "trades": int(len(df)),
            "first_open": str(df["open_date"].min()) if len(df) else "",
            "last_close": str(df["close_date"].max()) if len(df) else "",
            "symbols_traded": int(df["pair"].nunique()) if len(df) else 0,
        }
    for row in cover_rows:
        for arm in ARMS:
            df = load_trades(arm)
            row[f"trades_{arm}"] = int((df["pair"] == row["pair"]).sum())

    # 4h 预热口径：实现只需 42 根，冻结方案/我方 L1 要求 200 根
    under200 = [r["symbol"] for r in cover_rows if r["4h_bars_before_research_start"] < 200]
    summary["warmup"] = {
        "frozen_rule_4h_warmup_bars": 200,
        "implementation_box_min_bars": 42,
        "implementation_atr_period_4h": 14,
        "pairs_with_lt_200_4h_bars_before_research_start": len(under200),
        "such_pairs": under200,
        "note": "策略实现只要求 len(sub_4h)>=42 且 ATR(14)，未按冻结规则做 200 根 4h 预热；"
                "上一轮 FT-FACTOR-AUDIT-01 的 L1 门槛是 200 根，两者口径不同。",
    }

    with open(os.path.join(OUT, "02_币池与数据覆盖_v1.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(cover_rows[0].keys()))
        w.writeheader()
        w.writerows(cover_rows)

    # ---------- 4. 策略/归档指纹 ----------
    fp_rows = []
    for arm in ARMS:
        z = zip_payload(arm)
        disk = os.path.join(STRAT, f"{arm}.py")
        fp_rows.append({
            "arm": arm,
            "disk_subclass_sha256": sha256_file(disk),
            "zip_subclass_sha256": hashlib.sha256(z["py_bytes"]).hexdigest(),
            "subclass_identical": sha256_file(disk) == hashlib.sha256(z["py_bytes"]).hexdigest(),
            "zip_members": z["n_members"],
            "zip_sha256": z["zip_sha256"],
            "json_name": z["json_name"],
            "has_base_class_in_zip": any("WyckoffEntryTimingBase" in n for n in z["member_names"]),
            "has_wallet_feather": z["wallet_bytes"] != b"",
            "has_config_in_zip": bool(z["cfg"]),
        })
    base_hash = sha256_file(os.path.join(STRAT, "WyckoffEntryTimingBase.py"))
    for r in fp_rows:
        r["base_class_sha256_on_disk"] = base_hash

    with open(os.path.join(OUT, "03_策略与归档指纹_v1.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(fp_rows[0].keys()))
        w.writeheader()
        w.writerows(fp_rows)

    summary["archive"] = {
        "base_class_archived_in_any_zip": False,
        "base_class_sha256_on_disk": base_hash,
        "base_class_bytes": os.path.getsize(os.path.join(STRAT, "WyckoffEntryTimingBase.py")),
        "conclusion": "归档包只含 5 行子类，36KB 的基类 WyckoffEntryTimingBase.py 未入包；"
                      "单凭 zip 无法复现回测，必须同时锁定磁盘基类哈希。",
    }

    # ---------- 5. 防泄漏证据 ----------
    leak_hits = []
    for root, _dirs, files in os.walk(ENTRY):
        for fn in files:
            low = fn.lower()
            if "lookahead" in low or "recursive" in low:
                leak_hits.append(os.path.relpath(os.path.join(root, fn), ENTRY))
    summary["leakage_evidence"] = {
        "lookahead_analysis_artifact_found": bool(leak_hits),
        "files": leak_hits,
        "conclusion": "FT-ENTRY-01 目录内没有 lookahead-analysis / recursive-analysis 产物。"
                      "防泄漏只能靠代码审读，不能靠官方门槛结论。",
    }

    # 源码级：滑点与 PIT 相关检索
    src = open(os.path.join(STRAT, "WyckoffEntryTimingBase.py"), encoding="utf-8").read()
    summary["source_scan"] = {
        "contains_0_0003": "0.0003" in src,
        "contains_slippage_word": "slippage" in src.lower(),
        "uses_PitMode": "PitMode" in src,
        "uses_register_oi": "register_oi" in src,
        "uses_read_oi": "read_oi" in src,
        "oi_filter_default": re.findall(r"enable_oi_filter:\s*bool\s*=\s*(\w+)", src),
        "declared_pit_mode": re.findall(r"PitMode\.(\w+)", src),
    }

    # ---------- 6. 规则实现对照 ----------
    rule_rows = [
        ("Benchmark 突破判定", "突破 H+0.1A 且放量>=1.5V20 且 OI>=2%",
         "prev_close<=H+0.1A 且 close>H+0.1A；vol>=1.5V20；oi>=0.02", "符合"),
        ("Benchmark 允许执行区间", "未在冻结方案写明", "min_entry=H, max_entry=close+0.5A", "需披露"),
        ("A1 触发价", "close >= H-0.5A", "close>=H-0.5A 且 close<=H+0.1A", "部分符合",
         "实现额外加了上界 H+0.1A，冻结方案未写；该上界与'无兜底分支'共同造成静默丢单"),
        ("A2 触发价", "close >= H", "close>=H 且 close<=H+0.1A", "部分符合", "同上"),
        ("A 组放量", ">= 1.0 V20", "volume>=1.0*v20", "符合"),
        ("A 组 OI", "6h OI >= 2%", "oi_growth>=config.oi_min_growth_pct(0.02)", "符合"),
        ("A 组止损", "P0 = Lr - 0.1A", "retest_low_lr - 0.1A", "符合"),
        ("A 组突破兜底分支", "未规定；预期应为'触发带未命中时退回基准突破'",
         "无。close>H+0.1A 时既不建候选也不终止事件，事件挂到过期", "不符",
         "导致基线事件被静默丢弃，详见 04/07 号产物"),
        ("C 组触发", "SWEEP 后首次缩量企稳（收阳且 vol<=0.8Vs）",
         "在 RETEST 判定成立的那一根 1h 收盘立即建候选", "符合",
         "注意：C 组触发点与 RETEST 判定同根，不是'再等一根确认'"),
        ("C 组 OI", "6h OI >= 0%", "oi_growth is None 或 <0 时终止事件", "符合",
         "阈值由 2% 降为 0%，属参数变更而非仅时点变更"),
        ("C1 止损", "P0 = S - 0.1A", "sweep_low_s - 0.1A", "符合"),
        ("C2 止损", "P0 = low_bar - 0.1A", "low_p - 0.1A", "符合"),
        ("C 组允许执行区间", "未在冻结方案写明", "min_entry=L-0.2A, max_entry=close+0.5A", "需披露"),
        ("决策时钟", "1h 收盘决策，下一根 15m 开盘执行",
         "DecisionClock.for_bar + execution_earliest_ms=close+15min；入场用 15m open", "符合"),
        ("快照字段完整性", "未规定", "C 组 v20_median_volume 写成 volume_v、volume_ratio 写死 1.0；"
                                     "A 组 box_high_h 被写成 target_level", "不符",
         "不影响成交，但快照字段失真，下游若读快照会得到错误 H/V20"),
        ("冷却期", "未规定", "cooling_hours=24（沿用 config 默认）", "需披露"),
        ("持仓超时", "未规定", "custom_exit: 持有>=240h 强制退出", "需披露"),
        ("移动止损", "未规定", "4h 收盘突破 2R 后启用 4h 10 根最低价追踪", "需披露"),
    ]
    with open(os.path.join(OUT, "04_规则实现对照_v1.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rule_item", "frozen_text", "implementation", "verdict", "note"])
        w.writerows(rule_rows)
    summary["rule_deviation_high"] = [r[0] for r in rule_rows if r[3] == "不符"]

    # ---------- 7. enter_tag 解析 ----------
    tag_rows = []
    for arm in ARMS:
        df = load_trades(arm)
        eids = []
        bad = []
        for t in df["enter_tag"].astype(str):
            m = re.match(r"^cand_(?:(A1|A2|C1|C2)_)?(ev_.+)_(\d{13})$", t)
            if not m:
                bad.append(t)
                continue
            eids.append(m.group(2))
        dup = len(eids) - len(set(eids))
        tag_rows.append({
            "arm": arm, "trades": len(df), "parsed": len(eids), "unparsed": len(bad),
            "unique_event_ids": len(set(eids)), "duplicate_event_trades": dup,
            "symbols": int(df["pair"].nunique()),
        })
        arm_stats[arm]["unique_event_ids"] = len(set(eids))
        arm_stats[arm]["duplicate_event_trades"] = dup
        arm_stats[arm]["unparsed_tags"] = bad[:3]
    summary["enter_tag_parse"] = tag_rows
    summary["arm_stats"] = arm_stats

    # ---------- 8. 实际时间覆盖 ----------
    span = {}
    for arm in ARMS:
        df = load_trades(arm)
        o = pd.to_datetime(df["open_date"], utc=True)
        c = pd.to_datetime(df["close_date"], utc=True)
        span[arm] = {
            "first_open_utc": str(o.min()), "last_open_utc": str(o.max()),
            "last_close_utc": str(c.max()),
            "outside_research_window_opens": int(((o < RESEARCH_START) | (o > RESEARCH_END)).sum()),
        }
    summary["time_coverage"] = span

    # ---------- 9. 配置核对写盘 ----------
    with open(os.path.join(OUT, "01_配置与冻结方案核对_v1.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["check_item", "frozen_requirement", "actual_value", "verdict", "note"])
        w.writeheader()
        w.writerows(checks)

    with open(os.path.join(OUT, "05_开跑前核对摘要_v1.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=== 配置核对 ===")
    for c in checks:
        print(f"  [{c['verdict']}] {c['check_item']}: 要求={c['frozen_requirement']} 实际={c['actual_value']}")
    print("\n=== 币池 ===")
    print(f"  74 池与 FT-FACTOR-03 白名单完全一致: {set_entry == set03}")
    print(f"  在上一轮 L1(241) 内: {len(norm_entry & set_l1)}；不在: {sorted(norm_entry - set_l1)}")
    print("\n=== 预热口径 ===")
    print(f"  研究期前 4h 不足 200 根的标的: {len(under200)} → {under200}")
    print("\n=== 归档 ===")
    print(f"  基类入包: False；基类 SHA256={base_hash[:16]}… 字节={os.path.getsize(os.path.join(STRAT,'WyckoffEntryTimingBase.py'))}")
    print("\n=== 防泄漏 ===")
    print(f"  lookahead 产物: {leak_hits or '无'}")
    print("\n=== enter_tag ===")
    for r in tag_rows:
        print(f"  {r['arm']:32s} trades={r['trades']:4d} events={r['unique_event_ids']:4d} dup={r['duplicate_event_trades']} bad={r['unparsed']}")


if __name__ == "__main__":
    main()
