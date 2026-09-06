#!/usr/bin/env bash
#
# Smoke-test a built Copycast image with plain `docker run`:
#
#   scripts/smoke.sh copycast:ci
#
# Starts postgres + api + worker on a private network with a data directory handed to uid
# 1000, then checks:
#   - /healthz/ready answers 200 on the api (8080) and the worker (8081)
#   - /api/about reports the engine version pinned in uv.lock and a working ffmpeg
#   - /api/inboxes contains the default Inbox "Copycast"
#   - `copycast --version` prints the application version
#   - negative: a root-owned, empty bind mount makes `copycast api` exit 2 with the chown hint
#
# Environment:
#   SMOKE_ENGINE_VERSION   expected engine version (default: scripts/engine_version.py)
#   SMOKE_APP_VERSION      expected application version (default: not checked)
#   SMOKE_API_PORT / SMOKE_WORKER_PORT   host ports (defaults 18080 / 18081)
#   SMOKE_TIMEOUT          seconds to wait for readiness (default 180)
#   SMOKE_SKIP_NEGATIVE=1  skip the root-owned data directory check (it is skipped on its own
#                          when the engine maps bind-mount ownership, e.g. Docker Desktop)
#   SMOKE_KEEP=1           leave the containers running on failure for inspection
#
# Requires docker, curl, jq and python3 (3.11+ for tomllib).

set -euo pipefail

IMAGE=${1:?usage: scripts/smoke.sh IMAGE}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
API_PORT=${SMOKE_API_PORT:-18080}
WORKER_PORT=${SMOKE_WORKER_PORT:-18081}
TIMEOUT=${SMOKE_TIMEOUT:-180}
EXPECTED_ENGINE=${SMOKE_ENGINE_VERSION:-$(python3 "$ROOT/scripts/engine_version.py")}
EXPECTED_APP=${SMOKE_APP_VERSION:-}
POSTGRES_IMAGE=${SMOKE_POSTGRES_IMAGE:-postgres:17}

RUN_ID="copycast-smoke-$$-$RANDOM"
NET="$RUN_ID"
PG="$RUN_ID-pg"
API="$RUN_ID-api"
WORKER="$RUN_ID-worker"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/copycast-smoke.XXXXXX")
DB_URL="postgresql://copycast:copycast@$PG:5432/copycast"
FAILED=1

for tool in docker curl jq python3; do
  command -v "$tool" >/dev/null || { echo "smoke: $tool is required" >&2; exit 2; }
done

log()  { printf '\033[1m[smoke]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[smoke] FAIL:\033[0m %s\n' "$*" >&2; exit 1; }

# PEP 440-ish normalization so "2026.08.19" (yt_dlp.version) equals "2026.8.19" (uv.lock).
normalize_version() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/(^|[^0-9])0+([0-9])/\1\2/g'; }

# Run something as root inside the image, e.g. to fix ownership of bind-mounted directories.
as_root() { docker run --rm --user 0 --entrypoint sh -v "$WORK:/work" "$IMAGE" -c "$1"; }

