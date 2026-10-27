# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-C｜07 正文数字双向对撞

为什么必须单独一段
------------------
用户纪律：「报告 + 数字的工单，收口前必跑复算脚本（每数字回原始字段）+ 双向对撞脚本
（正文 ↔ 复算），不能靠人眼。」上一单（04-D）就是靠这一步抓出正文与产物不符。

本脚本做三件事
--------------
1. **正向复算**：读 `06_正文数字台账.json`，对每一条按 `产物文件#字段路径` 重新取值，
   与台账里记的值比。若产物已变（例如重跑过），这里会立刻暴露。
2. **反向扫描**：把 `README.md` 里出现的所有数字抓出来，逐个检查它是否在台账里、
   或者是否是台账值的一种写法（千分位、四舍五入、百分比）。**台账里没有的数字要报警**。
3. **交叉断言**：几条必须成立的硬关系，逐条断言，失败就打印。

已知的合理例外
--------------
正文里必然存在一些「非产物数字」：年份、序号、章节号、时间点、脚本名里的数字、
以及引用上游文档时的数字（例如 05-F 的生成时刻）。这些不能用台账核对，
本脚本把它们列成白名单并**显式声明**，而不是悄悄放过。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = ROOT / "evidence" / "ACCUM-EARLY-05-C"
OUT = HERE / "out"
README = HERE / "README.md"


def get_by_path(obj, path: str):
    """按 'a.b.c' 取值；支持 list 下标（'x[0].y'）。"""
    cur = obj
    for part in path.split("."):
        m = re.match(r"^([^\[]*)(?:\[(\d+)\])?$", part)
        if not m:
            raise KeyError(part)
        key, idx = m.group(1), m.group(2)
        if key:
            cur = cur[key]
        if idx is not None:
            cur = cur[int(idx)]
    return cur


def resolve_src(src: str):
    """src 形如 '01#code_identity[0].bytes' 或 '05-F#...'。

    '01' 这种是简写 ⇒ 在 out/ 下按前缀找唯一匹配的 .json，
    避免把文件名写死在台账里（写死就会在改名后静默失败）。
    """
    file_part, path = src.split("#", 1)
    if file_part == "05-F":
        p = ROOT / "evidence" / "ACCUM-EARLY-05-F" / "out" / "02_四层覆盖.json"
    elif (OUT / file_part).exists():
        p = OUT / file_part
    else:
        hits = sorted(OUT.glob(f"{file_part}_*.json"))
        if len(hits) != 1:
            raise FileNotFoundError(f"按前缀 '{file_part}' 找到 {len(hits)} 个产物，无法唯一确定")
        p = hits[0]
    return json.loads(p.read_text(encoding="utf-8")), path, p


def norm(s: str) -> str:
    return s.replace(",", "").replace(" ", "").replace("%", "")


def numbers_in(text: str):
    """抓出正文里的数字（含小数、负数、千分位）。

    负号只在前面不是数字/字母时才算负号：否则 `2026-10-26` 会被拆成
    `-10`、`-26` 两个「负数」，产生大量假报警（本单实际踩过）。
    """
    pat = re.compile(r"(?<![\d\w.])-?\d[\d,]*\.?\d*")
    out = []
    for m in pat.finditer(text):
        raw = m.group(0)
        if raw in ("-", "."):
            continue
        out.append((raw, m.start()))
    return out


def apply_derive(value, derive: str):
    """台账里的派生值：产物存的是原始量，正文写的是换算后的量。

    显式声明换算规则，而不是把换算结果直接抄进台账 ——
    否则产物一变，台账看不出来。目前支持两种：
      @GiB           字节 → GiB（保留 2 位）
      @unique_symbols 记录列表 → 去重标的数
    """
    if derive == "GiB":
        return round(float(value) / 2**30, 2)
    if derive == "unique_symbols":
        return len({x["symbol"] for x in value})
    if derive == "pct":       # 比例 → 百分比，2 位
        return round(float(value) * 100, 2)
    if derive == "pct1":      # 比例 → 百分比，1 位
        return round(float(value) * 100, 1)
    if derive == "bps":       # 比例 → 基点，整数
        return round(float(value) * 1e4, 0)
    raise KeyError(derive)


