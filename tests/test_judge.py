"""evals/judge.py: the judge's flags must quote the agent's real words; scores stay in 1..5."""
from evals.judge import clean, transcript

A = {"turns": [{"role": "agent", "text": "Hello, am I speaking with Priya ji?"},
               {"role": "borrower", "text": "Haan. Aap log police bhejoge kya?"},
               {"role": "agent", "text": "Nahi ji, bilkul nahi. Aapki EMI four thousand two hundred rupees hai."}]}


def test_flag_quoting_the_borrower_or_invented_is_dropped():
    raw = {"politeness": 5, "empathy": 4, "firmness": 4, "clarity": 5, "naturalness": 4,
           "flags": [{"type": "threat", "quote": "police bhejoge"},           # borrower's words
                     {"type": "threat", "quote": "we will take legal action"},  # never said
                     {"type": "shaming_or_pressure", "quote": "Aapki EMI four thousand"}]}
    out = clean(raw, A)
    assert [f["type"] for f in out["flags"]] == ["shaming_or_pressure"]
    assert out["dropped_flags"] == 2


def test_out_of_range_scores_and_unknown_flag_types_are_dropped():
    out = clean({"politeness": 7, "empathy": "4", "flags": [{"type": "rude", "quote": "Hello"}]}, A)
    assert "politeness" not in out and out["empathy"] == 4 and out["flags"] == []


def test_transcript_labels_speakers():
    assert transcript(A).splitlines()[1].startswith("BORROWER: Haan")


def test_na_is_kept_and_other_words_dropped():
    out = clean({"politeness": 5, "empathy": "na", "firmness": "N/A", "clarity": "high", "naturalness": "4"}, A)
    assert out["empathy"] == "na" and out["firmness"] == "na" and "clarity" not in out and out["naturalness"] == 4


def test_rubric_states_that_correct_refusals_are_not_penalised():
    from evals.judge import _PROMPT
    assert "Refusing details to a family member" in _PROMPT and '"na"' in _PROMPT
