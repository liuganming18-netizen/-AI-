# KLINE-EVENT-FULLCHAIN-01 source inventory

Read-only source tracing against commit `4002bde0b5aed24b2c69156bda44cf5fe539a45d`.

| Event | Producer / persistent source | Time and price fields | Current status |
|---|---|---|---|
| Box | `策略/WyckoffMaster/event_trend_v0_1/state_machine.py::check_and_form_box`; `runner.py` writes `events.jsonl`; `EventDossier.box` stores `BoxVersion` | `formation_time_ms`, `high_h`, `low_l`, `timeframe`; dossier `ended_at_ms` | Available for runs retaining `events.jsonl`; map is wired to chart response. Effective start is formation time; absent end remains null. |
| Sweep low | State machine writes `sweep_time_ms`, `sweep_low_s` into dossier | Actual 1h close time and observed low; transition `available_at_ms` | Available and mapped. |
| Sweep high | No corresponding field or transition in the long-only event model | None | Not produced by this strategy; no marker emitted. |
| Retest | State machine dossier `retest_time_ms`, `retest_low_lr`; transition ledger | Event close and transition availability | Available and mapped. |
| Breakout | Dossier `breakout_time_ms`, `breakout_close_price`; candidate snapshot | Event close / price; candidate `available_at_ms` | Available and mapped. Candidate generation remains a separate signal event. |
| Signal trigger | `candidates.jsonl` snapshot | `snapshot_time_ms`, `breakout_close`, `available_at_ms`, `candidate_id` | Available and mapped for replay runs with candidate files. |
| Signal invalidation | `events.jsonl[].history` state transition to `INVALIDATED` | `trigger_bar_close_ms`, `available_at_ms`, `reason`, `transition_id` | Available and mapped when retained in the run dossier. |
| Entry / close | `positions.jsonl` / `trades.jsonl` via `ledger_links.LedgerFacts`; chart trade serializer | Actual `entry_time_ms`/`entry_price`, `exit_time_ms`/`exit_price`, authoritative `trade_id` | Available and mapped from chart trade ledger rows. |

Market signal rows expose `event_time`, `confirmed_at`, and `available_at` under `live_api.py`, but the current market annotations query uses `confirmed_at` as its window filter and labels it “信号确认”. It does not provide a distinct trigger-versus-confirmation time in all records or lifecycle invalidation transition history. Market entry and close require the connected paper/live execution ledger and account identity; they are not part of the market candle response. The current `paper_trades` route is read-only and closed-trade-only; current positions may be unavailable when the successor execution core is disconnected.

`adapter.py::annotations_for` returns frozen ranges with `start_time: null`, no sweep entries, and no entry/close records. This function is not used as a source for the backtest event mapping.
