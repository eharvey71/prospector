# Prospector backlog

Open items, roughly by value. Move a line to Done (bottom) when shipped.

## Next up
- **User-side control over the admin catalog** — enrolled users should see
  "your matches include the shared catalog" in Settings and be able to opt
  out (own preference, e.g. `use_global_catalog`); matcher respects it
  alongside enrollment. Principle: admin offers, user can decline.
- **Skip-reason learning** — reasons are captured on Skip now; nothing
  consumes them yet. First consumers: "exclude this company?" nudge after
  repeated same-company skips; rubric-weight calibration later.
- **Re-score button** — match assessments are cached forever; a per-card
  re-score re-runs against the current profile (deliberately manual, one
  LLM call each; no bulk auto-rescore on profile edits).

## Bigger pieces
- **Email outcome tracking** — read confirmation/rejection/interview mail
  (Gmail, read-only), advance post-submission states, learn which score
  bands convert. The feature that closes the loop; dashboard rides on it.
- **Dashboard** — per-user funnel/outcomes + global engine health, built
  on outcome data (a submissions-only dashboard is a vanity page).
- **Auto-seed watchlists from profile** — run the company suggester
  automatically on first titles save, so a new user never touches board
  config even without the global catalog.
- **Follow-up nudger** — "no reply in N days" + drafted follow-up; trivial
  once email outcomes exist.

## Maybe / later
- Multiple named source resumes with a per-application picker (worker
  already honors per-app `resume_path`; tailoring covers most of the need).
- Jobs-aggregator discovery source (Adzuna etc.) for true open search.
- Writing-samples UI hint + character counter (drafting reads first 3
  samples, first ~2000 chars each).
- Exclude-cities (negative location list) if allow-list + work modes
  prove insufficient.
- Merge `claude/project-continuation-h5arx3` to main once the current
  batch has soaked.

## Done (recent highlights)
- Admin role + global board catalog with per-user enrollment.
- Location filter + metro expansion + work-arrangement modes.
- Skip reasons captured; skips visible/reversible in Done.
- Post-submit LLM judge; live-submission safety (never-resubmit marker,
  submit-button hardening); worker handles modern JS/iframe boards.
- Email-link (passwordless) sign-in; light theme; Prospector rename.
- anthropic SDK 1.x breakage fixed (temperature removal; bounded pin).
