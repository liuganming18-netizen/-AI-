#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 4h 跟踪止损「入场前价格」复核（全量，只读）

上一单（07 脚本）的结论是「60/60 合规」，但它的分桶条件是
`if not st or not isinstance(entry, int)` ⇒ 把「entry_time 是字符串」的成交
也归进「无止损轨迹」，于是 14,957 笔有 stop_trace 的成交里只有 5,081 笔被真正判过。

本脚本把 entry 时间统一解析（int 毫秒 / 毫秒字符串 / ISO 字符串 / entry_time_ms），
对**全部**有 stop_trace 的成交重判，并单独列出「解析不了时间」的成交——
这类只能记「未验证」，不能算通过。

判据：first_trail_ts < 入场后第一根已收盘 4h 的收盘时刻 ⇒ 用了入场前价格。
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/06_止损路径复核.json")
H4 = 4 * 3600 * 1000
RUN_ID_RE = re.compile(
    r"^(bt_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}|run_WX_EVENT_TREND_V0_1_.*|run_FT_ENTRY_09_.*"
    r"|run_FT_STOP20_.*|run_FT_EXIT01_.*|run_FT_STOPGRID01_.*|run_FT_STOPGRID02_.*"
    r"|run_FT_LOW4H_.*)$")


def to_ms(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if v > 10**11 else None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.isdigit() and len(s) >= 12:
            return int(s)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:                                      # noqa: BLE001
            return None
    return None


def first_4h_close_after(ts: int) -> int:
    return (ts // H4) * H4 + H4 - 1


def main() -> None:
    stat = Counter()
    by_engine = defaultdict(Counter)
    bad = []
    unparsed = []
    files = 0
    for dp, dn, fn in os.walk(os.path.join(ROOT, "回测日志")):
        keep = []
        for d in dn:
            if RUN_ID_RE.match(d):
                p = os.path.join(dp, d, "trades.jsonl")
                if not os.path.exists(p):
                    continue
                files += 1
                eng = "未知"
                try:
                    rj = json.load(open(os.path.join(dp, d, "run.json"), encoding="utf-8"))
                    eng = (rj.get("engine") or {}).get("exec_core_version") or "项目引擎(无engine字段)"
                except Exception:                              # noqa: BLE001
                    eng = "项目引擎(无run.json)"
                if eng.startswith("freqtrade"):
                    fam = "Freqtrade"
                elif eng.startswith("oi_res"):
                    fam = "OI共振(退役)"
                else:
                    fam = "项目引擎"
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            t = json.loads(line)
                        except Exception:                      # noqa: BLE001
                            stat["成交行解析失败"] += 1
                            continue
                        stat["成交总数"] += 1
                        by_engine[fam]["成交总数"] += 1
                        st = t.get("stop_trace") or []
                        if not st:
                            stat["无 stop_trace 字段或为空"] += 1
                            by_engine[fam]["无 stop_trace"] += 1
                            continue
                        entry = to_ms(t.get("entry_time"))
                        if entry is None:
                            entry = to_ms(t.get("entry_time_ms"))
                        if entry is None:
                            stat["有轨迹但入场时间解析不了（未验证）"] += 1
                            by_engine[fam]["未验证"] += 1
                            if len(unparsed) < 10:
                                unparsed.append({"file": os.path.relpath(p, ROOT),
                                                 "entry_time": t.get("entry_time"),
                                                 "stop_trace_n": len(st)})
                            continue
                        trails = [x for x in st if str(x.get("reason")) == "trail"
                                  and to_ms(x.get("ts")) is not None]
                        if not trails:
                            stat["有轨迹但从未上移"] += 1
                            by_engine[fam]["从未上移"] += 1
                            continue
                        f4 = first_4h_close_after(entry)
                        ft = min(to_ms(x["ts"]) for x in trails)
                        if ft < f4:
                            stat["首条跟踪线早于入场后首个4h收盘（用了入场前价格）"] += 1
                            by_engine[fam]["违规"] += 1
                            if len(bad) < 20:
                                bad.append({"file": os.path.relpath(p, ROOT),
                                            "engine": fam, "entry_ms": entry,
                                            "first_trail_ms": ft, "first_4h_close_ms": f4,
                                            "gap_hours": round((f4 - ft) / 3600000, 3)})
                        else:
                            stat["合规"] += 1
                            by_engine[fam]["合规"] += 1
            else:
                keep.append(d)
        dn[:] = keep

    judged = stat["合规"] + stat["首条跟踪线早于入场后首个4h收盘（用了入场前价格）"]
    out = {
        "扫描文件数": files,
        "统计": dict(stat),
        "按引擎": {k: dict(v) for k, v in by_engine.items()},
        "违规样例": bad,
        "未验证样例": unparsed,
        "可判定覆盖率": f"{judged}/{stat['成交总数']} = {judged / max(1, stat['成交总数']):.1%}",
        "判据": "first_trail_ts < 入场后第一根已收盘4h的收盘时刻 ⇒ 用了入场前价格；"
                "入场时间解析不了的只记『未验证』，不计入通过。",
        "与上一单的差异": "上一单把 entry_time 为字符串的成交并入『无止损轨迹』，"
                    "因此 14,957 笔有轨迹的成交只判了 5,081 笔；本脚本全部重判。",
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out["统计"], ensure_ascii=False, indent=1))
    print("按引擎", json.dumps(out["按引擎"], ensure_ascii=False))
    print("覆盖率", out["可判定覆盖率"])
    print("→", OUT)


if __name__ == "__main__":
    main()
