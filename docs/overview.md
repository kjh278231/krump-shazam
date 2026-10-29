# 프로젝트 개요 — Krump Track Finder

작성 2026-10-19. 이 문서는 전체 그림만 다룬다. 세부는 구성 요소별 문서에 있다(§7 문서 지도).

- 검색 단계의 원리·기준·보정: [검색 파이프라인](search-pipeline.md)
- 확장 프로그램의 동작: [확장 프로그램](extension.md)
- 측정 세트와 결과: [평가](evaluation.md)
- 배포 구성: [Lambda 배포](lambda-deployment.md)
- 영상별 기록(영상에서 찾은 곡의 공유): [영상별 기록](video-records.md)
- 설치·빌드·실행: [README](../README.md)

## 1. 무엇을 하나

브라우저 탭에서 나오는 크럼프 음악이 개인 라이브러리의 어느 트랙인지 찾는다. 대상은 유튜브·인스타그램의
배틀, 세션, 쇼케이스 영상이다. Chrome 확장 프로그램이 탭 소리를 녹음해 서버로 보내고, 서버가 라이브러리에서
**같은 녹음**을 찾는다. 비슷하게 들리는 곡을 추천하는 기능은 아니다.

- 라이브러리: `music/`(`N:\개인\Krump Track & Albums` 사본). 고유 트랙 4,132개, 약 244시간, 앨범 319개.
- 사용자 흐름: **지금 나오는 트랙 찾기** → 최대 30초 녹음(확실해지면 일찍 멈춤) → 곡 이름, 앨범 커버,
  곡 안의 위치, 10초 미리듣기, 판매 페이지 링크 → **맞음 / 틀림** 피드백. 못 찾으면 그 한 번만 45초나 1분으로
  늘려 다시 찾을 수 있다.
- 영상 탭(1.2.0부터): 같은 영상에서 다른 사용자가 이미 찾은 곡이 있으면, 팝업을 열자마자 그 곡들을 보여 준다
  ([영상별 기록](video-records.md)). 영상의 몇 초에 나오는 곡인지는 기록하지 않는다.
- 결과는 세 가지다. **찾음**(확정), **찾음 · 확실하지 않음**(미리듣기로 확인해 달라는 후보), **못 찾음**.
- 공개 상태: Chrome Web Store 1.0.0 Unlisted(2026-10-18), 1.2.0 제출(1.1.0은 내지 않고 합침,
  [확장 프로그램](extension.md) §6). 1.3.0부터 화면이 영어로도 나온다(한국어 Chrome이면 한국어, 그 밖은 영어,
  [확장 프로그램](extension.md) §7). 서버는 AWS Lambda(서울) 하나를 두 언어가 같이 쓴다.
- 쓰려면 개인정보처리방침에 동의해야 한다. 동의하면 녹음과 탭 제목·주소가 서버에 남고, 그 녹음과 피드백이 정확도 평가
  데이터가 된다. 찾은 곡은 같은 영상을 여는 다른 사용자에게 보인다([개인정보처리방침](privacy-policy.md)).

### 왜 어려운가

- 배틀 영상은 홀 잔향, 관중 함성, 박수, MC 목소리가 음악 위에 겹친다.
- 실패 원인은 곡이 라이브러리에 없어서가 아니다. 사용자가 확인한 실패 사례(WWB 9 → DARK,
  Rise Against 3-way → KILL EM, PS Buck Vol.4 → Afro Krump)는 모두 라이브러리에 있었고 속도도 1.000이었다.
  **잔향이 fingerprint가 쓰는 스펙트럼 피크를 번지게 하는 것**이 주원인이다.
- 크럼프 트랙은 드럼 루프와 샘플을 서로 많이 공유한다. 다른 곡과 짧고 약하게 맞는 경우가 흔해서
  "확정" 기준을 그만큼 보수적으로 잡는다.
- 라이브러리 안에 같은 녹음이 여러 파일로 있다(다른 앨범 사본, 세션 믹스, 프리뷰). 이들은 한 곡으로 묶는다.

