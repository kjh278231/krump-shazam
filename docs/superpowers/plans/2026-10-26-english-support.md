# English Support (1.3.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The extension shows Korean when Chrome's UI language is Korean and English otherwise, against the one deployed server.

**Architecture:** Chrome `_locales/{ko,en}/messages.json` with `default_locale: "en"`. A small `extension/i18n.js` wraps `chrome.i18n.getMessage`, fills `data-i18n` attributes in HTML and turns error objects into sentences. The server adds a machine `code` to user-facing errors and keeps `detail` unchanged for 1.0.0/1.2.0.

**Tech Stack:** Manifest V3, plain JS with no build step, Node 22 `node --test` for extension unit tests, FastAPI server, `tests/check_api.py` for checks against a running server.

**Spec:** `docs/superpowers/specs/2026-10-26-english-support-design.md` (Korean). Read it before starting.

## Global Constraints

- Language rule: `_locales/ko` and `_locales/en` only, `"default_locale": "en"`. No language switch in the UI.
- No build step, no npm packages. Chrome 116+.
- Server `detail` strings stay byte-for-byte as they are today; only a `code` field is added.
- `POLICY` in `popup.js` stays `'2026-10-22'`: nobody is asked to consent again.
- Nothing on any popup screen may move beyond what moves today (`docs/extension.md` §3).
- The service name "Krump Track Finder" is the same in both languages.
- English copy is short and direct, in the tone of the English text in `store/listing.md`.
- Repo docs stay in Korean (except `docs/privacy-policy.en.md`). Code comments stay in English with the existing density.
- Deploy only from `main` pushed to origin, via `stage.py` and `aws_setup.py` (`CLAUDE.md`).
- `manifest.json` `version` becomes `1.3.0`.

## Review Focus

1. **Longer English text in the 380px popup.** Every screen (consent, idle, idle with video records, listening, searching, found, unsure, not found, error) must fit without overflowing or clipped buttons. Task 6 checks it by hand, with screenshots.
2. **A non-JSON error body or an unknown `code`** (for example a Lambda 502 HTML page, or a code added to the server later) must show "Server error N" in the user's language, never raw server text or Korean `detail`. Pinned in Task 2 (`errorText` tests).
3. **State saved by 1.2.0** (`chrome.storage.session` `state.error` as a Korean string) might be read after the update. `errorText` must show a string as it is, not crash. Pinned in Task 2.
4. **1.0.0/1.2.0 against the new server**: same status and same `detail` text. Pinned in Task 1 (`--errors` asserts the old `detail`), plus the existing `--baseline`/`--compare` run.
5. **A Japanese or other non-Korean Chrome** must get English, and Korean Chrome Korean. Checked by hand in Task 6 by launching Chrome with `--lang=ja` and `--lang=ko`.

---

### Task 1: Server error codes

**Files:**
- Modify: `server/app.py` (`check_key` ~l.258, `identify_audio` ~l.334-355, `albums` ~l.624, new exception class + handler next to `app = FastAPI()`)
- Modify: `tests/check_api.py` (new `--errors` mode)
- Modify: `docs/search-pipeline.md` (the section on server endpoints/speed, wherever `/identify` errors are described; if none, add a short table to the server section)

**Interfaces:**
- Produces: error JSON `{"detail": <unchanged string>, "code": <code>}` with codes
  `bad_key` (401), `empty` (400), `undecodable` (400), `silent` (400), `no_albums` (404).
  Other errors (`bad video key`, `unknown query id`, preview errors) keep plain `HTTPException`. The extension never shows their text.

- [ ] **Step 1: Write the failing check**

Add `errors(srv: Server) -> int` to `tests/check_api.py` and a flag `--errors`. It needs no clips. It uses the `Checks` class (`c.ok(cond, what)`), the same way `contract` does:
  - POST `/identify` with `b""` → status 400, `code == "empty"`, `detail == "recording is empty (is the tab playing audio?)"`.
  - POST `/identify` with 5000 bytes of `b"x"` → 400, `code == "undecodable"`, `detail.startswith("ffmpeg could not decode the recording")`.
  - POST `/identify` with 5 s of silence (ffmpeg `-f lavfi -i anullsrc=r=48000:cl=mono -t 5 -c:a libopus` → webm) → 400, `code == "silent"`, `detail == "소리가 거의 녹음되지 않았습니다. 탭/영상이 음소거 상태이거나 멈춰 있지 않은지 확인하세요."`.
  - If `--key-file` is given: GET `/albums` with a wrong key → 401, `code == "bad_key"`, `detail == "API 키가 맞지 않습니다."`.
  - Update the module docstring with the new mode.

