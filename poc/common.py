"""Shared paths and Panako helpers for the fingerprinting PoC."""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

# Korean Windows consoles default to cp949, which can't print every track name.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT / "music"
DB_DIR = ROOT / "db" / "panako"
HOLDOUT_FILE = ROOT / "db" / "holdout.txt"
PANAKO_JAR = ROOT / "tools" / "Panako" / "build" / "libs" / "panako-2.1-all.jar"
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


def _find_java() -> str:
    hits = sorted(glob.glob(r"C:\Program Files\Eclipse Adoptium\jdk-17*\bin\java.exe"))
    return hits[-1] if hits else "java"


def _find_ffmpeg_dir() -> str | None:
    pattern = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe"
    )
    hits = sorted(glob.glob(pattern))
    return str(Path(hits[-1]).parent) if hits else None


JAVA = _find_java()
FFMPEG_DIR = _find_ffmpeg_dir()
FFMPEG = str(Path(FFMPEG_DIR) / "ffmpeg.exe") if FFMPEG_DIR else "ffmpeg"


def env() -> dict:
    e = os.environ.copy()
    if FFMPEG_DIR:
        e["PATH"] = FFMPEG_DIR + os.pathsep + e["PATH"]
    return e


EXCLUDE_FILE = ROOT / "db" / "exclude.txt"


def load_excluded() -> set[str]:
    """Normcased absolute paths of library files that must never be reported (db/exclude.txt)."""
    if not EXCLUDE_FILE.exists():
        return set()
    lines = EXCLUDE_FILE.read_text(encoding="utf-8").splitlines()
    return {os.path.normcase(str(MUSIC_DIR / l.strip())) for l in lines if l.strip() and not l.startswith("#")}


def list_audio(folder: Path = MUSIC_DIR) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXTS and p.is_file())


def _panako_cmd(command: str, db_dir: Path, threads: int, map_gb: int, extra_config: dict | None) -> list[str]:
    config = {
        "STRATEGY": "PANAKO",
        "DECODER_PIPE_ENVIRONMENT": "cmd.exe",
        "DECODER_PIPE_ENVIRONMENT_ARG": "/C",
        "PANAKO_LMDB_FOLDER": str(db_dir / "db"),
        "PANAKO_CACHE_FOLDER": str(db_dir / "cache"),
        "AVAILABLE_PROCESSORS": str(threads),
        "NUMBER_OF_QUERY_RESULTS": "5",
        # Cached prints are looked up by resource id; never let a query clip reuse them.
        **({"PANAKO_USE_CACHED_PRINTS": "FALSE"} if command in ("query", "queryServer") else {}),
        **(extra_config or {}),
    }
    return [
        JAVA,
        f"-Dpanako.lmdb.mapsize.gb={map_gb}",
        "-Dfile.encoding=UTF-8", "-Dsun.stdout.encoding=UTF-8", "-Dsun.stderr.encoding=UTF-8",
        "--add-opens", "java.base/java.nio=ALL-UNNAMED",
        "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
        "-jar", str(PANAKO_JAR), command,
        *[f"{k}={v}" for k, v in config.items()],
    ]


def panako(command: str, args: list[str], db_dir: Path = DB_DIR, threads: int = 1,
           map_gb: int = 32, extra_config: dict | None = None) -> subprocess.CompletedProcess:
    # Java 17 converts command-line arguments through the ANSI code page (cp949), which
    # turns characters like 'ö', '★' or Japanese into '?'. Panako also accepts a .txt file
    # listing one path per line, so pass files that way, read as UTF-8.
    list_file = None
    if args:
        fd, list_file = tempfile.mkstemp(suffix=".txt", prefix="panako_args_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(args))
        args = [list_file]
    cmd = [*_panako_cmd(command, db_dir, threads, map_gb, extra_config), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env())
    finally:
        if list_file:
            os.remove(list_file)


class PanakoServer:
    """One long-running Panako JVM (the patched `queryServer` command) shared by all callers.

    Starting a JVM and opening the storage cost most of a one-shot `query` (~2.5 s); the server
    pays that once. `query()` is thread-safe: requests are tagged with an id, run concurrently on
    `threads` Java threads, and their result lines are routed back by id. The JVM is restarted if
    it dies.
    """

    def __init__(self, db_dir: Path = DB_DIR, threads: int = 4, map_gb: int = 32,
                 extra_config: dict | None = None, log_path: Path | None = None):
        self._cmd = _panako_cmd("queryServer", db_dir, threads, map_gb, extra_config)
        self._log_path = log_path or (ROOT / "panako_server.log")
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[list[str], threading.Event]] = {}
        self._next_id = 0
        self.proc = None
        self._start()

    def _start(self) -> None:
        log = open(self._log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                     text=True, encoding="utf-8", errors="replace", env=env(), bufsize=1)
        for line in self.proc.stdout:  # wait until the storage is open
            if line.strip() == "READY":
                break
        else:
            raise RuntimeError(f"Panako query server did not start, see {self._log_path}")
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            head, _, rest = line.partition(";")
            with self._lock:
                entry = self._pending.get(head.strip())
            if entry is None:
                continue
            if rest.strip() == "END":
                entry[1].set()
            elif rest.strip().startswith("ERROR"):
                entry[0].append(rest.strip())
            else:
                entry[0].append(line)
        with self._lock:  # the JVM exited: fail everything still waiting
            for lines, done in self._pending.values():
                lines.append("ERROR server exited")
                done.set()

    def query(self, path: str | Path, timeout: float = 120) -> list[Match]:
        with self._lock:
            if self.proc.poll() is not None:
                self._start()
            rid = str(self._next_id)
            self._next_id += 1
            entry = ([], threading.Event())
            self._pending[rid] = entry
            self.proc.stdin.write(f"{rid}\t{path}\n")
            self.proc.stdin.flush()
        try:
            if not entry[1].wait(timeout):
                raise TimeoutError(f"Panako query timed out: {path}")
        finally:
            with self._lock:
                self._pending.pop(rid, None)
        errors = [l for l in entry[0] if l.startswith("ERROR")]
        if errors:
            raise RuntimeError(f"Panako query failed: {errors[0].strip()}")
        return next(iter(parse_query_output("".join(entry[0])).values()), [])

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
            self.proc.wait(10)


