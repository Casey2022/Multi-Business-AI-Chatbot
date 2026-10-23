# Owner guide

How to use the owner portal. The owner assistant on the Help page answers
from this guide, so it is written for owners, and it must stay true:
every bold phrase is a label you can find in the portal
(`assistant_test.py` checks that each one exists). Use bold only for
labels.

## The pages

Every business page has the same row of tabs:

- **Appointments**: every booking, newest first.
- **Conversations**: everything customers said to the assistant, and what it said back.
- **Calendar**: this week's bookings, hour by hour.
- **Knowledge base**: everything the assistant knows about your business. It answers customer questions from here.
- **Menu**: your items and prices. Only businesses that keep prices on a menu have this tab. No Menu tab? You can switch it on yourself: in Settings, set **Prices on a menu?** to yes and press **Save settings**.
- **Settings**: business details, how the assistant sounds, services, booking questions.

At the top of every page, **Chat as a customer** opens your chat in a new
tab, so you can ask the assistant something and hear exactly what a
customer would. **Help** is where you can ask the owner assistant, or
reach a person. The assistant is also on every page: press
**Ask the assistant** in the bottom corner.

## Drafts and Publish

Changes to the Knowledge base and the Menu are drafts. Customers keep
getting answers from the published version until you press
**Publish changes**. While something is waiting, a banner saying
"Unpublished changes." appears on the Knowledge base, Menu and Settings
pages and lists what's waiting. From the banner you can:

- **Publish changes**: make every waiting change live at once.
- **Discard all changes**: throw away every waiting change and go back to what customers see now.

Settings are different: they take effect as soon as you press
**Save settings**. There is nothing to publish for them.

Before publishing knowledge-base or menu changes, use **Preview answers**
on the Knowledge base page to see how the assistant will answer with them.

## Appointments

The list shows the customer, the service, when it's scheduled, its status
and whether it reached your calendar. **View** opens one appointment, with
everything the customer told the assistant and every message the customer
was sent about it.

- To move an appointment: open it with **View**, choose the new date and time, and press **Move appointment**. Availability isn't checked here; you're the authority on your own schedule. The customer is told about the change automatically.
- To cancel: open it and press **Cancel appointment**. It removes the event from your calendar as well and tells the customer. This can't be undone.
- There's no form for adding a booking by hand. Customers book through the assistant (by text or in the chat). To book for someone, use **Chat as a customer** and book it as they would.

## Conversations

Pick a customer on the left to read the whole conversation: what they
asked, what the assistant answered, and where a booking came from. This
page is for reading. You can't reply to a customer from here.

## Calendar

A week at a time. **← Previous**, **This week** and **Next →** move
between weeks. It shows bookings taken through the assistant.
Appointments added directly in Google Calendar still block availability,
but don't appear here. Connecting a Google Calendar is set up by your
operator: the assistant on the Help page drafts the message for you.

### Blocking off time

Under the week, **Block off time** keeps the assistant from booking a day,
several days, or specific hours (a vacation, a dentist appointment, a
staff training morning).

- Choose the day under From. For several days, also choose Until.
- Leave **Whole day** ticked to block every day from the first to the last. Untick it to choose a Start time and an End time instead: the block runs from the start time on the first day to the end time on the last.
- Add a note if you like. Only you see it; customers are just told that time isn't available, and offered other times.
- Press **Block this time**.

Blocked hours show striped on the calendar, and the list under the form
shows what's coming up. **Remove** unblocks one. If appointments are
already booked inside a block, you're told which; they aren't cancelled,
so move or cancel them yourself if you need to.

## Knowledge base

The assistant answers customer questions only from these sections.
Anything not here, it says it doesn't know.

- To add a section: scroll to **Add section** at the bottom, give it a title and write what the assistant should know, then press **Add section**.
- To edit a section: change its title or text and press **Save**. Save stays grey until something has really changed. A section you've saved but not published is marked "Changed since publish" or "New since publish".
- **History** shows what a section says now and every earlier version. Nothing is overwritten without being kept.
- **Delete section** removes a section. Previous versions are kept.
- If a section changed after you opened the page (the assistant changed it, or you saved it in another tab), pressing **Save** saves nothing and shows you both versions: the section as saved now, and what you'd written. Press **Keep my version** to save yours, or edit the section and press **Save**.

The assistant can also make these changes for you; see Help below.

What makes the assistant answer well:

- Put the words customers actually use in the title.
- State prices, timeframes and policies plainly and specifically.
- Say what you don't offer, and put that in the title too ("Septic tanks (not offered)").

