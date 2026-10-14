const LOCAL_SERVER = 'http://127.0.0.1:8765';
const $ = (id) => document.getElementById(id);
// { url, apiKey } from the settings section (chrome.storage.local); local server by default.
let server = { url: LOCAL_SERVER, apiKey: '' };
const authHeaders = () => (server.apiKey ? { 'X-Api-Key': server.apiKey } : {});
const VERDICT_TEXT = {
  confirmed: '확정',
  check: '추가 확인 필요',
  fail: '찾지 못함',
};
// Real failures so far were almost all in-library songs smeared by hall reverb, crowd and MC.
const FAIL_HELP = '현장 녹음이라 잔향·함성·MC 소리에 음악이 묻혔을 가능성이 큽니다. '
  + '음악이 크게 들리는 구간에서 다시 시도하거나 녹음 길이를 30~45초로 늘려보세요. '
  + '(라이브러리에 없는 곡일 수도 있습니다)';
const REASON_TEXT = {
  low: '점수가 낮습니다. 조금 더 길게 다시 들어보세요.',
  rival: '다른 곡도 비슷한 점수로 잡혔습니다 (루프·샘플 공유 가능). 들어보고 골라주세요.',
  rhythm_strong: '유력 후보 — fingerprint 검색으로는 못 찾아서 리듬 패턴으로 찾았습니다 (잔향 큰 현장 녹음). '
    + '▶ 10초 미리듣기로 확인해 주세요.',
  rhythm_weak: '약한 후보 — 리듬 패턴이 조금 더 비슷한 정도라 틀릴 수 있습니다. '
    + '꼭 들어보고 확인하거나, 음악이 잘 들리는 구간에서 다시 시도해 주세요.',
};

let tick = null;

function fmt(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

async function checkServer() {
  // Also wakes a cold Lambda while the user is still picking a duration.
  $('server').className = 'dot';
  try {
    const r = await fetch(`${server.url}/health`);
    $('server').className = r.ok ? 'dot on' : 'dot off';
    $('server').title = `${server.url} 연결됨`;
  } catch {
    $('server').className = 'dot off';
    $('server').title = `서버 연결 안 됨: ${server.url}`;
  }
}

async function loadSettings() {
  const { settings } = await chrome.storage.local.get('settings');
  server = { url: LOCAL_SERVER, apiKey: '', ...settings };
  $('server-url').value = server.url;
  $('api-key').value = server.apiKey;
}

async function saveSettings() {
  const url = $('server-url').value.trim().replace(/\/+$/, '') || LOCAL_SERVER;
  server = { url, apiKey: $('api-key').value.trim() };
  await chrome.storage.local.set({ settings: server });
  $('server-url').value = url;
  $('settings').open = false;
  checkServer();
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
    duration: Number($('duration').value), tabTitle: tab.title, tabUrl: tab.url, server,
  });
}

function showError(text) {
  $('status').className = 'error';
  $('status').textContent = text;
}

function render(state = {}) {
  clearInterval(tick);
  const busy = state.status === 'recording' || state.status === 'searching';
  $('start').hidden = busy;
  $('stop').hidden = state.status !== 'recording';
  $('duration').disabled = busy;
  $('status').className = '';
  $('status').textContent = '';
  $('result').textContent = '';

  if (state.status === 'recording') {
    const update = () => {
      const left = state.duration - (Date.now() - state.startedAt) / 1000;
      $('status').textContent = `듣는 중… 최대 ${fmt(left)} 남음 (확정되면 바로 멈춥니다 · 팝업을 닫아도 계속됩니다)`;
    };
    update();
    tick = setInterval(update, 500);
    const p = state.partial;
    if (p) {
      const hint = document.createElement('div');
      hint.className = 'reason';
      const by = p.method === 'rhythm' ? '리듬 패턴상' : '';
      hint.textContent = p.songs?.length
        ? `${p.seconds}초까지 들은 결과 ${by} 가장 유력: ${p.songs[0].file} — 더 듣는 중`
        : `${p.seconds}초까지는 아직 후보 없음 — 더 듣는 중`;
      $('result').append(hint);
    }
  } else if (state.status === 'searching') {
    $('status').textContent = '마지막 정밀 검색 중… (리듬 검색까지 하면 10초 정도 걸립니다)';
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
  const reason = r.reason === 'rhythm' ? `rhythm_${r.songs[0]?.strength || 'weak'}` : r.reason;
  const help = r.verdict === 'fail' ? FAIL_HELP : REASON_TEXT[reason];
  if (help) {
    const why = document.createElement('div');
    why.className = 'reason';
    why.textContent = help;
    box.append(why);
  }
  $('status').textContent = `${state.tabTitle || ''} · 검색 ${r.elapsed}s`;

  // Only the best song is shown; the rest of the candidate list stays server-side.
  r.songs.slice(0, 1).forEach((m) => {
    const el = document.createElement('div');
    el.className = 'match';
    const speed = m.time_factor && m.time_factor !== 1 ? ` · 속도 ${(m.time_factor * 100).toFixed(1)}%` : '';
    el.innerHTML = `
      <div class="name"></div>
      <div class="meta"></div>
      <div class="actions">
        <button data-act="play">▶ 10초 미리듣기</button>
        <button data-act="yes">✔ 맞음</button>
        <button data-act="no">✘ 틀림</button>
      </div>`;
    el.querySelector('.name').textContent = m.file;
    const scoreText = r.method === 'rhythm' ? `리듬 유사도 ${m.score}` : `점수 ${m.score}`;
    el.querySelector('.meta').textContent =
      `${m.folder} · ${scoreText} · 곡의 ${fmt(m.match_start)}–${fmt(m.match_stop)}${speed}`;
    const notes = [];
    if (m.ratio) notes.push(`다른 곡보다 ${m.ratio}배 높음`);
    if (m.preview_only) notes.push('미리보기 파일에서만 발견됨 — 같은 앨범의 곡입니다');
    if (m.also_in.length) notes.push(`같은 음원: ${m.also_in.map((x) => x.file).join(', ')}`);
    if (notes.length) {
      const n = document.createElement('div');
      n.className = 'meta';
      n.textContent = notes.join(' · ');
      el.querySelector('.meta').after(n);
    }
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
      `${server.url}/preview?path=${encodeURIComponent(m.path)}&start=${Math.max(0, m.match_start).toFixed(1)}`,
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
    const r = await fetch(`${server.url}/feedback`, {
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
$('save-settings').onclick = saveSettings;
$('library').onclick = () => chrome.tabs.create({ url: chrome.runtime.getURL('library.html') });
chrome.storage.session.get('state').then(({ state }) => render(state));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes.state) render(changes.state.newValue);
});
loadSettings().then(checkServer);