## 2. 아키텍처

```
Chrome 확장 (extension/, Manifest V3, 프레임워크 없는 JS)
  popup.js       화면(동의 / 대기 / 찾는 중 / 결과), 미리듣기, 피드백, 영상 기록 카드와 목록
  background.js  상태 저장(chrome.storage.session), 툴바 배지, 내 기록
  offscreen.js   탭 오디오 녹음(MediaRecorder, Opus, 1초 조각), 중간 확인, 최종 검색
  video.js       탭 주소에서 영상 키
  library.js     라이브러리 앨범 목록 페이지
  i18n.js        화면 문장(_locales/ko, _locales/en), 오류 코드를 문장으로
        │  HTTPS (Lambda Function URL), X-Api-Key 헤더
        ▼
서버 (server/app.py, FastAPI) — 로컬 127.0.0.1:8765 또는 Lambda 컨테이너(Lambda Web Adapter)
  POST /identify   ffmpeg로 16 kHz mono WAV 변환 → 3단계 검색 → 판정
  GET  /preview    찾은 위치에서 10초 MP3 발췌 (전체 파일은 내보내지 않음)
  POST /feedback   맞음 / 틀림 기록
  GET  /video      영상 하나에서 다른 사용자가 찾은 곡 (server/videos.py)
  GET  /albums     앨범 목록 (커버를 data URI로 넣은 JSON 하나)
  GET  /health     상태 확인, 웜 유지 핑
        │
        ├─ Panako queryServer   Java 17 JVM, LMDB fingerprint DB (db/panako/)
        ├─ pair stage           poc/pairfp.py, db/pair.npz
        ├─ rhythm search        poc/rhythm.py, db/rhythm.npz + cohort
        └─ S3                   previews/ (96 kbps MP3), recordings/ (동의한 녹음), videos/ (영상별 기록), runtime/ (DB·인덱스)
```

| 구성 요소 | 경로 | 역할 |
|---|---|---|
| 확장 프로그램 | `extension/` | 녹음, 화면, 미리듣기, 피드백. 서버 주소와 API 키는 `extension/config.js`(git에 없음) |
| API 서버 | `server/app.py` | 검색 파이프라인과 엔드포인트. 로컬과 Lambda에서 같은 코드 |
| 영상별 기록 | `server/videos.py`, `extension/video.js` | 영상 키, 공유 기준, 저장소([영상별 기록](video-records.md)) |
| 시험 | `tests/` | `videos.py`·`video.js` 단위 시험, 실행 중인 서버의 계약·호환 확인(`check_api.py`) |
| 검색 엔진 | `poc/common.py`, `poc/pairfp.py`, `poc/rhythm.py`, `poc/grouping.py` | Panako 클라이언트, pair stage, rhythm search, 같은 녹음 묶기와 판정 |
| Panako | `tools/Panako/`(git에 없음) + `patches/panako.patch` | upstream f1248f7에 우리 패치를 적용해 빌드 |
| 인덱스 빌드 | `poc/build_index.py`, `poc/pairfp.py build`, `poc/build_rhythm_index.py`, `poc/rhythm_cohort.py`, `poc/find_duplicates.py` | fingerprint DB, pair index, rhythm index, cohort, `db/same_audio.csv` |
| 앨범 데이터 | `server/build_albums.py`, `server/fetch_store_covers.py`, `db/album_links.json` | 앨범 목록, 커버, 판매 링크([앨범 링크](album-links.md)) |
| 배포 | `server/deploy/` | 이미지·데이터 준비(`stage.py`), AWS 리소스(`aws_setup.py`), 배포 조건 확인(`release.py`) |
| 사용량 대시보드 | `server/usage.py`, `server/usage.html` | 배포된 서버의 찾기·결과·피드백·Lambda 호출과 비용을 이 PC에서 보는 HTML([Lambda 배포](lambda-deployment.md)) |
| 스토어 | `store/` | 웹스토어 업로드 zip(`package.py`), 등록 문구, 스크린샷(한국어 `assets/`, 영어 `assets/en/`), 화면 확인·스크린샷 도구(`shots/`) |
| 평가 | `poc/evaluate.py`, `poc/make_testset.py`, `poc/real_video_test.py`, `poc/battle_*.py`, `poc/eval_*.py`, `realtest/` | 합성 잡음 세트, 실제 영상, 배틀 100편 측정 |

