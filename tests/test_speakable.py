"""Pre-TTS text check: each fix is applied and counted; clean sentences pass through unchanged."""
from emi_agent.speakable import prepare


def test_clean_sentences_pass_unchanged():
    for lang, s in [("hi", "आपकी personal loan की बारह हज़ार पाँच सौ रुपये की EMI पाँच नवंबर को due है।"),
                    ("en", "Your EMI of twelve thousand five hundred rupees is due on the fifth of November."),
                    ("hinglish", "मैं आपको SMS पर payment link भेज देती हूँ।")]:
        assert prepare(s, lang, "devanagari") == (s, [])


def test_url_and_emoji_are_never_spoken():
    text, hits = prepare("Use https://pay.lumenfinance.example/l/B003 to pay 🙂", "en", "devanagari")
    assert "http" not in text and "🙂" not in text and hits == ["url", "emoji"]


def test_last_four_read_back_is_digit_by_digit_in_the_number_style():
    assert prepare("मैंने 7743 सुना, क्या ये सही है?", "hi", "devanagari") == (
        "मैंने seven, seven, four, three सुना, क्या ये सही है?", ["digits"])  # default: English numbers
    assert prepare("मैंने 7743 सुना, क्या ये सही है?", "hi", "devanagari", "hindi") == (
        "मैंने सात, सात, चार, तीन सुना, क्या ये सही है?", ["digits"])
    assert prepare("I heard 7 7 4 3, is that right?", "en", "devanagari")[0] == (
        "I heard seven, seven, four, three, is that right?")


def test_year_amount_and_small_numbers():
    assert prepare("Your date of birth is 15 March 1988.", "en", "devanagari")[0] == (
        "Your date of birth is fifteen March nineteen eighty-eight.")
    assert prepare("आपकी EMI ₹12,500 है।", "hi", "devanagari")[0] == (
        "आपकी EMI twelve thousand five hundred rupees है।")
    assert prepare("आपकी EMI ₹12,500 है।", "hi", "devanagari", "hindi")[0] == (
        "आपकी EMI बारह हज़ार पाँच सौ रुपये है।")


def test_script_backstop_fixes_only_the_closed_word_list():
    text, hits = prepare("आपकी personal loan ki बारह हज़ार पाँच सौ rupaye की EMI paanch नवंबर ko due है।",
                         "hi", "devanagari")
    assert text == "आपकी personal loan की बारह हज़ार पाँच सौ रुपये की EMI पाँच नवंबर को due है।"
    assert hits == ["script_mix"]
    # Ambiguous words and English terms stay in Latin.
    assert prepare("मैं main point पर do बातें कहूँगी।", "hi", "devanagari")[1] == []
    # Pure romanised Hindi (no Devanagari) and the roman script setting are left alone.
    assert prepare("Aapki EMI kal due hai.", "hinglish", "devanagari")[1] == []
    assert prepare("आपकी EMI kal due hai.", "hi", "roman")[1] == []


def test_wrong_language_is_flagged_not_rewritten():
    s = "धन्यवाद, verify हो गया है।"
    assert prepare(s, "en", "devanagari") == (s, ["wrong_language"])
    assert prepare(s, "hi", "devanagari") == (s, [])


def test_hindi_fact_phrases_go_back_to_the_english_number_style():
    """v7/v8 evals: the model translated the tool's 'four thousand two hundred rupees' into Hindi in
    14 of 16 Hindi calls. With number style english, the known fact phrases are put back."""
    facts = {"चार हज़ार दो सौ रुपये": "four thousand two hundred rupees", "छह दिन": "six days"}
    text, hits = prepare("आपकी चार हजार दो सौ रुपये की EMI छह दिन से overdue है।", "hi", "devanagari",
                         "english", facts)
    assert text == "आपकी four thousand two hundred rupees की EMI six days से overdue है।"
    assert hits == ["number_style"]
