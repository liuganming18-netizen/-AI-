/**
 * UI-PENDING-01 · 挂单展示独立组件测试（不依赖后端、不依赖 A 窗口）
 *
 * 做法：用 `react-dom/server` 把**真实组件** `PositionPanel` 渲染成静态 HTML，
 * 再对 HTML 做机械断言。不新增测试框架依赖、不改生产构建。
 *
 * 纪律：
 *  1. 入参全部来自 `fixtures.ts` 的 **TEST FIXTURE**（人工构造，标的带 TESTFIX 前缀）；
 *  2. 只断言契约内字段（`paper_engine.account_view()` 的挂单投影），
 *     并对「方向 / 数量 / 价格」做**负向断言**，防止界面自己造交易语义；
 *  3. 本测试只证明「页面在给定输入下的渲染行为」，**不构成**真实成交闭环证据。
 *
 * 运行：
 *   cd UI界面/web && ./node_modules/.bin/tsx tests_pending/pending_states.test.tsx <out.json>
 */

import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { PositionPanel } from '../src/components/PositionPanel';
import { CASES, FIXTURE_BANNER, type CaseName } from './fixtures';

interface Result {
  case: CaseName;
  check: string;
  pass: boolean;
  expected: string;
  actual: string;
}

const results: Result[] = [];

const check = (c: CaseName, name: string, pass: boolean, expected: unknown, actual: unknown) => {
  results.push({
    case: c,
    check: name,
    pass: !!pass,
    expected: String(expected),
    actual: String(actual),
  });
};

const textOf = (html: string): string =>
  html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const blockOf = (html: string): { state: string | null; nPending: string | null } | null => {
  const m = html.match(/data-testid="paper-pending-block"[^>]*/);
  if (!m) return null;
  return {
    state: m[0].match(/data-state="([^"]*)"/)?.[1] ?? null,
    nPending: m[0].match(/data-n-pending="([^"]*)"/)?.[1] ?? null,
  };
};

/** 只取挂单块自己的文本（到「实盘仓位（只读）」小节为止），避免把账户卡里的金额算进来。 */
const blockTextOf = (html: string): string => {
  const i = html.indexOf('data-testid="paper-pending-block"');
  if (i < 0) return '';
  const j = html.indexOf('实盘仓位（只读）', i);
  return textOf(html.slice(i, j > i ? j : undefined));
};

/** 按 `paper-pending-row` 切分，返回每行的「文本 + data-pending-id」。 */
const rowsOf = (html: string): Array<{ id: string | null; text: string }> => {
  const parts = html.split('data-testid="paper-pending-row"');
  parts.shift();
  return parts.map((p) => ({
    id: p.match(/data-pending-id="([^"]*)"/)?.[1] ?? null,
    text: textOf(p),
  }));
};

function renderCase(c: CaseName): string {
  const f = CASES[c];
  return renderToStaticMarkup(
    <PositionPanel
      paperAccounts={f.accounts}
      paperPositions={[]}
      paperPending={f.pending}
      paperMissingReason={f.missingReason}
      liveAccounts={[]}
      liveMissingReason={null}
      orderBusy={false}
      onSelectTokenSymbol={() => undefined}
      onClosePosition={() => undefined}
      onOpenQuickPosition={() => undefined}
      isGateRunning={false}
    />,
  );
}

// ── ① 有挂单 ────────────────────────────────────────────────────────────────
{
  const c: CaseName = 'has_pending';
  const html = renderCase(c);
  const block = blockOf(html);
  const rows = rowsOf(html);
  const t = textOf(html);

  check(c, '挂单块存在', !!block, 'block != null', block);
  check(c, 'data-state = has_pending', block?.state === 'has_pending', 'has_pending', block?.state);
  check(c, 'data-n-pending = 2', block?.nPending === '2', '2', block?.nPending);
  check(c, '挂单行数 = 2', rows.length === 2, 2, rows.length);
  check(c, '挂单身份 pending_id 顺序', rows.map((r) => r.id).join(',') === '9001,9002', '9001,9002', rows.map((r) => r.id).join(','));
  check(c, '标题笔数与行数一致', t.includes('待成交挂单 (2 笔)'), '含「待成交挂单 (2 笔)」', t.includes('待成交挂单 (2 笔)'));

  const r0 = rows[0]?.text ?? '';
  const r1 = rows[1]?.text ?? '';
  check(c, '行1 标的名', r0.includes('TESTFIXUSDT'), '含 TESTFIXUSDT', r0.slice(0, 60));
  check(c, '行1 身份 #9001 可见', r0.includes('#9001'), '含 #9001', r0.slice(0, 60));
  check(c, '行1 状态中文+原码', r0.includes('等待成交') && r0.includes('pending'), '含「等待成交」且含 pending', r0);
  check(c, '行1 来源=实时信号触发', r0.includes('实时信号触发'), '含「实时信号触发」', r0);
  check(c, '行2 来源=页面手动请求', r1.includes('页面手动请求'), '含「页面手动请求」', r1);
  check(c, '行1 等待原因(结构性，非 0/—)', r0.includes('等待原因') && r0.includes('等待最早可成交 15m 闭合'), '含结构性等待说明', r0);
  check(c, '行2 等待原因取服务端 reason', r1.includes('test_fixture_reason_visible'), '含 test_fixture_reason_visible', r1);
  check(c, '行1 可成交时间 10-24 00:30Z', r0.includes('10-24 00:30Z'), '含 10-24 00:30Z', r0);
  check(c, '行2 可成交时间 10-24 01:30Z', r1.includes('10-24 01:30Z'), '含 10-24 01:30Z', r1);
  check(c, '行1 请求锚点 10-24 00:15Z', r0.includes('10-24 00:15Z'), '含 10-24 00:15Z', r0);
  check(c, '行1 信号确认/可得', r0.includes('10-24 00:00Z') && r0.includes('10-24 00:01Z'), '含 00:00Z / 00:01Z', r0);
  check(c, '行1 信号身份 signal_uid', r0.includes('sig_TESTFIX_9001'), '含 sig_TESTFIX_9001', r0);
  check(c, '行2 无 signal_uid 时显示 —', r1.includes('信号身份') && r1.includes('—'), '含「信号身份」且含 —', r1);

  // ── 负向断言：契约没有的字段，界面不许自己编（只看挂单块内部） ──
  const bt = blockTextOf(html);
  check(c, '未编造方向', !/方向\s*[:：]\s*(多|空|long|short)/i.test(bt), '无「方向: 多/空」', /方向\s*[:：]\s*(多|空|long|short)/i.test(bt));
  check(c, '未编造数量', !/数量\s*[:：]\s*\d/.test(bt), '无「数量: 数字」', /数量\s*[:：]\s*\d/.test(bt));
  check(c, '未编造价格', !/\$\s*\d/.test(bt), '无「$数字」', /\$\s*\d/.test(bt));
  check(c, '如实标注接口缺口', bt.includes('数量与成交价待实际成交时确定'), '含缺口说明', bt.includes('数量与成交价待实际成交时确定'));
  check(c, '夹具横幅未混入组件', !bt.includes(FIXTURE_BANNER), '组件内无夹具横幅', bt.includes(FIXTURE_BANNER));
}

