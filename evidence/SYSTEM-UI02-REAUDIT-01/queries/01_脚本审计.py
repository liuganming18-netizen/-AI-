#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-UI02-REAUDIT-01｜阶段一 · 测试脚本静态审计

对应工单第 3 条：「检查测试脚本是否实际点击原定页签、执行原定操作；
逐项核查截图、DOM、同一次 API 响应及源数据。」

做法：把 test_system_ui_02.py 按 `--- [n/16]` 分段，逐段统计
  click / expect / assert / wait / screenshot / API 抓取
并专门检测三类「看起来做了、其实没生效」的写法：

  D1 注入后无等待：设置 injected_error_path 与 screenshot() 之间没有
     wait_for_timeout / wait_for_response ⇒ DOM 不可能重渲染 ⇒ 截图必然等于上一张。
  D2 只抓不比：抓了 DOM 与 API，但没有把两者做比较（无断言、无比对表达式），
     对账结论只能靠人手写。
  D3 字面量冒充提取：把 17 / 549 / 150 / run_id 等写死在 audit_results 里，
     而不是从 DOM/API 取。

外加：
  D4 哈希自检范围：自检循环覆盖哪些文件，是否覆盖状态截图。
  D5 环境变量清单：是否设置了账本隔离所必需的 PAPER_LEDGER_DIR。
  D6 守护进程证据来源：状态文件写到哪个目录。

只读对方脚本，不执行它。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import datetime

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
SCRIPT = PROJ / "evidence/SYSTEM-UI-02/test_system_ui_02.py"
OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

# 账本隔离所必需的环境变量（依据 live_store.get_ledger_dir 的实现）
REQUIRED_ISOLATION_ENV = {
    "PAPER_LEDGER_DIR": "账本目录（live_store.LEDGER_DIR 的唯一来源，模块级常量）",
    "PAPER_LOCK_DIR": "锁目录",
    "LIVE_DB": "信号库（已在脚本中设置）",
}

VIEW_RE = re.compile(r"---\s*\[(\d+)/16\]")
INJECT_RE = re.compile(r"proxy_state\.injected_error_path\s*=\s*(['\"])(.+?)\1")
SCREEN_RE = re.compile(r"\.screenshot\(\s*path\s*=\s*str\(\s*EVID_DIR\s*/\s*(['\"])(.+?)\1")
WAIT_RE = re.compile(r"wait_for_timeout\(|wait_for_response\(|wait_for_selector\(")
CLICK_RE = re.compile(r"\.click\(")
EXPECT_RE = re.compile(r"\bexpect\(")
ASSERT_RE = re.compile(r"^\s*assert\b")
API_RE = re.compile(r"http_get_json\(|page\.evaluate\(")


