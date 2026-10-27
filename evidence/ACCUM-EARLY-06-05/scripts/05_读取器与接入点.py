# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜05 既有读取器与 `replay_one(...funding_events=...)` 接入点。

要回答的只有两句：
1. **有没有现成的冷档资金费读取器？** —— 有，`MarketDataRepository.funding(prefer="cold")`
   （读 `parquet/funding/binance/um/<标的>.parquet`）。不需要新建采集器。
2. **接在哪？** —— `replay_one(funding_events=...)` 是纯参数注入点，
   改调用方的取数函数即可，**不用改** `replay_calc.py` 与 `trading_core/funding.py`。

两个必须写进报告的坑（本脚本用行为验证，不靠读注释）
----------------------------------------------------
· **列名坑**：`funding_events_ms()` 只认 `rate` / `mark_price`；冷档列名是
  `funding_rate`。把 parquet 行原样喂进去 ⇒ `rate=None` ⇒ 每次结算都记 `funding_gap`
  （**不报错、不补 0，看起来像「没有资金费」**）。
· **热优先坑**：`MarketDataRepository._merge_dedup(a=热, b=冷, key=funding_time)`
  同键保留先出现的 a ⇒ 热库那条 mark 为空的记录会**挡住**冷档同刻的有效记录。
  另外 `funding(prefer="hot")` 在热库返回满 `limit` 条时**根本不查冷档**。

本脚本只读；不改任何生产文件。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"

TARGETS = [
    ("框架/backend/replay_calc.py", [
        r"^def settle_funding_events", r"^def replay_one", r"funding_events=funding_events",
        r"from 策略\.WyckoffMaster\.trading_core\.funding import"]),
    ("策略/WyckoffMaster/trading_core/funding.py", [
        r"^def funding_events_ms", r"^def settle_funding", r"ev\.get\(\"rate\"\)",
        r"FUNDING_MODEL_ASSUMPTION"]),
    ("框架/backend/paper_engine.py", [
        r"^def funding_events", r"^FUNDING_SOURCE", r"return funding_events\("]),
    ("框架/backend/market_repository.py", [
        r"^    def funding", r"^    def _cold_flat", r"^    def _merge_dedup",
        r"^    def _flat_shards", r"^    def from_archive"]),
    ("框架/backend/backtest_runner.py", [
        r"repo\.funding\(", r"^def load_symbol_data", r"FUNDING_GRID_TOL_MS"]),
    ("框架/backend/research_15m.py", [r"funding"]),
    ("框架/backend/strategies/funding_interval_meta_source.py", [
        r"^def load_interval_meta", r"funding_interval_meta\.sqlite3"]),
    ("evidence/ACCUM-EARLY-05-C/scripts/02_逐事件基线重建.py", [
        r"^def load_funding", r"funding_events=funding"]),
]


