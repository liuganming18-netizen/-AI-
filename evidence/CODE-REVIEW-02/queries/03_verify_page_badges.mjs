/* CODE-REVIEW-02 · 「按单 run」页签未登记徽标快查（只读，修订 testid） */
import { chromium } from 'playwright';
const browser = await chromium.launch({ args: ['--no-proxy-server'] });
const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
page.on('response', (r) => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url().slice(0, 140)}`); });

await page.goto('http://127.0.0.1:3000/', { waitUntil: 'networkidle' });
await page.getByRole('button', { name: '回测复盘' }).click();
await page.waitForTimeout(2000);
await page.locator('button', { hasText: '按单 run' }).first().click();
await page.waitForSelector('[data-run-id]', { timeout: 40000 });
await page.waitForTimeout(2500);

const items = await page.locator('[data-run-id]').count();
const labBadges = await page.locator('[data-testid="lab-run-unregistered"]').count();
const panelBadges = await page.locator('[data-testid="runitem-unregistered"]').count();
const firstText = (await page.locator('[data-testid="lab-run-unregistered"]').first().textContent().catch(() => null))
  ?? (await page.locator('[data-testid="runitem-unregistered"]').first().textContent().catch(() => null));
const unknownStatus = await page.locator('text=未登记').count();
await page.screenshot({ path: '/tmp/bt_verify_cr02/backtest_run_tab.png' });
console.log(JSON.stringify({ items, labBadges, panelBadges, unknownStatus, firstText, errors }, null, 2));
await browser.close();