def main() -> int:
    if not SCRIPT.exists():
        print(f"[FATAL] 找不到脚本: {SCRIPT}")
        return 2
    text = SCRIPT.read_text(encoding="utf-8")
    lines = text.split("\n")
    n = len(lines)

    # ---- 分段 ----
    marks = []
    for i, l in enumerate(lines, 1):
        m = VIEW_RE.search(l)
        if m:
            marks.append((i, int(m.group(1))))
    sections = []
    for idx, (ln, num) in enumerate(marks):
        end = marks[idx + 1][0] - 1 if idx + 1 < len(marks) else n
        sections.append({"视图": f"View {num:02d}", "起始行": ln, "结束行": end})

    per_view = []
    for s in sections:
        body = lines[s["起始行"] - 1:s["结束行"]]
        chunk = "\n".join(body)
        per_view.append({
            "视图": s["视图"],
            "行区间": [s["起始行"], s["结束行"]],
            "点击次数": len(CLICK_RE.findall(chunk)),
            "断言数_expect": len(EXPECT_RE.findall(chunk)),
            "断言数_assert": sum(1 for b in body if ASSERT_RE.match(b)),
            "等待次数": len(WAIT_RE.findall(chunk)),
            "截图数": len(SCREEN_RE.findall(chunk)),
            "API抓取次数": len(API_RE.findall(chunk)),
        })

    # ---- D1 注入后无等待 ----
    d1 = []
    for i, l in enumerate(lines, 1):
        m = INJECT_RE.search(l)
        if not m:
            continue
        path = m.group(2)
        if path is None or l.strip().startswith("#"):
            continue
        # 从下一行开始找：先遇到 screenshot 还是先遇到 wait
        verdict = "未找到截图"
        screenshot_line = None
        for j in range(i, min(i + 25, n)):
            if WAIT_RE.search(lines[j]):
                verdict = "注入后先等待（正常）"
                break
            sm = SCREEN_RE.search(lines[j])
            if sm:
                verdict = "注入后未等待直接截图（DOM 不可能重渲染）"
                screenshot_line = j + 1
                break
        d1.append({
            "注入行": i, "注入路径": path,
            "判定": verdict, "截图行": screenshot_line,
            "源码": lines[i - 1].strip(),
        })

    # ---- D2 只抓不比：抓了 DOM+API 但没有把两者做机械比较 ----
    # 判据（严格）：只有出现「同一行里同时引用 API 变量与 DOM 变量并做比较/断言」才算比对。
    # 单纯 expect(locator).to_be_visible() 只证明元素存在，不构成数据对账。
    api_vars = set()
    dom_vars = set()
    for l in lines:
        m = re.match(r"\s*([A-Za-z_][A-Za-z_0-9]*)\s*=\s*http_get_json\(", l)
        if m:
            api_vars.add(m.group(1))
        m = re.match(r"\s*([A-Za-z_][A-Za-z_0-9]*)\s*=\s*.*(inner_text\(\)|all_inner_texts\(\))", l)
        if m:
            dom_vars.add(m.group(1))

    d2 = []
    for s in sections:
        body = lines[s["起始行"] - 1:s["结束行"]]
        chunk = "\n".join(body)
        got_dom = any(v in chunk for v in dom_vars)
        got_api = any(v in chunk for v in api_vars)
        compared = False
        for b in body:
            if not re.search(r"(==|!=|<=|>=|\bin\b|\bassert\b)", b):
                continue
            has_api = any(v in b for v in api_vars)
            has_dom = any(v in b for v in dom_vars)
            if has_api and has_dom:
                compared = True
                break
        if got_dom and got_api:
            d2.append({
                "视图": s["视图"],
                "抓了DOM": got_dom, "抓了API": got_api,
                "有机械比较": compared,
                "结论": "已比对" if compared else "只抓不比：DOM 与 API 各自落盘，未做任何比较，对账结论由人手写",
            })
    d2_summary = {
        "本视图内同时抓了DOM与API的视图数": len(d2),
        "其中做了机械比较的": sum(1 for r in d2 if r["有机械比较"]),
        "API变量": sorted(api_vars),
        "DOM变量": sorted(dom_vars),
    }

    # ---- D3 字面量冒充提取 ----
    hardcoded = []
    for i, l in enumerate(lines, 1):
        if re.search(r'"(dims_count|backend_pool_total|ui_active_rows|run_id)"\s*:\s*', l):
            hardcoded.append({"行": i, "源码": l.strip()})

    # ---- D4 哈希自检范围 ----
    d4 = {"自检循环源码": [], "覆盖视图数": None, "是否覆盖状态截图": None}
    for i, l in enumerate(lines, 1):
        if "range(1, 17)" in l or "range(1,17)" in l:
            d4["覆盖视图数"] = 16
        if re.search(r'glob\(f"\{fn\}\*\.png"\)', l):
            d4["自检循环源码"].append({"行": i, "源码": l.strip()})
    d4["是否覆盖状态截图"] = any("state_" in l for l in lines if "glob" in l)

    # ---- D5 环境变量 ----
    envs = {}
    for i, l in enumerate(lines, 1):
        m = re.search(r'env\[\s*"([A-Z_0-9]+)"\s*\]\s*=', l)
        if m:
            envs[m.group(1)] = i
    d5 = {
        "已设置": envs,
        "必需但缺失": {k: v for k, v in REQUIRED_ISOLATION_ENV.items() if k not in envs},
    }

    # ---- D6 守护状态文件目录 ----
    d6 = []
    for i, l in enumerate(lines, 1):
        if "lock_dir = " in l or "BACKEND / \"locks\"" in l:
            d6.append({"行": i, "源码": l.strip()})

    # ---- 源码中是否存在「同一 locator 复用」 ----
    locators = {}
    for i, l in enumerate(lines, 1):
        m = re.search(r'page\.locator\(\s*"([^"]+)"\s*\)', l)
        if m:
            locators.setdefault(m.group(1), []).append(i)
    reused = {k: v for k, v in locators.items() if len(v) > 1}

    report = {
        "生成时刻": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "用途": "阶段一｜测试脚本静态审计（不执行被测脚本）",
        "被测脚本": str(SCRIPT),
        "脚本字节": len(SCRIPT.read_bytes()),
        "脚本行数": n,
        "脚本sha256": __import__("hashlib").sha256(SCRIPT.read_bytes()).hexdigest(),
        "逐视图操作强度": per_view,
        "D1_注入后无等待": d1,
        "D2_只抓不比": d2,
        "D2_汇总": d2_summary,
        "D3_字面量冒充提取": hardcoded,
        "D4_哈希自检范围": d4,
        "D5_环境变量": d5,
        "D6_守护状态文件目录": d6,
        "同一locator复用": reused,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "01_脚本审计.json"
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台 ----
    print("=" * 92)
    print(f"被测脚本 {SCRIPT.name}  {report['脚本字节']}B / {n} 行  sha256={report['脚本sha256'][:16]}")
    print("=" * 92)
    print(f"{'视图':8s} {'行区间':14s} {'点击':>4s} {'expect':>7s} {'assert':>7s} {'等待':>4s} {'截图':>4s} {'API':>4s}")
    for v in per_view:
        print(f"{v['视图']:8s} {str(v['行区间']):14s} {v['点击次数']:>4d} {v['断言数_expect']:>7d} "
              f"{v['断言数_assert']:>7d} {v['等待次数']:>4d} {v['截图数']:>4d} {v['API抓取次数']:>4d}")

    print()
    print("=" * 92)
    print("D1 注入后是否留出重渲染时间")
    print("=" * 92)
    for r in d1:
        print(f"  行{r['注入行']:>4d}  注入 {r['注入路径']:<26s} → {r['判定']}")

    print()
    print("D2 抓了 DOM+API 是否做机械比较")
    print(f"  同时抓了 DOM+API 的视图: {d2_summary['本视图内同时抓了DOM与API的视图数']}"
          f" ／ 其中做了机械比较: {d2_summary['其中做了机械比较的']}")
    for r in d2:
        print(f"  {r['视图']}: {r['结论']}")

    print()
    print("D3 字面量冒充提取")
    for r in hardcoded:
        print(f"  行{r['行']:>4d}: {r['源码']}")

    print()
    print("D4 哈希自检范围")
    print(f"  覆盖视图数={d4['覆盖视图数']}  是否覆盖 state_* 状态截图={d4['是否覆盖状态截图']}")
    for r in d4["自检循环源码"]:
        print(f"  行{r['行']:>4d}: {r['源码']}")

    print()
    print("D5 环境变量")
    print(f"  已设置: {sorted(d5['已设置'].keys())}")
    print(f"  必需但缺失: {list(d5['必需但缺失'].keys())}")

    print()
    print("D6 守护状态文件目录")
    for r in d6:
        print(f"  行{r['行']:>4d}: {r['源码']}")

    print()
    print("同一 locator 复用（复用即说明「不同视图」其实是同一元素）")
    for k, v in reused.items():
        print(f"  x{len(v)}  {k}  行 {v}")

    print(f"\n已落盘: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
