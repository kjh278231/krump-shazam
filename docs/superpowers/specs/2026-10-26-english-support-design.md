# 영어 지원 설계 (확장 프로그램 1.3.0)

작성 2026-10-26.

## 1. 의도

Krump Track Finder를 한국어 사용자만이 아니라 해외 크럼프 사용자도 쓸 수 있게 한다. 크럼프 영상과 라이브러리는
원래 해외 것이 대부분이고(곡·앨범 이름은 영어), 지금 막는 것은 한국어로만 된 화면이다.

## 2. 목표 / 비목표 / 반목표

목표
- 확장 프로그램의 모든 화면(팝업, 라이브러리 페이지, 개인정보처리방침 페이지, 오류 문장, 확장 프로그램 이름·설명)이
  **Chrome 브라우저 UI 언어가 한국어면 한국어, 그 밖의 모든 언어는 영어**로 보인다.
- 서버는 지금의 Lambda 함수 `krump-finder` 하나를 두 언어가 같이 쓴다.
- 개인정보처리방침 영어판을 둔다. 내용은 한국어판과 같은 번역이다.

비목표
- 사용자가 언어를 직접 고르는 버튼이나 설정. 두지 않는다.
- 한국어·영어 밖의 언어.
- 프로젝트 문서(`docs/`, `README.md`)의 영어판. 문서는 한국어 그대로다(방침 영어판만 예외).

반목표 (기술적으로 돌아가도 실패인 것)
- 언어별로 서버나 배포를 나누는 것.
- 1.0.0·1.2.0 사용자의 화면이 서버 변경으로 깨지는 것(오류 문장이 사라지거나 바뀌는 것).
- 영어판 방침이 생겼다는 이유로 기존 사용자에게 동의를 다시 받는 것.
- 영어 문장이 길어져 380px 팝업에서 버튼·제목이 넘치거나 잘리는 것.
- 화면에 움직임을 더하는 것(`docs/extension.md` §3: 캡처 중인 탭이 끊김).

## 3. 제약

- 확장 프로그램은 빌드 단계 없는 순수 JS·CSS, Manifest V3, Chrome 116 이상이다. 빌드 도구를 들이지 않는다.
- 배포는 origin에 push된 main에서만 한다(`CLAUDE.md`, `server/deploy/release.py`).
- 지금 배포된 1.0.0·1.2.0은 서버가 보내는 오류의 `detail` 문장을 그대로 화면에 띄운다
  (`offscreen.js` 74행, `library.js` 208행). 서버는 이 문장을 계속 보내야 한다.
- 동의는 방침 시행일(`popup.js` `POLICY`) 하나에 묶여 있다. 두 언어 방침은 같은 시행일을 쓴다.

## 4. 고려한 방식

| 방식 | 내용 | 득실 |
|---|---|---|
| **A. Chrome 기본 다국어(`_locales`)** — 채택 | `_locales/ko/messages.json`, `_locales/en/messages.json`, `default_locale: "en"`. HTML은 메시지 이름을 속성으로 달고, JS는 `chrome.i18n.getMessage` | 언어 판단과 "한국어 아니면 영어"를 Chrome이 해 준다. 이름·설명(`__MSG_…__`)과 같은 체계 하나. 숫자가 끼는 문장은 `$1` 자리표시로 써야 해서 조금 번거롭다 |
| B. 직접 만든 문장표(`strings.js`) | `{ko, en}` 표에서 `chrome.i18n.getUILanguage()`로 고름 | 문장 조립이 자유롭고 언어 버튼을 넣기 쉽다. 그러나 이름·설명은 어차피 `_locales`에 둬야 해 문장이 두 곳에 나뉜다. 언어 버튼을 두지 않으니 장점이 남지 않는다 |
| C. 화면별 HTML을 언어마다 | `popup.en.html` 등 | 팝업은 JS가 화면을 그려 HTML을 나눠도 해결되지 않는다. 뺐다 |

사용자가 A를 골랐다(2026-10-26, 제안대로 진행).

## 5. 정해진 것

### 5.1 언어 판단
- Chrome의 `_locales` 선택에 맡긴다. `ko` 폴더와 `en` 폴더만 두고 `default_locale`은 `en`이다.
- 기준은 Chrome UI 언어다. Chrome UI는 영어인데 웹사이트 선호 언어만 한국어인 사람은 영어로 본다. 이대로 둔다.
- HTML의 `lang` 속성도 보이는 언어에 맞춘다(`ko` / `en`).

### 5.2 확장 프로그램
- 사용자에게 보이는 모든 문장을 `messages.json`으로 옮긴다: `popup.js`, `popup.html`, `library.js`, `library.html`,
  `background.js`, `offscreen.js`, `manifest.json`의 `name`·`description`·`default_title`.
