import React, { useEffect, useState } from "react";
import { authFetch, readJson } from "../../services/api.js";
import { BookCover } from "../../components/ui.jsx";

// The way out of cold start, inside the card the reader is already looking at.
//
// "Is this for you?" answers from books the reader has engaged with, so a new
// account gets the honest but useless "nothing to compare this with yet". That
// state used to sit above a link to #browse -- which navigated AWAY from the
// result they had just photographed. The scan was thrown away to fix the scan.
//
// So the panel asks a question instead. One tap is enough: taste_profile sets
// MIN_PROFILE_BOOKS to 1 because it reports a checkable fact rather than
// predicting enjoyment, and a shelf demanding three would make the reader work
// three times harder than the claim needs.
//
// WHY THESE BOOKS. The server picks the ones that share a DISTINGUISHING
// subject with the book in hand, not six at random. That is choosing which
// question is worth asking -- the one whose answer changes what the card can
// say -- and it is not the same as choosing the answer: the shelf only ever
// asks whether the reader has read something, never assumes it, and a tap is
// never recorded as a "like". Measured before it was built: one tap turns cold
// start into a real answer for 196 of the 238 catalogue books that carry
// subjects.
//
// When the scanned book carries only shelf-wide labels (18% of the catalogue)
// the server says targeted=false and the copy stops promising an answer for
// THIS book. The shelf still appears, because the profile outlives the scan.
export function StarterShelf({ token, title, categories, bookId, onAnswered,
                              onMarked }) {
  const [books, setBooks] = useState([]);
  const [targeted, setTargeted] = useState(false);
  const [seen, setSeen] = useState([]);
  const [state, setState] = useState("loading");
  const [busy, setBusy] = useState(0);

  async function ask(body) {
    const response = await authFetch("/for-you", token, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, categories, book_id: bookId, ...body }),
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.error || "unavailable");
    return data;
  }

  function show(data) {
    setBooks(data.starters || []);
    setTargeted(Boolean(data.targeted));
    setState((data.starters || []).length ? "ready" : "exhausted");
  }

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    setSeen([]);
    ask({ want_starters: 6 })
      .then((data) => { if (!cancelled) show(data); })
      .catch(() => { if (!cancelled) setState("unavailable"); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, title, categories, bookId]);

  async function markRead(book) {
    setBusy(book.id);
    try {
      const response = await authFetch(`/catalogue/${book.id}/read`, token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // "finished" and not "want_to_read": the route accepts both, but only
        // finished/reading/favourite count towards the profile, so a
        // want-to-read tap would leave the panel visibly doing nothing.
        body: JSON.stringify({ status: "finished" }),
      });
      const marked = await readJson(response);
      if (!response.ok) throw new Error("failed");
      // Reported upward, not kept here: a tap that works unmounts this
      // component, and an undo living inside it would go with it.
      onMarked({ historyId: marked.history_id, title: book.title });

      // Re-ask with the reader's answer included. If it is now a real match
      // the parent replaces this whole panel; if not, the shelf stays and the
      // next tap can still land.
      const fresh = await ask({ want_starters: 6, exclude_ids: seen });
      onAnswered(fresh.for_you);
      show(fresh);
    } catch {
      setState("unavailable");
    } finally {
      setBusy(0);
    }
  }

  // "I have not read any of these" has to actually move. A book shares a
  // distinguishing subject with a median of 40 others, so there is nearly
  // always another six.
  async function showOthers() {
    const shown = seen.concat(books.map((b) => b.id));
    setBusy(-2);
    try {
      const fresh = await ask({ want_starters: 6, exclude_ids: shown });
      setSeen(shown);
      show(fresh);
    } catch {
      setState("unavailable");
    } finally {
      setBusy(0);
    }
  }

  if (state === "loading") {
    return <p className="for-you-empty" role="status">Finding books to compare with…</p>;
  }

  // Every failure here ends at the original way out rather than at nothing.
  if (state === "unavailable" || state === "exhausted") {
    return (
      <p className="for-you-empty">
        {state === "exhausted"
          ? "That is everything on our shelf worth asking about. "
          : "The shelf could not be loaded just now. "}
        <a href="#browse">Browse the verified books</a> and mark what you have read.
      </p>
    );
  }

  return (
    <div className="starter-shelf">
      <p className="for-you-empty">
        {targeted
          ? "Have you read any of these? One is enough for this section to answer."
          : "This book is tagged only with labels most of our shelf carries, so nothing here can answer it — but marking what you have read will answer the next book you scan."}
      </p>

      <div className="browse-grid starter-grid">
        {books.map((book) => (
          <button className="browse-card" key={book.id} type="button"
                  disabled={busy !== 0} onClick={() => markRead(book)}
                  aria-label={`I have read ${book.title} by ${book.author}`}>
            <BookCover src={book.thumbnail} fallback={book.thumbnail_fallback}
                       alt="" loading="lazy" />
            <span className="browse-card-title">{book.title}</span>
            <span className="browse-card-author">{book.author}</span>
            <span className="starter-cta">
              {busy === book.id ? "Saving…" : "I have read this"}
            </span>
          </button>
        ))}
      </div>

      <button className="starter-link" type="button" disabled={busy !== 0}
              onClick={showOthers}>
        {busy === -2 ? "Looking…" : "I have not read any of these"}
      </button>
    </div>
  );
}
