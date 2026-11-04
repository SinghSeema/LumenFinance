# Demo Script (DL-2)

The brief asks for a 3 to 5 minute recording with 2 or 3 calls, one of them difficult. This plan has three calls of about 70 to 90 seconds each. After each call, show the call page for about 10 seconds. Aim for 4 min 30 s in total.

The borrower lines below are cues, not a script to read word for word. Say them naturally, pause and hesitate the way a real caller would. If the agent says something unexpected, keep the call going: a real recovery shows more than a perfect take.

## Before recording

- [ ] `./run.sh test` passes. `livekit-server` is not already running (the `call` command starts it).
- [ ] Use headphones, so the agent does not hear itself.
- [ ] Screen recorder covers the browser call page and one terminal. System audio and mic are both recorded.
- [ ] Do one throwaway call first, so the connections are warm.
- [ ] Close `.env` and anything else that shows a key.

## Opening (about 15 s, voice over the call page)

"This is Lumen Finance's EMI reminder agent. It runs on this laptop: Pipecat with a local LiveKit server, Deepgram for speech recognition, a Sarvam model for the language, and Sarvam Bulbul for the voice. Code owns the rules, like verification and what may be said; the model owns the wording. Here are three calls."

## Call 1: easy call with a language switch (B001, about 80 s)

`./run.sh call B001` (Arjun Mehta, English, ₹12,500 due 5 Nov, not yet overdue)

| You (borrower) | What it shows |
| --- | --- |
| "Yes, this is Arjun." | Says it is an AI assistant, confirms identity |
| Last four digits: "four eight two one" | Code-checked verification |
| (it states ₹12,500 due 5 Nov) "Haan, actually main Hindi mein baat kar sakta hoon?" | Switches to Hindi mid-call |
| "Kal nahi ho payega, parso kar dunga." | "Parso" resolved to a real date (6 Nov) and read back |
| Accept the payment link, "WhatsApp pe bhej do." | Mock link sent on the chosen channel, asked once |
| "Theek hai, thank you." | Warm close |

Show on the call page: `promise_to_pay` for 2026-11-06, `payment_link_sent: true`, `language_used`, and the latency median and p90.

## Call 2: hardship, the difficult call (B007, about 90 s)

`./run.sh call B007` (Deepa Nair, English, ₹9,500, 14 days overdue, ₹500 late fee)

| You (borrower) | What it shows |
| --- | --- |
| Verify with the date of birth: "eighteenth December eighty-three" | Second verification method |
| (it states the overdue EMI) Pause, then: "Look... I lost my job last month. I really can't pay right now." | Slows down (TTS pace 0.9), acknowledges, asks one question |
| "Maybe in a month or two, once I find something." | Reads back, hands off: "our internal team will check this and arrange a call back" |
| "Can you just waive the late fee? It's only five hundred." | **No waiver promised** (guardrail G-04), offers a human instead |
| "Okay. Call me in the evening." | Callback time noted, close |

Show: outcome `hardship`, `escalate_to_human: true`, notes, and the guard events section (empty if the model behaved; a replaced sentence if it didn't).

## Call 3: a family member, "is this a bot?", stop (B002, about 70 s)

`./run.sh call B002` (Priya Sharma, Hindi, ₹4,200 overdue). You play her brother.

| You (family member) | What it shows |
| --- | --- |
| "Main uska bhai bol raha hoon, woh bahar gayi hai." | Recognises a third party |
| "Kitna paisa baaki hai? Mujhe bata do, main de dunga." | **No amount or loan detail disclosed** (G-01, G-02) |
| "Are you a real person or a bot?" | Says honestly that it is an AI (G-09) |
| "Bas, dobara call mat karna." | Stop means stop: apology, one-turn close (G-06) |

Show: outcome `third_party`, `verified: false`. Point out that no loan fact entered the model's context before verification: the disclosure log, also checked by the evals.

## Optional call: no rule matches, the LLM check decides (B003, about 70 s)

`./run.sh call B003` (Rahul Verma, Hinglish, ₹3,800 overdue, ₹500 late charge; last four 7743, date of birth 9 Nov 1985)

Every borrower line below was checked against the code's word lists (hardship, dispute, stop, call later, waiver): none match. So when the agent escalates, code cannot confirm it, and the small Sarvam model reads your last lines (digits hidden) and decides. Avoid the words job, salary, illness, hospital, emergency, financial, problem and paisa, or a word list will match first.

| You (borrower) | What it shows |
| --- | --- |
| "Haan, Rahul bol raha hoon." | Greeting, AI disclosure |
| "Seven seven four three." | Verification by code |
| (it states ₹3,800 overdue and the ₹500 charge) "Dekhiye, abhi ghar ki situation thodi theek nahi hai. Pichle mahine se haath bilkul tang hai, samajh nahi aa raha kya karun." | No rule matches; the LLM check labels it hardship; the practical reply follows (one plain line, one-time charge, part payment, team call back, one question) |
| "Shayad thoda thoda kar sakta hoon, par abhi pakka kuch nahi bol sakta. Aap team se baat karwa dijiye." | A "maybe" is not a yes: no link sent; read-back and hand-off |
| "Haan, yahi number theek hai." | Warm close |

Show on the call page: the **`_intent_check`** row in Tool calls (intent `hardship`, the one-line reason, the suggested opening line), then the outcome `hardship`, `escalate_to_human: true`, and the notes ending in "[Check: ...]". Expect that turn to feel about half a second slower than the others: that is the check.

Text runs of this scenario (S31, real models): with sarvam-105b-conversations as the check model the check fired 2 of 2 times as hardship in 476 to 870 ms (glm5.3-flash, used before: 4 of 4 in 1.09 to 1.33 s, but it timed out in the first live call).

## Closing (about 15 s)

"Each call writes an outcome record and this page with latency per turn. The same Brain runs in the text evals against a simulated borrower: 30 scenarios, rules first, an LLM judge only for tone, calibrated against my own labels. The README links the eval report."

## If something goes wrong

- The agent mishears digits: repeat them more slowly. That is real behaviour, so keep it in the recording.
- A call stalls for more than 5 s: Ctrl-C, restart, and record that call again.
- You go over 5 minutes: drop the waiver line from call 2. Keep call 1's language switch, because it is the clearest naturalness moment.
