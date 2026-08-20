import os
import sys
import types

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

# Route tests exercise control flow with OCR/barcode/model calls mocked. This
# keeps the suite fast and avoids loading multi-gigabyte runtime dependencies.
ocr_stub = types.ModuleType("ocrpp")
ocr_stub.OCR_REC_TIER = "mobile"
ocr_stub.OCR_ESCALATE_REC_TIER = "medium"
ocr_stub.process_book_cover = lambda path, rec_tier=None: {
    "probable_title": "", "probable_author": "", "full_text": "",
    "confidence_score": 0.0, "error": "No text found",
}
sys.modules.setdefault("ocrpp", ocr_stub)

barcode_stub = types.ModuleType("barcode_reader")
barcode_stub.read_isbn = lambda path: ""
sys.modules.setdefault("barcode_reader", barcode_stub)

summarizer_stub = types.ModuleType("summarizer")
summarizer_stub.MODEL_LOADED = False
summarizer_stub.load_model = lambda: None
summarizer_stub.generate_summary = lambda text, title="", author="", source_verified=False, **kwargs: {
    "summary": "" if not source_verified else "Short verified overview.",
    "method": "test", "status": "unavailable" if not source_verified else "ready",
}
sys.modules.setdefault("summarizer", summarizer_stub)
