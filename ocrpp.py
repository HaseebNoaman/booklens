# ocrpp.py
# This file reads the text from a book cover image using PaddleOCR.
# Main idea (Visual Hierarchy): on a book cover the TITLE is usually the
# biggest text and the AUTHOR name is smaller. So we measure the height of
# each piece of text and use the height to decide what is the title.
#
# ENGINE CHOICE — measured, not assumed.
#
# TWO RECOGNISERS, USED AS A LADDER. The headline finding is that the mobile
# and medium recognisers are COMPLEMENTARY, not ranked. On the 100-cover
# benchmark medium reads 12 covers mobile misses, and mobile reads 10 that
# medium misses. Compared head to head the difference looks negligible (+2),
# which is why the union went unexploited for so long:
#
#     mobile alone                 69 correct,  2 wrong, 29 rejected, 97%
#     medium alone                 71 correct,  2 wrong, 27 rejected, 97%
#     mobile, then medium ONLY
#     when mobile finds nothing    80 correct,  3 wrong, 17 rejected, 96%
#
# So the shipped behaviour is: read the cover with the FAST recogniser; if no
# book is matched, read it AGAIN with the medium recogniser before giving up
# and asking the user to type the title. That is +11 books found and twelve
# fewer covers that dump the user into manual entry, for one point of
# precision, still far above the documented 90% floor.
#
# It is also cheap. The second pass runs only on the ~29% of scans that have
# already failed, so an easy cover still costs about 9 seconds; only hard ones
# pay the extra. app.py drives the ladder — see the escalation loop in scan().
#
# DETECTION vs RECOGNITION. They are separate models and can be mixed.
# Measured on this machine at the production 1000px cap (one cover, warm):
#     det=medium rec=medium  33.2s   clean text
#     det=medium rec=mobile  25.6s   garbled ("Hew Yok", "Tset")
#     det=mobile rec=medium  13.6s   clean text
#     det=mobile rec=mobile   9.9s   garbled  <-- default first pass
# Detection is where the TIME goes (it sweeps the whole image); recognition is
# where the ACCURACY goes (it turns each crop into letters). That is exactly
# why escalating only the RECOGNISER is the cheap half of the trade.
#
# Override with OCR_DET_TIER / OCR_REC_TIER / OCR_ESCALATE_REC_TIER, or
# OCR_TIER for both halves of the first pass. Setting
# OCR_ESCALATE_REC_TIER equal to OCR_REC_TIER disables escalation entirely.
#
# TIMING of the first pass, measured 2026-07-25 at the 1000px cap, warm (the
# first one or two images after load are much slower because paddle
# initialises lazily — do not time those):
#     benchmark covers (n=20)      mean 10.76s  median 11.29s
#     real phone/web photos (n=5)  mean 13.03s  median 12.88s
#
# RESOLVED 2026-07-25: the "THE"/"10X" 10X Rule detection failure does NOT
# reproduce. Re-scanned the original phone photo at the 1000px cap and
# full_text contained THE / 10X / RULE / CARDONE. That failure was on an
# UNCAPPED full-resolution photo; the cap made it moot.
# EasyOCR (previous engine): correct 54, precision 92% — kept installed for
# comparison only; PaddleOCR reads stylized covers EasyOCR mangled.

# torch MUST be imported before paddle on Windows: paddlepaddle bundles its
# own copies of shared DLLs, and if paddle loads first, torch's shm.dll
# fails with WinError 127. The summarizer needs torch in the same process,
# so claim torch's DLLs first here.
import os

import torch  # noqa: F401  (imported for DLL load order, not used directly)

import cv2
from paddleocr import PaddleOCR

OCR_DET_MODELS = {"medium": "PP-OCRv6_medium_det", "mobile": "PP-OCRv5_mobile_det"}
OCR_REC_MODELS = {"medium": "PP-OCRv6_medium_rec", "mobile": "PP-OCRv5_mobile_rec"}


def _tier(var, default):
    tier = os.environ.get(var, os.environ.get("OCR_TIER", default)).strip().lower()
    return tier if tier in ("medium", "mobile") else default


OCR_DET_TIER = _tier("OCR_DET_TIER", "mobile")
OCR_REC_TIER = _tier("OCR_REC_TIER", "mobile")
OCR_TIER = f"det={OCR_DET_TIER}/rec={OCR_REC_TIER}"
_det_model = OCR_DET_MODELS[OCR_DET_TIER]
_rec_model = OCR_REC_MODELS[OCR_REC_TIER]

# Readers are built ONCE each and kept, because building one is slow.
_readers = {}


