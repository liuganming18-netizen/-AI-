#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-UI02-REAUDIT-01｜阶段一 · 真实 run 与费用一致性核对

对应工单第 4 条：「核查真实 run 是否存在、收益与费用是否一致，
以及信号、持仓、止损轨迹的正常／空态／失败状态。」

做法：不看交付报告怎么写，直接把交付自带的 CSV 04（run 逐字段对账表）
与磁盘上 run 目录的真实文件做机械对账：

  1. run 目录是否存在；
  2. manifest.json 声明的每个文件（bytes + sha256）与磁盘是否一致；
  3. CSV 04 每行声明的「服务端文件真实字段」路径是否真的存在
     （例如 `summary.json / trades.net_pnl` —— 若 summary.json 里没有 `trades`
      这一层，则该行路径本身是错的，数值对也不算对账成立）；
  4. 该路径下的真实值 与 CSV 04 声称的「落盘值」是否一致；
  5. CSV 04 声称的产物哈希 `6ff401541802…` 到底属于哪个文件（全目录逐文件算哈希）；
  6. win_rate / sharpe 这类「文件里根本没有」的字段，CSV 却写了具体数值的，单独列出。

只读，不写 run 目录。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
UI02 = PROJ / "evidence/SYSTEM-UI-02"
OUT = pathlib.Path(__file__).resolve().parent.parent / "out"

RUN_ID = "run_WX_EVENT_TREND_V0_1_BRETTUSDT_1792253027"
RUN_DIR = PROJ / "回测日志/event_trend_v0_1/runs_sep50" / RUN_ID
CSV04 = UI02 / "04_回测run详情与summary逐字段对账表.csv"
CLAIMED_ARTIFACT_HASH = "6ff401541802c1e37e39bab4a820a13ac753ff108be8464cf94e25d085e2e2e1"


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(doc, dotted: str):
    """按点号取嵌套字段，返回 (值, 是否命中)。"""
    cur = doc
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, False
    return cur, True


