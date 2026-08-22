# Choosing the books the catalogue keeps

The catalogue started as 250 records inherited from a dataset. Nobody had
checked whether a reader meeting one of them got a finished card, and measured,
many did not:

| checked | result |
|---|---|
| covers that actually load, asking Open Library for the **edition** we stored | **65%** (40 sampled) |
| of the failures, recoverable by asking for the cover **by ISBN** | **6 of 14** — including *The Da Vinci Code* |
| books carrying no subject specific enough to say anything about a reader | **50 of 250** |
| books whose stored summary describes the book's *structure* rather than its story | see below |

That last one is the clearest case. *To the Lighthouse* was shipping this as its
description:

> The second section opens with the Ramsays' summer home in the Hebrides… The
> third section closes with a large dinner party.

Three sentences that tell a reader nothing about the novel. The project's own
quality gate rejects it — it had simply never been pointed at the stored
summaries.

## The four scripts

```
python curate/audit_covers.py         # which cover route works, per book
python curate/audit_descriptions.py   # stored summary vs publisher text, same gate
python curate/audit_fame.py           # Open Library readers, per book
python curate/select.py               # decide, and run the acceptance gate
python curate/apply.py --confirm      # flip the status of everything not selected
```

The three audits are independent and network-bound; run them at the same time.
They write `cover_audit.json`, `description_audit.json` and `fame_audit.json`
next to this file. `select.py` reads all three — and runs without any of them,
applying only the bars it has data for, which is useful while an audit is still
going.

**Nothing is deleted.** `apply.py` moves a book's `verification_status` from
VERIFIED to NEEDS_REVIEW, and every reader-facing query already filters on
VERIFIED. `python curate/apply.py --restore --confirm` puts them all back.

## The shelf is 60, and the sort key is fame — after that was got wrong once

The first version of `select.py` filled by whichever **subject** was thinnest and
used readership only to break ties, on the reasoning that "Is this for you?" and
"Closest on our shelf" are built out of subjects, so the subjects must be
protected. It is a good argument and it produced a bad shelf.

Run against the real audits, it dropped **The 48 Laws of Power** — 51,033 Open
Library readers, the most-read book in the catalogue — because its subjects were
all shelf-wide and no subject needed it. It also dropped A Game of Thrones and
four Harry Potter books.

So the rule was inverted: **the most-read books that look finished**, with the 32
benchmark covers kept unconditionally. Measured at size 100, the reader-facing
numbers barely moved between the two strategies:

| strategy | subjects | nothing to offer, 2 books | one tap converts |
|---|---|---|---|
| thinnest-subject first | 63 | 0% | 95% |
| **most-read first** | 57 | 0% | 96% |
| the 250-book shelf | 83 | 0% | 98% |

A proxy that swings nine points while the thing it stands for does not move is
not a gate. The subject-count rule was removed from `select.py` and the two
measures a reader actually feels were kept.

### And why 60 rather than 50 or 100

The size was chosen by the same gate, not by taste. Over 400 sampled profiles at
each size:

| size | closest shelf empty, 1-book reader | one tap converts |
|---|---|---|
| 40 | 15% | 88% |
| 50 | 13% | 90% |
| **60** | **5%** | **96%** |
| 100 | 1% | 97% |

At 50, one new reader in eight opens an empty "closest on our shelf". 60 is the
smallest shelf where both features still work, and small enough that every book
on it was checked by hand.

<details>
<summary>The original argument, kept because it is still true about naive cuts</summary>

A cut that ignores subjects entirely does real damage — the numbers below are
from the 250-book shelf:

| | 250 | naive 100 | density-aware 100 |
|---|---|---|---|
| books that can be evidence at all | 200 | 77 | 96 |
| distinct subjects surviving | 83 | **50** | 84 |

A naive cut loses a third of the subjects. Filling by whichever subject is
currently thinnest keeps all of them, and the shelf ends up matching the
250-book one to within a point — because 50 of the 250 were contributing
nothing to either feature in the first place.

</details>

## The two rules that are not negotiable

**The 32 benchmark books stay.** Identification looks in the catalogue first, so
dropping one of them changes what `bench/` measures and the headline accuracy
figure stops describing the build it is quoted against. Re-measured after the
cut: **74 of 100 covers still identified, precision 84% → 85%.** The shrink cost
nothing, because 68 of those covers were never going through the catalogue.

**The acceptance gate has to pass.** `select.py` re-runs the numbers on whatever
it chose and refuses a shelf where a 1-book reader is left with nothing more
than 5% of the time, a 2-book reader is ever left with nothing, or one-tap
conversion falls below 90%. If a size fails, raise it — `--size 80` — rather
than shipping the smaller shelf.

## What the 60 look like now

Each book carries hand-written genres (`genres.json`), a description from a real
source with that source recorded, and a cover committed to this repository. A
catalogue card needs no network at all.

| | |
|---|---|
| books that can be evidence | **60 of 60** |
| reader left with nothing, profiles of 1 / 2 / 3 | **0% / 0% / 0%** |
| one tap converts the starter shelf | **100%** |
| descriptions written by a model | **0** |

Providers speak a different vocabulary from these labels, so
`taste_profile.SUBJECT_SYNONYMS` translates theirs into ours. Without it the
share of *scanned* books that share any subject with the shelf falls to 53%;
with it, 90%.

## Watch the denominator

`too_common_to_be_evidence` compares a subject's count against the number of
catalogue books **with genre text** — 238 of today's 250, not the 217 whose
genres survive normalisation. Using the wrong one moves the line: `fantasy` is
30.7% and counts as evidence at 238, and 33.6% and disqualified at 217.
`census()` in `select.py` matches `database.catalogue_subject_counts()` exactly,
and it must keep matching it.

Shrinking the shelf moves that denominator too, so `select.py` prints every
subject whose status changes. Read that list: a label that becomes "evidence"
starts appearing in "you have read N books tagged X", and this dataset applies
some labels loosely.
