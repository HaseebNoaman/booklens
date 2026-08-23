/* The small pieces of a result card that state a fact.
   Two components that report something the app knows -- whether the reader
   has read this book, and what Open Library's readers say about it -- and
   three helpers that put the edition's provenance into plain words. None of
   them fetches anything; they render what the card was already given.
*/
import React from "react";
import { Icon } from "../../components/ui.jsx";

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
//
// AND WHEN THERE IS NOTHING, IT SAYS SO.
//
// This block used to return null in both of its empty cases -- no record at
// all, and a record carrying no rating and no shelf count -- so it simply
// vanished. A reader could not tell "nobody has rated this" from "we never
// looked", and every other empty state on this card explains itself. It is
// worded as what we did, not as a fact about the book: Open Library is one
// catalogue, and a book unrated there may be rated everywhere else.
const NOTHING_FOUND = (
  <div className="live-signals">
    <p className="live-row live-caveat">
      No reader rating found for this book on Open Library.
    </p>
  </div>
);

function LiveSignals({ live, exactLength }) {
  if (!live) return NOTHING_FOUND;
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

  if (!rows.length) return NOTHING_FOUND;
  return <div className="live-signals">{rows}</div>;
}


function MetadataItem({ label, value }) {
  if (!value && value !== 0) return null;
  return <div className="metadata-item"><dt>{label}</dt><dd>{value}</dd></div>;
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
export { AlreadyRead, LiveSignals, MetadataItem, plainEdition, plainPageNote };
