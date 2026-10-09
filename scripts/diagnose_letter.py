"""Why did the wrong-employer guard refuse this user's letter?

The banner the user sees names ONE stray employer and blames writing
samples. Both can be misleading: the name comes from the first check (not
the one that actually failed), and the stray can just as easily come from
real work history, which a cover letter is supposed to mention.

This runs the real guard against the real profile so you can see which of
the two shapes you have:

  * a genuine leak   — the letter is about the wrong employer
  * a guard misfire  — the letter is correct, but the posting's company
                       name is long enough that the letter writes a short
                       form of it, which closes the "right employer is
                       named" escape hatch and lets a legitimately
                       mentioned past employer trip the check

Usage:
    python scripts/diagnose_letter.py <email> [posting-company-substring]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "functions"))

from google.cloud import firestore           # noqa: E402

from letter_guard import _squash, wrong_employer  # noqa: E402
from schemas import UserProfile              # noqa: E402


def main(email: str, needle: str = "") -> int:
    db = firestore.Client()

    users = list(db.collection("users").where("email", "==", email).limit(1).stream())
    if not users:
        print(f"no user with email {email!r}")
        return 1
    uid, user = users[0].id, users[0].to_dict()
    print(f"uid: {uid}\n")

    profile = UserProfile.model_validate(user)

    print("WORK HISTORY (every one of these is a stray candidate)")
    for w in profile.work_history:
        print(f"  - {w.company!r}")
    if not profile.work_history:
        print("  (none)")

    print("\nWRITING SAMPLES")
    for s in profile.writing_samples[:3]:
        text = s.text or ""
        print(f"  - {s.title!r}: {len(text)} chars")
        print(f"      {text[:160].strip()!r}...")
    if not profile.writing_samples:
        print("  (none — so the banner's advice does not apply)")

    # The applications that are stuck: scored and matched, no letter.
    apps = db.collection("users").document(uid).collection("applications")
    stuck = []
    for snap in apps.where("state", "==", "matched").stream():
        a = snap.to_dict() or {}
        pid = a.get("posting_id")
        post = (db.collection("jobPostings").document(pid).get().to_dict()
                or {}) if pid else {}
        company = post.get("company") or ""
        if needle and needle.lower() not in company.lower():
            continue
        stuck.append((snap.id, post, a))

    print(f"\nMATCHED-BUT-UNDRAFTED APPLICATIONS: {len(stuck)}")
    for app_id, post, a in stuck:
        company = post.get("company") or ""
        desc = post.get("description_text") or post.get("descriptionText") or ""
        print(f"\n  app {app_id}")
        print(f"    company : {company!r}")
        print(f"    squashed: {_squash(company)!r}")
        print(f"    desc    : {len(desc)} chars"
              + ("   <-- THIN: drafting has little to anchor on" if len(desc) < 400 else ""))

        # The escape hatch only opens if the letter reproduces the company
        # name in full. Show whether a natural short form would close it.
        words = [w for w in company.split() if len(w) > 2]
        if len(words) >= 4:
            print(f"    NOTE: {len(words)}-word company name. A letter that "
                  f"writes a short form of this")
            print(f"          (initials, or just the parent institution) will "
                  f"NOT match {_squash(company)!r},")
            print(f"          which closes the escape hatch and lets any past "
                  f"employer trip the guard.")

        letter = (a.get("letter") or {}).get("text") or ""
        if letter:
            stray = wrong_employer(letter, post, profile)
            print(f"    stored letter: {len(letter)} chars -> "
                  + (f"GUARD FIRES on {stray!r}" if stray else "guard passes"))
            print(f"    names the company in full: "
                  f"{_squash(company) in _squash(letter)}")
        else:
            print("    no stored letter (the draft was refused before saving)")

    print("\nReading the result:")
    print("  * company named in full = False AND the stray is a real past")
    print("    employer -> guard misfire; the letter was probably fine.")
    print("  * the stray appears where the POSTING's company should be")
    print("    -> genuine leak; the draft really is about the wrong place.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
