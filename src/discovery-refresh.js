// Public lists share the CDN cache. Poll only while the page is visible; each home-page visit
// owns one watcher and stops it on leaving. A late/aborted read must never replace newer data.
export const DISCOVERY_REFRESH_MS = 60_000;

// Delay additions while someone reads, but apply withdrawals/content edits immediately. Only
// visible rows matter: a bounded list's unseen tail rolling off is not a reason to move the page.
export function changedDiscoveryIds(shown, lists) {
  const cards = [...lists.frequent, ...lists.recent];
  const latest = new Map(cards.map(card => [card.public_id, card]));
  for (const card of cards) for (const other of card.similar || []) if (!latest.has(other.public_id)) latest.set(other.public_id, other);
  return new Set(shown.filter(card => {
    const next = latest.get(card.public_id);
    return !next || ['revision', 'question', 'summary', 'share_url'].some(key => key in card && card[key] !== next[key]);
  }).map(card => card.public_id));
}

export function watchDiscoveryLists({read, onLists, onUnavailable = () => {}, window: win = window, document: doc = document}) {
  let stopped = false, current = null, lastStarted = -Infinity;
  const cancel = () => {
    if (!current) return;
    clearTimeout(current.timer);
    current.controller.abort();
    current = null;
  };
  const refresh = (force = false) => {
    if (stopped || doc.visibilityState === 'hidden' || current || (!force && Date.now() - lastStarted < 1000)) return;
    lastStarted = Date.now();
    const request = {controller: new AbortController(), timer: null};
    current = request;
    request.timer = setTimeout(() => {
      if (current !== request) return;
      cancel();
      onUnavailable();
    }, 8000);
    void Promise.resolve().then(() => read(request.controller.signal)).then(lists => {
      if (stopped || current !== request || request.controller.signal.aborted) return;
      if (lists) onLists(lists, {manual: force});
      else onUnavailable();
    }).catch(() => {
      if (!stopped && current === request && !request.controller.signal.aborted) onUnavailable();
    }).finally(() => {
      if (current === request) { clearTimeout(request.timer); current = null; }
    });
  };
  const resume = () => {
    if (doc.visibilityState === 'hidden') {
      if (current) { cancel(); lastStarted = -Infinity; }
    } else refresh();
  };
  win.addEventListener('focus', resume);
  doc.addEventListener('visibilitychange', resume);
  const timer = setInterval(refresh, DISCOVERY_REFRESH_MS);
  refresh();
  return {
    refresh: () => refresh(true),
    stop() {
      stopped = true;
      clearInterval(timer);
      cancel();
      win.removeEventListener('focus', resume);
      doc.removeEventListener('visibilitychange', resume);
    },
  };
}
