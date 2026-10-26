// Album list of the server's library (GET /albums, server/build_albums.py).
// The last list is cached in chrome.storage.local so the page shows at once; a cold Lambda takes
// ~10 s to answer the refresh.
// SERVER ({ url, apiKey } of the deployed Lambda) comes from config.js.
const $ = (id) => document.getElementById(id);
const STORE_NAME = { bandcamp: t('store_bandcamp'), knkrtworld: 'KNKRTWORLD', gumroad: 'Gumroad' };
const OUT_ICON = '<svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>';
// Tiles for albums without cover art: [background, text].
const TONES = [['#2b2320', '#e9d9c8'], ['#e6e1d6', '#111010'], ['#3a1712', '#ffb59b'],
  ['#1f2a2e', '#cfe6ee'], ['#f2eee8', '#2a1410'], ['#2a2725', '#f2eee8']];
let albums = [];
let filter = 'all';  // all | buy | none
const expanded = new Set();  // ids of albums the user opened

const trackName = (key) => key.split('/').pop().replace(/\.[^.]+$/, '');

// Track names without the prefix every file of the album shares ("Artist - Album - 01 Song" ->
// "01 Song"), cut at the last " - " of that prefix.
function trackNames(a) {
  if (!a.names) {
    const names = a.tracks.map(trackName);
    let p = names.length > 1 ? names[0] : '';
    for (const n of names) while (!n.startsWith(p)) p = p.slice(0, -1);
    const cut = p.lastIndexOf(' - ');
    a.names = cut > 0 ? names.map((n) => n.slice(cut + 3)) : names;
  }
  return a.names;
}

function status(text, error = false) {
  $('status').textContent = text;
  $('status').className = error ? 'error' : '';
}

// Text with every occurrence of q wrapped in <mark>.
function highlighted(tag, text, q) {
  const el = document.createElement(tag);
  if (!q) {
    el.textContent = text;
    return el;
  }
  const low = text.toLowerCase();
  let i = 0;
  for (let j = low.indexOf(q); j >= 0; j = low.indexOf(q, i)) {
    el.append(text.slice(i, j));
    const m = document.createElement('mark');
    m.textContent = text.slice(j, j + q.length);
    el.append(m);
    i = j + q.length;
  }
  el.append(text.slice(i));
  return el;
}

