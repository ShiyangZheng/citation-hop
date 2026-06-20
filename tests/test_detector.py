"""Tests for the format detector and heuristic gate."""

import pytest

from citation_hop.detector import (
    detect_format,
    detect_in_text_citation,
    is_in_text_citation,
    is_likely_citation,
)


# ---------------------------------------------------------------------------
# detect_format
# ---------------------------------------------------------------------------

BIBTEX = """
@article{smith2019,
  author = {Smith, John},
  title  = {An important paper},
  year   = {2019},
  doi    = {10.1038/nature12373},
}
""".strip()

RIS = """
TY  - JOUR
AU  - Smith, John
TI  - An important paper
PY  - 2019
DO  - 10.1038/nature12373
ER  -
""".strip()

APA = (
    "Smith, J. (2019). An important paper on things. "
    "Journal of Important Things, 12(3), 45-67. "
    "https://doi.org/10.1038/nature12373"
)


def test_detect_bibtex():
    assert detect_format(BIBTEX) == "bibtex"


def test_detect_ris():
    assert detect_format(RIS) == "ris"


def test_detect_plain_apa():
    assert detect_format(APA) == "plain"


def test_detect_empty():
    assert detect_format("") is None


# ---------------------------------------------------------------------------
# is_likely_citation
# ---------------------------------------------------------------------------


def test_apa_passes_gate():
    assert is_likely_citation(APA)


def test_short_text_fails():
    assert not is_likely_citation("Smith 2019")


def test_long_but_no_signals_fails():
    # > 2000 chars but no citation signal
    assert not is_likely_citation("a" * 2500)


def test_random_paragraph_fails():
    assert not is_likely_citation(
        "The quick brown fox jumps over the lazy dog. "
        "This sentence has no citation signal at all."
    )


def test_bibtex_passes_gate():
    assert is_likely_citation(BIBTEX)


def test_ris_passes_gate():
    assert is_likely_citation(RIS)


def test_sinclair_1987_passes_gate():
    """Regression: ``Sinclair, John McH.`` (multi-letter given name, no
    period after "McH") used to fail the heuristic gate because the
    surname-initial regex required a trailing "." after a single capital
    letter.  The relaxed regex must accept this real-world APA reference.
    """
    text = (
        "Sinclair, John McH. 'Collocation: A Progress Report'. In "
        "Language Topics: Essays in Honor of Michael Halliday, vol. 2. 1987."
    )
    assert is_likely_citation(text)
    assert detect_format(text) == "plain"


# ---------------------------------------------------------------------------
# In-text citation detector
# ---------------------------------------------------------------------------
#
# In-text citations are SHORT (8-50 chars typically) and have a clear
# author + year shape.  They fail the full-reference gate (length < 40),
# so they need their own detector.


@pytest.mark.parametrize(
    "text, want_author, want_year",
    [
        ("(Smith, 2020)",             "Smith",             "2020"),
        ("(Smith & Jones, 2020)",     "Smith & Jones",     "2020"),
        ("(Smith et al., 2020)",      "Smith et al.",      "2020"),
        ("(Heidari, 2025)",           "Heidari",           "2025"),
        ("Smith (2020)",              "Smith",             "2020"),
        ("Smith et al. (2020)",       "Smith et al.",      "2020"),
        ("Smith and Jones (2020)",    "Smith & Jones",     "2020"),
        ("(Smith, 2020a)",            "Smith",             "2020a"),
        ("(Smith, 2020b)",            "Smith",             "2020b"),
        ("(Libben & Titone, 2008)",   "Libben & Titone",   "2008"),
    ],
)
def test_detect_in_text_citation_positive(text, want_author, want_year):
    got = detect_in_text_citation(text)
    assert got is not None, f"no match for {text!r}"
    assert got["kind"] == "author_year"
    assert got["author"] == want_author
    assert got["year"] == want_year
    assert is_in_text_citation(text)


@pytest.mark.parametrize(
    "text",
    [
        "(2025)",                              # no author
        "",                                    # empty
        "The Smith (2020) study",              # trailing prose
        "Recent work (Smith, 2020) found",     # embedded in prose
        "(2020)",                              # year only
        "Smith 2020",                          # missing parens
        "a" * 200,                             # too long
    ],
)
def test_detect_in_text_citation_negative(text):
    assert detect_in_text_citation(text) is None
    assert not is_in_text_citation(text)