## 3. 한 번의 찾기

자세한 내용은 확장 프로그램 쪽이 [확장 프로그램](extension.md) §2, 서버 쪽이 [검색 파이프라인](search-pipeline.md)이다.

1. 팝업이 `chrome.tabCapture`로 스트림 id를 받아 background에 넘기고, background가 offscreen 문서를 띄운다.
   offscreen 문서가 녹음하면서 소리를 스피커로 계속 내보낸다. 팝업을 닫아도 녹음은 계속되고, 상태는
   `chrome.storage.session`에 있어 다시 열면 이어서 보인다.
2. 10.5초부터 약 4초마다 지금까지의 녹음을 `partial=1`로 보낸다(15초·20초 기준에 맞춰 15.5·20.5초로 미룸).
   서버가 `final`이라고 답하면 녹음을 바로 멈추고 그 답을 결과로 쓴다.
3. 끝까지 녹음하면 전체 녹음으로 최종 검색을 한다.
4. 영상 탭이면 요청에 탭 주소에서 얻은 영상 키를 붙인다. 서버는 동의한 사용자의 확정·추가 확인 결과를 영상별 기록으로
   남긴다([영상별 기록](video-records.md)).

서버의 검색 순서(`server/app.py` `identify_audio`):

| 단계 | 언제 도나 | 결과 |
|---|---|---|
| Panako fingerprint 검색 | 항상 | 최고 곡 score ≥ 10이고 다른 곡이 그 60%에 못 미치면 확정. 5 미만은 실패 |
| pair stage (2점 해시) | Panako가 확정하지 못할 때 | 한 오프셋에 해시 24개(10초)·32개(15초부터), 라이벌 60% 미만이면 확정 |
| rhythm search, 속도 0.99–1.01 | Panako가 실패(추가 확인도 아님)이고 pair stage도 확정하지 못할 때. 중간 확인에선 10초부터 | 단독으로는 "확실하지 않음"까지만. pair stage 1위(해시 ≥ 20, 라이벌 60% 미만)와 같은 곡이면 확정(`rhythm+pair`) |
| rhythm search, 속도 0.95–1.05 | 최종 검색에서 앞 rhythm 단계가 아무것도 못 찾았을 때 | 빠르거나 느리게 재생된 재업로드용. "확실하지 않음"까지만 |

- 중간 확인에서 rhythm 후보는 강한 후보(유력)만 보이고, 녹음 길이별 비율 기준(10초 1.5, 15초 1.3,
  20초부터 1.2)을 넘으면 녹음을 끝낸다.
- 후보는 같은 녹음끼리 묶어 한 곡으로 판정한다(`poc/grouping.py`, `db/same_audio.csv`). 화면에는 최고 곡 하나만
  보이고, 앨범 정보는 같은 녹음의 사본 중 앨범이 확인되는 쪽에서 가져온다.
- 동의한 사용자의 최종 녹음만 저장하고 결과에 `id`를 붙인다. 피드백은 이 `id`에 기록된다.

## 4. 기술 배경

단계마다 원리, 기준, 보정 근거는 [검색 파이프라인](search-pipeline.md)에 있다. 여기서는 왜 세 단계인지만 요약한다.

### 4.1 오디오 fingerprint — Panako ([검색 파이프라인](search-pipeline.md) §2)

- 스펙트로그램의 국소 최대점(피크)을 골라 피크 몇 개의 주파수·시간 관계를 해시로 만들고, 쿼리와 라이브러리의
  해시가 같은 시간 차로 많이 겹치는 트랙을 찾는다. Shazam(Wang, ISMIR 2003)과 같은 계열이다.
