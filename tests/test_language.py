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
    # "Okay", "Yes", "Thank you", "After 4 PM" are said the same in a Hindi or an English call; they no
    # longer count ("Yes." then "After 4 PM?" flipped a Hindi call to English, replay of the 16:03 call).
    assert language_words("Okay.") == "" and language_words("After 4 PM?") == ""
    assert language_words("I am free after 4 PM") == "I free"


# C44: an explicit request for the number language (owner, 2026-11-04).
@pytest.mark.parametrize("said,style", [
    ("Amount English mein bataiye", "english"), ("अंग्रेज़ी में बोलिए", "english"),
    ("Please repeat the amount in English", "english"), ("हिंदी में बताइए", "hindi"), ("Hindi mein bolo", "hindi"),
    ("मैं English में नहीं समझती", None),          # a negation asks for the opposite: left to the model
    ("Hindi nahi, English mein", None), ("Hinglish mein", None), ("English medium school", None),
    ("I paid in cash", None)])
def test_asks_numbers_in(said, style):
    from emi_agent.language import asks_numbers_in
    assert asks_numbers_in(said) == style


# C46: Deepgram tagged every live turn "hi", English ones too (calls B004 19:51, B007 16:03).
@pytest.mark.parametrize("said,expected", [
    ("Can you please tell me the EMI amount in English?", "en"),
    ("Just wanted to share that I have lost my job this month.", "en"),
    ("Or is there any other way to handle?", "en"),
    ("theek hai", "hinglish"),                 # a Hindi word: the tag is believed
    ("haan main kal pay kar dunga", "hinglish"),
    ("जी payment हो जाएगी", "hi")])
def test_stt_hindi_tag_needs_a_hindi_word(said, expected):
    assert detect(said, "hi") == expected


def test_devanagari_turn_of_three_words_switches_at_once():
    t = LanguageTracker("en")
    assert t.update("hi", 2) is False and t.current == "en"   # "जी हाँ": too short alone
    t = LanguageTracker("en")
    assert t.update("hi", 3) is True and t.current == "hi"    # "जी भेज दीजिए"
    t = LanguageTracker("en")
    assert t.update("hinglish", 3) is False                   # Roman Hinglish keeps the two-turn rule


def test_live_call_replay_follows_the_borrower():
    """Call B004 19:51 turn by turn, as brain.py feeds the tracker (language_words, then detect)."""
    from emi_agent.language import language_words
    t = LanguageTracker("hi")
    seen = []
    for said in ["जी, मैं Sunita बोल रहा हूं.", "1, 2, 9 0.", "Can you please tell me the EMI amount in English?",
                 "जी भेज दीजिए.", "WhatsApp.", "जी payment हो जाएगी."]:
        content = language_words(said)
        if content == said.strip() or len(content.split()) >= 2:
            t.update(detect(content, "hi"), len(content.split()))
        seen.append(t.current)
    assert seen == ["hi", "hi", "en", "hi", "hi", "hi"]
