const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [svgPath, pngPath, widthArg, scaleArg] = process.argv.slice(2);
  const svg = fs.readFileSync(svgPath, 'utf8');
  const vb = svg.match(/viewBox="([^"]+)"/)[1].split(' ').map(Number);
  const width = parseInt(widthArg || '1600'); const height = Math.round(width * vb[3] / vb[2]);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: parseFloat(scaleArg || '1') });
  const sized = svg.replace(/width="[^"]+" height="[^"]+"/, `width="${width}" height="${height}"`);
  await page.setContent(`<html><body style="margin:0;background:#000">${sized}</body></html>`);
  await page.screenshot({ path: pngPath, clip: { x: 0, y: 0, width, height } });
  await browser.close();
})();
