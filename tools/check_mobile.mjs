#!/usr/bin/env node
/* check_mobile.mjs — 実ブラウザでの表示検査(DESIGN.md ADR-8 ゲートB)。
   各ページを幅 360px と 1024px で開き、全タブを順に表示して次を検査する。
     (1) 表示中の文字の実寸が 12px 以上か(SVG 内の文字は縮小後の大きさで測る)
     (2) ページ全体が横にはみ出していないか(表の横スクロールは対象外)
     (3) チャートの中身がカードの幅からはみ出していないか
     (4) チャート内の同種ラベル(棒の名前・値、折れ線の目盛り)が重なっていないか
   使い方: node tools/check_mobile.mjs docs/plans/<id>.html [...]
   Playwright が必要(npm i -D playwright && npx playwright install chromium)。
   見つからない場合は終了コード 2 で止まる(黙って合格にしない)。 */
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const MIN_PX = 12;
const WIDTHS = [360, 1024];

let chromium;
try {
  ({ chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright"));
} catch (e) {
  console.error("Playwright が見つかりません。npm i -D playwright && npx playwright install chromium を実行するか、");
  console.error("PLAYWRIGHT_MODULE に playwright の index.mjs のパスを指定してください。");
  process.exit(2);
}

const launchOpts = process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {};
const browser = await chromium.launch(launchOpts);
let bad = 0;

for (const file of process.argv.slice(2)) {
  const url = pathToFileURL(resolve(file)).href;
  const errors = [];
  let minSeen = Infinity;
  for (const width of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height: 800 } });
    await page.goto(url);
    const tabs = await page.$$(".tabs .tab");
    const n = Math.max(tabs.length, 1);
    for (let i = 0; i < n; i++) {
      if (tabs.length) {
        await tabs[i].click();
        await page.waitForTimeout(250);
      }
      const r = await page.evaluate(({ MIN_PX }) => {
        const out = { min: Infinity, small: [], overflow: null, chartOverflow: [], overlaps: [] };
        const visible = (el) => {
          const s = getComputedStyle(el);
          if (s.display === "none" || s.visibility === "hidden") return false;
          const b = el.getBoundingClientRect();
          return b.width > 0 && b.height > 0;
        };
        // (1) 文字の実寸
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const seen = new Set();
        while (walker.nextNode()) {
          const t = walker.currentNode;
          if (!t.nodeValue.trim()) continue;
          const el = t.parentElement;
          if (!el || seen.has(el) || el.closest("script,style,title")) continue;
          seen.add(el);
          if (!visible(el)) continue;
          let px = parseFloat(getComputedStyle(el).fontSize);
          if (el instanceof SVGElement && el.ownerSVGElement) {
            const m = el.getScreenCTM();
            if (m) px = px * Math.hypot(m.a, m.b);
          }
          if (px < out.min) out.min = px;
          if (px < MIN_PX - 0.05) out.small.push(`${px.toFixed(1)}px「${t.nodeValue.trim().slice(0, 16)}」`);
        }
        // (2) ページの横はみ出し
        const sw = document.documentElement.scrollWidth;
        if (sw > window.innerWidth + 1) out.overflow = `${sw}px > ${window.innerWidth}px`;
        // (3)(4) チャート
        const charts = [...document.querySelectorAll(".panel.active .b-chart")];
        charts.forEach((c, ci) => {
          if (c.scrollWidth > c.clientWidth + 1) out.chartOverflow.push(`chart#${ci} ${c.scrollWidth}>${c.clientWidth}`);
          for (const sel of [".vb-l", ".vb-v", ".ln-x>span", ".ln-y>span", ".s100-s"]) {
            const els = [...c.querySelectorAll(sel)].filter(visible);
            const boxes = els.map((e) => {
              // 文字の実際の幅(要素の幅ではなく中身の幅)で判定する
              const range = document.createRange();
              range.selectNodeContents(e);
              return range.getBoundingClientRect();
            });
            for (let a = 0; a < boxes.length; a++) {
              for (let b = a + 1; b < boxes.length; b++) {
                const A = boxes[a], B = boxes[b];
                const ix = Math.min(A.right, B.right) - Math.max(A.left, B.left);
                const iy = Math.min(A.bottom, B.bottom) - Math.max(A.top, B.top);
                if (ix > 0.5 && iy > 0.5) {
                  out.overlaps.push(`chart#${ci} ${sel}「${els[a].textContent.trim().slice(0, 8)}」と「${els[b].textContent.trim().slice(0, 8)}」`);
                }
              }
            }
          }
        });
        return out;
      }, { MIN_PX });
      const where = `${width}px tab${i + 1}`;
      if (r.min < minSeen) minSeen = r.min;
      if (r.small.length) errors.push(`${where}: ${MIN_PX}px 未満の文字 ${r.small.length}件(例 ${r.small.slice(0, 3).join("、")})`);
      if (r.overflow) errors.push(`${where}: ページが横にはみ出し ${r.overflow}`);
      r.chartOverflow.forEach((m) => errors.push(`${where}: チャートがカードからはみ出し ${m}`));
      r.overlaps.slice(0, 5).forEach((m) => errors.push(`${where}: ラベルの重なり ${m}`));
      if (r.overlaps.length > 5) errors.push(`${where}: ラベルの重なり ほか${r.overlaps.length - 5}件`);
    }
    await page.close();
  }
  const status = errors.length ? "NG" : "OK";
  console.log(`${status}  ${file}  (最小の文字 ${minSeen.toFixed(1)}px, errors=${errors.length})`);
  errors.forEach((e) => console.log(`  [ERROR] ${e}`));
  if (errors.length) bad++;
}

await browser.close();
process.exit(bad ? 1 : 0);
