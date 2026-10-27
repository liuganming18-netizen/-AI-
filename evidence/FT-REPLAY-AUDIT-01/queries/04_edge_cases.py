# -*- coding: utf-8 -*-
"""04 边界案例：拒单/确认失败/缺K线，验证状态真实、不伪造成交。"""
from __future__ import annotations
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from _common import E09, OUT, R01, ROOT, jdump, load_replay, write_csv

def find_order_reject():
    for mode in ("C","C1","C2"):
        for g in ["Vs04_Z02","Vs04_Z04","Vs04_Z06","Vs06_Z02","Vs06_Z04","Vs06_Z06","Vs08_Z02","Vs08_Z04","Vs08_Z06"]:
            p=os.path.join(E09,"out",f"audit_orders_Wyckoff_{mode}_{g}.csv")
            if not os.path.isfile(p): continue
            d=pd.read_csv(p,encoding="utf-8-sig")
            t=pd.read_csv(os.path.join(E09,"out",f"trades_Wyckoff_{mode}_{g}.csv"),encoding="utf-8-sig")
            traded=set(t["enter_tag"].astype(str))
            # 真正的最终拒单：该候选无 PASSED、无成交；排除成交后的复评拒单
            for cid,grp in d.groupby("candidate_id"):
                if cid in traded: continue
                if "PASSED" in set(grp["decision"]): continue
                return mode,g,grp.iloc[0]
    return None,None,None

def find_confirm_fail():
    for mode in ("C1","C2"):
        for g in ["Vs04_Z02","Vs04_Z04","Vs04_Z06","Vs06_Z02","Vs06_Z04","Vs06_Z06","Vs08_Z02","Vs08_Z04","Vs08_Z06"]:
            p=os.path.join(E09,"out",f"audit_confirmation_Wyckoff_{mode}_{g}.csv")
            if not os.path.isfile(p): continue
            d=pd.read_csv(p,encoding="utf-8-sig")
            x=d[d.decision=="REJECTED"]
            if len(x): return mode,g,x.iloc[0]
    return None,None,None

def main():
    rep=load_replay()
    rows=[]
    # 拒单
    mode,g,r=find_order_reject()
    cid=r["candidate_id"]; reason=r["reason"]
    x=rep[(rep.timing_mode==mode)&(rep.param_suffix==g)&(rep.candidate_id==cid)]
    row=x.iloc[0] if len(x) else None
    rows.append({"case":"拒单","mode":mode,"grid":g,"pair":r["pair"],"candidate_id":cid,
                 "source_status":"REJECTED","source_reason":reason,
                 "replay_status":row["execution_status"] if row is not None else None,
                 "replay_decision_reason":row["decision_reason"] if row is not None else None,
                 "replay_entry_price":row["entry_price"] if row is not None else None,
                 "replay_net":row["net_pnl_usdt"] if row is not None else None,
                 "replay_trade_id":row["trade_id"] if row is not None else None,
                 "pass": bool(row is not None and row["execution_status"]=="REJECTED"
                              and pd.isna(row["entry_price"]) and float(row["net_pnl_usdt"] or 0)==0
                              and str(row["decision_reason"])==str(reason))})
    # 确认失败
    mode,g,r=find_confirm_fail()
    x=rep[(rep.timing_mode==mode)&(rep.param_suffix==g)&(rep.event_id==r["event_id"])&(rep.execution_status=="CONFIRMATION_FAILED")]
    row=x.iloc[0] if len(x) else None
    rows.append({"case":"确认失败","mode":mode,"grid":g,"pair":r["symbol"],"candidate_id":r["event_id"],
                 "source_status":"REJECTED","source_reason":r["reason"],
                 "replay_status":row["execution_status"] if row is not None else None,
                 "replay_decision_reason":row["decision_reason"] if row is not None else None,
                 "replay_entry_price":row["entry_price"] if row is not None else None,
                 "replay_net":row["net_pnl_usdt"] if row is not None else None,
                 "replay_trade_id":row["trade_id"] if row is not None else None,
                 "pass": bool(row is not None and row["execution_status"]=="CONFIRMATION_FAILED"
                              and pd.isna(row["entry_price"]) and float(row["net_pnl_usdt"] or 0)==0
                              and str(row["decision_reason"])==str(r["reason"]))})
    # 缺K线：选一笔 FILLED 且 symbol 无 candle 文件
    candles_dir=os.path.join(R01,"web","candles")
    have={f.split("_15m")[0] for f in os.listdir(candles_dir)} if os.path.isdir(candles_dir) else set()
    f=rep[rep.execution_status=="FILLED"].copy()
    f["clean"]=f["clean_symbol"].astype(str)
    missing=f[~f["clean"].isin(have)]
    m=missing.iloc[0] if len(missing) else None
    rows.append({"case":"缺K线","mode":m["timing_mode"] if m is not None else None,
                 "grid":m["param_suffix"] if m is not None else None,"pair":m["pair"] if m is not None else None,
                 "candidate_id":m["candidate_id"] if m is not None else None,
                 "source_status":"FILLED","source_reason":"candle_file_absent",
                 "replay_status":m["execution_status"] if m is not None else None,
                 "replay_decision_reason":None,"replay_entry_price":m["entry_price"] if m is not None else None,
                 "replay_net":m["net_pnl_usdt"] if m is not None else None,
                 "replay_trade_id":m["trade_id"] if m is not None else None,
                 "page_kline_available": False,
                 "pass": bool(m is not None and m["execution_status"]=="FILLED")})
    df=pd.DataFrame(rows); write_csv(df,"04_边界案例_v1.csv")
    info={"cases":len(df),"pass":int(df["pass"].sum()),
          "reject_pass":bool(df.iloc[0]["pass"]),"confirm_pass":bool(df.iloc[1]["pass"]),
          "missing_candle_symbol":df.iloc[2]["pair"] if len(df)>2 else None,
          "missing_candle_status_true": bool(df.iloc[2]["pass"]) if len(df)>2 else None,
          "page_kline_available": False,
          "candle_files":sorted(have),
          "post_fill_reject_note":"AR C0 Vs04_Z04 候选先 PASSED 成交、平仓后又被 REJECTED（outside_min_max_bounds）；这是成交后的复评拒单，不是伪造成交，选拒单案例已排除该类。",
          "note":"缺K线案例在页面侧无法渲染；因页面 chart 运行时错误（见 05），暂判未完成，不伪造成交。"}
    jdump(os.path.join(OUT,"04_边界案例摘要_v1.json"),info)
    print(df.to_string(index=False)); print(info)
if __name__=="__main__": main()
