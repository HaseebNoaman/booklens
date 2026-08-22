"""The real thing: 100 cover IMAGES through the real OCR and the real funnel.

Everything else measured so far replayed cached OCR. This opens the actual
images, runs PaddleOCR at both tiers, and drives the same ladder scan() uses --
so the question it answers is the one that matters: of 100 covers a person
could photograph, how many produce a result card showing the RIGHT book, and
how many show a wrong one.

Nothing is written to the database. retrieve_* only reads the catalogue, and
the persistence half of scan() (history rows, attempts, saved books) is
deliberately not called, so this can be re-run without polluting anything.

Usage:  python run_images.py <label>
"""
import csv
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
IMAGES = os.path.join(HERE, "covers")
MANIFEST = os.path.join(HERE, "manifest.csv")

sys.path.insert(0, APP)
os.chdir(APP)

import app as booklens                      # loads PaddleOCR for real
from matching import HIGH_CONFIDENCE, NEEDS_CONFIRMATION, REJECTED
from ocrpp import OCR_REC_TIER, OCR_ESCALATE_REC_TIER, process_book_cover
from rapidfuzz import fuzz


def expected_books():
    """The ground truth, read with the csv module and not with split(',').

    manifest.csv quotes the titles that contain commas -- "Thinking, fast and
    slow", "Rich Dad, Poor Dad", "Guns, Germs, and Steel". Splitting on the
    comma turns the first into `"Thinking` and scores three correct answers as
    failures. rescore.py was fixed for exactly this and this file was not, so
    the bug returned the moment the harness ran again: three books lost.
    """
    with open(MANIFEST, encoding="utf-8", newline="") as handle:
        return {row["file"]: (row["title"], row["author"])
                for row in csv.DictReader(handle)}


def main_title(value):
    # Providers append series and edition wording after a colon. The book is
    # identified by what comes before it.
    return (value or "").split(":")[0].strip().lower()


def title_score(shown, expected):
    """How close the title on the card is to the book that was photographed.

    NOT token_set_ratio, which was the first thing tried and is wrong here: it
    returns 100 whenever one title's words are a subset of the other, so "The
    Alchemist Cocktail Book" scored a perfect match for "The Alchemist". That
    would have inflated the headline number with books nobody photographed.
    ratio catches punctuation-only differences ("Slaughterhouse-Five"), while
    token_sort_ratio punishes the EXTRA words that signal a different book.
    Measured on known-answer pairs, real matches land at 82-100 and impostors
    at 38-72, so 80 separates them and 70-80 is sent for manual inspection.
    """
    return max(fuzz.ratio(main_title(expected), main_title(shown)),
               fuzz.token_sort_ratio(main_title(expected), main_title(shown)))


def author_score(shown, expected):
    if not shown or not expected:
        return None
    return fuzz.token_set_ratio(expected.lower(), shown.lower())


def judge(shown_title, shown_author, expected_title, expected_author):
    """correct / wrong / review. The review band is inspected by hand."""
    t = title_score(shown_title, expected_title)
    a = author_score(shown_author, expected_author)
    if t >= 80 and (a is None or a >= 60):
        return "correct", t, a
    if t >= 70:
        return "review", t, a
    if t >= 80:
        return "review", t, a
    return "wrong", t, a


