"""NSE instruments: symbol lists, Yahoo symbols, tick rounding. Standard library only.

The file layouts below are the ones NSE has published for its equity list,
index constituent lists and F&O lot-size file. They could not be checked
against the live files when this was written, so every parser reads headers
by name, ignores stray spaces and extra columns, and raises a plain error when
a column it needs is missing. Run `scan.py universe` once on a real download
and read the counts it prints.

Only the EQ series is kept. Trade-for-trade series (BE, BZ) cannot be traded
intraday, and SME and other series are not part of this tool.
"""

import csv
import io
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from urllib.parse import quote

TRADABLE_SERIES = ("EQ",)
DEFAULT_TICK = 0.05   # most NSE equities; the real tick comes from the instrument master when known


class InstrumentError(Exception):
    pass


# ------------------------------------------------------------ symbols

def yahoo_symbol(sym, exchange="NSE"):
    """'RELIANCE' -> 'RELIANCE.NS', 'M&M' -> 'M%26M.NS' (URL-safe)."""
    suffix = ".BO" if exchange.upper() == "BSE" else ".NS"
    return quote(sym.strip().upper(), safe="") + suffix


def plain_yahoo(sym, exchange="NSE"):
    """'M&M' -> 'M&M.NS', not URL-encoded: the form Yahoo puts in a chart's meta.symbol."""
    return sym.strip().upper() + (".BO" if exchange.upper() == "BSE" else ".NS")


def from_yahoo(ysym):
    """'RELIANCE.NS' -> 'RELIANCE'. Index symbols such as ^NSEI pass through."""
    s = (ysym or "").strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


# ------------------------------------------------------------ files

def _rows(text):
    text = text.lstrip("\ufeff").replace("\r", "")
    return list(csv.reader(io.StringIO(text)))


def _header_index(rows, needed, label):
    """First row holding every name in `needed` (case and spaces ignored)."""
    for i, row in enumerate(rows):
        cells = [c.strip().upper() for c in row]
        if all(n in cells for n in needed):
            return i, {c: k for k, c in enumerate(cells) if c}
    raise InstrumentError("not an NSE %s file: no header with %s" % (label, ", ".join(needed)))


def parse_equity_list(text):
    """NSE equity list -> (kept, counts). kept = [{symbol, name, series, isin, lot}] for
    tradable series only; counts = rows seen and rows per series."""
    rows = _rows(text)
    h, ix = _header_index(rows, ["SYMBOL", "SERIES"], "equity list")
    name_k = next((k for c, k in ix.items() if c.startswith("NAME")), None)
    isin_k = next((k for c, k in ix.items() if c.startswith("ISIN")), None)
    lot_k = ix.get("MARKET LOT")
    kept, counts = [], {"rows": 0}
    for row in rows[h + 1:]:
        if len(row) <= max(ix["SYMBOL"], ix["SERIES"]) or not row[ix["SYMBOL"]].strip():
            continue
        counts["rows"] += 1
        series = row[ix["SERIES"]].strip().upper()
        counts[series] = counts.get(series, 0) + 1
        if series not in TRADABLE_SERIES:
            continue
        kept.append({
            "symbol": row[ix["SYMBOL"]].strip().upper(),
            "name": " ".join(row[name_k].split()) if name_k is not None and name_k < len(row) else "",
            "series": series,
            "isin": row[isin_k].strip() if isin_k is not None and isin_k < len(row) else "",
            "lot": row[lot_k].strip() if lot_k is not None and lot_k < len(row) else "",
        })
    return kept, counts


def parse_index_list(text):
    """NSE index constituent list -> set of symbols (EQ series only when a Series column exists)."""
    rows = _rows(text)
    h, ix = _header_index(rows, ["SYMBOL"], "index list")
    out = set()
    for row in rows[h + 1:]:
        if len(row) <= ix["SYMBOL"] or not row[ix["SYMBOL"]].strip():
            continue
        if "SERIES" in ix and ix["SERIES"] < len(row) and row[ix["SERIES"]].strip().upper() not in TRADABLE_SERIES:
            continue
        out.add(row[ix["SYMBOL"]].strip().upper())
    return out


def parse_fno_list(text):
    """NSE F&O lot-size file -> set of underlying symbols (index underlyings are
    harmless: they never match an equity-list symbol)."""
    rows = _rows(text)
    h, ix = _header_index(rows, ["SYMBOL"], "F&O lot-size file")
    out = set()
    for row in rows[h + 1:]:
        if len(row) > ix["SYMBOL"] and row[ix["SYMBOL"]].strip():
            out.add(row[ix["SYMBOL"]].strip().upper())
    return out


def group_of(symbol, fno_symbols):
    """'cas' for stocks in the closing auction session (reported as the F&O-eligible ones),
    'other' for the rest, 'any' when no F&O list is known."""
    if fno_symbols is None:
        return "any"
    return "cas" if symbol.upper() in fno_symbols else "other"


# ------------------------------------------------------------ prices

def round_to_tick(price, tick=DEFAULT_TICK, direction="nearest"):
    """Round a rupee price to the exchange tick. direction: nearest, up or down."""
    if tick <= 0:
        raise InstrumentError("tick must be positive")
    p, t = Decimal(str(price)), Decimal(str(tick))
    mode = {"nearest": ROUND_HALF_UP, "up": ROUND_CEILING, "down": ROUND_FLOOR}[direction]
    n = (p / t).to_integral_value(rounding=mode)
    return float(n * t)
