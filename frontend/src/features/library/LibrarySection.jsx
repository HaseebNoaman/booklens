import React, { useEffect, useState } from "react";
import { Icon, ModalShell } from "../../components/ui.jsx";
import { authFetch } from "../../services/api.js";
import { BookCover } from "../../components/ui.jsx";
import { QuickOverview, ReadingTools } from "../scan/BookOverview.jsx";

const STATUS_LABELS = {
  identified: "Identified",
  want_to_read: "Want to read",
  reading: "Reading",
  finished: "Finished",
};

    function LibrarySection({ token, version }) {
      const [history, setHistory] = useState([]);
      const [loaded, setLoaded] = useState(false);
      // filter: "all" shows every scan, "favorites" only the starred ones
      const [filter, setFilter] = useState("all");
      // the item currently open in the details popup (null = closed)
      const [selected, setSelected] = useState(null);
      // Something went wrong talking to the server. This used to be swallowed,
      // which was the worst bug in this section: a failed /history load left
      // the list empty and the empty-state then told a user with fifty books
      // "No scans yet â€” try your first book above." Silence is not an option
      // here; the user must be able to tell "you have nothing" apart from
      // "we could not reach the server".
      const [error, setError] = useState("");
      // Did the LOAD specifically fail? Kept separate from `error` so the
      // empty-state can be suppressed without suppressing it for, say, a
      // failed star toggle.
      const [loadFailed, setLoadFailed] = useState(false);

      // Reload whenever "version" changes (i.e. after every new scan).
      useEffect(() => {
        async function load() {
          try {
            const res = await authFetch("/history", token);
            const data = await res.json();
            if (res.ok) {
              setHistory(data);
              setLoadFailed(false);
              setError("");
            } else {
              setLoadFailed(true);
              setError(data.error || "Could not load your library.");
            }
          } catch (e) {
            setLoadFailed(true);
            setError("Could not reach the server. Your library is still safe — try again in a moment.");
          }
          setLoaded(true);
        }
        load();
      }, [version]);

      async function toggleStar(item, e) {
        e.stopPropagation();   // don't also open the details popup
        try {
          const res = await authFetch("/history/" + item.history_id + "/favorite",
                                      token, { method: "POST" });
          const data = await res.json();
          if (res.ok) {
            // Update just this one card with the value the server returned.
            setHistory(history.map(h =>
              h.history_id === item.history_id
                ? { ...h, is_favorite: data.is_favorite } : h));
            setError("");
          } else {
            setError(data.error || "Could not update that favorite.");
          }
        } catch (e) {
          // Previously silent: the star simply refused to move with no reason.
          setError("Could not reach the server — that favorite was not saved.");
        }
      }

      async function removeItem(item, e) {
        e.stopPropagation();
        if (!window.confirm('Remove "' + item.title + '" from your library?')) return;
        try {
          const res = await authFetch("/history/" + item.history_id,
                                      token, { method: "DELETE" });
          if (res.ok) {
            setHistory(history.filter(h => h.history_id !== item.history_id));
            setError("");
          } else {
            const data = await res.json().catch(() => ({}));
            setError(data.error || "Could not remove that book.");
          }
        } catch (e) {
          // Previously silent: the card just stayed there looking undeleted.
          setError("Could not reach the server — that book was not removed.");
        }
      }

      // Library filters use already-loaded private history fields; no extra
      // request or provider call is needed.
      const shown = history.filter(h => filter === "all" ||
        (filter === "favorites" && h.is_favorite) || h.reading_status === filter);

      function updateHistoryItem(historyId, changes) {
        setHistory((items) => items.map((item) =>
          item.history_id === historyId ? { ...item, ...changes } : item));
        setSelected((item) => item?.history_id === historyId ? { ...item, ...changes } : item);
      }

      return (
        <section className="section alt" id="library">
          <div className="container sec-center">
            <span className="eyebrow">Your library</span>
            <h2 className="sec-title">Recently identified</h2>
            <p className="sec-sub">Newest first. Save favorites, reopen details, or export your data.</p>
            <div className="lib-filter">
              <button className={"pill " + (filter === "all" ? "active" : "")}
                      onClick={() => setFilter("all")}>All Books</button>
              <button className={"pill " + (filter === "favorites" ? "active" : "")}
                      onClick={() => setFilter("favorites")}>Favorites</button>
              <button className={"pill " + (filter === "want_to_read" ? "active" : "")}
                      onClick={() => setFilter("want_to_read")}>Want to read</button>
              <button className={"pill " + (filter === "reading" ? "active" : "")}
                      onClick={() => setFilter("reading")}>Reading</button>
              <button className={"pill " + (filter === "finished" ? "active" : "")}
                      onClick={() => setFilter("finished")}>Finished</button>
              <button className="pill" onClick={async () => {
                // Download the library as CSV ("my data" export).
                try {
                  const res = await authFetch("/history/export", token);
                  if (!res.ok) {
                    // Previously `if (!res.ok) return;` â€” the button simply
                    // did nothing at all, with no feedback whatsoever.
                    setError("Could not export your library. Please try again.");
                    return;
                  }
                  const blob = await res.blob();
                  const a = document.createElement("a");
                  a.href = URL.createObjectURL(blob);
                  a.download = "booklens_library.csv";
                  a.click();
                  URL.revokeObjectURL(a.href);
                  setError("");
                } catch (e) {
                  setError("Could not reach the server — export failed.");
                }
              }}>Export CSV</button>
            </div>
            {error && <div className="error-msg">{error}</div>}
            {/* Only claim the library is empty when we actually KNOW it is.
                After a failed load we have no idea, so saying "No scans yet"
                would be a lie â€” the error message above covers that case. */}
            {loaded && !loadFailed && shown.length === 0 && (
              <div className="empty-note">
                {filter === "favorites"
                  ? "No favorites yet — select the star on any book."
                  : filter !== "all"
                    ? `No books marked "${STATUS_LABELS[filter]}" yet.`
                    : "No books yet — identify your first book above."}
              </div>
            )}
            <div className="lib-grid">
              {shown.map((item) => (
                // key uses history_id (unique per SCAN) â€” item.id is the
                // BOOK id and repeats when the same book is scanned twice.
                <article className="lib-item" key={item.history_id}>
                  <BookCover src={item.thumbnail} alt={"Cover of " + item.title} />
                  <button className="lib-main" onClick={() => setSelected(item)} aria-label={"Open details for " + item.title}>
                    <div className="t">{item.title}</div>
                    <div className="a">{item.author}</div>
                    <span className={`reading-status status-${item.reading_status || "identified"}`}>
                      {STATUS_LABELS[item.reading_status || "identified"]}
                    </span>
                    <div className="d">{item.scanned_at}</div>
                  </button>
                  <div className="lib-actions">
                    <button className={"icon-btn " + (item.is_favorite ? "starred" : "")}
                            onClick={(e) => toggleStar(item, e)}
                            title={item.is_favorite ? "Remove from favorites" : "Add to favorites"}
                            aria-label={item.is_favorite ? "Remove from favorites" : "Add to favorites"}>
                      <Icon name={item.is_favorite ? "starFill" : "star"} size={17} />
                    </button>
                    <button className="icon-btn danger"
                            onClick={(e) => removeItem(item, e)}
                            title="Remove from history" aria-label="Remove from history">
                      <Icon name="trash" size={16} />
                    </button>
                  </div>
                </article>
              ))}
            </div>
            {selected && (
              <BookDetailsModal item={selected} token={token}
                                onChanged={(changes) => updateHistoryItem(selected.history_id, changes)}
                                onClose={() => setSelected(null)} />
            )}
          </div>
        </section>
      );
    }

    // ==================== BOOK DETAILS POPUP ====================
    // Opens when a library card is clicked. The book's own fields all come
    // from the history response, so no extra request is needed for those â€”
    // but the AI summary is written asynchronously and may not exist yet,
    // which is why `token` is passed down for AISummary to poll with.
    function BookDetailsModal({ item, onClose, token, onChanged }) {
      return (
        <ModalShell onClose={onClose} labelledBy="book-details-title" wide>
            <div className="result-top library-detail-top">
              <BookCover src={item.thumbnail} alt={"Cover of " + item.title} />
              <div className="library-detail-copy">
                <h2 id="book-details-title" className="book-title library-detail-title">{item.title}</h2>
                <div className="book-author">by {item.author || "Unknown author"}</div>
                {item.published_date && <span className="tag">{item.published_date}</span>}
                {item.page_count > 0 && <span className="tag">{item.page_count} pages</span>}
                {item.page_count > 0 && (
                  <span className="tag">About {Math.max(1, Math.round(item.page_count / 40))}h at 40 pages/hour</span>
                )}
                {item.publisher && <span className="tag">{item.publisher}</span>}
                {(item.categories || "").split(",").filter(c => c.trim()).slice(0, 3).map(c => (
                  <span key={c} className="tag tag-genre">{c.trim()}</span>
                ))}
              </div>
            </div>
            {/* Reuses the same polling component as the scan result card.
                This used to be a static <p>: opening a book you had just
                scanned showed "Summary is still being written â€” check back in
                a minute" and then never updated, however long you waited,
                until the whole page was reloaded. AISummary shows the summary
                when it exists and otherwise polls /books/<id>/summary. */}
            <QuickOverview book={item} token={token} />
            <ReadingTools historyId={item.history_id} token={token}
                          initialStatus={item.reading_status}
                          initialNote={item.private_note}
                          initialFavorite={item.is_favorite}
                          onChanged={onChanged} />
            <p className="ocr-note">Scanned on {item.scanned_at}</p>
        </ModalShell>
      );
    }

export { LibrarySection };
