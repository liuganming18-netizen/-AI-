# -*- coding: utf-8 -*-
"""01 锁定 FT-ENTRY-09 源账本与 FT-REPLAY-01 批次/复盘索引。"""
from __future__ import annotations
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from _common import E09, R01, OUT, ROOT, jdump, load_source, sha256_file, write_csv

def walk_hashes(base, skip_dirs=("data","__pycache__")):
    rows=[]
    for root,dirs,files in os.walk(base):
        dirs[:]=[d for d in dirs if d not in skip_dirs and not d.startswith("backtest_results_")]
        for fn in files:
            p=os.path.join(root,fn); rel=os.path.relpath(p,ROOT).replace(os.sep,"/")
            if rel.endswith(".pyc"): continue
            rows.append({"file":rel,"size_bytes":os.path.getsize(p),"sha256":sha256_file(p)})
    return pd.DataFrame(rows).sort_values("file")

def main():
    e=walk_hashes(E09); r=walk_hashes(R01)
    write_csv(e,"01_FT09源账本指纹_v1.csv")
    write_csv(r,"01_FTREPLAY01指纹_v1.csv")
    src=load_source()
    groups=src.groupby(["mode","strategy"]).size().reset_index(name="trades")
    info={"ft09_files":len(e),"ft09_bytes":int(e.size_bytes.sum()),
          "r01_files":len(r),"r01_bytes":int(r.size_bytes.sum()),
          "source_trades":int(len(src)),"source_groups":int(src.strategy.nunique()),
          "per_mode":src.groupby("mode").size().to_dict(),
          "per_mode_net":src.groupby("mode").profit_abs.sum().round(4).to_dict(),
          "group_counts":groups.to_dict("records")}
    jdump(os.path.join(OUT,"01_输入锁定摘要_v1.json"),info)
    print(f"FT-ENTRY-09 {len(e)} files; FT-REPLAY-01 {len(r)} files; source {len(src)} trades/{src.strategy.nunique()} groups")
    print(info["per_mode"], info["per_mode_net"])
if __name__=="__main__": main()
