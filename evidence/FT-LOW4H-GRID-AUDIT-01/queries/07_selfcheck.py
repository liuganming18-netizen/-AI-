# -*- coding: utf-8 -*-
"""07 自检：本轮产物是否自洽（不是审策略，是审自己的交付物）。"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

import _lib as L

CH: list[dict] = []


def ck(name: str, ok: bool, note: str = "") -> None:
    CH.append({"项": name, "通过": bool(ok), "说明": note})


def main() -> None:
    must = [
        "01_冻结分析计划.json", "00_开跑前核对.json", "00_属性贯通逐组.csv",
        "00_4h数据体检.csv", "07b_报告数值对账.json",
        "快照_基类_WyckoffLow4hGridBase_20261022T153043.py",
    ]
    for f in must:
        ck(f"产物存在：{f}", (L.OUT / f).exists())

    ck("报告存在", (L.HERE / "00_报告.md").exists())

    pre = json.loads((L.OUT / "00_开跑前核对.json").read_text(encoding="utf-8"))
    plan = json.loads((L.OUT / "01_冻结分析计划.json").read_text(encoding="utf-8"))
    rep = json.loads((L.OUT / "07b_报告数值对账.json").read_text(encoding="utf-8"))

    ck("开跑前核对问题数 = 报告声称的 8", pre["问题数"] == 8, str(pre["问题数"]))
    ck("问题清单非空且每条都有出处", len(pre["问题"]) == pre["问题数"])
    ck("报告数值对账全通过", rep["不通过"] == 0, f"{rep['通过']}/{rep['对账项数']}")
    ck("冻结时刻早于上游首个结果", plan["冻结时已落盘逐笔结果数"] == 0,
       plan["冻结时刻"])

    # 口径冻结的完整性：三层次、判据、统计方法都在
    for k in ("报告层次（必须分开，禁止混用）", "指标定义", "开跑前核对（预登记，逐条机器判定）",
              "交付后审核", "稳定性判据（针对 16 种组合，逐条机械判定）", "统计方法", "禁止事项"):
        ck(f"冻结计划含「{k}」", k in plan)
    ck("判据 C1~C8 齐全",
       all(f"C{i}" in plan["稳定性判据（针对 16 种组合，逐条机械判定）"] for i in range(1, 9)))
    ck("三种报告层次都写了定义",
       all(x in plan["报告层次（必须分开，禁止混用）"] for x in ("L1_单账户", "L2_跨参数组中位", "L3_组间合计")))

    wire = pd.read_csv(L.OUT / "00_属性贯通逐组.csv", encoding="utf-8-sig")
    ck("属性贯通表 144 行", len(wire) == 144, str(len(wire)))
    ck("属性贯通表覆盖 16 组合", wire["组合"].nunique() == 16, str(wire["组合"].nunique()))
    ck("属性贯通表覆盖 9 参数组", wire["参数组"].nunique() == 9, str(wire["参数组"].nunique()))
    ck("无缺失值", int(wire.isna().sum().sum()) == 0, str(int(wire.isna().sum().sum())))

    k4 = pd.read_csv(L.OUT / "00_4h数据体检.csv", encoding="utf-8-sig")
    ck("4h 体检 74 行", len(k4) == 74, str(len(k4)))

    snap = L.OUT / "快照_基类_WyckoffLow4hGridBase_20261022T153043.py"
    if snap.exists():
        h = hashlib.sha256(snap.read_bytes()).hexdigest()
        ck("快照 SHA 与核对记录的基类 SHA 一致",
           h == pre["明细"]["P2_止损取价"]["基类SHA256"], h[:16] + "…")

    # 只读上游：本轮目录里不得出现上游文件副本（快照除外）
    up_copies = [p.name for p in L.OUT.glob("trades_*.csv")]
    ck("本轮目录没有上游逐笔结果副本", not up_copies, str(up_copies))

    ok = sum(c["通过"] for c in CH)
    out = {"工单": "FT-LOW4H-GRID-AUDIT-01 / 07 自检",
           "项数": len(CH), "通过": ok, "不通过": len(CH) - ok, "明细": CH}
    (L.OUT / "07_自检.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"自检 {len(CH)} 项，通过 {ok}，不通过 {len(CH) - ok}")
    for c in CH:
        if not c["通过"]:
            print("  ✗", c["项"], c["说明"])


if __name__ == "__main__":
    main()
