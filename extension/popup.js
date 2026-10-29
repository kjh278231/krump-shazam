// SERVER ({ url, apiKey } of the deployed Lambda) comes from config.js; there is no settings UI.
// One screen per state: 대기 → 찾는 중 → 찾음 / 찾음(확실하지 않음) / 못 찾음. The state lives in
// chrome.storage.session (background.js), because the popup closes whenever it loses focus.
// Searching needs the user's consent to the privacy policy (privacy.html): recordings are kept
// on the server to improve accuracy. Without it only the consent screen is shown.
// Consent is to one version of the policy, its 시행일. When the policy changes, set POLICY to the
// new 시행일 and policy_change_1/2 (both _locales) to what changed (one or two lines): earlier
// consents (1.0.0 stored `true`) are asked again. 1.1.0 was never published, so the lines are what
// changed since 1.0.0. The policy is in five places that change together: privacy.html,
// privacy.en.html, docs/privacy-policy.md, docs/privacy-policy.en.md and those two messages.
// In a video tab the 대기 screen also shows the songs other users found in this video (GET /video,
// docs/video-records.md) and this browser's own searches there.
const POLICY = '2026-10-22';
const POLICY_CHANGE = [t('policy_change_1'), t('policy_change_2')];
const $ = (id) => document.getElementById(id);
const authHeaders = () => ({ 'X-Api-Key': SERVER.apiKey });
// Every search records up to 30 s (the length the server's thresholds are calibrated on). Only
// after 못 찾음 can the next search, and that one alone, listen longer.
const FIRST_LENGTH = 30;
const RETRY_LENGTHS = [30, 45, 60];
// The video lookup does not wait for a cold Lambda (13-15 s): without an answer the popup works as
// before, and the request has still woken the server for the search.
const VIDEO_TIMEOUT = 3000;
const OWN_ROWS = 5;  // this browser's own searches listed per video
const DOTS_STEP = 500;  // ms between steps of the dots after 찾는 중
const STORE_NAME = { bandcamp: t('store_bandcamp'), knkrtworld: 'KNKRTWORLD', gumroad: 'Gumroad' };
const ICON = {
  listen: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M6.3 6.3a8 8 0 0 0 0 11.4M17.7 6.3a8 8 0 0 1 0 11.4"/></svg>',
  stop: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  again: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
  check: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  warn: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"><path d="M12 7v6"/><path d="M12 17h.01"/></svg>',
  play: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l10.5-6.5z"/></svg>',
  pause: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
  out: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>',
};

let consent = false;  // chrome.storage.local 'consent' is POLICY
let agreedBefore = false;  // consented to an earlier policy: the consent screen says what changed
const openPage = (file) => chrome.tabs.create({ url: chrome.runtime.getURL(file) });
let tick = null;  // steps the dots of the 찾는 중 screen (listening)
let serverDot = '';  // '', 'on' or 'off' (checkServer)
let answered = null;  // { id, correct } of the last result answered (ask)
let current = {};  // the state last rendered
// The active tab's video, looked up when the popup opens (loadVideo).
let tabVideo = null;  // { key, platform } (video.js videoKey), or null when the tab shows no video
let known = null;  // GET /video: { songs } other users found in it, or null
let mine = [];  // this browser's own searches in videos ('history', background.js remember)
let videoSeq = 0;  // a lookup overtaken by a newer one is dropped

// h('div', { class: 'x', onclick: f }, child, 'text', ...): a DOM element. `html` sets a static
// icon; everything else is text.
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  el.append(...children.flat().filter((c) => c != null && c !== false));
  return el;
}
const icon = (name) => h('span', { html: ICON[name], 'aria-hidden': 'true', style: 'display:contents' });

