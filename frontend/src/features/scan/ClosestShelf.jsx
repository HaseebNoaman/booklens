import React, { useEffect, useState } from "react";
import { authFetch, readJson } from "../../services/api.js";
import { BookCover } from "../../components/ui.jsx";

// "Then what here is close to what I like?"
//
// The refusal screen is the product working -- BookLens declines rather than
// guesses -- but it still ends with the reader holding nothing. The profile
// that answers "is this for you?" can answer this too: it is the same
// arithmetic pointed backwards, over our own 60 verified books.
//
// Every rule here exists to stop it becoming a recommender it has no right to
// be. See taste_profile.closest_from_shelf for the measurements.
//
//   - It is called "closest on our shelf", never "recommendations". There are
//     60 books, and the reader can see the whole shelf under Browse.
//   - Each book carries its REASON and the reader's own titles behind it, so
//     the claim can be checked. A number out of five could not be.
//   - Nothing is derived from any other account. 10 users and 23 history rows
//     cannot support collaborative filtering, and pretending otherwise would
//     be the one claim in this product with no measurement behind it.
//   - When there is nothing to say it renders NOTHING. An empty panel headed
//     "closest to what you have read" is worse than no panel: measured, 10% of
//     one-book profiles have no honest neighbour on this shelf.
export function ClosestShelf({ token, heading }) {
  const [books, setBooks] = useState([]);

  useEffect(() => {
    if (!token) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const response = await authFetch("/closest", token);
        const data = await readJson(response);
        if (!cancelled && response.ok) setBooks(data.books || []);
      } catch {
        // Silence is the right failure here: this section is a bonus on a
        // screen that already told the reader something true.
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  if (books.length === 0) return null;

  return (
    <section className="closest-shelf" aria-labelledby="closest-title">
      <h3 className="facts-heading" id="closest-title">
        {heading || "Closest to what you have read, from our shelf"}
      </h3>
      <div className="browse-grid closest-grid">
        {books.map((book) => (
          <article className="browse-card closest-card" key={book.id}>
            <BookCover src={book.thumbnail} fallback={book.thumbnail_fallback}
                       alt="" loading="lazy" />
            <span className="browse-card-title">{book.title}</span>
            <span className="browse-card-author">{book.author}</span>
            {/* The reason, in the reader's own books. Without this the section
                is an assertion; with it, it is a statement they can check. */}
            <span className="closest-reason">
              {book.reason} — you read {listOf(book.because)}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

function listOf(titles) {
  const names = titles || [];
  if (names.length <= 1) return names[0] || "";
  return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}
