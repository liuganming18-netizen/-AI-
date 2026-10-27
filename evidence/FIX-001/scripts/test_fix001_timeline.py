# -*- coding: utf-8 -*-
"""FIX-001 回归测试：统一时间轴推进与仓位槽位释放。

跑法
----
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/wyckoff-framework/bin/python
    $PY evidence/FIX-001/scripts/test_fix001_timeline.py            # 只跑可移植用例
    $PY evidence/FIX-001/scripts/test_fix001_timeline.py --integration   # 追加真实数据端到端

设计说明（为什么分成两层）
--------------------------
T1/T2 是**可移植**用例：不读行情库、不碰真实账本，从 GitHub 克隆到任何机器都能跑。
T3 是**端到端**用例：必须读本机 66G 行情库，且引擎 `new_entry_gate()` 默认 fail-closed，
需要 fixture 配置 + 隔离账本才能开仓 ⇒ 缺数据/缺配置时明确 skip，不伪装通过。

T2 用「插桩替身」代替 `paper_engine`，原因不是偷懒：引擎开仓要过 `new_entry_gate`
（默认拒绝一切新开仓）、配置指纹校验、paper epoch 门禁三道硬闸，构造满足它们的
合成环境等价于重写一个引擎。T2 因此只验证**本次改动的那段编排逻辑**——
「同一时刻是否先推进退出再尝试入场」「结果是否与输入币种顺序无关」；
真实引擎下的成交结果由 T3 负责。
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
BACKEND = PROJECT / "框架" / "backend"
for p in (str(PROJECT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

import accum_early_entry as A  # noqa: E402

FAILED: list[str] = []
PASSED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [通过] {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [失败] {name}  {detail}")


# ══════════════════════════════════════════════════════════════════════
# T1：候选拍平与排序 —— 结果与输入币种顺序无关
# ══════════════════════════════════════════════════════════════════════
def t1_event_order() -> None:
    print("\nT1 统一时间轴排序：与输入币种顺序无关")
    T = 1_700_000_000_000

    def mk(sym, scheme, ts, uid):
        return {"symbol": sym, "candidates": [{
            "scheme": scheme, "signal_uid": uid,
            "entry_bar_open_ms": ts, "earliest_entry_ms": ts - 1,
            "confirm_close_ms": ts - 900_000}]}

    base = [mk("BTCUSDT", "4a", T, "u1"),
            mk("ETHUSDT", "4a", T, "u2"),          # 同刻，字母序 ETH < BTC? 否，B<E
            mk("ADAUSDT", "4a", T, "u3"),
            mk("SOLUSDT", "4a", T + 900_000, "u4")]

    ref = [(e["t"], e["symbol"], e["scheme"], e["signal_uid"])
           for e in A._timeline_events(base)]
    check("T1.1 排序键为 (时间戳, symbol, scheme, signal_uid)",
          ref == sorted(ref), f"实际={ref}")

    # 同刻按 symbol 字母升序
    same_ts = [r[1] for r in ref if r[0] == T]
    check("T1.2 同一时间戳内按 symbol 字母升序",
          same_ts == ["ADAUSDT", "BTCUSDT", "ETHUSDT"], f"实际={same_ts}")

    # 打乱 50 次，事件序列必须逐条相同
    bad = 0
    for _ in range(50):
        shuffled = base[:]
        random.shuffle(shuffled)
        got = [(e["t"], e["symbol"], e["scheme"], e["signal_uid"])
               for e in A._timeline_events(shuffled)]
        if got != ref:
            bad += 1
    check("T1.3 打乱输入顺序 50 次，事件序列逐条一致", bad == 0, f"不一致 {bad} 次")

    # 缺时间戳的候选应被丢弃而不是塞到 0
    dropped = A._timeline_events([{"symbol": "X", "candidates": [
        {"scheme": "4a", "signal_uid": "u9"}]}])
    check("T1.4 缺 entry_bar_open_ms/earliest_entry_ms 的候选被跳过",
          dropped == [], f"实际={dropped}")


# ══════════════════════════════════════════════════════════════════════
# T2：编排顺序 —— 同刻先退出释放槽位，再入场；槽位真能复用
# ══════════════════════════════════════════════════════════════════════
class FakeLedger:
    """最小槽位模型：只记账位占用与调用顺序，替代 paper_engine。"""

    def __init__(self, max_positions: int, exit_plan: dict):
        self.max_positions = max_positions
        self.exit_plan = exit_plan          # symbol -> 计划平仓时刻
        self.positions: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self._next = 1

    # 被 A.open_positions 调用
    def open_positions(self, scheme):
        return [{"position_id": pid} for pid, p in self.positions.items()
                if "closed_at" not in p]

    # 被 A.try_open 调用（由 _replay_timeline 调用）
    def try_open(self, scheme, symbol, cand, cfg, store, **kw):
        t = int(cand["entry_bar_open_ms"])
        self.calls.append(("open", symbol, t))
        if len(self.open_positions(scheme)) >= self.max_positions:
            return {"ok": False, "called_paper_engine": True,
                    "paper_result": {"ok": False, "reason": "max_positions_reached"}}
        pid = self._next
        self._next += 1
        self.positions[pid] = {"symbol": symbol, "entry": t}
        return {"ok": True, "called_paper_engine": True,
                "paper_result": {"ok": True, "position_id": pid}}

    # 被 A.drive_position_exit 调用（由 _advance_open_positions 调用）
    def drive_position_exit(self, scheme, position_id, feed, *, cfg, end_ms,
                            legacy_4h_exit=False, store=None):
        self.calls.append(("drive", position_id, int(end_ms)))
        p = self.positions[int(position_id)]
        plan = self.exit_plan.get(p["symbol"])
        if plan is not None and plan <= int(end_ms):
            p["closed_at"] = plan            # 真实引擎记的是 bar 时刻，不是 end_ms
            return {"ok": True, "exit_ts": plan, "position_id": int(position_id)}
        return {"ok": True, "exited": False, "reason": "open_at_window_end",
                "position_id": int(position_id)}


def _patched(fake: FakeLedger, skip_advance: bool = False):
    """临时替换模块级符号，返回还原函数。"""
    saved = (A.try_open, A.drive_position_exit, A.open_positions,
             A._advance_open_positions)

    def _open_positions(scheme):
        return fake.open_positions(scheme)

    A.try_open = fake.try_open
    A.drive_position_exit = fake.drive_position_exit
    A.open_positions = _open_positions
    if skip_advance:
        A._advance_open_positions = lambda *a, **k: []      # 模拟「退出后置」的旧行为

    def restore():
        (A.try_open, A.drive_position_exit, A.open_positions,
         A._advance_open_positions) = saved
    return restore


def _cand(scheme, symbol, ts, uid):
    return {"scheme": scheme, "signal_uid": uid, "symbol": symbol,
            "entry_bar_open_ms": ts, "earliest_entry_ms": ts - 1,
            "confirm_close_ms": ts - 900_000}


def t2_slot_release() -> None:
    print("\nT2 槽位释放：A 占满 → A 平仓 → 后续时刻 B 成功入场")
    T = 1_700_000_000_000
    STEP = 900_000
    MAXPOS = 1

    per_symbol = [
        {"symbol": "AAAUSDT", "candidates": [_cand("4a", "AAAUSDT", T, "a1")]},
        {"symbol": "BBBUSDT", "candidates": [_cand("4a", "BBBUSDT", T, "b1"),
                                             _cand("4a", "BBBUSDT", T + STEP, "b2")]},
    ]
    # A 在 T+300 秒平仓 ⇒ 在 T 时刻仍占槽，到 T+STEP 已释放
    exit_plan = {"AAAUSDT": T + 300_000}

    # ── 修正后（统一时间轴）──
    fake = FakeLedger(MAXPOS, exit_plan)
    restore = _patched(fake)
    try:
        exits, stats = A._replay_timeline(None, cfg={}, per_symbol=per_symbol,
                                          schemes=("4a",), store=None)
    finally:
        restore()

    opened = [p["symbol"] for p in fake.positions.values()]
    check("T2.1 A 在 T 时刻占满唯一槽位",
          fake.positions.get(1, {}).get("symbol") == "AAAUSDT", f"实际={fake.positions}")
    check("T2.2 同刻 B 因槽位已满被拒",
          any(c[0] == "open" and c[1] == "BBBUSDT" and c[2] == T for c in fake.calls),
          "B 在 T 时刻的入场尝试缺失")
    check("T2.3 A 平仓后 B 在后续时间戳成功入场",
          any(p["symbol"] == "BBBUSDT" for p in fake.positions.values()),
          f"实际成交={opened}")
    check("T2.4 本次共开仓 2 笔（A + B）",
          stats["opened"] == 2, f"实际 opened={stats['opened']}")

    # 同刻顺序：T+STEP 上必须先 drive（释放槽位）再 open（B 入场）
    at_step = [c for c in fake.calls if c[2] == T + STEP]
    first_open = next((i for i, c in enumerate(at_step) if c[0] == "open"), None)
    last_drive = max((i for i, c in enumerate(at_step) if c[0] == "drive"), default=-1)
    check("T2.5 同一时间戳内先推进退出、后尝试入场",
          first_open is not None and last_drive < first_open,
          f"该时刻调用序列={at_step}")

    # ── 负向对照：把退出推进关掉（等价旧行为「退出后置」），B 必须进不来 ──
    fake2 = FakeLedger(MAXPOS, exit_plan)
    restore2 = _patched(fake2, skip_advance=True)
    try:
        A._replay_timeline(None, cfg={}, per_symbol=per_symbol,
                           schemes=("4a",), store=None)
    finally:
        restore2()
    check("T2.6 负向对照：退出不推进时 B 进不来（证明本测试能识别旧缺陷）",
          not any(p["symbol"] == "BBBUSDT" for p in fake2.positions.values()),
          f"实际成交={[p['symbol'] for p in fake2.positions.values()]}")


def t2_order_invariance() -> None:
    print("\nT2b 编排结果与输入币种顺序无关")
    T = 1_700_000_000_000
    STEP = 900_000
    MAXPOS = 2

    syms = ["CCCUSDT", "AAAUSDT", "EEEUSDT", "BBBUSDT", "DDDUSDT"]
    per_symbol = []
    for i, s in enumerate(syms):
        per_symbol.append({"symbol": s, "candidates": [
            _cand("4a", s, T + (i % 2) * STEP, f"{s}-1")]})
    exit_plan = {s: T + 600_000 for s in syms}

    def run(order):
        fake = FakeLedger(MAXPOS, exit_plan)
        restore = _patched(fake)
        try:
            A._replay_timeline(None, cfg={}, per_symbol=order,
                               schemes=("4a",), store=None)
        finally:
            restore()
        return [(p["symbol"], p["entry"]) for p in fake.positions.values()]

    ref = run(per_symbol)
    bad = 0
    for _ in range(20):
        shuffled = per_symbol[:]
        random.shuffle(shuffled)
        if run(shuffled) != ref:
            bad += 1
    check("T2b.1 打乱输入顺序 20 次，成交集合与时刻逐条一致", bad == 0, f"不一致 {bad} 次")
    # 槽位 2：T 时刻 3 个候选抢 2 个槽（按字母序 CCC/DDD 成交，EEE 被拒）；
    # 到 T+STEP 两笔都已按计划平仓并释放槽位 ⇒ AAA/BBB 成交。合计 4 笔。
    check("T2b.2 T 时刻 3 抢 2：只成交字母序前 2 个",
          sorted(s for s, t in ref if t == T) == ["CCCUSDT", "DDDUSDT"], f"实际={ref}")
    check("T2b.3 退出释放槽位后，后一时刻的 2 个候选全部成交",
          sorted(s for s, t in ref if t != T) == ["AAAUSDT", "BBBUSDT"], f"实际={ref}")
    check("T2b.4 合计成交 4 笔（槽位被复用，而不是只成交 2 笔）",
          len(ref) == 4, f"实际={ref}")


# ══════════════════════════════════════════════════════════════════════
# T3：真实数据端到端（缺数据/缺配置则 skip，不伪装通过）
# ══════════════════════════════════════════════════════════════════════
def t3_integration(symbols: list[str], start: str, end: str) -> None:
    print("\nT3 真实数据端到端：打乱顺序结果一致 + 旧行为对照")
    cfg_path = (PROJECT / "evidence" / "ACCUM-EARLY-06-W2" / "01" / "config"
                / "fixture_rules_06w2.json")
    if not cfg_path.exists():
        print(f"  [跳过] 缺少 fixture 配置：{cfg_path}")
        return
    try:
        import research_15m as R
        hot, _cold = R.default_paths()
        if not Path(hot).exists():
            print(f"  [跳过] 缺少行情库：{hot}")
            return
    except Exception as exc:                                  # noqa: BLE001
        print(f"  [跳过] 行情库不可用：{exc}")
        return

    run_dir = PROJECT / "evidence" / "FIX-001" / "runs" / "integration"
    (run_dir / "paper").mkdir(parents=True, exist_ok=True)
    gate = run_dir / "gate.json"
    gate.write_text(json.dumps({"allow_new_entries": True,
                                "set_by": "FIX-001 回归测试（隔离目录）"}), encoding="utf-8")
    os.environ.update({
        "PAPER_LEDGER_DIR": str(run_dir / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DB": str(run_dir / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(gate),
        "ACCUM05_CONFIG": str(cfg_path),
        "ACCUM05_FIXTURE_OK": "1",
        "WX_MARKET_MERGE": "1",
    })
    s_ms = A._ms_of(start)
    e_ms = A._ms_of(end)
    obs_db = run_dir / "observe.sqlite3"

    def run(order, timeline):
        # 每次运行必须清干净：账本、实时库、**观察库**
        # 观察库不清会让候选命中 dedup 而被跳过（07-W5 的候选已在共享库里），
        # 结果变成 0 候选 0 成交的「空对空」。
        for f in (run_dir / "paper").glob("*.sqlite3*"):
            f.unlink()
        for f in run_dir.glob("live_signals.sqlite3*"):
            f.unlink()
        for f in run_dir.glob("observe.sqlite3*"):
            f.unlink()
        res = A.research_run(config=str(cfg_path), symbols=order,
                             start_ms=s_ms, end_ms=e_ms, max_boxes=40,
                             open_candidates=True, drive_exits=True,
                             timeline_entries=timeline, observe_db=str(obs_db))
        led = res["ledgers"].get("4a") or {}
        n_cand = sum(len(r.get("candidates") or []) for r in res["per_symbol"])
        # net_pnl 在 paper_realized，不在 paper_position ⇒ 按 position_id 关联
        pnl = {r["position_id"]: r.get("net_pnl") for r in led.get("realized", [])}
        rows = [(p["symbol"], p["entry_ts"], p["exit_ts"], p["exit_reason"],
                 p["exit_price"], pnl.get(p["position_id"]))
                for p in led.get("positions", [])]
        return sorted(rows, key=lambda x: (x[0], x[1])), (res.get("errors") or []), n_cand

    try:
        a, err_a, cand_a = run(symbols, True)
        b, err_b, cand_b = run(list(reversed(symbols)), True)

        # 空对空不算证据：没有候选就说明环境没搭对，必须跳过而不是判通过
        if cand_a == 0:
            print("  [跳过] 本次 0 个候选 ⇒ 环境未就绪，空对空比较不构成证据")
            return

        check("T3.1 打乱币种顺序后持仓逐字段一致（含 symbol/entry_ts/exit_ts/net_pnl）",
              a == b and not err_a and not err_b,
              f"A={len(a)} 笔 / B={len(b)} 笔；错误={err_a or err_b}")
        check("T3.2 两次运行的候选数一致（扫描不受输入顺序影响）",
              cand_a == cand_b, f"A={cand_a} / B={cand_b}")
        legacy, _, _ = run(symbols, False)
        check("T3.3 修正后成交笔数严格多于旧行为（槽位真的被释放）",
              len(a) > len(legacy),
              f"timeline={len(a)} / legacy={len(legacy)}")
        print(f"  [记录] 候选={cand_a}，timeline 成交={len(a)} 笔，legacy 成交={len(legacy)} 笔")
    except Exception as exc:                                  # noqa: BLE001
        check("T3 端到端", False, f"运行异常：{exc!r}")


def main(argv: list[str]) -> int:
    t1_event_order()
    t2_slot_release()
    t2_order_invariance()

    if "--integration" in argv:
        idx = argv.index("--integration")
        rest = argv[idx + 1:]
        syms = (rest[0].split(",") if rest else
                ["ZORAUSDT", "XTZUSDT", "FLUXUSDT", "DOLOUSDT", "ZETAUSDT"])
        t3_integration([s.strip() for s in syms if s.strip()],
                       "2026-06-27T08:00:00Z", "2026-10-26T09:00:00Z")
    else:
        print("\n[跳过] T3 真实数据端到端（加 --integration 运行）")

    print("\n" + "=" * 62)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for f in FAILED:
        print(f"  失败：{f}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
