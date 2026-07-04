"""Engine-side state transitions with idempotency.

Every trigger function funnels through advance(): a Firestore transaction that
re-reads the current state, checks legality against ENGINE_TRANSITIONS, and
appends to stateHistory. Because Firestore triggers are at-least-once, a
replayed event will find the state already advanced and no-op cleanly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

from schemas import AppState, transition_allowed


def app_ref(db: firestore.Client, uid: str, app_id: str) -> firestore.DocumentReference:
    return db.collection("users").document(uid).collection("applications").document(app_id)


def advance(
    db: firestore.Client,
    uid: str,
    app_id: str,
    expected: AppState,
    to: AppState,
    note: Optional[str] = None,
    extra_fields: Optional[dict] = None,
) -> bool:
    """Move an application expected->to atomically.

    Returns True if the transition happened, False if the doc was not in the
    expected state (duplicate event, race, or manual intervention) — callers
    should treat False as "someone else already did my job" and stop.
    """
    ref = app_ref(db, uid, app_id)

    @firestore.transactional
    def _txn(txn: firestore.Transaction) -> bool:
        snap = ref.get(transaction=txn)
        if not snap.exists:
            return False
        current = AppState(snap.get("state"))
        if current != expected:
            return False
        if not transition_allowed(current, to):
            raise ValueError(f"Illegal engine transition {current} -> {to}")

        now = datetime.now(timezone.utc)
        update = {
            "state": to.value,
            "updatedAt": now,
            "stateHistory": firestore.ArrayUnion(
                [{"state": to.value, "ts": now, "note": note}]
            ),
        }
        if extra_fields:
            update.update(extra_fields)
        txn.update(ref, update)
        return True

    return _txn(db.transaction())
