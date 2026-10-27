#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LIVE-CHECK-09 探针⑤：把跨小时线采样切成「逐根 1h」的逐币报告（要求 2）

输入：`out/10_prod_samples.ndjson`（`10_sample_prod.py` 产出，每份带采集时刻与水位）
输出：`out/50_sample_analysis.json` + 控制台逐根摘要

为什么不能只报「一共 0 个候选」
----------------------------
「0」必须能拆到每一根新增小时线上，说清这一根发生了什么：
  · 水位从哪推到哪（是不是真的处理了这根）
  · 哪些标的在这根上换了阶段（换了才是「有动作」）
  · 有没有新候选（产生 / 入库 / 接口返回，三段分别核）
  · 没产生候选的，卡在哪一关（漏斗各关计数变化）
  · IDLE 的原因分布怎么变（冷却 / 等箱体 / 箱体已就绪等收盘），带实际值与阈值样例

分段规则：按 `watermark_1h` 变化切段。每段取「该水位的第一份样本」与
「上一水位的最后一份样本」做差。水位没变的时间段不切段，只在段内累计采样数。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
OUT = HERE.parents[1] / "out"
SRC = OUT / "10_prod_samples.ndjson"


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def diff_counter(a, b):
    """a、b 都是 dict[str,int]，返回 b-a 的非零项。"""
    a = a or {}
    b = b or {}
    out = {}
    for k in sorted(set(a) | set(b)):
        d = int(b.get(k) or 0) - int(a.get(k) or 0)
        if d:
            out[k] = d
    return out


