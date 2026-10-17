#!/usr/bin/env python3
"""web_import_test.py — reading a menu from a web page, and the checks that
keep a typed URL from reaching anything but a public web site.

Run it:  python3 web_import_test.py

The address checks (SSRF) are the point of this file: each refused address
below is a real way people have made a server fetch its own internals
(the cloud metadata address, localhost written as a number, an IPv6
wrapper around 127.0.0.1, a name that resolves to a private address, a
public page that redirects to one). The network is faked: a stand-in
resolver and opener, so no test touches the internet. The opener's own
mechanics (pinning the address, the size cap, the certificate check) are
tested against a local server, with the address check bypassed, since a
local server is exactly what the check refuses.
"""

import http.server
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="web_import_test_")) / "test.db"

import booking_state_test as helpers    # stubs the integrations llm imports

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def fake_resolver(table):
    def resolve(host, port, type=None):
        if host not in table:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (a, port)) for a in table[host]]
    return resolve


NAMES = fake_resolver({
    "menu.example": ["93.184.215.14"],
    "other.example": ["93.184.215.15"],
    "both.example": ["93.184.215.14", "10.0.0.5"],
    "meta.example": ["169.254.169.254"],
    "v6private.example": ["fd00::1"],
    "v6public.example": ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
})


def reply(text):
    return types.SimpleNamespace(usage=None, content=[types.SimpleNamespace(type="text", text=text)])