def _get_reader(det_tier, rec_tier):
    # Return the PaddleOCR instance for this pair of tiers, building it the
    # first time it is asked for. The DEFAULT pair is built eagerly below; the
    # escalation pair is built lazily, so a deployment whose scans always
    # succeed on the first pass never pays to load a second set of weights.
    key = (det_tier, rec_tier)
    if key not in _readers:
        print(f"LOADING OCR (PaddleOCR, det={det_tier}/rec={rec_tier})...")
        _readers[key] = PaddleOCR(
            lang="en",
            use_textline_orientation=True,     # reads rotated text (EasyOCR could not)
            use_doc_orientation_classify=False,  # document-scan extras, not needed
            use_doc_unwarping=False,             # for photos of covers
            text_detection_model_name=OCR_DET_MODELS[det_tier],
            text_recognition_model_name=OCR_REC_MODELS[rec_tier],
            # paddlepaddle 3.3.1 on Windows CPU crashes inside the oneDNN
            # executor (ConvertPirAttribute2RuntimeAttribute
            # NotImplementedError), so oneDNN acceleration stays off. Plain CPU
            # inference is correct, just slower.
            enable_mkldnn=False,
        )
    return _readers[key]


# THE ESCALATION TIER. When a scan fails on the default tier the caller may
# retry at this one before giving up and asking the user to type the title.
#
# WHY: the two recognisers are COMPLEMENTARY, not ranked. Measured on the
# 100-cover benchmark, medium reads 12 covers that mobile misses while mobile
# reads 10 that medium misses; the net is only +2, which is why the union was
# never exploited. Retrying only the FAILURES captures it:
#     mobile alone            69 correct, 29 rejected, 97% precision
#     medium alone            71 correct, 27 rejected, 97% precision
#     mobile then medium      80 correct, 17 rejected, 96% precision
# +11 books found, and twelve fewer covers that send the user to type the title
# by hand. It stays fast because the second pass only runs on the ~29% that
# already failed, so an easy cover still costs about 9 seconds.
OCR_ESCALATE_REC_TIER = _tier("OCR_ESCALATE_REC_TIER", "medium")

reader = _get_reader(OCR_DET_TIER, OCR_REC_TIER)

# These are common words printed on covers that are NOT the title or author.
# We will ignore any text block that contains one of these phrases.
NOISE_PHRASES = [
    'NEW YORK TIMES', 'BESTSELLER', 'A NOVEL', 'WINNER', 'PRIZE',
    'AWARD', 'MOTION PICTURE', 'EDITION', 'AUTHOR', 'BESTSELLING',
    'THE INTERNATIONAL', '#1', 'SUNDAY TIMES'
]


def isnoise(text):
    # Returns True if the text is one of the noise phrases above.
    textupper = text.upper()
    return any(noise in textupper for noise in NOISE_PHRASES)


# A text block counts as part of the title if it is at least this fraction as
# tall as the biggest text.
# Measured on the 100 covers in test_covers/: raising this to 0.55+ produced a
# "tidier" title, but it dropped the author's name from the text we send to
# Google, and the search then found the right book LESS often (recall ceiling
# fell 68 -> 59). Extra words help the search even when they are untidy, so we
# keep the original 0.50.
TITLE_HEIGHT_RATIO = 0.50

# Longest edge (pixels) handed to the OCR engine. See the sizing comment in
# extractbookinfo() for the measured time-vs-size curve. Override with
# OCR_MAX_DIM if a deployment wants to trade speed for resolution.
MAX_OCR_DIM = int(os.environ.get("OCR_MAX_DIM", "1000") or 1000)


