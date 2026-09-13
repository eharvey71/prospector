"use client";
// Settings — how the engine searches and drafts: matching preferences and
// the company watchlist. Who-you-are content (basics, resume, work history,
// writing samples) lives on /profile.
// Saving writes ONLY preferences + watchlist, so it can never clobber
// profile fields edited on the other page (and vice versa).

import { useEffect, useState } from "react";
import { onAuthStateChanged } from "firebase/auth";
import { doc, getDoc, setDoc, serverTimestamp } from "firebase/firestore";
import { httpsCallable } from "firebase/functions";
import { auth, db, functions } from "../../lib/firebase";
import { Busy, T, Nav, SignIn, box, btn, btnPrimary, input, label } from "../ui";

export default function SettingsPage() {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  const [titles, setTitles] = useState("");           // comma-separated
  const [titleSynonyms, setTitleSynonyms] = useState(""); // auto-generated, editable
  const [locationsStr, setLocationsStr] = useState("");  // semicolon-separated
  const [workMode, setWorkMode] = useState("local_or_remote");
  const [radius, setRadius] = useState("25");
  const [expanding, setExpanding] = useState(false);

  async function expandMetro() {
    const center = locationsStr.split(";")[0]?.trim();
    if (!center) {
      setStatus("Type a center place first — e.g. Richmond, VA");
      return;
    }
    setExpanding(true);
    setStatus("");
    try {
      const call = httpsCallable(functions, "expand_metro", { timeout: 60_000 });
      const res = await call({ center, radius: Number(radius) });
      const cur = locationsStr.split(";").map(s => s.trim()).filter(Boolean);
      const seen = new Set(cur.map(s => s.toLowerCase()));
      for (const t of res.data.towns || []) {
        if (!seen.has(t.toLowerCase())) { seen.add(t.toLowerCase()); cur.push(t); }
      }
      setLocationsStr(cur.join("; "));
      setStatus("Nearby towns added — prune any you don't want, then Save settings.");
    } catch (e) {
      setStatus(`Couldn't find nearby towns: ${e.message}`);
    } finally {
      setExpanding(false);
    }
  }
  const [excludeCompanies, setExcludeCompanies] = useState("");
  const [minScore, setMinScore] = useState(70);
  const [autoDraft, setAutoDraft] = useState(false);
  const [salaryStrategy, setSalaryStrategy] = useState("exact");
  const [tailorResume, setTailorResume] = useState(false);
  const [emailMatches, setEmailMatches] = useState(false);
  const [testingDigest, setTestingDigest] = useState(false);

  async function sendTestDigest() {
    setTestingDigest(true);
    setStatus("");
    try {
      const call = httpsCallable(functions, "send_test_digest", { timeout: 120_000 });
      const res = await call({});
      setStatus(`Test digest sent to ${res.data.to} with `
        + `${res.data.matches} match${res.data.matches === 1 ? "" : "es"} — `
        + `check your inbox (subject starts with [test]).`);
    } catch (e) {
      setStatus(`Couldn't send it: ${e.message}`);
    } finally {
      setTestingDigest(false);
    }
  }
  const [ghBoards, setGhBoards] = useState("");       // comma-separated slugs
  const [leverBoards, setLeverBoards] = useState("");
  const [customPages, setCustomPages] = useState(""); // career page URLs
  const [workdaySites, setWorkdaySites] = useState(""); // myworkdayjobs URLs
  const [ashbyBoards, setAshbyBoards] = useState("");
  const [srBoards, setSrBoards] = useState("");       // smartrecruiters ids
  const [workableBoards, setWorkableBoards] = useState("");
  const [linkedinSearches, setLinkedinSearches] = useState("");
  const [suggestRole, setSuggestRole] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [suggestions, setSuggestions] = useState([]); // verified boards
  const [unverified, setUnverified] = useState([]);   // fits, but no board found
  const [trackName, setTrackName] = useState("");
  const [tracking, setTracking] = useState(false);
  const [trackResults, setTrackResults] = useState([]); // resolution reports

  useEffect(() => onAuthStateChanged(auth, setUser), []);

  useEffect(() => {
    if (!user) return;
    (async () => {
      const snap = await getDoc(doc(db, "users", user.uid));
      if (snap.exists()) {
        const p = snap.data().preferences || {};
        setTitles((p.titles || []).join(", "));
        setTitleSynonyms((p.title_synonyms || []).join(", "));
        setLocationsStr((p.locations || []).join("; "));
        // Legacy docs have only the old checkbox pair — derive the mode.
        setWorkMode(p.work_mode
          || (p.remote_only ? "remote_only"
              : p.remote_ok === false ? "local_only" : "local_or_remote"));
        setExcludeCompanies((p.exclude_companies || []).join(", "));
        setMinScore(p.min_match_score ?? 70);
        setAutoDraft(p.auto_draft ?? false);
        setSalaryStrategy(p.salary_strategy || "exact");
        setTailorResume(p.tailor_resume ?? false);
        setEmailMatches(p.email_matches ?? false);
      }
      const wl = await getDoc(doc(db, "users", user.uid, "watchlist", "companies"));
      if (wl.exists()) {
        setGhBoards((wl.data().greenhouse || []).join(", "));
        setLeverBoards((wl.data().lever || []).join(", "));
        setCustomPages((wl.data().custom || []).join(", "));
        setWorkdaySites((wl.data().workday || []).join(", "));
        setAshbyBoards((wl.data().ashby || []).join(", "));
        setSrBoards((wl.data().smartrecruiters || []).join(", "));
        setWorkableBoards((wl.data().workable || []).join(", "));
        setLinkedinSearches((wl.data().linkedin || []).join(", "));
      }
    })();
  }, [user]);

  const csv = (s) => s.split(",").map(x => x.trim()).filter(Boolean);

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      await doSave();
      setStatus("Saved ✓");
      setTimeout(() => setStatus(""), 2500);
    } catch (e) {
      setStatus(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  async function doSave() {
    await setDoc(doc(db, "users", user.uid), {
      preferences: {
        titles: csv(titles),
        title_synonyms: csv(titleSynonyms),
        locations: locationsStr.split(";").map(s => s.trim()).filter(Boolean),
        work_mode: workMode,
        // Kept in sync for anything still reading the legacy flags.
        remote_only: workMode === "remote_only",
        remote_ok: workMode !== "local_only",
        exclude_companies: csv(excludeCompanies),
        min_match_score: Number(minScore) || 70,
        auto_draft: autoDraft,
        salary_strategy: salaryStrategy,
        tailor_resume: tailorResume,
        email_matches: emailMatches,
      },
      updatedAt: serverTimestamp(),
    }, { merge: true });
    await setDoc(doc(db, "users", user.uid, "watchlist", "companies"), {
      greenhouse: csv(ghBoards),
      lever: csv(leverBoards),
      custom: csv(customPages),
      workday: csv(workdaySites),
      ashby: csv(ashbyBoards),
      smartrecruiters: csv(srBoards),
      workable: csv(workableBoards),
      linkedin: csv(linkedinSearches),
    });
  }

  async function trackCompany(nameArg) {
    const name = (nameArg ?? trackName).trim();
    if (!name) return;
    setTracking(true);
    try {
      const call = httpsCallable(functions, "track_company", { timeout: 120_000 });
      const res = await call({ name });
      setUnverified(list => list.filter(x => x !== nameArg));
      setTrackResults(prev => [res.data, ...prev]);
      // Reflect the new watchlist entry in the fields below.
      const wl = await getDoc(doc(db, "users", user.uid, "watchlist", "companies"));
      if (wl.exists()) {
        setGhBoards((wl.data().greenhouse || []).join(", "));
        setLeverBoards((wl.data().lever || []).join(", "));
        setCustomPages((wl.data().custom || []).join(", "));
        setWorkdaySites((wl.data().workday || []).join(", "));
        setAshbyBoards((wl.data().ashby || []).join(", "));
        setSrBoards((wl.data().smartrecruiters || []).join(", "));
        setWorkableBoards((wl.data().workable || []).join(", "));
        setLinkedinSearches((wl.data().linkedin || []).join(", "));
      }
      setTrackName("");
    } catch (e) {
      setTrackResults(prev => [{ company: trackName, status: "error", detail: e.message }, ...prev]);
    } finally {
      setTracking(false);
    }
  }

  async function findCompanies() {
    if (!suggestRole.trim()) return;
    setSuggesting(true);
    setSuggestions([]);
    setUnverified([]);
    try {
      const call = httpsCallable(functions, "suggest_companies", { timeout: 300_000 });
      const res = await call({ role: suggestRole });
      const found = res.data?.companies || [];
      setSuggestions(found);
      setUnverified(res.data?.unverified || []);
      if (found.length === 0) setStatus("No verified boards found — try rewording the role");
    } catch (e) {
      setStatus(`Suggestion failed: ${e.message}`);
    } finally {
      setSuggesting(false);
    }
  }

  function addSuggestion(s) {
    if (s.ats === "greenhouse") {
      setGhBoards(prev => [...new Set([...csv(prev), s.slug])].join(", "));
    } else if (s.ats === "workday") {
      setWorkdaySites(prev => [...new Set([...csv(prev), s.slug])].join(", "));
    } else {
      setLeverBoards(prev => [...new Set([...csv(prev), s.slug])].join(", "));
    }
    setSuggestions(list => list.filter(x => x.slug !== s.slug));
  }

  if (!user) return <SignIn title="Settings" />;

  return (
    <main className="container">
      <Nav active="/settings" />
      <h1>Settings</h1>
      <p style={{ color: T.muted }}>
        Set up in order, top to bottom: say what you want, find companies,
        check the boards being watched, then tune how picky the engine is.
        Who you are — resume, work history, skills — is on the Profile page.
      </p>

      {/* ------------------------------------------------ step 1 */}
      <details className="panel tint-blue" open>
        <summary><span className="stepnum blue">1</span> What you&apos;re looking for</summary>
        <div className="panelbody">
          <span className="field-label">Target titles (comma-separated)</span>
          <input value={titles} onChange={e => setTitles(e.target.value)}
                 placeholder="e.g. grant writer, development officer" />
          <span className="field-label">
            Title synonyms — generated automatically about a minute after you
            save new titles; prune freely (anything here counts as a title match)
          </span>
          <textarea style={{ height: 70 }} value={titleSynonyms}
                    onChange={e => setTitleSynonyms(e.target.value)}
                    placeholder="(generated after you save new titles)" />
          <span className="field-label">
            Where do you want to work? (City, ST — separate several with
            semicolons; leave blank for anywhere)
          </span>
          <input value={locationsStr} onChange={e => setLocationsStr(e.target.value)}
                 placeholder="e.g. Richmond, VA" />
          <p className="hint">
            Jobs outside these places are filtered out before scoring — they
            never reach your queue and never cost an LLM call. Matching is
            by city name, so nearby towns matter — let the button below add
            them for you.
          </p>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            <select style={{ width: "auto" }} value={radius}
                    onChange={e => setRadius(e.target.value)}>
              <option value="10">within 10 miles</option>
              <option value="25">within 25 miles</option>
              <option value="50">within 50 miles</option>
            </select>
            <button className="btn" disabled={expanding} onClick={expandMetro}>
              {expanding
                ? <><span className="spinner sm" />Finding towns…</>
                : "Add nearby towns"}
            </button>
            <span className="hint" style={{ margin: 0 }}>
              around the first place in your list
            </span>
          </div>
          <span className="field-label">Work arrangement</span>
          <select value={workMode} onChange={e => setWorkMode(e.target.value)}>
            <option value="local_or_remote">
              On-site in my places, or fully remote
            </option>
            <option value="local_only">
              Only in my places — skip remote-only jobs
            </option>
            <option value="remote_only">
              Remote only — wherever the company is
            </option>
          </select>
          <span className="field-label">Exclude companies (comma-separated)</span>
          <input value={excludeCompanies} onChange={e => setExcludeCompanies(e.target.value)} />
        </div>
      </details>

      {/* ------------------------------------------------ step 2 */}
      <details className="panel tint-green">
        <summary><span className="stepnum green">2</span> Find companies to watch</summary>
        <div className="panelbody">
          <p className="hint" style={{ marginTop: 8 }}>
            Everything you Add or Track here is written into step 3&apos;s lists
            automatically — finish with Save settings to keep it. Your
            step-1 places and work arrangement steer suggestions: with
            locations set, you&apos;ll get employers that actually hire there.
          </p>
          <span className="field-label">Describe the role you want</span>
          <textarea style={{ height: 60 }}
            placeholder="e.g. entry-level marketing coordinator roles at consumer brands, remote-friendly"
            value={suggestRole}
            onChange={e => setSuggestRole(e.target.value)} />
          <div className="actions">
            <button className="btn-primary" disabled={suggesting} onClick={findCompanies}>
              {suggesting
                ? <><span className="spinner sm" />Searching — can take a minute</>
                : "Suggest companies"}
            </button>
          </div>
          {suggestions.map(s => (
            <div key={s.slug} className="subcard"
                 style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ flex: 1 }}>
                <strong>{s.company}</strong>{" "}
                <span className="hint" style={{ display: "inline" }}>
                  {s.ats} · {s.jobs} open roles
                  {s.ats === "workday" && " · letters prepped, you apply on their site"}
                </span>
                <div className="hint">
                  {(s.sample_titles || []).filter(Boolean).join(" · ")}
                </div>
              </div>
              <button className="btn" onClick={() => addSuggestion(s)}>Add</button>
            </div>
          ))}
          {unverified.length > 0 && (
            <>
              <p className="hint" style={{ marginTop: 14 }}>
                Also likely fits, but no supported job board was found
                automatically — Track resolves each one (career-page detection
                included):
              </p>
              {unverified.map((name) => (
                <div key={name} className="subcard"
                     style={{ display: "flex", alignItems: "center", gap: 12,
                              padding: "6px 12px" }}>
                  <strong style={{ flex: 1 }}>{name}</strong>
                  <button className="btn" disabled={tracking}
                          onClick={() => trackCompany(name)}>
                    {tracking ? <span className="spinner sm" /> : "Track"}
                  </button>
                </div>
              ))}
            </>
          )}

          <div className="orline">or track a specific company by name</div>
          <div style={{ display: "flex", gap: 8 }}>
            <input value={trackName}
                   placeholder="Company name — the engine figures out how they hire"
                   onChange={e => setTrackName(e.target.value)}
                   onKeyDown={e => e.key === "Enter" && trackCompany()} />
            <button className="btn-primary" disabled={tracking} onClick={() => trackCompany()}>
              {tracking
                ? <><span className="spinner sm" />Resolving…</>
                : "Track"}
            </button>
          </div>
          {trackResults.map((r, i) => {
            const color = r.status === "auto" ? "var(--ok)"
              : r.status === "tracked" ? "var(--accent)"
              : r.status === "manual" ? "var(--warn)" : "var(--danger)";
            const tag = r.status === "auto" ? "✓ automated"
              : r.status === "tracked" ? "tracking"
              : r.status === "manual" ? "manual submit"
              : r.status === "not_found" ? "not found" : "error";
            return (
              <div key={i} className="subcard"
                   style={{ borderLeft: `3px solid ${color}`, padding: "6px 10px" }}>
                <strong>{r.company}</strong>{" "}
                <span style={{ color, fontSize: 13 }}>{tag}</span>
                <div className="hint">{r.detail}</div>
              </div>
            );
          })}
        </div>
      </details>

      {/* ------------------------------------------------ step 3 */}
      <details className="panel tint-purple">
        <summary><span className="stepnum purple">3</span> Boards being watched</summary>
        <div className="panelbody">
          <p className="hint" style={{ marginTop: 8 }}>
            Filled automatically by step 2 — edit or prune freely. Every crawl
            (every 6 hours) pulls fresh jobs from all of these.
          </p>
          <p className="grouphead">Fully automated — the engine can submit for you</p>
          <span className="field-label">Greenhouse (the SLUG in boards.greenhouse.io/SLUG)</span>
          <input value={ghBoards} onChange={e => setGhBoards(e.target.value)} />
          <span className="field-label">Lever (jobs.lever.co/SLUG)</span>
          <input value={leverBoards} onChange={e => setLeverBoards(e.target.value)} />
          <span className="field-label">Ashby (jobs.ashbyhq.com/SLUG)</span>
          <input value={ashbyBoards} onChange={e => setAshbyBoards(e.target.value)} />
          <span className="field-label">SmartRecruiters (careers.smartrecruiters.com/COMPANY — case matters)</span>
          <input value={srBoards} onChange={e => setSrBoards(e.target.value)} />
          <span className="field-label">Workable (apply.workable.com/SLUG)</span>
          <input value={workableBoards} onChange={e => setWorkableBoards(e.target.value)} />

          <p className="grouphead">Watched — the engine preps the application, you apply on the company site</p>
          <span className="field-label">
            Workday career sites (full myworkdayjobs.com URLs). Workday requires
            a personal account, so the engine finds the jobs and writes the
            letter — the final application on their site is yours.
          </span>
          <input value={workdaySites} onChange={e => setWorkdaySites(e.target.value)}
                 placeholder="https://company.wd5.myworkdayjobs.com/External" />

          <p className="grouphead">LinkedIn saved searches — jobs found, then followed to the employer&apos;s own site</p>
          <span className="field-label">
            Searches, comma-separated. Use &quot;keywords | location&quot; to add a
            place (e.g. grant writer | Richmond, VA). No LinkedIn account is
            used — the engine reads the public listings and, where a posting
            links out to the company&apos;s own application site, follows it there.
          </span>
          <input value={linkedinSearches} onChange={e => setLinkedinSearches(e.target.value)}
                 placeholder="grant writer | Richmond VA, development director | remote" />

          <p className="grouphead">Career pages — crawled for job links</p>
          <span className="field-label">
            Careers page URLs (for companies on no supported board; JavaScript-only
            pages can&apos;t be read)
          </span>
          <input value={customPages} onChange={e => setCustomPages(e.target.value)}
                 placeholder="https://example.com/careers, https://…" />
        </div>
      </details>

      {/* ------------------------------------------------ step 4 */}
      <details className="panel tint-amber">
        <summary><span className="stepnum amber">4</span> Matching &amp; drafting behavior</summary>
        <div className="panelbody">
          <span className="field-label">Minimum match score (0–100)</span>
          <input type="number" min="0" max="100" value={minScore}
                 onChange={e => setMinScore(e.target.value)} />
          <p className="hint" style={{ color: "var(--warn)" }}>
            Every crawled posting scoring at or above this becomes a match.
            With auto-draft ON, each match immediately gets a cover letter
            written (several LLM calls each) — lower this carefully.
          </p>
          <label style={{ display: "block", marginTop: 12 }}>
            <input type="checkbox" checked={autoDraft}
                   onChange={e => setAutoDraft(e.target.checked)} /> Draft letters
            automatically <span className="hint" style={{ display: "inline" }}>
            (off: matches wait in the Matches tab and you pick which get letters)</span>
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            <input type="checkbox" checked={tailorResume}
                   onChange={e => setTailorResume(e.target.checked)} /> Tailor my
            resume for each application <span className="hint" style={{ display: "inline" }}>
            (a per-job PDF built from your profile facts — nothing invented.
            Off: your uploaded resume.pdf goes everywhere.)</span>
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            <input type="checkbox" checked={emailMatches}
                   onChange={e => setEmailMatches(e.target.checked)} /> Email me
            when new matches are found <span className="hint" style={{ display: "inline" }}>
            (one digest a day, never one per job — sent to the address on
            your Profile)</span>
          </label>
          <button className="btn" disabled={testingDigest}
                  style={{ marginTop: 8 }} onClick={sendTestDigest}>
            {testingDigest
              ? <><span className="spinner sm" />Sending…</>
              : "Send me a test digest now"}
          </button>
          <p className="hint">
            Builds the real email from your current matches and sends it
            immediately. Doesn&apos;t affect tomorrow&apos;s digest.
          </p>
          <span className="field-label">When a form asks for salary expectations</span>
          <select value={salaryStrategy}
                  onChange={e => setSalaryStrategy(e.target.value)}>
            <option value="exact">State my target exactly</option>
            <option value="range">Give a range around my target</option>
            <option value="negotiable">Say it&apos;s negotiable — no number</option>
          </select>
          <p className="hint">
            A number above the company&apos;s budget can auto-reject you before a
            human ever looks. A range or &quot;negotiable&quot; keeps you in play;
            your target itself is set on the Profile page.
          </p>
        </div>
      </details>

      <div className="savebar">
        <button onClick={save} disabled={saving}
                style={{ ...btnPrimary, padding: "10px 24px", fontSize: 16 }}>
          Save settings
        </button>
        <span style={{ marginLeft: 12 }}>
          {saving ? <Busy label="Saving…" /> : status}
        </span>
      </div>
    </main>
  );
}
