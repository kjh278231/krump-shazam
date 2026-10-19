// extension/video.js: video keys. Node 22, no packages:
//   node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const ctx = { URL };
vm.createContext(ctx);
vm.runInContext(readFileSync(new URL('../extension/video.js', import.meta.url), 'utf8'), ctx);
const { videoKey } = ctx;
const key = (url) => videoKey(url)?.key ?? null;

test('videoKey: YouTube forms', () => {
  const id = 'dQw4w9WgXcQ';
  for (const url of [
    `https://www.youtube.com/watch?v=${id}`,
    `https://youtube.com/watch?v=${id}&t=132s&list=PL123&index=4&pp=abc`,
    `https://m.youtube.com/watch?feature=share&v=${id}`,
    `https://music.youtube.com/watch?v=${id}&si=xyz`,
    `https://www.youtube.com/shorts/${id}`,
    `https://www.youtube.com/live/${id}?si=abc`,
    `https://www.youtube.com/embed/${id}?start=30`,
    `https://www.youtube-nocookie.com/embed/${id}`,
    `https://youtu.be/${id}?t=42`,
    `https://WWW.YouTube.COM/watch?v=${id}`,
  ]) assert.equal(key(url), `yt:${id}`, url);
  assert.equal(videoKey(`https://youtu.be/${id}`).platform, 'yt');
});

test('videoKey: Instagram reels and posts', () => {
  for (const url of [
    'https://www.instagram.com/reel/C1a2B3c4D5e/',
    'https://www.instagram.com/reels/C1a2B3c4D5e/?igsh=abc',
    'https://instagram.com/p/C1a2B3c4D5e/',
    'https://www.instagram.com/some.dancer/reel/C1a2B3c4D5e/',
    'https://www.instagram.com/some.dancer/p/C1a2B3c4D5e/?img_index=2',
  ]) assert.equal(key(url), 'ig:C1a2B3c4D5e', url);
  assert.equal(videoKey('https://www.instagram.com/reel/C1a2B3c4D5e/').platform, 'ig');
});

test('videoKey: no video', () => {
  for (const url of [
    'https://www.youtube.com/', 'https://www.youtube.com/feed/subscriptions', 'https://www.youtube.com/@channel',
    'https://www.youtube.com/results?search_query=krump', 'https://www.youtube.com/watch?v=short',
    'https://www.youtube.com/watch?list=PL123', 'https://www.youtube.com/shorts/', 'https://youtu.be/',
    'https://www.youtube.com/playlist?list=PL123', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ%2F..',
    'https://www.instagram.com/', 'https://www.instagram.com/explore/', 'https://www.instagram.com/reels/',
    'https://www.instagram.com/reels/audio/1234567890/', 'https://www.instagram.com/some.dancer/',
    'https://www.instagram.com/stories/some.dancer/1234567890/', 'https://www.instagram.com/a/b/reel/C1a2B3c4D5e/',
    'https://evil.example/watch?v=dQw4w9WgXcQ', 'https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ',
    'chrome://extensions/', 'file:///C:/video.mp4', 'not a url', '', undefined,
  ]) assert.equal(key(url), null, String(url));
});
