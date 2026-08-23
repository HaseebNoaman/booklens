import React, { useEffect, useState } from "react";
import { authFetch, readJson } from "../../services/api.js";
import { BookCover, Icon } from "../../components/ui.jsx";
import { ResultCard } from "../scan/ResultViews.jsx";

// Browse the verified books without holding one.
//
// The verified records were reachable only by photographing a cover or typing
// an exact title, so the most trustworthy data in the product was invisible to
// the people it was built for. This is the way in.
//
// IT SHOWS THE SAME CARD A SCAN DOES. This file used to carry a second,
// simplified card of its own -- a cut-down "Is this for you?", the stored
// overview as a bare paragraph, and a facts line -- so the 60 books this
// project actually vouches for showed LESS than a book it found on the
// internet: no reader rating, no edition line, no starter shelf, no library
// controls. /api/catalogue/<id> now answers with the confirm route's payload,
// field for field, and this renders ResultCard with it. The only difference
// left is where the data came from, which the card's source badge states.
//
// It still writes nothing by itself: looking at a book is not reading it. The
// reader has to say so, which is what the card's "I have read this" is for.
export function BrowseSection({ token, onLibraryChanged }) {
  const [query, setQuery] = useState("");
  const [books, setBooks] = useState([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [marking, setMarking] = useState(false);

  async function loadDetail(recordId) {
    const response = await authFetch(`/catalogue/${recordId}`, token);
    const data = await readJson(response);
    return response.ok ? data : null;
  }

  async function markRead(recordId) {
    setMarking(true);
    try {
      const response = await authFetch(`/catalogue/${recordId}/read`, token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "finished" }),
      });
      if (!response.ok) return;
      // Re-read the whole card rather than patching one field. Marking a book
      // read changes four answers at once -- already_read, the history id the
      // library controls hang off, is_favorite, and the taste evidence -- and
      // reading them back together is what keeps them from disagreeing.
      const fresh = await loadDetail(recordId);
      if (fresh) setSelected({ ...fresh, recordId });
      onLibraryChanged?.();
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
    // The grid row is enough to draw the cover and title immediately, so the
    // overlay opens on the book rather than on a spinner.
    setSelected({ pending: book, recordId: book.id });
    try {
      const data = await loadDetail(book.id);
      if (data) setSelected({ ...data, recordId: book.id });
      else setSelected(null);
    } catch {
      setSelected(null);
    }
  }

  if (!token) return null;

  const pending = selected?.pending;

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
             aria-label={(pending || selected.book || {}).title || "Book"}>
          <div className="browse-detail-inner">
            <button className="btn-outline browse-close" type="button"
                    onClick={() => setSelected(null)}>Close</button>
            {pending ? (
              <div className="browse-detail-loading">
                <BookCover className="result-cover" src={pending.thumbnail}
                           fallback={pending.thumbnail_fallback}
                           alt={`Cover of ${pending.title}`} />
                <div>
                  <h3 className="book-title">{pending.title}</h3>
                  <p className="book-author">by {pending.author || "Unknown author"}</p>
                  <p className="browse-status" role="status">Checking your library…</p>
                </div>
              </div>
            ) : (
              <ResultCard result={selected} token={token}
                          onReset={() => setSelected(null)}
                          resetLabel="Close"
                          onMarkRead={() => markRead(selected.recordId)}
                          markingRead={marking}
                          onLibraryChanged={async () => {
                            const fresh = await loadDetail(selected.recordId);
                            if (fresh) setSelected({ ...fresh, recordId: selected.recordId });
                            onLibraryChanged?.();
                          }} />
            )}
          </div>
        </div>
      )}
    </section>
  );
}