- [ ] **Step 2: Run it against the current server to see it fail**

Run: `KF_TMP=<scratch>/kf KF_VIDEO_DIR=<scratch>/videos KF_API_KEY=test python server/app.py`, then `python tests/check_api.py --errors --key-file <file containing "test">`
Expected: FAIL lines for every missing `code`.

- [ ] **Step 3: Implement**

In `server/app.py` add `class ApiError(HTTPException)` with `__init__(self, status: int, code: str, detail: str)`, and register `@app.exception_handler(ApiError)` that returns `JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code)`. Replace the five raises above with `ApiError`, keeping their `detail` text exactly. One comment line: the code is for the 1.3.0 extension's own sentences, and `detail` stays for 1.0.0/1.2.0.

- [ ] **Step 4: Run the checks**

Run: `python tests/check_api.py --errors --key-file <file>` → all OK.
Then the compatibility check from the docstring: `--baseline` on the commit before this change, `--compare` after it → no differences.

- [ ] **Step 5: Document and commit**

Write the codes table in `docs/search-pipeline.md`.
`git commit -m "Give the server's user-facing errors a code"` (+ attribution line).

---

### Task 2: i18n helper, locale files, localized manifest

**Files:**
- Create: `extension/i18n.js`, `extension/_locales/ko/messages.json`, `extension/_locales/en/messages.json`
- Modify: `extension/manifest.json`
- Create: `tests/i18n.test.mjs`
- Modify: `README.md` (test command line ~l.95 now covers both test files; the glob already does, so only the comment)

**Interfaces:**
- Produces (globals from `i18n.js`, loaded by a `<script>` before the page script):
  - `t(name: string, ...subs: (string|number)[]): string`: `chrome.i18n.getMessage(name, subs.map(String))`. It returns `name` itself when the message is missing.
  - `LANG: 'ko' | 'en'`: from the message `locale` (`"ko"` / `"en"`). It is the shown language, not `getUILanguage()`.
  - `localize(root: ParentNode = document): void`: `[data-i18n]` gets its `textContent` from the message. `data-i18n-placeholder`, `data-i18n-title` and `data-i18n-aria-label` set those attributes. It also sets `document.documentElement.lang = LANG`.
  - `errorText(err: string | { code: string, status?: number, detail?: string } | null): string`:
    - A string is returned as it is.
    - Known codes map to the message `err_<code>`. `capture` and `record` pass `detail` as `$1`.
    - An unknown code, or `code: 'server'`, gives `t('err_server', status)`.
    - Known codes: `bad_key`, `empty`, `undecodable`, `silent`, `no_albums` (server, Task 1), and `no_connection`, `record_start`, `not_recording`, `capture`, `record`, `server` (extension).
  - `albumTitle(title: string): string`: `'단일 곡'` (from `server/build_albums.py`) becomes `t('single_track')`; anything else is returned unchanged.
- Messages: snake_case keys. Substitutions are written as `$1`…`$9` directly in `message`. Every key exists in both files.
- `manifest.json`: `"default_locale": "en"`, `"name": "__MSG_ext_name__"`, `"description": "__MSG_ext_description__"`, `action.default_title: "__MSG_ext_name__"`, `"version": "1.3.0"`.

- [ ] **Step 1: Write the failing tests** in `tests/i18n.test.mjs` (style of `tests/video.test.mjs`: `vm` context)

