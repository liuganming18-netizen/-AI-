/**
 * PR2-WO-01 行为测试：前端三处误导性展示的回归。
 *
 * 覆盖：
 *   A. 信号触发时间不得回退到 `created_at`（建库时间）
 *   B. 多空比「别名」判定必须基于已声明关系，不能只凭数值相等
 *   C. 紧凑行去重只在「已声明成对且同值」时合并
 *   D. 价格标签必须区分「最新价 / K线收盘价 / 价格未知」
 *
 * 为什么不用 jest/vitest：本项目 `package.json` 未装任何测试框架，装框架要改
 * `package.json` / `package-lock.json`，超出本工单授权范围。`tsx` 已在 devDependencies
 * 里，故用「纯 TS 脚本 + 自建断言 + 服务端静态渲染」做行为验证，不新增依赖。
 *
 * 运行：`npx tsx tests/display-semantics.test.tsx`
 * 退出码：全部通过为 0；任一失败抛错 ⇒ 非 0。
 */

import { renderToStaticMarkup } from 'react-dom/server';

import { adaptSignal } from '../src/api/adapters';
import type {
  ObserveEvidence,
  ObserveEvidenceValue,
  ObserveItem,
  ObserveView,
} from '../src/api/liveTrading';
import type { RawSignal } from '../src/api/types';
import ObservePanel, {
  canonicalRatioKey,
  dedupeRatios,
  isRatioAliasOf,
} from '../src/components/ObservePanel';

/* ---------- 极简断言（不引依赖） ---------- */

let passed = 0;
const failures: string[] = [];

const ok = (name: string, cond: boolean, detail = ''): void => {
  if (cond) {
    passed += 1;
    return;
  }
  failures.push(detail ? `${name} —— ${detail}` : name);
};

const eq = (name: string, actual: unknown, expected: unknown): void => {
  ok(name, Object.is(actual, expected), `期望 ${String(expected)}，实际 ${String(actual)}`);
};

/* ---------- 夹具 ---------- */

const rawRow = (over: Partial<RawSignal> = {}): RawSignal => ({
  signal_id: 'sig-pr2wo01',
  exchange: 'binance',
  market_id: 'BTCUSDT',
  symbol: 'BTCUSDT',
  status: 'enterable',
  direction: 'long',
  strength: null,
  // 关键：建库时刻**一定存在且非空**，用来证明它不会被拿来冒充触发时间。
  created_at: '2026-10-01T00:00:00Z',
  expires_at: null,
  timeframe: '4h',
  ...over,
});

const rv = (key: string, value: number | null): ObserveEvidenceValue => ({
  key,
  label: key,
  value,
  atUtc: null,
  lagSec: null,
  fromLatestRow: true,
  missing: null,
});

const evidence = (ratios: ObserveEvidenceValue[]): ObserveEvidence => ({
  pending: false,
  inDecision: false,
  why: null,
  taker: null,
  ratios,
  stale: false,
  staleHours: null,
  staleNote: null,
  rangeFromUtc: null,
  rangeToUtc: null,
  source: null,
  availability: null,
  availabilityNote: null,
  tailNulls: [],
  missingCols: [],
  phasePrice: null,
  missing: [],
});

const baseItem: ObserveItem = {
  symbol: 'BTCUSDT',
  state: 'BOX',
  stateCn: '箱体观察',
  nextGate: 'SWEEP',
  createdUtc: null,
  updatedUtc: null,
  ageHours: null,
  lastBarUtc: null,
  dataReady: true,
  box: { h: 100, l: 90, a: 1, er: 0.5, widthAtr: 2 },
  satisfied: [],
  unmet: [],
  pendingEval: [],
  deadlineUtc: null,
  remainingHours: 5,
  expired: false,
  evidence: evidence([]),
};

/** `last_price` / `last_bar` 不在 ObserveItem 声明里（组件按 any 读取），故用附加字段构造。 */
const item = (extra: Record<string, unknown> = {}): ObserveItem =>
  ({ ...baseItem, ...extra }) as unknown as ObserveItem;

const view = (items: ObserveItem[]): ObserveView => ({
  kind: 'ok',
  message: null,
  items,
  counts: { BOX: items.length },
  observingTotal: items.length,
  dataNotReady: 0,
  universe: 10,
  shown: items.length,
  truncated: false,
  ageSeconds: 1,
  generatedAt: '2026-10-29T02:00:00Z',
  scanErrorsTotal: 0,
  definition: null,
});

const render = (v: ObserveView): string => renderToStaticMarkup(<ObservePanel view={v} />);

