# Lambda 배포

작성: 2026-10-14. 같은 날 배포했다. 아래 "배포된 구성"이 현재 상태다.

## 왜 Lambda인가

예상 트래픽은 하루 3~5회다. EC2를 상시로 띄우면 거의 놀고 있는 서버에 비용을 낸다.
Lambda는 호출된 시간만큼만 과금한다.

### 월 비용 비교 (서울 ap-northeast-2, 2026-10-14 AWS Pricing API 조회 단가)

| 항목 | EC2 상시 가동 | Lambda |
|---|---|---|
| 서버 실행 | t3.large $0.104/h → 약 $76, c7i.xlarge $0.2016/h → 약 $147 | 사용량 과금 → 약 $0.5 (아래 계산) |
| 디스크 / 이미지 | EBS 30GB 약 $3 | ECR 이미지(약 1GB) 약 $0.1 |
| S3 (미리듣기 10.2GB + 데이터 0.7GB + 녹음) | 약 $0.3 | 약 $0.3 |
| HTTPS 주소 | 도메인과 인증서 필요 | Lambda Function URL (무료, HTTPS) |
| **합계** | **약 $80~150** | **약 $1 미만** |

Lambda 단가 (서울):

| | 단가 |
|---|---|
| 실행, x86 | $0.0000166667 / GB-초 |
| 실행, ARM | $0.0000133334 / GB-초 |
| 요청 | $0.20 / 100만 건 |
| `/tmp` 512MB 초과분 | $0.0000000309 / GB-초 (무시할 수준) |
| 상시 대기(provisioned concurrency) | 켜 두는 동안 $0.0000051254 / GB-초 + 실행 $0.0000119592 / GB-초 |

실행비 계산 (10GB 메모리):
- 곡 찾기 1회 = 콜드 스타트 약 11초 + 검색 호출 3~4번 약 5초 → 약 16초 × 10GB = 160GB-초.
- 하루 5회 × 30일 = 150회 → 약 24,000GB-초 → x86 기준 약 $0.40/월.
- Lambda 상시 무료 한도(월 40만 GB-초, 100만 요청)가 계정에 적용되면 실행비는 0원이다.

웜 유지 (2026-10-18, `aws_setup.py warm`):
- 2점 해시 인덱스가 들어간 뒤 콜드 스타트가 13~15초가 됐다. 녹음의 첫 중간 검색(10.5초)보다 3~4초 늦다.
- EventBridge Scheduler가 5분마다 `/health`를 부른다(Function URL 요청 형식이라 앱까지 간다).
  한 번의 청구 시간은 3ms(측정)라 0.03GB-초이고, 월 8,640번에 약 $0.01이다. AWS가 몇 시간마다 인스턴스를 바꿀 때의
  콜드 스타트(회당 약 $0.002)를 더해도 월 $1 미만이다.
- 따뜻하게 유지되는 인스턴스는 1개뿐이고, 교체 직후에는 콜드 스타트가 난다.
- 상시 대기 인스턴스 1개(10GB)를 24시간 켜 두면 약 $133/월이라 쓰지 않는다. 컨테이너 이미지라 SnapStart는 쓸 수 없다.

참고: 혼자 쓰는 동안은 로컬 PC에서 서버를 돌리는 것도 0원이다. 확장 프로그램은 Lambda를 쓰고,
`extension/config.js`의 주소를 `http://127.0.0.1:8765`로 고치면 로컬 서버를 쓴다.

## 배포된 구성

```
Chrome 확장 ──HTTPS(Function URL, X-Api-Key 헤더)──▶ Lambda krump-finder (컨테이너 이미지, x86_64)
                                                     ├ 시작할 때 S3 runtime/에서 받아 /tmp에 둠
                                                     │   ├ rhythm.npz (234MB)
                                                     │   ├ pair.npz (2점 해시 인덱스, 214MB)
                                                     │   └ data.mdb (fingerprint DB, 1.17GB → zstd 조각 36개 472MB)
                                                     ├ Panako queryServer (JVM, 읽기 전용 LMDB)
                                                     ├ ffmpeg
                                                     ├─▶ S3 previews/   미리듣기용 96kbps MP3 (Range GET으로 10초만)
                                                     └─▶ S3 recordings/ 녹음 WAV + 결과·피드백 JSON
```

| 리소스 | 이름 / 값 |
|---|---|
| Lambda 함수 | `krump-finder`, 메모리 10240MB(vCPU 6개, `RHYTHM_THREADS=6`), 타임아웃 60초, `/tmp` 3072MB, 동시 실행 상한 3 |
| Function URL | `build/function_url.txt`, 확장 프로그램의 기본 서버. `AuthType NONE` + 앱의 API 키 확인 |
| API 키 | `build/api_key.txt` (git에 없음), 함수 환경 변수 `KF_API_KEY` |
| ECR 저장소 | `krump-finder`, 최근 이미지 3개만 유지 |
| IAM 역할 | `krump-finder-lambda`: CloudWatch Logs + 이 버킷의 `previews/`·`runtime/` 읽기, `recordings/` 읽기·쓰기 |
| S3 버킷 | `krump-finder-previews-136575447624`: 비공개(Block Public Access), SSE-S3 암호화 |
| 웜 유지 | EventBridge Scheduler 그룹 `krump-finder`의 `krump-finder-warm`(5분마다 `/health`), 역할 `krump-finder-scheduler`(이 함수 호출만) |
| 태그 | 모든 리소스에 `Project=krump-finder` |

