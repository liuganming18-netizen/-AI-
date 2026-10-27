# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 15：页面实机核对（v2，Playwright 真浏览器）。

对 13 号脚本挑出的 9 笔抽样，逐笔在 http://127.0.0.1:3000/ 上打开
`?run=<币级run_id>` 深链，读页面逐笔表里的实际文字，与源账本比对；
再点中该行，确认 K 线按该笔重新取数（focus_ms），并截图留证。

判据：页面打不开 / 表格读不到 / 数值与源账本不符 ⇒ 未通过。
不用「源码里有这个字段」代替页面实测。
"""
from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
SHOT = os.path.join(AUDIT, "screens")
BASE = "http://127.0.0.1:3000"
COLS = ["信号时间 (UTC)", "确认时间 (UTC)", "下单时间 (UTC)", "进场时间 (UTC)",
        "出场时间 (UTC)", "标的", "方向", "开仓价", "仓位金额", "原始 P0",
        "计划风险", "初始止损", "净盈亏"]


def utc(ms):
    if ms is None:
        return None
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M")


def num(v, nd=6):
    if v is None:
        return None
    try:
        return ("%%.%df" % nd) % float(v)
    except Exception:
        return str(v)


def main():
    from playwright.sync_api import sync_playwright

    payload = json.load(open(os.path.join(OUT, "13_页面期望值_v2.json"), encoding="utf-8"))
    os.makedirs(SHOT, exist_ok=True)
    rows, shots = [], []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        for i, s in enumerate(payload, 1):
            rid = s["币级run_id"]
            exp = s["期望"]
            url = "%s/?run=%s" % (BASE, rid)
            pg = b.new_page(viewport={"width": 1680, "height": 1050})
            reqs = []
            pg.on("request", lambda r: reqs.append(r.url) if "/chart" in r.url else None)
            errs = []
            pg.on("console", lambda m: errs.append(m.text[:150]) if m.type == "error" else None)
            rec = {
                "序号": i, "模式": s["模式"], "抽样类型": s["抽样类型"], "币种": s["币种"],
                "run_id": rid, "URL": url,
            }
            try:
                pg.goto(url, wait_until="networkidle", timeout=90000)
                pg.wait_for_timeout(3500)
                tbl = None
                for k in range(pg.locator("table").count()):
                    hs = pg.locator("table").nth(k).locator("thead th").all_inner_texts()
                    if "信号时间 (UTC)" in hs:
                        tbl = pg.locator("table").nth(k)
                        break
                if tbl is None:
                    rec.update({"页面打开": "是", "逐笔表": "未找到", "判定": "未通过"})
                    rows.append(rec)
                    pg.close()
                    continue
                headers = tbl.locator("thead th").all_inner_texts()
                body = tbl.locator("tbody tr")
                found = None
                for r in range(body.count()):
                    tds = body.nth(r).locator("td").all_inner_texts()
                    d = dict(zip(headers, tds))
                    if d.get("进场时间 (UTC)") == utc(exp["入场时间"]):
                        found = (r, d)
                        break
                rec["页面打开"] = "是"
                rec["逐笔表"] = "找到（%d 行）" % body.count()
                if not found:
                    rec["判定"] = "未通过：表里找不到该笔"
                    rows.append(rec)
                    pg.close()
                    continue
                r_idx, d = found

                def cmp(field, expect_str, got_key=None):
                    got = d.get(got_key or field)
                    ok = (str(got).strip() == str(expect_str).strip())
                    rows.append({
                        "序号": i, "币种": s["币种"], "模式": s["模式"], "字段": field,
                        "源账本": expect_str, "页面": got, "一致": ok,
                        "run_id": rid,
                    })
                    return ok

                def money(field, val):
                    got = str(d.get(field, ""))
                    g = re.sub(r"[^\d.\-]", "", got)
                    try:
                        ok = abs(float(g) - float(val)) < 0.005
                    except Exception:
                        ok = False
                    rows.append({"序号": i, "币种": s["币种"], "模式": s["模式"], "字段": field,
                                 "源账本": "%.2f" % val, "页面": got, "一致": ok, "run_id": rid})
                    return ok

                checks = [
                    cmp("进场时间 (UTC)", utc(exp["入场时间"])),
                    cmp("出场时间 (UTC)", utc(exp["出场时间"])),
                    cmp("标的", s["币种"]),
                    money("净盈亏", exp["净额"]),
                ]
                # 价格/数量/ P0 用「页面显示值 ≈ 源值」比较（页面按位数格式化）
                def near(field, val, nd):
                    got = d.get(field, "")
                    g = re.sub(r"[^\d.\-+]", "", str(got))
                    try:
                        ok = abs(float(g) - float(val)) < (10 ** (-nd)) * 1.01
                    except Exception:
                        ok = False
                    rows.append({"序号": i, "币种": s["币种"], "模式": s["模式"], "字段": field,
                                 "源账本": num(val, nd), "页面": got, "一致": ok, "run_id": rid})
                    return ok

                checks.append(near("开仓价", exp["入场价"], 4))
                checks.append(near("出场价", exp["出场价"], 4))

                def qty_cmp(val):
                    got = str(d.get("数量", ""))
                    g = re.sub(r"[^\d.\-]", "", got)
                    try:
                        ok = abs(float(g) - float(val)) <= max(1e-6, abs(float(val)) * 1e-6)
                    except Exception:
                        ok = False
                    rows.append({"序号": i, "币种": s["币种"], "模式": s["模式"], "字段": "数量",
                                 "源账本": num(val, 6), "页面": got, "一致": ok, "run_id": rid})
                    return ok

                checks.append(qty_cmp(exp["数量"]))
                if exp["P0"] is not None:
                    checks.append(near("原始 P0", exp["P0"], 6))
                if exp["信号时间"] is not None:
                    checks.append(cmp("信号时间 (UTC)", utc(exp["信号时间"])))
                if exp["确认时间"] is not None:
                    checks.append(cmp("确认时间 (UTC)", utc(exp["确认时间"])))

                # 下单时间 vs 进场时间：两列是否显示同一时刻
                same = str(d.get("下单时间 (UTC)")).strip() == str(d.get("进场时间 (UTC)")).strip()
                rec["下单时间列"] = d.get("下单时间 (UTC)")
                rec["进场时间列"] = d.get("进场时间 (UTC)")
                rec["下单时间等于进场时间"] = same

                # 研究期徽标
                rec["研究期徽标数"] = pg.locator("[data-testid=lab-run-research]").count()

                # 点中该行 ⇒ K 线按该笔重新取数
                # 先点另一行（若有多行），把选中状态挪走，否则「本来就选中」不会触发重新取数
                if body.count() > 1:
                    body.nth(0 if r_idx != 0 else 1).click()
                    pg.wait_for_timeout(1200)
                before = len(reqs)
                body.nth(r_idx).click()
                pg.wait_for_timeout(2500)
                focus = [u for u in reqs[before:] if "focus_ms" in u]
                rec["点击后chart请求"] = len(reqs) - before
                rec["focus_ms请求"] = focus[0].split("?")[-1] if focus else None
                tid = [u for u in reqs[before:] if "/trades/ft_" in u]
                rec["trade_id请求"] = tid[0].split("/trades/")[-1] if tid else None
                rec["期望trade_id"] = s.get("单runAPI_trade_id")
                panel = pg.locator("[data-testid=lab-chart-panel]")
                rec["图面板"] = (panel.inner_text()[:120].replace("\n", " / ") if panel.count() else None)

                shot = os.path.join(SHOT, "15_%02d_%s_%s.png" % (i, s["模式"], s["币种"]))
                pg.screenshot(path=shot, full_page=False)
                shots.append(shot)
                rec["截图"] = os.path.relpath(shot, AUDIT)
                rec["判定"] = "通过" if all(checks) else "未通过：存在字段不一致"
                rec["控制台错误"] = errs[:2]
            except Exception as e:
                rec["判定"] = "未通过：页面异常 %s" % str(e)[:120]
            rows.append(rec)
            pg.close()
        b.close()

    det = pd.DataFrame([r for r in rows if "字段" in r])
    det.to_csv(os.path.join(OUT, "15_页面逐字段核对_v2.csv"), index=False, encoding="utf-8-sig")
    summary_rows = [r for r in rows if "字段" not in r]
    pd.DataFrame(summary_rows).to_csv(os.path.join(OUT, "15_页面逐笔摘要_v2.csv"),
                                     index=False, encoding="utf-8-sig")

    per = det.groupby("序号")["一致"].apply(lambda x: "%d/%d" % (x.sum(), len(x))).to_dict()
    summary = {
        "页面样本数": len(summary_rows),
        "页面全部通过数": int(sum(1 for r in summary_rows if r.get("判定") == "通过")),
        "逐笔字段一致率": {str(k): v for k, v in per.items()},
        "总字段一致率": "%d/%d" % (int(det["一致"].sum()), len(det)),
        "下单时间等于进场时间的笔数": int(sum(1 for r in summary_rows if r.get("下单时间等于进场时间"))),
        "focus_ms请求命中数": int(sum(1 for r in summary_rows if r.get("focus_ms请求"))),
        "截图数": len(shots),
        "未通过明细": [{"序号": r["序号"], "币种": r.get("币种"), "判定": r.get("判定")}
                    for r in summary_rows if r.get("判定") != "通过"],
    }
    with open(os.path.join(OUT, "15_页面摘要_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)

    print(pd.DataFrame(summary_rows)[["序号", "模式", "币种", "页面打开", "逐笔表",
                                      "下单时间等于进场时间", "focus_ms请求", "判定"]].to_string(index=False))
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    bad = det[det["一致"] == False]
    if len(bad):
        print("\n不一致字段：")
        print(bad.to_string(index=False))


if __name__ == "__main__":
    main()
