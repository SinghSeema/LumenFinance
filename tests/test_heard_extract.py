"""Code-driven verification (D-25): pull a last-4 or a date of birth out of the borrower's words."""
from datetime import date

import pytest

from emi_agent.heard import find_dob, find_last4, number_tokens, partial_digits

TODAY = date(2026, 11, 3)


@pytest.mark.parametrize("text, want", [
    ("7743", "7743"),
    ("7 7 4 3", "7743"),
    ("it's 77 43", "7743"),
    ("saat saat chaar teen", "7743"),
    ("last four are seven seven four three", "7743"),
    ("double seven four three", "7743"),
    ("७७४३", "7743"),
    ("I don't remember", None),
    ("77", None),
    ("1234 or maybe 7743", None),  # two candidates: let the model ask
])
def test_find_last4(text, want):
    assert find_last4(text) == want


@pytest.mark.parametrize("texts, want, missing", [
    (["9 November 1985"], date(1985, 11, 9), set()),
    (["November 9th, 1985"], date(1985, 11, 9), set()),
    (["09/11/1985"], date(1985, 11, 9), set()),
    (["1985-11-09"], date(1985, 11, 9), set()),
    (["ninth November nineteen eighty five"], date(1985, 11, 9), set()),
    (["nau november unnis sau pachasi"], date(1985, 11, 9), set()),
    (["९ नवंबर १९८५"], date(1985, 11, 9), set()),
    (["Nineteen eighty five."], None, {"day", "month"}),
    (["1985"], None, {"day", "month"}),
    (["April"], None, {"day", "year"}),
    (["Nineteen eighty five.", "April 9"], date(1985, 4, 9), set()),
    (["no idea"], None, {"day", "month", "year"}),
    (["31 February 1985"], None, set()),  # not a real date
])
def test_find_dob(texts, want, missing):
    got, miss = find_dob(texts, TODAY)
    assert got == want
    assert miss == missing


@pytest.mark.parametrize("text, want", [
    ("April", True), ("9", True), ("saat", True), ("नवंबर", True),
    ("haan", False), ("okay", False), ("ji", False), ("hmm", False),
])
def test_has_number_or_month(text, want):
    from emi_agent.heard import has_number_or_month
    assert has_number_or_month(text) is want


@pytest.mark.parametrize("text", ["Register mobile number, double 7, 4 3.", "Double 7, 4 3.",
                                  "double seven four three", "7 7 4 3."])
def test_find_last4_double_digit_as_numeral(text):
    """Deepgram numerals=True writes 'double 7' (live call 2026-11-03)."""
    assert find_last4(text) == "7743"


def test_triple_numeral():
    assert number_tokens("triple 0 9") == ["0", "0", "0", "9"]


@pytest.mark.parametrize("text, partial", [
    ("It's double", True), ("7 4", True), ("7 4 3.", True), ("7 7 4 3.", False),
    ("Double 7, 4 3.", False), ("Haan ji.", False), ("last four digits", False),
])
def test_partial_digits(text, partial):
    assert partial_digits(text) is partial


def test_digits_said_across_two_consecutive_turns_only():
    from emi_agent.heard import digits_were_said
    assert digits_were_said("7743", ["It's double", "7 4 3."])
    assert not digits_were_said("7743", ["It's double", "haan ji", "4 3."])  # not consecutive