// ── ② 无挂单 ────────────────────────────────────────────────────────────────
{
  const c: CaseName = 'empty';
  const html = renderCase(c);
  const block = blockOf(html);
  const rows = rowsOf(html);
  const t = textOf(html);

  check(c, '挂单块存在（不再整块消失）', !!block, 'block != null', block);
  check(c, 'data-state = empty', block?.state === 'empty', 'empty', block?.state);
  check(c, 'data-n-pending = 0', block?.nPending === '0', '0', block?.nPending);
  check(c, '挂单行数 = 0', rows.length === 0, 0, rows.length);
  check(c, '空态文案明确', t.includes('当前无待成交挂单'), '含「当前无待成交挂单」', t);
  check(c, '标题笔数 0 笔', t.includes('待成交挂单 (0 笔)'), '含「(0 笔)」', t);
  check(c, '空态不出现夹具标的', !t.includes('TESTFIXUSDT'), '不含 TESTFIXUSDT', t.includes('TESTFIXUSDT'));
}

// ── ③ 请求失败（账本读不到） ─────────────────────────────────────────────────
{
  const c: CaseName = 'unreadable';
  const html = renderCase(c);
  const block = blockOf(html);
  const rows = rowsOf(html);
  const t = textOf(html);

  check(c, '挂单块存在（失败也要有位置）', !!block, 'block != null', block);
  check(c, 'data-state = unreadable', block?.state === 'unreadable', 'unreadable', block?.state);
  check(c, 'data-n-pending = -1（未知，不写 0）', block?.nPending === '-1', '-1', block?.nPending);
  check(c, '失败态不渲染任何挂单行', rows.length === 0, 0, rows.length);
  check(c, '失败态不渲染上一次缓存的挂单', !t.includes('TESTFIXUSDT'), '不含 TESTFIXUSDT', t.includes('TESTFIXUSDT'));
  check(c, '失败原因原样透出', t.includes('TEST FIXTURE：纸面账本读取失败'), '含 missing_reason', t);
  check(c, '明确「不等于 0 笔」', t.includes('不等于 0 笔'), '含「不等于 0 笔」', t);
  check(c, '不冒充空态', !t.includes('当前无待成交挂单'), '不含空态文案', t.includes('当前无待成交挂单'));
}

// ── ④ 未连接：整块不出现（避免把「没接通」渲染成「没挂单」） ──────────────────
{
  const c: CaseName = 'not_connected';
  const html = renderCase(c);
  const block = blockOf(html);
  check(c, '未连接时无挂单块', block === null, 'block == null', block);
}

// ── 输出 ────────────────────────────────────────────────────────────────────
const total = results.length;
const passed = results.filter((r) => r.pass).length;
const failed = results.filter((r) => !r.pass);

const payload = {
  ticket: 'UI-PENDING-01',
  kind: 'frontend-component-test',
  fixture: 'tests_pending/fixtures.ts（TEST FIXTURE，人工构造）',
  component: 'src/components/PositionPanel.tsx',
  data_source: 'none（不请求后端、不读账本）',
  note: '本结果只证明页面渲染行为，不构成真实成交闭环证据',
  generated_at_utc: new Date().toISOString(),
  summary: { total, passed, failed: failed.length, all_pass: failed.length === 0 },
  results,
};

const outArg = process.argv[2];
if (outArg) {
  const p = path.resolve(outArg);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(payload, null, 2), 'utf8');
  console.log(`[UI-PENDING-01] 断言结果已写入 ${p}`);
}

for (const r of results) {
  console.log(`${r.pass ? 'PASS' : 'FAIL'}  [${r.case}] ${r.check}`);
  if (!r.pass) console.log(`      expected=${r.expected} actual=${r.actual}`);
}
console.log(`\n[UI-PENDING-01] ${passed}/${total} 通过，失败 ${failed.length}`);
process.exit(failed.length === 0 ? 0 : 1);
