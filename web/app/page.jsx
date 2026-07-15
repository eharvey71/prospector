"use client";
// Queue page: one tab per pipeline stage instead of a single giant list.

import { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup } from "firebase/auth";
import {
  collection, doc, getDoc, limit, onSnapshot, orderBy, query,
  serverTimestamp, updateDoc, where, arrayUnion,
} from "firebase/firestore";
import { httpsCallable } from "firebase/functions";
import { ref as storageRef, getDownloadURL } from "firebase/storage";
import { auth, db, functions, googleProvider, storage } from "../lib/firebase";
import { T, Nav, card, btn, btnPrimary } from "./ui";

const ANSWER_LABELS = {
  why_company: "Why this company",
  salary: "Salary expectations",
  work_auth: "Work authorization",
};

// --- match insight card ------------------------------------------------
// One block that answers, in order: how good is this fit, why is it in this
// queue, why does it match, what's wrong with it, and what to do next.

const tierOf = (score, threshold) =>
  score >= 85 ? { label: "Strong match", color: T.ok }
  : score >= threshold ? { label: "Good match", color: T.accent }
  : { label: "Stretch", color: T.warn };

// Older applications stored red flags as plain strings.
const normFlag = (f) =>
  typeof f === "string" ? { severity: "concern", topic: "", detail: f } : f;

const sectionLabel = (color) => ({
  fontSize: 13, fontWeight: 600, color, marginTop: 10,
});

function FlagList({ flags, color }) {
  return (
    <ul style={{ margin: "4px 0 0" }}>
      {flags.map((f, i) => (
        <li key={i} style={{ marginBottom: 2 }}>
          {f.topic ? <strong style={{ color }}>{f.topic} — </strong> : null}
          {f.detail}
        </li>
      ))}
    </ul>
  );
}

export function MatchInsight({ app, threshold, queue }) {
  const m = app.match || {};
  const score = m.score ?? 0;
  const tier = tierOf(score, threshold);
  const flags = (m.red_flags || []).map(normFlag);
  const blockers = flags.filter((f) => f.severity === "blocker");
  const concerns = flags.filter((f) => f.severity !== "blocker");

  const whyHere = app.user_added
    ? score < threshold
      ? `Below your ${threshold}-point bar — showing only because you added it yourself.`
      : "You added this job yourself."
    : `Cleared your ${threshold}-point bar${queue === "matched" ? "; auto-draft is off, so it waits for your go-ahead" : ""}.`;

  const nextStep = queue === "review"
    ? "Read and edit the letter below — edits save when you click away. Approve to submit it, Reject to drop it."
    : blockers.length > 0
      ? "A cover letter can't fix a blocker — Skip unless the flag is wrong."
      : tier.label === "Stretch"
        ? "Long shot — spend a letter here only if you'd take it over a stronger match."
        : "Good odds — write the letter next; nothing is sent until you approve it.";

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 6 }}>
        <span style={{
          padding: "2px 10px", borderRadius: 999, fontSize: 13, fontWeight: 600,
          border: `1px solid ${tier.color}`, color: tier.color,
        }}>
          {score} · {tier.label}
        </span>
        <span style={{ color: T.muted, fontSize: 13 }}>{whyHere}</span>
      </div>

      {m.summary && (
        <p style={{ margin: "10px 0 0", fontStyle: "italic" }}>{m.summary}</p>
      )}

      {(m.reasons || []).length > 0 && (
        <>
          <div style={sectionLabel(T.ok)}>Why you match</div>
          <ul style={{ margin: "4px 0 0" }}>
            {(m.reasons || []).map((r, i) => <li key={i} style={{ marginBottom: 2 }}>{r}</li>)}
          </ul>
        </>
      )}

      {blockers.length > 0 && (
        <>
          <div style={sectionLabel(T.danger)}>
            Blockers — hard requirements you don&apos;t meet
          </div>
          <FlagList flags={blockers} color={T.danger} />
        </>
      )}

      {concerns.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary style={{ ...sectionLabel(T.warn), marginTop: 0, cursor: "pointer" }}>
            {concerns.length} concern{concerns.length > 1 ? "s" : ""} — weaker points a letter could address
          </summary>
          <FlagList flags={concerns} color={T.warn} />
        </details>
      )}

      <p style={{ color: T.muted, fontSize: 13, margin: "12px 0 8px" }}>{nextStep}</p>
    </div>
  );
}

