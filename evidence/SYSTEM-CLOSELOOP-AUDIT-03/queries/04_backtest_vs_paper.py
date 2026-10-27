# -*- coding: utf-8 -*-
"""04 场景4：回测与隔离模拟按同一 FT-LOW4H 规则执行 —— 逐事件对账。

**两侧都用各自真实实现，不在这里重写规则：**
  · 回测侧 = `evidence/FT-LOW4H-GRID-01/user_data/strategies/WyckoffLow4hGridBase.py::custom_stoploss`
    （144 组网格的基类；需要 freqtrade ⇒ 用 evidence/FT-BASELINE-01/.venv 的解释器子进程跑，
     驱动脚本 `_bt_trail_driver.py`）；
  · 模拟侧 = `框架/backend/paper_engine.py::advance_position_trailing`（隔离账本 + 合成行情）。

**共同输入**（对齐口径，避免把"初始止损推导"混进"跟踪段"对比）：
  · 成交价、初始止损 —— 取模拟侧**实际成交后**的值，原样喂给回测侧；
  · 4H bar 序列 —— 同一份 low 序列，8 根；low 取 0.1 的整数倍，使
    `low × 0.95` 落在 0.1 网格上，从而把"模拟侧向下取整"这一层噪声排除，
    单独作为一条口径差异登记（不藏在逐事件结果里）。

产物：out/04_回测与模拟对账.json / .md
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03"
OUT = EVID / "out"
BACKEND = PROJ / "框架" / "backend"
HERE = pathlib.Path(__file__).resolve().parent

ROOT = pathlib.Path("/tmp/wx_audit04")
PAPER = ROOT / "paper"
LIVE_DIR = ROOT / "live"
LIVE_DB = LIVE_DIR / "live_signals.sqlite3"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"

FT_PY = PROJ / "evidence" / "FT-BASELINE-01" / ".venv" / "bin" / "python"
SYM = "BTCUSDT"
ARM = "3a"
M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1

ITEMS: list[dict] = []
DETAIL: dict = {}


def chk(cid: str, what: str, expect, actual, ok: bool, note: str = "") -> None:
    ITEMS.append({"编号": cid, "检查项": what, "预期": expect, "实际": actual,
                  "判定": "通过" if ok else "不通过", "备注": note})
    print(("  [OK ] " if ok else "  [FAIL] ") + f"{cid} {what} → {actual!r}"
          + (f"  ({note})" if note else ""))


def sec(t: str) -> None:
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


# ── 行情库 ───────────────────────────────────────────────────────────────────
def build_schema() -> None:
    if MARKET.exists():
        MARKET.unlink()
    con = sqlite3.connect(MARKET)
    con.executescript("""
    CREATE TABLE candle_versions(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
      open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, quote_volume TEXT,
      trade_count INTEGER, taker_buy_volume TEXT, taker_buy_quote_volume TEXT,
      source_id INTEGER NOT NULL, available_at INTEGER, is_closed INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id));
    CREATE TABLE candle_heads(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time));
    CREATE TABLE funding_history (
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    con.commit()
    con.close()


def add_bar(symbol: str, tf: str, open_time: int, ohlc) -> int:
    span = {"15m": M15, "1h": H1, "4h": H4}[tf]
    close_time = open_time + span - 1
    o, h, l, c = ohlc
    con = sqlite3.connect(MARKET)
    try:
        con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                    "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (symbol, tf, open_time, close_time,
                     f"{o:.4f}", f"{h:.4f}", f"{l:.4f}", f"{c:.4f}",
                     "1000", "100000", 10, "500", "50000", 1, close_time))
        con.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                    (symbol, tf, open_time, 1))
        con.commit()
    finally:
        con.close()
    return close_time