function fmt(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
const lengthLabel = (s) => (s % 60 ? t('length_seconds', s) : t('length_minutes', s / 60));  // 45 -> 45초, 60 -> 1분

function setDot(state) {
  serverDot = state;
  const dot = document.querySelector('.dot');
  if (dot) {
    dot.className = `dot ${serverDot}`;
    dot.parentElement.title = serverDot === 'on' ? t('server_on') : serverDot === 'off' ? t('server_off') : '';
  }
}

async function checkServer() {
  // Also wakes a cold Lambda while the popup is open, before the user clicks.
  try {
    setDot((await fetch(`${SERVER.url}/health`)).ok ? 'on' : 'off');
  } catch {
    setDot('off');
  }
}

// The active tab's video (from its URL; the page itself is never read) and what others found in
// it. Not while recording: next to the recorder's checks this request could start a second cold
// Lambda instance. In a video tab it takes the place of /health (same number of requests as
// before); elsewhere /health as before.
async function loadVideo() {
  const seq = ++videoSeq;
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true }).catch(() => []);
  tabVideo = videoKey(t?.url);
  known = null;
  if (!tabVideo) return checkServer();
  // A result or error from another video would hide this one's records: back to the start.
  if ((current.status === 'done' || current.status === 'error') && current.video?.key !== tabVideo.key) {
    await toIdle();
    current = { status: 'idle' };
  }
  render(current);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), VIDEO_TIMEOUT);
  try {
    const q = new URLSearchParams({ key: tabVideo.key });
    const r = await fetch(`${SERVER.url}/video?${q}`, { headers: authHeaders(), signal: ctl.signal });
    const body = r.ok ? await r.json() : null;
    if (seq !== videoSeq) return;
    known = body?.key === tabVideo.key ? body : null;
    setDot(r.ok ? 'on' : 'off');
  } catch (e) {
    if (seq === videoSeq && e.name !== 'AbortError') setDot('off');
  } finally {
    clearTimeout(timer);
  }
  if (seq === videoSeq) render(current);
}

async function start(duration = FIRST_LENGTH) {
  if (!consent) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  let streamId;
  try {
    streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  } catch (e) {
    render({ status: 'error', error: { code: 'capture', detail: e.message } });
    return;
  }
  chrome.runtime.sendMessage({
    target: 'background', type: 'start', streamId, duration, tabTitle: tab.title, tabUrl: tab.url, share: true,
    video: videoKey(tab.url),
  });
}

function toIdle() {
  chrome.action.setBadgeText({ text: '' });
  return chrome.storage.session.set({ state: { status: 'idle' } });
}

// Header right side: library (with the server dot), REC, or back to the start.
function headAction(state) {
  const box = $('head-action');
  box.textContent = '';
  if (state.status === 'recording' || state.status === 'searching') {
    box.append(h('span', { class: 'rec' }, 'REC'));
  } else if (state.status === 'done' && state.result?.verdict !== 'fail') {
    // The video's records are looked up again: the song just found now shows as a record.
    const again = () => { toIdle(); render({ status: 'idle' }); loadVideo(); };
    box.append(h('button', { class: 'chip-btn', onclick: again }, icon('again'), t('search_again')));
  } else {
    box.append(h('button', {
      class: 'chip-btn', title: t('library_hint'),
      onclick: () => openPage('library.html'),
    }, h('span', { class: `dot ${serverDot}`, 'aria-hidden': 'true' }), t('library')));
  }
}

function render(state = {}) {
  current = state || {};
  clearInterval(tick);
  headAction(current);
  const view = $('view');
  view.textContent = '';
  const parts = current.status === 'recording' || current.status === 'searching' ? listening(current)
    : current.status === 'done' && current.result ? result(current.result, current)
      : consent ? idle(current.status === 'error' ? current.error : '') : consentScreen();
  view.append(...parts.filter(Boolean));
}

async function setConsent(value) {
  consent = value;
  agreedBefore = false;  // agreed now, or withdrawn: no change to point out next time
  try { await chrome.storage.local.set({ consent: value ? POLICY : false }); } catch {}
  if (!value) {  // withdrawing also forgets this browser's own record of searches
    mine = [];
    try { await chrome.storage.local.remove('history'); } catch {}
  }
  const { state } = await chrome.storage.session.get('state');
  render(state);
  if (value) loadVideo();
}

function consentScreen() {
  return [
    h('div', {}, h('h1', { class: 'headline' }, t('consent_title')),
      h('div', { class: 'sub' }, t('consent_sub'))),
    agreedBefore && h('div', { class: 'note' }, t('policy_changed'), POLICY_CHANGE.flatMap((l) => [h('br'), l])),
    h('ul', { class: 'tips' },
      h('li', {}, t('consent_1')),
      h('li', {}, t('consent_2')),
      h('li', {}, t('consent_3')),
      h('li', {}, t('consent_4'))),
    h('button', { class: 'linkish', onclick: () => openPage(t('privacy_page')) }, t('privacy_full')),
    h('button', { class: 'primary', onclick: () => setConsent(true) }, icon('check'), t('agree')),
    h('div', { class: 'hint' }, t('consent_hint')),
  ];
}