- Panako는 피크 3개로 해시를 만들고 속도 변화(±10%까지 검색)를 견딘다. 대신 피크 하나가 살아남을 확률이
  p일 때 해시는 p³로 살아남아, 잔향이 심하면 거의 남지 않는다([Shazam 잡음 강건성 검토](review-shazam-noise-robustness.md)).
- 합성 잡음 세트에서 관중 소리 3 dB 클립은 83%를 찾고, 라이브러리에 없는 곡 39개에서 실제 오탐은 0건이었다.
  반면 잔향이 강한 홀 녹음(관중 0 dB + 강한 잔향)은 0%다([평가](evaluation.md) §2).
- upstream에 우리 패치(`patches/panako.patch`)를 얹어 쓴다. 시간 정렬, 스레드 안전성, Windows, 상주 쿼리 서버,
  읽기 전용 DB(Lambda) 등이다.

### 4.2 pair stage — 2점 해시 fingerprint ([검색 파이프라인](search-pipeline.md) §3)

- Shazam처럼 피크 2개로 해시를 만든다. 해시가 p²로 살아남아 잔향·잡음에 Panako보다 강하다.
- 배틀 100편의 30초 구간 2,344개에서 확정 구간이 41개 → 414개, rhythm 일치 규칙까지 넣으면 464개가 됐다.
- 속도가 ±3%만 달라도 아무것도 못 찾는다. 속도가 다른 영상은 Panako와 rhythm search가 맡는다.

### 4.3 rhythm search ([검색 파이프라인](search-pipeline.md) §4)

- 잔향이 피크를 번지게 해도 드럼 타격의 타이밍은 남는다. 대역별 onset envelope를 라이브러리 전체와
  cross-correlation한다.
- 드럼 루프 트랙과 긴 믹스는 아무 크럼프 비트와도 잘 맞는다. 긴 믹스는 빼고(`db/exclude.txt`), 트랙마다 라이브러리
  밖 음악에 대한 평소 점수(cohort)를 두어 후보의 신뢰도를 판단한다.
- 단독으로는 확정하지 않는다. 라이브러리에 없는 곡에도 후보가 나오는 경우가 있어서다. pair stage가 같은 곡을
  1위로 꼽을 때만 확정한다.

### 4.4 기술 스택

| 영역 | 사용 기술 |
|---|---|
| 확장 프로그램 | Chrome Manifest V3, `tabCapture`, offscreen document, `MediaRecorder`(Opus), `chrome.storage`, 빌드 단계 없는 순수 JS·CSS |
| 서버 | Python 3.12, FastAPI + uvicorn, numpy, scipy, ffmpeg, boto3, zstandard |
| fingerprint | Panako 2.1(Java 17, LMDB) + 우리 패치 |
| 인프라 | AWS Lambda 컨테이너 이미지(x86_64, 10 GB, Lambda Web Adapter), ECR, S3, Function URL, EventBridge Scheduler(웜 유지), Budgets |

## 5. 데이터

| 데이터 | 위치 | 비고 |
|---|---|---|
| 음원 | `music/` | git에 없음 |
| fingerprint DB | `db/panako/` | Panako LMDB, 약 2,760만 fingerprint. git에 없음, `build_index.py`로 생성 |
| pair index | `db/pair.npz` | 해시 5,170만 개, 214 MB. git에 없음 |
| rhythm index | `db/rhythm.npz`, `db/rhythm_cohort*.npz` | git에 없음 |
| 같은 녹음 목록 | `db/same_audio.csv` | git에 있음(다시 만드는 데 약 50분) |
| 평가 제외 트랙 | `db/holdout.txt` | 약 2%, 인덱스에 넣지 않고 오탐 측정에 씀 |
| 보고 제외 파일 | `db/exclude.txt` | 긴 DJ 믹스·라이브 쇼 |
| 앨범·판매 링크 | `db/albums.json`, `db/album_links.json`, `db/covers/`, `db/store_covers/` | 판매 링크는 손으로 확인 |
| 사용자 녹음 | S3 `recordings/`(Lambda), `recordings/`(로컬) | 동의한 경우만. 로컬 `log.csv`는 `evaluate.py` manifest로 쓸 수 있음 |
| 영상별 기록 | S3 `videos/<플랫폼>/<영상 id>/<녹음 id>.json`(Lambda), `recordings/videos/`(로컬) | 동의한 사용자의 확정·추가 확인 결과. 확정과 맞음 받은 결과만 다른 사용자에게 보임 |
| 내 기록 | 사용자 브라우저 `chrome.storage.local`의 `history` | 최근 100건, 서버로 보내지 않음 |

