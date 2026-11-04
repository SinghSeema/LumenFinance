"""Output guard (LLD section 10)."""
import pytest

from emi_agent.guard import check, invented_verify_result

PROTECTED = ["twelve thousand five hundred rupees", "the day after tomorrow", "personal loan"]


def ok(sentence, verified=False, lang="en"):
    return check(sentence, verified=verified, protected=PROTECTED, lang=lang).category is None


@pytest.mark.parametrize("s", [
    "Hello, am I speaking with Arjun?",
    "I'm calling from Lumen Finance.",
    "Could you please confirm your date of birth?",
    "Thanks, and the last four digits of your registered mobile number?",
    "No problem, I'll call back at a better time.",
    "Kya main Arjun ji se baat kar rahi hoon?",
    "Yes, I'm an AI assistant.",
])
def test_unverified_safe_lines_pass(s):
    assert ok(s)


@pytest.mark.parametrize("s", [
    "Your EMI of twelve thousand five hundred rupees is due.",
    "The amount is 12500.",
    "It's about your personal loan.",
    "Your payment is due the day after tomorrow.",
    "It's due on the fifth of November.",
    "You are six days overdue.",
    "Aapki kisht barah hazaar ki hai.",
    "आपका लोन बकाया है।",
    "It's about one lakh.",
])
def test_unverified_disclosure_blocked(s):
    r = check(s, verified=False, protected=PROTECTED, lang="en")
    assert r.category == "unverified_disclosure"
    assert r.text != s


def test_verified_disclosure_allowed():
    assert ok("Your EMI of twelve thousand five hundred rupees is due the day after tomorrow.",
              verified=True)


@pytest.mark.parametrize("s", [
    "If you don't pay, we will take legal action.",
    "The police will come to your house.",
    "We'll send a recovery agent.",
    "You'll be blacklisted.",
    "Your family will find out.",
    "Warna police aa jayegi.",
    "वरना रिकवरी एजेंट घर आएंगे।",
    "आपको ब्लैकलिस्ट कर दिया जाएगा।",
    "हम आपके परिवार को बता देंगे।",
])
def test_threats_always_blocked(s):
    assert check(s, verified=True, protected=PROTECTED, lang="en").category == "threat"


@pytest.mark.parametrize("s", [
    "I can waive the late fee for you.",
    "We can give you a discount.",
    "I'll restructure the loan.",
    "Don't worry, we'll reverse the fee.",
    "Penalty maaf kar denge.",
    "मैं आपकी पेनल्टी माफ़ कर दूंगी।",
    "ब्याज माफ हो जाएगा।",
    "आपको छूट मिल जाएगी।",
    "हम आपकी किस्त कम कर देंगे।",
    "हम लोन रीस्ट्रक्चर कर सकते हैं।",
    "आपको माफ़ी मिल जाएगी।",
    "I cannot wait to give you a discount.",  # "cannot wait" is not a refusal
    "I can't believe it, but we can waive the fee.",
    "मैं छूट दे दूंगी, चिंता की कोई बात नहीं।",  # नहीं is in another clause
    "आपको छूट मिल जाएगी और कोई दिक्कत नहीं होगी।",  # नहीं not followed by a refusal verb
])
def test_promises_always_blocked(s):
    assert check(s, verified=True, protected=PROTECTED, lang="en").category == "promise"


@pytest.mark.parametrize("s", [
    "I'm not able to waive any fees, but a colleague can review it.",
    "I can't offer a discount myself.",
    "I’m sorry, but I can’t discuss waivers or changes to the loan.",  # curly apostrophes
    "Maaf kijiye, I didn't catch that.",
    "माफ़ कीजिएगा, आवाज़ साफ़ नहीं आई।",
    "माफ़ी चाहती हूं, मैं दोबारा पूछती हूं।",
    "मैं छूट नहीं दे सकती, पर colleague से बात करवा सकती हूं।",
    "मैं माफी चाहती हूँ, लेकिन मैं किसी भी तरह की छूट या बदलाव का वादा नहीं कर सकती।",  # S03 r3
    "I’m sorry, but I cannot promise or suggest any waiver, discount, or fee reversal.",  # S03 r4
    "I'm not able to offer you any kind of discount or waiver.",
])
def test_negated_or_unrelated_not_blocked(s):
    assert ok(s, verified=True)


def unverified(sentence, heard=""):
    return check(sentence, verified=False, protected=PROTECTED + ["12500"], lang="en",
                 heard=[heard] if heard else []).category


@pytest.mark.parametrize("s, heard", [
    ("Aapne last four digits 48 bataye, kya yeh sahi hai?", "haan 48"),
    ("Just to confirm, that's 4 8 2 1?", "it's 4821"),
    ("Just to confirm, that's 4821?", "it's 4 8 2 1"),
    ("So that's 4-8-2-1?", "4821"),
    ("So that's 4821?", "48 21"),               # whole numbers said in order, one turn
    ("So that's 4821?", "chaar aath do ek"),    # digits said as Hindi words
    ("You said 15 March 1988, is that right?", "My date of birth is 15 March 1988."),
    ("Theek hai, kal shaam 6 baje call karti hoon.", ""),
    ("I'll call you back tomorrow at 6 pm.", ""),
    ("Shall I call at 10:30?", ""),
])
def test_unverified_read_back_and_times_allowed(s, heard):
    assert unverified(s, heard) is None


