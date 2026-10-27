/**
 * 70_prod_page.mjs —— 生产页面：0 张卡 + 8 行诊断 + 51 条历史明细（SIGNAL-UI-LINK-07-R1 要求 4）
 *
 * 与前两个页面探针的区别：**本脚本不换源**，直接打开生产 3000 端口、走生产 8787，
 * 验的就是「用户此刻打开页面看到的东西」。
 *
 * 要证的六件事：
 *   ① 生产没有自然候选是**正常空列表**（`data-signals-state=ok`），不是故障；
 *   ② 不是「什么都没说」：空态诊断 8 行都在（最近处理到 / 行情最新 / 当前阶段 /
 *      本进程漏斗 / 历史回放 / 最近候选 / 空转原因 / 样例）；
 *   ③ 历史明细能展开，条数 == 接口给的 51，**ID 集合逐个相同**；
 *   ④ 每条明细都能读到 ID、时间、不可执行原因（不是只有个数字）；
 *   ⑤ 这 51 条**没有一张卡**出现在当前可执行列表里（不能冒充当前信号）；
 *   ⑥ 页面无错误框、无 JS 报错；刷新后仍然一致。
 *
 * 本脚本只读，不写任何数据库。
 */
import { createRequire } from 'node:module';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..', '..');
const OUT = resolve(HERE, '..', 'out');
const WEB = resolve(ROOT, 'UI界面', 'web');
const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const PROD_WEB = 'http://127.0.0.1:3000';
const PROD_API = 'http://127.0.0.1:8787';
mkdirSync(OUT, { recursive: true });

const result = {
  probe: '70_prod_page',
  generated_real_utc: new Date().toISOString(),
  target: { frontend: PROD_WEB, backend: PROD_API, rerouted: false },
  steps: [],
  ok: false,
};
const step = (name, ok, detail, soft = false) => {
  result.steps.push({ name, ok, detail, soft });
  console.log(`${ok ? '[OK]' : soft ? '[INFO]' : '[FAIL]'} ${name}${detail ? ' — ' + detail : ''}`);
};

