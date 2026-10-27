#!/usr/bin/env python3
"""深链打开每个 run，抓渲染异常（回测复盘 → 点记录后的等效路径）。"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

runs = json.load(open("/tmp/run_ids.json"))
out = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    for rid in runs:
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)[:1200]))
        page.on("console", lambda m: errs.append(m.text[:800]) if m.type == "error" and "same key" not in m.text else None)
        rec = {"run": rid}
        try:
            page.goto(f"http://127.0.0.1:3000/?run={rid}", wait_until="networkidle", timeout=45000)
            time.sleep(4)
            rec.update(page.evaluate(
                """() => ({
                  alerts: Array.from(document.querySelectorAll('[role=alert]')).map(e=>e.innerText.slice(0,400)),
                  bodylen: document.body.innerHTML.length,
                  textlen: document.body.innerText.trim().length,
                  head: document.body.innerText.slice(0,160),
                })"""
            ))
        except Exception as exc:
            rec["nav_error"] = str(exc)[:300]
        rec["errors"] = errs[:8]
        page.screenshot(path="/tmp/run_" + rid[-18:] + ".png")
        out.append(rec)
        ctx.close()
    browser.close()

print(json.dumps(out, ensure_ascii=False, indent=2)[:12000])
