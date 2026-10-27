#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""70_verify_readme.py — README 正文 ↔ 复算结果 双向对撞。

为什么要有这个脚本：报告里的每个数字都必须能回一手字段复算。
人眼复核抓不住「正文写 24.8、脚本算 24.84」「正文写 354 一致、脚本只跑了 2 个」
这类问题——本单初版就有两处（ER 读了不存在的字段名、箱体只验了 2 个币）。

三组检查：
  A 数字对撞   ：正文出现的每个数，逐个回到 out/10_diag_chain.json 的原始字段
  B 禁写检查   ：已被推翻的结论不许作为结论出现（允许在「初版错了」的修正叙述里被引用）
  C 引用白名单 ：第四节页面代码块是「页面原文引用」，不参与复算，但必须标明时点

用法：
    python 70_verify_readme.py      # 全过 exit 0，任一不过 exit 1
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
README = BASE / "README.md"
OUT = BASE / "out"
J = json.loads((OUT / "10_diag_chain.json").read_text(encoding="utf-8"))
TEXT = README.read_text(encoding="utf-8")

results: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"检查": name, "通过": bool(ok), "说明": detail})
    return bool(ok)


def num_forms(v: float) -> list[str]:
    """同一个数的几种合法写法（正文可能按不同精度写）。"""
    forms = set()
    for spec in ("g", ".4f", ".5f", ".6f", ".3f", ".2f"):
        try:
            forms.add(format(v, spec))
        except (ValueError, TypeError):
            pass
    forms.add(str(v))
    return [x for x in forms if x]


def has_num(sub: str, v: float) -> bool:
    """正文里是否出现该数的任一合法表示。"""
    return any(x in TEXT for x in num_forms(v))


# ═══ A 数字对撞 ══════════════════════════════════════════════════════════
E = J["①引擎"]
W = J["②写入"]
A = J["③接口"]["引擎"]
M = J["⑤市场活跃度"]
B = J["⑥箱体宽窄"]
R = J["⑦箱体重算"]

# A1 引擎：25 个等突破的币，逐个还差多少
check("A1 RETEST 币数 = 正文「25 个币」",
      E["RETEST币数"] == 25 and "25 个币，没有一个接近" in TEXT,
      f"JSON={E['RETEST币数']}")
check("A2 应出未出为空（若有币满足就是缺陷）",
      E["收盘+量都满足（应出未出）"] == [],
      f"JSON={E['收盘+量都满足（应出未出）']}")

for i, r in enumerate(E["最近5个"]):
    sym = r["symbol"]
    check(f"A3.{i} {sym} 还差 {r['gap_pct']:.2f}% 在正文",
          f"{r['gap_pct']:.2f}%" in TEXT and sym in TEXT,
          f"JSON={r['gap_pct']:.4f}")
    check(f"A3.{i} {sym} 收盘价 {r['close']} 在正文", has_num(sym, r["close"]),
          f"JSON={r['close']}")
    check(f"A3.{i} {sym} 门槛 {r['need_close']:.6f} 在正文", has_num(sym, r["need_close"]),
          f"JSON={r['need_close']}")

far = E["最远1个"]
check(f"A4 最远 {far['symbol']} 差 {far['gap_pct']:.1f}% 在正文",
      f"{far['gap_pct']:.1f}%" in TEXT and far["symbol"] in TEXT,
      f"JSON={far['gap_pct']:.4f}")

# A5 写入测试
check("A5 写入 0 行 → 1 行 在正文",
      W["写入前 live_signal 行数"] == 0 and W["写入后"] == 1 and "0 行 → 1 行" in TEXT,
      f"JSON={W['写入前 live_signal 行数']}→{W['写入后']}")
check("A6 写入无报错", W["错误"] is None and W["结论"] == "通过", f"JSON 结论={W['结论']}")

# A7 引擎状态计数（README 3.2 的时点说明块）
sc = A["state_counter"]
check("A7 state_counter 四个数都在正文（时点说明块）",
      f"BOX {sc['BOX']} / SWEEP {sc['SWEEP']} / RETEST {sc['RETEST']} / IDLE {sc['IDLE']}" in TEXT,
      f"JSON={sc}")

