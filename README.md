# Prospector

End-to-end job application pipeline on Firebase + Google Cloud Run:
discovery → matching → drafting → human review → tiered automated submission.

```
Cloud Scheduler ──▶ crawl_boards (Fn) ──▶ jobPostings (Firestore)
                                              │ trigger
                                              ▼
                                    on_posting_written (Fn)
                                    LLM match score per user
                                              │
                                              ▼
                          users/{uid}/applications  [state machine]
                                              │ trigger
              matched ──▶ drafting (Fn: draft → critique → revise)
                                              │
                          in_review ◀─────────┘
                              │  human approves in web UI
                              ▼
              approved ──▶ Cloud Task ──▶ /submit (Cloud Run + Playwright)
                              │                    │
                              ▼                    ▼
                       submitted / failed / needs_human
```

## Layout

| Path | What | Deploys to |
|---|---|---|
| `shared/` | Pydantic schemas + state machine (source of truth) | vendored into both |
| `functions/` | discovery, matching, drafting, task enqueue | Firebase Functions (Python 3.12) |
| `worker/` | FastAPI + Playwright submission service | Cloud Run |
| `web/` | Next.js review UI | Firebase Hosting |

The application document's `state` field drives everything. Engine-legal
transitions live in `shared/schemas.py::ENGINE_TRANSITIONS`; human-legal
transitions (approve/reject) are enforced in `firestore.rules`. Every
transition goes through `state_machine.advance()`, a transaction that makes
replayed Firestore triggers and duplicate Cloud Tasks deliveries no-ops.

## Setup

Prereqs: `firebase-tools` (npm i -g firebase-tools), `gcloud`, Python 3.12,
Node 20+.

```bash
firebase login && gcloud auth login
firebase projects:create your-project-id   # or use an existing one
firebase use your-project-id
gcloud config set project your-project-id

# Enable services
gcloud services enable run.googleapis.com cloudtasks.googleapis.com \
  cloudscheduler.googleapis.com firestore.googleapis.com

# Firestore (native mode) + rules + indexes
firebase deploy --only firestore

# Vendored shared modules (run after ANY edit to shared/)
./sync_shared.sh
```

### Functions

```bash
cd functions
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt ../shared

# Secrets / config
firebase functions:secrets:set ANTHROPIC_API_KEY
# LLM_PROVIDER defaults to anthropic; vertex and openai are also supported.
#   anthropic  ANTHROPIC_API_KEY
#   vertex     GOOGLE_CLOUD_PROJECT [, VERTEX_LOCATION]
#   openai     OPENAI_API_KEY [, OPENAI_BASE_URL for a compatible gateway]
# LLM_MODEL is the provider's model id, verbatim.
#
# Per-call-site override, for trying a new model without moving the whole
# pipeline onto it: LLM_PROVIDER_<ROLE> / LLM_MODEL_<ROLE> beat the globals
# for that role alone. Roles are matching, drafting, critique,
# screening_answers, synonyms, metro, discovery, track, suggest, extract,
# resume, resume_tailor, judge, agent. e.g.
#   LLM_PROVIDER_MATCHING=openai LLM_MODEL_MATCHING=<id>
# scores postings on the new model while letters keep being written by the
# old one. Spend is recorded per model per day in health/llm_<model>_<day>,
# so the two are comparable; the daily budget still counts them together.
#
# LLM_EFFORT / LLM_EFFORT_<ROLE> set reasoning effort on the openai
# provider. Published benchmarks for these models are quoted at a specific
# effort and the settings differ a lot in both quality and latency, so pick
# one deliberately. High effort can mean a very slow first token — fine for
# batch scoring, bad for anything a user is waiting on.
#
# LLM_REASONING_FLOOR (default 4000): on a reasoning model the token cap
# covers reasoning as well as visible output, so a call capped at 300 can
# spend the lot on thinking and return an empty string with finish_reason
# "length". Several call sites here are capped tight because the Claude
# models only ever counted visible output. The openai provider detects
# that case and retries once at this floor.

firebase deploy --only functions
```

