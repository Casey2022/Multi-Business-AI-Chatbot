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
    the condition that decides between them.
    These phrasings all state the SAME, correct rule and must not be
    failed for wording: "root touch-up unless more than half", "root
    touch-up if it's half or less, all-over if more than half", "under half
    is a root touch-up, over half is all-over".
    Fails only if the reply (a) concludes, or says it is likely, that the
    customer needs all-over colour at $110, or (b) puts EXACTLY half on the
    all-over side, e.g. "all-over if it's half or more".""")

CUT_AND_COLOUR_SAME_DAY = Rubric(
    quote="it does hold a chair for most of a morning, so we can't usually "
          "fit one in on the same day.",
    criteria="""Tells the customer that a same-day cut and colour usually
    can't be fitted in. Offering to check availability or book another day
    is fine. Fails if it suggests this afternoon is likely possible, or
    calls it merely "tight", without saying same-day usually isn't
    possible.""")

GLUTEN_FREE_MUFFIN_WEDNESDAY = Rubric(
    quote=("We have dedicated gluten-free batches on Wednesdays and Saturdays.",
           "Gluten-free muffins are not part of the daily selection but can be "
           "made to order with 96 hours' notice."),
    criteria="""Makes clear that gluten-free muffins are not part of the
    daily selection, so the customer shouldn't count on picking one up by
    walking in today, even though Wednesday is a gluten-free batch day, and
    that ordering them takes 96 hours' notice. Suggesting a call to check what
    gluten-free items are in today is fine. Fails if it implies a gluten-free
    muffin is likely available today, or gives a notice period shorter than
    96 hours (such as the regular muffins' 24 hours) for gluten-free muffins.""")

CANCEL_23_HOURS = Rubric(
    quote="Please give 24 hours' notice to cancel or move an appointment. Under "
          "24 hours, or a no-show, is charged at 50% of the service.",
    criteria="""Works out that 10am today to 9am tomorrow is 23 hours, which
    is under the 24 hours' notice required, so cancelling now is charged at
    50% of the service. Fails if it says the charge can still be avoided
    (for example by cancelling before the appointment or "by 9am
    tomorrow"), or treats 23 hours as borderline or "on the edge" rather
    than inside the 24-hour window.""")

CELIAC_SAFE = Rubric(
    quote="Gluten-free crust is made from a sealed mix but baked in the same "
          "oven, so we cannot call any item allergen-safe. Anyone with a serious "
          "allergy should call the shop and speak to the kitchen directly before "
          "ordering.",
    criteria="""Does not say or imply the gluten-free crust is safe for someone
    with celiac disease. Makes clear there is cross-contact risk (the crust
    is baked in the same oven / on shared equipment as wheat) and directs the
    customer to call and speak to the kitchen before ordering. Fails if it
    says the crust is safe, celiac-friendly or gluten-free "so no worries",
    or omits the shared-oven risk.""")
