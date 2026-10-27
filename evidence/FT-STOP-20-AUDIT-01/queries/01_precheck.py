# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 开跑前核对（01）

目的（对应工单「开跑前」两项）：
  A. 核对新方案确为「价格下跌 20% 止损 + 账户单笔风险预算 0.5%」，并明确「2R 上涨 40%」的联动。
  B. 确认除上述机制外，信号、数据区间、费用、退出规则及账户约束未变。

本轮做法：
  1) 定向穷举扫描 20% 止损实现/配置/批次（限 策略/、框架/、顶层脚本、evidence 名称、回测日志一级目录，
     不做全盘 grep —— 78G 工程禁全盘）。
  2) 锁定现有冻结配置 SHA-256 与关键参数（作为「未变」基线）。
  3) 锁定原 9 组 C1 基线账本 SHA-256。
  4) 用实测数据算「20% 止损 + 0.5% 风险」的机制耦合算术，并对照现方案实测止损幅度。

只读：不写策略、不跑回测、不改任何既有文件。产物只落 evidence/FT-STOP-20-AUDIT-01/。
"""
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "FT-STOP-20-AUDIT-01" / "out"
LOGS = ROOT / "evidence" / "FT-STOP-20-AUDIT-01" / "logs"
OUT.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

LOG_LINES = []


def log(msg=""):
    print(msg)
    LOG_LINES.append(str(msg))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 1. 定向扫描
STOP_PAT = re.compile(
    r"(20\s*%\s*止损|止损\s*20\s*%|stop[_ ]?loss[_ ]?pct|stop[_ ]?pct|"
    r"fixed[_ ]?stop|pct[_ ]?stop|stop[_ ]?loss[_ ]?20|STOP[_ ]?20|"
    r"trailing[_ ]?stop[_ ]?pct|price_stop)",
    re.IGNORECASE,
)

SCAN_TARGETS = []
# 策略/ 全量 py（体量小）
SCAN_TARGETS += [p for p in (ROOT / "策略").rglob("*.py") if "__pycache__" not in str(p)]
# 框架/ py，排除 node_modules / __pycache__
SCAN_TARGETS += [
    p
    for p in (ROOT / "框架").rglob("*.py")
    if "__pycache__" not in str(p) and "node_modules" not in str(p)
]
# 顶层脚本
SCAN_TARGETS += sorted(ROOT.glob("*.py"))
# 顶层文档
SCAN_TARGETS += sorted(ROOT.glob("*.md"))

scan_hits = []
scanned_files = 0
for p in SCAN_TARGETS:
    try:
        if p.stat().st_size > 4 * 1024 * 1024:
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        scanned_files += 1
        for i, line in enumerate(txt.splitlines(), 1):
            if STOP_PAT.search(line):
                scan_hits.append(
                    {"file": str(p.relative_to(ROOT)), "line": i, "text": line.strip()[:200]}
                )
    except Exception as e:  # noqa
        scan_hits.append({"file": str(p), "line": -1, "text": f"<读取失败 {e}>"})

# 目录/文件名层面：evidence 与回测批次里是否出现 20% 止损相关命名
NAME_PAT = re.compile(r"(STOP[_\-]?20|20PCT|_20P_|STOP20|PCT20)", re.IGNORECASE)
name_hits = []
for d in sorted((ROOT / "evidence").iterdir()):
    if NAME_PAT.search(d.name):
        name_hits.append({"kind": "evidence_dir", "path": d.name})
for d in sorted((ROOT / "回测日志" / "event_trend_v0_1").iterdir()):
    if NAME_PAT.search(d.name):
        name_hits.append({"kind": "btlog_dir", "path": d.name})
# 回测批次内 run 目录名（只看一级名称，不递归）
runs_root = ROOT / "回测日志" / "event_trend_v0_1" / "runs"
run_dir_names = 0
if runs_root.is_dir():
    for d in sorted(runs_root.iterdir()):
        run_dir_names += 1
        if NAME_PAT.search(d.name):
            name_hits.append({"kind": "run_dir", "path": d.name})

log("=" * 78)
log("FT-STOP-20-AUDIT-01 开跑前核对")
log("=" * 78)
log(f"内容扫描文件数: {scanned_files}")
log(f"内容命中(20%止损关键词): {len(scan_hits)}")
log(f"名称命中(目录/批次命名): {len(name_hits)}")
log(f"回测批次 run 目录枚举数: {run_dir_names}")
if scan_hits:
    for h in scan_hits[:40]:
        log(f"  HIT {h['file']}:{h['line']}  {h['text']}")
if name_hits:
    for h in name_hits[:40]:
        log(f"  NAME {h['kind']} {h['path']}")

# ---------------------------------------------------------------- 2. 冻结配置基线
CFG = ROOT / "策略" / "WyckoffMaster" / "event_trend_v0_1" / "config.py"
cfg_sha = sha256_file(CFG)

# 从源码按行提取参数值（避免 import 触发副作用）
PARAM_KEYS = [
    "atr_warmup_bars",
    "initial_stop_buffer_atr",
    "risk_budget_per_trade",
    "initial_equity",
    "max_active_positions",
    "max_symbol_positions",
    "max_total_risk_budget",
    "max_notional_per_position",
    "max_total_notional",
    "trailing_trigger_r",
    "trailing_lookback_4h_bars",
    "max_holding_days",
    "taker_fee_bps",
    "slippage_bps",
    "stress_taker_fee_bps",
    "stress_slippage_bps",
    "entry_delay_15m_bars",
    "box_bars_4h",
    "atr_period_4h",
    "sweep_max_overshoot_atr",
    "breakout_buffer_atr",
    "oi_min_growth_pct",
    "cooling_hours",
    "max_participation_of_prev_15m_quote",
]
cfg_text = CFG.read_text(encoding="utf-8")
params = []
for k in PARAM_KEYS:
    m = re.search(rf"^\s*{k}\s*:\s*[A-Za-z\[\], ]+=\s*([^\s#]+)", cfg_text, re.MULTILINE)
    ln = 0
    if m:
        for i, line in enumerate(cfg_text.splitlines(), 1):
            if re.match(rf"^\s*{k}\s*:", line):
                ln = i
                break
    params.append(
        {
            "参数": k,
            "值": m.group(1) if m else "<未找到>",
            "config.py 行号": ln,
            "机制归类": (
                "止损"
                if "stop" in k
                else "仓位/风险"
                if any(s in k for s in ("risk", "notional", "positions", "equity"))
                else "退出"
                if any(s in k for s in ("trailing", "holding"))
                else "费用"
                if "bps" in k
                else "信号/数据/其他"
            ),
        }
    )

log("")
log(f"config.py SHA-256 = {cfg_sha}")
log("冻结参数（现方案，作为『未变』基线）:")
for r in params:
    log(f"  [{r['机制归类']:<8}] {r['参数']:<38} = {r['值']:<12} (L{r['config.py 行号']})")

# ---------------------------------------------------------------- 3. 基线账本指纹
GROUPS = [f"C1_Vs{v}_Z{z}" for v in ("04", "06", "08") for z in ("02", "04", "06")]
FT09_OUT = ROOT / "evidence" / "FT-ENTRY-09" / "out"

baseline = []
for g in GROUPS:
    st = f"Wyckoff_{g}"
    for kind, pat in (
        ("trades", f"trades_{st}.csv"),
        ("confirmation", f"audit_confirmation_{st}.csv"),
        ("orders", f"audit_orders_{st}.csv"),
    ):
        p = FT09_OUT / pat
        if p.exists():
            baseline.append(
                {
                    "组": g,
                    "类型": kind,
                    "文件": str(p.relative_to(ROOT)),
                    "字节": p.stat().st_size,
                    "sha256": sha256_file(p),
                }
            )
        else:
            baseline.append(
                {"组": g, "类型": kind, "文件": pat, "字节": -1, "sha256": "<缺失>"}
            )

log("")
log("原 9 组 C1 基线账本指纹:")
for b in baseline:
    log(f"  {b['组']:<12} {b['类型']:<13} {b['字节']:>8}  {b['sha256'][:16]}…  {b['文件']}")

# ---------------------------------------------------------------- 4. 机制耦合算术
def read_csv(p: Path):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def event_id_from_tag(tag: str):
    """enter_tag 形如 cand_C1_ev_FT_ENTRY_02_binance_BCHUSDT_1639209600000_1639432800000"""
    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else None


group_stats = []
detail_rows = []
for g in GROUPS:
    st = f"Wyckoff_{g}"
    tp = FT09_OUT / f"trades_{st}.csv"
    cp = FT09_OUT / f"audit_confirmation_{st}.csv"
    if not tp.exists():
        continue
    trades = read_csv(tp)
    p0map = {}
    if cp.exists():
        for r in read_csv(cp):
            p0map[r["event_id"]] = r
    # 权益路径：某笔开仓时的权益 = 初始权益 + 所有「已平仓」笔的 profit_abs
    # （按 close_timestamp <= open_timestamp 判定，正确处理重叠持仓）
    closes = sorted((int(t["close_timestamp"]), float(t["profit_abs"])) for t in trades)
    trades.sort(key=lambda r: int(r["open_timestamp"]))
    dists = []
    risk_ratios = []
    join_ok = 0
    trailing_diff = 0
    for t in trades:
        eid = event_id_from_tag(t.get("enter_tag", ""))
        conf = p0map.get(eid) if eid else None
        p0 = None
        if conf:
            try:
                p0 = float(conf["orig_P0"])
                join_ok += 1
            except Exception:
                p0 = None
        if p0 is None:
            try:
                p0 = float(t["stop_loss_abs"])
            except Exception:
                p0 = None
        open_rate = float(t["open_rate"])
        amount = float(t["amount"])
        open_ts = int(t["open_timestamp"])
        equity_at_entry = 2000.0 + sum(pa for ct, pa in closes if ct <= open_ts)
        # 收盘时的 stop_loss_abs 若高于 orig_P0，说明已被跟踪止损上移
        try:
            sl_close = float(t["stop_loss_abs"])
        except Exception:
            sl_close = None
        if p0 and sl_close is not None and sl_close > p0 * 1.0001:
            trailing_diff += 1
        if p0 and open_rate > 0:
            dist_pct = (open_rate - p0) / open_rate
            dists.append(dist_pct)
            if equity_at_entry > 0:
                risk_amt = amount * (open_rate - p0)
                risk_ratios.append(risk_amt / equity_at_entry)
        detail_rows.append(
            {
                "组": g,
                "event_id": eid or "",
                "pair": t["pair"],
                "open_rate": open_rate,
                "P0": p0,
                "止损幅度%": round(dist_pct * 100, 4) if p0 and open_rate else None,
                "名义额": round(amount * open_rate, 4),
                "开仓时权益": round(equity_at_entry, 4),
                "名义/权益%": round(amount * open_rate / max(equity_at_entry, 1e-9) * 100, 4),
                "风险额": round(amount * (open_rate - p0), 6) if p0 else None,
                "风险/权益%": round(risk_ratios[-1] * 100, 6) if risk_ratios else None,
                "收盘止损": sl_close,
                "止损被上移": bool(p0 and sl_close is not None and sl_close > p0 * 1.0001),
                "exit_reason": t["exit_reason"],
                "profit_abs": float(t["profit_abs"]),
                "持仓小时": round(float(t["trade_duration"]) / 60.0, 3),
            }
        )

    def med(xs):
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    group_stats.append(
        {
            "组": g,
            "笔数": len(trades),
            "P0联接成功": join_ok,
            "止损被上移笔数": trailing_diff,
            "止损幅度_中位%": round(med(dists) * 100, 4) if dists else None,
            "止损幅度_均值%": round(sum(dists) / len(dists) * 100, 4) if dists else None,
            "止损幅度_最小%": round(min(dists) * 100, 4) if dists else None,
            "止损幅度_最大%": round(max(dists) * 100, 4) if dists else None,
            "风险占权益_中位%": round(med(risk_ratios) * 100, 6) if risk_ratios else None,
            "净额": round(sum(float(t["profit_abs"]) for t in trades), 6),
        }
    )

log("")
log("原 9 组 C1 实测止损幅度与风险预算占用:")
log(
    f"  {'组':<12}{'笔数':>5}{'P0联接':>7}{'止损上移':>8}"
    f"{'止损中位%':>11}{'止损均值%':>11}{'风险中位%':>11}{'净额':>13}"
)
for s in group_stats:
    log(
        f"  {s['组']:<12}{s['笔数']:>5}{s['P0联接成功']:>7}{s['止损被上移笔数']:>8}"
        f"{str(s['止损幅度_中位%']):>11}{str(s['止损幅度_均值%']):>11}"
        f"{str(s['风险占权益_中位%']):>11}{s['净额']:>13}"
    )

# 机制耦合：20% 止损 + 0.5% 风险预算 ⇒ 名义占权益比例
all_dists = [r["止损幅度%"] for r in detail_rows if r["止损幅度%"]]
all_risk = [r["风险/权益%"] for r in detail_rows if r["风险/权益%"] is not None]


def medv(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


med_dist = medv(all_dists)          # 单位：%
med_risk = medv(all_risk)           # 单位：%
med_dist_frac = med_dist / 100.0 if med_dist else None
coupling = {
    "现方案_实测止损幅度中位%": round(med_dist, 4) if med_dist else None,
    "现方案_风险预算参数%": 0.5,
    "现方案_实测风险占权益中位%": round(med_risk, 6) if med_risk is not None else None,
    "20方案_止损幅度%": 20.0,
    "20方案_风险预算%": 0.5,
    "20方案_推导名义占权益%": round(0.5 / 20.0 * 100, 6),
    "现方案_推导名义占权益%(按实测止损中位)": round(0.5 / med_dist * 100, 6) if med_dist else None,
    "仓位缩放倍数(现/20)": round((0.5 / med_dist) / (0.5 / 20.0), 4) if med_dist else None,
    "20方案_2R涨幅%": 40.0,
    "现方案_2R涨幅%(按实测止损中位)": round(med_dist * 2, 4) if med_dist else None,
    "初始权益": 2000.0,
    "20方案_单仓名义额(USDT)": round(2000.0 * 0.005 / 0.20, 4),
    "现方案_单仓名义额(USDT,按实测中位)": round(2000.0 * 0.005 / med_dist_frac, 4)
    if med_dist_frac
    else None,
    "max_notional_per_position%": 30.0,
    "20方案_是否触发单仓名义上限": (0.5 / 20.0 * 100) > 30.0,
    "20方案_3仓合计名义占权益%": round(3 * 0.5 / 20.0 * 100, 6),
    "max_total_notional%": 90.0,
    "20方案_3仓合计是否触发总名义上限": (3 * 0.5 / 20.0 * 100) > 90.0,
    "说明": "名义占权益% = 单笔风险预算% ÷ 止损幅度%；止损越宽，同样风险预算下仓位越小",
}

log("")
log("机制耦合算术（20% 止损 + 0.5% 风险预算）:")
for k, v in coupling.items():
    log(f"  {k:<46} = {v}")

# ---------------------------------------------------------------- 写出
(OUT / "01_扫描结果.json").write_text(
    json.dumps(
        {
            "扫描": {
                "内容扫描文件数": scanned_files,
                "内容命中数": len(scan_hits),
                "内容命中明细": scan_hits,
                "名称命中数": len(name_hits),
                "名称命中明细": name_hits,
                "回测批次run目录枚举数": run_dir_names,
                "扫描范围说明": "策略/ 与 框架/ 的 .py + 顶层 .py/.md 内容扫描；evidence 与回测批次仅名称匹配；未做全盘 grep",
            },
            "config_sha256": cfg_sha,
            "参数": params,
            "基线账本": baseline,
            "组统计": group_stats,
            "机制耦合": coupling,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

with open(OUT / "01_参数表.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["参数", "值", "config.py 行号", "机制归类"])
    w.writeheader()
    w.writerows(params)

with open(OUT / "01_基线指纹.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["组", "类型", "文件", "字节", "sha256"])
    w.writeheader()
    w.writerows(baseline)

with open(OUT / "01_逐笔止损明细.csv", "w", newline="", encoding="utf-8-sig") as f:
    if detail_rows:
        w = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        w.writeheader()
        w.writerows(detail_rows)

with open(OUT / "01_机制耦合.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["项目", "值"])
    for k, v in coupling.items():
        w.writerow([k, v])

(LOGS / "01_precheck.log").write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")

log("")
log(f"产物: {OUT}")
