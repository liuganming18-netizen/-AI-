#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR2-INTEGRATION-EXEC-01 · 跨模块反例（8 项）

目的：四项成果各自单测通过，不代表**拼在一起**仍成立。本脚本把工单要求的 8 条
跨模块检查放进**同一个隔离沙盒**里逐条跑，用生产代码的真实返回值断言，
而不是引用各工单自己的测试结论。

跑法（在集成工作树内）：

    cd /private/tmp/pr2-integration-exec-01/框架/backend
    export CONTRACT_SPEC_CSV="<真实工程>/数据/.../07_526币保守执行近似规格.csv"
    /usr/bin/python3 ../..//evidence/PR2-INTEGRATION-EXEC-01/scripts/01_跨模块反例.py

沙盒：每条检查独立 `tempfile.mkdtemp()`，`LIVE_DB` / `MARKET_DB` / `PAPER_LEDGER_DIR` /
`PAPER_LOCK_DIR` / `PAPER_GATE_FILE` 全指向临时目录；不碰生产库、不启服务。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
INTEGRATION_ROOT = EVIDENCE.parents[1]
BACKEND = INTEGRATION_ROOT / "框架" / "backend"
WEB = INTEGRATION_ROOT / "UI界面" / "web"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import live_api as API
import live_store as L
import paper_engine as PE
import unified_runtime as UR
from tests.test_pr2_recovered_harden import HardenBase, provenance  # 沙盒 + 夹具

ARM = "3a"
M15 = 900_000
H1 = 3_600_000


