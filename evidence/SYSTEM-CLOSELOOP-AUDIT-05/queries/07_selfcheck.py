# -*- coding: utf-8 -*-
"""07 报告数值机械复算：把 00_报告.md / 01_阶段回执.md 里引用的关键数字，
逐条回到 out/*.json 的原始字段复算，并检查报告里确实写了这个值。

纪律：报告数字必须能被脚本复算；人工抄写已验证会出错。
产物：out/07_自检.json
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import sys

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-05"
OUT = HERE / "out"

REPORT = io.open(HERE / "00_报告.md", encoding="utf-8").read()
RECEIPT = io.open(HERE / "01_阶段回执.md", encoding="utf-8").read()
BOTH = REPORT + "\n" + RECEIPT


def J(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> int:
    fz = J("00_开工冻结.json")
    tr = J("01_测试真实性.json")
    es = J("03_入口与状态.json")
    au = J("04_自动闭环.json")
    gd = J("05_行情防线.json")
    ev = J("06_起止版本.json")

    checks: list[tuple[str, bool, str]] = []

    def ck(label: str, cond: bool, detail: str = "") -> None:
        checks.append((label, bool(cond), detail))

    def hhmmss(iso: str) -> str:
        """从 ISO8601 取 HH:MM:SS（带时区后缀，不能直接 endswith 比对）。"""
        return iso[11:19] if len(iso) >= 19 else iso

    # 版本
    ck("冻结文件数=155", fz["本次"]["文件数"] == 155, str(fz["本次"]["文件数"]))
    ck("报告写了 155 文件", "155 文件" in BOTH)
    ck("收工变动=0", ev["V1_起止零变动"]["变动条数"] == 0, str(ev["V1_起止零变动"]["变动条数"]))
    ck("报告写了变动 0", "变动 0" in BOTH)
    ck("V2 不通过", ev["V2_日常实例最新"]["判定"] == "不通过")
    ck("V3 通过", ev["V3_生产未被写"]["判定"] == "通过")
    ck("8787 启动=23:13:40", hhmmss(fz["运行进程"]["启动时刻"]) == "23:13:40", fz["运行进程"]["启动时刻"])
    ck("报告写了 23:13:40", "23:13:40" in BOTH)
    ck("最新代码=00:39:35", hhmmss(fz["本次"]["最新mtime"]) == "00:39:35", fz["本次"]["最新mtime"])
    ck("报告写了 00:39:35", "00:39:35" in BOTH)
    ck("三启动脚本零命中",
       all(v["命中关键词"] == [] for v in fz["入口接线"]["启动脚本"].values()),
       json.dumps({k: v["命中关键词"] for k, v in fz["入口接线"]["启动脚本"].items()}, ensure_ascii=False))

    # 测试真实性
    ck("对方 execute_cycle=7", tr["C_汇总"]["execute_cycle_调用总次数"] == 7,
       str(tr["C_汇总"]["execute_cycle_调用总次数"]))
    ck("报告写了 7 次", "7 次" in BOTH)
    ck("对方 record_stop_update=0", tr["C_汇总"]["record_stop_update_调用总次数"] == 0)
    ck("对方裸写账本=0", len(tr["C_汇总"]["对账本四表的裸SQL写入"]) == 0)
    ck("生产库无身份列",
       all("stoploss_s" not in v["paper_position"]
           for k, v in tr["A_账本配置身份列"].items() if not k.startswith("隔离/")))
    ck("隔离库有身份列",
       all("config_fingerprint" in v["paper_position"]
           for k, v in tr["A_账本配置身份列"].items() if k.startswith("隔离/")))

    # 第1项
    ck("①入口不拉守护=不通过", es["判定"]["①统一入口是否拉起守护"] == "不通过")
    ck("②守护真实运行=通过", es["判定"]["②守护是否真实运行"] == "通过")
    ck("③状态接口与进程一致=通过", es["判定"]["③状态接口与进程一致"] == "通过")

    # 第2/3/4/6项
    ck("参数贯通=是", au["A_判定"]["结论"].startswith("参数贯通"))
    ck("缺配置无启动自检", au["B_缺配置"]["启动检查"].startswith("不通过"))
    ck("缺配置仍开仓", au["B_缺配置"]["缺配置下的信号结果"]["ok"] is True)
    ck("①挂单=通过", au["C_判定"]["①挂单"] == "通过")
    ck("②自动成交=通过", au["C_判定"]["②自动成交"] == "通过")
    ck("成交价=100.10", abs(au["C_判定"]["成交价"][0] - 100.10) < 1e-9, str(au["C_判定"]["成交价"][0]))
    ck("报告写了 100.10", "100.10" in BOTH)
    ck("初始止损=95.0", abs(au["C_判定"]["初始止损"][0] - 95.0) < 1e-9)
    ck("报告写了 95.0", "95.0" in BOTH)
    trail = [x["止损价"] for x in au["D_判定"]["轨迹明细"]]
    ck("上移序列=96.9/98.8/100.7",
       abs(trail[0] - 96.9) < 1e-9 and abs(trail[1] - 98.8) < 1e-9 and abs(trail[2] - 100.7) < 1e-9,
       json.dumps(trail))
    ck("报告写了 96.9/98.8/100.7", all(s in BOTH for s in ("96.9", "98.8", "100.7")))
    ck("上移次数=3", len(trail) == 3, str(len(trail)))
    rs = au["D_判定"]["重启"]
    ck("重启后止损保持", rs["重启前止损"] == rs["重启后止损"], json.dumps(rs["重启后止损"]))
    ck("重启后成交数不变", rs["重启前成交数"] == rs["重启后成交数"] == 1)
    ck("重启后轨迹数不变", rs["重启前轨迹数"] == rs["重启后轨迹数"] == 3)
    ck("D 判定=通过", au["D_判定"]["判定"] == "通过")
    E = au["E_判定"]
    ck("E 判定=通过", E["判定"] == "通过")
    ck("退出原因=trailing_stop_loss", E["退出原因"] == ["trailing_stop_loss"])
    ck("退出价=100.64965", abs(E["退出价"][0] - 100.64965) < 1e-6, str(E["退出价"][0]))
    ck("报告写了 100.64965", "100.64965" in BOTH)
    ck("费用自洽", E["费用是否自洽"] is True)
    ck("entry+exit=合计",
       abs(E["entry_fee"] + E["exit_fee"] - E["成交费合计"]) < 1e-9,
       f'{E["entry_fee"]}+{E["exit_fee"]}={E["成交费合计"]}')
    ck("现金=5001.994129749125", abs(E["账本现金"] - 5001.994129749125) < 1e-9, repr(E["账本现金"]))
    ck("报告写了 5001.994129749125", "5001.994129749125" in BOTH)
    ck("E 幂等=通过", au["E_幂等"]["判定"] == "通过")
    ck("F 重试=通过", au["F_重试"]["判定"] == "通过")
    G = au["G_第二配置"]
    ck("G 判定=通过", G["判定"] == "通过")
    ck("G 挂单身份 s=0.08/d=0.12",
       G["挂单记录的身份"][0]["stoploss_s"] == 0.08 and G["挂单记录的身份"][0]["trail_d"] == 0.12)
    ck("G 持仓身份 s=0.08/d=0.12",
       G["持仓记录的身份"][0]["stoploss_s"] == 0.08 and G["持仓记录的身份"][0]["trail_d"] == 0.12)
    ck("G 初始止损=92.0", abs(G["初始止损(轨迹首行)"] - 92.0) < 1e-9, str(G["初始止损(轨迹首行)"]))
    ck("G 理论=92.092", abs(G["理论初始止损"] - 92.092) < 1e-9)
    ck("G 跟踪后=93.2", abs(G["跟踪后止损"][0] - 93.2) < 1e-9, str(G["跟踪后止损"][0]))
    ck("G 若 d=0.05 会是 100.7", abs(G["若误用 d=0.05 会是"] - 100.7) < 1e-9)
    ck("报告写了 92.0 / 93.2", ("92.0" in BOTH) and ("93.2" in BOTH))

    # 第5项
    ck("注入 9/9", gd["统计"] == {"总": 9, "通过": 9, "不通过": 0}, json.dumps(gd["统计"]))
    ck("报告写了 9/9", "9/9" in BOTH)
    ck("轨迹路由 200", gd["止损轨迹路由"]["HTTP"] == 200)
    ck("不存在持仓 404", gd["止损轨迹路由_不存在持仓"]["HTTP"] == 404)
    reasons = {c["用例"]: c.get("missing_reason") for c in gd["用例"]}
    ck("假K线理由=未登记", any("未在行情库中登记" in (v or "") for k, v in reasons.items() if k.startswith("B ")))
    ck("未来K线理由=future_candle_not_closed",
       any("future_candle_not_closed" in (v or "") for k, v in reasons.items() if k.startswith("C ")))
    ck("错标的理由=symbol_mismatch",
       any("symbol_mismatch_with_position" in (v or "") for k, v in reasons.items() if k.startswith("D2")))
    ck("参数覆盖理由=拒绝覆盖",
       any("拒绝调用方覆盖" in (v or "") for k, v in reasons.items() if k.startswith("F ")))
    ck("缺 trail_d 理由=missing_parameter_trail_d",
       any("missing_parameter_trail_d" in (v or "") for k, v in reasons.items() if k.startswith("G ")))

    ok = sum(1 for _, p, _ in checks if p)
    total = len(checks)
    fails = [{"检查": l, "详情": d} for l, p, d in checks if not p]
    res = {"总": total, "通过": ok, "不通过": total - ok, "失败明细": fails,
           "结论": "全部一致" if ok == total else f"{total - ok} 条不一致",
           # 全量明细：复核者要能看到「查了哪 63 条」，而不只是失败项
           "明细": [{"检查": l, "一致": p, "实测": d} for l, p, d in checks]}
    (OUT / "07_自检.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"自检 {ok}/{total} 一致")
    for f in fails:
        print("  !!", f["检查"], f["详情"])
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