@pytest.mark.parametrize("s, heard", [
    ("Is it 4821?", ""),                       # digits the borrower never said
    ("Was that 4821?", "it's 48"),
    ("Is it 98?", "born in 1988"),             # a part of a number is not a read-back
    ("Ending 482?", "it's 4821"),
    ("Ending 21?", "4 8 2 1"),
    ("Your date of birth is 15 March 1988?", "15 1988"),  # month not said by the borrower
    ("Yes, 12500 is right.", "is it 12500?"),  # a guessed amount is never echoed
    ("That's ₹48.", "48"),
    ("Your payment of 48 is due.", "48"),      # loan words stay blocked
])
def test_unverified_unexplained_digits_still_blocked(s, heard):
    assert unverified(s, heard) == "unverified_disclosure"


def test_replacement_follows_language():
    r = check("Aapki kisht barah hazaar ki hai.", verified=False, protected=PROTECTED, lang="hi")
    assert r.category and "pehchaan" in r.text


def test_unverified_read_back_across_two_turns_is_blocked():
    r = check("So that's 4821?", verified=False, protected=PROTECTED, lang="en", heard=["48", "21"])
    assert r.category == "unverified_disclosure"


# G-08: a verification result only after a real check (live call 2026-11-04).
@pytest.mark.parametrize("s", [
    "क्षमा चाहती हूँ, यह जानकारी भी मेल नहीं खा रही।",
    "आपके पास एक आखिरी मौका है।",
    "Sorry, that doesn't match our records.",
    "That did not match.",
    "You have two attempts left.",
    "Maaf kijiye, yeh match nahi kar raha.",
])
def test_verify_claim_without_a_check_is_invented(s):
    assert invented_verify_result(s, checked_now=False, attempts_left=2)


@pytest.mark.parametrize("s", [
    "माफ़ कीजिए, यह जानकारी मेल नहीं खा रही।",
    "आपके पास एक और मौका है।",
    "Sorry, that doesn't match. You have one more chance.",
])
def test_verify_claim_after_a_failed_check_is_allowed(s):
    assert not invented_verify_result(s, checked_now=True, attempts_left=2)


def test_last_chance_only_when_one_attempt_is_left():
    s = "आपके पास एक आखिरी मौका है।"
    assert invented_verify_result(s, checked_now=True, attempts_left=2)
    assert not invented_verify_result(s, checked_now=True, attempts_left=1)


@pytest.mark.parametrize("s", [
    "Could you tell me your date of birth?",
    "जुलाई की कौन सी तारीख?",
    "Is this a good time to talk?",
    "Thank you, one moment.",
])
def test_ordinary_lines_are_not_verify_claims(s):
    assert not invented_verify_result(s, checked_now=False, attempts_left=3)


# v10 fix-run 2, S10 run 1: mixed-script claims with no check run.
@pytest.mark.parametrize("s", ["माफ़ कीजिए, वो भी match नहीं हुआ।", "आपके पास बस एक ही मौका बचा है।",
                               "अब आपके पास verification का कोई मौका नहीं बचा है।", "You have no more attempts."])
def test_mixed_script_verify_claims_need_a_check(s):
    assert invented_verify_result(s, checked_now=False, attempts_left=1)


from emi_agent.guard import invented_notice  # noqa: E402


@pytest.mark.parametrize("s", ["Just a reminder, this call is being recorded.", "This call may be recorded.",
                               "For your privacy, please do not share this payment link with anyone.",
                               "यह call record की जा रही है।"])
def test_invented_notices_are_caught(s):
    assert invented_notice(s)


@pytest.mark.parametrize("s", ["I've recorded that you'll pay on the thirteenth of November.",
                               "मैंने आपकी बात record कर ली है।", "I've sent the payment link on WhatsApp."])
def test_real_lines_are_not_notices(s):
    assert not invented_notice(s)


from emi_agent.guard import warm_close  # noqa: E402


@pytest.mark.parametrize("s", ["Thank you for talking with me, Deepa. Have a good day.", "आपका दिन शुभ हो।",
                               "Aapka din shubh ho."])
def test_warm_close_variants(s):
    assert warm_close(s)


from emi_agent.guard import unwanted  # noqa: E402


# Owner, 2026-11-04 (v15): these exact lines from a live call are never spoken.
@pytest.mark.parametrize("s", ["आप जो भी situation में हैं, बस वही बताइए।", "आप जो comfortable हों, बस वही बताइए।",
                               "आप का ध्यान रखें।", "कृपया अपना ध्यान रखें, Sunita जी।", "अपना ख्याल रखिए।",
                               "Please take care, Deepa.", "Just tell me whatever is comfortable."])
def test_unwanted_lines(s):
    assert unwanted(s)


