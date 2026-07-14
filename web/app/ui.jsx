// Shared dark theme + top navigation bar, used by every page.

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
export const box = { ...card, padding: 16, marginBottom: 20 };
export const btn = {
  background: T.panelAlt, color: T.text, border: `1px solid ${T.border}`,
  borderRadius: 6, padding: "8px 16px", cursor: "pointer",
};
export const btnPrimary = {
  ...btn, background: T.accent, color: "#10131a", border: "none", fontWeight: 600,
};
export const input = {
  display: "block", width: "100%", margin: "4px 0 12px", padding: 8,
  boxSizing: "border-box", background: T.panelAlt, color: T.text,
  border: `1px solid ${T.border}`, borderRadius: 6,
};
export const label = { fontSize: 13, fontWeight: 600, color: T.muted };

const PAGES = [
  { href: "/", title: "Queue", blurb: "review & track applications" },
  { href: "/profile", title: "Profile", blurb: "who you are" },
  { href: "/settings", title: "Settings", blurb: "how the engine searches" },
];

export function Nav({ active }) {
  return (
    <nav style={{
      display: "flex", gap: 6, alignItems: "center",
      borderBottom: `1px solid ${T.border}`,
      margin: "0 0 24px", paddingBottom: 0,
    }}>
      <span style={{ fontWeight: 700, marginRight: 12, padding: "10px 0" }}>
        Job Engine
      </span>
      {PAGES.map((p) => {
        const isActive = p.href === active;
        return (
          <a
            key={p.href}
            href={p.href}
            title={p.blurb}
            style={{
              padding: "10px 14px",
              color: isActive ? T.text : T.muted,
              textDecoration: "none",
              fontWeight: isActive ? 600 : 400,
              borderBottom: `2px solid ${isActive ? T.accent : "transparent"}`,
              marginBottom: -1,
            }}
          >
            {p.title}
          </a>
        );
      })}
    </nav>
  );
}
