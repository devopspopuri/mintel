#!/usr/bin/env bash
set -euo pipefail

if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
  alembic upgrade head
fi

exec "$@"
