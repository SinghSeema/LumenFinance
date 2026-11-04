# Results log

## 2026-11-03 baseline-v1 (`results/20261103-164209-baseline-v1`)

Setup: agent `groq:openai/gpt-oss-20b` (prompt v1), simulator `qwen/qwen3.8-27b`, dev set, 1 run each, today 2026-11-03. Tokens: agent 144,742 in / 9,757 out; simulator 20,666 in / 951 out. Cost $0 (free tier). One run per scenario, so pass rates are anecdotes, not rates.

Outcome pass 7/10. Disclosure violations 0. Escalation precision 1.0, recall 1.0 (2 cases).

| Scenario | Result | Root cause |
| --- | --- | --- |
| S04 dispute | FAIL: `stop_requested` | Escalated correctly, then recorded `stop_requested` although no stop was said. Also invented a DOB (`1990-05-15`) from its own example phrase "jaise 1990-05-15", using a verification attempt |
| S07 callback | FAIL: `verification_failed` | Borrower asked for a callback before verifying; agent repeated "share your date of birth" five times, in English to a Hindi speaker, then recorded `verification_failed` itself with no failed attempt |
| S09 language switch | FAIL: `language_used` `en` | Scenario flaw: the simulator switched for one Hinglish turn and the call ended; a switch needs two turns, and Roman Hindi is `hinglish`, not `hi` |

Found in passing runs:
- Every call: `verify_identity` called with `"?"` before the borrower gave a value (returns `unreadable_value`, no attempt used, but costs an LLM round trip each time).
- Payment link sent without asking the channel (F-05), and the confirmation repeated within one turn (S01, S02, S09).
- Guard false positives: S03 "I can’t discuss waivers" flagged as a promise (curly apostrophe defeats the negation check, a code bug); S08 "kal shaam 6 baje" and S10 reading back the borrower's own digits blocked as unverified disclosure (any digit before verification is blocked, which conflicts with Q-07 read-back).
- Masculine verb ("kar dunga") with a feminine voice (D-20).

Planned fixes, one at a time (E-10): code checks first (outcome consistency, verify value must come from the borrower, guard apostrophe), then prompt v2, then scenario S09.

### Change 1: guard v2 (D-22, owner approved)
Read-back of the borrower's own digits and times of day allowed before verification; raw EMI amount added to protected phrases; curly apostrophes normalised so "I can’t discuss waivers" is not a promise. Unit tests cover the three baseline false positives. Not yet re-run live.
Read-back match tightened to exact whole numbers (owner: loose substring match not acceptable).

### Change 2: code checks on model inputs (D-23)
- `verify_identity`: value must be one the borrower said (`not_heard`, no attempt used). Replay of the smoke and baseline transcripts: invented `1234` and `1990-05-15` rejected; all 7 genuine verifications accepted; S10's wrong-but-spoken values still use attempts.
- `record_outcome`: model cannot set `verification_failed` (S07, S10) or `stop_requested` without a detected stop (S04).
- Schema: verified forbids `wrong_number`/`third_party`/`verification_failed`; `callback_requested` needs `callback_time`.
- Stop phrases widened ("no more calls", "don't bother me", "pareshan mat karo", "call mat kijiye", curly apostrophes).
- 343 unit tests pass. Not yet re-run live.

### Change 3: two-tier stop detection (D-24, owner delegated)
"Don't call me in the morning", "abhi call mat karo, shaam ko karna", "bas karo, main pay kar dunga" no longer end the call as stop requests. "Again/ever/kabhi/dobara" and refusals to pay keep a weak phrase a stop; strong phrases always stop. Fixed in passing: "stop it now" had been lost to the "now" qualifier. 362 unit tests pass. Not yet re-run live.

## 2026-11-03 code-checks-v1prompt (`results/20261103-175535-code-checks-v1prompt`): CONTAMINATED, do not compare

Reported 7/10, 0 leaks. But `gpt-oss-20b` has a 200,000 tokens/day cap (found from a 429; response headers show only the per-minute cap). Earlier runs had used about 170k tokens, so the cap ran out about 3 calls into this run and the agent silently fell back to `gpt-oss-120b`. The run therefore mixes two models, and which turn used which model was not recorded.
- Garbage output appeared only in the later calls (S04, S07, S08, S10): runs of `*`, non-breaking and zero-width spaces, doubled words ("colleague colleague", "thik thik thik"). It never appeared in the 20b-only baseline. Hypothesis: it comes from the 120b fallback. Not yet confirmed; it matters because 120b is the voice fallback.
- Still informative as transcripts: S04 recorded `dispute` and S07 scheduled a callback without forcing verification (the code checks working); S08 and S10 escalated with reason `requested` although nobody asked for a human (over-escalation, a new failure); S07's scenario expects `verified: true`, but the simulator refused to verify and a callback needs no verification, so the expectation is wrong.

Harness fixes: evals now disable LLM fallback (one model per run; an exhausted cap becomes an infra error), and each artifact records `llm_turns_by_provider`. The prompt-v2 run was stopped at start: it would have run on 120b.

## Open at end of 2026-11-03
- Groq paid Dev tier: parked by the owner. Prices and Dev-tier limits not confirmed; prompt caching showed no hit in a test.
- Proposal pending: code-driven verification (verify_identity was 23% of agent requests in baseline-v1, mostly wasted).
- Not yet run: dev with code checks + prompt v1 (clean), then prompt v2. S07 expectation to fix; over-escalation in S08/S10; 120b garbage-output hypothesis.

## 2026-11-03 Phase 3 step 1: Sarvam model names and voice audition

- Sarvam API accepts only `bulbul:v3` and `bulbul:v4-flash` (the 400 error names both). `bulbul:v4` is rejected, and so is v2.
- v4-flash speakers are persona/use-case ids (`<name>_<lang>_<style>`, e.g. `simran_enhi_customer`); v3 names like `priya` are rejected for v4-flash.
- Pipecat 0.0.108 checks the model against its own list (v2, v3-beta, v3) and raises on `bulbul:v4-flash` before any network call. To use v4-flash: upgrade Pipecat or register a model config at startup. Not yet known: whether the WebSocket endpoint serves v4-flash.
- Audition: 5 female v4-flash speakers x 4 lines (roman greeting, roman/Devanagari/English EMI line), 20 calls, 1,565 characters, 16 kHz. WAVs in `results/tts_audition/bulbul_v4-flash/`. Speakers: ishita_enhi_customer, simran_enhi_customer, ritu_hi_banking, sanchita_hi_banking, priya_hi_recovery. Verdict by ear: PENDING (owner).
- livekit-server v1.13.9 installed to ~/.local/bin (sha256 verified against the release checksums).

## 2026-11-03 Phase 3 step 3: bot.py written, smoke-tested without a microphone

