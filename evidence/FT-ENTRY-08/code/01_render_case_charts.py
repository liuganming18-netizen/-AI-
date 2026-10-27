# -*- coding: utf-8 -*-
"""FT-ENTRY-08 案例图表渲染与清单生成脚本

严格落实工单要求：
1. 固定 12 个选样清单（4大类各最多3个，全局按时间排序去重）；
2. 输出决策视图（截止决策时刻，绝无未来走势）与复盘视图（展示后续路径）；
3. 严格包含原信号、确认K线、实际成交、冻结P0、退出位置、成交量及缩量判据、OI；
4. 明确区分真实成交与假设机会，绝不画虚假成交。
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT_ENTRY_07_DIR = BASE_DIR / "evidence/FT-ENTRY-07"
FT_ENTRY_08_DIR = BASE_DIR / "evidence/FT-ENTRY-08"
CHARTS_DIR = FT_ENTRY_08_DIR / "charts"
DATA_DIR = FT_ENTRY_07_DIR / "user_data/data/binance/futures"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-ENTRY-08-Renderer")

# 4大分类定义
CATEGORIES = [
    ("CAT1_AVOIDED_LOSS", "原方案亏损、确认后放弃 (Avoided Loss)"),
    ("CAT2_MISSED_GAIN", "原方案盈利、确认后放弃 (Missed Gain)"),
    ("CAT3_DELAYED_IMPROVED", "确认后仍成交且结果改善 (Delayed Improved)"),
    ("CAT4_DELAYED_DEGRADED", "确认后仍成交但结果变差 (Delayed Degraded)"),
]


def load_reconciliation_and_select() -> List[Dict[str, Any]]:
    """从 FT-ENTRY-07 逐候选对照表抽取 12 个固定候选。"""
    df_rec = pd.read_csv(FT_ENTRY_07_DIR / "02_逐候选对照表.csv")
    df_rec["open_time_dt"] = pd.to_datetime(df_rec["c0_open_time"])

    cat_conds = {
        "CAT1_AVOIDED_LOSS": df_rec["c0_filled"] & (~df_rec["c1_filled"]) & (df_rec["c0_profit_abs"] < 0),
        "CAT2_MISSED_GAIN": df_rec["c0_filled"] & (~df_rec["c1_filled"]) & (df_rec["c0_profit_abs"] > 0),
        "CAT3_DELAYED_IMPROVED": df_rec["c0_filled"] & df_rec["c1_filled"] & (df_rec["c1_profit_abs"] > df_rec["c0_profit_abs"]),
        "CAT4_DELAYED_DEGRADED": df_rec["c0_filled"] & df_rec["c1_filled"] & (df_rec["c1_profit_abs"] < df_rec["c0_profit_abs"]),
    }

    used_eids = set()
    selected_cases = []

    for cat_code, cat_title in CATEGORIES:
        cond = cat_conds[cat_code]
        pool = df_rec[cond].sort_values(["open_time_dt", "event_id"])
        picks = []
        for _, r in pool.iterrows():
            eid = r["event_id"]
            if eid not in used_eids:
                used_eids.add(eid)
                picks.append(eid)
                selected_cases.append({
                    "category_code": cat_code,
                    "category_title": cat_title,
                    "slot_index": len(picks),
                    "case_id": f"{cat_code}_{len(picks)}",
                    "event_id": eid,
                    "symbol": r["symbol"],
                    "target_param": r["param_group"],
                })
                if len(picks) == 3:
                    break

    logger.info(f"成功选取 {len(selected_cases)} 个案例。")
    return selected_cases


def get_feather_symbol(symbol: str) -> str:
    """转换币种为 feather 文件名前缀，例如 AR/USDT -> AR_USDT_USDT。"""
    base = symbol.split("/")[0].split(":")[0]
    return f"{base}_USDT_USDT"


class SvgChartDrawer:
    """高美观度金融级矢量 SVG 蜡烛图与指标绘制引擎。"""

    def __init__(self, width: int = 960, height: int = 680):
        self.width = width
        self.height = height

        # 区域划分
        self.margin_left = 65
        self.margin_right = 75
        self.margin_top = 50
        self.margin_bottom = 40

        self.plot_w = self.width - self.margin_left - self.margin_right

        # 主价格图
        self.price_y = self.margin_top
        self.price_h = 330

        # 成交量子图
        self.vol_gap = 25
        self.vol_y = self.price_y + self.price_h + self.vol_gap
        self.vol_h = 100

        # OI 子图
        self.oi_gap = 25
        self.oi_y = self.vol_y + self.vol_h + self.oi_gap
        self.oi_h = 80

    def render(
        self,
        df_candles: pd.DataFrame,
        df_oi: pd.DataFrame,
        meta: Dict[str, Any],
        is_review: bool,
        out_svg_path: Path
    ) -> None:
        """渲染单幅 SVG 图表。"""
        n_bars = len(df_candles)
        if n_bars == 0:
            return

        bar_spacing = self.plot_w / n_bars
        candle_width = max(2.0, min(bar_spacing * 0.7, 14.0))

        # 价格上下界计算 (含 P0)
        p_high = df_candles["high"].max()
        p_low = df_candles["low"].min()
        p0 = meta.get("p0_stop")
        if p0 and not math.isnan(p0):
            p_low = min(p_low, p0)
            p_high = max(p_high, p0)

        # 留 5% 缓冲区
        pad = (p_high - p_low) * 0.08
        y_max = p_high + pad
        y_min = p_low - pad
        price_range = max(1e-6, y_max - y_min)

        def price_to_y(price: float) -> float:
            ratio = (price - y_min) / price_range
            return self.price_y + (1.0 - ratio) * self.price_h

        # 成交量上下界
        v_max = max(df_candles["volume"].max() * 1.15, 1.0)
        vs_thresh = meta.get("vol_threshold")
        if vs_thresh:
            v_max = max(v_max, vs_thresh * 1.15)

        def vol_to_y(vol: float) -> float:
            ratio = vol / v_max
            return self.vol_y + (1.0 - ratio) * self.vol_h

        # OI 上下界
        if not df_oi.empty and "open_interest_value" in df_oi.columns:
            oi_max = df_oi["open_interest_value"].max()
            oi_min = df_oi["open_interest_value"].min()
        else:
            oi_max, oi_min = 1.0, 0.0
        oi_range = max(1e-6, oi_max - oi_min)

        def oi_to_y(val: float) -> float:
            ratio = (val - oi_min) / oi_range
            return self.oi_y + (1.0 - ratio) * self.oi_h

        svg_parts = []
        svg_parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" viewBox="0 0 {self.width} {self.height}" style="background-color: #131722; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;">')

        # 样式表
        svg_parts.append("""
        <defs>
            <linearGradient id="bullGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#26a69a"/><stop offset="100%" stop-color="#26a69a"/></linearGradient>
            <linearGradient id="bearGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#ef5350"/><stop offset="100%" stop-color="#ef5350"/></linearGradient>
            <filter id="badgeShadow" x="-10%" y="-10%" width="120%" height="120%"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.3"/></filter>
        </defs>
        """)

        # 标题栏
        view_title = "复盘视图 (Review View - 含后续路径与平仓)" if is_review else "决策视图 (Decision View - 仅截止决策时刻数据)"
        title_color = "#29b6f6" if is_review else "#ffb74d"
        sym_text = f"{meta['case_id']} | {meta['symbol']} [{meta['target_param']}]"
        svg_parts.append(f'<text x="{self.margin_left}" y="28" fill="#ffffff" font-size="16" font-weight="bold">{sym_text}</text>')
        svg_parts.append(f'<text x="{self.margin_left + 350}" y="28" fill="{title_color}" font-size="14" font-weight="600">[{view_title}]</text>')

        # 背景网格与边界线
        # 主价格区
        svg_parts.append(f'<rect x="{self.margin_left}" y="{self.price_y}" width="{self.plot_w}" height="{self.price_h}" fill="#1e222d" stroke="#2a2e39" stroke-width="1"/>')
        # 成交量子区
        svg_parts.append(f'<rect x="{self.margin_left}" y="{self.vol_y}" width="{self.plot_w}" height="{self.vol_h}" fill="#181b24" stroke="#2a2e39" stroke-width="1"/>')
        svg_parts.append(f'<text x="{self.margin_left + 8}" y="{self.vol_y + 16}" fill="#787b86" font-size="11" font-weight="bold">VOLUME &amp; THRESHOLD</text>')
        # OI 子区
        svg_parts.append(f'<rect x="{self.margin_left}" y="{self.oi_y}" width="{self.plot_w}" height="{self.oi_h}" fill="#181b24" stroke="#2a2e39" stroke-width="1"/>')
        svg_parts.append(f'<text x="{self.margin_left + 8}" y="{self.oi_y + 16}" fill="#787b86" font-size="11" font-weight="bold">OPEN INTEREST (USDT)</text>')

        # 价格水平网格线 (4条)
        for i in range(5):
            grid_p = y_min + (price_range / 4.0) * i
            gy = price_to_y(grid_p)
            svg_parts.append(f'<line x1="{self.margin_left}" y1="{gy}" x2="{self.margin_left + self.plot_w}" y2="{gy}" stroke="#2a2e39" stroke-dasharray="3,3" stroke-width="1"/>')
            svg_parts.append(f'<text x="{self.margin_left + self.plot_w + 6}" y="{gy + 4}" fill="#787b86" font-size="10">{grid_p:.4f}</text>')

        # 冻结 P0 止损线
        if p0 and not math.isnan(p0):
            p0_y = price_to_y(p0)
            svg_parts.append(f'<line x1="{self.margin_left}" y1="{p0_y}" x2="{self.margin_left + self.plot_w}" y2="{p0_y}" stroke="#ef5350" stroke-dasharray="4,4" stroke-width="1.5"/>')
            svg_parts.append(f'<text x="{self.margin_left + self.plot_w + 6}" y="{p0_y + 4}" fill="#ef5350" font-size="10" font-weight="bold">P0: {p0:.4f}</text>')

        # 绘制 K 线与成交量柱
        bar_x_coords = []
        for i, row in df_candles.iterrows():
            bx = self.margin_left + (i + 0.5) * bar_spacing
            bar_x_coords.append((bx, row["date_ms"]))

            op = row["open"]
            cp = row["close"]
            hp = row["high"]
            lp = row["low"]
            vol = row["volume"]

            is_up = cp >= op
            color = "#26a69a" if is_up else "#ef5350"

            # 影线
            hy = price_to_y(hp)
            ly = price_to_y(lp)
            svg_parts.append(f'<line x1="{bx}" y1="{hy}" x2="{bx}" y2="{ly}" stroke="{color}" stroke-width="1.2"/>')

            # 实体
            oy = price_to_y(op)
            cy = price_to_y(cp)
            body_top = min(oy, cy)
            body_h = max(abs(oy - cy), 1.5)
            rx = bx - candle_width / 2.0
            svg_parts.append(f'<rect x="{rx}" y="{body_top}" width="{candle_width}" height="{body_h}" fill="{color}"/>')

            # 成交量柱
            vy = vol_to_y(vol)
            vh = (self.vol_y + self.vol_h) - vy
            svg_parts.append(f'<rect x="{rx}" y="{vy}" width="{candle_width}" height="{vh}" fill="{color}" opacity="0.6"/>')

        # 成交量子图缩量阈值线
        if vs_thresh:
            v_thresh_y = vol_to_y(vs_thresh)
            svg_parts.append(f'<line x1="{self.margin_left}" y1="{v_thresh_y}" x2="{self.margin_left + self.plot_w}" y2="{v_thresh_y}" stroke="#fbc02d" stroke-dasharray="3,3" stroke-width="1.2"/>')
            svg_parts.append(f'<text x="{self.margin_left + self.plot_w + 6}" y="{v_thresh_y + 3}" fill="#fbc02d" font-size="9">VsThresh</text>')

        # 绘制 OI 折线
        if not df_oi.empty and "open_interest_value" in df_oi.columns and len(bar_x_coords) > 1:
            oi_pts = []
            for bx, ts in bar_x_coords:
                # 寻找最接近的 OI
                sub_oi = df_oi[df_oi["date_ms"] <= ts]
                if not sub_oi.empty:
                    val = sub_oi["open_interest_value"].iloc[-1]
                    oy = oi_to_y(val)
                    oi_pts.append(f"{bx:.1f},{oy:.1f}")
            if oi_pts:
                svg_parts.append(f'<polyline points="{" ".join(oi_pts)}" fill="none" stroke="#2979ff" stroke-width="1.8"/>')
                # 标注最新 OI
                last_oi_val = df_oi["open_interest_value"].iloc[-1]
                svg_parts.append(f'<text x="{self.margin_left + self.plot_w + 6}" y="{self.oi_y + self.oi_h / 2}" fill="#2979ff" font-size="9">{last_oi_val/1e6:.1f}M</text>')

        # 标注原信号柱 T0 与确认柱 T1
        t0_ms = meta.get("t0_ms")
        t1_ms = meta.get("t1_ms")
        x_t0 = None
        x_t1 = None
        for bx, ts in bar_x_coords:
            if ts == t0_ms:
                x_t0 = bx
            if ts == t1_ms:
                x_t1 = bx

        if x_t0 is not None:
            svg_parts.append(f'<line x1="{x_t0}" y1="{self.price_y}" x2="{x_t0}" y2="{self.price_y + self.price_h}" stroke="#42a5f5" stroke-dasharray="4,4" stroke-width="1.2"/>')
            svg_parts.append(f'<polygon points="{x_t0-5},{self.price_y - 2} {x_t0+5},{self.price_y - 2} {x_t0},{self.price_y + 8}" fill="#42a5f5"/>')
            svg_parts.append(f'<text x="{x_t0}" y="{self.price_y - 8}" fill="#42a5f5" font-size="11" font-weight="bold" text-anchor="middle">T0: 原信号</text>')

        if x_t1 is not None:
            svg_parts.append(f'<line x1="{x_t1}" y1="{self.price_y}" x2="{x_t1}" y2="{self.price_y + self.price_h}" stroke="#ffa726" stroke-dasharray="4,4" stroke-width="1.2"/>')
            svg_parts.append(f'<polygon points="{x_t1-5},{self.price_y - 2} {x_t1+5},{self.price_y - 2} {x_t1},{self.price_y + 8}" fill="#ffa726"/>')
            svg_parts.append(f'<text x="{x_t1}" y="{self.price_y - 8}" fill="#ffa726" font-size="11" font-weight="bold" text-anchor="middle">T1: 确认柱</text>')

        # 决策徽标卡片 (在主图内部右上角)
        dec = meta.get("c1_decision", "UNKNOWN")
        reason = meta.get("c1_reason", "")
        if dec == "CONFIRMED":
            badge_bg = "rgba(38, 166, 154, 0.2)"
            badge_border = "#26a69a"
            badge_text = f"✔ C1 确认通过 (CONFIRMED)"
            detail_text = f"判定理由: {reason}"
        else:
            badge_bg = "rgba(239, 83, 80, 0.2)"
            badge_border = "#ef5350"
            badge_text = f"✘ C1 确认拒绝 (REJECTED)"
            detail_text = f"放弃理由: {reason}"

        svg_parts.append(f'<g transform="translate({self.margin_left + self.plot_w - 240}, {self.price_y + 12})">')
        svg_parts.append(f'<rect width="230" height="52" rx="4" fill="{badge_bg}" stroke="{badge_border}" stroke-width="1.2" filter="url(#badgeShadow)"/>')
        svg_parts.append(f'<text x="12" y="22" fill="{badge_border}" font-size="11" font-weight="bold">{badge_text}</text>')
        svg_parts.append(f'<text x="12" y="40" fill="#d1d4dc" font-size="10">{detail_text[:28]}</text>')
        svg_parts.append('</g>')

        # 若是复盘视图：绘制真实成交与平仓标记
        if is_review:
            # 标记 C0 成交
            c0_open_ms = meta.get("c0_open_ms")
            c0_open_rate = meta.get("c0_open_rate")
            c0_close_ms = meta.get("c0_close_ms")
            c0_close_rate = meta.get("c0_close_rate")
            c0_exit_why = meta.get("c0_exit_reason", "")
            c0_profit = meta.get("c0_profit_abs", 0.0)

            # 寻找 C0 成交与退出坐标
            x_c0_entry = None
            x_c0_exit = None
            for bx, ts in bar_x_coords:
                if c0_open_ms and abs(ts - c0_open_ms) < 3600 * 1000:
                    x_c0_entry = bx
                if c0_close_ms and abs(ts - c0_close_ms) < 3600 * 1000:
                    x_c0_exit = bx

            if x_c0_entry and c0_open_rate:
                y_c0_e = price_to_y(c0_open_rate)
                svg_parts.append(f'<circle cx="{x_c0_entry}" cy="{y_c0_e}" r="5" fill="#42a5f5" stroke="#ffffff" stroke-width="1.5"/>')
                svg_parts.append(f'<text x="{x_c0_entry + 8}" y="{y_c0_e - 6}" fill="#42a5f5" font-size="10" font-weight="bold">C0入场 @ {c0_open_rate}</text>')

            if x_c0_exit and c0_close_rate:
                y_c0_x = price_to_y(c0_close_rate)
                p_col = "#26a69a" if c0_profit > 0 else "#ef5350"
                svg_parts.append(f'<polygon points="{x_c0_exit},{y_c0_x-6} {x_c0_exit-6},{y_c0_x+6} {x_c0_exit+6},{y_c0_x+6}" fill="{p_col}"/>')
                svg_parts.append(f'<text x="{x_c0_exit + 8}" y="{y_c0_x + 12}" fill="{p_col}" font-size="10" font-weight="bold">C0出场: {c0_exit_why} ({c0_profit:+.2f}U)</text>')

            # 标记 C1 成交（若有）
            c1_filled = meta.get("c1_filled", False)
            if c1_filled:
                c1_open_ms = meta.get("c1_open_ms")
                c1_open_rate = meta.get("c1_open_rate")
                c1_close_ms = meta.get("c1_close_ms")
                c1_close_rate = meta.get("c1_close_rate")
                c1_exit_why = meta.get("c1_exit_reason", "")
                c1_profit = meta.get("c1_profit_abs", 0.0)

                x_c1_entry = None
                x_c1_exit = None
                for bx, ts in bar_x_coords:
                    if c1_open_ms and abs(ts - c1_open_ms) < 3600 * 1000:
                        x_c1_entry = bx
                    if c1_close_ms and abs(ts - c1_close_ms) < 3600 * 1000:
                        x_c1_exit = bx

                if x_c1_entry and c1_open_rate:
                    y_c1_e = price_to_y(c1_open_rate)
                    svg_parts.append(f'<rect x="{x_c1_entry-5}" y="{y_c1_e-5}" width="10" height="10" fill="#ffa726" stroke="#ffffff" stroke-width="1.5"/>')
                    svg_parts.append(f'<text x="{x_c1_entry + 8}" y="{y_c1_e + 14}" fill="#ffa726" font-size="10" font-weight="bold">C1入场 @ {c1_open_rate}</text>')

                if x_c1_exit and c1_close_rate:
                    y_c1_x = price_to_y(c1_close_rate)
                    p_col = "#26a69a" if c1_profit > 0 else "#ef5350"
                    svg_parts.append(f'<circle cx="{x_c1_exit}" cy="{y_c1_x}" r="6" fill="{p_col}" stroke="#ffffff" stroke-width="1.5"/>')
                    svg_parts.append(f'<text x="{x_c1_exit + 8}" y="{y_c1_x - 6}" fill="{p_col}" font-size="10" font-weight="bold">C1出场: {c1_exit_why} ({c1_profit:+.2f}U)</text>')
            else:
                # 明确标注 C1 放弃，无真实头寸
                if x_t1 is not None:
                    svg_parts.append(f'<text x="{x_t1 + 10}" y="{self.price_y + 80}" fill="#ef5350" font-size="11" font-weight="bold">→ C1 确认未通过：放弃入场 (无真实头寸)</text>')

        # 底部时间刻度 (显示 6 个时间标签)
        step = max(1, n_bars // 6)
        for i in range(0, n_bars, step):
            bx, ts = bar_x_coords[i]
            dt_str = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).strftime("%m-%d %H:%M")
            svg_parts.append(f'<text x="{bx}" y="{self.height - 12}" fill="#787b86" font-size="10" text-anchor="middle">{dt_str}</text>')
            svg_parts.append(f'<line x1="{bx}" y1="{self.height - 30}" x2="{bx}" y2="{self.height - 25}" stroke="#2a2e39" stroke-width="1"/>')

        svg_parts.append('</svg>')

        with open(out_svg_path, "w", encoding="utf-8") as f:
            f.write("\n".join(svg_parts))


def main():
    logger.info("=== FT-ENTRY-08 案例图表渲染开始 ===")
    selected_cases = load_reconciliation_and_select()
    df_rec = pd.read_csv(FT_ENTRY_07_DIR / "02_逐候选对照表.csv")

    drawer = SvgChartDrawer(width=960, height=680)
    export_rows = []

    for c in selected_cases:
        cid = c["case_id"]
        eid = c["event_id"]
        sym = c["symbol"]
        param = c["target_param"]

        # 读取该候选在 FT-ENTRY-07 中的具体明细
        sub_rec = df_rec[(df_rec["event_id"] == eid) & (df_rec["param_group"] == param)]
        if sub_rec.empty:
            sub_rec = df_rec[df_rec["event_id"] == eid].iloc[:1]
        rec_row = sub_rec.iloc[0]

        # 读取确认审计表以获取精确 T0 与 T1
        audit_csv = FT_ENTRY_07_DIR / f"out/audit_confirmation_Wyckoff_C1_{param}.csv"
        df_audit = pd.read_csv(audit_csv) if audit_csv.exists() else pd.DataFrame()
        sub_audit = df_audit[df_audit["event_id"] == eid] if not df_audit.empty else pd.DataFrame()

        if not sub_audit.empty:
            a_row = sub_audit.iloc[0]
            t0_ms = int(a_row["orig_signal_time_ms"])
            t1_ms = int(a_row["confirm_time_ms"])
            p0 = float(a_row["orig_P0"])
            c1_dec = a_row["decision"]
            c1_reason = a_row["reason"]
        else:
            p0 = float(rec_row["P0_stop"]) if pd.notna(rec_row["P0_stop"]) else None
            c1_dec = rec_row["c1_decision"]
            c1_reason = str(rec_row["c1_reason"])
            t0_ms = int(datetime.fromisoformat(rec_row["c0_open_time"]).timestamp() * 1000) - 15 * 60 * 1000
            t1_ms = t0_ms + 3600 * 1000

        # 读取 1h K 线与 5m OI
        feather_sym = get_feather_symbol(sym)
        kline_path = DATA_DIR / f"{feather_sym}-1h-futures.feather"
        oi_path = DATA_DIR / f"{feather_sym}-5m-open_interest.feather"

        if not kline_path.exists():
            logger.error(f"未找到 K 线数据: {kline_path}")
            continue

        df_1h = pd.read_feather(kline_path)
        # 注意: feather 数据 date 为 datetime64[ms, UTC]，astype('int64') 本身即为毫秒
        df_1h["date_ms"] = df_1h["date"].astype("int64")

        df_oi = pd.read_feather(oi_path) if oi_path.exists() else pd.DataFrame()
        if not df_oi.empty:
            df_oi["date_ms"] = df_oi["date"].astype("int64")

        # 计算 20 周期成交量均线作为参考
        df_1h["vma20"] = df_1h["volume"].rolling(20).mean()

        # 计算决策视图窗口: [T0 - 24小时, T1]
        start_dec_ms = t0_ms - 24 * 3600 * 1000
        end_dec_ms = t1_ms
        df_dec = df_1h[(df_1h["date_ms"] >= start_dec_ms) & (df_1h["date_ms"] <= end_dec_ms)].copy().reset_index(drop=True)
        sub_oi_dec = df_oi[(df_oi["date_ms"] >= start_dec_ms) & (df_oi["date_ms"] <= end_dec_ms)].copy().reset_index(drop=True) if not df_oi.empty else pd.DataFrame()

        # 读取 C0 和 C1 交易退出时刻以确定复盘窗口
        c0_open_ms = int(datetime.fromisoformat(rec_row["c0_open_time"]).timestamp() * 1000) if pd.notna(rec_row["c0_open_time"]) else None
        c0_trades_csv = FT_ENTRY_07_DIR / f"out/trades_Wyckoff_C_{param}.csv"
        df_c0_t = pd.read_csv(c0_trades_csv) if c0_trades_csv.exists() else pd.DataFrame()
        sub_c0_t = df_c0_t[df_c0_t["enter_tag"].str.contains(eid, na=False)] if not df_c0_t.empty else pd.DataFrame()

        c0_close_ms = None
        c0_exit_rate = None
        if not sub_c0_t.empty:
            c0_row_t = sub_c0_t.iloc[0]
            c0_close_ms = int(datetime.fromisoformat(c0_row_t["close_date"]).timestamp() * 1000)
            c0_exit_rate = float(c0_row_t["close_rate"])

        c1_trades_csv = FT_ENTRY_07_DIR / f"out/trades_Wyckoff_C1_{param}.csv"
        df_c1_t = pd.read_csv(c1_trades_csv) if c1_trades_csv.exists() else pd.DataFrame()
        sub_c1_t = df_c1_t[df_c1_t["enter_tag"].str.contains(eid, na=False)] if not df_c1_t.empty else pd.DataFrame()

        c1_filled = rec_row["c1_filled"]
        c1_open_ms = None
        c1_close_ms = None
        c1_exit_rate = None
        if not sub_c1_t.empty and c1_filled:
            c1_row_t = sub_c1_t.iloc[0]
            c1_open_ms = int(datetime.fromisoformat(c1_row_t["open_date"]).timestamp() * 1000)
            c1_close_ms = int(datetime.fromisoformat(c1_row_t["close_date"]).timestamp() * 1000)
            c1_exit_rate = float(c1_row_t["close_rate"])

        # 复盘窗口终点
        all_exit_ms = [ms for ms in [c0_close_ms, c1_close_ms] if ms is not None]
        if all_exit_ms:
            end_rev_ms = max(all_exit_ms) + 6 * 3600 * 1000
        else:
            end_rev_ms = t1_ms + 48 * 3600 * 1000  # 放弃入场后展示 48 小时

        # 控制复盘视图最大柱数不超过 100 根 (防止过密)
        df_rev = df_1h[(df_1h["date_ms"] >= start_dec_ms) & (df_1h["date_ms"] <= end_rev_ms)].copy().reset_index(drop=True)
        if df_rev.empty:
            df_rev = df_dec.copy()
        if len(df_rev) > 100:
            df_rev = df_rev.iloc[:100]
        sub_oi_rev = df_oi[(df_oi["date_ms"] >= start_dec_ms) & (df_oi["date_ms"] <= df_rev["date_ms"].iloc[-1])].copy().reset_index(drop=True) if not df_oi.empty and not df_rev.empty else pd.DataFrame()

        # 计算当时对应的缩量阈值: Vs * V20
        # 取 T1 柱的 vma20
        row_t1 = df_1h[df_1h["date_ms"] == t1_ms]
        v_thresh = None
        if not row_t1.empty and pd.notna(row_t1["vma20"].iloc[0]):
            # 解析 param 中的 vs
            vs_val = 0.6
            if "Vs04" in param:
                vs_val = 0.4
            elif "Vs08" in param:
                vs_val = 0.8
            v_thresh = float(row_t1["vma20"].iloc[0]) * vs_val

        meta = {
            "case_id": cid,
            "event_id": eid,
            "symbol": sym,
            "target_param": param,
            "p0_stop": p0,
            "vol_threshold": v_thresh,
            "t0_ms": t0_ms,
            "t1_ms": t1_ms,
            "c1_decision": c1_dec,
            "c1_reason": c1_reason,
            "c0_filled": rec_row["c0_filled"],
            "c0_open_ms": c0_open_ms,
            "c0_open_rate": float(rec_row["c0_open_rate"]) if pd.notna(rec_row["c0_open_rate"]) else None,
            "c0_close_ms": c0_close_ms,
            "c0_close_rate": c0_exit_rate,
            "c0_exit_reason": rec_row["c0_exit_reason"],
            "c0_profit_abs": float(rec_row["c0_profit_abs"]) if pd.notna(rec_row["c0_profit_abs"]) else 0.0,
            "c1_filled": c1_filled,
            "c1_open_ms": c1_open_ms,
            "c1_open_rate": float(rec_row["c1_open_rate"]) if pd.notna(rec_row["c1_open_rate"]) else None,
            "c1_close_ms": c1_close_ms,
            "c1_close_rate": c1_exit_rate,
            "c1_exit_reason": rec_row["c1_exit_reason"],
            "c1_profit_abs": float(rec_row["c1_profit_abs"]) if pd.notna(rec_row["c1_profit_abs"]) else 0.0,
        }

        # 1. 渲染决策视图
        svg_dec_path = CHARTS_DIR / f"{cid}_decision.svg"
        drawer.render(df_dec, sub_oi_dec, meta, is_review=False, out_svg_path=svg_dec_path)

        # 2. 渲染复盘视图
        svg_rev_path = CHARTS_DIR / f"{cid}_review.svg"
        drawer.render(df_rev, sub_oi_rev, meta, is_review=True, out_svg_path=svg_rev_path)

        logger.info(f"已生成案例 {cid} 图表: {svg_dec_path.name} & {svg_rev_path.name}")

        # 汇总各参数下的决策
        all_sub = df_rec[df_rec["event_id"] == eid]
        params_summary = "; ".join([f"{r['param_group']}:C0={r['c0_profit_abs']:.1f}U,C1_dec={r['c1_decision']}" for _, r in all_sub.iterrows()])

        export_rows.append({
            "case_id": cid,
            "category_code": c["category_code"],
            "category_title": c["category_title"],
            "slot_index": c["slot_index"],
            "event_id": eid,
            "symbol": sym,
            "target_param": param,
            "t0_signal_time": datetime.fromtimestamp(t0_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "t1_confirm_time": datetime.fromtimestamp(t1_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "p0_stop": p0,
            "c1_decision": c1_dec,
            "c1_reason": c1_reason,
            "c0_filled": meta["c0_filled"],
            "c0_open_rate": meta["c0_open_rate"],
            "c0_exit_rate": meta["c0_close_rate"],
            "c0_profit_abs": meta["c0_profit_abs"],
            "c0_exit_reason": meta["c0_exit_reason"],
            "c1_filled": meta["c1_filled"],
            "c1_open_rate": meta["c1_open_rate"],
            "c1_exit_rate": meta["c1_close_rate"],
            "c1_profit_abs": meta["c1_profit_abs"],
            "c1_exit_reason": meta["c1_exit_reason"],
            "all_params_summary": params_summary
        })

    df_out = pd.DataFrame(export_rows)
    out_csv = FT_ENTRY_08_DIR / "01_选样清单表.csv"
    df_out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    logger.info(f"选样清单表已导出: {out_csv}，共 {len(df_out)} 行。")


if __name__ == "__main__":
    main()
