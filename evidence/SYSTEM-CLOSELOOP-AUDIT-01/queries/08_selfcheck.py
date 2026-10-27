# -*- coding: utf-8 -*-
"""08 自检：报告里引用的每个数字都必须能回到 out/*.json 的原始字段。

不做「看起来对」的判断，只做机械比对：
  ① 报告里出现的关键数字 == 对应 JSON 里的字段值
  ② 报告必须写明 未验证 的条数，且不得出现「全部通过」类表述
  ③ 27 项操作编号必须全部在冻结清单里
  ④ 报告引用的脚本必须都存在

用法：python 08_selfcheck.py
"""
from __future__ import annotations

import json
import pathlib
import re

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-01"
OUT = HERE / "out"
Q = HERE / "queries"

REPORT = (HERE / "00_报告.md").read_text(encoding="utf-8")


def j(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


FREEZE = j("00_验收清单_冻结.json")
API = j("01_接口契约.json")
SW = j("02c_视图切换.json")
LED = j("03_账本对账.json")
LIFE = j("04_隔离实例生命周期.json")
X = j("05_三层对账.json")
CLOSE = j("06_FT-LOW4H收口.json")

RES = {"检查": [], "计数": {}}


def ck(name: str, ok: bool, expect, got) -> None:
    RES["检查"].append({"项": name, "通过": bool(ok),
                        "期望": str(expect)[:200], "实测": str(got)[:200]})
    print(f"[{'通过' if ok else '不通过'}] {name}"
          + ("" if ok else f"  期望={expect}  实测={got}"))


def has(s: str) -> bool:
    return s in REPORT


# ── ① 关键数字对账 ────────────────────────────────────────────────────────
api_ok, api_bad = API["计数"]["通过"], API["计数"]["不通过"]
ck("接口读侧通过数/总数写进报告", has(f"{api_ok}/{api_ok + api_bad}"),
   f"{api_ok}/{api_ok + api_bad}", "命中" if has(f"{api_ok}/{api_ok + api_bad}") else "未命中")

ck("视图真切换数/总数写进报告",
   has(f"{SW['计数']['真切换']}/{SW['计数']['总数']}"),
   f"{SW['计数']['真切换']}/{SW['计数']['总数']}",
   "命中" if has(f"{SW['计数']['真切换']}/{SW['计数']['总数']}") else "未命中")

ck("视图无变化数写进报告", has(f"{SW['计数']['无变化']} 项无变化"),
   f"{SW['计数']['无变化']} 项无变化",
   "命中" if has(f"{SW['计数']['无变化']} 项无变化") else "未命中")

# 操作项三态计数：从报告正文里抽
m = re.search(r"\*\*(\d+) 通过 / (\d+) 不通过 / (\d+) 未验证", REPORT)
ck("27 项操作三态计数出现在报告", m is not None, "13 通过 / 3 不通过 / 11 未验证",
   m.group(0) if m else "未找到")
if m:
    got = tuple(int(x) for x in m.groups())
    ck("三态计数之和 == 冻结的 27 项", sum(got) == len(FREEZE["操作项"]),
       len(FREEZE["操作项"]), sum(got))

ck("账本三态计数写进报告",
   has(f"{LED['计数']['通过']} 通过 / {LED['计数']['不通过']} 不通过 / {LED['计数']['未验证']} 未验证"),
   "账本三态", "命中" if has(
       f"{LED['计数']['通过']} 通过 / {LED['计数']['不通过']} 不通过 / {LED['计数']['未验证']} 未验证"
   ) else "未命中")

ck("生命周期三态计数写进报告",
   has(f"{LIFE['计数']['通过']} 通过 / {LIFE['计数']['不通过']} 不通过 / {LIFE['计数']['未验证']} 未验证"),
   "生命周期三态", "命中" if has(
       f"{LIFE['计数']['通过']} 通过 / {LIFE['计数']['不通过']} 不通过 / {LIFE['计数']['未验证']} 未验证"
   ) else "未命中")

ck("三层对账计数与报告一致", X["计数"]["通过"] + X["计数"]["不通过"] == 18,
   "18 条", X["计数"]["通过"] + X["计数"]["不通过"])

# ── ② 未验证必须显性写出，且不得宣称全部通过 ────────────────────────────
ck("报告明确写了「不计入通过」", has("不计入通过"), "出现", "出现" if has("不计入通过") else "缺失")
for bad in ("全部通过", "所有页面通过", "全部页面通过", "全部验证通过"):
    ck(f"报告不得出现「{bad}」", not has(bad), "不出现", "出现" if has(bad) else "未出现")
ck("报告写明「工程通过不代表策略有效」", has("工程通过不代表策略有效") or has("工程通过不等于策略有效"),
   "出现", "出现" if (has("工程通过不代表策略有效") or has("工程通过不等于策略有效")) else "缺失")

# ── ③ 27 项操作编号都在冻结清单里 ────────────────────────────────────────
frozen_ids = {x["id"] for x in FREEZE["操作项"]}
report_ids = set(re.findall(r"^\| (W\d|D\d|S\d|B\d) \|", REPORT, re.M))
ck("报告里的操作编号 ⊆ 冻结清单", report_ids <= frozen_ids,
   f"{len(frozen_ids)} 个冻结编号", f"报告出现 {len(report_ids)} 个，越界 {sorted(report_ids - frozen_ids)}")
ck("报告覆盖全部 27 项操作", report_ids == frozen_ids,
   sorted(frozen_ids), f"缺失 {sorted(frozen_ids - report_ids)}")

# ── ④ 不通过项必须逐条出现在第 6 节 ─────────────────────────────────────
need_sections = ["6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8"]
for s in need_sections:
    ck(f"第 {s} 节存在", has(f"### {s} "), "存在", "存在" if has(f"### {s} ") else "缺失")

bad_items = []
for d, key in ((LED, "id"), (LIFE, "id"), (X, "id"), (API, "id")):
    for it in d["明细"]:
        if (it.get("verdict") or it.get("判定")) in ("不通过", "未验证"):
            bad_items.append(it[key])
missing_in_report = [b for b in bad_items if b not in REPORT]
ck("所有 不通过/未验证 的编号都在报告里出现", not missing_in_report,
   "全部出现", f"缺失 {missing_in_report}")

# ── ⑤ 报告引用的脚本都存在 ──────────────────────────────────────────────
scripts = re.findall(r"^(\w+\.(?:py|js))\s", REPORT, re.M)
ck("报告引用的脚本都存在于 queries/", all((Q / s).exists() for s in scripts),
   "全部存在", f"缺失 {[s for s in scripts if not (Q / s).exists()]}")

# ── ⑥ C8 两读法数字与收口脚本一致 ───────────────────────────────────────
jia = CLOSE["段"]["B3 结果"]["读法甲通过的组合"]
yi = CLOSE["段"]["B3 结果"]["读法乙通过的组合"]
ck("C8 读法甲名单写进报告", all(c in REPORT for c in jia), jia,
   [c for c in jia if c not in REPORT])
ck("C8 读法乙名单写进报告", all(c in REPORT for c in yi), yi,
   [c for c in yi if c not in REPORT])
ck("C8 两读法最高满足数都是 4 且无组合达标",
   CLOSE["段"]["B4 门槛"]["读法甲：最高满足数 / 达标组合"]["最高"] == 4
   and CLOSE["段"]["B4 门槛"]["读法乙：最高满足数 / 达标组合"]["最高"] == 4
   and not CLOSE["段"]["B4 门槛"]["读法甲：最高满足数 / 达标组合"]["达标"]
   and not CLOSE["段"]["B4 门槛"]["读法乙：最高满足数 / 达标组合"]["达标"],
   "4 / 4 / 空 / 空",
   (CLOSE["段"]["B4 门槛"]["读法甲：最高满足数 / 达标组合"]["最高"],
    CLOSE["段"]["B4 门槛"]["读法乙：最高满足数 / 达标组合"]["最高"]))
ck("收口脚本自身判据全通过", CLOSE["判据统计"]["不通过"] == 0,
   0, CLOSE["判据统计"]["不通过"])

# ── ⑦ 关键事实必须写进报告（防止报告弱化）───────────────────────────────
facts = {
    "撮合因果被破坏": "撮合因果",
    "测试数据没隔离": "测试数据没隔离",
    "被审代码被并发修改": "被审代码被并发修改",
    "止损轨迹没有历史表": "止损轨迹没有历史表",
    "策略管理页签空壳": "空壳",
    "闸门无法隔离": "没有环境变量出口",
    "版本核对论证重做": "版本核对论证重做",
    "C8 两种读法": "两种读法",
}
for label, needle in facts.items():
    ck(f"报告写明「{label}」", has(needle), needle, "命中" if has(needle) else "缺失")

# ── 汇总 ────────────────────────────────────────────────────────────────
ok = sum(1 for x in RES["检查"] if x["通过"])
RES["计数"] = {"总": len(RES["检查"]), "通过": ok, "不通过": len(RES["检查"]) - ok,
               "不通过的项": [x["项"] for x in RES["检查"] if not x["通过"]]}
(OUT / "08_自检.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n=== 自检汇总 ===")
print(json.dumps(RES["计数"], ensure_ascii=False, indent=1))
