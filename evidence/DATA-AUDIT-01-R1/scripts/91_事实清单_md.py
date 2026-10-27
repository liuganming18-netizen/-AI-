"""91 · 由 FACTS-DATA-v1.json 生成人读版事实清单（避免手抄数字）

产物：`02_数据事实清单_v1.md`
本脚本只读 `out/FACTS-DATA-v1.json`，不做任何计算 ⇒ md 里的数字与 JSON 必然一致。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import OUT

d = json.loads((OUT / "FACTS-DATA-v1.json").read_text(encoding="utf-8"))
facts = d["facts"]

lines = []
A = lines.append

A(f"# 02 · 数据事实清单 {d['version']}")
A("")
A(f"- 版本：**{d['version']}**（首版；上一版：无）")
A(f"- 生成时刻（UTC）：{d['generated_at_utc']}")
A(f"- 适用范围：{d['scope']}")
A(f"- 事实条数：{len(facts)}（已证 {d['counts']['已证']}，未知 {d['counts']['未知']}，"
  f"含纠正旧说法 {d['counts']['superseded']} 条）")
A("- 机器可读版本：`out/FACTS-DATA-v1.json`（本文件由 `scripts/91_事实清单_md.py` 从它生成，"
  "数字不是手抄）")
A("")
A("## 使用纪律")
A("")
for x in d["discipline"]:
    A(f"- {x}")
A("")
A("## 事实表")
A("")
A("| ID | 事实 | 值 | 口径 | 统计时点（UTC） | 置信度 | 证据 |")
A("|---|---|---|---|---|---|---|")
for f in facts:
    val = str(f["value"]).replace("|", "／").replace("\n", " ")
    A(f"| {f['id']} | {f['statement']} | {val} | {f['population']} | "
      f"{f['stats_at_utc']} | {f['confidence']} | `{f['evidence']}` |")
A("")
A("## 本版纠正的旧说法")
A("")
sup = [f for f in facts if f["supersedes"]]
if not sup:
    A("（无）")
else:
    A("| ID | 本版事实 | 被纠正的旧说法 |")
    A("|---|---|---|")
    for f in sup:
        A(f"| {f['id']} | {f['statement']}：{str(f['value']).replace('|','／')} | "
          f"{f['supersedes']} |")
A("")
A("## 未知项（不计入已证）")
A("")
unk = [f for f in facts if f["confidence"] != "已证"]
if not unk:
    A("（无）")
else:
    A("| ID | 事实 | 结论 | 说明 |")
    A("|---|---|---|---|")
    for f in unk:
        A(f"| {f['id']} | {f['statement']} | {f['value']} | {f.get('note') or ''} |")
A("")
A("## 需要展开看的备注")
A("")
noted = [f for f in facts if f.get("note")]
for f in noted:
    A(f"- **{f['id']}**（{f['statement']}）：{f['note']}")
A("")
A("## 引用规则（其他两单照此引用）")
A("")
A("1. 引用时必须带上 `ID` 与 `stats_at_utc`——结论绑定统计时刻，跨时段不能复用。")
A("2. `population` 写明「全量」的才能当全池结论；写「抽样」「N 个对照点」的不得引申。")
A("3. `confidence=未知` 的条目**不得**在任何材料里表述为「已确认」。")
A("4. 与本清单冲突的旧数字，一律以本清单为准，并注明「按 FACTS-DATA-v1」。")
A("5. 需要新增事实时，先在本目录加脚本产物，再升版本到 `FACTS-DATA-v2`，不要直接改 md。")
A("")
A("## 版本变更记录")
A("")
A("| 版本 | 时刻（UTC） | 变更 |")
A("|---|---|---|")
A(f"| {d['version']} | {d['generated_at_utc']} | 首版：把 `DATA-AUDIT-01` 的结论升级为全量可复算事实，"
  "并纠正关闭时间推断、funding 列结构、索引口径、聪明钱覆盖、单位口径等 |")
A("")

# 默认落到整改目录根（交付形态）；用 WX_OUT_DIR 复算时落到该目录内，
# 避免一次复算把已交付的 02 号文件刷掉。
target_dir = OUT.parent if OUT.name == "out" else OUT
path = target_dir / "02_数据事实清单_v1.md"
path.write_text("\n".join(lines), encoding="utf-8")
print(f"已生成 {path}（{len(lines)} 行）")
