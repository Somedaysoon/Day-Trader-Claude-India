"""NSE calendar, sessions and Indian time. Standard library only.

One source of truth for every skill. tools/sync_common.py copies this file and
the data/ folder next to it into each skill as _common/, so a skill folder
still works on its own.

Indian time is a fixed UTC+05:30 with no daylight saving, so the offset is a
constant and the machine's time zone is never used.

Hours are not one fixed set. Since 3 Aug 2026 the stocks in the closing
auction session (reported as the F&O-eligible ones) stop continuous trading at
15:15; from 7 Dec 2026 continuous trading runs to 15:30 again. The hours live
in data/sessions.json by effective date and by stock group. Group "cas" is the
auction group, "other" is everything else, and "any" (the default) means the
earlier of the two, which is the safe choice when the group is not known.

Holidays live in data/nse_holidays.json. A date outside the years that file
covers is reported as "calendar not known" instead of being guessed.
"""

import datetime
import json
import os

IST_MINUTES = 330
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
GROUPS = ("cas", "other", "any")


class MarketError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Naive UTC datetime -> naive Indian time."""
    return utc.replace(tzinfo=None) + datetime.timedelta(minutes=IST_MINUTES)


def ist_to_utc(local):
    """Naive Indian time -> naive UTC."""
    return local.replace(tzinfo=None) - datetime.timedelta(minutes=IST_MINUTES)


def ts_to_ist(ts):
    """Unix seconds -> naive Indian time. Never uses the machine's time zone."""
    return to_ist(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)))


def parse_now(text, error=MarketError):
    """ISO time -> naive UTC. A time without an offset is taken as Indian time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise error('--now wants a time like "2026-11-23 10:15" (Indian time) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at", error=MarketError):
    import re
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise error("%s wants an Indian time like 10:15" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _t(text):
    h, m = text.split(":")
    return datetime.time(int(h), int(m))


def add_minutes(t, minutes):
    """datetime.time plus minutes (kept within the day)."""
    base = datetime.datetime.combine(datetime.date(2000, 1, 1), t)
    return (base + datetime.timedelta(minutes=minutes)).time()


def clock_label(t):
    """datetime or time -> '10:15 AM'."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- data

_cache = {}


def _load(name):
    if name not in _cache:
        with open(os.path.join(DATA, name)) as fh:
            _cache[name] = json.load(fh)
    return _cache[name]


def calendar():
    return _load("nse_holidays.json")


def sessions_data():
    return _load("sessions.json")


def reload():
    """Forget the cached data files (tests and long-running watchers)."""
    _cache.clear()


def calendar_years():
    return tuple(calendar().get("years_covered", ()))


def calendar_known(d):
    """True when the holiday list covers this date's year."""
    return d.year in calendar_years()


# ---------------------------------------------------------------- days

def special_session(d):
    for s in calendar().get("special_sessions", []):
        if s.get("date") == d.isoformat():
            return s
    return None


def trading_day(d):
    """(is_trading_day, reason). Weekends, the NSE holiday list and special
    sessions that the strategy does not use."""
    cal = calendar()
    sp = special_session(d)
    if sp is not None and not sp.get("strategy", False):
        return False, "special session (%s), not used for the strategy" % sp.get("kind", "special")
    if d.weekday() >= 5:
        return False, "weekend"
    name = cal.get("holidays", {}).get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def next_trading_day(d):
    d = d + datetime.timedelta(days=1)
    for _ in range(40):
        if trading_day(d)[0]:
            return d
        d += datetime.timedelta(days=1)
    return d


def previous_trading_day(d):
    d = d - datetime.timedelta(days=1)
    for _ in range(40):
        if trading_day(d)[0]:
            return d
        d -= datetime.timedelta(days=1)
    return d


# ---------------------------------------------------------------- hours

def profile(d):
    """The session profile in force on date d (latest effective_from <= d)."""
    best = None
    for p in sessions_data()["profiles"]:
        if p["effective_from"] <= d.isoformat():
            if best is None or p["effective_from"] > best["effective_from"]:
                best = p
    if best is None:
        raise MarketError("no session profile covers %s" % d.isoformat())
    return best


