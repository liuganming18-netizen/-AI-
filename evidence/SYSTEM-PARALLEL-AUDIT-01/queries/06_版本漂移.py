#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-PARALLEL-AUDIT-01 / 并行工作第 4 条：版本漂移复检

目的
----
用户要求：「审核阶段绑定代码、配置、测试数据及运行进程身份；发现版本变化，只作废受影响项。」

本脚本做三件事，全部机械可复算：
1. 从 out/ 下各验收 JSON 的 `文件SHA` 段读出「审核当时绑定」的 SHA；
2. 重算磁盘上同名文件当前 SHA-256；
3. 按 IMPACT_MAP 把「哪个文件变了」映射成「哪个子项作废」，未变的一律标 `仍有效`。

设计约束
--------
* 只读：不写任何业务文件，只落 out/06_版本漂移.json。
* 不做全盘扫描（本机 78G 会 SIGTERM），只对 JSON 里显式列出的路径算 SHA。
* 任一文件缺失 ⇒ 标 `missing` 且对应子项作废，不静默跳过。

用法
----
    /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
        evidence/SYSTEM-PARALLEL-AUDIT-01/queries/06_版本漂移.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(HERE, "..", "out")

# 验收 JSON → 该 JSON 覆盖的任务子项清单（用于把文件漂移折算成子项作废）
AUDITS = {
    "03_A任务验收.json": {
        "task": "A",
        "items": ["A1_本金与风险参数生效", "A2_已有账户不重置", "A3_缺配置拒新单且不阻断退出"],
    },
    "04_B任务验收.json": {
        "task": "B",
        "items": ["B1_统一入口启动整链", "B2_进程状态准确", "B3_隔离与停止范围正确"],
    },
    "05_C任务验收.json": {
        "task": "C",
        "items": ["C1_原操作清单完整", "C2_UI与源账本一致", "C3_示例数据未混成真实证据"],
    },
}

# 文件 → 受影响子项（只有真正依赖该文件语义的子项才作废；其余保持有效）
IMPACT_MAP = {
    "框架/backend/live_store.py": [
        "A1_本金与风险参数生效", "A2_已有账户不重置", "A3_缺配置拒新单且不阻断退出",
        "C2_UI与源账本一致",
    ],
    "框架/backend/paper_engine.py": [
        "A1_本金与风险参数生效", "A3_缺配置拒新单且不阻断退出",
        "B1_统一入口启动整链", "C2_UI与源账本一致",
    ],
    "框架/backend/strategy_contracts.py": [
        "A3_缺配置拒新单且不阻断退出",
    ],
    "框架/backend/strategies/wx_execution.py": [
        "A1_本金与风险参数生效", "A3_缺配置拒新单且不阻断退出",
    ],
    "框架/backend/live_api.py": [
        "C2_UI与源账本一致", "C1_原操作清单完整",
    ],
    "框架/backend/api/routes.py": [
        "C2_UI与源账本一致",
    ],
    "框架/backend/service_manager.py": [
        "B1_统一入口启动整链", "B2_进程状态准确", "B3_隔离与停止范围正确",
    ],
    "框架/backend/unified_runtime.py": [
        "B1_统一入口启动整链", "B2_进程状态准确", "B3_隔离与停止范围正确",
    ],
    "框架/backend/adapter.py": [
        "C2_UI与源账本一致",
    ],
    "启动系统.command": [
        "B1_统一入口启动整链",
    ],
    "框架/backend/start.sh": [
        "B1_统一入口启动整链",
    ],
}


def sha256_of(rel: str) -> dict:
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        return {"exists": False, "sha256": None, "bytes": None, "mtime": None}
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    st = os.stat(p)
    return {
        "exists": True,
        "sha256": h.hexdigest(),
        "bytes": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def load_json(name: str) -> dict:
    p = os.path.join(OUT, name)
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    report: dict = {
        "生成时刻": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "用途": "并行工作第 4 条｜审核绑定文件 SHA 的漂移复检：只作废受影响项",
        "项目根": ROOT,
        "基准来源": {},
        "任务": {},
        "作废汇总": [],
        "总判定": None,
    }

    # ---- 基线快照（00_baseline.py 冻结时刻，供追溯，不参与作废判定） ----
    try:
        base = load_json("00_基线.json")
        report["基准来源"]["基线快照"] = {
            "生成时刻": base.get("生成时刻"),
            "文件SHA": base.get("文件SHA", {}),
        }
    except FileNotFoundError:
        report["基准来源"]["基线快照"] = None

    voided_all: list[str] = []

    for fname, meta in AUDITS.items():
        try:
            doc = load_json(fname)
        except FileNotFoundError:
            report["任务"][meta["task"]] = {
                "来源": fname, "状态": "missing_audit", "子项": {},
            }
            continue

        bound = doc.get("文件SHA", {}) or {}
        files: dict = {}
        voided: set[str] = set()

        for rel, rec in bound.items():
            want = (rec or {}).get("sha256")
            cur = sha256_of(rel)
            if not cur["exists"]:
                state = "missing"
            elif cur["sha256"] == want:
                state = "unchanged"
            else:
                state = "changed"
            files[rel] = {
                "审核时sha256": want,
                "审核时bytes": (rec or {}).get("bytes"),
                "审核时mtime": (rec or {}).get("mtime"),
                "当前sha256": cur["sha256"],
                "当前bytes": cur["bytes"],
                "当前mtime": cur["mtime"],
                "状态": state,
            }
            if state != "unchanged":
                for item in IMPACT_MAP.get(rel, meta["items"]):
                    if item in meta["items"]:
                        voided.add(item)

        items = {}
        for it in meta["items"]:
            items[it] = "作废_需重审" if it in voided else "仍有效"
        for it in sorted(voided):
            voided_all.append(f"{meta['task']}/{it}")

        unchanged_n = sum(1 for v in files.values() if v["状态"] == "unchanged")
        report["任务"][meta["task"]] = {
            "来源": fname,
            "审核时刻": doc.get("生成时刻"),
            "绑定文件数": len(files),
            "未变": unchanged_n,
            "已变": len(files) - unchanged_n,
            "文件": files,
            "子项": items,
            "结论": "全部子项仍有效" if not voided else f"{len(voided)} 项作废",
        }

    report["作废汇总"] = voided_all
    report["总判定"] = "无漂移_三任务结论全部仍有效" if not voided_all else "有漂移_仅作废受影响项"

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "06_版本漂移.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    # ---- 控制台摘要 ----
    print("=" * 72)
    print("版本漂移复检")
    print("=" * 72)
    for t in ("A", "B", "C"):
        r = report["任务"].get(t)
        if not r:
            print(f"[{t}] 缺少验收 JSON")
            continue
        if r.get("状态") == "missing_audit":
            print(f"[{t}] 缺少验收 JSON：{r['来源']}")
            continue
        print(f"[{t}] {r['来源']}  绑定 {r['绑定文件数']} 文件 / 未变 {r['未变']} / 已变 {r['已变']}")
        for it, st in r["子项"].items():
            print(f"     {'[作废]' if st.startswith('作废') else '[有效]'} {it}")
        for rel, v in r["文件"].items():
            if v["状态"] != "unchanged":
                print(f"     !! {v['状态']}: {rel}")
    print("-" * 72)
    print(f"总判定：{report['总判定']}")
    if voided_all:
        print("作废项：" + "、".join(voided_all))
    print(f"已落盘：{dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
