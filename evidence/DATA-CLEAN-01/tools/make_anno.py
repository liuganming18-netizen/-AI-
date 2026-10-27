#!/usr/bin/env python3
"""生成「系统文件标注表」：是什么 / 日期 / 能不能删。"""
import csv
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
EV = os.path.join(ROOT, "evidence/DATA-CLEAN-01")

# 路径 -> (是什么, 日期说明, 能否删, 依据)
ANNO = {
    # ── A 正式仓储 ──
    "数据/市场数据/parquet/candles": (
        "历史 K 线归档（按标的/周期/年份分文件）",
        "全池止于 2026-09-14；之后数据只在数据库",
        "不能删", "正式仓储，OHLCV 重建的唯一来源"),
    "数据/市场数据/parquet/oi": (
        "持仓量历史归档",
        "抽样 2025-09-17 ~ 2026-10-18",
        "不能删", "正式仓储，持仓量重建来源"),
    "数据/市场数据/parquet/metrics": (
        "多空比、大户持仓比等衍生指标（最大单块）",
        "抽样 2025-09-17 ~ 2026-10-08",
        "不能删", "正式仓储；研究输入的 oi_metrics 就是它的旧副本"),
    "数据/市场数据/parquet/funding": (
        "资金费历史",
        "2020-01 ~ 2026-10",
        "不能删", "正式仓储"),
    "数据/市场数据/parquet/candle_heads": (
        "K 线头部指针（指向 sources）",
        "随 candles 更新",
        "不能删", "仓储索引，删了 K 线无法定位"),
    "数据/市场数据/parquet/sources": (
        "数据来源登记（文件路径 + sha256 + 行数）",
        "2026-10-14",
        "不能删", "仓储溯源，candle_heads 依赖它"),
    "数据/市场数据/db": (
        "主库 market_v2.sqlite3（含 -wal 782MB / -shm）",
        "2026-10-19 仍在写",
        "不能删", "全系统唯一实时库；WAL 禁止手工截断"),

    # ── B 原始包 ──
    "数据/raw": (
        "交易所官方 K 线 zip 原始包（346,456 个文件）",
        "数据日期 2019-12-31 ~ 2026-10-10",
        "建议保留", "唯一原始来源；删了要重新下载，受 API 冷却限制"),
    "数据/raw_oi_metrics": (
        "交易所官方持仓量 zip 原始包（886,794 个文件）",
        "数据日期 2021-12-01 ~ 2026-10-14",
        "建议保留", "同上"),
    "数据/raw_rest_gap": (
        "REST 接口补缺的原始响应",
        "2026-10-08 ~ 2026-10-15",
        "可删（价值低）", "补缺过程产物，数据已入库；但仅 6.6MB，删了省不了多少"),
    "数据/archive": (
        "归档数据",
        "2026-10-14",
        "待核", "未查清内容"),
    "数据/contracts": (
        "合约规格（步长/最小量/最大量）",
        "2026-10-05 ~ 2026-10-12",
        "保留", "成交取整依赖它，删了回测跑不了"),

    # ── C 研究输入 ──
    "数据/研究输入/WyckoffMaster": (
        "v3.0 研究输入（已弃用，含 Holdout 区间）",
        "2026-10-15 生成；数据到 2026-10-14",
        "部分可删", "ohlcv/oi_metrics 已移废纸篓；mark1h 独有、funding 有溯源差异、s1 快照待核"),
    "数据/研究输入/WyckoffMaster/_backup/20261014T190050Z": (
        "第一份快照（与当前目录 521 个文件不同）",
        "2026-10-14 18:58 生成",
        "待核", "520 个 funding 的 source 列多 +fapi/+mark1h 溯源标记，数值相同"),
    "数据/研究输入/WyckoffMaster_v31": (
        "v3.1 当前研究输入（IS/Val 物理隔离，不含 Holdout）",
        "2026-10-15 生成；manifest 冻结未变",
        "不能删", "正式实验输入，manifest sha256 28800c42… 与历史记录一致"),
    "数据/研究输入/外部迁移_2026-10-16": (
        "外部工作树迁入的决策/派工数据（375 币池、日历、规格）",
        "2026-10-11 ~ 2026-10-13",
        "不能删", "wx_config 实际读取这里"),

    # ── D 回测产物 ──
    "回测日志/event_trend_v0_1/runs": (
        "主回测产物（1,195 个 run：成交/账本/净值/图表）",
        "2026-10-17 ~ 2026-10-19",
        "保留", "含逐笔成交与账本，属复现能力"),
    "回测日志/event_trend_v0_1/runs_sep_all_latest": (
        "9 月批次最新一次批量结果",
        "2026-10-18",
        "待核", "与 runs_sep50 系列各差 100 个文件，非简单重复"),
    "回测日志/event_trend_v0_1/runs_2025aug100": (
        "8 月 100 标的批量结果（完整版）",
        "2026-10-17",
        "待核", "与 runs_aug100 有 204 条差异，各自含独有 run"),
    "回测日志/event_trend_v0_1/runs_sep50_check": (
        "9 月 50 标的复核结果",
        "2026-10-18",
        "待核", "与 runs_sep50 差 100 个文件"),
    "回测日志/event_trend_v0_1/runs_sep50": (
        "9 月 50 标的批量结果",
        "2026-10-18",
        "待核", "同上"),
    "回测日志/event_trend_v0_1/runs_aug100": (
        "8 月 100 标的批量结果（精简版）",
        "2026-10-17",
        "待核", "体积只有完整版 29%，可能缺图表/净值"),
    "回测日志/event_trend_v0_1/runs_sep50_full": (
        "9 月 50 标的（另一版本）",
        "2026-10-18",
        "待核", "体积 90MB，是 runs_sep50 的 26%"),
    "回测日志/event_trend_v0_1/runs_sep50_strict": (
        "9 月 50 标的（严格模式版本）",
        "2026-10-18",
        "待核", "体积 26MB，最小"),
    "回测日志/event_trend_v0_1/reconstructed": (
        "重建样本（用于对账）",
        "2026-10-17",
        "保留", "仅 0.3MB，是反造假证据的一部分"),
    "回测日志/oi_resonance_v1": (
        "持仓量共振通道的回测目录（基本为空）",
        "2026-10-16",
        "可删（几乎空）", "只有 40KB 隔离目录，无实际产物"),
    "回测日志/batch_tests_archive": (
        "批量测试归档",
        "2026-10-17",
        "待核", "未查清与 runs 的关系"),
    "回测日志/legacy_archive": (
        "旧版回测归档",
        "2026-10-16 ~ 2026-10-17",
        "待核", "未查清是否与现有 runs 重复"),
    "回测日志/历史下载状态": (
        "历史数据下载任务状态（10,283 个文件）",
        "2026-10-05 ~ 2026-10-15",
        "待核", "可能是下载断点，删了可能要重下"),

    # ── E 审计证据 ──
    "evidence": (
        "各轮审计的证据包（报告/哈希/日志/复现脚本）",
        "2026-10-14 ~ 2026-10-19",
        "保留", "审计证据，工单默认保留"),

    # ── F 代码配置 ──
    "框架/backend": (
        "后端代码 + 运行日志",
        "2026-10-04 ~ 2026-10-19",
        "保留（日志可清）", "代码是系统本体；logs/ 下的日志可轮转"),
    "框架/data_hub": (
        "统一数据网关",
        "2026-10-14 ~ 2026-10-15",
        "保留", "所有 API 调用必须走它"),
    "框架/最新唐奇安威科夫接通与收尾_2026-10-11": (
        "旧工作区残留目录",
        "2026-10-15 ~ 2026-10-18",
        "待核", "仅 0.7MB，但可能被旧路径引用"),
    "UI界面/web": (
        "前端源码 + 依赖（node_modules 已排除统计）",
        "2026-10-04 ~ 2026-10-19",
        "保留", "系统界面；node_modules 可重装"),
    "docs": (
        "设计文档、施工单、复盘合同",
        "2026-10-03 ~ 2026-10-19",
        "保留", "项目知识沉淀"),
    "策略": (
        "策略代码（威科夫、事件趋势、撮合核心）",
        "2026-10-15 ~ 2026-10-19",
        "不能删", "系统核心，冻结区"),

    # ── G 运行时状态 ──
    "数据/任务队列": (
        "后台任务队列库（回测/补数据）",
        "2026-10-19",
        "保留", "删了排队中的任务全丢"),
    "数据/paper": (
        "纸面交易账本",
        "2026-10-18",
        "不能删", "交易记录，工单明令保留"),
}


