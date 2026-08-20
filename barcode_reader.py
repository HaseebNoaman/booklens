# barcode_reader.py
# This file reads the ISBN barcode from a book's BACK cover photo.
# Every printed book has an EAN-13 barcode that encodes its ISBN number.
# An ISBN identifies exactly ONE book edition, so if we can read it, the
# match is perfect — no OCR guessing and no fuzzy matching needed.
#
# We use OpenCV's built-in barcode detector (cv2.barcode). It has been part
# of the main opencv-python package since version 4.8, so this needs NO new
# libraries — we reuse the computer-vision library the project already has.

import cv2

# Create the detector once when this file is imported (same idea as the
# OCR reader in ocrpp.py: building it on every scan would waste time).
detector = cv2.barcode.BarcodeDetector()


def pick_isbn(decoded_info):
    # From everything the detector read, keep only a real book ISBN.
    # An ISBN-13 is exactly 13 digits and always starts with 978 or 979
    # (the "Bookland" prefixes reserved for books). This filter skips
    # price barcodes and any misreads.
    for code in decoded_info:
        if code and code.isdigit() and len(code) == 13 \
                and code.startswith(("978", "979")):
            return code
    return ""


def read_isbn(imagepath):
    # Try to find and decode a barcode in the image.
    # Returns the 13-digit ISBN as a string, or "" if no usable barcode.
    try:
        image = cv2.imread(imagepath)
        if image is None:
            return ""

        # Attempt LADDER. OpenCV's detector is tuned for camera PHOTOS
        # (naturally a little soft); razor-sharp scans/renders can fail
        # until blurred, and very large photos can fail until downscaled.
        # Measured on 10 rendered EAN-13 barcodes (test_covers/
        # evaluate_barcodes.py): plain + 3x3 blur decoded only 3/10; adding
        # the 5x5 blur rung took it to 10/10. Each rung only runs when the
        # previous ones failed, so photos that already worked cost nothing.
        candidates = [image,
                      cv2.GaussianBlur(image, (3, 3), 0),
                      cv2.GaussianBlur(image, (5, 5), 0)]
        if image.shape[1] > 1500:
            scale = 1000 / image.shape[1]
            small = cv2.resize(image, None, fx=scale, fy=scale)
            candidates.append(cv2.GaussianBlur(small, (3, 3), 0))

        for cand in candidates:
            ok, decoded_info, decoded_types, corners = \
                detector.detectAndDecodeWithType(cand)
            if ok:
                isbn = pick_isbn(decoded_info)
                if isbn:
                    return isbn

        return ""
    except Exception:
        # A barcode problem must NEVER break the scan — the caller simply
        # falls back to the normal OCR path when we return "".
        return ""