def main():
    ap = argparse.ArgumentParser(
        description="把跨小时线采样切成「逐根 1h」的逐币报告")
    ap.add_argument("--in", dest="src", default=str(SRC),
                    help="采样 NDJSON（默认 out/10_prod_samples.ndjson）")
    ap.add_argument("--out", default=str(OUT / "50_sample_analysis.json"),
                    help="输出路径（默认 out/50_sample_analysis.json）")
    args = ap.parse_args()
    src = Path(args.src)

    if not src.exists():
        print(f"[FAIL] 找不到采样文件：{src}")
        return 2
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("[FAIL] 采样文件为空")
        return 2

    # ── 按水位切段 ───────────────────────────────────────────────────────────
    segs = []          # 每段：{"wm": int, "first": row, "last": row, "n": int}
    for r in rows:
        wm = r["watermark"]["watermark_1h"]
        if not segs or segs[-1]["wm"] != wm:
            segs.append({"wm": wm, "first": r, "last": r, "n": 1})
        else:
            segs[-1]["last"] = r
            segs[-1]["n"] += 1

    report = {
        "probe": "SIGNAL-LIVE-CHECK-09/50_sample_analysis",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "source": str(SRC.name),
        "n_samples": len(rows),
        "sampled_from_utc": rows[0]["sampled_at_utc"],
        "sampled_to_utc": rows[-1]["sampled_at_utc"],
        "runtime_pids": sorted({r["runtime"]["pid"] for r in rows}),
        "segments": [],
        "totals": {},
    }

    print(f"样本 {len(rows)} 份｜{rows[0]['sampled_at_utc']} → {rows[-1]['sampled_at_utc']}")
    print(f"runtime pid 集合：{report['runtime_pids']}")
    print(f"水位段数：{len(segs)}（新增小时线 {max(0, len(segs) - 1)} 根）\n")

    # ── 逐段（第一段只作基线，不比较）────────────────────────────────────────
    for i, seg in enumerate(segs):
        cur = seg["last"]
        item = {
            "seq": i,
            "watermark_1h": seg["wm"],
            "watermark_1h_utc": iso(seg["wm"]),
            "watermark_4h_utc": cur["watermark"]["watermark_4h_utc"],
            "latest_1h_close_utc": cur["market_data"]["latest_1h_close_utc"],
            "n_samples_in_seg": seg["n"],
            "first_sampled_utc": seg["first"]["sampled_at_utc"],
            "last_sampled_utc": cur["sampled_at_utc"],
            "watermark_check": cur["watermark"]["check"],
            "state_counter": cur["state_counter"],
            "idle_counts": (cur.get("idle_breakdown") or {}).get("counts"),
            "idle_sample": (cur.get("idle_breakdown") or {}).get("sample"),
            "funnel_since_start": cur["funnel_since_start"],
            "signals_generated_total": cur["signals_generated_total"],
            "api": cur["api"],
            "live_db_n": cur["live_db"]["n"],
            "live_db_ids": [r["signal_uid"] for r in (cur["live_db"]["rows"] or [])],
        }
        if i > 0:
            prev = segs[i - 1]["last"]
            item["delta_vs_prev"] = {
                "watermark_advance_bars":
                    (seg["wm"] - segs[i - 1]["wm"]) // 3_600_000,
                "state_counter": diff_counter(prev["state_counter"], cur["state_counter"]),
                "idle_counts": diff_counter((prev.get("idle_breakdown") or {}).get("counts"),
                                            (cur.get("idle_breakdown") or {}).get("counts")),
                "funnel_since_start": diff_counter(prev["funnel_since_start"],
                                                   cur["funnel_since_start"]),
                "signals_generated_total":
                    (cur["signals_generated_total"] or 0)
                    - (prev["signals_generated_total"] or 0),
                "live_db_n": (cur["live_db"]["n"] or 0) - (prev["live_db"]["n"] or 0),
                "api_current_total": (cur["api"]["current_total"] or 0)
                                     - (prev["api"]["current_total"] or 0),
            }
            d = item["delta_vs_prev"]          # 下面异常判定要用，必须先绑定
            # 逐币阶段变化：这一根上谁动了
            ps_prev = prev.get("per_symbol_state") or {}
            ps_cur = cur.get("per_symbol_state") or {}
            changes = []
            for sym in sorted(set(ps_prev) | set(ps_cur)):
                a, b = ps_prev.get(sym), ps_cur.get(sym)
                if a != b:
                    changes.append({"symbol": sym, "from": a, "to": b})
            item["per_symbol_changes"] = changes
            item["per_symbol_changed_n"] = len(changes)

            # ── 三条链路异常检测（要求 4 的判据，不是靠肉眼看 0）───────────────
            #   应有未有   ：漏斗 CANDIDATE 增了，但入库行数没增
            #   已生成未入库：signals_generated_total 增了，但入库行数没增
            #   已入库未返回：入库行数增了，但接口（current+all）没跟着增
            # 注意：`funnel.since_start` 是本进程口径，重启后归零；本检测只在
            # 同一个 pid 的相邻两段之间比较，跨 pid 的段不做该判定（会假报）。
            anomalies = []
            same_pid = prev["runtime"]["pid"] == cur["runtime"]["pid"]
            if same_pid:
                d_cand = (d["funnel_since_start"].get("CANDIDATE") or 0)
                d_gen = d["signals_generated_total"]
                d_db = d["live_db_n"]
                if d_cand > 0 and d_db <= 0:
                    anomalies.append({
                        "kind": "应有未有",
                        "detail": f"漏斗 CANDIDATE +{d_cand}，但入库行数 {d_db:+d}",
                    })
                if d_gen > 0 and d_db <= 0:
                    anomalies.append({
                        "kind": "已生成未入库",
                        "detail": f"signals_generated_total +{d_gen}，但入库行数 {d_db:+d}",
                    })
                if d_db > 0:
                    prev_all = prev["api"].get("all_total") or 0
                    cur_all = cur["api"].get("all_total") or 0
                    if (cur["api"]["current_total"] or 0) + cur_all <= \
                            (prev["api"]["current_total"] or 0) + prev_all:
                        anomalies.append({
                            "kind": "已入库未返回",
                            "detail": f"入库 +{d_db}，但接口 current/all 未增"
                                      f"（current {prev['api']['current_total']}"
                                      f"→{cur['api']['current_total']}，"
                                      f"all {prev_all}→{cur_all}）",
                        })
            item["anomalies"] = anomalies

            # ── 迁移 ↔ 漏斗 对撞（不靠人眼看数字是否"对得上"）────────────────
            #   漏斗记的是「进入某状态的次数」，逐币迁移记的是「谁从哪到哪」。
            #   两者必须一一对上；对不上就是计数器与状态机脱节（诊断会说错话）。
            #
            # ⚠ 只在「这一段只推进了 1 根 1h」时才能判。
            #   逐币阶段是**首尾两次快照的差**：一段里推进 2 根时，中间那根的迁移
            #   会「去了又回来」被首尾差吃掉（例如 BOX→SWEEP→BOX），而漏斗增量
            #   两笔都记着 ⇒ 对撞必然不等，但那不是缺陷，是我的判据不成立。
            #   这种情况必须报「不可判」，不能报「不一致」——否则会制造假缺陷。
            adv = d["watermark_advance_bars"]
            if adv != 1:
                item["migration_types"] = None
                item["migration_vs_funnel"] = None
                item["migration_vs_funnel_all_equal"] = None
                item["migration_vs_funnel_note"] = (
                    f"本段推进 {adv} 根 1h，逐币首尾差无法还原中间迁移 ⇒ 不做对撞"
                    if adv > 1 else "本段未推进（同一水位），无从比较")
            else:
                mig = {}
                for c in changes:
                    mig[f"{c['from']}->{c['to']}"] = mig.get(f"{c['from']}->{c['to']}", 0) + 1
                fd = item["delta_vs_prev"]["funnel_since_start"] or {}
                enter = {}
                for k, v in mig.items():
                    to = k.split("->")[1]
                    enter[to] = enter.get(to, 0) + v
                rec = []
                for st in ("BOX", "SWEEP", "RETEST"):
                    a, b = enter.get(st, 0), int(fd.get(st, 0) or 0)
                    rec.append({"状态": st, "逐币迁移进入": a,
                                "漏斗计数增量": b, "一致": a == b})
                end_total = enter.get("IDLE", 0)
                end_funnel = int(fd.get("EXPIRED", 0) or 0) + int(fd.get("INVALIDATED", 0) or 0)
                rec.append({"状态": "IDLE(事件终止)",
                            "逐币迁移进入": end_total, "漏斗计数增量": end_funnel,
                            "一致": end_total == end_funnel,
                            "说明": "漏斗把终止拆成 EXPIRED / INVALIDATED 两项，"
                                    "逐币只能看到「回了 IDLE」；逐币具体是哪一项要看回放导出的"
                                    " last_terminated_event.termination_reason"})
                item["migration_types"] = mig
                item["migration_vs_funnel"] = rec
                item["migration_vs_funnel_all_equal"] = all(r["一致"] for r in rec)

            print(f"── 第 {i} 段：水位 {iso(seg['wm'])}（推进 {d['watermark_advance_bars']} 根）")
            print(f"   最新已收盘 1h={item['latest_1h_close_utc']} "
                  f"4h 水位={item['watermark_4h_utc']}")
            print(f"   阶段变化 {len(changes)} 币｜分布变化 {d['state_counter'] or '无'}")
            print(f"   漏斗增量 {d['funnel_since_start'] or '无'}")
            if item.get("migration_vs_funnel"):
                bad = [r for r in item["migration_vs_funnel"] if not r["一致"]]
                print(f"   迁移↔漏斗 对撞：{'全部一致' if not bad else '不一致 ' + str(bad)}")
            elif item.get("migration_vs_funnel_note"):
                print(f"   迁移↔漏斗 对撞：不可判（{item['migration_vs_funnel_note']}）")
            print(f"   新候选 {d['signals_generated_total']}｜入库 {d['live_db_n']:+d}｜"
                  f"接口 {d['api_current_total']:+d}")
            if changes:
                for c in changes[:12]:
                    print(f"     {c['symbol']}: {c['from']} → {c['to']}")
                if len(changes) > 12:
                    print(f"     …另有 {len(changes) - 12} 个")
            if anomalies:
                for a in anomalies:
                    print(f"   [ANOMALY] {a['kind']}：{a['detail']}")
            print()
        report["segments"].append(item)

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    first, last = rows[0], rows[-1]
    report["totals"] = {
        "watermark_start_utc": first["watermark"]["watermark_1h_utc"],
        "watermark_end_utc": last["watermark"]["watermark_1h_utc"],
        "bars_advanced": (last["watermark"]["watermark_1h"]
                          - first["watermark"]["watermark_1h"]) // 3_600_000,
        "candidates_since_start": (last["funnel_since_start"] or {}).get("CANDIDATE"),
        "signals_generated_total": last["signals_generated_total"],
        "live_db_rows": last["live_db"]["n"],
        "api_current_total": last["api"]["current_total"],
        "state_counter_start": first["state_counter"],
        "state_counter_end": last["state_counter"],
        "state_counter_delta": diff_counter(first["state_counter"], last["state_counter"]),
        "funnel_delta": diff_counter(first["funnel_since_start"], last["funnel_since_start"]),
        "per_symbol_changed_total": sum(
            s.get("per_symbol_changed_n", 0) for s in report["segments"]),
        "data_quality_blocked_n": (last.get("data_quality") or {}).get("blocked_n"),
        "scan_errors_total": last.get("scan_errors_total"),
        "three_stage_consistent": (
            (last["funnel_since_start"] or {}).get("CANDIDATE") == 0
            and last["live_db"]["n"] == 0
            and last["api"]["current_total"] == 0),
        "anomalies_total": sum(len(s.get("anomalies") or [])
                               for s in report["segments"]),
        "anomalies": [{"seq": s["seq"], **a}
                      for s in report["segments"] for a in (s.get("anomalies") or [])],
    }
    t = report["totals"]
    print("══ 汇总 ══")
    print(f"水位 {t['watermark_start_utc']} → {t['watermark_end_utc']}"
          f"（推进 {t['bars_advanced']} 根）")
    print(f"本进程候选 {t['candidates_since_start']}｜signals_generated_total "
          f"{t['signals_generated_total']}｜库 {t['live_db_rows']} 行｜"
          f"接口 {t['api_current_total']} 条")
    print(f"三段一致（产生=0 且 入库=0 且 接口=0）：{t['three_stage_consistent']}")
    print(f"阶段分布 {t['state_counter_start']} → {t['state_counter_end']}"
          f"（变化 {t['state_counter_delta'] or '无'}）")
    print(f"漏斗增量 {t['funnel_delta'] or '无'}")
    print(f"逐币阶段变化合计 {t['per_symbol_changed_total']} 次｜"
          f"数据质量阻断 {t['data_quality_blocked_n']} 币｜"
          f"扫描错误 {t['scan_errors_total']}")
    print(f"链路异常 {t['anomalies_total']} 处"
          + (f"：{t['anomalies']}" if t["anomalies_total"] else "（无「应有未有 / 已生成未入库 / 已入库未返回」）"))

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写出：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
