# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜15 双会话逐项对账（定位「最早分歧」+ 候选重复/遗漏）。

## 为什么要重写定位逻辑

12 号第一版把「最早分歧」定位在 4h 收盘采样上，结果 9 个币全部报
`分歧@ 2026-10-24T23:59:59Z`、`前一根相同=None`。这不是分歧真的发生在那里，
而是**采样口径的假象**：

  · `prod_session(advance="1h")` 的 hook 收到的是 **1h 收盘**；
  · C 从 `T0` 起跑、R 从 `Tr` 起跑，两者可比的 4h 收盘只剩 `[Tr, T1]` 里的那几根；
  · `T1 = 04:59:59Z`、`Tr = 22:59:59Z` ⇒ 交集里**只有 1 根 4h 收盘**；
  · 交集只有 1 个点 ⇒ 首个比较点当然「不同」，`prev` 当然 `None`。

**结论：4h 采样根本不可能定位到「最早分歧」**，因为可比区间太短。
正确做法是拿**状态机自己的转移日志**（`sm.transitions`，每条都带
`trigger_bar_close_ms`）来比 —— 它覆盖整个重放窗口，不依赖采样。

## 本脚本做什么

给一组标的 + 一组会话参数，跑 C（持续）与 R（真两段重启），逐币输出：

1. **转移日志逐条对账**：按 `trigger_bar_close_ms` 建索引，找最早一条不一致
   （内容不同，或只有一边有）。这就是「最早分歧 K 线」。
   同时给出**分歧前最后一条相同的转移**（= 工单要的「分歧前状态」）。
2. **候选明细对账**：`candidate_id` 集合差（only_C / only_R）+ **重复检测**
   （同一 `candidate_id` 在 `sm.candidates` 里出现多次 = 重复登记）。
3. **终态结构对账**：`diff_key` 全字段，含箱体/扫低/回踩/候选身份。
4. **输入历史对账**：两边窗口内 4h / 1h 根数、首根时刻、被移出窗口的 4h 清单。
5. **冷却值是否「行为惰性」**：`cooling_until_ms` 只在**没有活跃事件**时参与
   建档闸门（`check_and_form_box` 第一件事就是 `if self.active_event is not None: return None`），
   而建档时**不清零**它。所以「已有活跃事件时，cooling < box_formed」⇒ 这个
   字段此刻不参与任何判定，差异不产生行为后果。必须逐币算出来，不能口头断言。

## 两种供给口径（`--keep-mode`）

  · `as06`       —— 复刻 06 的 `03_restart_consistency.py`：
                    `keep_h = max(depth, anchor)`，`keep_from_ms = upto - keep_h*H1`。
                    **注意**：`00_common.load_hist` 的 `keep_from_ms` 只截 1h，
                    4h 仍从 `read_start` 起 ⇒ 1h 地板会比 4h 地板**晚**。
                    这是 06 那版的口径，必须能复现它的差异集合。
  · `consistent` —— 供给一致：`keep_from_ms = min(floor_C, floor_R)`，
                    4h 与 1h 的地板对齐，排除「1h 被截断」这个非重启因素。

两者都跑，才能区分「真重启差异」与「供给口径造成的假差异」。

## 用法

    # D1160 的 9 个差异币（两种口径都跑）
    python code/15_dual_session_diff.py --upto 2026-10-25T04:59:59Z \
        --depth 1160 --t0-hours 72 --restart-hours 6 --keep-mode both \
        --symbols AEROUSDT,AGTUSDT,... 

    # anchor2880 下候选日志不同的 6 个币
    python code/15_dual_session_diff.py --upto 2026-10-25T04:59:59Z \
        --depth 2880 --t0-hours 72 --restart-hours 6 --anchor-hours 2880 \
        --keep-mode as06 --symbols 1000PEPEUSDT,...

产出：out/15_双会话对账_{tag}.json、out/15_双会话对账_{tag}.csv
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT
H1 = cm.H1


# ── 从引擎里抠证据（只读，不改状态机）─────────────────────────────────────
def trans_index(eng, sym):
    """{trigger_bar_close_ms: [转移摘要...]}。同一根可能有多条（先失效后升级）。"""
    e = eng.engines.get(sym)
    if e is None:
        return {}
    m = {}
    for t in e.sm.transitions:
        k = int(t.trigger_bar_close_ms)
        m.setdefault(k, []).append({
            "from": t.from_state, "to": t.to_state,
            "reason": t.reason, "event_id": t.event_id,
        })
    return m


