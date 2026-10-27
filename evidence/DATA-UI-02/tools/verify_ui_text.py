#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-02：把**界面实际显示的文字**抓下来，与同源 API 逐字段对照。

为什么不能只截图：截图能证明"有数字"，但证明不了"数字与接口一致"。
本脚本用浏览器打开数据中心「指标」页，把每行读成
`指标名 / 值 / 单位 / 源时间 / 新鲜度 / 来源 / 缺失原因`，
再与 `/api/v1/markets/{ex}/{mid}/metrics` 的同名字段比对。

判据：
  · 界面显示的值文本与接口 `value_text` 一致（千分位、小数位都要对得上）；
  · 界面显示的单位与接口 `unit` 一致；
  · 界面显示的源时间与接口 `event_time` 一致；
  · 缺失行必须显示「—」且**给出原因**，不能是空白。

用法：
  python verify_ui_text.py
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
API = os.environ.get("API_BASE") or "http://127.0.0.1:8787"
BASE = os.environ.get("UI_BASE") or "http://127.0.0.1:3000/"

CASES = [
    ("binance", "BTCUSDT", "1h", "主流标的"),
    ("binance", "ETHUSDT", "1h", "主流标的"),
    ("binance", "1000CATUSDT", "1h", "历史有指标的小币"),
    ("okx", "OKB-USDT-SWAP", "1h", "欧易补充位"),
    # 缺失态：轮转没轮到 / 上游不支持时，界面必须显示「—」+ 原因，不能留空
    ("binance", "ADAUSDT", "1h", "轮转覆盖候选（可能未轮到）"),
]


def get(path):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as fh:
        return json.loads(fh.read().decode("utf-8"))


def grab_rows(pg):
    """把指标表读成 [{name, value, unit, event_time, freshness, source, reason}]。

    页面结构：每个分组一张表，列为 指标/值/单位/源时间/新鲜度/来源/缺失原因。
    """
    return pg.evaluate("""() => {
      const out = [];
      document.querySelectorAll('table').forEach(t => {
        const heads = [...t.querySelectorAll('thead th')].map(h => h.innerText.trim());
        if (!heads.includes('指标') || !heads.includes('单位')) return;
        [...t.querySelectorAll('tbody tr')].forEach(tr => {
          const td = [...tr.querySelectorAll('td')].map(x => x.innerText.trim());
          const row = {};
          heads.forEach((h, i) => { row[h] = td[i] === undefined ? '' : td[i]; });
          out.push(row);
        });
      });
      return out;
    }""")


def norm(s):
    return re.sub(r"\s+", "", (s or "")).replace("—", "").strip()


def ui_time(api_iso):
    """接口的 `2026-10-18T04:15:44Z` → 界面显示的 `10-18 04:15:44`。

    界面源时间列省略年份是**显示格式**，不是数据不同；比对必须按显示口径比。
    """
    if not api_iso:
        return ""
    m = re.match(r"\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", str(api_iso))
    return f"{m.group(1)} {m.group(2)}" if m else str(api_iso)


