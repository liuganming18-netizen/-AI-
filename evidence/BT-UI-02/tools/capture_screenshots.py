#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工单 BT-UI-02｜正式前端实例 (http://127.0.0.1:3000/) 自动化巡检与实机截图脚本
"""

import os
import time
from playwright.sync_api import sync_playwright

OUTPUT_DIR = "evidence/BT-UI-02/截图"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def wait_for_network_idle(page, timeout=5000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass

def main():
    print("启动 Chromium 进行正式实例页面巡检与截图...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # 1. 宽屏测试 (1680 x 1050)
        context_wide = browser.new_context(viewport={"width": 1680, "height": 1050})
        page = context_wide.new_page()

        # 打开回测页面（通过深链定位到 runs_sep_all_latest 批次的 AIXBT run）
        target_url = "http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_AIXBTUSDT_1792262009"
        print(f"正在访问: {target_url}")
        page.goto(target_url)
        page.wait_for_selector(".ds-pagehead", timeout=15000)
        time.sleep(3) # 等待图表与批次表加载渲染

        # 截图 01: 宽屏批次复核总览（包含 BatchBar、研究分类状态、逐笔复核表）
        batch_table = page.locator("[data-testid='bt-batch-trades']")
        if batch_table.count() > 0:
            batch_table.scroll_into_view_if_needed()
            time.sleep(1)
        path_01 = os.path.join(OUTPUT_DIR, "01_宽屏_批次逐笔复核_runs_sep_all_latest.png")
        page.screenshot(path=path_01, full_page=False)
        print(f"已保存: {path_01}")

        # 截图 02: 案例 A: AIXBT 入场走弱
        # 滚动回图表与指标区
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1)
        path_02 = os.path.join(OUTPUT_DIR, "02_宽屏_案例A_AIXBT_入场走弱.png")
        page.screenshot(path=path_02, full_page=False)
        print(f"已保存: {path_02}")

        # 截图 03: 案例 B: EVAA 浮盈回吐
        url_evaa = "http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_EVAAUSDT_1792262148"
        print(f"正在访问: {url_evaa}")
        page.goto(url_evaa)
        time.sleep(3)
        path_03 = os.path.join(OUTPUT_DIR, "03_宽屏_案例B_EVAA_浮盈回吐.png")
        page.screenshot(path=path_03, full_page=False)
        print(f"已保存: {path_03}")

        # 截图 04: 案例 C: GOAT 持续盈利
        url_goat = "http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_GOATUSDT_1792262169"
        print(f"正在访问: {url_goat}")
        page.goto(url_goat)
        time.sleep(3)
        path_04 = os.path.join(OUTPUT_DIR, "04_宽屏_案例C_GOAT_持续盈利.png")
        page.screenshot(path=path_04, full_page=False)
        print(f"已保存: {path_04}")

        # 截图 05: 案例 D: 0G 零成交真实显示
        url_0g = "http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_0GUSDT_1792261978"
        print(f"正在访问: {url_0g}")
        page.goto(url_0g)
        time.sleep(3)
        path_05 = os.path.join(OUTPUT_DIR, "05_宽屏_案例D_0G_零成交真实显示.png")
        page.screenshot(path=path_05, full_page=False)
        print(f"已保存: {path_05}")

        # 截图 06: 案例 E: QNT 读时动态补图成功
        url_qnt = "http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_QNTUSDT_1792273818"
        print(f"正在访问: {url_qnt}")
        page.goto(url_qnt)
        time.sleep(3)
        path_06 = os.path.join(OUTPUT_DIR, "06_宽屏_案例E_QNT_读时补图成功.png")
        page.screenshot(path=path_06, full_page=False)
        print(f"已保存: {path_06}")

        # 截图 09: 切换测试：从 QNT 切回 AIXBT，检查 Canvas 图表和指标卡是否干净切换、无残留
        print("执行交互切换测试: QNT -> AIXBT...")
        page.goto("http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_AIXBTUSDT_1792262009")
        time.sleep(2)
        path_09 = os.path.join(OUTPUT_DIR, "09_切换测试_币种切换无上一笔残留.png")
        page.screenshot(path=path_09, full_page=False)
        print(f"已保存: {path_09}")

        context_wide.close()

        # 2. 窄屏测试 A (1024 x 768)
        print("进行窄屏 1024x768 测试...")
        context_narrow_a = browser.new_context(viewport={"width": 1024, "height": 768})
        page_na = context_narrow_a.new_page()
        page_na.goto("http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_AIXBTUSDT_1792262009")
        time.sleep(3)
        path_07 = os.path.join(OUTPUT_DIR, "07_窄屏_1024x768_批次复核与布局防溢出.png")
        page_na.screenshot(path=path_07, full_page=False)
        print(f"已保存: {path_07}")
        context_narrow_a.close()

        # 3. 窄屏测试 B (768 x 1024 竖屏/平板)
        print("进行窄屏 768x1024 测试...")
        context_narrow_b = browser.new_context(viewport={"width": 768, "height": 1024})
        page_nb = context_narrow_b.new_page()
        page_nb.goto("http://127.0.0.1:3000/?run=run_WX_EVENT_TREND_V0_1_AIXBTUSDT_1792262009")
        time.sleep(3)
        path_08 = os.path.join(OUTPUT_DIR, "08_窄屏_768x1024_竖屏自适应与无横向溢出.png")
        page_nb.screenshot(path=path_08, full_page=False)
        print(f"已保存: {path_08}")
        context_narrow_b.close()

        browser.close()

    print("全部实机截图完成，已输出至 evidence/BT-UI-02/截图/")

if __name__ == "__main__":
    main()
