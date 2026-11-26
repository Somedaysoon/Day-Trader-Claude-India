#!/usr/bin/env python3
"""Entry Timer for trader-entry: watches a setup bar by bar and says the
moment a pick triggers. Standard library only. Long side only. It never places an order.

    python3 entry.py check RELIANCE                         # opening-range breakout, 5-minute bars
    python3 entry.py check RELIANCE --now "2026-11-23 10:15" --offline RELIANCE.json
    python3 entry.py check RELIANCE --setup level --level 1450.50   # first close above a price
    python3 entry.py check RELIANCE --interval 1m --min-volume-x 1.5 --above-vwap --write
    python3 entry.py watch RELIANCE --every 30 --until 14:45   # one line per closed bar
    python3 entry.py watchlist                              # every hit in trader/watchlist.json

Setups (longs only):
  orb    opening-range breakout. The range is the high and low of the first
         --or-minutes (default 15: the 9:15, 9:20 and 9:25 five-minute bars).
         It triggers on the first COMPLETED bar after the range that CLOSES
         above the range high. A wick above the high is not a trigger. The
         pre-open auction (9:00 to 9:15) is not part of any candle.
  level  the first completed bar that closes above --level (for example a
         20-day high from trader-scanner).
  --min-volume-x X  the trigger bar also needs at least X times the average
         volume of the opening-range bars (the 9:15 bar carries the opening
         auction's follow-through, so 1.0 is already a high bar).
  --above-vwap      the trigger bar must also close above the session VWAP
         through that bar. The VWAP is always shown on a trigger; this flag
         makes it a condition.

A trigger's time is the trigger bar's CLOSE time (the 10:10 bar closes at
10:15 AM) and its price is that close, to the paisa. Only bars on the interval
grid count (Yahoo's live tick, e.g. 10:49:45, is dropped) and a bar counts only
once it has closed: now >= its start + the interval.

The price is a bar close from Yahoo's free feed, which can lag. It is not a
price you can count on getting: look at the live quote in your own app before
you act on a trigger.

Data checks. A chart with an impossible bar (high below low, a close outside the
bar) gives no signal. An opening range with a missing bar (a halt, or a gap in
Yahoo's data) gives no signal either: the high of a range with a hole in it is
too low and would trigger early.

Hours are not one fixed set: since 3 Aug 2026 the stocks in the closing auction
session stop continuous trading at 15:15, and from 7 Dec 2026 everything runs to
15:30. --group cas|other|any picks the stock's group (the watchlist carries it;
any means the earlier end, the safe choice). The hours are in
_common/data/sessions.json.

Statuses: triggered; waiting (not open yet, range still forming, or no close
above the level yet); expired (no trigger by no_entries_after, by default 30
minutes before continuous trading ends: 14:45 now, 15:00 from 7 Dec);
no-data (the market is closed today, Yahoo sent no bars, or the bars failed a
data check).

trader/config.json (optional): "no_entries_before" (09:30) and
"no_entries_after" (the session's own default), Indian time.

watch wakes just after each bar closes (bars only change then), re-polls every
--every seconds (default 30) while Yahoo has not posted the new bar, prints one
line per closed bar and stops on a trigger (exit 0, writes trader/entry.json),
on expiry, at the close or at --until (exit 1). With --now or --offline it runs
on a simulated clock and replays the day without sleeping.

Data: Yahoo's free chart JSON (unofficial, can lag, bars not single trades)
  https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m
  falls back to query1 on HTTP 429 or 5xx; a 429 gets one retry after a pause.
  Yahoo's NSE (.NS) intraday coverage was not checked from the build environment.
  --offline PATH reads a saved chart JSON (.json or .json.gz) instead, or
  PATH/<SYMBOL>.json when PATH is a folder. Without --now, an offline file is
  read as of its own time (meta.regularMarketTime).

Exit codes: check 0 = triggered, 1 = anything else or an error. watch 0 on a
trigger, 1 otherwise. watchlist 0 when at least one symbol triggered.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import in_instruments as ins  # noqa: E402
from _common import in_market as mk  # noqa: E402
from _common import in_quality as quality  # noqa: E402

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0; +https://github.com/Jakeschincariol/day-trader-skill)"
PAUSE = 0.5      # seconds between requests to the free endpoint
BACKOFF = 5.0    # seconds before the one retry after an HTTP 429
GRACE = 2        # seconds after a bar closes before watch asks for it
SETTLE = 60      # seconds a closed bar may wait for Yahoo's data to catch up
HOSTS = ("query2", "query1")
INTERVALS = {"1m": 60, "5m": 300}
# no_entries_after is None: the session's own default (30 minutes before continuous trading ends)
DEFAULTS = {"no_entries_before": "09:30", "no_entries_after": None}
EPOCH = datetime.datetime(1970, 1, 1)
PRICE_NOTE = ("The price is a bar close from Yahoo's free feed and can lag. Check the live price in your own app "
              "before you act on it.")


class EntryError(Exception):
    pass


# ---------------------------------------------------------------- time (Indian time, from _common)

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    """Unix seconds -> naive UTC, without the machine's time zone."""
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc.replace(tzinfo=None) - EPOCH).total_seconds())


def iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_now(text):
    """--now -> naive UTC. "2026-11-23 10:15" (or ...T10:15, or just 10:15 for today)
    is Indian time; an offset or Z makes it absolute."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (mk.to_ist(utc_now()).date().isoformat(), t.zfill(5))
    return mk.parse_now(t, EntryError)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise EntryError("%s wants an Indian time like 14:45, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def hm(t):
    """10:10, 09:45: the contract's wall-clock form."""
    return "%02d:%02d" % (t.hour, t.minute)


def short(t):
    """9:45, 10:10: for sentences."""
    return "%d:%02d" % (t.hour, t.minute)


ampm = mk.clock_label
trading_day = mk.trading_day


def session(d, group="any"):
    """(open_time, close_time) of continuous trading in IST, or None if closed all day."""
    return mk.session(d, group)


def next_open(d):
    nd = mk.next_trading_day(d)
    return "%s %s" % (nd.isoformat(), hm(mk.session(nd)[0]))


# ---------------------------------------------------------------- Yahoo

def yahoo_symbol(sym):
    """The symbol as Yahoo writes it in a chart's meta: RELIANCE -> RELIANCE.NS."""
    return ins.plain_yahoo(sym)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (ins.yahoo_symbol(sym), rng, interval)


def chart_range(day, today, interval):
    """The smallest Yahoo range that still holds the session of `day`."""
    back = (today - day).days
    if back <= 0:
        return "1d"
    if back <= 6:
        return "5d"
    if interval == "1m":
        raise EntryError("Yahoo keeps 1-minute bars for about a week: use --interval 5m for %s" % day)
    if back <= 28:
        return "1mo"
    if back <= 58:
        return "60d"
    raise EntryError("Yahoo keeps 5-minute bars for about 60 days: %s is too far back" % day)


def _open_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _sleep(seconds):
    time.sleep(seconds)


def yahoo_get(path):
    """GET a Yahoo path: query2 first, query1 when query2 answers 429 or 5xx.
    An HTTP 429 gets one retry on the same host after BACKOFF seconds."""
    last = "no answer"
    for host in HOSTS:
        url = "https://%s.finance.yahoo.com%s" % (host, path)
        for attempt in (0, 1):
            try:
                return _open_url(url)
            except urllib.error.HTTPError as e:
                code, body = e.code, None
                try:
                    if code == 404:     # Yahoo says why in the body: an unknown ticker
                        body = e.read().decode("utf-8", "replace")
                except Exception:
                    body = None
                finally:
                    e.close()
                if code == 404:
                    if body:
                        return body
                    raise EntryError("Yahoo has no chart at %s (HTTP 404)" % url.split("?")[0])
                last = "HTTP %d" % code
                if code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if code == 429 or code >= 500:
                    break
                raise EntryError("Yahoo answered HTTP %d for %s" % (code, url.split("?")[0]))
            except urllib.error.URLError as e:
                last = "could not connect: %s" % (e.reason,)
                break
            except OSError as e:
                last = "network error: %s" % (e,)
                break
    raise EntryError("Yahoo did not answer (%s on query2 and query1): wait a minute and try again" % last)


