#!/bin/sh
# Container entry point: `web` (default), `worker`, `migrate` or any other command.
set -e

# Point the app at another database host, for example a restored copy being checked, without passing the
# password around: the host in BATCHLIGHT_DATABASE_URL is replaced and everything else is kept.
if [ -n "${BATCHLIGHT_DATABASE_HOST_OVERRIDE:-}" ] && [ -n "${BATCHLIGHT_DATABASE_URL:-}" ]; then
  BATCHLIGHT_DATABASE_URL=$(python -c '
import os
from urllib.parse import urlsplit, urlunsplit
url = urlsplit(os.environ["BATCHLIGHT_DATABASE_URL"])
host = os.environ["BATCHLIGHT_DATABASE_HOST_OVERRIDE"]
userinfo, _, _ = url.netloc.rpartition("@")
port = f":{url.port}" if url.port else ""
print(urlunsplit(url._replace(netloc=(userinfo + "@" if userinfo else "") + host + port)))
')
  export BATCHLIGHT_DATABASE_URL
fi

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
