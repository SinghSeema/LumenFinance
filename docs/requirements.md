# Requirements

Functional requirements (F), quality requirements (Q), guardrail rules (G) and borrower response types (R). IDs are the cross-reference targets used in HLD.md, LLD.md, evals.md and the assignment-checklist.

## 1. Outcome record fields

The call ends with one `OutcomeRecord` written to `results/calls.jsonl`. Fields: `borrower_id`, `verified`, `outcome`, `promise_to_pay_date`, `payment_link_sent`, `escalate_to_human`, `notes`, `language_used`, `callback_time`. Schema and validation rules are in `LLD.md` section 5.

## 2. Functional requirements

| ID | Requirement |
| --- | --- |
| F-01 | The agent must confirm the borrower's identity before sharing any loan details. Identity check uses one factor: date of birth or last four digits of the registered mobile number. |
| F-02 | A maximum of three verification attempts are allowed. On exhaustion the call ends with outcome `verification_failed` and no loan detail was shared. |
| F-03 | After verification, the agent states the EMI amount and the due date (or overdue amount and how many days) in spoken form, naturally. |
| F-04 | The agent asks for the borrower's intent and responds to it: will pay, already paid, promise to pay with a date, hardship, dispute, callback request, or stop. |
| F-05 | If the borrower intends to pay, the agent offers to send a payment link via the `send_payment_link` mock tool and confirms the channel. |
| F-06 | The call closes politely with a summary of the outcome and a `record_outcome` tool call that writes the structured record. |
| F-07 | The agent handles at least the eight borrower response types R1 to R8. |

## 3. Quality requirements

| ID | Requirement |
| --- | --- |
| Q-01 | Latency: response gap (VAD stop to playback start) median under 1.2 s, p90 under 1.8 s, measured over at least 30 voice turns. |
| Q-02 | Barge-in: the agent stops speaking promptly when the borrower speaks, cancels queued TTS and uses the interruption as the next turn. |
| Q-03 | Turn-taking: the agent prompts a silent caller after `turn.silence_prompts_secs`, then closes and records a callback note after `turn.silence_close_secs`. |
| Q-04 | Numbers and dates are spoken naturally in the borrower's language; the model never speaks raw digits for amounts. |
| Q-05 | Filler lines play if a tool call takes longer than `behavior.filler_delay_ms`. |
| Q-06 | Tone: polite, warm, firm about the reminder, never shaming or pressuring. |
| Q-07 | Recovery: ambiguous or unclear input (mumbled digits, unclear date) is handled by reading back and confirming, never by guessing. |
| Q-08 | Language: the agent replies in the borrower's latest language (English, Hindi or Hinglish); a single off-language word does not flip the language, two consecutive turns do. |

## 4. Mock dataset

`data/borrowers.json` contains 8 borrowers validated by the `Borrower` Pydantic model (LLD.md section 15). Each record has: `borrower_id`, `name`, `dob`, `last4`, `loan_type`, `emi_amount`, `due_date`, `days_overdue`, `preferred_language`.

`dob` and `last4` never enter the model context. `preferred_language` sets the language for the first agent turn only.

## 5. Guardrail rules

These rules are enforced by code; the prompt is the second line of defence.

| ID | Rule |
| --- | --- |
| G-01 | No loan detail (amount, date, loan type) before verification passes. |
| G-02 | No loan detail to a third party or a wrong-number caller, even after they demand it. |
| G-03 | No threats, shaming, urgency pressure or consequences that are not real. |
| G-04 | No promises of waivers, discounts, restructuring, fee reversals or any special treatment. Offer a human agent instead. |
| G-05 | Disputes, hardship and explicit escalation requests must set `escalate_to_human = True` in the outcome. |
| G-06 | A stop request ends the call immediately and records outcome `stop_requested`. |
| G-07 | The agent stays on topic. Off-topic questions (politics, personal advice) are redirected once, then the call proceeds. |
| G-08 | Ambiguous or unclear dates must be clarified with exactly one question before any date is recorded. |
| G-09 | If the borrower asks whether they are speaking to a human or a bot, the agent says it is an AI. |
| G-10 | Context assembly must not include loan data before verification (defence in depth; enforced by `build_context`). |
| G-11 | Verification refusals are fixed strings; the agent never hints at the correct answer or discloses the number of characters. |
| G-12 | The outcome record must be internally consistent (see LLD.md section 5 consistency rules). |

## 6. Borrower response types

| ID | Response | Handled by scenario |
| --- | --- | --- |
| R1 | Will pay or already paid | S01 |
| R2 | Promises to pay on a specific date | S02 |
| R3 | Financial hardship | S03 |
| R4 | Disputes the amount or the loan | S04 |
| R5 | Wrong number or third party answers | S05, S06 |
| R6 | Requests a callback | S07 |
| R7 | Rude, off-topic or asks if it is a bot | S08 |
| R8 | Switches language mid-call | S09 |