/* ---------- A. 触发时间 ---------- */

{
  const onlyCreated = adaptSignal(rawRow(), new Map());
  eq('A1 只有 created_at 时触发时间为 null', onlyCreated.triggerTime, null);
  // CryptoSignal 类型本身没有 created_at 字段 ⇒ 视图模型里唯一可能泄漏建库时间的地方
  // 就是 triggerTime。这里显式证明它没被写进去。
  ok(
    'A2 触发时间不等于建库时间',
    onlyCreated.triggerTime !== '2026-10-01T00:00:00Z',
    `triggerTime=${String(onlyCreated.triggerTime)}`,
  );

  const withConfirmed = adaptSignal(
    rawRow({ confirmed_at: '2026-10-28T12:00:00Z' }),
    new Map(),
  );
  eq('A3 confirmed_at 优先', withConfirmed.triggerTime, '2026-10-28T12:00:00Z');

  const withEvent = adaptSignal(rawRow({ event_time: '2026-10-28T08:00:00Z' }), new Map());
  eq('A4 无 confirmed_at 时用 event_time', withEvent.triggerTime, '2026-10-28T08:00:00Z');

  const both = adaptSignal(
    rawRow({ confirmed_at: '2026-10-28T12:00:00Z', event_time: '2026-10-28T08:00:00Z' }),
    new Map(),
  );
  eq('A5 confirmed_at 与 event_time 同时存在时取 confirmed_at', both.triggerTime, '2026-10-28T12:00:00Z');

  // 契约兼容：这两个字段仍然原样透传，未被本工单改动。
  eq('A6 confirmedAt 仍原样透传', both.confirmedAt, '2026-10-28T12:00:00Z');
  eq('A7 eventTime 仍原样透传', both.eventTime, '2026-10-28T08:00:00Z');
}

/* ---------- B. 别名判定 ---------- */

{
  ok(
    'B1 未声明关系的两列即使同值也不构成别名',
    !isRatioAliasOf(rv('oi_qty', 1.5), rv('taker_buy_sell_ratio', 1.5)),
  );

  ok(
    'B2 已声明成对且同值 ⇒ 别名',
    isRatioAliasOf(rv('retail_account_ls_ratio', 1.2345), rv('count_long_short_ratio', 1.2345)),
  );

  ok(
    'B3 已声明成对但读数分叉 ⇒ 不是别名（不藏差异）',
    !isRatioAliasOf(rv('retail_account_ls_ratio', 1.2), rv('count_long_short_ratio', 1.3)),
  );

  ok(
    'B4 两边都为 null 不算别名（未知 ≠ 相同）',
    !isRatioAliasOf(rv('retail_account_ls_ratio', null), rv('count_long_short_ratio', null)),
  );

  ok(
    'B5 一边有值一边为 null 不算别名',
    !isRatioAliasOf(rv('retail_account_ls_ratio', 1.2), rv('count_long_short_ratio', null)),
  );

  eq('B6 未声明列自成一組', canonicalRatioKey('oi_qty'), 'oi_qty');
  eq(
    'B7 已声明列归到代表键',
    canonicalRatioKey('count_long_short_ratio'),
    'retail_account_ls_ratio',
  );
  eq(
    'B8 另一对已声明列归到代表键',
    canonicalRatioKey('sum_toptrader_long_short_ratio'),
    'top_position_ls_ratio',
  );
}

/* ---------- C. 去重 ---------- */

{
  eq(
    'C1 两个无关列同值必须都保留',
    dedupeRatios([rv('oi_qty', 2.5), rv('taker_buy_sell_ratio', 2.5)]).length,
    2,
  );

  eq(
    'C2 已声明成对且同值只留一条',
    dedupeRatios([
      rv('retail_account_ls_ratio', 1.1),
      rv('count_long_short_ratio', 1.1),
    ]).length,
    1,
  );

  eq(
    'C3 已声明成对但分叉必须都保留',
    dedupeRatios([
      rv('retail_account_ls_ratio', 1.1),
      rv('count_long_short_ratio', 1.2),
    ]).length,
    2,
  );

  eq(
    'C4 值为 null 的列不被吞掉',
    dedupeRatios([rv('oi_qty', null), rv('taker_buy_sell_ratio', null)]).length,
    2,
  );

  // 原始意图不能丢：5 列多空比在「两对都同值」时应折叠成 3 类独立信息。
  eq(
    'C5 两对同值时 5 列折叠为 3 类',
    dedupeRatios([
      rv('retail_account_ls_ratio', 1.1),
      rv('count_long_short_ratio', 1.1),
      rv('top_position_ls_ratio', 2.2),
      rv('sum_toptrader_long_short_ratio', 2.2),
      rv('sum_taker_long_short_vol_ratio', 3.3),
    ]).length,
    3,
  );

  // 同一组内只留第一条，保证折叠后取值稳定。
  const collapsed = dedupeRatios([
    rv('retail_account_ls_ratio', 1.1),
    rv('count_long_short_ratio', 1.1),
  ]);
  eq('C6 折叠后保留先出现的那条', collapsed[0]?.key, 'retail_account_ls_ratio');
}

