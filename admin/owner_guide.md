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

### Update from a photo

Take a photo of your menu, price list or flyer and upload it under
**Update from a photo** (JPEG, PNG or WebP, up to 5MB), then press
**Read photo**. The assistant reads it and suggests changes to your
sections (and, if you have a menu, to your prices). On the review page,
tick the changes to keep (you can edit the wording first), then press
**Save ticked changes**, or **Discard all** to keep nothing. Saved changes
still need publishing. The photo itself isn't kept. iPhone HEIC photos
can't be read yet: share the photo as a JPEG, or take a screenshot and
upload that.

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
- **Limit service area?** and **How far will you travel?**: with the limit on, the assistant asks for the customer's address early in the booking and turns away addresses further than your distance. This needs address checking, which your operator sets up; without it the switch is greyed out.
- **Personality**: how the assistant sounds.
- **Services offered**: one per line. These are the only services the assistant will book; anything else, it says you don't offer.
- **What you call a booking**: "appointment", "order", "reservation".
- **Extra booking questions**: up to 5 questions the assistant asks after the service, the address and the date and time. The label is the short name you'll see on the appointment; the question is what the assistant asks. Tick services to ask a question only for those; nothing ticked means every booking. Clear both boxes on a row to drop it.
- **Prices on a menu?**: whether you have a Menu tab. Choose no if you quote per job. It can't be switched off while the menu still has items: remove them on the Menu page and publish first, so no prices vanish by surprise.
- Cancellation and deposit rules, if you have them, are shown at the bottom, read-only. The assistant applies them exactly. To change them, ask your operator (the assistant on the Help page drafts the message for you).

## Things only your operator can change

For any of these, ask the assistant on the Help page: it drafts a message
to support for you to check and send. Or write one yourself under
**Send to support**.

- Cancellation and deposit rules.
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
changes your menu, the page refreshes to show the change, unless you have
unsaved edits on it, in which case it tells you instead.

If you have a Menu tab, the assistant can also change your menu for you:
ask it ("raise the large pizza to $24", "add a calzone at $12", "take the
gluten-free crust off"). It changes prices, options, extras and items,
and saves each change as a draft, exactly as if you'd made it on the Menu
page: it's listed under "Saved as a draft" and in the banner, and
customers don't see it until you press **Publish changes**. To undo it,
ask the assistant to change it back, or press **Discard menu changes** on
the Menu page. It doesn't change anything else (knowledge-base sections,
settings or appointments); for those it tells you where to go.
The support message carries the page you were on and the version of the
app, never your customers' conversations or appointments, and you'll see
the reply on the Help page.
