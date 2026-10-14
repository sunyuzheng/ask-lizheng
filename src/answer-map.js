// The answer map, a timeline (时间长卷): every public material is a thin stroke in the row of its
// topic, on the day it was published, 2020 at the left and today at the right, where the question
// is. It is drawn while an answer is found and kept beside it afterwards. The stream lights what it
// actually used: the candidates it found, the judgment cards it chose (cream diamonds above the
// question) and the sources an answer cites, numbered as in the text, each with a thread to the
// question. A number opens the original material, the video at the cited moment or the post, and
// marks its source beside the answer, as a citation does; members' content is gold (2026-10-13,
// the owner). The owner chose the timeline the same day over a tilted disc of lights, which looked
// too much like city.lizheng.ai; a timeline also says more: an answer today draws on years of
// work. The service serves the layout (/api/answer-map, rebuilt with each context update by
// scripts/build_answer_map.py).

// ask.lizheng.ai reaches the service at /api/answer-map; lizheng.ai's homepage, which copies this
// file (scripts/sync-ask-app.mjs there), at /api/ask-lizheng/answer-map.
let loading;
export function loadAnswerMap(url = '/api/answer-map') {
  loading ??= fetch(url, {credentials: 'omit'})
    .then(response => { if (!response.ok) throw new Error(`answer map: ${response.status}`); return response.json(); })
    .then(indexMap)
    .catch(error => { loading = null; throw error; });
  return loading;
}

const MINT = '143,212,164';
const GOLD = '226,194,124';
const CREAM = '248,241,228';
const TAU = Math.PI * 2;
const YEAR_MS = 365.25 * 864e5;
const YOUTUBE = /(?:youtube\.com\/(?:watch\?v=|live\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/;
// The words drawn on the map; the homepage's English page passes its own.
const WORDS = {question: '这个问题', card: '判断卡', member: '会员'};

/** A date ('2024-01-12…') as a year and its fraction, or NaN. */
function yearOf(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value || '');
  if (!match) return NaN;
  const [y, m, d] = match.slice(1).map(Number);
  return y + (Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 1)) / YEAR_MS;
}

function indexMap(data) {
  const byKey = new Map(data.keys.map((key, i) => [key, i]));
  const byVideo = new Map();
  data.keys.forEach((key, i) => {
    const id = key.startsWith('videos/') ? key.slice(16) : key.startsWith('dialogues/youtube-') ? key.slice(18) : '';
    if (id.length === 11 && !byVideo.has(id)) byVideo.set(id, i);
  });
  // When each material was published: the layout's own days (d), or, in a layout made before it
  // had them, the distance from the centre of the old disc, which was its time.
  const [first, last] = data.years, [inner, outer] = data.radius;
  const when = Float32Array.from(data.keys, (_, i) => (data.d
    ? (data.d[i] >= 0 ? first + data.d[i] / 365.25 : NaN)
    : first + Math.min(1, Math.max(0, (data.r[i] / 1000 - inner) / (outer - inner))) * (last - first)));
  let latest = first + 1;
  for (const value of when) if (value > latest) latest = value;
  return {...data, byKey, byVideo, cardIndex: new Map(data.cards.map((card, i) => [card.id, i])), when, latest};
}

/** The stroke a source belongs to: by its public copy's path, else by its video. */
export function sourceItem(map, source, key = '') {
  if (key && map.byKey.has(key)) return map.byKey.get(key);
  const copy = /\/blob\/[^/]+\/(corpus\/.+)$/.exec(source?.public_copy_url || '');
  if (copy) {
    const found = map.byKey.get(decodeURIComponent(copy[1]).slice(7).replace(/\.(md|json)$/, ''));
    if (found !== undefined) return found;
  }
  const video = YOUTUBE.exec(source?.url || '');
  return video && map.byVideo.has(video[1]) ? map.byVideo.get(video[1]) : -1;
}

const ease = x => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);

/** A point on a thread (a cubic curve, [x0, y0, x1, y1, x2, y2, x3, y3]). */
function along(path, u) {
  const v = 1 - u, a = v * v * v, b = 3 * v * v * u, c = 3 * v * u * u, d = u * u * u;
  return {x: a * path[0] + b * path[2] + c * path[4] + d * path[6], y: a * path[1] + b * path[3] + c * path[5] + d * path[7]};
}

