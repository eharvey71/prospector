#!/usr/bin/env bash
# Deploy EVERYTHING, every time. Inefficient but effective: no more
# guessing which parts a change touched, no more stale vendored copies or
# un-built web bundles.
#
# Usage:
#     ./deploy.sh                # web + functions + rules + indexes + worker
#     ./deploy.sh --skip-worker  # everything except the (slow) worker build
#
# Not covered: the browser extension (nothing to deploy — reload it at
# chrome://extensions with the refresh icon after a git pull).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> branch: $(git branch --show-current)"

echo "==> [1/6] running tests"
if python3 -c "import pytest" 2>/dev/null; then
    python3 -m pytest tests/ -q   # set -e: failures stop the deploy
else
    echo "    WARNING: pytest not installed (pip install pytest pydantic) — SKIPPING tests"
fi

echo "==> [2/6] syncing shared/ into functions/ and worker/"
./sync_shared.sh

echo "==> [3/6] packaging the browser extension for the in-app download"
# Served at /job-engine-extension.zip by the Extension page. Rebuilt every
# deploy so the download can never lag behind the code.
mkdir -p web/public
rm -f web/public/job-engine-extension.zip
(cd web-extension && zip -qr ../web/public/job-engine-extension.zip . -x '.*')
echo "    $(unzip -l web/public/job-engine-extension.zip | tail -1 | xargs)"

echo "==> [4/6] building web"
(cd web && npm run build)

echo "==> [5/6] deploying hosting + functions + firestore rules & indexes"
firebase deploy --only hosting,functions,firestore:rules,firestore:indexes

if [[ "${1:-}" == "--skip-worker" ]]; then
    echo "==> [6/6] worker SKIPPED (--skip-worker)"
else
    echo "==> [6/6] deploying worker (Cloud Run build — takes a few minutes)"
    (cd worker && gcloud run deploy job-engine-worker --source . --region us-central1)
fi

echo "==> done. Hard-refresh the site (Cmd+Shift+R)."