function idle(error) {
  return [
    h('div', {}, h('h1', { class: 'headline' }, t('idle_title')),
      h('div', { class: 'sub' }, t('idle_sub'))),
    error && h('div', { class: 'error', role: 'alert' }, errorText(error)),
    h('button', { class: 'primary', onclick: () => start() }, icon('listen'), t('find_now')),
    h('div', { class: 'hint' }, t('idle_hint', lengthLabel(FIRST_LENGTH)), h('br'),
      t('idle_hint_close')),
    videoRecords(),
    h('div', { class: 'foot' },
      h('button', { class: 'linkish', onclick: () => openPage(t('privacy_page')) }, t('privacy')),
      h('span', { 'aria-hidden': 'true' }, '·'),
      h('button', { class: 'linkish', onclick: () => setConsent(false) }, t('withdraw'))),
  ];
}

// "이 영상에서 찾은 곡": the songs other users found in this video and this browser's own searches
// there that the shared songs do not already show (unsure finds nobody confirmed, 못 찾음). Where
// in the video each one plays is not known. A single shared song gets the full card (cover,
// preview, store link); there is no 맞음 / 틀림 on it, a doubtful user searches instead.
function videoRecords() {
  if (!tabVideo) return null;
  const songs = known?.songs || [];
  const shown = new Set(songs.map((s) => s.song.path));
  const own = mine.filter((e) => e.key === tabVideo.key && !(e.song && shown.has(e.song.path))).slice(-OWN_ROWS).reverse();
  if (!songs.length && !own.length) return null;
  const row = (name, sub) => h('li', {},
    h('span', { class: 'what' }, h('span', { class: 'name', title: name }, name), sub && h('span', { class: 'tag' }, sub)));
  const albumLine = (m) => [m.album?.title && albumTitle(m.album.title), m.album?.artist].filter(Boolean).join(' · ') || m.folder || null;
  const rows = songs.length === 1 ? [] : songs.map((s) => row(trackName(s.song), albumLine(s.song)));
  for (const e of own) {
    rows.push(e.song ? row(trackName(e.song), e.verdict === 'check' ? t('mine_unsure') : t('mine')) : row(t('not_found'), t('mine')));
  }
  const one = songs.length === 1 ? songs[0] : null;
  return h('section', { class: 'vlist', 'aria-label': t('video_songs') },
    h('div', { class: 'label' }, t('video_songs')),
    one && h('div', { class: 'note info' }, one.status === 'verified'
      ? t('video_verified') : t('video_found')),
    one && h('div', { class: 'card' }, ...songCard(one.song)),
    rows.length > 0 && h('ul', {}, rows));
}

function listening(state) {
  const searching = state.status === 'searching';
  // No elapsed time or progress bar: they kept the screen redrawing while the tab is captured.
  // Only the dots after 찾는 중 step (1, 2, 3, 1, ...), so the search visibly goes on.
  const time = h('span', { class: 'num' }, searching ? t('searching') : t('max_length', fmt(state.duration || 30)));
  const eq = h('div', { class: 'eq', 'aria-hidden': 'true' }, ...Array.from({ length: 7 }, () => h('span')));
  const dots = h('span', { class: 'dots', 'data-n': '1', 'aria-hidden': 'true' }, ...Array.from({ length: 3 }, () => h('span', {}, '.')));
  tick = setInterval(() => { dots.dataset.n = String((Number(dots.dataset.n) % 3) + 1); }, DOTS_STEP);
  return [
    h('div', { class: 'listen-head' },
      h('div', {}, h('h1', { class: 'headline' }, t('listening_title'), dots),
        h('div', { class: 'sub' }, searching ? t('searching_sub') : t('listening_sub'))),
      eq),
    h('div', { class: 'panel' },
      h('div', { class: 'panel-row' }, h('span', {}, t('listening_tab')), time),
      h('div', { class: 'tab-title', title: state.tabTitle || '' }, state.tabTitle || t('current_tab'))),
    searching
      ? h('button', { class: 'secondary', disabled: true }, t('searching_button'))
      : h('button', { class: 'secondary', onclick: () => chrome.runtime.sendMessage({ target: 'background', type: 'stop' }) },
        icon('stop'), t('stop_and_search')),
    h('button', { class: 'linkish cancel', onclick: cancel }, t('cancel')),
  ];
}

