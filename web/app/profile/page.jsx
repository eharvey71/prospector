"use client";
// Profile editor — replaces seed.py for day-to-day profile management.
// Everything writes to users/{uid} (and watchlist/companies), which the
// deployed firestore.rules already permit for the signed-in owner.
// Resume uploads land at users/{uid}/resume.pdf — the exact path the
// submission worker's adapter fetches from.

import { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup } from "firebase/auth";
import { deleteDoc, doc, getDoc, onSnapshot, setDoc, serverTimestamp } from "firebase/firestore";
import { httpsCallable } from "firebase/functions";
import { ref, uploadBytes, getMetadata } from "firebase/storage";
import { auth, db, functions, googleProvider, storage } from "../../lib/firebase";

const EMPTY_ROLE = { company: "", title: "", start: "", end: "", bullets: [""] };
const EMPTY_SAMPLE = { title: "", text: "" };

const T = {
  panel: "#1d2026", panelAlt: "#22262e", border: "#33383f",
  text: "#e2e4e9", muted: "#9aa1ad", ok: "#7fbf7f", danger: "#e06c75",
  accent: "#6f9ff3",
};
const box = { background: T.panel, border: `1px solid ${T.border}`, borderRadius: 8, padding: 16, marginBottom: 20 };
const input = {
  display: "block", width: "100%", margin: "4px 0 12px", padding: 8,
  boxSizing: "border-box", background: T.panelAlt, color: T.text,
  border: `1px solid ${T.border}`, borderRadius: 6,
};
const label = { fontSize: 13, fontWeight: 600, color: T.muted };
const btn = {
  background: T.panelAlt, color: T.text, border: `1px solid ${T.border}`,
  borderRadius: 6, padding: "8px 16px", cursor: "pointer",
};
const btnPrimary = { ...btn, background: T.accent, color: "#10131a", border: "none", fontWeight: 600 };