function tile(a) {
  const el = document.createElement('div');
  el.className = 'art';
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label', t('no_cover_of', albumTitle(a.title)));
  let h = 0;
  for (const c of a.id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  [el.style.background, el.style.color] = TONES[h % TONES.length];
  el.textContent = albumTitle(a.title) || '?';
  return el;
}

// The store's large cover (loaded from its image CDN), else the small inlined one, else a tile.
function art(a) {
  const sources = [a.art, a.cover].filter(Boolean);
  if (!sources.length) return tile(a);
  const img = document.createElement('img');
  img.className = 'art';
  img.alt = t('cover_alt', albumTitle(a.title));
  img.loading = 'lazy';
  img.decoding = 'async';
  let i = 0;
  img.src = sources[0];
  img.onerror = () => {
    if (++i < sources.length) img.src = sources[i];
    else img.replaceWith(tile(a));
  };
  return img;
}

function storeLink(store) {
  const link = document.createElement('a');
  const isAlbum = store.kind === 'album';
  const name = STORE_NAME[store.store] || store.store;
  link.className = `store ${isAlbum ? 'buy' : 'artist'}`;
  link.href = store.url;
  link.target = '_blank';
  link.rel = 'noopener';
  link.title = isAlbum ? t('buy_album_title', name) : t('artist_page_title', name);
  link.innerHTML = OUT_ICON;
  link.prepend(isAlbum ? t('buy') : t('artist'));
  return link;
}

function card(a, q, trackHit) {
  const el = document.createElement('article');
  el.className = 'album';
  const meta = document.createElement('div');
  meta.className = 'meta';
  const title = highlighted('h2', albumTitle(a.title), q);
  title.title = albumTitle(a.title);
  const sub = document.createElement('div');
  sub.className = 'sub';
  sub.append(highlighted('span', a.artist || t('no_artist'), a.artist ? q : ''), plural(a.tracks.length, 'track_count'));
  meta.append(title, sub);

  const actions = document.createElement('div');
  actions.className = 'actions';
  const more = document.createElement('button');
  more.type = 'button';
  more.textContent = t('track_list');
  more.setAttribute('aria-expanded', 'false');
  actions.append(more);
  if (a.store?.url?.startsWith('https://')) {
    actions.append(storeLink(a.store));
  } else {
    const none = document.createElement('span');
    none.className = 'no-store';
    none.textContent = t('no_store');
    actions.append(none);
  }
  el.append(art(a), meta, actions);

  let list = null;
  // matchesOnly: an album found through track names lists just those (a click shows all).
  const toggle = (show, matchesOnly = false) => {
    more.setAttribute('aria-expanded', String(show && !matchesOnly));
    more.textContent = show && !matchesOnly ? t('collapse') : t('track_list');
    el.classList.toggle('open', show && !matchesOnly);
    list?.remove();
    list = null;
    if (!show) return;
    list = document.createElement('ol');  // file names mostly carry their own track numbers
    list.className = 'tracks';
    trackNames(a).forEach((name, i) => {
      if (matchesOnly && !name.toLowerCase().includes(q)) return;
      const li = highlighted('li', name, q);
      li.title = a.tracks[i];
      list.append(li);
    });
    el.append(list);
  };
  more.onclick = () => {
    const show = more.getAttribute('aria-expanded') !== 'true';
    show ? expanded.add(a.id) : expanded.delete(a.id);
    toggle(show);
  };
  if (expanded.has(a.id)) toggle(true);
  else if (trackHit) toggle(true, true);
  return el;
}

function render() {
  const q = $('search').value.trim().toLowerCase();
  const box = $('albums');
  box.textContent = '';
  let shown = 0;
  for (const a of albums) {
    if (filter === 'buy' && !a.store) continue;
    if (filter === 'none' && a.store) continue;
    const own = !q || albumTitle(a.title).toLowerCase().includes(q) || a.artist.toLowerCase().includes(q);
    const trackHit = !own && trackNames(a).some((n) => n.toLowerCase().includes(q));
    if (!own && !trackHit) continue;
    box.append(card(a, q, trackHit));
    shown++;
  }
  if (!shown && albums.length) {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = t('no_results');
    box.append(p);
  }
  const stats = $('summary');
  stats.textContent = '';
  if (albums.length) {
    const tracks = albums.reduce((n, a) => n + a.tracks.length, 0);
    const linked = albums.filter((a) => a.store).length;
    for (const [n, label] of [[albums.length, t('stat_albums')], [tracks, t('stat_tracks')], [linked, t('stat_links')]]) {
      const s = document.createElement('span');
      const b = document.createElement('strong');
      b.textContent = n.toLocaleString(LANG);
      s.append(b, label);
      stats.append(s);
    }
  }
  $('shown').textContent = albums.length ? t('shown_count', shown.toLocaleString(LANG)) : '';
}

async function load() {
  let cached = null;
  try {
    ({ albumsCache: cached } = await chrome.storage.local.get('albumsCache'));
  } catch {}
  if (cached?.albums) {
    albums = cached.albums;
    render();
  }
  status(albums.length ? t('checking_list') : t('loading_list'));
  try {
    const r = await fetch(`${SERVER.url}/albums`, { headers: { 'X-Api-Key': SERVER.apiKey } });
    if (!r.ok) {
      // The server's code (ApiError in server/app.py), not its Korean detail.
      let code = 'server';
      try { code = (await r.json()).code || code; } catch {}
      throw Object.assign(new Error(`server ${r.status}`), { info: { code, status: r.status } });
    }
    albums = (await r.json()).albums;
    render();
    status('');
    try {
      await chrome.storage.local.set({ albumsCache: { albums } });
    } catch {}
  } catch (e) {
    const msg = errorText(e instanceof TypeError ? { code: 'no_connection' } : e.info || { code: 'server' });
    status(albums.length ? t('showing_saved', msg) : msg, true);
  }
}

localize();
$('search').addEventListener('input', render);
for (const b of $('filters').querySelectorAll('button')) {
  b.onclick = () => {
    filter = b.dataset.f;
    for (const o of $('filters').querySelectorAll('button')) o.setAttribute('aria-pressed', String(o === b));
    render();
  };
}
load();