def main():
    helpers._load_scheduler()
    import llm
    import photo_import
    import web_import as w
    db.init_db()

    heading("addresses that are refused before anything is fetched")
    PRIVATE, OTHER = "private network", None
    refused = {
        "http://localhost/admin": ("localhost", PRIVATE),
        "http://localhost:5001/admin": ("localhost on the app's own port", "port"),
        "http://127.0.0.1/": ("loopback", PRIVATE),
        "http://2130706433/": ("127.0.0.1 as one number", PRIVATE),
        "http://0x7f000001/": ("127.0.0.1 in hex", PRIVATE),
        "http://127.1/": ("127.0.0.1 shortened", PRIVATE),
        "http://0.0.0.0/": ("0.0.0.0", PRIVATE),
        "http://[::1]/": ("IPv6 loopback", PRIVATE),
        "http://[::ffff:127.0.0.1]/": ("127.0.0.1 wrapped in IPv6", PRIVATE),
        "http://[2002:7f00:1::]/": ("127.0.0.1 inside a 6to4 address (which Python calls global)", PRIVATE),
        "http://[64:ff9b::7f00:1]/": ("127.0.0.1 through NAT64", PRIVATE),
        "http://169.254.169.254/latest/meta-data/": ("the cloud metadata address", PRIVATE),
        "http://10.0.0.5/": ("a private network", PRIVATE),
        "http://192.168.1.1/": ("a home router", PRIVATE),
        "http://100.64.0.1/": ("carrier-grade NAT", PRIVATE),
        "http://224.0.0.1/": ("multicast", PRIVATE),
        "https://meta.example/": ("a name resolving to the metadata address", PRIVATE),
        "https://both.example/": ("a name resolving to a public AND a private address", PRIVATE),
        "https://v6private.example/": ("a name resolving to private IPv6", PRIVATE),
        "https://printer.local/": ("a .local name", PRIVATE),
        "ftp://menu.example/menu": ("not http(s)", "Only web addresses"),
        "file:///etc/passwd": ("a file on the server", "Only web addresses"),
        "gopher://menu.example/": ("gopher", "Only web addresses"),
        "https://owner:secret@menu.example/": ("a name and password in the address", "password"),
        "https://menu.example:8443/": ("an unusual port", "port"),
        "https://nosuch.example/": ("a site that doesn't exist", "Couldn't find"),
        "": ("nothing typed", "Paste"),
    }
    for url, (why, reason) in refused.items():
        try:
            scheme, host, port, path = w.check_url(url)
            w.resolve(host, port, NAMES)
            said = None
        except w.WebProblem as e:
            said = str(e)
        check(f"refused: {why} ({url or 'empty'})", said is not None and reason in said, said)
    for url, expect in (("https://menu.example/menu", "93.184.215.14"),
                        ("menu.example/menu", "93.184.215.14"),
                        ("https://v6public.example/", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"),
                        ("HTTPS://Menu.Example./menu?x=1", "93.184.215.14")):
        scheme, host, port, path = w.check_url(url)
        check(f"allowed: {url} → {expect}", w.resolve(host, port, NAMES) == expect)
    check("an address with no http(s):// gets https://",
          w.check_url("menu.example/menu") == ("https", "menu.example", 443, "/menu"))
    check("the query string is kept", w.check_url("https://menu.example/m?day=fri")[3] == "/m?day=fri")

    heading("fetching, with the network faked")
    calls = []

    def opener_from(responses):
        def opener(scheme, address, host, port, path, deadline):
            calls.append((scheme, address, host, port, path))
            return responses.pop(0)
        return opener
    page = b"<html><body><p>Large 18 inch pizza $25</p></body></html>"
    calls.clear()
    got = w.fetch("https://menu.example/menu", NAMES,
                  opener_from([(200, {"content-type": "text/html; charset=utf-8"}, page)]))
    check("the connection goes to the checked address, with the name for Host and TLS",
          calls == [("https", "93.184.215.14", "menu.example", 443, "/menu")], calls)
    check("…and returns the body, type and charset",
          got["body"] == page and got["type"] == "text/html" and got["charset"] == "utf-8")
    calls.clear()
    got = w.fetch("https://menu.example/old", NAMES, opener_from([
        (301, {"location": "/new"}, b""),
        (302, {"location": "https://other.example/menu"}, b""),
        (200, {"content-type": "text/html"}, page)]))
    check("redirects are followed, relative and absolute, each resolved again",
          [c[1] for c in calls] == ["93.184.215.14", "93.184.215.14", "93.184.215.15"]
          and got["url"] == "https://other.example/menu", calls)
    for location, why in (("http://169.254.169.254/latest/meta-data/", "the metadata address"),
                          ("http://localhost/admin", "localhost"),
                          ("https://both.example/", "a name with a private address"),
                          ("file:///etc/passwd", "a file")):
        calls.clear()
        try:
            w.fetch("https://menu.example/menu", NAMES,
                    opener_from([(302, {"location": location}, b""),
                                 (200, {"content-type": "text/html"}, b"secret")]))
            ok = False
        except w.WebProblem:
            ok = len(calls) == 1
        check(f"a public page redirecting to {why} is refused at the redirect", ok, calls)
    try:
        w.fetch("https://menu.example/loop", NAMES,
                opener_from([(302, {"location": "/loop"}, b"")] * (w.MAX_REDIRECTS + 2)))
        ok = False
    except w.WebProblem as e:
        ok = "redirected too many times" in str(e)
    check(f"more than {w.MAX_REDIRECTS} redirects is refused", ok)
    for status, words in ((404, "no page"), (403, "refused to let the reader in"), (401, "login"),
                          (429, "slow down"), (500, "error (500)")):
        try:
            w.fetch("https://menu.example/x", NAMES, opener_from([(status, {}, b"")]))
            ok = False
        except w.WebProblem as e:
            ok = words in str(e)
        check(f"a {status} is explained in words", ok)

    for headers, body, why in (({"cf-mitigated": "challenge", "server": "cloudflare"}, b"", "Cloudflare's header"),
                               ({"server": "cloudflare"}, b"<title>Just a moment...</title>", "a challenge page"),
                               ({}, b"<html><title>Attention Required! | Cloudflare</title>", "a block page")):
        try:
            w.fetch("https://menu.example/x", NAMES, opener_from([(403, headers, body)]))
            said = ""
        except w.WebProblem as e:
            said = str(e)
        check(f"bot protection is named as such ({why}), not mistaken for a login",
              "security check" in said and "Save as PDF" in said and "login" not in said, said)

    heading("the opener's own mechanics, against a local server")
    big = b"x" * (w.MAX_BYTES + 10)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = big if self.path == "/big" else (
                f"<p>host={self.headers.get('Host')} path={self.path}</p>".encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, headers, body = w._open("http", "127.0.0.1", "menu.example", port,
                                        "/menu?day=fri", time.monotonic() + 10)
        check("it connects to the address given, sending the checked name as Host",
              status == 200 and b"host=menu.example" in body and b"path=/menu?day=fri" in body, body)
        try:
            w._open("http", "127.0.0.1", "menu.example", port, "/big", time.monotonic() + 10)
            ok = False
        except w.WebProblem as e:
            ok = "too large" in str(e)
        check("a page over 2MB is refused while it's still arriving", ok)
    finally:
        server.shutdown()

    tmp = Path(tempfile.mkdtemp(prefix="web_tls_"))
    made = shutil.which("openssl") and subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=menu.example", "-keyout", str(tmp / "k.pem"), "-out", str(tmp / "c.pem")],
        capture_output=True).returncode == 0
    if made:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(tmp / "c.pem", tmp / "k.pem")
        tls = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        tls.socket = context.wrap_socket(tls.socket, server_side=True)
        threading.Thread(target=tls.serve_forever, daemon=True).start()
        try:
            w._open("https", "127.0.0.1", "menu.example", tls.server_address[1], "/",
                    time.monotonic() + 10)
            ok = False
        except w.WebProblem as e:
            ok = "certificate" in str(e)
        finally:
            tls.shutdown()
        check("a site whose certificate doesn't check out isn't read", ok)
    else:
        print("  (skipped the certificate check: openssl isn't available)")
    src = (ROOT / "web_import.py").read_text()
    check("no proxy from the environment is used, and redirects aren't followed by the library",
          "ProxyManager(" not in src and "proxy_from_url" not in src and "redirect=False" in src
          and "retries=False" in src)

    heading("a web page as text")
    menu_html = (ROOT / "evals" / "photos" / "crosstown_menu.html").read_text()
    text, hidden, title = w.page_text(menu_html)
    lines = text.splitlines()
    check("table rows and list items keep each item with its price, on one line",
          'Large 18" $25' in lines and "20 wings $22" in lines
          and "Calzone with ricotta & mozzarella $14" in lines, lines)
    check("scripts and styles aren't text", "dataLayer" not in text and "font-family" not in text)
    check("the page title is kept apart", title == "Menu | Crosstown Pizza Co." and not hidden)
    trick = (ROOT / "evals" / "photos" / "hidden_text.html").read_text()
    text, hidden, _ = w.page_text(trick)
    joined = " ".join(hidden)
    check("hidden text is set aside: display:none, the hidden attribute, size 0",
          "NOTE TO THE AI" in joined and "Delivery is free everywhere" in joined
          and "all pizzas are now $0" in joined, hidden)
    check("…and none of it is in the text, nor the comment's instructions (comments aren't listed: "
          "on real sites they're plugin noise)",
          not any(h in text for h in hidden) and "allergen" not in text and "allergen" not in joined
          and "$0" not in text and 'Any sub on a 10" roll $13' in text.splitlines(), text)
    t, h, _ = w.page_text('<p>Wings <span aria-hidden="true">$12</span></p><!--[if IE]><p>x</p><![endif]-->'
                          '<img src="m.png" alt="Calzones $14">')
    check("aria-hidden is visible text (it hides from screen readers, not eyes); "
          "short comment noise and image alt text are handled",
          "Wings $12" in t and not h and "Calzones $14" in t, (t, h))
    check("bytes are decoded by the header, a meta charset, or UTF-8, never failing",
          w.decode("café".encode("latin-1"), "latin-1") == "café"
          and w.decode(b'<meta charset="iso-8859-1">caf\xe9') .endswith("café")
          and "�" in w.decode(b"caf\xe9"))

    heading("the whole read, by what the link points at")
    config = {"business": {"name": "Crosstown Pizza"}}
    real_create = llm._create
    sent = []
    llm._create = lambda **c: (sent.append(c), reply('Large 18" $25'))[1]
    try:
        text, how, hidden, final = w.read_page(config, "https://menu.example/menu", NAMES,
                                               opener_from([(200, {"content-type": "text/html"},
                                                             trick.encode())]))
        check("an HTML page is read as 'web', with no model call, hidden text returned",
              how == "web" and sent == [] and hidden and "NOTE TO THE AI" not in text
              and final == "https://menu.example/menu")
        jpeg = (ROOT / "evals" / "photos" / "crosstown_menu.jpg").read_bytes()
        text, how, hidden, _ = w.read_page(config, "https://menu.example/menu.jpg", NAMES,
                                           opener_from([(200, {"content-type": "image/jpeg"}, jpeg)]))
        check("a link to a picture is read like an uploaded photo", how == "photo" and len(sent) == 1)
        try:
            import pypdf  # noqa: F401
            pdf = (ROOT / "evals" / "photos" / "hidden_text.pdf").read_bytes()
            text, how, hidden, _ = w.read_page(config, "https://menu.example/menu.pdf", NAMES,
                                               opener_from([(200, {"content-type": "application/pdf"}, pdf)]))
            check("a link to a PDF is read like an uploaded PDF, hidden text set aside",
                  how == "pdf_text" and any("NOTE TO THE AI" in r for r in hidden))
        except ImportError:
            print("  (skipped the PDF link: pypdf isn't installed)")
        for body, ctype, words in ((b"<html><body><div id='app'></div></body></html>", "text/html", "much text"),
                                   (b"PK\x03\x04zip", "application/zip", "isn't a web page")):
            try:
                w.read_page(config, "https://menu.example/x", NAMES,
                            opener_from([(200, {"content-type": ctype}, body)]))
                ok = False
            except w.WebProblem as e:
                ok = words in str(e)
            check(f"{'a page built by script (no text)' if 'html' in ctype else 'a zip file'} is "
                  f"explained in words", ok)
    finally:
        llm._create = real_create
    check("the review page calls it a web page", photo_import.source_word("web") == "web page")

    heading("the route and the page")
    routes = (ROOT / "admin" / "routes.py").read_text()
    route = routes.split("def knowledge_upload_url(")[1].split("\n@admin_bp")[0]
    check("the route checks business access, then the address, then the rate limit",
          "require_business_access(business_id)" in route[:400]
          and route.index("web_import.check_url(url)") < route.index("ratelimit.knowledge_upload(")
          and "save_upload_proposal(" in route and "origin=final_url" in route)
    check("any failure is told in words and logged, never a 500",
          "except (web_import.WebProblem, photo_import.UploadProblem)" in route
          and "log.exception(" in route)
    page = (ROOT / "admin" / "templates" / "admin" / "knowledge.html").read_text()
    form = page.split("knowledge_upload_url")[1].split("</form>")[0]
    check("the knowledge page has the address form, with a CSRF token",
          'name="url"' in form and "csrf_token()" in form and "Read page" in form)
    review = (ROOT / "admin" / "templates" / "admin" / "knowledge_upload.html").read_text()
    check("the review page says where it was read from, as a link that can't reach back",
          "proposal.origin" in review and 'rel="noopener noreferrer nofollow"' in review)
    db.save_upload_proposal(7, "x", [], source="web", hidden=["h"], origin="https://menu.example/menu")
    check("the address is stored with the proposal",
          db.get_upload_proposal(7)["origin"] == "https://menu.example/menu")
    reqs = (ROOT / "requirements.txt").read_text()
    check("urllib3 is in requirements.txt (imported directly)", "\nurllib3==" in reqs)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
