"use client";
// Admin: the global board catalog. Boards listed here are crawled for
// everyone and matched for ENROLLED users — complementing, never
// replacing, each user's own watchlist. Visible only to accounts with
// the admin custom claim (scripts/set_admin.py).

import { useEffect, useState } from "react";
import { onAuthStateChanged } from "firebase/auth";
import {
  collection, doc, getCountFromServer, getDoc, getDocs, query, setDoc,
  serverTimestamp, where,
} from "firebase/firestore";
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

  // Per-user results: funnel counters + pipeline state counts via
  // aggregation queries (server-side counts, no documents downloaded).
  const [stats, setStats] = useState({});
  useEffect(() => {
    if (!isAdmin || users.length === 0) return;
    (async () => {
      const out = {};
      await Promise.all(users.map(async (u) => {
        const apps = collection(db, "users", u.uid, "applications");
        const cnt = async (states) => (await getCountFromServer(
          query(apps, where("state", "in", states)))).data().count;
        const [funnel, matches, review, needs, inflight, submitted, failed,
               rejected] = await Promise.all([
          getDoc(doc(db, "users", u.uid, "stats", "funnel")),
          cnt(["matched"]), cnt(["drafted", "in_review"]), cnt(["needs_human"]),
          cnt(["approved", "queued", "submitting"]),
          cnt(["submitted"]), cnt(["failed"]), cnt(["rejected"]),
        ]);
        out[u.uid] = {
          funnel: funnel.exists() ? funnel.data() : null,
          matches, review, needs, inflight, submitted, failed, rejected,
        };
      }));
      setStats(out);
    })().catch((e) => setStatus(`Stats load failed: ${e.message}`));
  }, [isAdmin, users]); // eslint-disable-line react-hooks/exhaustive-deps

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

      <details className="panel tint-green" open>
        <summary>Results by user</summary>
        <div className="panelbody">
          {users.length === 0 && <p className="hint">No users loaded.</p>}
          {users.map((u) => {
            const s = stats[u.uid];
            const f = s?.funnel;
            const ago = (v) => {
              const d = v?.seconds ? new Date(v.seconds * 1000) : null;
              if (!d) return null;
              const m = Math.round((Date.now() - d.getTime()) / 60000);
              return m < 60 ? `${m}m ago` : m < 2880
                ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
            };
            return (
              <div key={u.uid} className="subcard">
                <strong>{u.name}</strong>{" "}
                <span style={{ color: T.muted, fontSize: 13 }}>{u.email}</span>
                {!s ? (
                  <p className="hint">counting…</p>
                ) : (
                  <>
                    <div className="chips">
                      <span className="chip">matches {s.matches}</span>
                      <span className="chip">review {s.review}</span>
                      <span className={"chip" + (s.needs ? " warn" : "")}>needs you {s.needs}</span>
                      <span className="chip">in flight {s.inflight}</span>
                      <span className={"chip" + (s.submitted ? " ok" : "")}>submitted {s.submitted}</span>
                      <span className={"chip" + (s.failed ? " danger" : "")}>failed {s.failed}</span>
                      <span className="chip">skipped/rejected {s.rejected}</span>
                    </div>
                    <p className="hint" style={{ margin: "6px 0 0" }}>
                      {f
                        ? `funnel: ${f.seen || 0} seen · ${f.prefiltered || 0} filtered · `
                          + `${f.scored || 0} scored · ${f.matched || 0} matched`
                          + (ago(f.updatedAt) ? ` · last activity ${ago(f.updatedAt)}` : "")
                        : "no matching activity yet — check titles and boards/catalog enrollment"}
                    </p>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </details>

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