비용 상한: 동시 실행 3개와 타임아웃 60초가 폭주 비용을 막는다. 2026-10-18 웹스토어 공개를 앞두고
AWS Budgets 경보를 추가했다: `KF_BUDGET_EMAIL=… KF_BUDGET_USD=10 python server/deploy/aws_setup.py budget`.
- `Project` 비용 할당 태그를 활성화하고(리소스는 바꾸지 않는 Billing 설정), `Project=krump-finder`로
  거른 월 $10 예산 `krump-finder-monthly`를 만든다. 계정의 다른 리소스 비용은 섞이지 않는다.
- 실제 비용 80%, 월말 예상 100%를 넘으면 메일이 온다. 태그가 비용에 반영되기까지 최대 24시간 걸린다.

## 배포·갱신 방법

배포는 origin에 push된 main만 기준으로 한다(`CLAUDE.md`). 메인 체크아웃이 `origin/main`과 같고 추적 파일에
미커밋 변경이 없어야 `stage.py`와 `aws_setup.py data image function`이 돈다(`server/deploy/release.py`).
워크트리나 다른 브랜치에서는 거절된다. 이미지 태그 끝에 커밋 해시가 붙는다.

```bash
python server/build_albums.py                    # 앨범 목록·표지 → db/albums.json, db/covers/ (이미지에 포함)
python server/fetch_store_covers.py              # 판매처 커버 → db/store_covers/ (db/album_links.json, 이미지에 포함)
python server/deploy/make_previews.py            # 라이브러리 → build/previews/ (96kbps CBR MP3)
aws s3 sync build/previews s3://krump-finder-previews-136575447624/previews/
python server/deploy/stage.py                    # build/lambda (이미지), build/runtime (S3 데이터)
python server/deploy/aws_setup.py bucket data image function warm
```

- 곡을 추가하면: 인덱스 재생성 → `build_albums.py` → (`album_links.json`에 새 앨범 링크 추가 →
  `fetch_store_covers.py`) → `make_previews.py` + `s3 sync` → `stage.py` → `aws_setup.py data image function`.
- 앨범 목록(`GET /albums`)은 표지를 data URI로 넣은 JSON 하나(약 2MB)로 보낸다. 표지마다 요청하면
  300번 넘게 Lambda를 호출하게 되고 동시 실행 상한(3)에 걸린다.
- 코드만 바꾸면 `stage.py` → `aws_setup.py image function`. 이미지에서 코드가 마지막 레이어라 몇 MB만 올라간다.
- 확장 프로그램: Function URL과 API 키는 `extension/config.js`(git에 없음)에 고정돼 있고, 팝업에서
  보거나 바꿀 수 없다. `aws_setup.py function`이 이 파일을 다시 쓰므로 URL·키가 바뀌면 다시 실행하고
  확장 프로그램을 새로고침한다. 키는 확장 프로그램 파일과 요청 헤더에 그대로 들어 있어 숨겨지지는 않는다.
- 주소는 공개돼도 된다. 키 없는 요청은 401로 거절되지만 Lambda 실행은 과금되므로, 최악의 경우(동시 실행 3개를
  한 달 내내) 약 $780/월이다. 이상하면 API 키를 바꾸거나(`KF_API_KEY`) 동시 실행 상한을 0으로 내린다.

## 서버 코드에서 바뀐 것

1. **fingerprint DB 읽기 전용 열기**: Panako 패치 8 (`-Dpanako.lmdb.readonly=true`, `MDB_RDONLY | MDB_NOLOCK`).
   Windows의 `data.mdb`는 맵 크기(32GB)만큼 잡혀 있으므로 `stage.py`가 사용 중인 페이지만 복사한다(1.17GB).
2. **경로 대신 라이브러리 키**: API의 `path`는 `music/` 기준 상대 경로(`/` 구분)다.
   Lambda에는 음원이 없으므로 `db/catalog.json`으로 키를 만든다(`poc/common.py` `Library`).
3. **기록 저장**: `KF_BUCKET`이 있으면 녹음과 결과를 S3 `recordings/<id>.wav`, `<id>.json`에 저장하고,
   피드백은 그 JSON을 고친다. 없으면 기존처럼 `recordings/log.csv`.
4. **미리듣기**: S3의 96kbps CBR MP3(ID3·Xing 헤더 없음)에서 `시작초 × 12000` 바이트부터
   11초 분량만 Range GET으로 받아 10초를 잘라 보낸다. 바이트 위치 오차는 6곡 중 5곡이 3ms 이내였다
   (1곡은 반복 루프 때문에 상관 측정이 한 마디 어긋났을 뿐이다). 원본 전체 파일은 내보내지 않는다.