# A8 漏斗 history
fh = A["funnel"]["history"]
check("A8 历史回放漏斗 7932→1923→1167→51 在正文",
      (f"箱体 {fh['BOX']} → 扫低 {fh['SWEEP']} → 回踩 {fh['RETEST']} → 候选 {fh['CANDIDATE']}"
       in TEXT),
      f"JSON={ {k: fh[k] for k in ('BOX','SWEEP','RETEST','CANDIDATE')} }")
check("A9 历史候选数 51 在正文",
      A["历史候选数"] == 51 and "51 条" in TEXT and fh["CANDIDATE"] == 51,
      f"JSON={A['历史候选数']}")
check("A10 历史候选最晚 = 2026-10-11T20:00Z 在正文",
      A["历史候选最晚"] == "2026-10-11T20:00:00Z" and "2026-10-11T20:00Z" in TEXT,
      f"JSON={A['历史候选最晚']}")

# A11 市场活跃度
check("A11 参与统计 528 币在正文",
      M["参与统计的币"] == 528 and "528 个币" in TEXT, f"JSON={M['参与统计的币']}")
check("A12 振幅中位 24.8% 在正文",
      f"{M['振幅中位_pct']:.1f}%" in TEXT, f"JSON={M['振幅中位_pct']}")
check("A13 振幅>20% 的 360 个在正文",
      f"{M['振幅大于20_pct的币']} 个 / {M['参与统计的币']}" in TEXT,
      f"JSON={M['振幅大于20_pct的币']}")
check("A14 振幅>30% 的 194 个在正文",
      f"{M['振幅大于30_pct的币']} 个" in TEXT, f"JSON={M['振幅大于30_pct的币']}")

top = {t["symbol"]: t for t in M["Top10"]}
for sym, amp_key in (("IRYSUSDT", True), ("AGTUSDT", False), ("JCTUSDT", False)):
    t = top.get(sym)
    if t is None:
        check(f"A15 {sym} 在 Top10", False, "不在 Top10 里")
        continue
    if amp_key:
        check(f"A15 {sym} 振幅 {t['amplitude_pct']:.0f}% 在正文",
              f"{t['amplitude_pct']:.0f}%" in TEXT, f"JSON={t['amplitude_pct']:.1f}")
    check(f"A15 {sym} 净涨 +{t['net_pct']:.0f}% 在正文",
          f"+{t['net_pct']:.0f}%" in TEXT, f"JSON={t['net_pct']:.2f}")

# A16 箱体分布
check("A16 有箱体的币 354 在正文",
      B["有箱体的币"] == 354 and "354 个" in TEXT, f"JSON={B['有箱体的币']}")
check("A17 H/L 中位 1.21 倍在正文",
      f"{B['H/L中位']:.2f} 倍" in TEXT, f"JSON={B['H/L中位']}")
check("A18 H/L>2 倍只有 4 个在正文",
      B["H/L大于2倍的币数"] == 4 and f"只有 {B['H/L大于2倍的币数']} 个超过 2 倍" in TEXT,
      f"JSON={B['H/L大于2倍的币数']}")
bk = B["H/L分档"]
check("A19 分档六个数字都在正文表格里",
      all(f"| {v} |" in TEXT or f"| {v}（" in TEXT for v in bk.values()),
      f"JSON={bk}")
for ex in B["最极端"][:2]:
    check(f"A20 {ex['symbol']} H/L {ex['hl_ratio']:.2f} 在正文",
          f"{ex['hl_ratio']:.2f}" in TEXT, f"JSON={ex['hl_ratio']:.4f}")
check("A21 ER 字段非空 = 全部有箱体的币（旧版读错字段名会得 0）",
      B["ER字段非空的币数"] == B["有箱体的币"],
      f"JSON={B['ER字段非空的币数']}/{B['有箱体的币']}")

# A22 箱体重算
check("A22 箱体重算 参与=逐位一致=354，不一致=0",
      R["参与比对的币"] == 354 and R["逐位一致"] == 354 and R["不一致"] == 0,
      f"JSON={R['逐位一致']}/{R['参与比对的币']}, 不一致={R['不一致']}")
