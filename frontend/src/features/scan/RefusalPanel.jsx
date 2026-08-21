/* What the reader sees when BookLens will not guess.
   Refusing is the product working, not an error, so this screen gets the
   same care as a success: it says which of the two failures happened, shows
   what the camera actually read, offers three ways forward, and ends with
   the one thing still worth saying when the book is unknown.
*/
import React, { useState } from "react";
import { ClosestShelf } from "./ClosestShelf.jsx";

// The refusal screen.
//
// BookLens refuses rather than guesses, so this is not an error state -- it is
// the product working. It gets the same design care as a success, and it always
// offers a way forward.
//
// The guidance is chosen by the failure reason the pipeline already recorded,
// because the two failures need opposite advice:
//   the cover could not be READ   -> a better photo genuinely helps
//   read fine, nothing matched    -> a clearer photo may still help, but the
//                                    barcode or the exact title verifies faster
// Telling someone to retake a photo that was never the problem is how an
// honest refusal still ends up wasting their time.
function refusalGuidance(failureReason, ocrStatus) {
  const unreadable = failureReason === "ocr_needs_user_edit" ||
    ocrStatus === "OCR_FAILED" || ocrStatus === "OCR_LOW_CONFIDENCE";
  if (unreadable) {
    return {
      detail: "The title text on this cover could not be read clearly.",
      hint: "A straight-on photo in even light, with the whole cover in frame, usually fixes this.",
      primary: "retake",
    };
  }
  return {
    detail: "The cover was read, but no record matched it closely enough to be certain.",
    hint: "A clearer photo may help; for faster verification, scan the barcode or enter the exact title.",
    primary: "barcode",
  };
}


function RefusalPanel({ result, onRetake, onBarcode, onTypeTitle, token }) {
  const guidance = refusalGuidance(result.failure_reason,
                                   (result.ocr || {}).status);
  const detected = (result.ocr || {}).extracted_title || "";

  return (
    <div className="card refusal-card">
      <span className="eyebrow refusal-eyebrow">Not identified</span>
      <h3 className="refusal-title">Couldn&rsquo;t confidently identify this book</h3>
      <p className="refusal-detail">{guidance.detail}</p>
      <p className="refusal-hint">{guidance.hint}</p>

      {/* What WAS read, when anything was. Showing it turns a dead end into a
          diagnosis: the reader can see whether the camera caught the wrong
          text, and correct it in one step instead of guessing. */}
      {detected && (
        <p className="refusal-detected">
          Text read from the cover: <b>{detected}</b>
        </p>
      )}

      <div className="refusal-actions">
        <button type="button" onClick={onRetake}
                className={guidance.primary === "retake" ? "btn" : "btn-outline"}>
          Retake photo
        </button>
        <button type="button" onClick={onBarcode}
                className={guidance.primary === "barcode" ? "btn" : "btn-outline"}>
          Scan barcode
        </button>
        <button type="button" className="btn-outline" onClick={onTypeTitle}>
          Type the title
        </button>
      </div>

      {/* Refusing is the product working, but it still ends with the reader
          holding nothing. This is the one thing that can still be said
          truthfully when the book in their hand is unknown -- and it renders
          nothing at all when there is nothing honest to show. */}
      <ClosestShelf token={token} />
    </div>
  );
}


function FallbackForm({ defaultTitle, defaultAuthor, message, onSearchByTitle, onReset, onRetry, loading, error }) {
  const [title, setTitle] = useState(defaultTitle || "");
  const [author, setAuthor] = useState(defaultAuthor || "");
  const [isbn, setIsbn] = useState("");

  function submit(event) {
    event.preventDefault();
    onSearchByTitle(title, author, isbn);
  }

  return (
    <div className="card fallback-card">
      <h3>Not sure which book this is</h3>
      <p className="desc-text fallback-message">
        {message || "BookLens could not confidently match this cover."} Edit the detected title, add the author when known, or enter an ISBN.
      </p>
      {onRetry && (
        <button className="btn full retry-button" type="button" disabled={loading} onClick={onRetry}>
          {loading ? "Retrying…" : "Try the same scan again"}
        </button>
      )}
      <form onSubmit={submit}>
        <label htmlFor="fallback-title">Book title</label>
        <input id="fallback-title" value={title} onChange={(event) => setTitle(event.target.value)}
               placeholder="e.g. The Great Gatsby" />
        <label htmlFor="fallback-author">Author <span className="td-muted">(optional)</span></label>
        <input id="fallback-author" value={author} onChange={(event) => setAuthor(event.target.value)}
               placeholder="Author name" />
        <label htmlFor="fallback-isbn">ISBN <span className="td-muted">(optional)</span></label>
        <input id="fallback-isbn" value={isbn} onChange={(event) => setIsbn(event.target.value)}
               inputMode="numeric" placeholder="ISBN-10 or ISBN-13" />
        {error && <div className="error-msg" role="alert">{error}</div>}
        <button className="btn full form-submit" type="submit" disabled={loading}>
          {loading ? "Searching…" : "Search by title"}
        </button>
      </form>
      <button className="btn-outline full secondary-action" type="button" onClick={onReset}>Choose another image</button>
    </div>
  );
}
export { refusalGuidance, RefusalPanel, FallbackForm };
