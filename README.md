# music-analyzer

Identify which track from the local library (`music/`) is playing in a browser, even with
crowd noise (cheering, clapping, MC). Approach: audio fingerprinting with
[Panako](https://github.com/JorenSix/Panako), validated first as an offline PoC on WAV clips
before building the Chrome extension.

## Layout

| Path | What |
|---|---|
| `music/` | Copy of `N:\개인\Krump Track & Albums` (audio only) |
| `tools/Panako/` | Panako 2.1 source checkout (not in git) |
| `patches/panako.patch` | Our changes to Panako (see below) |
| `db/panako/` | Fingerprint DB (LMDB) + per-track print cache |
| `db/rhythm.npz`, `db/rhythm_cohort.npz` | Rhythm fallback index and per-track score stats |
| `db/same_audio.csv` | Library files that contain the same recording |
| `db/holdout.txt` | ~2% of tracks never indexed, used to measure false positives |
| `poc/` | Indexing, evaluation and matching code (shared by the server) |
| `server/` | FastAPI backend for the extension; `server/deploy/` builds and deploys it to AWS Lambda |
| `extension/` | Chrome extension (Manifest V3) |
| `realtest/` | Real-video test results (CSV) |

## Requirements

- Eclipse Temurin JDK 17 and FFmpeg (installed via winget)
- Python 3.12 with numpy

Panako is not vendored. Clone upstream at the base commit and apply our patch
(see "Panako patches" below), then build (needs `JAVA_HOME` pointing at JDK 17):

```bash
git clone https://github.com/JorenSix/Panako.git tools/Panako
git -C tools/Panako checkout f1248f7
git -C tools/Panako apply ../../patches/panako.patch
cd tools/Panako && gradlew.bat shadowJar
```

Not in git (see `.gitignore`): `music/`, the generated DBs (`db/panako/`, `db/rhythm*.npz`;
rebuild with `build_index.py`, `build_rhythm_index.py`, `rhythm_cohort.py`), the album list
(`db/albums.json`, `db/covers/`; `server/build_albums.py`), `recordings/`,
downloaded video audio and generated test clips. `db/same_audio.csv` and `db/holdout.txt` are
committed because they are small and slow to rebuild.

## PoC workflow

```bash
python poc/build_index.py --threads 8      # fingerprint the library (resumable)
python poc/make_testset.py --n 100         # synthetic noisy 30 s clips + manifest.csv
python poc/evaluate.py --threads 8         # metrics -> poc/report.txt, poc/results.csv
```

To test real recordings, put WAVs anywhere and write a manifest CSV with columns
`clip,condition,expected,offset` (`expected` = full path of the library file, or empty if the
song is not in the library), then `python poc/evaluate.py --manifest your.csv`.

`poc/panako.sh` is a thin wrapper for running Panako commands by hand from Git Bash.

## Browser extension

1. Server: either the deployed AWS Lambda (see `docs/lambda-deployment.md`) or a local one —
   double-click `server\start.bat` (or `python server/app.py`), listening on
   `http://127.0.0.1:8765`. Needs `pip install fastapi uvicorn[standard]`.
2. Chrome → `chrome://extensions` → enable *Developer mode* → *Load unpacked* → pick `extension/`.
   Pin the extension. The default server is the deployed Lambda; enter the API key
   (`build/api_key.txt`, shared separately) in the popup's **서버 설정** (it opens by itself
   until a key is saved). For a local server enter `http://127.0.0.1:8765` and no key.
3. On a tab playing a video, click the extension icon → **현재 탭에서 찾기**. The popup can be
   closed while recording; the badge shows REC → ... → OK / ? / X.
4. The result shows the best song only, with its score and position in the library track.
   **▶ 10초 미리듣기** plays a 10 s MP3 excerpt from that spot, cut by the server's `/preview`.
   Full library files are never served, because of copyright once this is distributed.
   **✔ 맞음 / ✘ 틀림** records ground truth.
5. **라이브러리** (popup header) opens a tab listing the library's albums: cover, title, artist,
   track count; click an album for its tracks, search by album, artist or track name. The list
   comes from the server's `/albums` (cached in the extension, so it shows at once next time).
   Rebuild it after adding music: `python server/build_albums.py` → `db/albums.json` +
   `db/covers/` (needs `pip install mutagen pillow`). An album is a top-level folder, named by
   the files' album / album-artist tags or else the folder name ("Artist - Album"); a folder of
   loose tracks from several albums (`트랙리스트`) is split by album tag. Covers are the embedded
   pictures (287 of 319 albums), as 120 px JPEGs.

