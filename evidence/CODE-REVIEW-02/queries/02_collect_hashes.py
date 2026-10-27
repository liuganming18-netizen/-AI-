# -*- coding: utf-8 -*-
"""CODE-REVIEW-02 · 变更/新增文件哈希清单（脚本计算，落盘；不手写）。

覆盖：本单新增 + 修改的**全部**生产/工具/证据文件（前端 5、后端 5、批量脚本 12、
登记工具 1、日志模块 1、证据目录报告类）。哈希 = 文件当前内容 SHA-256。
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "CODE-REVIEW-02" / "05_文件清单与哈希.csv"

FILES = [
    # ── 后端（改） ──
    ("框架/backend/backtest_store.py", "改", "P0-1/2/3/5 合成诚实化 + register_external_run 写入口 + STATUS_UNKNOWN"),
    ("框架/backend/backtest_runner.py", "改", "P1-6/7 execute_run 运行级日志四出口 + 逐笔开关"),
    ("框架/backend/backtest_api.py", "改", "P1-8/9 图表兜底 null + 坏行计数日志；registered/status_basis 透传"),
    ("框架/backend/backtest_chart.py", "改", "同类 R7：equity cash/unrealized 缺字段 null"),
    ("框架/backend/run_tests.sh", "改", "测试隔离：BT_RUN_LOG 默认写 /tmp"),
    # ── 新增 ──
    ("框架/backend/bt_run_log.py", "新", "P1-6/7 运行日志单一实现（stdout + logs/backtest_runs.log 双写）"),
    ("工具_登记回测运行.py", "新", "P0-4 批量 run 登记工具（只走 store 写入口，原子、拒绝覆盖）"),
    # ── 批量脚本（改，插入 register_batch_run） ──
    ("run_batch_2025q4_100.py", "改", "P0-4 登记调用"),
    ("run_batch_2025q4_100_batch2.py", "改", "P0-4 登记调用"),
    ("run_batch_2026_aug_20.py", "改", "P0-4 登记调用"),
    ("run_batch_2026_aug_100.py", "改", "P0-4 登记调用"),
    ("run_batch_2026_sep_oct_50.py", "改", "P0-4 登记调用"),
    ("run_batch_round3_50.py", "改", "P0-4 登记调用"),
    ("run_batch_round4_all_remain.py", "改", "P0-4 登记调用"),
    ("run_batch_round5_100.py", "改", "P0-4 登记调用"),
    ("run_batch_round6_100.py", "改", "P0-4 登记调用"),
    ("run_clean_batch_test.py", "改", "P0-4 登记调用"),
    ("run_expand_batch_test.py", "改", "P0-4 登记调用"),
    ("run_random_50_batch_test.py", "改", "P0-4 登记调用"),
    # ── 前端（改） ──
    ("UI界面/web/src/utils/runMeta.ts", "改", "statusLabel/statusTone 增 unknown=未登记、interrupted=已中断"),
    ("UI界面/web/src/api/backtestReview.ts", "改", "BtRunListItem/BtRunDetail/BtBatchRun 增 registered/status_basis/record_note"),
    ("UI界面/web/src/components/BacktestWorkbench.tsx", "改", "留存记录清单状态列走 statusLabel + 未登记徽标；批次映射补 registered/record_note（原白名单丢字段）"),
    ("UI界面/web/src/api/labBacktest.ts", "改", "LabRunSummary 增 registered/status_basis/record_note"),
    # ── 旧组件（当前未被挂载的遗留代码，改动同步保留，不影响线上页面） ──
    ("UI界面/web/src/components/backtest/RunHeader.tsx", "改", "未登记徽标（registered===false）；组件当前无引用方"),
    ("UI界面/web/src/components/backtest/RunListPanel.tsx", "改", "未登记小徽标 + 注释更新；组件当前无引用方"),
    ("UI界面/web/src/components/backtest/RunInfoCard.tsx", "改", "注释更新（created_at 已非读时兜底）；组件当前无引用方"),
    # ── 批次端点透传 ──
    ("框架/backend/backtest_batches.py", "改", "_run_facts 增 has_run_json；runs_rows 透传 registered"),
    # ── 证据目录（新） ──
    ("evidence/CODE-REVIEW-02/00_报告.md", "新", "本单回执"),
    ("evidence/CODE-REVIEW-02/queries/01_verify_fixes.py", "新", "20 项核验脚本（rc=0）"),
    ("evidence/CODE-REVIEW-02/queries/02_collect_hashes.py", "新", "本哈希清单采集脚本"),
    ("evidence/CODE-REVIEW-02/logs/01_verify_fixes.log", "新", "核验实跑输出"),
    ("evidence/CODE-REVIEW-02/out/01_verify_results.json", "新", "核验结果（20/20）"),
    ("evidence/CODE-REVIEW-02/out/02_api_snapshot_after_fix.txt", "新", "重启后 API 实测存档"),
    ("evidence/CODE-REVIEW-02/out/check_report.md", "新", "门禁实跑（P0=1/P1=5/P2=18）"),
    ("evidence/CODE-REVIEW-02/out/check_report.json", "新", "门禁实跑 JSON"),
    ("evidence/CODE-REVIEW-02/03_实际命令.md", "新", "复现命令"),
    ("evidence/CODE-REVIEW-02/queries/03_verify_page_badges.mjs", "新", "页面徽标只读验收脚本（playwright）"),
    ("evidence/CODE-REVIEW-02/out/03_page_verify.txt", "新", "页面验收输出（lab-run-unregistered 544/544）"),
    ("evidence/CODE-REVIEW-02/out/backtest_run_tab.png", "新", "按单 run 页签验收截图"),
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    rows, missing = [], []
    for rel, role, note in FILES:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        rows.append({"path": rel, "role": role, "bytes": p.stat().st_size,
                     "sha256": sha256(p), "note": note})
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "role", "bytes", "sha256", "note"])
        w.writeheader()
        w.writerows(rows)
    print(f"清单 {len(rows)} 行；缺失 {len(missing)}：{missing}")
    print(f"输出：{OUT}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
