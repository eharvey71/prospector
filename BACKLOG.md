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

## Discovery hardening (from ATS research doc, 2026-09)
- **Scheduled board validation** — probe every watchlist/catalog slug on a
  schedule via the platforms' public endpoints; store
  `validation_status` (active/empty/dead) + `last_validated_at`; keep dead
  entries (don't delete) so retries don't re-add known-bad slugs; surface
  status in Settings step 3 and the admin catalog. Then rank future
  adapters by validated live-posting volume.
- **Workday URL parser: both shapes + tenant retry** — support
  `wd{n}.myworkdaysite.com/recruiting/{tenant}/{site}` (tenant in the
  PATH) alongside classic `{tenant}.wd{n}.myworkdayjobs.com/{site}`; on
  failure retry across common wd numbers before marking dead.
- **Wider ATS fingerprinting** — career-page classifier should recognize
  and store platforms we can't submit to yet (Paylocity, ADP, JazzHR,
  SuccessFactors, PageUp, PeopleAdmin, Interfolio, Oracle HCM, iCIMS,
  Taleo/BrassRing) as `ats_platform` on the record; follow one redirect
  hop and check iframe srcs. Converts guesses into an ATS map.

## Extension / application data
- **Login-gated boards** — autofill sometimes fails where sign-in or
  account creation comes before the form (Workday tenants, some
  iCIMS/Taleo). Suspects: the payload's domain check after an auth
  redirect, the form rendering post-login in a frame the summon path
  misses, and multi-step wizards where fields appear per step. Likely
  fixes: re-summon after navigation, per-step fill, payload surviving the
  auth hop.
- **References** — Profile section (name, relationship, company, email,
  phone, 3-4 entries) + extension autofill for the usual patterns
  (`reference_1_name`, "Reference 1 Email", repeated blocks). Extension
  only, same posture as self-identification: never the unattended worker.

## Known gap
- **Re-scan the existing pool for a user** — matching only runs when a
  posting is created or its description changes, so a settings change
  (new titles, opened-up locations, catalog enrollment) does NOT pull in
  postings already sitting in the pool; the user waits for new ones. An
  admin action "re-scan the pool for this user" (iterate active postings,
  non-forced match) would close it. Bounded by the prefilter, but it's
  real LLM spend, so keep it deliberate.

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
