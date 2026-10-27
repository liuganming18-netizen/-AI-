#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OOS-READY-01 自检：把报告里的关键数字与产物逐项核对。

任何一项不成立即抛 AssertionError，用于防止"报告写的和表里算的不一致"。
"""
import csv
import json
import os

BASE = "/Users/boos/Desktop/威科夫因子共振趋势/evidence/OOS-READY-01"
OUT = os.path.join(BASE, "out")
ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"


def load(name):
    return list(csv.DictReader(open(os.path.join(OUT, name), encoding="utf-8-sig")))


def main():
    checks = []

    # 1. 批次表行数
    bt = load("01_已观察范围_批次.csv")
    checks.append(("批次表 17 行", len(bt) == 17, len(bt)))

    # 2. 币种表行数
    st = load("01_已观察范围_币种.csv")
    checks.append(("币种表 507 行", len(st) == 507, len(st)))

    # 3. 资格表 8 段
    el = load("02_验证数据资格表.csv")
    checks.append(("资格表 8 段", len(el) == 8, len(el)))

    # 4. 三段事件数合计 = 296，候选合计 = 206
    seg = {r["段"]: r for r in el}
    u = sum(int(seg[s]["该段唯一事件数（296 口径）"]) for s in ("S3", "S4", "S5"))
    c = sum(int(seg[s]["该段候选数（206 口径）"]) for s in ("S3", "S4", "S5"))
    checks.append(("三段唯一事件合计 296", u == 296, u))
    checks.append(("三段候选合计 206", c == 206, c))

    # 5. 三段净额合计 ≈ 唯一交易总净额 81.436
    tot = sum(float(seg[s]["该段净额U"]) for s in ("S3", "S4", "S5"))
    tr = [json.loads(l) for l in open(os.path.join(ROOT, "evidence/FULL_SWEEP_200x100/trades_rich.jsonl"))]
    real = sum(float(t["net"]) for t in tr)
    checks.append(("三段净额合计≈296 笔总净额", abs(tot - real) < 0.02, round(tot, 4), round(real, 4)))

    # 6. 2022-01~2026-04-14 段为负、旧 Holdout 段为正
    checks.append(("S4 为负", float(seg["S4"]["该段净额U"]) < 0, seg["S4"]["该段净额U"]))
    checks.append(("S5 为正", float(seg["S5"]["该段净额U"]) > 0, seg["S5"]["该段净额U"]))

    # 7. FULL_SWEEP 窗口并集连续且起止正确
    probe = json.load(open(os.path.join(OUT, "00_数据覆盖探针.json")))
    iv = probe["sweep_window_union"]["merged_intervals"]
    checks.append(("窗口并集仅 1 段", len(iv) == 1, iv))
    checks.append(("并集起 2021-01-01 止 2026-10-13",
                   iv[0]["start"] == "2021-01-01" and iv[0]["end"] == "2026-10-13", iv[0]))

    # 8. 2020 年 OI 仅 1 币、2021 年 92 币
    cov = {(r["asset"], r["year"]): int(r["symbols_with_file"]) for r in load("00_数据覆盖探针.csv")}
    checks.append(("2020 年 OI 仅 1 币", cov[("oi_5m", "2020")] == 1, cov[("oi_5m", "2020")]))
    checks.append(("2021 年 OI 92 币", cov[("oi_5m", "2021")] == 92, cov[("oi_5m", "2021")]))
    checks.append(("2020 年 4h K线 56 币", cov[("candles", "2020")] == 56, cov[("candles", "2020")]))

    # 9. mark 覆盖：2022 起才有记录，2024/2025 年 100%
    mk = {r["year"]: float(r["mark_coverage_pct"]) for r in probe["funding_mark"]["by_year"]}
    checks.append(("mark 最早年份为 2022", min(mk) == "2022", sorted(mk)))
    checks.append(("2024/2025 mark 100%", mk["2024"] == 100.0 and mk["2025"] == 100.0, mk))

    # 10. 576 次运行中 0 次 verified
    man = list(csv.DictReader(open(os.path.join(ROOT, "evidence/BACKTEST-LOSS-02/all_runs_manifest_v2.csv"),
                                  encoding="utf-8-sig")))
    stt = {}
    for r in man:
        stt[r["historical_qualification_status"]] = stt.get(r["historical_qualification_status"], 0) + 1
    checks.append(("576 次运行 0 次 verified", "verified" not in stt and len(man) == 576, stt))

    # 11. 未覆盖段的接触状态必须是"资格未知"，不得写成"可证明未接触"
    unk = [s for s in ("S0", "S1", "S6") if "资格未知" in seg[s]["接触状态"]]
    checks.append(("S0/S1/S6 接触状态均为资格未知", len(unk) == 3, unk))
    checks.append(("资格表接触状态列不含旧表述",
                   all("可证明未进入研究样本" not in r["接触状态"] for r in el),
                   "查 CSV"))

    # 12. 未被覆盖的币中 52 个首个数据年份为 2026
    import glob
    plan = json.load(open(os.path.join(ROOT, "evidence/FULL_SWEEP_200x100/plan.json")))
    covered = {s for g in plan for s in g["symbols"]}
    allsym = {os.path.basename(os.path.dirname(os.path.dirname(f)))
              for f in glob.glob(os.path.join(ROOT, "数据/市场数据/parquet/candles/binance/um/*/4h/*.parquet"))}
    missing = allsym - covered

    def fy(s):
        ys = sorted(os.path.basename(f)[:4] for f in
                    glob.glob(os.path.join(ROOT, f"数据/市场数据/parquet/candles/binance/um/{s}/4h/*.parquet"))
                    if os.path.basename(f)[:4].isdigit())
        return ys[0] if ys else "?"
    n2026 = sum(1 for s in missing if fy(s) == "2026")
    checks.append(("未覆盖 55 币中 52 个为 2026 年", len(missing) == 55 and n2026 == 52, len(missing), n2026))

    ok = True
    for item in checks:
        name, passed = item[0], item[1]
        extra = item[2:]
        print(("PASS " if passed else "FAIL ") + name + ("" if not extra else f"  {extra}"))
        ok = ok and passed
    print("\n总计", len(checks), "项；", "全部通过" if ok else "存在失败项")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