cleanup() {
  local status=$?
  if [ "$FAILED" -ne 0 ] && [ "${SMOKE_KEEP:-0}" = "1" ]; then
    echo "[smoke] SMOKE_KEEP=1: leaving $API, $WORKER and $PG running" >&2
    return
  fi
  if [ "$FAILED" -ne 0 ]; then
    echo "[smoke] ---- api logs ----" >&2;    docker logs "$API"    2>&1 | tail -n 100 >&2 || true
    echo "[smoke] ---- worker logs ----" >&2; docker logs "$WORKER" 2>&1 | tail -n 100 >&2 || true
  fi
  docker rm -f "$API" "$WORKER" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  as_root 'rm -rf /work/data /work/rootdata' >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT

wait_for_http() {
  local url=$1 what=$2 deadline=$((SECONDS + TIMEOUT))
  until curl -fsS -o /dev/null "$url"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "[smoke] last response from $url:" >&2
      curl -sS "$url" >&2 || true
      fail "$what did not become ready within ${TIMEOUT}s"
    fi
    sleep 2
  done
}

log "image $IMAGE, expected engine $EXPECTED_ENGINE, work dir $WORK"
mkdir -p "$WORK/data" "$WORK/config" "$WORK/rootdata"
as_root 'chown -R 1000:1000 /work/data && chown 0:0 /work/rootdata && chmod 755 /work/rootdata'

log "starting postgres"
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER=copycast -e POSTGRES_PASSWORD=copycast -e POSTGRES_DB=copycast \
  "$POSTGRES_IMAGE" >/dev/null
deadline=$((SECONDS + TIMEOUT))
until docker exec "$PG" pg_isready -U copycast -d copycast >/dev/null 2>&1; do
  [ "$SECONDS" -lt "$deadline" ] || fail "postgres did not become ready"
  sleep 1
done

log "starting api and worker"
docker run -d --name "$API" --network "$NET" -p "127.0.0.1:$API_PORT:8080" \
  -v "$WORK/data:/data" -v "$WORK/config:/config:ro" \
  -e "COPYCAST__BASE_URL=http://localhost:$API_PORT" -e "COPYCAST__DATABASE_URL=$DB_URL" \
  "$IMAGE" api >/dev/null
docker run -d --name "$WORKER" --network "$NET" -p "127.0.0.1:$WORKER_PORT:8081" \
  -v "$WORK/data:/data" -v "$WORK/config:/config:ro" \
  -e "COPYCAST__BASE_URL=http://localhost:$API_PORT" -e "COPYCAST__DATABASE_URL=$DB_URL" \
  "$IMAGE" worker >/dev/null

wait_for_http "http://127.0.0.1:$WORKER_PORT/healthz/live" "worker"
wait_for_http "http://127.0.0.1:$API_PORT/healthz/live" "api"
wait_for_http "http://127.0.0.1:$WORKER_PORT/healthz/ready" "worker readiness"
wait_for_http "http://127.0.0.1:$API_PORT/healthz/ready" "api readiness"
log "api and worker are ready"

about=$(curl -fsS "http://127.0.0.1:$API_PORT/api/about")
engine=$(jq -r '.engine.version // empty' <<<"$about")
[ -n "$engine" ] || fail "/api/about has no engine.version: $about"
if [ "$(normalize_version "$engine")" != "$(normalize_version "$EXPECTED_ENGINE")" ]; then
  fail "engine version $engine does not match the lock ($EXPECTED_ENGINE)"
fi
ffmpeg=$(jq -r '.ffmpeg_version // .engine.ffmpeg_version // empty' <<<"$about")
[ -n "$ffmpeg" ] || fail "/api/about reports no ffmpeg: $about"
log "about: engine $engine, ffmpeg $ffmpeg"

inboxes=$(curl -fsS "http://127.0.0.1:$API_PORT/api/inboxes")
jq -e '[.. | objects | select((.kind? == "inbox") and ((.name? == "Copycast") or (.title? == "Copycast")))] | length > 0' \
  <<<"$inboxes" >/dev/null || fail "/api/inboxes does not contain the default Inbox: $inboxes"
log "default Inbox present"

version_out=$(docker run --rm "$IMAGE" --version)
grep -q '^copycast ' <<<"$version_out" || fail "copycast --version printed: $version_out"
if [ -n "$EXPECTED_APP" ]; then
  grep -q "^copycast $EXPECTED_APP\$" <<<"$version_out" || fail "expected app version $EXPECTED_APP, got: $version_out"
fi
grep -qi 'yt-dlp' <<<"$version_out" || fail "copycast --version does not mention the engine: $version_out"
log "copycast --version: $(head -n 1 <<<"$version_out")"

# Docker Desktop (and other VM-backed engines) map bind-mount ownership to the container user,
# so a root-owned directory looks writable from inside: probe as uid 1000 and skip the negative
# check when the mount cannot reproduce the condition instead of failing on a false positive.
skip_negative=${SMOKE_SKIP_NEGATIVE:-0}
if [ "$skip_negative" != "1" ] \
  && docker run --rm --user 1000 --entrypoint sh -v "$WORK/rootdata:/data" "$IMAGE" \
       -c 'touch /data/.probe 2>/dev/null && rm -f /data/.probe' >/dev/null 2>&1; then
  log "root-owned bind mount is writable by uid 1000 (ownership mapped by the engine): skipping the negative check"
  skip_negative=1
fi

if [ "$skip_negative" != "1" ]; then
  set +e
  negative_out=$(docker run --rm -v "$WORK/rootdata:/data" -e "COPYCAST__DATABASE_URL=$DB_URL" "$IMAGE" api 2>&1)
  negative_status=$?
  set -e
  [ "$negative_status" -eq 2 ] || fail "root-owned data dir: expected exit 2, got $negative_status: $negative_out"
  grep -q 'chown' <<<"$negative_out" || fail "root-owned data dir: no chown hint in: $negative_out"
  log "root-owned data dir refused with exit 2 and a chown hint"
fi

FAILED=0
log "OK"
