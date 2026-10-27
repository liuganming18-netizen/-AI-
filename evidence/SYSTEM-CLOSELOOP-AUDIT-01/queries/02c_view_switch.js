/**
 * 02c_view_switch.js —— 视图切换判定（真实点击 + 内容区指纹 + 行级差异）
 *
 * 为什么重做：02 轮用「整页 body.innerText 是否变化」判页签切换，被顶栏
 * WebSocket 秒数（"2 秒前" / "11:20:xx"）污染 ⇒ 恒 changed=true，无法区分
 * 「真切换」与「只是时钟在跳」。
 *
 * 本轮改用**内容区容器指纹**：
 *   容器 = document.querySelector('[data-testid="top-status-bar"]').nextElementSibling
 *        （02b 实测：内容列 = [header[data-testid=top-status-bar], div=视图容器]；
 *          注意顶栏是 data-testid，不是 id —— 第一版按 id 查，全部拿到 ok:false）
 *
 * 判据（机械可复算，不看感觉）：
 *   无变化      = 内容区文本 SHA 完全相同
 *   仅计数微变  = SHA 变了但**行级差异为 0**（只是数字/时钟在跳）
 *   真切换      = SHA 变了 **且** 新增或删除的文本行 ≥ 1
 *
 * 顺序纪律：每组先「停到别的选项」，再点目标项，避免"点默认页签＝无变化"被误判。
 *
 * 用法：NODE_PATH=<web/node_modules> node 02c_view_switch.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const BASE = process.env.UI_BASE || 'http://127.0.0.1:3000';
const OUT = path.join(__dirname, '..', 'out');
const SHOTS = path.join(__dirname, '..', 'shots');

const sha1 = (s) => crypto.createHash('sha1').update(s).digest('hex').slice(0, 16);

// ── 浏览器内函数（序列化执行，不引用外部变量）──────────────────────

function READ() {
  const bar =
    document.querySelector('[data-testid="top-status-bar"]') ||
    document.querySelector('#top-status-bar');
  const c = bar ? bar.nextElementSibling : null;
  if (!c) return { ok: false };
  const raw = c.innerText || '';
  const lines = raw
    .split('\n')
    .map((s) => s.replace(/\s+/g, ' ').trim())
    .filter(Boolean);
  return {
    ok: true,
    raw,
    flat: lines.join(' '),
    lines,
    textLen: raw.replace(/\s+/g, ' ').trim().length,
    tables: c.querySelectorAll('table').length,
    rows: c.querySelectorAll('tr').length,
    buttons: c.querySelectorAll('button').length,
    h: Array.from(c.querySelectorAll('h1,h2,h3'))
      .map((e) => (e.innerText || '').trim())
      .filter(Boolean)
      .slice(0, 12),
  };
}

/** 按文本点按钮；取文本最短的候选（页签标签最短）。返回点击前的 aria-selected。 */
function CLICK_BY_TEXT(needles) {
  const btns = Array.from(document.querySelectorAll('button,[role="tab"]'));
  for (const nd of needles) {
    const cands = btns.filter((b) => (b.innerText || b.textContent || '').includes(nd));
    if (cands.length) {
      cands.sort((x, y) => (x.innerText || '').length - (y.innerText || '').length);
      const el = cands[0];
      const wasSelected = el.getAttribute('aria-selected');
      const label = (el.innerText || '').replace(/\s+/g, ' ').trim();
      el.click();
      return { clicked: true, needle: nd, label, candidates: cands.length, wasSelected };
    }
  }
  return {
    clicked: false,
    needle: needles[0],
    candidates: 0,
    sample: btns
      .map((b) => (b.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .slice(0, 80),
  };
}

function CLICK_BY_TESTID(tid) {
  const el = document.querySelector(`[data-testid="${tid}"]`);
  if (!el) return { clicked: false, testid: tid };
  const wasSelected = el.getAttribute('aria-selected');
  el.click();
  return {
    clicked: true,
    testid: tid,
    label: (el.innerText || '').replace(/\s+/g, ' ').trim(),
    wasSelected,
  };
}

function CLICK_NAV(label) {
  const aside = document.querySelector('aside');
  if (!aside) return { clicked: false, label, reason: 'no aside' };
  const btns = Array.from(aside.querySelectorAll('button,div[role="button"],a'));
  const hit = btns.find((b) => (b.innerText || '').trim().startsWith(label));
  if (!hit) {
    return {
      clicked: false,
      label,
      sample: btns.map((b) => (b.innerText || '').trim()).filter(Boolean),
    };
  }
  hit.click();
  return { clicked: true, label };
}

// ── 行级差异（多重集差）────────────────────────────────────────────
function lineDiff(before, after) {
  const cnt = (arr) => {
    const m = new Map();
    for (const x of arr) m.set(x, (m.get(x) || 0) + 1);
    return m;
  };
  const A = cnt(before);
  const B = cnt(after);
  let removed = 0;
  let added = 0;
  const removedSample = [];
  const addedSample = [];
  for (const [k, v] of A) {
    const w = B.get(k) || 0;
    if (v > w) {
      removed += v - w;
      if (removedSample.length < 5) removedSample.push(k.slice(0, 120));
    }
  }
  for (const [k, v] of B) {
    const w = A.get(k) || 0;
    if (v > w) {
      added += v - w;
      if (addedSample.length < 5) addedSample.push(k.slice(0, 120));
    }
  }
  // 交集 = (前长 + 后长 − 删 − 增) / 2
  const inter = (before.length + after.length - removed - added) / 2;
  const union = inter + removed + added;
  return {
    added,
    removed,
    addedSample,
    removedSample,
    jaccard: union ? Number((inter / union).toFixed(4)) : 1,
  };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300));
  });

  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000); // 首屏行情表要时间，太短会拿到骨架

  const results = [];

  async function measure() {
    const s = await page.evaluate(READ);
    if (!s.ok) return { ok: false, raw: '', flat: '', lines: [], textLen: 0, hash: 'NO_CONTAINER', tables: 0, rows: 0, buttons: 0, h: [] };
    return {
      ok: true,
      raw: s.raw,
      flat: s.flat,
      lines: s.lines,
      textLen: s.textLen,
      hash: sha1(s.flat),
      tables: s.tables,
      rows: s.rows,
      buttons: s.buttons,
      h: s.h,
    };
  }

  async function switchAndMeasure(key, note, evalFn, arg) {
    const before = await measure();
    const clickInfo = await page.evaluate(evalFn, arg);
    await page.waitForTimeout(1500);
    const after = await measure();
    const d = lineDiff(before.lines, after.lines);
    let verdict;
    if (!clickInfo.clicked) verdict = '点不到';
    else if (before.hash === after.hash) verdict = '无变化';
    else if (d.added === 0 && d.removed === 0) verdict = '仅计数微变';
    else verdict = '真切换';

    const rec = {
      key,
      note,
      clickInfo,
      before: { hash: before.hash, textLen: before.textLen, tables: before.tables, rows: before.rows, h: before.h, head: before.flat.slice(0, 200) },
      after: { hash: after.hash, textLen: after.textLen, tables: after.tables, rows: after.rows, h: after.h, head: after.flat.slice(0, 200) },
      hashChanged: before.hash !== after.hash,
      lineDiff: { added: d.added, removed: d.removed, jaccard: d.jaccard, addedSample: d.addedSample, removedSample: d.removedSample },
      verdict,
      realSwitch: verdict === '真切换',
      ts: new Date().toISOString(),
    };
    await page.screenshot({ path: path.join(SHOTS, `02c_${key.replace(/[^\w.]/g, '_')}.png`) });
    console.log(
      `[${verdict}] ${key.padEnd(24)} click=${(clickInfo.clicked ? clickInfo.label || clickInfo.testid : '✗').replace(/\n/g, ' ')} | len ${before.textLen}→${after.textLen} | rows ${before.rows}→${after.rows} | 行增${d.added}删${d.removed} | J=${d.jaccard}`,
    );
    results.push(rec);
    return rec;
  }

  const navTo = async (label) => {
    await page.evaluate(CLICK_NAV, label);
    await page.waitForTimeout(1500);
  };

  // ── 1. 四个顶级页面（先停到别的页，避免"点当前页＝无变化"）────────
  await navTo('数据中心');
  for (const [key, label] of [
    ['nav.workbench', '交易工作台'],
    ['nav.datacenter', '数据中心'],
    ['nav.strategy', '策略管理'],
    ['nav.backtest', '回测复盘'],
  ]) {
    await switchAndMeasure(key, `导航：${label}`, CLICK_NAV, label);
  }

  // ── 2. 数据中心 8 个子视图（先停到"指标"以外的默认项，逐个点）─────
  await navTo('数据中心');
  const DC = [
    ['dc.backfill', ['核销', '待补']],
    ['dc.matrix', ['协同矩阵', '全资产协同']],
    ['dc.kline', ['K线']],
    ['dc.oi', ['OI']],
    ['dc.funding', ['费率']],
    ['dc.smartmoney', ['聪明钱']],
    ['dc.metrics', ['指标']],
    ['dc.feeds', ['红绿灯', '主视窗', '全域数据名称']], // 最后点回默认项 ⇒ 此时它确实不是当前项
  ];
  for (const [key, needles] of DC) {
    await switchAndMeasure(key, `数据中心子视图：${needles[0]}`, CLICK_BY_TEXT, needles);
  }

  // ── 3. 策略管理 3 个页签（默认 rules；依次点 deps → versions → rules）──
  await navTo('策略管理');
  const stratBase = await measure();
  for (const [key, needles] of [
    ['st.deps', ['数据依赖']],
    ['st.versions', ['版本对比']],
    ['st.rules', ['规则说明']],
  ]) {
    const rec = await switchAndMeasure(key, `策略管理页签：${needles[0]}`, CLICK_BY_TEXT, needles);
    rec.sameHashAsStrategyBase = rec.after.hash === stratBase.hash;
    rec.sameTextLenAsBase = rec.after.textLen === stratBase.textLen;
  }

  // ── 4. 回测复盘：两个面板 ───────────────────────────────────────
  await navTo('回测复盘');
  await page.waitForTimeout(800);
  await switchAndMeasure('bt.workbench.batch', '回测工作台：按批次', CLICK_BY_TESTID, 'bt-batch-mode-batch');
  await switchAndMeasure('bt.workbench.run', '回测工作台：按单 run', CLICK_BY_TESTID, 'bt-batch-mode-run');
  await switchAndMeasure('bt.review', '切到验收复盘', CLICK_BY_TESTID, 'backtest-pane-review');

  // ── 5. 验收复盘 4 个页签（默认 overview）─────────────────────────
  for (const [key, needles] of [
    ['bt.review.trades', ['交易复盘']],
    ['bt.review.signals', ['信号链']],
    ['bt.review.diagnostics', ['稳定性诊断']],
    ['bt.review.overview', ['总览']],
  ]) {
    await switchAndMeasure(key, `验收复盘页签：${needles[0]}`, CLICK_BY_TEXT, needles);
  }

  // ── 6. 切回历史回测 ────────────────────────────────────────────
  await switchAndMeasure('bt.back.workbench', '切回历史回测', CLICK_BY_TESTID, 'backtest-pane-workbench');

  const summary = {
    生成时刻: new Date().toISOString(),
    前端: BASE,
    判据: {
      无变化: '内容区文本 SHA 完全相同',
      仅计数微变: 'SHA 变了但行级差异为 0（只是数字/时钟在跳）',
      真切换: 'SHA 变了且新增或删除的文本行 ≥ 1',
      点不到: '目标按钮在当前视图中不存在',
    },
    计数: {
      总数: results.length,
      真切换: results.filter((r) => r.verdict === '真切换').length,
      仅计数微变: results.filter((r) => r.verdict === '仅计数微变').length,
      无变化: results.filter((r) => r.verdict === '无变化').length,
      点不到: results.filter((r) => r.verdict === '点不到').length,
    },
    consoleErrors,
    明细: results,
  };

  fs.writeFileSync(path.join(OUT, '02c_视图切换.json'), JSON.stringify(summary, null, 2), 'utf8');
  console.log('\n=== 汇总 ===');
  console.log(JSON.stringify(summary.计数, null, 2));
  console.log('consoleErrors =', consoleErrors.length, consoleErrors.slice(0, 5));

  await browser.close();
})();
