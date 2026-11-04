"""Numbers the borrower said: grounding for verify_identity and the guard's read-back rule."""
from datetime import date

import pytest

from emi_agent.heard import digits_were_said, dob_was_said, find_dob, number_tokens, said_numbers

DOB = date(1988, 3, 15)


@pytest.mark.parametrize("text, tokens", [
    ("it's 4821", ["4821"]),
    ("4 8 2 1", ["4", "8", "2", "1"]),
    ("४८२१", ["4821"]),
    ("four eight two one", ["4", "8", "2", "1"]),
    ("double four eight two", ["4", "4", "8", "2"]),
    ("four oh two one", ["4", "0", "2", "1"]),
    ("chaar aath do ek", ["4", "8", "2", "1"]),
    ("चार आठ दो एक", ["4", "8", "2", "1"]),
    ("nineteen eighty-eight", ["19", "88"]),
    ("nineteen eighty eight", ["19", "88"]),
    ("unnees sau atthaasi", ["1988"]),
    ("unnis sau athasi", ["1988"]),           # STT spelling variants
    ("nineteen hundred eighty eight", ["1988"]),
    ("उन्नीस सौ अट्ठासी", ["1988"]),
    ("pandrah march", ["15"]),
    ("the fifteenth of March", ["15"]),
    ("twenty first", ["21"]),
    ("15th", ["15"]),
    ("I am not sure", []),
])
def test_number_tokens(text, tokens):
    assert number_tokens(text) == tokens


def test_said_numbers_joins_neighbours_within_one_utterance_only():
    said = said_numbers(["it's 48 21", "and 99"])
    assert {"48", "21", "4821", "99"} <= said
    assert "2199" not in said and "8" not in said and "482" not in said


def test_digits_spoken_one_at_a_time_are_one_number():
    assert said_numbers(["4 8 2 1"]) == {"4821"}
    assert said_numbers(["chaar aath do ek"]) == {"4821"}
    assert "21" not in said_numbers(["four eight two one"])


@pytest.mark.parametrize("value, heard", [
    ("4821", ["it's 4821"]),
    ("4821", ["4 8 2 1"]),
    ("4821", ["48 21"]),
    ("4821", ["haan, chaar aath do ek"]),
    ("4821", ["it's four, eight, two, one"]),
    ("0123", ["zero one two three"]),
    ("4821", ["haan number le lo, 4 8 2 1"]),
    ("4821", ["number de do 4 8 2 1"]),     # Hindi "do" (give) next to the digits
    ("4821", ["ek minute... 4 8 2 1"]),     # Hindi "ek" (a) next to the digits
    ("4821", ["4 8 2 1, one moment"]),
])
def test_digits_were_said(value, heard):
    assert digits_were_said(value, heard)


@pytest.mark.parametrize("value, heard", [
    ("4821", []),
    ("4821", ["I don't remember"]),
    ("1234", ["yes this is Arjun"]),
    ("4821", ["it's 48"]),            # part of the value
    ("482", ["it's 4821"]),           # part of what was said
    ("4821", ["48", "21"]),           # split across two turns is not one answer
    ("?", ["it's 4821"]),
])
def test_digits_not_said(value, heard):
    assert not digits_were_said(value, heard)


@pytest.mark.parametrize("heard", [
    ["My date of birth is 15 March 1988."],
    ["15/03/1988"],
    ["15-3-88"],
    ["15031988"],
    ["March 15th, 1988"],
    ["the fifteenth of March nineteen eighty eight"],
    ["pandrah march unnees sau atthaasi"],
    ["पंद्रह मार्च उन्नीस सौ अट्ठासी"],
    ["15 March", "1988"],             # day and month first, year when asked
])
def test_dob_was_said(heard):
    assert dob_was_said(DOB, heard)


@pytest.mark.parametrize("heard", [
    [],
    ["yes, speaking"],
    ["15 March"],                     # no year
    ["March 1988"],                   # no day
    ["16 March 1988"],                # wrong day: the model must not "correct" it
    ["15 April 1988"],
])
def test_dob_not_said(heard):
    assert not dob_was_said(DOB, heard)


def test_baseline_s04_invented_dob_is_not_heard():
    """The model took '1990-05-15' from its own example sentence; the borrower never said it."""
    borrower_said = ["Haan, main Sunita Patel bol rahi hoon. Bataiye, kya kaam tha?",
                     "Theek hai, aap mujhse kya puchna chahti hain?",
                     "Achha, toh aap kya maang rahi hain?"]
    assert not dob_was_said(date(1990, 5, 15), borrower_said)


