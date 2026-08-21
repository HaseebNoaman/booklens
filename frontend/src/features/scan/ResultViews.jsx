import React, { useState } from "react";
import { Icon } from "../../components/ui.jsx";
import { authFetch, readJson } from "../../services/api.js";
import { BookCover } from "../../components/ui.jsx";
import { QuickOverview, ReadingTools, externalProvider, providerRecordUrl, sourceLabel, sourceTextFor } from "./BookOverview.jsx";

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

// "You have already read this."
//
// The only line on the card that is a FACT rather than an inference. Everything
// else here is the matcher's judgement or the taste profile's inference, both
// of which can be wrong; this one is a record of something the reader did. It
// goes above the title because in a shop it is the question being asked, and
// because a reader who has already read the book does not need the rest.
//
// A bare scan never produces this -- see database.prior_engagement(). Counting
// scans would mean the second look at a book announced "you have read this"
// purely because of the first.
function AlreadyRead({ record }) {
  if (!record) return null;
  const when = (record.when || "").slice(0, 10);
  const label = record.status === "reading"
    ? "You are reading this"
    : record.status === "finished"
      ? "You have read this"
      : "This is in your library";
  return (
    <p className="already-read" role="status">
      <Icon name="check" size={16} />
      <span>{label}{when ? ` — added ${when}` : ""}</span>
      {record.is_favorite && <span className="already-read-fav">Favourite</span>}
    </p>
  );
}


// What Open Library says about this book today.
//
// THREE WORDING DECISIONS, EACH CLOSING AN OBJECTION.
//
// The source is named inside the sentence -- "from 140 Open Library readers",
// never a bare "4.1 / 5". Those are Open Library's own users, not Goodreads and
// not the world, and saying so removes the overclaim rather than defending it.
//
// The shelf count is a FALLBACK, not a row. Next to a rating it adds nothing
// and invites the obvious objection: those are intentions, not readers. Shown
// only when there is no rating, it stops being redundant and becomes the one
// real signal a book published this year has -- measured, 86% of 2026 titles
// are known to Open Library and NONE are rated.
//
// The "~" before the page count is doing real work. This is a median across
// editions, not the copy in the reader's hand, and the card must not claim an
// exactness it does not have.
function LiveSignals({ live, exactLength }) {
  if (!live) return null;
  const { rating, n_ratings: raters, on_shelves: shelves,
          page_count: pages, rating_is_thin: thin, freshness } = live;
  const rows = [];

  if (rating) {
    rows.push(
      <p className="live-row" key="rating">
        <b>{rating} / 5</b> from {raters.toLocaleString()} Open Library reader{raters === 1 ? "" : "s"}
        {thin && <span className="live-caveat"> — too few to lean on</span>}
        {freshness && <span className="live-freshness">{freshness}</span>}
      </p>
    );
  } else if (shelves > 0) {
    rows.push(
      <p className="live-row" key="shelves">
        No rating yet. <b>{shelves.toLocaleString()}</b> people have it on a shelf
        <span className="live-caveat"> — that is interest, not a verdict</span>
      </p>
    );
  }

  // Never contradict an exact figure the reader's own edition already gave.
  if (pages > 0 && !exactLength) {
    rows.push(
      <p className="live-row" key="pages">
        ~{pages.toLocaleString()} pages · about {Math.max(1, Math.round(pages / 40))} hours
        <span className="live-caveat"> — median across editions</span>
      </p>
    );
  }

  if (!rows.length) return null;
  return <div className="live-signals">{rows}</div>;
}


function RefusalPanel({ result, onRetake, onBarcode, onTypeTitle }) {
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

function MetadataItem({ label, value }) {
  if (!value && value !== 0) return null;
  return <div className="metadata-item"><dt>{label}</dt><dd>{value}</dd></div>;
}

function MoreByAuthor({ author, excludeTitle, token }) {
  const [state, setState] = useState("idle");
  const [books, setBooks] = useState([]);
  const [note, setNote] = useState("");
  if (!author) return null;

  async function load() {
    setState("loading");
    try {
      const response = await authFetch("/books-by-author", token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ author, exclude_title: excludeTitle }),
      });
      const data = await readJson(response);
      setBooks(data.books || []);
      setNote(data.message || (data.books?.length ? "" : "No other books were found."));
    } catch {
      setNote("Suggestions are unavailable right now.");
    }
    setState("done");
  }

  return (
    <section className="more-author" aria-label={`More books by ${author}`}>
      {state === "idle" && <button className="btn-outline" onClick={load}><Icon name="book" size={15} /> More by {author.split(",")[0]}</button>}
      {state === "loading" && <p className="small" role="status">Looking for more books…</p>}
      {state === "done" && books.length > 0 && (
        <div>
          <h3 className="section-heading">More by {author.split(",")[0]}</h3>
          <div className="author-books">
            {books.map((item) => (
              <article className="author-book" key={`${item.title}-${item.published_date || ""}`}>
                <BookCover src={item.thumbnail} alt={`Cover of ${item.title}`} loading="lazy" />
                <b>{item.title}</b>
                {item.published_date && <small>{item.published_date}</small>}
              </article>
            ))}
          </div>
        </div>
      )}
      {state === "done" && note && <p className="small">{note}</p>}
    </section>
  );
}

