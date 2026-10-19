// SERVER ({ url, apiKey } of the deployed Lambda) comes from config.js; there is no settings UI.
// One screen per state: 대기 → 찾는 중 → 찾음 / 찾음(확실하지 않음) / 못 찾음. The state lives in
// chrome.storage.session (background.js), because the popup closes whenever it loses focus.
// Searching needs the user's consent to the privacy policy (privacy.html): recordings are kept
// on the server to improve accuracy. Without it only the consent screen is shown.
// Consent is to one version of the policy, its 시행일. When privacy.html changes, set POLICY to the
// new 시행일 and POLICY_CHANGE to what changed (one or two lines): earlier consents (1.0.0 stored
// `true`) are asked again. 1.1.0 was never published, so the lines are what changed since 1.0.0.
// In a video tab the 대기 screen also shows the songs other users found in this video (GET /video,
// docs/video-records.md) and this browser's own searches there.
const POLICY = '2026-10-20';
const POLICY_CHANGE = ['녹음이 최대 1분으로 늘었어요.', '찾은 곡을 같은 영상을 보는 다른 사용자에게 보여 줘요.'];
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
const STORE_NAME = { bandcamp: '밴드캠프', knkrtworld: 'KNKRTWORLD', gumroad: 'Gumroad' };
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
let tick = null;
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
const lengthLabel = (s) => (s % 60 ? `${s}초` : `${s / 60}분`);  // 45 -> 45초, 60 -> 1분

function setDot(state) {
  serverDot = state;
  const dot = document.querySelector('.dot');
  if (dot) {
    dot.className = `dot ${serverDot}`;
    dot.parentElement.title = serverDot === 'on' ? '서버 연결됨' : serverDot === 'off' ? '서버에 연결할 수 없음' : '';
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
    render({ status: 'error', error: `탭 오디오를 캡처할 수 없습니다: ${e.message}` });
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
    box.append(h('button', { class: 'chip-btn', onclick: again }, icon('again'), '다시 찾기'));
  } else {
    box.append(h('button', {
      class: 'chip-btn', title: '라이브러리에 있는 앨범 보기',
      onclick: () => openPage('library.html'),
    }, h('span', { class: `dot ${serverDot}`, 'aria-hidden': 'true' }), '라이브러리'));
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
    h('div', {}, h('h1', { class: 'headline' }, '시작하기 전에'),
      h('div', { class: 'sub' }, '곡을 찾으려면 아래 내용에 동의해 주세요.')),
    agreedBefore && h('div', { class: 'note' }, '개인정보처리방침이 바뀌었어요.', POLICY_CHANGE.flatMap((l) => [h('br'), l])),
    h('ul', { class: 'tips' },
      h('li', {}, '찾기를 누르면 이 탭의 소리(최대 1분)와 탭 제목·주소를 서버로 보내요.'),
      h('li', {}, '영상 탭에서 팝업을 열면 영상 id를 보내 다른 사용자가 찾은 곡을 조회해요. 이 조회는 저장하지 않아요.'),
      h('li', {}, '찾은 곡은 같은 영상을 보는 다른 사용자에게 보여요. 녹음·탭 제목·누가 찾았는지는 보이지 않아요.'),
      h('li', {}, '보낸 녹음은 서버에 저장해 정확도를 높이는 데 써요. 팔지 않아요. 이름·이메일·계정은 받지 않아요.')),
    h('button', { class: 'linkish', onclick: () => openPage('privacy.html') }, '개인정보처리방침 전체 보기'),
    h('button', { class: 'primary', onclick: () => setConsent(true) }, icon('check'), '동의하고 시작'),
    h('div', { class: 'hint' }, '동의하지 않으면 찾기를 쓸 수 없어요.'),
  ];
}

