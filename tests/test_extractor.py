"""Tests for the DOI extractor."""

import pytest

from citation_hop.extractor import extract_doi


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Bare DOI
        ("See 10.1038/nature12373 for details.", "10.1038/nature12373"),
        # Trailing punctuation
        ("...as in 10.1038/nature12373.", "10.1038/nature12373"),
        ("...as in 10.1038/nature12373,", "10.1038/nature12373"),
        ("...as in 10.1038/nature12373).", "10.1038/nature12373"),
        # doi: prefix
        ("doi:10.1038/nature12373", "10.1038/nature12373"),
        ("DOI: 10.1038/nature12373", "10.1038/nature12373"),
        # URL form
        ("https://doi.org/10.1038/nature12373", "10.1038/nature12373"),
        ("http://dx.doi.org/10.1038/nature12373", "10.1038/nature12373"),
        # DOI with tricky suffix characters
        ("10.1000/(SICI)1097-0118(199601)14:1<23::AID-JEO450>3.0.CO;2-7",
         "10.1000/(SICI)1097-0118(199601)14:1<23::AID-JEO450>3.0.CO;2-7"),
        # DOI embedded mid-sentence
        ("Reference (Smith, 1999) https://doi.org/10.1234/abc-xyz end.",
         "10.1234/abc-xyz"),
    ],
)
def test_extract_doi_positive(raw, expected):
    assert extract_doi(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no doi here",
        "10.4/short",          # registrant too short
        "https://example.com", # looks like a URL but not a doi.org one
    ],
)
def test_extract_doi_negative(raw):
    assert extract_doi(raw) is None
