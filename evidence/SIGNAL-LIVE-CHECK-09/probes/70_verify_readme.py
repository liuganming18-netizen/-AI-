#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""70_verify_readme.py — 把 README / 回执里的每个数字回原始产物复算，再做正文 ↔ 复算双向对撞。

为什么必须有这一步（项目纪律，踩过 4 次抄错校验和、1 次 168/164 口径错）：
    「报告 + 数字」的工单，收口前不能靠人眼复核。必须
      ① 复算脚本：每个数字回到它的一手字段重新算一遍；
      ② 双向对撞：正文里写的数字必须能在复算结果里找到，复算结果里的关键数字必须在正文里出现。
    只做单向会漏「正文多写了一个不存在的数」，只做另一向会漏「算出来了但没写进去」。

用法：
    python 70_verify_readme.py            # 默认核 README + 39 号回执
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent      # evidence/SIGNAL-LIVE-CHECK-09
ROOT = BASE.parent.parent                          # 项目根
OUT = BASE / "out"

README = BASE / "README.md"
RECEIPT = ROOT / "evidence/_统一执行台账/39_SIGNAL-LIVE-CHECK-09_收口回执.md"


def j(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> int:
    ana = j("50_sample_analysis.json")
    bar1 = j("35_iso_replay_bar1_final.json")
    # 第二根用「修复后复跑」的产物：`30_iso_replay.py` 改过成交量取整，
    # 旧产物（36_）的 next_gate 带浮点长尾。两份的**对照结论**相同，
    # 按「旧脚本产物必须与当前脚本复跑成对引用」的纪律，以 37_ 为准、36_ 留档。
    bar2 = j("37_iso_replay_bar2_fixed.json")
    bar2_legacy = j("36_iso_replay_bar2_final.json")
    vc = j("20_version_check.json")            # 11:50Z 那次（修复重启后）
    # 13:29Z 那次（修完 N1 后重核）：前端 `SignalListPanel.tsx` 被外部改动 ⇒ ok=false。
    # 要求 1 的结论必须与这份一致，不能拿 11:50Z 那次全等的结论当现在的状态。
    vc2 = j("22_version_check_afterfix.json")
    drift = j("23_frontend_drift_1321Z.json")
    reg = (OUT / "90_regression.txt").read_text(encoding="utf-8", errors="replace")

    segs = ana["segments"]
    seg1, seg2 = segs[1], segs[2]
    tot = ana["totals"]

    # ── ① 复算：每个数字回到一手字段 ────────────────────────────────────────
    facts = {
        "bar1_迁移数": seg1["per_symbol_changed_n"],
        "bar1_迁移类型": seg1["migration_types"],
        "bar1_漏斗增量": seg1["delta_vs_prev"]["funnel_since_start"],
        "bar1_水位": seg1["watermark_1h_utc"],
        "bar2_迁移数": seg2["per_symbol_changed_n"],
        "bar2_迁移类型": seg2["migration_types"],
        "bar2_漏斗增量": seg2["delta_vs_prev"]["funnel_since_start"],
        "bar2_水位": seg2["watermark_1h_utc"],
        "合计_迁移次数": tot["per_symbol_changed_total"],
        "合计_推进根数": tot["bars_advanced"],
        "合计_三段一致": tot["three_stage_consistent"],
        "合计_异常数": tot["anomalies_total"],
        "bar1_逐币差异": bar1["compare"]["per_symbol_diff_n"],
        "bar1_丢弃数": [bar1["compare"]["discarded_n_prod"], bar1["compare"]["discarded_n_replay"]],
        "bar1_漏斗合计相同": bar1["compare"]["funnel_total_equal"],
        "bar1_生产未动": bar1["production_untouched"],
        "bar2_逐币差异": bar2["compare"]["per_symbol_diff_n"],
        "bar2_丢弃数": [bar2["compare"]["discarded_n_prod"], bar2["compare"]["discarded_n_replay"]],
        "bar2_漏斗合计相同": bar2["compare"]["funnel_total_equal"],
        "bar2_生产未动": bar2["production_untouched"],
        "bar2_截止": bar2["clock"]["upto_utc"],
    }
    facts["bar2_新旧产物结论一致"] = all(
        bar2["compare"].get(k) == bar2_legacy["compare"].get(k)
        for k in ("per_symbol_diff_n", "state_counter_equal", "candidate_ids_equal",
                  "discarded_ids_equal", "funnel_total_equal", "watermark_equal"))
    m = re.search(r"(\d+) failed, (\d+) passed, (\d+) skipped", reg)
    facts["回归"] = [int(m.group(1)), int(m.group(2)), int(m.group(3))] if m else None

    # 版本一致性：两次核对都要记，结论只认最新那次（结论绑定核对时刻）
    facts["版本核对_1150Z通过"] = vc.get("ok")
    facts["版本核对_1329Z通过"] = vc2.get("ok")
    fp = vc2["delivered_fingerprint"]
    backend_files = [k for k in fp if not k.startswith("UI界面")]
    facts["后端交付文件全等"] = all(fp[k]["same"] for k in backend_files)
    facts["后端交付文件数"] = len(backend_files)
    facts["版本核对失败项"] = [c["name"] for c in vc2["checks"] if not c["ok"]]
    facts["后端落后文件数"] = (len(vc2.get("stale_backend_files") or [])
                          + len(vc2.get("stale_runtime_files") or []))
    facts["前端漂移_交付sha"] = drift["交付版本（07-R1 登记）"]["sha256"]
    facts["前端漂移_当前sha"] = drift["当前"]["sha16"]
    facts["前端漂移_字节差"] = drift["变化"]["字节差"]
    facts["前端漂移_mtime"] = drift["当前"]["mtime_utc"]

    # 迁移↔漏斗对撞（复算，不信 JSON 里的 all_equal 字段）
    # 只在「该段只推进 1 根 1h」时成立：逐币阶段是首尾快照差，跨多根会吃掉中间迁移。
    # 这里必须和 50_analyze_samples.py 用同一条纪律，否则两边结论会打架。
    recon_ok, recon_skipped = [], []
    for s in (seg1, seg2):
        adv = (s.get("delta_vs_prev") or {}).get("watermark_advance_bars")
        if adv != 1:
            recon_skipped.append({"wm": s["watermark_1h_utc"], "advance_bars": adv})
            continue
        fd = s["delta_vs_prev"]["funnel_since_start"] or {}
        ent = {}
        for k, v in (s["migration_types"] or {}).items():
            ent[k.split("->")[1]] = ent.get(k.split("->")[1], 0) + v
        for st in ("BOX", "SWEEP", "RETEST"):
            recon_ok.append(ent.get(st, 0) == int(fd.get(st, 0) or 0))
        recon_ok.append(ent.get("IDLE", 0) == int(fd.get("EXPIRED", 0) or 0)
                        + int(fd.get("INVALIDATED", 0) or 0))
    facts["迁移漏斗对撞_复算全过"] = (all(recon_ok) if recon_ok else None)
    facts["迁移漏斗对撞_不可判段"] = recon_skipped

    # ── ② 双向对撞：正文必须写到的字符串 ────────────────────────────────────
    readme = README.read_text(encoding="utf-8")
    receipt = RECEIPT.read_text(encoding="utf-8")
    delivery = (BASE / "DELIVERY-隔离入口.md").read_text(encoding="utf-8")
    # 版本指纹这类「同一事实三处登记」的东西，允许出现在任一份交付文档里；
    # 但**必须至少出现一次**，且三份文档之间不许互相矛盾（下面单独核）。
    corpus = readme + "\n" + receipt + "\n" + delivery

    must_in_readme = [
        str(facts["bar1_迁移数"]), "46", "526",
        str(facts["bar2_迁移数"]),
        "7958", "1931", "1176", "4123", "3390",
        "51", "15 失败 / 364 通过 / 79 跳过",
        "12:59:59Z", "11:59:59Z",
    ]
    # 版本指纹：三份文档合计必须全覆盖
    must_in_corpus = [
        "6218416f", "f2d40893", "b1289ce6", "bf8eb3ce", "07aefc6b",
        "1f1167de", "f4691db2", "1e1218ea",
        # 前端漂移必须登记：交付值 / 当前值 / 发生时刻
        "546fdab0", "13:21:41Z",
    ]
    must_in_receipt = ["46", "3", "526", "15 失败 / 364 通过 / 79 跳过",
                       "2026-10-25T12:00:00Z", "13:00:08Z"]

    missing = []
    for t in must_in_readme:
        if t not in readme:
            missing.append(("README", t))
    for t in must_in_corpus:
        if t not in corpus:
            missing.append(("三份交付文档合计", t))
    for t in must_in_receipt:
        if t not in receipt:
            missing.append(("回执", t))

    # 反向：正文里不许出现已知错误口径的结论
    forbidden = [
        ("README", "funnel_history_equal=True"),   # 本单从未为 True
        ("README", "漏斗相同=True"),
        # 13:29Z 核对已报该文件不符，正文不许再写「版本一致＝通过」这种整体结论
        ("README", "交付版本 | **通过**"),
    ]
    for src, t in forbidden:
        if t in readme:
            missing.append((src + "（不该出现）", t))

    # ── ③ 断言：复算结果本身必须满足的业务条件 ──────────────────────────────
    checks = [
        ("两根合计推进 2 根", facts["合计_推进根数"] == 2),
        ("三段一致（0/0/0）", facts["合计_三段一致"] is True),
        ("链路异常 0 处", facts["合计_异常数"] == 0),
        ("迁移↔漏斗复算全过", facts["迁移漏斗对撞_复算全过"] is True),
        ("bar1 逐币差异 0", facts["bar1_逐币差异"] == 0),
        ("bar2 逐币差异 0", facts["bar2_逐币差异"] == 0),
        ("bar1 丢弃 51/51", facts["bar1_丢弃数"] == [51, 51]),
        ("bar2 丢弃 51/51", facts["bar2_丢弃数"] == [51, 51]),
        ("bar1 漏斗合计相同", facts["bar1_漏斗合计相同"] is True),
        ("bar2 漏斗合计相同", facts["bar2_漏斗合计相同"] is True),
        ("bar1 生产未动", facts["bar1_生产未动"] is True),
        ("bar2 生产未动", facts["bar2_生产未动"] is True),
        ("bar2 新旧产物结论一致（改格式没改结论）",
         facts["bar2_新旧产物结论一致"] is True),
        ("回归 = 基线 15/364/79", facts["回归"] == [15, 364, 79]),
        ("bar1+bar2 迁移数 = 合计", facts["bar1_迁移数"] + facts["bar2_迁移数"]
         == facts["合计_迁移次数"]),
        # 版本一致性：只认最新那次核对，且结论必须停在「后端通过、前端一个文件不符」
        ("后端交付文件全等", facts["后端交付文件全等"] is True),
        ("后端无「改了没重启」文件", facts["后端落后文件数"] == 0),
        ("版本核对失败项只有前端 SignalListPanel.tsx",
         len(facts["版本核对失败项"]) == 1
         and "SignalListPanel.tsx" in facts["版本核对失败项"][0]),
        ("前端漂移已登记（交付 sha / 当前 sha / 时刻）",
         facts["前端漂移_交付sha"].startswith("bf8eb3ce")
         and facts["前端漂移_当前sha"].startswith("546fdab0")
         and facts["前端漂移_mtime"] == "2026-10-25T13:21:41Z"
         and "546fdab0" in corpus and "13:21:41Z" in corpus),
        ("正文无缺失项", not missing),
    ]

    print("== 复算事实 ==")
    for k, v in facts.items():
        print(f"  {k}: {v}")
    print("\n== 断言 ==")
    ok = True
    for name, cond in checks:
        print(f"  [{'OK' if cond else 'FAIL'}] {name}")
        ok = ok and bool(cond)
    if missing:
        print("\n== 正文缺失/不该出现 ==")
        for src, t in missing:
            print(f"  {src}: {t!r}")

    out = {"probe": "SIGNAL-LIVE-CHECK-09/70_verify_readme",
           "facts": facts,
           "assertions": {n: bool(c) for n, c in checks},
           "text_missing": [{"file": s, "token": t} for s, t in missing],
           "pass": ok}
    (OUT / "70_verify_readme.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果：{'全部通过' if ok else '有不通过项'} → {OUT / '70_verify_readme.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
