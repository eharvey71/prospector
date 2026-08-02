"""Per-application tailored resume: profile facts -> targeted PDF.

The resume, not the letter, is what ATS ranking and recruiter keyword
search run on. This reorders and rewords the candidate's OWN facts against
one posting — most relevant first, the posting's terminology for skills the
candidate genuinely has — renders a clean one-column PDF, and uploads it to
users/{uid}/resumes/{app_id}.pdf for the submission worker to attach.

Truthfulness is enforced structurally, not just by prompt:
  - Identity fields (company, title, dates, school, degree) are supplied to
    the LLM and must come back VERBATIM; code drops any role/education/
    project whose identity doesn't match the profile exactly.
  - Bullets may only reword profile bullets (same trust level as letters,
    which pass a grounding critique).
  - Any validation failure falls back to the uploaded static resume.
"""
from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field

from llm import generate_structured
from schemas import UserProfile

log = logging.getLogger("resume_tailor")

BUCKET = os.environ.get("STORAGE_BUCKET", "")

TAILOR_SYSTEM = """You tailor a candidate's resume to one job posting. You \
may ONLY: reorder items (most relevant to this posting first), drop items \
irrelevant to it, and reword the candidate's own bullet points — tightening \
them and using the POSTING'S exact terminology for skills/experience the \
candidate genuinely has. You may NEVER: invent facts, add skills not \
listed, change numbers, or alter companies, titles, dates, schools, or \
degrees — copy those fields VERBATIM from the facts (they are validated \
mechanically; altered items get discarded). summary is 2-3 lines aimed \
squarely at this posting, claiming only stated facts."""


class TailoredRole(BaseModel):
    company: str                    # verbatim from facts
    title: str                      # verbatim from facts
    dates: str                      # verbatim "start to end" from facts
    bullets: list[str] = Field(default_factory=list)


class TailoredProject(BaseModel):
    name: str                       # verbatim from facts
    blurb: str
    tech: list[str] = Field(default_factory=list)


class TailoredEducation(BaseModel):
    school: str                     # verbatim from facts
    degree: str                     # verbatim from facts
    year: str = ""
    highlights: list[str] = Field(default_factory=list)


class TailoredResume(BaseModel):
    summary: str
    skills: list[str] = Field(default_factory=list)   # most relevant first
    roles: list[TailoredRole] = Field(default_factory=list)
    projects: list[TailoredProject] = Field(default_factory=list)
    education: list[TailoredEducation] = Field(default_factory=list)


def build_tailored_resume(uid: str, app_id: str, profile: UserProfile,
                          posting: dict) -> str | None:
    """Generate, validate, render, upload. Returns the Storage path, or
    None when tailoring isn't possible (no facts, validation failure, no
    bucket) — callers fall back to the static resume."""
    if not BUCKET:
        return None
    if not (profile.work_history or profile.education or profile.projects):
        return None  # nothing to tailor from

    tailored = generate_structured(
        _tailor_prompt(profile, posting), TailoredResume,
        system=TAILOR_SYSTEM, max_tokens=3000,
    )
    tailored = _validate(tailored, profile)
    if tailored is None:
        log.warning("uid=%s app=%s tailored resume failed validation; "
                    "falling back to static", uid, app_id)
        return None

    pdf = render_pdf(tailored, profile)
    path = f"users/{uid}/resumes/{app_id}.pdf"
    from google.cloud import storage
    storage.Client().bucket(BUCKET).blob(path).upload_from_string(
        pdf, content_type="application/pdf")
    log.info("uid=%s app=%s tailored resume uploaded (%d roles, %d skills)",
             uid, app_id, len(tailored.roles), len(tailored.skills))
    return path


def _tailor_prompt(profile: UserProfile, posting: dict) -> str:
    role_lines = []
    for w in profile.work_history:
        dates = f"{w.start} to {w.end or 'present'}"
        role_lines.append(
            f"- company={w.company!r} title={w.title!r} dates={dates!r}\n"
            + "\n".join(f"    * {b}" for b in w.bullets))
    roles = "\n".join(role_lines)
    education = "\n".join(
        f"- school={e.school!r} degree={e.degree!r} year={e.year or ''!r}"
        + ("\n" + "\n".join(f"    * {b}" for b in e.bullets) if e.bullets else "")
        for e in profile.education
    )
    projects = "\n".join(
        f"- name={p.name!r} tech={', '.join(p.tech)}\n    * {p.description}"
        for p in profile.projects
    )
    desc = (posting.get("descriptionText") or posting.get("description_text") or "")[:6000]
    return f"""CANDIDATE FACTS (career stage: {profile.career_stage})
Skills: {", ".join(profile.skills)}
Roles (copy company/title/dates verbatim):
{roles or "(none)"}
Education (copy school/degree verbatim):
{education or "(none)"}
Projects (copy name verbatim):
{projects or "(none)"}

TARGET POSTING: {posting.get("title")} at {posting.get("company")}
{desc}

Produce the tailored resume content."""