The fake `chrome.i18n.getMessage(name, subs)` reads `extension/_locales/<lang>/messages.json` and replaces `$n` with `subs[n-1]`. It runs `i18n.js` once per language.
  - `locales: same keys` — key sets of ko and en are equal; each pair has the same set of `$n`; no empty `message`.
  - `locales: every key used exists` — collects `t('…')` and `err_…` codes from `extension/*.js`, `data-i18n*="…"` from `extension/*.html`, `__MSG_…__` from `manifest.json`; each is in en.
  - `no Korean left in code` — no Hangul inside string literals of `extension/*.js` except `'단일 곡'` in `i18n.js`. No Hangul outside comments in `extension/*.html` except `privacy.html`. (Will fail until Tasks 3–5; mark it `{ todo: true }` in this task and drop the `todo` in Task 5.)
  - `errorText` (en): `errorText({code:'silent'}) === <en err_silent>`; `errorText({code:'nope', status:502})` contains `'502'` and no Hangul; `errorText({code:'server', status:500})` contains `'500'`; `errorText({code:'capture', detail:'X'})` contains `'X'`; `errorText('옛 오류')` returns `'옛 오류'`; `errorText(null) === ''`.
  - `errorText` (ko): `errorText({code:'silent'})` is the Korean sentence.
  - `albumTitle`: en `albumTitle('단일 곡') === 'Single track'`, ko gives `'단일 곡'`; `albumTitle('Vol. 2') === 'Vol. 2'`.
  - `LANG`: `'en'` and `'ko'` respectively.

- [ ] **Step 2: Run** `node --test "tests/*.test.mjs"`. Expected: FAIL (`i18n.js` missing).

- [ ] **Step 3: Implement** `i18n.js`, both `messages.json` with the keys above (`locale`, `ext_name`, `ext_description`, `single_track`, every `err_*`), and the manifest changes. Write the Korean error texts from the current strings (`background.js` l.68/73, `offscreen.js` l.68, `popup.js` l.128, server `detail`s). The English `ext_description` follows the English summary in `store/listing.md`.

- [ ] **Step 4: Run** `node --test "tests/*.test.mjs"`. Expected: PASS (one todo).

- [ ] **Step 5: Commit** — `"Add the i18n helper and ko/en locale files"`.

---

### Task 3: Popup, background and offscreen in both languages

**Files:**
- Modify: `extension/popup.js`, `extension/popup.html`, `extension/background.js`, `extension/offscreen.js`
- Modify: `docs/extension.md` (new section "언어", file list gains `i18n.js` and `_locales/`; error flow now carries codes)

**Interfaces:**
- Consumes: `t`, `LANG`, `localize`, `errorText`, `albumTitle` (Task 2). Server `code`s (Task 1).
- Produces: `chrome.storage.session` `state.error` is now `{ code, status?, detail? }`. `background.js` `fail(error)` stores whatever it gets. The popup renders it with `errorText`.

- [ ] **Step 1: Move every user-facing string to messages**
  - `popup.html`: `<script src="i18n.js">` before `popup.js`, `localize()` at startup.
  - Every literal in `popup.js` becomes `t('…')`. That covers `lengthLabel` (two messages, seconds and minutes), `POLICY_CHANGE` (keys `policy_change_1`, `policy_change_2`), and `STORE_NAME.bandcamp` (a message: `밴드캠프` / `Bandcamp`).
  - Album titles shown by `songCard` and `videoRecords`' `albumLine` go through `albumTitle`.
  - The privacy links open `t('privacy_page')` (`privacy.html` / `privacy.en.html`, the second created in Task 5).
  - Update the `POLICY` comment: when the policy changes, both `privacy*.html`, both `docs/privacy-policy*.md` and both languages' `policy_change_*` change together.

- [ ] **Step 2: Errors as codes**
  - `offscreen.js` `identify` throws an error carrying `{code, status}`. Use the server's `code` when the body has one; on a network failure use `no_connection`; otherwise use `server` with the status.
  - The `record(...).catch` sends `error: {code, status?, detail?}`. Anything else that throws becomes `{code: 'record', detail: message}`.
  - `background.js` uses `fail({code: 'record_start'})` and `fail({code: 'not_recording'})`.
  - `popup.js`: `start()` renders `{code:'capture', detail: e.message}`, and `idle(error)` shows `errorText(error)`.

- [ ] **Step 3: Run** `node --test "tests/*.test.mjs"`. Expected: PASS. The "every key used exists" test now covers the popup.

