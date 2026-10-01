// Turns one answer into a shareable long image or an A4 PDF, drawn on canvas in
// lizheng.ai's look: the question first, then the answer, its limits and the
// sources, and a footer with the 立正 seal, the address and a QR code back to
// ask.lizheng.ai. Loaded only when someone saves an answer.
import qrcode from 'qrcode-generator';
import {MARK_PATHS, MARK_TRANSFORM, MARK_VIEWBOX} from './mark.js';

const SITE = 'https://ask.lizheng.ai/';
const W = 1080;                                   // layout width; drawn at up to 2x
const PAD = 84;
const INNER = W - PAD * 2;
const PAGE_H = Math.round(W * 841.89 / 595.28);   // A4 at this width
const PAGE_TOP = 92, PAGE_BOTTOM = PAGE_H - 96;
const C = {
  ink: '#121613', ink2: '#343a35', muted: '#6c7069', faint: '#8d9088', line: '#e2ddd1',
  ivory: '#f8f5ee', paper: '#fcfbf7', sand: '#efe9dc', green: '#238343', greenText: '#1c6b37',
  greenTint: '#e4efe3', forest: '#0f3d23', onForest: '#f8f1e4', amber: '#7a5512', amberTint: '#f5ead3',
};
const SERIF = '"Noto Serif SC", "Songti SC", "STSong", serif';
const SANS = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, -apple-system, "Segoe UI", sans-serif';
const KIND = {
  application: ['结合你的处境', C.amberTint, C.amber],
  source: ['材料里的观点', C.greenTint, C.greenText],
  synthesis: ['AI综合', C.sand, C.muted],
};
const font = (weight, size, family = SANS) => `${weight} ${size}px ${family}`;

// ---------- Text: inline runs, breakable units, lines ----------

const CJK = '⺀-鿿豈-﫿＀-￯　-〿';
const UNIT = new RegExp(`([${CJK}])|(\\s+)|([^\\s${CJK}]+)`, 'g');
const NO_START = new Set('，。、；：？！）」』”’》〉】…—·%,.;:?!)]}'.split(''));
const NO_END = new Set('（「『“‘《〈【([{'.split(''));

// Bold, citations [S3], links and code; other Markdown marks are dropped.
function inlineRuns(text, cites) {
  const out = [];
  const plain = value => value.replace(/\*\*|__|`/g, '').replace(/(^|[^*])\*(?!\*)/g, '$1');
  const pattern = /\*\*(.+?)\*\*|\[(S\d+)\](?!\()|\[([^\]]+)\]\([^)]*\)|`([^`]+)`/g;
  let last = 0, match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) out.push({text: plain(text.slice(last, match.index))});
    if (match[1]) out.push({text: plain(match[1]), bold: true});
    else if (match[2]) { if (cites.has(match[2])) out.push({cite: cites.get(match[2])}); }
    else out.push({text: match[3] || match[4]});
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push({text: plain(text.slice(last))});
  return out;
}

// The Markdown blocks answers use: paragraphs, lists and the odd heading.
function markdownBlocks(md) {
  const blocks = [];
  let para = [];
  const flush = () => { if (para.length) { blocks.push({text: para.join('')}); para = []; } };
  for (const raw of String(md || '').split('\n')) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    const item = line.match(/^(\d+[.、)]|[-*+•])\s+(.*)$/);
    if (item) {
      flush();
      blocks.push({text: item[2], marker: /^\d/.test(item[1]) ? item[1].replace(/[、)]$/, '.') : '•'});
      continue;
    }
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) { flush(); blocks.push({text: `**${heading[1]}**`}); continue; }
    para.push(line);
  }
  flush();
  return blocks;
}

function units(runs, style) {
  const out = [];
  for (const run of runs) {
    if (run.cite) { out.push({text: run.cite, cite: true, font: font(700, Math.round(style.size * 0.6))}); continue; }
    const f = font(run.bold ? 700 : style.weight, style.size, style.family);
    const color = run.bold ? style.strong || style.color : style.color;
    for (const m of run.text.matchAll(UNIT)) out.push({text: m[0], space: !!m[2], font: f, color});
  }
  return out;
}

