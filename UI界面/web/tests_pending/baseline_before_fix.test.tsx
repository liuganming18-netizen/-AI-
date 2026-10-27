/**
 * UI-PENDING-01 · 改动前对照（baseline）
 *
 * 目的：证明「这次不只是补测试，组件行为确实变了」。
 *
 * 做法：把**改动后的** `src/components/PositionPanel.tsx` 里那两处改动
 * （① 新增 `PENDING_STATUS` / `pendingOriginText`；② 重写挂单块）机械地**还原**成
 * 改动前的原文，生成 `tests_pending/_baseline/PositionPanel.baseline.tsx`，
 * 再用**同一套入参**渲染，检查改动前的 4 个缺陷是否真的存在。
 *
 * 判定方向与常规测试相反：这里「PASS」= **确认旧实现有此缺陷**。
 * 若旧实现其实是对的（4 项都不成立），说明本次改动属于「为截图重写界面」，
 * 本脚本会报错 —— 这正是它要防的事。
 *
 * 运行：node node_modules/tsx/dist/cli.mjs tests_pending/baseline_before_fix.test.tsx <out.json>
 */

import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import { CASES } from './fixtures';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(HERE, '../src/components/PositionPanel.tsx');
const BASE_DIR = path.resolve(HERE, '_baseline');
const BASE_FILE = path.resolve(BASE_DIR, 'PositionPanel.baseline.tsx');

// ── ① 改动前的原文（逐字）────────────────────────────────────────────────────
const NEW_CONSTS = `/**
 * 挂单状态码 → 中文文案（UI-PENDING-01）。
 *
 * **只做翻译，不改语义**：状态码原样保留在行内 \`data-status\` 与 \`font-mono\` 处，
 * 便于与账本 \`paper_pending.status\` 逐条核对；未知状态码原样显示，不猜测。
 * 契约里 \`pending_orders()\` 只取 \`status='pending'\`，其余三态属防御性映射。
 */
const PENDING_STATUS: Record<string, string> = {
  pending: '等待成交',
  filled: '已成交',
  rejected: '已拒单',
  expired: '已过期',
};

/** 挂单来源 → 文案（\`origin\` 为服务端字段，缺失即「未标注」，不推断）。 */
const pendingOriginText = (origin: string | null): string => {
  if (origin === 'signal') return '实时信号触发';
  if (origin === 'manual') return '页面手动请求';
  return origin ? origin : '未标注';
};

`;

const OLD_BLOCK = `          {/* 未来待成交挂单（服务端登记）——**计划下单**层：未成交，不冒充已买入 */}
          {paperConnected && scopedPending.length > 0 && (
            <div className="space-y-1.5 pt-1" data-testid="paper-pending-block">
              <div className="text-[10px] text-neutral-300 flex items-center justify-between">
                <span className="flex items-center gap-1 font-medium">
                  <Clock className="w-3 h-3 text-amber-400" />
                  <span>计划下单 · 待成交挂单 ({scopedPending.length} 笔)</span>
                </span>
                <span className="text-[9px] text-amber-400/80 font-mono">
                  未成交 · 等真实 15m 闭合 · 防旧价回填
                </span>
              </div>
              {scopedPending.map((p) => (
                <div
                  key={\`\${p.symbol}:\${p.pendingId}\`}
                  className="p-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-[10px] font-mono text-neutral-400 space-y-0.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-neutral-200 font-bold">{p.symbol}</span>
                    <span className="text-amber-300">
                      {p.origin === 'manual' ? '页面手动请求' : '实时信号触发'} · {p.status}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>最早可执行 15m:</span>
                    <span className="text-amber-200">{fmtCompactUtcMs(p.earliestFillMs)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>请求锚点时刻:</span>
                    <span className="text-neutral-300">{fmtCompactUtcMs(p.requestedAt)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>信号确认/可得:</span>
                    <span className="text-neutral-400">
                      {fmtCompactUtcMs(p.eventTime)} / {fmtCompactUtcMs(p.availableAt)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}`;