def cand_list(eng, sym):
    e = eng.engines.get(sym)
    if e is None:
        return []
    out = []
    for c in e.sm.candidates:
        out.append({
            "candidate_id": c.candidate_id,
            "snapshot_ms": int(c.snapshot_time_ms),
            "exec_earliest_ms": int(c.execution_earliest_ms),
        })
    return out


def dup_of(cands):
    """同一 candidate_id 出现多次 = 重复登记（返回重复的 id 列表）。"""
    seen, dup = set(), []
    for c in cands:
        cid = c["candidate_id"]
        if cid in seen:
            dup.append(cid)
        else:
            seen.add(cid)
    return dup


def first_divergence(mc, mr, lo_ms):
    """最早一条不一致的转移（只看两边都覆盖得到的 `>= lo_ms` 区间）。"""
    keys = sorted(set(mc) | set(mr))
    first, prev_same = None, None
    for k in keys:
        if k < lo_ms:
            continue
        if mc.get(k) != mr.get(k):
            first = k
            break
        prev_same = k
    return first, prev_same


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    depth_h, t0_h, k_h = 1160, 72, 6
    anchor_h = None
    keep_mode = "both"
    syms_arg = None
    tag_extra = ""
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
        if a == "--depth" and i + 1 < len(argv):
            depth_h = int(argv[i + 1])
        if a == "--t0-hours" and i + 1 < len(argv):
            t0_h = int(argv[i + 1])
        if a == "--restart-hours" and i + 1 < len(argv):
            k_h = int(argv[i + 1])
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])
        if a == "--keep-mode" and i + 1 < len(argv):
            keep_mode = argv[i + 1]
        if a == "--symbols" and i + 1 < len(argv):
            syms_arg = [x.strip().upper() for x in argv[i + 1].split(",") if x.strip()]
        if a == "--tag" and i + 1 < len(argv):
            tag_extra = "_" + argv[i + 1]

    T1 = cm.parse_utc_ms(upto_s)
    T0 = T1 - t0_h * H1
    Tr = T1 - k_h * H1
    anchor_ms = (T1 - anchor_h * H1) if anchor_h else None
    # **锚点模式下地板恒等于锚点**（`resolve_replay_floor` 的现行实现），
    # 与 upto 无关 ⇒ C 与 R 的地板**完全相同**。旧写法 `T0 - depth_h*H1` 是错的
    # （那是 `min(upto-replay, anchor)` 时代的算法，已被代码注释明确否定）。
    if anchor_ms:
        floor_C = floor_R = int(anchor_ms)
    else:
        floor_C = T0 - depth_h * H1
        floor_R = Tr - depth_h * H1

    syms = syms_arg or cm.universe()[0]
    print(f"[cfg] upto={cm.utc(T1)} depth={depth_h}h T0={cm.utc(T0)} Tr={cm.utc(Tr)} "
          f"anchor={cm.utc(anchor_ms) or '无'} 币数={len(syms)}")
    print(f"[cfg] floor_C={cm.utc(floor_C)} floor_R={cm.utc(floor_R)}")

    # ── 省资源模式的合法性：驱动序列必须等价 ──────────────────────────────
    # `prod_session(advance='1h')` 的 step 驱动序列 = 这些币 1h 收盘时刻的并集。
    # 只比「最大 open」不够 —— 少一根中间的收盘就会改变 step 次数。
    # 所以直接比 **close_time 集合**。
    drive_equiv, drive_detail = None, {}
    if syms_arg:
        drive9 = sorted({int(b["close_time"]) for s in syms
                         for b in (cm.load_hist([s], T1, read_days=200)[0]
                                   .get(s, {}).get("1h") or [])
                         if int(b["open_time"]) < T1 and int(b["close_time"]) > T0})
        con = cm.market_conn()
        try:
            rows = con.execute(
                "SELECT DISTINCT close_time FROM candles WHERE exchange='binance' "
                "AND market='um' AND timeframe='1h' AND is_closed=1 "
                "AND open_time<? AND close_time>?", (T1, T0)).fetchall()
        finally:
            con.close()
        driveall = sorted(int(r[0]) for r in rows)
        drive_equiv = (drive9 == driveall)
        drive_detail = {"n_close_9": len(drive9), "n_close_all": len(driveall),
                        "only_in_9": [cm.utc(x) for x in set(drive9) - set(driveall)][:10],
                        "only_in_all": [cm.utc(x) for x in set(driveall) - set(drive9)][:10]}
        print(f"[drive] 驱动序列 9币={len(drive9)} 全池={len(driveall)} ⇒ 等价={drive_equiv}")
        if not drive_equiv:
            print("[FATAL] 驱动序列不等价，省资源模式的结论不成立，停止。")
            return 2

    modes = ["as06", "consistent"] if keep_mode == "both" else [keep_mode]
    results = {}

    for mode in modes:
        keep_h = max(depth_h, anchor_h or 0)
        # 与 18/19 号同一口径（`int(keep_h/24)+10`），避免「读入天数」成为第二个变量
        read_days = int(keep_h / 24) + 10
        if mode == "as06":
            keep_from = int(T1) - keep_h * H1
        else:
            keep_from = int(min(floor_C, floor_R))
        hist, _ = cm.load_hist(syms, T1, read_days=read_days, keep_from_ms=keep_from)
        n4 = {s: len(hist.get(s, {}).get("4h") or []) for s in syms}
        n1 = {s: len(hist.get(s, {}).get("1h") or []) for s in syms}
        print(f"[load:{mode}] keep_from={cm.utc(keep_from)} 4h根数中位="
              f"{sorted(n4.values())[len(n4)//2]} 1h根数中位={sorted(n1.values())[len(n1)//2]}")

        def sess(init_ms, end_ms):
            return cm.prod_session(syms, hist, init_ms, end_ms, replay_hours=depth_h,
                                   advance="1h", anchor_ms=anchor_ms)

        print(f"[run:{mode}] C 持续运行 …")
        engC = sess(T0, T1)
        print(f"[run:{mode}] R 真两段（第一段丢弃）…")
        sess(T0, Tr)          # 第一段：跑完就丢 —— 真重启，不是「只 bootstrap@Tr」
        engR = sess(Tr, T1)

        detail = {}
        for s in syms:
            sc, sr = cm.snapshot(engC.engines[s]), cm.snapshot(engR.engines[s])
            mc, mr = trans_index(engC, s), trans_index(engR, s)
            lo = max(floor_C, floor_R)          # 两边都完整覆盖的起点
            first, prev = first_divergence(mc, mr, lo)
            cc, cr = cand_list(engC, s), cand_list(engR, s)
            idc = [c["candidate_id"] for c in cc]
            idr = [c["candidate_id"] for c in cr]
            only_c = [x for x in idc if x not in idr]
            only_r = [x for x in idr if x not in idc]
            struct_same = cm.diff_key(sc) == cm.diff_key(sr)
            ch = [f for f in ("state", "event_id", "box_formed_ms", "box_high", "box_low",
                              "sweep_time_ms", "retest_time_ms", "cooling_until_ms")
                  if sc.get(f) != sr.get(f)]

            # 冷却是否「行为惰性」：有活跃事件且 cooling < box_formed ⇒ 此刻不参与闸门
            def inert(sn):
                bf, cu = sn.get("box_formed_ms"), sn.get("cooling_until_ms")
                if sn.get("state") == "IDLE":
                    return False          # 无活跃事件 ⇒ cooling 正在生效，不惰性
                return bf is not None and cu is not None and int(cu) <= int(bf)
            inert_c, inert_r = inert(sc), inert(sr)

            b4 = hist.get(s, {}).get("4h") or []
            b1 = hist.get(s, {}).get("1h") or []
            winC = [b for b in b4 if int(b["open_time"]) >= floor_C and int(b["close_time"]) <= T1]
            winR = [b for b in b4 if int(b["open_time"]) >= floor_R and int(b["close_time"]) <= T1]
            lost = [b for b in b4 if floor_C <= int(b["open_time"]) < floor_R
                    and int(b["close_time"]) <= T1]
            c1C = [b for b in b1 if int(b["open_time"]) >= floor_C and int(b["close_time"]) <= T1]
            c1R = [b for b in b1 if int(b["open_time"]) >= floor_R and int(b["close_time"]) <= T1]

            if struct_same and idc == idr:
                kind = "identical"
            elif struct_same and only_c == [] and only_r == [] and idc != idr:
                kind = "candidate_order_only"
            elif struct_same:
                kind = "candidate_log_diff"
            else:
                kind = "structure_diff"

            detail[s] = {
                "symbol": s, "keep_mode": mode, "kind": kind,
                "structure_identical": struct_same,
                "diverged_fields": ch,
                "first_div_transition_ms": first,
                "first_div_transition_utc": cm.utc(first),
                "prev_identical_transition_utc": cm.utc(prev),
                "C_transitions_at_div": mc.get(first),
                "R_transitions_at_div": mr.get(first),
                "C_prev_transition": mc.get(prev),
                "n_transitions_C": len(engC.engines[s].sm.transitions),
                "n_transitions_R": len(engR.engines[s].sm.transitions),
                "candidates_C": cc, "candidates_R": cr,
                "n_cand_C": len(cc), "n_cand_R": len(cr),
                "dup_C": dup_of(cc), "dup_R": dup_of(cr),
                "cand_only_C": only_c, "cand_only_R": only_r,
                "cooling_inert_C": inert_c, "cooling_inert_R": inert_r,
                "final_C": {k: sc.get(k) for k in ("state", "box_formed_ms",
                                                   "cooling_until_ms", "sweep_time_ms",
                                                   "retest_time_ms")},
                "final_R": {k: sr.get(k) for k in ("state", "box_formed_ms",
                                                   "cooling_until_ms", "sweep_time_ms",
                                                   "retest_time_ms")},
                "final_C_utc": {"box": cm.utc(sc.get("box_formed_ms")),
                                "cooling": cm.utc(sc.get("cooling_until_ms"))},
                "final_R_utc": {"box": cm.utc(sr.get("box_formed_ms")),
                                "cooling": cm.utc(sr.get("cooling_until_ms"))},
                "n_4h_window_C": len(winC), "n_4h_window_R": len(winR),
                "n_4h_lost_from_window": len(lost),
                "lost_4h_range_utc": ([cm.utc(int(lost[0]["close_time"])),
                                       cm.utc(int(lost[-1]["close_time"]))] if lost else None),
                "n_1h_window_C": len(c1C), "n_1h_window_R": len(c1R),
                "first_4h_C_utc": cm.utc(int(winC[0]["open_time"])) if winC else None,
                "first_4h_R_utc": cm.utc(int(winR[0]["open_time"])) if winR else None,
                "keep_from_utc": cm.utc(keep_from),
            }
        del engC, engR
        results[mode] = detail

        n_kind = {}
        for r in detail.values():
            n_kind[r["kind"]] = n_kind.get(r["kind"], 0) + 1
        print(f"[{mode}] 分类 {n_kind}")
        for s, r in detail.items():
            if r["kind"] != "identical":
                print(f"    {s:14s} {r['kind']:18s} 分歧@ {r['first_div_transition_utc']} "
                      f"前一条相同={r['prev_identical_transition_utc']} "
                      f"字段={r['diverged_fields']} 候选 {r['n_cand_C']}→{r['n_cand_R']} "
                      f"重复C/R={len(r['dup_C'])}/{len(r['dup_R'])} 冷却惰性={r['cooling_inert_C']}/{r['cooling_inert_R']}")

    tag = f"D{depth_h}_R{k_h}h" + (f"_anchor{anchor_h}" if anchor_h else "") + tag_extra
    payload = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(T1), "T0_utc": cm.utc(T0), "Tr_utc": cm.utc(Tr),
        "depth_hours": depth_h, "restart_hours": k_h, "anchor_utc": cm.utc(anchor_ms),
        "floor_C_utc": cm.utc(floor_C), "floor_R_utc": cm.utc(floor_R),
        "symbols": syms, "is_full_pool": not bool(syms_arg),
        "drive_sequence_equivalent": drive_equiv, "drive_detail": drive_detail,
        "keep_modes": modes,
        "results": results,
        "locator": ("最早分歧用状态机转移日志（sm.transitions）定位，"
                    "不用 4h 收盘采样 —— 后者在 [Tr,T1] 内只剩 1 个可比点，"
                    "会把「最后共同点」误报成「最早分歧」。"),
    }
    cm.dump(f"15_双会话对账_{tag}.json", payload)

    rows = []
    for mode, detail in results.items():
        for s, r in detail.items():
            rows.append({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict))
                             else v) for k, v in r.items()})
    cm.dump_csv(f"15_双会话对账_{tag}.csv", rows)

    # 收口判据：只看「结构差异」与「候选集合差异」，顺序差异单独列
    bad = [(m, s, r["kind"]) for m, d in results.items() for s, r in d.items()
           if r["kind"] in ("structure_diff", "candidate_log_diff")]
    print(f"[verdict] {'PASS' if not bad else 'ATTENTION'}  "
          f"需解释条目 {len(bad)}")
    for m, s, k in bad:
        print(f"    {m:11s} {s:14s} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
