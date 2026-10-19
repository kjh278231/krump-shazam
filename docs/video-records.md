# 영상별 기록

작성 2026-10-19(1.2.0). 찾기 결과를 유튜브·인스타그램 영상별로 기록하고, 같은 영상을 여는 다른 사용자에게 그 영상에서
찾은 곡을 보여 주는 기능이다. 검색 단계와 판정은 [검색 파이프라인](search-pipeline.md) 그대로다. 팝업 화면은
[확장 프로그램](extension.md) §3, 공개되는 정보는 [개인정보처리방침](privacy-policy.md) 3-1번에 있다.

- 코드: `server/videos.py`(순수 함수와 저장소), `server/app.py`(`identify`, `GET /video`, `feedback`, `library_song`),
  `extension/video.js`(`videoKey`), `extension/popup.js`(`loadVideo`, `videoRecords`), `extension/background.js`(`remember`)
- 시험: `tests/test_videos.py`, `tests/video.test.mjs`, `tests/check_api.py`(§6)

## 1. 하는 일

1. 영상 탭에서 찾기를 하면 확장이 탭 주소에서 영상 키를 구해 요청에 붙인다(`vkey`, 동의한 경우만).
2. 공유한 녹음(동의한 사용자의 최종 녹음)의 확정·추가 확인 결과는 영상별 이벤트 객체로 저장한다.
3. 영상 탭에서 팝업을 열면 그 영상의 이벤트를 모아 "이 영상에서 찾은 곡"으로 보여 준다. 곡이 하나면 카드(미리듣기, 판매
   링크)로, 여럿이면 목록으로 보인다. 브라우저에 남긴 내 기록도 함께 보인다.

보이는 것은 **확정** 결과와 **사용자가 맞음을 누른 추가 확인** 결과다. 틀림이 붙은 결과, 확인 없는 후보, 못 찾음은 보이지
않는다. 기록에 대한 맞음/틀림은 받지 않는다(의심스러우면 직접 찾기).

**영상의 몇 초인지는 기록하지 않는다.** 처음 구현은 녹음 앞뒤와 중간 확인마다 `chrome.scripting`으로 탭의 `<video>` 재생
위치를 읽어 곡이 나오는 구간을 기록하고, 그 구간에서 팝업을 열면 녹음 없이 곡을 보여 줬다. 그런데 탭이 캡처되는 동안
스크립트를 실행하면 유튜브 재생이 끊겼다(2026-10-19, 실제 유튜브에서 측정).

| 빌드 | 녹음 중 | 녹음 시작 순간 |
|---|---|---|
| 녹음 중 위치를 읽음 | 0.4·0.1·0.2초 멈춤 3번(약 4초 간격, `waiting`, 버퍼 비어 있음) | — |
| 녹음 중 위치를 읽지 않음 | 없음 | 0.1초 1번(탭 캡처 시작 자체) |

캡처하지 않는 탭에서 같은 스크립트를 4초마다 돌릴 때는 멈춤이 없었다(실행 0.3~12.5ms). 녹음 직전과 직후에만 읽는 방식도
만들었지만, 구간 기능은 빼기로 했다(사용자 결정). 그래서 확장은 페이지를 읽지 않고 `scripting` 권한도 없다.

구간을 다시 만든다면 참고할 것(커밋하지 않은 시도에서 확인):
- 곡의 0:00이 영상의 몇 초인지(앵커) = 녹음 시작 위치 + `query_start` − `match_start` / `time_factor`. battle-100의 같은 재생에서
  15초 떨어진 30초 창 두 개의 앵커가 0.03초 안으로 맞았다. `time_factor`는 쿼리 1초에 곡이 흐르는 초다(4% 빠른 클립에서 1.041).
- 유튜브: 광고 중에는 플레이어(`.html5-video-player`)에 `ad-showing`, 라이브는 `.ytp-live-badge`가 보인다(라이브도
  `duration`은 유한). 쇼츠의 재생 영상은 `#shorts-player` 안에 있고, 숨은 `#movie_player`가 따로 있다.
- 탭이 캡처되는 동안에는 탭에서 스크립트를 실행하지 않는다(위 표).

## 2. 영상 키

`extension/video.js`의 `videoKey(url)`만 주소를 정규화한다. 서버는 키의 형식만 본다(`videos.parse_key`).

