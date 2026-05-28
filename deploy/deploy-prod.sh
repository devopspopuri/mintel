#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/proj/app/mintel}"

cd "$APP_DIR"

if [ ! -f .env.prod ]; then
  echo "Missing $APP_DIR/.env.prod. Copy .env.prod.example and set production secrets first." >&2
  exit 1
fi

mkdir -p data/postgres data/logs

docker compose --env-file .env.prod -f docker-compose.prod.yml build
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
for attempt in {1..15}; do
  if curl --fail --max-time 15 http://127.0.0.1:${MINTEL_PORT:-8009}/health; then
    exit 0
  fi
  echo "Health check attempt ${attempt}/15 failed; waiting for app startup..." >&2
  sleep 2
done
echo "Mintel health check failed after retries." >&2
exit 1
