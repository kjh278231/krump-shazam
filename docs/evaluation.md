# 평가

작성 2026-10-19(README의 PoC·실제 영상 결과를 옮김). 검색 단계마다의 보정 결과(pair stage의 battle-100 표, rhythm
search 기준 등)는 그 단계와 함께 [검색 파이프라인](search-pipeline.md)에 있다.

## 1. 평가 세트와 도구

| 세트 | 만들기 / 돌리기 | 결과 |
|---|---|---|
| 합성 열화 클립 | `python poc/make_testset.py --n 100` → `poc/testset/` + `manifest.csv`, `python poc/evaluate.py --threads 8` | `poc/report.txt`, `poc/results.csv` |
| 실제 녹음 | manifest CSV를 직접 써서 `python poc/evaluate.py --manifest your.csv` | manifest 옆에 보고서 |
| 확장 프로그램 사용자 녹음 | 로컬 서버의 `recordings/log.csv`가 그대로 manifest(피드백 없는 줄은 건너뜀) | 〃 |
| 실제 영상 | `python poc/real_video_test.py <audio dir>` | `realtest/windows_battles.csv`, `realtest/windows_known.csv` |
| battle-100 | `poc/battle_download.py` → `poc/battle_test.py` → `poc/battle_report.py` | `realtest/battle100/` |

**합성 열화 클립** (`poc/make_testset.py`). 라이브러리 트랙에서 30초를 잘라 조건별로 열화한다.
- `clean`: 그대로
- `pink10`: 핑크 잡음 10 dB SNR
- `crowd3`: 관중 웅성거림 + 박수 + 함성 3 dB SNR
- `crowd0_room`: 관중 0 dB + 방 잔향 + 7 kHz low-pass(폰·스트림 같은 소리)
- `speed+3`, `speed-3`: 3% 빠르게/느리게(DJ 피치 페이더처럼 템포와 음높이 함께) + 관중 6 dB

holdout 트랙(`db/holdout.txt`, 약 2%, 인덱스에 넣지 않음)은 `crowd3`로 만들어 오탐을 잰다. `evaluate.py`는 클립 전체와
15초 조각 3개(0–15, 8–23, 15–30초)를 질의하고, 전체 클립의 최고 일치가 기준 점수 이상이면 받아들인다.

**실제 녹음 manifest.** 열은 `clip,condition,expected,offset`이다. `expected`는 라이브러리 파일의 전체 경로이고,
라이브러리에 없는 곡이면 비운다.

**실제 영상** (`poc/real_video_test.py`). 영상 전체를 15초 간격의 30초 구간으로 잘라 모두 질의한다. 이웃 구간이 같은
트랙을 맞히고 트랙 안 위치도 영상만큼 움직였으면(±2초, 속도 보정) *일관된* 일치로 본다. 우연한 일치는 이렇게
맞아떨어지지 않으므로 일관된 일치는 거의 확실히 맞다.

**battle-100** (`realtest/battle100/`). 배틀 영상 100편을 서버와 똑같은 방식으로 돌린다.
- `poc/battle_download.py`: `videos.tsv`의 영상 오디오를 받는다. YouTube는 빠르게 10편쯤 받으면 yt-dlp를 봇으로
  막는다. 스크립트는 영상 사이에 쉬고, 막히면 기다렸다 다시 시도하며, 차단을 우회하지 않는다.
- `poc/battle_test.py`: 30초 구간을 15초마다 `server/app.py`의 `identify_audio`에 최종 검색처럼 넣는다 →
  `windows.jsonl`. 아무것도 기록하거나 올리지 않는다.
- `poc/battle_report.py`: 귀로 확인하는 HTML 보고서(`report.html`). 같은 녹음이 이어지는 구간을 곡 카드로 묶고,
  영상 쪽과 라이브러리 쪽 12초 클립을 나란히 들려준다. 맞음 / 틀림 / 모르겠음 답은 브라우저에 남고 JSON으로
  내보낸다. 공개용이 아니다.
- `poc/eval_pair.py`: 같은 구간을 pair stage로 다시 질의한다 → `pair_report.txt`([검색 파이프라인](search-pipeline.md) §3).

**rhythm search 보정 스크립트** (결과는 `realtest/`).

| 스크립트 | 무엇을 재나 | 결과 |
|---|---|---|
| `poc/eval_rhythm.py` | 정답이 있는/없는 질의에서 rhythm search 점수 | `rhythm_eval.csv` |
| `poc/eval_rhythm_z.py` | raw score와 cohort 정규화(z) 비교, 사용자 녹음 포함 | `rhythm_z.jsonl` |
| `poc/eval_rhythm_wide.py` | 0.95–1.05 wide 단계 | `rhythm_wide_report.txt` |
| `poc/eval_rhythm_early.py` | 녹음 중 멈춤 기준(`RHYTHM_EARLY`) | `rhythm_early.jsonl` |
| `poc/eval_rhythm_prefix.py` | 녹음 앞부분 15/20/25/30초의 정확도 | `rhythm_prefix.jsonl` |
| `poc/eval_rhythm_split.py` | 20초 반쪽 두 개가 같은 곡을 꼽을 때만 받아들이는 규칙 | `rhythm_split.csv` |
| `poc/eval_rhythm_votes.py` | 15초 하위 구간 투표 규칙 비교용 수집 | `rhythm_votes.jsonl` |