Candidates are grouped into songs (`poc/grouping.py`):
- Files containing the same recording (other album copy, a session mix or judge-showcase
  recording that includes it) are shown as one song with "같은 음원" listed. Links come from
  `python poc/find_duplicates.py` → `db/same_audio.csv` (every track queried against the DB
  with its cached prints, ~50 min; re-run after adding music). A link needs score ≥ 30 over
  ≥ 15 s; shorter/weaker cross-track hits are shared drum loops and samples, common in krump.
- Preview/promo/teaser files (27, each 58–99% covered by the album's own tracks) are hidden
  behind the real track; a song found only in a preview is flagged.
- Verdict: 확정 when the best song scores ≥ 10 and no *different* song reaches 60% of its
  score; otherwise 추가 확인 (low score or close rival); below 5 → 실패. On the synthetic set
  this gives 0 confirmed-but-wrong answers (crowd 3 dB: 85 confirmed, 5 check, 10 fail).

Flow: popup gets a tab-capture stream id → background service worker → offscreen document
records with `MediaRecorder` (Opus) while playing the audio back to the speakers →
`POST /identify` → server converts with FFmpeg and runs a Panako query (~2.5 s, mostly JVM
start-up).

On Lambda, final recordings go to S3 instead (`recordings/<id>.wav` + `<id>.json` in the
bucket). Locally, every recording is kept in `recordings/` and logged in `recordings/log.csv`, which is a valid
evaluate.py manifest (rows without feedback are skipped):
`python poc/evaluate.py --manifest recordings/log.csv`.

## PoC results (2026-10-13, synthetic degradations)

DB: 4,132 unique tracks (4,210 files; 78 byte-identical copies share an id), ~244 h of audio,
27.6 M fingerprints, built in 43 min with 8 threads. Query: 0.24 s per clip (8 threads, amortized).

Accept rule `score >= 10`, 100 random in-DB tracks per condition, 30 s clips:

| Condition | Identified |
|---|---|
| clean | 100% |
| pink noise 10 dB SNR | 99% |
| crowd (babble + claps + cheers) 3 dB | 83% |
| speed +3% + crowd 6 dB | 82% |
| speed −3% + crowd 6 dB | 85% |
| crowd 0 dB + heavy room reverb | 0% |

- Not-in-DB tracks (39, crowd 3 dB): **0 real false positives**. All 5 reported hits were
  the same song stored as another file (other master/album, a session mix or preview
  medley containing it).
- Misidentifications across ~600 in-DB queries: 1 (score 35). 8 other "wrong" hits were
  the same song under a different file.
- Reverb diagnosis (25 tracks): reverb alone with direct > reverberant (DRR +3 dB) 25/25;
  DRR −3 dB 13/25; crowd 3 dB + DRR −3 dB 2/25. Crowd alone: 0 dB 18/25, −3 dB 7/25.
  **Reverberant room recordings are the main weakness**, not crowd noise.
- Requiring 2/3 of the 15 s segments to agree cost ~17 points on noisy conditions with no
  precision gain, so it is reported but not required.

Next: validate with real browser recordings (event videos), then decide whether a
reverb-robust second stage (e.g. chroma/beat alignment over the top-N Panako candidates,
or source separation before querying) is needed.

## Server performance (2026-10-14, i7-13700F, Windows)

**Pipeline.**
- One Panako JVM (`queryServer`, 8 threads) is shared by all requests.
- Each request converts the capture once with ffmpeg to 16 kHz mono WAV. Panako reads that WAV
  directly, and the rhythm search resamples it in numpy.
- Requests run on FastAPI's thread pool. At most `RHYTHM_WORKERS` (default 3) rhythm searches
  run at once, since each holds ~0.5 GB of temporaries.

Panako query of a 30 s clip (`python poc/bench_panako_server.py`). Results are identical to
the one-shot `query` command (8/8), and non-ASCII paths work.

| | per query | concurrent |
|---|---|---|
| one-shot `panako query` | 3.56 s | — |
| query server, ffmpeg pipe | 2.02 s | — |
| query server, direct WAV | **0.32 s** | 20 queries/s with 8 callers |

End to end through the HTTP server (`python server/loadtest.py <webm>... --cleanup`):

| Requests | Single request | 8 concurrent clients |
|---|---|---|
| fingerprint match | 1.2 s (0.8 s of it is starting ffmpeg) | 3.5 req/s, median 1.9 s |
| rhythm fallback | 3.3–4.6 s (was 7–9 s) | mixed set: 1.5 req/s |

On Linux, process start-up is milliseconds, so the ffmpeg share should mostly disappear.

## Rhythm fallback (reverberant room recordings)

**When it runs.** If Panako finds nothing, the server runs a second search on rhythm
(`poc/rhythm.py`). Hall/camera recordings smear the spectral peaks Panako uses, but the timing
of the drum hits survives.

**How it works.**
- Each track is reduced to band-wise onset envelopes at ~43 frames/s, using 150–5500 Hz
  because the low end is lost in such recordings.
- A query is cross-correlated against the whole library at speeds 0.98–1.02.
- The library side is precomputed as overlap-save FFT chunks.
- Build: `python poc/build_rhythm_index.py` → `db/rhythm.npz` (~15 min). Re-run after adding
  music.
- A search takes ~5–10 s.

**Results.**
- Synthetic crowd 0 dB + heavy reverb clips, where Panako finds 0–3%: top-1 correct 71/100.
- Real case: in the WWB 9 judge showcase (Lil ROXXDAMUS - DARK, played at speed 1.000 with DJ
  jumps), Panako scored 0. Rhythm put DARK first in 6 of 7 windows, at 2–2.5× the next song.
  The user's own 30 s extension capture also comes back as DARK at 0:40.

**Loop tracks.** Plain drum loops ("bvckloop" tracks) and long mixes correlate with almost any
krump beat and win searches they have nothing to do with. Two fixes:
- `db/exclude.txt` lists files never to report. It holds six 30+ minute DJ/session mixes and
  live shows: two PLAYA session mixes (99%/81% covered by individual tracks), three Ted mixes
  and a live show (0% krump library content, except Ted's Krump Mix at 49%). Delete a line to
  bring a file back; no rebuild is needed.
- `python poc/rhythm_cohort.py` → `db/rhythm_cohort.npz` stores each track's usual score
  against audio that is not in the library (252 impostor queries from held-out tracks). The
  run uses the evaluation's holdout tracks, re-derived with `make_testset.py`'s seed.

Ranking stays on the raw score; z = (score − track mean) / track std alone was worse
(room clips 64 vs 73 top-1). "Agree" means the raw winner is also the z winner.

**When a candidate is shown** (calibrated on `realtest/rhythm_z.jsonl`):

| Level | Rule | Room clips (100) | Unknown songs (39) |
|---|---|---|---|
| 유력 후보 | ratio to best different song ≥ 1.2 and agree | 43 shown, 42 right | 3 |
| 약한 후보 | ratio ≥ 1.3, or agree and z ≥ 4 | 54 shown, 52 right (both levels) | 10 (both levels) |

User captures that Panako missed: DARK, Afro Krump and one KILL EM come back 유력; another
KILL EM comes back 약한. The fifth, a recording across a DJ jump-back, still fails.

Rhythm results are never 확정, only 추가 확인 with a "listen to check" prompt.

**During recording** (`poc/eval_rhythm_prefix.py` → `realtest/rhythm_prefix.jsonl`). Real hall
recordings almost never confirm by fingerprint, so mid-recording checks also run the rhythm
search from 15 s on.

| Prefix | Room clips: 유력 shown / right | Unknown songs: 유력 |
|---|---|---|
| 15 s | 5 / 4 | 4 |
| 20 s | 13 / 13 | 3 |
| 25 s | 26 / 26 | 3 |
| 30 s | 43 / 42 | 3 |

Stopping at the first 유력 at ≥ 20 s gives the same result as waiting for 30 s (room clips
43/42, DARK video 6/6, user captures 3/3, unknown songs 3/39), with a median stop at 25 s.
So a 유력 candidate at ≥ 20 s ends the recording, and from 15 s a 유력 candidate is shown only
as a hint. 약한 candidates are never shown mid-recording, because they appeared for 6–10 of
39 unknown songs.

Simulated through the extension against the server: DARK shows a hint at 15 s and the result
at 26 s (was 33 s). A clean clip is confirmed at 11 s. An unknown song gets no hint and fails
at the end.

**Battle videos.** The rhythm search also proposes consistent matches in battle videos that
Panako missed. The same track shows up over several windows, with track positions advancing
30 s. Examples:
- Rise Against 3-way → LUKE "KILL EM"
- EBS 2026 final → Proof the Xrook "Master Of The Goon Talk Vol. Z - 09"

The user confirmed KILL EM by recording that video with the extension. The others are
unverified.

## Real video test (2026-10-13)

`python poc/real_video_test.py <audio dir>` queries 30 s windows every 15 s along whole videos
and calls a match *consistent* when the neighbouring window hits the same track at the matching
position. Results per window: `realtest/windows_battles.csv`, `realtest/windows_known.csv`.

| Videos | Windows | Result |
|---|---|---|
| 3 track uploads on YouTube (Saturation, Morf Anthem, Scripterz vs Bullzeyex) | 39 | 39 correct |
| Krump session filmed live (Morf Anthem Part 1) | 4 | 4 correct |
| Dance video, I Am Everything (Playa x Kid NY) | 4 | 2 weak hits (score 11–14) |
| Dance video, This Is Nordside (Mozarf) | 6 | 0 (no trace even reverse-querying; likely another version/edit) |
| 11 battles (EBS 2024–26, Rise Against 2025, Illest 2K25…) + 4 videos with unknown songs | ~240 | 0 matches |

- No false identification in any real window.
- The battle videos found nothing with Panako, not even weak candidates. I first guessed that
  the DJs' tracks were not in the library. **That was wrong.** Every failing case the user
  labelled was in the library and played at speed 1.000:
  - WWB 9 → DARK
  - Rise Against 3-way → KILL EM
  - PS Buck Vol.4 → Afro Krump

  Hall reverb, crowd and MC smear the spectral peaks Panako needs. That degradation is the main
  failure cause, and the popup's failure message now says so first.
- Widening the speed search to ±25% only added random matches (score 6–13), so it stays at ±10%.
- Two "wrong" answers in the PoC were really the same recording: 나이키.mp3 = MOZARF
  "gon giv it to yall".

## Panako patches

Found while getting the PoC running on Windows with this library:

1. **Robust time alignment** (`PanakoStrategy`, `PANAKO_ROBUST_ALIGN=TRUE`, default on).
   Upstream estimates the query/reference speed ratio from only the first and last hit
   parts. With loop-based music plus noise it often picks an off-loop offset, the estimated
   speed falls outside 0.8–1.2, and the whole match is dropped (score ~700 or nothing).
   Replaced with a Hough-style search over all hits. On the smoke test (3 tracks × white/
   pink/crowd noise × 5–30 dB SNR) identification went from 18/45 to 45/45.
2. **Thread safety**: per-thread query/store queues lived in a plain `HashMap`
   (lost entries under concurrency) → `ConcurrentHashMap`. LMDB `maxReaders` equalled the
   thread count → raised. Exceptions in query worker threads were swallowed silently → logged.
3. **Windows**: LMDB preallocates its full map size on disk; the hard-coded 1 TB is now
   `-Dpanako.lmdb.mapsize.gb` (default still 1024; scripts use 32). The pipe decoder was
   created before CLI config arguments were applied, so `DECODER_PIPE_ENVIRONMENT=cmd.exe`
   was ignored.
4. **Non-ASCII paths** (not a source patch): Java 17 turns command-line arguments outside
   cp949 (`ö`, `★`, Japanese) into `?`, so ~120 tracks were skipped. `poc/common.py`
   passes file lists through a UTF-8 `.txt` file instead.
5. **Identifiers**: purely numeric file names (`01.mp3`) were used as the track id, so
   same-named files in different albums collided. Now content hash unless
   `-Dpanako.numeric.ids=true`.
6. **Query server** (`cli/QueryServer.java`, command `queryServer`): a long-running process that
   keeps the JVM and LMDB open and answers `id<TAB>path` lines from stdin on a thread pool. The
   Python client is `PanakoServer` in `poc/common.py`.
7. **Direct WAV input**: a mono 16-bit WAV already at 16 kHz is read with Java Sound instead of
   the ffmpeg pipe decoder. Each process start costs ~0.8 s on this Windows machine
   (on-access scanning), and the pipe decoder starts both `cmd.exe` and `ffmpeg`.
8. **Read-only DB** (`-Dpanako.lmdb.readonly=true`): opens LMDB with `MDB_RDONLY` +
   `MDB_NOLOCK` and without creating databases, for read-only file systems (AWS Lambda).
