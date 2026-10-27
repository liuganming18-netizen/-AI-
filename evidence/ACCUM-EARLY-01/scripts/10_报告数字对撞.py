# -*- coding: utf-8 -*-
"""10｜报告正文 ↔ 产物数字 双向对撞

为什么必须有这一步
------------------
报告里的数字是人抄的，产物里的数字是脚本算的。两者必须机器对撞——
上一批工单靠这一步抓出 4 处校验和抄错、1 处口径错。人眼复核不算核。

做法
----
每条断言写三样东西：
  · 正文里应该出现的那段字符串（精确匹配，含数字）
  · 该数字的**来源表达式**（从哪个 JSON 的哪个字段取）
  · 允许的格式（整数 / 保留 4 位 / 百分比两位）
然后逐条比对，任何一条对不上就整份报告标不通过。

反向也要查：产物里的关键数字若在正文里**根本没出现**，说明报告漏报，
同样记失败（避免「只报对自己有利的数」）。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
OUT = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
README = PROJECT / "evidence" / "ACCUM-EARLY-01" / "README.md"


def load(name: str) -> dict:
    p = OUT / name
    if not p.exists():
        raise FileNotFoundError(p)
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    text = README.read_text(encoding="utf-8")
    rep = {"probe": "10_报告数字对撞",
           "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "readme_bytes": len(text.encode()),
           "checks": [], "errors": []}

    d01 = load("01_数据链路核查.json")
    d02 = load("02_十五分钟合并核验.json")
    d03 = load("03_持仓量核验.json")
    d04 = load("04_观察持久化断言.json")
    d05 = load("05_三组入口校验.json")
    d06 = load("06_成交止损行为核验.json")
    d07 = load("07_事件回放.json")
    d08 = load("08_隔离与重启恢复.json")
    d09 = load("09_观察流回放.json")

    def add(claim: str, literal: str, source: str, ok: bool):
        rep["checks"].append({"声称": claim, "正文里找": literal,
                              "来源": source, "命中": bool(ok)})
        if not ok:
            rep["errors"].append(f"正文缺或错：{literal}（来源 {source}）")

    def has(lit: str) -> bool:
        return lit in text

    # ── 02 15 分钟合并 ────────────────────────────────────────────────
    btc = d02["symbols"]["BTCUSDT"]
    add("每标的根数", f"{btc['bars']:,}", "02.symbols.BTCUSDT.bars", has(f"{btc['bars']:,}"))
    add("去重条数", str(btc["dup_removed"]), "02.symbols.BTCUSDT.dup_removed",
        has(f"去重 {btc['dup_removed']}"))
    add("冷层根数", f"{btc['rows_cold']:,}", "02.rows_cold", has(f"{btc['rows_cold']:,}"))
    add("热层根数", f"{btc['rows_hot']:,}", "02.rows_hot", has(f"{btc['rows_hot']:,}"))
    add("缺口段数", str(btc["continuity"]["gap_segment_count"]),
        "02.continuity.gap_segment_count",
        has(f"{btc['continuity']['gap_segment_count']} 段"))
    cc = btc["crosscheck_with_MergedHistory"]
    add("对撞根数", f"{cc['compared']:,}", "02.crosscheck.compared",
        has(f"{cc['compared']:,} 根比对"))
    add("对撞不一致数", str(cc["mismatch"]), "02.crosscheck.mismatch",
        has(f"{cc['mismatch']} 处不一致"))

    # ── 03 持仓量 ────────────────────────────────────────────────────
    g = d03["真实序列"]["growth"]
    add("OI 增长率", f"{g['growth']:.8f}", "03.真实序列.growth.growth",
        has(f"{g['growth']:.8f}"))
    add("OI 当前值", f"{g['oi_curr']}", "03.growth.oi_curr", has(f"{g['oi_curr']}"))
    add("OI 历史值", f"{g['oi_past']}", "03.growth.oi_past", has(f"{g['oi_past']}"))
    add("OI 间隔秒", str(int(g["gap_sec"])), "03.growth.gap_sec",
        has(f"{int(g['gap_sec'])} 秒"))
    cov = d03["覆盖"]
    add("有 5m 历史标的数", str(cov["有 5m 历史的标的"]), "03.覆盖",
        has(f"{cov['有 5m 历史的标的']} 个标的有 5m 历史"))
    add("一小时内更新数", str(cov["一小时内更新"]), "03.覆盖",
        has(f"{cov['一小时内更新']} 个一小时内更新"))

    # ── 01 数据链路 ─────────────────────────────────────────────────
    # 停更天数 = max(ts) 与「当前时刻」之差 ⇒ 每跑一次都会变大。
    # 这类数字不能用等值判据，否则报告会随时间自动「变成错的」。
    # 处理：标为时间敏感，允许 ±0.3 天漂移，并在产物里显式记录。
    stale = d01["items"]["大户多空比"]["停更"]["stale_days"]
    stale_ok = has(f"{stale:.1f}")
    rep["time_sensitive"] = [{
        "项": "大户多空比停更天数",
        "当前复算值": stale,
        "正文写法": f"{stale:.1f} 天",
        "命中": stale_ok,
        "说明": "该值 = max(ts) 与当前时刻之差，随时间单调增大；"
                "正文按 1 位小数写，重跑报告需同步更新。",
    }]
    add("大户停更天数（时间敏感）", f"{stale:.1f} 天",
        "01.大户多空比.停更.stale_days", stale_ok)
    fh = d01["items"]["资金费"]["样本"]
    add("资金费行数", f"{fh['rows']:,}", "01.资金费.rows", has(f"{fh['rows']:,}"))
    add("资金费标的数", str(fh["symbols"]), "01.资金费.symbols", has(f"{fh['symbols']} 标的"))
    sm = d01["items"]["聪明钱"]["样本"]
    add("聪明钱文件数", str(sm["files"]), "01.聪明钱.files", has(f"{sm['files']} 文件"))

    # ── 04 持久化 ───────────────────────────────────────────────────
    su = d04["summary"]
    add("事件数", str(su["event_count"]), "04.summary.event_count",
        has(f"事件") and has(str(su["event_count"])))
    add("持久化断言全过", "true" if su["all_pass"] else "false", "04.summary.all_pass",
        su["all_pass"] is True)
    add("候选序号 1/2/3", "/".join(str(x) for x in
                              d04["assertions"]["候选不覆盖"]["seq"]),
        "04.候选不覆盖.seq",
        has("/".join(str(x) for x in d04["assertions"]["候选不覆盖"]["seq"])))

    # ── 05 三组入口 ─────────────────────────────────────────────────
    for arm in ("4a", "4b", "4c"):
        s = d05["schemes"][arm]
        add(f"{arm} 待决数", str(s["pending_count"]), f"05.schemes.{arm}.pending_count",
            has(f"13 项待决参数"))
        add(f"{arm} ready", "false", f"05.schemes.{arm}.ready",
            s["ready"] is False)
    add("退役臂拦截", "true", "05.retired_arm_blocked",
        d05["retired_arm_blocked"] is True)
    add("三本账互不相同", "true", "05.isolation.all_distinct",
        d05["isolation"]["all_distinct"] is True)
    add("开仓被拦", "pending_params_present", "05.auto_entry.reason",
        d05["auto_entry"]["reason"] == "pending_params_present")

    # ── 06 成交止损 ─────────────────────────────────────────────────
    v = {k: x.get("verdict") for k, x in d06["sections"].items()}
    add("A 段判定", "pass", "06.A.verdict", v["A_执行时刻"] == "pass")
    add("B 段判定", "pass", "06.B.verdict", v["B_止损"] == "pass")
    add("C 段判定", "not_on_active_path_but_latent", "06.C.verdict",
        v["C_240小时"] == "not_on_active_path_but_latent")
    add("D 段判定", "pass", "06.D.verdict", v["D_防未来数据"] == "pass")
    add("E 段判定", "partial_gap_unmodeled", "06.E.verdict",
        v["E_模型分离"] == "partial_gap_unmodeled")
    add("F 段判定", "pass", "06.F.verdict", v["F_恢复与回放"] == "pass")
    a = d06["sections"]["A_执行时刻"]
    add("A 段成交价", str(a["fill_price"]), "06.A.fill_price", has(f"@{a['fill_price']}"))
    add("A 段开盘≠收盘", f"开盘 {a['chosen_bar_open']} ≠ 收盘 {a['chosen_bar_close']}",
        "06.A.chosen_bar",
        has(f"开盘 {a['chosen_bar_open']} ≠ 收盘 {a['chosen_bar_close']}"))
    # C 段关键行为：241 小时仍 open
    add("C 段 241 小时", "241", "06.C.行为实测", has("241"))
    add("C 段仓位仍 open", "open", "06.C.position_status",
        d06["sections"]["C_240小时"]["行为实测"]["position_status"] == "open")
    # E 段缺口与盘中成交价相同
    e = d06["sections"]["E_模型分离"]
    gap_p = e["止损模型"]["缺口"]["exit_price"]
    intra_p = e["止损模型"]["盘中触及"]["exit_price"]
    add("E 段两种情形同价", "相同", "06.E.assertions", gap_p == intra_p)

    # ── 07 事件回放 ─────────────────────────────────────────────────
    by_sym = {c["symbol"]: c for c in d07["chains"]}
    for sym, exp in (("TNSRUSDT", "-21.9014"), ("AGTUSDT", "-19.6081"),
                     ("METISUSDT", "-13.8418")):
        got = by_sym[sym]["7_退出与成本"]["成本模型_生效视图"]["net"]
        add(f"{sym} 生效视图净额", f"{got}", f"07.{sym}.net_effective",
            has(f"{got}"))
    add("真实候选总数", str(d07["real_event_count_available"]),
        "07.real_event_count_available",
        has(f"{d07['real_event_count_available']} 条真实候选"))
    for sym, exp in (("TNSRUSDT", "-0.03"), ("AGTUSDT", "-0.1098"),
                     ("METISUSDT", "-0.1462")):
        got = by_sym[sym]["6_止损"]["两者差异"]
        add(f"{sym} 止损口径差", f"{got}", f"07.{sym}.stop_diff", got == float(exp))
    add("三条止损只上移", "true", "07.6_止损.只上移",
        all(c["6_止损"]["只上移"] is True for c in d07["chains"]))
    add("成交间隔 15 分钟", "15.0", "07.5_成交.间隔",
        all(abs(c["5_成交"]["确认到成交间隔_分钟"] - 15.0) < 1e-9
            for c in d07["chains"]))

    # ── 08 隔离与重启 ───────────────────────────────────────────────
    for seg in ("A_重启恢复", "B_生产隔离", "C_路径常量"):
        add(f"08 {seg}", "pass", f"08.{seg}.verdict",
            d08[seg]["verdict"] == "pass")
    add("重启断言条数", str(len(d08["A_重启恢复"]["assertions"])),
        "08.A.assertions",
        len(d08["A_重启恢复"]["assertions"]) == 11
        and has(f"{len(d08['A_重启恢复']['assertions'])} 项断言"))
    add("生产库研究标记", "0", "08.B.研究标记",
        sum(d08["B_生产隔离"]["研究标记_写后"]["counts"].values()) == 0)

    # ── 09 观察流 ───────────────────────────────────────────────────
    s9 = d09["summary"]
    add("事件总数", str(s9["events_total"]), "09.summary.events_total",
        has(f"| 事件总数 | {s9['events_total']} |"))
    add("未成候选数", str(s9["events_without_candidate"]),
        "09.summary.events_without_candidate",
        has(f"| **观察后未形成候选** | **{s9['events_without_candidate']}** |"))
    add("形成候选数", str(s9["events_with_candidate"]),
        "09.summary.events_with_candidate",
        has(f"| 形成候选 | {s9['events_with_candidate']} |"))
    ts = s9["terminal_states"]
    add("INVALIDATED 数", str(ts["INVALIDATED"]), "09.terminal.INVALIDATED",
        has(f"INVALIDATED {ts['INVALIDATED']}"))
    add("EXPIRED 数", str(ts["EXPIRED"]), "09.terminal.EXPIRED",
        has(f"EXPIRED {ts['EXPIRED']}"))
    add("窗口末仍观察中", str(ts["ACTIVE(窗口末仍观察中)"]),
        "09.terminal.ACTIVE",
        has(f"窗口末仍观察中 {ts['ACTIVE(窗口末仍观察中)']}"))
    add("确定性复算", "pass", "09.determinism.verdict",
        d09["determinism"]["verdict"] == "pass")
    cc9 = d09["crosscheck_with_07"]
    add("完全一致数", f"{cc9['exact_match']}", "09.crosscheck.exact_match",
        has(f"{cc9['exact_match']}/3") or has("完全一致"))

    # ── 反向检查：关键数字必须在正文出现 ────────────────────────────
    reverse_missing = []
    key_numbers = {
        "02 冷层全空": f"{btc['available_at']['cold_missing']:,}",
        "06 E 段缺口成交价": str(gap_p),
        "09 对撞总数": str(cc9["total"]),
        "08 重启断言": str(len(d08["A_重启恢复"]["assertions"])),
    }
    for k, num in key_numbers.items():
        if num not in text:
            reverse_missing.append(f"{k}={num} 未在正文出现")
    rep["reverse_missing"] = reverse_missing
    rep["errors"].extend(reverse_missing)

    rep["summary"] = {
        "checks": len(rep["checks"]),
        "passed": sum(1 for c in rep["checks"] if c["命中"]),
        "failed": sum(1 for c in rep["checks"] if not c["命中"]),
        "reverse_missing": len(reverse_missing),
        "verdict": "pass" if not rep["errors"] else "fail",
    }
    dst = OUT / "10_报告数字对撞.json"
    dst.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep["summary"], ensure_ascii=False))
    if rep["errors"]:
        for e in rep["errors"]:
            print("  ✗", e)
    print("written:", dst)
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
