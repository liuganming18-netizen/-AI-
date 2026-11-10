import asyncio
import os
import sys
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:3010"
SCREENSHOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../evidence/V9-UI-READONLY-02/screenshots"))

async def run_e2e():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        page_errors = []
        console_msgs = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("console", lambda msg: console_msgs.append(f"[{msg.type.upper()}] {msg.text}"))

        print(f"[Step 1] 导航至 {BASE_URL} 并验证初始加载...")
        await page.goto(BASE_URL)
        await page.wait_for_timeout(1500)
        title = await page.title()
        assert "GM · AI趋势研究系统" in title, f"标题不匹配: {title}"

        # 检查导航栏与状态
        nav_status = await page.text_content("#nav-status")
        print(f"  状态栏文字: {nav_status.strip()}")

        # 验证全市场代币列表
        rows = await page.locator("#market-rows tr.market-row").count()
        print(f"  已加载全市场标的行数: {rows}")
        assert rows >= 500, f"标的行数异常: {rows}"

        # 截图 1: 加密货币主页全景
        shot1 = os.path.join(SCREENSHOT_DIR, "01_crypto_market_overview.png")
        await page.screenshot(path=shot1, full_page=False)
        print(f"  已保存截图: {shot1}")

        # [Step 2] 搜索与过滤测试
        print("[Step 2] 验证搜索与筛选交互...")
        await page.fill("#search", "ETH")
        await page.wait_for_timeout(300)
        eth_rows = await page.locator("#market-rows tr.market-row").count()
        print(f"  搜索 'ETH' 结果数: {eth_rows}")
        assert 1 <= eth_rows < 50, f"搜索结果不合预期: {eth_rows}"

        # 清空筛选
        await page.click('button[data-clear="1"]')
        await page.wait_for_timeout(300)
        reset_rows = await page.locator("#market-rows tr.market-row").count()
        assert reset_rows == rows, f"清空筛选未能恢复: {reset_rows}"

        # 来源筛选 (OKX)
        await page.select_option("#source", "OKX")
        await page.wait_for_timeout(300)
        okx_rows = await page.locator("#market-rows tr.market-row").count()
        print(f"  OKX 标的筛选数: {okx_rows}")
        assert okx_rows == 23, f"OKX 标的数量应为 23，实测: {okx_rows}"

        # 恢复全部来源
        await page.select_option("#source", "all")
        await page.wait_for_timeout(300)

        # [Step 3] 标的行展开、K线与OI折叠、单位切换
        print("[Step 3] 验证标的展开、K线与OI独立折叠及单位切换...")
        # 初始加载时默认第一行展开
        init_expanded = await page.locator("tr.expanded").count()
        print(f"  初始展开行数: {init_expanded}")
        assert init_expanded == 1, "初始加载应默认展开第一行标的"

        # 截图 2: 详情展开
        shot2 = os.path.join(SCREENSHOT_DIR, "02_crypto_expanded_detail.png")
        await page.screenshot(path=shot2, full_page=False)
        print(f"  已保存截图: {shot2}")

        # 折叠 K 线
        await page.click('button[data-fold="k"]')
        await page.wait_for_timeout(300)
        # 折叠 OI
        await page.click('button[data-fold="oi"]')
        await page.wait_for_timeout(300)
        # 再展开 K 线与 OI
        await page.click('button[data-fold="k"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-fold="oi"]')
        await page.wait_for_timeout(300)

        # 切换周期为 1h
        await page.click('button[data-tf="1h"]')
        await page.wait_for_timeout(500)

        # 切换 OI 单位
        await page.click('button[data-oiunit="amount"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-oiunit="usd"]')
        await page.wait_for_timeout(300)

        # 关闭详情
        await page.click('button[data-close="1"]')
        await page.wait_for_timeout(300)
        assert await page.locator("tr.expanded").count() == 0, "点击关闭按钮应收起详情行"

        # 再次点击第一行标的展开
        first_row = page.locator("#market-rows tr.market-row").first
        await first_row.click()
        await page.wait_for_timeout(500)
        assert await page.locator("tr.expanded").count() == 1, "再次点击第一行标的应重新展开详情"

        # [Step 4] 导航至数据监控与三级拓扑画布测试
        print("[Step 4] 切换至【数据监控】，测试三级拓扑下钻与故障演示...")
        await page.click('button[data-nav="数据监控"]')
        await page.wait_for_timeout(600)

        # 截图 3: 数据监控总览
        shot3 = os.path.join(SCREENSHOT_DIR, "03_data_monitor_topology.png")
        await page.screenshot(path=shot3, full_page=False)
        print(f"  已保存截图: {shot3}")

        # 测试三级下钻：点击统一数据层模块 (node 2)
        await page.click('button[data-mnode="2"]')
        await page.wait_for_timeout(300)
        crumb = await page.text_content(".breadcrumbs")
        print(f"  一级下钻面包屑: {crumb.strip()}")
        assert "统一数据层" in crumb

        # 下钻至任务层：点击服务 0
        await page.click('button[data-mnode="0"]')
        await page.wait_for_timeout(300)
        crumb2 = await page.text_content(".breadcrumbs")
        print(f"  二级下钻面包屑: {crumb2.strip()}")

        # 截图 4: 三级下钻
        shot4 = os.path.join(SCREENSHOT_DIR, "04_data_monitor_drilldown.png")
        await page.screenshot(path=shot4, full_page=False)
        print(f"  已保存截图: {shot4}")

        # 返回全链路
        await page.click('button[data-level="0"]')
        await page.wait_for_timeout(300)

        # 故障演示
        await page.click('button[data-fault="gap"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-fault="none"]')
        await page.wait_for_timeout(300)

        # 数据覆盖 Tab 切换
        await page.click('button[data-datatab="OI"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-datatab="资金费率"]')
        await page.wait_for_timeout(300)

        # [Step 5] 导航至策略分析测试
        print("[Step 5] 切换至【策略分析】，测试阶段与说明切换...")
        await page.click('button[data-nav="策略分析"]')
        await page.wait_for_timeout(600)

        # 点击中期与末期
        await page.click('button[data-stage="中期"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-stage="末期"]')
        await page.wait_for_timeout(300)

        # Tab 切换
        await page.click('button[data-stratab="数据依赖"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-stratab="版本对比"]')
        await page.wait_for_timeout(300)

        # 截图 5: 策略分析
        shot5 = os.path.join(SCREENSHOT_DIR, "05_strategy_analysis.png")
        await page.screenshot(path=shot5, full_page=False)
        print(f"  已保存截图: {shot5}")

        # [Step 6] 导航至历史回测测试
        print("[Step 6] 切换至【历史回测】，验证绩效空态与逐根步进交互...")
        await page.click('button[data-nav="历史回测"]')
        await page.wait_for_timeout(600)

        # 验证 12 项指标全为空态 '—'
        nums = await page.locator(".card .num").all_text_contents()
        dash_count = sum(1 for n in nums if n.strip() == "—")
        print(f"  绩效空态指标数: {dash_count}")
        assert dash_count >= 12, f"未达到全部绩效空态: {dash_count}"

        # 样本集切换
        await page.click('button[data-scope="样本内"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-scope="样本外"]')
        await page.wait_for_timeout(300)

        # 逐笔 Tab 切换
        await page.click('button[data-backtab="权益与回撤"]')
        await page.wait_for_timeout(300)
        await page.click('button[data-backtab="科学性门禁"]')
        await page.wait_for_timeout(300)

        # 截图 6: 历史回测
        shot6 = os.path.join(SCREENSHOT_DIR, "06_backtest_workbench.png")
        await page.screenshot(path=shot6, full_page=False)
        print(f"  已保存截图: {shot6}")

        # [Step 7] 高分辨率多视口验证 (2560x1440)
        print("[Step 7] 验证 2560x1440 高分辨率展示...")
        await page.set_viewport_size({"width": 2560, "height": 1440})
        await page.wait_for_timeout(500)
        shot7 = os.path.join(SCREENSHOT_DIR, "07_resolution_2560x1440.png")
        await page.screenshot(path=shot7, full_page=False)
        print(f"  已保存截图: {shot7}")

        # 切回 1920x1080
        await page.set_viewport_size({"width": 1920, "height": 1080})
        await page.wait_for_timeout(300)
        shot8 = os.path.join(SCREENSHOT_DIR, "08_resolution_1920x1080.png")
        await page.screenshot(path=shot8, full_page=False)
        print(f"  已保存截图: {shot8}")

        # 校验页面无 JavaScript 异常
        print("[Step 8] 核验页面控制台与异常状态...")
        if page_errors:
            print("FATAL: 检测到页面 JavaScript 异常:")
            for e in page_errors:
                print(f"  {e}")
            sys.exit(1)
        else:
            print("  PAGE_ERROR 数量为 0，交互运行无未捕获异常！")

        print("全流程浏览器交互验收 100% 通过！")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_e2e())
