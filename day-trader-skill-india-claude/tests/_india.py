"""SYNTHETIC NSE charts for the tests. Nothing here is real market data.

The shapes follow Yahoo's v8 chart and v7 spark answers for a .NS symbol: UTC
timestamps on a 5-minute grid that starts at 09:15 Indian time, gmtoffset
19800, currency INR. The numbers are invented so each test can state exactly
what it expects. Replace them with real captures (tools/capture_fixtures.py)
before trusting any behaviour that depends on how Yahoo really answers.
"""

import datetime

EPOCH = datetime.datetime(1970, 1, 1)
DT = datetime.datetime


def ts(day, hh, mm):
    """Indian wall-clock time on `day` -> unix seconds."""
    return int((DT.combine(day, datetime.time(hh, mm)) - datetime.timedelta(minutes=330) - EPOCH).total_seconds())


def pad(head, n, tail=None):
    """Extend a list of closes to n bars with the last value (or `tail`)."""
    last = head[-1] if tail is None else tail
    return list(head) + [last] * (n - len(head))


def bars(closes, first_open=None, spread=0.3, vol=10000):
    """closes -> [(o, h, l, c, v)]. Each open is the previous close; high and low sit
    `spread` outside the body."""
    out, prev = [], first_open if first_open is not None else closes[0]
    for c in closes:
        v = vol[len(out)] if isinstance(vol, (list, tuple)) else vol
        out.append((prev, max(prev, c) + spread, min(prev, c) - spread, c, v))
        prev = c
    return out


def day_series(day, bar_list, start=(9, 15), step=5):
    """One session's bars -> (timestamps, o, h, l, c, v) lists."""
    t0 = DT.combine(day, datetime.time(*start))
    t, o, h, l, c, v = [], [], [], [], [], []
    for i, b in enumerate(bar_list):
        stamp = t0 + datetime.timedelta(minutes=step * i)
        t.append(ts(day, stamp.hour, stamp.minute))
        o.append(b[0]); h.append(b[1]); l.append(b[2]); c.append(b[3]); v.append(b[4])
    return t, o, h, l, c, v


def result(symbol, sessions, price=None, volume=None, prev_close=None, name_for_index=False):
    """sessions: {date: [(o,h,l,c,v)]} -> one Yahoo chart result dict."""
    t, o, h, l, c, v = [], [], [], [], [], []
    for day in sorted(sessions):
        a = day_series(day, sessions[day])
        for dst, src in zip((t, o, h, l, c, v), a):
            dst.extend(src)
    last_close = c[-1] if c else None
    meta = {"currency": "INR", "symbol": symbol, "exchangeName": "NSI", "instrumentType": "EQUITY",
            "gmtoffset": 19800, "timezone": "IST", "exchangeTimezoneName": "Asia/Kolkata",
            "regularMarketPrice": price if price is not None else last_close,
            "regularMarketVolume": volume if volume is not None else (sum(v) if v else 0),
            "chartPreviousClose": prev_close, "dataGranularity": "5m", "range": "5d"}
    return {"meta": meta, "timestamp": t,
            "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}


def v8(symbol, sessions, **kw):
    return {"chart": {"result": [result(symbol, sessions, **kw)], "error": None}}


def spark(items):
    """items: {symbol: sessions} -> a v7 spark answer (closes only)."""
    res = []
    for sym, sessions in items.items():
        r = result(sym, sessions)
        r["indicators"]["quote"] = [{"close": r["indicators"]["quote"][0]["close"]}]
        res.append({"symbol": sym, "response": [{"meta": r["meta"], "timestamp": r["timestamp"],
                                                 "indicators": r["indicators"]}]})
    return {"spark": {"result": res, "error": None}}


def daily(symbol, closes, last_day, price=None, volume=None):
    """Daily bars ending on last_day (one a weekday, no holiday handling): a 1d spark/v8 answer."""
    days, d = [], last_day
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d -= datetime.timedelta(days=1)
    days.reverse()
    t = [ts(x, 9, 15) for x in days]
    meta = {"currency": "INR", "symbol": symbol, "exchangeName": "NSI", "gmtoffset": 19800, "timezone": "IST",
            "regularMarketPrice": price if price is not None else closes[-1],
            "regularMarketVolume": volume if volume is not None else 1000000}
    return {"chart": {"result": [{"meta": meta, "timestamp": t,
                                  "indicators": {"quote": [{"close": list(closes)}]}}], "error": None}}