# Live call 2026-11-04 (B002): Deepgram wrote "बाइस" (short i); the table has "बाईस".
def test_dob_with_devanagari_spelling_variants():
    from datetime import date as _d
    T = _d(2026, 11, 4)
    want = _d(1994, 7, 22)
    assert find_dob(["मेरा जन्म तिथि बाइस July 19 94 है."], T)[0] == want
    assert find_dob(["बाइस July उन्नीस सौ चौरानवे."], T)[0] == want
    assert find_dob(["बाईस July उन्निस सौ चौरानबे"], T)[0] == want
    assert find_dob(["पच्चिस दिसंबर उन्नीस सौ अट्ठासी"], T)[0] == _d(1988, 12, 25)
    assert find_dob(["अट्ठाइस मई 1990"], T)[0] == _d(1990, 5, 28)


# Eval v8-already-paid S15 run2 (2026-11-04): correct details, three attempts charged by code.
from datetime import date as _date  # noqa: E402

from emi_agent.heard import find_last4  # noqa: E402

_T = _date(2026, 11, 3)
_PRIYA = _date(1994, 7, 22)


@pytest.mark.parametrize("said", [
    "जी, मेरी date of birth twenty-two July ninety-four है और mobile number के last four digits three three six seven हैं।",
    "जी, इक्कीस जुलाई... मतलब बाईस जुलाई nineteen ninety-four है, यानी twenty-two July 1994.",
    "ठीक है, mobile number के last four digits three three six seven हैं, और date of birth बाईस जुलाई उन्नीस सौ चौरानवे।",
    "मैंने तीन बार बता दिया है... date of birth twenty-two July 1994 और mobile के last digits three three six seven।",
    "Actually it is 22 July 1994",
    # Live call 2026-11-04: Deepgram ordinals and a split year.
    "22nd July 19 94.",
    "To 2nd... My date of birth is 2nd... 22nd July 19 94.",
    "July 22nd, 1994",
])
def test_dob_reads_the_day_next_to_the_month(said):
    assert find_dob([said], _T)[0] == _PRIYA


def test_two_different_dates_without_a_correction_word_are_not_guessed():
    got, missing = find_dob(["21 July, 22 July 1994"], _T)
    assert got is None and missing == set()


def test_last4_prefers_spoken_single_digits_over_joined_dob_parts():
    said = "मेरी date of birth twenty-two July ninety-four है और mobile number के last four digits three three six seven हैं।"
    assert find_last4(said) == "3367"


def test_danda_does_not_hide_a_number_word():
    assert find_dob(["बाईस जुलाई उन्नीस सौ चौरानवे।"], _T)[0] == _PRIYA


def test_misheard_day_is_missing_not_guessed():
    # Live call 2026-11-04: "बाईस July" came back as "Twice July". 19 is the year's first half.
    assert find_dob(["Date of birth, twice, July 19 94."], _T) == (None, {"day"})


@pytest.mark.parametrize("said,day", [("1st March 1988", 1), ("3rd March 1988", 3), ("4th March 1988", 4)])
def test_digit_ordinals(said, day):
    assert find_dob([said], _T)[0] == _date(1988, 3, day)


@pytest.mark.parametrize("said,year", [
    ("चौदह जून दो हज़ार एक", 2001),           # was read as 2002 ("June two" + "one")
    ("14 June two thousand one", 2001),
    ("14 June two thousand and five", 2005),
    ("fourteen June 2003", 2003),
])
def test_dob_with_a_2000s_year(said, year):
    assert find_dob([said], _T)[0] == _date(year, 6, 14)


def test_amount_in_thousands_is_one_number():
    assert "12500" in said_numbers(["बारह हज़ार पाँच सौ"])
    assert "12500" in said_numbers(["twelve thousand five hundred"])


def test_hundreds_with_an_unknown_word_is_not_the_year_1900():
    # STT bake-off 2026-11-04: "सतासी" (an unknown spelling of 87) left "उन्नीस सौ" = 1900.
    assert find_dob(["तेईस सितंबर उन्नीस सौ सतासी"], _T) == (None, {"year"})


# Live calls 2026-11-04: Deepgram multi wrote "बाईस" (22) as "Buys", "Bies", "Twice".
@pytest.mark.parametrize("said", ["Buys, July 19 94.", "Date of birth, buys, July 19 94.", "Bies July 1994",
                                  "bies, july nineteen ninety four"])
def test_misheard_22_before_a_month_is_22(said):
    assert find_dob([said], _T)[0] == _PRIYA
    assert dob_was_said(_PRIYA, [said])  # the tool's "was it said" check agrees


@pytest.mark.parametrize("said", ["Twice July 19 94.", "he buys things", "Bies.", "July buys 1994"])
def test_misheard_22_is_not_guessed_elsewhere(said):
    assert find_dob([said], _T)[0] is None
