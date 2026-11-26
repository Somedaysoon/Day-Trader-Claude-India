"""Checks on candle data before any rule trusts it. Standard library only.

A bar here is a dict with start, c and optionally o, h, l, v, as the scanner
and entry tools build them. Nothing in this file fixes data: it names what is
wrong so the caller can drop the chart and say why.
"""


def ohlc_problems(o, h, l, c):
    """Reasons one bar cannot be real. None values are skipped (a missing open is
    common); a high below the low or a close outside the range is not."""
    out = []
    vals = [x for x in (o, h, l, c) if x is not None]
    if any(x <= 0 for x in vals):
        out.append("non-positive price")
    if h is not None and l is not None and h < l:
        out.append("high below low")
    if c is not None and h is not None and c > h * (1 + 1e-9):
        out.append("close above high")
    if c is not None and l is not None and c < l * (1 - 1e-9):
        out.append("close below low")
    if o is not None and h is not None and o > h * (1 + 1e-9):
        out.append("open above high")
    if o is not None and l is not None and o < l * (1 - 1e-9):
        out.append("open below low")
    return out


def bar_problems(bars):
    """[(start, reason)] for every bar with an impossible OHLC."""
    out = []
    for b in bars:
        for why in ohlc_problems(b.get("o"), b.get("h"), b.get("l"), b.get("c")):
            out.append((b.get("start"), why))
    return out


def zero_volume(bars):
    """How many bars traded nothing. One is normal in a quiet stock; a run is a halt."""
    return sum(1 for b in bars if not b.get("v"))


def timestamp_problems(ts):
    """Raw timestamps -> {'duplicates': n, 'out_of_order': n}."""
    dup = sum(1 for i in range(1, len(ts)) if ts[i] == ts[i - 1])
    back = sum(1 for i in range(1, len(ts)) if ts[i] < ts[i - 1])
    return {"duplicates": dup, "out_of_order": back}


def jump_over(prev_close, price, limit_pct):
    """True when price moved more than limit_pct from prev_close: a split, a bonus
    or bad data until shown otherwise."""
    if not prev_close or prev_close <= 0 or price is None:
        return False
    return abs(price / prev_close - 1) * 100 > limit_pct
