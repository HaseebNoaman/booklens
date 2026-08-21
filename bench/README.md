# The identification benchmark

The number this project quotes comes from here, and nowhere else. Everything
needed to reproduce it is in this folder: the 100 cover photographs, the ground
truth, the harness, and the scorer.

```
python bench/run_images.py <label>      # ~29 min, opens every image for real
python bench/rescore.py <label>         # re-score saved results, seconds
python bench/analyse.py <label>_rescored  # sort the failures by cause
```

`run_images.py` opens each JPEG, runs PaddleOCR at both recogniser tiers, and
drives the same ladder `scan()` uses, including the chooser's de-duplication and
derived-edition filter. It calls the application's own helpers rather than
copying them — an earlier version kept its own copy of that logic, went stale
when `app.py` changed, and spent 34 minutes measuring code that was no longer
shipped. Nothing is written to the database.

## The result, 2026-08-21

`benchmark_2026_08_21.json` · 100 cover photographs · real OCR at both tiers ·
providers queried live · every disputed case decided by hand on title **and**
author.

| | covers |
|---|---|
| right book on the card | **74** |
| right work, another language's edition | 4 |
| **wrong book** | **10** |
| refused — said it was not sure | 12 |
| **precision when a card appeared** | **84%** |
| right book somewhere in what was offered | 77 |

Read the other way: **86 of 100 either offered the right book or admitted it did
not know. 10 misled.** That is the honest framing, because nothing here
auto-accepts — the reader confirms.

### The three wrong answers that share a shape

*The Shining* returned "Stephen King" by Bev Vincent. *Twilight* returned
"Stephenie Meyer" by Lisa Rondinelli Albert. *La Nuit* returned "Elie Wiesel" by
Lisa Moore. All three are **biographies of the author**, offered in place of the
book: the largest text on those covers is the author's name, OCR takes it for
the title, and the provider obliges. A title that is a person's name, written by
somebody else, is the signature — and it is the same family as the study-guide
problem, so the same filter should learn it.

## Why three different numbers exist

| number | what it measured |
|---|---|
| 69 / 2 / 29 @ 97% | `EVALUATION.md`, the build before OCR escalation |
| 80 / 3 / 17 @ 96% | after escalation shipped |
| this folder's result | today's build, today's provider responses, a stricter scorer |

They are **not** interchangeable. The build changed, the providers' answers
changed, and the scorer changed. Quote the one that matches the code you are
demonstrating, and say when it was measured.

## The scorer, and why it is not token_set_ratio

`token_set_ratio` returns 100 whenever one title's words are a subset of the
other, so **"The Alchemist Cocktail Book" scored a perfect match for "The
Alchemist"** — a different book entirely. Any number produced that way is
inflated by impostors.

`title_score()` uses `max(ratio, token_sort_ratio)` on the part of the title
before any colon: `ratio` forgives punctuation ("Slaughterhouse-Five" vs
"Slaughterhouse Five"), while `token_sort_ratio` punishes the *extra* words that
signal a different book. Measured on known-answer pairs, real matches land at
82–100 and impostors at 38–72, so the cut is 80 and **70–80 is reported as
`review` and read by hand**.

Judging is not fully automatic and should not be. Four kinds of near-miss need a
person:

- a genuinely different book that shares a title (*The Alchemist Cocktail Book*)
- a study guide or adaptation of the right book (*"Cormac McCarthy's The Road"*
  by Harold Bloom)
- the right work in another language (*El cuaderno de Noah*)
- the right book with a library-catalogue title string (*"Brave new world /by
  Aldous Huxley"*)

The saved `*_rescored.json` keeps every candidate list, so the hand decisions can
be re-checked rather than taken on trust.

## Known measurement bugs, already fixed

- `manifest.csv` quotes titles containing commas ("Thinking, fast and slow").
  Splitting on `,` turned the title into `"Thinking` and scored two correct
  answers as failures. Parsed with the `csv` module now.
- The harness carried its own copy of `scan()`'s ladder and drifted. It calls
  `app.py`'s functions directly now.

## What the failures are, when they happen

Roughly a sixth of the covers fail because **OCR never reads the title** — Dune,
Beloved, Verity, Wonder, Catch-22. No query strategy or ranking change reaches
those; only a better detector would. That is a limit worth stating, not hiding.