// The adapter's fill sheet: every answer it used (to copy into the form —
// the engine's browser session is gone, so nothing persists on the ATS
// side) plus the fields it couldn't answer.
function FillSheet({ sheet }) {
  if (!sheet || sheet.length === 0) return null;
  const needs = sheet.filter((e) => e.status !== "filled");
  const filled = sheet.filter((e) => e.status === "filled");
  return (
    <div style={{ margin: "10px 0" }}>
      {needs.length > 0 && (
        <div style={{
          background: T.panelAlt, borderLeft: `3px solid ${T.warn}`,
          borderRadius: 6, padding: "10px 12px", marginBottom: 8,
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: T.warn, marginBottom: 4 }}>
            Only you can answer these ({needs.length})
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>
            {needs.map((e, i) => (
              <li key={i} style={{ marginBottom: 6 }}>
                {e.field}
                {e.suggestion && (
                  <div style={{ fontSize: 13, marginTop: 2 }}>
                    <span style={{ color: T.accent }}>suggested: </span>
                    <span style={{ color: T.muted }}>{e.suggestion}</span>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {filled.length > 0 && (
        <details style={{
          background: T.panelAlt, borderRadius: 6, padding: "10px 12px",
        }}>
          <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600, color: T.ok }}>
            Answers for everything else ({filled.length}) — copy into the form
          </summary>
          <table style={{ marginTop: 8, fontSize: 14, borderSpacing: 0 }}>
            <tbody>
              {filled.map((e, i) => (
                <tr key={i}>
                  <td style={{ color: T.muted, paddingRight: 14, paddingBottom: 4,
                               verticalAlign: "top", whiteSpace: "nowrap" }}>
                    {e.field}
                  </td>
                  <td style={{ paddingBottom: 4 }}>{e.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

// Storage paths -> clickable links that open the screenshot in a new tab.
// storage.rules already lets the signed-in owner read users/{uid}/**.
function ScreenshotLinks({ paths }) {
  const [urls, setUrls] = useState({});
  useEffect(() => {
    (paths || []).forEach(async (p) => {
      try {
        const u = await getDownloadURL(storageRef(storage, p));
        setUrls((prev) => (prev[p] ? prev : { ...prev, [p]: u }));
      } catch { /* not readable — leave it as plain text */ }
    });
  }, [paths]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!paths || paths.length === 0) return null;
  return (
    <div style={{ fontSize: 13, marginTop: 8 }}>
      <span style={{ color: T.muted }}>Screenshots: </span>
      {paths.map((p) => {
        const name = p.split("/").pop().replace(/\.png$/, "");
        return urls[p] ? (
          <a key={p} href={urls[p]} target="_blank" rel="noreferrer"
             style={{ color: T.accent, marginRight: 12 }}>
            {name} ↗
          </a>
        ) : (
          <span key={p} style={{ color: T.muted, marginRight: 12 }}>{name}</span>
        );
      })}
    </div>
  );
}

function ScreeningAnswers({ answers }) {
  const entries = [
    ...Object.entries(answers || {}).filter(([k, v]) => k !== "extra" && v),
    ...Object.entries(answers?.extra || {}),
  ];
  if (entries.length === 0) return <p style={{ color: T.muted }}>None generated.</p>;
  return entries.map(([k, v]) => (
    <div key={k} style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: T.muted }}>
        {ANSWER_LABELS[k] || k}
      </div>
      <div style={{ whiteSpace: "pre-wrap" }}>{v}</div>
    </div>
  ));
}

export default function ReviewQueue() {
  const [user, setUser] = useState(null);
  const [apps, setApps] = useState([]);
  const [escalated, setEscalated] = useState([]);
  const [jobUrl, setJobUrl] = useState("");
  const [addStatus, setAddStatus] = useState("");
  const [matches, setMatches] = useState([]);       // awaiting manual drafting
  const [inflight, setInflight] = useState([]);     // approved/queued/submitting
  const [done, setDone] = useState([]);             // submitted/failed
  const [postings, setPostings] = useState({});     // posting_id -> {title, company}
  const [draftingIds, setDraftingIds] = useState([]);
  const [threshold, setThreshold] = useState(70);   // preferences.min_match_score
  const [tab, setTab] = useState("review");

  useEffect(() => onAuthStateChanged(auth, setUser), []);

  useEffect(() => {
    if (!user) return;
    getDoc(doc(db, "users", user.uid)).then((s) => {
      const t = s.data()?.preferences?.min_match_score;
      if (typeof t === "number") setThreshold(t);
    });
  }, [user]);

  useEffect(() => {
    if (!user) return;
    const base = collection(db, "users", user.uid, "applications");
    const unsub1 = onSnapshot(
      query(base, where("state", "==", "in_review"), orderBy("match.score", "desc")),
      (snap) => setApps(snap.docs.map((d) => ({ id: d.id, ...d.data() })))
    );
    const unsub2 = onSnapshot(
      query(base, where("state", "==", "needs_human")),
      (snap) => setEscalated(snap.docs.map((d) => ({ id: d.id, ...d.data() })))
    );
    const unsub3 = onSnapshot(
      query(base, where("state", "==", "matched"), orderBy("match.score", "desc")),
      (snap) => setMatches(snap.docs.map((d) => ({ id: d.id, ...d.data() })))
    );
    const unsub4 = onSnapshot(
      query(base, where("state", "in", ["approved", "queued", "submitting"]),
            orderBy("updatedAt", "desc")),
      (snap) => setInflight(snap.docs.map((d) => ({ id: d.id, ...d.data() })))
    );
    const unsub5 = onSnapshot(
      query(base, where("state", "in", ["submitted", "failed"]),
            orderBy("updatedAt", "desc"), limit(25)),
      (snap) => setDone(snap.docs.map((d) => ({ id: d.id, ...d.data() })))
    );
    return () => { unsub1(); unsub2(); unsub3(); unsub4(); unsub5(); };
  }, [user]);

  // Titles/companies for every card, whatever queue it's in.
  useEffect(() => {
    [...matches, ...apps, ...escalated, ...inflight, ...done].forEach(async (m) => {
      if (!m.posting_id || postings[m.posting_id]) return;
      const snap = await getDoc(doc(db, "jobPostings", m.posting_id));
      if (snap.exists()) {
        const p = snap.data();
        setPostings(prev => ({
          ...prev,
          [m.posting_id]: { title: p.title, company: p.company, url: p.url },
        }));
      }
    });
  }, [matches, apps, escalated, inflight, done]); // eslint-disable-line react-hooks/exhaustive-deps

  // Title @ company, linking to the original posting in a new tab.
  const jobLine = (a) => {
    const p = postings[a.posting_id];
    if (!p) return "…";
    const line = `${p.title} @ ${p.company}`;
    return p.url ? (
      <a href={p.url} target="_blank" rel="noreferrer"
         style={{ color: "inherit", textDecoration: "none" }}>
        {line} <span style={{ color: T.accent, fontSize: "0.8em" }}>↗</span>
      </a>
    ) : line;
  };

  async function addJob() {
    if (!jobUrl.trim()) return;
    setAddStatus("Reading the posting… (up to a minute)");
    try {
      const call = httpsCallable(functions, "add_job_url", { timeout: 300_000 });
      const res = await call({ url: jobUrl.trim() });
      setAddStatus(`Added: ${res.data.title} @ ${res.data.company} — drafting now; it will appear in Review when ready`);
      setJobUrl("");
    } catch (e) {
      setAddStatus(`Couldn't add it: ${e.message}`);
    }
  }

  async function writeLetter(appId) {
    setDraftingIds(ids => [...ids, appId]);
    try {
      const call = httpsCallable(functions, "request_draft", { timeout: 540_000 });
      await call({ app_id: appId });
      // the card moves to the review queue via the snapshot listeners
    } catch (e) {
      setAddStatus(`Drafting failed: ${e.message}`);
    } finally {
      setDraftingIds(ids => ids.filter(x => x !== appId));
    }
  }

  async function transition(appId, to, note) {
    const ref = doc(db, "users", user.uid, "applications", appId);
    await updateDoc(ref, {
      state: to,
      stateHistory: arrayUnion({ state: to, ts: new Date(), note }),
      updatedAt: serverTimestamp(),
    });
  }

  async function saveLetter(appId, text) {
    const ref = doc(db, "users", user.uid, "applications", appId);
    await updateDoc(ref, { "letter.text": text, updatedAt: serverTimestamp() });
  }

  if (!user) {
    return (
      <main style={{ padding: 40 }}>
        <h1>Job Engine</h1>
        <button style={btnPrimary} onClick={() => signInWithPopup(auth, googleProvider)}>
          Sign in with Google
        </button>
      </main>
    );
  }

  // Tabs: the two that need action from you come first. "urgent" tints the
  // count so a nonzero number reads as "look at me".
  const TABS = [
    { key: "review", label: "Review", count: apps.length, urgent: true,
      blurb: "Drafted letters waiting for your approval. Nothing is submitted until you approve it here." },
    { key: "needs_you", label: "Needs you", count: escalated.length, urgent: true,
      blurb: "The engine got stuck mid-submission (captcha, login wall, odd form). Finish these by hand — letter and answers are ready to copy." },
    { key: "matches", label: "Matches", count: matches.length,
      blurb: "Jobs that scored above your bar but don't have a letter yet. Pick which ones get one." },
    { key: "inflight", label: "In flight", count: inflight.length,
      blurb: "Approved applications the engine is submitting right now." },
    { key: "done", label: "Done", count: done.length,
      blurb: "Finished — submitted or failed. Most recent first." },
  ];
  const activeTab = TABS.find((t) => t.key === tab) || TABS[0];

  return (
    <main style={{ padding: "24px 40px 40px", maxWidth: 760, margin: "0 auto" }}>
      <Nav active="/" />

      <section style={{ ...card, padding: 14 }}>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            style={{
              flex: 1, padding: 8, background: T.panelAlt, color: T.text,
              border: `1px solid ${T.border}`, borderRadius: 6,
            }}
            placeholder="Paste any job posting URL — the engine drafts it for review"
            value={jobUrl}
            onChange={e => setJobUrl(e.target.value)}
            onKeyDown={e => e.key === "Enter" && addJob()}
          />
          <button style={btnPrimary} onClick={addJob}>Add job</button>
        </div>
        {addStatus && <p style={{ color: T.muted, marginBottom: 0 }}>{addStatus}</p>}
      </section>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        {TABS.map((t) => {
          const active = t.key === tab;
          const countColor = t.count > 0 && t.urgent ? T.warn : T.muted;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                ...btn,
                padding: "8px 14px",
                background: active ? T.panel : "transparent",
                borderColor: active ? T.accent : T.border,
                color: active ? T.text : T.muted,
                fontWeight: active ? 600 : 400,
              }}
            >
              {t.label}{" "}
              <span style={{ color: active ? countColor : T.muted, fontWeight: 600 }}>
                {t.count}
              </span>
            </button>
          );
        })}
      </div>
      <p style={{ color: T.muted, fontSize: 13, margin: "0 0 20px" }}>
        {activeTab.blurb}
      </p>

      {tab === "review" && (
        <>
          {apps.length === 0 &&
            <p style={{ color: T.muted }}>Nothing waiting. The engine will add drafts here.</p>}
          {apps.map((a) => (
            <article key={a.id} style={card}>
              <header>
                <h2 style={{ margin: "0 0 4px" }}>{jobLine(a)}</h2>
                <MatchInsight app={a} threshold={threshold} queue="review" />
              </header>
              <textarea
                defaultValue={a.letter?.text || ""}
                rows={14}
                style={{
                  width: "100%", margin: "12px 0", boxSizing: "border-box",
                  background: T.panelAlt, color: T.text,
                  border: `1px solid ${T.border}`, borderRadius: 6, padding: 10,
                }}
                onBlur={(e) => saveLetter(a.id, e.target.value)}
              />
              <button style={btnPrimary} onClick={() => transition(a.id, "approved", "approved in review UI")}>
                Approve &amp; submit
              </button>{" "}
              <button style={btn} onClick={() => transition(a.id, "rejected", "rejected in review UI")}>
                Reject
              </button>
            </article>
          ))}
        </>
      )}

      {tab === "needs_you" && (
        <>
          {escalated.length === 0 && <p style={{ color: T.muted }}>No escalations.</p>}
          {escalated.map((a) => (
            <article key={a.id} style={{ ...card, borderColor: T.warn }}>
              <h2 style={{ margin: "0 0 4px" }}>{jobLine(a)}</h2>
              <p><strong style={{ color: T.warn }}>Why:</strong> {a.submission?.error || "escalated"}</p>
              {postings[a.posting_id]?.url && (
                <a href={postings[a.posting_id].url} target="_blank" rel="noreferrer"
                   style={{ ...btnPrimary, display: "inline-block",
                            textDecoration: "none", marginBottom: 12 }}>
                  Open the job posting ↗
                </a>
              )}
              <FillSheet sheet={a.submission?.fill_sheet} />
              <ScreenshotLinks paths={a.submission?.screenshots} />
              <details style={{ margin: "8px 0" }}>
                <summary style={{ cursor: "pointer" }}>Cover letter (copy-paste ready)</summary>
                <pre style={{ whiteSpace: "pre-wrap", background: T.panelAlt, padding: 12, borderRadius: 6 }}>
                  {a.letter?.text}
                </pre>
              </details>
              <details>
                <summary style={{ cursor: "pointer" }}>Screening answers</summary>
                <div style={{ background: T.panelAlt, padding: 12, borderRadius: 6, marginTop: 8 }}>
                  <ScreeningAnswers answers={a.screeningAnswers} />
                </div>
              </details>
              <div style={{ marginTop: 12 }}>
                <button style={btnPrimary} onClick={() => transition(a.id, "submitted", "finished manually")}>
                  Mark submitted
                </button>{" "}
                <button style={btn} onClick={() => transition(a.id, "rejected", "abandoned from needs_human")}>
                  Abandon
                </button>
              </div>
            </article>
          ))}
        </>
      )}

      {tab === "matches" && (
        <>
          {matches.length === 0 &&
            <p style={{ color: T.muted }}>
              No matches waiting. With auto-draft on, matches skip this stage
              and go straight to Review.
            </p>}
          {matches.map((m) => (
            <article key={m.id} style={{ ...card, padding: 14 }}>
              <strong>{jobLine(m)}</strong>
              <MatchInsight app={m} threshold={threshold} queue="matched" />
              <button
                style={btnPrimary}
                disabled={draftingIds.includes(m.id)}
                onClick={() => writeLetter(m.id)}
              >
                {draftingIds.includes(m.id) ? "Writing… (about a minute)" : "Write the letter"}
              </button>{" "}
              <button style={btn} onClick={() => transition(m.id, "rejected", "skipped from matches")}>
                Skip
              </button>
            </article>
          ))}
        </>
      )}

      {tab === "inflight" && (
        <>
          {inflight.length === 0 && <p style={{ color: T.muted }}>Nothing in flight.</p>}
          {inflight.map((a) => (
            <article key={a.id} style={{ ...card, padding: 12 }}>
              <strong>{jobLine(a)}</strong>{" "}
              <span style={{ color: T.accent }}>
                {a.state === "approved" && "approved — waiting to queue"}
                {a.state === "queued" && "queued — submission task created"}
                {a.state === "submitting" && "submitting — browser is filling the form now"}
              </span>
            </article>
          ))}
        </>
      )}

      {tab === "done" && (
        <>
          {done.length === 0 && <p style={{ color: T.muted }}>Nothing finished yet.</p>}
          {done.map((a) => (
            <article key={a.id} style={{
              ...card, padding: 12,
              borderColor: a.state === "submitted" ? T.ok : T.danger,
            }}>
              <strong>{jobLine(a)}</strong>{" "}
              {a.state === "submitted" ? (
                <span style={{ color: T.ok }}>
                  ✓ submitted{a.submission?.confirmedAt ? ` — ${new Date(
                    a.submission.confirmedAt.seconds
                      ? a.submission.confirmedAt.seconds * 1000
                      : a.submission.confirmedAt).toLocaleString()}` : ""}
                </span>
              ) : (
                <span style={{ color: T.danger }}>✗ failed — {a.submission?.error || "see logs"}</span>
              )}
              {a.state === "failed" && (
                <div style={{ marginTop: 10 }}>
                  {a.letter?.text ? (
                    <button style={btn} onClick={() =>
                      transition(a.id, "in_review", "retried from Done — letter kept")}>
                      Back to review (keep letter)
                    </button>
                  ) : (
                    <button style={btn} onClick={() =>
                      transition(a.id, "matched", "retry drafting from Done")}>
                      Retry drafting
                    </button>
                  )}
                </div>
              )}
              <ScreenshotLinks paths={a.submission?.screenshots} />
            </article>
          ))}
        </>
      )}
    </main>
  );
}
