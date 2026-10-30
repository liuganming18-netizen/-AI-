# Changed files

- `框架/backend/backtest_chart.py` — map retained state-machine dossiers and candidates to read-only normalized chart events.
- `框架/backend/backtest_api.py` — attach time-window-filtered structure and actual ledger trade events to chart responses.
- `框架/backend/adapter.py` — market signal annotations use stored `event_time`; preserve `confirmed_at` as availability metadata.
- `框架/backend/tests/test_kline_event_fullchain.py` — source mapping test.
- `UI界面/web/src/api/labBacktest.ts` — normalized event response types.
- `UI界面/web/src/components/LabChartPanel.tsx` — event markers, click detail, and timeline-to-chart positioning.
- `UI界面/web/src/components/LightweightChart.tsx` — click-time callback without chart remount.
- `UI界面/web/src/api/adapters.ts` — prevent internal breakout/retest annotations from appearing in market charts.
- `evidence/KLINE-EVENT-FULLCHAIN-01/` — source inventory, event contract, validation outcomes, jitter reference, sync state, and integrity manifest.
