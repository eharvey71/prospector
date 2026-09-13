// Shared theme constants (for the pages still using inline styles) + nav.
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  isSignInWithEmailLink, onAuthStateChanged, sendSignInLinkToEmail,
  signInWithEmailLink, signInWithPopup, signOut,
} from "firebase/auth";
import { auth, googleProvider } from "../lib/firebase";

export const T = {
  panel: "#ffffff",
  panelAlt: "#f5f7fa",
  border: "#e3e8ef",
  text: "#14181e",
  muted: "#3f4b5c",
  warn: "#b7791f",
  danger: "#d64550",
  ok: "#2f9e63",
  accent: "#4a7de2",
};

export const card = {
  background: T.panel, border: `1px solid ${T.border}`,
  borderRadius: 10, padding: 20, marginBottom: 24,
};
export const box = { ...card, padding: 16, marginBottom: 20 };
export const btn = {
  background: T.panelAlt, color: T.text, border: `1px solid ${T.border}`,
  borderRadius: 8, padding: "8px 16px", cursor: "pointer",
};
export const btnPrimary = {
  ...btn, background: T.accent, color: "#fff", border: "none", fontWeight: 600,
};
export const input = {
  display: "block", width: "100%", margin: "4px 0 12px", padding: 8,
  boxSizing: "border-box", background: T.panelAlt, color: T.text,
  border: `1px solid ${T.border}`, borderRadius: 8,
};
export const label = { fontSize: 13, fontWeight: 600, color: T.muted };

// One sign-in screen for every page: Google popup, or an emailed
// sign-in link (passwordless). The same component also FINISHES a link
// sign-in: when the page loads from the emailed URL, it completes the
// exchange and cleans the address bar. It only renders while signed
// out, which is exactly when a link-click lands.
const EMAIL_KEY = "prospector_signin_email";

export function SignIn({ title = "Prospector" }) {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("");
  const [busyLabel, setBusyLabel] = useState("");

  useEffect(() => {
    if (!isSignInWithEmailLink(auth, window.location.href)) return;
    let addr = "";
    try { addr = window.localStorage.getItem(EMAIL_KEY) || ""; } catch { /* blocked */ }
    // Link opened on a different device/browser than requested from:
    // Firebase requires the email again as proof.
    if (!addr) addr = window.prompt("Confirm your email to finish signing in") || "";
    if (!addr) { setStatus("Sign-in not finished — email not confirmed."); return; }
    setBusyLabel("Finishing sign-in…");
    signInWithEmailLink(auth, addr, window.location.href)
      .then(() => {
        try { window.localStorage.removeItem(EMAIL_KEY); } catch { /* ok */ }
        window.history.replaceState({}, "", window.location.pathname);
      })
      .catch((e) => setStatus(`Sign-in link failed: ${e.message}`))
      .finally(() => setBusyLabel(""));
  }, []);

  async function sendLink() {
    const addr = email.trim();
    if (!addr) return;
    setBusyLabel("Sending the link…");
    setStatus("");
    try {
      await sendSignInLinkToEmail(auth, addr, {
        url: window.location.origin + window.location.pathname,
        handleCodeInApp: true,
      });
      try { window.localStorage.setItem(EMAIL_KEY, addr); } catch { /* ok */ }
      setStatus(`Sign-in link sent to ${addr} — check your inbox and open `
        + `it on this device.`);
    } catch (e) {
      setStatus(`Couldn't send the link: ${e.message}`);
    } finally {
      setBusyLabel("");
    }
  }

  return (
    <main className="container" style={{ maxWidth: 440 }}>
      <h1 style={{ marginTop: 48 }}>{title}</h1>
      <div style={{ background: T.panel, border: `1px solid ${T.border}`,
                    borderRadius: 10, padding: 20 }}>
        <button className="btn-primary" style={{ width: "100%" }}
                onClick={() => signInWithPopup(auth, googleProvider)}>
          Sign in with Google
        </button>
        <div className="orline">or use a sign-in link</div>
        <input value={email} placeholder="you@example.com" type="email"
               onChange={(e) => setEmail(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && sendLink()} />
        <button className="btn" style={{ width: "100%", marginTop: 8 }}
                disabled={!!busyLabel} onClick={sendLink}>
          Email me a sign-in link
        </button>
        {busyLabel && <Busy label={busyLabel} />}
        {status && <p className="hint" style={{ marginTop: 10 }}>{status}</p>}
      </div>
    </main>
  );
}

// THE wait indicator. Anywhere the user waits on the engine, render this
// (or put <span className="spinner sm" /> inside a button) — one look for
// "working on it" across the whole app.
export function Busy({ label }) {
  return (
    <span className="busy" role="status">
      <span className="spinner" />{label}
    </span>
  );
}

const PAGES = [
  { href: "/", title: "Queue" },
  { href: "/profile", title: "Profile" },
  { href: "/settings", title: "Settings" },
  { href: "/extension", title: "Extension" },
];

export function Nav({ active }) {
  const [email, setEmail] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  useEffect(() => onAuthStateChanged(auth, async (u) => {
    setEmail(u?.email || "");
    try {
      setIsAdmin(!!u && !!(await u.getIdTokenResult()).claims.admin);
    } catch {
      setIsAdmin(false);
    }
  }), []);
  const pages = isAdmin ? [...PAGES, { href: "/admin", title: "Admin" }] : PAGES;
  return (
    <nav className="nav">
      <span className="brand">Prospector</span>
      {/* Link, not <a>: a plain href reloads the whole app on every tab,
          which re-runs Firebase auth from scratch and flashes the sign-in
          screen between pages. */}
      {pages.map((p) => (
        <Link key={p.href} href={p.href}
              className={p.href === active ? "active" : undefined}>
          {p.title}
        </Link>
      ))}
      <span className="spacer" />
      {email && <span className="navmail">{email}</span>}
      <button className="signout" onClick={() => signOut(auth)}>Sign out</button>
    </nav>
  );
}