fingerprint 개선 실험(E1–E9)은 브랜치 `fp-experiments`의 `docs/fp-experiments-e1-e6.md`와 `poc/fpexp/`에 있다.

## 2. PoC 결과 (2026-10-13, 합성 열화)

DB: 고유 트랙 4,132개(파일 4,210개, 바이트가 같은 사본 78개는 id 공유), 약 244시간, fingerprint 2,760만 개, 스레드
8개로 43분에 빌드. 질의: 클립당 0.24초(스레드 8개로 돌린 전체 시간을 클립 수로 나눔).

받아들이는 기준 `score >= 10`, 조건마다 라이브러리 트랙 100개를 무작위로, 30초 클립:

| 조건 | 찾음 |
|---|---|
| clean | 100% |
| 핑크 잡음 10 dB SNR | 99% |
| 관중(웅성거림 + 박수 + 함성) 3 dB | 83% |
| 속도 +3% + 관중 6 dB | 82% |
| 속도 −3% + 관중 6 dB | 85% |
| 관중 0 dB + 강한 방 잔향 | 0% |

- 라이브러리에 없는 트랙(39개, 관중 3 dB): **실제 오탐 0건**. 보고된 5건은 모두 다른 파일로 저장된 같은 곡이었다
  (다른 마스터·앨범, 그 곡이 들어간 세션 믹스나 프리뷰 메들리).
- 라이브러리 곡 질의 약 600개 중 오인식은 1건(score 35). 나머지 "틀림" 8건은 다른 파일에 든 같은 곡이었다.
- 잔향 진단(트랙 25개): 직접음이 잔향보다 큰 잔향(DRR +3 dB)만 있으면 25/25, DRR −3 dB 13/25, 관중 3 dB +
  DRR −3 dB 2/25. 관중만 있으면 0 dB 18/25, −3 dB 7/25. **잔향 심한 방 녹음이 가장 큰 약점이고**, 관중 소리가 아니다.
- 15초 조각 3개 중 2개가 일치해야 받아들이게 하면, 잡음 조건에서 약 17%p를 잃고 정밀도는 나아지지 않았다. 그래서
  보고만 하고 요구하지 않는다.

이 결과 뒤에 실제 영상으로 검증했고(§3), 잔향에 강한 두 번째 단계로 rhythm search와 pair stage를 넣었다
([검색 파이프라인](search-pipeline.md)).

## 3. 실제 영상 테스트 (2026-10-13)

`python poc/real_video_test.py <audio dir>`(§1). 구간별 결과: `realtest/windows_battles.csv`,
`realtest/windows_known.csv`. 이 시점의 검색은 Panako뿐이었다.

| 영상 | 구간 | 결과 |
|---|---|---|
| YouTube 트랙 업로드 3편(Saturation, Morf Anthem, Scripterz vs Bullzeyex) | 39 | 39 정답 |
| 현장에서 찍은 크럼프 세션(Morf Anthem Part 1) | 4 | 4 정답 |
| 댄스 영상, I Am Everything (Playa x Kid NY) | 4 | 약한 일치 2건(score 11–14) |
| 댄스 영상, This Is Nordside (Mozarf) | 6 | 0(역방향 질의로도 흔적 없음, 다른 버전·편집으로 보임) |
| 배틀 11편(EBS 2024–26, Rise Against 2025, Illest 2K25…) + 곡을 모르는 영상 4편 | ~240 | 일치 0 |

- 실제 구간에서 잘못 찾은 경우는 없었다.
- 배틀 영상에서 Panako는 약한 후보조차 찾지 못했다. 처음에는 DJ가 튼 곡이 라이브러리에 없다고 추측했는데,
  **그건 틀렸다.** 사용자가 라벨을 단 실패 사례는 모두 라이브러리에 있었고 속도 1.000으로 재생됐다.
  - WWB 9 → DARK
  - Rise Against 3-way → KILL EM
  - PS Buck Vol.4 → Afro Krump

  홀 잔향, 관중, MC가 Panako에 필요한 스펙트럼 피크를 번지게 한다. 이 열화가 실패의 주원인이고, 팝업의 실패
  안내도 이것을 먼저 말한다.
- 속도 검색을 ±25%로 넓히면 우연한 일치(score 6–13)만 늘어서 ±10%를 유지한다.
- PoC의 "틀림" 2건은 사실 같은 녹음이었다: 나이키.mp3 = MOZARF "gon giv it to yall".

## 4. 미확인 후보 (배틀 영상)

rhythm search는 Panako가 놓친 배틀 영상에서도 일관된 일치를 내놓는다. 같은 트랙이 여러 구간에 걸쳐 나오고, 트랙 안
위치가 30초씩 나아간다.

- Rise Against 3-way → LUKE "KILL EM"
- EBS 2026 final → Proof the Xrook "Master Of The Goon Talk Vol. Z - 09"

KILL EM은 사용자가 그 영상을 확장 프로그램으로 녹음해 확인했다. 나머지는 확인되지 않았다.
