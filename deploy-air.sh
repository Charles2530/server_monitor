#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "$#" -ne 0 ]; then
    echo "Usage: $0 (uses config/runtime-profiles/macbook-air-current.json)" >&2
    exit 2
fi
exec .venv/bin/python scripts/deploy_launchagent.py --profile-file config/runtime-profiles/macbook-air-current.json