- 서비스 이름 "Krump Track Finder"는 두 언어에서 같다.
- `STORE_NAME`의 `밴드캠프`는 영어에서 `Bandcamp`.
- 숫자 표기(`toLocaleString`)와 날짜·시간 표기도 보이는 언어를 따른다.
- offscreen 문서에서는 `chrome.i18n`을 쓸 수 없다(offscreen은 `chrome.runtime`만 있음). `offscreen.js`·`background.js`는
  문장 대신 오류 종류(와 HTTP 상태)를 넘기고, 팝업이 문장으로 바꾼다. (설계자 판단)
- 서버 데이터 안의 한국어: 앨범이 없는 곡은 `server/build_albums.py`가 앨범 이름을 `"단일 곡"`으로 채운다. 데이터는 그대로
  두고, 확장 프로그램이 이 값을 보면 영어 화면에서 "Single" 같은 문장으로 바꿔 보인다. `data` 재배포가 필요 없다. (설계자 판단)
- 영어 문장은 짧게 쓰고, 380px 팝업의 모든 화면(동의, 대기, 영상 기록, 찾는 중, 찾음, 확실하지 않음, 못 찾음, 오류)에서
  넘치지 않는지 손으로 확인한다.

### 5.3 개인정보처리방침
- 영어판은 한국어판의 그대로인 번역이다. 시행일과 개정 이력은 같다. `POLICY`는 바꾸지 않는다.
- 확장 프로그램: `privacy.html`(한국어), `privacy.en.html`(영어). 팝업의 방침 링크는 보이는 언어 쪽을 연다.
- `POLICY_CHANGE`(방침이 바뀌었을 때 동의 화면의 한두 줄)도 두 언어로 둔다.
- GitHub: `docs/privacy-policy.md`(한국어, 웹스토어 방침 주소) 맨 위에 영어판 링크, 새 `docs/privacy-policy.en.md`.
  웹스토어 방침 주소는 하나만 받으므로 한국어판 주소를 그대로 둔다.
- 앞으로 방침을 고칠 때는 다섯 파일(두 `privacy*.html`, 두 `privacy-policy*.md`, `POLICY_CHANGE` 두 언어)을 같이 고친다.
  `docs/extension.md` §5와 `popup.js`의 `POLICY` 주석에 적는다.

### 5.4 서버
- 서버는 언어를 모른다. 요청에 언어를 보내지 않는다.
- 사용자에게 보일 수 있는 오류 응답에 기계용 `code`를 더한다. `detail`은 지금 문장 그대로 둔다(이전 버전 호환).
  대상: `/identify`의 400(빈 녹음, ffmpeg 해독 실패, 거의 무음), 401(API 키), `/albums`의 404.
- 1.3.0 확장 프로그램은 `code`가 있으면 자기 언어의 문장을 쓰고, 모르는 `code`이거나 없으면 일반 오류 문장
  ("서버 오류 N" / "Server error N")을 쓴다. 서버의 한국어 `detail`을 영어 화면에 띄우지 않는다.
- 서버를 먼저 배포한 뒤 1.3.0을 낸다. 서버 변경은 이전 버전에 영향이 없다.

### 5.5 웹스토어
- `store/listing.md`의 기존 English 설명을 영어 등록정보로 쓴다. 1.3.0에서 바뀐 점(영어 화면)만 반영한다.
- `default_locale: "en"`이라 웹스토어 등록정보의 기본 언어가 영어가 된다. 지금 문서는 "한국어 기본, 영어 추가"이므로
  `store/listing.md` §3을 "영어 기본, 한국어(`ko`) 추가"로 고친다. 한국어 설명은 그대로 `ko` 등록정보로 쓴다. (설계자 판단:
  "한국어가 아니면 영어"와 맞고, 바뀌는 건 대시보드에서 손으로 하는 단계뿐이다)
- 영어 스크린샷을 만든다(사용자 요청, 2026-10-26). 한국어 6장과 같은 화면·같은 구성(1280×800, 앨범 커버 대신 제목 타일)을
  영어 화면으로 찍어 `store/assets/en/`에 같은 파일 이름으로 둔다. 영어 등록정보에는 영어 스크린샷, 한국어 등록정보에는
  지금 스크린샷을 올린다. `store/listing.md` §3에 적는다. 찍는 방법은 빌더에게 맡긴다.
- 1.3.0 업데이트 절차는 `store/listing.md` §6 그대로다.

### 5.6 문서
- `docs/extension.md`: 언어 처리 절 추가, 파일 구성에 `_locales/`와 `privacy.en.html`.
- `docs/overview.md`: 아키텍처 표와 공개 상태(1.3.0, 영어).
- `docs/video-records.md`나 `docs/search-pipeline.md`는 바뀌지 않는다.

## 6. 빌더에게 맡기는 것

- 메시지 이름 짓는 규칙, 파일 안 순서.
- HTML에 메시지를 거는 방식(예: `data-i18n` 속성 + 작은 도우미 함수).
- `code` 값의 이름(예: `empty`, `undecodable`, `silent`, `bad_key`, `no_albums`).
- 두 `messages.json`의 키가 같은지 확인하는 시험을 `tests/`에 둘지. 두는 쪽을 권한다.
- 영어 문장의 구체적인 표현. 기존 `store/listing.md` English의 말투(짧고 직접적)를 따른다.
