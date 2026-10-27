# -*- coding: utf-8 -*-
"""FT-DATA-01：生成字段映射表与缺口清单（数字取自 logs/，不手抄）。"""
from __future__ import annotations

import csv
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
PKG = os.path.join(ROOT, "evidence/FT-DATA-01")
OUT = os.path.join(PKG, "out")
LOGS = os.path.join(PKG, "logs")

inv = json.load(open(os.path.join(LOGS, "00_inventory.json"), encoding="utf-8"))
exp = json.load(open(os.path.join(LOGS, "01_export.json"), encoding="utf-8"))

SYM = exp["symbols"]
P = {s: f"{s[:-4]}_USDT_USDT" for s in SYM}

MAP_ROWS = [
    # 序号, 数据类, 源路径, 源字段, 源类型, 源含义, 导出文件, 导出字段, 导出类型,
    # 转换方式, 语义是否改变, 可用时点依据
    [1, "K线4h", "candles/binance/um/{S}/4h/2025.parquet", "open_time", "int64",
     "4h K 线起点（UTC 毫秒）", "futures/{P}-4h-futures.feather", "date",
     "datetime64[ms] 无时区", "毫秒转 UTC 时间，去时区标记", "否（仍是起点）",
     "close_time 字段存在 ⇒ 交易所闭合可证"],
    [2, "K线4h", "同上", "open/high/low/close", "十进制字符串",
     "OHLC 价格", "同上", "open/high/low/close", "float64",
     "十进制字符串转 float64，不缩放不复权",
     "否（往返表示与原字面量一致，相对误差 0）", "同上"],
    [3, "K线4h", "同上", "volume", "十进制字符串", "基础币成交量", "同上", "volume",
     "float64", "十进制字符串转 float64", "否", "同上"],
    [4, "K线1h", "candles/binance/um/{S}/1h/2025.parquet", "open_time 及 OHLCV",
     "同 4h", "1h K 线", "futures/{P}-1h-futures.feather",
     "date/open/high/low/close/volume", "同 4h", "同 4h", "否", "同 4h"],
    [5, "K线15m", "candles/binance/um/{S}/15m/2025.parquet", "open_time 及 OHLCV",
     "同 4h", "15m K 线（执行周期）", "futures/{P}-15m-futures.feather",
     "date/open/high/low/close/volume", "同 4h", "同 4h", "否", "同 4h"],
    [6, "15m成交额", "candles/binance/um/{S}/15m/2025.parquet", "quote_volume",
     "十进制字符串", "15m 计价成交额（参与率上限用）",
     "sidecar/{S}_15m_quote_volume.csv", "quote_volume", "原样字符串",
     "不做数值转换，保持原十进制字面量", "否（完全未转换）", "同 4h"],
    [7, "15m成交笔数", "同上", "trade_count", "int64", "15m 成交笔数",
     "sidecar/{S}_15m_quote_volume.csv", "trade_count", "int64", "原样", "否", "同 4h"],
    [8, "OI 5m", "oi/binance/um/{S}/2025.parquet", "ts", "int64",
     "OI 5 分钟桶起点（UTC 毫秒）", "futures/{P}-5m-open_interest.feather", "date",
     "datetime64[ms] 无时区", "毫秒转 UTC 时间", "否（仍是桶起点）",
     "只有桶起点，fetched_at 全空 ⇒ 无发布时点证据，按桶结束假设"],
    [9, "OI 5m", "同上", "open_interest", "十进制字符串", "持仓量（基础币）", "同上",
     "open_interest_amount", "float64", "十进制字符串转 float64", "否",
     "同 8（假设时刻）"],
    [10, "OI 5m", "同上", "open_interest_value", "十进制字符串", "持仓量（计价币）",
     "同上", "open_interest_value", "float64", "十进制字符串转 float64", "否",
     "同 8（假设时刻）"],
    [11, "资金费", "funding/binance/um/{S}.parquet", "funding_time", "int64",
     "结算时刻（UTC 毫秒，含 0–16ms 偏移）",
     "futures/{P}-1h-funding_rate.feather 与 -8h-funding_rate.feather（同一份数据）",
     "date", "datetime64[ms] 无时区", "毫秒转 UTC 时间，保留毫秒不取整",
     "否（时间戳原样）", "结算时刻本身即事件时刻；发布时点无证据"],
    [12, "资金费", "同上", "funding_rate", "十进制字符串", "资金费率", "同上",
     "funding_rate", "float64", "十进制字符串转 float64", "否", "同上"],
    [13, "资金费 mark", "同上", "mark_price", "十进制字符串",
     "结算时点的标记价格", "sidecar/{S}_funding_settlements.csv",
     "mark_price_at_settlement", "原样字符串", "不做转换", "否（完全未转换）",
     "只有结算时点值，无连续 mark 序列"],
    [14, "mark 1h", "数据/研究输入/WyckoffMaster/mark1h/{S}.parquet", "open_time",
     "int64", "mark 1h 桶起点（UTC 毫秒）", "futures/{P}-1h-mark.feather", "date",
     "datetime64[ms] 无时区", "毫秒转 UTC 时间", "否（仍是起点）",
     "研究输入目录，非主行情链路；每小时一个值"],
    [15, "mark 1h", "同上", "mark_open", "float64（已是数值）",
     "每小时标记价格", "同上", "open/high/low/close", "float64",
     "同一值同时写入四列", "否（没有新增或推算价格）", "同上"],
    [16, "mark 1h", "—（无源字段）", "—", "—", "—", "同上", "volume", "float64",
     "构造值 0.0（mark 无成交量）", "是（构造列，须声明）", "同上"],
    [17, "K线闭合标记", "candles/.../2025.parquet", "is_closed", "int64",
     "是否已收盘", "不导出", "—", "—", "导出前筛掉 is_closed≠1", "否",
     "未收盘 K 线一律不进包"],
    [18, "可用时点", "candles/.../2025.parquet", "available_at", "object",
     "声称的可用时刻", "不导出", "—", "—",
     "全为 None，无信息，不导出也不臆造", "—", "无证据"],
    [19, "K线收盘时刻", "candles/.../2025.parquet", "close_time", "int64",
     "K 线收盘时刻（=起点+周期-1ms）", "不导出", "—", "—",
     "Freqtrade 由 timeframe 自行推导", "否", "可作闭合证据，但不随包导出"],
]

