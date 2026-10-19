"""Rubrics for the hard-set rows a keyword can't grade. See judge.py.

Each one quotes the document text it depends on (checked against
services.md by validate_tests) and says what a passing reply must DO with
that text, not which words it must use.
"""

from judge import Rubric

DEPOSIT_THREE_DAYS = Rubric(
    # 2026-09-30: the rules are data now (policies.cancellation, rendered by
    # policy.py) and the bot applies them with the cancellation_outcome tool.
    # A week of rewording the prose moved the wrong answer between this row
    # and WEDDING_CANCEL_MONTH_OUT without ever fixing both.
    quote=("Cancelled between 48 hours and 7 days before the order date: the "
           "whole deposit is forfeited and none of it comes back, for every "
           "cake."),
    criteria="""States the policy that cancelling less than 7 days before
    means the deposit is forfeited, and applies it: three days is inside that
    window, so the deposit is not refunded. This holds for ANY cake, wedding
    cakes included. Saying it can't look up the specific order is fine, and
    so is asking which kind of cake it was. Fails if it stops at "call us"
    without stating the policy, states the outcome without the 7-day rule, or
    says any part of a deposit (for example, a wedding deposit above 25%)
    comes back when cancelling inside 7 days.""")

WEDDING_CANCEL_MONTH_OUT = Rubric(
    # 2026-09-30: rendered from policies.cancellation (see DEPOSIT_THREE_DAYS).
    quote=("Cancelled 7 days or more before the order date: custom cake, the "
           "deposit comes back in full; wedding cake, 25% of the order price is "
           "kept, and only what was paid above that 25% is refunded; someone "
           "who paid just the 25% minimum gets nothing back."),
    criteria="""Says a month out is more than 7 days, so anything the customer
    paid above 25% of the order price is refunded and the 25% is kept. Adding
    "if you only paid the 25% minimum, there's nothing to refund" is fine.
    Fails if it promises a full refund of the deposit, never says the 25% is
    kept, or says nothing comes back whatever was paid (for example, claiming
    the wedding deposit is always exactly 25%).""")

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
    # 2026-10-11: Belmont's prices are its menu now; the first quote is the
    # generated price line, the second the rule that stayed in the section.
    quote=("Colour prices: root touch-up $85, all-over colour from $110",
           "Grey coverage is priced as a root touch-up unless the grey is more "
           "than half the head, in which case it's all-over."),
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
    # 2026-10-03: rendered from Belmont's policies.cancellation (policy.py).
    quote="Cancelled or moved less than 24 hours before the appointment: 50% of "
          "the service is charged.",
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

REPAIR_50_MILES = Rubric(
    quote="We work across Monroe County and the surrounding towns, within about "
          "35 miles of the Lyell Ave shop. For larger projects we'll travel "
          "further — call and ask. Smaller repair work we keep close to home, "
          "because a two-hour round trip for a door is not fair to the customer.",
    criteria="""Makes clear that a small repair 50 miles away is outside the
    range Ridgeline normally travels for small jobs (repairs are kept local,
    within about 35 miles), so it probably isn't something they'd take on.
    Suggesting the customer call to ask is fine. Wording doesn't matter
    ("close to home", "close to the shop", "outside our normal range" all
    pass). Fails if it agrees to do the job, or implies 50 miles is within
    range.""")


PARTY_TRAY_FRIDAY_FIVE = Rubric(
    # 2026-10-03: was the keywords ("4pm", "three hours"), which a reply
    # offering a tray tonight could still pass by mentioning either.
    quote=("Trays need at least three hours' notice, and on Friday and "
           "Saturday nights we ask for the order by 4pm",
           "The cutoff is for trays only: regular pizzas, wings and subs can be "
           "ordered for tonight right up until the kitchen stops taking orders"),
    # 2026-10-03, live: the cutoff was right, then "Could you order for a
    # different time?" The cutoff is for trays; regular food is still on.
    criteria="""It's 5pm on Friday. Makes clear a party tray can't be ordered
    for tonight, because the Friday order-by time of 4pm has passed (three
    hours' notice also can't be met for anything wanted now). Offering a tray
    for another day is fine. Fails if it offers or implies a party tray for
    tonight, never mentions the cutoff or the notice, or leaves the customer
    without food tonight: telling them to order for another time, or implying
    regular pizzas, wings or subs are cut off too, when they can still be
    ordered tonight.""")


