# Lambda 배포 검토

작성: 2026-10-14. 아직 결정 전이며, 리소스는 하나도 만들지 않았다.

## 왜 Lambda인가

예상 트래픽은 하루 3~5회다. EC2를 상시로 띄우면 거의 놀고 있는 서버에 비용을 낸다.
Lambda는 호출된 시간만큼만 과금한다.

### 월 비용 비교 (서울 ap-northeast-2, 2026-10-14 AWS Pricing API 조회 단가)

| 항목 | EC2 상시 가동 | Lambda |
|---|---|---|
| 서버 실행 | t3.large $0.104/h → 약 $76, c7i.xlarge $0.2016/h → 약 $147 | 사용량 과금 → 약 $0.2 (아래 계산) |
| 디스크 / 이미지 | EBS 30GB 약 $3 | ECR 이미지(약 3GB) 약 $0.3 |
| 미리듣기 음원 S3 (96kbps, 약 10.5GB) | 약 $0.3 | 약 $0.3 |
| HTTPS 주소 | 도메인과 인증서 필요 | Lambda Function URL (무료, HTTPS) |
| **합계** | **약 $80~150** | **약 $1 미만** |

Lambda 단가 (서울):

| | 단가 |
|---|---|
| 실행, x86 | $0.0000166667 / GB-초 |
| 실행, ARM | $0.0000133334 / GB-초 |
| 요청 | $0.20 / 100만 건 |

실행비 계산:
- 곡 찾기 1회 = 중간 검색 포함 약 3번 호출 × 메모리 4GB, 합계 약 22초(첫 호출의 콜드 스타트 포함) → 88GB-초.
- 하루 5회 × 30일 = 150회 → 약 13,000GB-초 → x86 기준 약 $0.22/월.
- Lambda 상시 무료 한도(월 40만 GB-초, 100만 요청)가 계정에 적용되면 실행비는 0원이다. 계정 유형에 따라 무료 한도가 다를 수 있어 확인이 필요하다.

참고: 혼자 쓰는 동안은 지금처럼 로컬 PC에서 서버를 돌리는 것이 0원이다.
Lambda가 필요한 경우는 두 가지다. 다른 사람도 쓰게 할 때, 그리고 PC가 꺼져 있어도 쓰고 싶을 때다.

## 구성안

```
Chrome 확장 ──HTTPS(Function URL, API 키 헤더)──▶ Lambda (컨테이너 이미지)
                                                   ├ Panako queryServer (JVM, init 단계에서 1회 기동)
                                                   ├ fingerprint DB (LMDB, 압축본 약 1.1GB, 이미지에 포함)
                                                   ├ rhythm index + cohort (약 0.25GB, 이미지에 포함)
                                                   ├ ffmpeg (정적 바이너리)
                                                   ├─▶ S3 previews/  미리듣기용 저음질 MP3 (Range GET으로 10초만)
                                                   └─▶ S3 recordings/ 녹음 WAV + 로그·피드백 (JSON)
```

| 설정 | 값 |
|---|---|
| 런타임 | 컨테이너 이미지 (Python 3.12 + Java 17 + ffmpeg) |
| 아키텍처 | x86_64. ARM64가 20% 싸지만 금액이 작아 호환성 위험을 피한다. Panako jar에는 jgaborator의 Linux x86_64·aarch64 라이브러리가 모두 들어 있음을 확인했다 |
| 메모리 | 4GB. rhythm index 약 1GB + 검색 중 임시 메모리 약 0.5GB + JVM. Lambda는 메모리에 비례해 vCPU가 배정되며, 4GB면 약 2.3 vCPU |
| 타임아웃 | 60초 |
| 동시 실행 상한 | 낮게 (예: 3). 비용 폭주 방지 |
| 엔드포인트 | Function URL (`AuthType NONE` + 앱 레벨 API 키 확인) 또는 IAM 인증 |
| S3 버킷 (안) | `krump-finder-previews-136575447624`: 비공개, Block Public Access, SSE-S3 암호화 |
| 태그 | 모든 리소스에 `Project=krump-finder` |

## 지금 코드에서 바꿔야 할 것

1. **fingerprint DB 읽기 전용 열기**
   - Lambda 이미지 파일 시스템은 읽기 전용이고, 쓸 수 있는 곳은 `/tmp`(최대 10GB)뿐이다.
   - Panako의 LMDB를 `MDB_RDONLY | MDB_NOLOCK`으로 여는 패치를 추가한다. `/tmp`로 1.1GB를 복사하면 콜드 스타트가 수 초 늘어난다.
   - DB는 `mdb_copy -c`(Python `lmdb` 패키지의 `env.copy(compact=True)`)로 압축해서 이미지에 넣는다. Windows의 `data.mdb`는 맵 크기만큼(32GB) 미리 잡혀 있으므로 그대로 복사하지 않는다.
