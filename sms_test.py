#!/usr/bin/env python3
"""sms_test.py — what a reply becomes as a text, and the simulator's wiring.

Run it:  python3 sms_test.py

Texts are billed per segment, and one curly quote turns a whole text into
Unicode (70 characters a segment instead of 160), so the punctuation swap
and the segment count are worth pinning down exactly. Then: invented 555
numbers can never be texted for real, a notice to one waits for the
simulator to pick it up, and the Twilio adapter and the simulator share one
path (which is how the adapter's NameError was found, 2026-10-12).
No network, no model, no Flask.
"""

import re
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="sms_test_")) / "test.db"

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def main():
    import sms

    heading("segments")
    seg = sms.segments
    check("160 plain characters is one text, 161 is two", seg("a" * 160)["segments"] == 1
          and seg("a" * 161)["segments"] == 2)
    check("a long plain text splits at 153 a segment", seg("a" * 306)["segments"] == 2
          and seg("a" * 307)["segments"] == 3)
    check("{ } [ ] ~ ^ | \\ € count double in plain texts",
          seg("{" * 80)["units"] == 160 and seg("{" * 81)["segments"] == 2)
    check("one em dash makes the whole text Unicode, and says so",
          seg("Yes — we deliver.")["encoding"] == "Unicode" and seg("Yes — we deliver.")["because"] == "—")
    check("Unicode: 70 a text, 67 a segment after that",
          seg("é—" + "a" * 68)["segments"] == 1 and seg("—" + "a" * 70)["segments"] == 2
          and seg("—" + "a" * 134)["segments"] == 3)
    check("an emoji counts 2 (it's two UTF-16 units)", seg("🍕" * 35)["units"] == 70
          and seg("🍕" * 35)["segments"] == 1 and seg("🍕" * 36)["segments"] == 2)
    check("é, ñ, ü and friends are in the plain alphabet", seg("café niño über")["encoding"] == "GSM-7")
    check("nothing is no segments", seg("")["segments"] == 0)

    heading("plain punctuation (for_sms)")
    out = sms.for_sms("“Yes” — it’s $22… see you soon")
    check("curly quotes, dashes, ellipsis and odd spaces become plain ones",
          out == "\"Yes\" - it's $22... see you soon", out)
    reply = "A large is “$22” — a medium is $17. Want one?"
    check("which keeps a typical reply in one plain text",
          seg(reply)["encoding"] == "Unicode" and seg(sms.for_sms(reply)) ==
          {"encoding": "GSM-7", "segments": 1, "units": len(sms.for_sms(reply)), "because": None})
    check("emoji are left alone (the business allows them; the cost is shown)",
          sms.for_sms("Anytime! 🍕") == "Anytime! 🍕")

    heading("invented numbers")
    check("+1555 and seven digits is the simulator's", sms.is_simulated("+15554821934"))
    check("a real number isn't", not sms.is_simulated("+15855550177")
          and not sms.is_simulated("+1555482193") and not sms.is_simulated("web_abc"))
    import notify
    check("a notice to an invented number waits for the simulator, never SMS",
          notify.channel_for("+15554821934") == ("textsim", None))
    check("a real number is still the SMS channel", notify.channel_for("+15855550177")[0] == "sms")
    import inspect
    src = inspect.getsource(notify.channel_for)
    check("…checked before real numbers (a 555 number matches the phone pattern too)",
          src.index("sms.is_simulated(recipient)") < src.index("_PHONE.match(recipient)"))
    db.init_db()
    conn = db.get_connection()
    conn.execute("INSERT INTO businesses (id, name, slug, config_path) VALUES (7, 'Crosstown', "
                 "'crosstown_pizza', 'config/crosstown_pizza.yaml')")
    conn.commit(); conn.close()
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "crosstown_pizza.yaml").read_text())
    appt = {"id": 41, "business_id": 7, "phone": "+15554821934", "service": "party tray",
            "datetime": "2099-10-16 17:00", "customer_name": "Dana"}
    status, reason = notify.notify_change(appt, cfg, "cancelled")
    picked = db.collect_notifications(7, "+15554821934", "2099-01-01T00:00:00")
    check("…and the simulator's pickup hands it over once",
          status == "pending" and reason is None and len(picked) == 1
          and db.collect_notifications(7, "+15554821934", "2099-01-01T00:00:01") == [],
          (status, reason, picked))

    heading("one path for Twilio and the simulator")
    app_src = (ROOT / "app.py").read_text()
    handler = app_src.split("def sms_reply(")[1].split("\ndef ")[0]
    handler = "\n".join(l for l in handler.splitlines() if not l.strip().startswith("#"))
    check("the Twilio adapter finds the business before anything uses its name",
          handler.index("get_business_by_number") < handler.index("sms_turn(")
          and 'business["name"]' not in handler.split("get_business_by_number")[0])
    turn = app_src.split("def sms_turn(")[1].split("\n@app.route")[0]
    check("both go through sms_turn, which uses the SMS channel and plain punctuation",
          'channel="sms"' in turn and "sms.for_sms(reply_text)" in turn
          and app_src.count("= sms_turn(business") == 2)
    sim = app_src.split("def textsim_reply(")[1].split("\n@app.route")[0]
    check("the simulator only takes a number it signed, caps length, and is rate limited",
          '_sim_number_from(data.get("token"))' in sim and "len(message) > 1000" in sim
          and "ratelimit.webchat(slug, phone, _client_ip())" in sim)
    check("its updates endpoint only reads a signed number's notices",
          '_sim_number_from(request.args.get("token"))'
          in app_src.split("def textsim_updates(")[1].split("\n@app.route")[0])
    check("a signed number must still be an invented one",
          "_SIM_NUMBER.fullmatch(phone)" in app_src.split("def _sim_number_from(")[1].split("\ndef ")[0])
    check("its regex is the same shape sms.is_simulated accepts",
          '_SIM_NUMBER = re.compile(r"\\+1555\\d{7}")' in app_src)
    csrf = (ROOT / "admin" / "csrf.py").read_text()
    check("exempt from the form token like the web chat (JSON, rate limited)",
          '"textsim_reply",' in csrf)
    page = (ROOT / "templates" / "text.html").read_text()
    check("the page shows replies as text, never markup", "innerHTML" not in page
          and "textContent" in page)
    check("the page shows the server's number and posts only its signed token",
          "{{ sim_phone | tojson }}" in page and "token: token" in page
          and "Math.random" not in page and "phone: phone" not in page)
    check("the web chat links to it", "url_for('text_page'" in (ROOT / "templates" / "demo.html").read_text())

    # A demo copy can be cleared under an open tab (an hour idle, or a server
    # restart). The page used to ask for updates every 10 seconds forever,
    # a 404 in the log each time (2026-10-15). Both chat pages now stop.
    for name in ("demo.html", "text.html"):
        src = (ROOT / "templates" / name).read_text()
        check(f"{name}: a 404 on the update check stops the checking",
              "const updatesTimer = setInterval(checkForUpdates, 10000);" in src
              and "clearInterval(updatesTimer);" in src
              and src.count("if (res.status === 404) {") >= 2)   # demo.html's history too
        check(f"{name}: it says so once, links to a new demo and closes the input",
              "if (ended) return;" in src and "url_for('demo_picker')" in src
              and "input.disabled = true;" in src and "if (inFlight || ended) return;" in src)
        check(f"{name}: the link is built as an element, not markup",
              'document.createElement("a")' in src and ".innerHTML =" not in src)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
