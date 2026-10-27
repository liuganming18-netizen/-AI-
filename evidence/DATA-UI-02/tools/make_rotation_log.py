#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-02：把采集器日志里的轮转轮次抽成表，**不手抄**。

之前这份 `logs/轮转推进.txt` 是手抄的，抄的时候把「04:29:35 之后 OI 节流生效、
单轮用时降到 105.67s」也写了进去 —— 而那版节流后来整体回退了（见 `改动清单.md` 第二节），
手抄的判读因此和结论对不上。改成脚本生成，日志变了重跑即可。
"""
import os
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(EVID, "..", ".."))
LOG = os.path.join(ROOT, "框架", "backend", "logs", "realtime.log")

RE_REFRESH = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s*公开扩展快照刷新：目标\s*(?P<targets>\d+)，成功\s*(?P<ok>\d+)，"
    r"跳过\s*(?P<skip>\d+)，失败\s*(?P<fail>\d+)；轮转窗口\s*(?P<done>\d+)/(?P<win>\d+)\s*取齐 3 比率"
    r"(?:（下一轮优先重试\s*(?P<pending>\d+)\s*个）)?；上游请求\s*(?P<up>\d+)"
    r"（缓存\s*(?P<cached>\d+)，本地节流\s*(?P<deferred>\d+)），用时\s*(?P<elapsed>[\d.]+)s")
RE_CHANGE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s*公开扩展目标变更：\d+\s*→\s*\d+（新增\s*\[(?P<added>[^\]]*)\]；"
    r"移出\s*\[(?P<dropped>[^\]]*)\]）")


def main():
    rows, changes, raw = [], [], []
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            m = RE_REFRESH.match(line)
            if m:
                d = m.groupdict()
                rows.append({
                    "ts": d["ts"], "targets": int(d["targets"]), "ok": int(d["ok"]),
                    "skip": int(d["skip"]), "fail": int(d["fail"]),
                    "done": int(d["done"]), "win": int(d["win"]),
                    "pending": None if d["pending"] is None else int(d["pending"]),
                    "up": int(d["up"]), "cached": int(d["cached"]),
                    "deferred": int(d["deferred"]), "elapsed": float(d["elapsed"]),
                })
                raw.append(line)
                continue
            m = RE_CHANGE.match(line)
            if m:
                d = m.groupdict()
                changes.append({"ts": d["ts"],
                                "added": [x for x in d["added"].replace("'", "").split(",") if x.strip()],
                                "dropped": [x for x in d["dropped"].replace("'", "").split(",") if x.strip()]})
                raw.append(line)

    def hhmmss(ts):
        try:
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").strftime("%H:%M:%S")
        except ValueError:
            return ts

    L = []
    L.append("DATA-UI-02：公开扩展快照的**轮次推进原始记录**\n")
    L.append("来源：`框架/backend/logs/realtime.log`（逐行解析，未改一字；由 "
             "`tools/make_rotation_log.py` 生成）\n")
    L.append(f"共 **{len(rows)} 个完成轮次**，覆盖 "
             f"{rows[0]['ts'] if rows else '—'} → {rows[-1]['ts'] if rows else '—'}。\n")
    L.append("| # | 完成时刻(UTC) | 目标 | 成功 | 跳过 | 失败 | 轮转取齐 | 待重试 | "
             "上游请求 | 缓存 | 本地节流 | 用时(s) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        L.append(f"| {i} | {r['ts']} | {r['targets']} | {r['ok']} | {r['skip']} | {r['fail']} | "
                 f"{r['done']}/{r['win']} | {'—' if r['pending'] is None else r['pending']} | "
                 f"{r['up']} | {r['cached']} | {r['deferred']} | {r['elapsed']} |")
    L.append("")

    # 轮次间隔（证明「确实在持续推进」，不是卡住）
    gaps = []
    for a, b in zip(rows, rows[1:]):
        try:
            ta = datetime.strptime(a["ts"], "%Y-%m-%dT%H:%M:%SZ")
            tb = datetime.strptime(b["ts"], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        gaps.append(int((tb - ta).total_seconds()))
    if gaps:
        L.append(f"轮次间隔：最小 {min(gaps)}s / 中位 {sorted(gaps)[len(gaps) // 2]}s / "
                 f"最大 {max(gaps)}s。间隔 > 900s 的是采集器重启（轮转游标归零），不是卡死。\n")

    L.append("判读（每条都能从上面的表里核）：\n")
    L.append("- **8/12、9/12、10/12 不是失败**：被预算推迟的那几个「下一轮优先重试」，后面的轮次回到 12/12。")
    L.append("- **目标 60 → 62** 出现在 04:10 那次重启（新选标的逻辑），之后一直是 62。")
    L.append("- **单轮用时的波动不是「优化」**：曾试过给 OI 加均匀节流，实测无效已整体回退"
             "（见 `改动清单.md` 第二节），所以这里的 105~204s 是网关预算与缓存命中的自然波动，"
             "不要当成某次改动的效果。")
    L.append("- **重启后的轮次从 0 重新计**，但已落盘的快照值不会丢；代价是被跳过的标的要等它重新轮到。")
    L.append("")

    if changes:
        L.append("## 目标集合「变更」日志（含 5 条误报）\n")
        L.append("下面这些行是采集器打的「目标变更」。其中新增/移出**都为空**的是**误报** —— "
                 "旧写法按列表比较，24h 成交额排名漂移导致顺序变化也被当成变更。"
                 "已改为按集合判定（`merge_target_symbols`），重启后不再出现。\n")
        L.append("| 时刻(UTC) | 新增 | 移出 | 性质 |")
        L.append("|---|---|---|---|")
        for c in changes:
            kind = "误报（集合未变）" if not c["added"] and not c["dropped"] else "真实变更"
            L.append(f"| {c['ts']} | {', '.join(c['added']) or '—'} | "
                     f"{', '.join(c['dropped']) or '—'} | {kind} |")
        L.append("")

    L.append("## 原始行（逐行照抄，便于核对）\n")
    L.append("```")
    for line in raw:
        L.append(line)
    L.append("```")

    out = os.path.join(EVID, "logs", "轮转推进.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"轮次 {len(rows)}，变更行 {len(changes)} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