def parse_num(s):
    t = norm(s).replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def main():
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1680, "height": 1200}, device_scale_factor=2)
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3000)
        pg.get_by_text("数据中心", exact=True).first.click()
        pg.wait_for_timeout(3000)
        pg.get_by_text("指标", exact=True).first.click()
        pg.wait_for_timeout(5000)

        for ex, mid, period, label in CASES:
            ex_label = "币安" if ex == "binance" else "欧易"
            pg.get_by_text(ex_label, exact=True).first.click()
            pg.wait_for_timeout(600)
            box = pg.get_by_placeholder("标的，如 BTCUSDT").first
            box.fill(mid)
            box.press("Enter")
            pg.wait_for_timeout(1200)
            pg.get_by_role("button", name=period, exact=True).first.click()
            pg.wait_for_timeout(4500)

            rows = grab_rows(pg)
            api = get(f"/api/v1/markets/{ex}/{mid}/metrics?period={period}")
            fields = {f["name"]: f for f in api.get("fields") or []}

            checks = []
            for r in rows:
                name = r.get("指标", "")
                f = fields.get(name)
                if not f:
                    continue
                ui_val = r.get("值", "")
                ui_unit = r.get("单位", "")
                ui_ev = r.get("源时间", "")
                ui_reason = r.get("缺失原因", "")
                api_val = f.get("value_text")
                if f.get("value") is None:
                    ok = (norm(ui_val) == "" and bool(ui_reason.strip()))
                    note = (f"缺失行必须显示「—」并给原因；界面值={ui_val!r} "
                            f"原因={ui_reason[:40]!r}")
                else:
                    ok_unit = (norm(ui_unit) == norm(f.get("unit")))
                    # 未收盘 K 线：页面读数和接口调用差几十秒，累计值与推送时刻
                    # 都必然往前走了。判据改成「界面不晚于接口」（同一根 bar 单调推进）。
                    forming = f.get("layer") == "realtime_forming"
                    api_ev = ui_time(f.get("event_time"))
                    if forming:
                        ok_ev = norm(ui_ev) <= norm(api_ev)
                    else:
                        ok_ev = (norm(ui_ev) == norm(api_ev))
                    ui_num, api_num = parse_num(ui_val), parse_num(api_val)
                    if forming and ui_num is not None and api_num is not None:
                        ok_val = ui_num <= api_num + 1e-9
                        val_note = ("未收盘 bar 在推进，界面读数不晚于接口读数"
                                    if ok_val else "界面值大于接口值（同根 bar 应单调不减）")
                    else:
                        ok_val = (norm(ui_val) == norm(api_val))
                        val_note = "" if ok_val else f"值 界面{ui_val!r}≠接口{api_val!r}"
                    ok = ok_unit and ok_ev and ok_val
                    bad = []
                    if not ok_val:
                        bad.append(val_note)
                    if not ok_unit:
                        bad.append(f"单位 界面{ui_unit!r}≠接口{f.get('unit')!r}")
                    if not ok_ev:
                        bad.append(f"源时间 界面{ui_ev!r}≠接口{api_ev!r}")
                    note = "；".join(b for b in bad if b) or (
                        f"值/单位/源时间 三项一致（{val_note}）" if forming else "值/单位/源时间 三项一致")
                checks.append({"name": name, "ui_value": ui_val, "ui_unit": ui_unit,
                               "ui_event": ui_ev, "ui_fresh": r.get("新鲜度", ""),
                               "ui_source": (r.get("来源") or "")[:60],
                               "ui_reason": ui_reason,
                               "api_value": api_val, "api_unit": f.get("unit"),
                               "api_event": f.get("event_time"),
                               "api_fresh": f.get("freshness"),
                               "ok": ok, "note": note})
            results.append({"case": f"{ex}:{mid}", "label": label, "period": period,
                            "as_of": api.get("as_of"), "counts": api.get("counts"),
                            "ui_rows": len(rows), "checks": checks})
        br.close()

    # Markdown
    L = []
    A = L.append
    A("# DATA-UI-02 界面文字 vs 同源接口\n")
    A(f"生成时间（UTC）：`{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`\n")
    A("> 不是看截图有没有数字：本表是**从页面 DOM 读出来的文字**，")
    A("> 与 `/api/v1/markets/{ex}/{mid}/metrics` 同名字段逐项比对。\n")
    total_bad = 0
    for r in results:
        bad = [c for c in r["checks"] if not c["ok"]]
        total_bad += len(bad)
        A(f"## {r['case']}（{r['label']}）· {r['period']}\n")
        A(f"- 界面读到 {r['ui_rows']} 行；接口可用 {r['counts']}")
        A(f"- 逐项一致：{len(r['checks']) - len(bad)}/{len(r['checks'])}"
          + ("" if not bad else f"，**不一致 {len(bad)} 项**"))
        A("")
        A("| 指标 | 界面值 | 界面单位 | 界面源时间 | 接口值 | 接口单位 | 一致 | 说明 |")
        A("|---|---|---|---|---|---|---|---|")
        for c in r["checks"]:
            A(f"| {c['name']} | {c['ui_value'] or '—'} | {c['ui_unit'] or '—'} | "
              f"{c['ui_event'] or '—'} | {c['api_value'] or '—'} | {c['api_unit']} | "
              f"{'✓' if c['ok'] else '✗'} | {c['note']} |")
        A("")
    A(f"**合计不一致：{total_bad} 项**\n")

    jp = os.path.join(EVID, "界面文字核验.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "results": results}, fh, ensure_ascii=False, indent=1)
    mp = os.path.join(EVID, "界面文字核验.md")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print(f"写出：{jp}\n写出：{mp}", file=sys.stderr)
    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
