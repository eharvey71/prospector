// Runs in EVERY frame (all_frames). The panel lives in the top frame; the
// actual application form very often lives in an embedded ATS iframe
// (a Greenhouse form inside a company's careers page), so "Fill this form"
// fills the top frame AND broadcasts into every child frame. Nothing is
// ever submitted.
//
// The panel appears two ways:
//   - automatically, when the page's domain matches the loaded job
//   - on demand, when the toolbar button or right-click menu asks for it —
//     for applications you reach by clicking through two or three domains
//     (LinkedIn -> careers page -> the ATS), where no auto-match happens.

(function () {
  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const fieldKey = (s) => norm((s || "").replace(/\(.*?\)/g, ""));

  // Demographic/legal and money labels never receive generic kit values.
  const SENSITIVE = /citizen|visa|sponsor|veteran|disab|gender|race|ethnic|hispanic|clearance|criminal/;
  const MONEY = /salary|compensation|pay\b|wage/;

  function labelMatches(field, label) {
    const f = fieldKey(field), l = norm(label);
    if (!f || !l) return false;
    if (SENSITIVE.test(l) && !SENSITIVE.test(f)) return false;
    if (MONEY.test(l) && !MONEY.test(f)) return false;
    if (f === l) return true;
    const ft = f.split(" "), lt = l.split(" ");
    if (ft.every((t) => lt.includes(t))) {
      if (ft.length >= 3 || lt.length <= 6) return true;
      // The field as a contiguous phrase inside a longer question:
      // "full name" in "welcome please enter your full name". Two-word
      // minimum so a bare "name" can't match every question containing
      // it; scattered tokens in a long label stay unmatched.
      if (ft.length >= 2 && l.includes(f)) return true;
      return false;
    }
    return f.length >= 15 && (l.includes(f) || f.includes(l));
  }

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
      const lbl = n !== el && n.querySelector("label, legend, [class*='label']");
      if (lbl && lbl.textContent.trim()) return lbl.textContent;
      // Question text often lives in a plain <p>/<div> right before the
      // field's container ("Please enter your full name" style forms) —
      // no label element anywhere.
      const prev = n.previousElementSibling;
      if (prev && !prev.querySelector("input, textarea, select, button")
          && prev.textContent.trim()
          && prev.textContent.trim().length <= 160) {
        return prev.textContent;
      }
      n = n.parentElement;
    }
    return el.name || el.placeholder || "";
  };

  // React re-renders from its own state: set through the native setter.
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

  // ------------------------------------------------------------------
  // EEO / self-identification: these questions get a dedicated matcher.
  // Labels are classified into a category, and the option is chosen by
  // category-specific rules with yes/no polarity safety — never by fuzzy
  // text similarity. No confident match -> the question stays blank.
  // ------------------------------------------------------------------
  // norm() turns "don't" into "don t" — patterns match that form.
  const DECLINE_RX = /decline|don t wish|dont wish|do not wish|prefer not|do not want|rather not/;
  const EEO_LABELS = {
    authorized: /legally authorized|authorized to work|eligible to work|work authorization/,
    sponsorship: /sponsor|require.*visa|visa.*status/,
    veteran: /veteran/,
    disability: /disab/,
    hispanic: /hispanic|latino|latinx/,
    race: /\brace\b|ethnicit/,
    gender: /\bgender\b/,
  };
  const EEO_ORDER = ["authorized", "sponsorship", "veteran", "disability",
                     "hispanic", "race", "gender"];

  // opts are normalized option texts; returns an index or -1 (leave blank).
  function pickEEOOption(cat, value, opts, eeoAll) {
    const v = norm(value);
    const find = (rx) => opts.findIndex((o) => rx.test(o));
    if (DECLINE_RX.test(v)) return find(DECLINE_RX);
    if (cat === "veteran") {
      if (/\bnot\b/.test(v)) return find(/\bam not\b|\bnot a protected\b/);
      return opts.findIndex((o) => /protected veteran/.test(o)
        && !/\bnot\b/.test(o) && !DECLINE_RX.test(o));
    }
    if (cat === "authorized" || cat === "sponsorship"
        || cat === "hispanic" || cat === "disability") {
      // ^no\b will not match "none of the above"; "No, I do not have a
      // disability" norms to "no i do not have..." and matches.
      if (/^yes\b/.test(v)) return find(/^yes\b/);
      if (/^no\b/.test(v)) return find(/^no\b/);
      return -1;
    }
    if (cat === "gender") {
      const exact = opts.findIndex((o) => o === v);
      return exact >= 0 ? exact : opts.findIndex((o) => o.startsWith(v + " "));
    }
    if (cat === "race") {
      // Combined Race/Ethnicity dropdowns list "Hispanic or Latino" as an
      // option; when the user identified as Hispanic, that wins.
      const hisp = (eeoAll || []).find((e) => e.cat === "hispanic");
      if (hisp && /^yes\b/.test(norm(hisp.value))) {
        const i = find(/hispanic|latino/);
        if (i >= 0) return i;
      }
      const groups = [
        [/american indian|alaska/, /american indian|alaska/],
        [/\basian\b/, /\basian\b/],          // \b keeps "caucasian" out
        [/black|african american/, /black|african american/],
        [/hawaiian|pacific island/, /hawaiian|pacific island/],
        [/two or more|multiracial/, /two or more|multiracial/],
        [/\bwhite\b/, /\bwhite\b|caucasian/],
      ];
      for (const [vrx, orx] of groups) if (vrx.test(v)) return find(orx);
      return -1;
    }
    return -1;
  }

  function eeoControls() {
    const out = [];
    for (const sel of document.querySelectorAll("select")) {
      if (sel.offsetParent === null || sel.selectedIndex > 0) continue;
      out.push({ kind: "select", el: sel, label: norm(labelFor(sel)),
                 opts: [...sel.options].map((o) => norm(o.textContent)) });
    }
    const groups = new Map();
    for (const r of document.querySelectorAll("input[type='radio']")) {
      if (r.offsetParent === null) continue;
      const key = r.name || "anon";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(r);
    }
    for (const radios of groups.values()) {
      if (radios.some((r) => r.checked)) continue;
      const fs = radios[0].closest("fieldset, [role='radiogroup'], ul, div");
      let glabel = fs?.querySelector("legend")?.textContent || "";
      if (!glabel) {
        const lbl = fs?.parentElement?.querySelector("label, legend, [class*='label']");
        if (lbl && !lbl.querySelector("input")) glabel = lbl.textContent;
      }
      out.push({
        kind: "radio", radios,
        label: norm(glabel || radios[0].name),
        opts: radios.map((r) =>
          norm(r.closest("label")?.textContent || labelFor(r) || r.value)),
      });
    }
    return out;
  }

  function fillEEO(eeo, done = new Set()) {
    const res = { count: 0, cats: new Set() };
    if (!eeo || !eeo.length) return res;
    for (const ctl of eeoControls()) {
      const cat = EEO_ORDER.find((c) => EEO_LABELS[c].test(ctl.label));
      if (!cat || done.has(cat)) continue;
      const entry = eeo.find((e) => e.cat === cat);
      if (!entry) continue;
      const i = pickEEOOption(cat, entry.value, ctl.opts, eeo);
      if (i < 0) continue;
      if (ctl.kind === "select") {
        ctl.el.value = ctl.el.options[i].value;
        ctl.el.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        ctl.radios[i].click();
      }
      res.count++;
      res.cats.add(cat);
    }
    return res;
  }

  // Custom dropdowns (react-select and kin — modern Greenhouse boards
  // render EEO questions this way): open the listbox with real mouse
  // events, pick the option, then VERIFY by reading the displayed value
  // back ([class*=single-value]; the inner input never mirrors it).
  // Unverified selections are not counted.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const mouse = (el, type) => el.dispatchEvent(
    new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));

  async function fillEEOCombos(eeo, doneCats) {
    const res = { count: 0, cats: new Set() };
    if (!eeo || !eeo.length) return res;
    const combos = [...document.querySelectorAll(
      "[role='combobox'], button[aria-haspopup='listbox']")]
      .filter((el) => el.offsetParent !== null && !el.closest(".iti"));
    for (const el of combos) {
      const cat = EEO_ORDER.find((c) => EEO_LABELS[c].test(norm(labelFor(el))));
      if (!cat || doneCats.has(cat) || res.cats.has(cat)) continue;
      // Disabled until an earlier answer enables it (race waits on the
      // Hispanic/Latino answer) — leave for a later round.
      if (el.disabled || el.getAttribute("aria-disabled") === "true") continue;
      const entry = eeo.find((e) => e.cat === cat);
      if (!entry) continue;
      const holder = el.closest("[class*='select'],[class*='combobox']")
        || el.parentElement;
      const shownNow = holder?.querySelector("[class*='single-value']")?.textContent
        || el.value || "";
      if (shownNow.trim()) continue;   // already answered — never overwrite

      mouse(el, "mousedown"); mouse(el, "mouseup");
      if (typeof el.click === "function") el.click();
      await sleep(400);   // listbox options can render lazily
      // Greenhouse pages carry ~230 hidden intl-tel-input options; visible
      // + non-.iti filtering is load-bearing here.
      const opts = [...document.querySelectorAll("[role='option']")]
        .filter((o) => o.offsetParent !== null && !o.closest(".iti"));
      const i = opts.length
        ? pickEEOOption(cat, entry.value, opts.map((o) => norm(o.textContent)), eeo)
        : -1;
      if (i < 0) {   // nothing safe to pick — close and move on
        el.dispatchEvent(new KeyboardEvent("keydown",
          { key: "Escape", bubbles: true }));
        mouse(document.body, "mousedown");
        continue;
      }
      mouse(opts[i], "mousedown"); mouse(opts[i], "mouseup");
      if (typeof opts[i].click === "function") opts[i].click();
      await sleep(250);
      const display = holder?.querySelector("[class*='single-value']")?.textContent
        || el.value || (el.tagName === "BUTTON" ? el.textContent : "") || "";
      // Verify: would our own picker have chosen what's now displayed?
      if (norm(display)
          && pickEEOOption(cat, entry.value, [norm(display)], eeo) === 0) {
        res.count++;
        res.cats.add(cat);
      }
    }
    return res;
  }

  async function fillAll(pending) {
    let count = 0;
    const filledKeys = new Set();
    const values = [...(pending.values || [])];
    if (pending.letter) values.push({ field: "cover letter", value: pending.letter });
    for (const e of pending.needs || []) {
      if (e.suggestion) values.push({ field: e.field, value: e.suggestion });
    }

    const direct = {
      "first name": "#first_name", "last name": "#last_name",
      "email": "#email, input[name='email'], input[type='email']",
      "phone": "#phone, input[name='phone'], input[type='tel']",
      "full name": "input[name='name'], input[autocomplete='name']",
      "linkedin": "input[name*='linkedin' i], input[id*='linkedin' i], "
        + "input[placeholder*='linkedin' i], input[aria-label*='linkedin' i]",
      "website": "input[name*='website' i], input[name*='portfolio' i]",
      "location": "input[name='location']",
      "cover letter": "textarea[name*='cover'], #cover_letter_text, textarea[name='comments']",
    };
    // First VISIBLE, EMPTY match — plain querySelector was grabbing hidden
    // inputs (ATSes keep e.g. a hidden urls[LinkedIn] field alongside the
    // styled visible one) and "filling" them invisibly.
    const firstVisible = (sel) => [...document.querySelectorAll(sel)]
      .find((e) => e.offsetParent !== null && !e.value) || null;
    for (const { field, value } of values) {
      if (!value || /entered \(full text/.test(value) || field === "Resume") continue;
      const f = fieldKey(field);
      let el = null;
      for (const [key, sel] of Object.entries(direct)) {
        if (f.includes(key)) { el = firstVisible(sel); break; }
      }
      if (!el || el.value) {
        el = fillables().find((cand) => labelMatches(field, labelFor(cand))) || null;
      }
      if (el && !el.value) {
        setValue(el, value); count++; filledKeys.add(f); continue;
      }
      for (const sel of document.querySelectorAll("select")) {
        if (sel.offsetParent === null) continue;
        if (!labelMatches(field, labelFor(sel))) continue;
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
    // EEO questions reveal conditionally (answering Hispanic/Latino "No"
    // makes the race question appear, sometimes after a delay) — so fill
    // in ROUNDS: re-scan, fill what's new, wait for reveals to render.
    // Stop only after two consecutive rounds gain nothing, so a slow
    // reveal gets a second chance instead of ending the loop.
    const eeoDone = new Set();
    let emptyRounds = 0;
    for (let round = 0; round < 5 && emptyRounds < 2; round++) {
      const native = fillEEO(pending.eeo, eeoDone);
      for (const c of native.cats) eeoDone.add(c);
      const combo = await fillEEOCombos(pending.eeo, eeoDone);
      for (const c of combo.cats) eeoDone.add(c);
      const gained = native.count + combo.count;
      count += gained;
      emptyRounds = gained ? 0 : emptyRounds + 1;
      await sleep(500);
    }
    for (const c of eeoDone) filledKeys.add("eeo " + c);
    return { count, filledKeys };
  }

  // ------------------------------------------------------------------
  // Child frames: no UI. Fill on request from the top-frame panel.
  // ------------------------------------------------------------------
  if (window !== window.top) {
    window.addEventListener("message", async (ev) => {
      if (ev.data?.type !== "JOB_ENGINE_FILL") return;
      try {
        const { pending } = await chrome.storage.local.get("pending");
        if (!pending) return;
        const { count } = await fillAll(pending);
        window.top.postMessage({ type: "JOB_ENGINE_FILL_RESULT", count }, "*");
      } catch { /* extension context gone — nothing to do */ }
    });
    return;
  }

  // ------------------------------------------------------------------
  // Top frame: the panel.
  // ------------------------------------------------------------------
  const P = { bg: "#1d2026", alt: "#23262d", border: "#2f343c", text: "#e2e4e9",
              muted: "#9aa1ad", accent: "#6f9ff3", warn: "#e0b34c" };
  let panel = null;
  let keepAlive = null;

  function dismiss() {
    clearInterval(keepAlive);
    keepAlive = null;
    panel?.remove();
    panel = null;
  }

  function showPanel(pending) {
    dismiss();   // a fresh summon always rebuilds

    panel = document.createElement("div");
    panel.style.cssText = `position:fixed;top:16px;right:16px;width:320px;max-height:80vh;
      overflow-y:auto;z-index:2147483647;background:${P.bg};color:${P.text};
      border:1px solid ${P.border};border-radius:10px;padding:14px;
      font:13px/1.45 system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.5)`;

    // SPA pages re-render aggressively and can sweep the panel out.
    keepAlive = setInterval(() => {
      if (panel && !document.documentElement.contains(panel)) {
        (document.body || document.documentElement).append(panel);
      }
    }, 800);

    const rowRegistry = [];
    const row = (label, value, key) => {
      const div = document.createElement("div");
      div.style.cssText = `margin:6px 0;padding:6px 8px;background:${P.alt};border-radius:6px`;
      const name = document.createElement("div");
      name.textContent = label;
      name.style.cssText = `color:${P.muted};font-size:11px`;
      rowRegistry.push({ key: key || fieldKey(label), div, name });
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

    const close = document.createElement("button");
    close.textContent = "✕";
    close.style.cssText = `float:right;background:none;border:none;color:${P.muted};cursor:pointer;font-size:14px`;
    close.onclick = dismiss;
    const h = document.createElement("div");
    h.innerHTML = `<strong>Job Engine autofill</strong>`
      + ` <span style="color:${P.muted};font-size:11px">v`
      + `${chrome.runtime.getManifest().version}</span>`;
    const sub = document.createElement("div");
    sub.textContent = `${pending.title || ""} @ ${pending.company || ""}`;
    sub.style.cssText = `color:${P.muted};margin:2px 0 10px`;
    panel.append(close, h, sub);

    // Clicked through to a different site than the job's own URL? Say so,
    // so nobody wonders whether these answers belong to this page.
    try {
      const tail = (u) => new URL(u).hostname.split(".").slice(-2).join(".");
      if (tail(pending.url) !== tail(location.href)) {
        const note = document.createElement("div");
        note.textContent = `Answers loaded from ${tail(pending.url)} — check they suit this form.`;
        note.style.cssText = `color:${P.warn};font-size:11.5px;margin:-6px 0 10px`;
        panel.append(note);
      }
    } catch { /* unparseable URL — skip the note */ }

    const fillBtn = document.createElement("button");
    fillBtn.textContent = "Fill this form";
    fillBtn.style.cssText = `width:100%;padding:9px;background:${P.accent};color:#10131a;
      border:none;border-radius:6px;font-weight:600;cursor:pointer;margin-bottom:6px`;
    const status = document.createElement("div");
    status.style.cssText = `color:${P.muted};margin-bottom:8px`;

    fillBtn.onclick = async () => {
      status.textContent = "Filling…";
      const { count, filledKeys } = await fillAll(pending);
      let frameCount = 0;
      const render = () => {
        const total = count + frameCount;
        status.textContent = `Filled ${total} field${total === 1 ? "" : "s"}`
          + (frameCount ? ` (${frameCount} in the embedded form)` : "")
          + ` — review everything, attach your resume by hand, then click the`
          + ` page's own Submit.`;
      };
      const collect = (ev) => {
        if (ev.data?.type !== "JOB_ENGINE_FILL_RESULT") return;
        frameCount += ev.data.count || 0;
        render();
      };
      window.addEventListener("message", collect);
      for (const f of document.querySelectorAll("iframe")) {
        try { f.contentWindow.postMessage({ type: "JOB_ENGINE_FILL" }, "*"); }
        catch { /* cross-origin contentWindow access — ignore */ }
      }
      // Combobox driving is slow by design (open, settle, pick, verify) —
      // give embedded frames time to finish reporting.
      setTimeout(() => window.removeEventListener("message", collect), 10000);
      render();
      for (const r of rowRegistry) {
        if (filledKeys.has(r.key) && !r.name.textContent.startsWith("✓")) {
          r.name.textContent = "✓ " + r.name.textContent;
          r.div.style.opacity = "0.55";
        }
      }
    };

    panel.append(fillBtn, status);

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
    if ((pending.eeo || []).length) {
      const t = document.createElement("div");
      t.textContent = "Self-identification — filled where the form allows:";
      t.style.cssText = `color:${P.accent};font-weight:600;margin-top:8px`;
      panel.append(t);
      for (const e of pending.eeo) panel.append(row(e.field, e.value, "eeo " + e.cat));
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
    clear.onclick = () => chrome.runtime.sendMessage({ kind: "clear" }, dismiss);
    panel.append(clear);

    (document.body || document.documentElement).append(panel);
  }

  // Summoned explicitly (toolbar button / right-click): no domain check —
  // the click IS the intent, and the whole point is pages we couldn't have
  // matched automatically.
  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg?.kind !== "show_panel") return;
    chrome.storage.local.get("pending").then(({ pending }) => {
      if (pending?.url) showPanel(pending);
      respond({ shown: !!pending?.url });
    });
    return true;   // async respond
  });

  // Automatic appearance on the job's own site.
  (async function () {
    const { pending } = await chrome.storage.local.get("pending");
    if (!pending || !pending.url) return;
    let targetHost;
    try { targetHost = new URL(pending.url).hostname; } catch { return; }
    const tail = (h) => h.split(".").slice(-2).join(".");
    if (tail(location.hostname) !== tail(targetHost)) {
      console.log(`[job-engine] autofill payload is for ${targetHost}; this is`
        + ` ${location.hostname} — click the toolbar button to show it here`);
      return;
    }
    showPanel(pending);
  })();
})();
