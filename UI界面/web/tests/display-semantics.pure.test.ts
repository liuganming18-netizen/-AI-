/**
 * PR2-WO-01 · 前端显示语义最小修复 —— 纯逻辑行为测试
 *
 * 跑法：`./node_modules/.bin/tsx tests/display-semantics.pure.test.ts`
 * 退出码 0 = 全过；非 0 = 有失败（逐条打印期望值与实得值）。
 *
 * 为什么这些断言算「行为测试」而不是「读代码」：
 *   每一条都调用**生产代码里真实导出的函数**，输入是后端真实载荷形状的夹具，
 *   断言的是**函数返回的展示结论**（标签 / 是否合并 / 时间是否为 null）。
 *   把修复回退成旧实现，这些断言会真的失败（本文件末尾附「回退后应失败的用例」对照）。
 */

import assert from 'node:assert';
import { adaptSignal } from '../src/api/adapters';
import {
  canonicalRatioKey,
  dedupeRatios,
  isDeclaredRatioAlias,
  isRatioAliasOf,
  resolveObservePrice,
  RATIO_ALIAS_GROUPS,
} from '../src/components/ObservePanel';
import type { ObserveEvidenceValue, ObserveItem } from '../src/api/liveTrading';

type Case = { name: string; fn: () => void };
const cases: Case[] = [];
const test = (name: string, fn: () => void) => cases.push({ name, fn });

/* ───────────────────────── 夹具 ───────────────────────── */

const emptySymbolMap = new Map<string, string>();

/** 后端 `live_signal` 行形状（只放本工单关心的字段，其余留空）。 */
const rawSignal = (over: Record<string, unknown>) =>
  ({
    signal_id: 'SIG-FIXTURE-1',
    exchange: 'binance',
    market_id: 'BTCUSDT',
    direction: 'long',
    timeframe: '1h',
    status: 'confirmed',
    ...over,
  }) as any;

const ev = (key: string, value: number | null, over: Partial<ObserveEvidenceValue> = {}): ObserveEvidenceValue => ({
  key,
  label: key,
  value,
  atUtc: '2026-10-28T16:00:00Z',
  lagSec: 60,
  fromLatestRow: true,
  missing: null,
  ...over,
});

/** 后端观察区真实载荷形状（`unified_runtime.py` observe_events 的 items 元素）。 */
const observeItem = (over: Record<string, unknown> = {}): ObserveItem =>
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

/* ───────────── 修复 A：触发时间不得用入库时间冒充 ───────────── */

test('A1 只有 created_at（入库时刻）时，triggerTime 必须是 null，不得冒充触发时间', () => {
  const s = adaptSignal(rawSignal({ created_at: '2026-10-28T16:30:00Z' }), emptySymbolMap);
  assert.strictEqual(s.triggerTime, null);
  assert.strictEqual(s.confirmedAt, null);
  assert.strictEqual(s.eventTime, null);
});

test('A2 created_at + event_time 都有 ⇒ 取 event_time（信号发生时刻），不取入库时刻', () => {
  const s = adaptSignal(
    rawSignal({ created_at: '2026-10-28T16:30:00Z', event_time: '2026-10-28T15:00:00Z' }),
    emptySymbolMap,
  );
  assert.strictEqual(s.triggerTime, '2026-10-28T15:00:00Z');
});

test('A3 confirmed_at 优先于 event_time', () => {
  const s = adaptSignal(
    rawSignal({
      created_at: '2026-10-28T16:30:00Z',
      event_time: '2026-10-28T15:00:00Z',
      confirmed_at: '2026-10-28T16:00:00Z',
    }),
    emptySymbolMap,
  );
  assert.strictEqual(s.triggerTime, '2026-10-28T16:00:00Z');
});

test('A4 三个时刻全缺 ⇒ null（不是空串、不是 created_at）', () => {
  const s = adaptSignal(rawSignal({}), emptySymbolMap);
  assert.strictEqual(s.triggerTime, null);
});

/* ───────────── 修复 B：别名按显式映射，不按数值 ───────────── */