ESTIMATE_VISIT_FRIDAY = Rubric(
    # 2026-10-04: business days. It's Friday; two to five business days out
    # skips the weekend.
    quote=("We book\nestimates two to five business days out, sooner if we have "
           "a\ncancellation."),
    criteria="""It's Friday. Says estimate visits are booked two to five
    business days out, and if it names days, they skip the weekend: the
    earliest is Tuesday and the latest the following Friday. Saying it can be
    sooner with a cancellation is fine, and so is offering to book. Fails if
    it counts Saturday or Sunday as business days (for example, says Sunday
    or Monday is two business days away), or promises a specific day as
    certain.""")


QUOTE_AFTER_THURSDAY = Rubric(
    # 2026-10-04: business days. It's Friday; the visit was Thursday.
    quote="sends a written quote within three business days",
    criteria="""It's Friday and the estimate visit was yesterday, Thursday.
    Says the written quote comes within three business days, and if it names
    a day, it's by Tuesday, because the weekend doesn't count. Fails if it
    counts Saturday or Sunday (for example, says the quote will come by
    Sunday or Monday), or drops "business" so that three days lands on the
    weekend.""")


# --- Prices and totals (2026-10-07) -------------------------------------
# Added to decide whether prices need code (a quote_total tool) the way
# notice periods did. Plain lookups have always passed; these are the sums,
# the conditions attached to a price, and prices the documents don't give.

# Since 2026-10-10 Crosstown's prices live in its menu (config menu:), and
# these are the price lines generated from it (menu.render_item).
_PIZZA_PRICES = ("Pizza prices: 10-inch personal $11, 14-inch medium $17, "
                 "18-inch large $22, Sicilian 16x16 square pan $26.",
                 "Topping: $1.75 each on a 10-inch personal, $2.25 each on a "
                 "14-inch medium, $2.75 each on an 18-inch large, $3.25 each on a "
                 "Sicilian 16x16 square pan.")

MEDIUM_TWO_TOPPINGS_DELIVERED = Rubric(
    quote=_PIZZA_PRICES + ("The delivery fee is $3 and the minimum order is $15.",),
    criteria="""Gives the total for a medium with two toppings, delivered:
    $17 + 2 x $2.25 = $21.50, plus the $3 delivery fee = $24.50 (tax aside).
    Showing the pieces is fine. Fails if any piece is wrong (for example a
    large's $2.75 topping price), the delivery fee is left out of a total it
    calls the total, or the sum is wrong.""")

GLUTEN_FREE_PERSONAL_TWO_TOPPINGS = Rubric(
    quote=_PIZZA_PRICES + ("Gluten-free crust: $3 extra on a 10-inch personal only",),
    criteria="""Gives the price of a gluten-free personal (10-inch) pizza with
    two toppings: $11 + $3 gluten-free + 2 x $1.75 = $17.50. Fails if it
    leaves out the $3, uses a medium or large topping price, or gets the sum
    wrong.""")

THIRTY_WINGS = Rubric(
    quote="Wings prices: 10 wings $12, 20 wings $22, 40 wings $40.",
    criteria="""There is no 30-wing price. Passes if it says wings come in
    10, 20 or 40 and either offers a combination (20 + 10 = $34) or the 40
    for $40, or both. Fails if it states a price for 30 wings as if it were
    on the menu (for example "30 wings are $33"), or gets a combination's sum
    wrong.""")

SICILIAN_TOPPINGS = Rubric(
    # 2026-10-10: the document had no Sicilian topping price and the bot
    # borrowed the large's $2.75 every run, through two prompt versions.
    # The owner set it ($3.25, 50 cents more than a large); the gap-filling
    # check moved to EXTRA_DIP below.
    quote=_PIZZA_PRICES,
    criteria="""A Sicilian with two toppings: $26 + 2 x $3.25 = $32.50.
    Showing the pieces is fine. Fails if it uses another size's topping price
    (for example the large's $2.75), or gets the sum wrong.""")