let browser = null;
try {
  // ── 接口基线（用来和页面比）────────────────────────────────────────────────
  const cur = await (await fetch(`${PROD_API}/api/v1/signals?scope=current&limit=500`)).json();
  const st = await (await fetch(`${PROD_API}/api/v1/paper/status`)).json();
  const bs = (st?.item?.signal_engine?.bootstrap) || {};
  const detail = bs.discarded_detail || [];
  const apiIds = detail.map((d) => d.candidate_id).sort();
  result.api = {
    current_total: cur.total,
    quality_status: cur.quality?.status,
    missing_reason: cur.missing_reason,
    note_len: (cur.quality?.note || '').length,
    paper_epoch_ms: cur.quality?.paper_epoch_ms,
    discarded_count: bs.history_candidates_discarded,
    discarded_detail_len: detail.length,
    discarded_ids_sample: apiIds.slice(0, 3),
  };
  step('接口：当前 0 条、status=ok、missing_reason 为空',
    cur.total === 0 && cur.quality?.status === 'ok' && !cur.missing_reason,
    `total=${cur.total} status=${cur.quality?.status} ` +
    `missing_reason=${JSON.stringify(cur.missing_reason)} note长度=${(cur.quality?.note || '').length}`);
  step('接口：历史明细 51 条',
    bs.history_candidates_discarded === 51 && detail.length === 51,
    `计数=${bs.history_candidates_discarded} 明细=${detail.length}`);

  // ── 页面 ──────────────────────────────────────────────────────────────────
  browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1200 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));

  await page.goto(PROD_WEB + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 60000 });
  // 空态要等诊断接口回来（守护状态接口首次返回较慢）
  await page.waitForSelector('[data-testid="signal-empty-diagnostics"]', { timeout: 120000 });

  const panelState = await page.$eval(
    '[data-testid="signal-panel"]', (n) => n.getAttribute('data-signals-state'));
  const cardCount = await page.$$eval('[data-testid="signal-row"]', (n) => n.length);
  const hasErr = !!(await page.$('[data-testid="signal-error"]'));
  const diagRows = await page.$$eval(
    '[data-testid="signal-empty-diagnostics"] > div',
    (nodes) => nodes.map((n) => (n.textContent || '').replace(/\s+/g, ' ').trim()),
  );

  result.page = {
    panel_state: panelState,
    card_count: cardCount,
    has_error_box: hasErr,
    diag_rows: diagRows,
    diag_row_count: diagRows.length,
  };

  step('① 页面状态 = ok（正常空列表，不是故障）', panelState === 'ok',
    `data-signals-state=${panelState}`);
  step('①b 当前可执行列表 0 张卡', cardCount === 0, `${cardCount} 张`);
  step('①c 没有错误框（signal-error 不存在）', !hasErr, hasErr ? '出现了错误框' : '无');

  const diagText = diagRows.join(' | ');
  const wanted = ['最近处理到', '行情最新', '当前阶段', '本进程漏斗', '历史回放',
    '最近候选', '空转原因'];
  const missingRows = wanted.filter((w) => !diagText.includes(w));
  step('② 空态诊断关键行都在（不是「什么都没说」）',
    missingRows.length === 0, `缺失：${missingRows.join('、') || '无'}；共 ${diagRows.length} 行`);
  step('②b 诊断说明了「引擎卡在哪一关」', /空转原因|历史回放/.test(diagText),
    (diagRows.find((r) => r.includes('空转原因')) || '').slice(0, 90));

  // ── 历史明细展开 ──────────────────────────────────────────────────────────
  const toggle = await page.$('[data-testid="signal-discarded-toggle"]');
  step('③ 有「展开历史回放候选明细」入口', !!toggle,
    toggle ? (await toggle.textContent()).trim().slice(0, 60) : '未找到');
  if (toggle) {
    const label = (await toggle.textContent()).trim();
    step('③b 入口文案里的条数 == 51', /（51 条/.test(label), label.slice(0, 60));
    await toggle.click();
    await page.waitForSelector('[data-testid="signal-discarded-row"]', { timeout: 30000 });
    const rows = await page.$$eval('[data-testid="signal-discarded-row"]', (nodes) =>
      nodes.map((n) => ({
        id: n.getAttribute('data-candidate-id'),
        text: (n.textContent || '').replace(/\s+/g, ' ').trim(),
      })),
    );
    result.page.discarded_rows = rows.length;
    result.page.discarded_ids_sample = rows.slice(0, 3).map((r) => r.id);
    const pageIds = rows.map((r) => r.id).sort();

    step('③c 明细条数 == 51', rows.length === 51, `${rows.length} 条`);
    step('③d 明细 ID 无空、无重复',
      pageIds.every(Boolean) && new Set(pageIds).size === pageIds.length,
      `${pageIds.length} 个 / 去重 ${new Set(pageIds).size} 个`);
    step('③e 页面 ID 集合 == 接口 ID 集合（逐个相同）',
      JSON.stringify(pageIds) === JSON.stringify(apiIds),
      `页面 ${pageIds.length} 个 / 接口 ${apiIds.length} 个，差异 ` +
      `${pageIds.filter((x) => !apiIds.includes(x)).length} + ` +
      `${apiIds.filter((x) => !pageIds.includes(x)).length}`);
    step('④ 每条明细都写了时间与不可执行原因',
      rows.every((r) => /\d{2}-\d{2}\s+\d{2}:\d{2}/.test(r.text) &&
        /(不补开|错过成交|早于启用)/.test(r.text)),
      `样例：${(rows[0]?.text || '').slice(0, 110)}`);
    step('⑤ 这 51 条没有一张出现在当前列表（不冒充当前信号）',
      cardCount === 0, `当前卡片 ${cardCount} 张`);

    const shot = '70_prod_page_discarded.png';
    await page.screenshot({ path: resolve(OUT, shot), fullPage: false });
    step('截图已保存（明细展开态）', true, 'out/' + shot);
  }

  // ── 刷新后仍一致 ──────────────────────────────────────────────────────────
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-testid="signal-empty-diagnostics"]', { timeout: 120000 });
  const state2 = await page.$eval(
    '[data-testid="signal-panel"]', (n) => n.getAttribute('data-signals-state'));
  const cards2 = await page.$$eval('[data-testid="signal-row"]', (n) => n.length);
  const diag2 = await page.$$eval(
    '[data-testid="signal-empty-diagnostics"] > div', (n) => n.length);
  step('⑥ 刷新后仍是 ok / 0 张卡 / 诊断行数不变',
    state2 === 'ok' && cards2 === 0 && diag2 === diagRows.length,
    `state=${state2} cards=${cards2} diag=${diag2}（刷新前 ${diagRows.length}）`);
  step('⑥b 页面无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | ') || '无');

  // 轮询：靠页面自己的 5s 定时器，不 reload
  await sleep(12000);
  const state3 = await page.$eval(
    '[data-testid="signal-panel"]', (n) => n.getAttribute('data-signals-state'));
  const cards3 = await page.$$eval('[data-testid="signal-row"]', (n) => n.length);
  step('⑥c 两轮轮询后状态稳定（不闪成故障、不凭空冒出卡片）',
    state3 === 'ok' && cards3 === 0, `state=${state3} cards=${cards3}`);
} catch (e) {
  step('异常中止', false, String(e && e.message ? e.message : e));
} finally {
  if (browser) {
    try {
      for (const p of browser.contexts()) {
        for (const pg of p.pages()) await pg.unrouteAll({ behavior: 'ignoreErrors' });
      }
    } catch { /* ignore */ }
    await browser.close().catch(() => {});
  }
}

result.ok = result.steps.filter((s) => !s.soft).every((s) => s.ok);
writeFileSync(resolve(OUT, '70_prod_page.json'), JSON.stringify(result, null, 2), 'utf8');
console.log('\n== 结果：' + (result.ok ? '全部通过' : '存在失败项') + ' ==');
console.log('写出：evidence/SIGNAL-UI-LINK-07-R1/out/70_prod_page.json');
process.exit(result.ok ? 0 : 1);