def parse_chart(text, sym):
    """Yahoo chart JSON v8 -> {symbol, meta, bars, prev_close, data_time}.
    Bars with a null close are dropped here; off-grid ticks later."""
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise EntryError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise EntryError("Yahoo has no data for %s: %s" % (sym, err.get("description") or "unknown ticker"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        o, h, lo = at("open", i), at("high", i), at("low", i)
        bars.append({"t": int(t), "o": float(c if o is None else o), "h": float(c if h is None else h),
                     "l": float(c if lo is None else lo), "c": float(c), "v": int(at("volume", i) or 0)})
    return {"symbol": str(meta.get("symbol") or sym).upper(), "meta": meta, "bars": bars,
            "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "data_time": meta.get("regularMarketTime")}


def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def offline_file(path, sym):
    if os.path.isdir(path):
        for name in (sym + ".json", sym + ".json.gz", yahoo_symbol(sym) + ".json", yahoo_symbol(sym) + ".json.gz"):
            p = os.path.join(path, name)
            if os.path.exists(p):
                return p
        raise EntryError("no offline file for %s in %s (want %s.json)" % (sym, path, sym))
    if not os.path.exists(path):
        raise EntryError("no offline file %s" % path)
    return path


def get_chart(sym, interval, day=None, offline=None, today=None):
    """Bars for one ticker, from Yahoo (range=1d for today) or a saved file."""
    sym = sym.upper()
    if offline:
        f = offline_file(offline, sym)
        try:
            text = read_text(f)
        except (OSError, ValueError) as e:
            raise EntryError("could not read %s: %s" % (f, e))
    else:
        today = today or mk.to_ist(utc_now()).date()
        text = yahoo_get(chart_path(sym, chart_range(day or today, today, interval), interval))
    ch = parse_chart(text, sym)
    if ch["symbol"] != yahoo_symbol(sym):
        raise EntryError("the chart file holds %s, not %s" % (ch["symbol"], sym))
    gran = ch["meta"].get("dataGranularity")
    if gran and gran != interval:
        raise EntryError("%s has %s bars, not %s: use --interval %s" % (sym, gran, interval, gran))
    return ch


# ---------------------------------------------------------------- the rule

def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except ValueError as e:
            raise EntryError("%s is not valid JSON: %s" % (path, e))
        for k in DEFAULTS:
            if doc.get(k):
                cfg[k] = doc[k]
    for k in DEFAULTS:
        if cfg[k]:
            parse_hhmm(cfg[k], k)
    return cfg


def r2(x):
    return None if x is None else round(float(x), 2) + 0.0   # + 0.0 turns -0.0 into 0.0


def evaluate(chart, now_utc, setup="orb", or_minutes=15, interval="5m", level=None,
             min_volume_x=None, cfg=None, group="any", above_vwap=False):
    """Pure function: the setup's status at now_utc from one session's bars."""
    cfg = cfg or dict(DEFAULTS)
    if interval not in INTERVALS:
        raise EntryError("--interval is 5m or 1m")
    step = INTERVALS[interval]
    if or_minutes <= 0 or (or_minutes * 60) % step:
        raise EntryError("--or-minutes must be a whole number of %s bars" % interval)
    if setup not in ("orb", "level"):
        raise EntryError("--setup is orb or level")
    if setup == "level" and level is None:
        raise EntryError("--setup level needs --level PRICE (for example a 20-day high)")
    if group not in mk.GROUPS:
        raise EntryError("--group is cas, other or any")
    n_or = or_minutes * 60 // step
    local = mk.to_ist(now_utc)
    day = local.date()
    res = {"symbol": ins.from_yahoo(chart["symbol"]), "setup": setup, "side": "buy", "date": day.isoformat(), "group": group,
           "interval": interval, "or_minutes": or_minutes, "now": local.strftime("%Y-%m-%d %H:%M:%S"),
           "status": "waiting", "phase": "", "reason": "", "opening_range": None, "level": None, "trigger": None,
           "last": None, "forming": None, "min_volume_x": min_volume_x, "above_vwap": bool(above_vwap),
           "prev_close": chart.get("prev_close"), "calendar_known": mk.calendar_known(day), "_bars": []}
    dt = chart.get("data_time")
    res["data_time"] = mk.to_ist(utc_of(dt)).strftime("%Y-%m-%d %H:%M:%S") if dt else None
    ok, why = trading_day(day)
    if not ok:
        res.update(status="no-data", phase="closed", reason="the NSE is closed today (%s)" % why,
                   next_open=next_open(day))
        return res
    o, c = session(day, group)
    open_dt = datetime.datetime.combine(day, o)
    close_dt = datetime.datetime.combine(day, c)
    before = datetime.datetime.combine(day, parse_hhmm(cfg["no_entries_before"], "no_entries_before"))
    after = cfg.get("no_entries_after")
    cutoff = datetime.datetime.combine(day, parse_hhmm(after, "no_entries_after") if after
                                       else mk.no_entries_after(day, group))
    cutoff = min(cutoff, close_dt)
    or_end = open_dt + datetime.timedelta(minutes=or_minutes)
    res.update(opens=hm(o), closes=hm(c), no_entries_before=hm(before), no_entries_after=hm(cutoff))

    by_slot = {}
    for b in chart["bars"]:
        if b["t"] % step:
            continue                       # Yahoo's live tick (10:49:45) is not a bar
        ist = mk.to_ist(utc_of(b["t"]))
        if ist.date() != day or not (open_dt <= ist < close_dt):
            continue                       # another day, the pre-open auction, the closing auction
        slot = int((ist - open_dt).total_seconds()) // step
        by_slot[slot] = dict(b, et=ist, end=ist + datetime.timedelta(seconds=step), slot=slot)
    bars, pending = [], None
    now_ts = ts_of(now_utc)
    top = max(by_slot) if by_slot else -1
    for slot in sorted(by_slot):
        b = by_slot[slot]
        end_ts = b["t"] + step
        if end_ts <= now_ts:
            # Closed by the clock. On live data it counts once Yahoo has caught up: its last
            # trade is at or after the close, a later bar exists, or a minute has passed.
            if dt is None or dt >= end_ts or slot < top or now_ts >= end_ts + SETTLE:
                bars.append(b)
            else:
                pending = b
                res["forming"] = {"bar_start": hm(b["et"]), "closes": ampm(b["end"]), "price": r2(b["c"])}
                res["lag_note"] = ("Yahoo's last trade is %s, before the %s bar's close: it counts once Yahoo "
                                   "catches up (a minute at most)" % (mk.to_ist(utc_of(dt)).strftime("%H:%M:%S"),
                                                                       short(b["et"])))
        elif b["et"] <= local:
            res["forming"] = {"bar_start": hm(b["et"]), "closes": ampm(b["end"]), "price": r2(b["c"])}
    res["_bars"] = bars
    res["bars_closed"] = len(bars)

    if local < open_dt:
        res.update(phase="pre-open", reason="the NSE opens at %s%s" % (
            short(o), " and the %d-minute range is set at %s" % (or_minutes, short(or_end)) if setup == "orb" else ""))
        return res

    bad = quality.bar_problems([dict(b, start=b["et"]) for b in bars])
    if bad:
        res.update(status="no-data", phase="open",
                   reason="bad data: %s in the %s bar. No signal from a chart that cannot be right" % (
                       bad[0][1], short(bad[0][0])))
        return res

    or_bars = [b for b in bars if b["slot"] < n_or]
    avg_vol = (sum(b["v"] for b in or_bars) / float(len(or_bars))) if or_bars else None
    if or_bars:
        res["opening_range"] = {"high": r2(max(b["h"] for b in or_bars)), "low": r2(min(b["l"] for b in or_bars)),
                                "minutes": or_minutes, "bars": len(or_bars), "of": n_or,
                                "complete": local >= or_end, "avg_volume": int(round(avg_vol))}
    if setup == "orb":
        if local < or_end:
            res.update(phase="forming", reason="%d of %d range bars closed; the range is set at %s" % (
                len(or_bars), n_or, short(or_end)))
            return res
        if not or_bars:
            res.update(status="no-data", phase="open", reason="Yahoo sent no bars for the opening range")
            return res
        if len(or_bars) < n_or:
            if pending is not None and pending["slot"] < n_or:
                res.update(phase="forming", reason="the %s range bar is not posted yet (Yahoo is catching up)" % (
                    short(pending["et"])))
                return res
            res.update(status="no-data", phase="open",
                       reason="the opening range has %d of %d bars (a halt or missing data): its high cannot be "
                              "trusted, and a range with a hole in it would trigger early" % (len(or_bars), n_or))
            return res
        lvl = max(b["h"] for b in or_bars)
    else:
        lvl = float(level)
    res["level"] = r2(lvl)

    cum_pv = cum_v = 0.0
    vwaps = {}
    for b in bars:                         # session VWAP through each closed bar, from the open
        cum_pv += (b["h"] + b["l"] + b["c"]) / 3.0 * b["v"]
        cum_v += b["v"]
        vwaps[b["slot"]] = (cum_pv / cum_v) if cum_v else None

    first_slot = n_or if setup == "orb" else 0
    trig, thin, weak = None, [], []
    for b in bars:
        if b["slot"] < first_slot or b["end"] < before or b["end"] > cutoff:
            continue
        if b["c"] > lvl:
            vx = (b["v"] / avg_vol) if avg_vol else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                thin.append(b)
                continue
            vw = vwaps.get(b["slot"])
            if above_vwap and (vw is None or b["c"] <= vw):
                weak.append(b)
                continue
            trig = b
            break
    last = bars[-1] if bars else None
    if last:
        res["last"] = {"bar_start": hm(last["et"]), "close": r2(last["c"]),
                       "vs_level_pct": r2(100.0 * (last["c"] / lvl - 1))}

    if trig:
        vx = (trig["v"] / avg_vol) if avg_vol else None
        vw = vwaps.get(trig["slot"])
        res.update(status="triggered", phase="open" if local < close_dt else "after-close",
                   reason="the %s bar closed above %s" % (short(trig["et"]), "the range high" if setup == "orb" else "the level"))
        res["trigger"] = {"time": ampm(trig["end"]), "close_time": hm(trig["end"]), "bar_start": hm(trig["et"]),
                          "bar_index": trig["slot"], "price": r2(trig["c"]), "close": trig["c"],
                          "volume": trig["v"], "volume_x": r2(vx), "pct_over": r2(100.0 * (trig["c"] / lvl - 1)),
                          "vwap": r2(vw), "above_vwap": (trig["c"] > vw) if vw is not None else None}
        res["minutes_since"] = int((local - trig["end"]).total_seconds() // 60)
        if dt and utc_of(dt) < mk.ist_to_utc(trig["end"]):
            res["data_note"] = ("Yahoo's last trade is %s, before this bar closed: its close can still move a "
                                "paisa or two" % mk.to_ist(utc_of(dt)).strftime("%H:%M:%S"))
        return res
    if not bars:
        res.update(status="no-data", phase="open", reason="Yahoo sent no bars for %s yet" % day.isoformat())
        return res
    what = "close above the range high Rs %.2f" % lvl if setup == "orb" else "close above Rs %.2f" % lvl
    if local >= cutoff and not (pending and pending["end"] <= cutoff):
        res.update(status="expired", phase="after-close" if local >= close_dt else "after-cutoff",
                   reason="no %s by %s, the last time for a new entry" % (what, short(cutoff)))
        return res
    res["phase"] = "open"
    if last["c"] > lvl and last in thin:
        res["reason"] = "the %s bar closed above at Rs %.2f but on %.2fx the range's volume, under your %.2fx" % (
            short(last["et"]), last["c"], last["v"] / avg_vol if avg_vol else 0, min_volume_x)
    elif last["c"] > lvl and last in weak:
        vw = vwaps.get(last["slot"])
        res["reason"] = "the %s bar closed above at Rs %.2f but under the session VWAP%s" % (
            short(last["et"]), last["c"], (" Rs %.2f" % vw) if vw is not None else "")
    elif last["c"] > lvl and last["end"] < before:
        res["reason"] = "the %s bar closed above at Rs %.2f, but no entries before %s (config)" % (
            short(last["et"]), last["c"], short(before))
    else:
        gap = lvl - last["c"]
        res["reason"] = "last close Rs %.2f (%s bar), Rs %.2f %s %s (%+.2f%%)" % (
            last["c"], short(last["et"]), abs(gap), "under" if gap >= 0 else "over",
            "the range high Rs %.2f" % lvl if setup == "orb" else "the level Rs %.2f" % lvl,
            100.0 * (last["c"] / lvl - 1))
    return res


def public(res):
    """The result without the internal bar list, ready for JSON."""
    return {k: v for k, v in res.items() if not k.startswith("_")}


def entry_doc(res, now_utc):
    """trader/entry.json, the contract shape (plus a plain-words reason)."""
    t = res.get("trigger") or {}
    orr = res.get("opening_range")
    return {"created_by": "trader-entry", "schema_version": 2, "symbol": res["symbol"], "exchange": "NSE",
            "currency": "INR", "timezone": "Asia/Kolkata", "group": res.get("group", "any"),
            "setup": res["setup"], "side": "buy",
            "date": res["date"], "status": res["status"], "time": t.get("time"), "bar_start": t.get("bar_start"),
            "bar_index": t.get("bar_index"), "price": t.get("price"), "level": res.get("level"),
            "vwap": t.get("vwap"), "interval": res["interval"],
            "opening_range": {"high": orr["high"], "low": orr["low"], "minutes": orr["minutes"]} if orr else None,
            "source": "yahoo", "checked_at": iso(now_utc),
            "checked_at_ist": mk.to_ist(now_utc).strftime("%Y-%m-%d %H:%M:%S"),
            "data_time_ist": res.get("data_time"), "price_note": PRICE_NOTE, "reason": res.get("reason")}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- words

LABEL = {"triggered": "TRIGGERED", "waiting": "WAITING", "expired": "EXPIRED", "no-data": "NO DATA"}
PHASE_LABEL = {"pre-open": "NOT OPEN YET", "forming": "RANGE FORMING", "closed": "MARKET CLOSED"}
SHORT = {"pre-open": "not open", "forming": "forming", "closed": "closed"}


def setup_name(res):
    if res["setup"] == "orb":
        return "opening-range breakout (%d-minute range)" % res["or_minutes"]
    return "close above Rs %.2f" % res["level"] if res["level"] is not None else "close above a level"


def describe(res):
    """Plain lines for a person."""
    when = datetime.datetime.strptime(res["now"], "%Y-%m-%d %H:%M:%S")
    bars = "5-minute" if res["interval"] == "5m" else "1-minute"
    head = "%s  %s %s IST  %s on %s bars (Yahoo, free, can lag)" % (
        res["symbol"], when.strftime("%a %Y-%m-%d"), when.strftime("%H:%M"), setup_name(res), bars)
    if res.get("data_time") and res["data_time"] <= res["now"] and res["data_time"][:10] == res["date"]:
        head += ", data to %s" % res["data_time"][11:]
    out = [head]
    orr = res.get("opening_range")
    if orr:
        start = datetime.datetime.strptime(res["opens"], "%H:%M")
        end = start + datetime.timedelta(minutes=orr["minutes"])
        out.append("Opening range %s-%s: high Rs %.2f, low Rs %.2f (%d of %d bars%s)." % (
            short(start), short(end), orr["high"], orr["low"], orr["bars"], orr["of"],
            "" if orr["complete"] else ", still forming"))
    label = PHASE_LABEL.get(res["phase"]) or LABEL.get(res["status"], res["status"].upper())
    t = res.get("trigger")
    if t:
        vw = ""
        if t.get("vwap") is not None:
            vw = ", %s VWAP Rs %.2f" % ("above" if t["above_vwap"] else "under", t["vwap"])
        out.append("TRIGGERED %s at Rs %.2f: the %s bar (bar %d) closed above %s Rs %.2f (%+.2f%%%s%s)." % (
            t["time"], t["price"], short(datetime.datetime.strptime(t["bar_start"], "%H:%M")), t["bar_index"],
            "the range high" if res["setup"] == "orb" else "the level", res["level"], t["pct_over"],
            (", %.2fx the range's volume" % t["volume_x"]) if res.get("min_volume_x") and t.get("volume_x") else "",
            vw))
        step = INTERVALS[res["interval"]] // 60
        if res.get("minutes_since", 0) > step and res.get("last"):
            out.append("That was %d minutes ago; the last close is Rs %.2f (%s bar). A late entry is a different "
                       "trade: the price, the stop distance and the odds have moved." % (
                           res["minutes_since"], res["last"]["close"], res["last"]["bar_start"]))
        if res.get("data_note"):
            out.append(res["data_note"] + ".")
        out.append(PRICE_NOTE)
        if res.get("minutes_since", 0) > step:
            out.append("Nothing was placed.")
        else:
            out.append("This is the moment to get in, if you take the trade. Nothing was placed.")
        out.append("Next: set the stop and target BEFORE you buy: /trader-exit "
                   "(exit.py plan --from trader/entry.json).")
    else:
        out.append("%s  %s." % (label, res["reason"]))
        f = res.get("forming")
        if res.get("lag_note") and res["status"] == "waiting":
            out.append(res["lag_note"] + ".")
        elif f and res["status"] == "waiting" and res["phase"] in ("open", "forming"):
            out.append("The %s bar is still forming (Rs %.2f so far, closes %s); it does not count until it closes." % (
                f["bar_start"].lstrip("0"), f["price"], f["closes"]))
        if res["status"] == "expired":
            out.append("No entry on this setup today. Nothing was placed.")
        if res.get("next_open"):
            out.append("Next open: %s IST." % res["next_open"])
    if not res.get("calendar_known"):
        out.append("Holiday list covers %s only: check nseindia.com for this date." % " and ".join(map(str, mk.calendar_years())))
    return out


def bar_line(b, res):
    """One watch line for a bar that just closed."""
    start = short(b["et"])
    orr = res.get("opening_range") or {}
    lvl = res.get("level")
    t = res.get("trigger")
    n_or = orr.get("of") or 0
    if res["setup"] == "orb" and b["slot"] < n_or:
        seen = [x for x in res["_bars"] if x["slot"] <= b["slot"]]
        note = "range so far Rs %.2f to Rs %.2f" % (min(x["l"] for x in seen), max(x["h"] for x in seen))
        if b["slot"] == n_or - 1:
            note = "opening range set: high Rs %.2f, low Rs %.2f" % (orr["high"], orr["low"])
    elif t and t["bar_index"] == b["slot"]:
        note = "TRIGGERED: closed above Rs %.2f (%+.2f%%). Get in now if you take it: %s, Rs %.2f" % (
            lvl, t["pct_over"], t["time"], t["price"])
    elif lvl is None:
        note = ""
    elif b["c"] > lvl:
        note = "above Rs %.2f" % lvl
        if res["setup"] == "level" or b["slot"] >= n_or:
            avg = orr.get("avg_volume")
            if res.get("min_volume_x") and avg:
                note += ", but volume %.2fx is under %.2fx" % (b["v"] / float(avg), res["min_volume_x"])
            elif b["end"] < datetime.datetime.combine(b["et"].date(), parse_hhmm(res["no_entries_before"], "x")):
                note += ", but no entries before %s" % res["no_entries_before"]
    else:
        note = "Rs %.2f under Rs %.2f" % (lvl - b["c"], lvl)
    return "%5s bar  closed Rs %.2f  %s" % (start, b["c"], note)


# ---------------------------------------------------------------- watch

class SimClock(object):
    """A clock that moves only when it sleeps: replays a day without waiting."""

    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += datetime.timedelta(seconds=max(1, int(round(seconds))))


def watch_loop(provider, clock, sleep, emit, opts, every=30, until=None, max_errors=5, on_trigger=None):
    """Poll bar by bar until a trigger (0), expiry, the close or --until (1).
    provider(now_utc) -> chart, clock() -> naive UTC, sleep(seconds), emit(line).
    It sleeps in steps of at most `every` seconds and re-reads the clock each time,
    so a computer that dozed off catches up as soon as it wakes."""
    step = INTERVALS[opts.get("interval", "5m")]
    printed, errors, said_preopen, first = -1, 0, False, True
    next_fetch = None
    while True:
        now = clock()
        if next_fetch is not None and now < next_fetch:
            sleep(max(1, min(every, (next_fetch - now).total_seconds())))
            continue
        local = mk.to_ist(now)
        day = local.date()
        s = session(day, opts.get("group", "any"))
        if not s:
            emit("%s: market closed today (%s). Nothing to watch." % (day.isoformat(), trading_day(day)[1]))
            return 1, None
        open_dt = datetime.datetime.combine(day, s[0])
        close_dt = datetime.datetime.combine(day, s[1])
        if local < open_dt + datetime.timedelta(seconds=step):
            if not said_preopen:
                emit("%s IST  %s: watching from the first bar's close at %s." % (
                    local.strftime("%H:%M"), "not open yet" if local < open_dt else "open, no bar closed yet",
                    short(open_dt + datetime.timedelta(seconds=step))))
                said_preopen = True
            next_fetch = mk.ist_to_utc(open_dt) + datetime.timedelta(seconds=step + GRACE)
            continue
        try:
            chart = provider(now)
            errors = 0
        except EntryError as e:
            errors += 1
            emit("%s IST  could not read bars: %s" % (local.strftime("%H:%M:%S"), e))
            if errors >= max_errors:
                emit("Stopping after %d failed reads in a row." % errors)
                return 1, None
            next_fetch = now + datetime.timedelta(seconds=every)
            continue
        res = evaluate(chart, now, **opts)
        bars = res["_bars"]
        lines = describe(res)
        fresh = first
        if first:
            emit(lines[0])
            if len(lines) > 1 and lines[1].startswith("Opening range"):
                emit(lines[1])
            if bars and not res.get("trigger"):
                emit(bar_line(bars[-1], res))
            printed = bars[-1]["slot"] if bars else -1
            first = False
        else:
            for b in bars:
                if b["slot"] > printed:
                    emit(bar_line(b, res))
                    printed = b["slot"]
        if res["status"] == "triggered":
            for line in lines[1:]:
                if line.startswith("Opening range") or (line.startswith("TRIGGERED") and not fresh):
                    continue
                emit(line)
            if on_trigger:
                on_trigger(res, now)
            return 0, res
        if res["status"] == "expired" or local >= close_dt:
            emit("EXPIRED  %s." % (res["reason"] or "the market closed without a trigger"))
            return 1, res
        if until and local >= until:
            emit("Stopped at --until %s without a trigger. %s." % (short(until), res["reason"]))
            return 1, res
        # sleep until just after the next bar closes; poll every `every` seconds if Yahoo is behind
        cur_slot = int((local - open_dt).total_seconds()) // step
        expected_last = cur_slot - 1
        behind = (not bars) or bars[-1]["slot"] < expected_last
        if behind:
            wait = every
        else:
            nxt = open_dt + datetime.timedelta(seconds=(cur_slot + 1) * step + GRACE)
            wait = (nxt - local).total_seconds()
        cutoff = datetime.datetime.combine(day, parse_hhmm(res.get("no_entries_after") or hm(close_dt), "cutoff"))
        for stop_at in (until, cutoff, close_dt):
            if stop_at and stop_at > local:
                wait = min(wait, (stop_at - local).total_seconds())
        next_fetch = now + datetime.timedelta(seconds=max(1, wait))


# ---------------------------------------------------------------- commands

def _opts(a, cfg):
    if a.side in ("sell", "short"):
        raise EntryError("Longs only: no short entries. Watch for a buy (--side buy) instead.")
    if a.setup == "orb" and a.level is not None:
        raise EntryError("--level is for --setup level (an orb's level is its range high)")
    if a.min_volume_x is not None and a.min_volume_x <= 0:
        raise EntryError("--min-volume-x must be above 0")
    if a.level is not None and a.level <= 0:
        raise EntryError("--level must be a price above 0")
    return {"setup": a.setup, "or_minutes": a.or_minutes, "interval": a.interval, "level": a.level,
            "min_volume_x": a.min_volume_x, "cfg": cfg, "group": a.group,
            "above_vwap": a.above_vwap}


def _load(a, sym, now):
    """(chart, now). An offline file without --now is read as of its own time."""
    real_today = mk.to_ist(utc_now()).date()
    if a.offline and now is None:
        ch = get_chart(sym, a.interval, offline=a.offline)
        return ch, (utc_of(ch["data_time"]) if ch.get("data_time") else utc_now())
    now = now or utc_now()
    return get_chart(sym, a.interval, mk.to_ist(now).date(), a.offline, real_today), now


def cmd_check(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    sym = ins.from_yahoo(a.symbol)
    chart, now = _load(a, sym, parse_now(a.now) if a.now else None)
    res = evaluate(chart, now, **opts)
    if a.write:
        write_json(a.write, entry_doc(res, now))
    if a.json:
        print(json.dumps(public(res), indent=2))
    else:
        for line in describe(res):
            print(line)
        if a.write:
            print("Wrote %s (status %s)." % (a.write, res["status"]))
    return 0 if res["status"] == "triggered" else 1


def cmd_watch(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    sym = ins.from_yahoo(a.symbol)
    if a.every <= 0:
        raise EntryError("--every must be above 0 seconds")
    start = parse_now(a.now) if a.now else None
    if a.offline or start is not None:
        if start is None:
            start = _load(a, sym, None)[1]
        clock = SimClock(start)
        now_fn, sleep_fn = clock.now, clock.sleep
    else:
        now_fn, sleep_fn = utc_now, _sleep
    real_today = mk.to_ist(utc_now()).date()

    def provider(now):
        return get_chart(sym, a.interval, mk.to_ist(now).date(), a.offline, real_today)

    until = None
    if a.until:
        t = parse_hhmm(a.until, "--until")
        until = datetime.datetime.combine(mk.to_ist(now_fn()).date(), t)

    def on_trigger(res, now):
        write_json(a.out, entry_doc(res, now))
        print("Wrote %s." % a.out, flush=True)

    def emit(line):
        print(line, flush=True)

    code, _ = watch_loop(provider, now_fn, sleep_fn, emit, opts, every=a.every, until=until,
                         on_trigger=on_trigger)
    return code


def cmd_watchlist(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    if not os.path.exists(a.watchlist):
        raise EntryError("no watchlist at %s: run /trader-scanner first (it writes the hits there)" % a.watchlist)
    try:
        with open(a.watchlist, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise EntryError("%s is not valid JSON: %s" % (a.watchlist, e))
    hits = [h for h in (doc.get("hits") or []) if isinstance(h, dict) and h.get("symbol")]
    if not hits:
        raise EntryError("no hits in %s: the scanner found nothing to watch" % a.watchlist)
    if a.offline and not os.path.isdir(a.offline):
        raise EntryError("watchlist --offline wants a folder of <SYMBOL>.json files")
    now = parse_now(a.now) if a.now else None
    rows = []
    for i, h in enumerate(hits):
        sym = str(h["symbol"]).upper()
        o = dict(opts)
        if h.get("group") in mk.GROUPS and a.group == "any":
            o["group"] = h["group"]   # the watchlist knows the stock's closing-auction group
        if a.setup == "level" and o["level"] is None:
            o["level"] = h.get("level")
        try:
            if a.setup == "level" and o["level"] is None:
                raise EntryError("needs a level: --level PRICE or a \"level\" in the watchlist")
            if i and not a.offline:
                _sleep(PAUSE)
            chart, when = _load(a, sym, now)
            rows.append((sym, evaluate(chart, when, **o), None))
        except EntryError as e:
            rows.append((sym, None, str(e)))
    if a.json:
        print(json.dumps([{"symbol": s, "result": public(r) if r else None, "error": e} for s, r, e in rows], indent=2))
    else:
        print("%-12s  %-9s  %-8s  %-12s  %-12s  %-12s  %s" % ("SYMBOL", "STATUS", "TIME", "PRICE", "LEVEL", "LAST", "VS LEVEL"))
        for s, r, e in rows:
            if e:
                print("%-12s  error: %s" % (s, e))
                continue
            t = r.get("trigger") or {}
            last = r.get("last") or {}
            status = SHORT.get(r["phase"], r["status"]) if r["status"] != "triggered" else "triggered"
            print("%-12s  %-9s  %-8s  %-12s  %-12s  %-12s  %s" % (
                s, status[:9], t.get("time") or "-", ("Rs %.2f" % t["price"]) if t else "-",
                ("Rs %.2f" % r["level"]) if r.get("level") is not None else "-",
                ("Rs %.2f" % last["close"]) if last else "-",
                ("%+.2f%%" % last["vs_level_pct"]) if last.get("vs_level_pct") is not None else "-"))
        n = sum(1 for _, r, _ in rows if r and r["status"] == "triggered")
        print("%d of %d triggered. Pick one, then: entry.py check <SYMBOL> --write, and /trader-exit." % (n, len(rows)))
        print("Free Yahoo bars, unofficial and can lag. Nothing was placed.")
    return 0 if any(r and r["status"] == "triggered" for _, r, _ in rows) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Entry Timer (trader-entry): watches a setup bar by bar on NSE stocks.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--setup", choices=["orb", "level"], default="orb")
        p.add_argument("--or-minutes", type=int, default=15, help="opening range length (default 15)")
        p.add_argument("--interval", choices=["5m", "1m"], default="5m")
        p.add_argument("--level", type=float, help="the price to close above (--setup level)")
        p.add_argument("--min-volume-x", type=float, help="trigger bar volume vs the range bars' average")
        p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="buy only")
        p.add_argument("--group", choices=list(mk.GROUPS), default="any",
                       help="closing-auction group: cas, other, or any (the earlier session end)")
        p.add_argument("--above-vwap", action="store_true", help="the trigger bar must also close above the session VWAP")
        p.add_argument("--now", help='pretend it is this Indian time, e.g. "2026-11-23 10:15"')
        p.add_argument("--offline", metavar="PATH", help="saved Yahoo chart JSON (.json/.json.gz) or a folder")
        p.add_argument("--config", default="trader/config.json")

    p = sub.add_parser("check", help="the setup's status right now")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--write", nargs="?", const="trader/entry.json", metavar="PATH",
                   help="save the result (default trader/entry.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("watch", help="poll bar by bar until it triggers or expires")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--every", type=float, default=30, help="seconds between polls while Yahoo is behind")
    p.add_argument("--until", help="stop at this Indian time (HH:MM)")
    p.add_argument("--out", default="trader/entry.json", help="where a trigger is written")
    p = sub.add_parser("watchlist", help="check every hit in trader/watchlist.json")
    common(p)
    p.add_argument("--watchlist", default="trader/watchlist.json")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](a)
    except EntryError as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("entry.py: stopped.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
