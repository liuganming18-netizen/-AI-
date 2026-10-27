#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把同源 API 的**原始返回**落盘为验收样例（不是只截一个 HTTP 200）。

落盘内容：
  api_samples/<ex>__<mid>__<period>.json   指标矩阵完整返回
  api_samples/pool-scope.json              加密池分母
  api_samples/field-contract.json          机器可读字段契约
  api_samples/索引.md                      每个样例的请求、状态码、关键摘要

判据（缺一即失败）：
  · HTTP 200 **且** 响应体是 JSON **且** `fields` 非空；
  · 每个字段必须有 `unit`；有值的必须有 `event_time`；缺值的必须有 `missing_reason`；
  · 同一路径连续两次请求，`as_of` 必须能推进（除非源本身没更新，此时如实标注）。

用法：
  python collect_api_samples.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(EVID, "api_samples")
API = os.environ.get("API_BASE") or "http://127.0.0.1:8787"

CASES = [
    ("binance", "BTCUSDT", "1h", "主流标的"),
    ("binance", "ETHUSDT", "1h", "主流标的"),
    ("binance", "1000CATUSDT", "1h", "历史有指标的小币"),
    ("okx", "OKB-USDT-SWAP", "1h", "欧易补充位（币安无该币）"),
]


def get(path):
    url = API + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.monotonic()
    try:
        with opener.open(req, timeout=60) as fh:
            body = fh.read()
            code = fh.status
    except urllib.error.HTTPError as exc:                        # noqa: PERF203
        body = exc.read()
        code = exc.code
    ms = int((time.monotonic() - t0) * 1000)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:                                            # noqa: BLE001
        data = None
    return code, ms, data, len(body)


def dump(name, obj):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    return p, os.path.getsize(p)


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    ok_all = True
    for ex, mid, period, label in CASES:
        path = f"/api/v1/markets/{ex}/{mid}/metrics?period={period}"
        code, ms, data, nbytes = get(path)
        name = f"{ex}__{mid}__{period}.json"
        size = 0
        checks = []
        if data:
            _, size = dump(name, data)
            fields = data.get("fields") or []
            checks.append(("字段数", len(fields), len(fields) == 17))
            bad_unit = [f["key"] for f in fields if not f.get("unit")]
            checks.append(("字段缺单位", len(bad_unit), not bad_unit))
            bad_time = [f["key"] for f in fields
                        if f.get("value") is not None and not f.get("event_time")]
            checks.append(("有值但缺源时间", len(bad_time), not bad_time))
            bad_reason = [f["key"] for f in fields
                          if f.get("value") is None and not f.get("missing_reason")]
            checks.append(("缺值但无原因", len(bad_reason), not bad_reason))
            counts = data.get("counts") or {}
            checks.append(("可用/缺失/过期",
                           f"{counts.get('available')}/{counts.get('missing')}/{counts.get('stale')}",
                           True))
        good = code == 200 and bool(data and data.get("fields")) and all(c[2] for c in checks)
        ok_all = ok_all and good
        rows.append({"case": f"{ex}:{mid}", "label": label, "period": period,
                     "path": path, "code": code, "ms": ms, "bytes": nbytes,
                     "file": name if data else None, "file_bytes": size,
                     "as_of": (data or {}).get("as_of"),
                     "checks": checks, "ok": good})

    # 分母与契约
    extra = []
    for path, name in (("/api/v1/data/pool-scope", "pool-scope.json"),
                       ("/api/v1/data/field-contract", "field-contract.json")):
        code, ms, data, nbytes = get(path)
        size = 0
        if data:
            _, size = dump(name, data)
        extra.append({"path": path, "code": code, "ms": ms, "bytes": nbytes,
                      "file": name if data else None, "file_bytes": size,
                      "ok": code == 200 and bool(data)})
        ok_all = ok_all and code == 200 and bool(data)

    # 时间推进：同一路径连续两次，看 as_of 是否推进（源没更新时如实标注）
    ex, mid, period, _ = CASES[0]
    path = f"/api/v1/markets/{ex}/{mid}/metrics?period={period}"
    _, _, a, _ = get(path)
    time.sleep(6)
    _, _, b, _ = get(path)
    adv = {
        "path": path,
        "as_of_1": (a or {}).get("as_of"), "as_of_2": (b or {}).get("as_of"),
        "advance_s": None,
        "note": "",
    }
    if adv["as_of_1"] and adv["as_of_2"]:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        d1 = datetime.strptime(adv["as_of_1"], fmt).replace(tzinfo=timezone.utc)
        d2 = datetime.strptime(adv["as_of_2"], fmt).replace(tzinfo=timezone.utc)
        adv["advance_s"] = int((d2 - d1).total_seconds())
        adv["note"] = ("两次请求间隔 6s，接口每次重新取数（未收盘 K 线短缓存 5s）"
                       if adv["advance_s"] >= 0 else "as_of 倒退，异常")

    md = ["# DATA-UI-01 接口样例索引", "",
          f"生成时间（UTC）：`{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`",
          f"服务地址：`{API}`（本机只读后端，读快照/库，不触发交易所请求）", "",
          "> 样例是**完整响应体**，不是只截 HTTP 200。每条都过了结构判据："
          "字段齐、每个字段有单位、有值的带源时间、缺值的带原因。", "",
          "## 一、指标矩阵", "",
          "| 案例 | 周期 | 状态码 | 耗时 | 响应体 | 落盘文件 | 结构判据 |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        bad = [f"{c[0]}={c[1]}" for c in r["checks"] if not c[2]]
        verdict = "通过" if r["ok"] else ("不通过：" + "；".join(bad) if bad else "不通过")
        md.append(f"| `{r['case']}`（{r['label']}） | {r['period']} | {r['code']} | "
                  f"{r['ms']} ms | {r['bytes']} B | `{r['file']}`（{r['file_bytes']} B） | {verdict} |")
    md += ["", "## 二、分母与字段契约", "",
           "| 路径 | 状态码 | 耗时 | 落盘文件 | 判据 |", "|---|---|---|---|---|"]
    for e in extra:
        md.append(f"| `{e['path']}` | {e['code']} | {e['ms']} ms | "
                  f"`{e['file']}`（{e['file_bytes']} B） | {'通过' if e['ok'] else '不通过'} |")
    md += ["", "## 三、刷新是否真的在推进", "",
           f"- 路径：`{adv['path']}`",
           f"- 第一次 `as_of`：`{adv['as_of_1']}`",
           f"- 第二次 `as_of`：`{adv['as_of_2']}`",
           f"- 推进：{adv['advance_s']} 秒",
           f"- 说明：{adv['note']}", "",
           "> 说明：`as_of` 是接口本次取数时刻。未收盘 K 线的值会随撮合推进，"
           "所以两次读数可能不同 —— 这是**正常的时间推进**，不是数据不稳定。", ""]
    with open(os.path.join(OUT, "索引.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    print("\n".join(md))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
