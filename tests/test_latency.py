"""Latency bookkeeping (LLD 13): stage durations, median/p90, exclusions."""
import json

from emi_agent.latency import Turn, append, median_p90, report


def test_stages_and_gap():
    t = Turn(vad_stop=10.0, stt_final=10.2, first_sentence=10.65, playback_start=10.9, stop_secs=0.5)
    assert t.stages_ms() == {"vad_stop->stt_final": 200, "stt_final->first_sentence": 450,
                             "first_sentence->playback_start": 250, "gap": 900}


def test_missing_mark_skips_only_its_stages():
    t = Turn(vad_stop=1.0, stt_final=None, first_sentence=1.5, playback_start=1.8)
    assert t.stages_ms() == {"first_sentence->playback_start": 300, "gap": 800}


def test_median_p90():
    assert median_p90([5]) == (5, 5)
    med, p90 = median_p90(list(range(1, 11)))
    assert med == 5.5 and round(p90, 1) == 9.1


def test_report_excludes_interrupted_and_incomplete(tmp_path):
    path = tmp_path / "lat.jsonl"
    for gap in (0.8, 1.0, 1.2):
        append(Turn(vad_stop=0, stt_final=0.1, first_sentence=0.5, playback_start=gap, stop_secs=0.5), path)
    append(Turn(vad_stop=0, playback_start=5.0, interrupted=True), path)
    append(Turn(vad_stop=0), path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    text = report(rows)
    assert text.startswith("5 turns, 3 complete")
    assert "gap " in text and "median   1000 ms" in text
    assert "median   1500 ms" in text  # gap + 0.5 s VAD stop


def test_report_says_how_many_gaps_end_at_the_filler():
    row = {"stop_secs": 0.5, "interrupted": False, "ms": {"gap": 1300}, "filler": True}
    assert "1 of them started with the slow-model filler" in report([row, {**row, "filler": False}])
