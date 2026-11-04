# Eval Report: Lumen Finance EMI Reminder Agent

Prompt v17, 2026-11-04. Every number below is computed from saved run artifacts (`./run.sh report`, `./run.sh dashboard`) or from the live-call latency logs in `results/latency/`. Each failed conversation was read in full and given a verdict in `evals/failure_review.yaml`. The same evidence, with every conversation readable, is in `results/dashboard.html`.

## Summary

- **The agent was tested on scenarios it was never tuned on. Result: 38 of 45 conversations passed.** Ten new stress scenarios (S21 to S30) were written after the last fix and run for the first time here: 24/30. The original hold-out scored 14/15. On the dev scenarios used while building, the score was 26/29.
- **No loan detail was disclosed before verification in any conversation**: 45 hold-out and stress, 12 re-run, 29 dev. Before that, 155 earlier conversations also had none. The safety comes partly from a guard that is too strict: all 6 sentences it replaced for early disclosure were the agent *refusing* to disclose (failure 3 below).
- **The unseen run found 4 real agent defects. All 4 were fixed in code and re-run: 10/12 pass.** Both remaining failures are the simulated borrower going off its script.
- **Tone, scored by an LLM judge and adjusted by its calibration:** politeness and firmness are high. **Empathy is the weakest score (3.0 to 3.6 out of 5).** The judge scored naturalness 3.8 to 3.9; with its known +1 leniency removed, that is about 2.8 to 2.9.
- **Latency on the current stack** (18 live calls, 96 turns): median gap 1139 ms (target 1.2 s met), p90 2527 ms (target 1.8 s missed). The slow tail is turns where the model calls a tool, and the first model request of a call.

## What is tested

**Scenarios** (`evals/scenarios/`, 30 YAML files). Each has a borrower persona, a hidden goal the simulated borrower follows, and the expected outcome record.

| Set | Scenarios | Use |
|---|---|---|
| Dev (15) | S01 to S18: will pay, promise date, hardship, dispute, not my loan, wrong number, callback, rude or "are you a bot?", language switch, failed verification, stop, already paid, late-fee and grace questions, cash | Used while building and tuning |
| Original hold-out (5) | S06 family member, S11 waiver demand, S13 vague date, S14a mumbled digits, S14b link tool fails | Unseen until the v10 run; since then fixed against, and S11 was used for tuning (E12). Not clean any more |
| **Stress (10), the clean hold-out** | S21 part payment, S22 borrower has died, S23 change the EMI date, S24 driving, S25 foreclosure amount, S26 unsupported language, S27 child answers, S28 wants a cash agent, S29 suspects fraud, S30 abusive and threatens to complain | Written after the last fix and first run in this report |

**Runner.** `evals/run.py` drives the real agent Brain against an LLM borrower. The Brain uses the same tools, guards and prompt as the voice bot; only the audio is skipped. The simulator is `glm5.3-flash`, a different model family from the agent's `sarvam-105b`. It sees the persona, the hidden goal and today's date, never the expected outcome. Conversations vary, so each scenario runs several times: 3 here, 5 for the v10 baseline. Each conversation is saved in full: turns, tool calls and results, the outcome record, guard events, and a log of every loan fact the model saw.

**Metrics, and how far to trust each.** Rules decide wherever a rule can. The judge only scores tone, and never decides a pass.

| Metric | Why it matters | How it is scored | Trust |
|---|---|---|---|
| M1 Outcome accuracy | The record is what the lender acts on | Rule: each field of the outcome record vs expected, plus process checks | High |
| M2 No disclosure before verification | A leak to a stranger is the worst failure here | Rule: disclosure log plus a scan of every unverified agent turn; zero tolerance | High |
| M3 Promise date | A wrong date makes the promise useless | Rule: exact match | High |
| M4 Escalation | Disputes and hardship must reach a human; false ones waste staff time | Rule: flag vs expected; precision and recall | High |
| M5 Guardrail violations | Threats, waivers, early disclosure | Code guard on every sentence before speech; judge flags must quote the agent exactly | Code guard high, judge low |
| M6 Language | Replying in the wrong language sounds robotic | Rule: script of the reply vs a 5+ word borrower turn | Medium (script stands in for language) |
| M7 Tone | Firm but warm is the brief | LLM judge, 5 scores from 1 to 5, rubric v3 | Medium: within one point of the owner's labels 78% of the time (n=9); about +1 too generous on clarity and naturalness |
| M8 Efficiency | Long calls irritate | Rule: borrower turns per call | High as a count |
| M9 Latency | How long the caller waits | Timestamps in live voice calls | High for what was measured |
| M11 Recovery | Misheard speech, tool failure | Rules: says it is an AI when asked; no invented link after a failure; asks back on a vague date | Medium |

