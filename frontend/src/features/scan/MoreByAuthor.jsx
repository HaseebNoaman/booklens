/* Other books by the same author, fetched only when asked for.
   Deliberately a button rather than an automatic section: it costs a
   provider round-trip, and most readers are done once they have their
   answer.
*/
import React, { useState } from "react";
import { BookCover, Icon } from "../../components/ui.jsx";
import { authFetch, readJson } from "../../services/api.js";

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
export { MoreByAuthor };