class Checks(HardenBase):
    """继承沙盒与夹具；检查方法统一用 `check_` 前缀，避免被 unittest 自动收集。"""

    # ── 工具 ────────────────────────────────────────────────────────────
    def _http(self, body):
        """调用真实下单入口（不 patch 业务函数，只固定水位以消除时钟抖动）。"""
        now = int(time.time() * 1000)
        import unittest.mock as mock
        with mock.patch.object(L, "effective_floor_ms", return_value=now - 60_000), \
             mock.patch.object(API, "resolve_live_floor_ms",
                               return_value=(now - 60_000, None)):
            return API.paper_order_post(body)

    def _live_ok(self, uid="cand_int_live", symbol="PR2USDT"):
        now = int(time.time() * 1000)
        self.insert_signal_row(uid, origin="live", exec_state="enterable", symbol=symbol,
                               event_time=now - 3000, confirmed_at=now - 2000,
                               available_at=now - 1000)
        return uid, symbol

    # ── 1. live 信号：准入 → 隔离模拟挂单 ───────────────────────────────
    def check_01_live_signal_to_pending(self):
        uid, symbol = self._live_ok()
        res, status = self._http({"action": "open", "arm": ARM, "symbol": symbol,
                                  "signal_uid": uid})
        self.assertEqual(status, 200, f"live 信号应被受理，实际 {status} {res}")
        self.assertEqual(res["quality"]["status"], "pending", res["quality"])
        self.assertEqual(self.pending_count(uid), 1, "live 信号必须落成 1 条模拟挂单")
        return {"status": status, "quality": res["quality"]["status"], "pending": 1}

    # ── 2. recovered 过期信号：不能进入模拟挂单 ─────────────────────────
    def check_02_expired_recovered_blocked(self):
        now = int(time.time() * 1000)
        snap = now - 4 * H1                      # 4 小时前确认，早已越窗
        uid = "cand_int_rec_expired"
        cand = self.old_candidate(uid, snapshot_time_ms=snap, available_at_ms=snap,
                                  execution_earliest_ms=snap + M15)
        st = self.persist_recovered(cand, now_ms=now)
        self.assertEqual(st["blocked"], 1, f"过期 recovered 必须被阻断，实际 {st}")
        self.assertEqual(self.live_row(uid)["executable_state"], "blocked")
        r = self.router.route_pending_orders(now_ms=now, origin="recovered")
        self.assertEqual(r["registered"], 0)
        self.assertEqual(self.pending_count(uid), 0, "过期 recovered 不得生成挂单")
        # 再走 HTTP 也不能绕过（executable_state 非 enterable）
        res, status = self._http({"action": "open", "arm": ARM, "symbol": "PR2USDT",
                                  "signal_uid": uid})
        self.assertEqual(status, 400, f"过期 recovered 不得经 HTTP 开仓：{res}")
        self.assertIn("executable_state_not_enterable", res["quality"]["note"])
        self.assertEqual(self.pending_count(uid), 0)
        return {"blocked_reasons": st["blocked_reasons"][uid],
                "http_status": status, "http_note": res["quality"]["note"]}

    # ── 3. 历史 / 回放 / 测试 UID：不能经 HTTP 绕过准入 ─────────────────
    def check_03_http_cannot_bypass_scope(self):
        rows = []
        for i, origin in enumerate(("historical_research", "replay", "test")):
            uid = f"cand_int_bypass_{i}"
            self.insert_signal_row(uid, origin=origin, exec_state="enterable",
                                   symbol=f"PR2BYP{i}USDT")
            res, status = self._http({"action": "open", "arm": ARM,
                                      "symbol": f"PR2BYP{i}USDT", "signal_uid": uid})
            self.assertEqual(status, 400, f"来源 {origin} 不得经 HTTP 开仓：{res}")
            self.assertIn("origin_not_actionable", res["quality"]["note"])
            self.assertEqual(self.pending_count(uid), 0)
            rows.append({"origin": origin, "status": status,
                         "note": res["quality"]["note"]})
        # 测试 UID（来源合法也不行）
        uid = "sig_closeloop_int_bypass"
        self.insert_signal_row(uid, origin="live", exec_state="enterable",
                               symbol="PR2TUIDUSDT")
        res, status = self._http({"action": "open", "arm": ARM,
                                  "symbol": "PR2TUIDUSDT", "signal_uid": uid})
        self.assertEqual(status, 400, f"测试 UID 不得经 HTTP 开仓：{res}")
        self.assertIn("test_signal_uid", res["quality"]["note"])
        self.assertEqual(self.pending_count(uid), 0)
        rows.append({"origin": "live(test uid)", "status": status,
                     "note": res["quality"]["note"]})
        return rows

    # ── 4. 同 UID 冲突：blocked 不能被升级为 enterable ──────────────────
    def check_04_conflict_no_upgrade(self):
        uid = "cand_int_conflict"
        cand = self.fresh_candidate(uid, symbol="PR2USDT")
        self.insert_row_from_candidate(cand, origin="recovered", exec_state="blocked",
                                       payload_over={"approved": False,
                                                     "blocked_reasons": ["manual_hold"]})
        st = self.persist_recovered(cand, now_ms=int(time.time() * 1000))
        self.assertEqual(st["conflict_rejected_n"], 1, f"冲突必须被拒绝：{st}")
        reasons = st["conflict_rejected"][uid]
        self.assertIn("conflict_upgrade_refused:blocked->enterable", reasons)
        row = self.live_row(uid)
        self.assertEqual(row["executable_state"], "blocked", "被拒绝后不得变成 enterable")
        self.assertEqual(row["origin"], "recovered")
        res, status = self._http({"action": "open", "arm": ARM, "symbol": "PR2USDT",
                                  "signal_uid": uid})
        self.assertEqual(status, 400, f"仍是 blocked，不得经 HTTP 开仓：{res}")
        self.assertEqual(self.pending_count(uid), 0)
        return {"reasons": reasons, "executable_state": row["executable_state"]}

    # ── 5. 手动模拟订单仍可登记 ─────────────────────────────────────────
    def check_05_manual_order_still_works(self):
        res, status = self._http({"action": "open", "arm": ARM, "symbol": "PR2USDT",
                                  "origin": "manual"})
        self.assertEqual(status, 200, f"手动开仓必须仍可登记：{status} {res}")
        uid = res["items"][0]["signal_uid"]
        self.assertRegex(uid, r"^manual:[0-9a-f]{32}$")
        self.assertEqual(self.pending_count(uid), 1, "手动订单必须落成 1 条挂单")
        # 显式指定合法 manual 身份也应受理（重试身份兼容）
        retry = f"manual:{int(time.time() * 1000)}"
        res2, status2 = self._http({"action": "open", "arm": ARM, "symbol": "PR2USDT",
                                    "origin": "manual", "signal_uid": retry})
        self.assertEqual(status2, 200, f"manual:<毫秒> 重试身份必须仍可登记：{res2}")
        self.assertEqual(self.pending_count(retry), 1)
        return {"server_uid": uid, "retry_uid": retry, "status": status}

    # ── 6. 前端不得把 K 线收盘价误标为实时最新价 ────────────────────────
    def check_06_frontend_price_label(self):
        """直接跑前端 DOM 测试（真实 Chromium 渲染），只认它自己的结论。"""
        p = subprocess.run(["node", "tests/display-semantics.dom.test.mjs"],
                           cwd=str(WEB), capture_output=True, text=True, timeout=180)
        out = (p.stdout or "") + (p.stderr or "")
        self.assertEqual(p.returncode, 0, f"DOM 测试失败：{out[-800:]}")
        self.assertIn("12/12 通过", out, f"DOM 测试未全通过：{out[-800:]}")
        self.assertIn("C1 只有 last_price + last_bar_utc ⇒ 界面显示「K线收盘价」", out)
        self.assertIn("C2b 取实时报价值而非 bar 收盘价", out)
        return {"dom_exit": p.returncode, "dom_summary": "12/12 通过"}

    # ── 7. 外部 HTTP 时钟注入被拒绝 ─────────────────────────────────────
    def check_07_external_clock_rejected(self):
        uid, symbol = self._live_ok("cand_int_clock", "PR2CLOCKUSDT")
        now = int(time.time() * 1000)
        out = []
        for field in ("now_ms", "requested_at"):
            res, status = self._http({"action": "open", "arm": ARM, "symbol": symbol,
                                      "signal_uid": uid, field: now})
            self.assertEqual(status, 400, f"注入 {field} 必须被拒：{res}")
            self.assertIn("external_clock_forbidden", res["quality"]["note"])
            out.append({"field": field, "status": status,
                        "note": res["quality"]["note"]})
        for origin in ("replay", "test"):
            res, status = self._http({"action": "open", "arm": ARM, "symbol": symbol,
                                      "signal_uid": uid, "origin": origin})
            self.assertEqual(status, 400, f"外部指定来源 {origin} 必须被拒：{res}")
            self.assertIn("external_origin_forbidden", res["quality"]["note"])
            out.append({"field": "origin", "value": origin, "status": status,
                        "note": res["quality"]["note"]})
        self.assertEqual(self.pending_count(uid), 0, "被拒请求不得留下挂单")
        return out

    # ── 8. long-only 执行白名单保持不变 ─────────────────────────────────
    def check_08_long_only_whitelist(self):
        self.assertEqual(tuple(API.ACTIONABLE_ORIGINS), ("live", "recovered"),
                         "可执行来源白名单不得扩大")
        uid = "cand_int_short"
        self.insert_signal_row(uid, origin="live", exec_state="enterable",
                               direction="short", symbol="PR2SHORTUSDT")
        row = self.live_row(uid)
        reasons = UR.signal_admission_reasons(
            row, origin="live", floor_ms=int(L.effective_floor_ms()))
        self.assertIn("direction_not_long:short", reasons,
                      f"空头信号必须被判不可执行：{reasons}")
        res, status = self._http({"action": "open", "arm": ARM, "symbol": "PR2SHORTUSDT",
                                  "signal_uid": uid})
        self.assertEqual(status, 400, f"空头信号不得经 HTTP 开仓：{res}")
        self.assertIn("direction_not_long:short", res["quality"]["note"])
        self.assertEqual(self.pending_count(uid), 0, "空头不得生成挂单")
        return {"actionable_origins": list(API.ACTIONABLE_ORIGINS),
                "short_reasons": reasons, "http_status": status}


