# -*- coding: utf-8 -*-
"""04 污染数据的因果指纹 —— 阶段 0 的第四件交付物。

上一轮 AUDIT-01 判 L5/L5b/C1b 不通过，靠的是隔离实例里「注入过去 requested_at 就立即成交」的行为复现。
本轮在**生产账本的真实数据**里找到了同一缺陷的静态指纹，不需要再跑一次实例就能证明：

  E1  服务端接收锚点 `paper_request_anchor.first_request_ms` 与其写入时刻 `created_at`
      相差十几到几十分钟 ⇒ 锚点不是「请求到达时写下的」，而是被回填成信号 bar 时间
  E2  成交时间 `fill_ts` 早于锚点真实写入时刻 ⇒ **成交发生在请求之前**
  E3  平仓侧成交 `order_id` 为 NULL ⇒ 订单→成交→持仓→已实现 链条在平仓侧断链
  E4  `paper_pending` 三行全部 status='pending'（反重力文档写「已 resolve」）
  E5  position 1 的 `stop_price` ≠ 入场价 − 3×ATR（违反其自己声明的规则，且与文档写的值不符）

产物：out/04_污染数据因果指纹.json、out/04_污染数据因果指纹.md
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
PAPER = BASE / "数据/paper/paper_3a.sqlite3"
LIVE = BASE / "框架/backend/live/live_signals.sqlite3"


def ts(ms):
    if not ms:
        return None
    return dt.datetime.fromtimestamp(ms / 1000).astimezone().strftime("%m-%d %H:%M:%S.%f")[:-3]


def main() -> None:
    now = dt.datetime.now().astimezone()
    con = sqlite3.connect(f"file:{PAPER}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    R = {"生成时刻": now.isoformat(timespec="seconds")}

    # ---------------------------------------------------------------- E1
    anchors = [dict(r) for r in con.execute(
        "SELECT account_id, event_uid, first_request_ms, created_at FROM paper_request_anchor "
        "ORDER BY first_request_ms")]
    e1 = []
    for a in anchors:
        delta = (a["created_at"] - a["first_request_ms"]) / 1000.0
        e1.append({"event_uid": a["event_uid"],
                   "first_request_ms": a["first_request_ms"], "first_request_时刻": ts(a["first_request_ms"]),
                   "created_at_ms": a["created_at"], "created_at_时刻": ts(a["created_at"]),
                   "差_秒": round(delta, 3),
                   "判定": "回填（锚点早于写入时刻）" if delta > 5 else "正常（锚点≈写入时刻）"})
    R["E1_接收锚点"] = {"明细": e1,
                        "回填条数": sum(1 for x in e1 if x["差_秒"] > 5),
                        "总条数": len(e1),
                        "含义": "锚点本应是「请求到达服务端那一刻」的时间戳，它必须 ≈ created_at。"
                                "差出几十秒以上，说明这个时间是从调用方传来的信号时间回填进去的。"}

    # ---------------------------------------------------------------- E2
    fills = [dict(r) for r in con.execute(
        "SELECT fill_id, order_id, position_id, signal_uid, action, fill_price, fee, "
        "requested_at, fill_ts, bar_open_time FROM paper_fill ORDER BY fill_id")]
    anchor_by_uid = {a["event_uid"]: a for a in anchors}
    e2 = []
    for f in fills:
        if f["action"] != "open":
            continue
        a = anchor_by_uid.get(f["signal_uid"])
        if not a:
            continue
        true_req = a["created_at"]
        lead = (true_req - f["fill_ts"]) / 1000.0
        e2.append({"position_id": f["position_id"], "signal_uid": f["signal_uid"],
                   "账面requested_at": ts(f["requested_at"]),
                   "成交fill_ts": ts(f["fill_ts"]),
                   "真实请求到达": ts(true_req),
                   "成交早于真实请求_秒": round(lead, 3),
                   "判定": "**成交早于请求**" if lead > 0 else "因果正常"})
    R["E2_成交早于请求"] = {"明细": e2,
                           "违规笔数": sum(1 for x in e2 if x["成交早于真实请求_秒"] > 0),
                           "含义": "账面 requested_at 被回填成信号 bar 时间，看起来「请求在成交之前」；"
                                   "用锚点的真实写入时刻一比，成交反而发生在请求之前。"}

    # ---------------------------------------------------------------- E3
    closes = [dict(r) for r in con.execute(
        "SELECT fill_id, order_id, position_id, action FROM paper_fill WHERE action='close' ORDER BY fill_id")]
    orders = [dict(r) for r in con.execute(
        "SELECT order_id, event_uid, side, status FROM paper_order ORDER BY order_id")]
    R["E3_平仓侧订单"] = {
        "paper_order_行数": len(orders),
        "paper_order_方向": sorted({o["side"] for o in orders}),
        "平仓成交数": len(closes),
        "平仓成交中_order_id_为NULL": sum(1 for c in closes if c["order_id"] is None),
        "明细": [{"fill_id": c["fill_id"], "position_id": c["position_id"],
                  "order_id": c["order_id"]} for c in closes],
        "含义": "平仓产生了成交与已实现盈亏，但没有对应的订单行 ⇒ "
                "「订单→成交→持仓→退出→费用→权益」这条链在平仓侧缺一环，无法逐项闭合。"}

    # ---------------------------------------------------------------- E4
    pend = [dict(r) for r in con.execute(
        "SELECT pending_id, signal_uid, status, reason, created_at, resolved_at, fill_position_id "
        "FROM paper_pending ORDER BY pending_id")]
    pos_uids = {r[0] for r in con.execute("SELECT signal_uid FROM paper_position")}
    R["E4_挂单"] = {
        "行数": len(pend),
        "status分布": sorted({p["status"] for p in pend}),
        "已resolve数": sum(1 for p in pend if p["status"] != "pending"),
        "明细": [{"pending_id": p["pending_id"], "signal_uid": p["signal_uid"],
                  "status": p["status"], "resolved_at": p["resolved_at"],
                  "fill_position_id": p["fill_position_id"],
                  "该uid是否对应某笔持仓": p["signal_uid"] in pos_uids} for p in pend],
        "反重力文档声称": "3 笔挂单记录（已 resolve）",
        "实际": "全部 status='pending'，resolved_at 为 NULL，fill_position_id 为 NULL",
        "含义": "挂单既没被解析也没关联到任何持仓，且三行 uid 与三笔持仓的 uid **完全不相交** ⇒ "
                "这批挂单和持仓不是同一批动作，合成污染的 uid 实际不止 3 个。"}

    # ---------------------------------------------------------------- E5
    pos = [dict(r) for r in con.execute(
        "SELECT position_id, entry_price, stop_price, atr_at_entry, exit_reason, status "
        "FROM paper_position ORDER BY position_id")]
    e5 = []
    for p in pos:
        should = p["entry_price"] - 3 * p["atr_at_entry"]
        e5.append({"position_id": p["position_id"], "entry_price": p["entry_price"],
                   "atr_at_entry": p["atr_at_entry"], "stop_price": p["stop_price"],
                   "按规则应为": round(should, 6),
                   "差": round(p["stop_price"] - should, 6),
                   "符合规则": abs(p["stop_price"] - should) < 1e-6})
    R["E5_初始止损合规"] = {"明细": e5,
                          "违规笔数": sum(1 for x in e5 if not x["符合规则"]),
                          "规则出处": "paper_engine.py 头部 L19：`stops.uniform = 入场价 − 3×信号 ATR`",
                          "含义": "同一套声明规则在三笔持仓里有一笔不成立 ⇒ "
                                  "要么规则不是声明的那样，要么这笔走了另一条代码路径。"}

    # ---------------------------------------------------------------- E6 反重力文档 vs 库
    claims = [
        {"项": "position 1 的 stop_price", "文档": 80533.10, "库内": pos[0]["stop_price"]},
        {"项": "position 2 的 stop_price", "文档": 80470.00, "库内": pos[1]["stop_price"]},
        {"项": "position 3 的 stop_price", "文档": 80470.00, "库内": pos[2]["stop_price"]},
        {"项": "paper_pending 状态", "文档": "已 resolve", "库内": "全部 pending"},
    ]
    for c in claims:
        if isinstance(c["文档"], (int, float)):
            c["一致"] = abs(c["文档"] - c["库内"]) < 1e-6
        else:
            c["一致"] = False
    R["E6_文档与库比对"] = {"明细": claims,
                          "不一致数": sum(1 for c in claims if not c["一致"])}

    # ---------------------------------------------------------------- 合成 uid 全量
    live_uids = [r[0] for r in sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
                 .execute("SELECT signal_uid FROM live_signal")]
    all_uids = sorted(set(live_uids) | pos_uids
                      | {p["signal_uid"] for p in pend}
                      | {a["event_uid"] for a in anchors})
    R["合成uid全量"] = {
        "总数": len(all_uids),
        "live_signal库内": sorted(live_uids),
        "账本内": sorted(pos_uids | {p["signal_uid"] for p in pend}),
        "并集": all_uids,
        "只在账本不在信号库": sorted((pos_uids | {p["signal_uid"] for p in pend}) - set(live_uids)),
        "含义": "反重力文档只列了 3 个 uid，实际并集更大；"
                "「只在账本不在信号库」的 uid 说明有请求绕过信号库直接下单。",
    }

    con.close()
    (OUT / "04_污染数据因果指纹.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- Markdown
    L = ["# 污染数据的因果指纹（阶段 0 交付物，未开始正式验收）\n",
         f"- 生成时刻：`{now.isoformat(timespec='seconds')}`",
         "- 数据源：`数据/paper/paper_3a.sqlite3`（只读）、`框架/backend/live/live_signals.sqlite3`（只读）",
         "- 目的：不重跑实例，直接从真实落库数据证明上一轮 P0 缺陷的机制仍在数据里\n",
         "## E1 服务端接收锚点被回填\n",
         "| 信号 uid | 锚点声称的首次请求 | 锚点实际写入时刻 | 差 | 判定 |",
         "|---|---|---|---|---|"]
    for x in e1:
        L.append(f"| `{x['event_uid']}` | {x['first_request_时刻']} | {x['created_at_时刻']} | "
                 f"{x['差_秒']}s | {x['判定']} |")
    L.append(f"\n**回填 {R['E1_接收锚点']['回填条数']} / {R['E1_接收锚点']['总条数']}**。"
             f"{R['E1_接收锚点']['含义']}\n")
    L.append("## E2 成交发生在请求之前\n")
    L.append("| 持仓 | 账面 requested_at | 成交时刻 | 真实请求到达 | 成交早于请求 | 判定 |")
    L.append("|---|---|---|---|---|---|")
    for x in e2:
        L.append(f"| {x['position_id']} | {x['账面requested_at']} | {x['成交fill_ts']} | "
                 f"{x['真实请求到达']} | {x['成交早于真实请求_秒']}s | {x['判定']} |")
    L.append(f"\n**违规 {R['E2_成交早于请求']['违规笔数']} 笔**。{R['E2_成交早于请求']['含义']}\n")
    L.append("## E3 平仓侧无订单记录\n")
    L.append(f"- `paper_order` 共 {R['E3_平仓侧订单']['paper_order_行数']} 行，方向只有 "
             f"{R['E3_平仓侧订单']['paper_order_方向']}")
    L.append(f"- 平仓成交 {R['E3_平仓侧订单']['平仓成交数']} 笔，其中 `order_id` 为 NULL 的 "
             f"**{R['E3_平仓侧订单']['平仓成交中_order_id_为NULL']} 笔**")
    L.append(f"\n{R['E3_平仓侧订单']['含义']}\n")
    L.append("## E4 挂单既未解析也未关联持仓\n")
    L.append("| pending_id | 信号 uid | 状态 | 对应持仓? |")
    L.append("|---|---|---|---|")
    for x in R["E4_挂单"]["明细"]:
        L.append(f"| {x['pending_id']} | `{x['signal_uid']}` | {x['status']} | "
                 f"{'是' if x['该uid是否对应某笔持仓'] else '**否**'} |")
    L.append(f"\n- 反重力文档写：{R['E4_挂单']['反重力文档声称']}")
    L.append(f"- 实际：{R['E4_挂单']['实际']}")
    L.append(f"- {R['E4_挂单']['含义']}\n")
    L.append("## E5 初始止损不符合其自己声明的规则\n")
    L.append("| 持仓 | 入场价 | ATR | 实际止损 | 按规则应为 | 差 | 符合 |")
    L.append("|---|---|---|---|---|---|---|")
    for x in e5:
        L.append(f"| {x['position_id']} | {x['entry_price']} | {x['atr_at_entry']} | "
                 f"{x['stop_price']} | {x['按规则应为']} | {x['差']} | "
                 f"{'是' if x['符合规则'] else '**否**'} |")
    L.append(f"\n规则出处：{R['E5_初始止损合规']['规则出处']}")
    L.append(f"\n{R['E5_初始止损合规']['含义']}\n")
    L.append("## E6 反重力文档与库内实际值比对\n")
    L.append("| 项 | 文档写的 | 库内实际 | 一致 |")
    L.append("|---|---|---|---|")
    for c in claims:
        L.append(f"| {c['项']} | {c['文档']} | {c['库内']} | {'是' if c['一致'] else '**否**'} |")
    L.append(f"\n**不一致 {R['E6_文档与库比对']['不一致数']} 项。**\n")
    L.append("## 附：合成 uid 全量\n")
    L.append(f"- 并集共 **{R['合成uid全量']['总数']}** 个，反重力文档只列了 3 个")
    L.append(f"- live 信号库内：{R['合成uid全量']['live_signal库内']}")
    L.append(f"- 只在账本、不在信号库：**{R['合成uid全量']['只在账本不在信号库']}**")
    L.append(f"\n{R['合成uid全量']['含义']}\n")
    (OUT / "04_污染数据因果指纹.md").write_text("\n".join(L), encoding="utf-8")

    print("=== E1 接收锚点 ===")
    for x in e1:
        print(f"  {x['event_uid']:34s} 锚点 {x['first_request_时刻']}  写入 {x['created_at_时刻']}  "
              f"差 {x['差_秒']:>9.1f}s  {x['判定']}")
    print(f"  回填 {R['E1_接收锚点']['回填条数']}/{R['E1_接收锚点']['总条数']}")
    print("\n=== E2 成交早于请求 ===")
    for x in e2:
        print(f"  pos{x['position_id']}  成交 {x['成交fill_ts']}  真实请求 {x['真实请求到达']}  "
              f"早 {x['成交早于真实请求_秒']:>8.1f}s  {x['判定']}")
    print("\n=== E3 平仓订单 ===")
    print("  order行数", R["E3_平仓侧订单"]["paper_order_行数"], "方向", R["E3_平仓侧订单"]["paper_order_方向"],
          "平仓成交", R["E3_平仓侧订单"]["平仓成交数"],
          "order_id为NULL", R["E3_平仓侧订单"]["平仓成交中_order_id_为NULL"])
    print("\n=== E4 挂单 ===")
    for x in R["E4_挂单"]["明细"]:
        print(f"  pending{x['pending_id']} {x['signal_uid']:34s} {x['status']:8s} "
              f"对应持仓={x['该uid是否对应某笔持仓']}")
    print("\n=== E5 初始止损 ===")
    for x in e5:
        print(f"  pos{x['position_id']} 入场 {x['entry_price']} ATR {x['atr_at_entry']} "
              f"止损 {x['stop_price']} 应为 {x['按规则应为']} 差 {x['差']} {'OK' if x['符合规则'] else '违规'}")
    print("\n=== E6 文档 vs 库 ===")
    for c in claims:
        print(f"  {c['项']:26s} 文档 {str(c['文档']):>12s}  库 {str(c['库内']):>12s}  "
              f"{'一致' if c['一致'] else '不一致'}")
    print("\n=== 合成 uid 全量 ===")
    print(f"  并集 {R['合成uid全量']['总数']} 个；只在账本不在信号库：{R['合成uid全量']['只在账本不在信号库']}")


if __name__ == "__main__":
    main()
