#!/usr/bin/env bash
#
# Smoke-test a built Copycast image with plain `docker run`:
#
#   scripts/smoke.sh copycast:ci
#
# Starts postgres + SeaweedFS (the Object store) + api + worker on a private network, then checks:
#   - /healthz/ready answers 200 on the api (8080) and the worker (8081)
#   - /api/about reports the engine version pinned in uv.lock and a working ffmpeg
#   - /api/buckets contains the default Bucket "Copycast"
#   - `copycast --version` prints the application version
#   - negative: a wrong Object store secret makes `copycast api` exit 2 with one storage line
#
# Environment:
#   SMOKE_ENGINE_VERSION   expected engine version (default: scripts/engine_version.py)
#   SMOKE_APP_VERSION      expected application version (default: not checked)
#   SMOKE_API_PORT / SMOKE_WORKER_PORT   host ports (defaults 18080 / 18081)
#   SMOKE_TIMEOUT          seconds to wait for readiness (default 180)
#   SMOKE_SKIP_NEGATIVE=1  skip the wrong-secret check
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
SEAWEEDFS_IMAGE=${SMOKE_SEAWEEDFS_IMAGE:-$(sed -nE 's/^ *image: (chrislusf\/seaweedfs:.*)$/\1/p' "$ROOT/docker-compose.yml" | head -n 1)}
S3_SECRET="smoke-$RANDOM-$RANDOM"

RUN_ID="copycast-smoke-$$-$RANDOM"
NET="$RUN_ID"
PG="$RUN_ID-pg"
S3="$RUN_ID-s3"
API="$RUN_ID-api"
WORKER="$RUN_ID-worker"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/copycast-smoke.XXXXXX")
DB_URL="postgresql://copycast:copycast@$PG:5432/copycast"
STORAGE_ENV=(-e "COPYCAST__STORAGE__URL=s3://copycast" -e "COPYCAST__STORAGE__ENDPOINT=http://$S3:8333"
  -e "COPYCAST__STORAGE__ACCESS_KEY_ID=copycast" -e "COPYCAST__STORAGE__SECRET_ACCESS_KEY=$S3_SECRET")
FAILED=1

for tool in docker curl jq python3; do
  command -v "$tool" >/dev/null || { echo "smoke: $tool is required" >&2; exit 2; }
done

log()  { printf '\033[1m[smoke]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[smoke] FAIL:\033[0m %s\n' "$*" >&2; exit 1; }

# PEP 440-ish normalization so "2026.08.19" (yt_dlp.version) equals "2026.8.19" (uv.lock),
# and a nightly's "2026.8.30.232658.dev0" (the lock) equals the "2026.08.30.232658" it reports.
normalize_version() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/(^|[^0-9])0+([0-9])/\1\2/g; s/\.dev[0-9]+$//'
}

cleanup() {
  local status=$?
  if [ "$FAILED" -ne 0 ] && [ "${SMOKE_KEEP:-0}" = "1" ]; then
    echo "[smoke] SMOKE_KEEP=1: leaving $API, $WORKER, $S3 and $PG running" >&2
    return
  fi
  if [ "$FAILED" -ne 0 ]; then
    echo "[smoke] ---- api logs ----" >&2;    docker logs "$API"    2>&1 | tail -n 100 >&2 || true
    echo "[smoke] ---- worker logs ----" >&2; docker logs "$WORKER" 2>&1 | tail -n 100 >&2 || true
  fi
  docker rm -f "$API" "$WORKER" "$S3" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
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
mkdir -p "$WORK/config"

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

log "starting seaweedfs ($SEAWEEDFS_IMAGE)"
docker run -d --name "$S3" --network "$NET" --entrypoint /bin/sh \
  -v "$ROOT/deploy/compose/seaweedfs/entrypoint.sh:/copycast/entrypoint.sh:ro" \
  -e COPYCAST_S3_ACCESS_KEY=copycast -e "COPYCAST_S3_SECRET_KEY=$S3_SECRET" \
  "$SEAWEEDFS_IMAGE" /copycast/entrypoint.sh >/dev/null
deadline=$((SECONDS + TIMEOUT))
until docker exec "$S3" test -f /tmp/ready >/dev/null 2>&1; do
  [ "$SECONDS" -lt "$deadline" ] || fail "seaweedfs did not become ready"
  sleep 1
done

log "starting api and worker"
docker run -d --name "$API" --network "$NET" -p "127.0.0.1:$API_PORT:8080" \
  -v "$WORK/config:/config:ro" "${STORAGE_ENV[@]}" \
  -e "COPYCAST__BASE_URL=http://localhost:$API_PORT" -e "COPYCAST__DATABASE_URL=$DB_URL" \
  "$IMAGE" api >/dev/null
docker run -d --name "$WORKER" --network "$NET" -p "127.0.0.1:$WORKER_PORT:8081" \
  -v "$WORK/config:/config:ro" "${STORAGE_ENV[@]}" \
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

buckets=$(curl -fsS "http://127.0.0.1:$API_PORT/api/buckets")
jq -e '[.. | objects | select((.kind? == "bucket") and ((.name? == "Copycast") or (.title? == "Copycast")))] | length > 0' \
  <<<"$buckets" >/dev/null || fail "/api/buckets does not contain the default Bucket: $buckets"
log "default Bucket present"

version_out=$(docker run --rm "$IMAGE" --version)
grep -q '^copycast ' <<<"$version_out" || fail "copycast --version printed: $version_out"
if [ -n "$EXPECTED_APP" ]; then
  grep -q "^copycast $EXPECTED_APP\$" <<<"$version_out" || fail "expected app version $EXPECTED_APP, got: $version_out"
fi
grep -qi 'yt-dlp' <<<"$version_out" || fail "copycast --version does not mention the engine: $version_out"
log "copycast --version: $(head -n 1 <<<"$version_out")"

if [ "${SMOKE_SKIP_NEGATIVE:-0}" != "1" ]; then
  set +e
  negative_out=$(docker run --rm --network "$NET" "${STORAGE_ENV[@]}" \
    -e "COPYCAST__STORAGE__SECRET_ACCESS_KEY=wrong-$S3_SECRET" \
    -e "COPYCAST__DATABASE_URL=$DB_URL" "$IMAGE" api 2>&1)
  negative_status=$?
  set -e
  [ "$negative_status" -eq 2 ] || fail "wrong Object store secret: expected exit 2, got $negative_status: $negative_out"
  grep -q 'refused the keys' <<<"$negative_out" || fail "wrong Object store secret: no storage hint in: $negative_out"
  log "wrong Object store secret refused with exit 2 and a storage hint"
fi

FAILED=0
log "OK"