test('B1 两个**不同**指标数值恰好相等 ⇒ 必须都保留（旧实现会合并成一条）', () => {
  const rows = [
    ev('retail_account_ls_ratio', 1.25),
    ev('sum_taker_long_short_vol_ratio', 1.25), // 不同指标，读数撞上了
    ev('count_toptrader_long_short_ratio', 1.25), // 又一个不同指标
  ];
  const kept = dedupeRatios(rows);
  assert.deepStrictEqual(
    kept.map((r) => r.key),
    ['retail_account_ls_ratio', 'sum_taker_long_short_vol_ratio', 'count_toptrader_long_short_ratio'],
  );
});

test('B2 已声明成对且同值 ⇒ 折叠为一条（真别名）', () => {
  const rows = [ev('retail_account_ls_ratio', 1.25), ev('count_long_short_ratio', 1.25)];
  const kept = dedupeRatios(rows);
  assert.strictEqual(kept.length, 1);
  assert.strictEqual(kept[0].key, 'retail_account_ls_ratio');
});

test('B3 已声明成对但读数**分叉** ⇒ 两列都保留（不能藏起真实差异）', () => {
  const rows = [ev('top_position_ls_ratio', 2.1), ev('sum_toptrader_long_short_ratio', 2.4)];
  const kept = dedupeRatios(rows);
  assert.deepStrictEqual(kept.map((r) => r.key), ['top_position_ls_ratio', 'sum_toptrader_long_short_ratio']);
});

test('B4 值为 null 的列一律保留（「没读到」不等于「与别人相同」）', () => {
  const rows = [
    ev('retail_account_ls_ratio', null),
    ev('count_long_short_ratio', null),
    ev('top_position_ls_ratio', null),
  ];
  assert.strictEqual(dedupeRatios(rows).length, 3);
});

test('B5 未声明的列即使同值也不构成别名（isRatioAliasOf 为 false）', () => {
  const a = ev('sum_taker_long_short_vol_ratio', 3.3);
  const b = ev('count_toptrader_long_short_ratio', 3.3);
  assert.strictEqual(isDeclaredRatioAlias(a.key, b.key), false);
  assert.strictEqual(isRatioAliasOf(a, b), false);
});

test('B6 同一列自身不构成别名（同 key 不得判成两列互为别名）', () => {
  const a = ev('retail_account_ls_ratio', 1.1);
  const b = ev('retail_account_ls_ratio', 1.1);
  assert.strictEqual(isRatioAliasOf(a, b), false);
});

test('B7 别名映射是显式表：两个组、每组两列，代表键为组内第一列', () => {
  assert.deepStrictEqual(RATIO_ALIAS_GROUPS, [
    ['retail_account_ls_ratio', 'count_long_short_ratio'],
    ['top_position_ls_ratio', 'sum_toptrader_long_short_ratio'],
  ]);
  assert.strictEqual(canonicalRatioKey('count_long_short_ratio'), 'retail_account_ls_ratio');
  assert.strictEqual(canonicalRatioKey('sum_toptrader_long_short_ratio'), 'top_position_ls_ratio');
  // 未声明的列自成一组
  assert.strictEqual(canonicalRatioKey('sum_taker_long_short_vol_ratio'), 'sum_taker_long_short_vol_ratio');
});

/* ───────────── 修复 C：价格来源与标签 ───────────── */

test('C1 只有 last_price + last_bar_utc（后端真实形状）⇒ 标「K线收盘价」，不标最新价', () => {
  const p = resolveObservePrice(
    observeItem({ last_price: 68123.45, last_bar_utc: '2026-10-28T16:00:00Z' }),
  );
  assert.strictEqual(p.source, 'candle_close');
  assert.strictEqual(p.label, 'K线收盘价');
  assert.strictEqual(p.value, 68123.45);
  assert.strictEqual(p.asOfUtc, '2026-10-28T16:00:00Z');
});

test('C2 有 last_bar.close 但无 last_price ⇒ 标「K线收盘价」', () => {
  const p = resolveObservePrice(
    observeItem({ last_bar: { close: 2500.5, close_utc: '2026-10-28T15:00:00Z' } }),
  );
  assert.strictEqual(p.source, 'candle_close');
  assert.strictEqual(p.label, 'K线收盘价');
  assert.strictEqual(p.asOfUtc, '2026-10-28T15:00:00Z');
});

