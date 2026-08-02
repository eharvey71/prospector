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

echo "==> [1/5] syncing shared/ into functions/ and worker/"
./sync_shared.sh

echo "==> [2/5] packaging the browser extension for the in-app download"
# Served at /job-engine-extension.zip by the Extension page. Rebuilt every
# deploy so the download can never lag behind the code.
mkdir -p web/public
rm -f web/public/job-engine-extension.zip
(cd web-extension && zip -qr ../web/public/job-engine-extension.zip . -x '.*')
echo "    $(unzip -l web/public/job-engine-extension.zip | tail -1 | xargs)"

echo "==> [3/5] building web"
(cd web && npm run build)

echo "==> [4/5] deploying hosting + functions + firestore rules & indexes"
firebase deploy --only hosting,functions,firestore:rules,firestore:indexes

if [[ "${1:-}" == "--skip-worker" ]]; then
    echo "==> [5/5] worker SKIPPED (--skip-worker)"
else
    echo "==> [5/5] deploying worker (Cloud Run build — takes a few minutes)"
    (cd worker && gcloud run deploy job-engine-worker --source . --region us-central1)
fi

echo "==> done. Hard-refresh the site (Cmd+Shift+R)."
