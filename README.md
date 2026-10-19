# music-analyzer

<img src="docs/logo.png" alt="Krump Track Finder logo" width="160">

Identify which track from the local library (`music/`) is playing in a browser, even with
crowd noise (cheering, clapping, MC). Approach: audio fingerprinting with
[Panako](https://github.com/JorenSix/Panako), validated first as an offline PoC on WAV clips
before building the Chrome extension.

Start with [`docs/overview.md`](docs/overview.md) (purpose, architecture, technical background). This README
covers setup, building the indexes and running; the rest is under [Documentation](#documentation).

## Layout

| Path | What |
|---|---|
| `music/` | Copy of `N:\개인\Krump Track & Albums` (audio only) |
| `tools/Panako/` | Panako 2.1 source checkout (not in git) |
| `patches/panako.patch` | Our changes to Panako ([`docs/search-pipeline.md`](docs/search-pipeline.md) §2.1) |
| `db/panako/` | Fingerprint DB (LMDB) + per-track print cache |
| `db/pair.npz` | Pair-hash index, the second fingerprint stage (`poc/pairfp.py`) |
| `db/rhythm.npz`, `db/rhythm_cohort.npz` | Rhythm fallback index and per-track score stats |
| `db/same_audio.csv` | Library files that contain the same recording |
| `db/holdout.txt` | ~2% of tracks never indexed, used to measure false positives |
| `poc/` | Indexing, evaluation and matching code (shared by the server) |
| `server/` | FastAPI backend for the extension; `server/deploy/` builds and deploys it to AWS Lambda |
| `extension/` | Chrome extension (Manifest V3) |
| `tests/` | Unit tests (`server/videos.py`, `extension/video.js`) and checks against a running server |
| `realtest/` | Real-video and battle-100 test results |
| `store/` | Chrome Web Store package script, listing text and assets |
| `docs/` | Design and reference documents (see [Documentation](#documentation)) |

## Requirements

- Eclipse Temurin JDK 17 and FFmpeg (installed via winget)
- Python 3.12. The server needs `pip install -r server/deploy/requirements.txt`; the album list
  also needs `mutagen` and `pillow`

Panako is not vendored. Clone upstream at the base commit and apply our patch
(see [`docs/search-pipeline.md`](docs/search-pipeline.md) §2.1), then build (needs `JAVA_HOME` pointing at JDK 17):

```bash
git clone https://github.com/JorenSix/Panako.git tools/Panako
git -C tools/Panako checkout f1248f7
git -C tools/Panako apply ../../patches/panako.patch
cd tools/Panako && gradlew.bat shadowJar
```

Not in git (see `.gitignore`): `music/`, the generated DBs (`db/panako/`, `db/pair.npz`, `db/rhythm*.npz`),
the album list (`db/albums.json`, `db/covers/`), the store covers (`db/store_covers/`), `recordings/`,
downloaded video audio and generated test clips; see "Building the indexes" below. `db/same_audio.csv` and
`db/holdout.txt` are committed because they are small and slow to rebuild.

## Building the indexes

Run after cloning and again after adding music. All write under `db/`.

```bash
python poc/build_index.py --threads 8      # fingerprint DB, db/panako/ (resumable, ~45 min)
python poc/pairfp.py build                 # pair index, db/pair.npz (~20 min)
python poc/build_rhythm_index.py           # rhythm index, db/rhythm.npz (~15 min)
python poc/rhythm_cohort.py                # rhythm cohort, db/rhythm_cohort.npz
python poc/rhythm_cohort.py --wide         # cohort of the 0.95-1.05 stage, db/rhythm_cohort_wide.npz
python poc/find_duplicates.py              # same-recording links, db/same_audio.csv (~50 min)
python server/build_albums.py              # album list and covers, db/albums.json + db/covers/
python server/fetch_store_covers.py        # store covers, db/store_covers/ (from db/album_links.json)
```

The deployed server also needs the preview MP3s and the new data staged and uploaded; the steps are in
[`docs/lambda-deployment.md`](docs/lambda-deployment.md) ("배포·갱신 방법").

`poc/panako.sh` is a thin wrapper for running Panako commands by hand from Git Bash.

## Running

1. Server: the extension uses the deployed AWS Lambda ([`docs/lambda-deployment.md`](docs/lambda-deployment.md)).
   To run it locally instead: `server\start.bat` or `python server/app.py` (http://127.0.0.1:8765), then point
   `extension/config.js` at it. That file (not in git) holds the endpoint and API key and is written by
   `python server/deploy/aws_setup.py function`.
2. Chrome → `chrome://extensions` → enable *Developer mode* → *Load unpacked* → pick `extension/`.
   Pin the extension.
3. On a tab playing a video, click **현재 탭에서 찾기**. It listens for up to 30 s; after 못 찾음 the retry
   can listen for 45 s or 1 min.
   How the extension behaves: [`docs/extension.md`](docs/extension.md).

Deploying the server: only from `main` as pushed to origin (`CLAUDE.md`, [`docs/lambda-deployment.md`](docs/lambda-deployment.md)).
Search accuracy tests and measurements: [`docs/evaluation.md`](docs/evaluation.md).

## Tests

No packages beyond the server's requirements; Node is optional (development only).

```bash
python -m unittest discover -s tests       # server/videos.py: video keys, sharing rules, stores
node --test "tests/*.test.mjs"             # extension/video.js (Node 22; quote the glob, a bare folder does not work)
```

`tests/check_api.py` checks a running server: the requests of earlier extension versions get the same answers
(`--baseline` before a server change, `--compare` after), the per-video records contract (`--contract`, local
server only) and a deployed server without storing anything (`--no-store`). It cuts its clips from the
battle-100 audio (`realtest/battle100/audio/`, not in git). Run the local server with `KF_TMP` and
`KF_VIDEO_DIR` pointing at a scratch folder so `recordings/` is not touched; details in
[`docs/video-records.md`](docs/video-records.md) §6.

## Documentation

| Document | Contents |
|---|---|
| [`docs/overview.md`](docs/overview.md) | Purpose, architecture, one search end to end, technical background, data, doc map |
| [`docs/search-pipeline.md`](docs/search-pipeline.md) | Search stages (Panako, pair stage, rhythm search), grouping and verdicts, thresholds and their calibration, Panako patches, server performance |
| [`docs/extension.md`](docs/extension.md) | Extension files, recording flow, popup screens, library page, consent and stored recordings, Web Store releases |
| [`docs/evaluation.md`](docs/evaluation.md) | Test sets and tools, PoC results, real-video test, unverified candidates |
| [`docs/lambda-deployment.md`](docs/lambda-deployment.md) | AWS Lambda: cost, deployed resources, deploy steps, cold starts |
| [`docs/video-records.md`](docs/video-records.md) | Per-video records: video keys, event format, sharing rules, `GET /video`, tests |
| [`docs/album-links.md`](docs/album-links.md) | Store links and covers (`db/album_links.json`) |
| [`docs/privacy-policy.md`](docs/privacy-policy.md) | Privacy policy |
| [`docs/review-shazam-noise-robustness.md`](docs/review-shazam-noise-robustness.md), [`docs/design-a-nmfp.md`](docs/design-a-nmfp.md), [`docs/design-b-muq.md`](docs/design-b-muq.md) | Fingerprint review and neural fingerprint proposals |