// ── ② 生成 baseline（还原改动）──────────────────────────────────────────────
const current = fs.readFileSync(SRC, 'utf8');
const startMarker = '{/* 未来待成交挂单（服务端登记）——**计划下单**层：未成交，不冒充已买入。';
const iStart = current.indexOf(startMarker);
if (iStart < 0) {
  throw new Error('未找到挂单块起始注释：源码结构已变化，baseline 脚本需同步更新');
}
// 挂单块结束于紧随其后的「持仓历史 → 回测复盘」页脚行（该页脚在挂单块之后）
const FOOTER = '<div className="text-[10px] text-neutral-500 flex items-center justify-between pt-1">';
const iEnd = current.indexOf(FOOTER, iStart);
if (iEnd < 0) throw new Error('未找到挂单块之后的页脚行');
const newBlock = current.slice(iStart, iEnd).replace(/\n+$/, '');

if (!current.includes(NEW_CONSTS)) {
  throw new Error('未找到新增的 PENDING_STATUS 常量段：baseline 脚本需同步更新');
}

let baseline = current.replace(NEW_CONSTS, '');
baseline = baseline.replace(newBlock, OLD_BLOCK);
// 相对路径下移一层
baseline = baseline
  .replace("from './TokenIcon'", "from '../../src/components/TokenIcon'")
  .replace("from '../api/liveTrading'", "from '../../src/api/liveTrading'");

fs.mkdirSync(BASE_DIR, { recursive: true });
fs.writeFileSync(BASE_FILE, baseline, 'utf8');

// ── ③ 用同一套入参渲染 baseline ─────────────────────────────────────────────
const mod: any = await import('./_baseline/PositionPanel.baseline.tsx');
const BaselinePanel: React.FC<any> = mod.PositionPanel;

const textOf = (html: string): string =>
  html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();

const render = (c: keyof typeof CASES): string => {
  const f = CASES[c];
  return renderToStaticMarkup(
    React.createElement(BaselinePanel, {
      paperAccounts: f.accounts,
      paperPositions: [],
      paperPending: f.pending,
      paperMissingReason: f.missingReason,
      liveAccounts: [],
      liveMissingReason: null,
      orderBusy: false,
      onSelectTokenSymbol: () => undefined,
      onClosePosition: () => undefined,
      onOpenQuickPosition: () => undefined,
      isGateRunning: false,
    }),
  );
};

const emptyHtml = render('empty');
const unreadableHtml = render('unreadable');
const hasHtml = render('has_pending');

const defects = [
  {
    name: 'D1 无挂单时整块消失（「没有挂单」与「没读到」无法区分）',
    confirmed: !emptyHtml.includes('paper-pending-block'),
  },
  {
    name: 'D2 账本读取失败时不渲染任何挂单状态（失败被吞成「什么都没发生」）',
    confirmed: !unreadableHtml.includes('paper-pending-block'),
  },
  {
    name: 'D3 挂单身份 pending_id 不显示（无法与账本行逐条对齐）',
    confirmed: !textOf(hasHtml).includes('#9001'),
  },
  {
    name: 'D4 等待原因 reason 字段不显示（服务端给了也不渲染）',
    confirmed: !textOf(hasHtml).includes('test_fixture_reason_visible'),
  },
];

const total = defects.length;
const confirmed = defects.filter((d) => d.confirmed).length;

const payload = {
  ticket: 'UI-PENDING-01',
  kind: 'baseline-before-fix',
  baseline_file: path.relative(path.resolve(HERE, '..'), BASE_FILE),
  baseline_sha256_source: '见 evidence 目录 04_源码哈希.json',
  fixture: 'tests_pending/fixtures.ts（TEST FIXTURE，人工构造）',
  note: 'PASS = 确认改动前存在该缺陷；全部 confirmed 才能说明本次改动不是「为截图重写界面」',
  generated_at_utc: new Date().toISOString(),
  summary: { total, confirmed, all_confirmed: confirmed === total },
  defects,
};

const outArg = process.argv[2];
if (outArg) {
  const p = path.resolve(outArg);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(payload, null, 2), 'utf8');
  console.log(`[UI-PENDING-01/baseline] 已写入 ${p}`);
}

for (const d of defects) {
  console.log(`${d.confirmed ? 'CONFIRMED' : 'NOT-CONFIRMED'}  ${d.name}`);
}
console.log(`\n[UI-PENDING-01/baseline] 旧实现缺陷确认 ${confirmed}/${total}`);
process.exit(confirmed === total ? 0 : 1);