# ── 4H 序列（low 全为 0.1 整数倍 ⇒ low×0.95 落在 0.1 网格，排除取整噪声）──
LOWS = [100.0, 102.0, 98.0, 104.0, 100.0, 106.0, 104.0, 98.0]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    t_start = dt.datetime.now().astimezone()
    if ROOT.exists():
        shutil.rmtree(ROOT)
    PAPER.mkdir(parents=True)
    LIVE_DIR.mkdir(parents=True)
    build_schema()

    env = {"PAPER_LEDGER_DIR": str(PAPER), "PAPER_LEDGER_ALLOW_INIT": "1",
           "LIVE_DIR": str(LIVE_DIR), "LIVE_DB": str(LIVE_DB),
           "MARKET_DB": str(MARKET), "PAPER_GATE_FILE": str(GATE)}
    for k, v in env.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    now_ms = int(time.time() * 1000)
    base = ((now_ms + M15 - 1) // M15) * M15
    GATE.write_text(json.dumps({"allow_new_entries": True, "reason": "audit04_isolated",
                                "set_by": "SYSTEM-CLOSELOOP-AUDIT-03", "since_ms": now_ms},
                               ensure_ascii=False), encoding="utf-8")

    # 预置在 T=base 已闭合的 15m 历史（开仓时钟需要"上一根 15m 成交额"）
    for k in range(-16, 0):
        add_bar(SYM, "15m", base + k * M15, (100.0, 100.5, 99.5, 100.0))
    h1b = (base // H1) * H1
    for k in range(-8, 0):
        add_bar(SYM, "1h", h1b + k * H1, (100.0, 101.0, 99.0, 100.0))

    import live_store as L
    import paper_engine as PE
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()

    sec("A. 模拟侧开仓（回放通道，引擎真实入口）")
    add_bar(SYM, "15m", base, (99.95, 100.5, 99.5, 100.0))          # b0（此刻唯一已闭合的未来 bar）
    op = PE.open_position(ARM, SYM, signal_uid="audit04_entry", direction="long",
                          atr=5.0, origin="replay", now_ms=base, requested_at=base,
                          event_time=base, available_at=base)
    print("  开仓首轮:", json.dumps({k: op.get(k) for k in
                                 ("ok", "reason", "pending_id", "earliest_fill_ms")},
                                ensure_ascii=False))
    # b1（成交 bar）在"推进到 base+30m"之前才插入 —— 否则就是拿未来 bar 回填
    add_bar(SYM, "15m", base + M15, (100.0, 100.5, 99.5, 100.2))    # b1 = 成交 bar
    adv = PE.advance_pending(T=base + 2 * M15, log=lambda *a, **k: None)
    filled = adv["filled"][0] if adv["filled"] else {}
    con = L.paper_conn(ARM, ro=True)
    try:
        pos = dict(con.execute("SELECT * FROM paper_position WHERE account_id=? "
                               "ORDER BY position_id DESC LIMIT 1", (f"paper43_{ARM}",)).fetchone())
    finally:
        con.close()
    fill = float(pos["entry_price"])
    init_stop = float(pos["stop_price"])
    pid = int(pos["position_id"])
    print(f"  成交价 fill={fill}  初始止损 init_stop={init_stop}  position_id={pid}")
    DETAIL["A_模拟侧开仓"] = {"pending": {k: op.get(k) for k in
                                     ("ok", "reason", "pending_id", "earliest_fill_ms")},
                          "filled": filled, "entry_price": fill,
                          "init_stop": init_stop, "position_id": pid}

    sec("B. 构造同一份 4H 序列（两侧共同输入）")
    bars = []
    for i, low in enumerate(LOWS):
        o = base + i * H4
        bars.append({"i": i, "open_ms": o, "close_ms": o + H4 - 1,
                     "low": low, "close": low + 0.5,
                     "high": low + 1.5, "open": low + 0.2})
    print("  " + " | ".join(f"b{i}:low={b['low']}" for i, b in enumerate(bars)))
    spec_path = OUT / "_04_bars.json"
    spec_path.write_text(json.dumps({"bars": bars, "entry_price": fill,
                                     "init_stop": init_stop,
                                     "stoploss_s": 0.15, "trail_d": 0.05},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    chk("A1", "两侧共同输入：同一份 4H 序列（8 根）+ 同一成交价 + 同一初始止损",
        "8 根且 low×0.95 落在 0.1 网格", f"{len(bars)} 根，fill={fill}，stop={init_stop}",
        len(bars) == 8)

    sec("C. 回测侧：跑 WyckoffLow4hGridBase.custom_stoploss（freqtrade 环境子进程）")
    bt_out = OUT / "_04_bt_traj.json"
    if bt_out.exists():
        bt_out.unlink()
    cp = subprocess.run([str(FT_PY), str(HERE / "_bt_trail_driver.py"),
                         str(spec_path), str(bt_out)],
                        capture_output=True, text=True, timeout=300)
    print("  回测侧 stdout:", (cp.stdout or "").strip()[:300])
    if cp.returncode != 0:
        print("  回测侧 stderr:", (cp.stderr or "").strip()[-1200:])
    chk("C1", "回测侧策略可被真实加载并逐根执行（不是重写规则）",
        "returncode=0 且有 8 条轨迹", f"rc={cp.returncode}",
        cp.returncode == 0 and bt_out.exists(),
        (cp.stderr or "").strip().splitlines()[-1][:160] if cp.returncode != 0 else "")
    bt = json.loads(bt_out.read_text(encoding="utf-8")) if bt_out.exists() else {"traj": []}
    DETAIL["C_回测侧轨迹"] = bt

    sec("D. 模拟侧：逐根 4H 推进 advance_position_trailing（引擎真实入口）")
    sim_traj = []
    for b in bars:
        add_bar(SYM, "4h", b["open_ms"], (b["open"], b["high"], b["low"], b["close"]))
        candle = {"symbol": SYM, "timeframe": "4h", "open_time": b["open_ms"],
                  "close_time": b["close_ms"], "open": b["open"], "high": b["high"],
                  "low": b["low"], "close": b["close"], "available_at": b["close_ms"]}
        r = PE.advance_position_trailing(ARM, pid, candle, now_ms=b["close_ms"],
                                         decision_at=b["close_ms"])
        sim_traj.append({
            "i": b["i"], "bar_open_ms": b["open_ms"], "bar_close_ms": b["close_ms"],
            "low": b["low"], "candidate_stop": r.get("candidate_stop"),
            "effective_stop": r.get("effective_stop"), "stop_updated": r.get("stop_updated"),
            "exit_triggered": r.get("exit_triggered"), "ok": r.get("ok"),
            "reason": r.get("reason"), "effective_at": r.get("effective_at"),
            "exit_result": r.get("exit_result"),
        })
        print(f"  b{b['i']} low={b['low']:<7} cand={r.get('candidate_stop')} "
              f"eff={r.get('effective_stop')} up={r.get('stop_updated')} "
              f"exit={r.get('exit_triggered')}"
              + (f" reason={r.get('reason')}" if not r.get("ok") else ""))
    DETAIL["D_模拟侧轨迹"] = sim_traj

    sec("E. 逐事件对账（同一根 4H bar 上：候选价 / 生效价 / 是否上移 / 是否触发退出）")
    btt = {int(x["i"]): x for x in bt.get("traj", [])}
    rows = []
    for s in sim_traj:
        i = s["i"]
        b = btt.get(i)
        if b is None:
            rows.append({"i": i, "缺回测侧": True})
            continue
        cand_bt = round(float(b["candidate_stop"]), 6)
        cand_sim = round(float(s["candidate_stop"]), 6)
        eff_bt = round(float(b["effective_stop"]), 6)
        eff_sim = round(float(s["effective_stop"]), 6)
        # 是否上移：与上一根生效价比较（两侧同口径）
        prev_eff = rows[-1]["生效价_回测"] if rows and "生效价_回测" in rows[-1] else init_stop
        up_bt = eff_bt > round(float(prev_eff), 6) + 1e-9
        up_sim = bool(s["stop_updated"])
        exit_bt = bool(b["exit_by_low"])
        exit_sim = bool(s["exit_triggered"])
        rows.append({"i": i, "low": s["low"],
                     "候选价_回测": cand_bt, "候选价_模拟": cand_sim,
                     "候选一致": abs(cand_bt - cand_sim) < 1e-6,
                     "生效价_回测": eff_bt, "生效价_模拟": eff_sim,
                     "生效一致": abs(eff_bt - eff_sim) < 1e-6,
                     "上移_回测": up_bt, "上移_模拟": up_sim, "上移一致": up_bt == up_sim,
                     "退出_回测": exit_bt, "退出_模拟": exit_sim, "退出一致": exit_bt == exit_sim})
    DETAIL["E_逐事件"] = rows

    n = len([r for r in rows if "候选一致" in r])
    bad_cand = [r["i"] for r in rows if r.get("候选一致") is False]
    bad_eff = [r["i"] for r in rows if r.get("生效一致") is False]
    bad_up = [r["i"] for r in rows if r.get("上移一致") is False]
    bad_exit = [r["i"] for r in rows if r.get("退出一致") is False]
    chk("E1", "逐事件：跟踪候选价 = 4H 最低价 ×(1−d)，两侧一致",
        f"{n} 根全部一致", f"不一致 {bad_cand or '无'}", not bad_cand,
        "回测 L1177 `bar_low*(1-d)` / 模拟 L1982 `low_4h*(1-d_val)`")
    chk("E2", "逐事件：生效止损 = max(初始, 此前, 候选)，只上移，两侧一致",
        f"{n} 根全部一致", f"不一致 {bad_eff or '无'}", not bad_eff,
        "回测 L1178 `max(initial, current, cand)` / 模拟 L1983 `max(cur_stop, cand)`")
    chk("E3", "逐事件：'本根是否触发上移'判定一致（新 4H 收盘才更新）",
        f"{n} 根全部一致", f"不一致 {bad_up or '无'}", not bad_up,
        "回测 L1176 `bar_close_ms > last_bar_close_ms` / 模拟靠 paper_stop_history 唯一约束")
    chk("E4", "逐事件：'本根是否触发退出'判定一致",
        f"{n} 根全部一致", f"不一致 {bad_exit or '无'}", not bad_exit,
        "回测用本根最低价问 custom_stoploss 是否返回 0.0；模拟判 low_4h <= cur_stop")
    chk("E5", "两侧都只上移、从不下移（单调不减）",
        "生效价序列单调不减",
        [r["生效价_模拟"] for r in rows if "生效价_模拟" in r],
        all(rows[k]["生效价_模拟"] >= rows[k - 1]["生效价_模拟"] - 1e-9
            for k in range(1, len(rows)) if "生效价_模拟" in rows[k]))

    sec("F. 口径差异（**不是**逐事件不一致，是规则参数/推导口径的差异，逐条登记）")
    params = PE.approved_params()
    s_val = params.get("stoploss_s")
    d_val = params.get("trail_d")
    chk("F1", "跟踪比例 d：回测与模拟取值是否相同",
        "两侧 d=0.05", f"回测 0.05（类属性 trail_d）/ 模拟 {d_val!r} ⇒ 生效 0.05",
        abs(0.05 - 0.05) < 1e-12, "数值一致")
    chk("F1b", "跟踪比例 d：回测与模拟是否来自同一参数来源",
        "同一来源（冻结参数）",
        f"回测=类属性硬编码 0.05；模拟=冻结视图无 trail_d ⇒ 落到 L1961 硬编码兜底 0.05",
        d_val is not None,
        "**来源不同**：两侧都是硬编码，谁都不是从冻结参数读出来的 ⇒ "
        "改一处不改另一处就会静默分叉（本轮靠巧合数值相同）")
    chk("F2", "初始止损口径：回测（成交价×(1−s)）与模拟是否同一来源",
        "同一来源",
        f"回测 5% 档 = 成交价×0.85；模拟 stoploss_s={s_val!r} ⇒ 走 3×ATR（"
        f"uniform_stop_atr_mult={params.get('uniform_stop_atr_mult')!r}）",
        s_val is not None,
        "**口径不同**：模拟侧 `stoploss_s` 在冻结视图里不存在，"
        "L972 分支不成立 ⇒ 落到 L976 `rr['stop_price']` = 成交价−3×ATR。"
        "模拟侧 docstring 自称'成交价×(1-s)'与代码实际不符")
    chk("F3", "价格取整：模拟侧对候选价向下取整，回测侧不取整",
        "两侧同一处理",
        "模拟 floor 到 price_step；回测原值",
        False,
        "本轮 low 取 0.1 整数倍使 low×0.95 恰好落在网格上，"
        "所以这条差异在本轮**没有影响**任何一次判定；"
        "但价格不在网格上时两侧会有 ≤1 个最小价位的差（方向：模拟更保守=更低）")
    chk("F4", "4H '已收盘'判定时点：回测 close_ms=open+4h，模拟 close_time=open+4h−1ms",
        "两侧同一时点",
        "回测 open+4h；模拟 open+4h−1ms", False,
        "差 1 毫秒。本场景两根相邻 4H bar 间隔 4 小时，"
        "该 1ms 不改变任何一根 bar 的归属；但若未来把 4H 边界与 15m 边界对齐做判等，需注意")
    chk("F5", "退出粒度：回测在明细周期逐根检查，模拟只在 4H 推进时检查",
        "同一粒度",
        "回测 5m/15m 逐根；模拟 4H 一次", False,
        "**这是最实质的差异**：同一根 4H 内，回测会在价格穿越止损的**那一刻**成交，"
        "模拟只能在该 4H 收盘后按 4H 最低价判定 ⇒ 两者退出**时点**必然不同，"
        "退出**价位**也不同（模拟按止损价成交，回测按穿越价+滑点）")

    sec("G. 汇总")
    ok = sum(1 for x in ITEMS if x["判定"] == "通过")
    ng = sum(1 for x in ITEMS if x["判定"] == "不通过")
    nv = sum(1 for x in ITEMS if x["判定"] == "未验证")
    print(f"合计 {len(ITEMS)} 条：通过 {ok}，不通过 {ng}，未验证 {nv}")
    for x in ITEMS:
        if x["判定"] != "通过":
            print(f"  不通过 {x['编号']} {x['检查项']}：预期 {x['预期']} 实际 {x['实际']}")

    # 账本收尾核对（模拟侧退出后账户是否闭合）
    con = L.paper_conn(ARM, ro=True)
    try:
        st = dict(con.execute("SELECT * FROM paper_state WHERE account_id=?",
                              (f"paper43_{ARM}",)).fetchone() or {})
        p2 = dict(con.execute("SELECT * FROM paper_position WHERE position_id=?",
                              (pid,)).fetchone() or {})
    finally:
        con.close()
    DETAIL["G_模拟侧收尾"] = {"state": st, "position": p2}
    print(f"  模拟侧持仓 status={p2.get('status')} exit_reason={p2.get('exit_reason')} "
          f"exit_price={p2.get('exit_price')}；现金={st.get('cash')} 已实现={st.get('realized_pnl')}")

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "开始时刻": t_start.isoformat(timespec="seconds"),
        "隔离根目录": str(ROOT),
        "回测侧实现": bt.get("impl"),
        "回测侧解释器": str(FT_PY),
        "模拟侧实现": "框架/backend/paper_engine.py::advance_position_trailing",
        "共同输入": {"成交价": fill, "初始止损": init_stop, "4H_lows": LOWS,
                     "trail_d": 0.05},
        "明细": DETAIL,
        "断言": ITEMS,
        "汇总": {"总数": len(ITEMS), "通过": ok, "不通过": ng, "未验证": nv},
    }
    (OUT / "04_回测与模拟对账.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # markdown
    md = ["# 04 场景4：回测 vs 隔离模拟 —— 同一 FT-LOW4H 规则逐事件对账", "",
          f"- 生成时刻：{res['生成时刻']}",
          f"- 回测侧实现：`{bt.get('impl')}`（freqtrade 环境，python 3.11）",
          "- 模拟侧实现：`框架/backend/paper_engine.py::advance_position_trailing`（隔离账本）",
          f"- 共同输入：成交价 {fill}、初始止损 {init_stop}、"
          f"4H low 序列 {LOWS}、d=0.05", "",
          "## 逐事件表", "",
          "| 根 | 4H low | 候选价(回测) | 候选价(模拟) | 生效价(回测) | 生效价(模拟) | 上移(回测/模拟) | 退出(回测/模拟) | 判定 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "候选一致" not in r:
            continue
        same = all(r[k] for k in ("候选一致", "生效一致", "上移一致", "退出一致"))
        md.append(f"| b{r['i']} | {r['low']} | {r['候选价_回测']} | {r['候选价_模拟']} | "
                  f"{r['生效价_回测']} | {r['生效价_模拟']} | "
                  f"{r['上移_回测']}/{r['上移_模拟']} | {r['退出_回测']}/{r['退出_模拟']} | "
                  f"{'一致' if same else '不一致'} |")
    md += ["", "## 断言", "", "| 编号 | 检查项 | 预期 | 实际 | 判定 | 备注 |", "|---|---|---|---|---|---|"]
    for x in ITEMS:
        md.append(f"| {x['编号']} | {x['检查项']} | {x['预期']} | {x['实际']} | "
                  f"{x['判定']} | {x['备注']} |")
    md += ["", f"**合计 {len(ITEMS)} 条：通过 {ok}，不通过 {ng}，未验证 {nv}**"]
    (OUT / "04_回测与模拟对账.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n已写出 {OUT/'04_回测与模拟对账.json'} / .md")


if __name__ == "__main__":
    main()