| 주소 | 키 |
|---|---|
| `youtube.com`(`www.`, `m.`, `music.`)의 `/watch?v=ID`, `/shorts/ID`, `/live/ID`, `/embed/ID`, `youtube-nocookie.com/embed/ID`, `youtu.be/ID` | `yt:ID` (`[A-Za-z0-9_-]{11}`) |
| `instagram.com/reel/CODE/`, `/reels/CODE/`, `/p/CODE/`, `/<사용자>/reel/CODE/`, `/<사용자>/p/CODE/` | `ig:CODE` (`[A-Za-z0-9_-]{5,64}`) |
| 인스타그램 피드·탐색·`/reels/audio/…`, 채널·검색·재생목록 페이지, 그 밖의 사이트 | 없음(조회·기록 안 함) |

`t`, `list`, `index`, `si`, `pp`, `feature` 같은 쿼리는 버린다. 팝업은 activeTab으로 받은 탭 주소만 쓴다.

## 3. 이벤트 객체

**쓰는 조건.** 최종 결과(일찍 멈춘 중간 확인 포함), `share=1`, 유효한 `vkey`, 판정이 `confirmed` 또는 `check`.

**위치.** Lambda는 S3 `videos/<플랫폼>/<id>/<녹음 id>.json`, 로컬 서버는 `KF_VIDEO_DIR`(기본 `recordings/videos/`) 아래
`<플랫폼>/<id>/<녹음 id>.json`. 녹음 id는 `recordings/`의 녹음과 같은 값이라 삭제 요청 때 함께 찾을 수 있다.

```json
{
  "v": 1, "id": "20261019-101500-123-ab12", "video": "yt:XXXXXXXXXXX",
  "verdict": "confirmed", "method": "pair",
  "song": {"path": "Artist - Album/Artist - Album - 03 Song.mp3", "id": 123456789,
           "match_start": 40.0, "score": 37, "second": 12},
  "feedback": null, "build": "abc1234", "date": "2026-10-19"
}
```

- `song.id`는 내용 id(Panako id, `top_ids[0]`)다. `match_start`는 일치가 시작한 곡 안 위치다.
- `feedback`은 `null`, `"correct"`, `"wrong"`. `/feedback`이 `video`와 함께 오면 고친다.
- `build`는 이미지를 만든 커밋(`stage.py`가 `server/build.txt`에 씀, 로컬은 `local`)이다. 분석용이다.
- 제목, 전체 주소, 시각(날짜 외), 오디오는 넣지 않는다. 다른 사용자에게 보일 수 있는 필드만 담는다.
- 저장이 실패해도 검색 응답은 그대로 간다(로그에는 키·id를 남기지 않음).

## 4. 모아서 보여 주기 (`videos.summarize`)

1. 형식이 틀리거나 `v`가 다른 이벤트는 건너뛴다.
2. 같은 곡(같은 경로, 또는 `same_audio`로 이어진 내용 id)의 이벤트를 묶는다.
3. **점수**(`weight`): 이벤트마다 확정 또는 맞음 +2, 틀림 −3, 그 밖(맞음 없는 추가 확인) 0. 곡의 점수가 `SHOW_SCORE`(2)
   이상이면 보인다. 확정 하나면 보이고, 틀림 하나는 확정 둘을 더 받아야 상쇄된다.
4. `status`는 `confirmed`(점수에 든 확정 이벤트가 있음) 또는 `verified`(맞음을 받은 추가 확인만 있음). 곡의 경로는 점수에
   든 이벤트에서 가장 많이 나온 것, `match_start`는 그 중앙값이다. 점수가 높은 곡부터.
5. 영상 하나에서 이벤트는 S3 LIST 한 페이지(`MAX_EVENTS` 1,000개)까지 읽는다.

**라이브러리가 바뀌면.** 공유 대상은 확정이나 사람이 맞다고 한 결과라 인덱스를 다시 만들어도 무효로 하지 않는다. 응답 때
경로를 지금 라이브러리에서 다시 찾는다(`app.py` `library_song`). 경로가 없으면 내용 id로 rhythm index의 `ids`/`paths`에서 찾고,
제외 목록(`db/exclude.txt`)에 있거나 못 찾으면 뺀다. 앨범 정보는 저장하지 않고 응답 때 `album_info()`로 붙인다.

## 5. API

### `POST /identify` 추가 파라미터 `vkey`

선택이다. 확장은 `share=1`일 때만 보낸다. 형식이 틀리면 무시하고 예전과 똑같이 답한다. 유효하면 최종 답에 `video`가
붙는다(이전 확장은 모르는 필드라 무시한다).

```json
"video": {"key": "yt:XXXXXXXXXXX"}
```

