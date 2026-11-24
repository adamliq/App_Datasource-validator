// Renders tools/icons/*.svg to the PNG sizes Splunk expects in static/.
// Needs Playwright (npm i playwright); run from the repo root: node tools/render_icons.js
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ICONS = { appIcon: 'appIcon.svg', appIconAlt: 'appIconAlt.svg' };
const SIZES = { '': 36, '_2x': 72 };

(async () => {
  const browser = await chromium.launch();
  for (const [name, file] of Object.entries(ICONS)) {
    const svg = fs.readFileSync(path.join(__dirname, 'icons', file), 'utf8');
    for (const [suffix, px] of Object.entries(SIZES)) {
      const page = await browser.newPage({ viewport: { width: px, height: px }, deviceScaleFactor: 1 });
      await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:${px}px;height:${px}px}</style>${svg}`);
      await page.screenshot({ path: path.join(__dirname, '..', 'static', `${name}${suffix}.png`), omitBackground: true, clip: { x: 0, y: 0, width: px, height: px } });
      await page.close();
    }
  }
  await browser.close();
})();
