# Prospector (repo: job-engine)
End-to-end job application pipeline. Firebase (Firestore/Functions/Hosting) +
Cloud Run Playwright worker. Project: job-engine-c8f9c, region us-central1.

## Architecture
Application state machine is the spine: discovered → matched → drafted →
in_review → approved → queued → submitting → submitted/failed/needs_human.
Engine transitions via shared/state_machine.py advance() (transactional,
idempotent). Human transitions (approve/reject) enforced in firestore.rules.

## Critical invariants — do not violate
- Shared code lives in shared/; run ./sync_shared.sh after editing it
  (vendored copies in functions/ and worker/ are gitignored).
- Worker deploys FROM worker/ dir: gcloud run deploy job-engine-worker
  --source . --region us-central1 (plus env vars; see README).
- SUBMIT_DRY_RUN gates real submissions. Live as of 2026-08 (user
  decision). A submission that has clicked Submit is NEVER retried:
  adapters call mark_submit_clicked() before the click and the worker
  escalates on any post-click uncertainty. Never weaken that.
- Adapter contract: escalate over guess. Never pick a form option without
  post-selection verification (see v2.2 sponsorship incident in git history).
- Playwright version in requirements.txt must exactly match the Docker base
  image version.
- The enqueue in functions/main.py must advance state to queued BEFORE
  creating the Cloud Task (race condition, fixed once already).

## Known quirks
- Greenhouse pages contain ~230 hidden [role=option] entries from the phone
  field's intl-tel-input — option enumeration must filter visible + non-.iti.
- React-select inputs stay empty after selection; read [class*=single-value]
  inside the control container.