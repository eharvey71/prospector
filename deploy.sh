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

echo "==> [1/4] syncing shared/ into functions/ and worker/"
./sync_shared.sh

echo "==> [2/4] building web"
(cd web && npm run build)

echo "==> [3/4] deploying hosting + functions + firestore rules & indexes"
firebase deploy --only hosting,functions,firestore:rules,firestore:indexes

if [[ "${1:-}" == "--skip-worker" ]]; then
    echo "==> [4/4] worker SKIPPED (--skip-worker)"
else
    echo "==> [4/4] deploying worker (Cloud Run build — takes a few minutes)"
    (cd worker && gcloud run deploy job-engine-worker --source . --region us-central1)
fi

echo "==> done. Hard-refresh the site (Cmd+Shift+R)."
