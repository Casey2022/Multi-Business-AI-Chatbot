"""web_import.py — read a menu from the business's own web page.

Added 2026-10-16 (photo upload phase 2, after PDFs). The owner pastes a
link ("https://crosstownpizza.com/menu") and gets the same review page a
photo or PDF gives: what was read, the suggested changes, the number
checks, nothing saved until they tick and save, nothing live until Publish.

The hard part isn't reading HTML, it's that a URL the owner types makes
OUR server send a request. Without care that's server-side request
forgery (SSRF): "http://169.254.169.254/latest/meta-data/" asks the cloud
host's metadata service for its credentials, "http://localhost:5001/admin"
reaches the app from inside, "http://10.0.0.5" walks the private network.
So, in order (fetch):

1. Only http and https, only ports 80 and 443, no "user:password@".
2. The host name is resolved once, and EVERY address it resolves to must
   be a public internet address (ipaddress' is_global): not loopback,
   private, link-local (the metadata address), multicast, reserved,
   unique-local IPv6, or an IPv4 address wrapped in IPv6. A literal like
   "2130706433" or "0x7f000001" (127.0.0.1 in disguise) is parsed the way
   a socket would read it, and refused.
3. The connection goes to the address that was checked, not the name
   (DNS rebinding: a name that answers "public" to the check and "private"
   to the request a moment later). TLS still checks the certificate
   against the name.
4. Redirects are followed by hand, at most MAX_REDIRECTS, and each one is
   checked from step 1 again: a public page that redirects to localhost is
   refused at the redirect.
5. Deadlines (connect, read, and a total) well inside the worker's 60s;
   at most MAX_BYTES read; proxies from the environment are not used.

What comes back is routed by its type: an HTML page is turned into text
here (page_text), a PDF or an image goes through photo_import exactly as
an upload would. A web page can hide text like a PDF can (display:none,
the hidden attribute, comments): that is set aside and listed for the
owner, never given to the model.
"""

import ipaddress
import logging
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

log = logging.getLogger("web_import")

MAX_URL = 2000
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_SECONDS = 5
READ_SECONDS = 10
TOTAL_SECONDS = 15
PORTS = {"http": 80, "https": 443}
USER_AGENT = "SimonSaysMenuReader/1.0 (reads a page its owner asked for)"
MIN_TEXT_CHARS = 40


class WebProblem(Exception):
    """Something the owner can act on, shown to them as is."""


# ---------------------------------------------------------------------------
# Is this address somewhere we may go?
# ---------------------------------------------------------------------------