def ladder(path):
    """scan()'s recogniser ladder, minus everything that writes to the database."""
    tiers = [OCR_REC_TIER]
    if OCR_ESCALATE_REC_TIER != OCR_REC_TIER:
        tiers.append(OCR_ESCALATE_REC_TIER)

    attempts = []
    recovery_fallback = None
    for tier in tiers:
        result = process_book_cover(path, rec_tier=tier)
        status = booklens.classify_ocr(result)
        attempts.append((result, status, tier))

        title = (result.get("probable_title") or "").strip()
        # Call the app's own sanitiser rather than re-implementing it here.
        author = booklens.usable_ocr_author(result.get("probable_author"))
        text = (result.get("full_text") or "").strip()
        lines = result.get("text_lines") or []
        if title or text or lines:
            local = booklens.retrieve_local_candidates(title, author, "", text, lines)
            if local["decision"] != REJECTED:
                if status != "OCR_SUCCESS" and local["decision"] == HIGH_CONFIDENCE:
                    local = dict(local)
                    local["decision"] = NEEDS_CONFIRMATION
                if local.get("tier") == booklens.RECOVERY_TIER:
                    if recovery_fallback is None:
                        recovery_fallback = (result, status, tier, local)
                else:
                    return result, status, tier, local, attempts

    for result, status, tier in sorted(
            (a for a in attempts if a[1] == "OCR_SUCCESS"),
            key=lambda a: float(a[0].get("confidence_score") or 0), reverse=True):
        title = (result.get("probable_title") or "").strip()
        author = booklens.usable_ocr_author(result.get("probable_author"))
        text = (result.get("full_text") or "").strip()
        ranked = booklens.retrieve_ranked_candidates(
            title, author, "", text, text_lines=result.get("text_lines") or [])
        ranked["tier"] = "external"
        if recovery_fallback is not None:
            ranked = booklens.merge_recovery_with_external(recovery_fallback[3], ranked)
        if ranked["decision"] != REJECTED:
            return result, status, tier, ranked, attempts

    if recovery_fallback is not None:
        return recovery_fallback + (attempts,)

    best = attempts[-1][0] if attempts else {}
    return best, (attempts[-1][1] if attempts else "OCR_FAILED"), tiers[-1], \
        {"decision": REJECTED, "candidates": [], "tier": "none"}, attempts


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "images"
    wanted = expected_books()
    files = sorted(f for f in os.listdir(IMAGES) if f in wanted)
    limit = int(os.environ.get("LIMIT", "0"))
    if limit:
        files = files[:limit]
    print("covers to run: %d" % len(files), flush=True)

    rows = []
    started = time.time()
    for i, name in enumerate(files, 1):
        exp_title, exp_author = wanted[name]
        t0 = time.time()
        try:
            result, status, tier, ranked, attempts = ladder(os.path.join(IMAGES, name))
            error = ""
        except Exception:                                      # noqa: BLE001
            result, status, tier, ranked, attempts = {}, "ERROR", "", \
                {"decision": "ERROR", "candidates": [], "tier": "none"}, []
            error = traceback.format_exc(limit=3)

        # What the reader is shown, not what the funnel returned: the chooser
        # collapses repeated editions before it reaches the screen.
        ranked = dict(ranked)
        ranked["candidates"] = booklens.collapse_duplicate_editions(
            booklens.drop_derived_products(ranked.get("candidates") or [],
                                           keep_when_empty=False))
        cands = ranked.get("candidates") or []
        top = cands[0].get("title") if cands else None
        top_author = cands[0].get("author") if cands else None
        if not cands:
            verdict, top_score, top_author_score = "refused", 0, None
        else:
            verdict, top_score, top_author_score = judge(
                top, top_author, exp_title, exp_author)
        best_in_list = max((title_score(c.get("title"), exp_title) for c in cands),
                           default=0)

        rows.append({
            "file": name,
            "expected_title": exp_title,
            "expected_author": exp_author,
            "verdict": verdict,
            "decision": ranked.get("decision"),
            "tier": ranked.get("tier"),
            "ocr_tier_used": tier,
            "ocr_status": status,
            "top": top,
            "top_score": top_score,
            "top_author_score": top_author_score,
            "best_in_list": best_in_list,
            "shown": [c.get("title") for c in cands],
            "shown_authors": [c.get("author") for c in cands],
            "ocr_passes": [
                {"tier": a[2], "status": a[1],
                 "title": (a[0].get("probable_title") or ""),
                 "author": (a[0].get("probable_author") or ""),
                 "full": (a[0].get("full_text") or "")[:400],
                 "confidence": float(a[0].get("confidence_score") or 0)}
                for a in attempts],
            "seconds": round(time.time() - t0, 1),
            "error": error,
        })
        print("%3d/%d %-16s %-8s %-28s exp=%-28s got=%s" % (
            i, len(files), name, verdict, (ranked.get("tier") or "")[:28],
            exp_title[:28], (top or "-")[:34]), flush=True)

        with open(os.path.join(HERE, label + ".json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)

    correct = sum(r["verdict"] == "correct" for r in rows)
    wrong = sum(r["verdict"] == "wrong" for r in rows)
    review = sum(r["verdict"] == "review" for r in rows)
    refused = sum(r["verdict"] == "refused" for r in rows)
    shown = correct + wrong + review
    print()
    print("== %s : REAL IMAGES through the REAL pipeline ==" % label)
    print("  covers               : %d" % len(rows))
    print("  correct book on card : %d" % correct)
    print("  WRONG book on card   : %d" % wrong)
    print("  NEEDS MY INSPECTION  : %d" % review)
    print("  refused (said unsure): %d" % refused)
    print("  precision when shown : %s" % (
        "%.0f%%" % (100.0 * correct / shown) if shown else "n/a"))
    print("  correct anywhere in the offered list: %d" % sum(
        r["best_in_list"] >= 80 for r in rows))
    print("  total minutes        : %.1f" % ((time.time() - started) / 60.0))


if __name__ == "__main__":
    main()
