# -*- coding: utf-8 -*-
"""07｜走**真实导出入口**下载并核对（隔离后端 8799，不碰生产 8787）。

工单第五条：「通过实际导出入口下载文件并核对内容」。

导出入口的事实（先查清再动手）：
  · 前端「导出订单/成交/逐笔 CSV」是**浏览器内** `new Blob(...)` + `a.download`
    （`BacktestWorkbench.tsx:1863-1873`），不是后端 CSV 端点；
  · 它喂给 CSV 的数据**全部**来自后端这几个 JSON 端点
    （`/trades` `/orders` `/fills` `/rejected`，见 `backtest_api.get_trades/get_orders/get_fills`）；
  ⇒ 「实际导出入口」= 这些端点的真实 HTTP 响应。本脚本起一个**独立后端实例**
    （PORT=8799，`EVENT_TREND_RUNS_DIR`/`MARKET_DB` 全部指向本单隔离目录），
    用真 HTTP 下载，再按前端**逐列定义**核对每个字段是否真的拿得到。

不做的事（写清楚，避免被当成已做）：
  · 不驱动浏览器点按钮（浏览器点击版在 `08_ui_export.py`，单独留证）；
  · 不改任何前端代码、不改 `vite.config.ts` 默认值。
"""
from __future__ import annotations

import csv
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

PORT = 8799
BASE_URL = f"http://127.0.0.1:{PORT}"
DL = cm.OUT / "07_download"
RUNS_DIR = cm.OUT / "iso_runs"
# 批次通道走的是**另一个**环境变量 `EVENT_TREND_BATCH_ROOT`（`backtest_batches.py`），
# 默认回落生产 `回测日志/event_trend_v0_1` ⇒ 只设 RUNS_DIR 时，页面「回测范围」里
# 列出的仍是生产批次（只读，但会让人误以为在看在跑的东西）。
# 这里另建一个只含本单批次的根，并把本单 runs 目录软链成唯一批次。
ISO_BATCH_ROOT = cm.OUT / "iso_batch_root"
TAG = "replay"

# 前端 CSV 列定义（原文摘录，逐列核对；改前端须同步改这里）
CSV_COLUMNS = {
    "orders": {                                  # BacktestWorkbench.tsx:1878-1882
        "header": ["run_id", "strategy_version", "symbol", "signal_id", "order_id",
                   "trade_id", "side", "order_type", "phase", "status", "action",
                   "price", "qty", "fee", "stop_price", "reason", "ts_ms", "time_utc"],
        # 载荷里必须能取到值的键（run_id/strategy_version/time_utc 由前端补，不查）
        "need": ["symbol", "signal_id", "order_id", "trade_id", "side", "phase",
                 "action", "price", "qty", "fee", "ts_ms"],
        "endpoint": "orders",
    },
    "fills": {                                   # BacktestWorkbench.tsx:1909-1913
        "header": ["run_id", "strategy_version", "symbol", "fill_id", "order_id",
                   "trade_id", "action", "fill_price", "quantity", "fee", "liquidity",
                   "sequence", "fill_time_ms", "fill_time_utc"],
        "need": ["symbol", "fill_id", "order_id", "trade_id", "action", "fill_price",
                 "quantity", "fee", "sequence", "fill_time_ms"],
        "endpoint": "fills",
    },
    "trades": {                                  # BacktestWorkbench.tsx:1935-1940
        "header": ["run_id", "trade_id", "symbol", "direction", "signal_id",
                   "entry_order_id", "exit_order_id", "entry_time_ms", "entry_time_utc",
                   "entry_price", "qty", "exit_time_ms", "exit_time_utc", "exit_price",
                   "exit_reason", "initial_stop", "net_pnl", "gross_pnl", "fees",
                   "funding_fee", "holding_hours", "mae", "mfe"],
        "need": ["trade_id", "symbol", "direction", "signal_id", "entry_order_id",
                 "exit_order_id", "entry_time_ms", "entry_price", "qty", "exit_time_ms",
                 "exit_price", "exit_reason", "initial_stop", "net_pnl", "gross_pnl",
                 "fees", "funding_fee", "holding_hours"],
        "endpoint": "trades",
    },
}