function measure(ctx, unit) {
  ctx.font = unit.font;
  return ctx.measureText(unit.text).width + (unit.cite ? 12 : 0);
}

// Greedy lines with Chinese line-break rules: closing punctuation never starts
// a line and opening punctuation never ends one. A URL or word wider than the
// line breaks by character.
function wrap(ctx, items, width) {
  const lines = [];
  let line = [], x = 0;
  const push = () => { while (line.length && line.at(-1).space) line.pop(); lines.push(line); line = []; x = 0; };
  const place = unit => {
    const w = measure(ctx, unit);
    if (unit.space && !line.length) return;
    if (x + w > width && line.length) {
      if (NO_START.has(unit.text)) { line.push({...unit, w}); x += w; return; }
      const carry = line.length > 1 && NO_END.has(line.at(-1).text) ? line.pop() : null;
      push();
      if (unit.space) return;
      if (carry) { line.push(carry); x += carry.w; }
    }
    line.push({...unit, w}); x += w;
  };
  for (const unit of items) {
    if (!unit.cite && !unit.space && unit.text.length > 1 && measure(ctx, unit) > width) {
      for (const ch of unit.text) place({...unit, text: ch});
    } else place(unit);
  }
  if (line.length) push();
  return lines;
}

const lineWidth = line => line.reduce((sum, u) => sum + u.w, 0);

function roundRect(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r);
  c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);
  c.arcTo(x, y, x + w, y, r);
  c.closePath();
}

function drawSeal(c, x, y, width, color) {
  const s = width / MARK_VIEWBOX[0];
  c.save();
  c.translate(x, y);
  c.scale(s, s);
  c.translate(...MARK_TRANSFORM.translate);
  c.scale(...MARK_TRANSFORM.scale);
  c.fillStyle = color;
  for (const d of MARK_PATHS) c.fill(new Path2D(d));
  c.restore();
}

function drawLine(c, line, x, mid, size) {
  c.textBaseline = 'middle';
  for (const u of line) {
    if (u.cite) {
      const h = Math.round(size * 0.72), w = u.w - 4, top = mid - size * 0.62;
      c.fillStyle = C.greenTint; roundRect(c, x + 2, top, w, h, 5); c.fill();
      c.fillStyle = C.greenText; c.font = u.font; c.textAlign = 'center';
      c.fillText(u.text, x + 2 + w / 2, top + h / 2 + 1);
      c.textAlign = 'left';
    } else {
      c.font = u.font; c.fillStyle = u.color; c.fillText(u.text, x, mid);
    }
    x += u.w;
  }
}

function fitEllipsis(c, text, width, force = false) {
  if (!force && c.measureText(text).width <= width) return text;
  let out = text;
  while (out.length > 1 && c.measureText(`${out}…`).width > width) out = out.slice(0, -1);
  return `${out}…`;
}

const plainUrl = url => String(url || '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');

// ---------- Rows: the unit of layout and of page breaks ----------

const gap = h => ({h, gap: true, draw() {}});

function textRows(ctx, runs, style, {indent = 0, marker = '', width = INNER} = {}) {
  const lines = wrap(ctx, units(runs, style), width - indent);
  const lh = Math.round(style.size * style.lh);
  return lines.map((line, i) => ({
    h: lh,
    keep: style.keep,
    draw(c, y, dx = 0) {
      if (marker && i === 0) {
        c.textBaseline = 'middle'; c.font = font(700, style.size, style.family); c.fillStyle = C.green;
        c.fillText(marker, PAD + dx + 4, y + lh / 2);
      }
      drawLine(c, line, PAD + dx + indent, y + lh / 2, style.size);
    },
  }));
}

