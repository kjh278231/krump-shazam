// Records the captured tab audio and sends it to the local server.
//
// Like Shazam, it doesn't wait for the full duration: from FIRST_CHECK seconds on, the audio
// recorded so far is sent for a quick fingerprint-only search every CHECK_EVERY seconds. A
// confirmed match stops the recording right away; otherwise the best candidate so far is shown
// and, at the end, the full recording gets the complete search (fingerprint + rhythm fallback).

const SERVER = 'http://127.0.0.1:8765';
const MIN_SECONDS = 5;
// Panako needs ~10 s: on clean clips 5-6 s confirmed 0/10, 8 s 2/10, 10 s 10/10.
const FIRST_CHECK = 10;
const CHECK_EVERY = 4;

let recorder = null;
let stopTimer = null;

function send(type, extra = {}) {
  // The background never replies; ignore the resulting "port closed" rejection.
  chrome.runtime.sendMessage({ ...extra, target: 'background', type }).catch(() => {});
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.target !== 'offscreen') return;
  if (msg.type === 'record') {
    record(msg).catch((e) => send('error', { error: String(e.message || e) }));
    sendResponse({ ok: true });
  }
  if (msg.type === 'stop') {
    const active = recorder?.state === 'recording';
    if (active) {
      const elapsed = (Date.now() - recorder.startedAt) / 1000;
      clearTimeout(stopTimer);
      // Too-short clips can't be identified; finish the minimum length first.
      stopTimer = setTimeout(() => recorder.stop(), Math.max(0, MIN_SECONDS - elapsed) * 1000);
    }
    sendResponse({ recording: active });
  }
});

async function identify(blob, params) {
  const qs = new URLSearchParams(params);
  let res;
  try {
    res = await fetch(`${SERVER}/identify?${qs}`, { method: 'POST', body: blob });
  } catch {
    throw new Error('서버에 연결할 수 없습니다. python server/app.py 를 실행했는지 확인하세요.');
  }
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch {}
    throw new Error(res.status === 400 ? detail : `서버 오류 ${res.status}: ${detail}`);
  }
  return res.json();
}

async function record({ streamId, duration, tabTitle, tabUrl }) {
  if (recorder?.state === 'recording') throw new Error('Already recording');
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
  });
  // Capturing a tab mutes it for the user; route the audio back to the speakers.
  const ctx = new AudioContext();
  ctx.createMediaStreamSource(stream).connect(ctx.destination);

  const chunks = [];
  const page = { title: tabTitle || '', url: tabUrl || '' };
  recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 160000 });
  recorder.startedAt = Date.now();
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const stopped = new Promise((resolve) => (recorder.onstop = resolve));
  recorder.start(1000);  // 1 s chunks; chunks[0] carries the WebM header, so any prefix decodes
  stopTimer = setTimeout(() => recorder.stop(), duration * 1000);

  let early = null;
  let inflight = false;
  let nextCheck = FIRST_CHECK;
  const checker = setInterval(async () => {
    const t = (Date.now() - recorder.startedAt) / 1000;
    if (inflight || early || recorder.state !== 'recording' || t < nextCheck || t > duration - 2) return;
    inflight = true;
    try {
      const r = await identify(new Blob(chunks, { type: 'audio/webm' }), { ...page, partial: 1 });
      if (recorder.state !== 'recording') return;  // the final search has taken over
      if (r.verdict === 'confirmed') {
        early = r;
        clearTimeout(stopTimer);
        recorder.stop();
      } else {
        send('partial', { result: r, seconds: Math.round(t) });
      }
    } catch {
      // Partial checks are best-effort (e.g. silence at the start); the final search reports errors.
    } finally {
      inflight = false;
      nextCheck = (Date.now() - recorder.startedAt) / 1000 + CHECK_EVERY;
    }
  }, 500);

  await stopped;
  clearInterval(checker);
  stream.getTracks().forEach((t) => t.stop());
  await ctx.close();

  if (early) {
    send('result', { result: early });
    return;
  }
  send('searching');
  send('result', { result: await identify(new Blob(chunks, { type: 'audio/webm' }), page) });
}