def _group(group):
    if group not in GROUPS:
        raise MarketError("group must be one of %s, not %r" % (", ".join(GROUPS), group))
    return group


def session(d, group="any"):
    """(open_time, close_time) of continuous trading in Indian time, or None
    if the market is closed all day. close_time is when continuous trading
    ends for that stock group, which is not always 15:30."""
    if not trading_day(d)[0]:
        return None
    cont = profile(d)["continuous"]
    if _group(group) == "any":
        spans = [(_t(v[0]), _t(v[1])) for v in cont.values()]
        return max(s[0] for s in spans), min(s[1] for s in spans)
    v = cont[group]
    return _t(v[0]), _t(v[1])


def auction(d, group="cas"):
    """(start, end) of the closing auction for a group, or None."""
    if not trading_day(d)[0]:
        return None
    v = profile(d).get("auction", {}).get(_group(group) if group != "any" else "cas")
    return (_t(v[0]), _t(v[1])) if v else None


def settled_at(d):
    """When the day's last print is in, for finishing a daily bar: the latest end of
    continuous trading or of the closing auction on that date (15:35 or 15:40)."""
    if not trading_day(d)[0]:
        return None
    prof = profile(d)
    ends = [_t(v[1]) for v in prof["continuous"].values()]
    ends += [_t(v[1]) for v in prof.get("auction", {}).values()]
    return max(ends)


def pre_open(d):
    if not trading_day(d)[0]:
        return None
    v = profile(d)["pre_open"]
    return _t(v[0]), _t(v[1])


def session_minutes(d, group="any"):
    s = session(d, group)
    if s is None:
        return 0
    return int((datetime.datetime.combine(d, s[1]) - datetime.datetime.combine(d, s[0])).total_seconds() // 60)


def session_bars(d, group="any", bar_min=5):
    """How many complete bars of bar_min minutes the continuous session holds
    (75 five-minute bars to 15:30, 72 to 15:15)."""
    return session_minutes(d, group) // bar_min


def opening_range_end(d, group="any", minutes=15):
    s = session(d, group)
    return add_minutes(s[0], minutes) if s else None


def no_entries_after(d, group="any", minutes_before_close=30):
    s = session(d, group)
    return add_minutes(s[1], -minutes_before_close) if s else None


def flat_by(d, group="any", minutes_before_close=10):
    s = session(d, group)
    return add_minutes(s[1], -minutes_before_close) if s else None


def state(now_ist, group="any"):
    """What the market is doing at a naive Indian time:
    (state, detail) with state one of 'closed', 'pre-open', 'open',
    'closing-auction'."""
    d, t = now_ist.date(), now_ist.time()
    ok, why = trading_day(d)
    if not ok:
        return "closed", why
    po = pre_open(d)
    s = session(d, group)
    if po[0] <= t < po[1]:
        return "pre-open", "pre-open auction; its prices are not part of the 5-minute candles"
    if s[0] <= t < s[1]:
        return "open", ""
    a = auction(d, "cas" if group == "any" else group)
    if a and a[0] <= t < a[1]:
        return "closing-auction", "closing auction session; continuous trading has ended for these stocks"
    return "closed", "outside market hours"


def next_open(now_ist, group="any"):
    """The next time continuous trading starts, as a naive Indian datetime."""
    d = now_ist.date()
    s = session(d, group)
    if s and now_ist.time() < s[0]:
        return datetime.datetime.combine(d, s[0])
    nd = next_trading_day(d)
    return datetime.datetime.combine(nd, session(nd, group)[0])


def bar_slots(d, group="any", bar_min=5):
    """Start times of every complete bar in the continuous session."""
    s = session(d, group)
    if s is None:
        return []
    out, cur = [], datetime.datetime.combine(d, s[0])
    end = datetime.datetime.combine(d, s[1])
    while cur + datetime.timedelta(minutes=bar_min) <= end:
        out.append(cur.time())
        cur += datetime.timedelta(minutes=bar_min)
    return out
