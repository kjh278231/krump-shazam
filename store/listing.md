# Chrome 웹스토어 등록 자료

개발자 대시보드(https://chrome.google.com/webstore/devconsole)에 그대로 옮겨 적을 내용입니다.
처음 등록은 일부 공개(Unlisted)로 합니다.

## 0. 올리기 전에

1. 서버를 먼저 배포합니다(`docs/lambda-deployment.md`). 익스텐션은 `share=1`을 보내고, 서버는 그때만 녹음을 저장합니다.
   1.2.0부터는 서버에 `GET /video`와 영상별 기록(`docs/video-records.md`)이 있어야 합니다.
2. `python store/package.py` → `build/store/krump-track-finder-<version>.zip`
   - `extension/config.js`(배포된 Lambda 주소와 API 키)가 들어갑니다.
   - 로컬 서버 권한(`http://127.0.0.1:8765/*`)은 뺍니다.
3. 개인정보처리방침 URL: `docs/privacy-policy.md`를 GitHub에 push하면
   https://github.com/kjh278231/krump-shazam/blob/main/docs/privacy-policy.md 에서 열립니다.
   익스텐션 안에는 같은 내용이 `privacy.html`로 들어 있습니다.

## 1. 개발자 등록 (처음 한 번, 직접 해야 함)

- Google 계정으로 개발자 등록. 등록비 US$5(한 번만)를 냅니다.
- 2단계 인증을 켜야 하고, 연락처 이메일 인증이 필요합니다.
- 판매자(trader) 여부: 무료·개인 프로젝트라면 "비판매자(non-trader)"를 고릅니다.

## 2. 패키지

- 새 항목 → `build/store/krump-track-finder-1.0.0.zip` 업로드

## 3. 스토어 등록정보 (Store listing)

- 언어: 한국어 (영어 설명은 아래 English를 추가 언어로)
- 카테고리: 엔터테인먼트 (Entertainment)
- 아이콘: zip 안의 `icons/icon128.png`가 자동으로 쓰입니다.
- 스크린샷(1280×800, `store/assets/`):
  1. `screenshot-1-found.png`
  2. `screenshot-2-unsure.png`
  3. `screenshot-3-listen.png`
  4. `screenshot-4-library.png`
  5. `screenshot-5-retry.png` (1.2.0부터)
  6. `screenshot-6-records.png` (1.2.0부터, 영상에서 다른 사용자가 찾은 곡)
- 작은 프로모션 타일(440×280): `store/assets/promo-small-440x280.png`
- 스크린샷의 앨범 커버는 일부러 제목 타일로 바꿨습니다. 다른 아티스트의 앨범 아트를 홍보 이미지에 쓰지 않기 위해서입니다.

### 요약 (132자 이내)

> 탭에서 나오는 크럼프 음악을 듣고 라이브러리에서 같은 곡을 찾아, 앨범 구매 페이지까지 연결해 줍니다.

### 설명

```
배틀·세션 영상을 보다가 "이 곡 뭐지?" 싶을 때, 버튼 한 번이면 됩니다.
Krump Track Finder는 지금 보고 있는 탭의 소리를 듣고 크럼프 음악 라이브러리(앨범 319개, 4,286곡)에서
같은 곡을 찾아 줍니다.

■ 이렇게 써요
1. 영상이 재생 중인 탭에서 익스텐션 아이콘을 누릅니다.
2. "지금 나오는 트랙 찾기"를 누릅니다. 최대 30초 동안 들어요.
3. 곡이 확실해지면 바로 멈추고 결과를 보여 줍니다. 팝업을 닫아도 계속 들어요.
4. 못 찾았다면 45초나 1분으로 늘려 다시 찾을 수 있어요.

■ 이런 걸 해요
- 앨범 커버, 곡·앨범·아티스트 이름, 곡의 몇 분 몇 초인지 보여 줍니다.
- 밴드캠프 등 아티스트의 판매 페이지로 바로 연결합니다(앨범 301개).
- 잔향과 함성이 큰 현장 영상도 리듬 패턴으로 찾습니다. 확실하지 않으면 그렇다고 알려 주고,
  10초 미리듣기로 같은 곡인지 들어 볼 수 있습니다.
- 같은 영상에서 다른 사용자가 이미 찾은 곡이 있으면, 팝업을 열자마자 그 곡을 보여 줍니다.
- 라이브러리 페이지에서 모든 앨범을 보고 검색할 수 있습니다.

■ 알아 두세요
- 라이브러리에 있는 곡만 찾을 수 있습니다.
- 처음 쓸 때와 방침이 바뀔 때 개인정보처리방침에 동의해야 합니다. 찾기를 누르면 탭의 소리(최대 1분)와 탭 제목·주소를
  서버로 보내고 저장합니다. 찾은 곡은 같은 영상을 보는 다른 사용자에게 보입니다(녹음·탭 제목·누가 찾았는지는 보이지
  않음). 이름·계정 같은 정보는 받지 않습니다.
```

### English

Summary:

> Listens to the current tab and finds the krump track in the library, with a link to buy the album.

```
Watching a krump battle or session and wondering what track is playing? One click.
Krump Track Finder listens to the current tab and finds the same track in a krump music library
(319 albums, 4,286 tracks).

- Shows the album cover, track, album, artist and where in the track the clip is.
- Links straight to the artist's store page (Bandcamp and others) for 301 albums.
- Finds tracks even in reverberant live recordings by their rhythm; when unsure it says so and
  lets you check with a 10-second preview.
- When others have already found tracks in the same video, the popup shows them as soon as you open it.
- Browse and search the whole library.

Only tracks in the library can be found. Using the extension requires agreeing to the privacy
policy (again when it changes): when you search, the tab's audio (up to 1 min) and its title and URL are
sent to the server and stored. The tracks found are shown to other users watching the same video (never the
recording, the tab title or who searched). No name, account or other personal details are collected.
```

## 4. 개인정보 보호 관행 (Privacy practices)

### 단일 목적 (Single purpose)

> 사용자가 요청할 때 현재 탭에서 재생 중인 음악을 듣고, 크럼프 음악 라이브러리에서 같은 곡을 찾아 보여 줍니다. 같은 영상에서
> 이미 찾은 곡이 있으면 그 결과를 바로 보여 줍니다.
> (Identifies the krump track playing in the current tab, on the user's request, and shows tracks already found
> in the same video.)

### 권한별 사유

| 권한 | 사유 |
|---|---|
| tabCapture | 사용자가 "지금 나오는 트랙 찾기"를 누른 탭의 오디오를 녹음해 곡을 찾습니다. 녹음 중에도 소리는 사용자에게 그대로 들립니다. |
| offscreen | Manifest V3 서비스 워커는 미디어를 녹음할 수 없어서, 오프스크린 문서에서 MediaRecorder로 탭 오디오를 녹음합니다(reason: USER_MEDIA). |
| activeTab | 사용자가 아이콘을 누른 현재 탭만 대상으로 합니다. 그 탭의 제목을 결과 화면에 표시하고, 주소에서 영상 id를 얻어 그 영상에서 찾은 곡을 조회합니다. 페이지 내용은 읽지 않습니다. |
| storage | 동의 여부(동의한 개인정보처리방침의 시행일), 진행 중인 찾기 상태, 라이브러리 목록 캐시, 내가 찾은 결과의 기록(최근 100건)을 브라우저에 저장합니다. |
| 호스트 권한 `https://*.lambda-url.ap-northeast-2.on.aws/*` | 녹음을 검색 서버(AWS Lambda)로 보내고 결과, 10초 미리듣기, 라이브러리 목록을 받습니다. |

### 원격 코드 (Remote code)

> 아니요, 원격 코드를 사용하지 않습니다. (Google Fonts의 글꼴 CSS와 앨범 커버 이미지만 불러오며, 실행되는 코드는 모두 패키지 안에 있습니다.)

### 데이터 사용 (Data usage)

수집 항목으로 체크할 것:
- **웹사이트 콘텐츠(Website content)**: 탭 오디오 녹음, 탭 제목
- **웹 기록(Web history)**: 찾기를 누른 탭의 주소(URL), 팝업을 연 영상 탭의 영상 id

체크하지 않을 것: 개인 식별 정보, 건강, 금융·결제, 인증 정보, 개인 커뮤니케이션, 위치, 사용자 활동.
(맞음/틀림 응답은 검색 결과에 대한 평가로, 위 웹사이트 콘텐츠와 함께 저장됩니다.)

메모(1.2.0부터): 찾은 곡은 같은 영상을 보는 이 익스텐션의 다른 사용자에게 보입니다. 사용자 대상 기능이며,
팔거나 외부에 넘기지 않습니다.

인증 세 가지 모두 체크:
- 승인된 사용 사례 외의 목적으로 제3자에게 사용자 데이터를 판매하거나 전송하지 않음
- 단일 목적과 관련 없는 목적으로 사용자 데이터를 사용하거나 전송하지 않음
- 신용도 판단이나 대출 목적으로 사용자 데이터를 사용하거나 전송하지 않음

개인정보처리방침 URL: 위 0-3의 URL

## 5. 배포 (Distribution)

- 공개 범위: **일부 공개(Unlisted)**. 링크를 아는 사람만 설치할 수 있습니다.
- 지역: 모든 지역
- 가격: 무료

## 6. 업데이트할 때

1. `extension/manifest.json`의 `version`을 올립니다(이전보다 커야 업로드됩니다).
2. `python store/package.py` → 대시보드의 패키지 탭에서 새 zip을 올리고 검토를 제출합니다.
   화면이 바뀌었으면 스토어 등록정보의 스크린샷과 설명도 맞춥니다.
3. 권한이나 수집 데이터가 바뀌면 4번과 개인정보처리방침도 고칩니다.

## 비용과 보안 메모

- API 키가 익스텐션 안에 들어 있어서, 누구나 꺼내 서버를 직접 호출할 수 있습니다.
  비용은 Lambda 동시 실행 3개 상한으로 묶여 있습니다.
- 월 $10 예산 경보(`krump-finder-monthly`)가 이 프로젝트 비용만 봅니다.
  - 실제 비용 80% 또는 월말 예상 100%를 넘으면 메일이 옵니다(`aws_setup.py budget`).
