"""theme_contrast.py — the colour tokens of each theme, and their contrast.

static/app.css defines the tokens twice: Night glass in ":root {" and the
Daylight theme in ':root[data-theme="light"] {' (2026-10-25). This reads
both and works out, for each theme, the contrast of every text colour on
what it's drawn over: a card (the frosted glass over the page) at the
page's own colour and at the brightest point of its backdrop, and the
white on a filled button. WCAG's 4.5:1 is the bar, as the stylesheet's own
header says ("contrast is measured, not eyeballed").

Run it:  python3 theme_contrast.py      (prints every pair)
theme_test.py holds every pair to the bar.
"""

import re
from pathlib import Path

CSS = Path(__file__).resolve().parent / "static" / "app.css"
TEXTS = ("ink", "ink-soft", "ink-faint", "pen", "booked", "pending", "refused")
FILLS = (("on-fill", "pen-fill"), ("on-fill", "pen-fill-2"), ("on-fill", "pen-fill-hover"),
         ("on-fill", "pen-fill-2-hover"), ("on-light", "pending-fill"),
         ("on-light", "pending-fill-hover"))
BAR = 4.5


def _block(css, opener):
    start = css.index(opener)
    return css[start:css.index("\n}", start)]


def tokens(css=None):
    """{"dark": {name: value}, "light": {...}}: Light is Dark with its own
    values on top, as the browser applies them."""
    css = css or CSS.read_text()
    dark = dict(re.findall(r"--([\w-]+):\s*([^;]+);", _block(css, ":root {")))
    light = dict(dark)
    light.update(re.findall(r"--([\w-]+):\s*([^;]+);", _block(css, ':root[data-theme="light"] {')))
    return {"dark": dark, "light": light}


def rgba(value):
    """(r, g, b, a) from #RGB, #RRGGBB or rgba()/rgb()."""
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    parts = [p.strip() for p in re.search(r"\(([^)]*)\)", value).group(1).split(",")]
    return (int(parts[0]), int(parts[1]), int(parts[2]),
            float(parts[3]) if len(parts) > 3 else 1.0)


def over(top, bottom):
    """`top` composited over an opaque `bottom`."""
    r, g, b, a = top
    return tuple(round(t * a + u * (1 - a)) for t, u in zip((r, g, b), bottom[:3])) + (1.0,)


def _luminance(colour):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in colour[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def pairs(theme):
    """[(what, ratio)] for a theme's tokens."""
    t = {k: v for k, v in theme.items()}
    out = []
    for ground_name in ("paper", "glow-brightest"):
        card = over(rgba(t["surface"]), over(rgba(t["smoke"]), rgba(t[ground_name])))
        for text in TEXTS:
            out.append((f"{text} on a card over {ground_name}",
                        ratio(over(rgba(t[text]), card), card)))
    for text, fill in FILLS:
        ground = rgba(t[fill])
        out.append((f"{text} on {fill}", ratio(over(rgba(t[text]), ground), ground)))
    return out


if __name__ == "__main__":
    for name, theme in tokens().items():
        print(f"\n{name}")
        for what, r in pairs(theme):
            print(f"  {'ok ' if r >= BAR else 'LOW'} {r:5.2f}  {what}")