def _murmur3_x86_32(data: bytes, seed: int = 0) -> int:
    c1, c2, m = 0xCC9E2D51, 0x1B873593, 0xFFFFFFFF
    h = seed
    n = len(data) // 4 * 4
    for i in range(0, n, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & m
        k = ((k << 15) | (k >> 17)) & m
        k = (k * c2) & m
        h ^= k
        h = ((h << 13) | (h >> 19)) & m
        h = (h * 5 + 0xE6546B64) & m
    k = 0
    tail = data[n:]
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if tail:
        k ^= tail[0]
        k = (k * c1) & m
        k = ((k << 15) | (k >> 17)) & m
        k = (k * c2) & m
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & m
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & m
    h ^= h >> 16
    return h - (1 << 32) if h & 0x80000000 else h  # Java int


def panako_id(path: str | Path) -> int:
    """Panako's resource identifier: content hash of 64 KB from the middle of the file.
    Byte-identical copies in different albums share an id (stored only once)."""
    block = 8 * 1024
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek((size // 2) // block * block)
        data = fh.read(block * 8)
    h = _murmur3_x86_32(data)
    if h == 0:
        raise ValueError(f"zero hash for {path}")
    # Java: Integer.MAX_VALUE / 2 + hash / 2 (integer division truncates toward zero)
    return (2**31 - 1) // 2 + int(h / 2)


@dataclass
class Match:
    query_path: str
    query_start: float
    query_stop: float
    match_path: str
    match_id: int
    match_start: float
    match_stop: float
    score: int
    time_factor: float
    freq_factor: float
    seconds_with_match: float


def _pct(s: str) -> float:
    return float(s.replace("%", "").strip())


def parse_query_output(stdout: str) -> dict[str, list[Match]]:
    """Map query path -> matches (sorted by score desc). Queries without a match map to []."""
    results: dict[str, list[Match]] = {}
    for line in stdout.splitlines():
        raw = line.split(";")
        if len(raw) < 13 or not raw[0].strip().isdigit():
            continue
        # Query paths are ours (no ';'), but library file names may contain ';',
        # so take the fixed columns from both ends and join the middle as the match path.
        head = [c.strip() for c in raw[:5]]   # idx, total, qpath, qstart, qstop
        tail = [c.strip() for c in raw[-7:]]  # id, start, stop, score, time%, freq%, sec%
        ref_path = ";".join(raw[5:-7]).strip()
        qpath = os.path.normcase(os.path.abspath(head[2]))
        bucket = results.setdefault(qpath, [])
        if not ref_path or ref_path.lower() == "null":
            continue
        try:
            score = int(float(tail[3]))
        except ValueError:
            continue
        if score <= 0:
            continue
        bucket.append(Match(
            query_path=qpath, query_start=float(head[3]), query_stop=float(head[4]),
            match_path=os.path.normcase(os.path.abspath(ref_path)),
            match_id=int(tail[0]), match_start=float(tail[1]), match_stop=float(tail[2]), score=score,
            time_factor=_pct(tail[4]), freq_factor=_pct(tail[5]),
            seconds_with_match=_pct(tail[6]),
        ))
    for matches in results.values():
        matches.sort(key=lambda m: m.score, reverse=True)
    return results
