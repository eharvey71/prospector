"use client";
// Admin: the global board catalog. Boards listed here are crawled for
// everyone and matched for ENROLLED users — complementing, never
// replacing, each user's own watchlist. Visible only to accounts with
// the admin custom claim (scripts/set_admin.py).

import { useEffect, useState } from "react";
import { onAuthStateChanged } from "firebase/auth";
import { collection, doc, getDoc, getDocs, setDoc, serverTimestamp } from "firebase/firestore";
import { auth, db } from "../../lib/firebase";
import { Busy, T, Nav, SignIn, input, label as labelStyle, btnPrimary } from "../ui";

const FIELDS = [
  ["greenhouse", "Greenhouse (the SLUG in boards.greenhouse.io/SLUG)"],
  ["lever", "Lever (jobs.lever.co/SLUG)"],
  ["ashby", "Ashby (jobs.ashbyhq.com/SLUG)"],
  ["smartrecruiters", "SmartRecruiters (careers.smartrecruiters.com/SLUG)"],
  ["workable", "Workable (apply.workable.com/SLUG)"],
  ["workday", "Workday (full myworkdayjobs.com careers URLs)"],
  ["custom", "Custom career pages (full URLs)"],
  ["linkedin", "LinkedIn searches (keywords, e.g. entry level marketing Richmond VA)"],
];

const CONFIG_PATH = ["config", "global", "watchlist", "companies"];

export default function AdminPage() {
  const [user, setUser] = useState(null);
  const [isAdmin, setIsAdmin] = useState(null);   // null = still checking
  const [lists, setLists] = useState({});          // field -> csv string
  const [enrollAll, setEnrollAll] = useState(false);
  const [enrolled, setEnrolled] = useState([]);    // uids
  const [users, setUsers] = useState([]);          // {uid, name, email}
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => onAuthStateChanged(auth, async (u) => {
    setUser(u);
    if (!u) { setIsAdmin(null); return; }
    try {
      setIsAdmin(!!(await u.getIdTokenResult()).claims.admin);
    } catch {
      setIsAdmin(false);
    }
  }), []);

  useEffect(() => {
    if (!isAdmin) return;
    (async () => {
      const snap = await getDoc(doc(db, ...CONFIG_PATH));
      const d = snap.exists() ? snap.data() : {};
      setLists(Object.fromEntries(
        FIELDS.map(([f]) => [f, (d[f] || []).join(", ")])));
      setEnrollAll(!!d.enroll_all);
      setEnrolled(d.enrolled_uids || []);
      const us = await getDocs(collection(db, "users"));
      setUsers(us.docs.map((x) => ({
        uid: x.id, name: x.data().name || "(no name)", email: x.data().email || "",
      })));
    })().catch((e) => setStatus(`Load failed: ${e.message}`));
  }, [isAdmin]);

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      const csv = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);
      await setDoc(doc(db, ...CONFIG_PATH), {
        ...Object.fromEntries(FIELDS.map(([f]) => [f, csv(lists[f])])),
        enroll_all: enrollAll,
        enrolled_uids: enrolled,
        updatedAt: serverTimestamp(),
      });
      setStatus("Saved ✓ — takes effect on the next crawl.");
    } catch (e) {
      setStatus(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  if (!user) return <SignIn title="Admin" />;
  if (isAdmin === null) return <main className="container"><Nav active="/admin" /><Busy label="Checking access…" /></main>;
  if (!isAdmin) {
    return (
      <main className="container">
        <Nav active="/admin" />
        <h1>Admin</h1>
        <p>This account doesn&apos;t have admin access. Grant it with{" "}
          <code>python scripts/set_admin.py &lt;UID&gt;</code>, then sign out
          and back in.</p>
      </main>
    );
  }

  return (
    <main className="container">
      <Nav active="/admin" />
      <h1>Global board catalog</h1>
      <p style={{ color: T.muted }}>
        Boards here are crawled for everyone and matched for the users
        enrolled below — on top of (never instead of) each user&apos;s own
        watchlist. Enrolled users don&apos;t have to configure anything.
      </p>

      <details className="panel tint-purple" open>
        <summary>Boards</summary>
        <div className="panelbody">
          {FIELDS.map(([f, label]) => (
            <div key={f}>
              <span style={labelStyle}>{label}</span>
              <input style={input} value={lists[f] || ""}
                     onChange={(e) => setLists((p) => ({ ...p, [f]: e.target.value }))} />
            </div>
          ))}
        </div>
      </details>

      <details className="panel tint-blue" open>
        <summary>Who gets the catalog</summary>
        <div className="panelbody">
          <label style={{ display: "block", margin: "10px 0" }}>
            <input type="checkbox" checked={enrollAll}
                   onChange={(e) => setEnrollAll(e.target.checked)} />
            {" "}Everyone — all current and future users
          </label>
          {!enrollAll && users.map((u) => (
            <label key={u.uid} style={{ display: "block", margin: "6px 0" }}>
              <input type="checkbox" checked={enrolled.includes(u.uid)}
                     onChange={(e) => setEnrolled((p) =>
                       e.target.checked ? [...p, u.uid] : p.filter((x) => x !== u.uid))} />
              {" "}{u.name} <span style={{ color: T.muted }}>{u.email}</span>
            </label>
          ))}
          {!enrollAll && users.length === 0 && (
            <p className="hint">No users loaded yet.</p>
          )}
        </div>
      </details>

      <div className="savebar">
        <button onClick={save} disabled={saving}
                style={{ ...btnPrimary, padding: "10px 24px", fontSize: 16 }}>
          Save catalog
        </button>
        <span style={{ marginLeft: 12 }}>
          {saving ? <Busy label="Saving…" /> : status}
        </span>
      </div>
    </main>
  );
}
