/**
 * 40_browser_page_check.mjs —— 真实浏览器核验「交易信号」页面（SIGNAL-UI-LINK-07）
 *
 * 目的（用户第 5 条要求）：
 *   用**实际浏览器**打开页面，确认「当前没有可执行信号」这件事在页面上被**解释清楚**，
 *   而不是一句「暂无实时信号」；并确认历史回放候选的**明细**能在页面上展开、
 *   条数与接口一致、候选 ID 可逐条核对。
 *
 * 纪律：
 *   · 只读页面，不点任何交易/开仓按钮，不修改任何后端状态；
 *   · 不做任何「等待后重试直到通过」的宽容处理：拿不到就如实失败；
 *   · 页面上的数字**不在这里重算**，只做「页面 ↔ 接口」逐字段对撞。
 *
 * 用法：
 *   node 40_browser_page_check.mjs [--url http://127.0.0.1:3000/] [--api http://127.0.0.1:8787]
 */
import { createRequire } from 'node:module';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, '..', 'out');
const WEB = resolve(HERE, '..', '..', '..', 'UI界面', 'web');
const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const argv = process.argv.slice(2);
const argOf = (name, dflt) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt;
};
const URL_PAGE = argOf('--url', 'http://127.0.0.1:3000/');
const API = argOf('--api', 'http://127.0.0.1:8787');

mkdirSync(OUT, { recursive: true });

const result = {
  probe: '40_browser_page_check',
  generated_at_utc: new Date().toISOString(),
  page_url: URL_PAGE,
  api_base: API,
  steps: [],
  ok: false,
};

const step = (name, ok, detail) => {
  result.steps.push({ name, ok, detail });
  console.log(`${ok ? '[OK]' : '[FAIL]'} ${name}${detail ? ' — ' + detail : ''}`);
};

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();

// 抓网络：页面自己发出的信号请求 / 守护状态请求，原样记录，用于对账
const net = { signals: [], daemon: [] };
page.on('response', async (resp) => {
  const u = resp.url();
  try {
    if (u.includes('/api/v1/signals')) {
      const body = await resp.json();
      net.signals.push({
        url: u,
        status: resp.status(),
        total: body?.total ?? null,
        items: (body?.items || []).length,
        as_of: body?.as_of ?? null,
        scope: body?.quality?.scope ?? null,
      });
    } else if (u.includes('/paper/execution_daemon/status') || u.includes('/paper/status')) {
      const body = await resp.json();
      const se = body?.item?.signal_engine || {};
      net.daemon.push({
        url: u,
        status: resp.status(),
        watermark_1h: se?.watermark_1h ?? null,
        watermark_4h: se?.watermark_4h ?? null,
        funnel: se?.funnel ?? null,
        idle_counts: se?.idle_breakdown?.counts ?? null,
        state_counter: se?.state_counter ?? null,
        discarded_detail_n: se?.bootstrap?.discarded_detail_n ?? null,
        history_candidates_discarded: se?.bootstrap?.history_candidates_discarded ?? null,
        discarded_ids: (se?.bootstrap?.discarded_detail || []).map((d) => d?.candidate_id),
      });
    }
  } catch {
    /* 非 JSON 响应忽略 */
  }
});

let pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => {
  if (m.type() === 'error') pageErrors.push('[console] ' + m.text());
});