function idle(error) {
  return [
    h('div', {}, h('h1', { class: 'headline' }, '지금 나오는 트랙'),
      h('div', { class: 'sub' }, '이 탭의 소리를 듣고 크럼프 라이브러리에서 찾아요.')),
    error && h('div', { class: 'error', role: 'alert' }, error),
    h('button', { class: 'primary', onclick: () => start() }, icon('listen'), '현재 탭에서 찾기'),
    h('div', { class: 'hint' }, `최대 ${lengthLabel(FIRST_LENGTH)} 듣고, 곡이 확실해지면 바로 멈춰요`, h('br'),
      '팝업을 닫아도 계속 들어요'),
    videoRecords(),
    h('div', { class: 'foot' },
      h('button', { class: 'linkish', onclick: () => openPage('privacy.html') }, '개인정보처리방침'),
      h('span', { 'aria-hidden': 'true' }, '·'),
      h('button', { class: 'linkish', onclick: () => setConsent(false) }, '동의 철회')),
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
  const albumLine = (m) => [m.album?.title, m.album?.artist].filter(Boolean).join(' · ') || m.folder || null;
  const rows = songs.length === 1 ? [] : songs.map((s) => row(trackName(s.song), albumLine(s.song)));
  for (const e of own) {
    rows.push(e.song ? row(trackName(e.song), e.verdict === 'check' ? '내 기록 · 확실하지 않음' : '내 기록') : row('못 찾음', '내 기록'));
  }
  const one = songs.length === 1 ? songs[0] : null;
  return h('section', { class: 'vlist', 'aria-label': '이 영상에서 찾은 곡' },
    h('div', { class: 'label' }, '이 영상에서 찾은 곡'),
    one && h('div', { class: 'note info' }, one.status === 'verified'
      ? '다른 사용자가 이 영상에서 찾고 맞다고 확인한 곡이에요.' : '다른 사용자가 이 영상에서 찾은 곡이에요.'),
    one && h('div', { class: 'card' }, ...songCard(one.song)),
    rows.length > 0 && h('ul', {}, rows));
}

function listening(state) {
  const searching = state.status === 'searching';
  const time = h('span', { class: 'num' });
  const fill = h('div');
  const update = () => {
    const total = state.duration || 30;
    const t = searching ? total : Math.min(total, (Date.now() - (state.startedAt || Date.now())) / 1000);
    time.textContent = searching ? '검색 중' : `${fmt(t)} / ${fmt(total)}`;
    fill.style.width = `${(t / total) * 100}%`;
  };
  update();
  if (!searching) tick = setInterval(update, 500);
  const eq = h('div', { class: 'eq', 'aria-hidden': 'true' }, ...Array.from({ length: 7 }, () => h('span')));
  return [
    h('div', { class: 'listen-head' },
      h('div', {}, h('h1', { class: 'headline' }, '찾는 중…'),
        h('div', { class: 'sub' }, searching ? '들은 소리 전체로 마지막 검색을 하고 있어요.' : '곡이 확실해지면 바로 멈춰요.')),
      eq),
    h('div', { class: 'panel' },
      h('div', { class: 'panel-row' }, h('span', {}, '듣는 탭'), time),
      h('div', { class: 'tab-title', title: state.tabTitle || '' }, state.tabTitle || '현재 탭'),
      h('div', { class: 'bar', 'aria-hidden': 'true' }, fill)),
    searching
      ? h('button', { class: 'secondary', disabled: true }, '검색 중…')
      : h('button', { class: 'secondary', onclick: () => chrome.runtime.sendMessage({ target: 'background', type: 'stop' }) },
        icon('stop'), '지금 멈추고 검색'),
  ];
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
      verdictRow('none', '못 찾음', state),
      h('h1', { class: 'headline sm' }, '이 구간에선', h('br'), '못 찾았어요'),
      h('ul', { class: 'tips' },
        h('li', {}, '음악이 크게 들리는 구간에서 다시 시도해 보세요.'),
        h('li', {}, 'MC·함성이 크면 45초나 1분으로 더 길게 들어 보세요.'),
        h('li', {}, '라이브러리에 없는 곡일 수도 있어요.')),
      h('div', { role: 'group', 'aria-label': '다시 찾을 때 최대 녹음 길이' },
        h('div', { class: 'label' }, '최대 녹음 길이'), lengths),
      h('button', { class: 'primary', onclick: () => start(retry) }, icon('again'), '다시 찾기'),
    ];
  }
  // check = a rhythm or low-score fingerprint candidate: right most of the time, but not always.
  const unsure = r.verdict === 'check';
  const m = r.songs[0];  // only the best song is shown
  const out = [
    verdictRow(unsure ? 'unsure' : 'found', unsure ? '찾음 · 확실하지 않음' : '찾음', state),
    unsure && h('div', { class: 'note' }, '확실하지 않아요. 미리듣기로 같은 곡인지 확인해 주세요.'),
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
  const albumTitle = album?.title || m.folder || '';

  const cover = album?.cover
    ? h('img', { class: 'cover', src: album.cover, alt: `앨범 커버: ${albumTitle}` })
    : h('div', { class: 'cover', role: 'img', 'aria-label': '앨범 커버 없음' }, albumTitle || '♪');

  const out = [
    h('div', { class: 'song' }, cover,
      h('div', { class: 'song-text' },
        h('div', {}, h('div', { class: 'track' }, name),
          albumTitle && h('div', { class: 'album' }, albumTitle),
          album?.artist && h('div', { class: 'artist' }, album.artist)),
        h('span', { class: 'pos' }, `곡의 ${fmt(m.match_start)}부터`))),
    preview(m),
  ];
  if (store) {
    const shop = STORE_NAME[store.store] || store.store;
    const isAlbum = store.kind === 'album';
    out.push(h('a', {
      class: isAlbum ? 'primary' : 'outline', href: store.url, target: '_blank', rel: 'noopener',
      onclick: (e) => { e.preventDefault(); chrome.tabs.create({ url: store.url }); },
    }, isAlbum ? `${shop}에서 앨범 구매` : `${shop}에서 아티스트 보기`, icon('out')));
    if (!isAlbum) out.push(h('div', { class: 'hint' }, '이 앨범의 판매 페이지는 없어서 아티스트 페이지로 연결해요.'));
  }
  return out;
}

