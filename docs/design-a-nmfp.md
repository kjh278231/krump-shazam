# 설계안 A — 열화 강건 신경망 핑거프린트 (NMFP, Araz et al. ISMIR 2025)

작성 2026-10-13 · 상태: 제안

근거 논문: Araz et al., *Enhancing Neural Audio Fingerprint Robustness to Audio Degradation for
Music Identification*, ISMIR 2025 ([arXiv 2506.22661](https://arxiv.org/abs/2506.22661)),
코드 [raraz15/neural-music-fp](https://github.com/raraz15/neural-music-fp).

## 1. 왜 바꾸는가

[평가](evaluation.md) "실제 영상 테스트"에 정리된 대로, 배틀 영상에서 Panako가 실패하는 원인은 **곡이 없어서가 아니라
홀 잔향 + 관중 + MC로 스펙트럼 피크가 번지는 것**입니다.

- 라벨된 실패 3건(WWB 9 → DARK, Rise Against 3-way → KILL EM, PS Buck Vol.4 → Afro Krump)은
  모두 라이브러리에 있고 속도 1.000으로 재생됐습니다.
- 합성 조건 `crowd0_room`은 0%, 잔향 DRR −3 dB + crowd 3 dB는 2/25입니다.
- 리듬 fallback은 `crowd0_room`에서 top-1 71/100을 맞히지만, unknown 곡의 15%에도 후보를 내기 때문에
  **추가 확인**으로만 표시할 수 있습니다.

Panako의 해시 `(f1, f2, Δt)`는 피크 위치 자체가 흐려지면 복구할 방법이 없습니다. NMFP는 1초 구간
멜 스펙트로그램 전체를 보고, **잔향·마이크·소음 열화를 시뮬레이션해서 학습한** 임베딩을 씁니다.
논문의 실제 공연장(바, 클럽, 콘서트홀) 녹음 평가에서 10초 쿼리 top-1이 84.6%였고, 같은 조건에서
NAFP-Adam은 71.7%, Shazam 계열 피크 방식 GraFP는 82.5%였습니다. 우리 실패 조건과 가장 가까운
평가입니다.

## 2. 목표와 비목표

**목표**
- `crowd0_room` 같은 잔향 + 관중 조건에서 **확정 수준 정밀도**로 식별합니다. 1차 목표는 리듬
  fallback의 top-1 71%를 넘으면서 holdout 오확정 0건입니다.
- 라벨된 실제 배틀 실패 3건을 확정 또는 추가 확인으로 맞힙니다.
- 기존 강점은 유지합니다: 깨끗한 업로드 100%, 속도 ±3% 조건 82–85%, 오확정 0건.

**비목표**
- 커버, 리믹스, 다른 버전 식별(option B 유지).
- Panako 제거. NMFP에는 템포 증강이 없어서 DJ 피치 페이더(±3–10%)는 Panako가 계속 맡습니다.

## 3. 바뀐 구조

```
                ┌────────────── server/app.py (Windows, :8765) ──────────────┐
 extension ──▶  │ ffmpeg → 22.05 kHz WAV                                     │
 (30 s Opus)    │   ├─▶ Panako query (기존, ~2.5 s)                           │
                │   └─▶ NMFP: 8 kHz 멜 → ONNX 임베딩 → 최근접 검색 → 오프셋 정렬 │
                │ grouping.group / verdict (점수 체계별 임계값)                  │
                │ 둘 다 실패 → rhythm fallback (기존, 추가 확인 전용)             │
                └─────────────────────────────────────────────────────────────┘
 학습/내보내기: WSL2 Ubuntu (TensorFlow 2.13 + GPU) → ONNX 파일 → Windows에서 onnxruntime
```

**결정 1: Panako와 NMFP를 병렬로 실행합니다.** NMFP 쿼리는 모델이 메모리에 상주하면 수백 ms 수준이라
병렬 실행 비용이 거의 없습니다. 판정 규칙:

| Panako | NMFP | 결과 |
|---|---|---|
| 확정 | 같은 곡 또는 없음 | 확정 (기존과 동일) |
| 확정 | 다른 곡을 확정 | 추가 확인 (충돌, 둘 다 표시) |
| 실패/약함 | 확정 | 확정 (`method=neural`) |
| 실패/약함 | 약함 | 추가 확인 |
| 실패 | 실패 | rhythm fallback |

"같은 곡"은 지금처럼 content id와 `same_audio.csv` 링크로 판단합니다.

**결정 2: 학습은 WSL2, 서빙은 Windows 네이티브 onnxruntime으로 합니다.**
- 공식 코드는 TensorFlow 2.13과 essentia를 씁니다. TensorFlow는 2.11부터 네이티브 Windows GPU를 지원하지
  않고, essentia도 Windows 휠이 없습니다. 이 PC에는 WSL2 Ubuntu가 이미 실행 중이고 GPU는 RTX 4070 Ti
  (12 GB)입니다.
- 학습한 모델은 `tf2onnx`로 ONNX로 내보냅니다. 그러면 서버와 인덱스 빌드는 기존 Python 3.12 환경에
  `onnxruntime`만 추가해서 돌릴 수 있습니다. 서버가 WSL에 의존하지 않게 됩니다.
- 위험 요소: 멜 전처리가 모델 그래프 안에 있으면 STFT 연산이 ONNX로 안 넘어갈 수 있습니다. 그 경우
  멜 계산을 numpy로 다시 구현하고, 같은 입력에 대해 TF 출력과 임베딩 코사인 ≥ 0.999로 일치하는지
  검증합니다.

**결정 3: 처음에는 FAISS 없이 numpy 전수 검색으로 시작합니다.** 규모를 계산하면 충분합니다.
- 라이브러리 244 h ÷ hop 0.5 s ≈ 176만 세그먼트입니다.
- 128차원 기준 fp16이면 약 450 MB, fp32면 약 900 MB입니다. 실제 임베딩 차원은 설정 파일에서 확인해야
  합니다. NAFP 계열은 128차원입니다.
- 30초 쿼리는 59개 세그먼트이고, 176만 × 128 행렬곱 한 번이 약 13 GFLOP이라 CPU에서 0.1–0.3 s로
  예상합니다.
- 느리면 `faiss-cpu`(IVF)나 GPU 행렬곱으로 바꿉니다.

## 4. 구성 요소

### 4.1 핑거프린트 모델
| 항목 | 값 (논문/공식 코드) |
|---|---|
| 입력 | 8 kHz 모노, 멜 스펙트로그램, 하한 160 Hz, 80 dB 다이내믹 레인지 → [−1, 1] |
| 세그먼트 | 1 s, 인덱싱/쿼리 hop 0.5 s |
| 인코더 | NAFP 구조 CNN (`configs/nmfp-triplet.yaml`) |
| 손실 | Triplet, hard positive + semi-hard negative mining, 제곱 유클리드, margin 0.5 |
| 배치 | anchor 512 × positive 2 (총 1,536), 트랙당 anchor 1개 |
| 가중치 | Zenodo 10.5281/zenodo.15719945 (`nmfp-triplet`, AGPL-3.0) |

라이선스: 가중치가 AGPL-3.0입니다. 개인 로컬 사용은 문제없지만, 이 서버를 다른 사람에게 네트워크
서비스로 제공하면 소스 공개 의무가 생깁니다.

### 4.2 증강 파이프라인 (도메인 적응용)
논문은 TUT 배경소음, OpenAIR·AIR·MIT 방 IR, 마이크 IR을 씁니다. 우리 실패 조건에 맞춰 다음을
추가하거나 비중을 바꿉니다.

| 열화 | 소스 | 이유 |
|---|---|---|
| 큰 홀 잔향, **DRR −10 ~ +3 dB** | OpenAIR 대형 공간, MIT IR survey, Aachen AIR | 진단상 DRR −3 dB에서 무너짐 (13/25) |
| 관중 (함성, 박수, 휘파람) | FSD50K `Crowd`/`Cheering`/`Applause` + 기존 `make_testset.crowd()` | 합성 군중만으로는 다양성이 부족함 |
| MC 음성 | LibriSpeech/MUSAN speech → PA 시뮬레이션 (200–5k Hz 대역, 소프트 클리핑, 같은 IR 통과) | 배틀 MC는 홀 스피커를 거쳐 나옴 |
| SNR | 음악 대비 −3 ~ +10 dB | `crowd0`, `crowd-3` 진단 조건 포함 |
| 저역 손실 | 고역통과 100–300 Hz | [검색 파이프라인](search-pipeline.md) rhythm search 섹션의 관찰 |
| 코덱 | ffmpeg AAC 96–128 kbps → Opus 48–96 kbps 재인코딩 | YouTube → 탭 캡처 경로 |
| 마이크 | 논문의 마이크 IR + 휴대폰 대역 제한 | 촬영 카메라/폰 |

잔향은 소음을 섞기 **전에** 음악에 적용하고, 관중과 MC에는 별도 IR을 적용합니다. 실제 홀에서도 관중이
마이크에 더 가깝기 때문입니다.

### 4.3 학습 데이터 분할
라이브러리 자체를 학습에 씁니다. 핑거프린트는 인덱싱한 곡을 구별하는 것이 목적이라 학습곡 = 인덱스곡이어도
됩니다. 다만 일반화를 측정하기 위해 세 그룹으로 나눕니다.

| 그룹 | 비율 | 학습 | 인덱스 | 용도 |
|---|---|---|---|---|
| train | ~88% | ✔ | ✔ | 학습 |
| index-only | ~10% | ✘ | ✔ | **나중에 추가할 곡**에 대한 일반화 측정 |
| holdout | 기존 `db/holdout.txt` (109 파일) | ✘ | ✘ | 오탐 측정 |

**중요: 같은 녹음을 negative로 쓰면 안 됩니다.** `same_audio.csv` 링크와 content id(`panako_id`)로 묶은
곡은 한 그룹으로 취급해서, 같은 배치 안에서 서로 negative가 되지 않게 합니다. 그렇지 않으면 다른 앨범의
같은 곡을 밀어내도록 학습됩니다.

크럼프는 프로듀서끼리 드럼 루프를 공유합니다. 다른 곡의 같은 루프는 원래 hard negative가 맞습니다.
semi-hard mining이 이 경우를 자연스럽게 다루지만, 평가에서 교차곡 혼동을 따로 집계합니다(§6).

### 4.4 검색과 정렬 (Shazam의 오프셋 투표를 그대로 씁니다)
1. 쿼리 30 s → 1 s 세그먼트 59개 → 임베딩 (L2 정규화).
2. 세그먼트마다 top-k(=20) 이웃 `(track_id, t_ref, sim)`을 찾습니다.
3. 후보마다 `Δ = t_ref − t_query`를 0.5 s 단위로 양자화해서 `(track_id, Δ)`별로 투표합니다.
4. 같은 `(track_id, Δ±0.5)`에 모인 세그먼트의 유사도 합을 **sequence score**로 씁니다.
   논문은 평균 유사도를 쓰지만, 우리는 정렬된 세그먼트 수도 함께 봐야 하므로 합을 씁니다.
5. 결과를 기존 `common.Match`로 바꿔서 `grouping.group()`을 그대로 탑니다.
   - `score` = round(sequence score × 10)
   - `query_start`/`query_stop` = 정렬된 첫/마지막 세그먼트
   - `match_start` = `t_ref`, `time_factor` = 100

판정 임계값(`CONFIRM_SCORE`, `CHECK_SCORE`, `RIVAL_RATIO`)은 Panako 값을 쓰면 안 됩니다.
`grouping.verdict()`에 점수 체계별 임계값 묶음을 넘기도록 바꾸고, NMFP 값은 §6의 보정 세트로 정합니다.

## 5. 구현 단계

### Phase 0 — 사전학습 가중치로 영점 평가 (학습 없음)
가장 싸고, 이 결과로 Phase 1을 할지 결정합니다.
1. WSL2에 conda 환경을 만듭니다(`python=3.11`, `tensorflow==2.13`, `faiss-gpu`, essentia).
   `tools/neural-music-fp`에 클론하고 커밋을 고정합니다.
2. `download-models.sh`로 `nmfp-triplet`을 받습니다.
3. `nmfp/export_onnx.py`: SavedModel을 ONNX로 내보내고 TF와 수치 일치를 검증합니다.
4. `poc/neural.py`: 디코드(8 kHz) → 멜 → onnxruntime 임베딩 → 검색 → 정렬 → `Match`.
5. `poc/build_neural_index.py`: 라이브러리 전체를 처리해서 `db/neural/emb.f16.npy`와
   `db/neural/segments.npy`(track_id, t)를 만듭니다. 재시작 가능해야 합니다. 예상 시간은 디코딩 위주로
   30–60분입니다(Panako 인덱스가 8스레드 43분). 측정해서 기록합니다.
6. `poc/evaluate.py --method neural`로 §6 평가를 돌립니다.

**결정 기준:** `crowd0_room`이 Panako 0%보다 의미 있게 높고(≥ 30%) holdout 오확정이 0이면 Phase 1로
갑니다. 거의 0이면 원인을 먼저 진단합니다(8 kHz 대역, 1 s 문맥, 크럼프 도메인).

### Phase 1 — 우리 데이터로 도메인 적응 미세조정
1. `nmfp/make_train_chunks.py`: train 그룹에서 8 kHz 30 s 청크를 뽑습니다. 논문은 1만 개를 썼고,
   우리는 곡당 2–3개로 약 1만 개입니다.
2. 증강 자산을 받습니다(§4.2): IR, FSD50K 관중, MUSAN/LibriSpeech. `nmfp/assets/`에 두고 출처와
   라이선스 목록을 남깁니다.
3. `configs/krump-triplet.yaml`: 사전학습 가중치에서 시작합니다.
   - 학습률은 논문 값의 1/10입니다.
   - 12 GB에서 배치 1,536이 안 들어가면 anchor 256 × 2로 줄입니다. semi-hard mining 품질이 배치
     크기에 의존하므로 크기별 결과를 기록합니다.
   - 같은 녹음 그룹을 negative에서 빼는 샘플러를 넣습니다(§4.3).
4. 학습합니다. 논문은 4090에서 100 epoch에 약 15시간이었고, 미세조정은 20–30 epoch로 시작합니다.
5. ONNX로 다시 내보내고 인덱스를 다시 만들고 평가합니다.

### Phase 2 — 서버 통합
1. `server/app.py`: 시작할 때 ONNX 세션과 임베딩 행렬을 로드합니다. `/identify`에서 Panako와 NMFP를
   스레드 두 개로 동시에 돌리고 §3 표대로 합칩니다. `method`에 `fingerprint`/`neural`/`both`를 기록합니다.
2. 팝업에 `method=neural`을 표시합니다. 위치 재생(▶ 해당 위치 듣기)은 `match_start`로 그대로 동작합니다.
3. partial(녹음 중간 확인) 요청에도 NMFP를 씁니다. 짧은 쿼리에 강한 것이 이 방식의 장점입니다
   (논문: 2 s 쿼리 실환경 75%).
4. README에 `build_neural_index.py` 재실행 안내를 넣습니다. 음악을 추가할 때 Panako, rhythm, neural
   세 가지를 다시 만들어야 합니다.

## 6. 평가 계획

| 세트 | 내용 | 지표 |
|---|---|---|
| 합성 | `poc/testset` 6개 조건 + `poc/diag`(room_drr±3/−10, crowd−3, crowd3_room−3) | top-1, 확정 비율, 오확정 |
| index-only | 학습에 안 쓴 곡으로 만든 같은 조건 | 학습곡과의 차이 = 과적합 정도 |
| holdout | 109 파일, crowd3 + crowd0_room | 오확정 (목표 0) |
| 실제 | 라벨된 배틀 윈도우 + `recordings/log.csv`의 ✔/✘ | 확정/추가 확인/실패 |
| 교차곡 | 공유 루프로 다른 곡이 1위나 경쟁자로 나온 횟수 | `RIVAL_RATIO` 보정 |

**선행 작업: 실제 라벨을 늘립니다.** 지금 실패 라벨은 3곡뿐이라 비교 근거가 약합니다.
- `realtest/labels.csv`(video, t_start, t_end, track_path)를 만듭니다.
- [평가](evaluation.md) "미확인 후보"의 리듬 후보(EBS 2026 final → Proof the Xrook 등)를 확인합니다.
- 확장 프로그램의 ✔/✘ 피드백을 쌓습니다.

비교 기준선은 같은 세트에서의 Panako, rhythm, Panako + rhythm(현재 운영 구조)입니다.

## 7. 위험과 대응

| 위험 | 대응 |
|---|---|
| ONNX 내보내기 실패 (멜 레이어) | numpy 멜 재구현 + 수치 검증. 안 되면 WSL2 마이크로서비스(:8766)로 서빙 |
| 8 kHz라 4 kHz 이상 정보 손실 | 잔향 조건에서는 고역이 먼저 죽어서 영향이 적을 것으로 예상. 깨끗한 조건은 Panako가 맡음 |
| 템포 변화에 약함 (증강 없음) | Panako 병행. 필요하면 Phase 1에 ±3% 리샘플 증강 추가 |
| 공유 루프로 교차곡 고점수 | 정렬 세그먼트 수 하한(예: ≥ 6 s 연속) + 별도 `RIVAL_RATIO` 보정 |
| unknown 곡 오확정 | holdout으로 임계값 보정, 목표 0건 유지 |
| AGPL 가중치 | 개인 로컬 사용으로 한정. 외부 공개 시 재검토 |
| 실제 라벨 부족 | §6 선행 작업 |

## 8. 산출물과 파일

| 경로 | 내용 |
|---|---|
| `tools/neural-music-fp/` | 공식 코드 (커밋 고정) |
| `nmfp/` | WSL2 쪽: 학습 청크 생성, 설정, 증강 자산 목록, ONNX 내보내기 |
| `db/neural/model.onnx` | 서빙 모델 |
| `db/neural/emb.f16.npy`, `segments.npy` | 라이브러리 임베딩, (track_id, t) |
| `poc/neural.py` | 임베딩·검색·정렬 → `Match` |
| `poc/build_neural_index.py` | 인덱스 빌드 (재시작 가능) |
| `poc/evaluate.py` | `--method panako|neural|both` |
| `server/app.py` | 병렬 실행 + 판정 병합 |
