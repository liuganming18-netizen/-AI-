# -*- coding: utf-8 -*-
"""01 运行来源核实：规模、压缩包对应、每组运行时版本证据、数据与配置。

核心原则：当前源码 SHA 不能代替运行时版本证据。
本脚本按优先级取证：zip 内嵌派生类源码 → zip 内嵌运行配置 → 日志起止时间 → 行为反证。
"""
from __future__ import annotations

import ast
import glob
import hashlib
import json
import os
import re
import zipfile
from datetime import datetime

import numpy as np
import pandas as pd

import _lib as L

RES: dict = {"段": {}}


def add(sec: str, key: str, val, ok=None, note: str = "") -> None:
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES.setdefault("判据", []).append({"段": sec, "项": key, "通过": bool(ok), "说明": note})


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p) -> str | None:
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def class_attrs(src: str) -> dict[str, dict]:
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            d = {}
            for s in node.body:
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                    try:
                        d[s.target.id] = ast.literal_eval(s.value)
                    except Exception:
                        pass
                elif isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name):
                    try:
                        d[s.targets[0].id] = ast.literal_eval(s.value)
                    except Exception:
                        pass
            out[node.name] = d
    return out


def main() -> None:
    strat_dir = L.UP / "user_data/strategies"
    on_disk = {p.stem: p for p in strat_dir.glob("*.py")}
    base_p = strat_dir / "WyckoffLow4hGridBase.py"
    base_sha = sha_file(base_p)
    base_mtime = datetime.fromtimestamp(base_p.stat().st_mtime)

    # ---------- P1 规模 ----------
    names = sorted(on_disk)
    derived = [n for n in names if n != "WyckoffLow4hGridBase"]
    add("P1_规模", "策略文件总数", len(names))
    add("P1_规模", "派生类文件数", len(derived), len(derived) == 144, f"应为 144")
    miss = [n for n in L.ALL if n not in on_disk]
    extra = [n for n in derived if n not in L.ALL]
    add("P1_规模", "缺失组", miss, not miss)
    add("P1_规模", "多余组", extra, not extra)
    dup = len(derived) - len(set(derived))
    add("P1_规模", "重复组数", dup, dup == 0)
    grid = {(s, d, p) for s in L.S_TIERS for d in L.D_TIERS for p in L.PARAMS}
    got = set()
    for n in derived:
        s, d, p = L.parse(n)
        got.add((s, d, p))
    add("P1_规模", "16×9 网格缺口", sorted(grid - got), not (grid - got), f"应有 {len(grid)} 格")
    add("P1_规模", "越界组合", sorted(got - grid), not (got - grid))

    # 派生类属性与名字一致
    bad = []
    attr_tab = []
    for n in L.ALL:
        p = on_disk.get(n)
        if p is None:
            continue
        src = p.read_text(encoding="utf-8")
        ca = class_attrs(src).get(n, {})
        s, d, par = L.parse(n)
        vs = int(par.split("_")[0].replace("Vs", "")) / 10.0
        z = int(par.split("_")[1].replace("Z", "")) / 10.0
        rec = {"策略": n, "stoploss_s": ca.get("stoploss_s"), "trail_d": ca.get("trail_d"),
               "c_vol_ratio_vs": ca.get("c_vol_ratio_vs"), "c_retest_zone_atr": ca.get("c_retest_zone_atr"),
               "timing_mode": ca.get("timing_mode")}
        attr_tab.append(rec)
        if abs(float(ca.get("stoploss_s", -1)) - L.S_PCT[s]) > 1e-12:
            bad.append({**rec, "问题": "stoploss_s 错位", "应": L.S_PCT[s]})
        if abs(float(ca.get("trail_d", -1)) - L.D_PCT[d]) > 1e-12:
            bad.append({**rec, "问题": "trail_d 错位", "应": L.D_PCT[d]})
        if abs(float(ca.get("c_vol_ratio_vs", -1)) - vs) > 1e-9:
            bad.append({**rec, "问题": "c_vol_ratio_vs 错位", "应": vs})
        if abs(float(ca.get("c_retest_zone_atr", -1)) - z) > 1e-9:
            bad.append({**rec, "问题": "c_retest_zone_atr 错位", "应": z})
        if ca.get("timing_mode") != "C1":
            bad.append({**rec, "问题": "timing_mode 非 C1"})
    add("P1_规模", "派生类参数错位", bad[:20], not bad, f"{len(bad)} 条错位")
    pd.DataFrame(attr_tab).to_csv(L.OUT / "01_派生类属性.csv", index=False, encoding="utf-8-sig")

    # ---------- P2 压缩包对应 ----------
    zips = sorted(glob.glob(str(L.UP / "out/*.zip")))
    zmap = {}
    other = []
    for z in zips:
        b = os.path.basename(z)
        if b.startswith("backtest-result-"):
            zmap[b[len("backtest-result-"):-4]] = z
        else:
            other.append(z)
    add("P2_压缩包", "zip 总数", len(zips))
    add("P2_压缩包", "标准结果包数", len(zmap), len(zmap) == 144)
    add("P2_压缩包", "非标准命名包", [os.path.basename(x) for x in other], len(other) == 2,
        "多出的 2 个，须逐个说明用途")

    # 逐个查非标准包的用途
    extra_detail = []
    for z in other:
        with zipfile.ZipFile(z) as zf:
            jn = [n for n in zf.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
            d = json.load(zf.open(jn[0])) if jn else {}
            keys = list(d.get("strategy", {}).keys())
            ntr = len(d.get("strategy", {}).get(keys[0], {}).get("trades", [])) if keys else 0
            py = [n for n in zf.namelist() if n.endswith(".py")]
            cls = None
            if py:
                m = re.search(r"class\s+(\w+)\(", zf.read(py[0]).decode("utf-8"))
                cls = m.group(1) if m else None
        extra_detail.append({
            "文件": os.path.basename(z), "大小": os.path.getsize(z),
            "修改时间": datetime.fromtimestamp(os.path.getmtime(z)).isoformat(timespec="seconds"),
            "内含策略": keys, "内含类名": cls, "笔数": ntr,
        })
    add("P2_压缩包", "非标准包明细", extra_detail)
    add("P2_压缩包", "标准包缺逐笔", sorted(set(zmap) - set(L.ALL)), not (set(zmap) - set(L.ALL)))
    add("P2_压缩包", "逐笔缺标准包", sorted(set(L.ALL) - set(zmap)), not (set(L.ALL) - set(zmap)))

    # ---------- P3 每组运行时证据 ----------
    rows = []
    for n in L.ALL:
        p = on_disk[n]
        z = zmap.get(n)
        log = L.UP / f"logs/{n}_backtest.log"
        rec = {"策略": n, "组合": L.combo_of(n), "参数组": L.param_of(n)}
        rec["派生类_SHA"] = sha_file(p)
        rec["派生类_mtime"] = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        # 日志
        if log.exists():
            txt = log.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", txt)
            rec["日志首行"] = m.group(1) if m else None
            rec["日志末次写入"] = datetime.fromtimestamp(log.stat().st_mtime).isoformat(timespec="seconds")
            rec["日志大小"] = log.stat().st_size
            # 日志里回显的关键配置
            rec["日志_max_open_trades"] = "max_open_trades: 3" in txt
            rec["日志_timerange"] = "20211201-20241231" in txt
            rec["日志_datadir"] = "FT-LOW4H-GRID-01/user_data/data/binance" in txt
        # zip
        if z:
            rec["zip_mtime"] = datetime.fromtimestamp(os.path.getmtime(z)).isoformat(timespec="seconds")
            with zipfile.ZipFile(z) as zf:
                pyl = [x for x in zf.namelist() if x.endswith(".py")]
                cfl = [x for x in zf.namelist() if x.endswith("_config.json")]
                if pyl:
                    b = zf.read(pyl[0])
                    rec["内嵌类_SHA"] = sha_bytes(b)
                    rec["内嵌类_等于磁盘"] = (rec["内嵌类_SHA"] == rec["派生类_SHA"])
                if cfl:
                    cfg = json.load(zf.open(cfl[0]))
                    rec["cfg_max_open_trades"] = cfg.get("max_open_trades")
                    rec["cfg_dry_run_wallet"] = cfg.get("dry_run_wallet")
                    rec["cfg_fee"] = (cfg.get("fee") or {}).get("fee") if isinstance(cfg.get("fee"), dict) else cfg.get("fee")
                    rec["cfg_trading_mode"] = cfg.get("trading_mode")
                    rec["cfg_margin_mode"] = cfg.get("margin_mode")
                    rec["cfg_timeframe"] = cfg.get("timeframe")
                    rec["cfg_pairs"] = len(cfg.get("exchange", {}).get("pair_whitelist", []))
                    rec["cfg_datadir"] = cfg.get("datadir")
                    rec["cfg_SHA"] = sha_bytes(zf.read(cfl[0]))
        rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_csv(L.OUT / "01_运行时证据逐组.csv", index=False, encoding="utf-8-sig")

    add("P3_运行时", "zip 内嵌派生类与磁盘逐文件一致", int(df["内嵌类_等于磁盘"].fillna(False).sum()),
        int(df["内嵌类_等于磁盘"].fillna(False).sum()) == 144, "144 组全部一致才可通过")
    add("P3_运行时", "日志首行时间范围",
        [df["日志首行"].min(), df["日志首行"].max()])
    add("P3_运行时", "基类_mtime", base_mtime.isoformat(timespec="seconds"))
    add("P3_运行时", "基类_SHA", base_sha)
    first_run = pd.to_datetime(df["日志首行"].min())
    add("P3_运行时", "全部运行晚于基类写入",
        bool(first_run > base_mtime),
        f"最早运行 {first_run}，基类写入 {base_mtime}；晚 {round((first_run - base_mtime).total_seconds() / 60, 1)} 分钟")
    add("P3_运行时", "运行期间基类是否被改写",
        "无法从 mtime 单独证明；见 P4 行为反证",
        None, "mtime 只说明'最后一次写入时刻'，改写后回写同样 mtime 的极端情形无法排除，故必须叠加行为反证")
    for k, want in [("cfg_max_open_trades", 3), ("cfg_dry_run_wallet", 2000),
                    ("cfg_trading_mode", "futures"), ("cfg_margin_mode", "cross"),
                    ("cfg_timeframe", "15m"), ("cfg_pairs", 74)]:
        vals = df[k].dropna().unique().tolist()
        add("P3_运行时", f"运行配置 {k} 取值", vals, len(vals) == 1 and vals[0] == want, f"应为 {want}")
    fee_vals = sorted({str(x) for x in df["cfg_fee"].dropna().unique()})
    add("P3_运行时", "运行配置 fee", fee_vals, len(fee_vals) == 1 and "0.0005" in fee_vals[0])

    # ---------- P4 行为反证：s / d 是否真的被读到 ----------
    tr, missing, zero = L.load_all()
    add("P4_行为反证", "逐笔表", {"组数": int(tr["策略"].nunique()), "笔数": len(tr)}, len(tr) > 0,
        f"缺 {len(missing)} 组，零成交 {len(zero)} 组")

    # 反证 s：仓位 = 权益 × 0.5% / s ⇒ stake/equity ≈ 0.005/s（0.10 / 0.05 / 0.0333 / 0.025）
    e = L.enrich(tr)
    e["仓位率"] = pd.to_numeric(e["stake_amount"], errors="coerce") / e["开仓时权益"]
    e["s_反推"] = L.RISK_RATE / e["仓位率"]
    # 排除触及 30% 名义上限的单（其余为纯 0.5%/s 口径）
    pure = e[e["仓位率"] < 0.29].copy()
    g2 = pure.groupby("组合").agg(
        笔数=("s_反推", "size"), 反推中位=("s_反推", "median"),
        仓位率中位=("仓位率", "median")).reset_index()
    g2["s_名义"] = g2["组合"].map(lambda c: L.S_PCT[c.split("_")[0]])
    g2["相对偏差"] = g2["反推中位"] / g2["s_名义"] - 1.0
    add("P4_行为反证", "由仓位反推的 s（逐组合）", g2.round(6).to_dict("records"))
    add("P4_行为反证", "四档 s 是否真的被读到",
        bool(g2["相对偏差"].abs().max() < 0.03), g2["相对偏差"].abs().max() < 0.03,
        f"最大相对偏差 {g2['相对偏差'].abs().max() * 100:.2f}%（来源：freqtrade 钱包口径与'开仓时已实现权益'差一个未平仓估值，非参数错）")
    add("P4_行为反证", "四档仓位率是否可区分",
        sorted(g2["仓位率中位"].round(4).unique().tolist()),
        len(g2["仓位率中位"].round(3).unique()) == 4,
        "若 custom_stoploss/custom_stake_amount 仍读 stoploss_pct(0.20)，四档仓位会全部塌缩到 0.025 权益")
    g2.to_csv(L.OUT / "01_仓位反推s.csv", index=False, encoding="utf-8-sig")

    # 反证 d：同一 s 下 4 档 d 必须产生 4 组不同行为
    piv = tr.groupby("组合").agg(笔数=("pair", "size"),
                                净收益=("profit_abs", "sum"),
                                止损比例中位=("stop_loss_ratio", "median")).reset_index()
    piv.to_csv(L.OUT / "01_组合行为差异.csv", index=False, encoding="utf-8-sig")
    dup_cnt = int(piv.duplicated(subset=["笔数", "净收益"], keep=False).sum())
    add("P4_行为反证", "16 组合的(笔数,净收益)重复项数", dup_cnt, dup_cnt == 0,
        "若 d 未被读取，同一 s 下 4 档 d 会产生完全相同的笔数与收益")

    # 标签可信度：止损已上移但标签仍是 stop_loss 的笔数
    moved = (pd.to_numeric(tr["stop_loss_abs"], errors="coerce") /
             pd.to_numeric(tr["open_rate"], errors="coerce") - 1.0) > -(tr["s"] - 1e-6)
    lab_sl = tr["exit_reason"].astype(str).eq("stop_loss")
    lab_tr = tr["exit_reason"].astype(str).eq("trailing_stop_loss")
    add("P4_行为反证", "标签口径核对", {
        "stop_loss 标签笔数": int(lab_sl.sum()),
        "其中止损已上移（标签口径会判错）": int((lab_sl & moved).sum()),
        "trailing_stop_loss 标签笔数": int(lab_tr.sum()),
        "其中止损未上移（标签口径会判错）": int((lab_tr & ~moved).sum()),
        "force_exit 笔数": int(tr["期末结算"].sum()),
    }, None, "标签不能替代机制判断，逐笔重建见 02")

    # ---------- P5 数据 ----------
    ddir = L.UP / "user_data/data"
    real = os.path.realpath(ddir)
    files = sorted(glob.glob(str(L.DATA / "*.feather")))
    add("P5_数据", "data 软链指向", real)
    add("P5_数据", "futures 文件数", len(files), len(files) == 518, "74 币 × 7 类")
    ref = L.BASE_DIR / "evidence/FT-ENTRY-02/user_data/data/binance/futures"
    diff = []
    for p in files:
        b = os.path.basename(p)
        q = ref / b
        if not q.exists() or sha_file(p) != sha_file(q):
            diff.append(b)
    add("P5_数据", "与 FT-ENTRY-02 内容不同的文件", diff, not diff, "逐文件 SHA-256 比对")

    # ---------- P6 其余文件 ----------
    n_trades = len(glob.glob(str(L.UP / "out/trades_*.csv")))
    n_conf = len(glob.glob(str(L.UP / "out/audit_confirmation_*.csv")))
    n_ord = len(glob.glob(str(L.UP / "out/audit_orders_*.csv")))
    add("P6_产物", "逐笔/确认表/订单表 份数", [n_trades, n_conf, n_ord],
        n_trades == n_conf == n_ord == 144)

    # 汇总
    crit = RES.get("判据", [])
    RES["判据统计"] = {"总": len(crit), "通过": sum(1 for c in crit if c["通过"]),
                       "不通过": sum(1 for c in crit if not c["通过"]),
                       "无判据": sum(1 for c in crit if c["通过"] is None)}
    with open(L.OUT / "01_运行来源核实.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=2, default=str)
    print("判据:", RES["判据统计"])
    for c in crit:
        if c["通过"] is False:
            print("  ✗", c["段"], c["项"], c["说明"])


if __name__ == "__main__":
    main()
