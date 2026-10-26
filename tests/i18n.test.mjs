// extension/i18n.js and the _locales message files. Node 22, no packages:
//   node --test "tests/*.test.mjs"
// chrome.i18n.getMessage is stood in for by the message files themselves, as Chrome reads them:
// $1..$9 in a message are the substitutions.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';

const EXT = new URL('../extension/', import.meta.url);
const read = (name) => readFileSync(new URL(name, EXT), 'utf8');
const messages = (lang) => JSON.parse(read(`_locales/${lang}/messages.json`));
const HANGUL = /[가-힣]/;

function load(lang) {
  const msgs = messages(lang);
  const getMessage = (name, subs = []) =>
    msgs[name]?.message.replace(/\$(\d)/g, (_, n) => subs[n - 1] ?? '') ?? '';
  const ctx = { chrome: { i18n: { getMessage } } };
  vm.createContext(ctx);
  vm.runInContext(read('i18n.js'), ctx);
  // const/let at the top of a script are not properties of the context: read them through it.
  return vm.runInContext('({ t, LANG, errorText, albumTitle })', ctx);
}

const sources = () => readdirSync(EXT).filter((f) => /\.(js|html)$/.test(f) && f !== 'config.js');
const dollars = (s) => [...new Set(s.match(/\$\d/g) || [])].sort().join();

test('locales: same keys and substitutions', () => {
  const ko = messages('ko');
  const en = messages('en');
  assert.deepEqual(Object.keys(ko).sort(), Object.keys(en).sort());
  for (const k of Object.keys(en)) {
    assert.ok(ko[k].message && en[k].message, `empty message ${k}`);
    assert.equal(dollars(ko[k].message), dollars(en[k].message), `substitutions of ${k}`);
  }
  assert.equal(en.locale.message, 'en');
  assert.equal(ko.locale.message, 'ko');
});

test('locales: every key used exists', () => {
  const en = messages('en');
  const used = new Set();
  for (const f of sources()) {
    const src = read(f);
    for (const m of src.matchAll(/\bt\('(\w+)'/g)) used.add(m[1]);
    for (const m of src.matchAll(/data-i18n(?:-[\w-]+)?="(\w+)"/g)) used.add(m[1]);
    for (const m of src.matchAll(/\bcode: '(\w+)'/g)) used.add(`err_${m[1]}`);
  }
  for (const m of read('manifest.json').matchAll(/__MSG_(\w+)__/g)) used.add(m[1]);
  const { ERROR_CODES } = vm.runInContext('({ ERROR_CODES })', (() => {
    const ctx = { chrome: { i18n: { getMessage: () => '' } } };
    vm.createContext(ctx);
    vm.runInContext(read('i18n.js'), ctx);
    return ctx;
  })());
  for (const code of ERROR_CODES) used.add(`err_${code}`);
  const missing = [...used].filter((k) => !(k in en));
  assert.deepEqual(missing, []);
});

test('no Korean left in code', { todo: true }, () => {
  const left = [];
  for (const f of sources()) {
    if (f === 'privacy.html') continue;
    const src = read(f);
    if (f.endsWith('.js')) {
      // String literals only; comments may stay Korean. A comment starts at a // after a space
      // (a URL's // follows a colon).
      const code = src.replace(/(^|\s)\/\/.*$/gm, '$1');
      for (const m of code.matchAll(/'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`/g)) {
        if (HANGUL.test(m[0]) && !(f === 'i18n.js' && m[0] === "'단일 곡'")) left.push(`${f}: ${m[0]}`);
      }
    } else {
      for (const line of src.replace(/<!--[\s\S]*?-->/g, '').split('\n')) if (HANGUL.test(line)) left.push(`${f}: ${line.trim()}`);
    }
  }
  assert.deepEqual(left, []);
});

test('errorText: English', () => {
  const { errorText, t } = load('en');
  assert.equal(errorText({ code: 'silent' }), t('err_silent'));
  assert.ok(!HANGUL.test(errorText({ code: 'silent' })));
  const unknown = errorText({ code: 'nope', status: 502 });
  assert.match(unknown, /502/);
  assert.ok(!HANGUL.test(unknown));
  assert.match(errorText({ code: 'server', status: 500 }), /500/);
  assert.match(errorText({ code: 'capture', detail: 'X' }), /X/);
  assert.match(errorText({ code: 'record', detail: 'Y' }), /Y/);
  assert.equal(errorText('옛 오류'), '옛 오류');  // state saved by 1.2.0
  assert.equal(errorText(null), '');
});

test('errorText: Korean', () => {
  const { errorText } = load('ko');
  assert.ok(HANGUL.test(errorText({ code: 'silent' })));
  assert.match(errorText({ code: 'nope', status: 502 }), /502/);
});

test('albumTitle', () => {
  assert.equal(load('en').albumTitle('단일 곡'), 'Single track');
  assert.equal(load('ko').albumTitle('단일 곡'), '단일 곡');
  assert.equal(load('en').albumTitle('Vol. 2'), 'Vol. 2');
});

test('LANG and t', () => {
  const en = load('en');
  assert.equal(en.LANG, 'en');
  assert.equal(load('ko').LANG, 'ko');
  assert.equal(en.t('no_such_message'), 'no_such_message');
});
