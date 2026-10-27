# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-C｜05 统一指标字典、四类结果规范与依赖登记

任务第 5 条：
    「准备统一指标和四类结果：两者都入场、仅原策略、仅新策略、都未入场。」

本脚本不跑任何新成绩，只把「要算什么、怎么算、现在能不能算、缺什么」钉死，
让依赖到齐后可以直接执行，而不需要再讨论口径。

同时做两件必须做的事：
1. 依赖状态**当场探测**（05-B / 05-D / 05-F 是并行窗口的产物，随时可能到货，
   所以必须记录探测时刻，不能写死「未交付」）；
2. 把「口径冲突」里新发现的一种情况登记出来：05-A 决策 5 推荐的成本口径是
   **混搭**（滑点取生效视图 5bps + 风险与参与率取策略包），
   而 `replay_calc.CALIBERS` 只有两套完整口径，**没有任何一套等于这个混搭**。
   若用户批准该推荐，必须先新增第三套口径，否则无法执行 —— 这不是本单能自己决定的。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"

DEPENDENCIES = {
    "05-B": "evidence/ACCUM-EARLY-05-B",
    "05-D": "evidence/ACCUM-EARLY-05-D",
    "05-F": "evidence/ACCUM-EARLY-05-F",
}


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dep_status(name: str) -> str:
    """依赖状态统一判定：有 README 才算「已交付」，否则按文件数分「部分 / 未到」。

    不能只看文件数 —— 并行窗口会先落脚本和中间产物，最后才写交付说明。
    只看文件数会把「还没跑完」误报成「部分交付」。
    """
    d = DEPS_CACHE.get(name) or {}
    if d.get("has_readme"):
        return "已交付"
    n = d.get("file_count", 0)
    if n == 0:
        return "未到（目录为空）"
    return f"部分（{n} 个文件，无交付说明）"


def dep_evidence(name: str) -> str:
    d = DEPS_CACHE.get(name) or {}
    return (f"探测时刻 {d.get('probed_at_utc')}：目录存在={d.get('exists')}，"
            f"文件 {d.get('file_count', 0)} 个，有 README={d.get('has_readme')}"
            + (f"，最新产物 {d.get('newest_json')}" if d.get("newest_json") else ""))


DEPS_CACHE = {}


