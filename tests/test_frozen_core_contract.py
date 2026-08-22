"""The identification core must not drift.

api.py, matching.py, ocrpp.py and barcode_reader.py produce the measured result:
74 correct, 8 wrong, 13 refused on the 100-cover benchmark, 85% precision when a
card appeared (bench/README.md, 2026-08-22). Packaging work, result-card work
and deployment work all happen AROUND these files, never inside them. This test
fails the moment one of them changes.

app.py and database.py are deliberately excluded: the card, the PORT fix and the
session wiring legitimately touch them.

If a change to a frozen file is genuinely intended, re-run the benchmark, then
regenerate the baseline:

    python -m tests.test_frozen_core_contract --rebaseline

Re-baselining without re-measuring is how a result silently stops being true.

Re-baselines so far, all 2026-08-23:

  1. Deleted 166 lines of Wikipedia code that had no caller and could not have
     run even if it had one -- wikipediaapi is not in requirements.txt, so the
     import failed inside a bare except and the function returned "".
  2. Deleted resolve_description(), 73 lines. It chose which source's text to
     show, and nothing has called it since whatitsabout_heuristic.py took that
     over -- verified against commit 31064c2, where it was already callerless.
     Its five helpers stay: they are what build_external_overview() fetches with.

  3. Retired the single-winner matcher: api.searchbook() and, with it,
     matching.pick_best(), verify_against_cover(), probable_title_agreement()
     and title_token_coverage() -- 324 lines. searchbook's only two callers
     were the disabled legacy endpoints, which returned 410 on their first
     statement long before this cleanup; deleting those endpoints is what made
     the rest visible. This is the FIRST re-baseline to touch matching.py.

The first two deleted code that could not execute, so the benchmark was not
re-run for them. The third could not make that argument -- matching.py is the
file the 74/100 comes from -- so it was proved three ways instead, in a git
worktree, before a line of the live tree was touched:

  * a tripwire in all four functions, recording any call to a file rather than
    raising (app.py's `except Exception` would have swallowed a raise). Three
    real cover scans, two typed searches and one ISBN search: not one call.
    The recorder itself was proved to fire by calling a function directly.
  * the full suite, 254 green -- weak evidence on its own here, because no
    test imports any of the four.
  * bench/run_images.py over 20 covers, before and after, sharing one database
    and one provider cache so the code was the only variable. Every field the
    harness records was identical: same decision, same shown list in the same
    order, same scores. 14 correct, 0 wrong, 1 refused, both times.

A re-baseline that cannot make the "it could not run" argument in one paragraph
needs that treatment, or the 25 minutes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).with_name("core_baseline.json")

FROZEN_SOURCE_FILES = (
    "api.py",
    "matching.py",
    "ocrpp.py",
    "barcode_reader.py",
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_hashes() -> dict:
    return {name: file_sha256(ROOT / name) for name in FROZEN_SOURCE_FILES}


def test_frozen_core_has_not_changed():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current = current_hashes()
    drifted = [n for n in FROZEN_SOURCE_FILES if baseline["files"].get(n) != current[n]]
    assert not drifted, (
        "Frozen identification core changed: %s. The 80/100 benchmark result no "
        "longer describes this code. Re-measure before re-baselining." % ", ".join(drifted)
    )


if __name__ == "__main__":
    import sys

    if "--rebaseline" in sys.argv:
        BASELINE_PATH.write_text(
            json.dumps({"files": current_hashes()}, indent=2) + "\n", encoding="utf-8"
        )
        print("Re-baselined %d files." % len(FROZEN_SOURCE_FILES))
    else:
        print(__doc__)
