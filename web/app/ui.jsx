// Shared theme constants (for the pages still using inline styles) + nav.
"use client";

import { signOut } from "firebase/auth";
import { auth } from "../lib/firebase";

export const T = {
  panel: "#1d2026",
  panelAlt: "#23262d",
  border: "#2f343c",
  text: "#e2e4e9",
  muted: "#9aa1ad",
  warn: "#e0b34c",
  danger: "#e06c75",
  ok: "#7fbf7f",
  accent: "#6f9ff3",
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
  ...btn, background: T.accent, color: "#10131a", border: "none", fontWeight: 600,
};
export const input = {
  display: "block", width: "100%", margin: "4px 0 12px", padding: 8,
  boxSizing: "border-box", background: T.panelAlt, color: T.text,
  border: `1px solid ${T.border}`, borderRadius: 8,
};
export const label = { fontSize: 13, fontWeight: 600, color: T.muted };

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
  return (
    <nav className="nav">
      <span className="brand">Job Engine</span>
      {PAGES.map((p) => (
        <a key={p.href} href={p.href}
           className={p.href === active ? "active" : undefined}>
          {p.title}
        </a>
      ))}
      <span className="spacer" />
      <button className="signout" onClick={() => signOut(auth)}>Sign out</button>
    </nav>
  );
}
