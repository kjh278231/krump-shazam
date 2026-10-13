#!/usr/bin/env bash
# Panako wrapper for Windows (Git Bash).
# Usage: poc/panako.sh <store|query|stats|...> [KEY=VALUE ...] files...
# Env overrides: PANAKO_DB (default: db/panako), PANAKO_MAP_GB (LMDB map size, default 16).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JAVA_BIN="$(ls -d "/c/Program Files/Eclipse Adoptium"/jdk-17*/bin | head -1)"
FFMPEG_BIN="/c/Users/ADMIN/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-9.0.2-full_build/bin"
export PATH="$JAVA_BIN:$FFMPEG_BIN:$PATH"
DB_WIN="$(cygpath -m "${PANAKO_DB:-$ROOT/db/panako}")"
JAR_WIN="$(cygpath -m "$ROOT/tools/Panako/build/libs/panako-2.1-all.jar")"

# Keep MSYS from rewriting "/C" into "C:/". Callers must pass Windows-style file paths
# (relative paths are fine).
export MSYS_NO_PATHCONV=1

cmd="$1"; shift
exec java \
  -Dpanako.lmdb.mapsize.gb="${PANAKO_MAP_GB:-16}" \
  --add-opens java.base/java.nio=ALL-UNNAMED \
  --add-opens java.base/sun.nio.ch=ALL-UNNAMED \
  -jar "$JAR_WIN" "$cmd" \
  STRATEGY=PANAKO \
  DECODER_PIPE_ENVIRONMENT=cmd.exe DECODER_PIPE_ENVIRONMENT_ARG=/C \
  PANAKO_LMDB_FOLDER="$DB_WIN/db" PANAKO_CACHE_FOLDER="$DB_WIN/cache" \
  "$@"