def iso(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


def scan() -> list:
    rows = []
    for rel, pats in TARGETS:
        p = ROOT / rel
        if not p.exists():
            rows.append({"file": rel, "exists": False})
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        hits = []
        for i, line in enumerate(lines, 1):
            for pat in pats:
                if re.search(pat, line):
                    hits.append({"line": i, "text": line.strip()[:160]})
                    break
        rows.append({
            "file": rel, "exists": True,
            "sha256_16": sha16(p), "bytes": p.stat().st_size,
            "mtime_utc": iso(p.stat().st_mtime * 1000),
            "lines": len(lines), "hits": hits,
        })
    return rows


def probe_column_trap() -> dict:
    """行为验证：把「冷档行」原样喂给 funding_events_ms 会怎样。"""
    sys.path.insert(0, str(ROOT))
    from 策略.WyckoffMaster.trading_core.funding import funding_events_ms, settle_funding
    from 策略.WyckoffMaster.trading_core.position import Position

    cold_shaped = {"funding_time": 1788307200000, "funding_rate": 0.0001,
                   "mark_price": 100.0}
    hot_shaped = {"time_ms": 1788307200000, "rate": 0.0001, "mark_price": 100.0}

    def run(ev):
        pos = Position(symbol="X", direction=1, signal_type=0, entry_time=0,
                       entry_time_sec=0, entry_price=0.0, qty=1.0, initial_stop=0.0,
                       current_stop=0.0, entry_fee=0.0, last_mark_price=0.0,
                       last_funding_ms=1788307200000 - 1)
        evs = funding_events_ms([ev])
        settle_funding(pos, 0.0, model="settlement_events", t_ms=1788307200000 + 1,
                       open_px=0.0, rate=evs[0].get("rate") if evs else None,
                       events=evs, funding_is_assumption=False)
        return {"normalized": evs,
                "cum_funding_fee": pos.cum_funding_fee,
                "gaps": pos.funding_gaps}

    return {"冷档形状行（funding_rate）": run(cold_shaped),
            "热库形状行（rate）": run(hot_shaped),
            "结论": ("原样喂冷档行 ⇒ rate 归一化为 None ⇒ 记 funding_gap、成本 0、**不报错**；"
                   "所以接入时必须显式把 funding_rate 映射成 rate，"
                   "否则「接上了」和「没接」在账面上完全一样")}


def probe_merge_priority() -> dict:
    """行为验证：热库 mark 为空的记录会不会挡住冷档同刻有效记录。"""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "框架" / "backend"))
    from market_repository import MarketDataRepository
    hot = [{"funding_time": 1000, "funding_rate": "0.001", "mark_price": None}]
    cold = [{"funding_time": 1000, "funding_rate": 0.001, "mark_price": 123.0}]
    merged = MarketDataRepository._merge_dedup(hot, cold, key=("funding_time",), limit=10)
    return {"输入_热": hot, "输入_冷": cold, "合并结果": merged,
            "结论": ("同键保留先出现的（热）⇒ mark 仍为 None，冷档的有效 mark 被丢弃；"
                   "补「整段缺事件」有效，补「同事件缺字段」无效")}


