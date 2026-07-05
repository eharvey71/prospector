from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

db = firestore.Client(project="job-engine-c8f9c")
UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"

apps = db.collection("users").document(UID).collection("applications")
for doc in apps.where(filter=FieldFilter("state", "==", "needs_human")).stream():
    doc.reference.update({
        "state": "approved",
        "submission.error": None,
        "submission.screenshots": [],
        "stateHistory": firestore.ArrayUnion([{
            "state": "approved",
            "ts": datetime.now(timezone.utc),
            "note": "admin reset to retest with adapter v2",
        }]),
        "updatedAt": datetime.now(timezone.utc),
    })
    print("reset", doc.id)
    
    
# from google.cloud import firestore
# from google.cloud.firestore_v1.base_query import FieldFilter

# db = firestore.Client(project="job-engine-c8f9c")
# UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"

# apps = db.collection("users").document(UID).collection("applications")
# for doc in apps.where(filter=FieldFilter("state", "in",
#         ["approved", "queued", "submitting", "needs_human", "failed"])).stream():
#     d = doc.to_dict()
#     sub = d.get("submission") or {}
#     print("state:", d["state"])
#     print("error/reason:", sub.get("error"))
#     print("screenshots:", sub.get("screenshots"))
#     print("history:", [(e["state"], e.get("note")) for e in d.get("stateHistory", [])])