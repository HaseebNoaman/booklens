"""Marketing must not reach the card as an overview.

The gap that prompted this: _MARKETING_RE caught promotional ADJECTIVES
("stunning", "gripping") but not promotional VERBS, so this shipped verbatim
onto a result card:

    "The 10X Rule unveils the principle of 'Massive Action,' allowing you to
     blast through business cliches and risk-aversion while taking concrete
     steps to reach your dreams."

The risk in fixing it is over-reach: a rule broad enough to catch that sentence
can easily reject ordinary fiction ("she unlocks the door"), so the fiction
cases below matter as much as the marketing ones.
"""
import whatitsabout_heuristic as H


def overview(text, title="", categories=""):
    out = H.select_what_its_about([{"text": text, "source": "t",
                                    "verification": "exact"}],
                                  title=title, categories=categories)
    return (out.get("overview") or "").strip() if out.get("status") == "ready" else ""


# ----- what must be rejected -----

def test_promotional_verbs_are_rejected():
    assert H._PROMO_RE.search("unveils the principle of Massive Action")
    assert H._PROMO_RE.search("unlocks the secrets of success")
    assert H._PROMO_RE.search("allowing you to blast through business cliches")
    assert H._PROMO_RE.search("this will transform your career")


def test_a_predominantly_promotional_source_yields_no_overview():
    # Hunting for the least-bad sentence inside a sales pitch returns a quieter
    # sales pitch. Publishing nothing is the honest answer.
    pitch = ("The 10X Rule unveils the principle of Massive Action, allowing "
             "you to blast through business cliches and risk-aversion while "
             "taking concrete steps to reach your dreams. This is the level of "
             "action that guarantees companies and individuals realize their "
             "goals and dreams. It shows why people get stuck and how to move.")
    assert overview(pitch, title="The 10X Rule",
                    categories="Business & Economics") == ""


# ----- what must NOT be rejected -----

def test_ordinary_fiction_is_not_mistaken_for_marketing():
    # "unlocks" and "unveils" are perfectly normal verbs in a story. Only their
    # promotional collocations are listed.
    for sentence in ("She unlocks the door and walks out into the rain.",
                     "The sculptor unveils a statue in the town square.",
                     "The detective discovers a body in the library."):
        assert not H._PROMO_RE.search(sentence), sentence


def test_a_normal_description_still_produces_an_overview():
    story = ("Jonathan Harker, a newly qualified English solicitor, travels to "
             "Count Dracula's remote castle in the Carpathian Mountains. Harker "
             "soon discovers that he is a prisoner there and that his host is "
             "not what he seems.")
    assert overview(story, title="Dracula", categories="Horror")


def test_non_fiction_addressing_the_reader_is_still_allowed():
    # A blanket ban on "you" would delete non-fiction coverage entirely, so the
    # rule must let ordinary second-person explanation through.
    text = ("This book explains how you can measure the productivity of a "
            "small team without slowing it down. It draws on studies of "
            "software teams collected between 2015 and 2020 across Europe.")
    assert overview(text, title="Measuring Teams",
                    categories="Business & Economics")


def test_the_method_version_was_bumped():
    # Cached v1 overviews may contain the marketing this rule removes, so the
    # version must change for them to regenerate.
    assert H.METHOD == "candidate_window_heuristic_v2"