// Drops the search: the recording stops, nothing is searched or shown, and the start screen returns.
function cancel() {
  chrome.runtime.sendMessage({ target: 'background', type: 'cancel' });
  render({ status: 'idle' });
  loadVideo();
}

function verdictRow(kind, text, state) {
  return h('div', { class: 'verdict' },
    h('span', { class: `badge ${kind}` }, kind === 'found' ? icon('check') : kind === 'unsure' ? icon('warn') : null, text),
    h('span', { class: 'tab', title: state.tabTitle || '' }, state.tabTitle || ''));
}

// "Artist - Album - 02 Song" -> "02 Song": the album line names the album. m.album is the album
// object of a result, or just its title in this browser's own record.
function trackName(m) {
  const title = typeof m.album === 'string' ? m.album : m.album?.title;
  let name = m.file.replace(/\.[^.]+$/, '');
  const cut = title ? name.lastIndexOf(`${title} - `) : -1;
  if (cut >= 0 && cut + title.length + 3 < name.length) name = name.slice(cut + title.length + 3);
  return name;
}

function result(r, state) {
  if (r.verdict === 'fail' || !r.songs.length) {
    // The longer lengths are offered here only, for this one retry; nothing is remembered.
    let retry = FIRST_LENGTH;
    const lengths = h('div', { class: 'lengths' });
    const pick = (s) => {
      retry = s;
      for (const b of lengths.children) b.setAttribute('aria-pressed', String(Number(b.dataset.s) === s));
    };
    for (const s of RETRY_LENGTHS) {
      lengths.append(h('button', { 'data-s': s, 'aria-pressed': String(s === retry), onclick: () => pick(s) }, lengthLabel(s)));
    }
    return [
      verdictRow('none', t('not_found'), state),
      h('h1', { class: 'headline sm' }, t('not_found_title_1'), h('br'), t('not_found_title_2')),
      h('ul', { class: 'tips' },
        h('li', {}, t('tip_loud')),
        h('li', {}, t('tip_longer')),
        h('li', {}, t('tip_missing'))),
      h('div', { role: 'group', 'aria-label': t('retry_length_group') },
        h('div', { class: 'label' }, t('retry_length')), lengths),
      h('button', { class: 'primary', onclick: () => start(retry) }, icon('again'), t('search_again')),
    ];
  }
  // check = a rhythm or low-score fingerprint candidate: right most of the time, but not always.
  const unsure = r.verdict === 'check';
  const m = r.songs[0];  // only the best song is shown
  const out = [
    verdictRow(unsure ? 'unsure' : 'found', unsure ? t('found_unsure') : t('found'), state),
    unsure && h('div', { class: 'note' }, t('unsure_note')),
    ...songCard(m),
  ];
  if (r.id) out.push(ask(r.id, m.path, r.video?.key));  // only stored queries take feedback
  return out;
}

// A found song: cover, names, where in the song, preview and store link. The same card shows a
// search's result and a recorded one (m.match_start is then where the song is now).
function songCard(m) {
  const album = m.album;
  const store = album?.store?.url?.startsWith('https://') ? album.store : null;
  const name = trackName(m);
  const albumName = (album?.title && albumTitle(album.title)) || m.folder || '';

  const cover = album?.cover
    ? h('img', { class: 'cover', src: album.cover, alt: t('cover_alt', albumName) })
    : h('div', { class: 'cover', role: 'img', 'aria-label': t('no_cover') }, albumName || '♪');

  const out = [
    h('div', { class: 'song' }, cover,
      h('div', { class: 'song-text' },
        h('div', {}, h('div', { class: 'track' }, name),
          albumName && h('div', { class: 'album' }, albumName),
          album?.artist && h('div', { class: 'artist' }, album.artist)),
        h('span', { class: 'pos' }, t('position', fmt(m.match_start))))),
    preview(m),
  ];
  if (store) {
    const shop = STORE_NAME[store.store] || store.store;
    const isAlbum = store.kind === 'album';
    out.push(h('a', {
      class: isAlbum ? 'primary' : 'outline', href: store.url, target: '_blank', rel: 'noopener',
      onclick: (e) => { e.preventDefault(); chrome.tabs.create({ url: store.url }); },
    }, isAlbum ? t('buy_album', shop) : t('see_artist', shop), icon('out')));
    if (!isAlbum) out.push(h('div', { class: 'hint' }, t('artist_only')));
  }
  return out;
}

