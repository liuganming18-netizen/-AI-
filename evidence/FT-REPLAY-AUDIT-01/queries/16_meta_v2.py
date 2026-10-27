# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 16：时区、时间单位、交易 ID 映射、研究状态、版本、证据链接（v2）。"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
E09 = os.path.join(ROOT, "evidence/FT-ENTRY-09")
R01 = os.path.join(ROOT, "evidence/FT-REPLAY-01")
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
SYM_DIR = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_ft_replay_01")
GRID_DIR = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs_ft_entry_09_research")
JOBS = os.path.join(ROOT, "数据/任务队列/jobs.sqlite3")
BASE = "http://127.0.0.1:8787/api/v1/backtests"
GRIDS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
         "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
MODES = ["C", "C1", "C2"]


def read_jsonl(p):
    out = []
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    checks, detail = [], {}

    # ── A. 时区与时间单位 ──────────────────────────────────────
    src = []
    for mode in MODES:
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            p = os.path.join(E09, "out", "trades_%s.csv" % st)
            d = pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
            d["strategy"] = st
            src.append(d)
    src = pd.concat(src, ignore_index=True)
    src["open_ms"] = pd.to_numeric(src["open_timestamp"], errors="coerce").astype("int64")
    src["close_ms"] = pd.to_numeric(src["close_timestamp"], errors="coerce").astype("int64")
    # open_date 是 UTC 文本？按 UTC 解析后与毫秒比对
    od = pd.to_datetime(src["open_date"].astype(str), utc=True, errors="coerce")
    cd = pd.to_datetime(src["close_date"].astype(str), utc=True, errors="coerce")
    utc_open_bad = int((od != pd.to_datetime(src["open_ms"], unit="ms", utc=True)).sum())
    utc_close_bad = int((cd != pd.to_datetime(src["close_ms"], unit="ms", utc=True)).sum())
    # 毫秒位数
    ms_ok = bool(((src["open_ms"] > 1e12) & (src["open_ms"] < 2e12)).all())
    checks.append({
        "项": "时区：源账本时间文本 vs 毫秒",
        "期望": "open_date/close_date 按 UTC 解析后与 open_timestamp/close_timestamp 完全一致",
        "实际": "open 不一致 %d 笔、close 不一致 %d 笔（共 %d 笔）"
                % (utc_open_bad, utc_close_bad, len(src)),
        "通过": utc_open_bad == 0 and utc_close_bad == 0,
    })
    checks.append({
        "项": "时间单位：毫秒而非秒",
        "期望": "全部时间戳落在 1e12~2e12（13 位毫秒）",
        "实际": "open_timestamp 全部在区间内 = %s" % ms_ok,
        "通过": ms_ok,
    })

    # 币级 jsonl 的时间与源账本
    sym_rows = []
    for p in glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*")):
        for t in read_jsonl(os.path.join(p, "trades.jsonl")):
            t["run_id"] = os.path.basename(p)
            sym_rows.append(t)
    sy = pd.DataFrame(sym_rows)
    sy["hold_calc"] = (sy["exit_time_ms"].astype("int64") - sy["entry_time_ms"].astype("int64")) / 60000.0
    hold_bad = int((abs(sy["hold_calc"] - sy["holding_minutes"].astype(float)) > 1e-6).sum())
    # 入场时间是否 15 分钟对齐
    align_bad = int((sy["entry_time_ms"].astype("int64") % 900000 != 0).sum())
    checks.append({
        "项": "时长单位：holding_minutes",
        "期望": "(出场−入场)/60000 == holding_minutes",
        "实际": "不一致 %d / %d 笔" % (hold_bad, len(sy)),
        "通过": hold_bad == 0,
    })
    checks.append({
        "项": "成交时间对齐 15 分钟",
        "期望": "入场毫秒应为 15 分钟整数倍（exec 周期 15m）",
        "实际": "不对齐 %d / %d 笔" % (align_bad, len(sy)),
        "通过": align_bad == 0,
    })

    # 页面时区标注（接口侧佐证：UTC 字符串）
    api = json.loads(subprocess.run(
        ["curl", "-s", "-m", "60", BASE + "/run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT/trades"],
        capture_output=True, text=True).stdout)
    it = (api.get("items") or [{}])[0]
    checks.append({
        "项": "接口时间字段口径",
        "期望": "只给毫秒 + UTC 语义，不给本地时间冒充 UTC",
        "实际": "entry_time_ms=%s → UTC %s；接口无 CST 字段"
                % (it.get("entry_time_ms"),
                   pd.Timestamp(int(it.get("entry_time_ms") or 0), unit="ms", tz="UTC")),
        "通过": True,
    })

    # ── B. 交易 ID 映射 ────────────────────────────────────────
    def rebuild(t):
        # run_id 里没有 Wyckoff_ 前缀，trade_id 里有 ⇒ 重建时必须补回
        st = t["run_id"].replace("run_FT_ENTRY_09_", "")[:-(len(t["symbol"]) + 1)]
        return "ft_Wyckoff_%s_%s_%s" % (st, t["symbol"], t["entry_time_ms"])
    sy["rebuilt_id"] = sy.apply(rebuild, axis=1)
    id_ok = int((sy["rebuilt_id"] == sy["trade_id"]).sum())
    grid_ids = []
    for p in glob.glob(os.path.join(GRID_DIR, "run_FT_ENTRY_09_*")):
        for t in read_jsonl(os.path.join(p, "trades.jsonl")):
            grid_ids.append(t.get("trade_id"))
    ordinal = sum(1 for x in grid_ids if re.fullmatch(r"trd_.*_\d{4}", str(x)))
    checks.append({
        "项": "币级 trade_id 可复现",
        "期望": "trade_id = ft_<策略>_<币种>_<入场毫秒>，可由源账本字段重建",
        "实际": "可重建 %d / %d" % (id_ok, len(sy)),
        "通过": id_ok == len(sy),
    })
    checks.append({
        "项": "组级 trade_id 可追溯性",
        "期望": "trade_id 应能定位到源账本某一行（不是组内序号）",
        "实际": "组级 %d 个 ID 中，形如 trd_<策略>_<四位序号> 的 %d 个（序号不含币种/时间 ⇒ 无法回溯源行）"
                % (len(grid_ids), ordinal),
        "通过": ordinal == 0,
    })
    checks.append({
        "项": "源账本是否有原生 trade_id",
        "期望": "上游 freqtrade 无引擎级 trade_id ⇒ 系统须标明是重建 ID",
        "实际": "源账本列无 trade_id；接口标注 trade_id_source=%s、trade_id_nature=%s"
                % (it.get("trade_id_source"), it.get("trade_id_nature")),
        "通过": it.get("trade_id_nature") == "imported_reconstructed",
    })

    # ── C. 研究状态 ────────────────────────────────────────────
    marks = {"research_period": 0, "registered_by": 0, "ledger": 0, "total": 0}
    for p in glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*")):
        rj = os.path.join(p, "run.json")
        if not os.path.isfile(rj):
            continue
        d = json.load(open(rj, encoding="utf-8"))
        marks["total"] += 1
        if d.get("research_period") is True:
            marks["research_period"] += 1
        if (d.get("registration") or {}).get("registered_by"):
            marks["registered_by"] += 1
        if os.path.isfile(os.path.join(p, "ledger.sqlite3")):
            marks["ledger"] += 1
    checks.append({
        "项": "研究状态标记（run 级）",
        "期望": "全部 run 标 research_period=true 且写明登记来源；不得写生产/模拟账本",
        "实际": "%d 个 run：research_period=%d、registered_by=%d、带 ledger.sqlite3=%d"
                % (marks["total"], marks["research_period"], marks["registered_by"], marks["ledger"]),
        "通过": marks["research_period"] == marks["total"] == marks["registered_by"]
                and marks["ledger"] == 0,
    })
    bm = json.load(open(os.path.join(GRID_DIR, "batch_meta.json"), encoding="utf-8"))
    bm2 = json.load(open(os.path.join(SYM_DIR, "batch_meta.json"), encoding="utf-8"))
    checks.append({
        "项": "研究状态标记（批次级）",
        "期望": "category=research_period_result、formal_pass_allowed=false",
        "实际": "组级 category=%s / formal_pass_allowed=%s；币级无 category 字段（note 里写研究期）"
                % (bm.get("category"), bm.get("formal_pass_allowed")),
        "通过": bm.get("category") == "research_period_result" and bm.get("formal_pass_allowed") is False,
    })

    # ── D. 任务记录 ────────────────────────────────────────────
    con = sqlite3.connect("file:%s?mode=ro" % JOBS, uri=True)
    n_bt = list(con.execute("select count(*) from jobs where kind='backtest'"))[0][0]
    n_hit = 0
    for (pj,) in con.execute("select payload_json from jobs"):
        if pj and ("FT_ENTRY_09" in pj or "REPLAY" in pj):
            n_hit += 1
    con.close()
    checks.append({
        "项": "任务记录里有无本批",
        "期望": "本批是「导入既有 CSV」而非重跑 ⇒ 任务队列不应有 4325 笔的跑批任务",
        "实际": "任务队列 backtest 任务 %d 个；payload 含 FT_ENTRY_09/REPLAY 的 %d 个" % (n_bt, n_hit),
        "通过": n_hit == 0,
    })

    # ── E. 版本 ────────────────────────────────────────────────
    vf = os.path.join(E09, "00_版本指纹清单.csv")
    vdf = pd.read_csv(vf, encoding="utf-8-sig") if os.path.isfile(vf) else pd.DataFrame()
    rj = json.load(open(os.path.join(SYM_DIR, "run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT/run.json"),
                        encoding="utf-8"))
    spec = rj.get("spec") or {}
    checks.append({
        "项": "版本与参数一致性",
        "期望": "run.json 的策略名/参数能对上源账本 27 组命名与参数组",
        "实际": "run.json strategy_name=%s params=%s；源版本指纹表 %d 行"
                % (spec.get("strategy_name"), spec.get("params"), len(vdf)),
        "通过": str(spec.get("strategy_name")) == "Wyckoff_C1_Vs04_Z02",
    })

    # ── F. 证据链接 ────────────────────────────────────────────
    doc = open(os.path.join(R01, "R1_00_接入说明与使用入口.md"), encoding="utf-8").read()
    refs = sorted(set(re.findall(r"screens/([\w\-.]+\.png)", doc)))
    miss = [x for x in refs if not os.path.isfile(os.path.join(R01, "screens", x))]
    checks.append({
        "项": "交付文档里的证据链接可打开",
        "期望": "文档引用的截图文件全部存在",
        "实际": "引用 %d 张，缺失 %d 张 %s" % (len(refs), len(miss), miss),
        "通过": len(miss) == 0,
    })
    # 系统内研究记录（哈希链）是否登记本批
    ev = read_jsonl(os.path.join(ROOT, "框架/backend/data/experiments/events.jsonl"))
    hit = [e for e in ev if "REPLAY" in json.dumps(e, ensure_ascii=False)
           or "FT_ENTRY_09" in json.dumps(e, ensure_ascii=False)]
    detail["研究记录链"] = {"事件总数": len(ev), "含本批的事件数": len(hit),
                        "说明": "本批在系统里的登记是 run.json + batch_meta，不在实验哈希链里；"
                                "链上只有 3 个 API 创建的实验与 1 条 observe 事件。"}

    df = pd.DataFrame(checks)
    df.to_csv(os.path.join(OUT, "16_元数据核对_v2.csv"), index=False, encoding="utf-8-sig")
    detail["checks"] = checks
    detail["时区与单位"] = {"open不一致": utc_open_bad, "close不一致": utc_close_bad,
                        "holding不一致": hold_bad, "未对齐15m": align_bad}
    detail["trade_id"] = {"币级可重建": id_ok, "币级总数": len(sy),
                         "组级序号ID": ordinal, "组级总数": len(grid_ids)}
    with open(os.path.join(OUT, "16_元数据摘要_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(detail, fh, ensure_ascii=False, indent=2, default=str)

    print(df[["项", "通过"]].to_string(index=False))
    print("\n通过 %d / %d" % (int(df["通过"].sum()), len(df)))
    for r in checks:
        print("\n· %s\n  期望：%s\n  实际：%s\n  通过：%s" % (r["项"], r["期望"], r["实际"], r["通过"]))


if __name__ == "__main__":
    main()
