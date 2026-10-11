"""Where do applications actually end up, and why?

The question this answers: has the engine ever submitted a job on its
own, and if not, what stopped it. Counts every application by state
across all users, then groups the escalations by reason — the reason
text is what says whether auto-submit is fixable or structurally
blocked.

Usage:
    python scripts/funnel.py
"""
from __future__ import annotations

import re
import sys
from collections import Counter

from google.cloud import firestore

ORDER = ["discovered", "matched", "drafted", "in_review", "approved",
         "queued", "submitting", "submitted", "needs_human", "failed",
         "rejected"]


def _norm(reason: str) -> str:
    """Collapse a reason to its shape so counts mean something: strip the
    URLs, quoted labels and numbers that make every message unique."""
    r = re.sub(r"https?://\S+", "<url>", reason or "")
    r = re.sub(r"[\"'“”‘’][^\"'“”‘’]{0,80}[\"'“”‘’]", "<quoted>", r)
    r = re.sub(r"\b\d+\b", "<n>", r)
    return r.strip()[:120] or "(no reason recorded)"


def main() -> int:
    db = firestore.Client()
    states: Counter = Counter()
    reasons: Counter = Counter()
    clicked = 0
    per_user: dict[str, Counter] = {}

    for user in db.collection("users").stream():
        uid = user.id
        label = (user.to_dict() or {}).get("email") or uid[:8]
        mine: Counter = Counter()
        for snap in (db.collection("users").document(uid)
                     .collection("applications").stream()):
            a = snap.to_dict() or {}
            state = a.get("state", "?")
            states[state] += 1
            mine[state] += 1
            sub_map = a.get("submission") or {}
            if sub_map.get("submitClickedAt") or a.get("clicked_submit"):
                clicked += 1
            if state in ("needs_human", "failed"):
                sub = a.get("submission") or {}
                reasons[_norm(sub.get("error") or "")] += 1
        if mine:
            per_user[label] = mine

    total = sum(states.values())
    print(f"APPLICATIONS: {total}\n")
    for state in ORDER + sorted(set(states) - set(ORDER)):
        n = states.get(state, 0)
        if n:
            print(f"  {state:12} {n:6}  {n / total:5.1%}")

    print(f"\nCLICKED SUBMIT AT LEAST ONCE: {clicked}")
    print("  (this is the only number that says the engine really filed "
          "something;\n   'submitted' without it would mean the judge "
          "called it, not that we clicked)")

    if reasons:
        print(f"\nWHY IT STOPPED ({sum(reasons.values())} escalations)")
        for reason, n in reasons.most_common(15):
            print(f"  {n:5}  {reason}")

    print("\nPER USER")
    for label, mine in sorted(per_user.items()):
        done = mine.get("submitted", 0)
        print(f"  {label:34} {sum(mine.values()):5} apps, "
              f"{done} submitted, {mine.get('needs_human', 0)} needs_human")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
