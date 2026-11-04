"""Language policy (LLD section 8, Q-08)."""
import pytest

from emi_agent.language import LanguageTracker, detect


@pytest.mark.parametrize("text,lang", [
    ("Yes, this is Arjun speaking.", "en"),
    ("हाँ, मैं बोल रहा हूँ", "hi"),
    ("haan ji, main Arjun bol raha hoon", "hinglish"),
    ("kal tak payment kar dunga", "hinglish"),
    ("ok", "en"),
])
def test_detect(text, lang):
    assert detect(text) == lang


def test_stt_hint_hindi_with_roman_text_is_hinglish():
    assert detect("theek hai", "hi") == "hinglish"


def test_single_english_word_does_not_flip():
    assert detect("haan, okay, main kal pay kar dunga") == "hinglish"


def test_switch_needs_two_consecutive_turns():
    t = LanguageTracker("en")
    assert t.update("hi") is False and t.current == "en"
    assert t.update("en") is False
    assert t.update("hi") is False
    assert t.update("hi") is True and t.current == "hi"


def test_hindi_family_switch_is_immediate():
    t = LanguageTracker("hi")
    assert t.update("hinglish") is True and t.current == "hinglish"
    assert t.update("hi") is True and t.current == "hi"


def test_history():
    t = LanguageTracker("en")
    t.update("hi"), t.update("hi")
    assert t.used == ["en", "hi"]


def test_full_sentence_in_the_other_language_switches_at_once():
    """S09 smoke 2026-11-03: the first Hindi sentence got an English reply."""
    t = LanguageTracker("en")
    assert t.update("hi", words=12) is True and t.current == "hi"


def test_short_turn_in_the_other_language_still_needs_two():
    t = LanguageTracker("hi")
    assert t.update("en", words=2) is False and t.current == "hi"  # "Thank you."
    assert t.update("en", words=1) is True and t.current == "en"


def test_devanagari_with_english_loanwords_is_hindi():
    # Was 'en' (letter ratio) until 2026-11-04; 7 words would have switched the call to English.
    assert detect("हाँ मैं pay कर दूँगा twenty fifth को") == "hi"
    assert detect("मेरी DOB 30 May 1991 है") == "hi"
    assert detect("Yes, my date of birth is 15 March 1988.") == "en"


def test_says_hindi_numbers():
    from emi_agent.language import says_hindi_numbers
    assert says_hindi_numbers("तीस May उन्नीस सौ इक्यानवे")
    assert says_hindi_numbers("pachchees tarikh ko")
    assert not says_hindi_numbers("ek second, do minute ruko")  # everyday words, not a number style
    assert not says_hindi_numbers("मैं 25 को pay कर दूँगा")


def test_garbled_number_answers_do_not_switch_language():
    """Live call 2026-11-04: 'Buys, July 19 94.' then 'Buys July' (STT for 'बाइस July') flipped a Hindi
    call to English. Numbers and month names say nothing about the language."""
    from emi_agent.language import language_words
    assert language_words("Buys, July 19 94.") == "Buys,"
    assert language_words("Buys July") == "Buys"
    assert language_words("मेरा जन्म तिथि बाइस July 19 94 है.").split() == ["मेरा", "जन्म", "तिथि", "है."]
    assert language_words("Okay.") == "Okay."  # nothing stripped: counts as before