/** Only members open its original (a member video, course, post or reply): its number is gold. */
export const membersOnly = source => source?.source_visibility === 'members-only';

/** The address a number opens: the source's own, https only. */
export function openableUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.href : '';
  } catch {
    return '';
  }
}

/**
 * The map on a canvas. `ambient` is the start screen's: nothing on it is pointed at, and the
 * strokes it is given (another reader's question) change with a crossfade. `words` names the
 * question at the right ('你的问题' under one's own answer) and the tooltips' labels.
 */
export class AnswerMap {
  constructor(canvas, map, {onSelect, onOpen, reduce = false, ambient = false, words = {}} = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.map = map;
    this.onSelect = onSelect;
    this.onOpen = onOpen;
    this.reduce = reduce;
    this.ambient = ambient;
    this.words = {...WORDS, ...words};
    this.state = {live: false, stage: '', candidates: [], cards: [], cited: [], sources: new Map(), selected: null};
    this.seen = new Map();          // when each thing first appeared: 'c:<item>', 'k:card', 'n:<item>'
    this.fading = new Map();        // strokes no longer lit, by when they went out (ambient)
    this.lastDraw = 0;
    this.hits = [];
    this.hover = null;
    this.visible = true;
    this.frame = 0;
    this.lastActive = 0;
    this.font = getComputedStyle(canvas).fontFamily || 'system-ui, sans-serif';
    if (!ambient) this.bindPointer();
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(canvas);
    this.seenOnScreen = new IntersectionObserver(([entry]) => { this.visible = entry.isIntersecting; this.wake(); });
    this.seenOnScreen.observe(canvas);
    this.resize();
  }

  destroy() {
    cancelAnimationFrame(this.frame);
    this.observer.disconnect();
    this.seenOnScreen.disconnect();
    this.unbind?.();
  }

  /** What the stream has shown so far; see answerMapState in answer-map-panel.jsx. */
  update(next) {
    const now = performance.now();
    const mark = (key, delay = 0) => { if (!this.seen.has(key)) this.seen.set(key, this.reduce ? now - 5000 : now + delay); };
    if (this.ambient) {
      // Another question: its strokes light one by one, the last one's go out together.
      const lit = new Set(next.candidates.map(row => row.item).filter(item => item >= 0));
      for (const row of this.state.candidates) {
        if (row.item < 0 || lit.has(row.item)) continue;
        if (!this.reduce) this.fading.set(row.item, now);
        this.seen.delete(`c:${row.item}`);
      }
      lit.forEach(item => this.fading.delete(item));
    }
    // Candidates light one after another. Numbers change between searches and again in the
    // final answer, so a stroke is remembered by its material, not its number.
    let stagger = 0;
    for (const row of next.candidates) if (row.item >= 0 && !this.seen.has(`c:${row.item}`)) { mark(`c:${row.item}`, stagger); stagger += 90; }
    next.cards.forEach(card => mark(`k:${card.id}`));
    next.cited.forEach(row => { if (row.item >= 0) mark(`n:${row.item}`); });
    if (next.stage !== this.state.stage) this.seen.set(`stage:${next.stage}`, now);
    this.state = next;
    this.wake();
  }

