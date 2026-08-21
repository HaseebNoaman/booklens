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

## Why the fill order is "thinnest subject", not "most famous"

"Keep the hundred most famous" is the obvious rule and it is the wrong one.
"Is this for you?" and "Closest on our shelf" are built out of *subjects*, and
famous books cluster into a handful of them. Measured:

| | today, 250 | naive 100 | **selected 100** |
|---|---|---|---|
| books that can be evidence at all | 200 | 77 | **96** |
| distinct subjects surviving | 83 | **50** | **84** |
| nothing to offer, 1-book profile | 2% | 5% | **1%** |
| nothing to offer, 2-book profile | 0% | 1% | **0%** |
| one tap converts the starter shelf | 98% | — | **97%** |

A naive cut loses a third of the subjects. Filling by whichever subject is
currently thinnest keeps all of them, and the shelf ends up matching the
250-book one to within a point — because 50 of the 250 were contributing
nothing to either feature in the first place.

Fame is the **tie-break**: when several books would fill the same subject slot,
the one more Open Library readers have shelved wins.

## The two rules that are not negotiable

**The 32 benchmark books stay.** Identification looks in the catalogue first, so
dropping one of them changes what `bench/` measures and the headline accuracy
figure stops describing the build it is quoted against.

**The acceptance gate has to pass.** `select.py` re-runs the table above on
whatever it chose and refuses to recommend a shelf with fewer than 80 subjects,
any 2-book profile left with nothing, or one-tap conversion below 90%. If a size
fails, raise it — `--size 120` — rather than shipping the smaller shelf.

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
