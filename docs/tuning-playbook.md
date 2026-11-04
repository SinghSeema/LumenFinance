# Tuning Playbook: Latency and Voice

Used in phases 3 and 4, once the voice loop runs against real APIs. "Tuning" here means configuration and pipeline changes measured with real calls, not model fine-tuning. Every figure in this file is a **vendor claim or a hypothesis until measured here**; results go in `results/` (CLAUDE.md working rules, E-10, E-12).

## 1. Ground rules

- Measure a baseline first: response gap (VAD stop to playback start) with per-stage times, median and p90 over at least 30 voice turns (M9, LLD section 13).
- Change one lever at a time, re-run the same turns, record before and after (E-10).
- Estimate the cost of a run before starting it; print token and character usage after.
- A lever that speeds things up but adds a guardrail miss, a cut-off borrower or a worse listening score is rejected, whatever it saves. Naturalness (55%) outweighs latency.
- Targets stay as in Q-01 (median under 1.2s, p90 under 1.8s) until measurements justify changing them. A 600ms turnaround is a stretch figure from vendor material, not a target.

## 2. Strategy review

Two strategies were proposed for keeping latency low with Sarvam Bulbul as the voice.

### Strategy 1: one TTS engine for the whole call (adopted as the direction)

Use Sarvam Bulbul for every agent utterance and optimise the rest of the pipeline instead of swapping engines. This matches D-05 (Bulbul primary, Cartesia only as an error fallback) and the rejected-ideas table in `design.md` (no second voice inside a call).

| Part of the strategy | Verdict | Notes |
| --- | --- | --- |
| Single voice for the whole call | Adopt | Already D-05 and design section 1a. Consistency is judged directly. |
| "Bulbul V4 Flash" with ~200 to 250ms time to first audio | Verify first | Pipecat 0.0.108 lists only `bulbul:v2`, `bulbul:v3-beta`, `bulbul:v3`. Whether a v4 exists, and how to reach it (newer Pipecat or a thin custom service), is a bake-off item. The TTFB figure is a vendor claim. |
| Pre-rendered filler audio injected straight into the output (0ms TTS) | Adopt | Strengthens D-12: render greeting, fillers and closing lines once with the chosen voice and settings, keep the audio in memory, push it as raw audio frames. Re-render whenever the voice, speed or script changes. Lines must match the voice's gender (D-20). |
| Stream text to TTS after a few words or a clause, not a full sentence | Experiment | Conflicts with the per-sentence output guard (LLD section 10) and may hurt Bulbul's prosody on tiny chunks. Test as experiment T3 below. |
| Silero VAD trailing silence at 200 to 250ms | Experiment, with limits | Risks cutting off a borrower mid-number or at natural Hindi pauses. Test as T2 together with the longer digits window (D-17). |

### Strategy 2: clone one voice on both Cartesia and Sarvam (rejected for this assignment)

Use Cartesia for very fast short acknowledgments and Sarvam for the rest, with the same voice cloned on both.

Rejected, because:
- **Consistency risk remains.** The proposal itself notes that prosody and resonance differ even with perfect cloning, and voice consistency is judged directly.
- **Cost and scope.** It needs a professional voice actor, cloning on two vendors and possibly an enterprise Sarvam plan. That is new paid work (CLAUDE.md: ask before adding a paid service) with no grade return.
- **Rights.** Cloning a real person's voice needs their documented consent; out of proportion for a take-home.
- **Complexity.** Two live TTS connections, per-turn routing and manual pitch and speed matching to explain line by line in the interview.
- **Not needed.** Pre-rendered filler audio (Strategy 1) already gives instant acknowledgments in the same voice.

Cartesia stays only as the error fallback (D-05) and as the bake-off comparison.

## 3. Experiments (run in this order)

Each row: what to change, the hypothesis, how to measure it, and when to keep it.

| ID | Lever | Hypothesis | Measure | Keep if |
| --- | --- | --- | --- | --- |
| T0 | Baseline | None | M9 per stage over 30+ turns, listening notes | Always recorded first |
| T1 | Pre-rendered audio for greeting, fillers, closing | Removes TTS time on those turns and dead air during tools | Gap on turns with a tool call; dead air during tools | Gap drops and the cached lines sound identical to live speech |
| T2 | VAD stop time: 0.25 / 0.4 / 0.6s, with `vad_stop_secs_digits` longer during verification and date questions (D-17) | Shorter stop cuts the gap; the digits window prevents cut-offs | Gap; count of cut-off utterances; S14a (mumbled digits) results | Smallest value with no rise in cut-offs or failed verifications |
| T3 | Flush text to TTS at a clause (comma) instead of a sentence; guard runs per clause and again on the accumulated sentence | Earlier first audio | Time to first audio; guard misses on a set of bad sentences split at commas; listening score | First audio clearly earlier, zero extra guard misses, prosody not worse |
| T4 | Warm TTS WebSocket kept open between turns | Removes connection setup per turn | TTS first-audio time | Measurable drop |
| T5 | LLM effort `low` vs `medium`; prompt caching hit rate | Lower effort cuts time to first token without guardrail loss | LLM first token; guardrail metrics M2 to M5 on the dev scenarios | Faster with no guardrail regression |
| T6 | Deepgram endpointing value | Earlier final transcripts | STT final time; transcript errors on digits | Faster with no rise in digit errors |
| T7 | Bulbul version (v3 vs any newer one found in the bake-off) | Lower first-audio time | TTS first audio; listening test | Faster and preferred or equal by a native Hindi speaker |

Out of scope for tuning: switching TTS engines inside a call, voice cloning, and the guard checks themselves (they are a guardrail, D-14).

## 4. Listening checks (with every TTS change)

- Hindi script sent to TTS: `roman`, `devanagari`, `mixed` (D-11).
- How "EMI" and other acronyms are spoken; whether spacing is needed.
- Amounts in lakh and thousand, dates, and last-four read-backs.
- Gender agreement between the voice and the cached lines (D-20).
- Barge-in: playback stops promptly and nothing queued plays afterwards (M10).

## 5. Results log template

Copy one block per experiment into `results/tuning-log.md`.

```
### T<n>: <lever> (<date>)
Config change: <section.key old -> new>
Runs: <turns>, profile <name>, commit/notes
Gap median / p90: before <x> / <y>  after <x> / <y>
Per stage (STT final, LLM first token, TTS first audio): before ... after ...
Guardrail / recognition impact: <none, or what broke>
Listening notes: <who listened, what they preferred>
Cost: <estimate before, actual after>
Decision: keep / revert, and why
```
