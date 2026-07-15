"""Admin reset for local testing. Deletes engine-generated data, never your
profile, watchlist, or resume.

Usage:
    python reset.py                 # show what would be deleted, delete nothing
    python reset.py --apps --yes    # delete all your applications
    python reset.py --postings --yes  # delete all crawled jobPostings
    python reset.py --all --yes     # both: full clean slate
"""
import argparse

from google.cloud import firestore

PROJECT = "job-engine-c8f9c"
UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"


def delete_all(coll, label: str, do_it: bool) -> None:
    docs = list(coll.stream())
    print(f"{label}: {len(docs)} docs")
    if not do_it or not docs:
        return
    batch = coll._client.batch()
    for i, doc in enumerate(docs):
        batch.delete(doc.reference)
        if (i + 1) % 400 == 0:
            batch.commit()
            batch = coll._client.batch()
    batch.commit()
    print(f"{label}: deleted")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apps", action="store_true", help="delete users/{uid}/applications")
    ap.add_argument("--postings", action="store_true", help="delete jobPostings")
    ap.add_argument("--all", action="store_true", help="both")
    ap.add_argument("--yes", action="store_true", help="actually delete (otherwise dry run)")
    ap.add_argument("--uid", default=UID)
    args = ap.parse_args()

    apps = args.apps or args.all
    postings = args.postings or args.all
    if not (apps or postings):
        apps = postings = True  # bare invocation: count everything, delete nothing
        args.yes = False

    db = firestore.Client(project=PROJECT)
    if apps:
        delete_all(
            db.collection("users").document(args.uid).collection("applications"),
            "applications", args.yes)
    if postings:
        delete_all(db.collection("jobPostings"), "jobPostings", args.yes)
    if not args.yes:
        print("dry run — nothing deleted. Add --yes to delete.")


if __name__ == "__main__":
    main()
