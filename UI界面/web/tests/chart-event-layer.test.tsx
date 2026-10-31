/**
 * CHART-RESTORE-56-04 回归测试：图表事件图层恢复后的行为断言。
 *
 * 覆盖（全部为服务端静态渲染，不需要浏览器、不需要后端）：
 *   A. `chart.events` 非空时，事件时间线条渲染出每个真实事件的可点按钮；
 *   B. `chart.events_unavailable` 非空时，渲染出逐条「不可用 + 原因」告警行；
 *   C. `chart.events` 为空时不渲染时间线条（不造假点、不造空壳）；
 *   D. 事件类型覆盖：box / sweep_low / sweep_high / retest / breakout / signal_invalidated
 *      六类都能原样进入时间线，不被吞掉。
 *
 * 运行：`npx tsx tests/chart-event-layer.test.tsx`
 * 退出码：全部通过为 0；任一失败抛错 ⇒ 非 0。
 *
 * 为什么不用 jest/vitest：本项目未装任何测试框架，装框架要改 package.json /
 * package-lock.json，超出本工单授权。`tsx` 已在 devDependencies 里。
 */

import { renderToStaticMarkup } from 'react-dom/server';

import { LabChartPanel } from '../src/components/LabChartPanel';
import type { LabChart, LabChartEvent } from '../src/api/labBacktest';

let passed = 0;
const failures: string[] = [];

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    passed += 1;
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ''}`);
  }
}

const T0 = 1790308800000; // 2026-09-25T12:00:00Z 附近，4h 闭 bar 对齐时刻
const H = 3_600_000;

function mkEvent(over: Partial<LabChartEvent> & Pick<LabChartEvent, 'event_id' | 'event_type'>): LabChartEvent {
  return {
    symbol: 'BTCUSDT',
    timeframe: '4h',
    event_time: T0,
    available_at: T0 + 60_000,
    price: 80_000,
    source: 'run_dir',
    ...over,
  } as unknown as LabChartEvent;
}

function mkChart(events: LabChartEvent[], unavailable: Array<{ event_type: string; reason: string }> = []): LabChart {
  return {
    symbol: 'BTCUSDT',
    exec_timeframe: '4h',
    window: { start_ms: T0 - 10 * H, end_ms: T0 + 10 * H },
    candles: [],
    signals: [],
    events,
    events_status: events.length ? 'available' : 'empty_or_unrecorded',
    events_unavailable: unavailable,
  } as unknown as LabChart;
}

function render(chart: LabChart | null): string {
  return renderToStaticMarkup(
    <LabChartPanel
      chart={chart}
      loading={false}
      error={null}
      missingReason={null}
      selectedTradeId={null}
      onSelectTrade={(_id: string | null) => {}}
    />,
  );
}

/* ── A. 事件时间线条 ─────────────────────────────────────────── */
const sixTypes = ['box', 'sweep_low', 'sweep_high', 'retest', 'breakout', 'signal_invalidated'];
const evs = sixTypes.map((t, i) =>
  mkEvent({
    event_id: `ev_${t}`,
    event_type: t,
    event_time: T0 + i * H,
    ...(t === 'box'
      ? {
          price_upper: 81_000,
          price_lower: 79_000,
          range_start_time: T0 - 6 * H,
          range_end_time: T0 + 6 * H,
          formation_time: T0 - 2 * H,
          confirmed_at: T0,
        }
      : {}),
  }),
);
const html = render(mkChart(evs));

check('A1 事件时间线条渲染出来', html.includes('回测事件时间线'), '未找到 aria-label');
for (const t of sixTypes) {
  check(`A2 事件类型「${t}」进入时间线`, html.includes(`>${t} · `), '未找到该事件按钮文本');
}
check('A3 六类事件各渲染一次', sixTypes.every((t) => (html.match(new RegExp(`>${t} · `, 'g')) || []).length === 1));

/* ── B. 不可用告警行 ─────────────────────────────────────────── */
const htmlU = render(mkChart([], [{ event_type: 'sweep_high', reason: '源记录未提供该类型事件' }]));
check('B1 不可用行带 data-testid', htmlU.includes('data-testid="event-unavailable-sweep_high"'));
check('B2 不可用行写明原因', htmlU.includes('源记录未提供该类型事件'));

/* ── C. 空事件不造壳 ─────────────────────────────────────────── */
const htmlEmpty = render(mkChart([]));
check('C1 空事件不渲染时间线', !htmlEmpty.includes('回测事件时间线'));
check('C2 空事件不渲染不可用行', !htmlEmpty.includes('event-unavailable-'));

/* ── D. 图表载荷为 null 时不崩 ───────────────────────────────── */
let nullOk = true;
try {
  render(null);
} catch (e) {
  nullOk = false;
}
check('D1 chart=null 不抛异常', nullOk);

/* ── 汇总 ───────────────────────────────────────────────────── */
if (failures.length) {
  console.error(`CHART-RESTORE-56-04 失败 ${failures.length} 项 / 通过 ${passed} 项：`);
  for (const f of failures) console.error('  ✗ ' + f);
  throw new Error(`chart event layer regression: ${failures.length} failed`);
}
console.log(`CHART-RESTORE-56-04 事件图层回归全部通过：${passed} 项`);
