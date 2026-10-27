# -*- coding: utf-8 -*-
"""07 报告数字自检：把报告里引用的关键数字，逐条回到 out/*.json 原始字段复算。

规矩（沿用 07b_报告数值对账 模式）：报告里的每个数字都必须能被本脚本机械复算；
任何一条不一致就整脚本失败（退出码 1），不允许「大概是这个数」。

产物：out/07_自检.json
"""
from __future__ import annotations

import json
import pathlib
import sys

OUT = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-CLOSELOOP-AUDIT-04/out")


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


CHECKS: list[tuple[str, object, object]] = []


def eq(label: str, got, want) -> None:
    CHECKS.append((label, got, want))


def main() -> None:
    f0 = load("00_开工冻结.json")
    f0a = load("00a_首采快照.json")
    a2 = load("02_自动闭环.json")
    a3 = load("03_行情防线.json")
    a4 = load("04_参数与闭合.json")
    a5 = load("05_止损轨迹.json")
    a6 = load("06_起止版本.json")

    # 00 版本
    eq("00 与 AUDIT-03 基准的变动条数", f0["变动"]["条数"], 10)
    eq("00 本轮文件总数", f0["本次"]["文件数"], 153)
    eq("00 首采快照文件数", len(f0a["全量指纹"]), 153)
    eq("00 入口脚本命中 paper 关键词数",
       sum(len(v.get("命中关键词", [])) for v in f0["入口接线"]["启动脚本"].values()), 0)
    eq("00 运行中的执行守护数", len(f0["入口接线"]["当前运行中的执行守护"]), 0)

    # 02 自动闭环
    eq("02 ①挂单", a2["判定"]["①挂单"], "通过")
    eq("02 ②成交", a2["判定"]["②成交"], "不通过")
    eq("02 ③止损自动上移", a2["判定"]["③止损自动上移"], "未执行")
    eq("02 ④自动退出", a2["判定"]["④自动退出"], "未执行")
    eq("02 ⑤单实例锁", a2["判定"]["⑤单实例锁"], "通过")
    eq("02 ⑥重启恢复", a2["判定"]["⑥重启恢复"], "通过")
    eq("02 拒单原因", a2["未成交原因"]["order_reject"][0][1], "missing_parameter_stoploss_s")
    eq("02 挂单仍为 pending", a2["未成交原因"]["pending_status"][0][0], "pending")

    # 03 行情防线
    eq("03 用例总数", a3["统计"]["总"], 8)
    eq("03 通过数", a3["统计"]["通过"], 4)
    eq("03 不通过数", a3["统计"]["不通过"], 4)
    by = {c["用例"][0]: c for c in a3["用例"]}
    eq("03 B 假K线注入后止损", round(by["B"]["止损_注入后"], 4), 190.0)
    eq("03 C 未来K线注入后止损", round(by["C"]["止损_注入后"], 4), 190.0)
    eq("03 F 外部参数覆盖后止损", round(by["F"]["止损_注入后"], 4), 96.9)
    eq("03 D 错误标的止损未动", by["D"]["止损_注入后"], 85.0)
    eq("03 G 缺 trail_d 拒因", by["G"]["reason"], "missing_parameter_trail_d")
    eq("03 止损轨迹路由 HTTP", a3["止损轨迹路由"]["HTTP"], 200)
    eq("03 不存在持仓 HTTP", a3["止损轨迹路由_不存在持仓"]["HTTP"], 404)
    eq("03 守护状态（跨进程）", a3["守护状态接口"]["item"]["status"], "not_started")

    # 04 参数与闭合
    eq("04 approved_params 含 stoploss_s", a4["③_配置"]["含 stoploss_s"], False)
    eq("04 approved_params 含 trail_d", a4["③_配置"]["含 trail_d"], False)
    eq("04 生效视图文件存在", a4["③_配置"]["生效视图存在"], False)
    eq("04 配置来源", a4["③_配置"]["approved_params._source"], "fallback_frozen_v2_2_conservative")
    eq("04 d=0.05 候选", round(a4["③_两组参数"]["d=0.05"]["candidate"], 4), 92.1)
    eq("04 d=0.10 候选", round(a4["③_两组参数"]["d=0.10"]["candidate"], 4), 87.3)
    eq("04 两组区别成立", a4["③_两组参数"]["区别成立"], True)
    eq("04 缺 trail_d 拒因", a4["③_缺参数"]["reason"], "missing_parameter_trail_d")
    eq("04 初始止损拒因", a4["③_初始止损"]["reason"], "missing_parameter_stoploss_s")
    eq("04 止损未写成老bar收盘", a4["④_不追溯"]["effective_at_是否被写成老bar收盘"], False)
    eq("04 4h 不触发退出", a4["④_分离_4h不退出"]["exit_triggered"], False)
    eq("04 15m 触发退出", a4["④_分离_15m退出"]["exit_triggered"], True)
    eq("04 闭合残差", a4["⑤_闭合"]["残差"], 0.0)
    eq("04 退出成交条数", a4["⑤_闭合"]["退出成交条数"], 1)
    eq("04 幂等（第二次拒绝）", a4["⑤_闭合"]["第二次退出（幂等）"]["reason"], "position_not_open")
    eq("04 判定·配置", a4["判定"]["③参数来自明确配置"], "不通过")
    eq("04 判定·两组参数", a4["判定"]["③两组参数有预期区别"], "通过")
    eq("04 判定·闭合", a4["判定"]["⑤账本闭合"], "通过")

    # 05 止损轨迹 UI
    eq("05 路由可访问", a5["判定"]["⑥止损轨迹路由可访问"], "通过")
    eq("05 页面与账本一致", a5["判定"]["⑥页面与账本一致"], "通过")
    eq("05 接口失败如实报错", a5["判定"]["⑥接口失败如实报错"], "通过")
    eq("05 截图重复组数", len(a5["截图去重"]), 0)
    eq("05 500 文案含模拟接口失败", "模拟接口失败" in (a5["接口500时页面文案"] or ""), True)
    eq("05 500 文案不含暂无上移记录", "暂无上移记录" in (a5["接口500时页面文案"] or ""), False)
    eq("05 账本轨迹条数", len(a5["账本轨迹"]), 2)

    # 06 起止版本
    eq("06 收工文件数", a6["文件数"]["收工"], 153)
    eq("06 起止变动条数", a6["验收期间变动"]["条数"], 0)
    eq("06 V1 起止零变动", a6["判定"]["V1 起止零变动"], "通过")
    eq("06 V2 进程跑最新代码", a6["判定"]["V2 进程跑最新代码"], "不通过")

    bad = [(l, g, w) for l, g, w in CHECKS if g != w]
    res = {"条数": len(CHECKS), "通过": len(CHECKS) - len(bad),
           "不通过": [{"项": l, "实测": g, "期望": w} for l, g, w in bad]}
    (OUT / "07_自检.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:2000])
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
