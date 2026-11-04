from datetime import date

import pytest

from emi_agent.dates import normalize

# TODAY = Tuesday 2026-11-03 (conftest)


@pytest.mark.parametrize("text,expected", [
    ("today", date(2026, 11, 3)),
    ("I'll pay aaj", date(2026, 11, 3)),
    ("tomorrow", date(2026, 11, 4)),
    ("kal kar dunga", date(2026, 11, 4)),
    ("कल", date(2026, 11, 4)),
    ("day after tomorrow", date(2026, 11, 5)),
    ("parso", date(2026, 11, 5)),
    ("parson tak", date(2026, 11, 5)),
    ("परसों", date(2026, 11, 5)),
])
def test_relative_words(text, expected, today):
    r = normalize(text, today)
    assert (r.date, r.ambiguous) == (expected, False)


@pytest.mark.parametrize("text,expected", [
    ("Friday", date(2026, 11, 6)),
    ("this Friday", date(2026, 11, 6)),
    ("by friday", date(2026, 11, 6)),
    ("shukravar ko", date(2026, 11, 6)),
    ("Monday", date(2026, 11, 9)),
    ("Tuesday", date(2026, 11, 10)),  # same weekday as today -> next week
    ("next Friday", date(2026, 11, 13)),  # evals.md S02 example
    ("agle shukravar", date(2026, 11, 13)),
    ("next Monday", date(2026, 11, 9)),
    ("sunday", date(2026, 11, 8)),
])
def test_weekdays(text, expected, today):
    r = normalize(text, today)
    assert (r.date, r.ambiguous) == (expected, False)


@pytest.mark.parametrize("text,expected", [
    ("on the 15th", date(2026, 11, 15)),
    ("15 tarikh", date(2026, 11, 15)),
    ("the 3rd", date(2026, 11, 3)),
    ("on the 1st", date(2026, 12, 1)),  # day already passed this month -> next month
    ("20 November", date(2026, 11, 20)),
    ("November 20", date(2026, 11, 20)),
    ("10th of December", date(2026, 12, 10)),
    ("5 jan", date(2027, 1, 5)),  # month already passed this year -> next year
])
def test_day_of_month(text, expected, today):
    r = normalize(text, today)
    assert (r.date, r.ambiguous) == (expected, False)


def test_last_day_of_month(today):
    assert normalize("this month's last day", today).date == date(2026, 11, 30)
    assert normalize("last day of the month", today).date == date(2026, 11, 30)


@pytest.mark.parametrize("text", [
    "month end",
    "end of the month",
    "mahine ke end mein",
    "when salary comes",
    "salary aane pe",
    "soon",
    "jaldi",
    "next week",
    "next month",
])
def test_ambiguous_phrases(text, today):
    r = normalize(text, today)
    assert r.ambiguous is True
    assert r.date is None


def test_ambiguous_wins_over_weekday(today):
    # "Friday, if the salary comes" is still conditional on salary.
    assert normalize("Friday if salary comes", today).ambiguous is True


def test_unrecognized(today):
    r = normalize("I don't know", today)
    assert (r.date, r.ambiguous, r.reason) == (None, True, "unrecognized")


def test_explicit_past_month_is_rejected(today):
    r = normalize("5 October", today)
    assert r.date is None and r.ambiguous and r.reason == "past_date"


def test_invalid_day(today):
    r = normalize("31 November", today)
    assert r.date is None and r.ambiguous and r.reason == "invalid_date"


def test_kal_inside_other_words_not_matched(today):
    # "kalpana" must not read as "kal".
    assert normalize("kalpana", today).reason == "unrecognized"


def test_iso_date_accepted(today):
    assert normalize("2026-11-13", today).date == date(2026, 11, 13)
    assert normalize("2026-10-01", today).reason == "past_date"


# Weekday + explicit date, and ordinal words (eval v6-lang-check S02 run1, 2026-11-04)
TODAY_TUE = date(2026, 11, 3)  # a Tuesday


@pytest.mark.parametrize("text, expected", [
    ("Friday 13 November", date(2026, 11, 13)),          # weekday and date agree: the date
    ("next Friday, the 13th", date(2026, 11, 13)),
    ("the sixth of November", date(2026, 11, 6)),
    ("November twenty-first", date(2026, 11, 21)),
    ("twenty first of November", date(2026, 11, 21)),
    ("on the 1st of December", date(2026, 12, 1)),
])
def test_explicit_date_wins_when_consistent(text, expected):
    assert normalize(text, TODAY_TUE).date == expected


@pytest.mark.parametrize("text", ["Friday the first of November", "Friday 14 November",
                                  "Monday, the 13th"])
def test_weekday_that_disagrees_with_the_date_is_a_conflict(text):
    r = normalize(text, TODAY_TUE)
    assert r.date is None and r.ambiguous and r.reason in ("conflict", "past_date")


def test_conflict_reason_when_both_are_in_the_future():
    assert normalize("Friday 14 November", TODAY_TUE).reason == "conflict"


def test_ordinal_word_without_a_month_is_not_a_date():
    assert normalize("ek second, main check karta hoon", TODAY_TUE).date is None
    assert normalize("the second one", TODAY_TUE).date is None


@pytest.mark.parametrize("text", ["मैं after 1 week करूंगी", "after a week", "ek hafte baad", "एक हफ्ते बाद"])
def test_after_a_week_is_vague(text):
    # Owner call B004 17:59: ask which day, never guess.
    assert normalize(text, date(2026, 11, 4)).reason == "ambiguous_phrase"
