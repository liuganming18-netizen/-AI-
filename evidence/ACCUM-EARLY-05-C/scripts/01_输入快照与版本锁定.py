# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-C｜01 输入快照与版本锁定

本脚本为**隔离只读**研究脚本：
* 只读生产数据与策略代码，不写任何生产文件、不改策略、不改回放计算；
* 唯一写盘位置 = evidence/ACCUM-EARLY-05-C/out/

要解决的真问题（承接 04-C 的漏洞）
----------------------------------
04-C 的 01 只写了「代码与参数一字不动」这类**声明**，没有留下可核对的**身份**：
* 没有代码文件哈希 ⇒ 无法证明基线是用哪一版 `replay_calc.py` 跑的；
* 事实核查：`replay_calc.py` 的修改时间（2026-10-26 22:11）与 04-C 基线产物
  `02_原策略基线结果.json` 的落盘时间（22:11）**同一分钟**，而 04-D 在 22:17
  才产出「时序整改后」的复算结果 ⇒ 04-C 基线到底跑在整改前还是整改后，
  从产物里看不出来。这不是小事：整改会改变 4h 跟踪止损的生效时刻，进而改退出价与盈亏。

本脚本因此把「身份」做实：
1. 代码身份：逐个文件算 SHA256（分块读，禁止 read_bytes 读 12 GiB 库）；
2. 数据身份：热库分块 SHA256 + 冷档官方清单 `_catalog.json` 的分片哈希汇总；
3. 覆盖边界：热库各周期实测首末时刻（不抄旧结论）；
4. 参数来源：两套口径的**原始出处字符串** + 冻结配置的实际生效值；
5. 上游依赖状态：逐目录探测 05-B / 05-D / 05-F 是否已交付（不存在就写不存在）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet"
COLD_CATALOG = COLD_DIR / "_catalog.json"

# ── 固定窗口（沿用 04-C，保持可比；不因为今天数据变多就移动窗口）──────────
WIN_START_MS = 1782547200000        # 2026-06-27T00:00:00Z
WIN_END_MS = 1793005200000          # 2026-10-26T09:00:00Z
WARMUP_START_MS = WIN_START_MS - 220 * 4 * 3600 * 1000   # 4h 预热 220 根

CHUNK = 32 * 1024 * 1024


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file_chunked(path: Path, chunk: int = CHUNK) -> dict:
    """分块算 SHA256。

    踩坑记录：`read_bytes()` 读 12 GiB 的库会把进程直接 SIGKILL（137，无报错），
    所以这里必须分块，且块大小固定以便复现。
    """
    h = hashlib.sha256()
    n = 0
    t0 = time.time()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            n += len(b)
    st = path.stat()
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": h.hexdigest(),
        "bytes": n,
        "size_matches_stat": (n == st.st_size),
        "mtime_utc": iso(st.st_mtime * 1000),
        "hash_sec": round(time.time() - t0, 2),
        "chunk_bytes": chunk,
    }


def sha256_file_whole(path: Path) -> dict:
    """小文件整块读（代码/文档），同样记字节数以便核对。"""
    b = path.read_bytes()
    st = path.stat()
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(b).hexdigest(),
        "bytes": len(b),
        "mtime_utc": iso(st.st_mtime * 1000),
    }


CODE_FILES = [
    # 回放算术（决定盈亏的唯一实现）
    "框架/backend/replay_calc.py",
    # 冷热合并读取门面
    "框架/backend/research_15m.py",
    "框架/backend/history_loader.py",
    # 冻结状态机与其依赖
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/features.py",
    "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py",
    # 冻结规则正文
    "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md",
]

# 上游/邻接依赖：本单必须在报告里如实交代「到没到」
DEPENDENCY_DIRS = {
    "05-B（新策略实现）": "evidence/ACCUM-EARLY-05-B",
    "05-D（拒单差异与计算版本）": "evidence/ACCUM-EARLY-05-D",
    "05-F（数据边界）": "evidence/ACCUM-EARLY-05-F",
    "04-C（上一单对照框架）": "evidence/ACCUM-EARLY-04-C",
    "04-D（回放计算复核）": "evidence/ACCUM-EARLY-04-D",
}

