import React, { useEffect, useState } from "react";
import { authFetch, readJson } from "../../services/api.js";
import { BookCover, Icon } from "../../components/ui.jsx";

// Browse the verified books without holding one.
//
// The 250 verified records were reachable only by photographing a cover or
// typing an exact title, so the most trustworthy data in the product was
// invisible to the people it was built for. This is the way in.
//
// It answers the same question a scan does -- "is this for me?" -- against the
// reader's own library, and it writes nothing: looking at a book is not
// reading it.
function ForYouLine({ forYou }) {
  if (!forYou) return null;
  const { state, subjects = [], examples = [], book_count: count = 0 } = forYou;

  if (state === "match") {
    return (
      <div className="browse-foryou match">
        <p className="browse-foryou-line">
          You have read or saved {count} {count === 1 ? "book" : "books"} with these subjects
        </p>
        <div className="tag-row">
          {subjects.map((s) => <span className="tag tag-genre" key={s}>{s}</span>)}
        </div>
        {examples.length > 0 && (
          <p className="browse-foryou-examples">
            {examples.join(", ")}
            {count > examples.length && ` and ${count - examples.length} more`}
          </p>
        )}
      </div>
    );
  }
  if (state === "interest_match") {
    return (
      <div className="browse-foryou match">
        <p className="browse-foryou-line">Matches an interest you chose</p>
        <div className="tag-row">
          {subjects.map((s) => <span className="tag tag-genre" key={s}>{s}</span>)}
        </div>
      </div>
    );
  }
  if (state === "cold_start") {
    return (
      <p className="browse-foryou empty">
        Mark a book below as read and BookLens can start answering this.
      </p>
    );
  }
  if (state === "no_match") {
    return (
      <p className="browse-foryou empty">
        None of the {count} books in your library share these subjects.
      </p>
    );
  }
  return (
    <p className="browse-foryou empty">
      Not enough subject data for this book to compare it with your library.
    </p>
  );
}

export function BrowseSection({ token }) {
  const [query, setQuery] = useState("");
  const [books, setBooks] = useState([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [marking, setMarking] = useState(false);
  const [marked, setMarked] = useState(false);

  async function markRead(book) {
    setMarking(true);
    try {
      const response = await authFetch(`/catalogue/${book.id}/read`, token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "finished" }),
      });
      if (response.ok) {
        setMarked(true);
        // Re-read the evidence: one book is now enough for a real answer.
        const fresh = await authFetch(`/catalogue/${book.id}`, token);
        const data = await readJson(fresh);
        if (fresh.ok) setSelected((s) => ({ ...s, for_you: data.for_you }));
      }
    } finally {
      setMarking(false);
    }
  }

  useEffect(() => {
    if (!token) return undefined;
    let cancelled = false;
    // Debounced so typing does not fire a request per keystroke.
    const timer = setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const response = await authFetch(
          `/catalogue?q=${encodeURIComponent(query)}`, token);
        const data = await readJson(response);
        if (cancelled) return;
        if (!response.ok) throw new Error(data.error || "Could not load the catalogue.");
        setBooks(data.books || []);
        setTotal(data.total || 0);
      } catch (err) {
        if (!cancelled) setError(err.message || "Could not reach the server.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, query ? 300 : 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [token, query]);

  async function open(book) {
    setMarked(false);
    setSelected({ ...book, loading: true });
    try {
      const response = await authFetch(`/catalogue/${book.id}`, token);
      const data = await readJson(response);
      if (response.ok) setSelected({ ...data.book, for_you: data.for_you });
      else setSelected({ ...book, loading: false });
    } catch {
      setSelected({ ...book, loading: false });
    }
  }

  if (!token) return null;

  return (
    <section className="container section" id="browse" aria-labelledby="browse-title">
      <div className="section-intro">
        <span className="eyebrow">Verified books</span>
        <h2 className="sec-title" id="browse-title">Browse without scanning</h2>
        <p className="sec-sub">
          Every book here has been checked against its source. Search one, or see
          whether it matches what you already read.
        </p>
      </div>

      <div className="browse-search">
        <Icon name="search" size={16} />
        <input value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder="Search title, author, or ISBN"
               aria-label="Search the verified books" />
      </div>

      {error && <div className="error-msg" role="alert">{error}</div>}
      {loading && <p className="browse-status" role="status">Loading…</p>}
      {!loading && !error && (
        <p className="browse-status">
          {total === 0 ? "No verified book matches that."
                       : `${books.length} of ${total} verified books`}
        </p>
      )}

      <div className="browse-grid">
        {books.map((book) => (
          <button className="browse-card" key={book.id} type="button"
                  onClick={() => open(book)}>
            <BookCover src={book.thumbnail} fallback={book.thumbnail_fallback}
                       alt={`Cover of ${book.title}`} loading="lazy" />
            <span className="browse-card-title">{book.title}</span>
            <span className="browse-card-author">{book.author}</span>
          </button>
        ))}
      </div>

      {selected && (
        <div className="browse-detail" role="dialog" aria-modal="true"
             aria-label={selected.title}>
          <div className="browse-detail-inner">
            <button className="btn-outline browse-close" type="button"
                    onClick={() => setSelected(null)}>Close</button>
            <div className="browse-detail-layout">
              <BookCover className="result-cover" src={selected.thumbnail}
                         fallback={selected.thumbnail_fallback}
                         alt={`Cover of ${selected.title}`} />
              <div>
                <h3 className="book-title">{selected.title}</h3>
                <p className="book-author">by {selected.author || "Unknown author"}</p>
                {selected.loading
                  ? <p className="browse-status">Checking your library…</p>
                  : <ForYouLine forYou={selected.for_you} />}
                {/* The way out of cold start. Marking a book read is a
                    deliberate act, which is exactly the signal the profile
                    counts -- and it needs no camera. */}
                <div className="browse-detail-actions">
                  <button className="btn" type="button" disabled={marking}
                          onClick={() => markRead(selected)}>
                    {marked ? "Added to your library" :
                     marking ? "Saving…" : "I have read this"}
                  </button>
                </div>
                {selected.summary && <p className="desc-text">{selected.summary}</p>}
                <p className="browse-detail-facts">
                  {[selected.publisher, selected.published_date,
                    selected.isbn_13 && `ISBN ${selected.isbn_13}`]
                    .filter(Boolean).join(" · ")}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
