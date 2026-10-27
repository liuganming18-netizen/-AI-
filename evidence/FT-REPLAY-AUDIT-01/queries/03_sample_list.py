# -*- coding: utf-8 -*-
"""03 固定抽样：C0/C1/C2 各 ≥2 笔，覆盖盈利/亏损/不同币/跨日；源账本与复盘逐项对照。"""
from __future__ import annotations
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from _common import E09, OUT, ROOT, jdump, load_replay, load_source, write_csv

GRIDS=["Vs04_Z02","Vs04_Z04","Vs04_Z06","Vs06_Z02","Vs06_Z04","Vs06_Z06","Vs08_Z02","Vs08_Z04","Vs08_Z06"]

def p0_source():
    rows=[]
    s=pd.read_csv(os.path.join(ROOT,"evidence/FT-ENTRY-07/02_逐候选对照表.csv"),encoding="utf-8-sig",low_memory=False)
    s["key"]=s["param_group"]+"|"+s["event_id"]
    rows.append(("C",s[["key","P0_stop"]]))
    for mode in ("C1","C2"):
        for g in GRIDS:
            p=os.path.join(E09,"out",f"audit_confirmation_Wyckoff_{mode}_{g}.csv")
            if os.path.isfile(p):
                d=pd.read_csv(p,encoding="utf-8-sig"); d["param_suffix"]=g
                rows.append((mode,d[["param_suffix","event_id","orig_P0"]]))
    out=[]
    for mode,d in rows:
        if mode=="C":
            d=d.rename(columns={"P0_stop":"P0"})
        else:
            d=d.rename(columns={"orig_P0":"P0"}); d["key"]=d["param_suffix"]+"|"+d["event_id"]; d=d[["key","P0"]]
        out.append(d)
    p=pd.concat(out,ignore_index=True)
    return p.drop_duplicates("key")

def main():
    src=load_source(); rep=load_replay(); p0=p0_source().set_index("key")["P0"].to_dict()
    src["key"]=src["strategy"].str.replace(r"^Wyckoff_(C1|C2|C)_","",regex=True)+"|"+src["grid"]+"|"+src["event_id"]
    src["key"]=src["grid"]+"|"+src["event_id"]
    src["P0"]=src["key"].map(p0)
    src["sig_ms"]=pd.to_numeric(src["enter_tag"].str.rsplit("_",n=1).str[-1],errors="coerce")
    src.loc[src["mode"]!="C","sig_ms"]=src.loc[src["mode"]!="C","sig_ms"]-3600_000
    src["confirm_ms"]=src["sig_ms"]+ (0 if True else 0)
    src.loc[src["mode"]!="C","confirm_ms"]=src.loc[src["mode"]!="C","sig_ms"]+3600_000
    src["R"]=(src["open_rate"]-src["P0"])*src["amount"]
    src["is_win"]=src["profit_abs"]>0
    src["cross_day"]=pd.to_datetime(src["close_timestamp"],unit="ms",utc=True).dt.date!=pd.to_datetime(src["open_timestamp"],unit="ms",utc=True).dt.date
    picks=[]
    for mode in ("C","C1","C2"):
        d=src[src["mode"]==mode].sort_values(["open_timestamp","event_id"])
        used=set()
        # 1) 第一笔盈利（不同币）
        w=d[d.is_win]
        if len(w): picks.append(("win",w.iloc[0])); used.add(w.iloc[0]["symbol"])
        # 2) 第一笔亏损（不同币）
        l=d[(~d.is_win)&(~d["symbol"].isin(used))]
        if len(l): picks.append(("loss",l.iloc[0])); used.add(l.iloc[0]["symbol"])
        # 3) 第一笔跨日（不同币，若还没有）
        x=d[(d.cross_day)&(~d["symbol"].isin(used))]
        if len(x): picks.append(("cross_day",x.iloc[0]))
    rows=[]
    for tag,r in picks:
        rk=r["trade_key"]
        rr=rep[(rep.execution_status=="FILLED")&(rep.trade_key==rk)]
        rr=rr.iloc[0] if len(rr) else None
        row={"tag":tag,"mode":r["mode"],"strategy":r["strategy"],"grid":r["grid"],"pair":r["pair"],
             "event_id":r["event_id"],"trade_key":rk,
             "signal_time":pd.to_datetime(int(r["sig_ms"]),unit="ms",utc=True),
             "confirm_time":pd.to_datetime(int(r["confirm_ms"]),unit="ms",utc=True),
             "entry_time":pd.to_datetime(int(r["open_timestamp"]),unit="ms",utc=True),
             "exit_time":pd.to_datetime(int(r["close_timestamp"]),unit="ms",utc=True),
             "entry_price":float(r["open_rate"]),"exit_price":float(r["close_rate"]),
             "qty":float(r["amount"]),"P0":float(r["P0"]) if pd.notna(r["P0"]) else None,
             "R":round(float(r["R"]),6) if pd.notna(r["R"]) else None,
             "net":float(r["profit_abs"]),"exit_reason":r["exit_reason"],
             "replay_trade_id":rr["trade_id"] if rr is not None else None,
             "replay_P0":float(rr["initial_stop_P0"]) if rr is not None else None,
             "replay_R":float(rr["planned_risk_R"]) if rr is not None else None,
             "replay_entry":float(rr["entry_price"]) if rr is not None else None,
             "replay_net":float(rr["net_pnl_usdt"]) if rr is not None else None}
        for a,b,n in [("entry_price","replay_entry","entry_diff"),("net","replay_net","net_diff"),("P0","replay_P0","P0_diff"),("R","replay_R","R_diff")]:
            row[n]=round(row[a]-row[b],8) if row[a] is not None and row[b] is not None else None
        rows.append(row)
    df=pd.DataFrame(rows)
    write_csv(df,"03_固定抽样_v1.csv")
    info={"samples":len(df),"per_mode":df.groupby("mode").size().to_dict(),
          "wins":int((df.net>0).sum()),"losses":int((df.net<0).sum()),
          "unique_symbols":int(df.pair.nunique()),
          "cross_day":int(((df.exit_time.dt.date)!= (df.entry_time.dt.date)).sum()),
          "P0_mismatch":int((df.P0_diff.abs()>1e-6).sum()),
          "entry_mismatch":int((df.entry_diff.abs()>1e-6).sum()),
          "net_mismatch":int((df.net_diff.abs()>1e-6).sum()),
          "note":"每模式取首笔盈利、首笔亏损、首笔跨日（不同币）；源账本=FT-ENTRY-09。"}
    jdump(os.path.join(OUT,"03_抽样摘要_v1.json"),info)
    print(df[["tag","mode","pair","grid","signal_time","entry_time","exit_time","entry_price","qty","P0","R","net","P0_diff","R_diff"]].to_string(index=False))
    print("\n",info)
if __name__=="__main__": main()
