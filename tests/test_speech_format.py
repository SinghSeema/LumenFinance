from datetime import date

import pytest

from emi_agent.speech_format import (
    amount_to_words, date_to_words, days_to_words, digits_to_words, number_to_words,
)


@pytest.mark.parametrize("n,words", [
    (0, "zero"),
    (7, "seven"),
    (15, "fifteen"),
    (21, "twenty-one"),
    (100, "one hundred"),
    (105, "one hundred five"),
    (999, "nine hundred ninety-nine"),
    (1000, "one thousand"),
    (2900, "two thousand nine hundred"),
    (12500, "twelve thousand five hundred"),
    (99999, "ninety-nine thousand nine hundred ninety-nine"),
    (100000, "one lakh"),
    (120000, "one lakh twenty thousand"),
    (1050000, "ten lakh fifty thousand"),
    (10000000, "one crore"),
    (12345678, "one crore twenty-three lakh forty-five thousand six hundred seventy-eight"),
])
def test_number_en_indian_grouping(n, words):
    assert number_to_words(n) == words


def test_amount_en():
    assert amount_to_words(12500, "en") == "twelve thousand five hundred rupees"
    assert amount_to_words(120000, "en") == "one lakh twenty thousand rupees"
    assert amount_to_words(1, "en") == "one rupee"


@pytest.mark.parametrize("amount,words", [
    (12499.49, "twelve thousand four hundred ninety-nine rupees"),
    (12499.5, "twelve thousand five hundred rupees"),
    (99999.5, "one lakh rupees"),
    (0.4, "zero rupees"),
])
def test_amount_rounding(amount, words):
    assert amount_to_words(amount, "en") == words


def test_amount_negative_rejected():
    with pytest.raises(ValueError):
        amount_to_words(-5, "en")


def test_amount_hindi_roman():
    assert amount_to_words(12500, "hi", "roman") == "barah hazaar paanch sau rupaye"
    assert amount_to_words(4200, "hi", "roman") == "chaar hazaar do sau rupaye"
    assert amount_to_words(120000, "hi", "roman") == "ek lakh bees hazaar rupaye"
    assert amount_to_words(1, "hi", "roman") == "ek rupaya"


def test_amount_hindi_devanagari():
    assert amount_to_words(12500, "hi", "devanagari") == "बारह हज़ार पाँच सौ रुपये"
    assert amount_to_words(18000, "hi", "devanagari") == "अठारह हज़ार रुपये"


def test_hinglish_uses_english_numbers():
    assert amount_to_words(3800, "hinglish") == "three thousand eight hundred rupees"


def test_hindi_irregular_numbers():
    assert number_to_words(99, "hi") == "ninyaanve"
    assert number_to_words(45, "hi") == "paintaalees"
    assert number_to_words(5600, "hi") == "paanch hazaar chhah sau"


def test_unknown_script_rejected():
    with pytest.raises(ValueError):
        amount_to_words(100, "hi", "klingon")


def test_date_relative(today):
    assert date_to_words(today, today, "en") == "today"
    assert date_to_words(date(2026, 11, 4), today, "en") == "tomorrow"
    assert date_to_words(date(2026, 11, 5), today, "en") == "the day after tomorrow"
    assert date_to_words(date(2026, 11, 4), today, "hi") == "kal"
    assert date_to_words(date(2026, 11, 5), today, "hi", "devanagari") == "परसों"


def test_date_absolute_en(today):
    assert date_to_words(date(2026, 11, 13), today, "en") == "the thirteenth of November"
    assert date_to_words(date(2026, 11, 21), today, "en") == "the twenty-first of November"
    assert date_to_words(date(2026, 10, 28), today, "en") == "the twenty-eighth of October"


def test_date_past_is_never_relative(today):
    # "kal" means both yesterday and tomorrow in Hindi; past dates get the full form.
    assert date_to_words(date(2026, 11, 2), today, "hi") == "do November"


def test_date_year_only_when_different(today):
    assert date_to_words(date(2027, 1, 5), today, "en") == "the fifth of January, twenty twenty-seven"
    assert date_to_words(date(2027, 1, 5), today, "hi") == "paanch January do hazaar sattaees"


def test_date_hindi(today):
    assert date_to_words(date(2026, 11, 13), today, "hi") == "terah November"
    assert date_to_words(date(2026, 11, 13), today, "hi", "devanagari") == "तेरह नवंबर"


def test_days():
    assert days_to_words(1, "en") == "one day"
    assert days_to_words(6, "en") == "six days"
    assert days_to_words(14, "hi") == "chaudah din"


def test_digits():
    assert digits_to_words("4821", "en") == "four, eight, two, one"
    assert digits_to_words("48 21", "hi") == "chaar, aath, do, ek"
    with pytest.raises(ValueError):
        digits_to_words("abc", "en")


# clean_for_tts: last step before TTS (D-21 backstop for markdown and invisible characters)
from emi_agent.speech_format import clean_for_tts


@pytest.mark.parametrize("raw, spoken", [
    ("**Namaste** Arjun ji.", "Namaste Arjun ji."),
    ("Aapki EMI **** due hai.", "Aapki EMI due hai."),
    ("Thik​ hai, kal‍ shaam.", "Thik hai, kal shaam."),
    ("# Payment\n- link bhej diya", "Payment link bhej diya"),
    ("`record_outcome` done", "record_outcome done"),
    ("   ", ""),
    ("आपकी EMI पाँच नवंबर को है।", "आपकी EMI पाँच नवंबर को है।"),  # Devanagari unchanged
])
def test_clean_for_tts(raw, spoken):
    assert clean_for_tts(raw) == spoken


# Number style (owner, 2026-11-04): Hindi sentence, English numbers by default
def test_number_lang_rule():
    from emi_agent.speech_format import number_lang
    assert number_lang("en", "hindi") == "en"
    assert number_lang("hi", "english") == "hi-en" and number_lang("hinglish", "english") == "hi-en"
    assert number_lang("hi", "hindi") == "hi" and number_lang("hinglish", "hindi") == "hi"


def test_hindi_sentence_english_numbers_forms():
    from datetime import date
    today = date(2026, 11, 3)
    assert amount_to_words(12500, "hi-en", "devanagari") == "twelve thousand five hundred rupees"
    assert date_to_words(date(2026, 11, 25), today, "hi-en", "devanagari") == "twenty-fifth November"
    assert date_to_words(date(2026, 11, 4), today, "hi-en", "devanagari") == "कल"
    assert date_to_words(date(2026, 11, 5), today, "hi-en", "roman") == "parson"
    assert date_to_words(date(2027, 1, 5), today, "hi-en", "roman") == "fifth January twenty twenty-seven"
    assert days_to_words(14, "hi-en", "devanagari") == "fourteen days"
