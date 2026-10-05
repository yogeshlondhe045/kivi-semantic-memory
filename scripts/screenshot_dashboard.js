// Capture the web dashboard with headless Chromium (Playwright). Optional documentation tool.
//   NODE_PATH=$(npm root -g) node scripts/screenshot_dashboard.js [url] [out.png]
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[2] || 'http://localhost:8080/';
  const out = process.argv[3] || 'dashboard.png';
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1350 } });
  await page.goto(url);
  await page.waitForTimeout(4000);          // let a few SSE updates arrive
  await page.screenshot({ path: out, fullPage: true });
  await browser.close();
  console.log('wrote', out);
})().catch((e) => { console.error(e); process.exit(1); });