## 6. 배포와 운영

- 서버는 Lambda 함수 `krump-finder`(동시 실행 상한 3, 타임아웃 60초) 하나다. 큰 데이터(fingerprint DB, rhythm
  index, pair index)는 이미지에 넣지 않고 콜드 스타트 때 S3 `runtime/`에서 `/tmp`로 받는다. 콜드 스타트는
  13~15초이고, 5분마다 `/health`를 불러 인스턴스 하나를 따뜻하게 유지한다.
- 배포는 origin에 push된 main에서만 한다(`CLAUDE.md`, `server/deploy/release.py`가 강제).
- 확장 프로그램 업데이트는 `manifest.json`의 `version`을 올리고 `python store/package.py`로 만든 zip을
  웹스토어 대시보드에 올린다.
- 비용: 하루 5회 사용 기준 월 $1 미만. `Project=krump-finder` 태그로 거른 월 $10 예산 경보가 있다.
- 사용량: `python server/usage.py`가 S3 기록과 CloudWatch를 읽어 `build/usage/index.html`을 만든다(이 PC에서만 봄).
  `server/usage-autostart.ps1`을 한 번 실행하면 로그인 때 로컬 보조 프로그램이 시작되고, HTML을 열 때마다
  최신 데이터를 가져온다([설치와 동작](lambda-deployment.md#사용량-대시보드)).

## 7. 문서 지도

| 문서 | 내용 |
|---|---|
| [README](../README.md) | 설치, 인덱스 빌드, 로컬 실행, 확장 프로그램 불러오기 |
| [검색 파이프라인](search-pipeline.md) | 단계 순서, 후보 묶기와 판정, Panako와 패치, pair stage, rhythm search, 기준과 보정, 서버 실행 구조와 속도 |
| [확장 프로그램](extension.md) | 파일 구성, 녹음 흐름, 팝업 화면, 라이브러리 페이지, 동의와 녹음 저장, 웹스토어 배포, 언어 |
| [평가](evaluation.md) | 평가 세트와 도구, PoC 결과, 실제 영상 테스트, 미확인 후보 |
| [Lambda 배포](lambda-deployment.md) | 왜 Lambda인가, 비용, 배포된 리소스, 배포 절차, 사용량 대시보드, 콜드 스타트 측정 |
| [영상별 기록](video-records.md) | 영상 키, 이벤트 형식, 공유 기준, `GET /video`, 시험, 구간을 기록하지 않는 이유 |
| [앨범 링크](album-links.md) | `db/album_links.json` 형식과 링크를 찾은 방법 |
| [개인정보처리방침](privacy-policy.md), [영어판](privacy-policy.en.md) | 보내는 정보, 보관, 철회 |
| [Shazam 잡음 강건성 검토](review-shazam-noise-robustness.md) | fingerprint 개선 실험(E1–E6)의 근거 |
| [설계안 A — NMFP](design-a-nmfp.md), [설계안 B — MuQ](design-b-muq.md) | 신경망 fingerprint 제안(구현 안 함) |
| `docs/fp-experiments-e1-e6.md` (브랜치 `fp-experiments`) | E1–E9 실험 결과 |
| `store/listing.md` | 웹스토어 등록 문구, 권한 사유 |
