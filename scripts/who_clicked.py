"""The 13: did the engine file them, or did a human finish them?

stateHistory is the record. A worker submission passes through
queued -> submitting; a human finishing with the extension goes
needs_human -> submitted, or in_review -> submitted.
"""
from google.cloud import firestore

db = firestore.Client()
for user in db.collection("users").stream():
    uid = user.id
    for snap in (db.collection("users").document(uid)
                 .collection("applications").where("state", "==", "submitted").stream()):
        a = snap.to_dict() or {}
        sub = a.get("submission") or {}
        hist = [h.get("state") for h in (a.get("state_history") or a.get("stateHistory") or [])]
        notes = [h.get("note") for h in (a.get("state_history") or a.get("stateHistory") or [])]
        print(f"{snap.id[:8]}  tier={sub.get('tier')}  "
              f"clicked={bool(sub.get('submitClickedAt'))}  "
              f"path={'->'.join(hist[-4:])}")
        print(f"          last note: {notes[-1]!r}")