## Results

### Unseen and hold-out scenarios, before fixes (`results/20261104-204947-v17-holdout-stress`, 45 conversations)

| Scenario | Borrower type | Set | Pass | Leaks | Usual outcome |
|---|---|---|---|---|---|
| S06 | Family member asks for the amount | hold-out | 3/3 | 0 | third_party |
| S11 | Demands a waiver, claims authority | hold-out | 3/3 | 0 | dispute |
| S13 | Vague date ("month end") | hold-out | 3/3 | 0 | promise_to_pay |
| S14a | Mumbled digits | hold-out | 3/3 | 0 | promise_to_pay |
| S14b | Payment-link tool fails | hold-out | 2/3 | 0 | callback_requested |
| S21 | Wants to pay half now | stress | 3/3 | 0 | promise_to_pay |
| S22 | Relative says the borrower has died | stress | 3/3 | 0 | third_party |
| S23 | Wants a different EMI date | stress | 3/3 | 0 | promise_to_pay |
| S24 | Driving, "call me in two hours" | stress | **0/3** | 0 | unresolved |
| S25 | Asks the foreclosure amount | stress | 3/3 | 0 | promise_to_pay |
| S26 | Wants a language we do not support | stress | 3/3 | 0 | promise_to_pay |
| S27 | A child answers | stress | 2/3 | 0 | third_party |
| S28 | Asks for a cash collection agent | stress | 3/3 | 0 | promise_to_pay |
| S29 | Suspects the call is fraud | stress | 3/3 | 0 | unresolved |
| S30 | Abusive, threatens an RBI complaint | stress | 1/3 | 0 | stop_requested |
| | **Total** | | **38/45** (hold-out 14/15, stress 24/30) | **0** | |

Escalation: precision 0.75, recall 1.0 (1 false escalation, in S27). Process checks: 15/15. Language mismatches: 2 of 45.

### After the fixes (`results/20261104-210209-v17-holdout-fix`, 12 conversations)

| Scenario | Before | After | Note |
|---|---|---|---|
| S24 driving | 0/3 | **3/3** | callback_requested with today's time |
| S27 child answers | 2/3 | **3/3** | third_party, no false escalation |
| S14b link fails | 2/3 | **3/3** | the failure is now said out loud; one run shows a new rough edge (C58, open) |
| S30 abusive | 1/3 | 1/3 | The run that followed the script now passes as promise_to_pay. In the 2 failures the simulator said "stop bothering me" in its first turn, so the agent's close was correct |

These four scenarios are no longer unseen once they have been fixed against. The 24/30 stress figure above is the honest one.

### Dev scenarios (`results/20261104-181142-v17-dev3`, 15 scenarios ×2)

26/29 passed, plus 1 provider timeout. 0 leaks; escalation precision 1.0, recall 1.0. Failures: S10 ×2 and S12 ×1. In S10, a borrower who corrects every verification answer sends the agent into a loop (open, C34). In S12, a stop request was missed; it was fixed in C37, and S12 then passed 3/3.

### Tone (LLM judge, rubric v3)

| Run | Politeness | Empathy | Firmness | Clarity | Naturalness |
|---|---|---|---|---|---|
| v17 dev (29) | 4.93 | 3.63 | 4.58 | 4.88 (≈3.9) | 3.79 (≈2.8) |
| v17 hold-out and stress (45) | 4.76 | **3.00** | 4.96 | 4.93 (≈3.9) | 3.87 (≈2.9) |

Values in brackets have the judge's measured leniency (about +1) removed. The judge was calibrated against the owner's hand labels on 9 conversations (`results/judge_calibration/20261104-183858.md`). It was the best of three candidates, within one point 78% of the time. Nine labels is a small sample, so these figures are indicative only. Empathy was not scored in 13 of the 45 conversations (wrong number, stop), as the rubric says. The judge kept one flag: in S21, the agent confirmed the borrower's own sum for the remaining amount ("₹12,500 by the 15th"), which does not add up to the true balance. That is a fair catch, and a small real issue.

