# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 公共只读工具。"""
from __future__ import annotations
import hashlib, json, os, re
import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
E09 = os.path.join(ROOT, "evidence/FT-ENTRY-09")
R01 = os.path.join(ROOT, "evidence/FT-REPLAY-01")
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
LOGS = os.path.join(AUDIT, "logs")

GRIDS = ["Vs04_Z02","Vs04_Z04","Vs04_Z06","Vs06_Z02","Vs06_Z04","Vs06_Z06","Vs08_Z02","Vs08_Z04","Vs08_Z06"]

def sha256_file(path):
    if not os.path.isfile(path): return None
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for c in iter(lambda:f.read(1<<20),b""): h.update(c)
    return h.hexdigest()

def write_csv(df,name):
    os.makedirs(OUT,exist_ok=True); p=os.path.join(OUT,name)
    df.to_csv(p,index=False,encoding="utf-8-sig"); return p

def jdump(path,obj):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2,default=str)
    return path

def load_source():
    rows=[]
    for mode in ("C","C1","C2"):
        for g in GRIDS:
            strat=f"Wyckoff_{mode}_{g}"
            p=os.path.join(E09,"out",f"trades_{strat}.csv")
            if not os.path.isfile(p): continue
            d=pd.read_csv(p,encoding="utf-8-sig")
            if d.empty: continue
            d["strategy"]=strat; d["mode"]=mode; d["grid"]=g
            rows.append(d)
    d=pd.concat(rows,ignore_index=True)
    d["event_id"]=d["enter_tag"].astype(str).str.replace(r"_\d+$","",regex=True).str.extract(r"(ev_FT_ENTRY_02_binance_[A-Z0-9]+_\d+)")[0]
    d["symbol"]=d["pair"].astype(str).str.split("/").str[0]
    d["event_key"]=d["symbol"]+"|"+d["event_id"]
    d["trade_key"]=d["strategy"]+"|"+d["pair"]+"|"+d["open_timestamp"].astype("int64").astype(str)
    return d

def load_replay():
    p=os.path.join(R01,"out","00_ft_entry_09_full_replay_records.csv")
    d=pd.read_csv(p,encoding="utf-8-sig",low_memory=False)
    d["strategy"]=d["run_id"].astype(str).str.replace("run_FT_ENTRY_09_","",regex=False)
    d["trade_key"]=d["strategy"]+"|"+d["pair"]+"|"+pd.to_numeric(d["fill_time_ms"],errors="coerce").fillna(-1).astype("int64").astype(str)
    return d
