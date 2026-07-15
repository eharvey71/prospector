// Runs on every page. If extension storage holds an autofill payload whose
// host matches this page, show a panel: one click fills what can be filled
// deterministically (text fields, native selects), and everything else gets
// a copy button. Nothing is ever submitted — the human owns the Submit click.

(async function () {
  const { pending } = await chrome.storage.local.get("pending");
  if (!pending || !pending.url) return;
  let targetHost;
  try { targetHost = new URL(pending.url).hostname; } catch { return; }
  // Match on the registrable domain, not the exact host — ATSes redirect
  // between subdomains (boards.greenhouse.io <-> job-boards.greenhouse.io)
  // and an exact match made the panel vanish after the hop.
  const tail = (h) => h.split(".").slice(-2).join(".");
  if (tail(location.hostname) !== tail(targetHost)) {
    console.log(`[job-engine] autofill payload is for ${targetHost}; this is`
      + ` ${location.hostname} — panel not shown`);
    return;
  }

  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

  const labelFor = (el) => {
    if (el.labels && el.labels.length) return el.labels[0].textContent;
    const aria = el.getAttribute("aria-label");
    if (aria) return aria;
    const ids = el.getAttribute("aria-labelledby");
    if (ids) {
      const t = ids.split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent || "").join(" ");
      if (t.trim()) return t;
    }
    let n = el;
    for (let d = 0; d < 5 && n; d++) {
      n = n.parentElement;
      const lbl = n?.querySelector("label, legend, [class*='label']");
      if (lbl && lbl.textContent.trim()) return lbl.textContent;
    }
    return el.name || el.placeholder || "";
  };

  // React re-renders from its own state, so set values through the native
  // setter and fire input/change — a plain .value assignment gets wiped.
  const setValue = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const fillables = () => [...document.querySelectorAll("input, textarea")]
    .filter((el) => el.offsetParent !== null
      && !["hidden", "submit", "button", "file", "radio", "checkbox"].includes(el.type)
      && !el.value);

  function fillAll() {
    let count = 0;
    const values = [...(pending.values || [])];
    if (pending.letter) values.push({ field: "cover letter", value: pending.letter });

    // Known ATS shortcuts first (exact ids/names), then label matching.
    const direct = {
      "first name": "#first_name", "last name": "#last_name",
      "email": "#email, input[name='email'], input[type='email']",
      "phone": "#phone, input[name='phone'], input[type='tel']",
      "full name": "input[name='name']",
      "location": "input[name='location']",
      "cover letter": "textarea[name*='cover'], #cover_letter_text, textarea[name='comments']",
    };
    for (const { field, value } of values) {
      if (!value || /entered \(full text/.test(value) || field === "Resume") continue;
      const f = norm(field);
      let el = null;
      for (const [key, sel] of Object.entries(direct)) {
        if (f.includes(key)) { el = document.querySelector(sel); break; }
      }
      if (!el || el.value) {
        el = fillables().find((cand) => {
          const l = norm(labelFor(cand));
          return l && f && (l.includes(f) || f.includes(l)) && l.length > 3;
        }) || null;
      }
      if (el && !el.value) { setValue(el, value); count++; continue; }

      // Native selects: match by option text.
      for (const sel of document.querySelectorAll("select")) {
        if (sel.offsetParent === null) continue;
        const l = norm(labelFor(sel));
        if (!(l.includes(f) || f.includes(l)) || l.length <= 3) continue;
        const opt = [...sel.options].find((o) =>
          norm(o.textContent) === norm(value)
          || norm(o.textContent).startsWith(norm(value)));
        if (opt) {
          sel.value = opt.value;
          sel.dispatchEvent(new Event("change", { bubbles: true }));
          count++;
        }
        break;
      }
    }
    return count;
  }

  // --- panel ---
  const P = { bg: "#1d2026", alt: "#22262e", border: "#33383f", text: "#e2e4e9",
              muted: "#9aa1ad", accent: "#6f9ff3", warn: "#e0b34c" };
  const panel = document.createElement("div");
  panel.style.cssText = `position:fixed;top:16px;right:16px;width:320px;max-height:80vh;
    overflow-y:auto;z-index:2147483647;background:${P.bg};color:${P.text};
    border:1px solid ${P.border};border-radius:10px;padding:14px;
    font:13px/1.45 system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.5)`;

  const row = (label, value) => {
    const div = document.createElement("div");
    div.style.cssText = `margin:6px 0;padding:6px 8px;background:${P.alt};border-radius:6px`;
    const name = document.createElement("div");
    name.textContent = label;
    name.style.cssText = `color:${P.muted};font-size:11px`;
    const val = document.createElement("div");
    val.textContent = value.length > 90 ? value.slice(0, 90) + "…" : value;
    const copy = document.createElement("button");
    copy.textContent = "copy";
    copy.style.cssText = `float:right;background:none;border:1px solid ${P.border};
      color:${P.accent};border-radius:4px;cursor:pointer;font-size:11px;padding:1px 8px`;
    copy.onclick = () => {
      navigator.clipboard.writeText(value);
      copy.textContent = "copied ✓";
      setTimeout(() => { copy.textContent = "copy"; }, 1500);
    };
    div.append(copy, name, val);
    return div;
  };

  const h = document.createElement("div");
  h.innerHTML = `<strong>Job Engine autofill</strong>`;
  // SPA pages re-render aggressively and can sweep the panel out of the
  // DOM — re-attach until the user closes or clears it.
  let keepAlive = null;
  const dismiss = () => { clearInterval(keepAlive); panel.remove(); };
  keepAlive = setInterval(() => {
    if (!document.documentElement.contains(panel)) {
      (document.body || document.documentElement).append(panel);
    }
  }, 800);

  const close = document.createElement("button");
  close.textContent = "✕";
  close.style.cssText = `float:right;background:none;border:none;color:${P.muted};cursor:pointer;font-size:14px`;
  close.onclick = dismiss;
  const sub = document.createElement("div");
  sub.textContent = `${pending.title || ""} @ ${pending.company || ""}`;
  sub.style.cssText = `color:${P.muted};margin:2px 0 10px`;

  const fillBtn = document.createElement("button");
  fillBtn.textContent = "Fill this form";
  fillBtn.style.cssText = `width:100%;padding:9px;background:${P.accent};color:#10131a;
    border:none;border-radius:6px;font-weight:600;cursor:pointer;margin-bottom:6px`;
  const status = document.createElement("div");
  status.style.cssText = `color:${P.muted};margin-bottom:8px`;
  fillBtn.onclick = () => {
    const n = fillAll();
    status.textContent = `Filled ${n} field${n === 1 ? "" : "s"} — review everything, `
      + `attach your resume by hand, then click the page's own Submit.`;
  };

  panel.append(close, h, sub, fillBtn, status);

  const needs = pending.needs || [];
  if (needs.length) {
    const t = document.createElement("div");
    t.textContent = `Only you can answer (${needs.length}):`;
    t.style.cssText = `color:${P.warn};font-weight:600;margin-top:8px`;
    panel.append(t);
    for (const e of needs) {
      panel.append(row(e.field, e.suggestion || "(no suggestion — your call)"));
    }
  }
  const values = pending.values || [];
  if (values.length || pending.letter) {
    const t = document.createElement("div");
    t.textContent = "Prepared values:";
    t.style.cssText = `color:${P.muted};font-weight:600;margin-top:10px`;
    panel.append(t);
    if (pending.letter) panel.append(row("Cover letter", pending.letter));
    for (const e of values) {
      if (e.value && !/entered \(full text/.test(e.value)) panel.append(row(e.field, e.value));
    }
  }
  for (const e of pending.answers || []) panel.append(row(e.field, e.value));

  const clear = document.createElement("button");
  clear.textContent = "Done with this job (clear)";
  clear.style.cssText = `width:100%;margin-top:10px;padding:6px;background:none;
    border:1px solid ${P.border};color:${P.muted};border-radius:6px;cursor:pointer`;
  clear.onclick = () => {
    chrome.runtime.sendMessage({ kind: "clear" }, dismiss);
  };
  panel.append(clear);

  (document.body || document.documentElement).append(panel);
})();
