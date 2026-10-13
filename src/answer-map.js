// The answer map: every public material as a light on a tilted disc, drawn while an answer is
// found and kept beside it afterwards. Distance from the centre is when a material was published,
// the direction is its topic, the colour its form; the ring around the question holds the
// judgment cards. The service serves the layout (/api/answer-map, rebuilt with each context
// update by scripts/build_answer_map.py). The stream lights up what it actually used: the
// candidates it found, the cards it chose, and the sources an answer cites, numbered as in the
// text. A numbered light opens its source, as a citation does.

let loading;
export function loadAnswerMap() {
  loading ??= fetch('/api/answer-map', {credentials: 'omit'})
    .then(response => { if (!response.ok) throw new Error(`answer map: ${response.status}`); return response.json(); })
    .then(indexMap)
    .catch(error => { loading = null; throw error; });
  return loading;
}

const GROUP = {v: 0, d: 0, w: 1, r: 1, x: 1, c: 2, b: 2};
const COLOR = [[248, 241, 228], [143, 212, 164], [226, 194, 124]];
const MINT = '143,212,164';
const GOLD = '226,194,124';
const CREAM = '248,241,228';
const TAU = Math.PI * 2;
const YOUTUBE = /(?:youtube\.com\/(?:watch\?v=|live\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/;

function indexMap(data) {
  const byKey = new Map(data.keys.map((key, i) => [key, i]));
  const byVideo = new Map();
  data.keys.forEach((key, i) => {
    const id = key.startsWith('videos/') ? key.slice(16) : key.startsWith('dialogues/youtube-') ? key.slice(18) : '';
    if (id.length === 11 && !byVideo.has(id)) byVideo.set(id, i);
  });
  return {...data, byKey, byVideo, cardIndex: new Map(data.cards.map((card, i) => [card.id, i]))};
}

/** The light a source belongs to: by its public copy's path, else by its video. */
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

export class AnswerMap {
  constructor(canvas, map, {onSelect, reduce = false} = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.map = map;
    this.onSelect = onSelect;
    this.reduce = reduce;
    this.rot = -0.35;
    this.spin = reduce ? 0 : 0.045;
    this.state = {live: false, stage: '', candidates: [], cards: [], cited: [], sources: new Map(), selected: null};
    this.seen = new Map();          // when each thing first appeared: 'c:S1', 'k:card', 'n:S3'
    this.hits = [];
    this.hover = null;
    this.visible = true;
    this.frame = 0;
    this.lastActive = 0;
    this.font = getComputedStyle(canvas).fontFamily || 'system-ui, sans-serif';
    const n = map.kinds.length;
    this.turn = new Float32Array(n);
    this.radius = new Float32Array(n);
    for (let i = 0; i < n; i++) { this.turn[i] = map.t[i] / 10000; this.radius[i] = map.r[i] / 1000; }
    this.bindPointer();
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
    // Candidates light one after another. Numbers change between searches and again in the
    // final answer, so a light is remembered by its material, not its number.
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
    this.w = box.width; this.h = box.height;
    this.canvas.width = Math.round(box.width * dpr);
    this.canvas.height = Math.round(box.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.narrow = box.width < 560;
    this.tilt = this.narrow ? 0.5 : 0.42;
    // Room for the topic names around the rim.
    this.R = Math.min(box.width * (this.narrow ? 0.4 : 0.41), (box.height - 34) / (2 * this.tilt * 1.18));
    this.cx = box.width / 2;
    this.cy = box.height / 2 + 4;
    this.wake();
  }

  wake() {
    this.lastActive = performance.now();
    if (!this.frame && this.visible) this.frame = requestAnimationFrame(t => this.draw(t));
  }

  point(turn, radius, rot = this.rot) {
    const a = turn * TAU + rot;
    return {x: this.cx + Math.cos(a) * radius * this.R, y: this.cy + Math.sin(a) * radius * this.R * this.tilt, z: Math.sin(a)};
  }

  bindPointer() {
    const c = this.canvas;
    let down = null;
    const at = event => { const b = c.getBoundingClientRect(); return {x: event.clientX - b.left, y: event.clientY - b.top}; };
    const hit = p => this.hits.find(h => p.x >= h.x && p.x <= h.x + h.w && p.y >= h.y && p.y <= h.y + h.h);
    const onDown = event => { down = {...at(event), rot: this.rot, moved: false}; c.setPointerCapture?.(event.pointerId); };
    const onMove = event => {
      const p = at(event);
      if (down) {
        if (Math.abs(p.x - down.x) > 4) down.moved = true;
        if (down.moved && !this.reduce) { this.rot = down.rot + (p.x - down.x) * 0.006; this.wake(); }
        return;
      }
      const h = hit(p);
      if ((h?.id || null) !== (this.hover?.id || null)) { this.hover = h || null; this.wake(); }
      c.style.cursor = h ? 'pointer' : 'grab';
    };
    const onUp = event => {
      if (down && !down.moved) { const h = hit(at(event)); if (h) this.onSelect?.(h.id); }
      down = null;
    };
    const onLeave = () => { if (this.hover) { this.hover = null; this.wake(); } };
    c.addEventListener('pointerdown', onDown);
    c.addEventListener('pointermove', onMove);
    c.addEventListener('pointerup', onUp);
    c.addEventListener('pointercancel', () => { down = null; });
    c.addEventListener('pointerleave', onLeave);
    this.unbind = () => {
      c.removeEventListener('pointerdown', onDown); c.removeEventListener('pointermove', onMove);
      c.removeEventListener('pointerup', onUp); c.removeEventListener('pointerleave', onLeave);
    };
  }

  draw(now) {
    this.frame = 0;
    if (!this.w) return;
    const {ctx, map, state} = this;
    const live = state.live && !this.reduce;
    if (live || now - this.lastActive < 2600) this.rot += this.spin / 60;
    ctx.clearRect(0, 0, this.w, this.h);

    const stage = state.stage;
    const searching = live && (stage === 'retrieving' || stage === 'matching');
    const since = key => (this.seen.has(key) ? (now - this.seen.get(key)) / 1000 : -1);
    const items = new Map();       // lit material -> {id, at}: seconds since it lit
    for (const row of state.candidates) {
      const at = row.item >= 0 ? since(`c:${row.item}`) : -1;
      if (at >= 0) items.set(row.item, {id: row.id, at});   // not yet lit: still an ordinary light
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
    const activeTopics = new Set([...items.keys()].map(i => map.g[i]));

    // Year rings: faint, with the first and last year named along one radius.
    const [first, last] = map.years;
    const [inner, outer, cardRing] = map.radius;
    ctx.lineWidth = 1;
    for (let y = first; y <= last; y++) {
      const r = inner + (outer - inner) * ((y - first) / (last - first));
      ctx.strokeStyle = `rgba(${CREAM},0.05)`;
      ctx.beginPath(); ctx.ellipse(this.cx, this.cy, r * this.R, r * this.R * this.tilt, 0, 0, TAU); ctx.stroke();
    }
    ctx.font = `11px ${this.font}`;
    ctx.textAlign = 'center';
    ctx.fillStyle = `rgba(${CREAM},0.34)`;
    const words = [];              // where text is, so the numbers keep clear of it
    const ruler = Math.PI * 0.22;
    for (const [label, r] of [[String(first), inner + 0.03], [String(last - 1), outer - 0.04]]) {
      const x = this.cx + Math.cos(ruler) * r * this.R, y = this.cy + Math.sin(ruler) * r * this.R * this.tilt;
      ctx.fillText(label, x, y + 4);
      words.push({x: x - 14, y: y - 6, w: 28, h: 13});
    }

    // Topic names at the rim, brighter where the answer's materials are.
    map.topics.forEach((topic, t) => {
      const mid = (topic.a[0] + topic.a[1]) / 2;
      const p = this.point(mid, 1.1);
      const on = activeTopics.has(t);
      ctx.font = `${on ? '600 ' : ''}${this.narrow ? 11 : 12}px ${this.font}`;
      ctx.fillStyle = `rgba(${on ? MINT : CREAM},${on ? 0.95 : 0.42 * (0.7 + 0.3 * (p.z + 1) / 2)})`;
      const w = ctx.measureText(topic.l).width;
      const x = Math.max(w / 2 + 6, Math.min(this.w - w / 2 - 6, p.x));   // whole, at a narrow map's edge
      ctx.fillText(topic.l, x, p.y + 4);
      words.push({x: x - w / 2 - 2, y: p.y - 8, w: w + 4, h: 16});
    });

    // The sweep while it reads: a beam from the question that wakes the lights it passes.
    let beam = null;
    if (searching) {
      beam = ((now / 1000) * 0.9) % TAU;
      const end = {x: this.cx + Math.cos(beam) * this.R, y: this.cy + Math.sin(beam) * this.R * this.tilt};
      const g = ctx.createLinearGradient(this.cx, this.cy, end.x, end.y);
      g.addColorStop(0, `rgba(${MINT},0)`); g.addColorStop(1, `rgba(${MINT},0.4)`);
      ctx.strokeStyle = g; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(this.cx, this.cy); ctx.lineTo(end.x, end.y); ctx.stroke();
    }

    // Every material.
    const n = map.kinds.length;
    for (let i = 0; i < n; i++) {
      if (items.has(i)) continue;
      const p = this.point(this.turn[i], this.radius[i]);
      const depth = 0.6 + 0.4 * (p.z + 1) / 2;
      const kind = map.kinds[i];
      let a = (kind === 'r' ? 0.42 : 0.66) * depth * (1 - 0.55 * dim);
      let size = (kind === 'r' ? 1.3 : 1.85) * depth;
      if (beam !== null) {
        const d = Math.atan2(Math.sin(this.turn[i] * TAU + this.rot - beam), Math.cos(this.turn[i] * TAU + this.rot - beam));
        if (d < 0 && d > -0.35) { const f = 1 + d / 0.35; a = Math.min(1, a + f * 0.55); size += f * 1.4; }
      }
      const [r, g, b] = COLOR[GROUP[kind] ?? 1];
      ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, size / 2, 0, TAU); ctx.fill();
    }

    // The judgment cards this answer draws on: each sits between the question and the materials
    // it was distilled from, in their direction, with a thread to each of them.
    for (const pick of state.cards) {
      const card = map.cards[map.cardIndex.get(pick.id)];
      if (!card) continue;
      const p = this.point(card.t / 10000, cardRing);
      const k = ease(since(`k:${card.id}`) / 0.8);
      ctx.strokeStyle = `rgba(${GOLD},${0.32 * k})`; ctx.lineWidth = 1;
      for (const i of card.src) {
        const q = this.point(this.turn[i], this.radius[i]);
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke();
        ctx.fillStyle = `rgba(${GOLD},${0.85 * k})`;
        ctx.beginPath(); ctx.arc(q.x, q.y, 2.2, 0, TAU); ctx.fill();
      }
      ctx.strokeStyle = `rgba(${GOLD},${0.6 * k})`;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(this.cx, this.cy); ctx.stroke();
      const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 12);
      glow.addColorStop(0, `rgba(${GOLD},${0.45 * k})`); glow.addColorStop(1, `rgba(${GOLD},0)`);
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(p.x, p.y, 12, 0, TAU); ctx.fill();
      const s = 3 + 2.5 * k;
      ctx.fillStyle = `rgba(${GOLD},${0.4 + 0.6 * k})`;
      ctx.beginPath(); ctx.moveTo(p.x, p.y - s); ctx.lineTo(p.x + s, p.y); ctx.lineTo(p.x, p.y + s); ctx.lineTo(p.x - s, p.y); ctx.closePath(); ctx.fill();
    }

    // Candidates: lit, each with its thread to the question; while the answer is written,
    // particles carry them in, more along the ones already cited.
    const writing = live && (stage === 'thinking' || stage === 'drafting' || stage === 'checking' || stage === 'repairing');
    const threads = [];
    for (const [i, info] of items) {
      const p = this.point(this.turn[i], this.radius[i]);
      const k = ease(info.at / 0.9);
      const cited = citedItems.has(i);
      const lift = Math.min(p.y, this.cy) - Math.abs(p.x - this.cx) * 0.16 - 16;
      const ctrl = {x: (p.x + this.cx) / 2, y: lift};
      const quiet = state.done && !cited;
      ctx.strokeStyle = `rgba(${MINT},${(quiet ? 0.12 : cited ? 0.7 : 0.32) * k})`;
      ctx.lineWidth = cited ? 1.4 : 1;
      ctx.beginPath(); ctx.moveTo(p.x, p.y);
      const steps = 22, upto = Math.round(steps * k);
      for (let s = 1; s <= upto; s++) {
        const u = s / steps;
        ctx.lineTo((1 - u) * (1 - u) * p.x + 2 * (1 - u) * u * ctrl.x + u * u * this.cx, (1 - u) * (1 - u) * p.y + 2 * (1 - u) * u * ctrl.y + u * u * this.cy);
      }
      ctx.stroke();
      threads.push({p, ctrl, cited});
      const halo = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 13);
      halo.addColorStop(0, `rgba(${MINT},${(quiet ? 0.15 : 0.5) * k})`); halo.addColorStop(1, `rgba(${MINT},0)`);
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(p.x, p.y, 13, 0, TAU); ctx.fill();
      const [r, g, b] = COLOR[GROUP[map.kinds[i]] ?? 1];
      ctx.fillStyle = `rgba(${r},${g},${b},${quiet ? 0.45 : 1})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, cited ? 3.4 : 2.8, 0, TAU); ctx.fill();
    }
    if (writing) {
      const t = now / 1000;
      threads.forEach(({p, ctrl, cited}, j) => {
        const count = cited ? 4 : 2;
        for (let s = 0; s < count; s++) {
          const u = (t * (cited ? 0.7 : 0.4) + s / count + j * 0.137) % 1;
          ctx.fillStyle = `rgba(${MINT},${cited ? 0.95 : 0.55})`;
          ctx.beginPath();
          ctx.arc((1 - u) * (1 - u) * p.x + 2 * (1 - u) * u * ctrl.x + u * u * this.cx, (1 - u) * (1 - u) * p.y + 2 * (1 - u) * u * ctrl.y + u * u * this.cy, cited ? 1.9 : 1.4, 0, TAU);
          ctx.fill();
        }
      });
    }

    // The question.
    const pulse = live ? 0.5 + 0.5 * Math.sin(now / 420) : 0.6;
    const core = ctx.createRadialGradient(this.cx, this.cy, 0, this.cx, this.cy, 30);
    core.addColorStop(0, `rgba(${MINT},${0.28 + 0.12 * pulse})`); core.addColorStop(1, `rgba(${MINT},0)`);
    ctx.fillStyle = core; ctx.beginPath(); ctx.arc(this.cx, this.cy, 30, 0, TAU); ctx.fill();
    ctx.strokeStyle = `rgba(${MINT},0.9)`; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(this.cx, this.cy, 8, 0, TAU); ctx.stroke();
    if (stage === 'checking' || stage === 'repairing' || state.done) {
      const k = state.done ? 1 : ease(since(`stage:${stage}`) / 2.4);
      ctx.strokeStyle = `rgba(${MINT},0.95)`; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(this.cx, this.cy, 13, -Math.PI / 2, -Math.PI / 2 + TAU * k); ctx.stroke();
    }

    // Numbers on the cited sources, as in the text; each opens its source. Lowest first, each in
    // the first spot beside its light that is clear of text, other numbers and cited lights, with
    // a short line back to the light when it had to move away.
    this.hits = [];
    ctx.font = `600 11px ${this.font}`;
    ctx.textAlign = 'center';
    const cited = [...citedItems].map(([i, info]) => ({info, p: this.point(this.turn[i], this.radius[i])}))
      .sort((a, b) => Number(a.info.id.slice(1)) - Number(b.info.id.slice(1)));
    const taken = [...words, ...cited.map(({p}) => ({x: p.x - 4, y: p.y - 4, w: 8, h: 8}))];
    const clear = (x, y, w, h) => x >= 2 && y >= 2 && x + w <= this.w - 2 && y + h <= this.h - 2
      && !taken.some(o => x < o.x + o.w + 2 && x + w + 2 > o.x && y < o.y + o.h + 2 && y + h + 2 > o.y);
    for (const {info, p} of cited) {
      const label = info.id.slice(1);
      const k = ease(info.at / 0.5);
      const w = Math.max(18, ctx.measureText(label).width + 10), h = 17;
      const spots = [[6, -h - 5], [-w - 6, -h - 5], [9, -h / 2], [-w - 9, -h / 2], [6, 5], [-w - 6, 5]]
        .map(([dx, dy]) => [p.x + dx, p.y + dy]);
      for (let up = 1; up <= 6; up++) spots.push([p.x + 6, p.y - h - 5 - up * (h + 3)], [p.x - w - 6, p.y - h - 5 - up * (h + 3)]);
      let [x, y] = spots.find(([sx, sy]) => clear(sx, sy, w, h)) || spots[0];
      x = Math.max(2, Math.min(this.w - w - 2, x)); y = Math.max(2, Math.min(this.h - h - 2, y));
      taken.push({x, y, w, h});
      const nx = Math.max(x, Math.min(p.x, x + w)), ny = Math.max(y, Math.min(p.y, y + h));
      ctx.globalAlpha = k;
      if (Math.hypot(nx - p.x, ny - p.y) > 10) {
        ctx.strokeStyle = `rgba(${MINT},0.55)`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(nx, ny); ctx.stroke();
      }
      const open = state.selected === info.id;
      if (open) {
        ctx.strokeStyle = `rgba(${CREAM},0.9)`; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.arc(p.x, p.y, 7, 0, TAU); ctx.stroke();
      }
      ctx.fillStyle = open || this.hover?.id === info.id ? `rgb(${CREAM})` : `rgb(${MINT})`;
      roundRect(ctx, x, y, w, h, 5); ctx.fill();
      ctx.fillStyle = '#0b2f1b';
      ctx.fillText(label, x + w / 2, y + 12.5);
      ctx.globalAlpha = 1;
      this.hits.push({id: info.id, x: x - 3, y: y - 3, w: w + 6, h: h + 6});
      this.hits.push({id: info.id, x: p.x - 7, y: p.y - 7, w: 14, h: 14});
    }
    // Candidates shown beside the map open their source too, while there is no answer yet.
    if (!state.done) for (const [i, info] of items) {
      if (citedItems.has(i) || !state.sources.has(info.id)) continue;
      const p = this.point(this.turn[i], this.radius[i]);
      this.hits.push({id: info.id, x: p.x - 7, y: p.y - 7, w: 14, h: 14});
    }

    // What the pointer is on: the source's title and date.
    if (this.hover) {
      const source = state.sources.get(this.hover.id);
      if (source?.title) {
        const text = `${this.hover.id.slice(1)} · ${(source.date || '').slice(0, 7).replace('-', '.')} · ${source.title}`;
        ctx.font = `12px ${this.font}`;
        const maxW = Math.min(this.w - 16, 320);
        let shown = text;
        while (ctx.measureText(shown).width > maxW - 16 && shown.length > 4) shown = shown.slice(0, -2);
        if (shown !== text) shown = shown.slice(0, -1) + '…';
        const w = ctx.measureText(shown).width + 16, h = 24;
        const x = Math.max(8, Math.min(this.w - w - 8, this.hover.x)), y = Math.max(8, this.hover.y - h - 6);
        ctx.fillStyle = 'rgba(6,28,16,0.92)'; roundRect(ctx, x, y, w, h, 6); ctx.fill();
        ctx.strokeStyle = `rgba(${MINT},0.4)`; ctx.lineWidth = 1; roundRect(ctx, x, y, w, h, 6); ctx.stroke();
        ctx.fillStyle = `rgba(${CREAM},0.95)`; ctx.textAlign = 'left'; ctx.fillText(shown, x + 8, y + 16);
      }
    }

    const moving = live || now - this.lastActive < 2600 || [...this.seen.values()].some(t => now - t < 1500);
    if (moving && this.visible) this.frame = requestAnimationFrame(t => this.draw(t));
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
