# ENTRY-AUDIT-02 · 输入冻结清单

- 冻结时刻（UTC）：2026-10-18 07:54:53
- 文件总数：2673（上游 30 · 代码 13 · 行情 2500 · metrics 109 · funding 21）
- 行情体量：1280.1 MB，覆盖 152 个标的的 15m/1h/4h

## 关键文件

| 类别 | 文件 | 字节 | 修改时间(UTC) | sha256 |
| :--- | :--- | ---: | :--- | :--- |
| upstream | `evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.csv` | 182615 | 2026-10-18 03:16:55 | `30c364f1fdd358da2112db468b2b071d…` |
| upstream | `evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.jsonl` | 424896 | 2026-10-18 03:16:55 | `ea7075b06a09f143328e2352524a9066…` |
| upstream | `evidence/FACTOR-ENTRY-01/candidate_features.parquet` | 103421 | 2026-10-18 03:16:06 | `d4c4bdf1bfff5d7cb03b5928a9e94484…` |
| upstream | `evidence/FACTOR-ENTRY-01/candidate_features.csv` | 163295 | 2026-10-18 03:16:06 | `628b1e59bedec5f6778570fbcff86b7c…` |
| upstream | `evidence/FACTOR-ENTRY-01/stage2_candidate_feature_pipeline.py` | 18648 | 2026-10-18 03:01:23 | `46b557926a79026c3ed393419dafdca2…` |
| upstream | `evidence/FACTOR-ENTRY-01/enrich_unified_candidate_dataset.py` | 18095 | 2026-10-18 03:15:05 | `d26d16ef290290ab2892289d6f9b5de2…` |
| upstream | `evidence/FACTOR-ENTRY-01/stage3_ablation_evaluation.py` | 11539 | 2026-10-18 03:16:51 | `53f2eac9d968431f001055cb6bda3945…` |
| upstream | `evidence/FACTOR-ENTRY-01/结论.md` | 11037 | 2026-10-18 03:17:56 | `3d07ef1f567b48c0575787c15c17d180…` |
| upstream | `evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet` | 128321 | 2026-10-18 04:40:26 | `25a3b0cfc29f78f0ef5a1218446e32e0…` |
| upstream | `evidence/FACTOR-ENTRY-03/candidate_features_enriched.csv` | 216768 | 2026-10-18 04:40:26 | `8a0b312a3f049388937fbb582ab52b01…` |
| upstream | `evidence/FACTOR-ENTRY-03/attribution_analysis.py` | 25149 | 2026-10-18 04:39:57 | `a79d7ef41744cdfda569a1b9a095dd0b…` |
| upstream | `evidence/FACTOR-ENTRY-03/evaluation_definitions_and_plan.md` | 5603 | 2026-10-18 04:37:11 | `445b91fb27c0b7126a1fad52fbdcdf50…` |
| upstream | `evidence/FACTOR-ENTRY-03/结论.md` | 12822 | 2026-10-18 04:42:20 | `81d893f776161db3b48da88f815b286f…` |
| upstream | `evidence/FACTOR-ENTRY-03/code_fingerprints.json` | 1568 | 2026-10-18 04:37:17 | `0ba89286a48205a1d401e7642d043ae1…` |
| upstream | `evidence/FACTOR-ENTRY-04/unfilled_reasons.json` | 4591 | 2026-10-18 06:48:04 | `db79b4067361fdd4ac53d498095e87d5…` |
| upstream | `evidence/FACTOR-ENTRY-04/reconciliation_227_to_205.csv` | 76199 | 2026-10-18 06:51:05 | `ab654b7f268f1abd2f1ea626d0c7f84e…` |
| upstream | `evidence/FACTOR-ENTRY-04/reconciliation_227_to_205.json` | 151557 | 2026-10-18 06:51:05 | `536fd14b08cc1f367b83125c175e9cc1…` |
| upstream | `evidence/FACTOR-ENTRY-04/reconciliation_summary.json` | 1070 | 2026-10-18 06:51:05 | `892eeefdee72cde64d582df1e23853ba…` |
| upstream | `evidence/FACTOR-ENTRY-04/reconciliation_and_evidence.md` | 10334 | 2026-10-18 06:50:37 | `3ade98b74d0cbb8a8d63e8600aa64e6d…` |
| upstream | `evidence/FACTOR-ENTRY-04/candidate_evaluations_audit.csv` | 182696 | 2026-10-18 06:51:05 | `06df4452aa79f19ba9995d1382f5a3b1…` |
| upstream | `evidence/FACTOR-ENTRY-04/audit_and_reconcile_04.py` | 21536 | 2026-10-18 06:49:15 | `f2250c2142a0c5bc6b8441348987513e…` |
| upstream | `evidence/FACTOR-ENTRY-04/code_fingerprints.json` | 1313 | 2026-10-18 06:51:11 | `ede96380ea7a61832af6f8aca9ea428b…` |
| upstream | `evidence/FACTOR-ENTRY-04/结论.md` | 9148 | 2026-10-18 06:51:38 | `5174523297469b5ad290b229bee8f365…` |
| upstream | `evidence/FULL_SWEEP_200x100/trades_rich.jsonl` | 147856 | 2026-10-17 22:04:40 | `7676245774387703cb9ed2c3eca26b20…` |
| upstream | `evidence/FULL_SWEEP_200x100/trades.jsonl` | 457832 | 2026-10-17 21:37:20 | `49610aabfe2488517d48645336ece778…` |
| upstream | `evidence/FULL_SWEEP_200x100/runs.jsonl` | 6993258 | 2026-10-17 21:37:20 | `aa97594722ad722bc2acdc8c44873718…` |
| upstream | `evidence/FULL_SWEEP_200x100/plan.json` | 315142 | 2026-10-17 19:57:32 | `b9f901d2484505b1c8783dde094afe97…` |
| upstream | `evidence/FULL_SWEEP_200x100/statistics.json` | 704 | 2026-10-17 22:06:35 | `104a239680d9d16a42379bc566ca4d91…` |
| upstream | `evidence/FULL_SWEEP_200x100/结论.md` | 8558 | 2026-10-17 22:06:42 | `a604c81fd838126f9d49a58c161885ad…` |
| upstream | `run_full_sweep_200x100.py` | 24769 | 2026-10-17 21:58:22 | `c6219858690d2aec3c9b65973722b3f5…` |
| code | `策略/WyckoffMaster/event_trend_v0_1/runner.py` | 33589 | 2026-10-17 19:56:44 | `d9e881589b6fdcd65bb326191dcb56db…` |
| code | `策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py` | 29445 | 2026-10-17 02:35:44 | `fc956b5502c5ff977402d34a0678955a…` |
| code | `策略/WyckoffMaster/event_trend_v0_1/state_machine.py` | 27321 | 2026-10-16 15:41:48 | `21c875a849331ce04c57c85efcacbad9…` |
| code | `策略/WyckoffMaster/event_trend_v0_1/contracts.py` | 8848 | 2026-10-16 16:34:00 | `6895e0949f9b683cd3280e299dfc40f9…` |
| code | `策略/WyckoffMaster/event_trend_v0_1/config.py` | 4758 | 2026-10-16 17:25:46 | `29a42d98f7b7c5302b7adc0cef20b4ea…` |
| code | `策略/WyckoffMaster/event_trend_v0_1/features.py` | 4534 | 2026-10-16 15:14:28 | `4f49073c91ef68398ea8617350880265…` |
| code | `策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py` | 52564 | 2026-10-17 04:12:58 | `97b9842de9a2e7c843416aa919841e81…` |
| code | `策略/WyckoffMaster/trading_core/engine.py` | 60728 | 2026-10-17 08:09:09 | `8cf36bc5e09f6e1c68f30e96a0f42b39…` |
| code | `策略/WyckoffMaster/trading_core/policy.py` | 10309 | 2026-10-16 16:30:49 | `6303d06ae6fea38be8c0a3fe5304742a…` |
| code | `策略/WyckoffMaster/trading_core/fill.py` | 18376 | 2026-10-17 02:43:15 | `f050652c0d83b41a8a60f4d088c7864d…` |
| code | `策略/WyckoffMaster/trading_core/risk.py` | 7377 | 2026-10-16 07:44:07 | `ad8e14d56e8e6cbf4b8e664bdface179…` |
| code | `策略/WyckoffMaster/trading_core/rejects.py` | 6519 | 2026-10-17 08:08:25 | `a8cae2c214a06cf7f723892fc6e178ac…` |
| code | `策略/WyckoffMaster/trading_core/ledger.py` | 4723 | 2026-10-16 07:44:35 | `c813e5842e8d3d6b1f6f00419099a46f…` |

> 完整 64 位哈希见 `00_freeze_manifest.json`。