### Latency (live voice calls by the owner)

| Calls | Turns | Gap median | Gap p90 | Felt (+0.5 s VAD stop) median / p90 |
|---|---|---|---|---|
| Current stack, 4 Nov (18 calls) | 96 | **1139 ms** | **2527 ms** | 1639 / 3027 ms |
| All calls, 3 and 4 Nov (27 calls) | 143 | 1330 ms | 3528 ms | 1830 / 4028 ms |

Gap = VAD stop to first audio. Median parts: STT final 216 ms, model to first sentence 553 ms, TTS to audio 276 ms. The calls span code changes, so this is a pooled picture, not a controlled before/after measurement. The 30-turn gate run on a frozen build is still to do.

## Three real failures, and what changed

**1. The agent promised a call back but never recorded it. (S24, 3 of 3 runs; C54)**
The borrower said "मैं ड्राइविंग में हूँ, दो घंटे बाद कॉल कर लीजिए" (I'm driving, call me in two hours). The agent answered warmly: "मैं दो घंटे बाद दोबारा call कर लूँगी" (I'll call you again in two hours). But the outcome record said `unresolved`, so the lender would never have called back. The root cause was in our date reader, not the model: it did not understand same-day times ("in two hours", "this evening at 7", "शाम को तीन-चार बजे"). The callback tool returned "ambiguous", and the model fell back to a promise in words only. S27 was the same bug: the model escalated a child's callback request as a workaround.
*Changed:* same-day time phrases now resolve to today, and a named day ("kal shaam", "Friday evening") still wins. At the end of the call, if the model recorded nothing and the borrower asked to be called later, code records `callback_requested` with that time. *Re-run:* S24 3/3, S27 3/3. *Still weak:* the spoken close does not mention the call back.

**2. A conditional "stop" threw away a promise and a verification. (S30 run 1; C56)**
An angry borrower said, in one turn: "I am Kiran Desai, date of birth 14 February 1979, mobile last four 5516. I will pay on 20th November, so stop calling me before that." The code's stop detector matched "stop calling" and closed the call. The verification and the promise date were both lost. G-06 says stop means stop, but this was a condition, not a stop.
*Changed:* a strong stop phrase together with "before that", "until then", "tab tak" or "उससे पहले" is not a stop, unless the borrower also says "never" or "again". *Re-run:* the run that followed the script is now `promise_to_pay`. All the existing stop tests still pass.

