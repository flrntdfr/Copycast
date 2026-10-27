#!/usr/bin/env sh
# Start the local stack (deploy/compose/docker-compose.local.yml) and the Vite dev server,
# following the container logs. Ctrl-C stops both; the Docker volumes keep the data.
set -eu
cd "$(dirname "$0")/.."
compose="docker compose -f deploy/compose/docker-compose.local.yml"

$compose up --build --detach --wait api worker
trap '$compose stop; kill 0' INT TERM
$compose logs --follow api worker &
pnpm --dir web run dev &
wait
