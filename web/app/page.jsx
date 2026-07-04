"use client";
// The review queue — the human-in-the-loop gate. Subscribes to applications
// in `in_review`, renders match reasoning + letter, and writes the two
// client-legal transitions (approved / rejected) that firestore.rules allow.
// The engine picks it up from there via the on_application_written trigger.

import { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup } from "firebase/auth";
import {
  collection, doc, onSnapshot, orderBy, query,
  serverTimestamp, updateDoc, where, arrayUnion,
} from "firebase/firestore";
import { auth, db, googleProvider } from "../lib/firebase";

export default function ReviewQueue() {
  const [user, setUser] = useState(null);
  const [apps, setApps] = useState([]);

  useEffect(() => onAuthStateChanged(auth, setUser), []);

  useEffect(() => {
    if (!user) return;
    const q = query(
      collection(db, "users", user.uid, "applications"),
      where("state", "==", "in_review"),
      orderBy("match.score", "desc")
    );
    return onSnapshot(q, (snap) =>
      setApps(snap.docs.map((d) => ({ id: d.id, ...d.data() })))
    );
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
      <main style={{ padding: 40, fontFamily: "system-ui" }}>
        <h1>Job Engine</h1>
        <button onClick={() => signInWithPopup(auth, googleProvider)}>
          Sign in with Google
        </button>
      </main>
    );
  }

  return (
    <main style={{ padding: 40, maxWidth: 760, margin: "0 auto", fontFamily: "system-ui" }}>
      <h1>Review queue ({apps.length})</h1>
      {apps.length === 0 && <p>Nothing waiting. The engine will add drafts here.</p>}
      {apps.map((a) => (
        <article key={a.id} style={{ border: "1px solid #ccc", borderRadius: 8, padding: 20, marginBottom: 24 }}>
          <header>
            <strong>Match score: {a.match?.score}</strong>
            <ul>{(a.match?.reasons || []).map((r, i) => <li key={i}>{r}</li>)}</ul>
            {(a.match?.red_flags || []).length > 0 && (
              <p style={{ color: "#b00" }}>
                Red flags: {(a.match.red_flags || []).join("; ")}
              </p>
            )}
          </header>
          <textarea
            defaultValue={a.letter?.text || ""}
            rows={14}
            style={{ width: "100%", margin: "12px 0" }}
            onBlur={(e) => saveLetter(a.id, e.target.value)}
          />
          <button onClick={() => transition(a.id, "approved", "approved in review UI")}>
            Approve &amp; submit
          </button>{" "}
          <button onClick={() => transition(a.id, "rejected", "rejected in review UI")}>
            Reject
          </button>
        </article>
      ))}
    </main>
  );
}
