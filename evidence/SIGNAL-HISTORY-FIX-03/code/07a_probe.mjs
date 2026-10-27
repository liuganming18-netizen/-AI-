// 探针：列出页面所有 data-testid、可见性、以及按钮文本（用于找到展开入口）
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(__dirname, '..', '..', '..', 'UI界面', 'web');
const req = createRequire(path.join(WEB, 'package.json'));
const { chromium } = req('playwright');

const URL = process.env.WX_UI_URL || 'http://127.0.0.1:3000';

const b = await chromium.launch({ headless: true });
const p = await b.newPage({ viewport: { width: 1600, height: 1400 } });
await p.goto(URL, { waitUntil: 'networkidle', timeout: 90000 }).catch(() => {});
await p.waitForTimeout(6000);
const ids = await p.$$eval('[data-testid]', (els) =>
  els.map((e) => ({ id: e.getAttribute('data-testid'), vis: !!(e.offsetWidth || e.offsetHeight) })));
console.log('testids:', JSON.stringify(ids));
const btns = await p.$$eval('button', (els) => els.map((e) => (e.innerText || '').trim()).filter(Boolean).slice(0, 60));
console.log('buttons:', JSON.stringify(btns));
const bodyLen = (await p.content()).length;
console.log('html_len', bodyLen);
const text = await p.evaluate(() => document.body.innerText.slice(0, 1500));
console.log('body_text_head:\n' + text);
await b.close();
