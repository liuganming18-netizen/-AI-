#!/usr/bin/env python3
"""生成清理候选 CSV（第一阶段交付，不含执行）。"""
import csv
import os
import subprocess

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
BASE = os.path.join(ROOT, "数据/研究输入/WyckoffMaster")


def du_mb(p):
    try:
        out = subprocess.check_output(["du", "-sk", p], text=True).split()
        return round(int(out[0]) / 1024, 1)
    except Exception:
        return None


rows = [
    {
        "批次": "第一批",
        "绝对路径": os.path.join(BASE, "_backup/20261014T192054Z"),
        "磁盘占用MB": du_mb(os.path.join(BASE, "_backup/20261014T192054Z")),
        "用途": "v3.0 研究输入的第二份完整快照（2026-10-14T19:20:54Z 生成）",
        "保留副本路径": os.path.join(BASE, "（即 live 本体，3155 文件逐字节相同）"),
        "独有文件数": 0,
        "独有数据量": 0,
        "内容核对结果": "与 live 全量 sha256 比对：3155/3155 完全一致，含 manifest.json；无独有文件、无独有数值",
        "运行依赖": "无（lsof 0 条；无代码/定时任务引用）",
        "重建验证": "不需要重建，live 本体即为同一内容",
        "建议": "删除",
        "预计净释放MB": du_mb(os.path.join(BASE, "_backup/20261014T192054Z")),
        "证据强度": "最高：全量哈希逐字节相同",
    },
    {
        "批次": "第二批",
        "绝对路径": os.path.join(BASE, "ohlcv"),
        "磁盘占用MB": du_mb(os.path.join(BASE, "ohlcv")),
        "用途": "v3.0 全时段 OHLCV 研究输入（15m/1h/4h，1578 文件）",
        "保留副本路径": "parquet/candles（到 2026-09-14）+ SQLite candle_versions（到 2026-10-19）",
        "独有文件数": 0,
        "独有数据量": 0,
        "内容核对结果": "525/526 标的比对成功；共同行数值不一致仅 3(15m)/29(1h)/109(4h) 行；研究输入多出的 153 万/38 万/9.5 万行为 2026-09-14 后增量，已在 SQLite 中逐值验证一致",
        "运行依赖": "无",
        "重建验证": "0GUSDT 15m 抽样：2925 行增量，open/high/low/close/volume 各 0 处不一致，研究输入独有 0 行。全池未逐标的做重建演练",
        "建议": "删除（需用户确认接受字节级不可复现）",
        "预计净释放MB": du_mb(os.path.join(BASE, "ohlcv")),
        "证据强度": "高：内容可重建；但重建文件字节不等于原文件",
    },
    {
        "批次": "第三批",
        "绝对路径": os.path.join(BASE, "oi_metrics"),
        "磁盘占用MB": du_mb(os.path.join(BASE, "oi_metrics")),
        "用途": "v3.0 全时段持仓量+多空比指标（526 文件）",
        "保留副本路径": "parquet/metrics（526 标的，含相同 6 个指标字段，另多 2 个字段）",
        "独有文件数": 0,
        "独有数据量": 0,
        "内容核对结果": "526/526 比对成功；99% 一致；2026-09~10 尾部有错位（值滞后一格）与 4 个比率字段为 NaN；仓储版本更完整准确",
        "运行依赖": "无",
        "重建验证": "仓储层已有同字段数据且更准；研究输入独有的 9758 行需逐条确认可从仓储补齐（未做）",
        "建议": "删除（需用户确认；同时作为数据质量问题上报）",
        "预计净释放MB": du_mb(os.path.join(BASE, "oi_metrics")),
        "证据强度": "中高：仓储更准，但独有 9758 行未逐条核完",
    },
    {
        "批次": "保留",
        "绝对路径": os.path.join(BASE, "mark1h"),
        "磁盘占用MB": du_mb(os.path.join(BASE, "mark1h")),
        "用途": "小时级标记价格序列（521 标的，1152 万行）",
        "保留副本路径": "无",
        "独有文件数": 521,
        "独有数据量": 11526345,
        "内容核对结果": "parquet 顶层无 mark 目录；SQLite 26 张表无 mark 相关表；funding.mark_price 仅为结算时刻值，非小时序列",
        "运行依赖": "无（但数据独有）",
        "重建验证": "无法重建",
        "建议": "保留",
        "预计净释放MB": 0,
        "证据强度": "最高：仓储层确认无对应数据",
    },
    {
        "批次": "保留",
        "绝对路径": os.path.join(BASE, "funding"),
        "磁盘占用MB": du_mb(os.path.join(BASE, "funding")),
        "用途": "资金费历史（526 文件）",
        "保留副本路径": "parquet/funding + SQLite funding_history（数值一致）",
        "独有文件数": 0,
        "独有数据量": 0,
        "内容核对结果": "520 个文件与快照 s1 仅 source 列不同；数值列全同。live 的 source 反而比 s1 少了 +fapi/+mark1h 溯源后缀",
        "运行依赖": "无",
        "重建验证": "数值可重建，溯源标记不可重建",
        "建议": "保留",
        "预计净释放MB": 0,
        "证据强度": "中：数值冗余但溯源信息有独有内容",
    },
    {
        "批次": "待核",
        "绝对路径": os.path.join(BASE, "_backup/20261014T190050Z"),
        "磁盘占用MB": du_mb(os.path.join(BASE, "_backup/20261014T190050Z")),
        "用途": "v3.0 研究输入的第一份快照（2026-10-14T18:58:36Z 生成）",
        "保留副本路径": "live（但 source 列不同）",
        "独有文件数": 0,
        "独有数据量": 520,
        "内容核对结果": "2634/3155 与 live 逐字节相同；520 个 funding 文件仅 source 列不同（s1 含 +fapi/+mark1h 溯源后缀）；manifest 亦不同",
        "运行依赖": "无",
        "重建验证": "数值可重建，source 溯源标记不可重建",
        "建议": "待核（是否保留溯源信息由用户定）",
        "预计净释放MB": 0,
        "证据强度": "不足以判为完全重复",
    },
    {
        "批次": "保留",
        "绝对路径": os.path.join(BASE, "_state"),
        "磁盘占用MB": du_mb(os.path.join(BASE, "_state")),
        "用途": "v3.0 构建进度状态",
        "保留副本路径": "无（快照未备份此文件）",
        "独有文件数": 1,
        "独有数据量": 0,
        "内容核对结果": "live 独有，两份快照均无",
        "运行依赖": "无",
        "重建验证": "重跑构建会重新生成",
        "建议": "保留（体积可忽略）",
        "预计净释放MB": 0,
        "证据强度": "体积过小，不值得动",
    },
    {
        "批次": "保留",
        "绝对路径": os.path.join(BASE, "DEPRECATED_含Holdout_勿用于选型.txt"),
        "磁盘占用MB": 0.0,
        "用途": "v3.0 目录的弃用声明",
        "保留副本路径": "无",
        "独有文件数": 1,
        "独有数据量": 0,
        "内容核对结果": "live 独有；内容是弃用依据本身",
        "运行依赖": "无",
        "重建验证": "文本可重写，但它是审计痕迹",
        "建议": "保留",
        "预计净释放MB": 0,
        "证据强度": "不应删除",
    },
]

out = os.path.join(ROOT, "evidence/DATA-CLEAN-01/清理候选.csv")
cols = list(rows[0].keys())
with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)

print(f"写入 {out}（{len(rows)} 行）\n")
tot = 0
for r in rows:
    if r["建议"] == "删除":
        tot += r["预计净释放MB"] or 0
    print(f"{r['批次']:6s} {os.path.basename(r['绝对路径']):42s} "
          f"{str(r['磁盘占用MB']):>9s} MB  {r['建议']}")
print(f"\n三批合计可释放（按 du 磁盘占用）: {tot:.1f} MB = {tot/1024:.2f} GB")
