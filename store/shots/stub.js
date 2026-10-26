// Stands in for the chrome.* API and the server so the real popup and library pages render a
// scene for the store screenshots and the layout check (store/shots/make.py). Loaded first, from
// popup.html / library.html in this folder: ?lang=ko|en&scene=<name>.
(() => {
  const q = new URLSearchParams(location.search);
  const lang = q.get('lang') || 'ko';
  const scene = q.get('scene') || 'idle';
  const getJSON = (url) => {
    const x = new XMLHttpRequest();
    x.open('GET', url, false);  // synchronous: the page script reads messages as it loads
    x.send();
    return JSON.parse(x.responseText);
  };
  const messages = getJSON(`../../extension/_locales/${lang}/messages.json`);

  const TAB = 'EBS RUSSIA | LADY WHIPHEAD VS PRINCESS TCHOKO | KRUMP BATTLE';
  const VIDEO = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
  const song = (artist, album, file, start, store) => ({
    file: `${artist} - ${album} - ${file}.mp3`, path: `${album}/${file}.mp3`, folder: album, match_start: start,
    album: { title: album, artist, cover: null, store },
  });
  const BANGA = song('10XGULLY', 'GOD LIKE IV', '02 BANGA TUCKED', 194,
    { store: 'bandcamp', kind: 'album', url: 'https://example.bandcamp.com/album/god-like-iv' });
  const HAYO = song('PLAYA', 'UNRELEASED 2Ø18', '02 HA YO', 41,
    { store: 'bandcamp', kind: 'artist', url: 'https://example.bandcamp.com' });
  const SINGLE = song('BIG MIJO', '단일 곡', 'GET BUCK', 12, null);
  const done = (result) => ({ status: 'done', tabTitle: TAB, result: { id: 'shot', ...result } });

  // scene: [session state, tab url, GET /video songs, consent]
  const SCENES = {
    consent: [{ status: 'idle' }, '', null, false],
    consent_changed: [{ status: 'idle' }, '', null, '2026-10-18'],
    idle: [{ status: 'idle' }, 'https://example.com', null, true],
    error: [{ status: 'error', error: { code: 'silent' } }, 'https://example.com', null, true],
    error_unknown: [{ status: 'error', error: { code: 'nope', status: 502 } }, 'https://example.com', null, true],
    records: [{ status: 'idle' }, VIDEO, [{ song: BANGA, status: 'confirmed' }], true],
    records_list: [{ status: 'idle' }, VIDEO,
      [{ song: BANGA, status: 'verified' }, { song: HAYO, status: 'confirmed' }, { song: SINGLE, status: 'confirmed' }], true],
    recording: [{ status: 'recording', duration: 30, tabTitle: TAB }, 'https://example.com', null, true],
    searching: [{ status: 'searching', duration: 60, tabTitle: TAB }, 'https://example.com', null, true],
    found: [done({ verdict: 'confirmed', songs: [BANGA] }), 'https://example.com', null, true],
    unsure: [done({ verdict: 'check', songs: [HAYO] }), 'https://example.com', null, true],
    single: [done({ verdict: 'confirmed', songs: [SINGLE] }), 'https://example.com', null, true],
    fail: [done({ verdict: 'fail', songs: [] }), 'https://example.com', null, true],
  };
  const [state, url, songs, consent] = SCENES[scene] || SCENES.idle;
  const local = { consent: consent === true ? '2026-10-22' : consent, history: [] };
  const area = (data) => ({
    get: async (keys) => Object.fromEntries([keys].flat().filter((k) => k in data).map((k) => [k, data[k]])),
    set: async () => {}, remove: async () => {},
  });

  window.chrome = {
    i18n: { getMessage: (name, subs = []) => messages[name]?.message.replace(/\$(\d)/g, (_, n) => subs[n - 1] ?? '') ?? '' },
    storage: { local: area(local), session: area({ state }), onChanged: { addListener() {} } },
    tabs: { query: async () => [{ id: 1, url, title: TAB }], create() {} },
    runtime: { getURL: (f) => f, sendMessage: async () => {} },
    action: { setBadgeText() {} },
    tabCapture: { getMediaStreamId: async () => { throw new Error('shot'); } },
  };
  window.SERVER = { url: 'https://server.invalid', apiKey: '' };
  const json = (body) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
  window.fetch = async (u) => {
    const path = new URL(u, location.href).pathname;
    if (path === '/health') return json({ ok: true });
    if (path === '/video') return json({ key: 'yt:dQw4w9WgXcQ', songs: songs || [] });
    if (path === '/albums') return json(getJSON('albums.json'));
    return new Response('', { status: 404 });
  };
})();
