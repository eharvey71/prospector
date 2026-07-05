#!/usr/bin/env bash
# Firebase deploys only functions/ and Cloud Run builds only worker/, so the
# shared modules get vendored into both before any deploy or build.
set -euo pipefail
cd "$(dirname "$0")"
for f in schemas.py state_machine.py llm.py; do
  cp "shared/$f" "functions/$f"
  cp "shared/$f" "worker/$f"
done
echo "synced shared modules -> functions/, worker/"
