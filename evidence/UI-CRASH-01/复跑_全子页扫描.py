#!/usr/bin/env python3
"""逐个子页扫描：数据中心 / 策略管理 / 回测复盘 的所有子页 tab。"""
import json
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000/"
PLAN = {
    "数据中心": ["主视窗", "待补数据与核销清单", "全资产协同矩阵", "K线", "OI", "费率", "聪明钱", "指标"],
    "策略管理": ["规则说明", "数据依赖", "版本对比"],
    "回测复盘": ["验收复盘", "完整回测工作台", "结果身份", "订单明细", "拒单明细", "资金费结算", "口径与审计"],
}

out = {"cases": [], "errors": []}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    cur = {"k": None}
    page.on("console", lambda m: out["errors"].append({"case": cur["k"], "t": m.text[:1000]})
            if m.type == "error" and "same key" not in m.text else None)
    page.on("pageerror", lambda e: out["errors"].append({"case": cur["k"], "t": "PAGEERROR: " + str(e)[:1500]}))
    page.goto(BASE, wait_until="networkidle", timeout=60000)
    time.sleep(2)

    for nav, tabs in PLAN.items():
        page.get_by_text(nav, exact=True).first.click()
        time.sleep(3)
        for tab in tabs:
            cur["k"] = f"{nav}/{tab}"
            before = len(out["errors"])
            try:
                page.locator(f"text={tab}").first.click(timeout=4000)
            except Exception as exc:
                out["cases"].append({"case": cur["k"], "skip": str(exc)[:120]})
                continue
            time.sleep(2.5)
            info = page.evaluate(
                """() => ({
                  alerts: Array.from(document.querySelectorAll('[role=alert]')).map(e=>e.innerText.slice(0,400)),
                  bodylen: document.body.innerHTML.length,
                  empty: document.body.innerText.trim().length,
                })"""
            )
            info["case"] = cur["k"]
            info["errs"] = out["errors"][before:]
            out["cases"].append(info)
            if info["alerts"] or info["errs"] or info["bodylen"] < 3000:
                page.screenshot(path="/tmp/tab_" + cur["k"].replace("/", "_") + ".png")
    browser.close()

print(json.dumps(out, ensure_ascii=False, indent=2)[:12000])