2. **기록 저장 위치**: `recordings/`와 `log.csv`를 S3(`recordings/` 접두사, 요청마다 JSON 1개)로 옮긴다. 피드백(`/feedback`)도 S3 객체를 갱신한다.
3. **미리듣기**
   - `/preview`가 S3에서 필요한 바이트만 가져오게 한다. CBR MP3이므로 오프셋 ≈ 시작초 × 비트레이트/8로 Range GET을 하고 10초를 잘라 보낸다.
   - 원본 전체 파일은 내보내지 않는다.
   - 변환 스크립트: 라이브러리 → 96kbps CBR MP3 → S3 업로드.
4. **API 키**: 서버는 `X-Api-Key` 헤더를 확인하고, 확장 프로그램 설정에 키를 넣는다.
5. **FastAPI를 Lambda에서 실행**: Lambda Web Adapter(컨테이너에 확장으로 추가)를 쓰면 지금 FastAPI 앱을 거의 그대로 쓸 수 있다.
6. **콜드 스타트 완화**: 팝업을 열거나 녹음을 시작하는 순간 `/health`를 호출해 미리 깨운다. 첫 중간 검색이 10초 뒤라서, 녹음하는 동안 준비가 끝나도록 한다.

## 콜드 스타트 (추정, 미측정)

한동안 호출이 없으면 실행 환경이 내려간다. 다음 요청은 아래를 새로 한다.
- 이미지 로드 (지연 로딩)
- JVM 기동 + LMDB 열기
- rhythm index 로드와 FFT 사전 계산 (로컬 1.5초)
- 첫 검색 시 LMDB 페이지 읽기

**합계 10~20초로 추정한다.** 하루 3~5회면 거의 매 요청이 콜드 스타트다. 위 6번의 미리 깨우기로 체감 지연을 줄인다. 상시 대기(Provisioned Concurrency)는 월 비용이 생겨 이 트래픽에는 맞지 않는다.

웜 상태 성능은 로컬 측정을 참고한다(README "Server performance").
- Panako 검색: 0.32초
- 리듬 검색: 약 2초 (데스크톱 1코어 기준, Lambda vCPU는 더 느릴 수 있음)

## Lambda 제한과 영향

| 제한 | 값 | 영향 |
|---|---|---|
| 동기 요청 페이로드 | 6MB | 30초 Opus 녹음은 약 0.6MB라 문제없음 |
| 컨테이너 이미지 | 최대 10GB | 약 3GB 예상 |
| 실행 시간 | 최대 15분 | 요청당 수 초 |
| `/tmp` | 최대 10GB | 녹음 변환용으로 충분 |

## 결정 필요 사항 (2026-10-14 기준 미정)

1. Lambda 방식과, 첫 요청 10~20초 지연을 감수할지
2. 리전: 서울. 버킷 이름: `krump-finder-previews-136575447624`
3. 미리듣기 음질: 96kbps(약 10.5GB) 또는 64kbps(약 7GB)
4. API 키 방식으로 접근 제한
5. 음원 저음질 사본을 S3에 올리는 것(저작권 판단은 사용자 몫)

## 배포 순서 (결정 후)

1. Panako 읽기 전용 패치, S3 저장소 코드, API 키 → 로컬에서 테스트
2. fingerprint DB 압축 복사본 생성
3. S3 버킷 생성(비공개·암호화) → 미리듣기 MP3 변환·업로드
4. ECR 저장소 생성 → 컨테이너 이미지 빌드·푸시 (Docker Desktop이 이 PC에 설치되어 있음)
5. IAM 역할: S3 버킷 읽기·쓰기, CloudWatch Logs만 허용
6. Lambda 함수 + Function URL 생성, 동시 실행 상한 설정
7. 확장 프로그램 서버 주소와 API 키 설정 → 실제 영상으로 확인, 콜드·웜 지연 측정
8. 비용 경보 (AWS Budgets, 예: 월 $5)

각 단계에서 리소스를 만들기 직전에 만들 목록을 확인받는다.

현재 AWS CLI는 이 PC에 `user-coco`(Administrators 그룹, 기본 리전 ap-northeast-2)로 설정되어 있다.
계정에 이미 있는 리소스(S3 버킷 12개, 정지된 EC2 r6i.8xlarge)는 건드리지 않는다.