5. **API 키**: `KF_API_KEY`가 있으면 `/health`를 뺀 모든 요청에 `X-Api-Key`가 맞아야 한다(아니면 401).
   확장 프로그램은 미리듣기도 fetch로 받아 헤더를 붙인다.
6. **FastAPI를 Lambda에서 실행**: Lambda Web Adapter 0.9.1. `/health`가 준비 확인 경로다.

## 콜드 스타트 (측정)

처음 설계는 데이터를 이미지에 넣는 것이었다. 측정해 보니 Lambda가 이미지 파일을 처음 읽을 때
(새 이미지 직후나 한동안 안 쓴 뒤) 약 7MB/s로 느려서 쓸 수 없었다.

| 방식 | 콜드 스타트 | 비고 |
|---|---|---|
| 데이터를 이미지에 포함, 새 이미지 직후 | 45~67초 | rhythm index 읽기 35초, Panako 18초. 첫 요청이 타임아웃(60초)으로 실패하기도 함 |
| 데이터를 이미지에 포함, 캐시된 경우 | 7.8초 | 대신 첫 검색들이 DB 페이지를 느리게 읽어 10초 검색 1회에 27초 |
| S3에서 원본 1.4GB 다운로드 | 19~25초 | 인스턴스당 약 80MB/s. 메모리 10GB로 올려도 같음 |
| S3, DB를 zstd 조각으로 | 9.8~12.8초 (새 이미지 직후 포함, 3회) | 다운로드 0.7GB. rhythm.npz 3초 → rhythm index 5초 동안 DB 다운로드가 겹쳐 진행 |
| **위 + 2점 해시 인덱스 (현재, 2026-10-18)** | **13.4~14.6초** (이미지 캐시 후 3회 중 2회. 1회는 27초로 import·Panako가 함께 느린 인스턴스) | 다운로드 0.92GB. pair.npz(조각 188MB)와 DB가 대역폭을 나눠 써서 둘 다 약 12초에 끝남. 처음 판(조각 아님, scipy.signal import)은 14.7초 |

웜 상태 응답 (서울 Lambda, 10GB, 이 PC에서 측정, 2026-10-15). 응답의 `timings`에 단계별 초가 들어 있다:

| 요청 | 시간 |
|---|---|
| 중간 검색, 10초 녹음 (fingerprint + 리듬, 2026-10-16) | 1.0~1.6초 |
| 중간 검색, 리듬 포함 (19초 녹음) | 1.3~1.5초 (fingerprint 0.5 + 리듬 0.8) |
| 최종 검색 (30초, fingerprint 실패 → 리듬) | 약 1.5초 |
| 최종 검색, 못 찾는 곡 (넓은 속도 범위까지) | 약 4.4초 (넓은 범위 2.9초) |
| 10초 미리듣기 | 0.5초 |
| 2점 해시 단계 (`timings.pair`, 2026-10-18) | 10초 녹음 0.07~0.15초, 30초 녹음 0.2~0.28초 |

확장 프로그램 흐름 시험 (하네스, Lambda):

| 녹음 | 결과 |
|---|---|
| 깨끗한 녹음, 콜드 상태에서 시작 | 깨우기 10.2초 → 12.6초에 확정 (로컬 11초) |
| WWB 9 → DARK | 26.1초에 리듬 유력 후보 |
| PS Buck → Afro Krump | 35.9초 (30초 녹음 + 최종 검색) |
| 라이브러리에 없는 곡 | 35.6초 "찾지 못함" |

확장 프로그램 쪽 대응:
- 녹음 버튼을 누르는 순간 `/health`로 깨운다. 첫 중간 검색은 그 응답이 온 뒤에 보낸다.
  인스턴스는 한 번에 요청 1개만 처리하므로, 준비 중에 요청을 더 보내면 콜드 인스턴스가 하나 더 뜬다.
- 녹음이 끝났을 때 중간 검색이 진행 중이면 그 결과를 기다린 뒤 최종 검색을 보낸다(같은 이유).
- 첫 중간 검색은 10.5초(1초 단위 조각 10개가 다 도착한 뒤)에 보낸다. 리듬 유력 후보로 멈추는 기준은
  녹음 길이에 따라 낮아진다(10초 1.5배, 15초 1.3배, 20초부터 1.2배). 그래서 13~15초, 18~20초 사이에 잡힌
  중간 검색은 15.5초, 20.5초로 미룬다. 예전에 20초 기준 하나만 있을 때 19.8초 검색은 후보를 찾아도 멈추지
  못했다(DARK 35.5초 → 26.1초).
- Lambda는 요청 사이에 인스턴스를 얼려서 백그라운드 스레드가 돌지 않는다. 미리 읽기 같은 작업은 요청 안에서 해야 한다.

## Lambda 제한과 영향

| 제한 | 값 | 영향 |
|---|---|---|
| 동기 요청 페이로드 | 6MB | 45초 Opus 녹음도 약 1MB라 문제없음 |
| 실행 시간 | 함수 설정 60초 | 요청당 수 초 |
| `/tmp` | 설정 3GB | 데이터 1.6GB + 녹음 변환 |
| 인스턴스 네트워크 | 약 80MB/s (측정) | 콜드 스타트 하한을 정함 |
