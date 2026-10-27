# -*- coding: utf-8 -*-
"""13 报告数字对账 + 自检 —— 报告里引用的每个关键数字，都必须能从这里机械复算出来。

为什么必须有这一步：本项目人工抄写数字已经出过错（两套结果混用、合计值差 0.5 USDT、
把笔数写成组数）。所以报告定稿前，所有关键数字一律回 `out/*.json` 的原始字段重算。

产物：out/13_报告对账.json / 06_报告自检.md
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03"
OUT = EVID / "out"
ROWS: list[dict] = []


def load(n):
    p = OUT / n
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def ck(no, what, expect, actual, ok, src):
    ROWS.append({"编号": no, "核对项": what, "报告写的": expect, "复算得到": actual,
                 "一致": "是" if ok else "否", "来源": src})
    print(("  [OK ] " if ok else "  [MISMATCH] ") + f"{no} {what}：报告 {expect} / 复算 {actual}")


def main():
    # ── 1. 各探针汇总数 ──────────────────────────────────────
    for f, exp in [("02_因果链.json", "43/34/9"), ("03_跟踪止损与退出.json", "34/30/4"),
                   ("04_回测与模拟对账.json", "13/8/5"), ("05_隔离与恢复.json", "22/22/0"),
                   ("07_UI核心操作.json", "24/22/2"), ("09_账本身份.json", "17/13/4"),
                   ("10_59条逐项.json", "62/44/8/10")]:
        d = load(f)
        s = d.get("汇总") or {}
        if "未验证" in s and f == "10_59条逐项.json":
            got = "%s/%s/%s/%s" % (s.get("总数"), s.get("通过"), s.get("不通过"), s.get("未验证"))
        else:
            got = "%s/%s/%s" % (s.get("总数"), s.get("通过"), s.get("不通过"))
        ck("S-" + f.split("_")[0], f"{f} 汇总", exp, got, got == exp, f"out/{f} → 汇总")

    # ── 2. 隔离环境：退出闭合的两个数 ────────────────────────
    d03 = load("03_跟踪止损与退出.json")
    a = {x["编号"]: x for x in d03.get("断言", [])}
    d7 = str(a.get("D7", {}).get("实际", ""))
    d10 = str(a.get("D10", {}).get("实际", ""))
    ck("S-净盈亏", "场景3 退出净盈亏", "-16.727018932", d7, "-16.727018932" in d7,
       "out/03 → 断言 D7.实际")
    ck("S-现金", "场景3 退出后现金", "4983.272981068", d10, "4983.272981" in d10,
       "out/03 → 断言 D10.实际")

    # ── 3. 生产账本：3 笔持仓 / 3 条挂单 / 现金 / 已实现 ──────
    d09 = load("09_账本身份.json")
    per = (d09.get("明细") or {}).get("逐臂") or {}
    n_pos = sum(v.get("n_pos", 0) for v in per.values())
    n_pend = sum(v.get("n_pending", 0) for v in per.values())
    cash = sum(v.get("cash", 0) for v in per.values())
    real = sum(v.get("realized", 0) for v in per.values())
    ck("S-生产持仓", "生产账本持仓笔数", "3", n_pos, n_pos == 3, "out/09 → 明细.逐臂.*.n_pos")
    ck("S-生产挂单", "生产账本挂单条数", "3", n_pend, n_pend == 3, "out/09 → 明细.逐臂.*.n_pending")
    ck("S-生产现金", "生产账本 Σ现金", "19995.654545", round(cash, 6),
       abs(cash - 19995.654545) < 1e-5, "out/09 → 明细.逐臂.*.cash")
    ck("S-生产已实现", "生产账本 Σ已实现", "-4.345455", round(real, 6),
       abs(real + 4.345455) < 1e-5, "out/09 → 明细.逐臂.*.realized")

    # ── 4. 07：UI 平仓的三个数 ───────────────────────────────
    d07 = load("07_UI核心操作.json")
    dd = (d07.get("明细") or {}).get("D_账本收尾") or {}
    pos = dd.get("position") or {}
    stt = dd.get("state") or {}
    ck("S-UI平仓价", "UI 平仓 exit_price", "100.1499", pos.get("exit_price"),
       str(pos.get("exit_price", "")).startswith("100.1499"), "out/07 → 明细.D_账本收尾.position")
    ck("S-UI现金", "UI 平仓后现金", "4999.916325", round(float(stt.get("cash") or 0), 6),
       abs(float(stt.get("cash") or 0) - 4999.9163252333) < 1e-6,
       "out/07 → 明细.D_账本收尾.state")
    ck("S-UI已实现", "UI 平仓后已实现", "-0.083675", round(float(stt.get("realized_pnl") or 0), 6),
       abs(float(stt.get("realized_pnl") or 0) + 0.08367476670001028) < 1e-9,
       "out/07 → 明细.D_账本收尾.state")
    ck("S-UI订单", "UI 平仓后 订单/成交/已实现行数", "2/2/1",
       f"{dd.get('n_order')}/{dd.get('n_fill')}/{dd.get('n_realized')}",
       (dd.get("n_order"), dd.get("n_fill"), dd.get("n_realized")) == (2, 2, 1),
       "out/07 → 明细.D_账本收尾")

    # ── 5. 版本：148 文件零变动 + 进程落后 ───────────────────
    d12 = load("12_结束版本核对.json")
    ck("S-文件变动", "结束核对：148 个被审文件变动数", "0", d12.get("变动文件数"),
       d12.get("变动文件数") == 0, "out/12 → 变动文件数")
    ck("S-进程", "8787 启动时刻 vs 最新文件 mtime", "21:49:54 < 21:57:12",
       f"{str(d12.get('8787启动时刻'))[11:19]} vs {str(d12.get('最新文件mtime'))[11:19]}",
       str(d12.get("8787启动时刻", ""))[11:19] == "21:49:54"
       and str(d12.get("最新文件mtime", ""))[11:19] == "21:57:12",
       "out/12 → 8787启动时刻 / 最新文件mtime")
    ck("S-进程判定", "进程是否加载最新代码", "False", d12.get("进程是否加载最新代码"),
       d12.get("进程是否加载最新代码") is False, "out/12 → 进程是否加载最新代码")

    # ── 6. 截图哈希唯一性 ────────────────────────────────────
    shots = sorted((EVID / "shots").glob("*.png"))
    shas = {}
    for p in shots:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        shas.setdefault(h, []).append(p.name)
    dup = {h: v for h, v in shas.items() if len(v) > 1}
    n06 = len([p for p in shots if p.name.startswith("06_")])
    # 06 组 = 16 个生产视图。生产实例当前 0 笔持仓，「止损轨迹」按钮不渲染，
    # 该视图截图在隔离实例上取（shots/07_2_stoptrail.png），故 06 组期望 16 而非 17。
    n07trail = len([p for p in shots if p.name == "07_2_stoptrail.png"])
    ck("S-截图06", "06 组截图张数", "16（生产 16 视图；止损轨迹图在 07 组）", n06,
       n06 == 16 and n07trail == 1,
       "shots/06_*.png 计数 + shots/07_2_stoptrail.png 存在")
    ck("S-截图唯一", "全部截图哈希重复组数", "0", len(dup), not dup,
       "shots/*.png 逐个 sha256")

    # ── 7. 合成信号 3/4 ──────────────────────────────────────
    x = (d09.get("明细") or {}).get("X3c") or {}
    ck("S-合成信号", "生产信号库合成前缀条数", "3/4",
       f"{x.get('合成前缀行数')}/{x.get('总行数')}",
       x.get("合成前缀行数") == 3 and x.get("总行数") == 4, "out/09 → 明细.X3c")

    # ── 8. 04 逐事件 8 根全一致 ──────────────────────────────
    d04 = load("04_回测与模拟对账.json")
    rows = (d04.get("明细") or {}).get("E_逐事件") or []
    n_ok = sum(1 for r in rows if all(r.get(k) for k in
                                     ("候选一致", "生效一致", "上移一致", "退出一致")
                                     if k in r))
    ck("S-逐事件", "04 逐事件一致的根数", "8", f"{n_ok}/{len(rows)}",
       n_ok == 8 and len(rows) == 8, "out/04 → 明细.E_逐事件")

    bad = [r for r in ROWS if r["一致"] != "是"]
    print(f"\n对账 {len(ROWS)} 项：一致 {len(ROWS) - len(bad)}，不一致 {len(bad)}")
    (OUT / "13_报告对账.json").write_text(
        json.dumps({"生成时刻": __import__("datetime").datetime.now().astimezone()
                    .isoformat(timespec="seconds"), "核对": ROWS,
                    "一致": len(ROWS) - len(bad), "不一致": len(bad)},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 06 报告自检", "",
          "## A. 报告数字对账（回 out/*.json 原始字段机械复算）", "",
          f"对账 {len(ROWS)} 项：**一致 {len(ROWS) - len(bad)}，不一致 {len(bad)}**", "",
          "| 编号 | 核对项 | 报告写的 | 复算得到 | 一致 | 来源 |", "|---|---|---|---|---|---|"]
    for r in ROWS:
        md.append(f"| {r['编号']} | {r['核对项']} | {r['报告写的']} | {r['复算得到']} | "
                  f"{r['一致']} | {r['来源']} |")
    md += ["", "## B. 禁止项自检（工单明令）", "",
           "| 禁止项 | 本轮做法 | 证据 |", "|---|---|---|",
           "| 不得直接调止损记录函数代替引擎推进 | 只经 `advance_position_trailing`；"
           "静态证明 `record_stop_update` 唯一调用方就是它 | 09 L4b（调用点行号 1988） |",
           "| 截图 / HTTP 200 / 库里有行 都不能单独证明闭环 | 每条都追到"
           "「订单→成交→持仓→费用→权益」五表数值 | 03 D5~D13、07 D1~D4 |",
           "| 不得写成通过（无法执行的标未验证） | 10 条未验证全部写了具体原因 | out/10_59条逐项.md |",
           "| 不得自行修改业务代码 | 全程未改 `框架/`、`UI界面/` 下任何文件 | 12 结束核对：148 文件零变动 |",
           "| 不得删除合成记录 / 替用户批准清理范围 | 只取证标注，未删 | 09 X3c |",
           "| 不得污染生产 | 写操作只在 `/tmp/wx_audit03|04|05`；生产 4 库+闸门+信号库 SHA 前后一致，PID 未变 | 05 E 段、07 E 段 |",
           "| 不得重跑 144 组 / 不改冻结规则 | 未跑网格；只调用现成策略的 `custom_stoploss` | 04 C1 |", ""]
    (EVID / "06_报告自检.md").write_text("\n".join(md), encoding="utf-8")
    print(f"已写出 {OUT/'13_报告对账.json'} 与 {EVID/'06_报告自检.md'}")


if __name__ == "__main__":
    main()
