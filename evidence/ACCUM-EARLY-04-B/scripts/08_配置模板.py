# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜08 生成「空白配置表单」与「字段表」。

为什么要有空白表单：工单要求「未批准阈值作为显式必填配置」。
一张空表能让批准方**逐格填**，填完就是 `kind="approved"` 的配置；
本单的 `fixture` 自测配置与它长得一样，但 `approved=false`，不会被误当成批准。

产物：
  config/批准配置_空白表单.json   —— 36 个字段全 null，只有结构骨架
  配置字段表.md                  —— 每个字段：谁定的、代码支持哪些取值、对应哪条待决问题
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

FORM = C.EVID / "config" / "批准配置_空白表单.json"
TABLE = C.EVID / "配置字段表.md"


def _blank(paths) -> dict:
    out: dict = {}
    for p in paths:
        cur = out
        parts = p.split(".")
        for seg in parts[:-1]:
            cur = cur.setdefault(seg, {})
        cur[parts[-1]] = None
    return out


def main() -> int:
    import accum_early_entry as AE
    import accum_pending as AP

    # 配置字段 → 待决项（反查）
    rev = {}
    for pk, paths in AE.PENDING_TO_CONFIG.items():
        for p in paths:
            rev[p] = pk

    form = _blank(sorted(AE.REQUIRED_PATHS))
    form["_meta"] = {
        "用途": "批准方逐格填写的空白表单；**填写不等于批准**，还须把 approved 改为 true 并给出 approval_ref",
        "字段数": len(AE.REQUIRED_PATHS),
        "格式版本": AE.CONFIG_SCHEMA,
        "代码版本": AE.VERSION,
        "纪律": "任何一格为 null 或缺失 ⇒ 开仓阻塞；白名单外的取值 ⇒ 视为不支持，同样阻塞",
        "本单未填任何值": True,
    }
    FORM.parent.mkdir(parents=True, exist_ok=True)
    FORM.write_text(json.dumps(form, ensure_ascii=False, indent=1), encoding="utf-8")

    rows = []
    unmapped = []
    for p in sorted(AE.REQUIRED_PATHS):
        pk = rev.get(p)
        # 有些冲突项没有进 PENDING_TO_CONFIG，用字段尾名回查冲突表（不猜、只按名字对上）
        if not pk and p.split(".")[-1] in AP.CONFLICTS:
            pk = p.split(".")[-1]
        src, question = "结构性字段（格式/身份/开关）", "—"
        if pk and pk in AP.PENDING:
            src = f"待决项 `{pk}`"
            question = (AP.PENDING[pk].get("问题") or "").replace("\n", " ")
            if not question and pk in AP.CONFLICTS:
                question = (AP.CONFLICTS[pk].get("冲突内容") or "").replace("\n", " ")
        elif pk and pk in AP.CONFLICTS:
            src = f"口径冲突 `{pk}`"
            question = (AP.CONFLICTS[pk].get("冲突内容") or "").replace("\n", " ")
        elif pk:
            src = f"待决项 `{pk}`"
            tail = p.split(".")[-1]
            if tail in AP.CONFLICTS:
                question = (AP.CONFLICTS[tail].get("冲突内容") or "").replace("\n", " ")
        else:
            unmapped.append(p)
        allowed = AE.IMPLEMENTED.get(p)
        rows.append({
            "field": p,
            "source": src,
            "question": question[:180],
            "implemented_values": sorted(allowed) if isinstance(allowed, set) else
            (allowed if isinstance(allowed, (list, tuple)) else allowed),
        })

    md = ["# ACCUM-EARLY-04-B｜配置字段表", "",
          f"共 {len(AE.REQUIRED_PATHS)} 个必填字段，**一个默认值都不给**；"
          f"其中 {len(rows) - len(unmapped)} 个能追到待决表或冲突表，{len(unmapped)} 个属结构性字段。", "",
          "| 字段 | 谁定的 | 代码支持哪些取值 | 待决问题 |", "|---|---|---|---|"]
    for r in rows:
        allowed = r["implemented_values"]
        a = "（自由取值/数值）" if allowed is None else "、".join(
            str(x) for x in (allowed if isinstance(allowed, list) else [allowed]))
        md.append(f"| `{r['field']}` | {r['source']} | {a} | {r['question']} |")
    md += ["", "## 空白表单", "",
           f"`config/批准配置_空白表单.json`（{len(AE.REQUIRED_PATHS)} 格全 null）。", "",
           "填完之后必须同时改两处，否则仍然阻塞开仓：`kind` 改为 `approved`、"
           "`approved` 改为 `true` 并在 `approval_ref` 写明批准依据。", "",
           "> 白名单外的取值不会被静默接受：`load_config()` 会把它记进 `unsupported`，"
           "`open_allowed` 保持 false。", ""]
    TABLE.write_text("\n".join(md), encoding="utf-8")

    res = {"form": str(FORM), "table": str(TABLE),
           "fields": len(AE.REQUIRED_PATHS),
           "mapped_to_pending_or_conflict": len(rows) - len(unmapped),
           "structural": len(unmapped),
           "unmapped": unmapped,
           "rows": rows}
    C.dump("08_配置模板.json", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
