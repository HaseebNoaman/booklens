/* The two cards a finished identification can produce.

   A confirmed book gets ResultCard; a single plausible-but-unconfirmed match
   gets SingleCandidateCard, which is the same card with a confirm bar and
   nothing written to the reader's library until they say yes.

   Everything these two are built from now lives beside this file:
   ResultFacts, RefusalPanel, CandidateSelection and ForYou.
   This file was 891 lines holding all of it at once.
*/
import React, { useEffect, useState } from "react";
import { BookCover } from "../../components/ui.jsx";
import { authFetch, readJson } from "../../services/api.js";
import { QuickOverview, ReadingTools, providerRecordUrl, sourceLabel,
         sourceTextFor } from "./BookOverview.jsx";
import { AlreadyRead, LiveSignals, MetadataItem, plainEdition,
         plainPageNote } from "./ResultFacts.jsx";
import { RefusalPanel, FallbackForm } from "./RefusalPanel.jsx";
import { CandidateSelection } from "./CandidateSelection.jsx";
import { ForYou } from "./ForYou.jsx";

// One candidate: show the answer, not a chooser.
//
// A "Choose the exact book" screen listing exactly one option is not a choice --
// it is a speed bump. In the 20-cover funnel, 16 of 19 confirmations offered a
// single option, so this was almost every scan. The reader had to commit before
// seeing anything worth having.
//
// So the single-candidate case shows the card itself, with the confirmation
// folded into it. Safety is unchanged: nothing is written to the library until
// "Yes, this is it" -- the server still requires an explicit confirm before the
// book exists. What changes is that the reader can see WHY they would say yes.
//
// The live overview is deliberately absent here: it is fetched per book id, and
// there is no book yet. It appears on the full card after confirmation.
function SingleCandidateCard({ result, token, scanImage, onResolved, onReset }) {
  const candidate = result.candidates[0];
  // "Is this for you?" is no longer fixed at the moment of the scan: the
  // starter shelf writes to the reader's library from inside this card, so the
  // answer has to be able to change under it.
  const [forYou, setForYou] = useState(candidate.for_you);
  useEffect(() => { setForYou(candidate.for_you); }, [candidate]);
  const [busy, setBusy] = useState(false);
  const [declined, setDeclined] = useState(false);
  const [error, setError] = useState("");

  // Said no. There is no second candidate to fall back to, so offering an empty
  // chooser would be a dead end -- send them to the things they can actually do.
  if (declined) {
    return <RefusalPanel result={{ ...result, failure_reason: "user_rejected_candidate" }}
                         token={token}
                         onRetake={() => onReset()}
                         onBarcode={() => onReset("barcode")}
                         onTypeTitle={() => onReset("type")} />;
  }

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      const response = await authFetch("/identify/confirm", token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ attempt_id: result.attempt_id,
                               candidate_id: candidate.candidate_id }),
      });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || "Could not confirm this book.");
      onResolved(data);
    } catch (err) {
      setError(err.message || "Could not reach the server.");
      setBusy(false);
    }
  }

  const evidence = candidate.edition_evidence || {};
  const published = (candidate.published_date || "").slice(0, 4);
  const facts = [
    candidate.page_count > 0 ? `${candidate.page_count} pages` : "",
    published ? `published ${published}` : "",
    candidate.publisher || "",
  ].filter(Boolean).join(" · ");
  const recordUrl = providerRecordUrl(candidate);

  return (
    <article className="card result-card" aria-labelledby="confirm-title">
      <div className="result-layout">
        <div className="cover-column">
          {/* The reader's own photo stands in when the record has no cover --
              it is the book actually in front of them. */}
          <BookCover className="result-cover" src={candidate.thumbnail}
                     fallback={scanImage} alt={`Cover of ${candidate.title}`} />
        </div>
        <div className="result-content">
          <AlreadyRead record={candidate.already_read} />
          <span className="eyebrow">Best match</span>
          <h2 className="book-title" id="confirm-title">{candidate.title}</h2>
          <p className="book-author">by {candidate.author || "Unknown author"}</p>

          <div className="confirm-bar">
            <p className="confirm-question">Is this the book in front of you?</p>
            <div className="button-row">
              <button className="btn" type="button" disabled={busy} onClick={confirm}>
                {busy ? "Saving…" : "Yes, this is it"}
              </button>
              <button className="btn-outline" type="button" disabled={busy}
                      onClick={() => setDeclined(true)}>
                No
              </button>
            </div>
          </div>
          {error && <div className="error-msg" role="alert">{error}</div>}

          <ForYou forYou={forYou} token={token} title={candidate.title}
                  categories={candidate.categories} onAnswered={setForYou} />

          {facts && <p className="candidate-facts-line">{facts}</p>}
          <p className="edition-plain">{plainEdition(evidence)}</p>

          {recordUrl && (
            <div className="result-actions">
              <a className="btn-outline" href={recordUrl} target="_blank" rel="noreferrer">
                Find online
              </a>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}


// resetLabel, onMarkRead and markingRead exist for Browse, which renders this
// same card for a verified book nobody scanned. There the last button closes an
// overlay rather than starting another scan, and the reader has no history row
// yet -- so the library controls that hang off result.history_id stay hidden,
// and "I have read this" stands in their place. A scan passes none of the
// three and behaves exactly as it did.
function ResultCard({ result, onReset, onSearchByTitle, onRetry, loading, error, token,
                      onLibraryChanged, onResolved, scanImage,
                      resetLabel = "Scan another book", onMarkRead,
                      markingRead = false }) {
  const book = result.book;
  const ocr = result.ocr || {};
  const [correcting, setCorrecting] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  // A refusal shows the designed panel first. The title form appears only when
  // the reader picks "Type the title" -- dropping them straight into a form
  // states the failure and the remedy in the same breath, and hides the fact
  // that the barcode is usually the faster way out.
  const [typingTitle, setTypingTitle] = useState(false);
  // Single owner for the favourite flag, shared with ReadingTools below so the
  // action row and the library panel can never show different answers.
  const [favorite, setFavorite] = useState(Boolean(result.is_favorite));
  const [savingFavorite, setSavingFavorite] = useState(false);
  // Same reason as the confirm card: the starter shelf can change this answer
  // without a new scan, so it cannot be read straight off the prop.
  const [forYou, setForYou] = useState(result.for_you);
  useEffect(() => { setForYou(result.for_you); }, [result]);

  if (result.status === "needs_confirmation" && result.candidates) {
    // One option is not a choice. The chooser earns its place only when there
    // are genuine alternatives to weigh.
    if (result.candidates.length === 1) {
      return <SingleCandidateCard result={result} token={token} scanImage={scanImage}
                                  onResolved={onResolved} onReset={onReset} />;
    }
    return <CandidateSelection result={result} token={token} onResolved={onResolved} onReset={onReset} />;
  }

  async function rejectMatch() {
    try {
      if (result.history_id) {
        await authFetch(`/history/${result.history_id}`, token, { method: "DELETE" });
        onLibraryChanged();
      }
    } finally {
      setCorrecting(true);
    }
  }

  // Refused, and the reader has not asked to type a title yet.
  if (!book && !correcting && !typingTitle) {
    return <RefusalPanel result={result} token={token}
                         onRetake={onRetry || onReset}
                         onBarcode={() => onReset("barcode")}
                         onTypeTitle={() => setTypingTitle(true)} />;
  }

  if (!book || correcting) {
    return <FallbackForm defaultTitle={ocr.extracted_title || ""} defaultAuthor={ocr.extracted_author || ""}
                         message={book ? "" : result.message} onSearchByTitle={onSearchByTitle}
                         onReset={onReset} onRetry={correcting ? null : onRetry}
                         loading={loading} error={error} />;
  }

  const isbn = book.isbn_13 || book.isbn_10 || result.isbn;
  const publishedYear = (book.published_date || "").slice(0, 4);
  // Two separate questions, answered separately by the server. See
  // edition_evidence() in app.py.
  //
  //   identity   -- do we know WHICH copy the reader is holding?
  //   pageBasis  -- what does this page count actually describe?
  //
  // A record having an ISBN proves neither. And confirming identity does not
  // by itself make the page count exact: Open Library reports page_count as a
  // median across the work's editions, so an OL record can match the reader's
  // own ISBN and still be describing an average of other printings.
  const evidence = result.edition_evidence || {};
  const isbnConfirmed = evidence.identity === "isbn_confirmed";
  const pageBasis = evidence.page_basis || "unknown";

  const editionMatch = plainEdition(evidence);

  // The page count is never hidden for being small -- it is qualified by where
  // it came from, so the reader can judge it.
  const pageNote = plainPageNote(pageBasis);

  // "Find online" points at the provider's own record for this edition; the
  // helper returns "" for catalogue rows, whose text is not an external
  // record, and the button hides itself in that case.
  const recordUrl = providerRecordUrl(book);
  // Same question the overview section asks, so the button and the text it
  // reveals can never disagree about whether there is anything to show.
  const previewText = (sourceTextFor(book) || "").trim();

  // Preview expands the collapsed provider description in place. The section
  // owns that <details>; opening it here keeps one source of truth for the
  // text instead of rendering a second copy.
  async function toggleSave() {
    if (!result.history_id) return;
    setSavingFavorite(true);
    try {
      const response = await authFetch(`/history/${result.history_id}/favorite`,
                                       token, { method: "POST" });
      const data = await readJson(response);
      if (response.ok) {
        setFavorite(Boolean(data.is_favorite));
        onLibraryChanged?.();
      }
    } finally {
      setSavingFavorite(false);
    }
  }

  function openSourceText() {
    const node = document.getElementById("source-details");
    if (!node) return;
    node.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // A duration reads as a fact about the reader's own copy, so it appears only
  // when both questions above answer yes: their ISBN identified the record AND
  // that record's page count belongs to that same ISBN's edition.
  const readingLength = isbnConfirmed && pageBasis === "isbn_edition" &&
    book.page_count > 0
    ? `${book.page_count < 200 ? "Short" : book.page_count < 400 ? "Standard" : "Long"} · about ${Math.max(1, Math.round(book.page_count / 40))} hours`
    : "";

  return (
    <article className="card result-card" aria-labelledby="result-title">
      {result.confidence === "medium" && !confirmed && (
        <div className="confirmation-banner">
          <p><b>Is this the right book?</b> BookLens found a likely match, but wants your confirmation.</p>
          <div className="button-row">
            <button className="btn" onClick={() => setConfirmed(true)}>Yes, that is it</button>
            <button className="btn-outline" onClick={rejectMatch}>No, let me correct it</button>
          </div>
        </div>
      )}

      <div className="result-layout">
        <div className="cover-column">
          {/* The reader's own photo stands in when the record carries no cover.
              15 of 19 cards in the funnel test had none, because catalogue rows
              have no cover column at all. */}
          <BookCover className="result-cover" src={book.thumbnail}
                     fallback={scanImage} alt={`Cover of ${book.title}`} />
          <span className={`source-badge ${book.catalogue_id ? "verified" : "external"}`}>{sourceLabel(book)}</span>
        </div>
        <div className="result-content">
          {/* "Book identified" sat directly beneath the source badge and above
              the title, so the card opened with two labels telling the reader
              something the title already tells them. The badge carries the
              part that is actually informative: who vouched for this. */}
          <AlreadyRead record={result.already_read} />
          <h2 className="book-title" id="result-title">{book.title}</h2>
          <p className="book-author">by {book.author || "Unknown author"}</p>
          <LiveSignals live={result.live} exactLength={Boolean(readingLength)} />
          {/* Subjects are shown ONCE, inside "Is this for you?", where they
              carry meaning. Repeating them here as decoration made the card
              say the same thing twice. */}
          {/* Two groups, because the reader is asking two different questions:
              how sure is this, and which object is it. Mixing "Identification"
              with "Publisher" in one flat list made neither easy to scan.
              MetadataItem renders nothing for an empty value, so a group with
              no known fields collapses to its heading and the rest of the card
              is unaffected. */}
          <ForYou forYou={forYou} token={token} title={book.title}
                  categories={book.categories} bookId={book.id}
                  onAnswered={setForYou} />

          {/* The description comes BEFORE the record details. A reader asks
              "is it for me", then "what is it about", and only then "which
              printing is this". The card used to answer the third question
              second, pushing the actual writing below a table. */}
          <QuickOverview book={book} token={token} />

          {/* The old "Identification" group reported "Match: User-confirmed
              match" and "Source: Local catalogue" -- the internal tiering
              vocabulary, restating what the badge at the top of the card
              already says, and telling the reader something they had just
              done themselves. The edition caveat is the only part that
              carried information, and it reads better as a sentence than as
              a table row. */}
          <p className="edition-plain">{editionMatch}</p>

          <h3 className="facts-heading">Book details</h3>
          <dl className="metadata-grid">
            <MetadataItem label="Publisher" value={book.publisher} />
            <MetadataItem label="Published" value={publishedYear} />
            <MetadataItem label="ISBN" value={isbn} />
            <MetadataItem label="Length" value={book.page_count > 0 ? `${book.page_count} pages${pageNote}` : ""} />
            <MetadataItem label="Reading time" value={readingLength} />
          </dl>
        </div>
      </div>

      {/* initialStatus matters wherever the card can open on a book that is
          already in the library -- Browse, after "I have read this". Without
          it the control opened on "Not started" for a book stored as finished,
          and the reader's next change wrote that back. A scan sends nothing,
          and the default is still "identified", which is what a fresh scan
          genuinely is. */}
      <ReadingTools historyId={result.history_id} token={token}
                    initialStatus={result.reading_status}
                    onChanged={onLibraryChanged} />
      <div className="result-actions">
        {/* Identifying a book already records it, so this is not "save the
            book" -- it marks it as one worth keeping. Labelled for what it
            does rather than implying the scan would otherwise be lost. */}
        {result.history_id && (
          <button className="btn" type="button" disabled={savingFavorite}
                  onClick={toggleSave}>
            {favorite ? "Saved to favourites" : "Save to favourites"}
          </button>
        )}
        {/* Browse's way into the library. Marking a book read is a deliberate
            act, which is exactly the signal the taste profile counts, and it
            needs no camera. It disappears once there is a history row, because
            the reading-status control above it does the same job better. */}
        {!result.history_id && onMarkRead && (
          <button className="btn" type="button" disabled={markingRead}
                  onClick={onMarkRead}>
            {markingRead ? "Saving…" : "I have read this"}
          </button>
        )}
        {/* Preview opens the provider text already on this page rather than
            sending the reader elsewhere; Find online leaves for the provider's
            own record. Both hide when there is nothing behind them, so the row
            never offers a button that does nothing. */}
        {previewText && (
          <button className="btn-outline" type="button" onClick={openSourceText}>
            Preview description
          </button>
        )}
        {recordUrl && (
          <a className="btn-outline" href={recordUrl} target="_blank" rel="noreferrer">
            Find online
          </a>
        )}
        <button className="btn-outline" onClick={onReset}>{resetLabel}</button>
      </div>
    </article>
  );
}


export { ResultCard };
