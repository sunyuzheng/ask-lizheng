// Walks the page on the local rig (npm run dev:rig) and checks what a release must not break:
// which list opens first, asking, reading what others asked while the answer is written (the place
// held as the answer grows above, with and without the browser's own scroll anchoring, which Safari
// lacks), the word that the answer is there or failed, the answer's summary shown as soon as it is
// written and withdrawn when the answer is written again, 问个类似的 under a conversation, and
// someone else's shared page. Exits 1 with the failed checks.
//
//   npm run dev:rig                                   (in another terminal; note its port)
//   ASK_URL=http://127.0.0.1:5173/ node scripts/page_walkthrough.mjs [folder for screenshots]
//
// Playwright is not a dependency of this project: PLAYWRIGHT_MODULE is the path of an installed
// playwright's index.mjs when `playwright` cannot be imported, and PLAYWRIGHT_CHROMIUM the browser
// to launch when the installed one is not the version that playwright expects.
import fs from 'node:fs';
import path from 'node:path';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const URL_ = process.env.ASK_URL || 'http://127.0.0.1:5173/';
const SHOTS = process.argv[2];
if (SHOTS) fs.mkdirSync(SHOTS, {recursive: true});
const browser = await chromium.launch(process.env.PLAYWRIGHT_CHROMIUM ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM} : {});
const failed = [];
const check = (name, ok, seen) => { console.log(`${ok ? '✓' : '✗'} ${name}${ok ? '' : `: ${JSON.stringify(seen)}`}`); if (!ok) failed.push(name); };
const SIZES = {desktop: {width: 1440, height: 900}, phone: {width: 390, height: 844}};

async function open(size, url = URL_) {
  const phone = size.width < 500;
  const context = await browser.newContext({viewport: size, deviceScaleFactor: 2, isMobile: phone, hasTouch: phone});
  const page = await context.newPage();
  page.errors = [];
  page.on('pageerror', error => page.errors.push(String(error)));
  await page.goto(url);
  return page;
}
const shot = (page, name) => (SHOTS ? page.screenshot({path: path.join(SHOTS, `${name}.png`)}) : null);
const ask = async (page, question) => { await page.fill('#question', question); await page.click('button.send'); };

// Asking, then reading what others asked while the answer is written above.
for (const [device, size] of Object.entries(SIZES)) for (const anchoring of [true, false]) {
  const tag = `${device}, ${anchoring ? 'browser holds the place' : 'no scroll anchoring (Safari)'}`;
  const page = await open(size);
  await page.waitForSelector('.qcard');
  const views = await page.$$eval('.discovery-views button', buttons => buttons.map(b => `${b.textContent}:${b.getAttribute('aria-pressed')}`));
  check(`${tag}: the list opens on 最近问`, views[0] === '最近问:true', views);
  if (anchoring) await shot(page, `${device}-1-start`);
  if (!anchoring) await page.addStyleTag({content: 'html, body { overflow-anchor: none !important; }'});
  await ask(page, '用AI效率变高了，为什么我的工作价值没变？');
  await page.waitForSelector('.working-peek');
  await page.waitForTimeout(2500);
  if (anchoring) await shot(page, `${device}-2-waiting`);
  await page.click('.working-peek');
  await page.waitForSelector('.qcard.open .answer');
  await page.waitForTimeout(1500);
  // After each growth of the conversation above, where the opened question sits on screen.
  await page.evaluate(() => {
    window.__tops = [];
    const layout = document.querySelector('.layout');
    new ResizeObserver(() => window.__tops.push(Math.round(document.querySelector('.qcard.open').getBoundingClientRect().top))).observe(layout);
  });
  await page.waitForSelector('.arrived', {timeout: 60000});
  await page.waitForTimeout(400);
  const tops = await page.evaluate(() => window.__tops);
  check(`${tag}: the question being read stays put while the answer grows (${tops.length} times)`, tops.length >= 3 && Math.max(...tops) - Math.min(...tops) <= 2, tops);
  check(`${tag}: says the answer is there`, (await page.$eval('.arrived', node => node.textContent)).startsWith('你的回答写好了'));
  if (anchoring) await shot(page, `${device}-3-answer-arrived`);
  await page.click('.arrived');
  await page.waitForTimeout(1500);
  const back = await page.evaluate(() => ({top: Math.round(document.querySelector('.turn:last-of-type').getBoundingClientRect().top), gone: !document.querySelector('.arrived')}));
  check(`${tag}: one tap back to the answer`, back.gone && back.top > 0 && back.top < 140, back);
  check(`${tag}: no page errors`, page.errors.length === 0, page.errors);
  await page.context().close();
}

