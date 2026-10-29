# 검색 파이프라인

작성 2026-10-19(README의 서버·검색 단계 설명을 옮김). 전체 그림은 [개요](overview.md), 측정 세트와 실제 영상
결과는 [평가](evaluation.md), Lambda에서의 속도와 콜드 스타트는 [Lambda 배포](lambda-deployment.md)에 있다.

## 0. 단계 순서

`server/app.py`의 `identify_audio`가 녹음 하나를 이 순서로 검색한다.

| 순서 | 단계 | 언제 도나 | 답 |
|---|---|---|---|
| 1 | Panako fingerprint 검색 (§2) | 항상 | 확정 / 추가 확인 / 실패 |
| 2 | pair stage (§3) | Panako가 확정하지 못할 때 | 확정 또는 없음 |
| 3 | rhythm search, 속도 0.99–1.01 (§4) | Panako가 실패이고 pair stage도 확정하지 못할 때. 녹음 중 확인에서는 10초부터 | 추가 확인. pair stage가 같은 곡을 1위로 꼽으면 확정 |
| 4 | rhythm search, 속도 0.95–1.05 (§4.6) | 최종 검색에서 3이 아무것도 보여 주지 못할 때 | 추가 확인 |

Panako가 추가 확인(점수가 낮거나 라이벌이 가까움)을 내면 rhythm search는 돌지 않는다. 판정 값은 서버에서
`confirmed` / `check` / `fail`이고, 팝업에는 찾음 / 찾음 · 확실하지 않음 / 못 찾음으로 보인다. 결과의 `method`는
`fingerprint`, `pair`, `rhythm`, `rhythm+pair` 중 하나다. 결과를 영상별로 기록하고 공유하는 일은 판정 뒤에
따로 한다([영상별 기록](video-records.md)).

**녹음 길이.** 확장 프로그램은 최대 30초를 녹음하고, 못 찾은 뒤 다시 찾을 때만 45초나 1분을 고를 수 있다
([확장 프로그램](extension.md) §3). 아래 기준은 모두 10–30초 오디오로 보정했고, 더 긴 오디오에도 그대로 쓴다.
rhythm search와 pair stage의 일치 규칙(§3)만 30초 이하에 한정한다.

## 1. 후보 묶기와 판정 (`poc/grouping.py`)

- 같은 녹음을 담은 파일(다른 앨범 사본, 그 곡이 들어간 세션 믹스나 저지 쇼케이스 녹음)은 한 곡으로 묶고
  "같은 음원"으로 함께 나열한다. 연결은 `python poc/find_duplicates.py` → `db/same_audio.csv`에서 온다. 모든
  트랙을 캐시된 print로 DB에 질의하며 약 50분 걸리고, 곡을 추가하면 다시 돌린다. 연결에는 15초 이상에 걸쳐
  score ≥ 30이 필요하다. 그보다 짧거나 약한 트랙 간 일치는 크럼프에 흔한 공유 드럼 루프와 샘플이다.
- 프리뷰·프로모·티저 파일(27개, 각각 58–99%가 앨범의 본 트랙에 들어 있음)은 본 트랙 뒤로 숨긴다. 프리뷰에서만
  찾은 곡은 따로 표시한다.
- 판정: 최고 곡이 score ≥ 10이고 *다른* 곡이 그 60%에 못 미치면 확정, 아니면 추가 확인(점수가 낮거나 라이벌이
  가까움), 5 미만은 실패다. 합성 세트에서 확정했는데 틀린 답은 0건이다(관중 3 dB: 확정 85, 추가 확인 5, 실패 10).
  pair stage도 같은 함수에 자기 기준을 넣어 판정한다.
- 정답 여부도 파일 경로가 아니라 내용 id와 `db/same_audio.csv` 연결로 판단한다. 라이브러리에 같은 녹음이 여러
  파일로 있기 때문이다.

## 2. Panako fingerprint 검색