try {
  await page.goto(URL_PAGE, { waitUntil: 'domcontentloaded', timeout: 45000 });
  step('打开页面', true, URL_PAGE);

  // 等信号面板出现（不重试到通过，只给一次固定上限）
  await page.waitForSelector('[data-testid="signal-empty"]', { timeout: 45000 });
  step('空态诊断块出现', true, '[data-testid="signal-empty"]');

  // 首帧必须是「正在读取」而不是「接口未返回」（不得把加载中诬告成故障）
  const pendingShown = await page.$('[data-testid="signal-empty-pending"]');
  const nodiagShown = await page.$('[data-testid="signal-empty-nodiag"]');
  result.first_paint = {
    pending_block: !!pendingShown,
    nodiag_block: !!nodiagShown,
  };
  step(
    '首帧不诬告接口故障',
    !nodiagShown,
    pendingShown ? '显示「正在读取逐币诊断」' : nodiagShown ? '显示「未取到」' : '两者都无',
  );

  // 等诊断行真正渲染（实测首次约 13 秒：API 侧 /markets 重查询占满线程后排队）
  const t0 = Date.now();
  await page.waitForSelector('[data-testid="signal-empty-diagnostics"]', { timeout: 90000 });
  const diagLatencyMs = Date.now() - t0;
  result.diagnostics_latency_ms = diagLatencyMs;
  step('逐币诊断渲染', true, `等待 ${(diagLatencyMs / 1000).toFixed(1)} 秒`);

  // 逐行读「标签 → 值」，不做任何解析推断
  const rows = await page.$$eval('[data-testid="signal-empty-diagnostics"] > div', (nodes) =>
    nodes
      .filter((n) => n.children.length === 2 && n.className.includes('justify-between'))
      .map((n) => ({
        label: (n.children[0].textContent || '').trim(),
        value: (n.children[1].textContent || '').trim(),
      })),
  );
  result.diagnostic_rows = rows;
  step('诊断行数', rows.length >= 6, `${rows.length} 行：${rows.map((r) => r.label).join(' / ')}`);

  const needLabels = ['最近处理到', '行情最新', '当前阶段', '本进程漏斗', '历史回放', '最近候选', '空转原因'];
  const labels = rows.map((r) => r.label);
  const missing = needLabels.filter((l) => !labels.includes(l));
  step('必需诊断行齐全', missing.length === 0, missing.length ? '缺：' + missing.join(',') : '7 项齐全');

  const getVal = (l) => (rows.find((r) => r.label === l) || {}).value ?? '';
  result.key_values = {
    最近处理到: getVal('最近处理到'),
    行情最新: getVal('行情最新'),
    当前阶段: getVal('当前阶段'),
    本进程漏斗: getVal('本进程漏斗'),
    历史回放: getVal('历史回放'),
    最近候选: getVal('最近候选'),
    空转原因: getVal('空转原因'),
    样例: getVal('样例'),
  };

  // 空态**不得**再出现旧的一句话文案（否则等于没解释）
  const bodyText = await page.innerText('body');
  const hasLegacy = bodyText.includes('暂无实时信号');
  step('旧「暂无实时信号」已消失', !hasLegacy, hasLegacy ? '仍存在旧文案' : '无旧文案');
  const hasHeader = bodyText.includes('当前没有可执行信号');
  step('新空态标题存在', hasHeader, hasHeader ? '当前没有可执行信号' : '缺失');

  // 展开历史回放候选明细
  const toggle = await page.$('[data-testid="signal-discarded-toggle"]');
  step('历史候选展开入口存在', !!toggle, toggle ? await toggle.innerText() : '未找到');
  let discardedIds = [];
  if (toggle) {
    await toggle.click();
    await page.waitForSelector('[data-testid="signal-discarded-list"]', { timeout: 15000 });
    discardedIds = await page.$$eval('[data-testid="signal-discarded-row"]', (nodes) =>
      nodes.map((n) => n.getAttribute('data-candidate-id')),
    );
    result.discarded_ids_on_page = discardedIds;
    const uniq = new Set(discardedIds);
    step(
      '明细行数 / 去重',
      discardedIds.length === uniq.size,
      `${discardedIds.length} 行，去重 ${uniq.size} 个 ID`,
    );
    const firstText = await page.$eval('[data-testid="signal-discarded-row"]', (n) =>
      (n.textContent || '').trim(),
    );
    result.first_discarded_text = firstText;
  }

  await page.screenshot({ path: resolve(OUT, '08_page_empty_diagnostics.png'), fullPage: false });
  step('截图已保存', true, 'out/08_page_empty_diagnostics.png');

  // 与接口对撞：页面上的明细 ID 集合必须与守护状态接口一致
  const dResp = net.daemon[net.daemon.length - 1];
  if (dResp) {
    const apiIds = dResp.discarded_ids || [];
    const sameLen = apiIds.length === discardedIds.length;
    const sameSet =
      sameLen && apiIds.every((x, i) => x === discardedIds[i]);
    step(
      '页面明细 ↔ 接口明细一致',
      sameSet,
      `接口 ${apiIds.length} 条 / 页面 ${discardedIds.length} 条；逐位相同=${sameSet}`,
    );
    result.cross_check = {
      api_detail_n: apiIds.length,
      page_detail_n: discardedIds.length,
      order_identical: sameSet,
      api_history_candidates_discarded: dResp.history_candidates_discarded,
      api_detail_n_field: dResp.discarded_detail_n,
    };
    // 页面上「历史回放 … 丢弃 N」的 N 必须等于接口的 history_candidates_discarded
    const m = /丢弃\s*(\d+)/.exec(getVal('历史回放') || '');
    const nPage = m ? Number(m[1]) : null;
    step(
      '「丢弃 N」↔ 接口',
      nPage !== null && nPage === dResp.history_candidates_discarded,
      `页面 ${nPage} / 接口 ${dResp.history_candidates_discarded}`,
    );
  } else {
    step('页面发起了守护状态请求', false, '未捕获到 /paper/execution_daemon/status 响应');
  }

  const sResp = net.signals[net.signals.length - 1];
  step(
    '页面发起的信号请求',
    !!sResp,
    sResp ? `${sResp.url} → ${sResp.status}，total=${sResp.total}，as_of=${sResp.as_of}` : '未捕获',
  );
  result.page_network = net;

  step('页面无 JS 报错', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | ') || '无');
  result.page_errors = pageErrors;

  result.ok = result.steps.every((s) => s.ok);
} catch (e) {
  step('异常中止', false, String(e && e.stack ? e.stack.split('\n')[0] : e));
  try {
    await page.screenshot({ path: resolve(OUT, '08_page_empty_diagnostics_FAILED.png') });
  } catch {
    /* ignore */
  }
  result.ok = false;
} finally {
  await browser.close();
}

writeFileSync(resolve(OUT, '08_browser_page.json'), JSON.stringify(result, null, 2), 'utf8');
console.log('\n== 结果：' + (result.ok ? '全部通过' : '存在失败项') + ' ==');
console.log('写出：evidence/SIGNAL-UI-LINK-07/out/08_browser_page.json');
process.exit(result.ok ? 0 : 1);