@pytest.mark.parametrize("s", ["क्या यह कुछ समय की बात है, या अभी कहना मुश्किल है?", "आपका दिन शुभ हो।",
                               "मैंने आपकी बात ध्यान से note कर ली है।"])
def test_unwanted_leaves_normal_lines(s):
    assert not unwanted(s)


from emi_agent.guard import once_kind  # noqa: E402


@pytest.mark.parametrize("s,kind", [("I am really sorry to hear about your job, Deepa.", "apology"),
                                    ("मैं सच में माफी चाहती हूँ Deepa जी, लेकिन...", "apology"),
                                    ("क्या यह कुछ समय की परेशानी है, या अभी कहना मुश्किल है?", "short_term"),
                                    ("Our internal team will check this and arrange a call back for you.", "handoff"),
                                    ("Is this number the best one to reach you on?", "contact"),
                                    ("आपका दिन शुभ हो, Deepa जी।", "warm_close"),
                                    ("Your EMI is due on the fifth.", None)])
def test_once_per_call_phrases(s, kind):
    assert once_kind(s) == kind


from emi_agent.guard import cant_change  # noqa: E402


@pytest.mark.parametrize("s,hit", [("I can't change the amount or the dates myself.", True),
                                   ("I cannot mark it as paid or change the amount myself, Kiran.", True),
                                   ("मैं खुद records में बदलाव नहीं कर सकती।", True),
                                   ("मैं खुद amount नहीं बदल सकती, पर मैंने note कर लिया है।", True),
                                   ("Our internal team will check this and arrange a call back for you.", False)])
def test_cant_change(s, hit):
    assert cant_change(s) is hit


# v16-special2: reading back the borrower's request is not a promise; any commitment still is (G-04).
from emi_agent.guard import _is_promise  # noqa: E402


@pytest.mark.parametrize("s", [
    "I have noted that you lost your job and are still looking for work, and that you would like some time and a waiver on the late fee.",
    "I understand you're asking for the late fee to be waived based on a special arrangement.",
    "I understand you're asking for the five hundred rupees late fee to be waived under a special arrangement, and for the EMI to be marked as paid.",
    "I understand you're asking for the late fee and EMI to be waived under a special arrangement.",
    "I have noted that you want a person to confirm any waiver or delay directly.",
    "मैंने note कर लिया है कि आप late fee में छूट चाहती हैं।",
    "I have noted your question about waiving the late fee."])
def test_reading_back_their_request_is_not_a_promise(s):
    assert not _is_promise(s)


@pytest.mark.parametrize("s", [
    "I'll waive the late fee for you.", "Don't worry, the late fee will be waived.",
    "I understand you want a waiver, and we will waive it this month.",
    "I understand you want a discount, so I've applied one.",
    "Since you asked for a waiver, I can waive the fee today.",
    "You asked for a waiver and the fee has been waived.",
    "आप छूट चाहती हैं, तो मैं छूट दे दूंगी।",
    "The late fee is waived."])
def test_commitments_are_still_promises(s):
    assert _is_promise(s)


def test_refusal_naming_the_due_date_is_not_a_promise():
    # v17-dev3 S17 run 1
    assert not _is_promise("I cannot change the due date or waive any charge myself, but our internal team can check if needed.")


def _unused(s):
    assert _is_promise(s)


# G-08, live call B004 (2026-11-04): amounts must come from code; no "charge added" when none was.
from emi_agent.guard import fee_added_claim, unknown_amount  # noqa: E402

@pytest.mark.parametrize("s,bad", [
    ("आपकी eighteen thousand rupees की EMI आठ नवंबर को due है।", False),
    ("अगर due date तक EMI नहीं भरी गई, तो five hundred rupees का late payment charge लगेगा।", False),
    ("Late fee पांच सौ रुपये है।", False),           # anusvara spelling of पाँच
    ("The late fee is ₹500.", False), ("Your EMI is Rs 18,000.", False),
    ("मैंने note कर लिया है कि आपको बारह हज़ार रुपये की उम्मीद है, जबकि records में अठारह हज़ार रुपये हैं।", False),
    ("Eighteen thousand rupees is due on 8 November 2026.", False),
    ("The late fee is seven hundred rupees.", True), ("आपको दो सौ रुपये extra देने होंगे।", True),
    ("That would be ₹750 more.", True), ("I can send you the payment link.", False)])
def test_unknown_amount(s, bad):
    # 12000: the borrower said it ("my EMI is twelve thousand"), so reading it back is fine.
    assert unknown_amount(s, {18000, 500, 12000}) is bad


@pytest.mark.parametrize("s,claim", [
    ("आपके loan terms के अनुसार, five hundred rupees का late payment charge लग गया है।", True),
    ("A late payment charge of five hundred rupees has been added.", True),
    ("No late charge has been added yet.", False),
    ("अगर due date तक EMI नहीं भरी, तो late payment charge लगेगा।", False),
    ("अभी कोई late charge नहीं लगा है।", False)])
def test_fee_added_claim(s, claim):
    assert fee_added_claim(s) is claim
