#!/bin/sh
set -eu
if [ "$#" -ne 0 ]; then
    echo "Usage: $0 (uses config/runtime-profiles/macbook-air-current.json)" >&2
    exit 2
fi
cd "$(dirname "$0")"
exec .venv/bin/python scripts/deploy_launchagent.py --profile-file config/runtime-profiles/macbook-air-current.json