- [Panako](https://github.com/JorenSix/Panako) 2.1. 스펙트럼 피크 3개로 해시를 만들어 LMDB fingerprint DB
  (`db/panako/`)에서 찾는다. 속도 변화는 ±10%까지 찾는다(±25%로 넓히면 우연한 일치만 늘었다,
  [평가](evaluation.md) "실제 영상 테스트").
- DB: 고유 트랙 4,132개(파일 4,210개, 바이트가 같은 사본 78개는 id를 공유), 약 244시간, fingerprint 2,760만 개.
  스레드 8개로 43분에 만들었다(`python poc/build_index.py --threads 8`, 이어서 만들기 가능).
- 서버는 `poc/common.py`의 `PanakoServer`로 상주 JVM(`queryServer`)에 질의한다.
- 잡음에는 강하지만 잔향에 약하다. 피크 하나가 살아남을 확률이 p이면 해시는 p³로 살아남는다
  ([Shazam 잡음 강건성 검토](review-shazam-noise-robustness.md)).

### 2.1 Panako 패치 (`patches/panako.patch`)

이 라이브러리로 Windows에서 PoC를 돌리면서 찾은 문제들이다.

1. **견고한 시간 정렬** (`PanakoStrategy`, `PANAKO_ROBUST_ALIGN=TRUE`, 기본값 켜짐). upstream은 질의와 레퍼런스의
   속도 비를 첫 히트 묶음과 마지막 히트 묶음만으로 추정한다. 잡음 섞인 루프 기반 음악에서는 루프에서 벗어난
   오프셋을 고르는 일이 잦고, 그러면 추정 속도가 0.8–1.2 밖으로 나가 일치 전체가 버려진다(score ~700 또는 없음).
   모든 히트에 대한 Hough 방식 탐색으로 바꿨다. 스모크 테스트(3 트랙 × 백색/핑크/관중 잡음 × 5–30 dB SNR)에서
   인식이 18/45에서 45/45가 됐다.
2. **스레드 안전성**: 스레드별 질의/저장 큐가 일반 `HashMap`에 있어 동시 접근 때 항목이 사라졌다 →
   `ConcurrentHashMap`. LMDB `maxReaders`가 스레드 수와 같았다 → 올림. 질의 작업 스레드의 예외가 조용히 삼켜졌다
   → 로그로 남김.
3. **Windows**: LMDB는 map 크기 전체를 디스크에 미리 잡는다. 고정된 1 TB를 `-Dpanako.lmdb.mapsize.gb`로 바꿨다
   (기본은 여전히 1024, 스크립트는 32). 파이프 디코더가 CLI 설정 인자보다 먼저 만들어져
   `DECODER_PIPE_ENVIRONMENT=cmd.exe`가 무시되던 것도 고쳤다.
4. **비ASCII 경로**(소스 패치 아님): Java 17은 cp949 밖의 명령줄 인자(`ö`, `★`, 일본어)를 `?`로 바꿔서 약 120
   트랙이 빠졌다. `poc/common.py`가 파일 목록을 UTF-8 `.txt` 파일로 넘긴다.
5. **식별자**: 숫자만으로 된 파일 이름(`01.mp3`)이 트랙 id로 쓰여 다른 앨범의 같은 이름 파일끼리 충돌했다.
   이제 `-Dpanako.numeric.ids=true`가 아니면 내용 해시를 쓴다.
6. **질의 서버** (`cli/QueryServer.java`, 명령 `queryServer`): JVM과 LMDB를 열어 둔 채 stdin으로 들어오는
   `id<TAB>path` 줄에 스레드 풀로 답하는 상주 프로세스. Python 클라이언트는 `poc/common.py`의 `PanakoServer`.
7. **WAV 직접 입력**: 이미 16 kHz인 mono 16비트 WAV는 ffmpeg 파이프 디코더 대신 Java Sound로 읽는다. 이 Windows
   PC에서는 프로세스 하나를 띄울 때마다 약 0.8초가 들고(실시간 검사), 파이프 디코더는 `cmd.exe`와 `ffmpeg`를
   둘 다 띄운다.
8. **읽기 전용 DB** (`-Dpanako.lmdb.readonly=true`): 읽기 전용 파일 시스템(AWS Lambda)을 위해 LMDB를
   `MDB_RDONLY` + `MDB_NOLOCK`으로, DB를 새로 만들지 않고 연다.

## 3. pair stage (2점 해시 fingerprint)

**왜.** Panako는 스펙트럼 피크 3개를 해시한다. 관중 소리가 있는 홀에서는 세 피크가 모두 살아남아야 해시가
남으므로, 피크 하나가 p로 살아남으면 해시는 p³로 남는다. 피크 2개짜리 Shazam식 해시(Wang 2003)는 p²로 남는다.
rhythm search가 찾아낸 battle-100 구간 409개에서 Panako는 29개, pair index는 290개를 확정한다
(`docs/fp-experiments-e1-e6.md` E5, 브랜치 `fp-experiments`). pair 해시는 속도 변화를 견디지 못한다. ±3%에서
아무것도 못 찾으므로, 속도가 다른 영상은 Panako와 rhythm search가 맡는다.

**원리** (`poc/pairfp.py`, audfprint 규약):
- 11025 Hz, 512점 STFT, hop 256. 로그 크기에 bin마다 시간 방향 high-pass를 걸어 onset 쪽을 살린다. 31 bin × 9
  프레임 안의 국소 최대점을 초당 최대 20개 고른다.
- 각 피크를 63 프레임·±31 bin 안의 다음 피크 3개와 짝지어 20비트 해시(f1, df, dt)를 만든다.
- 질의는 트랙마다 시간 오프셋에 투표하고(±1 프레임 허용), sub-hop 이동 4가지로 fingerprint를 뽑는다.
  score는 가장 많이 맞은 오프셋의 해시 수다.
- `python poc/pairfp.py build` → `db/pair.npz`: fingerprint DB의 파일 4,132개(LMDB resource map에서 읽음)를
  Panako id 그대로 넣어서 `db/same_audio.csv` 묶기를 그대로 쓴다. 해시 5,170만 개, 214 MB(해시마다 시작 오프셋,
  항목마다 파일 번호와 시간을 32비트에). 프로세스 4개로 19분에 만들었다. 질의는 오디오 10초에 0.1초, 30초에
  0.3초 걸린다.

**확정 기준.** 한 오프셋에 해시가 10초 오디오에서 24개, 15초부터 32개 있고 다른 곡이 그 60%에 못 미쳐야 확정이다
(`poc/pairfp.py`의 `CONFIRM`). 보정(`poc/fpexp/e7_pair_lengths.py`, 브랜치 `fp-experiments`)에는 10–30초로 자른
테스트 클립 3,842개를 썼다. 라이브러리에 없는 곡, 그리고 잡음이 섞이거나 속도가 다른 라이브러리 곡은 10초에서
최대 20, 15초부터 최대 29였다. 이들은 클립 전체에서 한 오프셋에 해시가 맞는 공유 드럼 루프라서, 15초를 넘겨도
기준을 키울 필요가 없다.

**rhythm search와 일치.** 그 기준에 못 미쳐도, 30초 이하 오디오에서 rhythm 후보가 pair stage의 1위 곡이고 해시가
20개 이상, 다른 곡이 60% 미만이면 확정한다(`poc/pairfp.py`의 `AGREE`). 녹음 중 확인에서도 마찬가지다.
보정(`poc/fpexp/e9_rhythm_pair.py prod`, 브랜치 `fp-experiments`, E9c)에는 라이브러리에 없는 곡의 클립 622개를
확장 프로그램이 보내는 10/15/20/24/30초로 잘라 썼다. rhythm search의 (틀린) 후보가 pair 1위였을 때 점수는 최대
18(30초; 24초에 14, 15–20초에 11)이었고, 매번 60% 이상의 라이벌이 있어 규칙이 한 번도 걸리지 않았다.

**battle-100** (`python poc/eval_pair.py` → `realtest/battle100/pair_report.txt`). 구간 2,344개를 pair stage로
10/15/20/30초씩 다시 질의했다.

| 배포된 답 (30초) | 구간 | pair stage가 같은 곡을 확정 | 다른 곡 | 없음 |
|---|---|---|---|---|
| fingerprint 확정 | 41 | (Panako가 먼저) | | |
| fingerprint 추가 확인 | 34 | 23 | 0 | 11 |
| rhythm 유력 | 410 | 254 | 2 | 154 |
| rhythm 약한 | 241 | 32 | 6 | 203 |
| 없음 | 1,618 | | 새 답 56 | 1,562 |

- 확정 구간이 41개에서 414개로, 일치 규칙까지 넣으면 464개(rhythm 유력 36개, 약한 14개 추가)로 늘었다.
- "다른 곡" 8개 중 3개는 바로 앞 구간에서 Panako가 pair의 곡을 확정한 경우다(ctwqUAT9bIE). 4개는 구간마다 rhythm
  답이 바뀌는 동안 pair 답은 그대로였다.
- 새 답 56개 중 46개는 30초 떨어진 구간도 같은 곡을 꼽는다.
- 녹음이 더 일찍 멈춘다. 어느 쪽으로든 답이 나온 구간에서 10초 멈춤은 27개 → 227개(일치 규칙 없이 218개),
  30초 끝까지 녹음은 428개 → 358개가 됐다. 10/15/20초 멈춤 중 확정은 219/70/35개다.

## 4. rhythm search

### 4.1 언제 도나

Panako가 실패이고 pair stage도 확정하지 못하면 서버가 rhythm search를 돌린다(`poc/rhythm.py`). 홀이나 카메라
녹음은 Panako가 쓰는 스펙트럼 피크를 번지게 하지만 드럼 타격의 타이밍은 남는다.

### 4.2 원리

- 각 트랙을 약 43 프레임/초의 대역별 onset envelope로 줄인다. 이런 녹음에서는 저역이 사라지므로 150–5500 Hz를 쓴다.
- 질의를 속도 0.99–1.01에서 라이브러리 전체와 cross-correlation한다. 최종 검색은 그래도 아무것도 없으면
  0.95–1.05도 시도한다(§4.6).
- 라이브러리 쪽은 overlap-save FFT 조각으로 미리 계산해 둔다. 조각마다 질의 길이만큼 겹치게 두므로 질의는
  `RhythmIndex.MAX_Q`(3,072 프레임, 약 71초)까지만 정확하다. 더 길면 조각 안에서 상관이 감겨 점수가 부푼다.
  2,048(약 47초)이던 때 1분 질의는 트랙 52–72개의 점수가 틀렸고, 직접 계산한 상관보다 최대 43% 높았다.
  3,072로 늘려도 47초 이하 질의의 점수는 같다(차이 1e-7 이하). 조각이 604개에서 614개로 늘어 메모리가 8 MB 늘고,
  검색 시간은 잰 범위에서 차이가 없었다(2026-10-19).
- 빌드: `python poc/build_rhythm_index.py` → `db/rhythm.npz`(약 15분). 곡을 추가하면 다시 돌린다.
- 검색은 속도 3개·스레드 3개로 약 0.3초, wide 단계가 약 2.2초 더 걸린다(이 PC; 스레드 6개면 1.6초). Lambda
  (10 GB, 스레드 6개)에서는 약 0.75초, wide 단계 약 2.9초 더.

### 4.3 결과

- Panako가 0–3%만 찾는 합성 관중 0 dB + 강한 잔향 클립에서 top-1 정답이 71/100이다.
- 실제 사례: WWB 9 저지 쇼케이스(Lil ROXXDAMUS - DARK, 속도 1.000, DJ 점프 있음)에서 Panako는 0점이었다. rhythm은
  구간 7개 중 6개에서 DARK를 다음 곡의 2–2.5배 점수로 1위에 올렸다. 사용자가 확장 프로그램으로 직접 녹음한 30초도
  DARK의 0:40 위치로 나온다.

### 4.4 루프 트랙

단순 드럼 루프("bvckloop" 트랙)와 긴 믹스는 거의 모든 크럼프 비트와 상관이 높아서, 관계없는 검색에서 1위를
차지한다. 두 가지로 막는다.

- `db/exclude.txt`: 보고하지 않을 파일 목록. 30분 넘는 DJ·세션 믹스와 라이브 쇼 6개가 들어 있다. PLAYA 세션 믹스
  2개(개별 트랙으로 99%/81%가 덮임), Ted 믹스 3개와 라이브 쇼 1개(크럼프 라이브러리 내용 0%, Ted's Krump Mix만
  49%)다. 줄을 지우면 그 파일이 돌아오고, 다시 빌드할 필요는 없다.
- `python poc/rhythm_cohort.py` → `db/rhythm_cohort.npz`: 라이브러리에 없는 오디오(holdout 트랙의 impostor 질의
  252개)에 대한 트랙별 평소 점수(cohort). 평가의 holdout 트랙을 쓰며, `make_testset.py`의 seed로 다시 뽑는다.

순위는 raw score로 매긴다. z = (score − 트랙 평균) / 트랙 표준편차만으로 매기면 더 나빴다(room 클립 top-1 64 vs
73). "agree"는 raw 1위가 z 1위이기도 하다는 뜻이다.

### 4.5 후보를 보여 주는 기준

`realtest/rhythm_z.jsonl`로 보정했다.

| 단계 | 규칙 | room 클립 (100) | 모르는 곡 (39) |
|---|---|---|---|
| 유력 후보 | 다른 곡 중 최고 점수 대비 비율 ≥ 1.2이고 agree | 43개 표시, 42개 정답 | 3 |
| 약한 후보 | 비율 ≥ 1.3, 또는 agree이고 z ≥ 4 | 54개 표시, 52개 정답(두 단계 합) | 10(두 단계 합) |

Panako가 놓친 사용자 녹음 중 DARK, Afro Krump, KILL EM 하나는 유력 후보로 나온다. 다른 KILL EM 하나는 약한
후보로 나온다. 다섯 번째인, DJ가 곡을 되돌린 지점을 가로지른 녹음은 여전히 실패한다.

rhythm 결과만으로는 확정하지 않는다. "들어 보고 확인해 달라"는 안내와 함께 추가 확인으로만 보인다. pair stage가
같은 곡을 꼽으면 확정이 된다(§3).

### 4.6 속도가 다른 영상

`poc/eval_rhythm_wide.py` → `realtest/rhythm_wide_report.txt`.

어떤 업로드는 트랙을 빠르거나 느리게 재생한다. 쇼츠 hdnJN-k6ePg는 WLDLF 07을 1.03배로 튼다. Panako는 속도를
되돌려도 현장 잡음 때문에 아무것도 찾지 못했다. 30초 질의는 실제 속도에서 약 0.3% 벗어나면 rhythm 일치를 잃어서,
0.99–1.01로는 이 영상을 놓쳤다.

그래서 그 검색이 아무것도 보여 주지 못하면 최종 검색(녹음 중 확인은 아님)이 0.95–1.05를 0.005 간격으로 다시
돈다. 자체 cohort(`rhythm_cohort.py --wide` → `db/rhythm_cohort_wide.npz`)와 더 엄격한 기준을 쓴다. 유력은 비율 ≥
1.25이고 agree, 약한은 비율 ≥ 1.3, 또는 agree이고 z ≥ 5다.

| wide 단계가 더한 것 | 표시 | 정답 |
|---|---|---|
| 1.2–4.8% 속도가 다른 room 클립 100개(첫 단계만으로는 6개 표시, 1개 정답) | +13 | 13 |
| 속도 1.0인 room 클립, DARK 영상, 배틀 구간 | 0 | – |
| 모르는 곡(속도 1.0 39개, 속도 변경 39개) | +1 | – |
| 사용자 녹음 | WLDLF 07(유력) | 정답 |

### 4.7 녹음 중 확인

`poc/eval_rhythm_early.py` → `realtest/rhythm_early.jsonl`.

배틀 구간 대부분은 어느 fingerprint로도 확정되지 않는다(battle-100 구간 2,344개 중 Panako 41개, pair stage 포함
414개). 그래서 녹음 중 확인에서도 10초부터 rhythm search를 돌린다. 유력 후보의 비율이 녹음한 길이의 기준을 넘으면
녹음을 끝낸다. 기준은 10초 1.5, 15초 1.3, 20초부터 1.2다(`server/app.py`의 `RHYTHM_EARLY`). 확인은 10.5초에
처음 돌고 그 뒤 4초마다 도는데, 한 단계 바로 앞에 걸리면 15.5 / 20.5초로 미룬다.

| 멈춤 규칙 | 배틀 곡(30초에 찾은 409개): 10 / 15 / 20초 멈춤, 틀림 | 30초에 아무것도 못 찾은 구간(1,618개): 멈춤 | 라이브러리에 없는 곡(356개): 틀림 |
|---|---|---|---|
| 20초에 1.2 (이전) | 0 / 0 / 270, 0 | 15 | 29 |
| **10초 1.5, 15초 1.3** | **30 / 135 / 106, 1** | **27** | **31** |
| 10, 15, 20초 모두 1.2 | 106 / 91 / 77, 5 | 50 | 43 |

라이브러리에 없는 곡의 "틀림"에는 라이브러리 다른 곳에 연결되지 않은 같은 곡 사본(예: 두 앨범에 든 같은 비트)도
들어 있다. 약한 후보는 모르는 곡 39개 중 6–10개에서 나왔기 때문에 녹음 중에는 보이지 않는다.

확장 프로그램을 거쳐 서버에 흉내 내 보면, DARK는 26초에 결과가 보인다(전에는 33초). 깨끗한 클립은 11초에
확정되고, 모르는 곡은 녹음이 끝난 뒤 실패로 나온다.

## 5. 서버 실행 구조와 속도 (2026-10-14, i7-13700F, Windows)

**구조.**
- Panako JVM 하나(`queryServer`, 스레드 8개)를 모든 요청이 같이 쓴다.
- 요청마다 녹음을 ffmpeg로 한 번만 16 kHz mono WAV로 바꾼다. Panako는 그 WAV를 바로 읽고, pair stage와 rhythm
  search는 numpy로 리샘플한다.
- 요청은 FastAPI 스레드 풀에서 돈다. rhythm search는 하나가 임시 메모리를 약 0.5 GB 잡으므로 동시에 최대
  `RHYTHM_WORKERS`(기본 3)개만 돈다.

30초 클립의 Panako 질의(`python poc/bench_panako_server.py`). 결과는 1회성 `query` 명령과 같고(8/8), 비ASCII
경로도 된다.

| | 질의당 | 동시 |
|---|---|---|
| 1회성 `panako query` | 3.56초 | — |
| 질의 서버, ffmpeg 파이프 | 2.02초 | — |
| 질의 서버, WAV 직접 | **0.32초** | 호출자 8개로 초당 20 질의 |

HTTP 서버 전체(`python server/loadtest.py <webm>... --cleanup`):

| 요청 | 요청 하나 | 동시 클라이언트 8개 |
|---|---|---|
| fingerprint 일치 | 1.2초(그중 0.8초가 ffmpeg 시작) | 초당 3.5 요청, 중앙값 1.9초 |
| rhythm search | 3.3–4.6초(전에는 7–9초) | 섞인 세트: 초당 1.5 요청 |

녹음 길이별 최종 검색(로컬 서버, Opus 160 kbps webm을 `POST /identify`, 2026-10-19). 배틀 영상 6개 구간과
인덱스에 없는 트랙(`db/holdout.txt`) 4개, 각 2회:

| 길이 | 찾은 경우 (`elapsed`) | 못 찾는 곡, 넓은 속도 범위까지 | 단계별 (fingerprint / pair / rhythm / wide) |
|---|---|---|---|
| 30초 | 0.9–1.7초 | 5.3–5.5초 | 0.6 / 0.35 / 0.6 / 3.8초 |
| 1분 | 1.9–2.6초 | 6.4초 | 1.2 / 0.7 / 0.6 / 3.9초 |

fingerprint와 pair stage는 길이에 비례하고 rhythm search는 길이와 거의 무관하다.

Linux에서는 프로세스 시작이 밀리초 단위라 ffmpeg 몫은 대부분 사라질 것이다. Lambda의 단계별 시간은
[Lambda 배포](lambda-deployment.md)에 있다. 응답의 `timings`에 단계별 초가 들어 있다.

## 6. 사용자에게 보이는 오류 (2026-10-26)

확장 프로그램이 화면에 띄우는 오류는 응답 JSON에 `code`가 붙는다. 1.3.0부터 확장 프로그램이 `code`를 보고 사용자 언어의
문장을 고른다. `detail`은 예전 문장 그대로다. 1.0.0·1.2.0은 `detail`을 그대로 띄우기 때문이다(`server/app.py` `ApiError`).

| 상태 | `code` | 언제 | `detail` |
|---|---|---|---|
| 401 | `bad_key` | API 키가 다름 | API 키가 맞지 않습니다. |
| 400 | `empty` | 녹음이 1,000바이트 미만 | recording is empty (is the tab playing audio?) |
| 400 | `undecodable` | ffmpeg가 녹음을 못 읽음 | ffmpeg could not decode the recording: … |
| 400 | `silent` | 거의 무음(RMS < 0.003, 음소거된 탭) | 소리가 거의 녹음되지 않았습니다. … |
| 404 | `no_albums` | `GET /albums`인데 앨범 목록 파일이 없음 | 앨범 목록이 없습니다 … |

그 밖의 오류(`GET /video`의 잘못된 키, 미리듣기·피드백 실패)는 `code`가 없다. 확장 프로그램이 그 문장을 보이지 않기 때문이다.
확인: `python tests/check_api.py --errors --key-file <키 파일>`(`detail`이 그대로인지도 본다).