CHECKS = [
    ("1 live 信号 → 隔离模拟挂单", "check_01_live_signal_to_pending"),
    ("2 recovered 过期 → 不得进挂单", "check_02_expired_recovered_blocked"),
    ("3 历史/回放/测试 UID → HTTP 不能绕过", "check_03_http_cannot_bypass_scope"),
    ("4 同 UID 冲突 → blocked 不升级", "check_04_conflict_no_upgrade"),
    ("5 手动模拟订单仍可登记", "check_05_manual_order_still_works"),
    ("6 前端不把 K 线收盘价标成最新价", "check_06_frontend_price_label"),
    ("7 外部 HTTP 时钟注入被拒绝", "check_07_external_clock_rejected"),
    ("8 long-only 白名单保持不变", "check_08_long_only_whitelist"),
]


def main() -> int:
    runner = Checks()
    results = []
    for label, method in CHECKS:
        runner.setUp()
        try:
            detail = getattr(runner, method)()
            results.append({"n": label, "ok": True, "detail": detail})
            print(f"PASS  {label}")
        except AssertionError as exc:
            results.append({"n": label, "ok": False, "detail": str(exc)})
            print(f"FAIL  {label} :: {exc}")
        except Exception as exc:                                  # noqa: BLE001
            results.append({"n": label, "ok": False,
                            "detail": f"{type(exc).__name__}: {exc}",
                            "trace": traceback.format_exc()[-1200:]})
            print(f"ERROR {label} :: {type(exc).__name__}: {exc}")
        finally:
            runner.tearDown()

    ok = sum(1 for r in results if r["ok"])
    print(f"\n跨模块反例：{ok}/{len(results)} 通过")
    report = {"ok": ok, "total": len(results), "results": results,
              "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    out = EVIDENCE / "out" / "01_跨模块反例.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{out}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
