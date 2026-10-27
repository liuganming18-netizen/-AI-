/**
 * 02b_probe_dom.js —— 只读 DOM 结构探针（不判定，只为定位"内容区容器"）
 *
 * 目的：02 轮取证用「整页 body.innerText 是否变化」判页签切换，结果被顶栏
 *       WebSocket 秒数干扰（恒 changed=true）。本探针找出一个**不含顶栏**的
 *       内容区容器，供 02c 用它做真正的切换判定。
 *
 * 用法：NODE_PATH=<web/node_modules> node 02b_probe_dom.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.UI_BASE || 'http://127.0.0.1:3000';
const OUT = path.join(__dirname, '..', 'out');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  const info = await page.evaluate(() => {
    const walk = (el, depth, maxDepth) => {
      const out = [];
      if (depth > maxDepth) return out;
      for (const c of Array.from(el.children)) {
        const cls = (c.className || '').toString().slice(0, 90);
        const txt = (c.innerText || '').replace(/\s+/g, ' ').trim();
        out.push({
          depth,
          tag: c.tagName.toLowerCase(),
          testid: c.getAttribute('data-testid'),
          cls,
          textLen: txt.length,
          head: txt.slice(0, 80),
          nChild: c.children.length,
        });
        out.push(...walk(c, depth + 1, maxDepth));
      }
      return out;
    };
    const body = document.body;
    const root = body.firstElementChild;
    return {
      bodyTextLen: (body.innerText || '').length,
      tree: walk(root, 1, 3),
    };
  });

  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, '02b_dom_tree.json'), JSON.stringify(info, null, 2), 'utf8');

  // 找出"顶栏"与"内容区"：顶栏含时间/连接状态，内容区应是它的兄弟
  const siblings = info.tree.filter((n) => n.depth === 3 || n.depth === 4);
  console.log('bodyTextLen =', info.bodyTextLen);
  for (const n of info.tree) {
    console.log(
      '  '.repeat(n.depth - 1) +
        `${n.tag}${n.testid ? '#' + n.testid : ''} len=${n.textLen} :: ${n.head}`,
    );
  }

  await browser.close();
})();