def extractbookinfo(imagepath, rec_tier=None):
    # rec_tier overrides the recognition model for THIS call only. app.py
    # passes OCR_ESCALATE_REC_TIER on a retry after the first pass failed to
    # find a book; everything else about the pipeline stays identical, so the
    # only variable is how the letters were read.
    # Step 1: read the image with OpenCV. cv2.imread also applies EXIF
    # rotation, which matters for phone photos taken in portrait.
    image = cv2.imread(imagepath)
    if image is None:
        return {"error": "Could not read image file"}

    # Step 1b: normalise the image SIZE before OCR.
    # OCR time grows steeply with pixel count while the text read out stays the
    # same — a cover's title is huge at any of these sizes. Measured on this
    # machine (PP-OCRv6 medium, one cover, identical title text every time):
    #     1000px 27s | 1280px 44s | 1600px 52s | 3200px 179s
    # Phone photos arrive at 1600-4000px, so a photo used to cost 40-180s of
    # pure OCR. Capping the long edge is therefore free speed, not a trade.
    # 1000px is ALSO the resolution the 100-cover benchmark was measured at
    # (500px sources upscaled 2x), so production now runs in exactly the
    # regime those accuracy numbers (70/100, 96% precision) came from.
    # Tiny thumbnails still get the 2x upscale first — small text needs it —
    # and the cap then keeps the result from ballooning (a 900px image used to
    # become 1800px and cost ~60s).
    height_px, width_px = image.shape[:2]
    if max(height_px, width_px) < 1000:
        image = cv2.resize(image, None, fx=2, fy=2,
                           interpolation=cv2.INTER_CUBIC)
    longest_edge = max(image.shape[:2])
    if longest_edge > MAX_OCR_DIM:
        scale = MAX_OCR_DIM / longest_edge
        # INTER_AREA is the correct filter for shrinking (avoids aliasing that
        # would break thin letter strokes).
        image = cv2.resize(image, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)

    # Step 2: run OCR. PaddleOCR returns parallel lists: the text lines
    # (rec_texts), how sure it is about each (rec_scores), and the 4-corner
    # box around each line (rec_polys). The lists are in reading order.
    if rec_tier in (None, OCR_REC_TIER):
        engine = reader
    else:
        engine = _get_reader(OCR_DET_TIER, rec_tier)
    output = engine.predict(image)
    if not output:
        return {"error": "No text found on the cover"}
    result = output[0]
    texts = result.get("rec_texts") or []
    scores = result.get("rec_scores") or []
    polys = result.get("rec_polys")
    if polys is None:
        polys = result.get("dt_polys") or []

    extracted_blocks = []
    for text, confidence, poly in zip(texts, scores, polys):
        cleantext = str(text).strip()

        # Skip text the OCR was not sure about.
        if confidence < 0.35:
            continue
        # Skip tiny junk like single symbols.
        if len(cleantext) < 2 and not cleantext.isalnum():
            continue
        # Skip the noise phrases.
        if isnoise(cleantext):
            continue

        # Work out how tall this text is (in pixels).
        y_coords = [float(point[1]) for point in poly]
        height = max(y_coords) - min(y_coords)

        extracted_blocks.append({
            "text": cleantext,
            "height": height,
            "confidence": float(confidence)
        })

    # If nothing useful was found, return an error.
    if len(extracted_blocks) == 0:
        return {"error": "No text found on the cover"}

    # Keep ALL readable cover text (still in top-to-bottom reading order,
    # BEFORE we sort by height). This becomes a last-resort search query
    # for messy covers where the title guess below turns out wrong.
    all_cover_text = " ".join(b["text"] for b in extracted_blocks)

    # Step 3: sort all text blocks from TALLEST to SHORTEST.
    extracted_blocks.sort(key=lambda x: x["height"], reverse=True)

    # The tallest block height is our reference.
    max_height = extracted_blocks[0]["height"]

    title_blocks = []
    probable_author = ""
    for block in extracted_blocks:
        # Any text nearly as tall as the biggest text -> part of the title.
        if block["height"] >= max_height * TITLE_HEIGHT_RATIO:
            title_blocks.append(block)
        else:
            # The first smaller block that is not just numbers -> the author.
            if probable_author == "" and not block["text"].isdigit():
                probable_author = block["text"]

    # Join the title blocks in HEIGHT order (tallest first) — deliberately.
    # We tried re-joining them in reading order instead (so "Prejudice and
    # Pride" comes out as "Pride and Prejudice") and it looked tidier, but
    # measured end-to-end it found FEWER books (correct 54 -> 51 on EasyOCR,
    # evaluate_fair_comparison.py, 2026-07-10). The search query is capped
    # at 8 words, and tallest-first puts the most important title words
    # inside that cap; the fuzzy match that verifies the result is
    # word-order-insensitive, so the jumbled order costs nothing.
    probable_title = " ".join(b["text"] for b in title_blocks)

    return {
        "probable_title": probable_title,
        "probable_author": probable_author,
        "full_text": all_cover_text,
        "text_lines": [b["text"] for b in extracted_blocks],
        "ocr_method": "visual_hierarchy",
        "confidence_score": sum(b["confidence"] for b in extracted_blocks) / len(extracted_blocks)
    }


def process_book_cover(imagepath, rec_tier=None):
    # This is the function app.py calls. It wraps extractbookinfo and
    # returns a clean, predictable dictionary.
    result = extractbookinfo(imagepath, rec_tier=rec_tier)

    # If OCR failed, still return the dictionary but with empty values + error.
    if "error" in result:
        return {
            "probable_title": "",
            "probable_author": "",
            "raw_text": "",
            "cleaned_text": "",
            "full_text": "",
            "text_lines": [],
            "confidence_score": 0.0,
            "isbn": "",
            "error": result["error"]
        }

    probable_title = result["probable_title"]
    probable_author = result["probable_author"]
    rawtext = (probable_title + " " + probable_author).strip()

    return {
        "probable_title": probable_title,
        "probable_author": probable_author,
        "raw_text": rawtext,
        "cleaned_text": rawtext,
        "full_text": result.get("full_text", ""),
        "text_lines": result.get("text_lines", []),
        "confidence_score": float(result.get("confidence_score") or 0),
        "isbn": ""
    }
