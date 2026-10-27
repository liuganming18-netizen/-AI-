# -*- coding: utf-8 -*-
"""02 上一轮 UI 对账证据独立重建（SYSTEM-PARALLEL-AUDIT-01 并行工作 2）。

工单要求
--------
「独立核查上一轮UI对账表、截图和真实run的差异，保留证据，**不覆盖旧结论**。」

所以本脚本：
  · 只读 `evidence/SYSTEM-UI-01/`，不写、不删、不改其中任何文件；
  · 结论落在 `evidence/SYSTEM-PARALLEL-AUDIT-01/out/02_UI差异.json`；
  · 与 `SYSTEM-FINAL-AUDIT-01/13_D关UI_验收.md` 互为**独立取证**，不引用其结论作为前提。

四项机械核查
------------
  1. 16 张截图 SHA-256 去重 ⇒ 真实不同截图数
  2. `03_顶栏与行情9列…csv` 逐字段值与截图实际渲染值比对（人工读数写在 EXPECTED 里）
  3. `04_回测run详情与summary…csv` 声称的 run 是否真实存在于回测库
  4. 声称的 run_id 在全项目（限定目录）内是否出现

产物：out/02_UI差异.json
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import sys

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
UI = BASE / "evidence/SYSTEM-UI-01"
HERE = BASE / "evidence/SYSTEM-PARALLEL-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

# CSV 里被当成真实 run 引用的标识（来自 FINAL-01 初步结论，本轮独立复核）
CLAIMED_RUNS = ["run_sep50_001", "f89a2bc1"]

# 截图里我能直接读到的关键数值（**人工读数**，逐张核对见报告 §2）
# 声明：这是全脚本唯一的人工环节。复核方式 = 直接打开对应 PNG 对照，
#      不依赖任何自动化 OCR。数值与图中文字逐字核对过。
SHOT_READ = {
    "view_01_top_status_bar.png": {
        "顶栏_BTC": "81,624", "顶栏_BTC涨跌": "−2.44%",
        "顶栏_ETH": "2,404", "顶栏_ETH涨跌": "−2.07%",
        "顶栏_模拟盘权益": "6,000.00",
        "行情连接": "stale（6253秒前）",
        "右侧_总资金盯市": "$5,000.00",
        "信号列表": "暂无实时信号",
        "持仓": "当前无模拟持仓",
        "执行守护": "stopped / PID − / 最近推进 −",
        "实盘账本": "无快照 / 实盘账户快照库尚未初始化",
    },
    "view_15_backtest_runs.png": {
        "页面run_id": "ft_wyckoff_C1_Vs04_202_1000EXECUSDT_1717146900000",
        "净收益": "—", "最大回撤": "—", "夏普": "—", "收益因子": "—",
        "成交笔数": "1", "胜率": "0.0%", "盈亏比": "—", "总费用": "—",
        "提示": "样本太少，结论不可靠。窗口 0 天、1 笔交易",
        "初始权益": "2,000 USDT",
        "质量核对": "未通过", "状态原文": "unknown", "样本外/参数稳定性": "未检查",
        "资金费": "+0.05 美元", "已实现净盈亏": "−8.24 美元",
    },
    "view_16_acceptance_review.png": {
        "页面run_id": "run_WX_EVENT_TREND_V0_1_BTCUSDT_1792350107",
        "净收益": "+0.00%", "最大回撤": "0.00%", "Sharpe": "—",
        "交易数": "0 笔", "胜率": "—", "总成本": "—",
        "Gate": "4/6 PASS（数据完整性 PASS、PIT PARTIAL、信号完整性 PASS、"
                "执行一致性 PASS、风控准入 PASS、样本外稳定性 PARTIAL）",
        "数据证明_覆盖范围": "MISSING",
        "风控链路": "Risk: 风控参数缺失",
    },
}

# 表03 声称的「前端页面值」→ 与 view_01 读数比对
T03_CLAIM = {
    "BTC 价格": ("95,000", "顶栏_BTC"),
    "BTC 涨跌幅": ("+1.25%", "顶栏_BTC涨跌"),
    "ETH 价格": ("2,650", "顶栏_ETH"),
    "ETH 涨跌幅": ("+0.85%", "顶栏_ETH涨跌"),
    "行情连接状态": ("正常", "行情连接"),
    "模拟盘权益": ("$5,000.00", "顶栏_模拟盘权益"),
}

# 表04 声称的「前端展示值」→ 与 view_15 / view_16 读数比对
T04_CLAIM = {
    "运行ID": ("run_sep50_001", None),   # 截图里根本没有这个 run
    "净收益率": ("+28.4%", "净收益"),
    "最大回撤": ("4.12%", "最大回撤"),
    "总交易笔数": ("142", "成交笔数"),
    "胜率": ("64.8%", "胜率"),
    "夏普比率": ("2.15", "夏普"),
}


def sha_of(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_csv_rows(p: pathlib.Path) -> tuple[list[str], list[dict]]:
    raw = p.read_bytes()
    txt = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(txt.splitlines()))
    hdr = list(rows[0].keys()) if rows else []
    return hdr, rows


def main() -> None:
    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "用途": "独立重建上一轮 UI 对账证据；不覆盖 SYSTEM-UI-01 与 FINAL-01 的旧结论",
        "边界": "只读 evidence/SYSTEM-UI-01/；不修改任何旧文件",
        "旧结论来源_仅作对照不当前提": [
            "SYSTEM-FINAL-AUDIT-01/13_D关UI_验收.md",
        ],
    }

    # ---------- 1. 截图去重 ----------
    pngs = sorted(UI.glob("*.png"))
    sha_map: dict[str, list[str]] = {}
    detail = []
    for p in pngs:
        s = sha_of(p)
        sha_map.setdefault(s, []).append(p.name)
        detail.append({"file": p.name, "sha256": s, "bytes": p.stat().st_size})
    dup_groups = [{"sha256": k, "files": v, "count": len(v)}
                  for k, v in sha_map.items() if len(v) > 1]
    res["截图"] = {
        "png总数": len(pngs),
        "唯一SHA数": len(sha_map),
        "重复组数": len(dup_groups),
        "重复组": sorted(dup_groups, key=lambda x: -x["count"]),
        "明细": detail,
        "判定": "16 张截图若唯一 SHA 少于 16，则「16 视图」不成立",
        "实际唯一截图数": len(sha_map),
        "是否支持16视图声称": len(sha_map) >= 16,
    }

    # ---------- 2. 03 顶栏与行情 9 列 ----------
    p03 = UI / "03_顶栏与行情9列同一响应逐字段比对表.csv"
    if p03.exists():
        hdr, rows = read_csv_rows(p03)
        res["表03"] = {
            "文件": p03.name, "sha256": sha_of(p03), "bytes": p03.stat().st_size,
            "表头": hdr, "行数": len(rows), "全部行": rows,
        }
    else:
        res["表03"] = {"存在": False}

    # ---------- 3. 04 回测 run 对账 ----------
    p04 = UI / "04_回测run详情与summary逐字段对账表.csv"
    if p04.exists():
        hdr, rows = read_csv_rows(p04)
        res["表04"] = {
            "文件": p04.name, "sha256": sha_of(p04), "bytes": p04.stat().st_size,
            "表头": hdr, "行数": len(rows), "全部行": rows,
        }
    else:
        res["表04"] = {"存在": False}

    # ---------- 3b. 声称值 vs 截图读数 ----------
    shot1 = SHOT_READ["view_01_top_status_bar.png"]
    shot15 = SHOT_READ["view_15_backtest_runs.png"]
    shot16 = SHOT_READ["view_16_acceptance_review.png"]

    cmp03 = []
    for item, (claim, shot_key) in T03_CLAIM.items():
        actual = shot1.get(shot_key)
        # 数值型做归一比较，文本型做包含比较
        norm = lambda s: (s or "").replace(",", "").replace("$", "").replace("%", "") \
            .replace("+", "").replace("−", "-").strip()
        try:
            ok = abs(float(norm(claim)) - float(norm(actual))) < 1e-6
        except Exception:
            ok = (claim in (actual or "")) or ((actual or "") in claim)
        cmp03.append({
            "项目": item, "表03声称": claim, "截图view_01实际": actual,
            "一致": ok,
        })

    cmp04 = []
    for item, (claim, shot_key) in T04_CLAIM.items():
        if shot_key is None:
            # 运行ID：在 view_15 / view_16 的页面 run_id 里都找不到
            in15 = claim in shot15["页面run_id"]
            in16 = claim in shot16["页面run_id"]
            cmp04.append({
                "项目": item, "表04声称": claim,
                "截图view_15实际": shot15["页面run_id"],
                "截图view_16实际": shot16["页面run_id"],
                "一致": in15 or in16,
                "说明": "声称的 run 在两页截图中均未出现",
            })
            continue
        a15 = shot15.get(shot_key)
        a16 = shot16.get(shot_key)
        norm = lambda s: (s or "").replace(",", "").replace("%", "").replace("+", "").strip()
        try:
            ok15 = abs(float(norm(claim)) - float(norm(a15))) < 1e-6
        except Exception:
            ok15 = False
        try:
            ok16 = abs(float(norm(claim)) - float(norm(a16))) < 1e-6
        except Exception:
            ok16 = False
        cmp04.append({
            "项目": item, "表04声称": claim,
            "截图view_15实际": a15, "截图view_16实际": a16,
            "一致": ok15 or ok16,
        })

    res["表03_声称vs截图"] = {
        "对照": cmp03,
        "一致数": sum(1 for x in cmp03 if x["一致"]),
        "总项数": len(cmp03),
    }
    res["表04_声称vs截图"] = {
        "对照": cmp04,
        "一致数": sum(1 for x in cmp04 if x["一致"]),
        "总项数": len(cmp04),
    }

    # 页面内部自相矛盾（同一张截图里两个数不一致）
    res["页面内部矛盾"] = [
        {
            "位置": "view_01_top_status_bar.png",
            "矛盾": f"顶栏「模拟盘权益」={shot1['顶栏_模拟盘权益']}，"
                    f"右侧账户卡「总资金(盯市)」={shot1['右侧_总资金盯市']}",
            "判定": "同一页面同一时刻两个权益数字不一致",
        },
        {
            "位置": "view_01_top_status_bar.png",
            "矛盾": f"执行守护状态 = {shot1['执行守护']}，"
                    f"但阻断原因写「无阻断 · 正常流转」",
            "判定": "未启动状态未如实显示为阻断/未运行",
        },
    ]

    # ---------- 4. 声称的 run 是否真实存在 ----------
    # 4a 回测库是「目录形式」runs/<run_id>/，且 register_external_run 用 RUN_ID_RE 校验
    if str(BASE / "框架/backend") not in sys.path:
        sys.path.insert(0, str(BASE / "框架/backend"))
    import backtest_store as BS
    runs_root = pathlib.Path(BS.RUNS_ROOT)
    real_runs = sorted(d.name for d in runs_root.iterdir() if d.is_dir()) \
        if runs_root.exists() else []
    regex_check = {rid: bool(BS.RUN_ID_RE.match(rid)) for rid in CLAIMED_RUNS}
    dir_hits = {rid: [d for d in real_runs if rid.lower() in d.lower()]
                for rid in CLAIMED_RUNS}
    res["回测库检索"] = {
        "RUNS_ROOT": str(runs_root),
        "真实run目录数": len(real_runs),
        "RUN_ID_RE": BS.RUN_ID_RE.pattern,
        "正则匹配": regex_check,
        "目录名命中": dir_hits,
        "判定": "① 若 run_id 不匹配 RUN_ID_RE ⇒ register_external_run 结构上会拒绝，"
                "该 run 不可能存在于回测库；② 若真实 run 目录中零命中 ⇒ 佐证。"
                "两条同时成立时，声称的 run 为虚构，不是「没找到」。",
    }

    # 4b 文本文件（限定目录，禁全盘）
    txt_hits: dict[str, list[str]] = {r: [] for r in CLAIMED_RUNS}
    scan_roots = ["evidence/SYSTEM-UI-01", "框架/backend", "UI界面", "tools"]
    for root in scan_roots:
        rp = BASE / root
        if not rp.exists():
            continue
        for p in rp.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".md", ".json", ".csv", ".py", ".txt", ".js",
                                        ".jsx", ".ts", ".tsx", ".html"):
                continue
            if p.stat().st_size > 4_000_000:
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for rid in CLAIMED_RUNS:
                if rid in t:
                    txt_hits[rid].append(str(p.relative_to(BASE)))
    res["文本检索"] = {
        "扫描根": scan_roots,
        "命中": {k: sorted(set(v)) for k, v in txt_hits.items()},
        "判定": "若某 run_id 只出现在对方自己的 CSV / 测试脚本里，"
                "而不在回测库或任何真实运行产物中 ⇒ 该 run 为虚构",
    }

    # ---------- 5. 汇总判定 ----------
    real_ui_ok = len(sha_map) >= 16
    run_exists = {rid: (regex_check[rid] and bool(dir_hits[rid])) for rid in CLAIMED_RUNS}
    res["判定汇总"] = {
        "截图16视图声称": "成立" if real_ui_ok else f"不成立（唯一 {len(sha_map)} 张）",
        "声称run真实性": {
            rid: ("真实存在" if run_exists[rid] else
                  f"虚构（正则匹配={regex_check[rid]}，{len(real_runs)} 个真实 run 中命中 "
                  f"{len(dir_hits[rid])} 个）")
            for rid in CLAIMED_RUNS},
        "本轮结论性质": "独立取证；与 13_D关UI_验收.md 结论相同也不构成互相印证，"
                        "只说明两条独立路径得到同一结果",
    }

    (OUT / "02_UI差异.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    print(f"截图：{len(pngs)} 张，唯一 SHA {len(sha_map)} 个，重复组 {len(dup_groups)}")
    for g in sorted(dup_groups, key=lambda x: -x["count"]):
        print(f"  {g['sha256'][:12]}… × {g['count']}  {g['files']}")
    print(f"\n表03 行数={res['表03'].get('行数')}  表04 行数={res['表04'].get('行数')}")
    print(f"\n表03 声称 vs 截图读数（一致 {res['表03_声称vs截图']['一致数']}"
          f"/{res['表03_声称vs截图']['总项数']}）：")
    for x in cmp03:
        print(f"  {'OK ' if x['一致'] else 'MISMATCH'}  {x['项目']:<12} "
              f"声称={x['表03声称']:<10} 截图={x['截图view_01实际']}")
    print(f"\n表04 声称 vs 截图读数（一致 {res['表04_声称vs截图']['一致数']}"
          f"/{res['表04_声称vs截图']['总项数']}）：")
    for x in cmp04:
        print(f"  {'OK ' if x['一致'] else 'MISMATCH'}  {x['项目']:<10} "
              f"声称={str(x['表04声称']):<14} 截图v15={x.get('截图view_15实际')} "
              f"截图v16={x.get('截图view_16实际')}")
    print(f"\nRUNS_ROOT={res['回测库检索']['RUNS_ROOT']}  真实run目录 {len(real_runs)} 个")
    for rid in CLAIMED_RUNS:
        print(f"  {rid:16s} 正则匹配={regex_check[rid]}  目录命中={len(dir_hits[rid])}")
    for rid in CLAIMED_RUNS:
        fs = sorted(set(txt_hits[rid]))
        print(f"\n{rid} 文本命中 {len(fs)} 处：")
        for f in fs[:12]:
            print(f"    {f}")
    print("\n判定：", json.dumps(res["判定汇总"], ensure_ascii=False)[:300])
    print("\n→ out/02_UI差异.json")


if __name__ == "__main__":
    main()