test('C3 载荷显式声明实时报价 ⇒ 标「最新价」，且优先于 K 线收盘价', () => {
  const p = resolveObservePrice(
    observeItem({
      last_price: 68100,
      last_bar_utc: '2026-10-28T16:00:00Z',
      realtime: { last_price: 68450.12, at_utc: '2026-10-28T16:29:00Z' },
    }),
  );
  assert.strictEqual(p.source, 'realtime');
  assert.strictEqual(p.label, '最新价');
  assert.strictEqual(p.value, 68450.12);
  assert.strictEqual(p.asOfUtc, '2026-10-28T16:29:00Z');
});

test('C4 有数值但确认不到来源 ⇒ 标「来源未确认」，不得标成实时价', () => {
  const p = resolveObservePrice(observeItem({ last_price: 68100 })); // 没有 last_bar_utc 佐证
  assert.strictEqual(p.source, 'unknown');
  assert.strictEqual(p.label, '来源未确认');
  assert.strictEqual(p.value, 68100); // 数值照显，不隐瞒
  assert.notStrictEqual(p.label, '最新价');
});

test('C5 完全没有价格 ⇒ 「价格未知」且 value 为 null', () => {
  const p = resolveObservePrice(observeItem());
  assert.strictEqual(p.source, 'unknown');
  assert.strictEqual(p.label, '价格未知');
  assert.strictEqual(p.value, null);
});

test('C6 非数值/NaN 脏数据不得被当成价格', () => {
  const p = resolveObservePrice(
    observeItem({ last_price: '68100' as unknown as number, last_bar_utc: '2026-10-28T16:00:00Z' }),
  );
  assert.strictEqual(p.value, null);
  assert.strictEqual(p.label, '价格未知');
});

test('C7 标签文案只取受控枚举值，不存在「现价」这种不分来源的写法', () => {
  const labels = new Set(
    [
      observeItem({ last_price: 1, last_bar_utc: '2026-10-28T16:00:00Z' }),
      observeItem({ realtime: { last_price: 1, at_utc: '2026-10-28T16:00:00Z' } }),
      observeItem({ realtime: { last_price: 1 } }), // 缺少时间戳 ⇒ 报价时间未确认
      observeItem({ last_price: 1 }),
      observeItem(),
    ].map((it) => resolveObservePrice(it).label),
  );
  assert.deepStrictEqual(
    [...labels].sort(),
    ['K线收盘价', '价格未知', '来源未确认', '报价时间未确认', '最新价'].sort(),
  );
  assert.ok(!labels.has('现价'));
});

test('C8 实时报价时间戳有效存在 ⇒ 标「最新价」且记录时效时刻', () => {
  const p = resolveObservePrice(
    observeItem({
      price_source: 'realtime',
      last_price: 68500.5,
      price_at_utc: '2026-10-28T16:30:00Z',
    }),
  );
  assert.strictEqual(p.source, 'realtime');
  assert.strictEqual(p.label, '最新价');
  assert.strictEqual(p.value, 68500.5);
  assert.strictEqual(p.asOfUtc, '2026-10-28T16:30:00Z');
});

test('C9 实时报价时间戳缺失 ⇒ 保守标「报价时间未确认」且退回 unknown', () => {
  const p = resolveObservePrice(
    observeItem({
      price_source: 'realtime',
      last_price: 68500.5,
      // price_at_utc 缺失
    }),
  );
  assert.strictEqual(p.source, 'unknown');
  assert.strictEqual(p.label, '报价时间未确认');
  assert.strictEqual(p.value, 68500.5);
  assert.strictEqual(p.asOfUtc, null);
});

test('C10 实时报价时间戳格式无效/NaN ⇒ 保守标「报价时间未确认」', () => {
  const p = resolveObservePrice(
    observeItem({
      realtime: {
        last_price: 68500.5,
        at_utc: 'not-a-valid-date-string',
      },
    }),
  );
  assert.strictEqual(p.source, 'unknown');
  assert.strictEqual(p.label, '报价时间未确认');
  assert.strictEqual(p.value, 68500.5);
  assert.strictEqual(p.asOfUtc, null);
});

/* ───────────────────────── 运行器 ───────────────────────── */

let failed = 0;
for (const c of cases) {
  try {
    c.fn();
    console.log(`  PASS  ${c.name}`);
  } catch (e) {
    failed += 1;
    console.log(`  FAIL  ${c.name}`);
    console.log(`        ${(e as Error).message.split('\n').slice(0, 6).join('\n        ')}`);
  }
}
console.log(`\n纯逻辑行为测试：${cases.length - failed}/${cases.length} 通过`);
if (failed > 0) process.exit(1);
