import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { fetchPaperLedger } from '../src/api/liveTrading';
import { PositionPanel } from '../src/components/PositionPanel';
async function main() {
 const originalFetch = globalThis.fetch;
 try {
  globalThis.fetch = async () => new Response(JSON.stringify({items: ['3a', '3b'].map((arm, i) => ({arm, account_id: `paper43_${arm}`, initial_equity: 5000, cash: 5000, positions: [], pending: [{pending_id: 1, symbol: 'TESTFIXUSDT', direction: i ? 'short' : 'long', status: 'pending', requested_at: 1, earliest_fill_ms: 9999999999999}]}))}), {headers: {'Content-Type': 'application/json'}});
  const ledger = await fetchPaperLedger();
  assert.deepEqual(ledger.pending.map(p => [p.arm, p.direction]), [['3a', 'long'], ['3b', 'short']]);
  const html = renderToStaticMarkup(<PositionPanel paperAccounts={ledger.accounts} paperPositions={[]} paperPending={ledger.pending} paperMissingReason={null} liveAccounts={[]} liveMissingReason={null} orderBusy={false} onSelectTokenSymbol={() => undefined} onClosePosition={() => undefined} onOpenQuickPosition={() => undefined} isGateRunning={false} />);
  for (const text of ['data-arm="3a"', 'data-arm="3b"', '方向: 多', '方向: 空']) assert.ok(html.includes(text), text);
  console.log('PASS: same-symbol/same-id orders retain both arms and directions through API mapping and rendering');
 } finally { globalThis.fetch = originalFetch; }
}
main().catch(e => { console.error(e); process.exitCode = 1; });