- [ ] **Step 4: Load the unpacked extension against the local server and check by hand in Korean Chrome:**
  - every screen shows the same Korean as before;
  - a search in a muted tab shows the Korean silent message;
  - stopping the server shows the connection message.

- [ ] **Step 5: Document and commit** — `"Show the popup in Korean or English"`.

---

### Task 4: Library page in both languages

**Files:**
- Modify: `extension/library.html`, `extension/library.js`

**Interfaces:**
- Consumes: Task 2 globals. `/albums` errors are rendered with `errorText` (`no_albums`, `bad_key`, `server`, `no_connection`).

- [ ] **Step 1:** `library.html` loads `i18n.js`. `<title>` and every static text get `data-i18n*`.
- [ ] **Step 2:** `library.js` strings become `t(...)`. Counts go through messages with `$1`, and numbers are formatted with `toLocaleString(LANG)`. Album titles go through `albumTitle`. The `STORE_NAME` table is shared with the popup through messages. The fetch error builds `{code, status}` the same way `offscreen.js` does.
- [ ] **Step 3: Run** `node --test "tests/*.test.mjs"`. Expected: PASS.
- [ ] **Step 4:** Open the library page in Korean Chrome and check it reads as before. Search for a single-track album and check it shows `단일 곡`.
- [ ] **Step 5: Commit** — `"Show the library page in Korean or English"`.

---

### Task 5: English privacy policy

**Files:**
- Create: `extension/privacy.en.html` (a copy of `privacy.html` translated; `lang="en"`, same 시행일 `2026-10-22` written as "Effective October 22, 2026", same revision history)
- Create: `docs/privacy-policy.en.md`
- Modify: `docs/privacy-policy.md` (first line links to the English version), `docs/extension.md` §5 (five files change together), `tests/i18n.test.mjs` (drop `todo` from the Korean-left test)

- [ ] **Step 1:** Translate the policy faithfully, with nothing added or dropped. Compare section by section and table row by table row against the Korean.
- [ ] **Step 2: Run** `node --test "tests/*.test.mjs"`, now with the Korean-left test active. Expected: PASS. If it fails, a string was missed in Tasks 3–4: fix it there.
- [ ] **Step 3: Commit** — `"Add the English privacy policy"`.

---

### Task 6: English check in Chrome, screenshots, listing

**Files:**
- Create: `store/assets/en/screenshot-1-found.png` … `screenshot-6-records.png` (same names, same 1280×800 composition as `store/assets/`, album covers replaced by title tiles)
- Modify: `store/listing.md` (§3: English is the default listing language and Korean (`ko`) is added; English screenshots go to the English listing; English description mentions the English UI; §6 unchanged), `docs/overview.md` (architecture table: `i18n.js`, `_locales/`; public status: 1.3.0 adds English)

- [ ] **Step 1:** Launch Chrome on a fresh profile with `--lang=en-US`, load the unpacked extension, and walk through every screen listed in Review Focus 1. Fix any overflow by shortening the English text first. Change CSS only if a text cannot be shortened, and never with animation.
- [ ] **Step 2:** Repeat a quick pass with `--lang=ja` (expect English) and `--lang=ko` (expect Korean).
- [ ] **Step 3:** Take the six English screenshots the same way as the Korean ones (same screens, same layout), into `store/assets/en/`.
- [ ] **Step 4:** Update `store/listing.md` and `docs/overview.md`.
- [ ] **Step 5: Commit** — `"Add English store screenshots and listing notes for 1.3.0"`.

---

### Task 7: Release

- [ ] **Step 1:** Merge into `main`, `git push origin main`, `python server/deploy/stage.py`, `python server/deploy/aws_setup.py image function` (code only; no `data`).
- [ ] **Step 2:** Run a deployed smoke test: `python tests/check_api.py --server <URL> --key-file <file> --no-store`, and `--errors` against the deployed server. Expected: all OK, with the old `detail` texts.
- [ ] **Step 3:** `python store/package.py` → `build/store/krump-track-finder-1.3.0.zip`. Check that `_locales/`, `i18n.js` and `privacy.en.html` are in the file list.
- [ ] **Step 4:** Uploading to the Web Store is the user's step (`store/listing.md` §6). Report the zip path.