def _validate(t: TailoredResume, profile: UserProfile) -> TailoredResume | None:
    """Drop anything whose identity fields aren't verbatim from the profile.
    Returns None when so much was dropped the result would misrepresent by
    omission (e.g. the candidate has roles but none survived)."""
    real_roles = {(w.company, w.title) for w in profile.work_history}
    real_dates = {(w.company, w.title): f"{w.start} to {w.end or 'present'}"
                  for w in profile.work_history}
    roles = []
    for r in t.roles:
        key = (r.company, r.title)
        if key in real_roles and r.bullets:
            roles.append(TailoredRole(company=r.company, title=r.title,
                                      dates=real_dates[key], bullets=r.bullets))
    t.roles = roles

    real_edu = {(e.school, e.degree) for e in profile.education}
    t.education = [e for e in t.education if (e.school, e.degree) in real_edu]
    real_projects = {p.name for p in profile.projects}
    t.projects = [p for p in t.projects if p.name in real_projects]

    real_skills = {s.lower() for s in profile.skills}
    t.skills = [s for s in t.skills if s.lower() in real_skills] or profile.skills

    if profile.work_history and not t.roles:
        return None
    if not (t.roles or t.education or t.projects):
        return None
    return t


# ---------------------------------------------------------------------------
# PDF rendering (fpdf2, core fonts). Core fonts are latin-1; sanitize.
# ---------------------------------------------------------------------------

# Smart punctuation the LLM loves -> latin-1 equivalents (an em dash is not
# latin-1 and would render as '?').
_PUNCT = {"—": "-", "–": "-", "‘": "'", "’": "'",
          "“": '"', "”": '"', "…": "...", "•": "-", "™": "(TM)",
          " ": " "}


def _tx(s: str) -> str:
    s = s or ""
    for bad, good in _PUNCT.items():
        s = s.replace(bad, good)
    return s.encode("latin-1", "replace").decode("latin-1")


def render_pdf(t: TailoredResume, profile: UserProfile) -> bytes:
    """One-column layout. Every block RESETS X to the left margin and uses
    explicit new_x/new_y — fpdf2's multi_cell leaves the cursor at the
    block's right edge by default, which shoved every bullet after the
    first into a skinny column at the page's right. Long titles wrap
    instead of overflowing into the right-aligned dates."""
    from fpdf import FPDF

    pdf = FPDF(format="letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(18, 15, 18)
    pdf.add_page()
    W = pdf.epw  # effective page width
    DATE_W = 34  # right column reserved for dates/years

    def block(text: str, *, size=10, style="", width=None, height=5) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(width or W, height, _tx(text),
                       new_x="LMARGIN", new_y="NEXT")

    def heading(text: str) -> None:
        pdf.ln(2)
        block(text.upper(), size=11, style="B", height=6)
        y = pdf.get_y()
        pdf.line(pdf.l_margin, y, pdf.l_margin + W, y)
        pdf.ln(1.5)

    def titled_row(left: str, right: str) -> None:
        """Bold wrapped title on the left, date right-aligned at its top."""
        pdf.set_x(pdf.l_margin)
        y0 = pdf.get_y()
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.multi_cell(W - DATE_W, 5.5, _tx(left),
                       new_x="LMARGIN", new_y="NEXT")
        y1 = pdf.get_y()
        if right:
            pdf.set_xy(pdf.l_margin + W - DATE_W, y0)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(DATE_W, 5.5, _tx(right), align="R")
        pdf.set_xy(pdf.l_margin, max(y1, y0 + 5.5))

    def bullet(text: str) -> None:
        block(f"- {text}", width=W - 4)

    # Name + contact
    block(profile.name, size=16, style="B", height=8)
    contact = " | ".join(x for x in (
        profile.email, profile.phone, profile.location,
        profile.linkedin, profile.website) if x)
    block(contact, size=9, height=4.5)
    pdf.ln(1)

    if t.summary:
        block(t.summary)

    if t.skills:
        heading("Skills")
        block(" · ".join(t.skills))

    if t.roles:
        heading("Experience")
        for r in t.roles:
            titled_row(f"{r.title} — {r.company}", r.dates)
            pdf.set_font("Helvetica", "", 10)
            for b in r.bullets:
                bullet(b)
            pdf.ln(1)

    if t.projects:
        heading("Projects")
        for p in t.projects:
            tech = f"  ({', '.join(p.tech)})" if p.tech else ""
            titled_row(p.name + tech, "")
            pdf.set_font("Helvetica", "", 10)
            bullet(p.blurb)
            pdf.ln(1)

    if t.education:
        heading("Education")
        for e in t.education:
            titled_row(f"{e.degree}, {e.school}", e.year or "")
            pdf.set_font("Helvetica", "", 10)
            for h in e.highlights:
                bullet(h)
            pdf.ln(1)

    return bytes(pdf.output())