function header() {
  return {h: 150, draw(c, y) {
    c.fillStyle = C.forest; c.fillRect(0, y, W, 150);
    drawSeal(c, PAD, y + 50, 96, C.onForest);
    c.textBaseline = 'middle'; c.fillStyle = C.onForest; c.font = font(900, 40, SERIF);
    c.fillText('问问立正', PAD + 96 + 22, y + 76);
    c.font = font(400, 24); c.fillStyle = 'rgba(248,241,228,0.72)'; c.textAlign = 'right';
    c.fillText('ask.lizheng.ai', W - PAD, y + 78);
    c.textAlign = 'left';
  }};
}

// A section heading with its kind badge after the last line, or under it when
// the line is full.
function headingRows(ctx, heading, kind) {
  const [label, bg, fg] = KIND[kind] || KIND.synthesis;
  ctx.font = font(500, 20);
  const bw = ctx.measureText(label).width + 28;
  const style = {size: 34, lh: 1.5, weight: 700, family: SERIF, color: C.ink, keep: true};
  const lines = wrap(ctx, units([{text: heading}], style), INNER);
  const lh = Math.round(style.size * style.lh);
  const badge = (c, x, mid) => {
    c.fillStyle = bg; roundRect(c, x, mid - 17, bw, 34, 17); c.fill();
    c.fillStyle = fg; c.font = font(500, 20); c.textBaseline = 'middle'; c.fillText(label, x + 14, mid + 1);
  };
  const rows = lines.map((line, i) => ({h: lh, keep: true, draw(c, y) {
    drawLine(c, line, PAD, y + lh / 2, style.size);
    if (i === lines.length - 1 && lineWidth(line) + 18 + bw <= INNER) badge(c, PAD + lineWidth(line) + 18, y + lh / 2);
  }}));
  if (!lines.length || lineWidth(lines.at(-1)) + 18 + bw > INNER) rows.push({h: 44, keep: true, draw(c, y) { badge(c, PAD, y + 22); }});
  return rows;
}

function limitsRow(ctx, text) {
  const lines = textRows(ctx, [{text}], {size: 23, lh: 1.75, weight: 400, color: C.muted}, {width: INNER - 56});
  const h = 28 + 36 + lines.reduce((sum, r) => sum + r.h, 0) + 24;
  return {h, draw(c, y) {
    c.fillStyle = C.sand; roundRect(c, PAD, y, INNER, h, 20); c.fill();
    c.fillStyle = C.ink2; c.font = font(700, 23); c.textBaseline = 'middle';
    c.fillText('这个回答的边界', PAD + 28, y + 28 + 14);
    let cy = y + 28 + 36;
    for (const row of lines) { row.draw(c, cy, 28); cy += row.h; }
  }};
}

function sourceRow(ctx, source, number) {
  const style = {size: 27, weight: 600, color: C.ink};
  const all = wrap(ctx, units([{text: source.title || ''}], style), INNER - 70);
  const titleLines = all.slice(0, 2);
  const h = 24 + titleLines.length * 40 + 34 + 24;
  const type = source.source_type?.includes('video') ? '视频' : source.source_type === 'context' ? 'AI整理' : '文章';
  const meta = [type, source.date?.slice(0, 10) || '日期未标明', source.timecode ? `从 ${source.timecode} 开始` : ''].filter(Boolean).join(' · ');
  return {h, link: source.url, draw(c, y) {
    c.fillStyle = C.line; c.fillRect(PAD, y, INNER, 2);
    c.fillStyle = C.greenTint; roundRect(c, PAD, y + 26, 44, 44, 10); c.fill();
    c.fillStyle = C.greenText; c.font = font(700, 22); c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText(number, PAD + 22, y + 49);
    c.textAlign = 'left';
    let ty = y + 24;
    titleLines.forEach((line, i) => {
      c.font = font(600, 27); c.fillStyle = C.ink;
      const text = line.map(u => u.text).join('');
      c.fillText(i === 1 && all.length > 2 ? fitEllipsis(c, text, INNER - 70, true) : text, PAD + 70, ty + 20);
      ty += 40;
    });
    c.font = font(400, 21); c.fillStyle = C.muted;
    const metaText = `${meta} · `;
    c.fillText(metaText, PAD + 70, ty + 17);
    const mw = c.measureText(metaText).width;
    c.fillStyle = C.greenText;
    c.fillText(fitEllipsis(c, plainUrl(source.url), INNER - 70 - mw), PAD + 70 + mw, ty + 17);
  }};
}

