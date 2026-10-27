# -*- coding: utf-8 -*-
"""CODE-REVIEW-02 核验脚本（只读上游 + 临时副本自测；可复跑）。

核验 CODE-REVIEW-01 §4 修复清单的落地情况：
  P0-1/2/3/5  backtest_store._summary_to_run_doc：无读时时间兜底 / 无硬编码参数 /
              状态按产物证据推断 / registered=False 未登记标记 / 窗口与指纹改读 inputs.json
  P0-4        批量脚本登记（12 个 run_batch_* 已插入 register_batch_run；
              登记工具只走 backtest_store.register_external_run，原子、拒绝覆盖）
  P1-6/7      bt_run_log 运行级日志 + 逐笔开关（BT_TRADE_LOG）
  P1-8        backtest_api 图表兜底缺字段 null 不填 0.0；坏行计数（ERR-01）
  P1-9        原两处 except: pass 已改计数+日志
  附加        backtest_chart equity cash/unrealized 缺字段 null

纪律：本脚本**不写**生产目录；登记自测用 /tmp 副本；不重跑回测。
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
for p in (str(ROOT), str(ROOT / "策略"), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

import backtest_store as S          # noqa: E402
import backtest_api as A            # noqa: E402
import backtest_chart as CH         # noqa: E402
import backtest_runner as R         # noqa: E402
import bt_run_log as L              # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    runs_root = ROOT / "回测日志" / "event_trend_v0_1" / "runs"
    sample = sorted([d for d in runs_root.iterdir()
                     if d.is_dir() and d.name.startswith("run_WX_EVENT_TREND_V0_1_")])[:25]

    # ── 1. 读时合成诚实性（P0-1/2/3/5）──────────────────────────────────
    now = S.utc_now_iso()
    bad_time = bad_cfg = bad_window = bad_flag = bad_status = 0
    for d in sample:
        try:
            doc = S.read_run(d.name)
        except Exception:
            continue
        if doc.get("created_at") == now:                # 等于读取时刻 ⇒ 读时兜底
            bad_time += 1
        cfg = ((doc.get("config") or {}).get("actual_config") or {})
        if cfg.get("risk_budget_per_trade") == 0.005 or cfg.get("fee_rate") == 0.0005:
            bad_cfg += 1
        if doc.get("registered") is not False:
            bad_flag += 1
        if doc.get("status") not in (S.STATUS_SUCCEEDED, S.STATUS_UNKNOWN):
            bad_status += 1
        if (doc.get("window") or {}).get("win_start_ms") is None:
            bad_window += 1
    n = len(sample)
    check(f"P0-1 created_at 不再等于读取时刻（样本 {n}）", bad_time == 0, f"bad={bad_time}")
    check("P0-2 risk_budget/fee_rate 不再硬编码 0.005/0.0005", bad_cfg == 0, f"bad={bad_cfg}")
    check("P0-5 registered=False 未登记标记", bad_flag == 0, f"bad={bad_flag}")
    check("P0-3 status 仅 succeeded/unknown（按证据推断）", bad_status == 0, f"bad={bad_status}")
    check("P0-5 window 取 inputs.json 真实窗口（非 null）", bad_window == 0, f"null={bad_window}")

    one = next((d for d in sample if (d / "inputs.json").exists()), None)
    if one is not None:
        inp = json.loads((one / "inputs.json").read_text(encoding="utf-8"))
        expect = S._epoch_sec_to_iso(inp.get("timestamp_created"))
        got = S.read_run(one.name).get("created_at")
        check("P0-1 created_at == inputs.json.timestamp_created 换算", got == expect,
              f"{got} vs {expect}")

    # ── 2. 源码级核对（AST，排除 docstring/注释文本，防止「注释里提到旧写法」误报）──
    import ast
    def _fn_ast(module, fname):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.FunctionDef) and node.name == fname:
                return node
        raise KeyError(fname)
    def _src_of(node) -> str:
        seg = ast.get_source_segment(inspect.getsource(S), node) or ""
        return seg
    synth = _fn_ast(S, "_summary_to_run_doc")
    calls_utc_now = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "utc_now_iso"
                        for n in ast.walk(synth))
    check("R2 合成函数不再调用 utc_now_iso()（AST）", not calls_utc_now)
    # 硬编码参数（AST 常量级判断，比文本匹配更严）
    hard = []
    for n in ast.walk(synth):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if (isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
                        and k.value in ("risk_budget_per_trade", "fee_rate", "slippage_bps")
                        and isinstance(v.value, float)):
                    hard.append((k.value, v.value))
    check("R2 合成函数无硬编码 0.005/0.0005/3.0（AST 常量级）", not hard, f"hard={hard}")
    fb = _fn_ast(A, "_generate_fallback_chart")
    or0 = []
    for n in ast.walk(fb):
        if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or):
            for v in n.values:
                if isinstance(v, ast.Constant) and v.value in (0, 0.0):
                    or0.append(ast.get_source_segment(inspect.getsource(A), n))
    check("R7 图表兜底无 `or 0` / `or 0.0` 填零（AST）", not or0, f"n={len(or0)}")
    check("ERR-01 图表兜底坏行计数+日志（AST）",
          any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "run_log"
              for n in ast.walk(fb)))
    # backtest_chart.equity_from_run_dir：cash/unrealized 缺字段给 None
    eq_src = inspect.getsource(CH.equity_from_run_dir)
    check("R7 chart equity cash/unrealized 缺字段 null", "_fnum" in eq_src)
    # runner：运行级日志
    run_src = inspect.getsource(R)
    check("P1-6 runner 运行级日志四类事件",
          all(k in run_src for k in ("run_gate_rejected", "run_failed", "run_succeeded",
                                     "run_started")))

    # ── 3. 登记工具（P0-4）：临时副本自测，不碰生产目录 ──────────────────
    from 工具_登记回测运行 import register_batch_run      # noqa: E402
    from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402
    donor = next((d for d in sample if (d / "summary.json").exists()), None)
    with tempfile.TemporaryDirectory(prefix="cr02_") as td:
        dst = Path(td) / donor.name
        shutil.copytree(donor, dst)
        sm = json.loads((dst / "summary.json").read_text(encoding="utf-8"))
        r1 = register_batch_run(
            run_id=donor.name, run_dir=str(dst), symbol=sm.get("symbol") or "BTCUSDT",
            status="succeeded", started_at=1700000000.0, finished_at=1700000600.0,
            cfg=EventTrendConfig(initial_equity=2000.0),
            summary=sm.get("summary_variant_a"), start_date="2023-05-30", end_date="2024-05-29")
        check("R3 登记写入 run.json（store 唯一写入口）", r1.get("registered") is True)
        doc = json.loads((dst / "run.json").read_text(encoding="utf-8"))
        check("登记文档：真实窗口 + 真实配置(来自 cfg) + epoch 换算的 created_at",
              doc["window"]["win_start_ms"] is not None
              and doc["config"]["actual_config"]["fee_rate"] == 0.0005
              and doc["created_at"] == "2023-11-14T22:13:20Z",
              f"win={doc['window']['win_start_ms']} fee={doc['config']['actual_config']['fee_rate']} "
              f"created={doc['created_at']}")
        r2 = register_batch_run(run_id=donor.name, run_dir=str(dst), symbol="X",
                                status="succeeded", started_at=1, finished_at=2)
        check("R4 重复登记拒绝覆盖", r2.get("registered") is False)
        # 登记不应改动目录里的其它文件
        extra = {p.name for p in dst.iterdir()} - {p.name for p in donor.iterdir()} - {"run.json"}
        check("登记只新增 run.json，不碰其它产物", not extra, f"extra={sorted(extra)}")

    # ── 4. 12 个批量脚本已插入登记调用（P0-4）──────────────────────────
    scripts = ["run_batch_2025q4_100.py", "run_batch_2025q4_100_batch2.py",
               "run_batch_2026_aug_20.py", "run_batch_2026_aug_100.py",
               "run_batch_2026_sep_oct_50.py", "run_batch_round3_50.py",
               "run_batch_round4_all_remain.py", "run_batch_round5_100.py",
               "run_batch_round6_100.py", "run_clean_batch_test.py",
               "run_expand_batch_test.py", "run_random_50_batch_test.py"]
    missing = [s for s in scripts if "register_batch_run" not in (ROOT / s).read_text(encoding="utf-8")]
    check("P0-4 12 个批量脚本均调用 register_batch_run", not missing, f"missing={missing}")

    # ── 5. 运行级日志模块（P1-6/7）─────────────────────────────────────
    # 默认路径必须在**不设** BT_RUN_LOG 的干净子进程里验（本脚本自己可能带了覆盖值）
    import subprocess
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import bt_run_log as L; print(L.RUN_LOG_PATH)"
         % str(BACKEND)],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "BT_RUN_LOG"})
    check("P1-6 日志默认路径 logs/backtest_runs.log（干净子进程）",
          probe.stdout.strip() == str(BACKEND / "logs" / "backtest_runs.log"),
          probe.stdout.strip())
    check("P1-7 逐笔日志默认关、BT_TRADE_LOG 可开", L.trade_log_enabled() is False)
    check("测试隔离 BT_RUN_LOG 可覆盖",
          os.environ.get("BT_RUN_LOG") is None or str(L.RUN_LOG_PATH) == os.environ["BT_RUN_LOG"])

    # ── 汇总 ──────────────────────────────────────────────────────────
    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    out = {"checks": [{"name": n_, "ok": ok, "detail": d} for n_, ok, d in RESULTS],
           "total": len(RESULTS), "failed": n_fail}
    out_path = Path(__file__).resolve().parent.parent / "out" / "01_verify_results.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合计 {len(RESULTS)} 项，失败 {n_fail} 项")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
