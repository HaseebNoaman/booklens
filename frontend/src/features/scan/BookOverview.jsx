import React, { useEffect, useMemo, useState } from "react";
import { Icon } from "../../components/ui.jsx";
import { authFetch, readJson } from "../../services/api.js";

const ENGLISH_MARKERS = new Set([
  "a", "and", "are", "as", "at", "be", "book", "but", "by", "for", "from",
  "has", "have", "her", "his", "in", "is", "it", "of", "on", "she", "story",
  "that", "the", "their", "they", "this", "to", "was", "when", "who", "with",
]);

const FOREIGN_MARKERS = {
  Indonesian: new Set(["akan", "buku", "dan", "dari", "dengan", "ini", "ketika", "lebih", "tidak", "untuk", "yang"]),
  Dutch: new Set(["als", "boek", "dat", "de", "een", "en", "haar", "het", "hun", "naar", "over", "van", "voor", "ze"]),
  Swedish: new Set(["är", "att", "boken", "den", "det", "för", "från", "har", "inte", "med", "när", "och", "som", "till"]),
};

function cleanDisplayText(value) {
  const input = String(value || "");
  if (!input) return "";
  if (typeof DOMParser === "undefined") {
    return input.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  }
  const documentValue = new DOMParser().parseFromString(input, "text/html");
  return (documentValue.body.textContent || "")
    .replace(/,?\s*(?:the\s+)?#?\d*\s*(?:(?:new york times|sunday times|usa today|international)\s+)?bestselling author(?:\s+of\s+[^.!?]+)?/gi, "")
    .replace(/(?:\s*#[\w-]+){2,}\s*$/u, "")
    .replace(/\s+/g, " ").trim();
}

function detectedLanguage(value) {
  const tokens = cleanDisplayText(value).toLowerCase().match(/[a-zà-ÿ']+/g) || [];
  if (tokens.length < 6) return "Unknown";
  const english = tokens.filter((word) => ENGLISH_MARKERS.has(word)).length;
  let foreignName = "Unknown";
  let foreignScore = 0;
  Object.entries(FOREIGN_MARKERS).forEach(([name, words]) => {
    const score = tokens.filter((word) => words.has(word)).length;
    if (score > foreignScore) {
      foreignName = name;
      foreignScore = score;
    }
  });
  if (foreignScore >= 2 && foreignScore > english) return foreignName;
  if (english >= 2 && english >= foreignScore) return "English";
  return "Unknown";
}

function externalProvider(book) {
  const overviewSource = book.description_source || "";
  // The identified edition and the overview text may come from different
  // exact-ID providers. Label the text source first; metadata IDs are fallback.
  if (overviewSource.startsWith("google")) return "Google Books";
  if (overviewSource.startsWith("openlibrary")) return "Open Library";
  if (book.google_books_id) return "Google Books";
  if (book.open_library_edition_id || book.open_library_work_id) return "Open Library";
  return "another source";
}

function sourceLabel(book) {
  // What a reader wants from this badge is "can I trust it, and who said so".
  // It used to read "Machine-matched catalogue source" and "External metadata ·
  // Google Books" -- the internal tiering vocabulary, on the card.
  return book.catalogue_id ? "Checked by BookLens"
                           : `From ${externalProvider(book)}`;
}

function providerRecordUrl(book) {
  // Catalogue overview text comes from the stored catalogue source, not from
  // the external metadata record, so do not imply that an external URL is the
  // exact source of a catalogue summary.
  if (book.catalogue_id) return "";
  if (book.google_books_id) return `https://books.google.com/books?id=${encodeURIComponent(book.google_books_id)}`;
  const openLibraryId = book.open_library_edition_id || book.open_library_work_id;
  if (openLibraryId) {
    const cleanId = String(openLibraryId).replace(/^\//, "");
    return `https://openlibrary.org/${cleanId.startsWith("works/") ? cleanId : `books/${cleanId}`}`;
  }
  return "";
}

function overviewQuality(value, language, isCatalogue = false) {
  const text = cleanDisplayText(value);
  const words = text.match(/[A-Za-zÀ-ÿ0-9']+/g) || [];
  const sentences = text.match(/[.!?](?:\s|$)/g) || [];
  if (!text) return { usable: false, text: "", reason: "No grounded overview is stored for this record." };
  if (language !== "English" && language !== "Unknown") {
    return { usable: false, text, reason: `The exact source description is in ${language}, so BookLens did not present it as an English overview.` };
  }
  // Catalogue text is verified, so the only thing worth rejecting is a stub.
  //
  // This floor was 55 words with a 2-sentence minimum, which hid the summary
  // on 53 of the 237 catalogue books that have one -- 22% -- behind "the
  // available text is too short or incomplete". Dracula's verified summary is
  // 38 words and perfectly readable, and it was one of them.
  //
  // 25 words matches the lower bound the external path already uses, so both
  // routes now answer the same question the same way. Measured against the
  // real catalogue it hides 2 records instead of 53, and both deserve it: an
  // 11-word scene-setting fragment, and one that opens "In chapter 32".
  //
  // The external bound moved on 2026-08-23 from 25-65 words and at most 2
  // sentences, to 15-90 and no sentence ceiling. That pair of numbers was the
  // shape of a one-or-two-sentence WINDOW, and the backend stopped extracting
  // windows: it now shows the publisher's description with the sentences that
  // are not about the book removed. Measured over 190 books, the median such
  // description is 71 words across three or four sentences, so the old ceiling
  // would have blanked most of them a second time, on the client, after the
  // server had already decided they were fine. The floor moved for the same
  // reason -- The Clan of the Cave Bear's entire Open Library record is 18
  // words and is a complete answer.
  //
  // These two numbers must stay in step with MIN_WORDS and MAX_WORDS in
  // whatitsabout_heuristic.py. They are duplicated rather than served because
  // the card also renders text that never passed through that module.
  const invalidCatalogue = isCatalogue && (words.length < 25 || sentences.length < 1);
  const invalidExternal = !isCatalogue &&
    (words.length < 15 || words.length > 90 || sentences.length < 1);
  if (invalidCatalogue || invalidExternal || !/[.!?]$/.test(text)) {
    return { usable: false, text, reason: "The available text is too short or incomplete to present as a useful overview." };
  }
  return { usable: true, text, reason: "" };
}

// Which field holds the text the reader can inspect. Catalogue rows carry a
// verified summary; external rows carry the provider description. The card's
// "Preview description" button has to ask the same question this section does,
// or it hides itself while there is text sitting right below it.
function sourceTextFor(book, description) {
  return book.catalogue_id ? book.verified_summary : (description ?? book.description);
}

function sourceExcerpt(value, maximum = 1600) {
  const text = cleanDisplayText(value);
  if (text.length <= maximum) return { text, shortened: false };
  const cut = text.slice(0, maximum);
  const sentenceEnd = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
  return { text: `${cut.slice(0, sentenceEnd > 300 ? sentenceEnd + 1 : maximum).trim()}…`, shortened: true };
}

function unavailableMessage(book, status, qualityReason, hasSourceText = false) {
  if (qualityReason) return qualityReason;
  if (status === "model_unavailable") return "Overview temporarily unavailable because the configured summarizer could not run.";
  if (book.catalogue_id) return "A useful stored overview is not available for this catalogue record.";
  // Two different situations were being reported with one sentence, and the
  // sentence was false in the commoner of them. When the provider DID return a
  // description but no part of it passed the overview checks, saying "no
  // description was found" contradicts the publisher's text printed a few
  // centimetres below it -- which reads as a bug rather than as a decision.
  if (hasSourceText) {
    return "BookLens did not shorten this record's text into an overview — no part of it passed the quality checks. The publisher's own description is below.";
  }
  return "No reliable English source description was found for this exact record. Metadata is still available below.";
}

function QuickOverview({ book, token }) {
  const [summary, setSummary] = useState(book.ai_summary || "");
  const [status, setStatus] = useState(book.summary_status || "");
  const [description, setDescription] = useState(book.description || "");
  const [descriptionSource, setDescriptionSource] = useState(book.description_source || "");
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    setSummary(book.ai_summary || "");
    setStatus(book.summary_status || "");
    setDescription(book.description || "");
    setDescriptionSource(book.description_source || "");
    setTimedOut(false);
    if ((book.ai_summary || "").trim() || !book.id ||
        ["unavailable", "model_unavailable"].includes(book.summary_status)) return undefined;

    let stopped = false;
    let timer;
    let attempts = 0;
    async function poll() {
      attempts += 1;
      try {
        const response = await authFetch(`/books/${book.id}/summary`, token);
        const data = await readJson(response);
        if (stopped) return;
        if (data.description) setDescription(data.description);
        if (data.description_source) setDescriptionSource(data.description_source);
        if (data.status === "ready" && data.summary) {
          setSummary(data.summary);
          setStatus("ready");
          return;
        }
        if (["unavailable", "model_unavailable"].includes(data.status)) {
          setStatus(data.status);
          return;
        }
      } catch {
        // Metadata remains usable if one status poll fails.
      }
      if (!stopped && attempts < 20) timer = window.setTimeout(poll, 3000);
      else if (!stopped) setTimedOut(true);
    }
    timer = window.setTimeout(poll, 2500);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [book.id, book.ai_summary, book.summary_status, token]);

  const sourceText = sourceTextFor(book, description);
  const sourceLanguage = detectedLanguage(sourceText || summary);
  const quality = useMemo(
    () => overviewQuality(summary, detectedLanguage(summary || sourceText), Boolean(book.catalogue_id)),
    [summary, sourceText, book.catalogue_id],
  );
  const excerpt = useMemo(() => sourceExcerpt(sourceText), [sourceText]);
  const unavailable = ["unavailable", "model_unavailable"].includes(status);
  const recordUrl = providerRecordUrl({ ...book, description_source: descriptionSource });

  return (
    <section className="overview-section" aria-labelledby="quick-overview-title">
      <div className="overview-heading">
        <h3 id="quick-overview-title">What it’s about</h3>
      </div>
      <div className={`summary-box ${quality.usable ? "" : "summary-safe-state"}`} aria-live="polite">
        {quality.usable && <p>{quality.text}</p>}
        {!quality.usable && summary && <p>{unavailableMessage(book, status, quality.reason, Boolean(excerpt.text))}</p>}
        {!summary && unavailable && <p>{unavailableMessage(book, status, "", Boolean(excerpt.text))}</p>}
        {!summary && !unavailable && !timedOut && <p className="summary-pending">Checking the exact source and preparing an English overview…</p>}
        {!summary && !unavailable && timedOut && <p>The overview is taking longer than expected. The identification and edition metadata remain available.</p>}
      </div>
      <div className="source-meta-row">
        {sourceLanguage && sourceLanguage !== "English" && (
          <span>Written in <b>{sourceLanguage}</b></span>
        )}
        {recordUrl && <a href={recordUrl} target="_blank" rel="noreferrer">View identified edition record</a>}
      </div>
      {excerpt.text && (
        <details className="source-details" id="source-details">
          <summary>
            <span>Publisher&rsquo;s description</span>
            <small>May contain spoilers</small>
          </summary>
          <p className="desc-text">{excerpt.text}</p>
          {excerpt.shortened && <p className="source-truncated">Long source shortened in this view.</p>}
        </details>
      )}
    </section>
  );
}

const READING_OPTIONS = [
  // "Identified" is the state a book lands in when it is scanned. It is not a
  // reading status anyone would pick, so it is labelled for what it means to a
  // reader rather than for what the database calls it.
  ["identified", "Not started"],
  ["want_to_read", "Want to read"],
  ["reading", "Reading"],
  ["finished", "Finished"],
];

// Favourites are owned entirely by the card's action row, and the private-note
// field went on 2026-08-23: across 41 history rows it had collected 0 notes,
// while the reading status sitting beside it had been used 15 times. It was one
// more control competing for attention with the one people actually use.
//
// The column and the route stay -- the data would be the reader's. The PATCH
// simply omits the key now, which the route reads as "leave the note alone"
// rather than as an empty note.
function ReadingTools({ historyId, token, initialStatus = "identified",
                        onChanged }) {
  const [readingStatus, setReadingStatus] = useState(initialStatus || "identified");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  if (!historyId) return null;

  async function save(nextStatus = readingStatus) {
    setSaving(true);
    setMessage("");
    try {
      const response = await authFetch(`/history/${historyId}/reading`, token, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reading_status: nextStatus }),
      });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || "Could not save your reading details.");
      setReadingStatus(data.reading_status);
      setMessage("Saved to your private library.");
      onChanged?.(data);
    } catch (error) {
      setMessage(error.message || "Could not reach the server.");
    } finally {
      setSaving(false);
    }
  }


  return (
    <section className="reading-tools" aria-labelledby={`reading-tools-${historyId}`}>
      <div className="reading-tools-heading">
        <h3 id={`reading-tools-${historyId}`}>Keep this book</h3>
        <span className="saved-indicator"><Icon name="book" size={15} /> Saved</span>
      </div>
      <div className="reading-controls">
        <div>
          <label htmlFor={`reading-status-${historyId}`}>Reading status</label>
          <select id={`reading-status-${historyId}`} value={readingStatus} disabled={saving}
                  onChange={(event) => { setReadingStatus(event.target.value); save(event.target.value); }}>
            {READING_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </div>
      </div>
      {message && <p className="reading-message" role="status">{message}</p>}
    </section>
  );
}

export {
  QuickOverview,
  ReadingTools,
  providerRecordUrl,
  sourceLabel,
  sourceTextFor,
};
