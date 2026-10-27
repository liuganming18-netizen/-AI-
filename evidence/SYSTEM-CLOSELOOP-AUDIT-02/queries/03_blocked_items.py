# -*- coding: utf-8 -*-
"""03 阻塞项与污染复算 —— 阶段 0 的第三件交付物。

三件事：

  1. **独立复算**反重力在 05_合成记录准确清单 里报的每一个数（不采信其自报）
  2. 检查它声称的「读侧排除」过滤器是否真的存在，以及是否会误伤正常信号
  3. 列出即使版本冻结也无法由审核方独立完成的项，写明卡点在哪、谁才能解

产物：out/03_阻塞项与污染复算.json、out/03_阻塞项与污染复算.md
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sqlite3

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

LIVE_DB = BASE / "框架/backend/live/live_signals.sqlite3"
PAPER_DB = BASE / "数据/paper/paper_3a.sqlite3"
GATE = BASE / "框架/backend/live/paper_new_entry_gate.json"
LIVE_API = BASE / "框架/backend/live_api.py"

SYN = ("sig_closeloop_%", "sig_ui_test_%")


def ro(p: pathlib.Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def main() -> None:
    now = dt.datetime.now().astimezone()
    R: dict = {"生成时刻": now.isoformat(timespec="seconds")}

    # ---------------------------------------------------------- 1. 独立复算
    con = ro(LIVE_DB)
    sig_total = q(con, "SELECT COUNT(*) FROM live_signal")[0][0]
    sig_syn = q(con, "SELECT signal_uid, symbol, direction, first_seen_at, event_time FROM live_signal "
                     "WHERE signal_uid LIKE ? OR signal_uid LIKE ? ORDER BY signal_uid", SYN)
    con.close()

    con = ro(PAPER_DB)
    # paper_position 没有 net_pnl 列（反重力文档里给了 net_pnl，那其实是 paper_realized 的值）
    pos = q(con, "SELECT position_id, account_id, symbol, qty, entry_price, stop_price, "
                 "entry_ts, exit_ts, exit_price, exit_reason, status, "
                 "entry_fee, exit_fee, funding_paid, signal_uid, model_version FROM paper_position "
                 "ORDER BY position_id")
    n_order = q(con, "SELECT COUNT(*) FROM paper_order")[0][0]
    n_fill = q(con, "SELECT COUNT(*) FROM paper_fill")[0][0]
    n_real = q(con, "SELECT COUNT(*) FROM paper_realized")[0][0]
    n_pend = q(con, "SELECT COUNT(*) FROM paper_pending")[0][0]
    has = lambda t: q(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (t,))[0][0]
    n_anchor = q(con, "SELECT COUNT(*) FROM paper_request_anchor")[0][0] if has("paper_request_anchor") else None
    anchors = q(con, "SELECT account_id, event_uid, first_request_ms, created_at FROM paper_request_anchor "
                     "ORDER BY first_request_ms") if has("paper_request_anchor") else []
    fills = q(con, "SELECT fill_id, order_id, position_id, action, qty, fill_price, fee, "
                   "slippage_cost, requested_at, first_request_ms, fill_ts, bar_open_time "
                   "FROM paper_fill ORDER BY fill_id")
    realized = q(con, "SELECT id, position_id, gross_pnl, net_pnl, entry_fee, exit_fee, exit_reason "
                      "FROM paper_realized ORDER BY id")
    state = q(con, "SELECT account_id, cash, realized_pnl, fees_paid, funding_paid FROM paper_state")
    n_stoph = q(con, "SELECT COUNT(*) FROM paper_stop_history")[0][0] if has("paper_stop_history") else None
    pos_cols = [d[1] for d in q(con, "PRAGMA table_info(paper_position)")]
    con.close()

    fees_sum = sum(f[6] or 0 for f in fills)
    gross_sum = sum(r[2] or 0 for r in realized)
    net_sum = sum(r[3] or 0 for r in realized)
    st = state[0] if state else (None,) * 5

    # 因果新列：对方为修 P0-1 加的字段，用来核对「服务端接收锚点」是否真的落库
    causal_cols = ["requested_at", "earliest_fill_ms", "first_request_ms", "epoch_ms",
                   "voided", "void_reason"]
    recount = {
        "live_signal_总行数": sig_total,
        "live_signal_合成行数": len(sig_syn),
        "live_signal_合成明细": [{"signal_uid": s[0], "symbol": s[1], "direction": s[2],
                                  "first_seen_at": s[3], "event_time": s[4]} for s in sig_syn],
        "live_signal_真实信号行数": sig_total - len(sig_syn),
        "live_signal_结论": (f"整张表 {sig_total} 行全部是合成记录，真实信号 0 条 ⇒ "
                          "即使读侧过滤掉合成前缀，当前信号区也只会是空的，"
                          "「页面无合成信号」将变成空洞成立，不能当作通过证据"),
        "paper_position_行数": len(pos),
        "paper_position_status": sorted({p[10] for p in pos}),
        "paper_position_明细": [{"position_id": p[0], "symbol": p[2], "qty": p[3],
                                 "entry_price": p[4], "stop_price": p[5], "entry_ts": p[6],
                                 "exit_ts": p[7], "exit_price": p[8], "exit_reason": p[9],
                                 "status": p[10], "signal_uid": p[14],
                                 "model_version": p[15]} for p in pos],
        "paper_order_行数": n_order,
        "paper_fill_行数": n_fill,
        "paper_realized_行数": n_real,
        "paper_pending_行数": n_pend,
        "paper_request_anchor_行数": n_anchor,
        "paper_request_anchor_明细": [{"event_uid": a[1], "first_request_ms": a[2]} for a in anchors],
        "paper_stop_history_行数": n_stoph,
        "paper_stop_history_结论": ("表已建但 **0 行** ⇒ 止损轨迹在真实账本里一条都没有，"
                                 "L4「初始止损与跟踪上移可重建」既不能证真也不能证伪；"
                                 "同时也说明上一轮认为「开仓会写 initial_stop」这条并未实际发生"
                                 if n_stoph == 0 else f"有 {n_stoph} 行"),
        "paper_position_新增因果列": [c for c in causal_cols if c in pos_cols],
        "paper_state": {"account_id": st[0], "cash": st[1], "realized_pnl": st[2],
                        "fees_paid": st[3], "funding_paid": st[4]},
        "复算_手续费合计": round(fees_sum, 8),
        "复算_毛盈亏合计": round(gross_sum, 8),
        "复算_净盈亏合计": round(net_sum, 8),
        "复算_现金变动": round((st[1] or 0) - 5000.0, 8),
        "勾稽_净盈亏==毛盈亏-手续费": abs(net_sum - (gross_sum - fees_sum)) < 1e-6,
    }

    # 与反重力自报逐项比对
    claimed = {"live_signal": 3, "paper_position": 3, "paper_order": 3, "paper_fill": 6,
               "paper_realized": 3, "paper_pending": 3, "现金变动": -4.3455}
    actual = {"live_signal": len(sig_syn), "paper_position": len(pos), "paper_order": n_order,
              "paper_fill": n_fill, "paper_realized": n_real, "paper_pending": n_pend,
              "现金变动": round((st[1] or 0) - 5000.0, 4)}
    cmp_rows = []
    for k in claimed:
        a, c = actual[k], claimed[k]
        ok = (abs(a - c) <= 1e-3) if isinstance(c, float) else (a == c)
        cmp_rows.append({"项": k, "反重力自报": c, "独立复算": a, "一致": ok})
    recount["自报与复算比对"] = cmp_rows
    recount["自报是否全部准确"] = all(r["一致"] for r in cmp_rows)

    # ---------------------------------------------------------- 2. 读侧排除
    txt = LIVE_API.read_text(encoding="utf-8", errors="ignore")
    filt = []
    for i, line in enumerate(txt.splitlines(), 1):
        if ("sig_closeloop" in line or "sig_ui_test" in line or "synthetic" in line.lower()
                or "NOT LIKE" in line.upper()):
            filt.append({"行": i, "内容": line.strip()[:160]})
    # 找 live_signals 读接口的实现位置
    m = re.search(r"def live_signals", txt)
    ctx = []
    if m:
        start = txt[:m.start()].count("\n")
        ctx = [{"行": start + 1 + i, "内容": l.strip()[:160]}
               for i, l in enumerate(txt.splitlines()[start:start + 40])]

    filter_block = {
        "命中行数": len(filt),
        "命中明细": filt,
        "live_signals_实现前40行": ctx,
        "是否存在读侧排除": len(filt) > 0,
        "风险_误伤": ("LIKE 'sig_%' 这类宽匹配会连带排除正常信号；"
                   "必须精确到 sig_closeloop_% 与 sig_ui_test_% 两个前缀，且要有单元测试证明正常信号不被排除"
                   if filt else "未实现"),
        "定性": ("**读侧排除不等于数据清理**：合成记录仍在库、亏损仍在账本（cash 已少 "
                 f"{abs(round((st[1] or 0) - 5000.0, 4))} USDT）。"
                 "页面看起来干净 ≠ 账本干净，两者必须分开判。"),
    }

    # ---------------------------------------------------------- 3. 闸门
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    gate_info = {"allow_new_entries": gate.get("allow_new_entries"),
                 "set_by": gate.get("set_by"), "reason": gate.get("reason"),
                 "mtime": dt.datetime.fromtimestamp(GATE.stat().st_mtime).astimezone().isoformat(timespec="seconds")}

    # ---------------------------------------------------------- 4. 阻塞项
    blocked = [
        {"编号": "B-01", "项": "版本冻结回执", "阻塞对象": "⑦ 版本一致 / 正式验收整体",
         "卡点": "evidence/SYSTEM-CLOSELOOP-02/ 里没有任何「版本冻结回执」实体；"
                 "对方在 20:22:09 / 20:23:36 / 20:24:51 / 20:26:21 / 20:26:34 / 20:27:15 连续改代码，"
                 "最后改动距本轮 T0 仅 16 秒",
         "谁能解": "反重力（出具书面冻结回执，含被冻结文件清单与 SHA-256）",
         "不解决会怎样": "正式验收不得开始；本轮只能交阶段 0"},
        {"编号": "B-02", "项": "生产库合成记录处置范围", "阻塞对象": "② 隔离 / L8 / X3c",
         "卡点": f"生产 live 库仍有 {len(sig_syn)} 条合成信号；纸面账本仍有 {len(pos)} 笔测试持仓、"
                 f"{n_order} 笔订单、{n_fill} 笔成交、{n_real} 笔已实现，现金少 "
                 f"{abs(round((st[1] or 0) - 5000.0, 4))} USDT。工单明令「不删除合成记录，不替用户批准清理范围」",
         "谁能解": "用户（明确批准清理范围与备份方式）",
         "不解决会怎样": "L8 只能维持不通过；X3c 只能记录「数据在库、仅读侧隐藏」，不得判通过"},
        {"编号": "B-03", "项": "生产闸门被对方改为关闭", "阻塞对象": "W3 / A20 / ② 隔离",
         "卡点": f"生产闸门 allow_new_entries={gate_info['allow_new_entries']}、"
                 f"set_by={gate_info['set_by']}、mtime={gate_info['mtime']}；"
                 "这是审核期间由被审方改动的生产状态，不是审核方动作",
         "谁能解": "需在报告中如实记录；恢复开闸须由用户决定，审核方不代为翻转",
         "不解决会怎样": "「日常实例闸门未被测试动作改变」这条只能证明审核方没改，不能证明整体未被改"},
        {"编号": "B-04", "项": "「既定规则」的唯一文本", "阻塞对象": "⑥ 回测与模拟同规则对账",
         "卡点": "paper_engine.py 头部声明 ROI/trailing=off、max_holding_hours=720、"
                 "stops=入场价−3×ATR；而回测冻结参数是 trailing_trigger_r=2.0、"
                 "trailing_lookback_4h_bars=10、max_holding_days=10。两套口径不同，"
                 "且 paper 侧 record_stop_update() 零调用 ⇒ 跟踪止损在模拟链路里没实现",
         "谁能解": "反重力（书面确认模拟盘「既定规则」到底是哪一套）",
         "不解决会怎样": "同规则对账只能做「差异清单」，不能做「一致性判定」"},
        {"编号": "B-05", "项": "前向纸面验证的合格独立区间", "阻塞对象": "四问之④",
         "卡点": "合格独立验证区间 = 0（723 行「币×区间」审计结论，④49 条属计算级接触）",
         "谁能解": "需先裁决前向方案与固定起点（工单：最后一项通过也不自动启动验证）",
         "不解决会怎样": "工程条件可判，策略有效性不可判；两者不得混说"},
    ]

    R.update({"一_污染独立复算": recount,
              "二_读侧排除": filter_block,
              "三_生产闸门": gate_info,
              "四_阻塞项": blocked})

    (OUT / "03_阻塞项与污染复算.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------------------------------------------------- Markdown
    L = ["# 阻塞项与污染复算（阶段 0 交付物，未开始正式验收）\n",
         f"- 生成时刻：`{now.isoformat(timespec='seconds')}`",
         "- 原则：不采信被审方自报，每个数独立从库里重算\n",
         "## 一、反重力自报 vs 独立复算\n",
         "| 项 | 反重力自报 | 独立复算 | 一致 |", "|---|---|---|---|"]
    for r in cmp_rows:
        L.append(f"| {r['项']} | {r['反重力自报']} | {r['独立复算']} | {'是' if r['一致'] else '**否**'} |")
    L.append(f"\n**自报是否全部准确：{'是' if recount['自报是否全部准确'] else '否'}**\n")
    L.append("勾稽：")
    L.append(f"- 手续费合计 `{recount['复算_手续费合计']}`（由 6 笔成交逐笔相加）")
    L.append(f"- 毛盈亏合计 `{recount['复算_毛盈亏合计']}`")
    L.append(f"- 净盈亏合计 `{recount['复算_净盈亏合计']}`")
    L.append(f"- 净 = 毛 − 手续费：`{recount['勾稽_净盈亏==毛盈亏-手续费']}`")
    L.append(f"- 现金变动 `{recount['复算_现金变动']}`\n")
    L.append("## 二、读侧排除过滤器\n")
    L.append(f"- 在 `live_api.py` 里命中 `{filter_block['命中行数']}` 行与合成标识/排除相关：\n")
    if filter_block["命中明细"]:
        L.append("| 行 | 内容 |")
        L.append("|---|---|")
        for h in filter_block["命中明细"][:20]:
            L.append(f"| {h['行']} | `{h['内容']}` |")
    else:
        L.append("> 未命中。对方在方案里写的「读侧排除」**尚未落到代码**。")
    L.append("")
    L.append(f"**定性**：{filter_block['定性']}\n")
    L.append(f"**误伤风险**：{filter_block['风险_误伤']}\n")
    L.append("## 三、生产闸门现状\n")
    L.append(f"- `allow_new_entries` = `{gate_info['allow_new_entries']}`")
    L.append(f"- `set_by` = `{gate_info['set_by']}`")
    L.append(f"- `reason` = `{gate_info['reason']}`")
    L.append(f"- 文件 mtime = `{gate_info['mtime']}`\n")
    L.append("## 四、阻塞项\n")
    L.append("| 编号 | 项 | 阻塞对象 | 卡点 | 谁能解 |")
    L.append("|---|---|---|---|---|")
    for b in blocked:
        L.append(f"| {b['编号']} | {b['项']} | {b['阻塞对象']} | {b['卡点']} | {b['谁能解']} |")
    L.append("")
    (OUT / "03_阻塞项与污染复算.md").write_text("\n".join(L), encoding="utf-8")

    print("=== 自报 vs 复算 ===")
    for r in cmp_rows:
        print(f"  {r['项']:16s} 自报 {str(r['反重力自报']):>10s}  复算 {str(r['独立复算']):>12s}  "
              f"{'一致' if r['一致'] else '不一致'}")
    print(f"\n勾稽 净=毛-费 : {recount['勾稽_净盈亏==毛盈亏-手续费']}")
    print(f"读侧排除命中行 : {filter_block['命中行数']}")
    for h in filter_block["命中明细"][:8]:
        print(f"   L{h['行']}: {h['内容'][:110]}")
    print(f"\n闸门 : {json.dumps(gate_info, ensure_ascii=False)}")
    print(f"阻塞项 : {len(blocked)} 条")


if __name__ == "__main__":
    main()
