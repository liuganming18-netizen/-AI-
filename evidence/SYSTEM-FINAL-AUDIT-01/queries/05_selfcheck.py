# -*- coding: utf-8 -*-
"""05 自检：把四关报告与最终交付里引用的每个数字，回到 out/*.json 原始字段复算，
并校验文档里确实写了该值。不一致返回 rc=1。

纪律：报告数字必须能被脚本机械复算；不一致时**改报告**，不改脚本去迁就报告。
产物：out/05_自检.json
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-FINAL-AUDIT-01")
OUT = HERE / "out"

DOCS = ["00_依赖核对与基线.md", "01_A关配置.md", "02_B关风险.md",
        "03_C关自动运行.md", "04_回测一致性专项.md", "05_最终交付.md",
        "07_版本漂移与复验.md", "08_契约测试独立复核.md", "09_冻结版复验.md",
        "10_A关配置_复验.md", "11_B关风险_复验.md", "12_C关自动运行_复验.md",
        "13_D关UI_验收.md"]
BOTH = "\n".join((HERE / d).read_text(encoding="utf-8") for d in DOCS if (HERE / d).exists())


def J(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> int:
    fz = J("00_基线冻结.json")
    A = J("02_A关配置.json")
    B = J("04_B关风险.json")
    C = J("03_C关自动运行.json")
    a = J("03_A.json")
    b = J("03_B.json")

    checks: list[tuple[str, bool, str]] = []

    def ck(label: str, cond: bool, detail: str = "") -> None:
        checks.append((label, bool(cond), str(detail)))

    def has(s: str) -> bool:
        return s in BOTH

    # ── 版本与进程 ──
    ck("冻结文件数=155", fz["本次"]["文件数"] == 155, fz["本次"]["文件数"])
    ck("报告写了 155 文件", has("155 文件"))
    ck("回执 8 条全一致", fz["反重力冻结回执核验"]["声明条数"] == 8 and fz["反重力冻结回执核验"]["一致"] == 8,
       fz["反重力冻结回执核验"]["判定"])
    ck("报告写了 8/8", has("8/8"))
    ck("相对AUDIT-05变动=1", fz["与AUDIT05基准比对"]["变动条数"] == 1,
       fz["与AUDIT05基准比对"]["变动条数"])
    ck("变动文件=paper_loop.py",
       fz["与AUDIT05基准比对"]["明细"][0]["文件"] == "框架/backend/paper_loop.py",
       fz["与AUDIT05基准比对"]["明细"][0]["文件"])
    ck("报告写了 154 文件一致", has("154 文件"))
    ck("8787 启动=23:13:40", fz["运行进程"]["启动时刻"][11:19] == "23:13:40",
       fz["运行进程"]["启动时刻"])
    ck("报告写了 23:13:40", has("23:13:40"))
    ck("8787 pid=40532", fz["运行进程"]["pid"] == 40532, fz["运行进程"]["pid"])
    ck("进程是否最新=False", fz["进程是否最新"] is False, fz["进程是否最新"])

    # ── A 关 ──
    s1, s2, s3 = A["场景"][0], A["场景"][1], A["场景"][2]
    ck("S1 静默接受", s1["结论"].startswith("静默接受"), s1["结论"])
    ck("S1 source=fallback", s1["approved_params"]["source"] == "fallback_frozen_v2_2_conservative",
       s1["approved_params"]["source"])
    ck("S1 hold=720", s1["approved_params"]["max_holding_hours"] == 720,
       s1["approved_params"]["max_holding_hours"])
    ck("S1 cap=0.03", abs(s1["approved_params"]["portfolio_initial_stop_risk_cap"] - 0.03) < 1e-12,
       s1["approved_params"]["portfolio_initial_stop_risk_cap"])
    ck("S1 fingerprint=NULL", s1["挂单落库"][0]["config_fingerprint"] is None,
       s1["挂单落库"][0]["config_fingerprint"])
    ck("S2 静默接受", s2["结论"].startswith("静默接受"), s2["结论"])
    ck("S2 source 自称 frozen_view", str(s2["approved_params"]["source"]).startswith("frozen_view"),
       s2["approved_params"]["source"])
    ck("S2 hold=720", s2["approved_params"]["max_holding_hours"] == 720,
       s2["approved_params"]["max_holding_hours"])
    ck("S3 拒绝", s3["结论"].startswith("拒绝"), s3["结论"])
    ck("报告写了 720", has("720"))
    ck("订单层无身份", A["判定"]["A2_身份贯穿"]["订单"] is False, A["判定"]["A2_身份贯穿"])
    ck("报告写了 paper_order 无身份", has("`paper_order`（订单）") or has("订单层是断的"))

    # ── B 关 ──
    qtys = [u["数量"] for u in B["用例"]]
    ck("B 数量=4.995/4.995/4.166/1.666",
       [round(q, 3) for q in qtys] == [4.995, 4.995, 4.166, 1.666], qtys)
    ck("B 止损全为 95.0", all(abs(u["初始止损"] - 95.0) < 1e-9 for u in B["用例"]),
       [u["初始止损"] for u in B["用例"]])
    ratios = [round(u["实际/预算"], 3) for u in B["用例"]]
    ck("B 风险比=1.019/1.019/0.850/0.340", ratios == [1.019, 1.019, 0.85, 0.34], ratios)
    ck("B 分叉判定=存在", B["判定"]["分叉判定"].startswith("存在"), B["判定"]["分叉判定"])
    ck("报告写了 0.340x", has("0.340x"))
    ck("报告写了 1.019x", has("1.019x"))

    # ── C 关 ──
    ck("统一入口不通过", C["统一入口核对"]["结论"] == "不通过", C["统一入口核对"]["结论"])
    ck("三脚本全零命中", all(v == [] for v in C["统一入口核对"]["启动脚本命中"].values()),
       C["统一入口核对"]["启动脚本命中"])
    ca, cb = C["用例"][0], C["用例"][1]
    # 01:27 版的 C 关数值已不可复算（原始 JSON 被 01:49 冻结版复跑覆盖，该版代码亦已不存在）
    # ⇒ 按脚本纪律「改报告不改脚本」：报告已声明降级，这里只校验该声明存在 + 覆盖事实
    ck("03_A 现为冻结版复跑（成交 0）", ca["成交"] == [], len(ca["成交"]))
    ck("03_B 现为冻结版复跑（成交 0）", cb["成交"] == [], len(cb["成交"]))
    doc03 = (HERE / "03_C关自动运行.md").read_text(encoding="utf-8")
    ck("03 报告已声明证据覆盖", "证据覆盖披露" in doc03)
    ck("03 报告已把旧数值降级为不可复算", "不可复算" in doc03)
    for n in ("03_A_冻结版.json", "03_B_冻结版.json", "03_C关自动运行_冻结版.json"):
        ck(f"冻结版产物另存 {n}", (OUT / n).exists())

    # ── 我方探针零推进（脚本静态断言）──
    src = (HERE / "queries/03_gateC_run.py").read_text(encoding="utf-8")
    banned = ["advance_pending(", "advance_position_trailing(", "check_position_exit_on_bar(",
              "close_at_price(", "record_stop_update(", "execute_cycle("]
    ck("我方探针 0 次推进方法", all(x not in src for x in banned),
       [x for x in banned if x in src])

    # ── 版本漂移与 A 关复验（07 号文件）──
    DR = J("00b_漂移复采.json")
    A2 = J("02b_A关复验.json")
    ck("漂移条数=4", DR["漂移条数"] == 4, DR["漂移条数"])
    ck("漂移含 4 个后端文件",
       {d["文件"] for d in DR["漂移"]} == {"框架/backend/live_api.py", "框架/backend/live_store.py",
                                        "框架/backend/paper_engine.py", "框架/backend/paper_loop.py"},
       [d["文件"] for d in DR["漂移"]])
    ck("报告写了 571e1929a1eb", has("571e1929a1eb"))
    ck("报告写了 d168797055bc", has("d168797055bc"))
    ck("复验 S1/S2/S3 全部无挂单",
       all(c["挂单落库"] == [] for c in A2["场景"][:3]),
       [c["挂单落库"] for c in A2["场景"][:3]])
    ck("复验 S1 理由=config_file_not_found",
       "config_file_not_found" in A2["场景"][0]["结论"], A2["场景"][0]["结论"][:80])
    ck("复验 S2 理由=missing_required_parameters",
       "missing_required_parameters" in A2["场景"][1]["结论"], A2["场景"][1]["结论"][:80])
    ck("复验四表均有身份列",
       all(A2["四层身份列"][t]["有stoploss_s"] for t in
           ("paper_pending", "paper_order", "paper_fill", "paper_position")),
       {t: v["有stoploss_s"] for t, v in A2["四层身份列"].items()})
    ck("复验 A2 判定=通过", A2["判定"]["A2_身份贯穿"]["结论"] == "通过",
       A2["判定"]["A2_身份贯穿"]["结论"])
    # B 关分叉：01:27 版不通过，11:49 版已消除（结论翻转，断言同步改语义）
    pe = (pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/框架/backend/paper_engine.py")
          .read_text(encoding="utf-8"))
    ck("stop_atr_mult 仍传入但对 FT-LOW4H 不生效（走 stoploss_s 分支）",
       "stop_atr_mult=stop_mult" in pe)
    ck("止损计算已移出 paper_engine（统一到 apply_order_rounding）",
       "1.0 - s_val" not in pe)
    ck("11:49 复验：分叉已消除", "分叉已消除" in BOTH)
    ck("生产四臂哈希已变（非我方写入）",
       all(DR["生产四臂"][k]["sha256"] != v for k, v in
           zip(("paper_3a", "paper_3b", "paper_3c", "paper_3d"),
               ("b7dd1971277d2d50", "c38b2cad7630ef6f", "5edfa35e9ad259ea", "23446394d4a6cdbc"))),
       {k: v["sha256"] for k, v in DR["生产四臂"].items()})
    ck("报告写了 F-09", has("F-09"))

    # ── 契约测试独立复核（08 号文件）──
    R1 = J("08b_契约测试_复跑.json")
    S1 = J("08_契约测试_运行前SHA.json")
    raw = (OUT / "08_契约测试_运行输出.txt").read_text(encoding="utf-8")
    ck("v1 执行 RC=1", "RC=1" in raw, raw.splitlines()[0] if raw else "")
    ck("v1 崩在 no such table: funding_history", "no such table: funding_history" in raw)
    ck("v2 复跑 RC=0", str(R1["RC"]) == "0", R1["RC"])
    ck("v2 六项全通过", R1["stdout"].count("【通过】") == 6, R1["stdout"].count("【通过】"))
    ck("v2 第6项确实通过", "6. 既有持仓退出保护核验通过" in R1["stdout"])
    # 两遍执行的后端代码必须完全相同（差异只能来自测试夹具）
    back = ("框架/backend/live_store.py", "框架/backend/paper_engine.py",
            "框架/backend/paper_loop.py", "框架/backend/strategy_contracts.py",
            "框架/backend/live_api.py")
    ck("两遍执行后端 5 个 SHA 完全一致",
       all(S1["sha256"][f] == R1["sha256"][f] for f in back),
       [f for f in back if S1["sha256"][f] != R1["sha256"][f]])
    ck("两遍测试文件 SHA 不同（v1≠v2）",
       S1["sha256"]["evidence/SYSTEM-CONTRACT-01/test_system_contract_01.py"]
       != R1["sha256"]["evidence/SYSTEM-CONTRACT-01/test_system_contract_01.py"])
    ck("报告写了 v1 SHA 1e8cf713c049", has("1e8cf713c049"))
    ck("报告写了 v2 SHA b25c48a09720", has("b25c48a09720"))
    # 套件内直接调引擎推进方法的次数（机械计数，不手抄）
    tsrc = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/"
                        "evidence/SYSTEM-CONTRACT-01/test_system_contract_01.py"
                        ).read_text(encoding="utf-8")
    import re as _re
    n_engine = len(_re.findall(
        r"PE\.(_register_pending|advance_pending|advance_position_trailing|"
        r"check_position_exit_on_bar|close_at_price|record_stop_update)\(", tsrc))
    ck("套件直接调推进方法=7 处", n_engine == 7, n_engine)
    ck("报告写了 7 处", "共 7 处" in BOTH)
    ck("套件第1项为恒真断言", 'p_info["unifiable"] is False' in tsrc)
    ck("套件夹具 slippage 非冻结(0.0005)", '"slippage_each_side": 0.0005' in tsrc)
    ck("套件已补 funding_history 表", "CREATE TABLE IF NOT EXISTS funding_history" in tsrc)
    ck("07 记了 01:43:11 漂移", "01:43:11" in BOTH)
    ck("08 撤回旧容差结论", "此项撤回" in BOTH)

    # ── 冻结回执核验 + 冻结版复验（09 号文件）──
    RC = J("09_契约冻结回执核验.json")
    ck("回执条数=10", RC["条数"] == 10, RC["条数"])
    ck("回执 10/10 一致", RC["一致"] == RC["条数"], RC["一致"])
    ck("回执含 5 个后端文件", sum(1 for d in RC["明细"]
        if d["文件"].startswith("框架/backend/")) == 5)
    ck("09 写了 paper_engine 冻结 SHA", "d26c535c8c4c" in BOTH)
    ck("09 写了 paper_loop 冻结 SHA", "571e1929a1eb" in BOTH)
    # B 关：从 out/04 复算四档，不看报告抄写
    B4 = J("04_B关风险.json")
    ck("B 关四档全部 filled（干净测量）",
       all(u["open_position_reason"] == "filled" for u in B4["用例"]),
       [u["open_position_reason"] for u in B4["用例"]])
    ck("B 关初始止损四档恒 95.0",
       {u["初始止损"] for u in B4["用例"]} == {95.0},
       sorted({u["初始止损"] for u in B4["用例"]}))
    ck("B 关数量四档=4.995/4.995/4.166/1.666",
       [u["数量"] for u in B4["用例"]] == [4.995, 4.995, 4.166, 1.666],
       [u["数量"] for u in B4["用例"]])
    ck("B 关实际/预算四档",
       [round(u["实际/预算"], 6) for u in B4["用例"]] == [1.01898, 1.01898, 0.849864, 0.339864],
       [round(u["实际/预算"], 6) for u in B4["用例"]])
    ck("B 关判定=存在分叉", "存在" in B4["判定"]["分叉判定"], B4["判定"]["分叉判定"][:40])
    # C 关：两用例成交 0 且被新因果规则拒绝
    REJ = J("09c_C关拒绝证据.json")
    for case in ("A", "B"):
        u = REJ["用例"][case]
        ck(f"C 关用例{case} 成交 0 行", u["position行数"] == 0 and u["fill行数"] == 0,
           [u["position行数"], u["fill行数"]])
        ck(f"C 关用例{case} 拒绝理由=missed_fill_moment_no_backfill",
           u["pending"][0]["reason"] == "missed_fill_moment_no_backfill",
           u["pending"][0]["reason"])
        ck(f"C 关用例{case} 挂单带配置身份",
           u["pending"][0]["stoploss_s"] is not None and u["pending"][0]["config_fingerprint"],
           [u["pending"][0]["stoploss_s"], u["pending"][0]["config_fingerprint"]])
    ck("C 关 A 用例时间差=3853616ms", REJ["用例"]["A"]["created_at减earliest_fill_ms"] == 3853616,
       REJ["用例"]["A"]["created_at减earliest_fill_ms"])
    ck("09 写了 3,853,616", "3,853,616" in BOTH)
    ck("09 判 C 关为未验证", "未验证" in BOTH)
    # 生产四库身份列：3 列齐备，config_source 只在 paper_order
    P4 = J("09b_生产四库身份列.json")
    t4 = ("paper_pending", "paper_order", "paper_fill", "paper_position")
    ck("生产四库 stoploss_s/trail_d/config_fingerprint 全齐",
       all(P4[a]["表列"][t][c] for a in ("3a", "3b", "3c", "3d") for t in t4
           for c in ("stoploss_s", "trail_d", "config_fingerprint")))
    ck("生产 config_source 只在 paper_order",
       all(P4[a]["表列"]["paper_order"]["config_source"]
           and not P4[a]["表列"]["paper_fill"]["config_source"] for a in ("3a", "3b", "3c", "3d")))
    ck("09 指出 paper_fill 无 config_source", "`paper_fill` 没有" in BOTH)

    # ══════════════════════════════════════════════════════════════════
    # 11:49 四关复验（out/10~18 + 10~13 号报告）
    # ══════════════════════════════════════════════════════════════════
    A2 = J("15_A关复验.json")
    B2 = J("11_B关纯函数复验.json")
    B3 = J("16_B关风险重算.json")
    C2 = J("13_C关真进程重启.json")
    D2 = J("17_D关截图去重.json")
    S = J("14_参数敏感度.json")
    L10 = J("10_四套件清单核验.json")

    # ── 四套件清单核验 ──
    ck("CONTRACT 清单 10 条", L10["套件"]["SYSTEM-CONTRACT-01"]["条数"] == 10)
    ck("CONTRACT 仅 7 条一致", L10["套件"]["SYSTEM-CONTRACT-01"]["一致"] == 7,
       L10["套件"]["SYSTEM-CONTRACT-01"]["一致"])
    ck("RISK 清单不含后端代码", L10["套件"]["SYSTEM-RISK-01"]["含后端代码行"] == 0)
    ck("RUNTIME 清单不含后端代码", L10["套件"]["SYSTEM-RUNTIME-01"]["含后端代码行"] == 0)
    ck("UI 清单不含后端代码", L10["套件"]["SYSTEM-UI-01"]["含后端代码行"] == 0)
    ck("报告写了无固定版本", has("当前没有可核验的固定版本"))

    # ── A 关 ──
    a1 = A2["判定"]
    ck("A1 缺视图被拒", a1["A1_缺视图被拒"] is True)
    ck("A1 缺stops被拒", a1["A1_缺stops被拒"] is True)
    ck("A1 s=0 被拒", a1["A1_s为0被拒"] is True)
    ck("A1 d=0 被拒", a1["A1_d为0被拒"] is True)
    ck("A1 好配置能登记", a1["A1_好配置能登记"] is True)
    ck("A2 四层有 s/d/指纹", a1["A2_四层有s_d_指纹"] is True)
    ck("A3 s 覆盖被拒", a1["A3_s覆盖被拒"] is True)
    ck("A3 trail_d 未被覆盖", a1["A3_trail_d未被客户端覆盖"] is True)
    ck("A3 stop_price 污染 pending",
       A2["A3_客户端覆盖"][2]["落库实际值"]["paper_pending"]["initial_stop_price"] == 1.0)
    ck("C3 深挖成交止损=95.0",
       abs(A2["C3深挖"]["成交后持仓"]["initial_stop_price"] - 95.0) < 1e-9)
    ck("C3 深挖实际风险=25.0",
       abs(A2["C3深挖"]["实际风险_止损距离x数量"] - 25.0) < 1e-2)
    ck("报告写了 A 关通过", "A 关：通过" in BOTH)
    ck("报告写了缺陷 A-1", "缺陷 A-1" in BOTH)

    # ── B 关 ──
    stops = {u["stop"] for u in B2["用例"]}
    qtys = {u["qty"] for u in B2["用例"]}
    ck("B 关只改 ATR 止损唯一", len(stops) == 1, stops)
    ck("B 关只改 ATR 数量唯一", len(qtys) == 1, qtys)
    ck("B 关止损=95.0", stops == {95.0}, stops)
    ck("B 关数量=4.901", qtys == {4.901}, qtys)
    ck("B 关风险比预算=0.9998",
       abs(B2["用例"][0]["风险比预算"] - 0.9998) < 1e-4)
    rb = {r["配置"]: r["实际/预算"] for r in B3["重算"]}
    ck("B 关 A 组 0.9048", abs(rb["A_frozen"] - 0.9048) < 1e-3, rb["A_frozen"])
    ck("B 关 B 组 0.9651", abs(rb["B_frozen"] - 0.9651) < 1e-3, rb["B_frozen"])
    ck("B 关两组都 ≤1.0", all(v <= 1.0 for v in rb.values()), rb)
    ck("报告写了 B 关通过", "B 关：通过" in BOTH)

    # ── C 关 ──
    cm = {u["配置"]: u for u in C2["用例"]}
    ck("C 两组 stage1 均 SIGKILL(-9)",
       all(u["阶段"][0]["返回码"] == -9 for u in C2["用例"]))
    ck("C 两组 stage2 均正常退出",
       all(u["阶段"][1]["返回码"] == 0 for u in C2["用例"]))
    ck("C 两组均最终退出",
       all(u["最终"]["持仓"]["status"] == "closed" for u in C2["用例"]))
    ck("C A 组上移 2 次", cm["A_frozen"]["最终"]["跟踪上移次数"] == 2,
       cm["A_frozen"]["最终"]["跟踪上移次数"])
    ck("C B 组上移 1 次", cm["B_frozen"]["最终"]["跟踪上移次数"] == 1,
       cm["B_frozen"]["最终"]["跟踪上移次数"])
    ck("C A 组现金 5001.884087279",
       abs(cm["A_frozen"]["最终"]["现金"] - 5001.884087279) < 1e-9)
    ck("C B 组现金 4978.9299300344",
       abs(cm["B_frozen"]["最终"]["现金"] - 4978.9299300344) < 1e-9)
    ck("C A 组费用自洽", cm["A_frozen"]["闭合校验"]["手续费总和=账本"] is True)
    ck("C A 组现金闭合", cm["A_frozen"]["闭合校验"]["现金=引擎初始权益+已实现"] is True)
    ck("C 两组视图权益≠引擎权益（A-1 显影）",
       all(u["闭合校验"]["视图声明权益=引擎初始权益"] is False for u in C2["用例"]))
    ck("C 幂等：stage2 无新增路由",
       all("routed=0" in u["阶段"][1]["stdout"] for u in C2["用例"]))
    ck("报告写了 C0 不通过", "C0" in BOTH and "不通过" in BOTH)
    ck("报告写了回填假设被否定", "假设不成立" in BOTH)
    ck("报告写了 bar_after_attempt_clock_no_lookahead",
       "bar_after_attempt_clock_no_lookahead" in BOTH)

    # ── D 关 ──
    ck("D 截图 16 张", D2["截图总数"] == 16)
    ck("D 去重后 10 张", D2["去重后"] == 10, D2["去重后"])
    ck("D 重复组 3 组", len(D2["重复组"]) == 3, len(D2["重复组"]))
    ck("D 无独立证据 6 项", len(D2["无独立证据的视图"]) == 6)
    ck("D 报告写了 10 张不同", "只有 10 张不同" in BOTH or "10 张" in BOTH)
    ck("D 报告写了 run_sep50_001 不存在", "run_sep50_001" in BOTH)
    ck("D 报告写了 95,000 与截图不符", "95,000" in BOTH)
    ck("D 关判定不通过", "D 关：不通过" in BOTH)

    # ── 参数敏感度 ──
    ck("S initial_equity 不改变数量", S["判定"]["initial_equity 是否改变数量"] is False)
    ck("S initial_equity 不进 equity()",
       S["判定"]["视图 initial_equity_USDT 是否进入 equity()"] is False)
    ck("S risk_per_trade 改变数量", S["判定"]["risk_per_trade 是否改变数量"] is True)
    ck("S single_name 改变数量", S["判定"]["single_name 是否改变数量"] is True)
    ck("S participation 改变数量", S["判定"]["participation 是否改变数量"] is True)

    # ── 最终交付五项 ──
    ck("交付写了不可交付日常部署", "否。" in BOTH and "是否可交付日常部署验证" in BOTH)
    ck("交付写了 U-1~U-7", "U-7" in BOTH)
    ck("交付写了自我披露", "我这一轮自己犯的错" in BOTH)

    ok = sum(1 for _, p, _ in checks if p)
    total = len(checks)
    fails = [{"检查": l, "详情": d} for l, p, d in checks if not p]
    res = {"总": total, "通过": ok, "不通过": total - ok, "失败明细": fails,
           "结论": "全部一致" if ok == total else f"{total - ok} 条不一致",
           "明细": [{"检查": l, "一致": p, "实测": d} for l, p, d in checks]}
    (OUT / "05_自检.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"自检 {ok}/{total} 一致")
    for f in fails:
        print("  !!", f["检查"], f["详情"])
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