def _opener():
    """绕开 HTTP_PROXY：本机回环不该走代理（项目已知坑）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(path: str, *, timeout=180):
    url = BASE_URL + path
    t0 = time.time()
    try:
        with _opener().open(url, timeout=timeout) as r:
            body = r.read()
            return r.status, json.loads(body.decode("utf-8")), round(time.time() - t0, 3)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body.decode("utf-8")), round(time.time() - t0, 3)
        except Exception:                                   # noqa: BLE001
            return e.code, {"raw": body[:400].decode("utf-8", "replace")}, round(time.time() - t0, 3)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _dir_fingerprint(p: Path) -> dict:
    if not p.exists():
        return {"exists": False, "n_entries": 0}
    ents = list(p.iterdir())
    return {"exists": True, "n_entries": len(ents),
            "max_mtime": max((e.stat().st_mtime for e in ents), default=None)}


def main():
    # ── 0. 前置断言 ─────────────────────────────────────────────────────
    assert str(RUNS_DIR).startswith(str(cm.ME)), "隔离 runs 目录不在本单 evidence 内"
    if not _port_free(PORT):
        raise SystemExit(f"端口 {PORT} 已被占用 ⇒ 中止（不复用未知进程）")
    d4 = cm.read_json(cm.OUT / f"04_{TAG}.json")
    reg = cm.read_json(cm.OUT / "06_工作台注册.json")
    if not reg.get("runs"):
        raise SystemExit("06 未注册任何 run ⇒ 中止（不空转判通过）")
    run_ids = [r["run_id"] for r in reg["runs"]]

    prod_runs = cm.BASE / "回测日志" / "event_trend_v0_1" / "runs"
    prod_ledger = cm.PROD_LEDGER_DIR / f"paper_{cm.PROD_ARM}.sqlite3"
    before = {
        "prod_runs": _dir_fingerprint(prod_runs),
        "prod_ledger_sha256": cm.sha256_file(prod_ledger) if prod_ledger.exists() else None,
        "prod_ledger_mtime": prod_ledger.stat().st_mtime if prod_ledger.exists() else None,
    }

    # ── 1. 起独立后端（隔离 env）────────────────────────────────────────
    # 批次根指向一个**空目录**：`backtest_batches.batch_ids()` 扫 `runs*` 子目录，
    # 空目录 ⇒ 0 个批次 ⇒ 页面显示「无批次目录」。
    # 试过把本单 runs 软链成 `runs_sot04`，`/batches` 直接抛 `'runs_sot04'` 500
    # （该模块对批次目录另有假设），故放弃软链 —— 本单是「单 run 回放」，不是批次。
    ISO_BATCH_ROOT.mkdir(parents=True, exist_ok=True)
    for stale in ISO_BATCH_ROOT.glob("runs*"):
        if stale.is_symlink():
            stale.unlink()
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT),
        "EVENT_TREND_RUNS_DIR": str(RUNS_DIR),
        "EVENT_TREND_BATCH_ROOT": str(ISO_BATCH_ROOT),
        "MARKET_DB": str(cm.ISO_MARKET_DB),
        "PAPER_LEDGER_DIR": d4["isolated_paths"]["LEDGER_DIR"],
        "PAPER_GATE_FILE": str(cm.ISO_GATE_FILE),
        "LIVE_DIR": str(cm.ISO_LIVE_DIR),
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    })
    env.pop("HTTP_PROXY", None)
    env.pop("http_proxy", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("https_proxy", None)

    log = DL / "backend_8799.log"
    DL.mkdir(parents=True, exist_ok=True)
    fh = log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(cm.BASE / "框架" / "backend" / "adapter.py")],
        cwd=str(cm.BASE), env=env, stdout=fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    started = cm.utc_now()
    try:
        # ── 2. 等健康 ────────────────────────────────────────────────────
        ready, health = False, None
        for _ in range(120):
            try:
                st, body, _ = http_get("/api/health", timeout=3)
                if st == 200:
                    ready, health = True, body
                    break
            except Exception:                               # noqa: BLE001
                pass
            time.sleep(0.5)
        if not ready:
            raise SystemExit(f"后端 {PORT} 60s 未就绪，见 {log}")

        # ── 3. 下载：列表 → 每个 run 的全套端点 ───────────────────────────
        st, listing, _ = http_get("/api/v1/backtests?limit=500")
        ids_in_list = {str(r.get("run_id")) for r in (listing.get("items") or [])}
        listing_hit = sorted(ids_in_list & set(run_ids))
        # 批次通道也核一遍：必须只剩本单批次，否则说明隔离没盖住 `/batches`
        st_b, batches, _ = http_get("/api/v1/backtests/batches")
        batch_ids_seen = [b.get("batch_id") for b in (batches.get("items") or [])]

        results = []
        # K 线重建（`_generate_fallback_chart` → `load_symbol_data` 读全历史 parquet）
        # 单个 run 数秒到十几秒 ⇒ 47 个 run × 2 个周期不现实，也不是工单要求。
        # 工单只要求「验证对应 K 线可重开」⇒ 抽样：多笔 run + 两笔单笔 run。
        multi = [r["run_id"] for r in reg["runs"] if r["n_trades"] > 1]
        single = [r["run_id"] for r in reg["runs"] if r["n_trades"] == 1]
        chart_sample = (multi[:1] + single[:2])
        for rid in run_ids:
            item = {"run_id": rid, "downloads": {}, "checks": {}, "errors": [],
                    "chart_sampled": rid in chart_sample}
            endpoints = [
                ("detail", f"/api/v1/backtests/{rid}"),
                ("metrics", f"/api/v1/backtests/{rid}/metrics"),
                ("trades", f"/api/v1/backtests/{rid}/trades"),
                ("orders", f"/api/v1/backtests/{rid}/orders"),
                ("fills", f"/api/v1/backtests/{rid}/fills"),
                ("rejected", f"/api/v1/backtests/{rid}/rejected"),
            ]
            if rid in chart_sample:
                endpoints += [
                    ("chart_4h", f"/api/v1/backtests/{rid}/chart?timeframe=4h"),
                    ("chart_15m", f"/api/v1/backtests/{rid}/chart?timeframe=15m"),
                ]
            for name, path in endpoints:
                st, body, dt = http_get(path)
                fn = DL / f"{rid}__{name}.json"
                fn.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
                item["downloads"][name] = {"path": str(fn), "http": st, "sec": dt,
                                           "bytes": fn.stat().st_size}
                if st != 200:
                    item["errors"].append(f"{name} HTTP {st}")
            results.append(item)

        # ── 4. 核对：逐列可得性 + 与账本逐笔对账 ─────────────────────────
        import sqlite3
        led_p = Path(d4["isolated_paths"]["LEDGER_DIR"]) / f"paper_{cm.PROD_ARM}.sqlite3"
        con = sqlite3.connect(f"file:{led_p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        led_orders = [dict(r) for r in con.execute("SELECT * FROM paper_order")]
        led_fills = [dict(r) for r in con.execute("SELECT * FROM paper_fill")]
        led_real = [dict(r) for r in con.execute("SELECT * FROM paper_realized")]
        led_blocked = [dict(r) for r in con.execute("SELECT * FROM paper_blocked")]
        con.close()

        coverage, recon = [], []
        chart_checks = []
        n_trades_by_run = {r["run_id"]: r["n_trades"] for r in reg["runs"]}
        n_rej_by_run = {r["run_id"]: r.get("n_rejected", 0) for r in reg["runs"]}
        for item in results:
            rid = item["run_id"]
            got = {}
            n_tr = n_trades_by_run.get(rid, 0)
            # 行数期望：trades = 笔数；fills = 每笔开+平各 1 条；orders = 开+平 + 拒单
            expect_rows = {"trades": n_tr, "fills": 2 * n_tr,
                           "orders": 2 * n_tr + n_rej_by_run.get(rid, 0)}
            for kind, spec in CSV_COLUMNS.items():
                body = json.loads((DL / f"{rid}__{spec['endpoint']}.json").read_text(encoding="utf-8"))
                items = body.get("items") or []
                got[kind] = items
                per = {}
                # 订单要分状态看：**拒单订单在账本里 qty/price/fee/trade_id 本来就是 NULL**
                # （`account_drawdown_locked` 等前置闸门在定量之前就拒了，engine 不填数量价格），
                # 把「拒单没有价格」当成「列取不到」是误判。成交订单才要求这几列齐全。
                filled_o = [x for x in items if x.get("status") == "filled"]
                rej_o = [x for x in items if x.get("status") == "rejected"]
                for k in spec["need"]:
                    base = (filled_o if (kind == "orders" and k in
                                         ("trade_id", "price", "qty", "fee")) else items)
                    have = sum(1 for x in base if x.get(k) is not None)
                    per[k] = {"non_null": have, "rows": len(base),
                              "ok": (have == len(base)) if base else None}
                if kind == "orders" and rej_o:
                    have = sum(1 for x in rej_o if x.get("reason"))
                    per["reason@rejected"] = {"non_null": have, "rows": len(rej_o),
                                              "ok": have == len(rej_o)}
                # 三态（通过 / 不通过 / 未验证）：`all()` 会把 None 当 False，
                # 那会把「本 run 没有成交订单 ⇒ 这几列无从检查」误报成「空列」。
                vals = [v["ok"] for v in per.values()]
                all_ok = (False if any(v is False for v in vals)
                          else (True if any(v is True for v in vals) else None))
                coverage.append({
                    "run_id": rid, "kind": kind, "rows": len(items),
                    "rows_filled": len(filled_o) if kind == "orders" else None,
                    "rows_rejected": len(rej_o) if kind == "orders" else None,
                    "rows_expected": expect_rows[kind],
                    "row_count_ok": len(items) == expect_rows[kind],
                    "total": body.get("total"),
                    "columns": spec["header"], "per_column": per,
                    "all_columns_ok": all_ok,
                    "empty_columns": [k for k, v in per.items() if v["ok"] is False],
                })

            # 账本对账（按标的切片）
            sym = rid.rsplit("_", 1)[0].replace("run_WX_EVENT_TREND_V0_1_", "")
            lo = [o for o in led_orders if o.get("symbol") == sym]
            lf = [f for f in led_fills if f.get("symbol") == sym]
            lr = [r for r in led_real if r.get("symbol") == sym]
            api_tr = got.get("trades", [])
            api_rj = got.get("rejected", [])
            recon.append({
                "run_id": rid, "symbol": sym,
                "orders_api_vs_ledger": [len(got.get("orders", [])), len(lo)],
                "fills_api_vs_ledger": [len(got.get("fills", [])), len(lf)],
                "trades_api_vs_ledger": [len(api_tr), len(lr)],
                "rejected_api_vs_blocked": [len(api_rj),
                                            len([b for b in led_blocked
                                                 if b.get("symbol") == sym])],
                "counts_match": (len(got.get("orders", [])) == len(lo)
                                 and len(got.get("fills", [])) == len(lf)
                                 and len(api_tr) == len(lr)),
                "net_pnl_api": round(sum(float(t.get("net_pnl") or 0) for t in api_tr), 10),
                "net_pnl_ledger": round(sum(float(r.get("net_pnl") or 0) for r in lr), 10),
                "net_pnl_match": abs(
                    sum(float(t.get("net_pnl") or 0) for t in api_tr)
                    - sum(float(r.get("net_pnl") or 0) for r in lr)) <= 1e-9,
                "fee_api": round(sum(float(t.get("fees") or 0) for t in api_tr), 10),
                "fee_ledger": round(sum(float(r.get("entry_fee") or 0)
                                        + float(r.get("exit_fee") or 0) for r in lr), 10),
            })

            # K 线可重开（抽样）：`candles` 必须非空、标的与周期必须对得上、
            # 且不得带 `missing_reason`（带 = 没画出图却返回 200）
            if item["chart_sampled"]:
                for tf in ("4h", "15m"):
                    body = json.loads((DL / f"{rid}__chart_{tf}.json")
                                      .read_text(encoding="utf-8"))
                    # 载荷是信封结构：K 线在 `chart` 里，不在顶层
                    ch = body.get("chart") or {}
                    candles = ch.get("candles") or []
                    chart_checks.append({
                        "run_id": rid, "timeframe": tf,
                        "envelope_missing_reason": body.get("missing_reason"),
                        "candles": len(candles),
                        "first_t": candles[0].get("t") if candles else None,
                        "last_t": candles[-1].get("t") if candles else None,
                        "symbol": ch.get("symbol"),
                        "display_timeframe": ch.get("display_timeframe"),
                        "exec_timeframe": ch.get("exec_timeframe"),
                        "signal_timeframe": ch.get("signal_timeframe"),
                        "candles_source": ch.get("candles_source"),
                        "window_matches_run_window": ch.get("candles_window_matches_run_window"),
                        "derived_from": (ch.get("derived_from") or "")[:200],
                        "signals": len(ch.get("signals") or []),
                        "trades_markers": len(ch.get("trades") or []),
                        "ok": bool(candles) and not body.get("missing_reason"),
                    })

        # ── 5. 按前端列定义生成 CSV（抽样 run 落盘留证；全量逐笔见 05_*.csv）──
        csv_written = []
        for item in results:
            rid = item["run_id"]
            if rid not in chart_sample:
                continue
            for kind, spec in CSV_COLUMNS.items():
                rows = json.loads((DL / f"{rid}__{spec['endpoint']}.json")
                                  .read_text(encoding="utf-8")).get("items") or []
                p = DL / f"{kind}_{rid}.csv"
                with p.open("w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(spec["header"])
                    for r in rows:
                        w.writerow([r.get(k) for k in spec["header"]])
                csv_written.append({"file": str(p), "rows": len(rows)})

        after = {
            "prod_runs": _dir_fingerprint(prod_runs),
            "prod_ledger_sha256": cm.sha256_file(prod_ledger) if prod_ledger.exists() else None,
            "prod_ledger_mtime": prod_ledger.stat().st_mtime if prod_ledger.exists() else None,
        }
        report = {
            "generated_at_utc": cm.utc_now(),
            "backend": {"port": PORT, "pid": proc.pid, "started_at": started,
                        "cmdline": [sys.executable, "框架/backend/adapter.py"],
                        "env_subset": {k: env[k] for k in
                                       ("PORT", "EVENT_TREND_RUNS_DIR",
                                        "EVENT_TREND_BATCH_ROOT", "MARKET_DB",
                                        "PAPER_LEDGER_DIR", "PAPER_GATE_FILE", "LIVE_DIR")},
                        "health": health, "log": str(log)},
            "entry_point_facts": {
                "frontend_csv_is_client_side_blob": True,
                "frontend_source": "UI界面/web/src/components/BacktestWorkbench.tsx:1863-1967",
                "data_endpoints": ["/api/v1/backtests/{id}/trades",
                                   "/api/v1/backtests/{id}/orders",
                                   "/api/v1/backtests/{id}/fills",
                                   "/api/v1/backtests/{id}/rejected"],
                "note": ("前端 CSV = 浏览器内 Blob 生成；喂它的数据全部来自上面四个 JSON 端点。"
                         "本脚本下载的就是这四个端点的真实响应，并按前端列定义逐列核对。"),
            },
            "n_runs_registered": len(run_ids),
            "n_runs_in_list_endpoint": len(listing_hit),
            "runs_missing_from_list": sorted(set(run_ids) - set(listing_hit)),
            "batches_endpoint": {
                "http": st_b, "total": batches.get("total"),
                "batch_ids": batch_ids_seen,
                "batch_root": batches.get("batch_root"),
                "isolated": str(batches.get("batch_root") or "").startswith(str(cm.ME)),
            },
            "coverage": coverage,
            "reconciliation": recon,
            "chart_sample_runs": chart_sample,
            "chart_checks": chart_checks,
            "csv_written": csv_written,
            "isolation_proof": {"before": before, "after": after,
                                "prod_runs_unchanged": before["prod_runs"] == after["prod_runs"],
                                "prod_ledger_unchanged": (
                                    before["prod_ledger_sha256"] == after["prod_ledger_sha256"])},
        }
        cm.write_json("07_导出核验.json", report)

        print(f"注册 run {len(run_ids)} 个；列表端点命中 {len(listing_hit)} 个")
        print(f"批次端点：total={batches.get('total')} ids={batch_ids_seen} "
              f"root={batches.get('batch_root')} 隔离={str(batches.get('batch_root') or '').startswith(str(cm.ME))}")
        bad = [c for c in coverage if c["all_columns_ok"] is False]
        cnt_bad = [c for c in coverage if not c["row_count_ok"]]
        na = [c for c in coverage if c["all_columns_ok"] is None]
        print(f"列覆盖：{len(coverage)} 组，全列有值 {len(coverage) - len(bad) - len(na)} 组，"
              f"空列 {len(bad)} 组，无该类行(不适用) {len(na)} 组；行数不符 {len(cnt_bad)} 组")
        for c in bad:
            print(f"  空列 {c['run_id']} {c['kind']}: {c['empty_columns']}")
        for c in cnt_bad[:8]:
            print(f"  行数 {c['run_id']} {c['kind']}: 实际 {c['rows']} 期望 {c['rows_expected']}")
        rb = [r for r in recon if not (r["counts_match"] and r["net_pnl_match"])]
        print(f"账本对账：{len(recon)} 个 run，不符 {len(rb)} 个")
        for r in rb[:8]:
            print(f"  {r['run_id']} {r}")
        cb = [c for c in chart_checks if not c["ok"]]
        print(f"K 线重开：抽样 {len(chart_sample)} 个 run / {len(chart_checks)} 个周期，"
              f"失败 {len(cb)}")
        for c in chart_checks:
            print(f"  {c['run_id']} {c['timeframe']}: candles={c['candles']} "
                  f"display={c['display_timeframe']} src={c['candles_source']} "
                  f"signals={c['signals']} markers={c['trades_markers']} "
                  f"missing={c['envelope_missing_reason']}")
        print(f"生产未变：runs={report['isolation_proof']['prod_runs_unchanged']} "
              f"ledger={report['isolation_proof']['prod_ledger_unchanged']}")
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:                                   # noqa: BLE001
            try:
                proc.kill()
            except Exception:                               # noqa: BLE001
                pass
        fh.close()


if __name__ == "__main__":
    raise SystemExit(main())
