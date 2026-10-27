# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜05 待决表清理与可比较规则提案。

工单第三部分：
  · 三组入场阈值未批准前不得宣称已实现提前入场策略；
  · 待决表只留真正待决项，**已确定项不再重新询问**；
  · 风险配置与滑点来源冲突保留标记；
  · 下一轮交付可比较规则提案，先由 Codex 审核再定实验配置。

本脚本把 `框架/backend/accum_pending.py` 的三张表渲染成
JSON + 一份人读的 Markdown，并做硬校验（不靠人眼）。

输出：
  evidence/ACCUM-EARLY-02/out/05_待决表与规则提案.json
  evidence/ACCUM-EARLY-02/05_待决表与规则提案.md
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

OUT = PROJECT / "evidence" / "ACCUM-EARLY-02" / "out"


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x).replace("\n", " ") for x in r) + " |")
    return "\n".join(out)


def render_md(snap, rep):
    L = []
    L.append("# ACCUM-EARLY-02 待决表（清理后）与三组可比较规则提案\n")
    L.append(f"- 生成：{rep['generated_at_utc']}")
    L.append(f"- 模块：`框架/backend/accum_pending.py`（{snap['version']}）")
    L.append(f"- 校验：**{'通过' if snap['校验']['通过'] else '不通过'}** "
             f"（{snap['校验']['计数']}）\n")

    L.append("## 零、先说清楚一件事\n")
    for i, s in enumerate(snap["前置声明"], 1):
        L.append(f"{i}. {s}")
    L.append("")

    L.append("## 一、已确定（不再询问）\n")
    L.append("这些事已经有人定了，或者在本单里定死了。再问一遍就是浪费你的时间。\n")
    L.append(md_table(
        ["项", "值", "依据", "落在哪"],
        [[k, v["值"], v["依据"], v["落在哪"]] for k, v in snap["已确定"].items()]))
    L.append("")

    L.append("## 二、口径冲突（保留标记，不替你选）\n")
    L.append("这三项不是「参数还没定」，是**两份文件写着不同的值**。"
             "挑一个就等于改历史口径，必须你或裁决方定。\n")
    for k, v in snap["口径冲突"].items():
        L.append(f"### {k} · {v['冲突内容']}\n")
        L.append(md_table(["来源", "值", "文件位置"],
                          [[n, i["值"], i["来源"]] for n, i in v["两套值"].items()]))
        L.append("")
        L.append(f"- **影响**：{v['影响']}")
        L.append(f"- **本单做法**：{v['本模块做法']}")
        L.append(f"- **为什么不能自己选**：{v['为什么不能自己选']}")
        L.append(f"- **取证**：`{v['取证']}`\n")

    L.append("## 三、真待决（阻塞开仓，共 "
             f"{snap['校验']['计数']['真待决']} 项）\n")
    L.append("只留这些。每一项都没有默认值 —— 不是「我忘了填」，是**不许我填**。\n")
    L.append(md_table(
        ["项", "问题", "影响"],
        [[k, v["问题"], v["影响"]] for k, v in snap["真待决"].items()]))
    L.append("")

    b = snap["对照基线"]
    L.append("## 四、对照基线（任何提案都必须跟它比）\n")
    L.append(f"- **定义**：{b['定义']}")
    L.append(f"- **样本**：{b['样本']} 条")
    L.append(f"- **生效视图口径**：可回放 {b['生效视图口径']['可回放']} 条，"
             f"已实现净额合计 **{b['生效视图口径']['已实现净额合计']}**，"
             f"中位 {b['生效视图口径']['中位']}，"
             f"初始止损退出 {b['生效视图口径']['初始止损退出']} / "
             f"跟踪止损退出 {b['生效视图口径']['跟踪止损退出']}")
    L.append(f"- **策略包口径**：可回放 {b['策略包口径']['可回放']} 条，"
             f"拒单 {b['策略包口径']['拒单']} 条，"
             f"已实现净额合计 **{b['策略包口径']['已实现净额合计']}**，"
             f"中位 {b['策略包口径']['中位']}")
    L.append(f"- **为什么是它**：{b['为什么它是唯一可比基线']}")
    L.append(f"- **注意**：{b['注意']}\n")

    L.append("## 五、三组可比较规则提案\n")
    for p in snap["三组提案"]:
        L.append(f"### {p['编号']}（工程名 `{p['工程名']}`）· {p['一句话']}\n")
        L.append("**改变什么**")
        for x in p["改变什么"]:
            L.append(f"- {x}")
        L.append("\n**沿用什麼**")
        for x in p["沿用什麼"]:
            L.append(f"- {x}")
        L.append("\n**需要哪些数据**")
        L.append(md_table(["数据", "状态"],
                          [[d["数据"], d["状态"]] for d in p["需要哪些数据"]]))
        L.append("\n**如何独立检验**")
        for x in p["如何独立检验"]:
            L.append(f"- {x}")
        L.append(f"\n**仍待定**：{'、'.join(p['仍待定'])}")
        L.append(f"\n**是否已实现**：`claim_implemented = {p['claim_implemented']}`"
                 f" —— 阈值没批准，就不能说实现了\n")

    L.append("## 六、校验\n")
    v = snap["校验"]
    L.append(f"- 结果：**{'通过' if v['通过'] else '不通过'}**")
    L.append(f"- 说明：{v['说明']}")
    if v["错误"]:
        L.append("- 错误：")
        for e in v["错误"]:
            L.append(f"  - `{e}`")
    L.append("")
    return "\n".join(L)


def main() -> int:
    rep = {"probe": "05_待决表与规则提案", "generated_at_utc": iso(time.time() * 1000),
           "工单": "ACCUM-EARLY-02 第三部分", "errors": []}
    try:
        import accum_pending as AP
        snap = AP.snapshot()
        rep["snapshot"] = snap
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "05_待决表与规则提案.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        md = render_md(snap, rep)
        (PROJECT / "evidence" / "ACCUM-EARLY-02" / "05_待决表与规则提案.md").write_text(
            md, encoding="utf-8")
        print(json.dumps({
            "校验": snap["校验"],
            "已确定": len(snap["已确定"]),
            "冲突": len(snap["口径冲突"]),
            "真待决": len(snap["真待决"]),
            "提案": [p["编号"] for p in snap["三组提案"]],
            "对照基线": {k: snap["对照基线"][k] for k in ("样本",)},
        }, ensure_ascii=False, indent=1, default=str))
    except Exception as exc:                                      # noqa: BLE001
        rep["errors"].append({"stage": "render",
                              "error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc()[-2000:]})
        print(json.dumps(rep["errors"], ensure_ascii=False, indent=1))
        return 1
    return 0 if snap["校验"]["通过"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
