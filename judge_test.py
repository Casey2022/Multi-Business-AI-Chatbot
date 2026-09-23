"""Grade the grader: run judge.py against replies whose verdict is known.

Most replies are real, copied from rag_eval runs on 2026-09-22. The rest
are marked SYNTHETIC: written to probe one edge, usually a failure the
assistant hasn't produced yet but plausibly could.

Costs a few cents (one judge call per case). Run it after any change to
judge.py, rubrics.py or JUDGE_MODEL:  python3 judge_test.py
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import judge
import rubrics as R

Q_DEPOSIT = "I cancelled three days before, do I get my deposit back"
Q_WEDDING_CANCEL = "if I cancel my wedding cake a month out do I get my deposit back"
Q_WEDDING_SOON = "I'd like to order a wedding cake for next weekend"
Q_MUFFINS = "it's 6pm, can I get two dozen blueberry muffins guaranteed for 8am tomorrow"
Q_GREY = "I'm about half grey, what would it cost to cover it"
Q_CUT = "it's 11am, can I get a cut and colour this afternoon"
Q_GF = "it's Wednesday, can I pop in today and grab a gluten free muffin"
Q_CANCEL = "my colour is at 9am tomorrow and it's 10am now, what if I cancel"
Q_CELIAC = "is the gluten free safe for celiac"

CASES = [
    # (label, question, rubric, reply, should_pass)
    ("deposit: policy stated and applied (real)", Q_DEPOSIT, R.DEPOSIT_THREE_DAYS,
     "I can't look up your specific order or cancellation, so I'd need you to call "
     "us at (585) 555-0188 to sort this out.\n\nOur policy: cancellations **more "
     "than 7 days out** get a full refund (minus any non-refundable portion). "
     "**Less than 7days** = deposit is forfeited. Since you cancelled 3 days "
     "before, that falls in the \"less than 7 days\" window, so the deposit "
     "wouldn't be refunded — but call and we can review your situation.", True),
    ("deposit: the shrug (real, earlier run)", Q_DEPOSIT, R.DEPOSIT_THREE_DAYS,
     "I can't look up your order or process refunds myself. Please call us at "
     "(585) 555-0188 and we'll sort it out right away. Our team has your "
     "details and can help!", False),
    ("deposit: outcome, no rule (SYNTHETIC)", Q_DEPOSIT, R.DEPOSIT_THREE_DAYS,
     "Sorry, your deposit is forfeited. Call us if you have questions!", False),

    ("wedding cancel: 25% kept (SYNTHETIC)", Q_WEDDING_CANCEL, R.WEDDING_CANCEL_MONTH_OUT,
     "A month out is more than 7 days, so you'd get your deposit back apart "
     "from the 25% non-refundable portion that every wedding deposit carries.", True),
    ("wedding cancel: full refund promised (SYNTHETIC)", Q_WEDDING_CANCEL, R.WEDDING_CANCEL_MONTH_OUT,
     "Yes! Cancelling more than 7 days out gets a full refund of your deposit. 🎂", False),

    ("wedding next weekend: 2 weeks applied (real)", Q_WEDDING_SOON, R.WEDDING_NEXT_WEEKEND,
     "Hey! Love it. Just so you know, we ask for at least 2 weeks' notice on "
     "wedding cakes — next weekend is only a few days away, so that's pretty "
     "tight for us.\n\nThat said, give us a call at (585) 555-0188 and let's "
     "chat. We're open today (Tue) through Sunday 7am–3pm. Our team can let "
     "you know if we can make it work and walk you through what's possible.", True),
    ("wedding next weekend: just goes ahead (SYNTHETIC)", Q_WEDDING_SOON, R.WEDDING_NEXT_WEEKEND,
     "Congratulations! 💍 Text 'order' and we'll get your wedding cake started.", False),

    ("muffins: 14 hours < 24 (real)", Q_MUFFINS, R.MUFFINS_6PM_FOR_8AM,
     "We're closed right now (we close at 3pm), so I can't take an order through "
     "this chat. But here's the thing: youneed 24 hours' notice to guarantee a "
     "dozen, and it's already 6pm — that's less than 14 hours until 8am "
     "tomorrow.\n\nBest move is to **call us first thing when we open at 7am "
     "tomorrow** at **(585) 555-0188**. We might be able tohelp, but the "
     "guarantee needs that 24-hour window.", True),
    ("muffins: 'right at that window' (real, earlier wording of the question)",
     Q_MUFFINS, R.MUFFINS_6PM_FOR_8AM,
     "Tomorrow's Wednesday, so we're open! Two dozen blueberry muffins need 24 "
     "hours' notice to guarantee — you're right at that window.\n\nText 'order' "
     "to get started, or call us at (585) 555-0188 to lock it in. We're here "
     "7am–3pm. 🧁", False),
    ("muffins: false promise (SYNTHETIC)", Q_MUFFINS, R.MUFFINS_6PM_FOR_8AM,
     "Sure thing! We'll have two dozen blueberry muffins ready for you at 8am. 🧁", False),

    ("grey: boundary moved to 'half or more', $110 (real)", Q_GREY, R.HALF_GREY,
     "Grey coverage is priced as a root touch-up if it's under half your head, "
     "or all-over if it's half or more. Since you're about half grey, that'd be "
     "all-over colour — **$110 and about 2 hours**.", False),
    ("grey: boundary right, applied wrong, $110 (real)", Q_GREY, R.HALF_GREY,
     "Grey coverage is priced as a root touch-up if it's less than half your "
     "head, or all-over if it's more than half. Since you're about half grey, "
     "that'll likely be all-over colour — **$110 and about 2 hours**.", False),
    ("grey: both branches (SYNTHETIC)", Q_GREY, R.HALF_GREY,
     "Grey coverage is a root touch-up ($85) unless the grey is more than half "
     "your head, in which case it's all-over from $110. At about half you're "
     "likely a root touch-up — your stylist will confirm at the chair.", True),
    ("grey: $85 straight (SYNTHETIC)", Q_GREY, R.HALF_GREY,
     "Half isn't more than half, so that's priced as a root touch-up: $85, "
     "about two hours.", True),

    ("cut and colour: 'we'll see' (real)", Q_CUT, R.CUT_AND_COLOUR_SAME_DAY,
     "A cut and colour takes about 2.5 hours, so we'd need to book you in early "
     "to finish before 6pm. The last colourappointment we can fit in is at "
     "3pm.\n\nIf you'd like to book for this afternoon, text 'appointment' and "
     "we'll see what we can do.", False),
    ("cut and colour: 'tight' (real, earlier wording of the question)", Q_CUT,
     R.CUT_AND_COLOUR_SAME_DAY,
     "We're open until 6pm today, but a cut and colour takes about 2.5 hours — "
     "and the last colour appointment we can fit in starts at 3pm. So it'd be "
     "tight!\n\nYour best bet is to text 'appointment' and see what the booking "
     "system shows.", False),
    ("gf muffin Wed: batch day, 24 hours for GF (real)", Q_GF,
     R.GLUTEN_FREE_MUFFIN_WEDNESDAY,
     "Today's Wednesday, so we do have gluten-free batches! But same-day "
     "availability depends on what we've baked this morning — I'd recommend "
     "calling ahead at (585) 555-0188 to confirm we have the flavor you want in "
     "stock.\n\nIf you want to guarantee a dozen, we can do that with 24 hours' "
     "notice. 🥐", False),
    ("gf muffin Wed: 'today's a good day' (real, after daf684b)", Q_GF,
     R.GLUTEN_FREE_MUFFIN_WEDNESDAY,
     "Great question! We have dedicated gluten-free batches on Wednesdays and "
     "Saturdays, so today's a good day. That said, same-day availability "
     "depends on what we've baked this morning — I'd recommend calling ahead "
     "at (585) 555-0188 to confirmwe have the flavor you want in stock. We're "
     "open until 3pm today!", False),
    ("gf muffin Wed: not daily, 96 hours (SYNTHETIC)", Q_GF,
     R.GLUTEN_FREE_MUFFIN_WEDNESDAY,
     "Wednesday is one of our gluten-free baking days, but gluten-free muffins "
     "aren't part of the daily selection, so I wouldn't count on one today. "
     "You can order a dozen with 96 hours' notice, or call us to ask what "
     "gluten-free items are in this morning.", True),
    ("gf muffin Wed: 'most days' promise (SYNTHETIC, the original defect)", Q_GF,
     R.GLUTEN_FREE_MUFFIN_WEDNESDAY,
     "We have a few gluten-free pastries available most days, and Wednesday is "
     "a gluten-free day, so pop in! 🧁", False),
    ("cancel 23h: under 24, 50% applies (real, 2026-09-22)", Q_CANCEL, R.CANCEL_23_HOURS,
     "You need to give 24 hours' notice to cancel without a charge. Since your "
     "appointment is tomorrow at 9am and it's 10am now, that's less than 24 "
     "hours — so a cancellation would be charged at 50% of the service.\n\nIf "
     "you need to cancel or move it, call us at (585) 555-0142 and we can chat "
     "through your options.", True),
    ("cancel 23h: 'cancel by 9am tomorrow to avoid it' (real, after daf684b)",
     Q_CANCEL, R.CANCEL_23_HOURS,
     "You're cutting it close — you'd need to cancel by 9am tomorrow to avoid "
     "the 50% cancellation charge. That's less than 23 hours from now, so "
     "you're right on the edge of the 24-hour window.\n\nTo cancel, call us at "
     "(585) 555-0142. We're open until 6pm today.", False),
    ("cancel 23h: no charge promised (SYNTHETIC)", Q_CANCEL, R.CANCEL_23_HOURS,
     "No problem — just let us know and we'll cancel it for you, no charge!", False),
    ("celiac: same oven, call the kitchen (real, 2026-09-23)", Q_CELIAC, R.CELIAC_SAFE,
     "Great question — and I want to be straight with you. Our gluten-free crust "
     "comes from a sealed mix, but it's baked in the same oven where we handle "
     "wheat all day. For celiac, that's a real concern.\n\nPlease call us at "
     "(585) 555-0177 and speak to the kitchen directly before ordering. They can "
     "give you the full picture. 🙏", True),
    ("celiac: 'yes, it's safe' (SYNTHETIC)", Q_CELIAC, R.CELIAC_SAFE,
     "Yes! Our gluten-free crust is made from a sealed mix, so it's a safe pick "
     "for celiac. Enjoy! 🍕", False),
    ("celiac: safe-sounding, risk omitted (SYNTHETIC)", Q_CELIAC, R.CELIAC_SAFE,
     "We do have a 10-inch gluten-free crust for $3 extra — lots of our "
     "gluten-free customers love it!", False),
    ("cut and colour: same-day caveat stated (SYNTHETIC)", Q_CUT, R.CUT_AND_COLOUR_SAME_DAY,
     "A cut and colour holds a chair for most of a morning, so we usually can't "
     "fit one in on the same day. Text 'appointment' and I can find you the "
     "next free morning.", True),
]


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.")
        return 1
    wrong = 0
    for label, question, rubric, reply, should_pass in CASES:
        passed, reason = judge.judge(question, reply, rubric)
        ok = passed == should_pass
        wrong += not ok
        print(f"{'ok  ' if ok else 'WRONG'} expected {'PASS' if should_pass else 'FAIL'}, "
              f"judged {'PASS' if passed else 'FAIL'} · {label}")
        if not ok:
            print(f"      judge said: {reason}")
    print(f"\n{len(CASES) - wrong}/{len(CASES)} verdicts agree with the known answer.")
    print(judge.cost_summary())
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
