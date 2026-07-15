"use client";
// Settings — how the engine searches and drafts: matching preferences and
// the company watchlist. Who-you-are content (basics, resume, work history,
// writing samples) lives on /profile.
// Saving writes ONLY preferences + watchlist, so it can never clobber
// profile fields edited on the other page (and vice versa).

import { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup } from "firebase/auth";
import { doc, getDoc, setDoc, serverTimestamp } from "firebase/firestore";
import { httpsCallable } from "firebase/functions";
import { auth, db, functions, googleProvider } from "../../lib/firebase";
import { T, Nav, box, btn, btnPrimary, input, label } from "../ui";

export default function SettingsPage() {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("");

  const [titles, setTitles] = useState("");           // comma-separated
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [excludeCompanies, setExcludeCompanies] = useState("");
  const [minScore, setMinScore] = useState(70);
  const [autoDraft, setAutoDraft] = useState(true);
  const [salaryStrategy, setSalaryStrategy] = useState("exact");
  const [tailorResume, setTailorResume] = useState(true);
  const [ghBoards, setGhBoards] = useState("");       // comma-separated slugs
  const [leverBoards, setLeverBoards] = useState("");
  const [customPages, setCustomPages] = useState(""); // career page URLs
  const [workdaySites, setWorkdaySites] = useState(""); // myworkdayjobs URLs
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
        setRemoteOnly(!!p.remote_only);
        setExcludeCompanies((p.exclude_companies || []).join(", "));
        setMinScore(p.min_match_score ?? 70);
        setAutoDraft(p.auto_draft ?? true);
        setSalaryStrategy(p.salary_strategy || "exact");
        setTailorResume(p.tailor_resume ?? true);
      }
      const wl = await getDoc(doc(db, "users", user.uid, "watchlist", "companies"));
      if (wl.exists()) {
        setGhBoards((wl.data().greenhouse || []).join(", "));
        setLeverBoards((wl.data().lever || []).join(", "));
        setCustomPages((wl.data().custom || []).join(", "));
        setWorkdaySites((wl.data().workday || []).join(", "));
      }
    })();
  }, [user]);

  const csv = (s) => s.split(",").map(x => x.trim()).filter(Boolean);

  async function save() {
    setStatus("Saving…");
    await setDoc(doc(db, "users", user.uid), {
      preferences: {
        titles: csv(titles),
        remote_only: remoteOnly,
        exclude_companies: csv(excludeCompanies),
        min_match_score: Number(minScore) || 70,
        auto_draft: autoDraft,
        salary_strategy: salaryStrategy,
        tailor_resume: tailorResume,
      },
      updatedAt: serverTimestamp(),
    }, { merge: true });
    await setDoc(doc(db, "users", user.uid, "watchlist", "companies"), {
      greenhouse: csv(ghBoards),
      lever: csv(leverBoards),
      custom: csv(customPages),
      workday: csv(workdaySites),
    });
    setStatus("Saved ✓");
    setTimeout(() => setStatus(""), 2500);
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

  if (!user) {
    return (
      <main style={{ padding: 40 }}>
        <h1>Settings</h1>
        <button style={btnPrimary} onClick={() => signInWithPopup(auth, googleProvider)}>
          Sign in with Google
        </button>
      </main>
    );
  }

  return (
    <main style={{ padding: "24px 40px 40px", maxWidth: 760, margin: "0 auto" }}>
      <Nav active="/settings" />
      <h1>Settings</h1>
      <p style={{ color: T.muted }}>
        How the engine searches: which companies it watches, what counts as a
        match, and whether letters are drafted automatically. Who you are —
        resume, work history, skills — is on the Profile page.
      </p>

      <section style={box}>
        <h2>Matching</h2>
        <span style={label}>Target titles (comma-separated)</span>
        <input style={input} value={titles} onChange={e => setTitles(e.target.value)} />
        <span style={label}>Exclude companies (comma-separated)</span>
        <input style={input} value={excludeCompanies} onChange={e => setExcludeCompanies(e.target.value)} />
        <span style={label}>Minimum match score (0-100)</span>
        <input style={input} type="number" min="0" max="100" value={minScore}
               onChange={e => setMinScore(e.target.value)} />
        <p style={{ color: T.warn, fontSize: 13, marginTop: -6 }}>
          Every crawled posting scoring at or above this becomes a match.
          With auto-draft ON, each match immediately gets a cover letter
          written (several LLM calls each) — lower this carefully.
        </p>
        <span style={label}>When a form asks for salary expectations</span>
        <select style={input} value={salaryStrategy}
                onChange={e => setSalaryStrategy(e.target.value)}>
          <option value="exact">State my target exactly</option>
          <option value="range">Give a range around my target</option>
          <option value="negotiable">Say it&apos;s negotiable — no number</option>
        </select>
        <p style={{ color: T.muted, fontSize: 13, marginTop: -6 }}>
          A number above the company&apos;s budget can auto-reject you before a
          human ever looks. A range or &quot;negotiable&quot; keeps you in play;
          your target itself is set on the Profile page.
        </p>
        <label style={{ display: "block", marginBottom: 8 }}>
          <input type="checkbox" checked={tailorResume}
                 onChange={e => setTailorResume(e.target.checked)} /> Tailor my
          resume for each application <span style={{ color: T.muted, fontSize: 13 }}>
          (a per-job PDF built from your profile facts — reordered and reworded
          toward the posting, nothing invented. Off: your uploaded resume.pdf
          goes everywhere.)</span>
        </label>
        <label style={{ display: "block", marginBottom: 8 }}>
          <input type="checkbox" checked={autoDraft}
                 onChange={e => setAutoDraft(e.target.checked)} /> Draft letters
          automatically <span style={{ color: T.muted, fontSize: 13 }}>
          (off: matches wait in the Matches tab and you pick which get letters)</span>
        </label>
        <label style={{ display: "block", marginBottom: 8 }}>
          <input type="checkbox" checked={remoteOnly}
                 onChange={e => setRemoteOnly(e.target.checked)} /> Remote only
        </label>
      </section>

      <section style={box}>
        <h2>Company watchlist</h2>

        <h3 style={{ marginTop: 0 }}>Track a company by name</h3>
        <p style={{ color: T.muted, fontSize: 13 }}>
          Type a company (e.g. Acme Corp). The engine finds how they run job
          applications and sets up what it can — no need to know their ATS.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <input style={{ ...input, margin: 0 }} value={trackName}
                 placeholder="Company name"
                 onChange={e => setTrackName(e.target.value)}
                 onKeyDown={e => e.key === "Enter" && trackCompany()} />
          <button style={btnPrimary} disabled={tracking} onClick={trackCompany}>
            {tracking ? "Resolving…" : "Track"}
          </button>
        </div>
        {trackResults.map((r, i) => {
          const color = r.status === "auto" ? T.ok
            : r.status === "tracked" ? T.accent
            : r.status === "manual" ? T.warn : T.danger;
          const tag = r.status === "auto" ? "✓ automated"
            : r.status === "tracked" ? "tracking"
            : r.status === "manual" ? "manual submit"
            : r.status === "not_found" ? "not found" : "error";
          return (
            <div key={i} style={{ borderLeft: `3px solid ${color}`, padding: "6px 10px", margin: "8px 0", background: T.panelAlt, borderRadius: 4 }}>
              <strong>{r.company}</strong>{" "}
              <span style={{ color, fontSize: 13 }}>{tag}</span>
              <div style={{ color: T.muted, fontSize: 13 }}>{r.detail}</div>
            </div>
          );
        })}

        <h3 style={{ marginTop: 24 }}>Or enter boards manually</h3>
        <span style={label}>Greenhouse board slugs (comma-separated — the SLUG in boards.greenhouse.io/SLUG)</span>
        <input style={input} value={ghBoards} onChange={e => setGhBoards(e.target.value)} />
        <span style={label}>Lever board slugs (jobs.lever.co/SLUG)</span>
        <input style={input} value={leverBoards} onChange={e => setLeverBoards(e.target.value)} />
        <span style={label}>
          Career page URLs (comma-separated — for companies not on Greenhouse/Lever;
          each crawl finds new postings on these pages. JavaScript-only pages can&apos;t be read.)
        </span>
        <input style={input} value={customPages} onChange={e => setCustomPages(e.target.value)}
               placeholder="https://example.com/careers, https://…" />
        <span style={label}>
          Workday career sites (comma-separated myworkdayjobs.com URLs — jobs are
          discovered and drafted; Workday submission stays manual)
        </span>
        <input style={input} value={workdaySites} onChange={e => setWorkdaySites(e.target.value)}
               placeholder="https://company.wd5.myworkdayjobs.com/External" />

        <h3 style={{ marginTop: 20 }}>Find companies for me</h3>
        <p style={{ color: T.muted, fontSize: 13 }}>
          Describe the role you want; the engine proposes companies and only
          shows ones with a live, supported job board. Adding one puts it in
          the fields above — hit Save settings to keep it.
        </p>
        <textarea
          style={{ ...input, height: 60 }}
          placeholder="e.g. entry-level marketing coordinator roles at consumer brands, remote-friendly"
          value={suggestRole}
          onChange={e => setSuggestRole(e.target.value)}
        />
        <button style={btnPrimary} disabled={suggesting} onClick={findCompanies}>
          {suggesting ? "Searching… (can take a minute)" : "Suggest companies"}
        </button>
        {suggestions.map(s => (
          <div key={s.slug} style={{
            display: "flex", alignItems: "center", gap: 12,
            background: T.panelAlt, border: `1px solid ${T.border}`,
            borderRadius: 6, padding: "8px 12px", marginTop: 8,
          }}>
            <div style={{ flex: 1 }}>
              <strong>{s.company}</strong>{" "}
              <span style={{ color: T.muted, fontSize: 13 }}>
                {s.ats} · {s.jobs} open roles
                {s.ats === "workday" && " · found & drafted for you, you submit"}
              </span>
              <div style={{ color: T.muted, fontSize: 12 }}>
                {(s.sample_titles || []).filter(Boolean).join(" · ")}
              </div>
            </div>
            <button style={btn} onClick={() => addSuggestion(s)}>Add</button>
          </div>
        ))}
        {unverified.length > 0 && (
          <>
            <p style={{ color: T.muted, fontSize: 13, marginTop: 16, marginBottom: 4 }}>
              Also likely fits, but no supported job board was found
              automatically. Track resolves each one properly (careers-page
              detection included):
            </p>
            {unverified.map((name) => (
              <div key={name} style={{
                display: "flex", alignItems: "center", gap: 12,
                background: T.panelAlt, border: `1px solid ${T.border}`,
                borderRadius: 6, padding: "6px 12px", marginTop: 6,
              }}>
                <strong style={{ flex: 1 }}>{name}</strong>
                <button style={btn} disabled={tracking}
                        onClick={() => trackCompany(name)}>
                  {tracking ? "…" : "Track"}
                </button>
              </div>
            ))}
          </>
        )}
      </section>

      <div style={{ position: "sticky", bottom: 0, background: "#15171c", padding: "12px 0" }}>
        <button onClick={save} style={{ ...btnPrimary, padding: "10px 24px", fontSize: 16 }}>
          Save settings
        </button>
        <span style={{ marginLeft: 12 }}>{status}</span>
      </div>
    </main>
  );
}
