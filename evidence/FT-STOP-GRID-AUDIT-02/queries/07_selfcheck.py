# -*- coding: utf-8 -*-
"""07 自检：本轮所有产物是否存在、彼此是否自相矛盾。

只查"能机械判定"的东西：文件齐不齐、组数对不对、闭合误差是否为 0、
同一数字在两份产物里是否一致。不做主观评价。
"""
from __future__ import annotations

import json
import zipfile

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
CHECKS: list[dict] = []


def chk(name: str, ok: bool, detail="") -> None:
    CHECKS.append({"检查": name, "结果": "通过" if ok else "不通过", "说明": detail})


def load(name: str):
    p = OUT / name
    if not p.exists():
        return None
    if p.suffix == ".json":
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return pd.read_csv(p, encoding="utf-8-sig")


def main() -> None:
    need = ["00_开跑前核对.json", "01_冻结分析计划.json", "02_跟踪与完整性核对.json",
            "02b_账户对账.json", "03_指标重算.json", "03_逐组指标.csv",
            "04_配对与分解.json", "04_Δ分解.csv", "04b_稳定性裁决.json",
            "05_跳过与影子收益.json", "06_K线抽核.json", "06b_假设结束日.json"]
    miss = [n for n in need if not (OUT / n).exists()]
    chk("产物齐全", not miss, f"缺失={miss}")

    pre = load("00_开跑前核对.json")
    if pre:
        n = pre.get("问题数", pre.get("问题总数"))
        chk("开跑前核对 问题数=0", n == 0, f"问题数={n}")

    per = load("03_逐组指标.csv")
    ok = per[per["状态"] == "ok"] if per is not None else pd.DataFrame()
    chk("03 组数=36", len(ok) == 36, f"实际={len(ok)}")
    cnt = ok.groupby("档位").size().to_dict() if len(ok) else {}
    chk("每档 9 组", all(cnt.get(t) == 9 for t in L.TIERS), str(cnt))
    dup = ok["策略"].duplicated().sum() if len(ok) else -1
    chk("无重复组", dup == 0, f"重复={dup}")

    m3 = load("03_指标重算.json")
    if m3:
        f = m3["资金费闭合对账"]
        chk("资金费逐笔零误差", f["误差超1e-6的笔数合计"] == 0,
            f"超差笔数={f['误差超1e-6的笔数合计']} 最大={f['最大逐笔绝对误差']}")
        m = m3["MTM 期末权益与自报闭合"]
        chk("MTM 期末权益闭合", m["最大绝对差USDT"] is not None and m["最大绝对差USDT"] < 1e-6,
            f"最大差={m['最大绝对差USDT']}")
        chk("资金事件数>0", f["资金事件总数"] > 0, f"事件数={f['资金事件总数']}")

    dec = load("04_Δ分解.csv")
    m4 = load("04_配对与分解.json")
    if dec is not None and m4:
        chk("Δ分解 恒等式闭合", m4["Δ分解"]["恒等式校验最大误差"] < 1e-9,
            f"误差={m4['Δ分解']['恒等式校验最大误差']}")
        chk("Δ分解 36 行", len(dec) == 36, f"实际={len(dec)}")

    # 03 与 02 的笔数一致
    i2 = load("02_跟踪与完整性核对.json")
    if i2 and len(ok):
        tot03 = int(ok["笔数"].sum())
        got = None
        for k in ("逐笔总数", "总笔数", "笔数"):
            if k in i2:
                got = i2[k]
                break
        if got is not None:
            chk("02 与 03 笔数一致", int(got) == tot03, f"02={got} 03={tot03}")

    # 04b 汇总条数与判据数一致
    m4b = load("04b_稳定性裁决.json")
    if m4b:
        for t, v in m4b.get("汇总", {}).items():
            chk(f"04b {t} 条数=判据数",
                v["满足条数"] + len(v["未满足"]) == len(v["判据"]),
                f"{v['满足条数']}+{len(v['未满足'])} vs {len(v['判据'])}")

    # 06 抽核笔数
    m6 = load("06_K线抽核.json")
    if m6:
        chk("06 抽核 80 笔", m6["抽核笔数"] == 80, f"实际={m6['抽核笔数']}")
        chk("06 无不一致", len(m6["不一致清单"]) == 0, f"不一致={len(m6['不一致清单'])}")

    # 结果包齐全（36 个 zip）
    zips = list((L.UP / "out").glob("backtest-result-Wyckoff_C1_Stop*.zip"))
    chk("上游结果包 36 个", len(zips) == 36, f"实际={len(zips)}")

    # 日志/结果包里的自报笔数与 CSV 行数一致
    bad = []
    for name in L.ALL:
        p = L.UP / f"out/backtest-result-{name}.zip"
        csv = L.UP / f"out/trades_{name}.csv"
        if not p.exists() or not csv.exists():
            bad.append(name)
            continue
        try:
            with zipfile.ZipFile(p) as zf:
                nm = [x for x in zf.namelist() if x.endswith(".json") and not x.endswith(".meta.json")]
                d = json.loads(zf.read(nm[0]))
            st = d.get("strategy", {}).get(name, {})
            n_self = st.get("total_trades")
            n_csv = len(pd.read_csv(csv, encoding="utf-8-sig"))
            if n_self is not None and int(n_self) != int(n_csv):
                bad.append(f"{name}: 自报{n_self} vs CSV{n_csv}")
        except Exception as e:  # noqa: BLE001
            bad.append(f"{name}: {e}")
    chk("自报成交数与 CSV 行数一致", not bad, f"异常={bad[:5]}")

    df = pd.DataFrame(CHECKS)
    df.to_csv(OUT / "07_自检.csv", index=False, encoding="utf-8-sig")
    n_bad = int((df["结果"] != "通过").sum())
    with open(OUT / "07_自检.json", "w", encoding="utf-8") as f:
        json.dump({"工单": "FT-STOP-GRID-AUDIT-02 / 07 自检",
                   "检查项": len(df), "通过": int(len(df) - n_bad), "不通过": n_bad,
                   "明细": CHECKS}, f, ensure_ascii=False, indent=2, default=str)
    print(f"检查 {len(df)} 项，通过 {len(df) - n_bad}，不通过 {n_bad}")
    if n_bad:
        print(df[df["结果"] != "通过"].to_string())


if __name__ == "__main__":
    main()
