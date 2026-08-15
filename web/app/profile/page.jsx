"use client";
// Profile editor — who you are: basics, resume, work history, writing
// samples. Engine behavior (matching preferences, company watchlist) lives
// on /settings.
// Saving writes ONLY the profile fields below, so it can never clobber
// preferences edited on the Settings page (and vice versa).
// Resume uploads land at users/{uid}/resume.pdf — the exact path the
// submission worker's adapter fetches from.

import { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup } from "firebase/auth";
import { deleteDoc, doc, getDoc, onSnapshot, setDoc, serverTimestamp } from "firebase/firestore";
import { ref, uploadBytes, getMetadata } from "firebase/storage";
import { auth, db, googleProvider, storage } from "../../lib/firebase";
import { T, Nav, box, btn, btnPrimary, input, label } from "../ui";

const EMPTY_ROLE = { company: "", title: "", start: "", end: "", bullets: [""] };
const EMPTY_SAMPLE = { title: "", text: "" };
const EMPTY_EDU = { school: "", degree: "", year: "", bullets: [""] };
const EMPTY_PROJECT = { name: "", description: "", tech: "" }; // tech: csv in UI

export default function ProfilePage() {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("");
  const [resumeInfo, setResumeInfo] = useState(null);
  const [extraction, setExtraction] = useState(null);

  // Flat profile fields
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [website, setWebsite] = useState("");
  const [location, setLocation] = useState("");
  const [workAuth, setWorkAuth] = useState("");
  const [salaryTarget, setSalaryTarget] = useState("");
  const [skills, setSkills] = useState("");           // comma-separated in UI
  const [history, setHistory] = useState([]);
  const [samples, setSamples] = useState([]);
  const [careerStage, setCareerStage] = useState("experienced");
  const [education, setEducation] = useState([]);
  const [projects, setProjects] = useState([]);
  // Standard screeners: "" = not set (question escalates), "yes"/"no"
  const [relocation, setRelocation] = useState("");
  const [onsite, setOnsite] = useState("");

  useEffect(() => onAuthStateChanged(auth, setUser), []);

  // Extraction suggestion appears live once the engine finishes parsing an
  // uploaded resume (usually well under a minute after upload).
  useEffect(() => {
    if (!user) return;
    return onSnapshot(
      doc(db, "users", user.uid, "resume_extraction", "latest"),
      (snap) => setExtraction(snap.exists() ? snap.data() : null)
    );
  }, [user]);

  // Load existing profile + resume status
  useEffect(() => {
    if (!user) return;
    (async () => {
      const snap = await getDoc(doc(db, "users", user.uid));
      if (snap.exists()) {
        const d = snap.data();
        setName(d.name || "");
        setEmail(d.email || user.email || "");
        setPhone(d.phone || "");
        setLinkedin(d.linkedin || "");
        setWebsite(d.website || "");
        setLocation(d.location || "");
        setWorkAuth(d.work_auth || "");
        setSalaryTarget(d.salary_target || "");
        setSkills((d.skills || []).join(", "));
        setHistory((d.work_history || []).map(r => ({
          company: r.company || "", title: r.title || "",
          start: r.start || "", end: r.end || "",
          bullets: Array.isArray(r.bullets) && r.bullets.length ? r.bullets : [""],
        })));
        setSamples((d.writing_samples || []).map(s => ({
          title: s.title || "", text: s.text || "",
        })));
        setCareerStage(d.career_stage || "experienced");
        setEducation((d.education || []).map(e => ({
          school: e.school || "", degree: e.degree || "", year: e.year || "",
          bullets: Array.isArray(e.bullets) && e.bullets.length ? e.bullets : [""],
        })));
        setProjects((d.projects || []).map(p => ({
          name: p.name || "", description: p.description || "",
          tech: (p.tech || []).join(", "),
        })));
        const tri = (v) => (v === true ? "yes" : v === false ? "no" : "");
        setRelocation(tri(d.screeners?.open_to_relocation));
        setOnsite(tri(d.screeners?.onsite_ok));
      } else {
        setEmail(user.email || "");
      }
      try {
        const meta = await getMetadata(ref(storage, `users/${user.uid}/resume.pdf`));
        setResumeInfo(`resume.pdf uploaded ${new Date(meta.updated).toLocaleString()}`);
      } catch {
        setResumeInfo(null);
      }
    })();
  }, [user]);

  const csv = (s) => s.split(",").map(x => x.trim()).filter(Boolean);

  async function save() {
    setStatus("Saving…");
    const profile = {
      name,
      email,
      phone: phone || null,
      linkedin: linkedin || null,
      website: website || null,
      location: location || null,
      work_auth: workAuth || null,
      salary_target: salaryTarget || null,
      skills: csv(skills),
      work_history: history.map(r => ({
        company: r.company, title: r.title, start: r.start,
        end: r.end || null,
        bullets: (r.bullets || []).filter(Boolean),
      })),
      writing_samples: samples.filter(s => s.title || s.text),
      career_stage: careerStage,
      education: education
        .filter(e => e.school || e.degree)
        .map(e => ({
          school: e.school, degree: e.degree, year: e.year || null,
          bullets: (e.bullets || []).filter(Boolean),
        })),
      projects: projects
        .filter(p => p.name || p.description)
        .map(p => ({ name: p.name, description: p.description, tech: csv(p.tech) })),
      screeners: {
        open_to_relocation: relocation === "" ? null : relocation === "yes",
        onsite_ok: onsite === "" ? null : onsite === "yes",
      },
      updatedAt: serverTimestamp(),
    };
    await setDoc(doc(db, "users", user.uid), profile, { merge: true });
    setStatus("Saved ✓");
    setTimeout(() => setStatus(""), 2500);
  }

  async function uploadResume(file) {
    if (!file) return;
    if (file.type !== "application/pdf") {
      setStatus("Resume must be a PDF");
      return;
    }
    setStatus("Uploading resume…");
    await uploadBytes(ref(storage, `users/${user.uid}/resume.pdf`), file, {
      contentType: "application/pdf",
    });
    setResumeInfo(`resume.pdf uploaded ${new Date().toLocaleString()}`);
    setStatus("Resume uploaded ✓");
    setTimeout(() => setStatus(""), 2500);
  }

  async function applyExtraction() {
    const x = extraction;
    if (x.name) setName(x.name);
    if (x.location) setLocation(x.location);
    if (x.work_auth) setWorkAuth(x.work_auth);
    if (x.skills?.length) {
      const merged = new Set([...csv(skills), ...x.skills]);
      setSkills([...merged].join(", "));
    }
    if (x.work_history?.length) {
      setHistory(x.work_history.map(r => ({
        ...r, end: r.end || "", bullets: r.bullets?.length ? r.bullets : [""],
      })));
    }
    await dismissExtraction();
    setStatus("Applied — review the fields below, then Save profile");
  }

  async function dismissExtraction() {
    await deleteDoc(doc(db, "users", user.uid, "resume_extraction", "latest"));
  }

  // --- small helpers for list editing ---
  const setRole = (i, patch) =>
    setHistory(h => h.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const setSample = (i, patch) =>
    setSamples(s => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const setEdu = (i, patch) =>
    setEducation(e => e.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const setProject = (i, patch) =>
    setProjects(p => p.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  if (!user) {
    return (
      <main className="container">
        <h1>Profile</h1>
        <button style={btnPrimary} onClick={() => signInWithPopup(auth, googleProvider)}>
          Sign in with Google
        </button>
      </main>
    );
  }

  return (
    <main className="container">
      <Nav active="/profile" />
      <h1>Profile</h1>
      <p style={{ color: T.muted }}>
        This is what the engine knows about you. Matching scores against it,
        and cover letters can only claim what&apos;s written here. Which
        companies to watch and how picky to be lives in Settings.
      </p>

      <details className="panel" open>
        <summary>Basics</summary>
        <div className="panelbody">
        <span style={label}>Where are you in your career?</span>
        <select style={input} value={careerStage} onChange={e => setCareerStage(e.target.value)}>
          <option value="entry">Just starting out — score me on education, projects &amp; potential</option>
          <option value="some">A few years of experience</option>
          <option value="experienced">Experienced — score me on my work history</option>
        </select>
        {careerStage === "entry" && (
          <p style={{ color: T.muted, fontSize: 13, marginTop: -6 }}>
            Matching will weigh your Education and Projects sections like work
            history, skip senior-level postings, and only treat explicit hard
            requirements (like &quot;5+ years required&quot;) as blockers.
          </p>
        )}
        <span style={label}>Name</span>
        <input style={input} value={name} onChange={e => setName(e.target.value)} />
        <span style={label}>Email (goes on applications)</span>
        <input style={input} value={email} onChange={e => setEmail(e.target.value)} />
        <span style={label}>Phone</span>
        <input style={input} value={phone} onChange={e => setPhone(e.target.value)} />
        <span style={label}>LinkedIn profile URL</span>
        <input style={input} value={linkedin} placeholder="https://www.linkedin.com/in/…"
               onChange={e => setLinkedin(e.target.value)} />
        <span style={label}>Website / portfolio (optional)</span>
        <input style={input} value={website} onChange={e => setWebsite(e.target.value)} />
        <span style={label}>Location (City, ST — e.g. Denver, CO)</span>
        <input style={input} value={location} onChange={e => setLocation(e.target.value)} />
        <span style={label}>Work authorization</span>
        <input style={input} value={workAuth} onChange={e => setWorkAuth(e.target.value)} />
        <span style={label}>Salary target (optional)</span>
        <input style={input} value={salaryTarget} onChange={e => setSalaryTarget(e.target.value)} />
        <span style={label}>Skills (comma-separated)</span>
        <textarea style={{ ...input, height: 70 }} value={skills} onChange={e => setSkills(e.target.value)} />
      </div>
      </details>

      <details className="panel">
        <summary>Standard application answers</summary>
        <div className="panelbody">
        <p style={{ color: T.muted, fontSize: 13 }}>
          Almost every application asks these. Set them once and the engine
          answers them for you; leave one unset and that question comes back
          to you instead. (Consent questions and &quot;have you interviewed
          here before&quot; always come back to you.)
        </p>
        <span style={label}>Open to relocation?</span>
        <select style={input} value={relocation} onChange={e => setRelocation(e.target.value)}>
          <option value="">Not set — ask me each time</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
        <span style={label}>Willing to work in-person / hybrid (some days in an office)?</span>
        <select style={input} value={onsite} onChange={e => setOnsite(e.target.value)}>
          <option value="">Not set — ask me each time</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </div>
      </details>

      <details className="panel">
        <summary>Resume</summary>
        <div className="panelbody">
        <p style={{ color: resumeInfo ? T.ok : T.danger }}>
          {resumeInfo || "No resume uploaded — submissions will escalate without one."}
        </p>
        <input type="file" accept="application/pdf"
               onChange={e => uploadResume(e.target.files?.[0])} />

        {extraction && (
          <div style={{ ...box, background: T.panelAlt, borderColor: T.accent, marginTop: 16, marginBottom: 0 }}>
            <strong style={{ color: T.accent }}>Extracted from your resume</strong>
            <p style={{ color: T.muted, fontSize: 13 }}>
              Nothing is saved until you apply it, review the fields, and hit
              Save profile. Applying replaces the work-history form with the
              extracted roles and merges skills.
            </p>
            <ul style={{ fontSize: 14 }}>
              {extraction.name && <li>Name: {extraction.name}</li>}
              {extraction.location && <li>Location: {extraction.location}</li>}
              {extraction.work_auth && <li>Work authorization: {extraction.work_auth}</li>}
              {extraction.skills?.length > 0 && <li>{extraction.skills.length} skills: {extraction.skills.join(", ")}</li>}
              {extraction.work_history?.length > 0 && (
                <li>{extraction.work_history.length} roles: {extraction.work_history.map(r => `${r.title} @ ${r.company}`).join("; ")}</li>
              )}
            </ul>
            <button style={btnPrimary} onClick={applyExtraction}>Apply to form</button>{" "}
            <button style={btn} onClick={dismissExtraction}>Dismiss</button>
          </div>
        )}
      </div>
      </details>

      <details className="panel">
        <summary>Work history</summary>
        <div className="panelbody">
        {history.map((r, i) => (
          <div key={i} style={{ ...box, background: T.panelAlt }}>
            <span style={label}>Company</span>
            <input style={input} value={r.company} onChange={e => setRole(i, { company: e.target.value })} />
            <span style={label}>Title</span>
            <input style={input} value={r.title} onChange={e => setRole(i, { title: e.target.value })} />
            <div style={{ display: "flex", gap: 12 }}>
              <div style={{ flex: 1 }}>
                <span style={label}>Start (YYYY-MM)</span>
                <input style={input} value={r.start} onChange={e => setRole(i, { start: e.target.value })} />
              </div>
              <div style={{ flex: 1 }}>
                <span style={label}>End (blank = current)</span>
                <input style={input} value={r.end} onChange={e => setRole(i, { end: e.target.value })} />
              </div>
            </div>
            <span style={label}>Bullets (one per line — these are the facts letters can use)</span>
            <textarea
              style={{ ...input, height: 110 }}
              value={(r.bullets || []).join("\n")}
              onChange={e => setRole(i, { bullets: e.target.value.split("\n") })}
            />
            <button style={btn} onClick={() => setHistory(h => h.filter((_, j) => j !== i))}>
              Remove role
            </button>
          </div>
        ))}
        <button style={btn} onClick={() => setHistory(h => [...h, { ...EMPTY_ROLE }])}>
          + Add role
        </button>
      </div>
      </details>

      <details className="panel">
        <summary>Education</summary>
        <div className="panelbody">
        <p style={{ color: T.muted, fontSize: 13 }}>
          Degrees, bootcamps, certifications. For early-career matching this
          carries the weight work history carries for veterans.
        </p>
        {education.map((e, i) => (
          <div key={i} style={{ ...box, background: T.panelAlt }}>
            <span style={label}>School / program</span>
            <input style={input} value={e.school} onChange={ev => setEdu(i, { school: ev.target.value })} />
            <div style={{ display: "flex", gap: 12 }}>
              <div style={{ flex: 2 }}>
                <span style={label}>Degree / credential (e.g. BS Biology)</span>
                <input style={input} value={e.degree} onChange={ev => setEdu(i, { degree: ev.target.value })} />
              </div>
              <div style={{ flex: 1 }}>
                <span style={label}>Year (or expected)</span>
                <input style={input} value={e.year} onChange={ev => setEdu(i, { year: ev.target.value })} />
              </div>
            </div>
            <span style={label}>Highlights (one per line — coursework, honors, GPA, activities)</span>
            <textarea
              style={{ ...input, height: 90 }}
              value={(e.bullets || []).join("\n")}
              onChange={ev => setEdu(i, { bullets: ev.target.value.split("\n") })}
            />
            <button style={btn} onClick={() => setEducation(x => x.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        ))}
        <button style={btn} onClick={() => setEducation(x => [...x, { ...EMPTY_EDU }])}>
          + Add education
        </button>
      </div>
      </details>

      <details className="panel">
        <summary>Projects</summary>
        <div className="panelbody">
        <p style={{ color: T.muted, fontSize: 13 }}>
          School, personal, volunteer, or open-source work — anything you
          built or ran. Letters and matching can use these as facts.
        </p>
        {projects.map((p, i) => (
          <div key={i} style={{ ...box, background: T.panelAlt }}>
            <span style={label}>Project name</span>
            <input style={input} value={p.name} onChange={ev => setProject(i, { name: ev.target.value })} />
            <span style={label}>What you built / did, and the outcome</span>
            <textarea
              style={{ ...input, height: 90 }}
              value={p.description}
              onChange={ev => setProject(i, { description: ev.target.value })}
            />
            <span style={label}>Tools / skills used (comma-separated)</span>
            <input style={input} value={p.tech} onChange={ev => setProject(i, { tech: ev.target.value })} />
            <button style={btn} onClick={() => setProjects(x => x.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        ))}
        <button style={btn} onClick={() => setProjects(x => [...x, { ...EMPTY_PROJECT }])}>
          + Add project
        </button>
      </div>
      </details>

      <details className="panel">
        <summary>Writing samples</summary>
        <div className="panelbody">
        <p style={{ color: T.muted }}>
          Real prose in your voice — this is what keeps cover letters from
          sounding AI-generated. Emails, blog posts, docs — anything you wrote.
        </p>
        {samples.map((s, i) => (
          <div key={i} style={{ ...box, background: T.panelAlt }}>
            <span style={label}>Title</span>
            <input style={input} value={s.title} onChange={e => setSample(i, { title: e.target.value })} />
            <span style={label}>Text</span>
            <textarea style={{ ...input, height: 140 }} value={s.text}
                      onChange={e => setSample(i, { text: e.target.value })} />
            <button style={btn} onClick={() => setSamples(x => x.filter((_, j) => j !== i))}>
              Remove sample
            </button>
          </div>
        ))}
        <button style={btn} onClick={() => setSamples(x => [...x, { ...EMPTY_SAMPLE }])}>
          + Add sample
        </button>
      </div>
      </details>

      <div className="savebar">
        <button onClick={save} style={{ ...btnPrimary, padding: "10px 24px", fontSize: 16 }}>
          Save profile
        </button>
        <span style={{ marginLeft: 12 }}>{status}</span>
      </div>
    </main>
  );
}
