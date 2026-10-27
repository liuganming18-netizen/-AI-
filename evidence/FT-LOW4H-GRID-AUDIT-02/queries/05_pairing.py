# -*- coding: utf-8 -*-
"""05 与上一轮 FT-STOP-GRID-02 的配对比较 + 新增/减少/跳过 + Δ 分解 + C8。

口径澄清（在计算前写定）：
  冻结文本 C8 写作「新增/减少/跳过交易之外的净改善占比 ≤ 50%」，但其标题是
  「改善不能只来自两边都成交的交易」——两者语义相反。本脚本按**标题语义**实现主口径
  （配对事件贡献的净改善占比 ≥ 50% 才算机制性改善），同时把相反口径一并输出，
  两种读法都摆在表里，读者可自行核对。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
RES: dict = {"段": {}}
PREV_S = {"S05": "Stop05", "S10": "Stop10", "S15": "Stop15", "S20": "Stop20"}


def add(sec: str, key: str, val, ok=None, note: str = "") -> None:
    RES["段"].setdefault(sec, {})[key] = val
    if ok is not None:
        RES.setdefault("判据", []).append({"段": sec, "项": key, "通过": bool(ok), "说明": note})


def ev_id(tag) -> str | None:
    if not isinstance(tag, str) or "cand_C1_" not in tag:
        return None
    return "_".join(tag.split("cand_C1_", 1)[-1].split("_")[:7])


def load_prev() -> pd.DataFrame:
    frames = []
    for s in L.S_TIERS:
        for p in L.PARAMS:
            n = f"Wyckoff_C1_{PREV_S[s]}_{p}"
            f = L.PREV / f"out/trades_{n}.csv"
            if not f.exists():
                continue
            d = pd.read_csv(f, encoding="utf-8-sig")
            if d.empty:
                continue
            d["策略"] = n
            d["s_code"] = s
            d["s"] = L.S_PCT[s]
            d["参数组"] = p
            d["事件ID"] = d["enter_tag"].map(ev_id)
            d["期末结算"] = d["exit_reason"].astype(str).eq("force_exit")
            d["open_ms"] = pd.to_numeric(d["open_timestamp"], errors="coerce").astype("int64")
            d["close_ms"] = pd.to_numeric(d["close_timestamp"], errors="coerce").astype("int64")
            frames.append(d)
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    cur, _, _ = L.load_all()
    cur = L.enrich(cur)
    cur["事件ID"] = cur["enter_tag"].map(ev_id)
    prev = load_prev()
    prev = L.enrich(prev)

    # ---------- 上一轮原始账本合计（纠正上游对照数字用） ----------
    base = prev.groupby("s_code")["profit_abs"].agg(["size", "sum"]).reset_index()
    add("上一轮基准", "按止损档位的 L3 合计（原始账本重算）",
        base.rename(columns={"size": "笔数", "sum": "净收益"}).round(2).to_dict("records"))

    # ---------- 逐配对 ----------
    rows = []
    for combo in L.COMBOS:
        s_code, d_code = combo.split("_")
        for p in L.PARAMS:
            n_cur = f"Wyckoff_C1_{s_code}_{d_code}_{p}"
            n_prev = f"Wyckoff_C1_{PREV_S[s_code]}_{p}"
            a = cur[cur["策略"].eq(n_cur)]
            b = prev[prev["策略"].eq(n_prev)]
            if a.empty or b.empty:
                continue
            ea = a.groupby("事件ID")["R"].sum()
            eb = b.groupby("事件ID")["R"].sum()
            both = ea.index.intersection(eb.index)
            new = ea.index.difference(eb.index)
            gone = eb.index.difference(ea.index)
            d_tot = float(ea.sum() - eb.sum())
            d_matched = float((ea[both] - eb[both]).sum())
            d_new = float(ea[new].sum())
            d_gone = float(-eb[gone].sum())
            diff = (ea[both] - eb[both])
            pos = int((diff > 0).sum()); neg = int((diff < 0).sum())
            rows.append({
                "组合": combo, "参数组": p, "本轮": n_cur, "上一轮": n_prev,
                "本轮笔数": len(a), "上轮笔数": len(b),
                "共有事件": len(both), "新增": len(new), "减少": len(gone),
                "Δ总R": d_tot, "Δ共有事件": d_matched, "Δ新增": d_new, "Δ减少": d_gone,
                "恒等式误差": d_tot - (d_matched + d_new + d_gone),
                "配对为正": pos, "配对为负": neg,
                "符号检验p": L.sign_test_p(pos, neg),
                "ΔR中位": float(diff.median()) if len(diff) else None,
                "本轮总R": float(ea.sum()), "上轮总R": float(eb.sum()),
                "共有事件占比": len(both) / max(1, len(ea)),
                "配对贡献占比": (d_matched / d_tot) if abs(d_tot) > 1e-9 else None,
            })
    pr = pd.DataFrame(rows)
    pr["p_BH"] = L.bh_fdr(pr["符号检验p"].tolist())
    pr.to_csv(OUT / "05_逐配对.csv", index=False, encoding="utf-8-sig")

    add("配对", "配对数", len(pr), len(pr) == 144)
    add("配对", "Δ 分解恒等式最大误差", float(pr["恒等式误差"].abs().max()),
        float(pr["恒等式误差"].abs().max()) < 1e-9)
    add("配对", "共有事件占比（中位 / 最小）",
        [float(pr["共有事件占比"].median()), float(pr["共有事件占比"].min())],
        None, "本轮同时改了启动时点/参考K线/跟踪距离，交易集合本身会变")
    add("配对", "新增/减少笔数合计", {"新增": int(pr["新增"].sum()), "减少": int(pr["减少"].sum())})
    add("配对", "符号检验原始 p<0.05 的配对数", int((pr["符号检验p"] < 0.05).sum()), None, f"共 {len(pr)}")
    add("配对", "BH-FDR 后 p<0.05 的配对数", int((pr["p_BH"] < 0.05).sum()),
        int((pr["p_BH"] < 0.05).sum()) == 0, "若为 0，任何'某组合更好'都只是点估计")

    # ---------- 按组合汇总 ----------
    agg = pr.groupby("组合").agg(
        配对数=("参数组", "size"),
        Δ总R合计=("Δ总R", "sum"), Δ总R中位=("Δ总R", "median"),
        Δ共有事件合计=("Δ共有事件", "sum"),
        新增合计=("新增", "sum"), 减少合计=("减少", "sum"),
        配对贡献占比中位=("配对贡献占比", "median"),
    ).reset_index()
    agg["配对贡献占比_合计口径"] = agg["Δ共有事件合计"] / agg["Δ总R合计"]
    agg.to_csv(OUT / "05_组合汇总.csv", index=False, encoding="utf-8-sig")

    # C8：配对事件贡献的净改善占比 ≥ 50% 且 Δ总R > 0
    agg["C8"] = (agg["Δ总R合计"] > 0) & (agg["配对贡献占比_合计口径"] >= 0.5)
    agg["C8_相反读法"] = (agg["Δ总R合计"] > 0) & (agg["配对贡献占比_合计口径"] <= 0.5)
    add("C8", "按组合的 Δ 分解", agg.round(4).to_dict("records"))
    add("C8", "C8 满足的组合（标题语义）", agg.loc[agg["C8"], "组合"].tolist(), None,
        f"{int(agg['C8'].sum())}/16")
    add("C8", "C8 满足的组合（冻结文本字面）", agg.loc[agg["C8_相反读法"], "组合"].tolist(), None,
        f"{int(agg['C8_相反读法'].sum())}/16")

    # ---------- 跳过交易（确认表 CONFIRMED 但未成交） ----------
    skip_rows = []
    for combo in L.COMBOS:
        s_code, d_code = combo.split("_")
        for p in L.PARAMS:
            n = f"Wyckoff_C1_{s_code}_{d_code}_{p}"
            f = L.UP / f"out/audit_confirmation_{n}.csv"
            if not f.exists():
                continue
            d = pd.read_csv(f, encoding="utf-8-sig")
            conf = int((d["decision"].astype(str) == "CONFIRMED").sum())
            trd = int(cur["策略"].eq(n).sum())
            skip_rows.append({"组合": combo, "参数组": p, "确认通过": conf, "实际成交": trd,
                              "跳过": conf - trd})
    sk = pd.DataFrame(skip_rows)
    sk.to_csv(OUT / "05_跳过统计.csv", index=False, encoding="utf-8-sig")
    add("跳过", "确认通过但未成交（全 144 组）", {
        "确认通过": int(sk["确认通过"].sum()), "实际成交": int(sk["实际成交"].sum()),
        "跳过": int(sk["跳过"].sum()),
        "跳过率pct": float(sk["跳过"].sum() / max(1, sk["确认通过"].sum()) * 100),
    })
    add("跳过", "按组合的跳过率",
        sk.groupby("组合").apply(lambda g: round(g["跳过"].sum() / max(1, g["确认通过"].sum()) * 100, 2))
        .to_dict(), None, "跳过 = 槽位已满或最小下单额不足，不是信号无效")

    # 合并 C8 回稳定性表
    st = pd.read_csv(OUT / "04_稳定性逐组合.csv")
    st = st.merge(agg[["组合", "C8", "C8_相反读法", "Δ总R合计", "配对贡献占比_合计口径"]], on="组合")
    st["C1~C8满足数"] = st[["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]].sum(axis=1)
    st["达到5条"] = st["C1~C8满足数"] >= 5
    st.to_csv(OUT / "05_稳定性终裁.csv", index=False, encoding="utf-8-sig")
    add("终裁", "16 组合 C1~C8 满足数", st.set_index("组合")["C1~C8满足数"].to_dict())
    add("终裁", "达到 5 条门槛的组合", st.loc[st["达到5条"], "组合"].tolist(),
        bool(st["达到5条"].any()), "门槛：C1~C8 中 ≥5 条且 C8 满足")

    crit = RES.get("判据", [])
    RES["判据统计"] = {"总": len(crit), "通过": sum(1 for c in crit if c["通过"]),
                       "不通过": sum(1 for c in crit if not c["通过"]),
                       "无判据": sum(1 for c in crit if c["通过"] is None)}
    with open(OUT / "05_配对与终裁.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=2, default=str)
    print("判据:", RES["判据统计"])
    print(st[["组合", "L2中位", "Δ总R合计", "配对贡献占比_合计口径", "C1~C8满足数", "达到5条"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
