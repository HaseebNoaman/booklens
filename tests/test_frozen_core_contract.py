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

The one re-baseline so far, 2026-08-23: api.py lost 166 lines of Wikipedia code
that had no caller and could not have run even if it had one -- wikipediaapi is
not in requirements.txt, so the import failed inside a bare except and the
function returned "". Code that cannot execute cannot change a measurement, so
the benchmark was not re-run. A re-baseline that cannot make that argument in
one paragraph needs the 25 minutes instead.
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
