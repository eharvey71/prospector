"use client";
// Queue: compact scannable rows, one per job; click a row to expand its
// details, letter, and actions. Color is reserved for meaning.

import { useEffect, useRef, useState } from "react";
import { onAuthStateChanged, signInWithPopup } from "firebase/auth";
import {
  collection, doc, getDoc, limit, onSnapshot, orderBy, query,
  serverTimestamp, updateDoc, where, arrayUnion,
} from "firebase/firestore";
import { httpsCallable } from "firebase/functions";
import { ref as storageRef, getDownloadURL } from "firebase/storage";
import { auth, db, functions, googleProvider, storage } from "../lib/firebase";
import { Busy, Nav } from "./ui";

const ANSWER_LABELS = {
  why_company: "Why this company",
  salary: "Salary expectations",
  work_auth: "Work authorization",
};

const RUBRIC_ORDER = ["skills", "seniority", "domain", "logistics"];

const tierOf = (score, threshold) =>
  score >= 85 ? { label: "Strong", cls: "ok" }
  : score >= threshold ? { label: "Good", cls: "accent" }
  : { label: "Stretch", cls: "warn" };

const normFlag = (f) =>
  typeof f === "string" ? { severity: "concern", topic: "", detail: f } : f;

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

function MatchInsight({ app, threshold, queue }) {
  const m = app.match || {};
  const score = m.score ?? 0;
  const flags = (m.red_flags || []).map(normFlag);
  const blockers = flags.filter((f) => f.severity === "blocker");
  const concerns = flags.filter((f) => f.severity !== "blocker");
  const rubric = m.rubric || {};
  const hasRubric = RUBRIC_ORDER.some((k) => k in rubric);

  const whyHere = (app.user_added
    ? score < threshold
      ? `Below your ${threshold}-point bar — shown because you added it yourself.`
      : "You added this job yourself."
    : `Cleared your ${threshold}-point bar.`)
    + (queue === "matched" ? " Auto-draft is off, so no letter is written until you ask." : "");

  const nextStep = queue === "review"
    ? "Edit the letter below (saves when you click away), then Approve or Reject."
    : blockers.length > 0
      ? "A cover letter can't fix a blocker — Skip unless the flag is wrong."
      : score < threshold
        ? "Long shot — spend a letter here only if you'd take it over a stronger match."
        : "Good odds — write the letter next; nothing is sent until you approve it.";

  return (
    <div>
      {m.summary && <p style={{ margin: "10px 0 2px", fontStyle: "italic" }}>{m.summary}</p>}
      <p className="hint" style={{ margin: "2px 0 0" }}>{whyHere}</p>

      {hasRubric && (
        <div className="chips">
          {RUBRIC_ORDER.filter((k) => k in rubric).map((k) => (
            <span key={k}
                  className={`chip ${rubric[k] >= 7 ? "ok" : rubric[k] >= 4 ? "warn" : "danger"}`}>
              {k} {rubric[k]}/10
            </span>
          ))}
        </div>
      )}

      {(m.reasons || []).length > 0 && (
        <details className="fold">
          <summary>Why these numbers</summary>
          <ul className="foldbody" style={{ margin: 0, paddingLeft: 26 }}>
            {(m.reasons || []).map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </details>
      )}

      {blockers.length > 0 && (
        <details className="fold dangerline" open>
          <summary>Blockers — hard requirements not met ({blockers.length})</summary>
          <ul className="foldbody" style={{ margin: 0, paddingLeft: 26 }}>
            {blockers.map((f, i) => (
              <li key={i}>{f.topic ? <strong>{f.topic} — </strong> : null}{f.detail}</li>
            ))}
          </ul>
        </details>
      )}

      {concerns.length > 0 && (
        <details className="fold warnline">
          <summary>{concerns.length} concern{concerns.length > 1 ? "s" : ""} a letter could address</summary>
          <ul className="foldbody" style={{ margin: 0, paddingLeft: 26 }}>
            {concerns.map((f, i) => (
              <li key={i}>{f.topic ? <strong>{f.topic} — </strong> : null}{f.detail}</li>
            ))}
          </ul>
        </details>
      )}

      <p className="hint" style={{ margin: "10px 0 0" }}>{nextStep}</p>
    </div>
  );
}

function ScreeningAnswers({ answers }) {
  const entries = [
    ...Object.entries(answers || {}).filter(([k, v]) => k !== "extra" && v),
    ...Object.entries(answers?.extra || {}),
  ];
  if (entries.length === 0) return <p className="hint">None generated.</p>;
  return entries.map(([k, v]) => (
    <div key={k} style={{ marginBottom: 10 }}>
      <div className="field-label" style={{ margin: "0 0 2px" }}>{ANSWER_LABELS[k] || k}</div>
      <div style={{ whiteSpace: "pre-wrap" }}>{v}</div>
    </div>
  ));
}

function FillSheet({ sheet }) {
  if (!sheet || sheet.length === 0) return null;
  const needs = sheet.filter((e) => e.status !== "filled");
  const known = needs.filter((e) => e.suggestion);
  const yours = needs.filter((e) => !e.suggestion);
  const filled = sheet.filter((e) => e.status === "filled");
  return (
    <div style={{ margin: "8px 0" }}>
      {known.length > 0 && (
        <details className="fold" open>
          <summary style={{ color: "var(--accent)" }}>
            Known answers the robot couldn&apos;t click ({known.length}) — autofill will try them
          </summary>
          <ul className="foldbody" style={{ margin: 0, paddingLeft: 26 }}>
            {known.map((e, i) => (
              <li key={i}>
                {e.field.replace(/\s*\(could not verify selection\)/, "")}
                {": "}<span style={{ color: "var(--muted)" }}>{e.suggestion}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {yours.length > 0 && (
        <details className="fold warnline" open>
          <summary>Only you can answer these ({yours.length})</summary>
          <ul className="foldbody" style={{ margin: 0, paddingLeft: 26 }}>
            {yours.map((e, i) => <li key={i}>{e.field}</li>)}
          </ul>
        </details>
      )}
      {filled.length > 0 && (
        <details className="fold">
          <summary>Answers for everything else ({filled.length}) — copy into the form</summary>
          <table className="foldbody" style={{ borderSpacing: 0, width: "100%" }}>
            <tbody>
              {filled.map((e, i) => (
                <tr key={i}>
                  <td style={{ color: "var(--muted)", paddingRight: 14, paddingBottom: 4,
                               verticalAlign: "top", whiteSpace: "nowrap" }}>{e.field}</td>
                  <td style={{ paddingBottom: 4 }}>{e.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function ScreenshotLinks({ paths }) {
  const [urls, setUrls] = useState({});
  useEffect(() => {
    (paths || []).forEach(async (p) => {
      try {
        const u = await getDownloadURL(storageRef(storage, p));
        setUrls((prev) => (prev[p] ? prev : { ...prev, [p]: u }));
      } catch { /* not readable */ }
    });
  }, [paths]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!paths || paths.length === 0) return null;
  return (
    <div className="hint" style={{ marginTop: 8 }}>
      Screenshots:{" "}
      {paths.map((p) => {
        const name = p.split("/").pop().replace(/\.png$/, "");
        return urls[p] ? (
          <a key={p} href={urls[p]} target="_blank" rel="noreferrer"
             style={{ marginRight: 12 }}>{name} ↗</a>
        ) : (
          <span key={p} style={{ marginRight: 12 }}>{name}</span>
        );
      })}
    </div>
  );
}

// Engine health strip: last crawl, today's LLM usage, recent pipeline
// errors. Turns "why is my queue empty" from a debugging session into a
// glance. Amber = something deserves attention.
function HealthStrip() {
  const [h, setH] = useState(null);
  useEffect(() => {
    (async () => {
      const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      const [crawl, llm, errors] = await Promise.all([
        getDoc(doc(db, "health", "crawl")),
        getDoc(doc(db, "health", `llm_${day}`)),
        getDoc(doc(db, "health", "errors")),
      ]).then((snaps) => snaps.map((s) => (s.exists() ? s.data() : null)));
      setH({ crawl, llm, errors });
    })().catch(() => {});
  }, []);
  if (!h) return null;

  const ts = (v) => v && new Date(v.seconds ? v.seconds * 1000 : v);
  const ago = (d) => {
    if (!d) return null;
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    return mins < 60 ? `${mins}m ago` : mins < 60 * 48
      ? `${Math.round(mins / 60)}h ago` : `${Math.round(mins / 1440)}d ago`;
  };

  const crawlAt = ts(h.crawl?.lastRunAt);
  const crawlStale = !crawlAt || (Date.now() - crawlAt.getTime()) > 8 * 3600e3;
  const crawlBad = h.crawl && h.crawl.ok === false;
  const errAt = ts(h.errors?.lastErrorAt);
  const errRecent = errAt && (Date.now() - errAt.getTime()) < 24 * 3600e3;
  const kTok = ((h.llm?.input_tokens || 0) + (h.llm?.output_tokens || 0)) / 1000;

  return (
    <p className="funnel" style={{ marginTop: -12 }}>
      <span style={{ color: crawlBad || crawlStale ? "var(--warn)" : undefined }}
            title={crawlBad ? h.crawl?.error : undefined}>
        crawl {crawlAt ? `${ago(crawlAt)}` : "never"}
        {h.crawl?.ok === true && ` · ${h.crawl.postings} postings`}
        {crawlBad && " · FAILED"}
        {!crawlBad && crawlStale && crawlAt && " · overdue"}
      </span>
      {" | "}
      <span title="LLM usage today (all users)">
        LLM today: {h.llm?.calls || 0} calls · {Math.round(kTok)}k tokens
        {(h.llm?.errors || 0) > 0 && (
          <span style={{ color: "var(--warn)" }}> · {h.llm.errors} failed</span>
        )}
      </span>
      {errRecent && (
        <span style={{ color: "var(--warn)" }} title={h.errors?.lastError}>
          {" | "}pipeline errors in last 24h — hover for the latest
        </span>
      )}
    </p>
  );
}

function ResumeLink({ path }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!path) return;
    getDownloadURL(storageRef(storage, path)).then(setUrl).catch(() => {});
  }, [path]);
  if (!path) return null;
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" style={{ fontSize: 13 }}>
      Tailored resume for this job ↗
    </a>
  ) : (
    <span className="hint">Tailored resume…</span>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ReviewQueue() {
  const [user, setUser] = useState(null);
  const [apps, setApps] = useState([]);
  const [escalated, setEscalated] = useState([]);
  const [jobUrl, setJobUrl] = useState("");
  const [addStatus, setAddStatus] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [matches, setMatches] = useState([]);
  const [inflight, setInflight] = useState([]);
  const [done, setDone] = useState([]);
  const [postings, setPostings] = useState({});
  const [draftingIds, setDraftingIds] = useState([]);
  const [threshold, setThreshold] = useState(70);
  const [tab, setTab] = useState("matches");
  const tabChosen = useRef(false);   // stop auto-selection once the user
                                     // clicks (or once we've picked)
  const [queryError, setQueryError] = useState("");
  const [funnel, setFunnel] = useState(null);
  const [openId, setOpenId] = useState(null);   // one expanded row at a time
  const [userDoc, setUserDoc] = useState(null);

  useEffect(() => onAuthStateChanged(auth, setUser), []);

  useEffect(() => {
    if (!user) return;
    getDoc(doc(db, "users", user.uid)).then((s) => {
      const d = s.data() || {};
      setUserDoc(d);
      const t = d.preferences?.min_match_score;
      if (typeof t === "number") setThreshold(t);
    });
    return onSnapshot(doc(db, "users", user.uid, "stats", "funnel"),
      (snap) => setFunnel(snap.exists() ? snap.data() : null),
      () => {});
  }, [user]);

  useEffect(() => {
    if (!user) return;
    const base = collection(db, "users", user.uid, "applications");
    const onErr = (name) => (err) => {
      console.error(`${name} query failed:`, err);
      setQueryError(`The "${name}" list failed to load: ${err.message}`);
    };
    const unsub1 = onSnapshot(
      query(base, where("state", "==", "in_review"), orderBy("match.score", "desc")),
      (snap) => setApps(snap.docs.map((d) => ({ id: d.id, ...d.data() }))),
      onErr("Review"));
    const unsub2 = onSnapshot(
      query(base, where("state", "==", "needs_human")),
      (snap) => setEscalated(snap.docs.map((d) => ({ id: d.id, ...d.data() }))),
      onErr("Needs you"));
    const unsub3 = onSnapshot(
      query(base, where("state", "==", "matched"), orderBy("match.score", "desc")),
      (snap) => setMatches(snap.docs.map((d) => ({ id: d.id, ...d.data() }))),
      onErr("Matches"));
    const unsub4 = onSnapshot(
      query(base, where("state", "in", ["approved", "queued", "submitting"]),
            orderBy("updatedAt", "desc")),
      (snap) => setInflight(snap.docs.map((d) => ({ id: d.id, ...d.data() }))),
      onErr("In flight"));
    const unsub5 = onSnapshot(
      query(base, where("state", "in", ["submitted", "failed", "rejected"]),
            orderBy("updatedAt", "desc"), limit(25)),
      (snap) => setDone(snap.docs.map((d) => ({ id: d.id, ...d.data() }))),
      onErr("Done"));
    return () => { unsub1(); unsub2(); unsub3(); unsub4(); unsub5(); };
  }, [user]);

  useEffect(() => {
    [...matches, ...apps, ...escalated, ...inflight, ...done].forEach(async (m) => {
      if (!m.posting_id || postings[m.posting_id]) return;
      const snap = await getDoc(doc(db, "jobPostings", m.posting_id));
      if (snap.exists()) {
        const p = snap.data();
        setPostings(prev => ({
          ...prev,
          [m.posting_id]: { title: p.title, company: p.company, url: p.url,
                            active: p.active },
        }));
      }
    });
  }, [matches, apps, escalated, inflight, done]); // eslint-disable-line react-hooks/exhaustive-deps

  // Land the user where the workflow actually starts: the first tab (in
  // pipeline order) that has work in it. Runs until data first appears or
  // the user clicks a tab, then never again.
  useEffect(() => {
    if (tabChosen.current) return;
    const first = [["matches", matches], ["review", apps], ["needs_you", escalated]]
      .find(([, list]) => list.length > 0);
    if (first) {
      setTab(first[0]);
      tabChosen.current = true;
    }
  }, [matches, apps, escalated]);

  async function addJob(urlArg) {
    // Guard: onClick handlers receive the click EVENT as the first arg —
    // only a real string counts as a URL override.
    const url = (typeof urlArg === "string" ? urlArg : jobUrl).trim();
    if (!url) return;
    setAddBusy(true);
    setAddStatus("");
    try {
      const call = httpsCallable(functions, "add_job_url", { timeout: 300_000 });
      const res = await call({ url });
      setAddStatus(
        `Added: ${res.data.title} @ ${res.data.company} — `
        + (userDoc?.preferences?.auto_draft
           ? "drafting now; it will appear in Review when ready"
           : "waiting in Matches; open it and click \"Write the letter\" when you want one")
      );
      setJobUrl("");
    } catch (e) {
      setAddStatus(`Couldn't add it: ${e.message}`);
    } finally {
      setAddBusy(false);
    }
  }

  // The extension's toolbar button opens /?add=<job url> — auto-submit it
  // once, then scrub the query string so a refresh can't double-add.
  const addParamHandled = useRef(false);
  useEffect(() => {
    if (!user || addParamHandled.current) return;
    const url = new URLSearchParams(window.location.search).get("add");
    if (!url) return;
    addParamHandled.current = true;
    window.history.replaceState({}, "", window.location.pathname);
    setJobUrl(url);
    addJob(url);
  }, [user]); // eslint-disable-line react-hooks/exhaustive-deps

  async function writeLetter(appId) {
    setDraftingIds(ids => [...ids, appId]);
    try {
      const call = httpsCallable(functions, "request_draft", { timeout: 540_000 });
      await call({ app_id: appId });
    } catch (e) {
      setAddStatus(`Drafting failed: ${e.message}`);
    } finally {
      setDraftingIds(ids => ids.filter(x => x !== appId));
    }
  }

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

  const US_STATES = {
    AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California",
    CO: "Colorado", CT: "Connecticut", DE: "Delaware", FL: "Florida", GA: "Georgia",
    HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa",
    KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland",
    MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi",
    MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire",
    NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina",
    ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania",
    RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee",
    TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington",
    WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming", DC: "District of Columbia",
  };

  function standardKit(u) {
    if (!u) return [];
    const kit = [];
    const add = (field, value) => value && kit.push({ field, value, status: "filled" });
    const [first, ...rest] = (u.name || "").split(" ");
    add("First name", first);
    add("Last name", rest.join(" "));
    add("Full name", u.name);
    add("Email", u.email);
    add("Phone", u.phone);
    add("LinkedIn Profile", u.linkedin);
    add("Website", u.website);
    add("Location (City)", u.location);
    const m = (u.location || "").match(/^([^,]+),\s*([A-Za-z]{2})\b/);
    if (m) {
      add("City", m[1].trim());
      add("State", US_STATES[m[2].toUpperCase()] || m[2]);
      add("Country", "United States");
    }
    return kit;
  }

  // Structured self-identification + work-eligibility answers for the
  // extension's dedicated EEO matcher (label classes + option polarity,
  // not fuzzy text matching). cat names are the fill.js contract.
  function eeoKit(u) {
    if (!u) return [];
    const eeo = [];
    const auth = (u.work_auth || "").toLowerCase();
    if (/citizen|green card|permanent resident|authorized to work/.test(auth)) {
      eeo.push({ cat: "authorized", field: "Authorized to work in the US?", value: "Yes" });
      eeo.push({ cat: "sponsorship", field: "Require visa sponsorship?", value: "No" });
    }
    const s = u.selfid || {};
    if (s.gender) eeo.push({ cat: "gender", field: "Gender", value: s.gender });
    if (s.hispanic_latino) eeo.push({ cat: "hispanic", field: "Hispanic or Latino?", value: s.hispanic_latino });
    if (s.race) eeo.push({ cat: "race", field: "Race", value: s.race });
    if (s.veteran_status) eeo.push({ cat: "veteran", field: "Veteran status", value: s.veteran_status });
    if (s.disability_status) eeo.push({ cat: "disability", field: "Disability status", value: s.disability_status });
    return eeo;
  }

  async function openWithAutofill(a) {
    const p = postings[a.posting_id];
    if (!p?.url) return;
    let resumeUrl = null;
    if (a.resume_path) {
      try { resumeUrl = await getDownloadURL(storageRef(storage, a.resume_path)); }
      catch { /* panel just won't show the link */ }
    }
    const sheet = a.submission?.fill_sheet || [];
    const answers = a.screeningAnswers || {};
    const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    const sheetFilled = sheet.filter((e) => e.status === "filled");
    const have = new Set(sheetFilled.map((e) => norm(e.field)));
    const payload = {
      url: p.url, title: p.title, company: p.company,
      letter: a.letter?.text || "",
      resumeUrl,
      values: [
        ...sheetFilled,
        ...standardKit(userDoc).filter((e) => !have.has(norm(e.field))),
      ],
      needs: sheet.filter((e) => e.status !== "filled"),
      eeo: eeoKit(userDoc),
      answers: [
        ...Object.entries(answers)
          .filter(([k, v]) => k !== "extra" && v)
          .map(([k, v]) => ({ field: ANSWER_LABELS[k] || k, value: v })),
        ...Object.entries(answers.extra || {})
          .map(([k, v]) => ({ field: k, value: v })),
      ],
    };
    let acked = false;
    let stale = false;
    const onAck = (ev) => {
      if (ev.data?.type === "JOB_ENGINE_AUTOFILL_ACK") acked = true;
      if (ev.data?.type === "JOB_ENGINE_EXTENSION_STALE") stale = true;
    };
    window.addEventListener("message", onAck);
    window.postMessage({ type: "JOB_ENGINE_AUTOFILL", payload }, "*");
    // Give the extension a moment to store the payload, then open the
    // posting — but NOT when the handoff failed: an extension holding a
    // previous job's answers on the new posting is worse than a warning.
    setTimeout(() => {
      window.removeEventListener("message", onAck);
      if (stale) {
        setAddStatus("The extension was updated since this tab loaded, so "
          + "it couldn't take the answers. RELOAD THIS TAB and click "
          + "Open & autofill again.");
        return;
      }
      window.open(p.url, "_blank");
      if (!acked) {
        setAddStatus("Opened the posting, but the autofill extension isn't "
          + "responding — install or update it from the Extension tab. The "
          + "answers on this card still work by copy-paste.");
      }
    }, 600);
  }

  if (!user) {
    return (
      <main className="container">
        <h1>Job Engine</h1>
        <button className="btn-primary" onClick={() => signInWithPopup(auth, googleProvider)}>
          Sign in with Google
        </button>
      </main>
    );
  }

  // Pipeline order: a job moves left to right through these tabs.
  const TABS = [
    { key: "matches", label: "Matches", count: matches.length, urgent: true,
      blurb: "Scored above your bar but no letter yet. Pick which ones get one." },
    { key: "review", label: "Review", count: apps.length, urgent: true,
      blurb: "Drafted letters waiting for your approval. Nothing is submitted until you approve it here." },
    { key: "needs_you", label: "Needs you", count: escalated.length, urgent: true,
      blurb: "The engine got stuck mid-submission. Finish these by hand — answers are prepared." },
    { key: "inflight", label: "In flight", count: inflight.length,
      blurb: "Approved applications the engine is submitting right now." },
    { key: "done", label: "Done", count: done.length,
      blurb: "Submitted, failed, or skipped by you. Skips can be brought back." },
  ];
  const activeTab = TABS.find((t) => t.key === tab) || TABS[0];

  // Left-edge stripe color keyed to where the job is in the pipeline.
  const STRIPE = {
    matched: "s-accent", drafted: "s-purple", in_review: "s-purple",
    needs_human: "s-danger", approved: "s-accent", queued: "s-accent",
    submitting: "s-accent", submitted: "s-ok", failed: "s-danger",
    rejected: "s-muted",
  };

  // One compact row per job; body renders only when expanded.
  function Row({ a, right, children, expandable = true }) {
    const p = postings[a.posting_id];
    const m = a.match || {};
    const open = openId === a.id;
    const tier = m.score != null ? tierOf(m.score, threshold) : null;
    return (
      <div className={"row " + (STRIPE[a.state] || "s-muted") + (open ? " open" : "")}>
        <div
          className={"rowhead" + (expandable ? "" : " static")}
          onClick={expandable ? () => setOpenId(open ? null : a.id) : undefined}
        >
          {tier && <span className={`pill ${tier.cls}`}>{m.score}</span>}
          <div className="rowtitle">
            <span className="t">{p?.title || "…"}</span>
            <span className="c">{p?.company || ""}</span>
            {p?.active === false && <span className="pill warn" style={{ marginLeft: 8 }}>may be closed</span>}
          </div>
          {m.posting_salary && <span className="pill money">{m.posting_salary}</span>}
          {right}
          {expandable && <span className="chev">{open ? "▾" : "▸"}</span>}
        </div>
        {open && expandable && (
          <div className="rowbody" onClick={(e) => e.stopPropagation()}>
            {p?.url && (
              <a href={p.url} target="_blank" rel="noreferrer"
                 style={{ fontSize: 13 }}>Open posting ↗</a>
            )}
            {children}
          </div>
        )}
      </div>
    );
  }

  return (
    <main className="container">
      <Nav active="/" />

      {queryError && (
        <div className="banner">
          {queryError}
          {queryError.toLowerCase().includes("index") && (
            <div className="hint">
              Usually a missing index — run: firebase deploy --only firestore:indexes
            </div>
          )}
        </div>
      )}

      <div className="addbar">
        <input
          placeholder="Paste any job posting URL — the engine drafts it for review"
          value={jobUrl}
          onChange={e => setJobUrl(e.target.value)}
          onKeyDown={e => e.key === "Enter" && addJob()}
        />
        <button className="btn-primary" onClick={() => addJob()}>Add job</button>
      </div>
      {addBusy && <Busy label="Reading the posting — this can take up to a minute" />}
      {!addBusy && addStatus &&
        <p className="hint" style={{ marginBottom: 10 }}>{addStatus}</p>}
      {funnel && (
        <p className="funnel"
           title="Where crawled jobs went: seen = evaluated for you; filtered = didn't resemble your titles/skills; scored = rated; matched = cleared your bar">
          {funnel.seen || 0} seen · {funnel.prefiltered || 0} filtered out ·{" "}
          {funnel.scored || 0} scored · {funnel.matched || 0} matched
        </p>
      )}
      <HealthStrip />

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.key}
                  onClick={() => { tabChosen.current = true; setTab(t.key); }}
                  className={`tab tab-${t.key}` + (t.key === tab ? " active" : "")}>
            {t.label}
            <span className={"n" + (t.count > 0 && t.urgent ? " hot" : "")}>{t.count}</span>
          </button>
        ))}
      </div>
      <p className="tabblurb">{activeTab.blurb}</p>

      {tab === "review" && (
        <>
          {apps.length === 0 && <p className="empty">Nothing waiting. The engine will add drafts here.</p>}
          {apps.map((a) => (
            <Row key={a.id} a={a}>
              <MatchInsight app={a} threshold={threshold} queue="review" />
              <div style={{ marginTop: 6 }}><ResumeLink path={a.resume_path} /></div>
              {!a.letter?.text && (
                <p className="hint" style={{ marginTop: 8 }}>
                  No letter — this application will be submitted without one.
                  Type below only if you want to add one.
                </p>
              )}
              <textarea className="letter" defaultValue={a.letter?.text || ""}
                        onBlur={(e) => saveLetter(a.id, e.target.value)} />
              <div className="actions">
                <button className="btn-primary"
                        onClick={() => transition(a.id, "approved", "approved in review UI")}>
                  Approve &amp; submit
                </button>
                <button className="btn"
                        onClick={() => transition(a.id, "rejected", "rejected in review UI")}>
                  Reject
                </button>
              </div>
            </Row>
          ))}
        </>
      )}

      {tab === "needs_you" && (
        <>
          {escalated.length === 0 && <p className="empty">No escalations.</p>}
          {escalated.map((a) => (
            <Row key={a.id} a={a}
                 right={<span className="pill warn">needs you</span>}>
              <p style={{ margin: "10px 0 6px" }}>
                <strong style={{ color: "var(--warn)" }}>Why: </strong>
                {a.submission?.error || "escalated"}
              </p>
              <div className="actions" style={{ marginTop: 0 }}>
                <button className="btn-primary" onClick={() => openWithAutofill(a)}>
                  Open &amp; autofill ↗
                </button>
              </div>
              <FillSheet sheet={a.submission?.fill_sheet} />
              <div style={{ margin: "6px 0" }}><ResumeLink path={a.resume_path} /></div>
              <details className="fold">
                <summary>Cover letter (copy-paste ready)</summary>
                <pre className="foldbody" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                  {a.letter?.text}
                </pre>
              </details>
              <details className="fold">
                <summary>Screening answers</summary>
                <div className="foldbody">
                  <ScreeningAnswers answers={a.screeningAnswers} />
                </div>
              </details>
              <ScreenshotLinks paths={a.submission?.screenshots} />
              <div className="actions">
                <button className="btn-primary"
                        onClick={() => transition(a.id, "submitted", "finished manually")}>
                  Mark submitted
                </button>
                <button className="btn"
                        onClick={() => transition(a.id, "rejected", "abandoned from needs_human")}>
                  Abandon
                </button>
              </div>
            </Row>
          ))}
        </>
      )}

      {tab === "matches" && (
        <>
          {matches.length === 0 &&
            <p className="empty">
              No matches waiting. With auto-draft on, matches skip this stage and go straight to Review.
            </p>}
          {matches.map((a) => (
            <Row key={a.id} a={a}>
              <MatchInsight app={a} threshold={threshold} queue="matched" />
              <div className="actions">
                <button className="btn-primary" disabled={draftingIds.includes(a.id)}
                        onClick={() => writeLetter(a.id)}>
                  {draftingIds.includes(a.id)
                    ? <><span className="spinner sm" />Writing — about a minute</>
                    : "Write the letter"}
                </button>
                <button className="btn"
                        onClick={() => transition(a.id, "in_review",
                          "no letter — sent straight to review")}>
                  Apply without letter
                </button>
                <button className="btn"
                        onClick={() => transition(a.id, "rejected", "skipped from matches")}>
                  Skip
                </button>
              </div>
            </Row>
          ))}
        </>
      )}

      {tab === "inflight" && (
        <>
          {inflight.length === 0 && <p className="empty">Nothing in flight.</p>}
          {inflight.map((a) => (
            <Row key={a.id} a={a} expandable={false}
                 right={
                   <span className="pill accent">
                     {a.state === "approved" && "waiting to queue"}
                     {a.state === "queued" && "queued"}
                     {a.state === "submitting" &&
                       <><span className="spinner sm" />submitting…</>}
                   </span>
                 } />
          ))}
        </>
      )}

      {tab === "done" && (
        <>
          {done.length === 0 && <p className="empty">Nothing finished yet.</p>}
          {done.map((a) => (
            <Row key={a.id} a={a}
                 right={
                   a.state === "submitted"
                     ? <span className="pill ok">✓ submitted</span>
                     : a.state === "rejected"
                       ? <span className="pill">skipped</span>
                       : <span className="pill danger">✗ failed</span>
                 }>
              {a.state === "rejected" ? (
                <div className="actions" style={{ marginTop: 10 }}>
                  <button className="btn" onClick={() =>
                    transition(a.id, "matched", "un-skipped from Done")}>
                    Put it back in Matches
                  </button>
                </div>
              ) : a.state === "submitted" ? (
                <p className="hint" style={{ marginTop: 10 }}>
                  Submitted{a.submission?.confirmedAt ? ` — ${new Date(
                    a.submission.confirmedAt.seconds
                      ? a.submission.confirmedAt.seconds * 1000
                      : a.submission.confirmedAt).toLocaleString()}` : ""}.
                </p>
              ) : (
                <>
                  <p style={{ marginTop: 10, color: "var(--danger)" }}>
                    Failed — {a.submission?.error || "see logs"}
                  </p>
                  <div className="actions">
                    {a.letter?.text ? (
                      <button className="btn" onClick={() =>
                        transition(a.id, "in_review", "retried from Done — letter kept")}>
                        Back to review (keep letter)
                      </button>
                    ) : (
                      <button className="btn" onClick={() =>
                        transition(a.id, "matched", "retry drafting from Done")}>
                        Retry drafting
                      </button>
                    )}
                  </div>
                </>
              )}
              <ScreenshotLinks paths={a.submission?.screenshots} />
            </Row>
          ))}
        </>
      )}
    </main>
  );
}