/* ---------- D. 价格标签（真实渲染） ---------- */

/** 从静态渲染结果里精确取出「价格」那一格的值，避免撞上箱体上沿等其它数字。 */
const priceCell = (html: string): string => {
  const m = html.match(/data-testid="observe-price-value">([^<]*)</);
  return m ? m[1] : '<未找到价格元素>';
};

{
  const liveOnly = render(
    view([
      item({
        price_source: 'realtime',
        last_price: 123.45,
        price_at_utc: '2026-10-28T16:29:00Z',
      }),
    ]),
  );
  ok('D1 显式实时价且时间戳有效 ⇒ 标签「最新价」', liveOnly.includes('最新价'));
  ok('D2 显式实时价时不得出现「K线收盘价」', !liveOnly.includes('K线收盘价'));
  ok('D3 显式实时价时来源标记为 realtime', liveOnly.includes('data-price-source="realtime"'));
  eq('D4 实时价数值被渲染', priceCell(liveOnly), '123.4500');

  const closeOnly = render(view([item({ last_bar: { close: 100.5 } })]));
  ok('D5 只有 K 线收盘价 ⇒ 标签「K线收盘价」', closeOnly.includes('K线收盘价'));
  ok('D6 只有收盘价时不得出现「最新价」', !closeOnly.includes('最新价'));
  ok('D7 收盘价来源标记为 candle_close', closeOnly.includes('data-price-source="candle_close"'));
  eq('D8 收盘价数值被渲染', priceCell(closeOnly), '100.5000');

  const none = render(view([item({})]));
  ok('D9 两个价都没有 ⇒ 标签「价格未知」', none.includes('价格未知'));
  ok('D10 无价时来源标记为 unknown', none.includes('data-price-source="unknown"'));
  ok('D11 无价时不再出现旧标签「现价」', !none.includes('>现价<'));

  const both = render(
    view([
      item({
        price_source: 'realtime',
        last_price: 200,
        price_at_utc: '2026-10-28T16:29:00Z',
        last_bar: { close: 100 },
      }),
    ]),
  );
  ok('D12 两者都有 ⇒ 标签取「最新价」', both.includes('最新价'));
  eq('D13 两者都有时数值取实时价而不是收盘价', priceCell(both), '200.0000');
  ok('D14 两者都有时来源标记为 realtime', both.includes('data-price-source="realtime"'));

  const unconfirmed = render(view([item({ last_price: 123.45 })]));
  ok('D15 只有未确认来源的 last_price ⇒ 标签「来源未确认」', unconfirmed.includes('来源未确认'));
  ok('D16 来源未确认时标记为 unknown', unconfirmed.includes('data-price-source="unknown"'));

  // GM-UI-BRANCH-02-R1：时间戳缺失与无效边界测试
  const missingTime = render(view([item({ price_source: 'realtime', last_price: 123.45 })]));
  ok('D17 实时报价时间戳缺失 ⇒ 标「报价时间未确认」', missingTime.includes('报价时间未确认'));
  ok('D18 实时报价时间戳缺失时来源退回 unknown', missingTime.includes('data-price-source="unknown"'));

  const invalidTime = render(
    view([
      item({
        price_source: 'realtime',
        last_price: 123.45,
        price_at_utc: 'not-a-valid-iso-date',
      }),
    ]),
  );
  ok('D19 实时报价时间戳无效 ⇒ 标「报价时间未确认」', invalidTime.includes('报价时间未确认'));
  ok('D20 实时报价时间戳无效时来源退回 unknown', invalidTime.includes('data-price-source="unknown"'));
}

/* ---------- 汇总 ---------- */

if (failures.length > 0) {
  console.error(`\n未通过 ${failures.length} 项：`);
  for (const f of failures) console.error(`  · ${f}`);
  throw new Error(`PR2-WO-01 行为测试未通过：${failures.length} 项失败 / 共 ${passed + failures.length} 项`);
}

console.log(`PR2-WO-01 行为测试全部通过：${passed} 项`);
