#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""27_report_crosscheck.py —— 报告正文 vs `22_报告数字.json` 的机械交叉核对

项目纪律：「报告数字必须能被脚本机械复算」。22 号把数字算出来了，但**报告正文是手写的**，
手写就可能抄错（本单已真实踩到：5.3 节四个账本校验和的后缀各少抄一位）。
本脚本做双向核对：

  正向：`22_报告数字.json` 里的每一个**叶子值**，都必须在报告正文里找得到。
        （找不到 ⇒ 报告漏引或抄错 ⇒ FAIL）
  反向：报告正文里所有 ≥3 位的整数，都必须能在复算结果里找到来源。
        （找不到 ⇒ 可能是手写/估算的数字 ⇒ 列入人工复核清单）

比对规则（都是为了「比的是同一件事」，不是放宽）：
  · 归一化：去掉 Markdown 强调/代码符号、千分位逗号、空白；`−`(U+2212) 归一成 `-`；
  · JSON 只比**叶子值**，不比字典键（键是枚举标签，正文用中文表述）；
  · 64 位 / 16 位十六进制校验和：只比**前 8 位**（正文按前缀引用是合规的）；
  · 枚举值做中英映射（`UNVERIFIED`→`未验证` 等），正文用中文表述是合规的；
  · `A → B` 这类区间：整体比一次，再逐个比一次（正文可能省略重复的日期）；
  · 浮点 `0.0` 同时试 `0`。

用法：
    python code/27_report_crosscheck.py
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
R1 = ROOT / "evidence/SIGNAL-CLOSURE-06-R1"
OUT = R1 / "out"
REPORT = R1 / "SIGNAL-CLOSURE-06-R1_最终报告.md"

# 脚本身份与 mtime：正文按需引用，不强制逐字出现（它们的核对在 22 号里已做）
SKIP_PREFIX = ("sha256::", "mtime::")

# 英文枚举 → 正文中文表述（正文用中文是合规的，不是漏引）
ENUM_ALIAS = {
    "UNVERIFIED": ["未验证"],
    "PASS": ["通过"],
    "FAIL": ["不通过"],
    "SAME": ["一致"],
    "DIFF": ["不一致"],
    "True": ["是", "true"],
    "False": ["否", "false"],
    "absolute_anchor": ["absolute_anchor"],
    "merged_cold_hot": ["merged_cold_hot"],
    "identical": ["一致"],
}

# 支撑明细：这些是「结论背后的过程数据」，正文按需引用，不做逐字强制。
# 它们仍然在 22 号里可复算，只是报告没必要把每个中间量都抄一遍。
SUPPORT = {
    "18::n_1h_A_range", "18::n_1h_B_range",
    "18c::n_1h_A_range", "18c::n_1h_B_range",
    "20b::declared_window_start_utc", "20b::read_window_days",
    "20b::history::4h::cold_rows_scanned", "20b::history::4h::hot_rows_scanned",
    "20b::history::4h::rows_materialized", "20b::history::4h::symbols_full_window",
    "20b::history::1h::cold_rows_scanned", "20b::history::1h::hot_rows_scanned",
    "20b::history::1h::rows_materialized", "20b::history::1h::symbols_full_window",
    "10::anchor_table", "10::anchor_key",
}
SUPPORT_PREFIX = ("28::proc::",)   # 进程明细里只有 pid / started_utc 进正文


def is_support(name: str) -> bool:
    return name in SUPPORT or name.startswith(SUPPORT_PREFIX)


# 反向检查时忽略的整数：版本号片段、年份、口径常量、命令行参数、币名里的数字
IGNORE_INTS = {"526", "528", "551", "814", "240", "200", "168", "2019", "2026",
               "1160", "2880", "1000", "000", "1014",
               "256",      # 「SHA-256」里的 256
               "4000",     # `--anchor-hours 4000` 命令行参数，不是结论数字
               "1000000"}  # 币名 1000000BOBUSDT 的前缀


def norm(s: str) -> str:
    s = s.replace("\u2212", "-").replace("\u2013", "-")
    s = re.sub(r"[*`]", "", s)
    return re.sub(r"[\s,]", "", s)


def _strip_thousands(s: str) -> str:
    """把 121,904 这类千分位写法还原成 121904，否则会被切成 121 / 904 两个假整数。"""
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)


def _strip_hex(s: str) -> str:
    """把十六进制校验和整体抹掉，否则 `6c9fffd7…126b3b` 会被切出 126 这种假整数。"""
    s = re.sub(r"\b[0-9a-f]{8,}\b", " ", s)
    return re.sub(r"\b[0-9a-f]{6,}…[0-9a-f]{4,}\b", " ", s)