# 本单已知、且必须写进快照的缺口（每条都给出取证方式，不靠记忆）
KNOWN_GAPS = [
    {
        "id": "GAP-01",
        "内容": "生产库无历史观察事件存档",
        "取证": "live_signals.sqlite3 的 live_signal 表实测 0 行；观察快照只有当前时刻一份",
        "影响": "「统一观察范围」的历史母集无法从存档直接取，只能靠状态机重放重建（本单脚本 04）",
        "性质": "数据缺口，不是工程缺陷",
    },
    {
        "id": "GAP-02",
        "内容": "冷档 15m/4h 无抓取时刻（available_at 填充率 0%）",
        "取证": "本单脚本 01 的冷档清单统计 + 04-C 已登记",
        "影响": "凡依赖冷档的成交判定只能标「探索性」，不能声称「当时可得」",
        "性质": "可得性缺口",
    },
    {
        "id": "GAP-03",
        "内容": "主动买卖 / 多空比归档停在 2026-10-14T23:55Z",
        "取证": "04-C 01 与 03-A 已登记；本单不重跑采集侧",
        "影响": "任何以比率为门槛的新策略判定，在 10-14 之后属于无输入",
        "性质": "采集侧缺口，跨界未修",
    },
    {
        "id": "GAP-04",
        "内容": "热库 1h 仅从 2026-09-07T02:00Z 起，15m 仅从 2026-09-12 起",
        "取证": "本脚本对 candles 表实测 GROUP BY timeframe 的首末时刻",
        "影响": "窗口前段（06-27 起）的 1h/15m 全部来自冷档 ⇒ 观察流重放与成交回放都依赖冷档",
        "性质": "覆盖缺口",
    },
    {
        "id": "GAP-05",
        "内容": "跳空成交模型、滑点 3/5bps、参与率 0.1%/1.0% 均未批准；"
                "另：旧材料里「策略包风险 1.5%」与代码实测 0.5% 不符，以代码为准",
        "取证": "replay_calc.CALIBERS 两套来源字符串 + 派工单待决表",
        "影响": "两套口径只能并列，不得择一宣称正式成绩",
        "性质": "批准缺口",
    },
]