export default function ProfilePage() {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("");
  const [resumeInfo, setResumeInfo] = useState(null);
  const [extraction, setExtraction] = useState(null);

  // Flat profile fields
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [location, setLocation] = useState("");
  const [workAuth, setWorkAuth] = useState("");
  const [salaryTarget, setSalaryTarget] = useState("");
  const [skills, setSkills] = useState("");           // comma-separated in UI
  const [titles, setTitles] = useState("");           // comma-separated
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [excludeCompanies, setExcludeCompanies] = useState("");
  const [minScore, setMinScore] = useState(70);
  const [autoDraft, setAutoDraft] = useState(true);
  const [history, setHistory] = useState([]);
  const [samples, setSamples] = useState([]);
  const [ghBoards, setGhBoards] = useState("");       // comma-separated slugs
  const [leverBoards, setLeverBoards] = useState("");
  const [customPages, setCustomPages] = useState(""); // career page URLs
  const [workdaySites, setWorkdaySites] = useState(""); // myworkdayjobs URLs
  const [suggestRole, setSuggestRole] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [suggestions, setSuggestions] = useState([]); // verified boards
  const [trackName, setTrackName] = useState("");
  const [tracking, setTracking] = useState(false);
  const [trackResults, setTrackResults] = useState([]); // resolution reports

  useEffect(() => onAuthStateChanged(auth, setUser), []);

  // Extraction suggestion appears live once the engine finishes parsing an
  // uploaded resume (usually well under a minute after upload).
  useEffect(() => {
    if (!user) return;
    return onSnapshot(
      doc(db, "users", user.uid, "resume_extraction", "latest"),
      (snap) => setExtraction(snap.exists() ? snap.data() : null)
    );
  }, [user]);

  // Load existing profile + watchlist + resume status
  useEffect(() => {
    if (!user) return;
    (async () => {
      const snap = await getDoc(doc(db, "users", user.uid));
      if (snap.exists()) {
        const d = snap.data();
        setName(d.name || "");
        setEmail(d.email || user.email || "");
        setLocation(d.location || "");
        setWorkAuth(d.work_auth || "");
        setSalaryTarget(d.salary_target || "");
        setSkills((d.skills || []).join(", "));
        setTitles((d.preferences?.titles || []).join(", "));
        setRemoteOnly(!!d.preferences?.remote_only);
        setExcludeCompanies((d.preferences?.exclude_companies || []).join(", "));
        setMinScore(d.preferences?.min_match_score ?? 70);
        setAutoDraft(d.preferences?.auto_draft ?? true);
        setHistory((d.work_history || []).map(r => ({
          company: r.company || "", title: r.title || "",
          start: r.start || "", end: r.end || "",
          bullets: Array.isArray(r.bullets) && r.bullets.length ? r.bullets : [""],
        })));
        setSamples((d.writing_samples || []).map(s => ({
          title: s.title || "", text: s.text || "",
        })));
      } else {
        setEmail(user.email || "");
      }
      const wl = await getDoc(doc(db, "users", user.uid, "watchlist", "companies"));
      if (wl.exists()) {
        setGhBoards((wl.data().greenhouse || []).join(", "));
        setLeverBoards((wl.data().lever || []).join(", "));
        setCustomPages((wl.data().custom || []).join(", "));
        setWorkdaySites((wl.data().workday || []).join(", "));
      }
      try {
        const meta = await getMetadata(ref(storage, `users/${user.uid}/resume.pdf`));
        setResumeInfo(`resume.pdf uploaded ${new Date(meta.updated).toLocaleString()}`);
      } catch {
        setResumeInfo(null);
      }
    })();
  }, [user]);

  const csv = (s) => s.split(",").map(x => x.trim()).filter(Boolean);

  async function save() {
    setStatus("Saving…");
    const profile = {
      name,
      email,
      location: location || null,
      work_auth: workAuth || null,
      salary_target: salaryTarget || null,
      skills: csv(skills),
      work_history: history.map(r => ({
        company: r.company, title: r.title, start: r.start,
        end: r.end || null,
        bullets: (r.bullets || []).filter(Boolean),
      })),
      writing_samples: samples.filter(s => s.title || s.text),
      preferences: {
        titles: csv(titles),
        remote_only: remoteOnly,
        exclude_companies: csv(excludeCompanies),
        min_match_score: Number(minScore) || 70,
        auto_draft: autoDraft,
      },
      updatedAt: serverTimestamp(),
    };
    await setDoc(doc(db, "users", user.uid), profile, { merge: true });
    await setDoc(doc(db, "users", user.uid, "watchlist", "companies"), {
      greenhouse: csv(ghBoards),
      lever: csv(leverBoards),
      custom: csv(customPages),
      workday: csv(workdaySites),
    });
    setStatus("Saved ✓");
    setTimeout(() => setStatus(""), 2500);
  }

  async function uploadResume(file) {
    if (!file) return;
    if (file.type !== "application/pdf") {
      setStatus("Resume must be a PDF");
      return;
    }
    setStatus("Uploading resume…");
    await uploadBytes(ref(storage, `users/${user.uid}/resume.pdf`), file, {
      contentType: "application/pdf",
    });
    setResumeInfo(`resume.pdf uploaded ${new Date().toLocaleString()}`);
    setStatus("Resume uploaded ✓");
    setTimeout(() => setStatus(""), 2500);
  }

  async function applyExtraction() {
    const x = extraction;
    if (x.name) setName(x.name);
    if (x.location) setLocation(x.location);
    if (x.work_auth) setWorkAuth(x.work_auth);
    if (x.skills?.length) {
      const merged = new Set([...csv(skills), ...x.skills]);
      setSkills([...merged].join(", "));
    }
    if (x.work_history?.length) {
      setHistory(x.work_history.map(r => ({
        ...r, end: r.end || "", bullets: r.bullets?.length ? r.bullets : [""],
      })));
    }
    await dismissExtraction();
    setStatus("Applied — review the fields below, then Save profile");
  }

  async function dismissExtraction() {
    await deleteDoc(doc(db, "users", user.uid, "resume_extraction", "latest"));
  }

  async function trackCompany() {
    if (!trackName.trim()) return;
    setTracking(true);
    try {
      const call = httpsCallable(functions, "track_company", { timeout: 120_000 });
      const res = await call({ name: trackName.trim() });
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
    try {
      const call = httpsCallable(functions, "suggest_companies", { timeout: 300_000 });
      const res = await call({ role: suggestRole });
      const found = res.data?.companies || [];
      setSuggestions(found);
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
    } else {
      setLeverBoards(prev => [...new Set([...csv(prev), s.slug])].join(", "));
    }
    setSuggestions(list => list.filter(x => x.slug !== s.slug));
  }

  // --- small helpers for list editing ---
  const setRole = (i, patch) =>
    setHistory(h => h.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const setSample = (i, patch) =>
    setSamples(s => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  if (!user) {
    return (
      <main style={{ padding: 40 }}>
        <h1>Profile</h1>
        <button style={btnPrimary} onClick={() => signInWithPopup(auth, googleProvider)}>
          Sign in with Google
        </button>
      </main>
    );
  }

  return (
    <main style={{ padding: 40, maxWidth: 760, margin: "0 auto" }}>
      <nav style={{ marginBottom: 20 }}>
        <a href="/" style={{ color: T.accent }}>← Review queue</a>
      </nav>
      <h1>Profile</h1>
      <p style={{ color: T.muted }}>
        This is what the engine knows about you. Matching scores against it,
        and cover letters can only claim what&apos;s written here.
      </p>

      <section style={box}>
        <h2>Basics</h2>
        <span style={label}>Name</span>
        <input style={input} value={name} onChange={e => setName(e.target.value)} />
        <span style={label}>Email (goes on applications)</span>
        <input style={input} value={email} onChange={e => setEmail(e.target.value)} />
        <span style={label}>Location</span>
        <input style={input} value={location} onChange={e => setLocation(e.target.value)} />
        <span style={label}>Work authorization</span>
        <input style={input} value={workAuth} onChange={e => setWorkAuth(e.target.value)} />
        <span style={label}>Salary target (optional)</span>
        <input style={input} value={salaryTarget} onChange={e => setSalaryTarget(e.target.value)} />
        <span style={label}>Skills (comma-separated)</span>
        <textarea style={{ ...input, height: 70 }} value={skills} onChange={e => setSkills(e.target.value)} />
      </section>

      <section style={box}>
        <h2>Resume</h2>
        <p style={{ color: resumeInfo ? T.ok : T.danger }}>
          {resumeInfo || "No resume uploaded — submissions will escalate without one."}
        </p>
        <input type="file" accept="application/pdf"
               onChange={e => uploadResume(e.target.files?.[0])} />

        {extraction && (
          <div style={{ ...box, background: T.panelAlt, borderColor: T.accent, marginTop: 16, marginBottom: 0 }}>
            <strong style={{ color: T.accent }}>Extracted from your resume</strong>
            <p style={{ color: T.muted, fontSize: 13 }}>
              Nothing is saved until you apply it, review the fields, and hit
              Save profile. Applying replaces the work-history form with the
              extracted roles and merges skills.
            </p>
            <ul style={{ fontSize: 14 }}>
              {extraction.name && <li>Name: {extraction.name}</li>}
              {extraction.location && <li>Location: {extraction.location}</li>}
              {extraction.work_auth && <li>Work authorization: {extraction.work_auth}</li>}
              {extraction.skills?.length > 0 && <li>{extraction.skills.length} skills: {extraction.skills.join(", ")}</li>}
              {extraction.work_history?.length > 0 && (
                <li>{extraction.work_history.length} roles: {extraction.work_history.map(r => `${r.title} @ ${r.company}`).join("; ")}</li>
              )}
            </ul>
            <button style={btnPrimary} onClick={applyExtraction}>Apply to form</button>{" "}
            <button style={btn} onClick={dismissExtraction}>Dismiss</button>
          </div>
        )}
      </section>

      <section style={box}>
        <h2>Work history</h2>
        {history.map((r, i) => (
          <div key={i} style={{ ...box, background: T.panelAlt }}>
            <span style={label}>Company</span>
            <input style={input} value={r.company} onChange={e => setRole(i, { company: e.target.value })} />
            <span style={label}>Title</span>
            <input style={input} value={r.title} onChange={e => setRole(i, { title: e.target.value })} />
            <div style={{ display: "flex", gap: 12 }}>
              <div style={{ flex: 1 }}>
                <span style={label}>Start (YYYY-MM)</span>
                <input style={input} value={r.start} onChange={e => setRole(i, { start: e.target.value })} />
              </div>
              <div style={{ flex: 1 }}>
                <span style={label}>End (blank = current)</span>
                <input style={input} value={r.end} onChange={e => setRole(i, { end: e.target.value })} />
              </div>
            </div>
            <span style={label}>Bullets (one per line — these are the facts letters can use)</span>
            <textarea
              style={{ ...input, height: 110 }}
              value={(r.bullets || []).join("\n")}
              onChange={e => setRole(i, { bullets: e.target.value.split("\n") })}
            />
            <button style={btn} onClick={() => setHistory(h => h.filter((_, j) => j !== i))}>
              Remove role
            </button>
          </div>
        ))}
        <button style={btn} onClick={() => setHistory(h => [...h, { ...EMPTY_ROLE }])}>
          + Add role
        </button>
      </section>

      <section style={box}>
        <h2>Writing samples</h2>
        <p style={{ color: T.muted }}>
          Real prose in your voice — this is what keeps cover letters from
          sounding AI-generated. Emails, blog posts, docs — anything you wrote.
        </p>
        {samples.map((s, i) => (
          <div key={i} style={{ ...box, background: T.panelAlt }}>
            <span style={label}>Title</span>
            <input style={input} value={s.title} onChange={e => setSample(i, { title: e.target.value })} />
            <span style={label}>Text</span>
            <textarea style={{ ...input, height: 140 }} value={s.text}
                      onChange={e => setSample(i, { text: e.target.value })} />
            <button style={btn} onClick={() => setSamples(x => x.filter((_, j) => j !== i))}>
              Remove sample
            </button>
          </div>
        ))}
        <button style={btn} onClick={() => setSamples(x => [...x, { ...EMPTY_SAMPLE }])}>
          + Add sample
        </button>
      </section>

      <section style={box}>
        <h2>Matching preferences</h2>
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
        <label style={{ display: "block", marginBottom: 8 }}>
          <input type="checkbox" checked={autoDraft}
                 onChange={e => setAutoDraft(e.target.checked)} /> Draft letters
          automatically <span style={{ color: T.muted, fontSize: 13 }}>
          (off: matches wait in the review page and you pick which get letters)</span>
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
          Type a company (e.g. Pearson). The engine finds how they run job
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
          the fields above — hit Save profile to keep it.
        </p>
        <textarea
          style={{ ...input, height: 60 }}
          placeholder="e.g. solutions engineering roles in ed-tech or AI products, remote-friendly"
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
              </span>
              <div style={{ color: T.muted, fontSize: 12 }}>
                {(s.sample_titles || []).filter(Boolean).join(" · ")}
              </div>
            </div>
            <button style={btn} onClick={() => addSuggestion(s)}>Add</button>
          </div>
        ))}
      </section>

      <div style={{ position: "sticky", bottom: 0, background: "#15171c", padding: "12px 0" }}>
        <button onClick={save} style={{ ...btnPrimary, padding: "10px 24px", fontSize: 16 }}>
          Save profile
        </button>
        <span style={{ marginLeft: 12 }}>{status}</span>
      </div>
    </main>
  );
}
