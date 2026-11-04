"""Call report page: renders a finished call, escapes text, flags early disclosure."""
from emi_agent import call_report
from emi_agent.brain import Brain
from emi_agent.config import load
from emi_agent.data import load_borrowers
from scripted_llm import ScriptedLLM, say

ROW = {"stop_secs": 0.5, "interrupted": False, "after_barge_in": False, "bridge": False,
       "ms": {"vad_stop->stt_final": 150, "stt_final->first_sentence": 600,
              "first_sentence->playback_start": 250, "gap": 1000}}


async def test_report_shows_outcome_transcript_latency(today, tmp_path):
    llm = ScriptedLLM([say("", ("verify_identity", {"factor": "last4", "value": "4821"})),
                       say("Thank you, Arjun.")])
    brain = Brain(load(), load_borrowers()["B001"], today, llm, calls_path=tmp_path / "calls.jsonl")
    [s async for s in brain.respond("Yes, it's 4821 <b>")]
    brain.finish()
    page = call_report.write(brain, [ROW, {**ROW, "interrupted": True}], {"profile": "test"},
                             tmp_path / "r.html").read_text()
    assert "Call report: B001" in page
    assert '"outcome": "unresolved"' in page            # the outcome record JSON
    assert "4821 &lt;b&gt;" in page and "<b>\n" not in page  # borrower text is escaped
    assert "verify_identity" in page                    # tool calls listed
    assert "1000 ms" in page and "n=1" in page          # interrupted turn left out of the median
    assert "none" in page                               # no early disclosure


def test_report_without_latency_rows(today, tmp_path):
    brain = Brain(load(), load_borrowers()["B002"], today, ScriptedLLM([]), calls_path=tmp_path / "c.jsonl")
    brain.finish()
    page = call_report.render(brain, [], {})
    assert "no complete turns measured" in page and "No tool calls." in page
