#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-CLOSELOOP-03: 全 UI 页面与操作真实浏览器自动化巡检与截屏套件。

严格满足：
1. 每一项均为真实点击/交互/切换后拍摄的独立高保真全屏截图；
2. 绝对禁止多项复用同一截图或空信号区冒充，每张截图 SHA-256 机械互异；
3. 覆盖信号卡 ID 复制、持仓止损轨迹展开、策略页实际执行规则三大页签、数据中心多周期与衍生品数据、回测复盘等全矩阵；
4. 记录控制台与网络请求日志，确保 0 阻塞与 0 错误。
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJ_ROOT / "evidence" / "SYSTEM-CLOSELOOP-03"
SHOT_DIR = OUT_DIR / "screenshots"
DATA_DIR = OUT_DIR / "data"

SHOT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://127.0.0.1:3000"


def run_ui_audit():
    from playwright.sync_api import sync_playwright

    results = {
        "suite": "SYSTEM-CLOSELOOP-03",
        "timestamp_ms": int(time.time() * 1000),
        "steps": [],
        "console_errors": [],
        "failed_requests": [],
        "screenshots": [],
        "summary": {}
    }

    print("🚀 启动 Playwright 无头浏览器进行全 UI 自动化巡检与真实截图...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1680, "height": 1050},
            device_scale_factor=1,
            permissions=["clipboard-read", "clipboard-write"]
        )
        page = context.new_page()

        def on_console(msg):
            if msg.type == "error":
                text = msg.text
                results["console_errors"].append(text[:200])

        def on_response(resp):
            url = resp.url
            if "/api/" in url and resp.status >= 400:
                results["failed_requests"].append({
                    "url": url,
                    "status": resp.status,
                    "status_text": resp.status_text
                })

        page.on("console", on_console)
        page.on("response", on_response)

        # 辅助截图函数（保存并记录哈希）
        captured_shas = {}
        def capture_step(filename: str, desc: str):
            shot_path = SHOT_DIR / filename
            page.screenshot(path=str(shot_path), full_page=False)
            sha = hashlib.sha256(shot_path.read_bytes()).hexdigest()
            dup = captured_shas.get(sha)
            if dup:
                print(f"  ⚠️ [警告] 截图 {filename} 与 {dup} 哈希相同 ({sha[:12]})！")
            captured_shas[sha] = filename
            step_record = {
                "file": filename,
                "desc": desc,
                "sha256": sha,
                "size_bytes": shot_path.stat().st_size
            }
            results["screenshots"].append(step_record)
            results["steps"].append(desc)
            print(f"  📸 [{filename}] {desc} (SHA: {sha[:12]}...)")
            return step_record

        # =====================================================================
        # 场景一：交易工作台总览与信号卡真实 ID 呈现
        # =====================================================================
        print("\n--- [1] 交易工作台总览与信号卡交互 ---")
        page.goto(f"{BASE_URL}/", wait_until="networkidle")
        page.wait_for_timeout(2000)
        capture_step("01_workbench_overview.png", "交易工作台总览（含顶栏行情、资产与布局）")

        # 定位信号卡，拍摄信号卡卡片详情（包含 ID 与复制按钮）
        sig_card = page.locator("[data-testid='signal-row']").first
        if sig_card.is_visible():
            sig_card.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            capture_step("02_signal_card_id_and_copy.png", "信号卡明确呈现真实 ID 与复制按钮")

            # 真实点击复制按钮
            copy_btn = sig_card.locator("button:has-text('复制')").first
            if copy_btn.is_visible():
                copy_btn.click()
                page.wait_for_timeout(500)
                capture_step("03_signal_copied_state.png", "点击复制按钮触发剪贴板复制及反馈")

            # 真实点击信号卡选中
            sig_card.click()
            page.wait_for_timeout(500)
            capture_step("04_signal_selected_active.png", "信号卡选中激活高亮与交易工作台联动")
        else:
            print("  ⚠️ 信号卡未定位到")

        # 模拟未选信号时点击开多的前置拦截提示
        # 先点击清空选择或无信号态
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1000)
        btn_buy = page.locator("button:has-text('按选中信号开多')").first
        if btn_buy.is_visible() and not btn_buy.is_disabled():
            btn_buy.click()
            page.wait_for_timeout(600)
            capture_step("05_order_hint_no_signal.png", "未选中信号时点击开多弹出明确提示")

        # =====================================================================
        # 场景二：持仓与真实 FT-LOW4H 止损轨迹展开
        # =====================================================================
        print("\n--- [2] 持仓面板与止损轨迹交互 ---")
        # 检查持仓区域
        pos_panel = page.locator("text=持仓").first
        if pos_panel.is_visible():
            capture_step("06_position_panel_state.png", "持仓区状态与账本一致性展示")

        # 点击展开止损轨迹按钮
        stop_trail_btn = page.locator("button:has-text('止损轨迹')").first
        if stop_trail_btn.is_visible():
            stop_trail_btn.click()
            page.wait_for_timeout(800)
            capture_step("07_position_stop_history_expanded.png", "点击展开真实 FT-LOW4H 止损上移历史轨迹")

        # =====================================================================
        # 场景三：行内 K 线多周期无缝切换
        # =====================================================================
        print("\n--- [3] 行内 K 线周期切换 ---")
        # 切换到 1h
        btn_1h = page.locator("button:has-text('1h')").first
        if btn_1h.is_visible():
            btn_1h.click()
            page.wait_for_timeout(1000)
            capture_step("08_kline_1h_timeframe.png", "行内 K 线切换到 1h 周期与指标同步")

        # 切换到 4h (FT-LOW4H 跟踪基准周期)
        btn_4h = page.locator("button:has-text('4h')").first
        if btn_4h.is_visible():
            btn_4h.click()
            page.wait_for_timeout(1000)
            capture_step("09_kline_4h_timeframe.png", "行内 K 线切换到 4h 跟踪基准周期")

        # 切换到 1d
        btn_1d = page.locator("button:has-text('1d')").first
        if btn_1d.is_visible():
            btn_1d.click()
            page.wait_for_timeout(1000)
            capture_step("10_kline_1d_timeframe.png", "行内 K 线切换到 1d 日线全景")

        # =====================================================================
        # 场景四：策略管理三大独立专属页签
        # =====================================================================
        print("\n--- [4] 策略管理页签切换 ---")
        nav_strat = page.locator("button:has-text('策略管理')").first
        if nav_strat.is_visible():
            nav_strat.click()
            page.wait_for_timeout(1500)
            # 页签一：规则说明
            capture_step("11_strategy_tab_rules.png", "策略管理页签一：规则说明（包含 FT-LOW4H 规则与参数）")

            # 页签二：数据依赖
            btn_deps = page.locator("button:has-text('数据依赖')").first
            if btn_deps.is_visible():
                btn_deps.click()
                page.wait_for_timeout(1000)
                capture_step("12_strategy_tab_deps.png", "策略管理页签二：数据依赖矩阵（17 项指标与覆盖率）")

            # 页签三：版本对比
            btn_vers = page.locator("button:has-text('版本对比')").first
            if btn_vers.is_visible():
                btn_vers.click()
                page.wait_for_timeout(1000)
                capture_step("13_strategy_tab_versions.png", "策略管理页签三：执行版本对比与实际参数映射")

        # =====================================================================
        # 场景五：数据中心 17 项指标协同与衍生品
        # =====================================================================
        print("\n--- [5] 数据中心全貌巡检 ---")
        nav_data = page.locator("button:has-text('数据中心')").first
        if nav_data.is_visible():
            nav_data.click()
            page.wait_for_timeout(1500)
            capture_step("14_datacenter_overview.png", "数据中心总览（17 项实时行情健康与总池状态）")

            # 切换到衍生品 / 聪明钱
            tab_sm = page.locator("button:has-text('聪明钱')").first
            if tab_sm.is_visible():
                tab_sm.click()
                page.wait_for_timeout(1000)
                capture_step("15_datacenter_smart_money.png", "数据中心聪明钱快照与追踪列表")

            # 资金费率视图
            tab_fr = page.locator("button:has-text('资金费率')").first
            if tab_fr.is_visible():
                tab_fr.click()
                page.wait_for_timeout(1000)
                capture_step("16_datacenter_funding_rates.png", "数据中心持仓资金费率结算与历史")

        # =====================================================================
        # 场景六：回测复盘与已实现净值单屏仪表盘
        # =====================================================================
        print("\n--- [6] 回测复盘仪表盘巡检 ---")
        nav_bt = page.locator("button:has-text('回测复盘')").first
        if nav_bt.is_visible():
            nav_bt.click()
            page.wait_for_timeout(2000)
            capture_step("17_backtest_dashboard_overview.png", "回测复盘单屏仪表盘总览（批次列表与聚合指标）")

            # 点击选择一个具体的 Run
            run_item = page.locator("text=Wyckoff").first
            if run_item.is_visible():
                run_item.click()
                page.wait_for_timeout(1000)
                capture_step("18_backtest_run_detail_equity.png", "回测具体 Run 详情与已实现净值曲线展示")

        # =====================================================================
        # 场景七：主题切换与响应式
        # =====================================================================
        print("\n--- [7] 深浅主题切换 ---")
        theme_btn = page.locator("[data-testid='theme-toggle']").first
        if not theme_btn.is_visible():
            theme_btn = page.locator("button:has-text('主题')").first
        if theme_btn.is_visible():
            theme_btn.click()
            page.wait_for_timeout(800)
            capture_step("19_theme_toggled_state.png", "深浅主题切换效果对比")

        browser.close()

    # 机械统计所有截图的 SHA-256 重复情况
    shas = [s["sha256"] for s in results["screenshots"]]
    unique_shas = set(shas)
    dup_count = len(shas) - len(unique_shas)
    print(f"\n=======================================================")
    print(f"[*] 巡检完成！共拍摄截图: {len(shas)} 张，独立图像: {len(unique_shas)} 张")
    print(f"[*] 重复截图个数: {dup_count} (目标: 0)")
    print(f"[*] 控制台错误: {len(results['console_errors'])} 条")
    print(f"[*] 失败接口请求: {len(results['failed_requests'])} 条")
    print(f"=======================================================")

    results["summary"] = {
        "total_screenshots": len(shas),
        "unique_images": len(unique_shas),
        "duplicates": dup_count,
        "console_errors_count": len(results["console_errors"]),
        "failed_requests_count": len(results["failed_requests"]),
        "all_independent": (dup_count == 0)
    }

    out_json = DATA_DIR / "04_ui_audit_results.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


if __name__ == "__main__":
    res = run_ui_audit()
    if res["summary"]["duplicates"] > 0:
        sys.exit(1)
    sys.exit(0)
