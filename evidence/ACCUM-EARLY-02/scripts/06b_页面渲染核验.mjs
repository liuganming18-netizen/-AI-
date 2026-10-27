/**
 * 06b_页面渲染核验.mjs —— 观察区「市场证据」在生产页面上是否真的看得见。
 *
 * 为什么单独一个探针
 * ----------------
 * 本项目反复踩同一个坑：**接口有字段 ≠ 前端解析 ≠ 页面渲染**。
 * SIGNAL-OBSERVE-FIX-12 就是「状态机 346 个、接口没明细、前端连字段都没解析、
 * 渲染只在折叠里拼计数串」四段断裂，最后表现为「页面没有观察区」。
 *
 * 所以这一段只验页面，而且**页面上的数字必须与接口逐个相同**，
 * 不能用「页面上有这几个字」当通过。
 *
 * 要证的八件事
 * -----------
 *   ① 观察区面板在（reason=has_items），不是空态、不是故障；
 *   ② 渲染行数 == 接口条数（不截断时）；
 *   ③ **每一行**都有证据条（`observe-evidence`），状态只能是 ok / stale；
 *   ④ 面板顶部计数与接口 `observing_total` 一致；
 *   ⑤ 挑一行：页面的主动买卖 / 多空比 / 阶段均价三个数 **与接口逐个相同**；
 *   ⑥ 陈旧徽标出现，且 `data-evidence-stale-hours` 与接口 `陈旧_小时` 一致；
 *   ⑦ 点开「条件」后，逐列明细里 5 个多空比列名都在（含时刻与来源）；
 *   ⑧ 全程无 JS 报错。
 *
 * 只读：不点下单、不改任何数据。截图存 out/。
 */
import { createRequire } from 'node:module';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..', '..');
const OUT = resolve(HERE, '..', 'out');
const WEB = resolve(ROOT, 'UI界面', 'web');
const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const PROD_WEB = 'http://127.0.0.1:3000';
const PROD_API = 'http://127.0.0.1:8787';
const RATIO_COLS = [
  'retail_account_ls_ratio',
  'count_long_short_ratio',
  'top_position_ls_ratio',
  'sum_toptrader_long_short_ratio',
  'count_toptrader_long_short_ratio',
];
mkdirSync(OUT, { recursive: true });

const result = {
  probe: '06b_页面渲染核验',
  generated_real_utc: new Date().toISOString(),
  target: { frontend: PROD_WEB, backend: PROD_API },
  steps: [],
  ok: false,
};
const step = (name, ok, detail, soft = false) => {
  result.steps.push({ name, ok, detail, soft });
  console.log(`${ok ? '[OK]' : soft ? '[INFO]' : '[FAIL]'} ${name}${detail ? ' — ' + detail : ''}`);
};

