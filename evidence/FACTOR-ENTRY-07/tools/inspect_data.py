#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np

df_fe06 = pd.read_csv("evidence/FACTOR-ENTRY-06/reconstructed_candidate_features.csv")
df_ea03_id = pd.read_csv("evidence/ENTRY-AUDIT-03/03_identity_full.csv")

merged = pd.merge(df_fe06, df_ea03_id, on="candidate_id", suffixes=("_fe06", "_ea03"))
filled = merged[merged["is_filled"] == True].copy()
print("Total filled:", len(filled))

diff_net = (filled["net_pnl_fe06"] - filled["net_pnl_ea03"]).abs()
print("Max abs net_pnl diff:", diff_net.max())
print("Number of rows with diff > 1e-4:", (diff_net > 1e-4).sum())

print("fee_paid sum (EA03):", filled["fee_paid"].sum())
print("funding_cost sum (EA03):", filled["funding_cost"].sum())
print("gross_pnl sum (EA03):", filled["gross_pnl"].sum())
print("net_pnl_ea03 sum:", filled["net_pnl_ea03"].sum())
print("net_pnl_fe06 sum:", filled["net_pnl_fe06"].sum())

# 检查符号翻转（Sign change）
flip_to_loss = filled[(filled["net_pnl_fe06"] > 0) & (filled["net_pnl_ea03"] <= 0)]
print(f"\nFlipped from Win (>0) to Loss (<=0): {len(flip_to_loss)}")
for idx, r in flip_to_loss.iterrows():
    sym = r["symbol_fe06"]
    cid = r["candidate_id"]
    n06 = r["net_pnl_fe06"]
    n03 = r["net_pnl_ea03"]
    fee = r["fee_paid"]
    funding = r["funding_cost"]
    mfe_r = r["mfe_r"]
    cat06 = r["category_standard_mixed"]
    print(f"  {sym:12s} cid={cid} net_06={n06:+.4f} net_03={n03:+.4f} fee={fee:.4f} funding={funding:+.4f} mfe_r={mfe_r:.4f} cat06={cat06}")

flip_to_win = filled[(filled["net_pnl_fe06"] <= 0) & (filled["net_pnl_ea03"] > 0)]
print(f"\nFlipped from Loss (<=0) to Win (>0): {len(flip_to_win)}")
for idx, r in flip_to_win.iterrows():
    sym = r["symbol_fe06"]
    cid = r["candidate_id"]
    n06 = r["net_pnl_fe06"]
    n03 = r["net_pnl_ea03"]
    fee = r["fee_paid"]
    funding = r["funding_cost"]
    mfe_r = r["mfe_r"]
    cat06 = r["category_standard_mixed"]
    print(f"  {sym:12s} cid={cid} net_06={n06:+.4f} net_03={n03:+.4f} fee={fee:.4f} funding={funding:+.4f} mfe_r={mfe_r:.4f} cat06={cat06}")
