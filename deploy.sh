#!/bin/sh
set -eu
if [ "$#" -ne 0 ]; then
    echo "Usage: $0 (automatically selects the matching Mac runtime profile)" >&2
    exit 2
fi
cd "$(dirname "$0")"
exec .venv/bin/python scripts/deploy_launchagent.py --profile-file auto
