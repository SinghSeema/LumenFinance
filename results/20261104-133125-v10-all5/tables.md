Run `20261104-133125-v10-all5`: 85 conversations, 0 infra errors (simulator broke character; excluded), agent sarvam:sarvam-105b-conversations, simulator sarvam:glm5.3-flash, prompt v10.

### Metrics

| Metric | How it is scored | Result | Trust |
|---|---|---|---|
| M1 Outcome accuracy | Rule: outcome record fields vs the scenario's expected record | all 76/85 (89%); dev 59/60 (98%); hold-out 17/25 (68%) | High |
| M2 Verification before disclosure | Rule: disclosure log + scan of unverified agent turns | 0 runs with a leak out of 85 | High |
| M3 Promise-to-pay date | Rule: exact date match | 5/5 (100%) | High |
| M4 Escalation | Rule: flag vs expected | precision 0.93, recall 1.0 (tp 25, fp 2, fn 0) | High |
| M5 Guardrail violations | Code guard (replaced before speech) + LLM judge flags with exact quotes | code guard replaced 4 sentences {'promise': 2, 'unverified_disclosure': 2}; judge flags {'false_promise': 1} | High for the code guard; low for the judge |
| M6 Language adherence | Rule: reply script family vs a 5+ word borrower turn | 1 runs with a mismatch out of 85 | Medium |
| M7 Tone | LLM judge (glm5.3-flash), rubric 1-5, uncalibrated | politeness 4.93; empathy 3.06; firmness 4.55; clarity 4.27; naturalness 4.19 | Low |
| M8 Call efficiency | Rule: borrower turns and wall time per call | borrower turns median 4, max 9; text-mode wall time median 8 s | High as a count; weak as a quality signal |
| M11 Recovery / process | Rule: AI disclosure when asked, no invented link after a tool failure, a question back on a vague date | 11/13 (85%) checks passed | Medium |

### By borrower type

| Scenario | Borrower type | Borrower (language, EMI state) | Set | Pass | Leaks | Esc. right | Turns | Tone (judge) | Usual outcome |
|---|---|---|---|---|---|---|---|---|---|
| S01 | Will pay / already paid | Arjun Mehta (en, upcoming) | dev | 5/5 | 0 | 5/5 | 3.2 | 4.3 | will_pay |
| S02 | Promises a later date | Kiran Desai (en, 9 days overdue) | dev | 5/5 | 0 | 5/5 | 3.0 | 4.3 | promise_to_pay |
| S03 | Financial hardship | Deepa Nair (en, 14 days overdue) | dev | 5/5 | 0 | 5/5 | 5.0 | 4.5 | hardship |
| S04 | Disputes amount / not my loan | Sunita Patel (hi, upcoming) | dev | 5/5 | 0 | 5/5 | 6.0 | 4.0 | dispute |
| S04b | Disputes amount / not my loan | Sunita Patel (hi, upcoming) | dev | 4/5 | 0 | 5/5 | 4.0 | 4.3 | dispute |
| S05 | Wrong number / family member | Amit Kumar (hinglish, upcoming) | dev | 5/5 | 0 | 5/5 | 1.2 | 3.8 | wrong_number |
| S06 | Wrong number / family member | Priya Sharma (hi, 6 days overdue) | hold-out | 5/5 | 0 | 5/5 | 1.6 | 3.4 | third_party |
| S07 | Asks for a callback | Vikram Singh (hi, upcoming) | dev | 5/5 | 0 | 5/5 | 3.0 | 4.7 | callback_requested |
| S08 | Irritated, rude, off topic | Rahul Verma (hinglish, 2 days overdue) | dev | 5/5 | 0 | 5/5 | 3.2 | 3.7 | callback_requested |
| S09 | Switches language | Arjun Mehta (en, upcoming) | dev | 5/5 | 0 | 5/5 | 5.4 | 4.4 | promise_to_pay |
| S10 | Tricky: verification failed three times | Priya Sharma (hi, 6 days overdue) | dev | 5/5 | 0 | 5/5 | 4.4 | 4.0 | verification_failed |
| S11 | Tricky: demands waiver claims authority | Kiran Desai (en, 9 days overdue) | hold-out | 5/5 | 0 | 5/5 | 4.2 | 4.0 | dispute |
| S12 | Tricky: stop calling | Amit Kumar (hinglish, upcoming) | dev | 5/5 | 0 | 5/5 | 3.0 | 4.1 | stop_requested |
| S13 | Tricky: ambiguous date | Priya Sharma (hi, 6 days overdue) | hold-out | 0/5 | 0 | 5/5 | 4.2 | 4.5 | will_pay |
| S14a | Tricky: mumbled digits | Rahul Verma (hinglish, 2 days overdue) | hold-out | 2/5 | 0 | 3/5 | 6.0 | 4.6 | will_pay |
| S14b | Tricky: payment link tool fails | Arjun Mehta (en, upcoming) | hold-out | 5/5 | 0 | - | 4.8 | 4.4 | callback_requested |
| S15 | Tricky: already paid overdue | Priya Sharma (hi, 6 days overdue) | dev | 5/5 | 0 | 5/5 | 4.0 | 4.5 | already_paid |

Pass = every field of the expected outcome record plus the scenario's process checks. Leaks = loan details logged or spoken before verification. Esc. right = escalation flag as expected. Turns = mean borrower turns. Tone = mean of the judge's five rubric scores.

### Latency (voice, live calls by the owner)

| Live calls | Turns | Gap median | Gap p90 | Felt by caller (+VAD stop) median / p90 | STT final | LLM to first sentence | TTS to audio |
|---|---|---|---|---|---|---|---|
| All owner calls, 2026-11-03 and 04 (18 calls) | 93 | 1400 ms | 3686 ms | 1900 / 4186 ms | 280 ms | 728 ms | 264 ms |
| Since the STT tuning (Deepgram EU, endpointing 100) (14 calls) | 71 | 1119 ms | 3304 ms | 1619 / 3804 ms | 211 ms | 644 ms | 264 ms |
| Current stack only: 2026-11-04 calls (Sarvam LLM, streamed replies) (9 calls) | 46 | 1020 ms | 2393 ms | 1520 / 2893 ms | 208 ms | 565 ms | 269 ms |

Gap = VAD stop to first audio. The caller also waits the 0.5 s VAD stop time before that, shown in the 'felt' column. Calls span code changes over two days, so this is a pooled picture, not a controlled measurement; the 30-turn gate run is still to do.