  resize() {
    const box = this.canvas.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(box.width * dpr);
    this.canvas.height = Math.round(box.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.layout(box.width, box.height);
    this.wake();
  }

  /** Rows for the topics, named at the left; the years along the bottom; the question at the right. */
  layout(w, h) {
    const {ctx, map} = this;
    this.w = w; this.h = h;
    this.narrow = w < 560;
    this.labelSize = this.narrow ? 10.5 : 12;
    ctx.font = `600 ${this.labelSize}px ${this.font}`;
    const widest = Math.max(...map.topics.map(topic => ctx.measureText(topic.l).width));
    this.left = Math.ceil(widest) + (this.narrow ? 14 : 20);
    this.right = this.ambient ? 44 : this.narrow ? 54 : 76;
    this.top = this.ambient ? 16 : this.narrow ? 26 : 30;
    this.bottom = this.narrow ? 24 : 28;
    this.laneH = (h - this.top - this.bottom) / map.topics.length;
    const today = map.years[0] + (Date.now() - Date.UTC(map.years[0], 0, 1)) / YEAR_MS;
    this.t0 = map.years[0];
    this.t1 = Math.max(map.latest, today) + 0.03;
    this.nodeX = w - this.right * 0.46;
    this.nodeY = this.top + (h - this.top - this.bottom) / 2;
  }

  x(when) { return this.left + (Math.min(this.t1, Math.max(this.t0, when)) - this.t0) / (this.t1 - this.t0) * (this.w - this.left - this.right); }

  laneY(g) { return this.top + this.laneH * (g + 0.5); }

  /** Where a material's stroke is; a source the layout has no day for goes by its own date. */
  place(i, source) {
    const when = this.map.when[i];
    return {x: this.x(when >= this.t0 ? when : yearOf(source?.date) || this.t0), y: this.laneY(this.map.g[i])};
  }

  /** The thread from a stroke to the question: along its row, then bending in. */
  thread(x, y, endY = this.nodeY) {
    const nx = this.nodeX - 10;
    return [x, y, x + (nx - x) * 0.55, y, nx - (nx - x) * 0.18, endY, nx, endY];
  }

  wake() {
    this.lastActive = performance.now();
    if (!this.frame && this.visible) this.frame = requestAnimationFrame(t => this.draw(t));
  }

  bindPointer() {
    const c = this.canvas;
    let down = null;
    const at = event => { const b = c.getBoundingClientRect(); return {x: event.clientX - b.left, y: event.clientY - b.top}; };
    const hit = p => this.hits.find(h => p.x >= h.x && p.x <= h.x + h.w && p.y >= h.y && p.y <= h.y + h.h);
    const onDown = event => { down = at(event); };
    const onMove = event => {
      const p = at(event);
      if (down && Math.hypot(p.x - down.x, p.y - down.y) > 6) down.moved = true;
      const h = hit(p);
      if ((h?.id || null) !== (this.hover?.id || null)) { this.hover = h || null; this.wake(); }
      c.style.cursor = h?.source ? 'pointer' : 'default';
    };
    const onUp = event => {
      if (down && !down.moved) {
        const h = hit(at(event));
        if (h?.source) {
          this.onSelect?.(h.id);
          const source = this.state.sources.get(h.id);
          const url = openableUrl(source?.url);
          if (url) { window.open(url, '_blank', 'noopener,noreferrer'); this.onOpen?.(source); }
        }
      }
      down = null;
    };
    const onCancel = () => { down = null; };
    const onLeave = () => { if (this.hover) { this.hover = null; this.wake(); } };
    c.addEventListener('pointerdown', onDown);
    c.addEventListener('pointermove', onMove);
    c.addEventListener('pointerup', onUp);
    c.addEventListener('pointercancel', onCancel);
    c.addEventListener('pointerleave', onLeave);
    this.unbind = () => {
      c.removeEventListener('pointerdown', onDown); c.removeEventListener('pointermove', onMove);
      c.removeEventListener('pointerup', onUp); c.removeEventListener('pointercancel', onCancel);
      c.removeEventListener('pointerleave', onLeave);
    };
  }

  draw(now) {
    this.frame = 0;
    if (!this.w) return;
    // The start screen's map moves slowly; half the frames are enough for it.
    if (this.ambient && now - this.lastDraw < 30) { this.frame = requestAnimationFrame(t => this.draw(t)); return; }
    this.lastDraw = now;
    const {ctx, map, state} = this;
    const live = state.live && !this.reduce;
    const stage = state.stage;
    const searching = live && (stage === 'retrieving' || stage === 'matching');
    const writing = (live && (stage === 'thinking' || stage === 'drafting' || stage === 'checking' || stage === 'repairing'))
      || (this.ambient && !this.reduce);
    const since = key => (this.seen.has(key) ? (now - this.seen.get(key)) / 1000 : -1);
    ctx.clearRect(0, 0, this.w, this.h);
    this.hits = [];

    const items = new Map();       // lit material -> {id, at}: seconds since it lit
    for (const row of state.candidates) {
      const at = row.item >= 0 ? since(`c:${row.item}`) : -1;
      if (at >= 0) items.set(row.item, {id: row.id, at});   // not yet lit: still an ordinary stroke
    }
    const citedItems = new Map();  // cited material -> {id as the text numbers it, at}
    for (const row of state.cited) {
      if (row.item < 0) continue;
      const at = since(`n:${row.item}`);
      citedItems.set(row.item, {id: row.id, at});
      if (!items.has(row.item)) items.set(row.item, {id: row.id, at: Math.max(0, at)});
    }
    const focus = items.size > 0 ? Math.min(1, Math.max(...[...items.values()].map(v => ease(v.at / 1.2)))) : 0;
    const dim = state.done ? 1 : focus;
    // Rows name themselves brighter where the candidates are, and once answered, the cited sources.
    const activeLanes = new Set([...(state.done && citedItems.size ? citedItems : items).keys()].map(i => map.g[i]));
    const span = this.laneH * 0.3;
    const bottomY = this.h - this.bottom;

    // Years: a faint line where each begins, its name in the middle of its span.
    ctx.lineWidth = 1;
    ctx.font = `${this.narrow ? 10 : 11}px ${this.font}`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    for (let year = Math.ceil(this.t0); year < this.t1; year++) {
      const x = Math.round(this.x(year)) + 0.5, end = this.x(Math.min(this.t1, year + 1));
      ctx.strokeStyle = `rgba(${CREAM},0.07)`;
      ctx.beginPath(); ctx.moveTo(x, this.top - 6); ctx.lineTo(x, bottomY + 3); ctx.stroke();
      if (end - x > 26) {
        ctx.fillStyle = `rgba(${CREAM},${this.ambient ? 0.3 : 0.4})`;
        ctx.fillText(String(year), (x + end) / 2, bottomY + 17);
      }
    }

    // A row for each topic, named at the left.
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    map.topics.forEach((topic, g) => {
      const y = this.laneY(g), on = activeLanes.has(g);
      ctx.font = `${on ? 600 : 400} ${this.labelSize}px ${this.font}`;
      ctx.fillStyle = on ? `rgba(${MINT},0.95)` : `rgba(${CREAM},${this.ambient ? 0.34 : 0.48})`;
      ctx.fillText(topic.l, this.left - (this.narrow ? 8 : 12), y);
      ctx.strokeStyle = `rgba(${CREAM},0.045)`;
      ctx.beginPath(); ctx.moveTo(this.left, Math.round(y) + 0.5); ctx.lineTo(this.w - this.right + 4, Math.round(y) + 0.5); ctx.stroke();
    });

    // The search: a beam from today back to 2020, waking the strokes it passes.
    let beam = null;
    if (searching) {
      const from = this.x(this.t1), to = this.x(this.t0);
      beam = from - ((now / 2600) % 1) * (from - to);
      const glow = ctx.createLinearGradient(beam, 0, beam + 64, 0);
      glow.addColorStop(0, `rgba(${MINT},0.2)`); glow.addColorStop(1, `rgba(${MINT},0)`);
      ctx.fillStyle = glow; ctx.fillRect(beam, this.top - 6, 64, bottomY - this.top + 9);
      ctx.fillStyle = `rgba(${MINT},0.75)`; ctx.fillRect(beam - 0.75, this.top - 6, 1.5, bottomY - this.top + 9);
    }

    // Every material: one stroke each, the rest a little quieter once an answer's are lit.
    const n = map.keys.length;
    ctx.fillStyle = `rgba(${CREAM},${(this.ambient ? 0.15 : 0.17) * (1 - 0.3 * dim)})`;
    for (let i = 0; i < n; i++) {
      if (items.has(i) || !(map.when[i] >= this.t0)) continue;
      ctx.fillRect(this.x(map.when[i]) - 0.5, this.laneY(map.g[i]) - span, 1, span * 2);
    }
    if (beam !== null) for (let i = 0; i < n; i++) {
      if (items.has(i)) continue;
      const x = this.x(map.when[i]), d = x - beam;
      if (!(d >= 0 && d < 54)) continue;
      const f = 1 - d / 54, reach = span + f * this.laneH * 0.1;
      ctx.fillStyle = `rgba(${CREAM},${0.5 * f})`;
      ctx.fillRect(x - 0.5, this.laneY(map.g[i]) - reach, 1, reach * 2);
    }

    // The last question's strokes, going out.
    for (const [i, gone] of this.fading) {
      const k = 1 - (now - gone) / 700;
      if (k <= 0) { this.fading.delete(i); continue; }
      const {x, y} = this.place(i);
      ctx.fillStyle = `rgba(${MINT},${0.8 * k})`;
      ctx.fillRect(x - 0.75, y - span * 1.2, 1.5, span * 2.4);
    }

    // The judgment cards this answer draws on: cream diamonds above the question, a thread to each
    // material they were distilled from.
    const cards = state.cards.map((pick, j) => {
      const card = map.cards[map.cardIndex.get(pick.id)];
      if (!card) return null;
      return {card, k: ease(since(`k:${card.id}`) / 0.8), x: this.nodeX, y: Math.max(this.top, this.nodeY - 30 - j * (this.narrow ? 17 : 21))};
    }).filter(Boolean);
    for (const {card, k, x, y} of cards) {
      ctx.strokeStyle = `rgba(${CREAM},${0.2 * k})`; ctx.lineWidth = 1;
      for (const i of card.src) {
        const p = this.place(i);
        const path = this.thread(p.x, p.y, y);
        ctx.beginPath(); ctx.moveTo(p.x, p.y);
        ctx.bezierCurveTo(path[2], path[3], path[4], path[5], x - 6, y); ctx.stroke();
        ctx.fillStyle = `rgba(${CREAM},${0.8 * k})`;
        ctx.beginPath(); ctx.arc(p.x, p.y, 2, 0, TAU); ctx.fill();
      }
    }

    // The answer's materials: lit, each with its thread to the question; while the answer is
    // written, particles carry them in, more along the ones already cited. On the start screen
    // one particle a thread, slowly: another reader's answer, drawn from these.
    const threads = [];
    for (const [i, info] of items) {
      const source = state.sources.get(info.id);
      const cited = citedItems.has(i);
      const tone = cited && membersOnly(source) ? GOLD : MINT;
      const quiet = state.done && !cited;   // found, not used
      const k = ease(info.at / 0.7);
      const {x, y} = this.place(i, source);
      if (!quiet) {
        const path = this.thread(x, y);
        ctx.strokeStyle = `rgba(${tone},${(cited ? 0.7 : 0.3) * k})`;
        ctx.lineWidth = cited ? 1.3 : 1;
        ctx.beginPath(); ctx.moveTo(x, y);
        const steps = 28, upto = Math.round(steps * k);
        for (let s = 1; s <= upto; s++) { const p = along(path, s / steps); ctx.lineTo(p.x, p.y); }
        ctx.stroke();
        threads.push({path, cited, tone});
        const flash = info.at < 0.6 ? 1 - info.at / 0.6 : 0;   // a candidate as it is found
        const r = 10 + 8 * flash;
        const halo = ctx.createRadialGradient(x, y, 0, x, y, r);
        halo.addColorStop(0, `rgba(${tone},${(cited ? 0.5 : 0.3 + 0.4 * flash) * k})`); halo.addColorStop(1, `rgba(${tone},0)`);
        ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
      }
      const reach = this.laneH * (cited ? 0.42 : 0.36);
      ctx.fillStyle = `rgba(${tone},${quiet ? 0.32 : 0.05 + 0.95 * k})`;
      ctx.fillRect(x - (cited ? 1 : 0.75), y - reach, cited ? 2 : 1.5, reach * 2);
      if (cited) { ctx.beginPath(); ctx.arc(x, y, 3, 0, TAU); ctx.fill(); }
      if (state.selected === info.id) {
        ctx.strokeStyle = `rgba(${CREAM},0.9)`; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.arc(x, y, 6.5, 0, TAU); ctx.stroke();
      }
    }
    if (writing) {
      const t = now / 1000;
      threads.forEach(({path, cited, tone}, j) => {
        const count = this.ambient ? 1 : cited ? 3 : 1;
        for (let s = 0; s < count; s++) {
          const p = along(path, (t * (this.ambient ? 0.18 : cited ? 0.55 : 0.35) + s / count + j * 0.137) % 1);
          ctx.fillStyle = `rgba(${tone},${cited ? 0.95 : 0.6})`;
          ctx.beginPath(); ctx.arc(p.x, p.y, cited ? 1.8 : 1.3, 0, TAU); ctx.fill();
        }
      });
    }

    // The question, today, at the right.
    const pulse = live ? 0.5 + 0.5 * Math.sin(now / 420) : 0.6;
    const core = ctx.createRadialGradient(this.nodeX, this.nodeY, 0, this.nodeX, this.nodeY, 26);
    core.addColorStop(0, `rgba(${MINT},${0.26 + 0.12 * pulse})`); core.addColorStop(1, `rgba(${MINT},0)`);
    ctx.fillStyle = core; ctx.beginPath(); ctx.arc(this.nodeX, this.nodeY, 26, 0, TAU); ctx.fill();
    ctx.strokeStyle = `rgba(${MINT},0.9)`; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(this.nodeX, this.nodeY, 8, 0, TAU); ctx.stroke();
    ctx.fillStyle = `rgba(${MINT},0.95)`; ctx.beginPath(); ctx.arc(this.nodeX, this.nodeY, 3, 0, TAU); ctx.fill();
    if (stage === 'checking' || stage === 'repairing' || state.done) {
      const k = state.done ? 1 : ease(since(`stage:${stage}`) / 2.4);
      ctx.strokeStyle = `rgba(${MINT},0.95)`; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(this.nodeX, this.nodeY, 12.5, -Math.PI / 2, -Math.PI / 2 + TAU * k); ctx.stroke();
    }
    if (!this.ambient) {
      ctx.font = `${this.narrow ? 10 : 11}px ${this.font}`; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      ctx.fillStyle = `rgba(${CREAM},0.6)`;
      ctx.fillText(this.words.question, this.nodeX, this.nodeY + (this.narrow ? 27 : 30));
    }
    for (const {card, k, x, y} of cards) {
      const glow = ctx.createRadialGradient(x, y, 0, x, y, 11);
      glow.addColorStop(0, `rgba(${CREAM},${0.4 * k})`); glow.addColorStop(1, `rgba(${CREAM},0)`);
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(x, y, 11, 0, TAU); ctx.fill();
      const s = 2.5 + 2.5 * k;
      ctx.fillStyle = `rgba(${CREAM},${0.4 + 0.6 * k})`;
      ctx.beginPath(); ctx.moveTo(x, y - s); ctx.lineTo(x + s, y); ctx.lineTo(x, y + s); ctx.lineTo(x - s, y); ctx.closePath(); ctx.fill();
      this.hits.push({id: `card:${card.id}`, title: card.title, x: x - 9, y: y - 9, w: 18, h: 18});
    }

    // Numbers on the cited sources, as in the text; each opens its source. Left to right, each
    // above or below its stroke, or moved aside along the row, with a short line back.
    ctx.font = `600 11px ${this.font}`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    const marks = [...citedItems].map(([i, info]) => ({info, ...this.place(i, state.sources.get(info.id))}))
      .sort((a, b) => a.x - b.x);
    const taken = [{x: this.nodeX - 28, y: this.nodeY - 18, w: 56, h: 52}];
    const clear = (x, y, w, h) => x >= this.left - 4 && y >= 1 && x + w <= this.w - 2 && y + h <= bottomY + 2
      && !taken.some(o => x < o.x + o.w + 2 && x + w + 2 > o.x && y < o.y + o.h + 2 && y + h + 2 > o.y);
    for (const {info, x, y} of marks) {
      const label = info.id.slice(1);
      const tone = membersOnly(state.sources.get(info.id)) ? GOLD : MINT;
      const k = ease(info.at / 0.5);
      const w = Math.max(17, ctx.measureText(label).width + 10), h = 16;
      const reach = this.laneH * 0.42;
      const spots = [];
      for (let step = 0; step < 8; step++) {
        for (const dx of step ? [step * (w + 3), -step * (w + 3)] : [0]) {
          spots.push([x - w / 2 + dx, y - reach - h - 2, step * 2], [x - w / 2 + dx, y + reach + 2, step * 2 + 1]);
        }
      }
      spots.sort((a, b) => a[2] - b[2]);
      let [bx, by] = spots.find(([sx, sy]) => clear(sx, sy, w, h)) || spots[0];
      bx = Math.max(2, Math.min(this.w - w - 2, bx));
      taken.push({x: bx, y: by, w, h});
      ctx.globalAlpha = k;
      if (Math.abs(bx + w / 2 - x) > 3) {
        ctx.strokeStyle = `rgba(${tone},0.6)`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, by > y ? y + reach : y - reach); ctx.lineTo(bx + w / 2, by > y ? by : by + h); ctx.stroke();
      }
      const open = state.selected === info.id || this.hover?.id === info.id;
      ctx.fillStyle = open ? `rgb(${CREAM})` : `rgb(${tone})`;
      roundRect(ctx, bx, by, w, h, 4); ctx.fill();
      ctx.fillStyle = '#0b2f1b';
      ctx.fillText(label, bx + w / 2, by + 12);
      ctx.globalAlpha = 1;
      this.hits.push({id: info.id, source: true, x: bx - 3, y: by - 3, w: w + 6, h: h + 6});
      this.hits.push({id: info.id, source: true, x: x - 6, y: y - reach, w: 12, h: reach * 2});
    }
    // Candidates beside the map open their source too, while there is no answer yet.
    if (!state.done) for (const [i, info] of items) {
      if (citedItems.has(i) || !state.sources.has(info.id)) continue;
      const {x, y} = this.place(i, state.sources.get(info.id));
      this.hits.push({id: info.id, source: true, x: x - 6, y: y - span, w: 12, h: span * 2});
    }

    // What the pointer is on: the source's number, date and title, or the judgment card.
    if (this.hover) {
      const source = state.sources.get(this.hover.id);
      const text = this.hover.title ? `${this.words.card} · ${this.hover.title}`
        : source?.title ? `${this.hover.id.slice(1)} · ${(source.date || '').slice(0, 7).replace('-', '.')} · ${membersOnly(source) ? `${this.words.member} · ` : ''}${source.title}${openableUrl(source.url) ? ' ↗' : ''}`
        : '';
      if (text) {
        ctx.font = `12px ${this.font}`;
        const maxW = Math.min(this.w - 16, 340);
        let shown = text;
        while (ctx.measureText(shown).width > maxW - 16 && shown.length > 4) shown = shown.slice(0, -2);
        if (shown !== text) shown = shown.slice(0, -1) + '…';
        const w = ctx.measureText(shown).width + 16, h = 24;
        const x = Math.max(8, Math.min(this.w - w - 8, this.hover.x + this.hover.w / 2 - w / 2));
        const y = this.hover.y - h - 6 >= 4 ? this.hover.y - h - 6 : this.hover.y + this.hover.h + 6;
        ctx.fillStyle = 'rgba(6,28,16,0.94)'; roundRect(ctx, x, y, w, h, 6); ctx.fill();
        ctx.strokeStyle = `rgba(${MINT},0.4)`; ctx.lineWidth = 1; roundRect(ctx, x, y, w, h, 6); ctx.stroke();
        ctx.fillStyle = `rgba(${CREAM},0.95)`; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'; ctx.fillText(shown, x + 8, y + 16);
      }
    }

    const moving = live || (this.ambient && !this.reduce) || this.fading.size > 0 || now - this.lastActive < 2600
      || [...this.seen.values()].some(t => now - t < 1500);
    if (moving && this.visible) this.frame = requestAnimationFrame(t => this.draw(t));
  }
}

/**
 * One finished frame of the map, drawn once into `canvas` at `width`×`height` (scaled by `scale`):
 * what a saved image or PDF shows, numbers and all, with no observers and no animation. Returns
 * where each numbered stroke and its number landed, so a PDF can link them to their sources.
 */
export function paintAnswerMap(canvas, map, state, {width, height, scale = 1, font = 'system-ui, sans-serif', words = {}}) {
  const view = Object.create(AnswerMap.prototype);
  Object.assign(view, {
    canvas, ctx: canvas.getContext('2d'), map, reduce: true, ambient: false, words: {...WORDS, ...words},
    state: {live: false, stage: '', candidates: [], cards: [], cited: [], sources: new Map(), selected: null},
    seen: new Map(), fading: new Map(), hits: [], hover: null, visible: false, frame: 0, lastActive: 0, lastDraw: 0, font,
  });
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  view.ctx.setTransform(scale, 0, 0, scale, 0, 0);
  view.layout(width, height);
  view.update(state);             // with reduce, everything has already arrived
  view.draw(performance.now());
  return view.hits.filter(hit => hit.source);
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