let browser = null;
try {
  // ── 接口基线：页面上的数必须和它逐个相同 ────────────────────────────────
  const env = await (await fetch(`${PROD_API}/api/v1/signals/observe?arm=3a`)).json();
  const apiItems = env.items || [];
  const apiBySym = new Map(apiItems.map((x) => [x.symbol, x]));
  result.api = {
    observing_total: env.observing_total,
    shown: env.shown,
    truncated: env.truncated,
    quality_status: env.quality?.status,
    counts: env.counts,
    with_evidence: apiItems.filter((x) => x.evidence && !x.evidence.pending).length,
  };
  step('接口：有观察事件且都带 evidence',
    apiItems.length > 0 && result.api.with_evidence === apiItems.length,
    `事件 ${apiItems.length} 条，带证据 ${result.api.with_evidence} 条`);

  browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1400 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));

  await page.goto(PROD_WEB + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="observe-panel"]', { timeout: 120000 });
  await page.waitForSelector('[data-testid="observe-row"]', { timeout: 120000 });

  // ① 面板状态
  const panel = await page.$eval('[data-testid="observe-panel"]', (n) => ({
    kind: n.getAttribute('data-observe-kind'),
    reason: n.getAttribute('data-observe-reason'),
    total: n.getAttribute('data-observe-total'),
  }));
  result.page = { panel };
  step('① 观察区面板在，且是「有数据」态（不是空态/故障）',
    panel.reason === 'has_items' && panel.kind === 'ok',
    `kind=${panel.kind} reason=${panel.reason} total=${panel.total}`);

  // ② 行数
  const rows = await page.$$eval('[data-testid="observe-row"]', (ns) =>
    ns.map((n) => ({
      symbol: n.getAttribute('data-symbol'),
      state: n.getAttribute('data-state'),
      expired: n.getAttribute('data-expired'),
    })));
  result.page.row_count = rows.length;
  const expectRows = Math.min(apiItems.length, 60); // 组件默认 cap=60
  step('② 渲染行数 == 接口条数（默认上限 60）',
    rows.length === expectRows,
    `页面 ${rows.length} 行 / 接口 ${apiItems.length} 条 / 期望 ${expectRows}`);

  // ③ 每行都有证据条
  const evStates = await page.$$eval('[data-testid="observe-evidence"]', (ns) =>
    ns.map((n) => ({
      state: n.getAttribute('data-evidence-state'),
      stale: n.getAttribute('data-evidence-stale-hours'),
      text: (n.textContent || '').replace(/\s+/g, ' ').trim(),
    })));
  result.page.evidence_count = evStates.length;
  result.page.evidence_states = [...new Set(evStates.map((e) => e.state))];
  const badState = evStates.filter((e) => !['ok', 'stale'].includes(e.state));
  step('③ 每一行都有证据条，且状态只有 ok / stale',
    evStates.length === rows.length && badState.length === 0,
    `证据条 ${evStates.length} / 行 ${rows.length}；异常状态 ${badState.length}`);

  // ④ 顶部计数
  const totalText = (await page.$eval('[data-testid="observe-total"]', (n) =>
    (n.textContent || '').trim())) || '';
  const totalNum = Number((totalText.match(/(\d+)/) || [])[1]);
  step('④ 面板顶部「观察中 N」== 接口 observing_total',
    totalNum === env.observing_total,
    `页面「${totalText}」/ 接口 ${env.observing_total}`);

  // ⑤ 挑一行逐值比对（优先挑有陈旧标注的，覆盖面更大）
  const idx = evStates.findIndex((e) => e.state === 'stale');
  const pick = idx >= 0 ? idx : 0;
  const pickSym = rows[pick].symbol;
  const apiIt = apiBySym.get(pickSym);
  const apiEv = apiIt?.evidence || {};
  const apiM = apiEv['主动买卖与多空比'] || {};
  const apiTaker = apiM['主动买卖']?.值 ?? null;
  const apiRatios = apiM['三类多空比'] || {};
  const apiPhase = apiEv['阶段均价']?.['阶段均价'] ?? null;
  const apiStale = apiM['陈旧_小时'] ?? null;

  const evText = evStates[pick].text;
  // 紧凑行格式：证据 主动买卖 0.53 · 多空比 3.74/1.37/3.59 · 阶段均价 0.1040
  const mTaker = evText.match(/主动买卖\s*(-?[\d.]+)/);
  const mRatio = evText.match(/多空比\s*([\d./]+)/);
  const mPhase = evText.match(/阶段均价\s*([\d.eE+-]+)/);
  const pageTaker = mTaker ? Number(mTaker[1]) : null;
  const pageRatios = mRatio ? mRatio[1].split('/').map(Number) : [];
  const pagePhase = mPhase ? Number(mPhase[1]) : null;

  const near = (a, b, tol) => a !== null && b !== null && Math.abs(a - b) <= tol;
  const takerOk = near(pageTaker, apiTaker, 0.005);
  // 多空比：接口 5 列去重后的值，应与页面逐个相同（页面按同值去重）
  const apiIndep = [];
  for (const k of RATIO_COLS) {
    const v = apiRatios[k]?.值 ?? null;
    if (v === null) continue;
    if (!apiIndep.some((x) => Math.abs(x - v) < 1e-9)) apiIndep.push(v);
  }
  const ratiosOk =
    apiIndep.length === pageRatios.length &&
    apiIndep.every((v, i) => near(pageRatios[i], v, 0.005));
  const phaseOk = near(pagePhase, apiPhase, Math.max(1e-9, Math.abs(apiPhase ?? 0) * 1e-4));

  result.page.pick = {
    symbol: pickSym,
    api_taker: apiTaker,
    page_taker: pageTaker,
    api_ratios_indep: apiIndep,
    page_ratios: pageRatios,
    api_phase: apiPhase,
    page_phase: pagePhase,
    api_stale_hours: apiStale,
    page_stale_hours: evStates[pick].stale,
    evidence_text: evText,
  };
  step('⑤ 页面的主动买卖 == 接口值', takerOk, `页面 ${pageTaker} / 接口 ${apiTaker}`);
  step('⑤b 页面的多空比（去重后）== 接口值', ratiosOk,
    `页面 [${pageRatios}] / 接口去重 [${apiIndep}]`);
  step('⑤c 页面的阶段均价 == 接口值', phaseOk, `页面 ${pagePhase} / 接口 ${apiPhase}`);

  // ⑥ 陈旧徽标
  const staleBadge = await page.$('[data-testid="observe-evidence-stale"]');
  const staleText = staleBadge ? (await staleBadge.textContent()).trim() : '';
  const staleAttr = evStates[pick].stale;
  step('⑥ 陈旧徽标出现，且小时数与接口一致',
    !!staleBadge && staleAttr !== '' && Math.abs(Number(staleAttr) - apiStale) < 0.1,
    `徽标「${staleText}」/ 页面 ${staleAttr} / 接口 ${apiStale}`);

  // ⑦ 展开明细：5 个多空比列名都在
  const rowEl = (await page.$$('[data-testid="observe-row"]'))[pick];
  await rowEl.$eval('[data-testid="observe-row-toggle"]', (b) => b.click());
  await page.waitForSelector('[data-testid="observe-evidence-detail"]', { timeout: 20000 });
  const detailText = await page.$eval('[data-testid="observe-evidence-detail"]', (n) =>
    (n.textContent || '').replace(/\s+/g, ' ').trim());
  const missingCols = RATIO_COLS.filter((c) => !detailText.includes(c));
  const hasSource = /来源/.test(detailText);
  const hasTimeRange = /时间范围/.test(detailText);
  const hasAvailability = /可得性/.test(detailText);
  result.page.detail_text = detailText.slice(0, 1200);
  step('⑦ 展开后逐列明细含 5 个多空比列名', missingCols.length === 0,
    missingCols.length ? `缺：${missingCols.join('、')}` : '5 列齐全');
  step('⑦b 明细含「时间范围 / 来源 / 可得性」三项',
    hasTimeRange && hasSource && hasAvailability,
    `时间范围=${hasTimeRange} 来源=${hasSource} 可得性=${hasAvailability}`);

  // ⑧ JS 报错
  step('⑧ 全程无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | ') || '无');

  // 截图
  const shot = resolve(OUT, '06b_观察证据_页面.png');
  await page.screenshot({ path: shot, fullPage: false });
  result.page.screenshot = shot;

  const fails = result.steps.filter((s) => !s.ok && !s.soft);
  result.ok = fails.length === 0;
  result.fail_count = fails.length;
} catch (e) {
  result.error = String(e && e.stack ? e.stack : e);
  console.log('[FAIL] 探针异常 — ' + result.error.slice(0, 400));
} finally {
  if (browser) await browser.close();
  writeFileSync(resolve(OUT, '06b_页面渲染核验.json'),
    JSON.stringify(result, null, 1), 'utf-8');
  console.log(`\n写入 ${resolve(OUT, '06b_页面渲染核验.json')}`);
  console.log(result.ok ? '结论=通过' : `结论=不通过（${result.fail_count ?? '?'} 项）`);
}