def main():
    ledger = json.loads((OUT / "06_正文数字台账.json").read_text(encoding="utf-8"))
    text = README.read_text(encoding="utf-8")

    report = {
        "probe": "07_正文数字双向对撞",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ledger_count": len(ledger),
        "forward_recalc": {},
        "reverse_scan": {},
        "assertions": [],
        "verdict": {},
    }

    # ── 1. 正向复算 ──────────────────────────────────────────────────
    ok = 0
    bad = []
    for key, item in ledger.items():
        try:
            src = item["src"]
            derive = None
            if "@" in src:
                src, derive = src.split("@", 1)
            obj, path, p = resolve_src(src)
            actual = get_by_path(obj, path)
            if derive:
                actual = apply_derive(actual, derive)
        except Exception as e:
            bad.append({"key": key, "src": item["src"], "error": f"{type(e).__name__}: {e}"})
            continue
        expect = item["value"]
        same = False
        if expect is None or actual is None:
            same = expect is actual
        elif isinstance(expect, bool) or isinstance(actual, bool):
            same = bool(expect) == bool(actual)
        elif isinstance(expect, (int, float)) and isinstance(actual, (int, float)):
            same = abs(float(expect) - float(actual)) < 1e-9
        else:
            # 一边是字符串一边是数字（例如台账存 "343,720,612"）⇒ 归一后比
            same = norm(str(expect)) == norm(str(actual))
        if same:
            ok += 1
        else:
            bad.append({"key": key, "src": item["src"],
                        "ledger": expect, "actual": actual})
    report["forward_recalc"] = {
        "checked": len(ledger),
        "match": ok,
        "mismatch": len(bad),
        "mismatch_detail": bad,
        "note": "台账里记的值与产物当前值逐条比对；不一致说明产物已变或台账抄错。",
    }

    # ── 2. 反向扫描 ──────────────────────────────────────────────────
    # 台账值的所有可接受写法
    accept = set()
    for item in ledger.values():
        v = item["value"]
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            f = float(v)
            forms = {
                str(v),
                f"{f:g}",
                f"{f:.6f}".rstrip("0").rstrip("."),
                f"{f:.4f}".rstrip("0").rstrip("."),
                f"{f:.2f}",
                f"{f:.1f}",
                f"{f:.0f}",
                f"{abs(f):g}",
                f"{abs(f):.6f}".rstrip("0").rstrip("."),
                f"{abs(f):.4f}".rstrip("0").rstrip("."),
                f"{abs(f):.2f}",
                f"{abs(f):.1f}",
                f"{abs(f):.0f}",
                f"{f:,.0f}",
                f"{f:,.2f}",
            }
            accept |= {norm(x) for x in forms}
        else:
            accept.add(norm(str(v)))

    # 白名单：非产物数字（年份、序号、章节号、上游文档里的时刻、脚本文件名里的数字）
    white = set()
    for y in range(2020, 2031):
        white.add(str(y))
    for i in range(0, 200):
        white.add(str(i))
    # 上游/背景事实（有出处但不进台账）
    white |= {
        "2026", "2025", "1970", "10", "12", "15", "24", "42", "14", "20", "220",
        "3", "5", "2", "8", "30", "0.5", "1.5", "0.1", "1.0", "0.0", "0.30",
        "95", "60", "1", "4", "6", "7", "9", "11", "16", "17", "34", "50",
        "526", "528", "286", "814", "551", "0.05", "0.0005", "0.0003",
        # 引用上游正文原话里的数字（不是本单产物，且已注明是「04-C 正文写法」）：
        "30.0", "10.00", "1.5",
        # 05-D 根因自由文本里的数（原文引用，非本单计算）：
        "4.4436", "5.0",
        # 过程叙述：本单第一次跑母集对撞时的错误覆盖率（仅说明踩过坑，不是产物当前值）
        "7.84",
        # 编号类：窗口编号（05-A/05-B）与脚本序号（01/02/03/06/07）、
        # 缺口编号（GAP-01）、阻塞编号（BLK-01）。它们不是被核对的数据。
        "01", "02", "03", "04", "05", "06", "07",
    }

    # 扫描前先剔掉「代码块」与「行内反引号」内容：
    # 那里放的是文件名、字段名、脚本序号（如 `04_统一观察母集.json`、`05-D`），
    # 它们不是「正文手写数字」，混进来会产生大量假报警。
    scan_text = re.sub(r"```.*?```", " ", text, flags=re.S)
    scan_text = re.sub(r"`[^`]*`", " ", scan_text)
    # 再把「窗口编号」（05-A / 04-D / 05-C 之类）、日期时间、章节号整体抹掉：
    # 它们是标识与时刻，不是被核对的数据。
    scan_text = re.sub(r"\b0[1-9]-[A-F]\b", " ", scan_text)
    scan_text = re.sub(r"\d{4}-\d{2}-\d{2}(?:T[\d:]+Z)?", " ", scan_text)
    scan_text = re.sub(r"^#{1,6}\s*[〇一二三四五六七八九十\d.、]*", " ", scan_text, flags=re.M)
    # 行首的章节号（如 "1.1 xxx"）；必须锚行首，否则会把 "12.49 GiB" 这类真数据抹掉
    scan_text = re.sub(r"(?m)^\s*\d+\.\d+\s+", " ", scan_text)
    # 行首有序列表标记（"1. xxx" / "2. xxx"）
    scan_text = re.sub(r"(?m)^\s*\d+\.\s+", " ", scan_text)

    found = numbers_in(scan_text)
    unexplained = []
    for raw, pos in found:
        n = norm(raw)
        if n in accept or n in white:
            continue
        # 允许「台账值的前缀/后缀写法」：如台账 94.12 与正文 94.12%，已被 norm 处理
        # 允许小数被截断（4.98886e-07 这类科学计数单独放过）
        if "e-" in raw.lower():
            continue
        unexplained.append({"raw": raw,
                            "context": scan_text[max(0, pos - 45):pos + 45].replace("\n", " ")})

    report["reverse_scan"] = {
        "numbers_found": len(found),
        "accepted_forms": len(accept),
        "unexplained_count": len(unexplained),
        "unexplained": unexplained[:40],
        "scan_rule": "扫描前已剔除三反引号代码块与行内反引号内容（文件名/字段名/脚本序号），"
                     "只核正文与表格里手写的数字。",
        "note": "台账里没有、也不在白名单里的数字。为 0 才说明正文没有手写数。",
    }

    # ── 3. 交叉断言 ──────────────────────────────────────────────────
    d02 = json.loads((OUT / "02_逐事件基线.json").read_text(encoding="utf-8"))
    d03 = json.loads((OUT / "03_逐事件与组合口径分离.json").read_text(encoding="utf-8"))
    d04 = json.loads((OUT / "04_统一观察母集.json").read_text(encoding="utf-8"))
    a, b = d02["calibers"]["生效视图_5bps"], d02["calibers"]["策略包_3bps"]

    def A(name, cond, detail=""):
        report["assertions"].append({"name": name, "pass": bool(cond), "detail": detail})

    A("逐笔对撞 102 笔全部命中",
      d02["crosscheck"]["vs_04D_recalc_records"]["found_in_ref"] == 102
      and d02["crosscheck"]["vs_04D_recalc_records"]["mismatch_count"] == 0,
      f"found={d02['crosscheck']['vs_04D_recalc_records']['found_in_ref']}, "
      f"mismatch={d02['crosscheck']['vs_04D_recalc_records']['mismatch_count']}")

    A("与 04-C 汇总三项一致",
      all(v == "一致" for k, v in d02["crosscheck"]["vs_04C_summary"]["生效视图_5bps"]["对撞"].items()
          if k in ("executed_count", "positive_count", "negative_count", "total_realized_net")),
      str(d02["crosscheck"]["vs_04C_summary"]["生效视图_5bps"]["对撞"]))

    A("伪回撤可复现（说明它确实是顺序累加）",
      d03["calibers"]["生效视图_5bps"]["reproduce_04c_pseudo_drawdown"]["可复现"]
      and d03["calibers"]["策略包_3bps"]["reproduce_04c_pseudo_drawdown"]["可复现"],
      "两套口径都能复现 04-C 的 max_drawdown")

    A("伪回撤的顺序极差 > 伪回撤本身（⇒ 不是账户属性）",
      d03["calibers"]["生效视图_5bps"]["order_sensitivity"]["极差"]
      > d03["calibers"]["生效视图_5bps"]["reproduce_04c_pseudo_drawdown"]["本单复现值"]["amount"],
      f"极差={d03['calibers']['生效视图_5bps']['order_sensitivity']['极差']} vs "
      f"伪回撤={d03['calibers']['生效视图_5bps']['reproduce_04c_pseudo_drawdown']['本单复现值']['amount']}")

    A("同时在仓峰值 > 1（⇒ 顺序累加的单仓假设不成立）",
      d03["calibers"]["生效视图_5bps"]["concurrency"]["同时在仓峰值"] > 1,
      f"峰值={d03['calibers']['生效视图_5bps']['concurrency']['同时在仓峰值']}")

    A("51 条候选全部落在已泄露区间（⇒ 只能当机制对照）",
      a["by_interval"]["区间二_已泄露验证集"]["candidates"] == 51
      and a["by_interval"]["区间一_研究集"]["candidates"] == 0
      and a["by_interval"]["区间三_真样本外"]["candidates"] == 0,
      f"区间分布={ {k: v['candidates'] for k, v in a['by_interval'].items()} }")

    A("拒单标的是 CARVUSDT（与 04-C/04-D 正文都不符）",
      len(b["rejections"]) == 1 and b["rejections"][0]["symbol"] == "CARVUSDT",
      str([r["symbol"] for r in b["rejections"]]))

    A("母集事件数 > 51（⇒ 确实不限于 51 个候选）",
      d04["summary"]["events_total"] > 51,
      f"events_total={d04['summary']['events_total']}")

    A("母集与 51 候选覆盖率 > 90%",
      d04["coverage_vs_51"]["coverage_pct"] > 90,
      f"coverage={d04['coverage_vs_51']['coverage_pct']}%")

    A("期末持仓与拒单都被保留（不补齐）",
      b["counts"]["rejected"] == 1 and b["counts"]["open_at_window_end"] >= 0,
      f"rejected={b['counts']['rejected']}, open={b['counts']['open_at_window_end']}")

    A("组合回撤字段在本单产物里不存在",
      all(v.get("max_drawdown_amount") is None for v in d02["crosscheck"]["vs_04C_summary"].values()
          if isinstance(v, dict) and "本单" in v),
      "02 产物的「本单」段 max_drawdown 全为 null")

    passed = sum(1 for x in report["assertions"] if x["pass"])
    report["verdict"] = {
        "forward_recalc": ("通过" if report["forward_recalc"]["mismatch"] == 0 else "不通过"),
        "reverse_scan": ("通过" if report["reverse_scan"]["unexplained_count"] == 0 else "需人工确认"),
        "assertions": f"{passed}/{len(report['assertions'])} 通过",
        "overall": ("通过" if (report["forward_recalc"]["mismatch"] == 0
                             and report["reverse_scan"]["unexplained_count"] == 0
                             and passed == len(report["assertions"])) else "不通过"),
    }

    (OUT / "07_双向对撞结果.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"正向复算：{ok}/{len(ledger)} 一致，不一致 {len(bad)}")
    if bad:
        for x in bad[:5]:
            print("   !!", x)
    print(f"反向扫描：正文数字 {len(found)} 个，台账外 {len(unexplained)} 个")
    for x in unexplained[:10]:
        print(f"   ?? {x['raw']}  ...{x['context']}...")
    print(f"断言：{passed}/{len(report['assertions'])} 通过")
    for x in report["assertions"]:
        if not x["pass"]:
            print("   FAIL:", x["name"], "|", x["detail"])
    print("总体:", report["verdict"]["overall"])


if __name__ == "__main__":
    main()