// The server cuts a 10 s excerpt from the matched spot; full files are never sent. Fetched rather
// than set as <audio src> because the request needs the API key header. Confirmed and unsure
// finds get the same player.
function preview(m) {
  const p = $('player');
  const btn = h('button', { class: 'play' });
  const time = h('span', {}, '0:10');
  const fill = h('div');
  let loaded = false;
  const show = (state) => {
    btn.disabled = state === 'loading';
    const playing = state === 'playing';
    btn.setAttribute('aria-label', playing ? '미리듣기 멈춤' : '10초 미리듣기 재생');
    btn.textContent = '';
    btn.append(icon(playing ? 'pause' : 'play'));
  };
  p.ontimeupdate = () => {
    time.textContent = `${fmt(p.currentTime)} / ${fmt(p.duration || 10)}`;
    fill.style.width = `${(p.currentTime / (p.duration || 10)) * 100}%`;
  };
  p.onended = p.onpause = () => show('paused');
  p.onplay = () => show('playing');
  btn.addEventListener('click', async () => {
    if (!p.paused) return p.pause();
    if (!loaded) {
      show('loading');
      time.textContent = '불러오는 중…';
      try {
        const r = await fetch(
          `${SERVER.url}/preview?path=${encodeURIComponent(m.path)}&start=${Math.max(0, m.match_start).toFixed(1)}`,
          { headers: authHeaders() });
        if (!r.ok) throw new Error(r.status);
        if (p.src.startsWith('blob:')) URL.revokeObjectURL(p.src);
        p.src = URL.createObjectURL(await r.blob());
        loaded = true;
      } catch (e) {
        show('paused');
        time.textContent = `불러오지 못함 (${e.message})`;
        return;
      }
    }
    p.currentTime = p.ended ? 0 : p.currentTime;
    p.play();
  });
  show('paused');
  return h('div', { class: 'player' }, btn,
    h('div', { class: 'player-info' },
      h('div', { class: 'player-row' }, h('span', {}, '10초 미리듣기'), time),
      h('div', { class: 'bar light', 'aria-hidden': 'true' }, fill)));
}

// One answer per result. It is kept in chrome.storage.session ('answered'), so reopening the
// popup shows the answer instead of the buttons. Kept from the click on, so closing the popup
// mid-request still counts; dropped again if the server did not take it. `video` (the result's
// video key) lets the server mark the video's record too.
function ask(id, path, video = '') {
  const row = h('div', { class: 'ask' });
  const thanks = (correct) => { row.textContent = `${correct ? '맞음' : '틀림'}으로 기록했어요. 고마워요!`; };
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
      row.textContent = '기록하지 못했어요. 잠시 후 다시 눌러 주세요.';
      setTimeout(() => { row.textContent = ''; row.append(...buttons()); }, 2500);
    }
  };
  const buttons = () => [h('span', {}, '결과가 맞나요?'),
    h('div', {}, h('button', { onclick: () => send(true) }, '맞음'), h('button', { onclick: () => send(false) }, '틀림'))];
  if (answered?.id === id) thanks(answered.correct);
  else row.append(...buttons());
  return row;
}

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
