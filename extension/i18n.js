// Text in the user's language: Korean when Chrome's UI language is Korean, English otherwise
// (_locales/ko and _locales/en, default_locale en; docs/extension.md "언어"). Loaded before each
// page's script. The offscreen document has no chrome.i18n, so errors travel as { code } objects
// and are worded here.

// t('err_server', 502): the message with $1, $2 filled in; the name itself when it is missing.
const t = (name, ...subs) => chrome.i18n.getMessage(name, subs.map(String)) || name;
const LANG = t('locale');  // 'ko' or 'en': the language shown, not Chrome's own (ja shows en)

// plural(1, 'track_count'): message <name>_one for exactly one, else <name>; $1 is n written for LANG.
const plural = (n, name) => t(n === 1 ? `${name}_one` : name, n.toLocaleString(LANG));

// <span data-i18n="ext_name">; data-i18n-placeholder, -title and -aria-label set those attributes.
function localize(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
  for (const attr of ['placeholder', 'title', 'aria-label']) {
    for (const el of root.querySelectorAll(`[data-i18n-${attr}]`)) el.setAttribute(attr, t(el.getAttribute(`data-i18n-${attr}`)));
  }
  document.documentElement.lang = LANG;
}

// Codes from the server (ApiError in server/app.py) and from the extension itself. Each is the
// message err_<code>; capture and record carry the browser's own message as $1.
const ERROR_CODES = ['bad_key', 'empty', 'undecodable', 'silent', 'no_albums',
  'no_connection', 'record_start', 'not_recording', 'capture', 'record', 'server'];

// An error as the user reads it. A string is shown as it is (state saved by 1.2.0); an unknown
// code is a plain "server error N", never the server's own text.
function errorText(err) {
  if (!err) return '';
  if (typeof err === 'string') return err;
  if (err.code !== 'server' && ERROR_CODES.includes(err.code)) return t(`err_${err.code}`, err.detail ?? '');
  return t('err_server', err.status ?? '');
}

// The server names a track without an album '단일 곡' (server/build_albums.py); the data stays as it is.
const albumTitle = (title) => (title === '단일 곡' ? t('single_track') : title);
