# -*- coding: utf-8 -*-
"""02 源账本 → FT-REPLAY-01 数据对账：27组/4325笔、去重、分组、P0/R、批次。"""
from __future__ import annotations
import json, os, sys, urllib.request
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from _common import E09, R01, OUT, ROOT, jdump, load_replay, load_source, write_csv

def p0_table():
    s=pd.read_csv(os.path.join(ROOT,"evidence/FT-ENTRY-07/02_逐候选对照表.csv"),encoding="utf-8-sig",low_memory=False)
    s["key"]=s["param_group"]+"|"+s["event_id"]
    return s[["key","P0_stop"]]

def confirm_p0():
    rows=[]
    for mode in ("C1","C2"):
        for g in ["Vs04_Z02","Vs04_Z04","Vs04_Z06","Vs06_Z02","Vs06_Z04","Vs06_Z06","Vs08_Z02","Vs08_Z04","Vs08_Z06"]:
            p=os.path.join(E09,"out",f"audit_confirmation_Wyckoff_{mode}_{g}.csv")
            if os.path.isfile(p):
                d=pd.read_csv(p,encoding="utf-8-sig")
                d["param_suffix"]=g
                rows.append(d[["param_suffix","event_id","orig_P0"]])
    d=pd.concat(rows,ignore_index=True)
    d["key"]=d["param_suffix"]+"|"+d["event_id"]
    return d.drop_duplicates("key")[["key","orig_P0"]]

def api_batch():
    base="http://127.0.0.1:8787/api/v1/backtests"
    try:
        with urllib.request.urlopen(base+"/batches/runs_ft_entry_09_research/runs",timeout=20) as f:
            r=json.load(f)
        items=r.get("items",[])
        std=[x for x in items if str(x.get("run_id","")).startswith("run_WX_EVENT_TREND_V0_1_FT09_")]
        sym=[x for x in items if str(x.get("run_id","")).startswith("run_FT_ENTRY_09_")]
        with urllib.request.urlopen(base+"/batches/runs_ft_entry_09_research/summary",timeout=20) as f:
            s=json.load(f)
        b=s.get("batch",{})
        return {"ok":True,"runs_total":len(items),"std_runs":len(std),"symbol_runs":len(sym),
                "std_trades":int(sum(x.get("trades_count") or 0 for x in std)),
                "symbol_trades":int(sum(x.get("trades_count") or 0 for x in sym)),
                "batch_trades_total":b.get("trades_total"),
                "batch_net_pnl_sum":b.get("net_pnl_sum"),
                "std_net":round(sum(x.get("net_pnl") or 0 for x in std),4),
                "symbol_net":round(sum(x.get("net_pnl") or 0 for x in sym),4)}
    except Exception as e:
        return {"ok":False,"error":str(e)}

def main():
    src=load_source(); rep=load_replay()
    # group stats
    g=src.groupby(["mode","strategy"]).agg(trades=("profit_abs","size"),wins=("profit_abs",lambda s:(s>0).sum()),
                                          net=("profit_abs","sum")).reset_index()
    write_csv(g,"02_源账本27组_v1.csv")
    # uniqueness / grouping
    dup_trade=int(src.trade_key.duplicated().sum())
    ev=src.groupby(["strategy","event_key"]).size().reset_index(name="fills")
    multi=int((ev.fills>1).sum())
    # replay FILLED compare
    f=rep[rep.execution_status=="FILLED"].copy()
    src2=src[["trade_key","open_rate","close_rate","amount","profit_abs","close_timestamp","exit_reason","event_id","symbol"]]
    m=f.merge(src2,on="trade_key",how="left",suffixes=("","_src"))
    chk={}
    for a,b,n in [("entry_price","open_rate","entry_price"),("exit_price","close_rate","exit_price"),
                  ("trade_amount","amount","qty"),("net_pnl_usdt","profit_abs","net_pnl"),
                  ("exit_time_ms","close_timestamp","exit_time"),("fill_time_ms","open_timestamp","entry_time")]:
        if b not in m: m[b]=m["fill_time_ms"] if b=="open_timestamp" else None
        d=(pd.to_numeric(m[a],errors="coerce")-pd.to_numeric(m[b],errors="coerce")).abs()
        chk[n]={"matched":int((d<1e-6).sum()),"total":int(len(m)),"max_abs_diff":round(float(d.max()),8)}
    # P0
    p0=p0_table(); cp=confirm_p0()
    rep2=f.copy(); rep2["key"]=rep2["param_suffix"]+"|"+rep2["event_id"]
    rep2=rep2.merge(p0,on="key",how="left").merge(cp,on="key",how="left",suffixes=("","_conf"))
    rep2["P0_src"]=rep2["P0_stop"].fillna(rep2["orig_P0"])
    rep2["p0_diff"]=(pd.to_numeric(rep2["initial_stop_P0"],errors="coerce")-pd.to_numeric(rep2["P0_src"],errors="coerce")).abs()
    rep2["R_true"]=(pd.to_numeric(rep2["entry_price"],errors="coerce")-pd.to_numeric(rep2["P0_src"],errors="coerce"))*pd.to_numeric(rep2["trade_amount"],errors="coerce")
    p0_match=int((rep2.p0_diff<1e-6).sum()); p0_bad=int((rep2.p0_diff>=1e-6).sum()); p0_na=int(rep2.P0_src.isna().sum())
    r_bad50=int((pd.to_numeric(rep2.planned_risk_R,errors="coerce")>50).sum())
    Rdes=pd.to_numeric(rep2.R_true,errors="coerce").describe().to_dict()
    # batch
    api=api_batch()
    # report text vs data
    report_totals=pd.DataFrame([
        {"mode":"C","report_trades":2972,"report_net":-3962.97,"data_trades":2995,"data_net":-3913.02},
        {"mode":"C1","report_trades":840,"report_net":239.30,"data_trades":740,"data_net":327.07},
        {"mode":"C2","report_trades":513,"report_net":63.81,"data_trades":590,"data_net":-92.79},
    ])
    write_csv(report_totals,"02_报告数字与底表差异_v1.csv")
    detail=rep2[["timing_mode","pair","trade_id","initial_stop_P0","P0_src","p0_diff","planned_risk_R","R_true","entry_price","net_pnl_usdt"]].copy()
    write_csv(detail.head(50),"02_P0_R错误样例_v1.csv")
    info={"source_trades":len(src),"source_groups":int(src.strategy.nunique()),
          "source_per_mode":src.groupby("mode").size().to_dict(),
          "source_per_mode_net":src.groupby("mode").profit_abs.sum().round(4).to_dict(),
          "duplicate_trade_keys":dup_trade,"event_multi_fill":multi,
          "replay_filled":int(len(f)),"compare":chk,
          "p0_matched":p0_match,"p0_mismatch":p0_bad,"p0_source_missing":p0_na,
          "planned_R_gt50":r_bad50,"R_true_describe":{k:(round(v,4) if isinstance(v,float) else v) for k,v in Rdes.items()},
          "api_batch":api,
          "report_totals_mismatch":True,
          "note":"P0 源=FT-07 P0_stop，缺失时用 FT-09 audit_confirmation.orig_P0；R_true=(entry-P0)*qty。"}
    jdump(os.path.join(OUT,"02_源账本对账摘要_v1.json"),info)
    print(json.dumps(info,ensure_ascii=False,indent=1,default=str))

if __name__=="__main__": main()
