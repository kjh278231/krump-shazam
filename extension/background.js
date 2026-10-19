// Coordinates the popup and the offscreen recorder. Offscreen documents only get
// chrome.runtime, so the state they report is stored here in chrome.storage.session,
// which the popup reads (the popup closes whenever it loses focus). Finished searches in a video
// also go into this browser's own record ('history' in chrome.storage.local).

const OFFSCREEN_URL = 'offscreen.html';
const HISTORY_MAX = 100;

async function ensureOffscreen() {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (contexts.length) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ['USER_MEDIA'],
    justification: 'Record tab audio for track identification',
  });
}

async function setState(patch) {
  const { state = {} } = await chrome.storage.session.get('state');
  await chrome.storage.session.set({ state: { ...state, ...patch } });
}

// One line per finished search in a video (docs/video-records.md): the newest HISTORY_MAX, no covers.
async function remember(r) {
  const top = r.verdict !== 'fail' ? r.songs?.[0] : null;
  const { history = [] } = await chrome.storage.local.get('history');
  history.push({
    id: r.id, key: r.video.key, verdict: r.verdict,
    song: top && { path: top.path, file: top.file, folder: top.folder, album: top.album?.title || '', match_start: top.match_start },
    date: new Date().toISOString().slice(0, 10),
  });
  await chrome.storage.local.set({ history: history.slice(-HISTORY_MAX) });
}

async function fail(error) {
  await setState({ status: 'error', error });
  badge('ERR', '#ff5a1f');
}

function badge(text, color) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
  // Dark text on the orange and amber badges, light on the grey ones.
  if (color) chrome.action.setBadgeTextColor?.({ color: ['#ff5a1f', '#f2b33d'].includes(color) ? '#111010' : '#f2eee8' });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== 'background') return;
  (async () => {
    switch (msg.type) {
      case 'start': {
        await ensureOffscreen();
        await setState({
          status: 'recording', startedAt: Date.now(), duration: msg.duration,
          tabTitle: msg.tabTitle, tabUrl: msg.tabUrl, video: msg.video || null, result: null, error: null, partial: null,
        });
        badge('REC', '#ff5a1f');
        // msg's own target/type must not override these, so spread it first.
        const ack = await chrome.runtime.sendMessage({ ...msg, target: 'offscreen', type: 'record' }).catch(() => null);
        if (!ack?.ok) await fail('녹음을 시작하지 못했습니다. 다시 시도해 주세요.');
        break;
      }
      case 'stop': {
        const ack = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'stop' }).catch(() => null);
        if (!ack?.recording) await fail('진행 중인 녹음이 없습니다. 다시 시작해 주세요.');
        break;
      }
      case 'partial':
        await setState({ partial: { ...msg.result, seconds: msg.seconds } });
        break;
      case 'searching':
        await setState({ status: 'searching' });
        badge('...', '#3a3532');
        break;
      case 'result': {
        await setState({ status: 'done', result: msg.result });
        const v = msg.result.verdict;
        // Found / not found only; an unconfirmed find keeps the amber colour.
        badge(v === 'fail' ? 'X' : 'OK', v === 'confirmed' ? '#ff5a1f' : v === 'check' ? '#f2b33d' : '#3a3532');
        if (msg.result.video) await remember(msg.result).catch(() => {});
        break;
      }
      case 'error':
        await fail(msg.error);
        break;
    }
  })();
});
