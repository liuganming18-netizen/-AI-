#!/usr/bin/env python3
"""抓取前端页面运行时错误（console / pageerror / 网络失败）。"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3000/"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/probe_crash"

logs = []
errors = []
failed = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    page.on("console", lambda m: logs.append({"type": m.type, "text": m.text[:2000]}))
    page.on("pageerror", lambda e: errors.append(str(e)[:4000]))
    page.on("requestfailed", lambda r: failed.append({"url": r.url[:300], "err": r.failure}))
    page.goto(URL, wait_until="networkidle", timeout=60000)
    time.sleep(3)
    page.screenshot(path=OUT + ".png", full_page=False)
    html_len = page.evaluate("document.body.innerHTML.length")
    text = page.evaluate("document.body.innerText.slice(0,600)")
    browser.close()

print(json.dumps({
    "url": URL,
    "body_html_len": html_len,
    "body_text": text,
    "pageerrors": errors,
    "console_error": [l for l in logs if l["type"] in ("error", "warning")][:40],
    "requestfailed": failed[:20],
}, ensure_ascii=False, indent=2))