// "Is this for you?" -- evidence from the reader's own library.
//
// Four states, and each says something different. They must not be collapsed:
// a book with no subjects is the PUBLISHER's gap, while an empty profile is the
// reader's starting point, and telling a well-read user to "build your profile"
// because a publisher omitted subjects would be simply wrong.
//
// No score, no percentage, no verdict. The section shows what the reader has
// read and lets them draw the conclusion.
function ForYou({ forYou }) {
  if (!forYou) return null;
  const { state, subjects = [], examples = [], book_count: bookCount = 0 } = forYou;

  if (state === "no_subject_data") {
    return (
      <section className="for-you" aria-labelledby="for-you-title">
        <h3 className="facts-heading" id="for-you-title">Is this for you?</h3>
        <p className="for-you-empty">
          Not enough subject data for this book to compare it with your library.
        </p>
      </section>
    );
  }

  // Interests are a weaker signal than a book someone read, and the wording
  // says so. It must never read like "you have read N books".
  if (state === "interest_match") {
    return (
      <section className="for-you" aria-labelledby="for-you-title">
        <h3 className="facts-heading" id="for-you-title">Is this for you?</h3>
        {/* Names the interest instead of saying "an interest you chose". A
            reader with eight interests wants to know WHICH one this book hits;
            the vague version told them something they already knew. */}
        <p className="for-you-evidence">
          You picked {subjects.length === 1 ? subjects[0] : subjects.slice(0, -1).join(", ") + " and " + subjects[subjects.length - 1]} — this is one
        </p>
        <div className="tag-row">
          {subjects.map((subject) => (
            <span className="tag tag-genre" key={subject}>{subject}</span>
          ))}
        </div>
        <p className="for-you-examples">
          Mark a few books you have read and this will answer from those instead.
        </p>
      </section>
    );
  }

  if (state === "cold_start") {
    return (
      <section className="for-you" aria-labelledby="for-you-title">
        <h3 className="facts-heading" id="for-you-title">Is this for you?</h3>
        <p className="for-you-empty">
          BookLens has nothing to compare this with yet. Tell it one book you
          have already read and it can answer.
        </p>
        {/* This used to be the end of the road: the reader was asked to save
            books with no way to do it, and no onboarding anywhere. */}
        <a className="btn for-you-action" href="#browse">Add a book you have read</a>
        {subjects.length > 0 && (
          <div className="tag-row">
            {subjects.map((subject) => (
              <span className="tag tag-genre" key={subject}>{subject}</span>
            ))}
          </div>
        )}
      </section>
    );
  }

  if (state === "no_match") {
    return (
      <section className="for-you" aria-labelledby="for-you-title">
        <h3 className="facts-heading" id="for-you-title">Is this for you?</h3>
        <p className="for-you-empty">
          None of the {bookCount} books in your library share these subjects.
        </p>
        {subjects.length > 0 && (
          <div className="tag-row">
            {subjects.map((subject) => (
              <span className="tag tag-genre" key={subject}>{subject}</span>
            ))}
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="for-you" aria-labelledby="for-you-title">
      <h3 className="facts-heading" id="for-you-title">Is this for you?</h3>
      <p className="for-you-evidence">
        You have read or saved {bookCount} {bookCount === 1 ? "book" : "books"} with these subjects
      </p>
      <div className="tag-row">
        {subjects.map((subject) => (
          <span className="tag tag-genre" key={subject}>{subject}</span>
        ))}
      </div>
      {examples.length > 0 && (
        // The count and the list must reconcile. Only three titles are shown,
        // so when the count is higher the remainder is stated outright --
        // otherwise the card claims five books and displays three, and the
        // first person to count them stops trusting the number.
        <p className="for-you-examples">
          {examples.join(", ")}
          {bookCount > examples.length &&
            ` and ${bookCount - examples.length} more`}
        </p>
      )}
    </section>
  );
}


// The edition story in the reader's language.
//
// The underlying states are precise but unreadable ("page_basis:
// ol_work_median"). A person standing in a bookshop needs to know one thing:
// can I trust these numbers for the copy in my hand?
function plainEdition(evidence) {
  const confirmed = (evidence || {}).identity === "isbn_confirmed";
  const basis = (evidence || {}).page_basis || "unknown";
  if (confirmed && basis === "isbn_edition") {
    return "This exact edition, confirmed from the ISBN you scanned.";
  }
  if (confirmed) {
    return "Right book — but the page details are typical across editions, not this printing.";
  }
  if (basis === "google_volume") {
    return "Edition not confirmed — details may differ from your copy.";
  }
  if (basis === "ol_work_median") {
    return "Edition not confirmed — page details vary between editions.";
  }
  return "Edition not confirmed — scan the barcode for an exact match.";
}

// Short qualifier beside the page count, so the number is never mistaken for a
// fact about the reader's own copy.
function plainPageNote(basis) {
  return {
    isbn_edition: "",
    google_volume: " (publisher’s edition)",
    ol_work_median: " (varies by edition)",
    catalogue_record: "",
    unknown: "",
  }[basis] || "";
}


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
  const [busy, setBusy] = useState(false);
  const [declined, setDeclined] = useState(false);
  const [error, setError] = useState("");

  // Said no. There is no second candidate to fall back to, so offering an empty
  // chooser would be a dead end -- send them to the things they can actually do.
  if (declined) {
    return <RefusalPanel result={{ ...result, failure_reason: "user_rejected_candidate" }}
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

          <ForYou forYou={candidate.for_you} />

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


function ResultCard({ result, onReset, onSearchByTitle, onRetry, loading, error, token,
                      onLibraryChanged, onResolved, scanImage }) {
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
    return <RefusalPanel result={result}
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
          <ForYou forYou={result.for_you} />

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

      <ReadingTools historyId={result.history_id} token={token}
                    onChanged={onLibraryChanged} />
      <MoreByAuthor author={book.author} excludeTitle={book.title} token={token} />
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
        <button className="btn-outline" onClick={onReset}>Scan another book</button>
      </div>
    </article>
  );
}

export { CandidateSelection, FallbackForm, MoreByAuthor, QuickOverview as AISummary, ResultCard };