def main() -> int:
    rep: dict = {
        "生成时刻": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "用途": "阶段一｜真实 run 存在性、收益与费用一致性机械核对",
        "run_id": RUN_ID,
        "run目录": str(RUN_DIR),
        "run目录存在": RUN_DIR.is_dir(),
    }

    if not RUN_DIR.is_dir():
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "02_真实run对账.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[FATAL] run 目录不存在:", RUN_DIR)
        return 2

    # ---- 1. manifest 完整性 ----
    man = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    declared = man.get("files", {})
    integ = []
    for name, rec in declared.items():
        p = RUN_DIR / name
        if not p.exists():
            integ.append({"文件": name, "状态": "missing"})
            continue
        b = p.read_bytes()
        h = hashlib.sha256(b).hexdigest()
        ok = (h == rec.get("sha256")) and (len(b) == rec.get("bytes"))
        integ.append({
            "文件": name, "manifest_bytes": rec.get("bytes"), "disk_bytes": len(b),
            "manifest_sha256": rec.get("sha256"), "disk_sha256": h,
            "状态": "OK" if ok else "MISMATCH",
        })
    rep["manifest完整性"] = {
        "声明文件数": len(declared),
        "一致": sum(1 for r in integ if r["状态"] == "OK"),
        "不一致": [r for r in integ if r["状态"] != "OK"],
        "逐文件": integ,
    }

    # ---- 2. 交付声称的产物哈希归属 ----
    actual = {}
    for p in sorted(RUN_DIR.iterdir()):
        if p.is_file():
            actual[p.name] = sha256_file(p)
    rep["声称产物哈希归属"] = {
        "声称值": CLAIMED_ARTIFACT_HASH,
        "命中文件": [n for n, h in actual.items() if h == CLAIMED_ARTIFACT_HASH] or None,
        "命中说明": "若为 null，说明该哈希不属于本 run 目录任何文件",
        "逐文件实际sha256": actual,
    }
    # 文本内是否出现过该哈希
    text_hits = []
    for p in list(RUN_DIR.glob("*.json")) + list(RUN_DIR.glob("*.jsonl")):
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if CLAIMED_ARTIFACT_HASH[:16] in t:
            text_hits.append(p.name)
    rep["声称产物哈希归属"]["正文出现文件"] = text_hits or None

    # ---- 3. CSV 04 逐行机械对账 ----
    summary = json.loads((RUN_DIR / "summary.json").read_text(encoding="utf-8"))
    runjson = json.loads((RUN_DIR / "run.json").read_text(encoding="utf-8"))
    rep["summary顶层键"] = list(summary.keys())
    rep["summary是否存在trades层"] = "trades" in summary

    rows = list(csv.reader(CSV04.open(encoding="utf-8-sig")))
    header, body = rows[0], rows[1:]

    DOCS = {"summary.json": summary, "run.json": runjson, "manifest.json": man}

    def split_path(backend_path: str):
        """把 'summary.json / trades.net_pnl' 拆成 (doc_key, dotted)。
        'manifest.json files.summary.sha256' 这种没有斜杠的写法也兼容。"""
        s = backend_path.strip()
        for key in DOCS:
            if s.startswith(key):
                rest = s[len(key):].strip()
                rest = rest.lstrip("/").strip()
                rest = rest.replace("files.", "files.", 1)
                # 'manifest.json files.summary.sha256' → 'files.summary.sha256'
                if rest.startswith("files."):
                    parts = rest.split(".")
                    if len(parts) >= 3 and parts[0] == "files":
                        rest = f"files.{parts[1]}.{parts[2]}"
                return key, rest
        return None, s

    recon = []
    for r in body:
        if len(r) < 5:
            continue
        field_label, front_field, backend_path, dom_value, disk_claim = r[0], r[1], r[2], r[3], r[4]
        doc_key, dotted = split_path(backend_path)
        if doc_key is None:
            # 没有文件名前缀的（如 'summary.json (样本不足30笔)' 已在上面处理；其余视为不可解析）
            recon.append({
                "对账字段": field_label, "声明路径": backend_path,
                "解析文件": None, "解析字段": dotted,
                "路径存在": False,
                "声明路径真实值": None,
                "CSV声称落盘值": disk_claim, "CSV声称前端值": dom_value,
                "trading_summary同名字段存在": False, "trading_summary值": None,
                "判定": "声明路径无法解析",
            })
            continue
        val, hit = resolve(DOCS[doc_key], dotted)
        tail = dotted.split(".")[-1]
        alt_val, alt_hit = resolve(summary, "trading_summary." + tail)
        recon.append({
            "对账字段": field_label,
            "声明路径": backend_path,
            "解析文件": doc_key,
            "解析字段": dotted,
            "路径存在": hit,
            "声明路径真实值": val if hit else None,
            "CSV声称落盘值": disk_claim,
            "CSV声称前端值": dom_value,
            "trading_summary同名字段存在": alt_hit,
            "trading_summary值": alt_val if alt_hit else None,
            "判定": ("路径不存在" if not hit else "待人工比对"),
        })

    # 数值比对（仅对能解析成数字/布尔的做）
    def norm(x):
        if isinstance(x, bool):
            return str(x)
        if isinstance(x, (int, float)):
            return repr(float(x))
        return str(x)

    for r in recon:
        if not r["路径存在"]:
            continue
        claimed = (r["CSV声称落盘值"] or "").strip()
        real = r["声明路径真实值"]
        # 允许四舍五入：把 claim 当浮点解析后与 real 比
        try:
            c = float(claimed.replace("$", "").replace(",", ""))
            ok = abs(c - float(real)) <= max(0.01, abs(float(real)) * 1e-6)
        except Exception:
            ok = norm(real).lower() == claimed.lower()
        r["判定"] = "一致" if ok else "不一致"

    rep["CSV04逐行对账"] = recon
    rep["CSV04统计"] = {
        "行数": len(recon),
        "声明路径不存在的行": sum(1 for r in recon if not r["路径存在"]),
        "一致": sum(1 for r in recon if r["判定"] == "一致"),
        "不一致": sum(1 for r in recon if r["判定"] == "不一致"),
    }

    # ---- 4. 文件里根本没有的字段 ----
    rep["文件内不存在的字段"] = {
        "win_rate": {"summary有": "win_rate" in summary,
                     "trading_summary有": "win_rate" in (summary.get("trading_summary") or {}),
                     "run.json.metrics.win_rate_pct": (runjson.get("metrics") or {}).get("win_rate_pct")},
        "sharpe": {"summary有": "sharpe" in summary,
                   "trading_summary有": "sharpe" in (summary.get("trading_summary") or {})},
        "max_drawdown": {"run.json.metrics.max_drawdown_pct": (runjson.get("metrics") or {}).get("max_drawdown_pct")},
    }
    rep["run.json.metrics"] = runjson.get("metrics")
    rep["summary.trading_summary"] = summary.get("trading_summary")
    rep["formal_pass_allowed"] = {
        "summary顶层": summary.get("formal_pass_allowed"),
        "trading_summary": (summary.get("trading_summary") or {}).get("formal_pass_allowed"),
        "run.json": runjson.get("formal_pass_allowed"),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "02_真实run对账.json"
    dst.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台 ----
    print("=" * 88)
    print(f"run 目录: {RUN_DIR.name}")
    print("=" * 88)
    mi = rep["manifest完整性"]
    print(f"manifest 声明 {mi['声明文件数']} 文件 ／ 一致 {mi['一致']} ／ 不一致 {len(mi['不一致'])}")
    for r in mi["不一致"]:
        print("   !!", r)

    print()
    print("声称产物哈希 6ff401541802… 归属：", rep["声称产物哈希归属"]["命中文件"])
    print("  summary.json 实际 sha256 =", actual.get("summary.json"))
    print("  manifest 内 summary.json 声明 =", declared.get("summary.json", {}).get("sha256"))
    print("  正文出现该哈希的文件 =", rep["声称产物哈希归属"]["正文出现文件"])

    print()
    print("summary 顶层键:", rep["summary顶层键"])
    print("summary 是否存在 trades 层:", rep["summary是否存在trades层"])

    print()
    print("=" * 88)
    print("CSV 04 逐行机械对账")
    print("=" * 88)
    print(f"{'字段':10s} {'声明路径':44s} {'路径存在':8s} {'判定':8s}")
    for r in recon:
        print(f"{r['对账字段'][:9]:10s} {r['声明路径'][:43]:44s} "
              f"{str(r['路径存在']):8s} {r['判定']:8s}")
    s = rep["CSV04统计"]
    print(f"\n共 {s['行数']} 行：路径不存在 {s['声明路径不存在的行']} ／ 一致 {s['一致']} ／ 不一致 {s['不一致']}")

    print()
    print("文件内不存在的字段：")
    print(json.dumps(rep["文件内不存在的字段"], ensure_ascii=False, indent=1))

    print(f"\n已落盘: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
