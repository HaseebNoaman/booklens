"""Put the selection into effect -- reversibly.

Nothing is deleted. A book that leaves the shelf has its `verification_status`
moved from VERIFIED to NEEDS_REVIEW, which is the flag every reader-facing query
already filters on: `verified_catalogue_candidates`, `lookup_verified_catalogue`,
browse, the catalogue detail route, the starter shelf and the closest shelf all
ask for VERIFIED and nothing else. The row, its summary and its identifiers stay
exactly where they were.

A removed book is therefore still identifiable from a cover photo. It simply
takes the external provider path and is labelled External rather than Verified,
which is the honest description of what we then know about it.

    python curate/apply.py                # say what would change, change nothing
    python curate/apply.py --confirm      # do it
    python curate/apply.py --restore --confirm   # put every dropped book back

The gate in select.py has to have passed. Shipping a shelf that failed its own
acceptance test is the one thing this script will not do.
"""
import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

import database                                    # noqa: E402

SELECTION = os.path.join(HERE, "selection.json")
NOTE = "Removed from the verified shelf by curate/select.py: %s"


def load():
    if not os.path.exists(SELECTION):
        sys.exit("No selection.json -- run curate/select.py first.")
    with io.open(SELECTION, encoding="utf-8") as handle:
        return json.load(handle)


def current_status(record_id):
    row = database.get_catalogue_book(record_id)
    return row["verification_status"] if row is not None else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true",
                        help="actually write; without it nothing changes")
    parser.add_argument("--restore", action="store_true",
                        help="return every dropped book to VERIFIED")
    args = parser.parse_args()

    selection = load()
    if not args.restore and not selection.get("gate_passed"):
        sys.exit("The acceptance gate FAILED for this selection:\n   %s\n"
                 "Raise the size in select.py until it passes."
                 % "\n   ".join(selection.get("gate_failures") or []))

    targets = selection["drop"]
    want = "VERIFIED" if args.restore else "NEEDS_REVIEW"
    verb = "restore" if args.restore else "remove"

    todo = [t for t in targets if current_status(t["id"]) not in (None, want)]
    print("%s %d of %d books (the rest are already %s)"
          % (verb, len(todo), len(targets), want))
    for item in todo[:10]:
        print("   %-40s %s" % (item["title"][:40], item.get("why", "")))
    if len(todo) > 10:
        print("   ... and %d more" % (len(todo) - 10))

    if not args.confirm:
        print("\nDry run. Nothing was written. Add --confirm to apply.")
        return

    changed = failed = 0
    for item in todo:
        payload = {"verification_status": want}
        if not args.restore:
            # Demoting a record also drops the machine-verified claim, because
            # the claim is what VERIFIED means here.
            payload["machine_verified"] = 0
            payload["verification_notes"] = NOTE % item.get("why", "not selected")
        else:
            payload["machine_verified"] = 1
            payload["verification_notes"] = ""
        try:
            if database.update_catalogue_book(item["id"], payload, None):
                changed += 1
            else:
                failed += 1
                print("   no such record:", item["id"])
        except ValueError as error:
            failed += 1
            print("   refused %-34s %s" % (item["title"][:34], error))

    database.reset_subject_counts()
    verified = len(database.list_catalogue("VERIFIED"))
    print("\n%d changed, %d refused. The verified shelf is now %d books."
          % (changed, failed, verified))
    print("Undo with: python curate/apply.py --restore --confirm")


if __name__ == "__main__":
    main()
