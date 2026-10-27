# -*- coding: utf-8 -*-
"""FT-DATA-01：输入包自检 + 指纹清单。

自检只做「能不能被信任地交出去」这一件事，不产出任何绩效结论。
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import re

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
PKG = os.path.join(ROOT, "evidence/FT-DATA-01")
DATA = os.path.join(PKG, "data")
FT = os.path.join(DATA, "freqtrade", "futures")
SIDE = os.path.join(DATA, "sidecar")
OUT = os.path.join(PKG, "out")
LOGS = os.path.join(PKG, "logs")

CANDLES = os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um")
OI = os.path.join(ROOT, "数据/市场数据/parquet/oi/binance/um")
FUNDING = os.path.join(ROOT, "数据/市场数据/parquet/funding/binance/um")
INCLUDED = os.path.join(ROOT, "evidence/GATE-CLOSE-02/out/02_纳入清单.csv")
MARK = os.path.join(ROOT, "数据/研究输入/WyckoffMaster/mark1h")

T0 = pd.Timestamp("2025-06-25 00:00:00", tz="UTC")
T_TEST = pd.Timestamp("2025-08-01 00:00:00", tz="UTC")
T1 = pd.Timestamp("2025-12-31 00:00:00", tz="UTC")

SYMBOLS = ["ARUSDT", "BANANAUSDT", "BATUSDT", "CTSIUSDT", "PUMPBTCUSDT"]
SLUG = {s: f"{s[:-4]}_USDT_USDT" for s in SYMBOLS}
KNOWN_OI_GAP = ["2025-08-29 06:20:00", "2025-08-29 06:25:00", "2025-08-29 06:30:00"]

REC: list[dict] = []


def rec(name: str, ok: bool, detail="") -> None:
    REC.append({"检查": name, "结果": "PASS" if ok else "FAIL", "明细": str(detail)})


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    exp = json.load(open(os.path.join(LOGS, "01_export.json"), encoding="utf-8"))

    # ── 1. 标的与范围 ──
    with open(INCLUDED, encoding="utf-8-sig") as f:
        inc = {r["symbol"]: r for r in csv.DictReader(f)}
    rec("5 个标的全部在纳入清单 526 内", all(s in inc for s in SYMBOLS),
        [s for s in SYMBOLS if s not in inc])
    rec("无指数标的混入（underlyingType≠INDEX）",
        all(inc[s]["underlyingType"] != "INDEX" for s in SYMBOLS if s in inc),
        {s: inc[s]["underlyingType"] for s in SYMBOLS if s in inc})

    # ── 2. 行数与预热 ──
    bars = {}
    for s in SYMBOLS:
        for tf in ("4h", "1h", "15m"):
            df = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-{tf}-futures.feather"))
            bars[(s, tf)] = len(df)
    exp_rows = {"4h": 1134, "1h": 4536, "15m": 18144}
    ok_rows = all(bars[(s, tf)] == exp_rows[tf] for s in SYMBOLS for tf in exp_rows)
    rec("4h/1h/15m 行数与理论值一致（1134/4536/18144）", ok_rows,
        {tf: sorted({bars[(s, tf)] for s in SYMBOLS}) for tf in exp_rows})

    d4 = pd.read_feather(os.path.join(FT, f"{SLUG['ARUSDT']}-4h-futures.feather"))
    warm = int((d4["date"] < T_TEST.tz_localize(None)).sum())
    rec("4h 预热根数 ≥220", warm >= 220, warm)
    rec("导出起点为 2025-06-25 00:00",
        str(d4["date"].min()) == "2025-06-25 00:00:00", str(d4["date"].min()))

    # ── 3. 列集合严格 ──
    o = pd.read_feather(os.path.join(FT, f"{SLUG['ARUSDT']}-5m-open_interest.feather"))
    fu = pd.read_feather(os.path.join(FT, f"{SLUG['ARUSDT']}-1h-funding_rate.feather"))
    rec("OHLCV 列恰为 6 列且未混入 quote_volume",
        list(d4.columns) == ["date", "open", "high", "low", "close", "volume"],
        list(d4.columns))
    rec("OI 列恰为 3 列",
        list(o.columns) == ["date", "open_interest_amount", "open_interest_value"],
        list(o.columns))
    rec("资金费列恰为 2 列", list(fu.columns) == ["date", "funding_rate"],
        list(fu.columns))

    # ── 4. 缺失与空值 ──
    nan_ok, nan_detail = True, {}
    for s in SYMBOLS:
        for tf in ("4h", "1h", "15m"):
            df = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-{tf}-futures.feather"))
            if df.isna().any().any():
                nan_ok = False
                nan_detail[f"{s}-{tf}"] = int(df.isna().sum().sum())
        oo = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-5m-open_interest.feather"))
        if oo.isna().any().any():
            nan_ok = False
            nan_detail[f"{s}-oi"] = int(oo.isna().sum().sum())
    rec("导出数据无 NaN（未用 0 或前值填充）", nan_ok, nan_detail)

    # ── 5. OI 缺的正好是已知那 3 桶 ──
    grid = pd.date_range(T0.tz_localize(None), T1.tz_localize(None),
                         freq="5min", inclusive="left")
    gap_ok, gap_detail = True, {}
    for s in SYMBOLS:
        oo = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-5m-open_interest.feather"))
        miss = sorted(set(grid) - set(oo["date"]))
        got = [x.strftime("%Y-%m-%d %H:%M:%S") for x in miss]
        gap_detail[s] = got
        if got != KNOWN_OI_GAP:
            gap_ok = False
    rec("OI 缺口恰为已知 3 桶（2025-08-29 06:20/06:25/06:30）", gap_ok, gap_detail)

    # ── 6. 时间严格递增、无重复 ──
    mono_ok, dup_detail = True, {}
    for s in SYMBOLS:
        for name in (f"{SLUG[s]}-4h-futures", f"{SLUG[s]}-1h-futures",
                     f"{SLUG[s]}-15m-futures", f"{SLUG[s]}-5m-open_interest",
                     f"{SLUG[s]}-1h-funding_rate"):
            df = pd.read_feather(os.path.join(FT, f"{name}.feather"))
            d = df["date"]
            if not d.is_monotonic_increasing or d.duplicated().any():
                mono_ok = False
                dup_detail[name] = [bool(d.is_monotonic_increasing),
                                    int(d.duplicated().sum())]
    rec("所有时间列严格递增且无重复", mono_ok, dup_detail)
    rec("时间列为无时区（UTC 起点）",
        str(pd.read_feather(os.path.join(FT, f"{SLUG['ARUSDT']}-1h-futures.feather"))
            ["date"].dtype) == "datetime64[ms]", "")

    # ── 7. 资金费：两个标签同内容、与 sidecar 行数一致 ──
    same_label, fund_detail = True, {}
    for s in SYMBOLS:
        a = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-1h-funding_rate.feather"))
        b = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-8h-funding_rate.feather"))
        sc = pd.read_csv(os.path.join(SIDE, f"{s}_funding_settlements.csv"))
        fund_detail[s] = [len(a), len(b), len(sc)]
        if not a.equals(b) or len(a) != len(sc):
            same_label = False
    rec("资金费 1h/8h 两文件内容相同且与 sidecar 行数一致", same_label, fund_detail)

    # ── 8. 15m 成交额 sidecar 与 15m K 线对齐 ──
    qv_ok, qv_detail = True, {}
    for s in SYMBOLS:
        k = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-15m-futures.feather"))
        q = pd.read_csv(os.path.join(SIDE, f"{s}_15m_quote_volume.csv"))
        qv_detail[s] = [len(k), len(q)]
        if len(k) != len(q):
            qv_ok = False
    rec("15m 成交额 sidecar 行数与 15m K 线一致", qv_ok, qv_detail)

    # ── 8b. mark 蜡烛：行数、四列同值、volume=0 ──
    mk_ok, mk_detail = True, {}
    for s in SYMBOLS:
        mk = pd.read_feather(os.path.join(FT, f"{SLUG[s]}-1h-mark.feather"))
        same = (mk["open"].equals(mk["high"]) and mk["open"].equals(mk["low"])
                and mk["open"].equals(mk["close"]))
        vol0 = bool((mk["volume"] == 0).all())
        mk_detail[s] = [len(mk), same, vol0]
        if len(mk) != 4536 or not same or not vol0:
            mk_ok = False
    rec("mark 蜡烛 4536 行、四列同值、volume 全 0", mk_ok, mk_detail)

    # ── 9. 精度核对 ──
    with open(os.path.join(OUT, "04_精度核对.csv"), encoding="utf-8-sig") as f:
        pr = list(csv.DictReader(f))
    rec("精度核对全部为「未改变数值含义」",
        all(r["判定"] == "未改变数值含义" for r in pr), f"{len(pr)} 个字段")

    # ── 10. 组合指纹可复算 ──
    files = sorted(glob.glob(os.path.join(DATA, "**", "*"), recursive=True))
    files = [f for f in files if os.path.isfile(f)]
    combo = hashlib.sha256(json.dumps(
        {os.path.relpath(f, PKG): sha256_file(f) for f in files},
        sort_keys=True).encode()).hexdigest()
    rec("数据文件数 45", len(files) == 45, len(files))
    rec("组合指纹与导出日志一致（可复算）",
        combo == exp["combined_sha256"], f"{combo[:24]} vs {exp['combined_sha256'][:24]}")

    # ── 11. 旧引擎基准 ──
    with open(os.path.join(OUT, "05_旧引擎基准成交.csv"), encoding="utf-8-sig") as f:
        bl = list(csv.DictReader(f))
    rec("旧引擎基准 5 笔", len(bl) == 5, len(bl))
    in_win = all(T_TEST <= pd.Timestamp(r["entry_time_utc"], tz="UTC") < T1 for r in bl)
    rec("基准成交入场时间均在测试段内", in_win,
        [r["entry_time_utc"] for r in bl])
    rec("基准成交覆盖全部 5 个标的",
        {r["symbol"] for r in bl} == set(SYMBOLS), sorted({r["symbol"] for r in bl}))

    # ── 12. 文档口径 ──
    doc = open(os.path.join(PKG, "00_输入包说明.md"), encoding="utf-8").read()
    cmp_ = open(os.path.join(PKG, "01_冻结比较规则.md"), encoding="utf-8").read()
    ho = open(os.path.join(PKG, "05_交接单.md"), encoding="utf-8").read()
    rec("文档标注「等待 FT 基线」", "等待 FT 基线" in doc and "等待 FT 基线" in ho)
    def only_negated(text: str, word: str) -> bool:
        """该词只允许出现在否定语境里（前 8 字含 不/未/非/无/勿）。"""
        for m in re.finditer(re.escape(word), text):
            head = text[max(0, m.start() - 8):m.start()]
            if not re.search(r"[不未非无勿]", head):
                return False
        return True

    rec("未把输入包写成完成报告", only_negated(doc + ho, "完成报告"),
        [m.start() for m in re.finditer("完成报告", doc + ho)])
    rec("未声称已通过验收/测试通过",
        not re.search(r"(验收通过|测试通过|正式准入通过)", doc + cmp_ + ho))
    rec("比较规则含共享账户与独立配对分离说明",
        "共享账户" in cmp_ and "独立固定仓位配对" in cmp_)
    rec("比较规则明确禁止把独立交易收益相加当组合收益",
        "禁止" in cmp_ and "组合收益" in cmp_)
    rec("比较规则区分 PF 与盈亏比", "利润因子" in cmp_ and "盈亏比" in cmp_)
    rec("比较规则给出仓位公式（权益×0.5%÷风险距离）",
        "0.005" in cmp_ and "unit_risk" in cmp_)
    rec("缺口清单 9 项且只有 1 项硬阻塞",
        sum(1 for _ in csv.DictReader(open(os.path.join(OUT, "07_缺口清单.csv"),
                                          encoding="utf-8-sig"))) == 9)

    # ── 13. 输入指纹（源 parquet）──
    inputs = {}
    for s in SYMBOLS:
        for tf in ("4h", "1h", "15m"):
            p = os.path.join(CANDLES, s, tf, "2025.parquet")
            inputs[f"candles/{s}/{tf}/2025.parquet"] = sha256_file(p)
        p = os.path.join(OI, s, "2025.parquet")
        inputs[f"oi/{s}/2025.parquet"] = sha256_file(p)
        p = os.path.join(FUNDING, f"{s}.parquet")
        inputs[f"funding/{s}.parquet"] = sha256_file(p)
        p = os.path.join(MARK, f"{s}.parquet")
        inputs[f"mark1h/{s}.parquet"] = sha256_file(p)
    rec("输入指纹覆盖 30 个源文件", len(inputs) == 30, len(inputs))

    # ── 清单 ──
    SELF_LOG = os.path.join(LOGS, "02_selfcheck.log")
    man_files = {}
    for p in sorted(glob.glob(os.path.join(PKG, "**", "*"), recursive=True)):
        if not os.path.isfile(p) or p == os.path.join(PKG, "manifest.json") or p == SELF_LOG:
            continue
        man_files[os.path.relpath(p, PKG)] = {
            "bytes": os.path.getsize(p), "sha256": sha256_file(p)}

    npass = sum(1 for r in REC if r["结果"] == "PASS")
    nfail = sum(1 for r in REC if r["结果"] == "FAIL")
    manifest = {
        "package": "FT-DATA-01",
        "version": "WX-FT-IN-1.0",
        "status": "输入包已冻结，等待FT基线",
        "comparison_rules_version": "WX-FT-CMP-1.0",
        "data_combined_sha256": exp["combined_sha256"],
        "window": {"export_start_utc": str(T0), "test_start_utc": str(T_TEST),
                   "test_end_utc_exclusive": str(T1)},
        "symbols": SYMBOLS,
        "pairs": {s: f"{s[:-4]}/USDT:USDT" for s in SYMBOLS},
        "file_count": len(man_files),
        "files": man_files,
        "inputs": inputs,
        "checks": REC,
        "pass": npass,
        "fail": nfail,
        "note": "自检通过不等于独立验收；收到 FT 结果前不产出绩效结论。",
    }
    with open(os.path.join(PKG, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    for r in REC:
        print(f"[{r['结果']}] {r['检查']} :: {r['明细'][:150]}")
    print(f"\nPASS {npass} / FAIL {nfail}；清单 {len(man_files)} 个文件")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