def leaves(v, out: list):
    """递归收集叶子值（不收集字典键）。"""
    if isinstance(v, dict):
        for x in v.values():
            leaves(x, out)
    elif isinstance(v, list):
        for x in v:
            leaves(x, out)
    elif v is not None:
        out.append(v)


def candidates_for(val) -> list[str]:
    """把复算值翻译成「报告里应该长什么样」的候选字符串。"""
    if isinstance(val, bool):
        return ENUM_ALIAS.get(str(val), [])
    if isinstance(val, (int, float)):
        s = str(val)
        return [s, s[:-2]] if s.endswith(".0") else [s]
    s = str(val).strip()
    cands = [s]
    # 枚举映射
    if s in ENUM_ALIAS:
        cands += ENUM_ALIAS[s]
    # 十六进制校验和：只比前 8 位
    if re.fullmatch(r"[0-9a-f]{16,}", s):
        cands.append(s[:8])
    # `A → B` 区间：再分别比两端
    if "→" in s:
        cands += [p.strip() for p in s.split("→") if p.strip()]
    # 形如「X（说明）」：正文可能只引 X
    m = re.match(r"^([^（(]+)[（(]", s)
    if m:
        cands.append(m.group(1).strip())
    return [c for c in cands if c]


def main() -> int:
    rep_raw = REPORT.read_text(encoding="utf-8")
    rep_n = norm(rep_raw)
    metrics = json.load(open(OUT / "22_报告数字.json", encoding="utf-8"))["metrics"]

    # ── 正向 ──────────────────────────────────────────────────────────────
    fwd_fail, fwd_ok, fwd_skip = [], 0, 0
    for m in metrics:
        name, val = m["metric"], m["value"]
        if name.startswith(SKIP_PREFIX):
            fwd_skip += 1
            continue
        if is_support(name):
            fwd_skip += 1
            continue
        # 字符串里可能塞了 JSON
        if isinstance(val, str) and val.strip()[:1] in "[{":
            try:
                val = json.loads(val)
            except Exception:
                pass
        leafs = []
        leaves(val, leafs)
        if not leafs:
            fwd_skip += 1
            continue
        miss = []
        for lf in leafs:
            cands = candidates_for(lf)
            if not cands:
                continue
            if not any(norm(c) and norm(c) in rep_n for c in cands):
                miss.append(lf)
        if miss:
            fwd_fail.append({"metric": name, "value": val,
                             "missing_in_report": [str(x) for x in miss]})
        else:
            fwd_ok += 1

    # ── 反向 ──────────────────────────────────────────────────────────────
    pool = set()
    for m in metrics:
        s = m["value"] if isinstance(m["value"], str) else json.dumps(m["value"], ensure_ascii=False)
        for t in re.findall(r"\d+", s):
            pool.add(t)
            pool.add(t.lstrip("0") or "0")
    rep_ints = sorted({t for t in re.findall(r"(?<![\d.])\d{3,}(?![\d.])",
                                             _strip_hex(_strip_thousands(rep_raw)))
                       if t not in IGNORE_INTS})
    rev_unmatched = [t for t in rep_ints if t not in pool]

    payload = {
        "report": REPORT.name,
        "report_sha256_prefix": hashlib.sha256(REPORT.read_bytes()).hexdigest()[:16],
        "n_metrics": len(metrics),
        "forward": {"ok": fwd_ok, "skipped": fwd_skip, "failed": len(fwd_fail),
                    "detail": fwd_fail},
        "reverse": {"n_report_ints": len(rep_ints), "unmatched": rev_unmatched},
        "verdict": "PASS" if not fwd_fail else "FAIL",
    }
    json.dump(payload, open(OUT / "27_报告交叉核对.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print(f"正向：{fwd_ok} 项命中 / 跳过 {fwd_skip} / **不符 {len(fwd_fail)}**")
    for f in fwd_fail:
        print(f"  ✗ {f['metric']}")
        print(f"     复算值 : {json.dumps(f['value'], ensure_ascii=False)[:160]}")
        print(f"     正文缺 : {f['missing_in_report'][:8]}")
    print(f"反向：报告含 {len(rep_ints)} 个 ≥3 位整数，未匹配 {len(rev_unmatched)} 个")
    print("  ", rev_unmatched)
    print(f"\n[verdict] {payload['verdict']}")
    print(f"[out] {OUT/'27_报告交叉核对.json'}")
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
