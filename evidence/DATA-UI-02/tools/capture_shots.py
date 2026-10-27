#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-02：把数据中心「指标」页按验收案例截图，供逐字段对照留证。

截图只证明「界面长什么样」；数值是否与接口一致由 `verify_ui_text.py` 判。
两者必须成对存在：只有截图 = 有数字但不知真假；只有文字核验 = 不知界面是否真这么显示。

用法：
  python capture_shots.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(EVID, "screenshots")
BASE = os.environ.get("UI_BASE") or "http://127.0.0.1:3000/"

CASES = [
    ("binance", "BTCUSDT", "1h", "01_币安_BTCUSDT", "主流标的"),
    ("binance", "ETHUSDT", "1h", "02_币安_ETHUSDT", "主流标的"),
    ("binance", "1000CATUSDT", "1h", "03_币安_1000CATUSDT", "历史有指标的小币"),
    ("okx", "OKB-USDT-SWAP", "1h", "04_欧易_OKB", "欧易补充位"),
    # 缺失态：轮转窗口还没轮到的标的，界面必须显示「—」+ 原因，不能留空
    ("binance", "ADAUSDT", "1h", "05_币安_ADAUSDT_缺失态", "轮转未轮到"),
]


def expand_all(pg):
    """把页面上的折叠块展开（`<details>` / 「展开」按钮），让证据可见。"""
    n = pg.evaluate("""() => {
      let n = 0;
      document.querySelectorAll('details').forEach(d => {
        if (!d.open) { d.open = true; n++; }
      });
      [...document.querySelectorAll('button,a,span')].forEach(el => {
        const t = (el.innerText || '').trim();
        if (t === '展开' || t === '展开全部' || t === '查看详情' || t === '更多') {
          try { el.click(); n++; } catch (e) {}
        }
      });
      return n;
    }""")
    return n


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    made = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1680, "height": 1200}, device_scale_factor=2)
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3000)
        pg.get_by_text("数据中心", exact=True).first.click()
        pg.wait_for_timeout(3000)
        pg.get_by_text("指标", exact=True).first.click()
        pg.wait_for_timeout(5000)

        for ex, mid, period, stem, label in CASES:
            ex_label = "币安" if ex == "binance" else "欧易"
            pg.get_by_text(ex_label, exact=True).first.click()
            pg.wait_for_timeout(600)
            box = pg.get_by_placeholder("标的，如 BTCUSDT").first
            box.fill(mid)
            box.press("Enter")
            pg.wait_for_timeout(1500)
            pg.get_by_role("button", name=period, exact=True).first.click()
            pg.wait_for_timeout(5000)

            # ① 折叠前的干净视图
            path = os.path.join(OUT, f"{stem}.png")
            pg.screenshot(path=path, full_page=True)
            made.append(path)

            # ② 展开证据后的视图
            opened = expand_all(pg)
            pg.wait_for_timeout(1500)
            path2 = os.path.join(OUT, f"{stem}_展开证据.png")
            pg.screenshot(path=path2, full_page=True)
            made.append(path2)
            print(f"{ex}:{mid}（{label}）→ 折叠块展开 {opened} 个")

            # ③ 历史覆盖表：它在**内层滚动容器**里，整页截图抓不到 ⇒ 直接截那块元素。
            #    工单要求「历史覆盖与当前实时状态分开」，这张就是分开后的历史覆盖侧。
            path3 = os.path.join(OUT, f"{stem}_历史覆盖.png")
            try:
                head = pg.get_by_text("历史覆盖（按周期分列", exact=False).first
                head.scroll_into_view_if_needed()
                pg.wait_for_timeout(600)
                box_el = head.locator(
                    "xpath=ancestor::div[contains(@class,'rounded-lg')][1]")
                box_el.screenshot(path=path3)
            except Exception:                                     # noqa: BLE001
                pg.screenshot(path=path3, full_page=False)
            made.append(path3)

        br.close()
    for m in made:
        print("写出：", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
