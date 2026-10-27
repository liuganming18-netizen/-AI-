// 探测：标的选择器 DOM 结构 + 各周期点击后的标签（识别「1h 源却标 15m」这类假图）
const { createRequire } = require('module');
const req = createRequire('/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/package.json');
const { chromium } = req('playwright');
const fs = require('fs');
const RID = process.argv[2] || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const OUTDIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/20_前端端到端';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1680, height: 1050 } })).newPage();
  await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`,
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(14000);

  const dom = await page.evaluate(() => {
    const el = document.querySelector('[data-lab-section="lab-chart"]');
    if (!el) return null;
    // 找包含「选择标的」文本的最近容器
    const all = Array.from(el.querySelectorAll('*'));
    const label = all.find((n) => n.children.length === 0 && /选择标的/.test(n.textContent || ''));
    const box = label ? label.parentElement : null;
    const btns = Array.from(el.querySelectorAll('button')).map((b) => ({
      text: (b.textContent || '').trim().slice(0, 20),
      cls: b.className.slice(0, 80),
      disabled: b.disabled,
      title: b.getAttribute('title') || '',
    }));
    return {
      labelHtml: label ? label.outerHTML.slice(0, 200) : null,
      boxHtml: box ? box.outerHTML.slice(0, 1200) : null,
      buttons: btns,
    };
  });

  const readHead = () => page.evaluate(() => {
    const el = document.querySelector('[data-lab-section="lab-chart"]');
    if (!el) return null;
    const lines = el.innerText.split('\n').map((s) => s.trim()).filter(Boolean);
    return lines.slice(0, 3).join(' | ');
  });

  const seq = [];
  for (const tf of ['15m', '4h', '1D', '1h']) {
    const b = page.getByRole('button', { name: tf, exact: true });
    const n = await b.count();
    if (n) { await b.first().click({ timeout: 8000 }).catch(() => {}); await sleep(4000); }
    seq.push({ tf, found: n, head: await readHead() });
  }

  // 点标的 chip（若按钮找不到，用文本定位）
  let symRes = null;
  const chip = page.locator('[data-lab-section="lab-chart"]').getByText('BCH', { exact: true }).first();
  if (await chip.count()) {
    await chip.click({ timeout: 8000 }).catch(() => {});
    await sleep(9000);
    symRes = { clicked: 'BCH', head: await readHead() };
  }

  const out = { run_id: RID, dom, tf_sequence: seq, symbol_click: symRes };
  fs.writeFileSync(`${OUTDIR}/probe_dom.json`, JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2).slice(0, 6000));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
