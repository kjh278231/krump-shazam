// SERVER ({ url, apiKey } of the deployed Lambda) comes from config.js; there is no settings UI.
const $ = (id) => document.getElementById(id);
const authHeaders = () => ({ 'X-Api-Key': SERVER.apiKey });
// Only the outcome is shown (찾는 중 → 찾음 / 못 찾음), not how the search got there.
const VERDICT_TEXT = {
  confirmed: '찾음',
  check: '찾음',
  fail: '못 찾음',
};
// check = a rhythm or low-score fingerprint candidate: right most of the time, but not always.
const CHECK_NOTE = '확실하지 않아요. 미리듣기로 확인해 주세요.';
const FAIL_NOTE = '음악이 잘 들리는 구간에서 다시 시도해 보세요.';
const STORE_NAME = { bandcamp: '밴드캠프', knkrtworld: 'KNKRTWORLD', gumroad: 'Gumroad' };

// The album's purchase page, or the artist's page where the album itself was not found.
function storeLink(store) {
  const a = document.createElement('a');
  a.className = 'buy';
  a.href = store.url;
  a.target = '_blank';
  a.rel = 'noopener';
  const name = STORE_NAME[store.store] || store.store;
  a.textContent = store.kind === 'album' ? `${name}에서 앨범 구매 ↗` : `${name}에서 아티스트 보기 ↗`;
  a.onclick = (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: store.url });
  };
  return a;
}

function coverOf(album) {
  if (album?.cover) {
    const img = document.createElement('img');
    img.src = album.cover;
    img.alt = '';
    img.className = 'cover';
    return img;
  }
  const el = document.createElement('div');
  el.className = 'cover';
  el.textContent = '♪';
  return el;
}

function fmt(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

async function checkServer() {
  // Also wakes a cold Lambda while the user is still picking a duration.
  $('server').className = 'dot';
  try {
    const r = await fetch(`${SERVER.url}/health`);
    $('server').className = r.ok ? 'dot on' : 'dot off';
    $('server').title = r.ok ? '서버 연결됨' : '서버 응답 오류';
  } catch {
    $('server').className = 'dot off';
    $('server').title = '서버 연결 안 됨';
  }
}

async function start() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  let streamId;
  try {
    streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  } catch (e) {
    showError(`탭 오디오를 캡처할 수 없습니다: ${e.message}`);
    return;
  }
  chrome.runtime.sendMessage({
    target: 'background', type: 'start', streamId,
    duration: Number($('duration').value), tabTitle: tab.title, tabUrl: tab.url,
  });
}

function showError(text) {
  $('status').className = 'error';
  $('status').textContent = text;
}

function render(state = {}) {
  const busy = state.status === 'recording' || state.status === 'searching';
  $('start').hidden = busy;
  $('stop').hidden = state.status !== 'recording';
  $('duration').disabled = busy;
  $('status').className = '';
  $('status').textContent = '';
  $('result').textContent = '';

  if (busy) {
    $('status').textContent = '찾는 중…';
  } else if (state.status === 'error') {
    showError(state.error);
  } else if (state.status === 'done' && state.result) {
    renderResult(state.result, state);
  }
}

function renderResult(r, state) {
  const box = $('result');
  const v = document.createElement('div');
  v.className = `verdict ${r.verdict}`;
  v.textContent = VERDICT_TEXT[r.verdict];
  box.append(v);
  const help = r.verdict === 'fail' ? FAIL_NOTE : r.verdict === 'check' ? CHECK_NOTE : '';
  if (help) {
    const why = document.createElement('div');
    why.className = r.verdict === 'fail' ? 'reason muted' : 'reason';
    why.textContent = help;
    box.append(why);
  }
  $('status').textContent = state.tabTitle || '';

  // Only the best song is shown; the rest of the candidate list stays server-side.
  r.songs.slice(0, 1).forEach((m) => {
    const el = document.createElement('div');
    el.className = 'match';
    el.innerHTML = `
      <div class="top">
        <div class="text"><div class="name"></div><div class="album"></div><div class="meta"></div></div>
      </div>
      <div class="actions">
        <button data-act="play">▶ 10초 미리듣기</button>
        <button data-act="yes">✔ 맞음</button>
        <button data-act="no">✘ 틀림</button>
      </div>`;
    const album = m.album;
    el.querySelector('.top').prepend(coverOf(album));
    // "Artist - Album - 02 Song" -> "02 Song": the album line below names the album.
    let name = m.file.replace(/\.[^.]+$/, '');
    const cut = album ? name.lastIndexOf(`${album.title} - `) : -1;
    if (cut >= 0 && cut + album.title.length + 3 < name.length) name = name.slice(cut + album.title.length + 3);
    el.querySelector('.name').textContent = name;
    el.querySelector('.album').textContent = album
      ? [album.title, album.artist].filter(Boolean).join(' · ') : m.folder;
    el.querySelector('.meta').textContent = `곡의 ${fmt(m.match_start)}부터`;
    if (album?.store?.url?.startsWith('https://')) el.querySelector('.top').after(storeLink(album.store));
    el.querySelector('[data-act=play]').onclick = (e) => play(m, e.currentTarget);
    el.querySelector('[data-act=yes]').onclick = () => feedback(r.id, true, m.path, el);
    el.querySelector('[data-act=no]').onclick = () => feedback(r.id, false, m.path, el);
    box.append(el);
  });
}

async function play(m, button) {
  // The server cuts a 10 s excerpt from the matched spot; full files are never sent.
  // Fetched rather than set as <audio src> because the request needs the API key header.
  const label = button.textContent;
  button.disabled = true;
  button.textContent = '불러오는 중…';
  try {
    const r = await fetch(
      `${SERVER.url}/preview?path=${encodeURIComponent(m.path)}&start=${Math.max(0, m.match_start).toFixed(1)}`,
      { headers: authHeaders() });
    if (!r.ok) throw new Error(r.status);
    const p = $('player');
    if (p.src.startsWith('blob:')) URL.revokeObjectURL(p.src);
    p.src = URL.createObjectURL(await r.blob());
    p.hidden = false;
    p.play();
  } catch (e) {
    showError(`미리듣기를 불러오지 못했습니다 (${e.message})`);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function feedback(id, correct, path, el) {
  try {
    const r = await fetch(`${SERVER.url}/feedback`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ id, correct, path }),
    });
    if (!r.ok) throw new Error(r.status);
    el.querySelector('.actions').outerHTML = `<div class="thanks">${correct ? '맞음' : '틀림'}으로 기록했습니다.</div>`;
  } catch {
    showError('피드백 저장 실패 (서버 확인)');
  }
}

$('start').onclick = start;
$('stop').onclick = () => chrome.runtime.sendMessage({ target: 'background', type: 'stop' });
$('library').onclick = () => chrome.tabs.create({ url: chrome.runtime.getURL('library.html') });
chrome.storage.session.get('state').then(({ state }) => render(state));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes.state) render(changes.state.newValue);
});
// Old builds kept { url, apiKey } in storage; drop it.
chrome.storage.local.remove('settings');
checkServer();
