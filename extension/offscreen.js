// Records the captured tab audio and sends it to the local server.
//
// Like Shazam, it doesn't wait for the full duration: from FIRST_CHECK seconds on, the audio
// recorded so far is searched every CHECK_EVERY seconds (fingerprint and rhythm). When the server
// marks a check final (fingerprint confirmed, or a rhythm candidate clear enough for the length
// recorded) the recording stops right away; otherwise, at the end, the full recording gets the
// complete search.
//
// In a video tab shared requests also carry the video's key (vkey, docs/video-records.md), so the
// server can record the songs found in that video.

// Server address and API key: SERVER from config.js (fixed at build time, aws_setup.py).
const MIN_SECONDS = 5;
// Panako needs ~10 s: on clean clips 5-6 s confirmed 0/10, 8 s 2/10, 10 s 10/10. The extra
// 0.5 s makes sure the 10th 1 s chunk has arrived.
const FIRST_CHECK = 10.5;
const CHECK_EVERY = 4;
// Audio lengths at which the server lowers its bar for stopping early (server/app.py RHYTHM_EARLY).
const RHYTHM_STEPS = [15, 20];

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
    res = await fetch(`${SERVER.url}/identify?${qs}`, {
      method: 'POST', body: blob, headers: { 'X-Api-Key': SERVER.apiKey },
    });
  } catch {
    throw new Error('서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.');
  }
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch {}
    throw new Error(res.status === 400 ? detail : `서버 오류 ${res.status}: ${detail}`);
  }
  return res.json();
}

async function record({ streamId, duration, tabTitle, tabUrl, share, video }) {
  if (recorder?.state === 'recording') throw new Error('Already recording');
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
  });
  // Capturing a tab mutes it for the user; route the audio back to the speakers.
  const ctx = new AudioContext();
  ctx.createMediaStreamSource(stream).connect(ctx.destination);

  // Wake the server as soon as recording starts (a cold Lambda needs several seconds; the first
  // check is at 10 s). Checks wait for this reply: a request sent while the only instance is still
  // starting would start a second cold instance instead of queueing.
  let warm = false;
  fetch(`${SERVER.url}/health`).catch(() => {}).finally(() => { warm = true; });

  const chunks = [];
  // The tab's title, URL and video go with the audio only when the user agreed to share recordings
  // (the server then keeps them; otherwise it deletes the audio after the search).
  const page = share ? { title: tabTitle || '', url: tabUrl || '', share: 1, ...(video?.key ? { vkey: video.key } : {}) } : {};
  recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 160000 });
  recorder.startedAt = Date.now();
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const stopped = new Promise((resolve) => (recorder.onstop = resolve));
  recorder.start(1000);  // 1 s chunks; chunks[0] carries the WebM header, so any prefix decodes
  stopTimer = setTimeout(() => recorder.stop(), duration * 1000);

  let early = null;
  let inflight = null;  // promise of the check in progress
  let nextCheck = FIRST_CHECK;
  const check = async (t) => {
    try {
      const r = await identify(new Blob(chunks, { type: 'audio/webm' }), { ...page, partial: 1 });
      // final: a fingerprint confirmation, or a clear rhythm candidate (server decides).
      // Kept even if the recording stopped meanwhile: it then replaces the final search.
      if (r.final) {
        early = r;
        clearTimeout(stopTimer);
        if (recorder.state === 'recording') recorder.stop();
      } else if (recorder.state === 'recording') {
        send('partial', { result: r, seconds: Math.round(t) });
      }
    } catch {
      // Partial checks are best-effort (e.g. silence at the start); the final search reports errors.
    } finally {
      inflight = null;
      nextCheck = (Date.now() - recorder.startedAt) / 1000 + CHECK_EVERY;
      // A check just short of 15 or 20 s is held to the stricter bar of the shorter length;
      // waiting a moment for the step gives it the lower one.
      for (const step of RHYTHM_STEPS) {
        if (nextCheck > step - 2 && nextCheck < step + 0.5) nextCheck = step + 0.5;
      }
    }
  };
  const checker = setInterval(() => {
    const t = (Date.now() - recorder.startedAt) / 1000;
    if (!warm || inflight || early || recorder.state !== 'recording' || t < nextCheck || t > duration - 2) return;
    inflight = check(t);
  }, 500);

  await stopped;
  clearInterval(checker);
  stream.getTracks().forEach((t) => t.stop());
  await ctx.close();
  // A check still running when the recording ends is waited for: sending the final search
  // alongside it would start a second (cold) Lambda instance, which is slower than waiting.
  if (inflight && !early) {
    send('searching');
    await inflight;
  }

  if (early) {
    send('result', { result: early });
    return;
  }
  send('searching');
  send('result', { result: await identify(new Blob(chunks, { type: 'audio/webm' }), page) });
}