function footerRow() {
  const h = 260;
  const qr = qrcode(0, 'M');
  qr.addData(SITE);
  qr.make();
  return {h, link: SITE, footer: true, draw(c, y) {
    c.fillStyle = C.paper; c.fillRect(0, y, W, h);
    c.fillStyle = C.line; c.fillRect(0, y, W, 2);
    drawSeal(c, PAD, y + 56, 78, C.green);
    c.textBaseline = 'middle';
    c.fillStyle = C.ink; c.font = font(900, 32, SERIF); c.fillText('问问立正', PAD + 78 + 18, y + 76);
    c.fillStyle = C.ink2; c.font = font(400, 25); c.fillText('在 ask.lizheng.ai 问你自己的问题', PAD, y + 138);
    c.fillStyle = C.muted; c.font = font(400, 19);
    c.fillText('AI根据立正公开的文章和视频整理，不是本人实时回复；', PAD, y + 182);
    c.fillText('重要的判断，请回到原文核对。', PAD, y + 210);
    const size = 168, n = qr.getModuleCount(), cell = size / (n + 4);
    const qx = W - PAD - size, qy = y + 34;
    c.fillStyle = '#ffffff'; roundRect(c, qx - 6, qy - 6, size + 12, size + 12, 14); c.fill();
    c.fillStyle = C.ink;
    for (let r = 0; r < n; r++) {
      for (let col = 0; col < n; col++) {
        if (qr.isDark(r, col)) c.fillRect(qx + (col + 2) * cell, qy + (r + 2) * cell, Math.ceil(cell), Math.ceil(cell));
      }
    }
    c.fillStyle = C.muted; c.font = font(500, 18); c.textAlign = 'center';
    c.fillText('扫码提问', qx + size / 2, qy + size + 28);
    c.textAlign = 'left';
  }};
}

