#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据中心「指标」子页截图（验收用，不是只截一个首页）。

流程：首页 → 侧栏「数据中心」→ 顶栏「指标」→ 逐个案例截图 → 展开折叠证据。
产出到 `evidence/DATA-UI-01/screenshots/`。

用法：
  python shoot_ui.py                 # 默认 4 个案例
  python shoot_ui.py --only btc      # 只截一个
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(EVID, "screenshots")
BASE = os.environ.get("UI_BASE") or "http://127.0.0.1:3000/"
VIEWPORT = {"width": 1680, "height": 1200}

# (文件名, 交易所按钮文字, 标的, 周期, 说明)
CASES = [
    ("01_btc_1h", "币安", "BTCUSDT", "1h", "主流标的"),
    ("02_eth_1h", "币安", "ETHUSDT", "1h", "主流标的"),
    ("03_1000cat_1h", "币安", "1000CATUSDT", "1h", "历史有指标的小币"),
    ("04_okx_okb_1h", "欧易", "OKB-USDT-SWAP", "1h", "欧易补充位"),
]


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    shots = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport=VIEWPORT, device_scale_factor=2)
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3000)

        pg.get_by_text("数据中心", exact=True).first.click()
        pg.wait_for_timeout(3000)
        pg.get_by_text("指标", exact=True).first.click()
        pg.wait_for_timeout(5000)

        for name, ex_label, sym, period, label in CASES:
            if only and only not in name:
                continue
            pg.get_by_text(ex_label, exact=True).first.click()
            pg.wait_for_timeout(600)
            box = pg.get_by_placeholder("标的，如 BTCUSDT").first
            box.fill(sym)
            box.press("Enter")
            pg.wait_for_timeout(1200)
            # 周期按钮：精确匹配文本
            pg.get_by_role("button", name=period, exact=True).first.click()
            pg.wait_for_timeout(5000)
            path = os.path.join(OUT, f"{name}.png")
            pg.screenshot(path=path, full_page=True)
            shots.append((path, f"{sym} {period} {label}"))
            print(f"截图 {os.path.basename(path)}")

        # 折叠证据展开
        det = pg.locator("details").first
        if det.count():
            det.click()
            pg.wait_for_timeout(1500)
            path = os.path.join(OUT, "05_evidence_open.png")
            pg.screenshot(path=path, full_page=True)
            shots.append((path, "详细证据展开"))
            print("截图 05_evidence_open.png")

        br.close()
    for p_, d in shots:
        print(f"{os.path.basename(p_)}  {os.path.getsize(p_)} B  {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