def check_url(url):
    """(scheme, host, port, path-with-query) of an acceptable URL, or
    WebProblem. Nothing is resolved here."""
    url = (url or "").strip()
    if not url:
        raise WebProblem("Paste the address of the page with your menu or prices.")
    if len(url) > MAX_URL:
        raise WebProblem("That address is too long.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url                       # "crosstownpizza.com/menu"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise WebProblem("That doesn't look like a web address.")
    scheme = parts.scheme.lower()
    if scheme not in PORTS:
        raise WebProblem("Only web addresses (http:// or https://) can be read.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise WebProblem("Remove the name and password from the address.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise WebProblem("That address has no site name in it.")
    if port is not None and port != PORTS[scheme]:
        raise WebProblem("Only ordinary web addresses can be read (no :port numbers).")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, PORTS[scheme], path


def _literal_ip(host):
    """The address a host written as a number stands for ("127.1",
    "2130706433", "0x7f000001", "::1"), or None for a name. inet_aton reads
    the short and numeric forms the way connect() would."""
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fx.]+", host, re.I):
        try:
            return ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def public(address):
    """Whether we may connect to this address: a public internet address,
    and not an IPv4 address wrapped in IPv6. Current Pythons already call
    the wrapped forms (::ffff:, 2002::, Teredo) non-global; the explicit
    check is a second lock, since that list has changed between versions."""
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip in ipaddress.ip_network("64:ff9b::/96"):
        # NAT64: an IPv4 address carried in IPv6, reached through a gateway.
        return public(str(ipaddress.ip_address(int(ip) & 0xFFFFFFFF)))
    if ip.version == 6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        return inner is not None and inner.is_global and public(str(inner))
    return ip.is_global and not ip.is_multicast


def resolve(host, port, resolver=None):
    """The one address to connect to, after checking EVERY address the
    name resolves to. Refused if any of them is private: a name pointing at
    both a public and a private address is a trick, not a mistake."""
    literal = _literal_ip(host)
    if literal is not None:
        if not public(str(literal)):
            raise WebProblem("That address points inside a private network, so it can't be read.")
        return str(literal)
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise WebProblem("That address points inside a private network, so it can't be read.")
    resolver = resolver or socket.getaddrinfo
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise WebProblem(f"Couldn't find a site at “{host}”. Check the address.")
    addresses = []
    for info in infos:
        address = info[4][0].split("%")[0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise WebProblem(f"Couldn't find a site at “{host}”. Check the address.")
    if not all(public(a) for a in addresses):
        log.warning("Web import refused %s: it resolves to a non-public address", host)
        raise WebProblem("That address points inside a private network, so it can't be read.")
    # Prefer IPv4: some hosts' IPv6 routes are broken.
    return next((a for a in addresses if ":" not in a), addresses[0])


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _open(scheme, address, host, port, path, deadline):
    """(status, headers {lowercase: value}, body bytes) for one GET to the
    checked address, with the host name for the Host header and TLS. The
    network part: tests replace it."""
    import urllib3
    timeout = urllib3.Timeout(connect=CONNECT_SECONDS,
                              read=max(1.0, min(READ_SECONDS, deadline - time.monotonic())))
    if scheme == "https":
        import ssl
        context = ssl.create_default_context()
        try:
            import certifi
            context.load_verify_locations(certifi.where())
        except Exception:
            pass
        pool = urllib3.HTTPSConnectionPool(address, port=port, timeout=timeout, retries=False,
                                           maxsize=1, server_hostname=host,
                                           assert_hostname=host, ssl_context=context)
    else:
        pool = urllib3.HTTPConnectionPool(address, port=port, timeout=timeout, retries=False,
                                          maxsize=1)
    try:
        from urllib.parse import quote
        response = pool.urlopen("GET", quote(path, safe="/?=&%:+,;@!$'()*~#[]"),
                                redirect=False, preload_content=False,
                                assert_same_host=False, release_conn=False,
                                headers={"Host": host,
                                         "User-Agent": USER_AGENT,
                                         "Accept": "text/html,application/pdf,image/*;q=0.8,*/*;q=0.5",
                                         "Accept-Encoding": "identity"})
        body = bytearray()
        for chunk in response.stream(64 * 1024, decode_content=False):
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise WebProblem("That page is too large to read (over 2MB).")
            if time.monotonic() > deadline:
                raise WebProblem("That site took too long to answer. Try again, or upload a "
                                 "photo or PDF of the menu instead.")
        headers = {k.lower(): v for k, v in response.headers.items()}
        return response.status, headers, bytes(body)
    except WebProblem:
        raise
    except urllib3.exceptions.SSLError:
        raise WebProblem("That site's security certificate didn't check out, so it wasn't read.")
    except (urllib3.exceptions.TimeoutError, socket.timeout):
        raise WebProblem("That site took too long to answer. Try again, or upload a photo "
                         "or PDF of the menu instead.")
    except urllib3.exceptions.HTTPError as e:
        log.info("Web import: couldn't reach %s: %s", host, type(e).__name__)
        raise WebProblem(f"Couldn't reach “{host}”. Check the address, or upload a photo "
                         "or PDF of the menu instead.")
    finally:
        pool.close()


def fetch(url, resolver=None, opener=None):
    """{"url": where it ended up, "type": media type, "body": bytes} for a
    URL that passes every check, following up to MAX_REDIRECTS redirects,
    each checked again. WebProblem otherwise."""
    opener = opener or _open
    deadline = time.monotonic() + TOTAL_SECONDS
    for hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = check_url(url)
        address = resolve(host, port, resolver)
        status, headers, body = opener(scheme, address, host, port, path, deadline)
        if status in (301, 302, 303, 307, 308):
            location = headers.get("location")
            if not location:
                raise WebProblem("That page sent us somewhere without saying where.")
            url = urljoin(f"{scheme}://{host}{path}", location)
            continue
        if status in (401, 403):
            raise WebProblem("That page needs a login, or turned us away, so it couldn't be "
                             "read. Upload a photo or PDF of the menu instead.")
        if status == 404:
            raise WebProblem("There's no page at that address (404). Check the link.")
        if status >= 400:
            raise WebProblem(f"That site answered with an error ({status}). Try again later, "
                             "or upload a photo or PDF of the menu instead.")
        media = (headers.get("content-type") or "").split(";")[0].strip().lower()
        return {"url": f"{scheme}://{host}{path}", "type": media, "body": body,
                "charset": _charset(headers.get("content-type") or "")}
    raise WebProblem("That page redirected too many times.")


def _charset(content_type):
    m = re.search(r"charset=([\w.-]+)", content_type, re.I)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# A page as text
# ---------------------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object",
         "canvas", "select", "button"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav",
          "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
          "table", "br", "hr", "blockquote", "pre", "figure", "figcaption", "address",
          "form", "fieldset", "details", "summary"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed",
         "source", "track", "wbr", "param"}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|"
                           r"font-size\s*:\s*0(?![.\d])|opacity\s*:\s*0(?![.\d])", re.I)


