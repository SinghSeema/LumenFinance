"""evals/calibrate.py agreement maths."""
from evals.calibrate import agreement


def test_agreement_counts_within_one_exact_na_and_flags():
    owner = [{"politeness": 5, "empathy": "na", "firmness": 4, "clarity": 3, "naturalness": 2, "flags": []},
             {"politeness": 4, "empathy": 2, "firmness": "na", "clarity": "na", "naturalness": 4,
              "flags": ["false_promise"]}]
    judge = [{"politeness": 5, "empathy": "na", "firmness": 2, "clarity": 4, "naturalness": 2, "flags": []},
             {"politeness": 3, "empathy": "na", "firmness": "na", "clarity": 1, "naturalness": 4,
              "flags": [{"type": "threat", "quote": "x"}]}]
    a = agreement(owner, judge)
    assert a["politeness"]["exact"] == 0.5 and a["politeness"]["within1"] == 1.0
    assert a["firmness"]["within1"] == 0.5          # 4 vs 2 is off by two
    assert a["empathy"]["na_agree"] == 0.5          # na/na agree; 2 vs na does not
    assert a["clarity"]["mae"] == 1.0               # only both-numeric pairs: |3 - 4|
    assert a["flags"] == {"tp": 0, "fp": 1, "fn": 1, "precision": 0.0, "recall": 0.0}
