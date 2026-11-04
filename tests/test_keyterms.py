"""Deepgram keyterm sets (emi_agent/keyterms.py) and their wiring into the STT service."""
import pytest

from emi_agent import config, keyterms, providers


def test_none_sends_nothing_even_with_a_name():
    assert keyterms.for_call("none", "Priya") == []


def test_dates_has_the_misheard_words_and_the_name():
    terms = keyterms.for_call("dates", "Priya")
    assert {"बाईस", "उन्नीस सौ", "जुलाई", "Lumen Finance", "Priya"} <= set(terms)
    assert "दो" not in terms and "एक" not in terms  # everyday words only in "full"


def test_full_adds_hindi_0_to_10():
    assert {"दो", "एक", "सात"} <= set(keyterms.for_call("full"))


def test_unknown_set_fails_fast():
    with pytest.raises(ValueError, match="Unknown stt.keyterms"):
        keyterms.for_call("dats")


@pytest.mark.parametrize("name,expect", [("none", None), ("dates", "बाईस")])
def test_keyterms_reach_the_deepgram_query(name, expect):
    cfg = config.load(overrides=[f"stt.keyterms={name}"])
    cfg["stt"]["deepgram_api_key"] = "test"
    kw = providers.make_stt(cfg, "Priya")._build_connect_kwargs()
    if expect is None:
        assert "keyterm" not in kw
    else:
        assert isinstance(kw["keyterm"], list) and expect in kw["keyterm"] and "Priya" in kw["keyterm"]


def test_reference_lines_parse_correctly():
    """Every bake-off line scores correct on its own text, so a miss is the STT's, not the parser's."""
    from bakeoff.stt_keyterms import LINES, score
    bad = [(lid, text) for lid, kind, exp, text in LINES if not score(kind, exp, text)[0]]
    assert not bad
