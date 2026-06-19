"""Tests for the format detector and heuristic gate."""

import pytest

from citation_hop.detector import detect_format, is_likely_citation


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
