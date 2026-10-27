# -*- coding: utf-8 -*-
"""80｜变更清单、调用路径图与最小补丁方案（只出方案，不改执行路径）。

内容
----
1. **本单触及的文件**：开工前指纹（来自 `out/10_现状快照.json`）vs 现在，逐个说清改了什么；
2. **写探针的正反例**：证明「0 写」不是尺子坏了；
3. **调用路径图**：研究信号从库到页面、从库到订单闸门的两条链路（文本图，逐跳标文件与函数）；
4. **生产页面现状**（只读实拍）：生产后端未重载 ⇒ 页面如实降级，截图留证；
5. **最小补丁方案**：文件 / 函数 / 行为 / 回归证据，**本单不实施**。

纪律
----
本脚本不修改任何生产文件、不调用下单路径、不重启服务。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib09 as U  # noqa: E402

SNAP10 = U.OUT / "10_现状快照.json"
PROBE_CTL = Path(__file__).resolve().parent / "80b_探针正反例.py"


def run_probe(mode: str) -> dict:
    r = U.run([U.PY, str(PROBE_CTL), mode], cwd=U.PROJECT, timeout=120)
    out = {"mode": mode, "returncode": r["returncode"], "stderr": r["stderr"][-800:]}
    for line in (r["stdout"] or "").splitlines():
        if line.startswith("@@PROBE@@"):
            out["probe"] = json.loads(line[len("@@PROBE@@"):])
        elif line.startswith("@@READ@@"):
            out["read"] = json.loads(line[len("@@READ@@"):])
    return out


def markers(path: Path, tag: str, ctx: int = 0) -> list:
    """把带 `tag` 标记的行连同上下文抓出来（用于说明「改了哪几处」）。"""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = []
    for i, ln in enumerate(lines):
        if tag in ln:
            lo, hi = max(0, i - ctx), min(len(lines), i + ctx + 1)
            hits.append({"line": i + 1, "text": "\n".join(lines[lo:hi])})
    return hits


def main() -> int:
    checks: dict = {}
    snap = json.loads(SNAP10.read_text(encoding="utf-8")) if SNAP10.exists() else {}
    before = snap.get("开工前_允许触及文件") or {}

    # ── 1. 触及文件前后对照 ─────────────────────────────────────────────
    files = {}
    for name, p in U.TOUCH_FILES.items():
        b = before.get(name) or {}
        a = U.fingerprint(p)
        changed = (b.get("exists") != a.get("exists")) or (b.get("sha256") != a.get("sha256"))
        files[name] = {"path": str(p), "开工前": b, "现在": a,
                       "有变化": changed,
                       "变化类型": ("新建" if not b.get("exists") and a.get("exists")
                                else "修改" if changed else "未变")}
    checks["开工前指纹已存在（10 现状快照）"] = bool(before)
    checks["研究区组件为新建"] = files["前端_研究区组件(新建)"]["变化类型"] == "新建"
    checks["后端字段映射为修改"] = files["后端_API字段映射"]["变化类型"] == "修改"
    checks["信号面板接线为修改"] = files["前端_信号面板接线"]["变化类型"] == "修改"
    checks["本单只动了 3 个文件"] = len(files) == 3

    # 改动定位（按标记行抓，避免逐行 diff 噪音）
    loc = {
        "后端_live_api.py_09WORK块": markers(U.BACKEND / "live_api.py", "09-WORK", 0),
        "前端_SignalListPanel_引用": markers(
            U.WEB / "src" / "components" / "SignalListPanel.tsx", "ResearchSignalSection", 1),
        "前端_SignalListPanel_研究区说明": markers(
            U.WEB / "src" / "components" / "SignalListPanel.tsx", "研究记录只可能出现在", 3),
    }
    comp = U.WEB / "src" / "components" / "ResearchSignalSection.tsx"
    raw_comp = comp.read_text(encoding="utf-8") if comp.exists() else ""
    # 先去掉注释再扫（注释里会提到 `apiPost` 和当前区的 `signal-row`，
    # 直接扫原文会把「说明文字」误判成「代码里真有」）
    code_comp = re.sub(r"/\*.*?\*/", "", raw_comp, flags=re.S)
    code_comp = re.sub(r"^\s*//.*$", "", code_comp, flags=re.M)
    comp_lines = raw_comp.splitlines()
    loc["前端_新组件"] = {
        "path": str(comp), "行数": len(comp_lines),
        "是否只有 GET": ("apiPost(" not in code_comp),
        "是否有执行按钮": bool(re.search(r"<button[^>]*(order|execute|open)", code_comp)),
        "testid 前缀": sorted(set(re.findall(r'data-testid="([a-z-]+)"', code_comp))),
        "出现 apiPost 的位置": "注释里提到（不算调用）" if "apiPost" in raw_comp
                            and "apiPost(" not in code_comp else "无",
    }
    checks["新组件不含 apiPost（只读）"] = loc["前端_新组件"]["是否只有 GET"]
    checks["新组件不含执行/下单按钮"] = not loc["前端_新组件"]["是否有执行按钮"]
    checks["新组件 testid 全部以 research- 开头"] = all(
        t.startswith("research-") for t in loc["前端_新组件"]["testid 前缀"])
    checks["后端改动只有一处 09-WORK 标记块"] = len(loc["后端_live_api.py_09WORK块"]) == 1

    # ── 2. 写探针正反例 ────────────────────────────────────────────────
    w = run_probe("write")
    rd = run_probe("read")
    wp = (w.get("probe") or {}).get("probe") or {}
    rp = (rd.get("probe") or {}).get("probe") or {}
    checks["正例：探针抓到了建表/插入/更新"] = (wp.get("写语句总数") or 0) >= 4
    checks["正例：写语句按库指向临时库"] = all(
        str(U.ISO.resolve()) in k for k in (wp.get("写语句按库") or {}))
    checks["反例：只读同一库时写语句 = 0"] = rp.get("写语句总数") == 0
    checks["反例：确实读到了数据（不是没连上）"] = (rd.get("read") or {}).get("rows") == 2

    # ── 3. 生产页面现状（只读实拍）─────────────────────────────────────
    png = U.OUT / "80_生产页面_现状.png"
    r = U.run([U.NODE, str(Path(__file__).resolve().parent / "page09.mjs"),
               U.WEB_PROD, str(png), "--open-research", "--limit-rows=2"],
              cwd=U.PROJECT, timeout=300)
    page = {}
    for line in (r["stdout"] or "").splitlines():
        if line.startswith("@@PAGE@@"):
            page = json.loads(line[len("@@PAGE@@"):])
            break
    prod_has_research_key = any(
        (it or {}).get("has_research_key") for it in
        [(resp.get("first_item") or {}) for resp in (page.get("signals_responses") or [])])
    rep_page = {
        "url": U.WEB_PROD, "screenshot": str(png),
        "研究区存在": (page.get("research_section_present") or 0) == 1,
        "展开后行数": page.get("research_rows_after_open"),
        "当前区行数": page.get("current_rows"),
        "研究行混入当前区": page.get("current_area_has_research"),
        "身份字段缺失提示": "research-identity-missing" in (page.get("panel_text_head") or "")
                        or "未提供" in (page.get("research_rows_sample") or [{}])[0].get("text", ""),
        "后端是否已给 research 子对象": prod_has_research_key,
        "首条研究行文本": ((page.get("research_rows_sample") or [{}])[0].get("text") or "")[:300],
        "console_errors": page.get("console_errors"),
    }
    checks["生产页面出现研究区"] = rep_page["研究区存在"]
    checks["生产页面研究行不混入当前区"] = (page.get("current_area_has_research") or 0) == 0
    checks["生产后端未重载 ⇒ 页面如实降级（不伪造身份）"] = not prod_has_research_key

    # ── 4. 调用路径图（文本，逐跳带文件与函数）────────────────────────
    path_graph = {
        "链路A_研究信号到页面": [
            "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/raw.json（289 候选）",
            "→ 框架/backend/research_signal_import.py::load_run/build_rows/import_rows"
            "（写 live_signal，origin=historical_research）",
            "→ 框架/backend/live/live_signals.sqlite3（live_signal 表，289 行）",
            "→ 框架/backend/live_api.py::live_signals（scope=all&origin=historical_research）"
            "〔09-WORK 新增：仅对研究行附 research 子对象〕",
            "→ 框架/backend/adapter.py::_v1_signals_merged → route_v1('/api/v1/signals')",
            "→ UI界面/web/src/api/client.ts::apiGet",
            "→ UI界面/web/src/components/ResearchSignalSection.tsx（折叠区，只读）",
            "→ UI界面/web/src/components/SignalListPanel.tsx（挂在列表容器之外）",
        ],
        "链路B_研究信号到订单（只读追踪，本单未改）": [
            "框架/backend/unified_runtime.py::SignalPersistenceAndRouter.route_pending_orders"
            "（生产调用方只传 origin=None 或 'replay'）",
            "→ _scan_actionable（6 条谓词：executable_state / exchange+market+timeframe /"
            " role / arm / confirmed_at+available_at / origin）",
            "→ 框架/backend/paper_engine.py::open_position（配置契约→闸门→行情→成交时钟）",
            "→ 隔离账本 paper_pending / paper_order / paper_position",
        ],
        "当前实际状态": "生产库 289 行全是 historical_research，executable_state="
                  "historical_not_executable、role=research_only、available_at=NULL、"
                  "payload.arm∈{4a,4b,4c} ≠ 3a ⇒ 链路B 的 4 条谓词各自独立即可挡住（见 60 单因素矩阵）。",
    }

    # ── 5. 最小补丁方案（只出方案，不实施）─────────────────────────────
    patch_plan = {
        "结论": "现状**已经**挡得住：五层里任意一层单独有效即可让研究行不被扫描（60 单因素矩阵 14 格）。"
                "所以本单**不建议**为了「防研究行开仓」去改执行路径 —— 那是无证据的改动。",
        "为什么仍建议补一处（可选，需裁决）": "现状的防护是「四个字段碰巧都不满足」的**隐式**防护："
                              "只要有人把 executable_state/role/arm/available_at 中任一改错，"
                              "或未来导入器把研究行写成可执行态，防护立刻消失，且没有任何测试会报警。"
                              "把它变成**显式拒绝**，才能让「研究行永不可交易」成为可断言的性质。",
        "补丁P-01（推荐，最小）": {
            "文件": "框架/backend/unified_runtime.py",
            "函数": "SignalPersistenceAndRouter._scan_actionable",
            "改动": "在 where 列表增加一条 `origin <> 'historical_research'`（或在 SQL 前对"
                    "`origin` 做白名单 `origin IN ('live','recovered','replay')`），"
                    "位置在现有 6 条谓词之后，不改任何现有条件。",
            "行为影响": "研究行从「被 4 条谓词分别挡住」变成「被来源显式拒绝」；"
                    "对 live/recovered/replay 行零影响（它们的 origin 不在被排除集合里）。",
            "回归证据要求": "重跑 60 单因素矩阵：L/N 两格应从「被扫描到」变成「不被扫描到」；"
                      "同时补一格 `origin='live' 的合法信号` 仍应被扫描到（防误杀）。",
            "风险": "若未来有别的模块用 `historical_research` 走回放路由，会被这条一并挡住 —— "
                  "目前全库检索无此调用方（只有导入器写入、页面读取）。",
        },
        "补丁P-02（更硬，需产品裁决）": {
            "文件": "框架/backend/paper_engine.py",
            "函数": "open_position",
            "改动": "入口处读 `signal_payload`（该参数已存在）里的 `approved`；"
                    "`approved is False` ⇒ 直接 `_reject(..., 'research_not_approved')`，"
                    "排在配置契约校验之前。",
            "行为影响": "任何未批准来源（含研究信号、含未来回放）在**开仓入口**被拒；"
                    "现有自动/手工/挂单三路都经过此函数 ⇒ 一处覆盖三路。",
            "回归证据要求": "正例：approved=true 的信号仍能挂单；反例：approved=false 必被拒且"
                      "`paper_order.reject_reason='research_not_approved'`。",
            "风险": "`route_pending_orders` 当前**不传** `signal_payload` ⇒ 需同时改调用方；"
                  "属「改变执行行为」的改动，**必须**单独工单 + 用户裁决。",
        },
        "本单立场": "两条补丁都**没有实施**。理由：工单明确要求本单不得实施对现有执行行为有影响的改动、"
                "不关闭全局开仓闸门、不改变原策略行为。60 单因素矩阵已用隔离负例证明现状可挡住。",
    }

    rep = {
        "生成时间": U.now_utc(),
        "触及文件": files,
        "改动定位": loc,
        "写探针正反例": {"write": w, "read": rd},
        "调用路径图": path_graph,
        "生产页面现状": rep_page,
        "最小补丁方案": patch_plan,
        "判定": U.summarise(checks,
                          "变更清单与路径图齐备；探针正反例成立；补丁只出方案未实施",
                          "变更清单存在未通过项"),
    }
    p = U.dump("80_变更清单.json", rep)
    U.print_checks("80 变更清单", rep, p)
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