def main():
    t0 = time.time()
    anchor = json.loads((OUT / "00_范围锚定.json").read_text(encoding="utf-8"))
    files = scan()
    cold_files = list((ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" /
                       "um").glob("*.parquet"))

    readers = [
        {"名称": "replay_calc.settle_funding_events", "层": "参数注入（本身不取数）",
         "位置": "框架/backend/replay_calc.py:350",
         "取数": "调用方传 funding_events；本函数只做区间过滤 entry<结算≤exit 与逐笔结算",
         "含mark": "看入参", "含可得时点": "不使用（入参里的 fetched_at 不参与判定）",
         "被replay使用": True},
        {"名称": "replay_calc.replay_one", "层": "参数注入（接入点）",
         "位置": "框架/backend/replay_calc.py:523（参数）/565（调用结算）",
         "取数": "`funding_events: List[dict]` 直接透传给 settle_funding_events",
         "含mark": "看入参", "含可得时点": "否", "被replay使用": True,
         "结论": "这是**唯一需要的接入点**：换掉调用方的取数函数即可，不必改本文件"},
        {"名称": "trading_core.funding.settle_funding", "层": "结算内核",
         "位置": "策略/WyckoffMaster/trading_core/funding.py:67",
         "取数": "不取数；只消费 (time_ms, rate, mark_price)",
         "含mark": "必需", "含可得时点": "否", "被replay使用": True,
         "结论": "缺 rate/mark ⇒ funding_gap，不补 0；不认 funding_rate 这个列名"},
        {"名称": "paper_engine.funding_events", "层": "热库", "位置": "框架/backend/paper_engine.py:563",
         "取数": "SELECT funding_time,funding_rate,mark_price FROM funding_history（无 exchange 之外的层路由）",
         "含mark": "是", "含可得时点": "否（不返回 fetched_at）", "被replay使用": "否（生产记账）",
         "结论": "生产侧只读热库 ⇒ 15.8 天缺口段里持仓的资金费在模拟盘上会漏记"},
        {"名称": "MarketDataRepository.funding", "层": "热+冷（已有路由）",
         "位置": "框架/backend/market_repository.py:241",
         "取数": ("prefer='hot'：先热库 LIMIT n；仅当 prefer='cold' 或热库不足 n 条时才读冷档 "
                "parquet，按 funding_time 去重（热优先）"),
         "含mark": "是（冷档也有）", "含可得时点": "否（冷档无此列，热库返回 fetched_at 但未用于判定）",
         "被replay使用": "否（被 backtest_runner 使用）",
         "结论": "**现成的冷档读取器**；但热优先去重挡住「同事件补字段」，且 limit 满额时不查冷档"},
        {"名称": "backtest_runner.load_symbol_data", "层": "热+冷（经仓库层）",
         "位置": "框架/backend/backtest_runner.py:362",
         "取数": "repo.funding(ex, mk, sym, start=..., end=...)（未显式传 prefer ⇒ 默认 hot 优先）",
         "含mark": "是", "含可得时点": "否", "被replay使用": "间接（回测主链）",
         "结论": "回测链已有冷档能力，但优先级是热优先 ⇒ 同事件缺字段仍补不上"},
        {"名称": "research_15m（冷热合并读取器）", "层": "仅 K 线", "位置": "框架/backend/research_15m.py",
         "取数": "只合并 15m/1h/4h K 线，**没有任何 funding 代码**",
         "含mark": "—", "含可得时点": "K 线层有 available_at，资金费层没有", "被replay使用": "否",
         "结论": "「冷热合并」目前只做了行情，没做资金费 ⇒ 缺口不是读取器能力问题，是没接"},
        {"名称": "strategies/funding_interval_meta_source", "层": "周期元数据（只读伴生库）",
         "位置": "框架/backend/strategies/funding_interval_meta_source.py",
         "取数": "读 `funding_interval_meta.sqlite3`（401 MB）→ interval_hours_at",
         "含mark": "—", "含可得时点": "有（available_at_ms，因果口径 HUMAN_RATIFIED_*）",
         "被replay使用": "否",
         "结论": ("伴生库**不在本仓库**（`Path.glob` 与 tests 里的路径都不存在）⇒ "
                "原生周期元数据不可用，本单只能用实测周期推断")},
        {"名称": "05-C/scripts/02.load_funding", "层": "热库", "位置":
         "evidence/ACCUM-EARLY-05-C/scripts/02_逐事件基线重建.py:71",
         "取数": "funding_history 单表，窗口 entry→entry+14 天（区间外由结算函数再过滤）",
         "含mark": "是", "含可得时点": "取 fetched_at 但只落盘不判定", "被replay使用": "是（51 笔基线）",
         "结论": "51 笔基线的资金费就是这里算的 ⇒ 04-C/05-C 的资金费口径 = 只读热库"},
    ]

    out = {
        "task": "ACCUM-EARLY-06-05",
        "probe": "05_读取器与接入点",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "冷档资金费分片": {"files": len(cold_files),
                    "pattern": "数据/市场数据/parquet/funding/binance/um/<标的>.parquet",
                    "列": ["funding_time", "funding_rate", "mark_price", "rate_type", "source"]},
        "现成读取器": ("有：MarketDataRepository.funding(prefer='cold') → _cold_flat(metric='funding')；"
                  "不需要新建采集器"),
        "读取器清单": readers,
        "代码取证": files,
        "行为验证_列名坑": probe_column_trap(),
        "行为验证_热优先坑": probe_merge_priority(),
        "接入方案（只描述，本单不改代码）": {
            "接入点": "replay_one(funding_events=...)：调用方取数函数返回 List[dict]",
            "最小改动面": ["调用方的取数函数（05-C 的 load_funding / paper_engine 的 funding_events）"],
            "不必改": ["replay_calc.py", "trading_core/funding.py"],
            "必须做的映射": "冷档列 funding_rate → rate；mark_price 原样；时间 funding_time → time_ms",
            "必须处理的三件事": [
                "热优先去重会挡住「同事件缺 mark」⇒ 需按「有值优先」而非「先出现优先」",
                "冷档无 fetched_at/available_at ⇒ 合并后必须把可得性标为未验证，不能伪造",
                "冷档覆盖到某时刻为止（本单实测其窗口内最后一条）⇒ 之后的结算只能靠热库，"
                "不能声明「合并后全窗口完整」"],
            "验证方式建议": ("合并前后对同一笔交易算两次成本，差 = 补回的结算；"
                       "并用「把冷档行原样喂进去」的负例证明映射缺失会静默记 gap"),
        },
        "elapsed_sec": round(time.time() - t0, 2),
    }
    (OUT / "05_读取器与接入点.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"列名坑": out["行为验证_列名坑"]["结论"],
                      "热优先坑": out["行为验证_热优先坑"]["合并结果"],
                      "读取器数": len(readers),
                      "冷档分片": len(cold_files)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