EXTRA_DIP = Rubric(
    # 2026-10-10: a price the documents don't give, replacing the Sicilian
    # as the gap-filling check.
    quote=("Wings come with celery and your choice of blue cheese or ranch."),
    # First run: "Extra ranch with wings isn't priced in our menu, so I can't
    # confirm the cost … call us" failed only for not mentioning the
    # included dip. That reply is right; the gap is what this row checks.
    criteria="""The documents say wings come with blue cheese or ranch but give
    no price for extra dip. Passes if it says it doesn't know or can't confirm
    what extra dip costs (suggesting a call, or that the kitchen can confirm,
    is fine). Mentioning that one dip already comes with the wings is good but
    not required. Fails if it states a price for extra ranch or blue cheese,
    or says extra is free, as fact.""")

EIGHTEEN_CUPCAKES = Rubric(
    quote="Standard cupcake prices: $3.50 each, sold only in multiples of 12 ($42 for 12).",
    criteria="""Says standard cupcakes are sold by the dozen, so 18 isn't an
    order size, and offers a dozen ($42) or two dozen ($84). Fails if it
    quotes 18 at $3.50 each ($63) as an order it can take without
    mentioning the dozen, or gets a dozen's price wrong.""")

TWO_DOZEN_MUFFINS = Rubric(
    quote="Muffin prices: $3.25 each or $35 for 12.",
    criteria="""Two dozen muffins at the dozen price is $70. Fails if it
    charges 24 at $3.25 each ($78) as the price for two dozen, or gets the
    sum wrong.""")

TWELVE_INCH_CHARACTER_CAKE = Rubric(
    quote=("Birthday cake prices: 6-inch $45 (serves 6-8), 12-inch $120 "
           "(serves 30-40). Custom design, theme or character: $20 to $60 extra "
           "on any birthday cake."),
    criteria="""A 12-inch birthday cake with a character design: $120 plus
    $20 to $60, so about $140 to $180 depending on complexity. Giving the
    pieces without the total passes too. Fails if it gives one fixed price
    as if the design cost were known, leaves out the design cost, or gets the
    range wrong.""")

HYDRO_JET_AFTER_HOURS = Rubric(
    quote=("There is a $75 after-hours service call fee in addition to "
           "standard labor rates.",
           "Hydro-jetting is $400 for a typical home service line."),
    criteria="""After hours, hydro-jetting a typical home line is $400 plus
    the $75 after-hours fee: about $475. Giving both pieces without adding
    them passes. Fails if it leaves out the after-hours fee, or quotes the
    standard $150 drain cleaning as the price of hydro-jetting.""")

LEAK_DETECTION_APPLIED = Rubric(
    quote=("We charge $125 for the initial leak detection visit, which is "
           "applied to the repair cost if you proceed."),
    criteria="""The customer was quoted $600 for the repair after the $125
    detection visit. Because the $125 is applied to the repair, they pay $600
    in total ($125 already paid, $475 more). Fails if it says the total is
    $725, or that the $125 is charged on top.""")


SERVICE_RULES_FREE_AT_30 = Rubric(
    # 2026-10-18. journey_eval's Belmont "service rules" policy (colour has
    # its own 48-hour window). Graded by a judge, not a pattern: a right
    # answer often adds "under 24 hours you'd be charged 50%", which is true,
    # and four rounds of regex kept failing it (journey_eval 2026-10-17/18).
    # All three windows: with only the middle one quoted, the judge read a
    # true "under 24 hours it's 50%" as the colour rule misapplied (2026-10-18).
    quote=("Cancelled or moved 48 hours or more before the appointment: there "
           "is no charge, for every appointment.",
           "Cancelled or moved between 24 hours and 48 hours before the "
           "appointment: a colour appointment, 50% of the service is charged; "
           "any other appointment, there is no charge.",
           "Cancelled or moved less than 24 hours before the appointment: 50% "
           "of the service is charged, for every appointment."),
    criteria="""The customer's service is a haircut or a cut and colour, which
    is not plain colour, so it follows the rule for any other appointment.
    Thirty hours is between 24 and 48 hours, so cancelling now has no charge:
    the reply must say so. With less than 24 hours' notice EVERY appointment,
    theirs included, is charged 50%, so saying that is correct and fine; so is
    saying a plain colour appointment would cost 50% in this window. Fails if it says this customer would
    be charged anything for cancelling now, or treats their service as a
    colour appointment.""")


