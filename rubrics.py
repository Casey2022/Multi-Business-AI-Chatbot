"""Rubrics for the hard-set rows a keyword can't grade. See judge.py.

Each one quotes the document text it depends on (checked against
services.md by validate_tests) and says what a passing reply must DO with
that text, not which words it must use.
"""

from judge import Rubric

DEPOSIT_THREE_DAYS = Rubric(
    quote="Less than 7 days: deposit forfeited.",
    criteria="""States the policy that cancelling less than 7 days before
    means the deposit is forfeited, and applies it: three days is inside that
    window, so the deposit is not refunded. Saying it can't look up the
    specific order is fine, but the reply fails if it stops there without
    stating the policy, or states the outcome without the 7-day rule.""")

WEDDING_CANCEL_MONTH_OUT = Rubric(
    quote="Wedding cakes require a 25% non-refundable deposit. Cancellation "
          "more than 7 days out: full refund of any deposit beyond the "
          "non-refundable portion.",
    criteria="""Says a month out is more than 7 days, so the deposit is
    refunded EXCEPT the 25% non-refundable portion. Fails if it promises a
    full refund of the deposit, or doesn't mention that part is
    non-refundable.""")

WEDDING_NEXT_WEEKEND = Rubric(
    quote="We ask for at least 2 weeks' notice for wedding cakes",
    criteria="""States the 2-week minimum notice and says plainly that next
    weekend is inside it, so the order is short of the notice the bakery
    asks for. Offering a call to see what's possible is fine. Fails if it
    says or implies the order can simply go ahead, or never mentions the
    2-week notice.""")

MUFFINS_6PM_FOR_8AM = Rubric(
    quote="order a dozen with 24 hours' notice to guarantee it",
    criteria="""Recognises that 6pm tonight to 8am tomorrow is less than the
    24 hours' notice needed to guarantee muffins, so the order cannot be
    guaranteed. Suggesting they call when the shop opens is fine. Fails if
    it promises, or implies, that the muffins can be guaranteed, or calls
    the timing 'right at the window'.""")

HALF_GREY = Rubric(
    quote="Root touch-up is $85 and takes about two hours. All-over colour "
          "starts at $110 and also takes about two hours. Grey coverage is "
          "priced as a root touch-up unless the grey is more than half the "
          "head, in which case it's all-over.",
    criteria="""Applies the rule that grey coverage is a root touch-up ($85)
    unless the grey is MORE than half the head, when it is all-over
    ($110+). "About half" is not clearly more than half. A passing reply
    either quotes the $85 root touch-up, or gives both prices together with
    the condition that decides between them. Fails if it concludes, or says
    it is likely, that the customer needs all-over colour at $110, or if it
    moves the boundary (e.g. "half or more").""")

CUT_AND_COLOUR_SAME_DAY = Rubric(
    quote="it does hold a chair for most of a morning, so we can't usually "
          "fit one in on the same day.",
    criteria="""Tells the customer that a same-day cut and colour usually
    can't be fitted in. Offering to check availability or book another day
    is fine. Fails if it suggests this afternoon is likely possible, or
    calls it merely "tight", without saying same-day usually isn't
    possible.""")