### Preview answers

Before publishing, **Preview answers** asks the assistant your questions
twice: as things are now, and as they will be after you publish. Type up
to 5 questions, one per line, or leave the box empty to get questions
about the sections you've changed. "Facts change" means a price, time,
condition or yes/no answer differs; "Same facts" means only the wording
changed.

### Update from a photo, PDF or web page

Take a photo of your menu, price list or flyer, or use the PDF of it, and
upload it under **Update from a photo, PDF or web page** (a JPEG, PNG or
WebP photo, or a PDF of up to 10 pages, each up to 5MB), then press
**Read file**. If your menu is on your website, paste the page's address
instead and press **Read page**. The
assistant reads it and suggests changes to your sections (and, if you have
a menu, to your prices). On the review page, tick the changes to keep (you
can edit the wording first), then press **Save ticked changes**, or
**Discard all** to keep nothing. Saved changes still need publishing. The
file itself isn't kept.

- A PDF made on a computer is read exactly, straight from its text. A
  scanned PDF is read like a photo.
- A password-protected PDF can't be read: save a copy without the password.
- A web page is read once, when you press **Read page**. Pages behind a
  login, pages whose menu is a picture, and pages built entirely by a
  script can't be read, and some sites' security checks turn the reader
  away. For any of those, open the page in your browser, save it as a PDF
  (File, Print, Save as PDF) and upload that: a PDF saved that way is read
  exactly. A browser saves only what's showing, so if your menu is in tabs
  (Pizza, Subs, Drinks…), save each tab. Only public web addresses can be
  read.
- iPhone HEIC photos can't be read yet: share the photo as a JPEG, or take
  a screenshot and upload that.
- Something read wrong? On the review page you can type the right price
  into a menu change's **Price to save** box, and fix a section change's
  wording under "Edit the wording before saving". If the reading itself is
  wrong (a $25 read as $26), open the text that was read at the bottom, fix
  it, and press **Suggest again from this text**: the suggestions are made
  again from your version.
- Text in a PDF or web page that can't be seen (white on white, tiny,
  invisible, or hidden by the page) isn't used. The review page lists it at the top, so you can
  see what was left out.

## Menu

The assistant answers price questions from the menu and nowhere else.
Anything not on the menu, it says isn't on the menu rather than guess.

- Click an item to open it and edit it, then press **Save** on that item.
- To add an item: fill in **Add an item** at the bottom and press **Add item**.
- To remove an item: open it and press **Remove item**. You can still discard this before publishing.
- **Discard menu changes** throws away the menu's unpublished changes only (knowledge-base edits are kept).

Each item has:

- **Item**: its name.
- **Other words customers use**: comma-separated. The assistant looks the price up when a customer names the item or one of these words.
- **Knowledge-base section these prices go with**: the section the prices are shown with, so the assistant reads them together.
- **Options and prices**: one row per size or version. To add an option, fill in an empty row (there are always two at the bottom; save to get more). Tick **Remove** on a row to drop it. Change the **Order** numbers, or use the ↑ ↓ buttons, to put options in order.
- **Extras**: toppings and add-ons. **On** says which option an extra can go on ("any" for all of them); an extra not listed for an option can't go on it. Tick **Each** when the price is per piece (3 toppings = 3 × the price).
- **Sold by the piece**: for things like cupcakes or muffins, with a price each, an optional "only in multiples of", a minimum, and pack prices.

Type a price the way you'd write it on the board: 48, from 110 (a
starting price), 20 to 60 (a range), or words like "quoted after a trial".

Saved menu changes are drafts. Preview them on the Knowledge base page,
then publish.

## Settings

Changes here take effect as soon as you press **Save settings**.