// The server cuts a 10 s excerpt from the matched spot; full files are never sent. Fetched rather
// than set as <audio src> because the request needs the API key header. Confirmed and unsure
// finds get the same player. Played at half volume: at full volume it was too loud next to the tab.
function preview(m) {
  const p = $('player');
  p.volume = 0.5;
  const btn = h('button', { class: 'play' });
  const time = h('span', {}, '0:10');  // stays still while playing, like every screen
  let loaded = false;
  const show = (state) => {
    btn.disabled = state === 'loading';
    const playing = state === 'playing';
    btn.setAttribute('aria-label', playing ? t('preview_pause') : t('preview_play'));
    btn.textContent = '';
    btn.append(icon(playing ? 'pause' : 'play'));
  };
  p.onended = p.onpause = () => show('paused');
  p.onplay = () => show('playing');
  btn.addEventListener('click', async () => {
    if (!p.paused) return p.pause();
    if (!loaded) {
      show('loading');
      time.textContent = t('loading');
      try {
        const r = await fetch(
          `${SERVER.url}/preview?path=${encodeURIComponent(m.path)}&start=${Math.max(0, m.match_start).toFixed(1)}`,
          { headers: authHeaders() });
        if (!r.ok) throw new Error(r.status);
        if (p.src.startsWith('blob:')) URL.revokeObjectURL(p.src);
        p.src = URL.createObjectURL(await r.blob());
        loaded = true;
        time.textContent = '0:10';
      } catch (e) {
        show('paused');
        time.textContent = t('load_failed', e.message);
        return;
      }
    }
    p.currentTime = p.ended ? 0 : p.currentTime;
    p.play();
  });
  show('paused');
  return h('div', { class: 'player' }, btn,
    h('div', { class: 'player-info' },
      h('div', { class: 'player-row' }, h('span', {}, t('preview_label')), time)));
}

// One answer per result. It is kept in chrome.storage.session ('answered'), so reopening the
// popup shows the answer instead of the buttons. Kept from the click on, so closing the popup
// mid-request still counts; dropped again if the server did not take it. `video` (the result's
// video key) lets the server mark the video's record too.
function ask(id, path, video = '') {
  const row = h('div', { class: 'ask' });
  const thanks = (correct) => { row.textContent = correct ? t('answered_right') : t('answered_wrong'); };
  const remember = (a) => {
    answered = a;
    (a ? chrome.storage.session.set({ answered: a }) : chrome.storage.session.remove('answered')).catch(() => {});
  };
  const send = async (correct) => {
    for (const b of row.querySelectorAll('button')) b.disabled = true;
    remember({ id, correct });
    try {
      const r = await fetch(`${SERVER.url}/feedback`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ id, correct, path, video }),
      });
      if (!r.ok) throw new Error(r.status);
      thanks(correct);
    } catch {
      remember(null);
      row.textContent = t('answer_failed');
      setTimeout(() => { row.textContent = ''; row.append(...buttons()); }, 2500);
    }
  };
  const buttons = () => [h('span', {}, t('ask')),
    h('div', {}, h('button', { onclick: () => send(true) }, t('right')), h('button', { onclick: () => send(false) }, t('wrong')))];
  if (answered?.id === id) thanks(answered.correct);
  else row.append(...buttons());
  return row;
}

localize();  // the page's lang; the popup's text is all built by script
(async () => {
  try {
    // Earlier builds also kept a chosen length ('duration'); it is no longer read.
    const { consent: agreed, history } = await chrome.storage.local.get(['consent', 'history']);
    consent = agreed === POLICY;
    agreedBefore = !consent && (agreed === true || typeof agreed === 'string');
    mine = Array.isArray(history) ? history : [];
  } catch {}
  const { state, answered: a } = await chrome.storage.session.get(['state', 'answered']);
  answered = a || null;
  render(state);
  const busy = state?.status === 'recording' || state?.status === 'searching';
  if (consent && !busy) loadVideo();
  else checkServer();
})();
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes.state) render(changes.state.newValue);
  if (area === 'local' && changes.history) mine = changes.history.newValue || [];
});