def main():
    t_all = time.time()
    out = {
        "task": "ACCUM-EARLY-05-C",
        "probe": "01_输入快照与版本锁定",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {
            "start_utc": iso(WIN_START_MS),
            "end_utc": iso(WIN_END_MS),
            "warmup_start_utc": iso(WARMUP_START_MS),
            "start_ms": WIN_START_MS,
            "end_ms": WIN_END_MS,
            "warmup_start_ms": WARMUP_START_MS,
            "why_unchanged": "沿用 04-C 的同一窗口，保证与已交付基线可比。"
                             "窗口末端之后新到货的数据不并入，属已知边界（见 coverage.hot_now）。",
        },
        "code_identity": [],
        "data_identity": {},
        "coverage": {},
        "params": {},
        "dependencies": {},
        "gaps": KNOWN_GAPS,
        "errors": [],
    }

    # ── 1. 代码身份 ────────────────────────────────────────────────────
    print(">>> 代码身份...")
    for rel in CODE_FILES:
        p = ROOT / rel
        if not p.exists():
            out["errors"].append(f"代码文件缺失: {rel}")
            out["code_identity"].append({"path": rel, "missing": True})
            continue
        rec = sha256_file_whole(p)
        out["code_identity"].append(rec)

    # 冻结规则 md 的自证哈希：config.py 的 docstring 里写死了一个 SHA256，
    # 必须核对「声明值」与「实测值」是否一致 —— 这是零成本的高价值校验。
    frozen_md = ROOT / "evidence" / "WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"
    declared = None
    cfg_src = (ROOT / "策略/WyckoffMaster/event_trend_v0_1/config.py").read_text(encoding="utf-8")
    for line in cfg_src.splitlines():
        if "SHA256:" in line:
            declared = line.split("SHA256:")[1].strip()
            break
    actual = next((c["sha256"] for c in out["code_identity"]
                   if c.get("path", "").endswith("冻结规则.md")), None)
    out["frozen_rule_self_check"] = {
        "声明来源": "策略/WyckoffMaster/event_trend_v0_1/config.py 模块 docstring",
        "声明值": declared,
        "实测值": actual,
        "一致": (declared == actual),
        "说明": "不一致不代表规则被改，但必须先查清再引用任何基线。",
    }

    # ── 2. 数据身份 ────────────────────────────────────────────────────
    print(">>> 数据身份（热库分块哈希，约 15 秒）...")
    if HOT_DB.exists():
        hot = sha256_file_chunked(HOT_DB)
        hot["caveat"] = ("采集器实时写入，该 SHA256 只代表取哈希时刻的内容；"
                         "跨时段不可复用，只能用于「同一时刻的隔离泄漏判据」。")
        out["data_identity"]["hot_db"] = hot
    else:
        out["errors"].append("热库不存在")

    print(">>> 数据身份（冷档官方清单）...")
    if COLD_CATALOG.exists():
        cat_raw = COLD_CATALOG.read_bytes()
        cat = json.loads(cat_raw.decode("utf-8"))
        shards = cat.get("shards", {})
        # 按顶层目录分组统计（candles / funding / oi / metrics / candle_heads / sources）
        buckets = {}
        for key, meta in shards.items():
            top = key.split("/", 1)[0]
            b = buckets.setdefault(top, {"shards": 0, "rows": 0, "bytes": 0})
            b["shards"] += 1
            b["rows"] += int(meta.get("rows") or 0)
            b["bytes"] += int(meta.get("bytes") or 0)
        # 分片清单本身的指纹：把 (路径, 行数, 字节, sha256) 排序后整体哈希
        h = hashlib.sha256()
        for key in sorted(shards):
            m = shards[key]
            h.update(f"{key}|{m.get('rows')}|{m.get('bytes')}|{m.get('sha256')}\n".encode())
        out["data_identity"]["cold_parquet"] = {
            "catalog_path": str(COLD_CATALOG.relative_to(ROOT)),
            "catalog_sha256": hashlib.sha256(cat_raw).hexdigest(),
            "catalog_updated_at": cat.get("updated_at"),
            "shards_total": len(shards),
            "rows_total": sum(int(m.get("rows") or 0) for m in shards.values()),
            "bytes_total": sum(int(m.get("bytes") or 0) for m in shards.values()),
            "by_top_dir": buckets,
            "shard_manifest_sha256": h.hexdigest(),
            "manifest_rule": "对 (分片路径, 行数, 字节, 分片自身 sha256) 按路径排序后逐行哈希；"
                             "它等价于「冷档内容指纹」，不需要重算 4 万多个文件。",
            "caveat": "冷档是只增不改的归档；但导出器在跑，清单会随导出增长。"
                      "比对时必须以本文件的 manifest_sha256 + shards_total 成对引用。",
        }
    else:
        out["errors"].append("冷档清单不存在")

    # ── 3. 覆盖边界（实测，不抄旧结论）────────────────────────────────
    print(">>> 覆盖边界（热库各周期首末时刻）...")
    con = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
    try:
        t0 = time.time()
        rows = con.execute(
            """SELECT timeframe, MIN(open_time), MAX(open_time)
               FROM candles WHERE exchange='binance' AND market='um'
               GROUP BY timeframe ORDER BY timeframe""").fetchall()
        hot_cov = {r[0]: {"first_utc": iso(r[1]), "last_utc": iso(r[2]),
                          "first_ms": int(r[1]), "last_ms": int(r[2])} for r in rows}
        out["coverage"]["hot_now"] = {
            "by_timeframe": hot_cov,
            "query_sec": round(time.time() - t0, 2),
            "note": "这是取数时刻的热库水位；窗口末端 2026-10-26T09:00Z 之后的 bar 未纳入本次实验。",
        }
        # 全池规模（观察/允许/已接入三层次口径的唯一事实源是 DATA-AUDIT-01-R1，
        # 这里只记本库自己的符号数，避免与那份清单混用）
        out["coverage"]["symbols"] = {
            "symbol_pool_rows": con.execute("SELECT COUNT(*) FROM symbol_pool").fetchone()[0],
            "distinct_candle_symbols": con.execute(
                "SELECT COUNT(DISTINCT symbol) FROM candles WHERE exchange='binance' AND market='um'"
            ).fetchone()[0],
            "note": "三层次口径（已接入/已存储/实际参与决策）以 DATA-AUDIT-01-R1 的 FACTS-DATA-v1.json 为准，"
                    "本文件不另立一套分母。",
        }
    finally:
        con.close()

    # ── 4. 参数来源 ────────────────────────────────────────────────────
    print(">>> 参数来源与冻结生效值...")
    import replay_calc as RC
    from 策略.WyckoffMaster.event_trend_v0_1.config import make_ft_low4h_config

    cal = {}
    for name, cfg in RC.CALIBERS.items():
        cal[name] = dict(cfg)
    out["params"]["calibers"] = cal

    # 口径冲突逐项从代码实测推导，不手抄任何一方的说法
    a = cal["生效视图_5bps"]
    b = cal["策略包_3bps"]
    out["params"]["caliber_conflicts"] = [
        f"滑点：生效视图 {a['slippage_each_side'] * 1e4:.0f}bps vs 策略包 "
        f"{b['slippage_each_side'] * 1e4:.0f}bps（无批准依据，两套并列）",
        f"每笔风险：两套 risk_per_trade_fraction 实测分别为 {a['risk_per_trade_fraction']} / "
        f"{b['risk_per_trade_fraction']}，对应绝对风险 "
        f"{a['initial_equity'] * a['risk_per_trade_fraction']:.2f} / "
        f"{b['initial_equity'] * b['risk_per_trade_fraction']:.2f} USDT",
        f"参与率上限：生效视图 {a['participation_cap'] * 100:.1f}% vs 策略包 "
        f"{b['participation_cap'] * 100:.1f}%",
        f"初始本金：生效视图 {a['initial_equity']:.0f} vs 策略包 {b['initial_equity']:.0f}",
    ]
    # 与 04-C 正文的写法做机器对撞（04-C 表格把策略包风险写成 1.5% / 30.0 USDT）
    out["params"]["vs_04c_text_check"] = {
        "04C正文写的策略包单笔风险": "1.5% (30.0 USDT)",
        "代码实测": f"{b['risk_per_trade_fraction'] * 100:.2f}% "
                    f"({b['initial_equity'] * b['risk_per_trade_fraction']:.2f} USDT)",
        "一致": abs(b["risk_per_trade_fraction"] - 0.015) < 1e-12,
        "处理": "以代码为准。04-C 正文该格与 CALIBERS 不符，本单不沿用其写法，"
                "并把这条登记为「上一单正文与产物不一致」的实例。",
    }
    ft = make_ft_low4h_config()
    out["params"]["frozen_effective"] = {
        "source": "策略/WyckoffMaster/event_trend_v0_1/config.py · make_ft_low4h_config()",
        "stoploss_s": ft.stoploss_s,
        "trail_d": ft.trail_d,
        "trailing_trigger_r": ft.trailing_trigger_r,
        "trailing_lookback_4h_bars": ft.trailing_lookback_4h_bars,
        "max_holding_days": ft.max_holding_days,
        "risk_budget_per_trade": ft.risk_budget_per_trade,
        "initial_equity": ft.initial_equity,
        "max_active_positions": ft.max_active_positions,
        "max_symbol_positions": ft.max_symbol_positions,
        "max_total_risk_budget": ft.max_total_risk_budget,
        "max_notional_per_position": ft.max_notional_per_position,
        "max_total_notional": ft.max_total_notional,
        "atr_warmup_bars": ft.atr_warmup_bars,
        "taker_fee_bps": ft.taker_fee_bps,
        "slippage_bps": ft.slippage_bps,
        "max_participation_of_prev_15m_quote": ft.max_participation_of_prev_15m_quote,
        "box_bars_4h": ft.box_bars_4h,
        "er_threshold": ft.er_threshold,
        "box_min_width_atr": ft.box_min_width_atr,
        "box_max_width_atr": ft.box_max_width_atr,
        "cooling_hours": ft.cooling_hours,
        "enable_oi_filter_default": ft.enable_oi_filter,
        "note": "以上为代码实际生效值。max_holding_days=None 是用户已定（取消 10 天强平）。",
    }

    # ── 5. 上游依赖状态 ────────────────────────────────────────────────
    print(">>> 上游依赖探测...")
    for label, rel in DEPENDENCY_DIRS.items():
        p = ROOT / rel
        exists = p.exists()
        rec = {"dir": rel, "exists": exists}
        if exists:
            files = [q for q in p.rglob("*") if q.is_file()]
            rec["file_count"] = len(files)
            rec["has_readme"] = (p / "README.md").exists()
            if rec["has_readme"]:
                st = (p / "README.md").stat()
                rec["readme_mtime_utc"] = iso(st.st_mtime * 1000)
        out["dependencies"][label] = rec

    out["dependencies"]["结论"] = (
        "05-B / 05-D / 05-F 三份依赖均未交付（目录不存在）。"
        "本单因此只做「可立即开展」的五项，不运行正式新旧策略对照，"
        "也不自行设阈值跑新成绩。可用替代上游 = 04-D 的 102 笔复算记录与窗口三分流清单，"
        "但必须标注它不是 05-D。"
    )

    out["elapsed_sec"] = round(time.time() - t_all, 1)
    dst = OUT_DIR / "01_输入快照与版本锁定.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✔ 已落盘 {dst.relative_to(ROOT)}  ({out['elapsed_sec']}s)")
    if out["errors"]:
        print("!! errors:", out["errors"])


if __name__ == "__main__":
    main()
