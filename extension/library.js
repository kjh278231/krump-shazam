// Album list of the server's library (GET /albums, server/build_albums.py).
// The last list is cached in chrome.storage.local so the page shows at once; a cold Lambda takes
// ~10 s to answer the refresh.
// The deployed AWS Lambda (docs/lambda-deployment.md); a local server is set in 서버 설정.
const DEFAULT_SERVER = 'https://n7m4s62lmqfrtif557t7i4omji0evzfa.lambda-url.ap-northeast-2.on.aws';
const $ = (id) => document.getElementById(id);
let albums = [];
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
function highlighted(text, q) {
  const span = document.createElement('span');
  if (!q) {
    span.textContent = text;
    return span;
  }
  const low = text.toLowerCase();
  let i = 0;
  for (let j = low.indexOf(q); j >= 0; j = low.indexOf(q, i)) {
    span.append(text.slice(i, j));
    const m = document.createElement('mark');
    m.textContent = text.slice(j, j + q.length);
    span.append(m);
    i = j + q.length;
  }
  span.append(text.slice(i));
  return span;
}

function card(a, q, trackHit) {
  const el = document.createElement('section');
  el.className = 'album';
  const head = document.createElement('button');
  head.setAttribute('aria-expanded', 'false');
  let cover;
  if (a.cover) {
    cover = document.createElement('img');
    cover.src = a.cover;
    cover.alt = '';
  } else {
    cover = document.createElement('div');
    cover.textContent = (a.title || '?').trim().charAt(0).toUpperCase();
  }
  cover.className = 'cover';
  const info = document.createElement('div');
  info.className = 'info';
  const title = highlighted(a.title, q);
  title.className = 'title';
  title.title = a.title;
  const artist = highlighted(a.artist || '아티스트 정보 없음', a.artist ? q : '');
  artist.className = 'artist';
  const count = document.createElement('div');
  count.className = 'count';
  count.textContent = `${a.tracks.length}곡`;
  info.append(title, artist, count);
  head.append(cover, info);
  el.append(head);

  let list = null;
  // matchesOnly: an album found through track names lists just those (a click shows all).
  const toggle = (show, matchesOnly = false) => {
    head.setAttribute('aria-expanded', String(show && !matchesOnly));
    list?.remove();
    list = null;
    if (!show) return;
    list = document.createElement('ul');  // file names mostly carry their own track numbers
    list.className = 'tracks';
    trackNames(a).forEach((name, i) => {
      if (matchesOnly && !name.toLowerCase().includes(q)) return;
      const li = document.createElement('li');
      li.append(highlighted(name, q));
      li.title = a.tracks[i];
      list.append(li);
    });
    el.append(list);
  };
  head.onclick = () => {
    const show = head.getAttribute('aria-expanded') !== 'true';
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
    const own = !q || a.title.toLowerCase().includes(q) || a.artist.toLowerCase().includes(q);
    const trackHit = !own && trackNames(a).some((n) => n.toLowerCase().includes(q));
    if (!own && !trackHit) continue;
    box.append(card(a, q, trackHit));
    shown++;
  }
  if (!shown && albums.length) {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = '검색 결과가 없습니다.';
    box.append(p);
  }
  const tracks = albums.reduce((n, a) => n + a.tracks.length, 0);
  $('summary').textContent = albums.length
    ? `앨범 ${albums.length.toLocaleString()}개 · ${tracks.toLocaleString()}곡${q ? ` · ${shown}개 표시` : ''}`
    : '';
}

async function load() {
  let settings;
  try {
    ({ settings } = await chrome.storage.local.get('settings'));
  } catch {}
  const server = { url: DEFAULT_SERVER, apiKey: '', ...settings };
  let cached = null;
  try {
    ({ albumsCache: cached } = await chrome.storage.local.get('albumsCache'));
  } catch {}
  if (cached?.url === server.url) {
    albums = cached.albums;
    render();
  }
  status(albums.length ? '최신 목록 확인 중…' : '서버에서 불러오는 중… (서버가 쉬고 있었다면 10초 정도 걸립니다)');
  try {
    const r = await fetch(`${server.url}/albums`, { headers: server.apiKey ? { 'X-Api-Key': server.apiKey } : {} });
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json()).detail || ''; } catch {}
      throw new Error(`서버 오류 ${r.status}${detail ? `: ${detail}` : ''}`);
    }
    albums = (await r.json()).albums;
    render();
    status('');
    try {
      await chrome.storage.local.set({ albumsCache: { url: server.url, albums } });
    } catch {}
  } catch (e) {
    const msg = e instanceof TypeError ? `서버(${server.url})에 연결할 수 없습니다. 팝업의 서버 설정을 확인하세요.` : e.message;
    status(albums.length ? `${msg} — 저장해 둔 목록을 보여줍니다.` : msg, true);
  }
}

$('search').addEventListener('input', render);
load();
