/**
 * PR2-WO-01 · DOM 行为夹具（只用于本工单测试，不进生产路由）
 *
 * 为什么需要：`resolveObservePrice` / `dedupeRatios` 的单元测试只证明**判定逻辑**对，
 * 证明不了「界面上真的换了文案」。这里把夹具作为 props 灌进**真实组件** `ObservePanel`，
 * 由 `display-semantics.dom.test.mjs` 用 Chromium 断言渲染出来的文字。
 *
 * 夹具全部是后端观察区载荷的真实形状（`unified_runtime.py` observe_events），
 * 价格/时刻固定，避免断言随真实数据漂移。
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import ObservePanel from '../src/components/ObservePanel';
import type { ObserveEvidenceValue, ObserveItem, ObserveView } from '../src/api/liveTrading';

const ratio = (key: string, value: number | null): ObserveEvidenceValue => ({
  key,
  label: key,
  value,
  atUtc: '2026-10-28T16:00:00Z',
  lagSec: 60,
  fromLatestRow: true,
  missing: null,
});

const baseItem = (over: Partial<Record<string, unknown>>): ObserveItem =>
  ({
    symbol: 'BTCUSDT',
    state: 'BOX',
    stateCn: '箱体观察',
    nextGate: 'SWEEP',
    createdUtc: '2026-10-26T00:00:00Z',
    updatedUtc: '2026-10-28T15:00:00Z',
    ageHours: 63,
    lastBarUtc: '2026-10-28T16:00:00Z',
    dataReady: true,
    box: { h: 70000, l: 65000, a: 900, er: 0.3, widthAtr: 5.5 },
    satisfied: [],
    unmet: [],
    pendingEval: [],
    deadlineUtc: null,
    remainingHours: 10,
    expired: false,
    evidence: null,
    ...over,
  }) as unknown as ObserveItem;

/**
 * 四个标的，分别覆盖价格的四种来源状态：
 *  · CANDLE  只有 last_price + last_bar_utc（后端当前真实形状）⇒ 应显示「K线收盘价」
 *  · REALTIME 载荷显式带实时报价 ⇒ 应显示「最新价」
 *  · UNSOURCED 有数值但无 bar 时刻佐证 ⇒ 应显示「来源未确认」
 *  · NOPRICE  什么都没有 ⇒ 应显示「价格未知」
 */
const items: ObserveItem[] = [
  baseItem({
    symbol: 'CANDLEUSDT',
    last_price: 68123.45,
    last_bar_utc: '2026-10-28T16:00:00Z',
    breakout_target: 70000,
    evidence: {
      taker: ratio('sum_taker_long_short_vol_ratio', 1.02),
      ratios: [
        ratio('retail_account_ls_ratio', 1.25),
        ratio('count_long_short_ratio', 1.25), // 已声明别名 + 同值 ⇒ 应折叠并标注
        ratio('count_toptrader_long_short_ratio', 1.25), // 未声明、数值相同 ⇒ 必须独立、不得标别名
      ],
      stale: false,
      staleHours: null,
      staleNote: null,
      rangeFromUtc: '2026-10-28T12:00:00Z',
      rangeToUtc: '2026-10-28T16:00:00Z',
      source: 'metrics_hist',
      availability: null,
      availabilityNote: null,
      tailNulls: [],
      missingCols: [],
      phasePrice: null,
      missing: [],
    },
  }),
  baseItem({
    symbol: 'REALTIMEUSDT',
    last_price: 68100,
    last_bar_utc: '2026-10-28T16:00:00Z',
    realtime: { last_price: 68450.12, at_utc: '2026-10-28T16:29:00Z' },
  }),
  baseItem({ symbol: 'UNSOURCEDUSDT', last_price: 68100 }),
  baseItem({ symbol: 'NOPRICEUSDT' }),
];

const view: ObserveView = {
  kind: 'ok',
  message: null,
  items,
  counts: { BOX: 4, SWEEP: 0, RETEST: 0 },
  observingTotal: 4,
  dataNotReady: 0,
  universe: 4,
  shown: 4,
  truncated: false,
  ageSeconds: 5,
  generatedAt: '2026-10-28T16:30:00Z',
  scanErrorsTotal: 0,
  definition: 'TEST FIXTURE（PR2-WO-01）',
};

const App: React.FC = () => (
  <div id="fixture-root" data-fixture="pr2-wo-01" style={{ background: '#0a0a0a', padding: 8 }}>
    <ObservePanel view={view} />
  </div>
);

createRoot(document.getElementById('root') as HTMLElement).render(<App />);