def probe_deps():
    out = {}
    for name, rel in DEPENDENCIES.items():
        p = ROOT / rel
        rec = {"dir": rel, "exists": p.exists(), "probed_at_utc": iso_now()}
        if p.exists():
            files = sorted(q for q in p.rglob("*") if q.is_file())
            rec["file_count"] = len(files)
            rec["files"] = [str(q.relative_to(ROOT)) for q in files][:40]
            rec["has_readme"] = (p / "README.md").exists()
            outs = [q for q in files if q.suffix == ".json"]
            rec["json_outputs"] = [str(q.relative_to(ROOT)) for q in outs][:20]
            if outs:
                newest = max(outs, key=lambda q: q.stat().st_mtime)
                rec["newest_json"] = str(newest.relative_to(ROOT))
                rec["newest_json_mtime_utc"] = datetime.fromtimestamp(
                    newest.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out[name] = rec
    DEPS_CACHE.update(out)
    return out


def main():
    import replay_calc as RC

    d01 = json.loads((OUT_DIR / "01_输入快照与版本锁定.json").read_text(encoding="utf-8"))
    d02 = json.loads((OUT_DIR / "02_逐事件基线.json").read_text(encoding="utf-8"))
    d03 = json.loads((OUT_DIR / "03_逐事件与组合口径分离.json").read_text(encoding="utf-8"))
    d04 = json.loads((OUT_DIR / "04_统一观察母集.json").read_text(encoding="utf-8"))

    out = {
        "task": "ACCUM-EARLY-05-C",
        "probe": "05_指标字典与依赖登记",
        "generated_at_utc": iso_now(),
        "metric_dictionary": {},
        "four_quadrants": {},
        "dependencies": probe_deps(),
        "caliber_issue": {},
        "blocking_register": [],
        "execution_gate": {},
    }

    # ── 一、统一指标字典 ──────────────────────────────────────────────
    # 每个指标必须写清：算什么、用什么数据、现在能不能算、算出来属于哪一层
    MD = out["metric_dictionary"]

    MD["M01_提前时长"] = {
        "定义": "原策略入场时刻 − 新策略入场时刻",
        "单位": "小时 / 15m 根数",
        "数据依赖": "两套策略在同一母集事件上的入场时刻",
        "现在可算": False,
        "缺什么": "05-B 实现 + 用户批准配置（05-A 五项决策待批）",
        "所属层": "L1 逐事件",
    }
    MD["M02_入场价格变化率"] = {
        "定义": "(新策略入场价 − 原策略入场价) / 原策略入场价 × 100%",
        "单位": "%",
        "数据依赖": "同上",
        "现在可算": False,
        "缺什么": "同上",
        "所属层": "L1 逐事件",
        "注意": "两套策略的入场价都必须是「成交价」（含滑点并过取整），不是信号价；"
                "取整规则唯一实现 = 框架/backend/replay_calc.py 调用的 trading_core/fill.py。",
    }
    MD["M03_成交与拒单"] = {
        "定义": "成交笔数 / 拒单笔数 / 拒单原因分布",
        "单位": "笔",
        "数据依赖": "replay_calc.plan_entry 的返回状态",
        "现在可算": "原策略可算（已算）",
        "缺什么": "新策略侧需 05-B",
        "所属层": "L1 逐事件",
        "已得": {cal: {"executed": b["counts"]["executed"], "rejected": b["counts"]["rejected"]}
                 for cal, b in d02["calibers"].items()},
    }
    MD["M04_逐事件净收益"] = {
        "定义": "realized_net = (退出成交价 − 入场成交价) × 数量 − 双边手续费 − 资金费",
        "单位": "USDT",
        "数据依赖": "15m 成交、4h 跟踪止损、资金费结算、合约规格",
        "现在可算": "原策略可算（已算，见 02）",
        "缺什么": "新策略侧需 05-B",
        "所属层": "L1 逐事件",
        "禁止": "不得把这个数除以初始本金当账户收益率；不得逐笔累加后称组合收益。",
    }
    MD["M05_累加净额"] = {
        "定义": "Σ 逐事件 realized_net（同口径、同母集内）",
        "单位": "USDT",
        "数据依赖": "M04",
        "现在可算": "原策略可算（已算）",
        "缺什么": "新策略侧需 05-B",
        "所属层": "L1 求和（合法用法：同口径横向比较）",
        "已得": {cal: b["sum_realized_net_of_closed"] for cal, b in d02["calibers"].items()},
        "禁止": "不得称「总收益」「账户收益」「净值变化」。它是逐事件求和。",
    }
    MD["M06_费用与资金费"] = {
        "定义": "双边手续费合计 / 资金费结算合计",
        "单位": "USDT",
        "数据依赖": "费率口径（5bps 手续费固定；滑点两套冲突）、funding_history",
        "现在可算": "原策略可算（已算）",
        "缺什么": "新策略侧需 05-B；滑点口径待批",
        "所属层": "L1 逐事件",
        "已得": {cal: {"fees": b["sum_fees"], "funding": b["sum_funding_cost"]}
                 for cal, b in d02["calibers"].items()},
    }
    MD["M07_假突破损失"] = {
        "定义": "第三象限（仅新策略入场）中净亏损事件的净额求和",
        "单位": "USDT",
        "数据依赖": "新策略入场判定 + 原策略未入场判定 + M04",
        "现在可算": False,
        "缺什么": "05-B + 批准配置",
        "所属层": "L1 按象限分组",
    }
    MD["M08_机会成本"] = {
        "定义": "第二象限（仅原策略入场）中原策略盈利事件的净额求和",
        "单位": "USDT",
        "数据依赖": "同上",
        "现在可算": False,
        "缺什么": "05-B + 批准配置",
        "所属层": "L1 按象限分组",
    }
    MD["M09_未成交与期末持仓"] = {
        "定义": "拒单事件单独列示；期末未平仓只计未实现浮动盈亏，不并入已实现",
        "单位": "笔 / USDT",
        "数据依赖": "replay_calc 的 entry 状态与 exit.hit",
        "现在可算": "原策略可算（已算）",
        "缺什么": "新策略侧需 05-B",
        "所属层": "L1 逐事件",
        "已得": {cal: {"open_at_window_end": b["counts"]["open_at_window_end"],
                       "sum_unrealized": b["sum_unrealized_net_of_open"]}
                 for cal, b in d02["calibers"].items()},
    }
    MD["M10_组合最大回撤"] = {
        "定义": "账户权益曲线（真实时间轴、含并发资金与仓位约束）的峰值回撤",
        "单位": "USDT / %",
        "数据依赖": "组合调度规则（同时持仓上限、多信号取舍、权益滚动）+ M04",
        "现在可算": False,
        "缺什么": [
            "被批准的并发上限（3 仓 vs 10 仓两套说法）",
            "被批准的多信号取舍规则（无任何文件规定）",
            "被批准的被挤掉候选处理方式（丢弃/顺延/部分成交）",
        ],
        "所属层": "L3 组合账户",
        "状态": "UNVERIFIED",
        "禁止": "不得用 04-C 的 346.235886 USDT / 6.761% 代替 —— 那是顺序累加伪回撤，见 03。",
        "为什么不拿它当「0」": "「没算」不等于「没有回撤」。写 0 是造假，只能写未验证。",
    }
    MD["M11_观察事件覆盖"] = {
        "定义": "统一观察母集的事件数与阶段分布（含未成候选）",
        "单位": "个",
        "数据依赖": "冻结状态机 + 冷热合并 1h/4h",
        "现在可算": "已算（见 04）",
        "缺什么": "—",
        "所属层": "母集定义（不属盈亏）",
        "已得": d04["summary"],
    }

    # ── 二、四类结果 ─────────────────────────────────────────────────
    qr = d04["quadrant_readiness"]
    out["four_quadrants"] = {
        "定义口径": {
            "全集": "统一观察母集里的每一个观察事件（不是只算已成候选的）",
            "原策略入场": "该事件在母集重放中走到 CANDIDATE（并可成交）",
            "新策略入场": "该事件在 05-B 实现下满足获批的提前入场条件（并可成交）",
            "注意": "「入场」必须指真的成交（过最小下单量、过参与率），"
                    "不是「产生信号」。拒单不算入场。",
        },
        "Q1_两者都入场": {
            "判定": "原策略入场 且 新策略入场",
            "要算": ["M01 提前时长", "M02 入场价差", "M04 双方净额差", "M06 费用差",
                     "退出原因分布差"],
            "现在可实例化": qr["Q1_两者都入场"]["可实例化"],
            "阻塞": qr["Q1_两者都入场"]["原因"],
            "备注": "同事件两笔不能相加当「组合收益」——它们是同一机会的两种做法，互斥。",
        },
        "Q2_仅原策略入场": {
            "判定": "原策略入场 且 新策略未入场（含：条件不满足 / 被拒单 / 未成交）",
            "要算": ["M08 机会成本（原策略盈利部分）", "新策略未入场的具体原因分类"],
            "现在可实例化": qr["Q2_仅原策略入场"]["可实例化"],
            "阻塞": qr["Q2_仅原策略入场"]["原因"],
        },
        "Q3_仅新策略入场": {
            "判定": "新策略入场 且 原策略未入场",
            "要算": ["M07 假突破损失", "退出原因分布", "亏损笔数占比"],
            "现在可实例化": qr["Q3_仅新策略入场"]["可实例化"],
            "阻塞": qr["Q3_仅新策略入场"]["原因"],
            "候选池已备": {
                "停在RETEST未突破": qr["Q3_仅新策略入场"]["原因"] and
                                    d04["quadrant_readiness"]["Q4_都未入场"]["细分"]["停在RETEST"],
                "说明": "这是新策略唯一能新增机会的来源；本单只备池，不判定谁能进。",
            },
        },
        "Q4_都未入场": {
            "判定": "原策略未入场 且 新策略未入场",
            "要算": ["噪声过滤比例", "各停靠阶段分布", "观察时长分布"],
            "现在可实例化": "一半（原策略未入场已确认）",
            "已得": qr["Q4_都未入场"],
            "阻塞": "「新策略未入场」仍需 05-B；所以现在只能叫「原策略未入场」，不能叫「都未入场」。",
        },
        "结论": qr["结论"],
    }

    # ── 三、口径冲突（含新发现的混搭口径问题）────────────────────────
    a = RC.CALIBERS["生效视图_5bps"]
    b = RC.CALIBERS["策略包_3bps"]
    # 05-A 决策 5 推荐：滑点取生效视图、风险与参与率取策略包
    hybrid = {
        "slippage_each_side": a["slippage_each_side"],          # 5bps
        "risk_per_trade_fraction": b["risk_per_trade_fraction"],  # 0.5%
        "initial_equity": b["initial_equity"],                    # 2000
        "single_name_notional_cap": b["single_name_notional_cap"],  # 30%
        "gross_long_notional_cap": b["gross_long_notional_cap"],    # 90%
        "participation_cap": b["participation_cap"],              # 0.1%
    }
    matches = []
    for name, cfg in RC.CALIBERS.items():
        same = all(abs(float(cfg[k]) - float(v)) < 1e-12 for k, v in hybrid.items())
        matches.append({"caliber": name, "identical": same})
    out["caliber_issue"] = {
        "05A决策5推荐的混搭口径": hybrid,
        "现有口径是否等于它": matches,
        "结论": ("没有任何一套现有口径等于 05-A 决策 5 的推荐混搭。"
                 "若用户批准该推荐，必须先新增第三套口径（或明确改哪一套），"
                 "否则「按批准口径跑」无法执行 —— 本单不自行新增。"
                 if not any(m["identical"] for m in matches)
                 else "已有一套口径与之相同。"),
        "另一处必须说明": "CALIBERS 里没有 max_active_positions 字段 ⇒ "
                          "replay_calc 的单事件回放**根本不含仓位数量约束**。"
                          "这从代码层面证实了 03 的结论：逐事件回放做不出组合回撤。",
        "两套口径的原始出处": {name: cfg.get("来源") for name, cfg in RC.CALIBERS.items()},
    }

    # ── 四、阻塞登记 ─────────────────────────────────────────────────
    dep = out["dependencies"]
    out["blocking_register"] = [
        {
            "id": "BLK-01",
            "项目": "用户批准配置",
            "状态": "未到",
            "依据": "05-A 五项收敛决策仍标「待批准」；其 README 明确「待用户裁决」。",
            "解除后解锁": ["Q1/Q2/Q3 三象限判定", "新策略侧全部指标", "第三套口径（如批准混搭）"],
        },
        {
            "id": "BLK-02",
            "项目": "05-B 新策略实现",
            "状态": dep_status("05-B"),
            "依据": dep_evidence("05-B"),
            "解除后解锁": ["四象限完整判定"],
        },
        {
            "id": "BLK-03",
            "项目": "05-D 拒单差异与计算版本",
            "状态": dep_status("05-D"),
            "依据": dep_evidence("05-D"),
            "接入结果": "已接入并对撞：计算版本、拒单标的与口径、覆盖口径（101 成交 + 1 拒单）、"
                        "全量净额与分流净额全部对上，见 02 产物的 crosscheck.vs_05D。",
        },
        {
            "id": "BLK-04",
            "项目": "05-F 数据边界",
            "状态": dep_status("05-F"),
            "依据": dep_evidence("05-F"),
            "接入结果": "已接入 05-F 针对本窗口的四层覆盖，见报告第五节。"
                        "区间边界另用 05-A 的《验证区间使用审计》。",
        },
        {
            "id": "BLK-05",
            "项目": "跳空成交模型",
            "状态": "未批",
            "依据": "04-D 已交三模型敏感性对照（历史 51 笔真实跳空 0 笔，极差 0），依据未批。",
            "影响": "当前不影响这 51 笔的数值（无跳空），但影响未来含跳空的样本。",
        },
        {
            "id": "BLK-06",
            "项目": "滑点 3bps / 5bps 与参与率 0.1% / 1.0%",
            "状态": "未批",
            "依据": "两套口径并存；05-A 推荐混搭口径但未批准，且现有代码无该组合。",
        },
    ]

    # ── 五、执行闸门：什么条件下才允许跑正式对照 ─────────────────────
    out["execution_gate"] = {
        "允许跑正式新旧对照的前提（全部满足才跑）": [
            "① 用户明确批准 05-A 的五项决策（或明确指定替代值），并落成可核对的批准文件；",
            "② 05-B 交付完成（有 README、有可执行入口、配置校验通过）；",
            "③ 05-D 的拒单差异与计算版本已核对（或用户明确同意以 04-D 代替）；",
            "④ 若批准的是混搭口径，第三套口径已写入 replay_calc 并被核对；",
            "⑤ 母集与基线在本单的哈希下复跑一致（防止依赖到齐期间代码又变）。",
        ],
        "本单不做的": [
            "不设任何新策略阈值",
            "不跑新策略成绩",
            "不用旧总收益代替新结果",
            "不把逐事件求和包装成组合成绩",
        ],
        "跑之前必须重做的校验": "重跑 01（代码/数据哈希）与 02（基线对撞），"
                                "若哈希变了，本单的所有数字失效，必须重新出。",
    }

    dst = OUT_DIR / "05_指标字典与依赖登记.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✔ 已落盘 {dst.relative_to(ROOT)}")
    print("  依赖:", {k: (v.get("file_count"), v.get("has_readme")) for k, v in dep.items()})
    print("  混搭口径是否已有:", matches)
    print("  阻塞项:", len(out["blocking_register"]))


if __name__ == "__main__":
    main()