def main():
    raw = json.load(open(os.path.join(EV, "objects_raw.json"), encoding="utf-8"))
    rows = []
    for i, r in enumerate(raw, 1):
        p = r["path"]
        if p not in ANNO:
            continue
        what, date, can, why = ANNO[p]
        mb = r["bytes"] / 1024 / 1024
        # WyckoffMaster 的统计含其下 _backup，需扣除避免重复计数
        if p == "数据/研究输入/WyckoffMaster":
            sub = next((x for x in raw
                        if x["path"] == "数据/研究输入/WyckoffMaster/_backup/20261014T190050Z"), None)
            if sub:
                mb -= sub["bytes"] / 1024 / 1024
                r = dict(r)
                r["files"] -= sub["files"]
        rows.append({
            "序号": i,
            "路径": p,
            "体积MB": round(mb, 1),
            "文件数": r["files"],
            "是什么": what,
            "日期": date,
            "能否删除": can,
            "依据": why,
            "文件最后修改": f"{r['mtime_min']} ~ {r['mtime_max']}",
        })

    out_csv = os.path.join(EV, "系统文件标注.csv")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # 按可删性汇总
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0.0])
    for r in rows:
        agg[r["能否删除"]][0] += 1
        agg[r["能否删除"]][1] += r["体积MB"]

    print(f"写入 {out_csv}（{len(rows)} 项）\n")
    print("=== 按结论汇总 ===")
    for k, (n, mb) in sorted(agg.items(), key=lambda x: -x[1][1]):
        print(f"  {k:16s} {n:>3d} 项  {mb:>9.1f} MB")
    print(f"\n合计 {sum(r['体积MB'] for r in rows):.1f} MB")

    # 生成 Markdown
    md = ["# 系统文件标注表\n",
          "> 生成时间 2026-10-19 10:36。体积按实际字节计（非磁盘块占用）。",
          "> `UI界面/web` 与 `框架` 已排除 `node_modules`。\n"]
    groups = [
        ("A 正式仓储（不能删）", ["数据/市场数据/parquet/candles", "数据/市场数据/parquet/oi",
                              "数据/市场数据/parquet/metrics", "数据/市场数据/parquet/funding",
                              "数据/市场数据/parquet/candle_heads", "数据/市场数据/parquet/sources",
                              "数据/市场数据/db"]),
        ("B 原始包（建议保留）", ["数据/raw", "数据/raw_oi_metrics", "数据/raw_rest_gap",
                            "数据/archive", "数据/contracts"]),
        ("C 研究输入", ["数据/研究输入/WyckoffMaster",
                    "数据/研究输入/WyckoffMaster/_backup/20261014T190050Z",
                    "数据/研究输入/WyckoffMaster_v31", "数据/研究输入/外部迁移_2026-10-16"]),
        ("D 回测产物", ["回测日志/event_trend_v0_1/runs",
                    "回测日志/event_trend_v0_1/runs_sep_all_latest",
                    "回测日志/event_trend_v0_1/runs_2025aug100",
                    "回测日志/event_trend_v0_1/runs_sep50_check",
                    "回测日志/event_trend_v0_1/runs_sep50",
                    "回测日志/event_trend_v0_1/runs_aug100",
                    "回测日志/event_trend_v0_1/runs_sep50_full",
                    "回测日志/event_trend_v0_1/runs_sep50_strict",
                    "回测日志/event_trend_v0_1/reconstructed", "回测日志/oi_resonance_v1",
                    "回测日志/batch_tests_archive", "回测日志/legacy_archive",
                    "回测日志/历史下载状态"]),
        ("E 审计证据", ["evidence"]),
        ("F 代码与配置", ["框架/backend", "框架/data_hub",
                     "框架/最新唐奇安威科夫接通与收尾_2026-10-11",
                     "UI界面/web", "docs", "策略"]),
        ("G 运行时状态", ["数据/任务队列", "数据/paper"]),
    ]
    idx = {r["路径"]: r for r in rows}
    for title, paths in groups:
        md.append(f"\n## {title}\n")
        md.append("| 路径 | 体积 | 文件数 | 是什么 | 日期 | 能否删 | 依据 |")
        md.append("|---|---|---|---|---|---|---|")
        for p in paths:
            r = idx.get(p)
            if not r:
                continue
            md.append(f"| `{p}` | {r['体积MB']:.1f} MB | {r['文件数']:,} | {r['是什么']} | "
                      f"{r['日期']} | **{r['能否删除']}** | {r['依据']} |")
    with open(os.path.join(EV, "系统文件标注.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"写入 {os.path.join(EV, '系统文件标注.md')}")


if __name__ == "__main__":
    main()
