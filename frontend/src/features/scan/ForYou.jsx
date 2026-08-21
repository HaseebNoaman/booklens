/* "Is this for you?" -- the panel that answers from the reader's own books.
   Five states, each saying something different, plus the undo for a starter
   shelf tap. It owns that undo rather than the shelf, because a tap that
   works unmounts the shelf and the undo would vanish with it.
*/
import React, { useEffect, useState } from "react";
import { authFetch, readJson } from "../../services/api.js";
import { StarterShelf } from "./StarterShelf.jsx";
import { ClosestShelf } from "./ClosestShelf.jsx";

// "Is this for you?" -- evidence from the reader's own library.
//
// Four states, and each says something different. They must not be collapsed:
// a book with no subjects is the PUBLISHER's gap, while an empty profile is the
// reader's starting point, and telling a well-read user to "build your profile"
// because a publisher omitted subjects would be simply wrong.
//
// No score, no percentage, no verdict. The section shows what the reader has
// read and lets them draw the conclusion.
function ForYou({ forYou, token, title, categories, bookId, onAnswered }) {
  // The undo for a starter-shelf tap is owned HERE, not by the shelf.
  //
  // A tap writes to the reader's real library, and when it works the shelf
  // unmounts -- the section has become a real answer and no longer needs it.
  // An undo living inside the shelf would therefore disappear at exactly the
  // moment it was needed: after a tap that did something.
  const [undo, setUndo] = useState(null);
  const [undoing, setUndoing] = useState(false);
  useEffect(() => { setUndo(null); }, [title, categories, bookId]);

  async function undoLast() {
    if (!undo) return;
    setUndoing(true);
    try {
      await authFetch(`/history/${undo.historyId}`, token, { method: "DELETE" });
      const response = await authFetch("/for-you", token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, categories, book_id: bookId }),
      });
      const data = await readJson(response);
      if (response.ok) onAnswered(data.for_you);
      setUndo(null);
    } catch {
      // The book stays in the library and the reader can remove it there.
      // Better than a card that claims an undo it did not perform.
    } finally {
      setUndoing(false);
    }
  }

  if (!forYou) return null;
  const { state, subjects = [], examples = [], book_count: bookCount = 0 } = forYou;

  const tags = subjects.length > 0 ? (
    <div className="tag-row">
      {subjects.map((subject) => (
        <span className="tag tag-genre" key={subject}>{subject}</span>
      ))}
    </div>
  ) : null;

  const shelf = (
    <StarterShelf token={token} title={title} categories={categories}
                  bookId={bookId} onAnswered={onAnswered} onMarked={setUndo} />
  );

  function body() {
    if (state === "no_subject_data") {
      return (
        <p className="for-you-empty">
          Not enough subject data for this book to compare it with your library.
        </p>
      );
    }

    // Interests are a weaker signal than a book someone read, and the wording
    // says so. It must never read like "you have read N books".
    if (state === "interest_match") {
      return (
        <>
          {/* Names the interest instead of saying "an interest you chose". A
              reader with eight interests wants to know WHICH one this book
              hits; the vague version told them something they already knew. */}
          <p className="for-you-evidence">
            You picked {subjects.length === 1 ? subjects[0] : subjects.slice(0, -1).join(", ") + " and " + subjects[subjects.length - 1]} — this is one
          </p>
          {tags}
          {/* This used to end by telling the reader that books they had read
              would answer better, with no way to say what those were. */}
          {shelf}
        </>
      );
    }

    if (state === "cold_start") {
      return (
        <>
          <p className="for-you-empty">
            BookLens has nothing to compare this with yet. Tell it one book you
            have already read and it can answer.
          </p>
          {/* This used to be the end of the road twice over: first the reader
              was asked to save books with no way to do it, then with a link
              that navigated AWAY from the result they had just photographed.
              The question is asked here now, on the card, and one answer
              settles it -- see StarterShelf.jsx. */}
          {shelf}
          {tags}
        </>
      );
    }

    if (state === "no_match") {
      return (
        <>
          <p className="for-you-empty">
            None of the {bookCount} books in your library share these subjects.
          </p>
          {tags}
          {/* Told "not for you" and left there. The shelf turns a verdict into
              a next step, and it is the same evidence read backwards. */}
          <ClosestShelf token={token}
                        heading="Closer to what you have read, from our shelf" />
        </>
      );
    }

    return (
      <>
        <p className="for-you-evidence">
          You have read or saved {bookCount} {bookCount === 1 ? "book" : "books"} with these subjects
        </p>
        {tags}
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
      </>
    );
  }

  return (
    <section className="for-you" aria-labelledby="for-you-title">
      <h3 className="facts-heading" id="for-you-title">Is this for you?</h3>
      {undo && (
        <p className="starter-undo" role="status">
          Added <b>{undo.title}</b> to your library.{" "}
          <button className="starter-link" type="button" disabled={undoing}
                  onClick={undoLast}>{undoing ? "Removing…" : "Undo"}</button>
        </p>
      )}
      {body()}
    </section>
  );
}
export { ForYou };
