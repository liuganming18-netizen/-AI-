# -*- coding: utf-8 -*-
"""06 报告数值自检 —— 报告里每个关键数字，都必须能回到 out/*.json 的原始字段。

项目纪律（MEMORY）：人工抄写已验证会出错，所以凡报告引用的关键数字，
都要有一条「回到原始字段」的对账，并显式禁止已确认抄错的旧值。

本脚本做三件事：
  1. 从各 out/*.json 读出「真值」，逐条在 00_报告.md 里找它
  2. 找出报告里出现、但任何 json 都没有的数字（可疑：可能是手写错的）
  3. 显式禁止本轮已确认的旧错值

产物：out/06_报告自检.json
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"
OUT = HERE / "out"
REPORT = HERE / "00_报告.md"


def j(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> None:
    rep = REPORT.read_text(encoding="utf-8")
    drift = j("00_漂移判定.json")
    scope = j("01_整改范围核对.json")
    chk = j("02_复验清单.json")
    blk = j("03_阻塞项与污染复算.json")
    cau = j("04_污染数据因果指纹.json")
    dlv = j("05_反重力交付核验.json")

    checks: list[dict] = []

    def need(描述, 值, 来源):
        s = str(值)
        # 报告排版会在数字与斜杠之间加空格（如「7 / 29」），比较时统一去掉空格
        hit = (s in rep) or (s.replace(" ", "") in rep.replace(" ", ""))
        checks.append({"描述": 描述, "值": s, "来源": 来源, "报告里有": hit})

    # ---- 版本
    need("被审文件数", drift["文件数"], "00_漂移判定.文件数")
    # 布尔值在报告里写成中文判定，这里做映射，不能直接比 "True"/"False"
    need("D0 结果", "通过" if drift["D0_存在冻结回执"] else "不通过", "00_漂移判定.D0_存在冻结回执")
    need("D1 结果", "通过" if drift["D1_间隔内零变化"] else "不通过", "00_漂移判定.D1")
    need("D2 结果", "通过" if drift["D2_采样后无新写入"] else "不通过", "00_漂移判定.D2")
    need("D3 结果", "通过" if drift["D3_进程跑最新代码"] else "不通过", "00_漂移判定.D3")
    need("后端进程号", drift["D3_进程"]["pid"], "00_漂移判定.D3_进程.pid")
    need("观察窗秒数", int(drift["观察窗_s"]), "00_漂移判定.观察窗_s")
    need("最后改动距T1秒", int(drift["最后一次改动距T1_s"]), "00_漂移判定.最后一次改动距T1_s")

    # ---- 代码变动
    need("代码变动数", chk["统计"]["代码变动文件数"], "02_复验清单.统计.代码变动文件数")
    need("上轮13项受影响", chk["统计"]["13项通过中受代码修改影响"], "02_复验清单.统计")
    need("上轮13项不受影响", chk["统计"]["13项通过中不受影响"], "02_复验清单.统计")
    need("条目总数", len(chk["条目"]), "02_复验清单.条目")
    for c in chk["代码变动"]["变动"]:
        need(f"变动文件 {c['文件'].split('/')[-1]} 的当前短哈希",
             c["当前_sha"], f"02_复验清单.代码变动 → {c['文件']}")
        need(f"变动文件 {c['文件'].split('/')[-1]} 的改前字节",
             f'{c["AUDIT01_字节"]:,}', f"02_复验清单.代码变动 → {c['文件']}")
        # 右列必须一起校验：本轮就是漏了右列，把 4 个当前字节数抄成摘要里的旧值
        need(f"变动文件 {c['文件'].split('/')[-1]} 的改后字节",
             f'{c["当前_字节"]:,}', f"02_复验清单.代码变动 → {c['文件']}")
        need(f"变动文件 {c['文件'].split('/')[-1]} 的 mtime",
             c["当前_mtime"][11:19], f"02_复验清单.代码变动 → {c['文件']}")

    # ---- 污染复算
    rc = blk["一_污染独立复算"]
    need("live_signal 合成行数", rc["live_signal_合成行数"], "03.一.live_signal_合成行数")
    need("paper_position 行数", rc["paper_position_行数"], "03.一.paper_position_行数")
    need("paper_order 行数", rc["paper_order_行数"], "03.一.paper_order_行数")
    need("paper_fill 行数", rc["paper_fill_行数"], "03.一.paper_fill_行数")
    need("paper_realized 行数", rc["paper_realized_行数"], "03.一.paper_realized_行数")
    need("paper_pending 行数", rc["paper_pending_行数"], "03.一.paper_pending_行数")
    need("paper_stop_history 行数", rc["paper_stop_history_行数"], "03.一.paper_stop_history_行数")
    need("净盈亏合计", f'{abs(rc["复算_净盈亏合计"]):.8f}', "03.一.复算_净盈亏合计")
    need("手续费合计", rc["复算_手续费合计"], "03.一.复算_手续费合计")
    need("现金变动绝对值", abs(rc["复算_现金变动"]), "03.一.复算_现金变动")

    # ---- 因果指纹
    e1 = cau["E1_接收锚点"]
    need("E1 回填条数", e1["回填条数"], "04.E1.回填条数")
    need("E1 总条数", e1["总条数"], "04.E1.总条数")
    for x in e1["明细"]:
        if x["差_秒"] > 5:
            need(f"E1 {x['event_uid'][-10:]} 回填秒数", f'{x["差_秒"]:.1f}', "04.E1.明细.差_秒")
    e2 = cau["E2_成交早于请求"]
    need("E2 违规笔数", e2["违规笔数"], "04.E2.违规笔数")
    for x in e2["明细"]:
        need(f"E2 pos{x['position_id']} 提前秒数", f'{x["成交早于真实请求_秒"]:.1f}', "04.E2.明细")
    e3 = cau["E3_平仓侧订单"]
    need("E3 平仓成交 order_id 为 NULL 数", e3["平仓成交中_order_id_为NULL"], "04.E3")
    e4 = cau["E4_挂单"]
    need("E4 已 resolve 数", e4["已resolve数"], "04.E4.已resolve数")
    e5 = cau["E5_初始止损合规"]
    need("E5 违规笔数", e5["违规笔数"], "04.E5.违规笔数")
    for x in e5["明细"]:
        if not x["符合规则"]:
            need(f"E5 pos{x['position_id']} 止损差", x["差"], "04.E5.明细.差")
            need(f"E5 pos{x['position_id']} 实际止损", f'{x["stop_price"]:,.1f}', "04.E5.明细.stop_price")
            need(f"E5 pos{x['position_id']} 应有止损", f'{x["按规则应为"]:,.1f}', "04.E5.明细.按规则应为")
    need("E6 文档与库不一致数", cau["E6_文档与库比对"]["不一致数"], "04.E6.不一致数")
    need("合成 uid 并集数", cau["合成uid全量"]["总数"], "04.合成uid全量.总数")
    need("只在账本不在信号库数", len(cau["合成uid全量"]["只在账本不在信号库"]), "04.合成uid全量")

    # ---- 交付核验
    need("交付文件数", dlv["F1_交付文件"]["数量"], "05.F1.数量")
    need("截图数", dlv["F2_截图重复"]["截图数"], "05.F2.截图数")
    need("独立图像数", dlv["F2_截图重复"]["独立图像数"], "05.F2.独立图像数")
    need("回执64位哈希数", dlv["F3_回执是否给指纹"]["其中64位SHA256个数"], "05.F3")
    need("回执16位哈希数", dlv["F3_回执是否给指纹"]["其中16位短哈希个数"], "05.F3")
    need("引用文件不存在数", dlv["F4_回执引用的文件"]["不存在数"], "05.F4.不存在数")
    need("行号越界数", dlv["F5_回执行号"]["越界数"], "05.F5.越界数")
    need("结果文件条目数", dlv["F6_七项闭环"]["结果文件条目数"], "05.F6.结果文件条目数")
    need("引擎内 record_stop_update 命中数", dlv["F7_止损轨迹"]["引擎内命中数"], "05.F7")
    need("引擎内调用点数", len(dlv["F7_止损轨迹"]["引擎内调用点（排除定义行）"]), "05.F7")
    need("生产轨迹表行数", dlv["F7_止损轨迹"]["生产账本 paper_stop_history 行数"], "05.F7")
    need("断言总数", dlv["F8_正向成交路径"]["测试内断言总数"], "05.F8.测试内断言总数")
    need("正向成交断言数", len(dlv["F8_正向成交路径"]["正向断言（要求正常请求真的成交）"]), "05.F8")

    # ---- 显式禁止的旧错值（本轮已确认错误，不得再出现）
    forbidden = [
        {"值": "activeTab 只参与按钮", "原因": "本轮更正：L105/116/127 有渲染分支"},
        {"值": "挂单已 resolve", "原因": "库内全部 pending"},
        {"值": "止损 80,533.10 是实际值", "原因": "库内是 81,200.0，文档抄错"},
        {"值": "7 项全部 PASSED", "原因": "结果文件只有 5 条，这是对方说法不是事实"},
        {"值": "止损轨迹可重建", "原因": "record_stop_update 零调用，轨迹永不产生"},
    ]
    # 引号（「」）里的内容属于「引用对方的说法再反驳」，不算违规；
    # 只有不加引号直接断言才算违规。
    rep_unquoted = re.sub(r"「[^」]*」", "", rep)
    for f in forbidden:
        f["报告里出现"] = f["值"] in rep_unquoted

    # ---- 报告里出现但来源不明的数字（粗筛）
    num_in_report = set(re.findall(r"\d[\d,]{2,}", rep))
    known = set()
    for c in checks:
        known |= set(re.findall(r"\d[\d,]{2,}", c["值"]))
    for v in (rc, cau, dlv, chk):
        known |= set(re.findall(r"\d[\d,]{2,}", json.dumps(v, ensure_ascii=False)))
    def variants(v: str) -> set[str]:
        out = {v}
        try:
            out.add(f"{int(v.replace(',', '')):,}")
        except ValueError:
            pass
        return out
    known_expanded: set[str] = set()
    for k in known:
        known_expanded |= variants(k)
    unknown = sorted(num_in_report - known_expanded)

    fail = [c for c in checks if not c["报告里有"]]
    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "检查项数": len(checks),
        "通过": len(checks) - len(fail),
        "失败": len(fail),
        "失败明细": fail,
        "禁止出现的旧错值": forbidden,
        "旧错值违规": [f for f in forbidden if f["报告里出现"]],
        "报告里来源不明的数字": unknown,
        "结论": ("全部通过" if not fail and not [f for f in forbidden if f["报告里出现"]]
                 else "有不通过项，见失败明细"),
    }
    (OUT / "06_报告自检.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"=== 报告数值自检 ===")
    print(f"  检查项 {len(checks)}  通过 {res['通过']}  失败 {len(fail)}")
    for c in fail:
        print(f"  ✗ {c['描述']}: 期望「{c['值']}」在报告里，未找到（来源 {c['来源']}）")
    print(f"\n  旧错值违规: {len(res['旧错值违规'])}")
    for f in res["旧错值违规"]:
        print(f"  ✗ 「{f['值']}」仍在报告里 —— {f['原因']}")
    print(f"\n  报告里来源不明的数字 {len(unknown)} 个: {unknown[:20]}")
    print(f"\n  结论: {res['结论']}")


if __name__ == "__main__":
    main()