check("A23 四项分项一致数都是 354",
      all(R[k] == 354 for k in ("H一致数", "L一致数", "ER一致数", "WA一致数")),
      f"JSON={ {k: R[k] for k in ('H一致数','L一致数','ER一致数','WA一致数')} }")
check("A24 正文写了「354 / 354」",
      "**354 / 354**" in TEXT, "表格里四项都应是 354 / 354")
check("A25 正文保留了舍入坑的判据说明",
      "只存 6 位小数" in TEXT and "同一时刻的不同表示不算不一致" in TEXT,
      "这是本轮踩过的坑，必须留痕")

# ═══ B 禁写检查（带豁免）══════════════════════════════════════════════
FORBIDDEN = [
    "静默是节律",
    "固有节律",
    "只剩加时间",
    "不是缺陷",
]
# 这些词出现在同一段里，说明是在「复述初版错在哪」，属合法引用
EXEMPT_CUES = ("初版", "这是错的", "被证伪", "翻案", "已重写", "修正说明")

exempt, violations = [], []
for phrase in FORBIDDEN:
    for m in re.finditer(re.escape(phrase), TEXT):
        pos = m.start()
        ctx = TEXT[max(0, pos - 400):pos + 200]
        cue = next((c for c in EXEMPT_CUES if c in ctx), None)
        rec = {"短语": phrase, "位置": pos, "豁免依据": cue,
               "上下文": ctx[max(0, len(ctx) - 200):].replace("\n", " ")[-160:]}
        (exempt if cue else violations).append(rec)

check("B1 已推翻结论没有作为结论出现", not violations,
      f"违规 {len(violations)} 处: {[v['短语'] for v in violations]}")
check("B2 豁免引用每一条都带反证词", all(e["豁免依据"] for e in exempt),
      f"豁免 {len(exempt)} 处")
check("B3 豁免数量在预期内（≤3，多了要人工复核）", len(exempt) <= 3,
      f"实际 {len(exempt)} 处")
check("B4 正文有显式的修正说明块",
      "**修正说明**" in TEXT and "这个结论**是错的**" in TEXT,
      "推翻旧结论必须写明，不能静默改")

# ═══ C 引用白名单：页面原文代码块必须标时点 ══════════════════════════
check("C1 第四节标明了是页面原文引用 + 时点",
      "页面原文引用" in TEXT and "12:00Z" in TEXT,
      "代码块里的 9（回踩）与 JSON 的 10 不同，必须说明是不同时点")
check("C2 正文说明了 313 与 312 的时点差异",
      "**313**" in TEXT and "**312**" in TEXT and "时点说明" in TEXT,
      "跨时点直接比会得出假差异")
check("C3 没有把 since_start 与 history 混为一谈",
      "since_start" in TEXT and "history" in TEXT,
      "预热回放 vs 本进程累加，不能相加或互替")

# ═══ 汇总 ═══════════════════════════════════════════════════════════════
passed = sum(1 for r in results if r["通过"])
total = len(results)
ok_all = passed == total

out = {
    "probe": "SIGNAL-CHAIN-DIAG-10/verify_readme",
    "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "readme": str(README.relative_to(BASE.parents[0]) if False else README.name),
    "source": "out/10_diag_chain.json",
    "总断言": total,
    "通过": passed,
    "失败": total - passed,
    "结论": "全过" if ok_all else "有失败",
    "断言明细": results,
    "禁写豁免": exempt,
    "禁写违规": violations,
}
p = OUT / "70_verify_readme.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

for r in results:
    print(f"[{'OK' if r['通过'] else 'FAIL'}] {r['检查']}"
          + (f"  |  {r['说明']}" if r["说明"] and not r["通过"] else ""))
print(f"\n{passed}/{total} 通过 → {p}")
if exempt:
    print("\n禁写豁免（合法引用被推翻的旧结论）：")
    for e in exempt:
        print(f"  · 「{e['短语']}」@ {e['位置']} 依据=「{e['豁免依据']}」")
if violations:
    print("\n禁写违规：")
    for v in violations:
        print(f"  · 「{v['短语']}」@ {v['位置']}: {v['上下文']}")
raise SystemExit(0 if ok_all else 1)
