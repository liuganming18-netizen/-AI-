# -*- coding: utf-8 -*-
"""08 本轮审核自检。

检查四件事：
  A 产物齐全（报告 + 27 个 out 文件 + 9 个脚本）
  B 报告结构完整（§0~§10 章节、表格数、字数）
  C 各脚本判据统计（一致性类必须全通过；结论类点名放行）
  D 交叉一致性（不同脚本算出的同一个量必须相等）
  E 已作废旧值不得回流（出现时必须带"作废/不采用/更正"标记）
  F 报告里的复现命令必须指向真实存在的脚本

输出：out/08_自检.json
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

import _lib as L

OUT = L.OUT
REP = L.HERE / "00_报告.md"

CHECKS: list[dict] = []


def ok(sec: str, item: str, passed: bool, detail: str = "") -> None:
    CHECKS.append({"段": sec, "项": item, "通过": bool(passed), "说明": detail})


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- A 产物齐全

EXPECT_OUT = [
    "01_冻结分析计划.json", "01_运行来源核实.json", "01_运行时证据逐组.csv",
    "01_派生类属性.csv", "01_仓位反推s.csv", "01_初始止损比例反证.csv", "01_组合行为差异.csv",
    "02_执行核对.json", "02_逐笔重建明细.csv", "02_分类与标签交叉.csv", "02_退出分类逐组合.csv",
    "03_账户与指标.json", "03_逐组指标.csv", "03_MTM期末闭合.csv", "03_资金费对账.csv",
    "03_十六组合三层次.csv",
    "04_稳定性.json", "04_稳定性逐组合.csv", "04_极端盈利依赖.csv", "04_参数组离散.csv", "04_前后半段.csv",
    "05_配对与终裁.json", "05_逐配对.csv", "05_组合汇总.csv", "05_跳过统计.csv", "05_稳定性终裁.csv",
    "06_系统核对.json", "07_报告复核.json", "07_报告数字复核.csv", "07b_报告数值对账.json",
    "07b_报告数值对账.csv",
    "09_产物清单.csv", "09_产物清单.md", "09_144组与产物对应.csv",
]
EXPECT_Q = ["_lib.py", "00_freeze_plan.py", "01_provenance.py", "02_execution.py", "03_account.py",
            "04_stability.py", "05_pairing.py", "06_system.py", "07_report_diff.py",
            "07b_报告数值对账.py", "08_selfcheck.py", "09_manifest.py"]

miss_out = [f for f in EXPECT_OUT if not (OUT / f).exists()]
ok("A 产物", "out 文件齐全", not miss_out, f"{len(EXPECT_OUT) - len(miss_out)}/{len(EXPECT_OUT)}" +
   (f"；缺 {miss_out}" if miss_out else ""))
miss_q = [f for f in EXPECT_Q if not (L.HERE / "queries" / f).exists()]
ok("A 产物", "queries 脚本齐全", not miss_q, f"{len(EXPECT_Q) - len(miss_q)}/{len(EXPECT_Q)}" +
   (f"；缺 {miss_q}" if miss_q else ""))
ok("A 产物", "报告存在且非空", REP.exists() and REP.stat().st_size > 20000,
   f"{REP.stat().st_size} 字节" if REP.exists() else "不存在")

# ---------------------------------------------------------------- B 报告结构

TXT = REP.read_text(encoding="utf-8")
TXT_N = TXT.replace("\u2212", "-")
heads = re.findall(r"^## (.+)$", TXT, re.M)
ok("B 报告", "章节数 = 11（§0~§10）", len(heads) == 11, f"实际 {len(heads)} 节：{heads}")
for h in ["0. 结论摘要", "一、运行来源核实", "二、逐笔退出规则与因果执行", "三、账户、费用与真实权益回撤",
          "四、16 种组合比较与稳定性", "五、与上一轮的配对比较", "六、系统展示核对",
          "七、上游报告的错误与更正", "八、逐项判定与复现命令", "九、最终回答", "十、不做的事（边界声明）"]:
    ok("B 报告", f"章节存在：{h}", any(h in x for x in heads))

n_tab = len(re.findall(r"^\|", TXT, re.M))
ok("B 报告", "表格行数 ≥ 150", n_tab >= 150, f"实际 {n_tab} 行")
for kw in ["哪些结果可信", "值得冻结进入下一阶段", "收益与回撤的代价", "可证明未参与选参的留出数据",
           "页面未验收", "不建议把任何一种冻结"]:
    ok("B 报告", f"最终回答覆盖：{kw}", kw in TXT)

# ---------------------------------------------------------------- C 判据统计

def js(n):
    with open(OUT / n, encoding="utf-8") as f:
        return json.load(f)


CONSISTENT = {                     # 一致性类：必须全通过
    "01_运行来源核实.json": 27,
    "02_执行核对.json": 17,
    "03_账户与指标.json": 10,
    "06_系统核对.json": 13,
    "07_报告复核.json": 14,
    "07b_报告数值对账.json": 125,
}
for f, n in CONSISTENT.items():
    st = js(f).get("判据统计", {})
    ok("C 判据", f"{f} 全通过", st.get("总") == n and st.get("通过") == n,
       f"总 {st.get('总')} / 通过 {st.get('通过')} / 不通过 {st.get('不通过')}（期望 {n}/{n}）")

VERDICT_FALSE = {                  # 结论类：预期为 False，是审核结论本身
    ("配对", "BH-FDR 后 p<0.05 的配对数"): "57 个显著 ⇒ 与上一轮的差异不是纯噪声",
    ("终裁", "达到 5 条门槛的组合"): "0 个达到门槛 ⇒ 不建议冻结任何组合",
}
crit5 = js("05_配对与终裁.json")["判据"]
f5 = {(c["段"], c["项"]) for c in crit5 if not c["通过"]}
ok("C 判据", "05 的不通过项恰好是两条结论型判据", f5 == set(VERDICT_FALSE),
   f"实际 {sorted(f5)}")
for k, why in VERDICT_FALSE.items():
    ok("C 判据", f"结论型判据已记录：{k[1]}", k in f5, why)
ok("C 判据", "04 为描述性分析（无二元判据）", js("04_稳定性.json").get("判据统计", {}).get("总") == 0)

# ---------------------------------------------------------------- D 交叉一致性

a02 = js("02_执行核对.json")["段"]
a03 = js("03_账户与指标.json")["段"]
a05 = js("05_配对与终裁.json")["段"]
a06 = js("06_系统核对.json")["段"]
a01 = js("01_运行来源核实.json")["段"]
t051 = pd.read_csv(OUT / "05_稳定性终裁.csv", encoding="utf-8-sig")
t031 = pd.read_csv(OUT / "03_逐组指标.csv", encoding="utf-8-sig")
cls = pd.read_csv(OUT / "02_退出分类逐组合.csv", encoding="utf-8-sig").set_index("组合")

ok("D 交叉", "组数一致（01/02/03）", a01["P4_行为反证"]["逐笔表"]["组数"] == a02["规模"]["组数"] ==
   a03["完整性"]["组数"] == 144, f'{a01["P4_行为反证"]["逐笔表"]["组数"]}/{a02["规模"]["组数"]}/{a03["完整性"]["组数"]}')
ok("D 交叉", "笔数一致（01/02/03/06）", a01["P4_行为反证"]["逐笔表"]["笔数"] == a02["规模"]["笔数"] ==
   a03["完整性"]["总笔数"] == a06["接口"]["批次 trades.count"] == 9849,
   f'{a01["P4_行为反证"]["逐笔表"]["笔数"]}/{a02["规模"]["笔数"]}/{a03["完整性"]["总笔数"]}/{a06["接口"]["批次 trades.count"]}')
ok("D 交叉", "四类构成之和 = 总笔数",
   sum(a02["分类"]["四类构成（重建）"].values()) == a02["规模"]["笔数"],
   f'{a02["分类"]["四类构成（重建）"]} → {sum(a02["分类"]["四类构成（重建）"].values())}')
ok("D 交叉", "逐组合四类之和 = 各组合笔数",
   int(cls.sum(axis=1).sum()) == a02["规模"]["笔数"] and
   bool((cls.sum(axis=1) == t031.groupby("组合")["笔数"].sum()).all()),
   f"{int(cls.sum(axis=1).sum())}")
ok("D 交叉", "03 手续费合计 = 逐组表合计",
   abs(a03["账户"]["手续费合计"] - t031["手续费USDT"].sum()) < 1e-6,
   f'{a03["账户"]["手续费合计"]:.4f} vs {t031["手续费USDT"].sum():.4f}')
ok("D 交叉", "03 资金费合计 = 逐组表合计",
   abs(a03["账户"]["资金费合计"] - t031["资金费USDT"].sum()) < 1e-6,
   f'{a03["账户"]["资金费合计"]:.4f} vs {t031["资金费USDT"].sum():.4f}')
ok("D 交叉", "05 本轮总R合计 = 03 总R合计",
   abs(pd.read_csv(OUT / "05_逐配对.csv", encoding="utf-8-sig")["本轮总R"].sum() - t031["总R"].sum()) < 1e-6,
   f'{pd.read_csv(OUT / "05_逐配对.csv", encoding="utf-8-sig")["本轮总R"].sum():.4f}')
ok("D 交叉", "16 组合 L3 合计 = 144 组总USDT",
   abs(t051["L3合计"].sum() - t031["总USDT"].sum()) < 1e-6,
   f'{t051["L3合计"].sum():.4f} vs {t031["总USDT"].sum():.4f}')
ok("D 交叉", "16 组合笔数合计 = 9,849", int(t051["组数"].sum()) == 144 and
   int(t031["笔数"].sum()) == 9849, f'{int(t051["组数"].sum())} 组 / {int(t031["笔数"].sum())} 笔')
ok("D 交叉", "05 配对数 = 144", a05["配对"]["配对数"] == 144)
ok("D 交叉", "上一轮四档净收益 = 报告 §5.1",
   [round(r["净收益"], 2) for r in a05["上一轮基准"]["按止损档位的 L3 合计（原始账本重算）"]] ==
   [695.44, -82.55, -25.13, -466.57])
ok("D 交叉", "06 批次 net_pnl_sum = 我方净收益（±0.01）",
   abs(a06["接口"]["批次 net_pnl_sum（由逐笔行现算）"] - t031["总USDT"].sum()) < 0.01,
   f'{a06["接口"]["批次 net_pnl_sum（由逐笔行现算）"]} vs {t031["总USDT"].sum():.4f}')

# ---------------------------------------------------------------- E 旧值不得回流

BANNED = ["2,849", "95.6%", "30.07%", "极差中位 **280.27", "8/16", "-757.38", "102%~332%",
          "92%~205%", "3.65%", "5.49%", "L2 中位为正的 13 个组合", "毛利的 17%"]
ALLOW = ["作废", "不采用", "更正", "错用", "草稿"]
leak = []
for i, ln in enumerate(TXT_N.splitlines(), 1):
    for b in BANNED:
        if b in ln and not any(a in ln for a in ALLOW):
            leak.append(f"第 {i} 行出现 {b}")
ok("E 旧值", "已作废旧值未回流", not leak,
   "全部旧值只出现在带『作废/不采用/更正』标记的自我更正句里" if not leak else "；".join(leak))

# ---------------------------------------------------------------- F 复现命令

cmds = re.findall(r"`python ([\w\u4e00-\u9fa5]+\.py)`", TXT)
missing = [c for c in sorted(set(cmds)) if not (L.HERE / "queries" / c).exists()]
ok("F 复现", "报告里的复现命令都指向真实脚本", not missing,
   f"引用 {sorted(set(cmds))}" + (f"；缺 {missing}" if missing else ""))

# ---------------------------------------------------------------- 汇总

bad = [c for c in CHECKS if not c["通过"]]
res = {
    "说明": "本轮审核的自检：产物齐全 / 报告结构 / 判据统计 / 交叉一致性 / 旧值不回流 / 复现命令",
    "判据统计": {"总": len(CHECKS), "通过": len(CHECKS) - len(bad), "不通过": len(bad)},
    "不通过明细": bad,
    "产物 SHA256": {
        str(p.relative_to(L.HERE)): {"sha256": sha(p), "字节": p.stat().st_size}
        for p in sorted(list(OUT.glob("*")) + list((L.HERE / "queries").glob("*.py")) + [REP])
    },
    "逐项": CHECKS,
}
with open(OUT / "08_自检.json", "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2, default=str)

print(f"自检 {len(CHECKS)} 条：通过 {len(CHECKS) - len(bad)}，不通过 {len(bad)}")
for c in bad:
    print(f"  ✗ {c['段']} | {c['项']} | {c['说明'][:110]}")