- **Taking new bookings?**: set it to No to pause new bookings, say while you're away or fully booked. The assistant keeps answering questions, but anyone asking to book (or part-way through booking) gets your message instead. **What the assistant says instead** starts with a standard wording; change it if you like ({phone} becomes your phone number; clear it to go back to the standard). **Turn bookings back on automatically on** is optional: choose a day and bookings switch back on by themselves that morning. While bookings are paused, every page shows a reminder with a **Change** button.
- Business details: **Business name**, **Phone**, **Address**, **Hours (as shown to customers)** and **Service area**, as the assistant tells customers.
- **After-hours emergency line (if you have one)**: a separate number for emergencies outside your hours, like an on-call dispatcher. The assistant gives it for an emergency out of hours and your main phone for everything else. Leave it blank to use one number.
- **Limit service area?** and **How far will you travel?**: with the limit on, the assistant asks for the customer's address early in the booking and turns away addresses further than your distance. This needs address checking, which your operator sets up; without it the switch is greyed out.
- **Personality**: how the assistant sounds.
- **Services offered**: one per line. These are the only services the assistant will book; anything else, it says you don't offer.
- **What you call a booking**: "appointment", "order", "reservation".
- **Extra booking questions**: up to 5 questions the assistant asks after the service, the address and the date and time. The label is the short name you'll see on the appointment; the question is what the assistant asks. Tick services to ask a question only for those; nothing ticked means every booking. Clear both boxes on a row to drop it.
- **Prices on a menu?**: whether you have a Menu tab. Choose no if you quote per job. It can't be switched off while the menu still has items: remove them on the Menu page and publish first, so no prices vanish by surprise.
- Cancellation rules are shown near the bottom. To change them, press **Change cancellation rules** (or **Set up cancellation rules** if you don't have any yet); see Cancellation rules below.

### Cancellation rules

What a customer is charged for cancelling, by how much notice they give.
The assistant works out the timing and the amounts from these itself.

- Each window is "with at least so many hours or days of notice", and what happens then: no charge, a percentage of the price, or the full price (and, if you take deposits, refund the deposit, keep it, or keep a percentage). The last window, "with less notice than that", covers everything shorter, so there's never a gap.
- Leave a window's notice empty to drop it. You can have up to 5, plus the last one.
- If you take deposits, you can change how much they are.
- To give some services their own rules (colour needs 48 hours, everything else 24), tick them under **Different rules for some services**: each opens its own box straight away, starting the same as everything else, so change only what should differ, with its own windows, then save. **Remove** on a service's box (or unticking it) puts it back under everything else. Only the services you tick: a combined one, like a cut and colour, has its own tick, otherwise it follows the rules for everything else.
- Notes are said as written ("A no-show is charged the same as cancelling late."), so keep any numbers in them in step with the windows.
- Press **Save as draft**. Like a menu change, customers keep hearing the current rules until you press **Publish changes**; the page shows what they're told now and after you publish, and **Preview answers** on the Knowledge base page lets you ask the assistant about it first. **Discard all changes** undoes it.
- **Remove these rules** (also a draft) means the assistant stops quoting any charges for cancelling.
- If your knowledge base also talks about cancelling, the page names those sections: once your rules are published, take the cancelling part out of them, so customers aren't told two different things.

## Things only your operator can change

For any of these, ask the assistant on the Help page: it drafts a message
to support for you to check and send. Or write one yourself under
**Send to support**.

- The kinds of order your cancellation rules tell apart (for example, custom and wedding cakes).
- Your texting phone number.
- Connecting or changing your Google Calendar, and the hours bookings can be made in.
- Address checking for the service area.
- Logins: adding a person, or changing a password.
- Anything that looks broken.

## Help

On the Help page you can ask the owner assistant how to do something
(**Ask the assistant**), or write to a person with **Send to support**.

The same assistant is on every page, behind the **Ask the assistant**
button in the bottom corner: it opens a panel over the page you're on,
with the same conversation as the Help page, and stays open as you move
from page to page. Press **Close** (or Esc) to put it away. When it
changes something, the page refreshes to show the change, unless you have
unsaved edits on it, in which case it tells you instead.

The assistant can change your knowledge base for you: ask it ("we now
close at 6 on Saturdays", "add that we take Apple Pay", "add a section
about gift cards", "delete the drywall section"). It changes only the
words that need changing and leaves the rest of the section as it was.
It won't write a number you didn't give it, and it won't put menu prices
or cancellation rules in a section: menu prices belong on the Menu, and
cancellation rules on the Cancellation rules page.

If you have a Menu tab, it can change your menu too ("raise the large
pizza to $24", "add a calzone at $12", "take the gluten-free crust off"):
prices, options, extras and items.

Every change it makes is a draft, exactly as if you'd made it yourself:
it's listed under "Saved as a draft" with the words before and after, the
banner lists it, and customers don't see it until you press
**Publish changes**. Check it with **Preview answers** first if you like. To undo
it, ask the assistant to change it back, use the section's **History**,
or press **Discard all changes** (or **Discard menu changes** on the Menu
page). It doesn't change settings or appointments; for those it tells you
where to go.
The support message carries the page you were on and the version of the
app, never your customers' conversations or appointments, and you'll see
the reply on the Help page.