### Worker (Cloud Run)

```bash
cd worker
gcloud run deploy job-engine-worker \
  --source . \
  --region us-central1 \
  --no-allow-unauthenticated \
  --concurrency 1 --memory 2Gi --timeout 900 \
  --set-env-vars STORAGE_BUCKET=your-project-id.appspot.com,SUBMIT_DRY_RUN=true \
  --set-secrets ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest
# The secret powers the Tier 2 agentic adapter; create it once with
#   gcloud secrets create ANTHROPIC_API_KEY --data-file=- <<< "sk-ant-..."
# (or reuse the secret Firebase created for functions).

# Cloud Tasks queue with polite per-queue rate limiting
gcloud tasks queues create submissions \
  --location us-central1 \
  --max-dispatches-per-second 0.2 \
  --max-concurrent-dispatches 1 \
  --max-attempts 3

# Invoker service account for OIDC-authenticated task delivery
gcloud iam service-accounts create task-invoker
gcloud run services add-iam-policy-binding job-engine-worker \
  --region us-central1 \
  --member serviceAccount:task-invoker@your-project-id.iam.gserviceaccount.com \
  --role roles/run.invoker
```

Then point the functions at the worker (use the URL `gcloud run deploy`
printed):

```bash
firebase functions:config:set worker.url="https://job-engine-worker-....run.app"
# or set WORKER_URL / WORKER_INVOKER_SA / TASKS_QUEUE as function env vars
firebase deploy --only functions
```

### Web

```bash
cd web && npm install
cp .env.example .env.local   # fill from Firebase console -> web app config
npm run dev                  # local
npm run deploy               # build + firebase deploy --only hosting
```

## Seed data to get moving

1. Sign in once through the web UI (creates your `uid`).
2. Go to `/profile` and upload your resume. The `on_resume_uploaded` function
   extracts name/location/skills/work history via LLM and stages the result
   at `users/{uid}/resume_extraction/latest`; a suggestion panel appears in
   the profile page — apply it, review the fields, and Save. (Nothing is
   written to your live profile without that explicit apply + save.)
3. Fill in what extraction can't know: writing samples, preferences, salary
   target, and the company watchlist (all on the same page). The watchlist
   takes Greenhouse/Lever board slugs plus arbitrary career-page URLs; the
   "Find companies for me" box suggests verified boards for a role
   description. One-off jobs from anywhere can be pasted into the box at
   the top of the review queue.
4. Trigger a crawl manually (Cloud Scheduler console → force run) or wait 6h.
5. Watch applications appear in the review queue.

## Go-live checklist (deliberately manual)

- [ ] `SUBMIT_DRY_RUN=true` run end-to-end: approve a real posting, inspect
      the `pre_submit` screenshot in Storage, confirm every field is right.
- [ ] Verify the escalation path: approve a posting with custom required
      questions and confirm it lands in `needs_human` instead of guessing.
- [x] Flip `SUBMIT_DRY_RUN=false` on the Cloud Run service to go live:
      `gcloud run services update job-engine-worker --region us-central1 \
        --update-env-vars SUBMIT_DRY_RUN=false`
      Back to safe: same command with `SUBMIT_DRY_RUN=true`.
      Live-mode guarantee: an application whose Submit button was
      clicked is never submitted again — the adapter records the click
      first, and any uncertainty afterwards escalates to needs_human.
- [ ] Keep queue rate limits low (the defaults above are ~1 submission / 5s
      max, 1 at a time). You are applying as yourself; behave like yourself.

## What's intentionally not here yet

- **Gmail feedback loop** (confirmations/rejections → status updates).
- **Workday** — permanently Tier 3 until you hate yourself enough.

## A note on ToS

Many job boards prohibit automated submissions. This system is built around a
human approving every application, per-domain rate limits, dry-run defaults,
and escalation over guessing — use it accordingly, on boards where you're
comfortable, applying as yourself.
