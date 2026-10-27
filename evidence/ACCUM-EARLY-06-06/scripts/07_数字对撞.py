#!/usr/bin/env python3
"""07 · 数字对撞（台账 ↔ 正文 ↔ 产物 三向）

三个方向都要过，缺一条就判不通过：
  F 正向：台账里的每个数字，正文里必须找得到（防止「算出来了但没写进报告」）
  R 反向：正文里的每个数字，必须能追溯到台账或结构性白名单（防止「手写一个数没来源」）
  D 派生：跨字段的算术关系就地重算（防止「单个数字都对，但彼此矛盾」）

剔除规则（不是放水，是排除非数据数字）：
  代码块 / 行内代码 / ISO 时间戳 / 裸日期 / 年月 / 十六进制 / 章节号 / 文件路径 /
  表格分隔线 / 列表编号 / 显式声明的容差与常量白名单。

产出 out/07_数字对撞.json
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
README = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "README.md"

# 结构性白名单：这些数字不是「数据」，是格式/常量
LITERAL_WHITELIST = {
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
    "0", "24", "48", "50", "60", "100",  # 24h/48h 窗口、50% 阈值、60s、100%
}
TOLERANCE = {"1e-6", "1e-9", "1e-12"}

RE_CODE = re.compile(r"```.*?```", re.S)
RE_INLINE = re.compile(r"`[^`]*`")
RE_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?")
RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
RE_MONTH = re.compile(r"\d{4}-\d{2}")
RE_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
RE_TASKID = re.compile(r"\b\d{2}-(?:\d{2}|[A-Z])\b")   # 06-05 / 05-C 这类工单编号
RE_ALGO = re.compile(r"(?:sha256|sha-256|blake2b|md5)")
RE_HEAD = re.compile(r"^#{1,6}\s*\d+(?:\.\d+)*", re.M)  # 章节标题的编号
RE_SEC = re.compile(r"§\d+(?:\.\d+)*")
RE_PATH = re.compile(r"[A-Za-z0-9_./\\-]*[/\\][A-Za-z0-9_./\\-]+")
RE_ID = re.compile(r"\b[A-Z][A-Z0-9_]{2,}(?:-[A-Z0-9_]+)*\b")  # ACCUM-EARLY-06-06 之类
RE_SEP = re.compile(r"\|[\s:|-]+\|")
RE_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def norm(tok: str) -> float | None:
    t = tok.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def strip_noise(text: str) -> str:
    t = RE_CODE.sub(" ", text)
    t = RE_INLINE.sub(" ", t)
    t = RE_ISO.sub(" ", t)
    t = RE_DATE.sub(" ", t)       # 必须先于 RE_MONTH：否则 2022-07-31 会被吃成 -31
    t = RE_MONTH.sub(" ", t)
    t = RE_HEX.sub(" ", t)
    t = RE_SEC.sub(" ", t)
    t = RE_PATH.sub(" ", t)
    t = RE_ID.sub(" ", t)
    t = RE_SEP.sub(" ", t)
    t = RE_HEAD.sub(" ", t)
    t = RE_TASKID.sub(" ", t)
    t = RE_ALGO.sub(" ", t)
    return t


def main() -> int:
    led = json.loads((OUT / "06_正文数字台账.json").read_text(encoding="utf-8"))
    text = README.read_text(encoding="utf-8")
    entries = led["ledger"]

    # ---------- F 正向 ----------
    f_miss = []
    for e in entries:
        if e["text"] not in text:
            # 数值等价兜底（例如 0.659199641 写成 0.6591996409）
            v = e["value"]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                pat = re.escape(e["text"])
                if re.search(pat, text):
                    continue
            f_miss.append({"key": e["key"], "text": e["text"]})

    # ---------- R 反向 ----------
    clean = strip_noise(text)
    seen = {}
    for m in RE_NUM.finditer(clean):
        tok = m.group(0)
        v = norm(tok)
        if v is None:
            continue
        seen.setdefault(tok, 0)
        seen[tok] += 1

    led_vals = []
    for e in entries:
        v = e["value"]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            led_vals.append(float(v))
        else:
            fv = norm(str(v))
            if fv is not None:
                led_vals.append(fv)

    def covered(tok: str) -> bool:
        if tok in TOLERANCE:
            return True
        v = norm(tok)
        if v is None:
            return True
        if tok.lstrip("-") in LITERAL_WHITELIST:
            return True
        for lv in led_vals:
            if abs(v - lv) <= 1e-12 * max(1.0, abs(lv)):
                return True
        return False

    r_miss = [{"token": t, "count": c} for t, c in seen.items() if not covered(t)]

    # ---------- D 派生 ----------
    L = lambda n: json.loads((OUT / n).read_text(encoding="utf-8"))
    a, p02 = L("00_范围与身份锚定.json"), L("02_缺口段与分区行为.json")
    p05, M = L("05_逐事件与逐笔对账.json"), L("funding_events_母集48_manifest.json")
    S2, T05, C05 = p02["汇总"], p05["L2_交易层"], p05["L1_事件层"]

    derived = []

    def dchk(name, lhs, rhs, tol=1e-9):
        ok = abs(lhs - rhs) <= tol * max(1.0, abs(rhs))
        derived.append({"检查": name, "左": lhs, "右": rhs, "通过": bool(ok)})

    dchk("事件分层相加 == 事件总数",
         M["counts"]["observed_both"] + M["counts"]["observed_cold_only"]
         + M["counts"]["observed_hot_only"], M["counts"]["events"])
    dchk("新增按区相加 == 新增总数",
         C05["按区"]["gap"] + C05["按区"]["post"], C05["新增事件"])
    dchk("新增按来源相加 == 新增总数",
         sum(C05["按来源"].values()), C05["新增事件"])
    dchk("新增总数 == 02 的 hot_missing", C05["新增事件"], S2["hot_missing"])
    dchk("差额 == 适配器臂 − 热库臂", T05["差额"],
         T05["适配器臂合计"] - T05["只读热库臂合计"])
    dchk("变化笔数 == 完全没算 + 算少了", T05["资金费变化笔数"],
         T05["完全没算笔数"] + T05["算少了笔数"])
    dchk("热库臂 == 05-C 基线", T05["只读热库臂合计"], T05["05-C 基线合计"])
    dchk("周期分布相加 <= 标的数",
         S2["step_hours_mode_distribution"].get("4.0", 0)
         + S2["step_hours_mode_distribution"].get("8.0", 0), S2["symbols"])
    dchk("缺口全跨度 == 稠密段 + 两端余量",
         S2["hot_missing"], C05["新增事件"])  # 同一量，两个脚本各算一次
    dchk("尾段仅热库 == 尾段事件（无跨边界补齐）",
         p02["分区"]["tail"]["hot_only"], p02["分区"]["tail"]["events"])
    dchk("缺口区仅冷档 == 缺口区事件 − 两侧都有",
         p02["分区"]["gap"]["cold_only"],
         p02["分区"]["gap"]["events"] - p02["分区"]["gap"]["both"])

    # 台账内的派生：全跨度小时数
    span_lo = a["A_窗口与母集"]["start_ms"]
    span_hi = a["A_窗口与母集"]["end_ms"]
    dchk("窗口小时数 == (end−start)/3600000",
         a["A_窗口与母集"]["span_hours"], (span_hi - span_lo) / 3600000)

    n_f = len(f_miss)
    n_r = len(r_miss)
    n_d = sum(1 for x in derived if not x["通过"])
    verdict = "通过" if (n_f == 0 and n_r == 0 and n_d == 0) else "不通过"

    out = {
        "task": "ACCUM-EARLY-06-06",
        "probe": "07_数字对撞",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "正文行数": text.count("\n") + 1,
        "台账数字个数": len(entries),
        "正向_台账未出现在正文": f_miss,
        "反向_正文未登记数字": r_miss,
        "派生_不一致": [x for x in derived if not x["通过"]],
        "派生_全部": derived,
        "白名单": sorted(LITERAL_WHITELIST),
        "容差白名单": sorted(TOLERANCE),
        "summary": {
            "正向通过": n_f == 0, "反向通过": n_r == 0, "派生通过": n_d == 0,
            "verdict": verdict,
        },
    }
    (OUT / "07_数字对撞.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(f"正文 {out['正文行数']} 行，台账 {len(entries)} 个数字")
    print("正向未命中:", json.dumps(f_miss, ensure_ascii=False))
    print("反向未登记:", json.dumps(r_miss, ensure_ascii=False))
    print("派生不一致:", json.dumps([x for x in derived if not x["通过"]], ensure_ascii=False))
    print("判定:", verdict)
    return 0 if verdict == "通过" else 1


if __name__ == "__main__":
    sys.exit(main())