class _PageText(HTMLParser):
    """Visible text with line breaks where the page breaks lines; hidden
    text (display:none, visibility:hidden, the hidden attribute, zero size
    or opacity, comments) collected separately. Table cells are joined on
    one line, so an item and its price stay together."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.line, self.hidden = [], [], []
        self.stack = []                    # (tag, skipped, hidden) per open element
        self.title = ""
        self._in_title = False

    def _skipping(self):
        return any(s for _, s, _ in self.stack)

    def _hiding(self):
        return any(h for _, _, h in self.stack)

    def _break(self):
        text = " ".join(" ".join(self.line).split())
        if text:
            self.lines.append(text)
        self.line = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK:
            self._break()
        if tag in ("td", "th"):
            self.line.append("  ")
        if tag == "img" and a.get("alt") and not self._skipping() and not self._hiding():
            self.line.append(a["alt"])
        if tag in _VOID:
            return
        # aria-hidden isn't counted: it hides text from screen readers, not
        # from the eye (icons, decorative prices), so it's visible content.
        hidden = "hidden" in a or bool(_HIDDEN_STYLE.search(a.get("style", "")))
        self.stack.append((tag, tag in _SKIP, hidden))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        # Close up to the matching open element (forgiving of bad HTML).
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if self.stack[i][2]:
                    self.line.append(" ")        # an inline hidden span doesn't break the line
                del self.stack[i:]
                break
        if tag in _BLOCK:
            self._break()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skipping():
            return
        if self._hiding():
            words = " ".join(data.split())
            if words:
                self.hidden.append(words)
            return
        self.line.append(data)

    def handle_comment(self, data):
        words = " ".join(data.split())
        if words.startswith(("[if", "[endif", "<![endif")):    # old IE conditionals
            return
        if len(re.findall(r"[A-Za-z]", words)) >= 12:          # not a stray marker
            self.hidden.append(words)

    def result(self):
        self._break()
        return "\n".join(self.lines), self.hidden, " ".join(self.title.split())


def page_text(html):
    """(visible text, hidden runs, page title) of an HTML page."""
    parser = _PageText()
    try:
        parser.feed(html)
        parser.close()
    except Exception as e:                     # html.parser is forgiving; be safe anyway
        log.info("HTML parsing stopped early: %s", type(e).__name__)
    text, hidden, title = parser.result()
    # Runs of the same hidden words (a hidden block's lines) joined once.
    seen, runs = set(), []
    for run in hidden:
        if run not in seen:
            seen.add(run)
            runs.append(run)
    return text, runs, title


def decode(body, charset=None):
    """Page bytes as text: the header's charset, else a <meta charset>,
    else UTF-8, never failing (bad bytes become �)."""
    if not charset:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w.-]+)", body[:4096], re.I)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# The whole thing
# ---------------------------------------------------------------------------

def read_page(config, url, resolver=None, opener=None):
    """(text for the proposer, how it was read, hidden runs, the final URL).
    How: "web" (an HTML page's visible text), or photo_import's own
    "pdf_text" / "pdf_scan" / "photo" when the link was to a file."""
    import photo_import
    got = fetch(url, resolver=resolver, opener=opener)
    body, media = got["body"], got["type"]
    if photo_import.sniff_pdf(body) or media == "application/pdf":
        try:
            media_type = photo_import.check_upload(body)
        except photo_import.UploadProblem as e:
            raise WebProblem(str(e))
        text, how, hidden = photo_import.read_upload(config, body, media_type)
        return text, how, hidden, got["url"]
    if photo_import.sniff_image(body):
        try:
            media_type = photo_import.check_upload(body)
        except photo_import.UploadProblem as e:
            raise WebProblem(str(e))
        text, how, hidden = photo_import.read_upload(config, body, media_type)
        return text, how, hidden, got["url"]
    if media and not (media.startswith("text/") or "html" in media or "xml" in media):
        raise WebProblem("That link isn't a web page, PDF or picture, so it can't be read.")
    text, hidden, title = page_text(decode(body, got["charset"]))
    if media == "text/plain":
        text, hidden = decode(body, got["charset"]), []
    if len(re.findall(r"[A-Za-z0-9]", text)) < MIN_TEXT_CHARS:
        raise WebProblem("Couldn't find much text on that page. Its menu may be a picture or "
                         "built by a script the reader can't run: upload a photo, screenshot "
                         "or PDF of it instead.")
    if title:
        text = f"{title}\n\n{text}"
    return text[:photo_import.MAX_TRANSCRIPTION], "web", hidden, got["url"]
