"""Grant or revoke the Prospector admin custom claim.

Admins can edit the global board catalog (/admin page) and see the user
list for enrollment. Needs firebase-admin and application-default
credentials with rights on the project:

    pip install firebase-admin
    gcloud auth application-default login

Usage:
    python scripts/set_admin.py <UID>            # grant
    python scripts/set_admin.py <UID> --revoke   # revoke

The user must sign out and back in afterward — claims live in the ID
token, which refreshes on sign-in.
"""
import sys

import firebase_admin
from firebase_admin import auth

PROJECT = "job-engine-c8f9c"


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        sys.exit(1)
    uid = sys.argv[1]
    revoke = "--revoke" in sys.argv[2:]
    firebase_admin.initialize_app(options={"projectId": PROJECT})
    user = auth.get_user(uid)  # fails loudly on a bad uid
    auth.set_custom_user_claims(uid, None if revoke else {"admin": True})
    print(f"{'revoked admin from' if revoke else 'granted admin to'} "
          f"{user.email or uid} — they must sign out and back in.")


if __name__ == "__main__":
    main()