function layout(ctx, {question, result, date}) {
  // Number sources 1..n in the shared file; the answer's ids skip sources it did not use.
  const cites = new Map((result.sources || []).map((s, i) => [s.id, String(i + 1)]));
  const rows = [header(), gap(62)];
  rows.push(...textRows(ctx, [{text: '你的问题'}], {size: 23, lh: 1.6, weight: 600, color: C.greenText, keep: true}), gap(6));
  rows.push(...textRows(ctx, [{text: question}], {size: 44, lh: 1.5, weight: 700, family: SERIF, color: C.ink}));
  const day = `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  const meta = result.status === 'sources-only' ? `找到的公开材料 · ${day}` : `AI根据立正公开的文章和视频整理 · ${day}`;
  rows.push(gap(14), ...textRows(ctx, [{text: meta}], {size: 21, lh: 1.6, weight: 400, color: C.faint}));
  rows.push(gap(30), {h: 2, draw(c, y) { c.fillStyle = C.line; c.fillRect(PAD, y, INNER, 2); }}, gap(34));
  for (const block of markdownBlocks(result.summary)) {
    rows.push(...textRows(ctx, inlineRuns(block.text, cites), {size: 32, lh: 1.75, weight: 600, color: C.ink, strong: C.ink}));
  }
  for (const section of result.sections || []) {
    rows.push(gap(46), ...headingRows(ctx, section.heading, section.kind), gap(8));
    markdownBlocks(section.body).forEach((block, i) => {
      if (i) rows.push(gap(16));
      rows.push(...textRows(ctx, inlineRuns(block.text, cites),
        {size: 29, lh: 1.85, weight: 400, color: C.ink2, strong: C.ink},
        block.marker ? {indent: 40, marker: block.marker} : {}));
    });
  }
  if (result.limitations) rows.push(gap(44), limitsRow(ctx, result.limitations));
  if (result.sources?.length) {
    rows.push(gap(54), {h: 52, keep: true, draw(c, y) {
      c.textBaseline = 'middle';
      c.fillStyle = C.ink; c.font = font(700, 34, SERIF); c.fillText('出处', PAD, y + 26);
      c.fillStyle = C.faint; c.font = font(400, 21);
      c.fillText(`回答依据的${result.sources.length}份公开原文`, PAD + 88, y + 28);
    }}, gap(10));
    result.sources.forEach((source, i) => rows.push(sourceRow(ctx, source, String(i + 1))));
  }
  rows.push(gap(56), footerRow());
  return rows;
}

// ---------- Rendering ----------

function canvasFor(height, scale) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(W * scale);
  canvas.height = Math.round(height * scale);
  const c = canvas.getContext('2d');
  c.scale(scale, scale);
  c.fillStyle = C.ivory;
  c.fillRect(0, 0, W, height);
  return {canvas, c};
}

function renderLong(rows) {
  const height = rows.reduce((sum, row) => sum + row.h, 0);
  // iOS caps canvas area near 16.7M pixels; stay under it at any length.
  const scale = Math.min(2, Math.sqrt(15e6 / (W * height)));
  const {canvas, c} = canvasFor(height, scale);
  let y = 0;
  for (const row of rows) { row.draw(c, y); y += row.h; }
  return canvas;
}

// Pages break between rows. A kept row (a heading) moves with the next real
// row, and gaps are never carried to the top of a page.
function paginate(rows) {
  const pages = [];
  let page = [], y = 0;
  const following = i => {
    let h = 0;
    for (let j = i + 1; j < rows.length; j++) { h += rows[j].h; if (!rows[j].gap && !rows[j].keep) break; }
    return h;
  };
  rows.forEach((row, i) => {
    if (row.gap && !page.length && pages.length) return;
    const need = row.h + (row.keep ? following(i) : 0);
    if (y + need > PAGE_BOTTOM && page.length) {
      pages.push(page); page = []; y = PAGE_TOP;
      if (row.gap) return;
    }
    page.push({row, y});
    y += row.h;
  });
  if (page.length) pages.push(page);
  return pages;
}

function renderPages(rows, question) {
  const pages = paginate(rows);
  return pages.map((items, index) => {
    const {canvas, c} = canvasFor(PAGE_H, 2);
    const links = [];
    for (const {row, y} of items) {
      row.draw(c, y);
      if (row.link) links.push(row.footer ? {x: 0, y, w: W, h: row.h, url: row.link} : {x: PAD, y, w: INNER, h: row.h, url: row.link});
    }
    c.textBaseline = 'middle'; c.font = font(400, 18); c.fillStyle = C.faint;
    if (index) c.fillText(fitEllipsis(c, `问问立正 · ${question}`, INNER - 160), PAD, 46);
    c.textAlign = 'right';
    c.fillText(`ask.lizheng.ai · ${index + 1} / ${pages.length}`, W - PAD, PAGE_H - 44);
    c.textAlign = 'left';
    return {canvas, links};
  });
}

const toBlob = (canvas, type, quality) => new Promise((resolve, reject) => {
  canvas.toBlob(blob => (blob ? resolve(blob) : reject(new Error('canvas'))), type, quality);
});

// ---------- A minimal PDF: one JPEG per A4 page, plus link annotations ----------

function latin1(text) {
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i) & 0xff;
  return bytes;
}

function utf16Hex(text) {
  let hex = 'FEFF';
  for (let i = 0; i < text.length; i++) hex += text.charCodeAt(i).toString(16).padStart(4, '0');
  return hex.toUpperCase();
}

const pdfString = url => String(url).replace(/[^\x20-\x7E]/g, ch => encodeURIComponent(ch)).replace(/([\\()])/g, '\\$1');
const num = value => String(Math.round(value * 100) / 100);

async function makePdf(pages, title) {
  const PW = 595.28, PH = 841.89, sx = PW / W, sy = PH / PAGE_H;
  const chunks = [], offsets = [];
  let offset = 0;
  const push = data => {
    const bytes = typeof data === 'string' ? latin1(data) : data;
    chunks.push(bytes);
    offset += bytes.length;
  };
  const object = (id, write) => { offsets[id] = offset; push(`${id} 0 obj\n`); write(); push('\nendobj\n'); };
  const pageId = i => 4 + i * 3, contentId = i => 5 + i * 3, imageId = i => 6 + i * 3;
  const total = 3 + pages.length * 3;
  const images = await Promise.all(pages.map(async p => new Uint8Array(await (await toBlob(p.canvas, 'image/jpeg', 0.9)).arrayBuffer())));
  push('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');
  object(1, () => push('<< /Type /Catalog /Pages 2 0 R >>'));
  object(2, () => push(`<< /Type /Pages /Kids [${pages.map((_, i) => `${pageId(i)} 0 R`).join(' ')}] /Count ${pages.length} >>`));
  object(3, () => push(`<< /Title <${utf16Hex(title)}> /Creator (ask.lizheng.ai) /Producer (ask.lizheng.ai) >>`));
  pages.forEach((page, i) => {
    const annots = page.links.map(l => `<< /Type /Annot /Subtype /Link /Rect [${num(l.x * sx)} ${num(PH - (l.y + l.h) * sy)} ${num((l.x + l.w) * sx)} ${num(PH - l.y * sy)}] /Border [0 0 0] /A << /S /URI /URI (${pdfString(l.url)}) >> >>`).join(' ');
    object(pageId(i), () => push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PW} ${PH}] /Resources << /XObject << /Im${i} ${imageId(i)} 0 R >> /ProcSet [/PDF /ImageC] >> /Contents ${contentId(i)} 0 R${annots ? ` /Annots [${annots}]` : ''} >>`));
    const content = `q ${PW} 0 0 ${PH} 0 0 cm /Im${i} Do Q`;
    object(contentId(i), () => push(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`));
    object(imageId(i), () => {
      push(`<< /Type /XObject /Subtype /Image /Width ${page.canvas.width} /Height ${page.canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${images[i].length} >>\nstream\n`);
      push(images[i]);
      push('\nendstream');
    });
  });
  const xref = offset;
  push(`xref\n0 ${total + 1}\n0000000000 65535 f \n`);
  for (let id = 1; id <= total; id++) push(`${String(offsets[id]).padStart(10, '0')} 00000 n \n`);
  push(`trailer\n<< /Size ${total + 1} /Root 1 0 R /Info 3 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(chunks, {type: 'application/pdf'});
}

// ---------- Entry ----------

async function ensureFonts({question, result}) {
  if (!document.fonts?.load) return;
  const headings = [question, '问问立正出处', ...(result.sections || []).map(s => s.heading)].join('');
  await Promise.all([
    document.fonts.load(font(700, 34, SERIF), headings),
    document.fonts.load(font(900, 40, SERIF), '问问立正'),
  ]).catch(() => {});
}

export async function exportAnswer(kind, data) {
  await ensureFonts(data);
  const ctx = document.createElement('canvas').getContext('2d');
  const rows = layout(ctx, data);
  const base = `问问立正-${data.question.replace(/[\\/:*?"<>|\s]+/g, '').slice(0, 18) || '回答'}`;
  if (kind === 'png') return {blob: await toBlob(renderLong(rows), 'image/png'), name: `${base}.png`, type: 'image/png'};
  const pages = renderPages(rows, data.question);
  return {blob: await makePdf(pages, `问问立正：${data.question}`), name: `${base}.pdf`, type: 'application/pdf'};
}
