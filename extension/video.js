// Video keys for the per-video records (docs/video-records.md). A classic script that calls no
// chrome API: popup.html loads it with <script>, tests/video.test.mjs in a vm context.

const YT_HOSTS = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
  'youtube-nocookie.com', 'www.youtube-nocookie.com'];

// The video a URL shows: { key: 'yt:<id>' | 'ig:<code>', platform }, or null (feeds, channels,
// search, other sites). Only this normalises URLs; the server just checks the key's form.
// Instagram: reels and posts only (/reel/, /reels/, /p/, also under /<user>/), not the feed.
function videoKey(url) {
  let u;
  try { u = new URL(url); } catch { return null; }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  const host = u.hostname.toLowerCase();
  const parts = u.pathname.split('/').filter(Boolean);
  let id = null;
  if (host === 'youtu.be') id = parts[0];
  else if (YT_HOSTS.includes(host)) {
    if (parts[0] === 'watch') id = u.searchParams.get('v');
    else if (['shorts', 'live', 'embed'].includes(parts[0])) id = parts[1];
    else return null;
  }
  if (id !== null) return /^[A-Za-z0-9_-]{11}$/.test(id || '') ? { key: `yt:${id}`, platform: 'yt' } : null;
  if (host === 'instagram.com' || host === 'www.instagram.com') {
    const i = parts.findIndex((p) => p === 'reel' || p === 'reels' || p === 'p');
    const code = parts[i + 1];
    if ((i === 0 || i === 1) && code && code !== 'audio' && /^[A-Za-z0-9_-]{5,64}$/.test(code)) {
      return { key: `ig:${code}`, platform: 'ig' };
    }
  }
  return null;
}
