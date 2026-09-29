#!/bin/sh
set -eu
cd "$(dirname "$0")"
case "${1:-}" in
  ''|--with-deps) ;;
  *) echo 'Usage: ./setup.sh [--with-deps]' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo 'Usage: ./setup.sh [--with-deps]' >&2; exit 2; }
python3 -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required"'
[ ! -f .env ] || chmod 600 .env
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m scripts.setup_browser "$@"
