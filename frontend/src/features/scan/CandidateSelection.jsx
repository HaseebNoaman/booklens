/* The chooser: several plausible books, and the reader decides.
   Most scans land here. Nothing is written until one is confirmed, and
   "None of these is my book" leads to the refusal panel rather than back
   to an empty photo picker.
*/
import React, { useState } from "react";
import { BookCover } from "../../components/ui.jsx";
import { authFetch, readJson } from "../../services/api.js";
import { AlreadyRead } from "./ResultFacts.jsx";
import { RefusalPanel } from "./RefusalPanel.jsx";

function CandidateSelection({ result, token, onResolved, onReset }) {
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const [declined, setDeclined] = useState(false);
  const candidates = result.candidates || [];

  // None of these is the book in their hand.
  //
  // Most scans land on this screen, and it used to have the weakest way out of
  // the three result screens: a quiet "Search again" that returned the reader
  // to the photo picker with no route to the barcode or to typing the title --
  // the two things that actually work once a chooser has failed. The refusal
  // panel and the single-candidate card have offered all three routes for a
  // while. The busiest screen now offers them too.
  if (declined) {
    return <RefusalPanel result={{ ...result, failure_reason: "user_rejected_candidate" }}
                         token={token}
                         onRetake={() => onReset()}
                         onBarcode={() => onReset("barcode")}
                         onTypeTitle={() => onReset("type")} />;
  }
  // Providers format the same author differently (for example "R.F. Kuang"
  // and "R. F. Kuang"). Ignore punctuation when deciding whether candidates
  // are editions of the same work; ISBN still distinguishes the exact edition.
  const normalizeIdentity = (value) => String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  const normalizeWorkTitle = (value) => normalizeIdentity(value)
    .replace(/\b(?:lp|large print|paperback|hardcover|ebook)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const identity = (candidate) => `${normalizeWorkTitle(candidate.title)}|${normalizeIdentity(candidate.author)}`;
  const sameWorkDifferentEditions = candidates.length > 1 &&
    candidates.every((candidate) => identity(candidate) === identity(candidates[0]));

  async function select(candidate) {
    setBusyId(candidate.candidate_id);
    setError("");
    try {
      const response = await authFetch("/identify/confirm", token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ attempt_id: result.attempt_id, candidate_id: candidate.candidate_id }),
      });
      const data = await readJson(response);
      if (response.ok) onResolved(data);
      else setError(data.error || "That candidate could not be confirmed.");
    } catch {
      setError("BookLens could not reach the server. Please try again.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="card candidate-section">
      <div className="status-banner warning" role="status">
        <h3>{sameWorkDifferentEditions ? "Choose the edition" : "Choose the exact book"}</h3>
        <p>{sameWorkDifferentEditions
          ? "The title and author identify the same work, but providers returned different editions. Publisher, year, language, pages and ISBN help distinguish them."
          : (result.message || "A few books look like this cover. Which one is it?")}</p>
        {sameWorkDifferentEditions && (
          <p className="edition-help">A front cover usually identifies the work. Scan or enter the back-cover ISBN when you need an exact edition.</p>
        )}
      </div>
      <div className="candidate-grid">
        {candidates.map((candidate, index) => (
          <article className="candidate-card" key={candidate.candidate_id}>
            <BookCover src={candidate.thumbnail} alt={`Cover of ${candidate.title}`} />
            <div className="candidate-copy">
              {sameWorkDifferentEditions && <span className="edition-option">Edition option {index + 1}</span>}
              <AlreadyRead record={candidate.already_read} />
              <h4 className="candidate-title">{candidate.title}</h4>
              <p className="book-author">by {candidate.author || "Unknown author"}</p>
              <dl className="candidate-facts">
                {candidate.publisher && <div><dt>Publisher</dt><dd>{candidate.publisher}</dd></div>}
                {candidate.published_date && <div><dt>Published</dt><dd>{candidate.published_date}</dd></div>}
                {candidate.page_count > 0 && <div><dt>Length</dt><dd>{candidate.page_count} pages</dd></div>}
                {candidate.description_language?.name && <div><dt>Description</dt><dd>{candidate.description_language.name}</dd></div>}
                {(candidate.isbn_13 || candidate.isbn_10) && <div><dt>ISBN</dt><dd>{candidate.isbn_13 || candidate.isbn_10}</dd></div>}
              </dl>
{/* The raw score and the ranking reasons were shown here --
                  "Candidate match · 64%", "Recovered from scrambled OCR;
                  confirmation required". Those are internals: a percentage
                  invites the reader to trust a number the method cannot
                  justify, and the reasons describe the algorithm rather than
                  the book. The cover, title, author and publisher are what a
                  person actually compares against the object in their hand. */}
            </div>
            <button className="btn candidate-action" disabled={busyId !== null} onClick={() => select(candidate)}>
              {busyId === candidate.candidate_id ? "Confirming…" : "Select this edition"}
            </button>
          </article>
        ))}
      </div>
      {error && <div className="error-msg" role="alert">{error}</div>}
      {/* Says what the reader is actually thinking. "Search again" read like
          they had done something wrong, and led nowhere useful. */}
      <button className="btn-outline candidate-reset" onClick={() => setDeclined(true)}>
        None of these is my book
      </button>
    </div>
  );
}
export { CandidateSelection };
