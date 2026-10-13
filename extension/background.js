// Coordinates the popup and the offscreen recorder. Offscreen documents only get
// chrome.runtime, so the state they report is stored here in chrome.storage.session,
// which the popup reads (the popup closes whenever it loses focus).

const OFFSCREEN_URL = 'offscreen.html';

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

async function fail(error) {
  await setState({ status: 'error', error });
  badge('ERR', '#d93025');
}

function badge(text, color) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== 'background') return;
  (async () => {
    switch (msg.type) {
      case 'start': {
        await ensureOffscreen();
        await setState({
          status: 'recording', startedAt: Date.now(), duration: msg.duration,
          tabTitle: msg.tabTitle, tabUrl: msg.tabUrl, result: null, error: null, partial: null,
        });
        badge('REC', '#d93025');
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
        badge('...', '#5f6368');
        break;
      case 'result': {
        await setState({ status: 'done', result: msg.result });
        const v = msg.result.verdict;
        badge(v === 'confirmed' ? 'OK' : v === 'check' ? '?' : 'X',
          v === 'confirmed' ? '#188038' : v === 'check' ? '#e37400' : '#5f6368');
        break;
      }
      case 'error':
        await fail(msg.error);
        break;
    }
  })();
});
