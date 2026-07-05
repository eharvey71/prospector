from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

db = firestore.Client(project="job-engine-c8f9c")
UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"

apps = db.collection("users").document(UID).collection("applications")
for doc in apps.where(filter=FieldFilter("state", "in",
        ["approved", "queued", "submitting", "needs_human", "failed"])).stream():
    d = doc.to_dict()
    sub = d.get("submission") or {}
    print("state:", d["state"])
    print("error/reason:", sub.get("error"))
    print("screenshots:", sub.get("screenshots"))
    print("history:", [(e["state"], e.get("note")) for e in d.get("stateHistory", [])])