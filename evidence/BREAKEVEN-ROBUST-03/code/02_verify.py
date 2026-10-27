"""
BREAKEVEN-ROBUST-03 自动化验证与指纹固化脚本
验证固定仓位重算结果的算术完整性、恒等式闭环与版本指纹
"""

import os
import json
import hashlib
from decimal import Decimal
import pandas as pd

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
EVIDENCE_DIR = os.path.join(BASE_DIR, "evidence/BREAKEVEN-ROBUST-03")
OUT_DIR = os.path.join(EVIDENCE_DIR, "out")

def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def verify_all():
    print("=== BREAKEVEN-ROBUST-03 自检与闭环核验 ===")
    
    # 1. 验证输入版本指纹
    fingerprint_path = os.path.join(OUT_DIR, "输入版本指纹.json")
    assert os.path.exists(fingerprint_path), "输入版本指纹文件不存在"
    with open(fingerprint_path, "r", encoding="utf-8") as f:
        fp = json.load(f)
    print(f"[PASS] 读取输入版本指纹成功: 包含 {len(fp)} 项输入依赖")

    # 2. 读取 independent_results_fixed.json
    results_path = os.path.join(OUT_DIR, "independent_results_fixed.json")
    with open(results_path, "r", encoding="utf-8") as f:
        res = json.load(f)
    
    ev = res["event_201_fixed_margin"]
    # 算术对账核验
    tot_a = Decimal(str(ev["A_net_U"]))
    tot_1r = Decimal(str(ev["B_1R_net_U"]))
    tot_2r = Decimal(str(ev["C_2R_net_U"]))
    delta_1r = Decimal(str(ev["B_1R_delta_U"]))
    delta_2r = Decimal(str(ev["C_2R_delta_U"]))

    assert round(tot_1r - tot_a, 6) == round(delta_1r, 6), f"1R 净差恒等式不成立: {tot_1r - tot_a} != {delta_1r}"
    assert round(tot_2r - tot_a, 6) == round(delta_2r, 6), f"2R 净差恒等式不成立: {tot_2r - tot_a} != {delta_2r}"
    print(f"[PASS] 201事件级总额恒等式成立: A={tot_a:.6f}, 1R={tot_1r:.6f}, 2R={tot_2r:.6f}, delta_1R={delta_1r:.6f}, delta_2R={delta_2r:.6f}")

    # 3. 验证 1R 分类及累加贡献
    cats = ev["1R_breakdown"]
    assert cats["improved_count"] == 11, f"改善笔数异常: {cats['improved_count']}"
    assert cats["worsened_count"] == 16, f"恶化笔数异常: {cats['worsened_count']}"
    assert cats["unchanged_count"] == 174, f"不变笔数异常: {cats['unchanged_count']}"
    assert cats["total_changed_count"] == 27, f"变化总笔数异常: {cats['total_changed_count']}"
    assert cats["improved_count"] + cats["worsened_count"] + cats["unchanged_count"] == 201, "分类总数不为201"

    sum_pos = Decimal(str(cats["improved_sum_U"]))
    sum_neg = Decimal(str(cats["worsened_sum_U"]))
    assert round(sum_pos + sum_neg, 6) == round(delta_1r, 6), f"正负贡献求和不等于1R总净差: {sum_pos + sum_neg} != {delta_1r}"
    print(f"[PASS] 1R变化笔数与贡献闭环: 改善11笔(+{sum_pos:.6f}U), 恶化16笔({sum_neg:.6f}U), 不变174笔, 合计变化27笔, 正负贡献和严格等于总差额")

    # 4. 验证逐笔表
    trades_csv = os.path.join(OUT_DIR, "逐笔贡献与变化_固定仓位.csv")
    df_trades = pd.read_csv(trades_csv)
    assert len(df_trades) == 201, f"逐笔表行数不等于201: {len(df_trades)}"

    # 验证 OPUSDT 记录
    op_trade = df_trades[(df_trades["symbol"] == "OPUSDT") & (df_trades["entry_time_utc"].str.startswith("2024-01-12 14:15"))]
    assert len(op_trade) == 1, "未找到 OPUSDT 2024-01-12 14:15 交易"
    op_row = op_trade.iloc[0]
    op_pnl_a = Decimal(str(op_row["A_net_U"]))
    op_pnl_1r = Decimal(str(op_row["B_1R_net_U"]))
    op_delta_1r = Decimal(str(op_row["B_1R_delta_U"]))
    assert round(op_pnl_a, 6) == round(op_pnl_1r, 6), f"固定仓位下 OPUSDT A与1R应相等: {op_pnl_a} vs {op_pnl_1r}"
    assert round(op_delta_1r, 6) == Decimal("0"), f"固定仓位下 OPUSDT 1R差额应为0: {op_delta_1r}"
    assert op_row["1R_category"] == "不变", f"OPUSDT 状态应为不变: {op_row['1R_category']}"
    print(f"[PASS] OPUSDT 2024-01-12 14:15 核验通过: A={op_pnl_a:.6f}, 1R={op_pnl_1r:.6f}, delta={op_delta_1r:.6f}, 状态=不变")

    # 5. 验证 2R 唯一增益
    lunc_trade = df_trades[(df_trades["symbol"] == "1000LUNCUSDT") & (df_trades["entry_time_utc"].str.startswith("2023-06-04 03:15"))]
    assert len(lunc_trade) == 1, "未找到 1000LUNCUSDT 2023-06-04 03:15 交易"
    lunc_row = lunc_trade.iloc[0]
    lunc_contrib = Decimal(str(lunc_row["C_2R_delta_U"]))
    assert round(lunc_contrib, 6) == Decimal("3.048029"), f"1000LUNCUSDT 2R贡献异常: {lunc_contrib}"
    
    other_2r = df_trades[~((df_trades["symbol"] == "1000LUNCUSDT") & (df_trades["entry_time_utc"].str.startswith("2023-06-04 03:15")))]
    max_other_2r = other_2r["C_2R_delta_U"].abs().max()
    assert Decimal(str(max_other_2r)) == Decimal("0"), f"2R 其他交易存在非零贡献: {max_other_2r}"
    print(f"[PASS] 2R 唯一增益交易核验通过: 仅 1000LUNCUSDT 产生 +{lunc_contrib:.6f}U，其余 200 笔贡献严格为 0")

    # 6. 验证极值敏感性与逐笔移除
    sens = ev["sensitivity"]
    assert round(Decimal(str(sens["1R_without_max_positive_delta_U"])), 6) == Decimal("-63.545958")
    assert round(Decimal(str(sens["1R_without_max_negative_delta_U"])), 6) == Decimal("3.640775")
    assert sens["1R_reversed_count"] == 1
    print(f"[PASS] 敏感性核验通过: 移除MOVR后差额=-63.545958U, 移除PUMP后差额=+3.640775U, 变号场景数仅1个")

    # 7. 生成 versions.json (包含所有输出与输入哈希)
    all_files = {}
    for root, dirs, files in os.walk(EVIDENCE_DIR):
        for f in sorted(files):
            if f in ["versions.json"]:
                continue
            fpath = os.path.join(root, f)
            relpath = os.path.relpath(fpath, BASE_DIR)
            all_files[relpath] = {
                "sha256": sha256_file(fpath),
                "size_bytes": os.path.getsize(fpath)
            }
    
    # 补充关键输入文件哈希
    for in_rel in [
        "evidence/BREAKEVEN-02/out/paired_fixed.csv",
        "evidence/BREAKEVEN-02/out/duplicate_groups.json",
        "evidence/BREAKEVEN-01/out/paired_trades.csv"
    ]:
        full_in = os.path.join(BASE_DIR, in_rel)
        if os.path.exists(full_in):
            all_files[f"[INPUT] {in_rel}"] = {
                "sha256": sha256_file(full_in),
                "size_bytes": os.path.getsize(full_in)
            }

    versions_path = os.path.join(OUT_DIR, "versions.json")
    with open(versions_path, "w", encoding="utf-8") as f:
        json.dump(all_files, f, indent=2, ensure_ascii=False)
    print(f"[PASS] 全量版本哈希固化至: {versions_path} (共 {len(all_files)} 个文件)")

    print("=== 全部自检通过，无一异常 ===")

if __name__ == "__main__":
    verify_all()
