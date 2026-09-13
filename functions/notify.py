"""Email notifications: a once-a-day digest of new matches.

Delivery is the Firebase "Trigger Email from Firestore" extension: this
module writes a document to the `mail` collection and the extension sends
it. Nothing here talks to an SMTP server, so a missing extension degrades
to unsent documents rather than errors — and no other code path depends
on mail being delivered.

Per match instead of per day was tempting and wrong: a crawl can surface
a dozen matches at once, and twelve emails is how a notification feature
gets muted in a week.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

log = logging.getLogger("notify")

APP_URL = "https://job-engine-c8f9c.web.app"
MAX_ROWS = 12          # listed in the email; the rest are a "+N more" line


def send_match_digests(db: firestore.Client) -> int:
    """One digest per user with new matches since their last digest.
    Returns the number of emails queued."""
    sent = 0
    for user_doc in db.collection("users").stream():
        try:
            if _digest_for_user(db, user_doc):
                sent += 1
        except Exception:
            log.exception("digest failed uid=%s", user_doc.id)
    return sent


def _collect_rows(db: firestore.Client, uid: str, since=None) -> list[dict]:
    """The user's matched jobs, newest-matched first when `since` is given
    and simply all of them when it isn't (the test digest)."""
    q = (db.collection("users").document(uid).collection("applications")
         .where(filter=FieldFilter("state", "==", "matched")))
    if since is not None:
        q = q.where(filter=FieldFilter("updatedAt", ">", since))
    rows = []
    for snap in q.limit(50).stream():
        a = snap.to_dict() or {}
        p = (db.collection("jobPostings").document(a.get("posting_id") or snap.id)
             .get().to_dict() or {})
        rows.append({
            "score": (a.get("match") or {}).get("score"),
            "title": p.get("title") or "(untitled)",
            "company": p.get("company") or "",
            "url": p.get("url") or "",
        })
    rows.sort(key=lambda r: r["score"] or 0, reverse=True)
    return rows


def _queue_mail(db: firestore.Client, to: str, rows: list[dict],
                prefix: str = "") -> None:
    db.collection("mail").add({
        "to": [to],
        "message": {
            "subject": prefix + _subject(rows),
            "html": _html(rows),
            "text": _text(rows),
        },
    })


def send_test_digest(db: firestore.Client, uid: str) -> dict:
    """Queue the real digest, on demand, from the user's current matches.

    Deliberately ignores both the daily window and the lastSentAt marker
    so it can be run any time, and does NOT move that marker — a test
    must not swallow tomorrow's real digest. Raises ValueError with a
    user-facing reason when there's nothing to send."""
    data = db.collection("users").document(uid).get().to_dict() or {}
    to = (data.get("email") or "").strip()
    if not to:
        raise ValueError("no email address on your Profile to send to")
    rows = _collect_rows(db, uid)
    if not rows:
        raise ValueError("you have no matched jobs right now, so a digest "
                         "would be empty — add a job by URL or wait for the "
                         "next crawl, then try again")
    _queue_mail(db, to, rows, prefix="[test] ")
    log.info("queued TEST digest uid=%s matches=%d", uid, len(rows))
    return {"to": to, "matches": len(rows)}


def _digest_for_user(db: firestore.Client, user_doc) -> bool:
    uid = user_doc.id
    data = user_doc.to_dict() or {}
    prefs = data.get("preferences") or {}
    if not prefs.get("email_matches", False):
        return False
    to = (data.get("email") or "").strip()
    if not to:
        return False

    state_ref = db.collection("users").document(uid).collection("stats").document("digest")
    since = (state_ref.get().to_dict() or {}).get("lastSentAt")
    if since is None:
        # First run: look back a day rather than emailing the whole history.
        since = datetime.now(timezone.utc) - timedelta(days=1)

    rows = _collect_rows(db, uid, since)
    if not rows:
        return False

    _queue_mail(db, to, rows)
    state_ref.set({"lastSentAt": datetime.now(timezone.utc),
                   "lastCount": len(rows)}, merge=True)
    log.info("queued digest uid=%s matches=%d", uid, len(rows))
    return True


def _subject(rows: list[dict]) -> str:
    n = len(rows)
    top = rows[0]
    if n == 1:
        return f"Prospector: {top['title']} at {top['company']}"
    return (f"Prospector: {n} new matches — top is {top['title']} "
            f"at {top['company']}")


def _text(rows: list[dict]) -> str:
    lines = [f"{r['score'] if r['score'] is not None else '--'}  "
             f"{r['title']} at {r['company']}\n    {r['url']}"
             for r in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        lines.append(f"...and {len(rows) - MAX_ROWS} more")
    return ("New matches waiting in Prospector:\n\n"
            + "\n".join(lines)
            + f"\n\nReview them: {APP_URL}\n")


def _html(rows: list[dict]) -> str:
    items = []
    for r in rows[:MAX_ROWS]:
        score = r["score"] if r["score"] is not None else "--"
        title = escape(r["title"])
        link = (f'<a href="{escape(r["url"], quote=True)}" '
                f'style="color:#4a7de2;text-decoration:none">{title}</a>'
                if r["url"] else title)
        items.append(
            '<tr>'
            '<td style="padding:6px 10px 6px 0;color:#4a7de2;font-weight:700;'
            'white-space:nowrap">' + str(score) + '</td>'
            '<td style="padding:6px 0">' + link
            + ' <span style="color:#3f4b5c">' + escape(r["company"]) + '</span></td>'
            '</tr>')
    more = (f'<p style="color:#3f4b5c">…and {len(rows) - MAX_ROWS} more.</p>'
            if len(rows) > MAX_ROWS else "")
    return (
        '<div style="font:14px/1.5 system-ui,-apple-system,sans-serif;'
        'color:#14181e;max-width:560px">'
        f'<h2 style="font-size:18px;margin:0 0 4px">'
        f'{len(rows)} new match{"es" if len(rows) != 1 else ""}</h2>'
        '<p style="color:#3f4b5c;margin:0 0 14px">Scored above your bar. '
        'Nothing is submitted without your approval.</p>'
        '<table style="border-collapse:collapse">' + "".join(items) + '</table>'
        + more +
        f'<p style="margin-top:18px"><a href="{APP_URL}" '
        'style="background:#4a7de2;color:#fff;padding:9px 18px;'
        'border-radius:8px;text-decoration:none;font-weight:600">'
        'Review them</a></p>'
        '<p style="color:#3f4b5c;font-size:12px;margin-top:18px">'
        'Turn these off in Settings → Matching &amp; drafting behavior.</p>'
        '</div>')