// The summary first: the answer's conclusion is there to read as soon as it is written, where the
// reader already is, with the sections arriving under it; the final answer then takes its place.
for (const [device, size] of Object.entries(SIZES)) {
  const page = await open(size);
  await page.waitForSelector('.qcard');
  await ask(page, '用AI效率变高了，为什么我的工作价值没变？');
  await page.waitForSelector('.partial .summary', {timeout: 60000});
  await page.waitForTimeout(300);
  const first = await page.evaluate(() => {
    const box = document.querySelector('.partial .summary').getBoundingClientRect();
    return {text: document.querySelector('.partial .summary').textContent.trim(), sections: document.querySelectorAll('.partial .answer-section').length,
      top: Math.round(box.top), bottom: Math.round(box.bottom), screen: innerHeight, scrolled: Math.round(scrollY)};
  });
  check(`${device}: the summary shows before any section is written`, first.text.length > 0 && first.sections === 0, first);
  check(`${device}: the summary is on screen without scrolling`, first.top >= 0 && first.bottom <= first.screen, first);
  await shot(page, `${device}-5-summary-first`);
  await page.waitForSelector('.partial .answer-section', {timeout: 60000});
  const order = await page.evaluate(() => {
    const summary = document.querySelector('.partial .summary'), section = document.querySelector('.partial .answer-section');
    return {summary: summary?.textContent.trim(), below: !!(summary && summary.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING)};
  });
  check(`${device}: the sections arrive under the summary, which stays`, order.below && order.summary === first.text, order);
  await shot(page, `${device}-6-summary-and-section`);
  await page.waitForSelector('.turn .answer', {timeout: 60000});
  const done = await page.evaluate(() => ({draft: !!document.querySelector('.partial'), summary: document.querySelector('.turn .answer .summary')?.textContent.trim()}));
  check(`${device}: the final answer takes the draft's place`, !done.draft && done.summary === first.text, done);
  check(`${device}, summary first: no page errors`, page.errors.length === 0, page.errors);
  await page.context().close();
}

// An answer found invalid and written again: what was shown of it goes, summary included, and the
// repaired one comes back the same way.
{
  const page = await open(SIZES.desktop);
  await page.waitForSelector('.qcard');
  await ask(page, '这一条需要修正吗？');
  await page.waitForSelector('.partial .answer-section', {timeout: 60000});
  await page.waitForFunction(() => /修正/.test(document.querySelector('.working-top [role=status]')?.textContent || ''), null, {timeout: 60000});
  const withdrawn = await page.evaluate(() => ({draft: !!document.querySelector('.partial'), summary: !!document.querySelector('.turn .summary'), materials: document.querySelectorAll('.candidates .candidate').length}));
  check('repairing withdraws the summary and sections; the materials stay', !withdrawn.draft && !withdrawn.summary && withdrawn.materials > 0, withdrawn);
  await shot(page, 'desktop-7-repairing');
  await page.waitForSelector('.partial .summary', {timeout: 60000});
  check('the repaired summary shows again', (await page.$$('.partial .answer-section')).length === 0);
  await page.waitForSelector('.turn .answer', {timeout: 60000});
  check('repaired answer: no page errors', page.errors.length === 0, page.errors);
  await page.context().close();
}

// A failed answer while reading below; then 问个类似的 from there.
{
  const page = await open(SIZES.desktop);
  await page.waitForSelector('.qcard');
  await ask(page, '这一条会失败吗？');
  await page.waitForSelector('.working-peek');
  await page.click('.working-peek');
  await page.waitForSelector('.qcard.open .answer');
  await page.waitForSelector('.arrived', {timeout: 60000});
  check('a failed answer is said too', (await page.$eval('.arrived', node => node.textContent)).startsWith('你的回答没有完成'));
  await shot(page, 'desktop-4-answer-failed');
  await page.click('.qcard.open .qcard-actions .pill-button:not(.qcard-like)');
  await page.waitForTimeout(1200);
  const box = await page.evaluate(() => ({filled: document.getElementById('question').value.length > 0, focused: document.activeElement?.id === 'question',
    seen: document.getElementById('question').getBoundingClientRect().top < innerHeight}));
  check('问个类似的 under a conversation fills the box and shows it', box.filled && box.focused && box.seen, box);
  check('failed answer: no page errors', page.errors.length === 0, page.errors);
  await page.context().close();
}

// Someone else's shared page: their conversation, then what others asked.
{
  const page = await open(SIZES.desktop, new URL('/s/2026-01-01/rig', URL_).href);
  await page.waitForSelector('.turn');
  await page.waitForSelector('.thread-others .qcard');
  const seen = await page.evaluate(() => ({scrollY, others: document.querySelectorAll('.thread-others .qcard').length}));
  check('a shared page opens at its top, with what others asked under it', seen.scrollY === 0 && seen.others > 0, seen);
  check('shared page: no page errors', page.errors.length === 0, page.errors);
  await page.context().close();
}

await browser.close();
if (failed.length) { console.error(`\n${failed.length} failed`); process.exit(1); }
console.log('\nall passed');