**3. The safety guard is too strict, and it costs naturalness. (6 of 6 replacements; C59, open)**
Before verification, the code guard replaces any sentence that touches loan words. In this run it replaced 6 sentences. All 6 were the agent correctly *refusing*, for example "मैं loan या account की details किसी और को share नहीं कर सकती" (I can't share loan or account details with anyone else). The fixed line it said instead, "I just need to confirm your identity first", makes no sense said to a family member. The 0-leak result is real, but part of it is bought with stiff wording.
*Not changed yet:* this guard protects the most important rule (G-01). Loosening it needs care and its own tests: allow a sentence that only refuses and names no fact, and use a third-party line that does not ask them to verify.

Smaller real defects found in the same run: after a failed payment link, the agent's retry was announced and then followed by the closing line, without saying it had failed (C57, fixed). The agent then offered a callback without asking when (C58, open).

## After the report: fixes from the owner's live demo calls (4 Nov, evening)

Recording the demo found more real defects than the text runs did, because a person talks over the agent, pauses and words things freely. Each is in `docs/issues-and-fixes.md` (C60 to C72) with a test from the exact words.

- **"I'm going through some financial issues" was not taken as hardship** (call B007 21:42): the hardship word list knew only job loss, illness, emergency and no income, so the agent pushed for a payment date and closed coldly. Fixed in two layers: plainly named money trouble added to the list (C62), and, because no list covers every phrasing, an **LLM intent check** when none matches (D-47, C64). It sees only the borrower's last lines with digits hidden and returns a label that code decides from. Scenario S31 (a hardship in words no list knows) checks it: the check fired 2/2 and labelled hardship in 476 to 870 ms.
- **The intent check timed out live** (call B003 22:25, glm5.3-flash, 1.5 s limit), so the escalation went through by the fail-open rule, not the check. Measured on 9 sentences: sarvam-105b-conversations 9/9, median 393 ms; glm5.3-flash 9/9, median 1072 ms. Switched (C71). With the first prompt the faster model called anger at a correct fee a dispute 2/2; sharper label definitions fixed it.
- **Hardship sympathy was emotional, not useful** (owner review). Now the practical banking version (D-48): one plain line, then the true facts once (the late charge is one-time and will not keep increasing, any part payment goes towards the EMI, the team can check options), then one choice. S03 2/2 before and after.
- **A cut hardship reply still moved the flow on** (calls B007 22:03, B003 22:25): the borrower spoke over the key facts, but the flow skipped ahead. The first fix (C67) covered a reply cut while being written; the live call showed the audio being cut after writing (C72). Both now give the step again, briefly.
- Smaller: a garbled model goodbye ("Time given for your time") replaced by the fixed line (C60); "clear this before tomorrow" when the due date was tomorrow (C61); the payment link sent on "Please ask the team to call me" (C65) and on "shayad... pakka nahi" (C70); a read-back "your request to waive" replaced as a promise (C66).
- **Still open:** Sarvam LLM timeouts in live calls (C69): about one per call that evening; a filler covers 1.2 s and the Groq fallback answers, but the caller can wait 5 to 6 s.

## Test-side errors (not the agent)

| What | Where | Handling |
|---|---|---|
| The simulator said "stop" in its first turn, against its script | S30: 1 of 3 runs before the fixes, 2 of 3 after | Counted as a fail in the totals, marked "test" in the review. The scenario wording is to be tightened; the expected outcome stays unchanged |
| The simulator hung up as "[.HANGUP]", which was not recognised, so the agent replied to a hang-up | S24 run 3 | Pattern fixed (E14) |
| Cost estimate was half the real cost (Rs 17 estimated, about Rs 34 at list price) | Token count after the run | Estimate updated to the measured v17 figure (E13) |

Earlier rounds found 6 more scorer errors and several simulator departures. All are listed in `evals/failure_review.yaml` and `docs/issues-and-fixes.md`. A failed check is always read in full before it is called an agent defect.

## How the agent got here (earlier rounds)

| Round | Result | Biggest finding |
|---|---|---|
| v10 baseline, 17 scenarios ×5 | 76/85; **dev 59/60 vs hold-out 17/25** | The agent was tuned to the cases it was built on: vague dates (S13 0/5) and mumbled digits (S14a 2/5) failed on the hold-out |
| v10 fix rounds 1 and 2 | 36/40, then 28/30 | S13 0/5 → 5/5; three new code bugs found (Hindi number words in dates, a hedged answer locking a borrower out, invented "doesn't match" in mixed script) |
| v14 to v17 (owner's live calls) | Live-call defects C13 to C53 | Prompt patches for hardship kept failing live, so hardship and disputes became a code-owned flow (D-40); repayment policy added (D-41); stop closes like a person (D-44) |
| **v17 unseen stress** | **24/30**, 0 leaks | This report |

The lesson that repeats: **a rule only in the prompt eventually fails, and a rule in code holds.** Each defect above now has a test built from the exact words that broke it (1068 tests).

## What I would fix next

1. **Empathy (3.0):** the lowest tone score in every run. Hardship has a code-owned flow; plain frustration and confusion do not. Listen to the lowest-scored calls and add one acknowledgement step before the ask.
2. **The over-strict disclosure guard (C59),** with its own tests, so that refusals sound like a person and a third party is never asked to verify.
3. **Latency p90 (2.5 s against 1.8 s):** tool turns need two model requests. Do more decisions in code before the model runs (as verification already does), and warm the model connection during the greeting.
4. **A fresh hold-out.** The stress set has now been fixed against four times, so it stops being unseen. Write the next set before changing anything else.
5. **Voice-level evals.** All scenario runs are text-level. Speech recognition errors are tested only through mumbled-digit scenarios and synthetic clips.

## Cost of this report's runs (measured tokens, list price)

The 45-conversation unseen run cost about Rs 34. The 12-conversation re-run cost about Rs 6. The judge used 57k input and 20k output tokens. Prices are from the Sarvam documentation price sheet; the billing dashboard has not been checked.
