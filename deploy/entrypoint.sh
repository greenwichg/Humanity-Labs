#!/bin/sh
# Container entry point: `web` (default), `worker`, `migrate` or any other command.
set -e
case "${1:-web}" in
  web)
    exec uvicorn batchlight.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-2}" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
    ;;
  worker)
    exec python -m batchlight.worker
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
