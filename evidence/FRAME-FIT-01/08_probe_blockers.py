#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-01 步骤八：实测「正式实验现在会被哪些理由拒」。

不是手写一张清单，而是**真的**在临时登记库里造一个尽可能完整的实验，
然后调 `formal_eligible` 与 `guard_create(mode=formal)`，把返回的 blockers 原样记下来。

这样「阻断原因」有实证，不是自我声明。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

import experiment_registry as E   # noqa: E402

OUT = HERE / "08_阻断原因实测.json"
OUT_MD = HERE / "08_未配置项与阻断原因.md"


def probe(reg: E.ExperimentRegistry, *, name: str, scope: dict,
          policy: E.SplitPolicy, freeze: bool, mode: str = E.MODE_FORMAL,
          eid: str = "exp_probe") -> dict:
    parts = E.current_design_parts(sample_scope=scope, split_policy=policy,
                                   metrics_spec=E.DEFAULT_METRICS_SPEC)
    try:
        reg.register(design=parts, sample_scope=scope, split_policy=policy,
                     metrics_spec=E.DEFAULT_METRICS_SPEC,
                     experiment_id=eid, mode=mode, by="probe",
                     note=f"阻断原因实测：{name}")
    except E.ExperimentBlocked as exc:
        return {"scenario": name, "register_blocked": exc.reasons}
    if freeze:
        try:
            reg.freeze(eid, by="probe")
        except E.ExperimentBlocked as exc:
            return {"scenario": name, "freeze_blocked": exc.reasons}
    ok, why = reg.formal_eligible(eid)
    # 再走一次真实创建入口的闸门
    spec = {"symbols": scope["symbols"],
            "window_start": scope["window_start"],
            "window_end": scope["window_end"],
            "experiment": {"mode": mode, "experiment_id": eid}}
    gate = E.guard_create(spec, registry=reg)
    return {"scenario": name, "formal_eligible": ok,
            "formal_blockers": why,
            "gate_ok": gate.get("ok"), "gate_blockers": gate.get("blockers")}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="framefit_probe_"))
    try:
        reg = E.ExperimentRegistry(tmp / "registry")
        scope_pinned = {"symbols": ["BTCUSDT"],
                        "window_start": "2024-01-01", "window_end": "2024-06-30",
                        "source": "request"}
        scope_other = {"symbols": ["BTCUSDT"],
                       "window_start": "2025-01-01", "window_end": "2025-06-30",
                       "source": "request"}

        results = []
        cand = E.propose_chronological_split("2021-12-01", "2026-10-18",
                                             isolation_hours=240)

        def fresh(name, **kw):
            # 每个场景一个独立登记库：否则「同一设计身份不得重复登记」会互相干扰
            r = E.ExperimentRegistry(tmp / ("reg_" + name[:6].replace(" ", "_")))
            return probe(r, name=name, **kw)

        # 场景 1：什么都不给（最裸的正式实验）
        results.append(fresh("只登记、不冻结、分组不配",
                             scope=scope_pinned, policy=E.SplitPolicy(),
                             freeze=False, eid="exp_s1"))

        # 场景 2：冻结了，但分组仍未配置
        results.append(fresh("已冻结、分组仍未配置",
                             scope=scope_pinned, policy=E.SplitPolicy(),
                             freeze=True, eid="exp_s2"))

        # 场景 3：冻结 + 分组配好（候选划分）+ 窗口正好是写死窗口
        results.append(fresh("已冻结 + 分组已配（候选划分）",
                             scope=scope_pinned, policy=cand,
                             freeze=True, eid="exp_s3"))

        # 场景 4：同上，但请求窗口不是写死窗口
        results.append(fresh("已冻结 + 分组已配，但请求窗口≠写死窗口",
                             scope=scope_other, policy=cand,
                             freeze=True, eid="exp_s4"))

        # 场景 5：研究模式（对照：证明闸门不是一律拒绝）
        results.append(fresh("研究模式（对照）",
                             scope=scope_pinned, policy=E.SplitPolicy(),
                             freeze=False, mode=E.MODE_RESEARCH, eid="exp_s5"))

        payload = {
            "audit": "FRAME-FIT-01",
            "registry_root": "临时目录（已删除，不留痕）",
            "scenarios": results,
            "note": ("每个场景都是真调 register/freeze/formal_eligible/guard_create 得到的，"
                     "不是手写清单。"),
        }
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")

        # ── Markdown ──
        L = []
        L.append("# 未配置项与正式实验阻断原因\n")
        L.append("> 下面每一条都是**实测**出来的：在临时登记库里真的造实验、真的调闸门，")
        L.append("> 把返回的拒绝理由原样抄下来。原始输出见 `08_阻断原因实测.json`。\n")

        L.append("## 一、五个场景的实测结果\n")
        for r in results:
            L.append(f"### {r['scenario']}\n")
            if "register_blocked" in r:
                L.append("登记即被拒：")
                for x in r["register_blocked"]:
                    L.append(f"- {x}")
                L.append("")
                continue
            if "freeze_blocked" in r:
                L.append("冻结被拒：")
                for x in r["freeze_blocked"]:
                    L.append(f"- {x}")
                L.append("")
                continue
            L.append(f"- `formal_eligible` = **{r['formal_eligible']}**")
            L.append(f"- 创建入口 `guard_create` 放行 = **{r['gate_ok']}**")
            if r["formal_blockers"]:
                L.append("- 阻断理由：")
                for x in r["formal_blockers"]:
                    L.append(f"  - {x}")
            extra = [x for x in (r["gate_blockers"] or [])
                     if x not in (r["formal_blockers"] or [])]
            if extra:
                L.append("- 创建入口额外理由：")
                for x in extra:
                    L.append(f"  - {x}")
            L.append("")

        L.append("## 二、结构性未配置项（不是这次忘了填，是确实还没有）\n")
        items = [
            ("正式划分日期", "UNCONFIGURED",
             "框架只给候选（60/20/20 与 70/15/15），切哪天由人定。"
             "未定 ⇒ 没有「留出」这回事 ⇒ 不给正式资格。"),
            ("解封方案", "UNCONFIGURED",
             "谁有权解封、解封后多久必须登记结果、解封后能否再改参数 —— 这三条没有契约。"
             "机制已实现（一次性解封 + 冻结前置），方案本身待定。"),
            ("执行链样本范围", "缺陷",
             "`_run_event_trend` 写死 2024-01-01~2024-06-30，请求窗口不生效。"
             "登记范围与实际范围脱节 ⇒ 身份再准也绑不上真实样本。本单不修，只阻断。"),
            ("参数注入", "缺陷",
             "同一函数只把 `initial_equity` 传给配置，其余 48 个参数在事件趋势链上不生效。"
             "「改参数换身份」机制成立，但改参数不会改变结果 ⇒ 试验册会记下大量"
             "「不同身份、相同结果」的条目。这一条要在修好注入后才能解读。"),
            ("227 条样本的干净留出", "不可得",
             "样本横跨 2021-12 ~ 2026-10。任何覆盖全区的划分，留出段都会命中已观察样本"
             "（60/20/20 命中 57 条，70/15/15 命中 43 条）。"
             "要干净留出只能等新行情或换标的池。"),
            ("历史 2130 个 run 的设计身份", "UNKNOWN",
             "它们发生在登记库存在之前。工单第 6 条：缺历史标 UNKNOWN，不补造。"
             "它们只作为「已发生的事实」保留，不得被追认为某次正式实验的结果。"),
            ("四组实验计划的正式文件", "不存在",
             "冻结规则 A/B/C、FLOW-OI-01 阶梯四组、FACTOR-ENTRY-01 平行四组 —— 三套都只存在于"
             "代码变体与证据目录里，没有一份带分组规则/指标/试验次数的计划书。"
             "本框架用 `group` 字段预留位置，历史三套一律标 UNKNOWN。"),
            ("数据版本（运行前）", "不可得",
             "数据指纹要读 K 线才能算。跑前只能冻结设计身份，跑后再绑运行身份。"
             "这不是缺陷，是时序决定的。"),
            ("规格历史资格", "0 条 verified",
             "STEP1-A 已实测：全池 `verified` = 0（缺 spec_changes 覆盖证据）。"
             "规格不合格 ⇒ 即使实验身份齐全，成交价的可信度仍另说。"),
            ("PIT 接入", "并行窗口进行中",
             "本单边界内不动 PIT 代码。`trading_core/pit.py` 与新增的 `pit_bridge.py` 一个字没改。"),
        ]
        L.append("| 项目 | 状态 | 说明 |")
        L.append("|---|---|---|")
        for a, b, c in items:
            L.append(f"| {a} | **{b}** | {c} |")
        L.append("")

        L.append("## 三、结论\n")
        L.append("按现在的状态，**任何一个正式实验都拿不到资格**，最短的阻断链是：")
        L.append("")
        L.append("1. 没有批准的划分日期 ⇒ 分组未配置；")
        L.append("2. 就算配了划分，执行链写死窗口 ⇒ 登记范围 ≠ 实际范围；")
        L.append("3. 就算窗口对上了，留出段仍含已观察样本 ⇒ 该段不能声称未见；")
        L.append("4. 就算以上都解决，历史 run 无身份、规格 0 条 verified ⇒ 结果仍不能对外。")
        L.append("")
        L.append("这不是框架太严，是这四件事本来就没做完。框架的作用是把它们**摆出来**，")
        L.append("而不是让人在文档里含糊过去。")
        L.append("")
        OUT_MD.write_text("\n".join(L), encoding="utf-8")

        for r in results:
            if "formal_eligible" in r:
                print(f"{r['scenario']}: formal={r['formal_eligible']} "
                      f"gate={r['gate_ok']} 理由 {len(r['formal_blockers'])} 条")
            else:
                print(f"{r['scenario']}: 登记/冻结被拒")
        print(f"-> {OUT_MD.name} / {OUT.name}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