### `GET /video?key=<키>`

API 키가 필요하다. 키 형식이 틀리면 400, 기록이 없으면 빈 목록. 영상 목록을 주는 엔드포인트는 없다. 저장하지 않는다.

```json
{
  "key": "yt:XXXXXXXXXXX",
  "songs": [{"status": "confirmed", "song": {"path": "...", "file": "...", "folder": "...", "match_start": 40.0,
             "album": {"id": "...", "title": "...", "artist": "...", "cover": "data:...", "store": {}}}}]
}
```

응답에 녹음 id, 날짜, 이벤트 수는 없다.

### `POST /feedback` 추가 필드

`video`(영상 키, 기본 `""`). 예전처럼 `recordings/` 기록을 고친 뒤, 키가 유효하고 그 녹음의 이벤트가 있으면 `feedback`을
고친다. 이벤트가 없으면(못 찾음, 영상이 아님) 그냥 넘어간다.

### 브라우저의 내 기록

background가 영상에서 끝난 찾기마다 `chrome.storage.local`의 `history`에 한 줄을 더한다(최근 100건, 커버 없음).
`{id, key, verdict, song{path, file, folder, album(제목), match_start}, date}`. 팝업 목록에서 공유 목록에 없는 것(확인 없는 후보,
못 찾음 등)을 "내 기록"으로 보인다. 동의를 철회하면 지운다.

## 6. 시험

### 6.1 단위 시험

```bash
python -m unittest discover -s tests       # server/videos.py (stdlib unittest, Panako 없이)
node --test "tests/*.test.mjs"             # extension/video.js (Node 22, 패키지 없음)
```

Node 22의 `node --test`는 인자를 glob으로 읽어서, `node --test tests/`처럼 폴더를 주면 그 폴더를 모듈 하나로 실행하려다
실패한다. 따옴표로 감싼 glob을 쓴다(셸 대신 Node가 펼침).

### 6.2 서버 계약과 이전 확장 호환 (`tests/check_api.py`)

로컬 서버는 녹음과 영상 기록을 임시 폴더에 쓰게 띄운다(`recordings/`를 건드리지 않음).

```bash
KF_TMP=<scratch>/kf KF_VIDEO_DIR=<scratch>/videos python server/app.py
python tests/check_api.py --baseline <scratch>/api_baseline.json         # 서버를 바꾸기 전
python tests/check_api.py --compare <scratch>/api_baseline.json --video-dir <scratch>/videos
python tests/check_api.py --contract --video-dir <scratch>/videos
python tests/check_api.py --server "$(cat build/function_url.txt)" --key-file build/api_key.txt --no-store   # 배포 뒤
```

- `--baseline`/`--compare`: 1.0.0·1.1.0이 보내는 요청(`share=1` 최종·중간 확인, 피드백, 미리듣기, 앨범, `/health`)의 상태
  코드, 응답 키, 판정, 최고 곡이 같은지, `video` 필드와 이벤트 파일이 생기지 않는지 본다.
- `--contract`: 확정·추가 확인·못 찾음, 맞음·틀림, 일찍 멈춘 확인, 인스타그램, 잘못된 키, `share` 없음.
- `--no-store`: `share=1`을 보내지 않아 아무것도 저장하지 않는다.
- 클립은 battle-100 영상의 30초 구간이다(`realtest/battle100/audio/`, git에 없음).

### 6.3 측정 (2026-10-19, 로컬)

`GET /video`(웜): 이벤트 3개 2ms, 200개 33ms(구간이 있던 판, 첫 호출은 디스크에서 1초). S3에서는 이벤트 수만큼 GET을 16개씩
병렬로 하므로 200개면 0.3초 안팎으로 추정한다. Lambda 측정은 배포 뒤 [Lambda 배포](lambda-deployment.md)의 웜 상태 응답 표에
넣는다.

## 7. 운영

- 특정 영상의 기록 지우기: `aws s3 rm s3://<bucket>/videos/yt/<id>/ --recursive`(로컬은 `recordings/videos/yt/<id>/` 폴더).
- 녹음 하나의 삭제 요청: `recordings/<녹음 id>.*`와 `videos/*/*/<녹음 id>.json`을 함께 지운다.
- 거짓 기록: API 키가 확장 안에 있어 누구나 이벤트를 만들 수 있지만, 확정은 라이브러리 곡의 실제 오디오가 있어야 나온다.
  문제가 생기면 그 영상의 기록을 지우고, 필요하면 API 키를 바꾼다.