COLOUR_IS_NOT_CUT_AND_COLOUR = Rubric(
    # 2026-10-18, live, Belmont with only "cut and colour" given its own
    # 48-hour window: a colour customer was told (rightly) there's no charge,
    # then "colour appointments get charged differently in that window".
    quote=("Cancelled or moved 48 hours or more before the appointment: there "
           "is no charge, for every appointment.",
           "Cancelled or moved between 24 hours and 48 hours before the "
           "appointment: a cut and colour appointment, 50% of the service is "
           "charged; any other appointment, there is no charge.",
           "Cancelled or moved less than 24 hours before the appointment: 50% "
           "of the service is charged, for every appointment."),
    criteria="""The customer has a plain colour appointment. Only a cut and
    colour has its own rule (between 24 and 48 hours); plain colour follows the
    rule for any other appointment, so at 30 hours there is no charge, and the
    reply must say so. With less than 24 hours' notice EVERY appointment, plain
    colour included, is charged 50%: saying their colour appointment would be
    charged 50% if it gets under 24 hours is correct, not the cut and colour
    rule misapplied. Mentioning that a cut and colour would cost 50% in the
    24-48 hour window is also fine. Fails if it says
    this customer is charged anything for cancelling now, or says or implies
    that colour (as opposed to a cut and colour) appointments are charged
    differently.""")


# Opening hours vs notice (2026-10-18). check_notice compares hours of
# notice and says nothing about opening hours, so in each of these the
# notice is MET and the shop is shut at the time wanted. The hours are in
# the prompt on every message; these measure whether the reply uses them.
_SUNRISE_HOURS = ("We're open Tuesday through Sunday, 7am to 3pm, closed Mondays. "
                  "Cake pickup is available anytime during open hours")

CLOSED_MONDAY_CAKE = Rubric(
    quote=("We need at least 72 hours' notice for any custom cake order.", _SUNRISE_HOURS),
    criteria="""It is Thursday at 10am. Monday at 10am is 96 hours away, so the
    72 hours' notice is met, but the bakery is closed on Mondays, so the cake
    can't be picked up then. The reply must say they're closed Mondays (or
    that Monday pickup isn't possible) and should offer another time, such as
    Sunday or Tuesday, during open hours. Fails if it says a Monday pickup
    works, or only talks about the notice being met.""")

AFTER_CLOSING_MUFFINS = Rubric(
    quote=("order a dozen with 24 hours' notice to guarantee it.", _SUNRISE_HOURS),
    criteria="""It is Saturday at 1pm. Sunday at 5pm is 28 hours away, so the
    24 hours' notice is met, but the bakery closes at 3pm, so 5pm pickup isn't
    possible. The reply must say they close at 3pm (or that 5pm is after
    closing) and should offer a time they're open, such as Sunday between 1pm
    and 3pm (still 24 hours' notice). Fails if it agrees to 5pm.""")

EARLIEST_PICKUP_TUESDAY = Rubric(
    quote=("order a dozen with 24 hours' notice to guarantee it.", _SUNRISE_HOURS),
    criteria="""It is Sunday at 1pm. With 24 hours' notice the muffins could be
    ready Monday at 1pm, but the bakery is closed on Mondays, so the earliest
    pickup is Tuesday when they open at 7am. Fails if it offers Monday, or
    names a time on Monday as the earliest pickup.""")

TRAY_AFTER_CLOSE = Rubric(
    quote=("Trays need at least three hours' notice, and on Friday and Saturday "
           "nights we ask for the order by 4pm",
           "We're open Tuesday to Sunday, 11am to 10pm, closed Mondays. The "
           "kitchen stops taking orders at 9:45pm."),
    criteria="""It is Tuesday at 10am and they want a party tray on Thursday at
    11pm. The three hours' notice is met and the 4pm cutoff is only for Friday
    and Saturday, but Crosstown closes at 10pm (the kitchen stops taking
    orders at 9:45pm), so 11pm isn't possible. The reply must say they're
    closed by then and should offer an earlier time Thursday evening. Fails
    if it agrees to 11pm.""")