- Pipeline: LiveKit in -> VADProcessor (Silero; transport-level VAD is deprecated in 0.0.108) -> Deepgram -> BrainProcessor -> Sarvam TTS -> LiveKit out. `python3 -m emi_agent.bot --profile hindi_bulbul`; browser client `python3 scripts/serve_client.py` (livekit-client 2.22.4).
- Turn end: a finalized Deepgram transcript after VAD stop starts the reply; otherwise a `turn.stt_wait_secs` (0.3 s) wait after VAD stop, because Deepgram sends no finalized frame when it already sent every word.
- Barge-in: `turn.min_words_interrupt` words, or any stop request, while the agent is talking. Cancels the Brain turn (Brain now records the cut turn with a "[cut off by the borrower]" marker, in a `finally`) and pushes an InterruptionFrame downstream. Shorter utterances while the agent talks are ignored as backchannels.
- Hang-up: after the Brain sets should_hang_up, wait until playback has been quiet for 1 s, then finish() and EndTaskFrame. Borrower leaving or Ctrl-C also records an outcome.
- Latency (`emi_agent.latency`): per turn vad_stop, stt_final, first_sentence (not first token: TTS needs a sentence), playback_start (BotStartedSpeakingFrame), into results/latency/*.jsonl; the report also adds VAD stop_secs to show what the borrower feels.
- `clean_for_tts` strips markdown and zero-width characters before TTS (D-21 backstop).
- Smoke test (silent fake borrower, no LLM call): bot joined room, greeting played over bulbul:v4-flash WebSocket, recorded ~3.5 s of speech, borrower left, outcome `unresolved` written to results/calls.jsonl (that row is the smoke test). Sarvam TTFB 0.49 s in this single sample: not a measurement.
- Not yet built: silence prompts, fillers on slow tools, the digits VAD switch (D-17), cached greeting audio, context-aware backchannel rule (current rule is word count only). Cut-off marker records what the Brain yielded, which can be more than what was actually played.
- 385 tests pass.

## 2026-11-03 First owner voice calls (B003): what failed, and fixes

Call 1 (owner): 9 turns, gap median 1,679 ms / p90 2,728 ms (VAD stop to playback; +0.5 s VAD stop = what the borrower felt). LLM stage median 1,116 ms. 20b served 5 turns, 120b 13.
Call 2 (owner): 5 turns, gap median 3,110 ms / p90 7,975 ms; LLM stage p90 7,450 ms. 20b 1 turn, 120b 14.
Cause of call 2 (confirmed by a 429 body): gpt-oss-20b daily cap, "Limit 200000, Used 199445". Every 20b request was refused, the SDK retried twice honouring retry-after, then 120b answered. A call also needs more than 8k tokens/min: ~1.9k input tokens per request, 2-3 requests on a tool turn.
Other failures seen in call 1:
- 40 s dead air: a Deepgram final arrived after the 0.3 s VAD-stop wait, not marked finalized, and sat unused until the next utterance. Fixed: such a final starts a new short wait.
- Duplicate final ("Can you hear me?" twice in 7 ms) made the agent barge in on itself. Fixed: a repeat within 1.5 s is dropped.
- "April" while the agent talked was dropped as a backchannel. Fixed: an utterance with a number or month counts as a barge-in.
- 120b spoke a tool call as text ("record_outcome({...})"), so no outcome was recorded and the call did not hang up. NOT fixed: needs a guard change (owner approval).
- Verification robotic: "year-month-day format", the same guard line four times. Partly addressed by D-25 (partial date note asks only for the missing part, no format instruction).
Fixes made (424 tests pass; bot.py turn logic has no unit tests, not yet re-tested live):
- Voice: `llm.max_retries=0` for the primary (falls over at once), last fallback keeps 1 retry.
- `llm.reasoning_effort` config (null = Groq default). T5 measurement aborted by the 20b daily cap after one pair (default 1,512 ms but it made a tool call; low 632 ms): not a result, default unchanged. Output: results/t5_effort_ttft_20261103.txt.
- D-25 code-driven verification.
- Logged: guard-blocked original sentence; barge-in delay from speech start.
- Config loader: `--set` now works for keys whose default is null.

## 2026-11-03 Turn-end and STT fixes, then a scripted end-to-end call

Owner call 3 (B003): verified, outcome promise_to_pay 2026-11-07. Agent asked "7 November or 11 July?" because Deepgram smart_format turned a spoken date into US-order "11/07/2026". Checked on an audition clip: smart_format "due on the November 5"; numerals+punctuate "due on the 5th of November." Changed: smart_format off, numerals on, punctuate on. Also seen: Deepgram heard Hindi "chaudah hazaar" as "aadha hazaar" (STT accuracy, not fixed).
Turn end: per-turn VAD stop -> transcript was ~300 ms in calls 1-2 = the fixed stt_wait timer (Deepgram's final was already in hand). Changed: reply at once at VAD stop unless Deepgram has interim words not yet final; then wait for that final, capped at turn.stt_wait_secs (now 1.0). 7 unit tests drive BrainProcessor with frame sequences from the owner's calls.
Scripted end-to-end call (fake borrower speaking Sarvam-made lines, B003): DOB said as "nau November, unnis sau pachasi" was split by VAD at the natural pause; code joined the halves and verified (D-25 working). But:
- Deepgram lag: finals 0.86-1.32 s after VAD stop (~1.4-1.8 s after speech end), so at VAD stop Deepgram had not even sent interims for the last words; the pending-words check cannot see them. Fragment turn plus barge-in still happen. Deepgram streaming latency (language=multi, endpointing, network) needs measuring on its own.
- The pause inside a spoken date ended the VAD turn: evidence for D-17 (longer VAD stop while a date or digits are expected).
- 120b asked for the birth year after code had verified: it followed a stale "year missing" note from the cancelled fragment turn. Fixed: the verified note says it replaces earlier notes.
2 turns only: gap median 3,419 ms (120b, 20b out of daily quota). 432 tests pass.

## 2026-11-03 Deepgram streaming latency (bakeoff/stt_latency.py, scratchpad stt_probe.py)

Method: two Sarvam-voiced Hindi borrower clips (line1 2.5 s, line2 3.9 s), streamed as the bot does (PCM, real-time pace, silence after, Finalize at +500 ms as VAD stop). Times from the last voiced 10 ms chunk. n=6 per arm (3 runs x 2 clips); synthetic clean audio, not a real mic.

| Arm | Median final done (ms) |
| --- | --- |
| US host, multi, endpointing 300 (old) | 850-870 |
| US host, multi, endpointing 100 | 646-654 |
| US host, hi, endpointing 300 / 100 | 887 / 1102 (more variance, one dropped final) |
| EU host, multi, endpointing 300 | 716 |
| EU host, multi, endpointing 100 (new) | 560 |

- Network: api.deepgram.com resolves to Virginia, TCP connect ~0.31-0.33 s with 1.4 s spikes (2 of 5); api.eu.deepgram.com (Frankfurt) 0.16-0.18 s (curl, 5 tries each).
- In-bot probe (LiveKit -> VAD -> Deepgram, fake borrower): final after VAD stop 310-343 ms before the change, 75-130 ms after it. Our pipeline adds no measurable delay; LiveKit input arrives in ~70 ms chunks (2200 bytes).
- **Correction** to the earlier entry: "Deepgram lag 0.86-1.32 s after VAD stop" was wrong. line1.wav has a 500 ms pause after "Haan ji,", so VAD (stop_secs 0.5) ended the turn mid-sentence and the "late final" was the rest of the sentence still being spoken. The fragment turn is caused by VAD ending the turn at a natural pause, not by STT lag.
- Accuracy: language=multi heard "Haan ji" as "Haji" or Korean "하지" in 6/6 runs; language=hi got "हां जी" 6/6 but was slower and dropped one final. Not switched; needs real-voice clips.
- Changed: stt.endpointing 300 -> 100, new stt.host api.eu.deepgram.com. Tests 432 pass. Not yet confirmed in a live mic call.

## 2026-11-03 21:28 owner call (B003), after the STT tuning

- vad_stop->stt_final median 297 / p90 368 ms (n=6): the Deepgram EU + endpointing 100 change holds live. LLM (stt_final->first_sentence) median 2284 ms is now the bulk of the gap (2797 ms median).
- Bug, fixed: Deepgram numerals writes "double 7, 4 3"; heard.py only read "double seven", so code verification and the read-back check both missed 7743 twice. Regression from the numerals switch. Test added; 437 pass.
- Two long silences: 25 s after "November को pay करूंगा" and 33 s after "7th November." Not proven (llm.py logged nothing). Likely Groq rate-limit waits: three requests went out in 5 s (10.7, 13.0, 15.2) from barge-ins, so the 20b 8k tokens/min cap would have failed over to 120b, which retries once after Groq's retry-after delay; no client timeout set. Added per-request LLM logging (first text ms, done ms, tools, tokens, failures).
- Call ended with no goodbye: after the stalled turn, "Are you there?" got no reply and the call hung up (outcome recorded inside the stalled turn, so the stage was probably CLOSING).
- False escalation: STT heard the opening reply as "Human finance." and the LLM answered "I will connect you with a colleague", so escalate_to_human=true. The agent also said "Thank you for calling." but carried on.
- Agent replied in English to the borrower's Hindi turns.
- Fixes (owner approved): D-26 escalation needs a clear request; D-27 voice LLM 4 s timeout, no retry-after waits, hold line + one retry, closing line when the closing turn was cut or empty, no "goodbye"/"alvida" in fixed lines. 450 tests pass. Not yet checked in a live call.
- llm.reasoning_effort set to low by the owner (2026-11-03), ahead of the T5 rerun (only n=1 so far: first text 632 vs 1512 ms). Applies to text evals too; dev evals not re-run with it.

## 2026-11-03 Gemini free tier as LLM (bakeoff/effort_ttft.py, B003 system prompt, 10 s timeout)

- gemini-2.5-flash-lite: 404, "no longer available to new users".
- gemini-3.5-flash-lite (default and reasoning_effort=low): 5 of 6 requests timed out at 10 s; the one that answered took 2422 ms (verify_identity called correctly). reasoning_effort=none is rejected (400).
- gemini-3.5-flash, reasoning_effort=low: first text 3798, 3610, 5859, 1387 ms (median 3704, n=4); Hindi roman reply and the verify_identity call were correct.
- For comparison, Groq in the 21:47 owner call: gpt-oss-20b first text 787 ms, gpt-oss-120b 536-1246 ms.
- Not switched: free-tier Gemini is too slow from here for a voice turn. GeminiLLM adapter kept (provider: gemini) for later use. Also fixed: a timeout in the middle of a stream raised raw httpx.ReadTimeout past both adapters; it is now an LLMError so the fallback runs.

## 2026-11-03 Sarvam-hosted LLMs (T5 script, B003 prompt, Sarvam key, results/t5_sarvam_20261103.txt)

n=4 each (2 runs x greeting reply + last-four turn); all four called verify_identity correctly on the last-four turn and replied in roman Hinglish.

| Model | Median first text | Median output tokens |
| --- | --- | --- |
| sarvam-105b-conversations | 477 ms (225-736) | ~34 |
| glm5.3-flash | 630 ms | ~46 |
| sarvam-105b, reasoning low | 1500 ms | ~136 |
| deepseekv4.1-flash-1017 | 1683 ms | ~85 |

Groq gpt-oss-20b in the 21:47 owner call: 787 ms. Sarvam bills in rupees from the existing account; docs list sarvam-105b at Rs 29.28 / 1M input, Rs 73.2 / 1M output (not checked against the dashboard). Generic OpenAICompatLLM adapter (providers gemini, sarvam) replaces GeminiLLM.
- Default LLM switched to sarvam-105b-conversations; fallbacks groq gpt-oss-20b then 120b. LLM failure after the outcome is recorded now gives the closing line, not the apology. 451 tests pass. Dev evals not re-run on the new model.

## 2026-11-03 23:26 owner call (B003), sarvam-105b-conversations primary

- Latency (n=5): vad_stop->stt_final 205 / 390 ms, stt_final->first_sentence 698 / 1710 ms, gap median 1119 / p90 2333 ms. First call with the gap median under 1.2 s; p90 still above 1.8 s; n too small for the gate. LLM first text 261-938 ms; 11 requests, no fallback used.
- "Double 7, 4 3." verified in one turn (code path). Outcome promise_to_pay 2026-11-10 ("next Tuesday") correct.
- Split turns still the main defect: "जी मैं राहुल" | "बोल रहा हूं." and "मैं अभी payment नहीं कर पाऊंगा. Because" | "right now" | "right now, I don't have money..." -> fragment replies, barge-ins, "aawaz saaf nahi aayi", record_outcome called on the fragment.
- The model replies in Devanagari although script=roman (likely mirroring Deepgram's Devanagari transcripts). guard.py has few Devanagari promise words (only "पेनल्टी माफ"), so G-04 keyword coverage is weaker for this model.
- STT heard "next week" as "Next point."; the agent answered that one turn in English.
- D-28 unfinished-turn pause (0.4 s), D-29 Devanagari script + Devanagari guard words. 475 tests pass. Not yet tried live.

## 2026-11-03 23:37 owner dispute call (B004 Sunita, S04-style), sarvam-105b-conversations

- Pass on every S04 expectation: verified (DOB "तीस May उन्नीस सौ इक्यानवे", by code), amount dispute -> escalate_to_human on the first dispute turn, no argument, no promise, no guard hit; second claim (loan not hers, identity misuse) handled calmly; outcome dispute, escalate_to_human true, notes accurate. Closing "समय देने के लिए धन्यवाद, आपका दिन शुभ हो" (no goodbye/alvida).
- Latency (n=5): gap median 859 / p90 1060 ms; LLM first text 246-541 ms; 7 requests, no fallback. Within the phase-3 targets, but n=5.
- No fragment turns this call; three VAD segments of the DOB answer were joined into one turn. The unfinished-turn pause had no log line, so whether it fired is unknown; log line added.
- Minor: first escalation line "मैं आपकी बात अपने colleague से करवा देती हूँ" sounds like a live transfer (the next sentence says they will contact her). The call hung up 1.1 s after the closing line while the borrower had just started speaking.

## 2026-11-03 open points, batch 1 (495 tests pass; none tried live yet)

- Prompt v3 = v1 plus one themed change (handoff and closing): a colleague will call back, never "connecting you now"; close with thanks, no "goodbye"/"alvida". Code notes (stop, time-up, third party) reworded the same way. prompt_version now v3.
- Hang-up waits until the borrower has also stopped speaking (a last "thank you" is no longer cut off).
- Last four digits split across two turns are joined by code (as for DOB) when the first part was cut off mid-digits ("It's double" | "7 4 3"); whole numbers in separate turns ("48" | "21") still do not join. digits_were_said follows the same rule.
- Evals: S04 now covers the amount dispute only; new S04b (dev) covers "not my loan / identity misuse". Dev set is 11 scenarios.

## 2026-11-03 23:49 dev eval smoke, sarvam-105b-conversations + prompt v3 (results/20261103-234943-sarvam-v3-smoke)

- 10/11 pass, runs=1 each; leaks 0, guard hits 0; escalation precision 1.0, recall 1.0 (tp 3). S04 and new S04b pass.
- Tokens: agent 101,402 in / 2,278 out (Sarvam), simulator 20,329 / 1,038 (Groq free). Sarvam cost at the docs price ~Rs 3.1 (computed, not read from the dashboard). The runner's pre-run estimate still describes Groq limits; it is wrong for a Sarvam agent.
- S09 FAIL on the outcome label only: borrower "parso tak zaroor jama kar dunga" (= the due date); the agent asked for an exact date and recorded promise_to_pay 2026-11-05; the scenario expects will_pay. Spec gap: will_pay vs promise_to_pay is not defined for "pay by the due date".
- S09 also: first agent reply after the switch to Hindi was in English (LanguageTracker needs two Hindi turns, by design); "समझ गया" (masculine) from a female agent; payment link sent by SMS without asking the channel.

## 2026-11-03 fixes after the smoke run (owner approved a-d)

- (a) Outcome definitions in LLD 5 and the record_outcome tool: will_pay = agrees, no date; promise_to_pay = names a date, the due date included. S09 now accepts both (accepted_outcomes), noted in the scenario file.
- (b) LanguageTracker: a 5+ word turn in the other language switches at once; short turns still need two in a row.
- (c) Prompt v4 = v3 + one line: feminine Hindi forms ("समझ गई", never "समझ गया"). prompt_version v4.
- (d) evals/run.py estimate is per provider (Sarvam rupees from smoke-run tokens per call).
- 505 tests pass. Full dev run next: sarvam + v4, runs 5.

## 2026-11-04 dev eval, sarvam-105b-conversations + prompt v4, runs 5 (results/20261103-235541-sarvam-v4-dev5)

- Outcome pass 48/55 (87%). Leaks 0. Escalation precision 0.88, recall 1.0 (tp 15, fp 2, fn 0). Guard hits 19 (S08 9, S10 4, S01 2, S03 2, S02 1, S07 1).
- Tokens: agent 639,214 in / 13,231 out (Sarvam, ~Rs 19.7 at the docs price, computed); simulator 135,950 / 6,369 (Groq free). Estimate was ~Rs 15.
- Per scenario: S01 5/5, S02 5/5, S03 5/5, S04 5/5, S04b 5/5, S05 2/5, S07 3/5, S08 4/5, S09 5/5, S10 4/5, S12 5/5.
- Failures, by cause (raw score above is not adjusted):
  - Agent defect: S05 r1, r2 - escalate_to_human(reason other) after a wrong number (both escalation false positives).
  - Ambiguous simulator line: S05 r3 - stranger said "Yeh number toh mere paas hai. Amit ji kahan hain?"; agent recorded third_party and asked when Amit can be reached.
  - Spec question: S07 r3, r4 - simulated borrower refused to verify and asked for "kal subah"; agent scheduled the callback without verification (no loan detail shared); scenario expects verified true.
  - Spec question: S08 r2 - "Chup ho ja, utha-utha ke poochhna band karo" detected as a stop request; outcome stop_requested; scenario accepts unresolved or callback_requested only.
  - Simulator fault: S10 r3 - simulator gave up after 2 failed attempts and emitted "[EXTRACTION_RESULT]" and "[" as turns; agent recorded unresolved (correct with 1 attempt left).
- Guard hits: most are unverified_disclosure on polite refusals that name "loan"/"EMI" before verification ("बिना verification के loan details नहीं बता सकती"). By G-02 these do reveal a loan exists, so blocking is defensible, but the safe line repeats and sounds robotic. One false positive: S03 r3 "मैं किसी भी तरह की छूट या बदलाव का वादा नहीं कर सकती" blocked as a promise; Hindi puts नहीं at the end, beyond the 3-word negation window.
- Fixes A-D approved and made (D-30): escalation gate after wrong_number/third_party; prompt v5 (no loan words before verification); Hindi clause-bounded negation in the guard; S05 persona clarified, S07 verified [true, false], S08 accepts stop_requested, simulator garbage lines -> infra error after one retry. 519 tests pass. Expectation changes are scenario-spec decisions, not re-scoring of the 48/55 run.

## 2026-11-04 guard English window + step 1 (one TTS response per reply)

- Guard: English refusal reach-back extended over promise-related words and list items only ("I cannot promise or suggest any waiver, discount, or fee reversal" is a refusal; "I cannot wait to give you a discount" stays a promise). Tests added.
- bot.py: each reply is pushed as LLMFullResponseStart + one AggregatedTextFrame per guarded sentence + LLMFullResponseEnd (no End on barge-in). Pipecat's TTS then pauses only at the end of the reply, not after each sentence. Log line added for the first reply after a barge-in. 524 tests pass.
- Scripted call (fake borrower, B003, 2 turns): the 3-sentence reply played as one 10.5 s stretch with no stop/start between sentences (before: 0.2-0.9 s gap at each boundary). Reply after a barge-in: first audio 257 ms after first sentence (reconnect not in the way). D-28 fired: "Haji" waited 0.4 s and joined "...राहुल वर्मा बोल रहा हूं." into one turn.
- Open: TTS first audio was 734 ms on reply 1 and 242 ms on reply 2 (n=2). Possible cause: Sarvam now gets the first sentence together with the next ones (min_buffer_size 50, max_chunk_length 150), so its first chunk can be longer. Not established; needs the live calls. The first LLM request of the call took 1759 ms (later 267 ms); earlier calls' first requests were 541-938 ms, so a cold connection at call start is likely; warming it during the greeting is a candidate.

## 2026-11-04 Smart Turn v3 (setup B) vs current (setup A) - B rejected for now

- Built as a config option: turn.detector smart_turn, profile hindi_bulbul_smartturn (VAD stop 0.2 s triggers a check; held turns end at a 2.0 s silence cap); src/emi_agent/turn_gate.py, tests/test_turn_gate.py. Default stays detector vad (setup A). 527 tests pass.
- Local check (bakeoff/smart_turn_check.py, Sarvam-voiced clips): ~70 ms per check on this laptop (1 thread). Verdicts: "Haan ji," -> finished; full "Haan ji, main Rahul Verma bol raha hoon" -> NOT finished (wrong); DOB cut before/mid year -> not finished (right); full DOB -> NOT finished (wrong).
- Scripted call through B (B003, 2 turns): "Haji." judged finished -> fragment reply + barge-in; "मैं राहुल वर्मा बोल रहा हूं." held to the 2 s cap; DOB split into "...नौ November" + "उन्नीस सौ पचासी" -> fragment reply + barge-in (code still joined and verified). Gap median 3138 ms (n=2) vs setup A ~1.0-1.5 s in earlier calls.
- Verdict: Smart Turn v3 misjudges finished Hindi sentences as unfinished on these clips, which costs the full silence cap. Caveat: synthetic voice, small n; a live A/B with a real voice was not run.
- Also seen: after a barge-in that happened just as the next reply began, first audio came 2152 ms after the first sentence - the Sarvam reconnect CAN land on the critical path. One case; the warm spare connection is the candidate fix.

## 2026-11-04 TTS input-script check, Bulbul v4-flash vs Cartesia sonic-3.6 (results/tts_script_check/20261104-010601)

- Purpose: test the claim "Hindi in Devanagari, English terms in Latin reads best" (D-11 `mixed`) on both engines. bakeoff/tts_script_check.py: 5 real agent lines x forms mixed / deva / roman (+ English line, + Cartesia-documented-unsupported Devanagari+romanised mix) x 4 feminine voices (Sarvam simran, ishita; Cartesia Kriti, Arushi; locale hi-IN, Cartesia normalization off). 68 clips, 3,050 chars per engine, 0 failures. Blind listen.html + key.csv; owner's ear scores pending.
- Objective proxy only (Deepgram nova-3 multi transcription of each clip; intelligibility, NOT naturalness): mixed, deva and roman are all intelligible on both engines; "EMI", "verify", "UPI", "payment link", "colleague" survive in every form, including Devanagari-transliterated English (ई एम आई -> "emi").
- Defect, Cartesia voice Kriti: Hindi number words spoken as English numbers in every form ("बारह हज़ार पाँच सौ" -> "twelve thousand five hundred"), and the digit read-back "सात सात चार तीन" -> "seven thousand seven hundred forty three" (c047, c054, c055) - a wrong read-back for G-08/S14a. Arushi and both Sarvam voices read them as written. Needs ear confirmation.
- The unsupported mix (Devanagari + romanised Hindi) misbehaved on both engines: Sarvam ishita switched the amount to English (c028), Cartesia Arushi repeated "आपकी" (c024). Supports a closed-word-list script backstop before TTS.
- Sarvam simran, roman form: "call karenge" came out unintelligible (c065); roman is the weakest form for Bulbul (n=1).
- Whole-clip REST request time from this laptop: Cartesia median 669 ms, Sarvam 1571 ms (n=34 each). Full clip, not first-audio latency; the live pipeline uses WebSockets. Not evidence for the latency gate.

## 2026-11-04 eval review: simulator, process checks, M6, and the English-reply regression

- Judge: evals.judge_model was never called; score.py is rule-only. Owner: rules first, judge + calibration later. Added process checks counted in pass (ai_disclosure after the first bot question; no_invented_link when the link tool failed; clarify_vague_date opt-in, S13) and M6 language_mismatches (reported, not in pass). Re-scoring sarvam-v4-dev5 offline: pass unchanged 48/55.
- Simulator moved from Groq qwen3.8-27b (daily cap) to Sarvam glm5.3-flash (owner choice). Every run from here uses a different simulator from v4/v5: not a like-for-like comparison.
- Finding (M6): English 5+ word borrower turn -> English reply: v1 gpt-oss-20b 11/11, v3 10/13, v4 15/54, v5 (partial run) 2/38. Tools return English facts; the model translated them. Cause: Devanagari-only examples added in prompts v4 and v5.

## 2026-11-04 prompt v6 (language first) targeted run (results/20261104-103518-v6-lang-check)

- v6 = v5 + one theme: language section first, "English means every word English", English equivalents beside each Devanagari example. S01, S02, S03, S09, S12 x3; agent sarvam-105b-conversations, simulator glm5.3-flash.
- English -> English 35/35 (v5: 2/38); Hindi/Hinglish -> Hindi 15/15 (no over-correction). lang mismatches 0.
- Tokens: agent 144,408 in / 2,928 out (~Rs 4.4 at the docs price, computed); simulator 26,487 / 2,797.
- New regression from v6: 5 of 6 S01/S03 runs said the pre-verification line ("an important matter regarding your account") AFTER verification and added invented reminders, incl. "this call may be recorded for quality and training purposes" (invented policy). -> prompt v7.
- S02 r1 FAIL (real): simulator said "this coming Friday, that is the first of November" (contradiction); the agent read back "Friday the first of November" and the code recorded 2026-11-06. Root cause in dates.normalize: a weekday overrode an explicit date ("Friday 13 November" -> 6 Nov), and ordinal words were unrecognized. Fixed: explicit date wins; a disagreeing weekday -> date_unclear "conflict"; ordinal words parsed only next to a month name. Tests added.
- S03 r1, r3 INFRA: the simulator garbage filter treated a bare "[HANGUP]" as garbage. Fixed and tested.
- New: src/emi_agent/speakable.py runs after the guard on every sentence (URL/emoji drop, digit backstop in the sentence's script, closed-list romanised-Hindi -> Devanagari in Devanagari sentences, wrong_language flag). Hits are on Sentence.speech and CallState.speech_hits and counted by the scorer. The v6 run predates it.

## 2026-11-04 prompt v7 targeted run (results/20261104-103930-v7-lang-check)

- v7 = v6 + one theme (v6 side effect): the "important matter" line is for before verification only; the facts turn says the tool's facts and one question, nothing else.
- Invented reminders/policies: 0 of 15 (v6: 5 of 6 S01/S03 runs). The "important matter" line still appears after verification in 9 of 15 (wordy, not wrong). English -> English 36/36; Hindi -> Hindi 15, one Hindi -> English.
- Raw pass 8/15. None is a language failure: S03 x3 escalated for hardship but never called record_outcome (also v6 r1, r3: 5 of 6) -> code now records the escalation reason (hardship/dispute) when the model records nothing; S02 x3 simulator said "this Friday" (goal: "next Friday"), agent recorded 6 Nov correctly -> S02 hidden goal now quotes the exact words (owner); S01 r1 "pay by tomorrow" -> promise_to_pay, correct by LLD 5 -> S01 accepts promise_to_pay (owner).
- prompt_version default -> v7.

## 2026-11-04 number style: Hindi sentence, English numbers (owner decision)

- behavior.hindi_numbers: english. Hindi/Hinglish replies get English number words inside the Hindi sentence (speech_format number_lang 'hi-en'): "twelve thousand five hundred rupees", "twenty-fifth November", "fourteen days", read-back "seven, seven, four, three"; today/tomorrow/day after stay Hindi (कल / परसों). If the borrower says numbers in Hindi words (says_hindi_numbers; एक/दो/teen/das/saath/sau excluded as everyday words), the call switches to Hindi numbers and the model gets the facts again as a note. Disclosure guard phrases include the new forms.
- Bug fixed: language.detect judged Devanagari by letters, so "हाँ मैं pay कर दूँगा twenty fifth को" was 'en' (7 words -> an immediate switch to English). Now by words (1/3 Devanagari = Hindi), same rule as the scorer.
- Listening A/B clips (results/tts_script_check/20261104-105223, 30 clips; owner's ears pending). Deepgram proxy: English numbers inside Devanagari sentences intelligible on simran, ishita and Cartesia Arushi. Arushi turned "fifteen March" into "पंद्रह March" (Cartesia voices convert numbers unpredictably: Kriti the other way). Romanised "parson" misread by both Sarvam voices ("parcel", "percent"); only matters for script roman or a roman-writing fallback LLM.
- 572 tests pass.

## 2026-11-04 v7 dev run (results/20261104-105425-v7-dev5)

- 52/55 raw (S08 4/5, S12 3/5, the rest 5/5). Leaks 0, infra 0, escalation precision 0.94 recall 1.0 (tp 15 fp 1). Pre-TTS wrong_language hits 6. Failures not yet reviewed. New simulator (glm5.3-flash): not like-for-like with v4 (48/55).
- Tokens: agent 533,566 in / 10,384 out (~Rs 16.4 at the docs price, computed); simulator 115,978 / 15,114.

## 2026-11-04 live call B002 (owner, Hindi): DOB never verified

- 22 July 1994 said twice; never verified; verified on the last four ("Double 3, 6 7" -> 3367, by code). No attempt charged (unreadable values are free), no leak. Gap median 844 / p90 1189 ms (n=7).
- Root cause: Deepgram wrote "बाइस" (short i); the number table has only "बाईस", so find_dob saw no day. "बाईस July उन्नीस सौ चौरानवे" parses.
- Also: (a) verify_identity's own parser took digit formats only, so the model's call with the spoken words was "unreadable"; (b) after the code note "only the day is missing", the model said "यह जानकारी हमारे records से मेल नहीं खा रही" (nothing had been checked); (c) "Buys, July 19 94." + "Buys July" (STT for बाइस) flipped the call to English (two short 'en' turns).
- Fixes (owner approved, test-first; replays of the call pass): Devanagari spelling-tolerant number words (ई/इ, ी/ि, ऊ/उ, ू/ु, ँ/ं, nukta, ब/व; unambiguous only); verify_identity falls back to find_dob on spoken values; note_dob_partial says nothing was checked and gives an example question; language detection ignores digits, number words and month names, and a turn left with under 2 words does not count toward a switch. 577 tests pass.
- Not changed (owner): Hindi number words in a DOB answer still switch the call to Hindi numbers (the amount was "चार हज़ार दो सौ रुपये").
- Also seen: "मैंने तो पूरा amount pay कर दिया था" on an overdue EMI -> escalate dispute, no record_outcome (code fallback recorded dispute).

## 2026-11-04 "already paid" on an overdue EMI: prompt v8 + code (owner request after the B002 live call)

- Wanted: thank, never argue, say gently it still shows pending, "no worries", ask when/how it was paid, a colleague will check and call back. v8 = v7 + that one theme (English and Hindi example lines; "a payment they say they made is not a dispute"; never ask for OTP/PIN/password/card). Code: record_outcome(already_paid) on an overdue loan sets escalate_to_human (flag escalate:payment_check) and tells the model a colleague will check; not overdue = just noted. New dev scenario S15 (B002, overdue, says paid by UPI). prompt_version default -> v8.
- Targeted run (results/20261104-111341-v8-already-paid): S01 3/3, S04 3/3 (unchanged), S15 2/3. The two passes sounded as wanted ("हमारे records में यह अभी pending दिख रहा है, पर कोई बात नहीं ... आपने payment कैसे किया था, UPI से या bank transfer से?").
- S15 r2: correct DOB and last four, locked out; all three charged attempts were made BY CODE with wrong dates (1994-07-21, 1900-07-04, 1994-07-03). Causes in heard.find_dob: first small number taken as the day ("मैंने तीन बार बता दिया" -> 3; "last four digits" -> 4); self-correction ignored ("इक्कीस जुलाई... मतलब बाईस"); danda glued to "चौरानवे।" hid the 94; two-digit year "July ninety-four" unsupported; find_last4 saw 2294 and 3367 and gave up; said_numbers read "last four digits three three six seven" as 43367; the tool could not read spoken last-four digits.
- Fixes (tests from the exact utterances): day = the number next to the month word; "last four digits" stripped; after a correction word only the rest counts; two different days without one = not a date (no code verify, no charge); "July ninety-four" -> 1994; danda excluded from words; spelled single-digit run wins for last four; tool reads spoken last four. Re-run (results/20261104-111734-v8-verify-fix): S15 5/5, S10 5/5 (wrong details still fail).
- Scorer M6 false positive fixed: borrower lines of mostly English number words were called English; numbers/months now stripped first (as the live detector).
- Finding: the model translated the tool's English amount ("four thousand two hundred rupees") into Hindi in 14 of 16 Hindi calls since the number-style change (v7-dev5 + today's runs), undoing the owner's number style. Fix: speakable swaps this borrower's exact fact phrases in Hindi number words back to the tool's phrases when the style is english (hit "number_style"). Not yet re-measured in an eval. 598 tests pass.

## 2026-11-04 live call B002: greeting cut 2 s in, 31 s silence, "Hello" -> "2."

- Diagnosis (no fix first, owner): barge-in at 37:39.9 with no VAD speech; triggered by a Deepgram interim (text not logged). Likely the borrower's "hello" heard as "2" (the later "Hello" was finalized as "2."), let through by the lone-number rule; echo possible. No final followed, so no turn and no recovery: the silence_prompts/close config keys were never implemented (dead config, as was backchannel_filter). The greeting goes straight to TTS, so the model believed the identity question was asked and went straight to DOB.
- Settings review: browser echoCancellation/noiseSuppression/autoGainControl on; no server noise filter; Silero VAD confidence 0.7, min_volume 0.6, start 0.1 s (default 0.2), stop 0.5 s. Barge-in used only Deepgram text, so VAD thresholds never applied to it.
- Fixes (owner approved 1-3 + greeting-cut note), test-first replays: (1) every barge-in logs its text, source (interim/final) and whether VAD heard speech; (2) barge-in needs VAD to agree (speaking, or stopped < 1 s ago) and 2+ words; a lone number/month only right after a DOB/last-four question; a stop request always interrupts; (3) silence prompt built: 4 s after a barge-in with no turn, 8 s after the agent finishes -> "Hello, क्या आप मुझे सुन पा रहे हैं?" (or the greeting again if it was cut and nothing heard since); 10 s more -> polite close, outcome unresolved; a stale never-finalized interim no longer blocks it; (4) a barge-in on the greeting tells the Brain, which notes the model to ask the identity question again. backchannel_filter key removed. 606 tests pass. Not yet tried live.
- Not done: server noise filter (RNNoise = new dependency, needs owner OK), VAD start back to 0.2 s, Deepgram keyterms.

## 2026-11-04 live call B002 (11:56) review, verification fixes, Deepgram keyterm bake-off

- Call: "बाईस July" heard as "buys, July"; "22nd July 19 94" not read by code ("22nd" split as 22 + nd); the model said "मेल नहीं खा रही ... आखिरी मौका" with no check run (prompt-only rule V3 ignored) and guessed the missing day itself, using an attempt. vad_stop->stt_final median 210 ms: STT was not the latency problem; LLM 626 ms median.
- Fixes (tests first, 656 pass): digit ordinals; thousands ("दो हज़ार एक" was read as 2002); birth years before 1920 = year missing ("उन्नीस सौ सतासी" was 1900); code guard replaces an invented mismatch/attempts claim with one re-ask; the model's DOB call is not checked while code has a partial date.
- Owner decision: verification asks the last four digits first, then the date of birth (D-32, prompt v9, code `ask_next`). Not yet run in evals or live.
- Deepgram confidence (transcript + low words) logged per borrower turn (D-34); no added latency, logging only.
- Keyterm bake-off (bakeoff/stt_keyterms.py; results/stt_keyterms/20261104-130330.txt): 25 borrower lines (12 DOB, 6 last four, 7 with no number) x 3 TTS voices (Sarvam simran, ishita; Cartesia arushi), streamed as the bot does, nova-3 multi, EU host, 1 run each. Correct value parsed by our code:

  | keyterms | DOB | last four | no false number | final_done median |
  |---|---|---|---|---|
  | none  | 15/36 | 9/18  | 21/21 | 519 ms |
  | dates | 26/36 | 13/18 | 21/21 | 518 ms |
  | full  | 26/36 | 15/18 | 21/21 | 539 ms |

- Main failure mode is not Hindi numbers as such: multi wrote 27/75 transcripts (none) in Telugu, Kannada, Bengali or Arabic script ("six zero five four" -> "సిక్స్ జీరో ఫైవ్ ఫోర్"); 14/75 with dates, 12/75 with full. No documented way found to limit multi to hi+en.
- Caveats: synthetic TTS voices, not a phone mic; n = 1 run; latency differences are within noise. The owner's recordings (`python3 -m bakeoff.stt_keyterms record`) decide the default; stt.keyterms stays none until then.
- Owner decision: disputes and "already paid" on an overdue EMI now hear "the concerned team will look into it and arrange a callback" (D-35, prompt v10, code-owned escalation lines); no callback time or ticket number. 668 tests pass. v10 not yet run in evals or live.

## 2026-11-04 13:47 live call B002 (after v10) and the v10 full eval

- Live: "बाईस" heard as "Twice", "Bies.", "Bies." (confidence 0.67, 0.59); verified on "22nd July". Last four came back "Double 3, 5 7" (3357, not 3367): one real attempt used. Keyterms were still none.
- Changes: stt.keyterms none -> full (D-33 evidence + this call); "buys/bies/byes" before a month = 22 (V13); startup line `deepgram query: model=nova-3 language=multi keyterms=51 ...`; per-turn `langs` from Deepgram (multi only). 680 tests pass.
- v10 eval (results/20261104-133125-v10-all5, all 17 scenarios x5, resumed after the session ended at 42/85): 75/85 pass, 0 leaks, escalation precision 0.93 recall 1.0 (fp 2). S13 0/5 (vague date), S14a 2/5, S04 4/5, S04b 4/5. Agent tokens 667,151 in / 9,647 out (this half only). Failures not yet reviewed.

## 2026-11-04 afternoon: v10 baseline, two fix rounds, judge, dashboard

- Baseline (all 17 x5, 20261104-133125-v10-all5): 76/85 re-scored (75/85 as first scored), dev 59/60, hold-out 17/25, 0 leaks, escalation P 0.93 R 1.0. Judge (glm5.3-flash, ~79k in / 38k out tokens): politeness 4.93, empathy 3.06, firmness 4.55, clarity 4.27, naturalness 4.19; spot check: 6 of the 9 lowest scores punished correct refusals (low trust).
- Fix round 1 (8 scenarios x5, 20261104-135904-v10-fixes): 36/40 re-scored. Found V14 (Hindi number words in promise dates) and V15 (hedged-answer lockout).
- Fix round 2 (S02 S04 S13 S14a S15 x5 in 20261104-140824-v10-fixes2; S10 S14a x5 in 20261104-141354-v10-fixes3 after V16): 28/30; S13 5/5; S14a 3/5 with both failures the simulator improvising "already paid". 0 leaks in all 155 conversations. 706 tests pass.
- Latency, current stack (all 4 Nov calls, 46 turns): gap median 1020 ms, p90 2393 ms. Median target met, p90 missed.
- New: evals/judge.py, evals/report.py, evals/dashboard.py (results/dashboard.html), evals/failure_review.yaml, docs/eval-report.md, run.sh.

## 2026-11-04 15:00 no dead air on tool turns (D-36)

- Measured from the 13:46 live call log: the two slow turns were the tool turns ("22nd July" verify_identity 1802 ms; "Using UPI?" record_outcome 2433 ms to first sentence); the other six turns 284-710 ms. First turn of a call is no longer cold (8 of 9 calls 530-632 ms).
- Change: tool_start event from the stream; a bridge line per tool in the caller's language; latency record marks bridge turns.
- One real Sarvam tool turn (text mode, B002, "मैं सात नवंबर को pay कर दूँगी"): bridge at 1825 ms, tool call complete 2215 ms, reply 2563 ms. Gain ~740 ms on that turn (n=1). Sarvam needs ~1.8 s before it starts a tool call; the bridge cannot come sooner without guessing. 713 tests pass. Not yet tried in a live voice call.
- 15:15 D-37 / C13, prompt v11: "what is this about?" answered before verification (account / personal matter, never the loan). First version asked a relative for the digits (v11-purpose S06 run 2); made conditional on who answered. v11-purpose2: S01, S04b, S06 2/2 each after a scorer fix (a correct "personal matter" answer was scored as unanswered). Bridge ack shortened to "जी।"/"Okay." after it doubled with the model's "समझ गई". 724 tests.
- 15:05 D-38 / C14, prompt v12: late_fee (Rs 500 on overdue loans) said once + direct pay-online ask. v12-latefee (S01 S02 S03 S15 x3): 12/12, fee once on all 9 overdue calls, 0 on upcoming, never repeated after hardship. C15: 4 invented-notice sentences in 3/12 ("this call is being recorded"...), now dropped by code. 743 tests.
- 15:40 prompt v13 (C16 hardship tone), C17 (no self-scheduled callback after any team escalation), escalation bridge "I hear you.", E8 bot-question scorer by proximity. Labelling set cut to 10 critical calls (owner). 760 tests.
- 15:50 v13-hardship2 (S03 S11 x3): 6/6, 0 callbacks scheduled after escalation, hardship replies open with sorry + thanks. Model still added "Is there anything else?" (S03 2/3, S11 3/3) and repeated the warm close (S03 run 2): code now drops "anything else" in hardship and any second "Please take care". Known open: "the concerned team" mixed into 2/3 hardship calls. 761 tests.
- 16:20 prompt v14 / D-39 / C18 special situations. v14-special (S03 S04 S04b S11 S15 x3): 15/15, 0 "colleague"/"concerned team". v14-special2/3 (S03 S04 S04b x2 each): all pass; fixed the not-my-loan question in amount disputes (code picks guidance from what was said), hand-off once, staged guidance (turns 5-7 -> 2-4 sentences), Hindi escalation bridge "जी।". Open: OTP advice leaked into one amount dispute (prompt rule added). 777 tests.
- 16:45 owner live hardship call B007 (16:03): apology cut 250 ms in and never heard; reply repeated to a fragment; English flip; contact question twice; guard false positive ("can't promise or hint at any waiver"); bridge flag bug (C19-C21). Fixes: unheard replies (cut within 1 s) re-say their key point; unfinished wait 0.4 -> 0.7 s (she resumed 26 ms after 0.4 s); split-sentence language judged whole, Hinglish rule (3+ Devanagari words and a fifth); neutral words (yes/ok/thank you/PM); once-per-call apology/hand-off/contact/short-term/warm close, forgotten if unheard. Replay of the call (real Sarvam, text): Hindi throughout, apology once and heard, hand-off once. 791 tests. Not yet tried live.

## 2026-11-04 16:30-16:50 prompt v15/v16: hardship and disputes as a code-owned flow (D-40)

- v15: owner wording (no "whatever is comfortable" / "take care"; close "आपका दिन शुभ हो"), C22.
- v16 / special.py: one step per turn by code. Runs, S03 S04 S04b S11 x3 each (Sarvam agent + glm5.3-flash simulator, ~Rs 4 each by the runner's estimate, not billed figures):
  - v16-special (20261104-163436): 12/12; read: repeated "can't change", curt "Okay." close, waiver demand on the amount script, last request ignored.
  - v16-special2 (20261104-163903): 11/11 + 1 infra (Sarvam timeout); found promise-guard false positives on read-backs (C25).
  - v16-special3 (20261104-164251): 12/12; turns still ran past the question; model copied the example close line.
  - v16-special4 (20261104-164548): 12/12, 0 leaks, escalation P 1.0 R 1.0; one "colleague" hit is the agent repeating the borrower's own words (scorer false positive).
- Pace on bulbul:v4-flash (REST, same Hindi sentence): speech 4.1-4.5 s at 1.0, 4.7-5.1 s at 0.9, 5.6 s at 0.8; one 0.9 render had 4.3 s extra silence. n=3 per pace.
- Not yet tried live: pace switch over the websocket, 1.2 s hardship wait, the whole flow by voice.

## 2026-11-04 17:00-17:45 owner live call B004 and prompt v17 (D-41..D-43)

- Live call B004 16:57 (logs/bot-B004.log): invented "five hundred rupees has been added" on a loan not yet due; "due थी"; split question, then closed on "I need to pay" as will_pay (C28-C30).
- v17: generic policy (data/policy.yaml) in the prompt + get_policy tool; amount and fee-claim checks; due-status note; split-sentence note; already_paid / will_pay checks; hardship and dispute gates; greeting "AI assistant"; "account verification"; gender-neutral Hindi questions; everyday words. New scenarios S16-S18 (borrower questions).
- Runs (Sarvam agent + glm5.3-flash simulator; rupee figures are the runner's estimates, not bills):
  - v17-policy (20261104-171941) 11/11 + 1 infra; v17-policy2 (172312) 12/12; v17-policy3 (172539) 5/6 (S16: mild money worry escalated as hardship); v17-policy4 (172807) 9/10 (S17 already_paid).
  - v17-dev2 (173050, all 15 dev scenarios x2, ~Rs 11): 27/30 as scored; S08 x2 hardship false positive (fixed: C33), S10 run 1 hedged loop (open, C34).
  - v17-dev2-fix (173559), fix2 (173754): S08 still escalating (dispute, other), found E9.
  - v17-gates (174021, S03 S04 S04b S08 S11 x2): 9/10 as scored, 10/10 re-scored after E10; escalation P 1.0 R 1.0.
- Not yet: live voice call on v17; hold-out run; S10 hedged-answer loop.

## 2026-11-04 18:00-18:25 owner live call B004 (v17), LLM judge, fixes

- Owner live call B004 17:59 (results/latency/20261104-175918-B004.jsonl): late fee answered correctly (₹500 if paid after the due date), "account verification", no invented charge. Gaps n=8: 720-2127 ms, median ~1040 ms. Problems C36.
- Judge rubric v3 (E11). sarvam glm5.3-flash, UNCALIBRATED (owner labels still pending):
  - v17-dev2 (30 conv): politeness 4.90, empathy 3.52, firmness 4.88, clarity 4.96, naturalness 3.90; 0 flags. Found C35 (reasoning spoken).
  - v17-dev3 (20261104-181142, all dev x2, 26/29 + 1 infra; S10 x2 = C34, S12 run 2 = C37): politeness 4.93, empathy 3.63, firmness 4.58, clarity 4.88, naturalness 3.79; 0 flags.
  - v17-dev3-fix (181920, S02 S08 S12 S16 S17 x2, 9/10): politeness 5.00, empathy 2.88, firmness 4.88, clarity 5.00, naturalness 4.30 (n=8).
  - v17-final-check (182146, S01 S05 S16 x2): 6/6.
- Lowest judge dimension is empathy: the agent states facts before acknowledging a worry. Open.

## 2026-11-04 18:40 judge calibration (owner labels on the v17 sample)

- 10 labelled, 9 usable (S17 run 1 left blank). results/judge_calibration/20261104-183858.md.
- Overall within one point: sarvam glm5.3-flash 78%, groq gpt-oss-120b 73%; gemini-3.5-flash failed (provider 503, high demand). Exact agreement 11-44%.
- The judge is lenient: judge minus owner, mean: clarity +1.17, naturalness +1.11, politeness +0.56, firmness +0.50, empathy -0.22. Largest gap S12 run 1 (stop calling): owner naturalness 1, judge 5; S08 run 1: owner 2, judge 4.
- n/a mismatch: the rubric says firmness/clarity "na" when no reminder could be given; the owner scored those 3-4.
- So judge tone scores overstate naturalness and clarity by about one point; report them with this offset and n=9.
- Fixed on the way: judge waits for Groq's 429 retry time; one failing judge no longer stops calibration.
