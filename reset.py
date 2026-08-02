"""Admin reset for local testing. Deletes engine-generated data, never
profiles, watchlists, or resumes.

Usage:
    python reset.py                        # dry run: counts, deletes nothing
    python reset.py --apps --yes           # delete YOUR applications + stats
    python reset.py --apps --yes --uid X   # another user's
    python reset.py --apps --all-users --yes   # EVERY user's applications+stats
    python reset.py --postings --yes       # delete all crawled jobPostings
    python reset.py --all --yes            # apps (this uid) + postings
    python reset.py --all --all-users --yes  # everything engine-generated,
                                             # every user — full clean slate
"""
import argparse

from google.cloud import firestore

PROJECT = "job-engine-c8f9c"
UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"


def delete_all(coll_or_docs, label: str, do_it: bool, client) -> None:
    docs = list(coll_or_docs.stream())
    print(f"{label}: {len(docs)} docs")
    if not do_it or not docs:
        return
    batch = client.batch()
    for i, doc in enumerate(docs):
        batch.delete(doc.reference)
        if (i + 1) % 400 == 0:
            batch.commit()
            batch = client.batch()
    batch.commit()
    print(f"{label}: deleted")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apps", action="store_true", help="delete applications (+ funnel stats)")
    ap.add_argument("--postings", action="store_true", help="delete jobPostings")
    ap.add_argument("--all", action="store_true", help="apps + postings")
    ap.add_argument("--all-users", action="store_true",
                    help="apps/stats for EVERY user, not just --uid")
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
        if args.all_users:
            # Collection-group sweep: catches applications under "ghost"
            # users (subcollection exists, parent profile doc never saved)
            # that iterating the users collection would miss.
            delete_all(db.collection_group("applications"),
                       "applications (ALL users)", args.yes, db)
            delete_all(db.collection_group("stats"),
                       "funnel stats (ALL users)", args.yes, db)
        else:
            user_ref = db.collection("users").document(args.uid)
            delete_all(user_ref.collection("applications"),
                       f"applications ({args.uid})", args.yes, db)
            delete_all(user_ref.collection("stats"),
                       f"funnel stats ({args.uid})", args.yes, db)
    if postings:
        delete_all(db.collection("jobPostings"), "jobPostings", args.yes, db)
    if not args.yes:
        print("dry run — nothing deleted. Add --yes to delete.")


if __name__ == "__main__":
    main()