with open(os.path.join(OUT, "06_字段映射.csv"), "w", newline="",
          encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["序号", "数据类", "源路径", "源字段", "源类型", "源含义",
                "导出文件", "导出字段", "导出类型", "转换方式", "语义是否改变",
                "可用时点依据"])
    for r in MAP_ROWS:
        w.writerow([str(x).replace("{S}", "<symbol>").replace("{P}", "<PAIR>")
                    for x in r])

pg = inv["pool_gap"]["5m"]
GAPS = [
    ["D-01", "数据可用时点", "所有数据源都没有发布时点证据",
     "K线 available_at 全为 None；OI fetched_at 全为空；资金费无发布时间字段",
     "只能按「K线收盘」「OI 桶结束」假设可得，属声明假设，不具 PIT 正式资格",
     "随包标注 declared_assumption，不据此宣称正式历史精确验证",
     "否（不阻止机械核对，阻止正式结论）"],
    ["D-02", "数据缺口", f"OI 5m 全池同时缺 {len(pg)} 桶",
     f"缺桶时刻 {', '.join(x[:16] for x in pg)} UTC；100 个候选标的中 86 个同时缺",
     "该时段任何标的都无法通过 OI 门槛，两套引擎都会看到同一个洞",
     "保留空桶，不补 0、不插值、不前向填充", "否"],
    ["D-03", "口径差异", "mark 价格只有每小时一个值，没有 OHLC 四价",
     "数据/研究输入/WyckoffMaster/mark1h/{标的}.parquet 只有 open_time 与 mark_open；"
     "5 个标的在导出区间内 4,536 行、零缺、零空值；该目录不在主行情链路",
     "Freqtrade 的 mark 蜡烛要求标准 OHLCV 列，单值无法直接满足",
     "把一个值同时写入 open/high/low/close、volume 置 0，并声明这是重复填写；"
     "另给结算时点的精确 mark 到 sidecar", "否（构造列已声明）"],
    ["D-04", "口径差异", "资金费结算间隔不是单一周期",
     "实测：ARUSDT/BATUSDT/CTSIUSDT 为 8h，PUMPBTCUSDT 为 4h，BANANAUSDT 为 1h 与 4h 混合",
     "Freqtrade 的资金费文件名带周期标签，单一标签无法表达混合间隔",
     "同一份逐条结算数据同时以 1h 与 8h 两种标签提供；若其版本会按标签重采样，"
     "必须改读 sidecar", "否"],
    ["D-05", "口径差异", "资金费时间戳带 0–16 毫秒偏移",
     "五个标的的偏移分布均为 0–16ms（多数为 0），来自归档与 mark 对齐",
     "若加载器要求严格对齐整点，可能落到相邻桶",
     "保留原值不取整；如需对齐，向下取整到小时，最大改动 16ms",
     "否"],
    ["D-06", "字段缺失", "Freqtrade 的 OHLCV 没有 quote_volume",
     "本策略参与率上限 0.001 × 前一根 15m 成交额，必须用成交额",
     "不加载该列则下单量约束失效，成交会偏大",
     "单独导出 15m 成交额 sidecar；若不加载必须在结论里声明该约束未生效",
     "否（但未加载则结果不可比）"],
    ["D-07", "样本量", "本窗口旧引擎可比对成交仅 5 笔",
     "既有批次 runs_2025aug100 在 2025-08-01~2025-12-31 全池只有 6 笔成交，"
     "主清单 5 个标的各 1 笔",
     "回撤、胜率、利润因子这类统计量在此样本下没有判别力",
     "只做逐笔字段与算术一致性核对，不做绩效优劣判断", "是（阻止当作绩效结论）"],
    ["D-08", "范围", "指数类标的已排除",
     "官方 exchangeInfo：ALLUSDT、BTCDOMUSDT 的 underlyingType=INDEX",
     "本包不含指数标的", "沿用 GATE-CLOSE-02 纳入清单 526 个标的",
     "否"],
    ["D-09", "历史规格", "历史合约规格未证实",
     "PRECISION-04：576 次运行 0 个 verified，formal_pass_allowed 全 False",
     "价格步长、最小下单量按当前规格假设",
     "标为假设限制，不包装成正式历史精确验证", "否（阻止正式历史精确验证）"],
]

with open(os.path.join(OUT, "07_缺口清单.csv"), "w", newline="",
          encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["编号", "类别", "缺口", "证据", "影响", "处置", "是否阻止本次比对"])
    for r in GAPS:
        w.writerow(r)

print("字段映射", len(MAP_ROWS), "行；缺口", len(GAPS), "项")
print("阻止项:", [r[0] for r in GAPS if r[6].startswith("是")])
