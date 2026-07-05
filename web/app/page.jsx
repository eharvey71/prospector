"use client";
// Review queue + needs_human queue, dark theme.

import { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup } from "firebase/auth";
import {
  collection, doc, onSnapshot, orderBy, query,
  serverTimestamp, updateDoc, where, arrayUnion,
} from "firebase/firestore";
import { auth, db, googleProvider } from "../lib/firebase";

// --- dark palette ---
export const T = {
  panel: "#1d2026",
  panelAlt: "#22262e",
  border: "#33383f",
  text: "#e2e4e9",
  muted: "#9aa1ad",
  warn: "#e0b34c",
  danger: "#e06c75",
  ok: "#7fbf7f",
  accent: "#6f9ff3",
};

export const card = {
  background: T.panel, border: `1px solid ${T.border}`,
  borderRadius: 8, padding: 20, marginBottom: 24,
};
export const btn = {
  background: T.panelAlt, color: T.text, border: `1px solid ${T.border}`,
  borderRadius: 6, padding: "8px 16px", cursor: "pointer",
};
export const btnPrimary = { ...btn, background: T.accent, color: "#10131a", border: "none", fontWeight: 600 };

const ANSWER_LABELS = {
  why_company: "Why this company",
  salary: "Salary expectations",
  work_auth: "Work authorization",
};

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

  useEffect(() => onAuthStateChanged(auth, setUser), []);

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
    return () => { unsub1(); unsub2(); };
  }, [user]);

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

  return (
    <main style={{ padding: 40, maxWidth: 760, margin: "0 auto" }}>
      <nav style={{ marginBottom: 20 }}>
        <a href="/profile" style={{ color: T.accent }}>Profile →</a>
      </nav>

      <h1>Review queue ({apps.length})</h1>
      {apps.length === 0 && <p style={{ color: T.muted }}>Nothing waiting. The engine will add drafts here.</p>}
      {apps.map((a) => (
        <article key={a.id} style={card}>
          <header>
            <strong>Match score: {a.match?.score}</strong>
            <ul>{(a.match?.reasons || []).map((r, i) => <li key={i}>{r}</li>)}</ul>
            {(a.match?.red_flags || []).length > 0 && (
              <p style={{ color: T.danger }}>
                Red flags: {(a.match.red_flags || []).join("; ")}
              </p>
            )}
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

      <h1 style={{ marginTop: 40 }}>Needs a human ({escalated.length})</h1>
      {escalated.length === 0 && <p style={{ color: T.muted }}>No escalations.</p>}
      {escalated.map((a) => (
        <article key={a.id} style={{ ...card, borderColor: T.warn }}>
          <p><strong style={{ color: T.warn }}>Why:</strong> {a.submission?.error || "escalated"}</p>
          {(a.submission?.screenshots || []).length > 0 && (
            <p style={{ fontSize: 13, color: T.muted }}>
              Screenshots in Storage: {(a.submission.screenshots || []).join(", ")}
            </p>
          )}
          <details style={{ marginBottom: 8 }}>
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
    </main>
  );
}
