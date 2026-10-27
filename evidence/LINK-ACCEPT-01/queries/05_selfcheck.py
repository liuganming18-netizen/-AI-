#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""05_selfcheck.py —— 报告数字回到 out/*.json 机械复算

规矩：报告里出现的每个关键数字，都必须能从 out/ 的原始字段算出来。
算不出来或对不上，就说明报告抄错了。

输出：out/06_报告对账.json、05_自检.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BASE = ROOT / "evidence" / "LINK-ACCEPT-01"
OUT = BASE / "out"

rows: list[dict] = []


def ck(no: str, what: str, expect, got, ok: bool, src: str) -> None:
    rows.append({"编号": no, "核对项": what, "报告写的": expect, "复算得到": got,
                 "一致": "是" if ok else "否", "来源": src})


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> None:
    t0 = load("T0_冻结快照.json")
    drift = load("T1_T0_变动.json")
    obj = load("00_对象核对.json")
    base = load("01_基线完整性.json")
    iso = load("05_隔离进程环境.json")
    nat = load("03_自然运行.json")
    led = load("04_账本来源.json")

    # 1. 快照
    ck("S-T0", "T0 时刻", "19:23:49", t0["生成时刻"][11:19],
       t0["生成时刻"][11:19] == "19:23:49", "out/T0_冻结快照.json → 生成时刻")
    ck("S-文件数", "被审文件数", 159, t0["被审文件数"], t0["被审文件数"] == 159,
       "out/T0_冻结快照.json → 被审文件数")

    # 2. 交付状态
    ck("S-A", "A 目录存在", False, obj["对象"]["A"]["存在"], obj["对象"]["A"]["存在"] is False,
       "out/00_对象核对.json → 对象.A.存在")
    ck("S-B", "B 文件数", 2, obj["对象"]["B"]["文件数"], obj["对象"]["B"]["文件数"] == 2,
       "out/00_对象核对.json → 对象.B.文件数")

    # 3. 基线
    ck("S-基线", "基线哈希一致数", "3/4", f"{base.get('哈希一致数')}/{base.get('哈希条目数')}",
       base.get("哈希一致数") == 3 and base.get("哈希条目数") == 4,
       "out/01_基线完整性.json → 哈希一致数/哈希条目数")
    ur = next((r for r in base["哈希核对"] if r["文件"].endswith("unified_runtime.py")), {})
    ck("S-漂移哈希", "unified_runtime 声明 vs 实际",
       "6b7e08d6… vs f0d49e1a…",
       f"{ur.get('声明', '')[:10]}… vs {ur.get('实际', '')[:10]}…",
       ur.get("一致") is False, "out/01_基线完整性.json → 哈希核对[unified_runtime]")

    # 4. 进程
    procs = {p.get("pid"): p for p in base.get("进程", [])}
    p42250 = procs.get(42250, {})
    p50390 = procs.get(50390, {})
    ck("S-8787", "8787 进程号/启动", "42250 / 17:00:14",
       f"{p42250.get('pid')} / {str(p42250.get('启动时刻'))[11:19]}",
       p42250.get("pid") == 42250 and str(p42250.get("启动时刻"))[11:19] == "17:00:14",
       "out/01_基线完整性.json → 进程")
    ck("S-worker", "守护进程号/启动", "50390 / 18:24:03",
       f"{p50390.get('pid')} / {str(p50390.get('启动时刻'))[11:19]}",
       p50390.get("pid") == 50390 and str(p50390.get("启动时刻"))[11:19] == "18:24:03",
       "out/01_基线完整性.json → 进程")
    ck("S-进程判定", "守护是否加载最新 unified_runtime", False,
       base.get("worker是否加载最新unified_runtime"),
       base.get("worker是否加载最新unified_runtime") is False,
       "out/01_基线完整性.json → worker是否加载最新unified_runtime")

    # 5. 隔离
    ck("S-写隔离", "8799 写路径全部隔离", True, iso.get("写路径全部隔离"),
       iso.get("写路径全部隔离") is True, "out/05_隔离进程环境.json → 写路径全部隔离")
    ck("S-隔离账本", "隔离目录账本文件数", 4, len(iso.get("隔离目录", {}).get("账本文件") or []),
       len(iso.get("隔离目录", {}).get("账本文件") or []) == 4,
       "out/05_隔离进程环境.json → 隔离目录.账本文件")
    ck("S-隔离闸门", "隔离闸门文件是否存在", False,
       iso.get("隔离目录", {}).get("闸门文件存在"),
       iso.get("隔离目录", {}).get("闸门文件存在") is False,
       "out/05_隔离进程环境.json → 隔离目录.闸门文件存在")
    ck("S-读共享", "MARKET_DB 是否指向生产", True,
       iso.get("读共享", {}).get("MARKET_DB_指向生产"),
       iso.get("读共享", {}).get("MARKET_DB_指向生产") is True,
       "out/05_隔离进程环境.json → 读共享")

    # 6. 自然运行
    se = nat.get("信号引擎", {})
    op = nat.get("订单处理", {})
    ed = nat.get("执行守护", {})
    ck("S-信号", "signals_generated_total", 0, se.get("signals_generated_total"),
       se.get("signals_generated_total") == 0, "out/03_自然运行.json → 信号引擎")
    ck("S-路由", "orders_routed_total", 0, op.get("orders_routed_total"),
       op.get("orders_routed_total") == 0, "out/03_自然运行.json → 订单处理")
    ck("S-持仓", "open_positions", 0, ed.get("open_positions"),
       ed.get("open_positions") == 0, "out/03_自然运行.json → 执行守护")
    ck("S-轮次", "cycle_count", 1192, nat.get("运行身份", {}).get("cycle_count"),
       nat.get("运行身份", {}).get("cycle_count") == 1192, "out/03_自然运行.json → 运行身份")
    ck("S-臂数", "账本臂数", 4, led.get("臂数"), led.get("臂数") == 4,
       "out/04_账本来源.json → 臂数")

    # 7. 账本来源
    ck("S-成交", "退出成交条数", 3, led.get("退出成交条数"), led.get("退出成交条数") == 3,
       "out/04_账本来源.json → 退出成交条数")
    ck("S-合成", "合成来源成交数", 3, led.get("合成来源成交数"),
       led.get("合成来源成交数") == 3, "out/04_账本来源.json → 合成来源成交数")
    ck("S-自然成交", "自然来源成交数", 0, led.get("自然来源成交数"),
       led.get("自然来源成交数") == 0, "out/04_账本来源.json → 自然来源成交数")

    # 8. T0→T1
    ck("S-变动", "T0→T1 变动文件数", 3, drift.get("变动文件数"),
       drift.get("变动文件数") == 3, "out/T1_T0_变动.json → 变动文件数")
    names = [c["文件"] for c in drift.get("变动明细", [])]
    ck("S-账本模块", "变动明细是否含 live_store.py", True,
       any(n.endswith("live_store.py") for n in names),
       any(n.endswith("live_store.py") for n in names), "out/T1_T0_变动.json → 变动明细")

    n_ok = sum(1 for r in rows if r["一致"] == "是")
    summary = {"核对项数": len(rows), "一致": n_ok, "不一致": len(rows) - n_ok,
               "明细": rows}
    (OUT / "06_报告对账.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 05 自检（报告数字对账）", "",
             f"对账 {len(rows)} 项：**一致 {n_ok}，不一致 {len(rows) - n_ok}**", "",
             "| 编号 | 核对项 | 报告写的 | 复算得到 | 一致 | 来源 |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['编号']} | {r['核对项']} | {r['报告写的']} | {r['复算得到']} | {r['一致']} | {r['来源']} |")
    (BASE / "05_自检.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for r in rows:
        print(f"  [{'OK ' if r['一致'] == '是' else 'BAD'}] {r['编号']} {r['核对项']}：报告 {r['报告写的']} / 复算 {r['复算得到']}")
    print(f"\n对账 {len(rows)} 项：一致 {n_ok}，不一致 {len(rows) - n_ok}")
    print(f"已写出 out/06_报告对账.json 与 05_自检.md")


if __name__ == "__main__":
    main()
