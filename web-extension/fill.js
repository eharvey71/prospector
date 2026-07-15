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

  const fieldKey = (s) => norm((s || "").replace(/\(.*?\)/g, ""));

  function fillAll() {
    let count = 0;
    const filledKeys = new Set();
    const values = [...(pending.values || [])];
    if (pending.letter) values.push({ field: "cover letter", value: pending.letter });
    // Suggested answers for the open questions are attempted too — the
    // human is watching, so a visible best-effort fill beats a copy button.
    for (const e of pending.needs || []) {
      if (e.suggestion) values.push({ field: e.field, value: e.suggestion });
    }

    // Known ATS shortcuts first (exact ids/names), then label matching.
    const direct = {
      "first name": "#first_name", "last name": "#last_name",
      "email": "#email, input[name='email'], input[type='email']",
      "phone": "#phone, input[name='phone'], input[type='tel']",
      "full name": "input[name='name']",
      "linkedin": "input[name*='linkedin' i], input[id*='linkedin' i]",
      "website": "input[name*='website' i], input[name*='portfolio' i]",
      "location": "input[name='location']",
      "cover letter": "textarea[name*='cover'], #cover_letter_text, textarea[name='comments']",
    };
    for (const { field, value } of values) {
      if (!value || /entered \(full text/.test(value) || field === "Resume") continue;
      // "(could not verify selection)" and similar annotations from the
      // worker would break label matching — strip parentheticals.
      const f = fieldKey(field);
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
      if (el && !el.value) {
        setValue(el, value); count++; filledKeys.add(f); continue;
      }

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
          count++; filledKeys.add(f);
        }
        break;
      }
    }
    return { count, filledKeys };
  }

  // --- panel ---
  const P = { bg: "#1d2026", alt: "#22262e", border: "#33383f", text: "#e2e4e9",
              muted: "#9aa1ad", accent: "#6f9ff3", warn: "#e0b34c" };
  const panel = document.createElement("div");
  panel.style.cssText = `position:fixed;top:16px;right:16px;width:320px;max-height:80vh;
    overflow-y:auto;z-index:2147483647;background:${P.bg};color:${P.text};
    border:1px solid ${P.border};border-radius:10px;padding:14px;
    font:13px/1.45 system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.5)`;

  const rowRegistry = [];   // fieldKey -> row nodes, for post-fill ✓ marks

  const row = (label, value) => {
    const div = document.createElement("div");
    div.style.cssText = `margin:6px 0;padding:6px 8px;background:${P.alt};border-radius:6px`;
    const name = document.createElement("div");
    name.textContent = label;
    name.style.cssText = `color:${P.muted};font-size:11px`;
    rowRegistry.push({ key: fieldKey(label), div, name });
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
    const { count, filledKeys } = fillAll();
    status.textContent = `Filled ${count} field${count === 1 ? "" : "s"} — checked rows `
      + `below went in. Review everything, attach your resume by hand, then `
      + `click the page's own Submit.`;
    for (const r of rowRegistry) {
      if (filledKeys.has(r.key) && !r.name.textContent.startsWith("✓")) {
        r.name.textContent = "✓ " + r.name.textContent;
        r.div.style.opacity = "0.55";
      }
    }
  };

  panel.append(close, h, sub, fillBtn, status);

  if (pending.resumeUrl) {
    const rl = document.createElement("a");
    rl.href = pending.resumeUrl;
    rl.target = "_blank";
    rl.rel = "noreferrer";
    rl.textContent = "Download the tailored resume ↗ — then attach it to the form";
    rl.style.cssText = `display:block;color:${P.accent};margin-bottom:8px;text-decoration:underline`;
    panel.append(rl);
  }

  const needs = pending.needs || [];
  const known = needs.filter((e) => e.suggestion);
  const yours = needs.filter((e) => !e.suggestion);
  if (known.length) {
    const t = document.createElement("div");
    t.textContent = `Known answers — Fill will try these (${known.length}):`;
    t.style.cssText = `color:${P.accent};font-weight:600;margin-top:8px`;
    panel.append(t);
    for (const e of known) {
      panel.append(row(e.field.replace(/\s*\(could not verify selection\)/, ""),
                       e.suggestion));
    }
  }
  if (yours.length) {
    const t = document.createElement("div");
    t.textContent = `Only you can answer (${yours.length}):`;
    t.style.cssText = `color:${P.warn};font-weight:600;margin-top:8px`;
    panel.append(t);
    for (const e of yours) panel.append(row(e.field, "(your call)"));
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
