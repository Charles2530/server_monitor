#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8766 --no-access-log
